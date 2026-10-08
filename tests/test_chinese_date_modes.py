"""Publisher dates must survive all mode boundaries without changing weekly state."""

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from philosophy_frontier_monitor.config import CnkiSpaceConfig, load_watchlist
from philosophy_frontier_monitor.pipeline import FeedSnapshot, run_on_demand
from philosophy_frontier_monitor.search import SearchOptions, run_paper_search
from philosophy_frontier_monitor.search_report import render_paper_search
from philosophy_frontier_monitor.sources.cnki_space import (
    CnkiRecord,
    CnkiScan,
    CnkiSearchTerm,
    parse_result_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 28, 1, tzinfo=UTC)


@pytest.mark.parametrize("dates,raw_month,eligible", [
    ({"publication_date": "2026-09-15"}, None, True),
    ({"publication_date": "2026-05-15"}, None, False),
    ({"issue_label_month": "2026-09", "publication_date": "2026-08-31"}, None, True),
    ({"issue_label_month": "2026-08"}, 9, False),
])
def test_search_and_pull_use_the_same_reviewed_dates(tmp_path, dates, raw_month, eligible):
    evidence = yaml.safe_load((FIXTURES / "cnki_reviewed_evidence.yaml").read_text("utf-8"))
    evidence["records"][0].update(dates)
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(yaml.safe_dump(evidence, allow_unicode=True), "utf-8")
    original = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(
        original, cnki_space=CnkiSpaceConfig(True, (CnkiSearchTerm("合成检索词"),), 1, reviewed),
        storage=replace(original.storage, state_database=tmp_path / "state.sqlite3"),
    )
    # An unopened sentinel catches an accidental state write/open without requiring baseline setup.
    config.storage.state_database.write_bytes(b"private-state-sentinel")
    before = sha256(config.storage.state_database.read_bytes()).hexdigest()
    raw = parse_result_page((FIXTURES / "cnki_space_list.html").read_text("utf-8"))[0][0]
    record = replace(raw, label_month=raw_month)
    missing = CnkiRecord("缺日期记录", "https://www.cnki.com.cn/Article/CJFDTOTAL-TEST002.htm",
                         ("甲",), "哲学分析", 2026, "10", None)

    def cnki_loader(*args, **kwargs):
        # Flat discovery records also need the same exact-bibliography date corroboration.
        return CnkiScan((), (record, missing), kwargs["checked_at"], "success", 1, 1, (), ())

    def feed_loader(feed, checked_at):
        return FeedSnapshot(feed.feed_key, feed.category_id, feed.url, checked_at, "empty", ())

    search = run_paper_search(
        config, now=NOW, options=SearchOptions(year_from=2026, year_to=2026),
        feed_loader=feed_loader, cnki_loader=cnki_loader, allow_development_fixture=True,
    )
    candidate = search.cnki_candidates[0]
    rendered = render_paper_search(search)
    assert candidate.publication_date == dates.get("publication_date")
    assert candidate.date_evidence_urls == (evidence["records"][0]["evidence_url"],)
    assert candidate.date_conflict == (raw_month == 9)
    assert candidate.date_evidence_urls[0] in rendered
    assert "出版月份未核实" in rendered and "缺日期记录" in rendered
    assert "日期证据冲突" in rendered if candidate.date_conflict else "刊方标示出版日期" in rendered
    assert len(search.cnki_candidates) == 2  # Historical candidates keep their own date precision.
    assert search.total_matches == 0

    pull = run_on_demand(
        config, now=NOW, lookback_days=7, feed_loader=feed_loader, cnki_loader=cnki_loader,
        allow_development_fixture=True,
    )
    assert (record.title in pull.report_markdown) == eligible
    assert "缺日期记录" not in pull.report_markdown
    assert pull.stats["cnki_recent_issue_candidates"] == int(eligible)
    if eligible:
        assert candidate.date_evidence_urls[0] in pull.report_markdown
        assert candidate.publication_date in pull.report_markdown
    assert sha256(config.storage.state_database.read_bytes()).hexdigest() == before


def test_search_year_scope_does_not_expand_when_publisher_dates_are_used(tmp_path):
    evidence = yaml.safe_load((FIXTURES / "cnki_reviewed_evidence.yaml").read_text("utf-8"))
    evidence["records"][0]["publication_date"] = "2026-09-15"
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(yaml.safe_dump(evidence, allow_unicode=True), "utf-8")
    config = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(config, cnki_space=CnkiSpaceConfig(
        True, (CnkiSearchTerm("合成检索词"),), 1, reviewed,
    ))
    record = parse_result_page((FIXTURES / "cnki_space_list.html").read_text("utf-8"))[0][0]
    result = run_paper_search(
        config, now=NOW, options=SearchOptions(year_from=2025, year_to=2025),
        feed_loader=lambda feed, at: FeedSnapshot(feed.feed_key, feed.category_id,
                                                  feed.url, at, "empty", ()),
        cnki_loader=lambda *args, **kw: CnkiScan((), (record,), kw["checked_at"],
                                               "success", 1, 1, (), ()),
        allow_development_fixture=True,
    )
    assert result.cnki_candidates == ()
    assert result.stats["cnki_publisher_date_supported"] == 0
