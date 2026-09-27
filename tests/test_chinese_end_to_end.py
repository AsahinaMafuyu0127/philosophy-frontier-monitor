"""Fresh isolated Chinese-leads journey through all three public modes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import philosophy_frontier_monitor.pipeline as pipeline
from philosophy_frontier_monitor.config import CnkiSpaceConfig, load_watchlist
from philosophy_frontier_monitor.official_journals import (
    list_issues,
    new_observation,
    record_issue,
    review_observed_issue,
)
from philosophy_frontier_monitor.pipeline import (
    FeedSnapshot,
    PipelineError,
    establish_baseline,
    run_on_demand,
    run_weekly,
)
from philosophy_frontier_monitor.search import run_paper_search
from philosophy_frontier_monitor.search_report import render_paper_search
from philosophy_frontier_monitor.sources.cnki_space import (
    CnkiIssue,
    CnkiRecord,
    CnkiScan,
    CnkiSearchTerm,
)
from philosophy_frontier_monitor.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def test_fresh_profile_search_pull_weekly_and_reviewed_issue_journey(
    workspace_tmp_path: Path, monkeypatch,
) -> None:
    """A source-labelled candidate never turns into a verified paper by report mode."""
    now = datetime.now(UTC).replace(microsecond=0)
    year = now.year
    month = now.month
    month_label = f"{year}-{month:02d}"
    db = workspace_tmp_path / "official-journals.sqlite3"
    pending = new_observation(
        "zhexue-dongtai", year, "8",
        "https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paper_list/type_benqi",
        "journal-site",
    )
    record_issue(db, pending)
    assert list_issues(db, reviewed_only=True) == ()
    reviewed = review_observed_issue(
        db, pending.journal_id, pending.year, pending.issue, pending.evidence_url,
        label_month=month_label,
    )

    original = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(
        original,
        cnki_space=CnkiSpaceConfig(True, (CnkiSearchTerm("测试术语"),), 1),
        storage=replace(
            original.storage,
            state_database=workspace_tmp_path / "state.sqlite3",
            report_directory=workspace_tmp_path / "reports",
        ),
    )

    def feed_loader(feed, checked_at):
        return FeedSnapshot(feed.feed_key, feed.category_id, feed.url, checked_at,
                            "sha256:empty", ())

    def issue(number: str) -> CnkiIssue:
        record = CnkiRecord(
            title=f"合成中文论文{number}",
            url=f"https://www.cnki.com.cn/Article/CJFDTOTAL-TEST{year}{number}.htm",
            authors=("测试作者",), venue="哲学动态", year=year,
            issue=number, label_month=month,
        )
        return CnkiIssue(number, "哲学动态", year, number, month, (record,), (), now)

    first, second = issue("8"), issue("9")

    def cnki_scan(issues, checked_at):
        return CnkiScan(
            issues, tuple(record for item in issues for record in item.records),
            checked_at, "success", 1, 1, (), (),
        )

    search = run_paper_search(
        config, now=now, feed_loader=feed_loader,
        cnki_loader=lambda *_args, **kwargs: cnki_scan((first,), kwargs["checked_at"]),
        allow_development_fixture=True,
    )
    search_text = render_paper_search(
        search, official_issues=list_issues(db, reviewed_only=True)
    )
    assert search.total_matches == 0
    assert "合成中文论文8" in search_text
    assert "期刊官方发布渠道：新期次线索" in search_text

    pull = run_on_demand(
        config, now=now, lookback_days=30, feed_loader=feed_loader,
        cnki_loader=lambda *_args, **kwargs: cnki_scan((first,), kwargs["checked_at"]),
        allow_development_fixture=True,
    )
    assert "合成中文论文8" in pull.report_markdown
    assert "期刊官方发布渠道：新期次线索" in pull.report_markdown
    assert pull.stats["cnki_recent_issue_candidates"] == 1

    monkeypatch.setattr(pipeline, "require_production_taxonomy", lambda _snapshot: None)
    reviewed_at = datetime.fromisoformat(reviewed.reviewed_at).astimezone(UTC)
    first_start = reviewed_at - timedelta(days=1)
    first_end = first_start + timedelta(days=7)
    second_end = first_end + timedelta(days=7)
    scans = [(first,), (first, second)]

    def weekly_cnki(*_args, **kwargs):
        return cnki_scan(scans.pop(0), kwargs["checked_at"])

    with StateStore(config.storage.state_database) as state:
        establish_baseline(
            config, state, now=first_start - timedelta(days=1),
            feed_loader=feed_loader, allow_development_fixture=True,
        )
        week_one = run_weekly(
            config, state, now=first_end, window_start=first_start, window_end=first_end,
            feed_loader=feed_loader, cnki_loader=weekly_cnki,
        )
        week_two = run_weekly(
            config, state, now=second_end, window_start=first_end, window_end=second_end,
            feed_loader=feed_loader, cnki_loader=weekly_cnki,
        )
        assert state.known_cnki_issue_keys({"8", "9"}) == {"8", "9"}
        with pytest.raises(PipelineError, match="already committed"):
            run_weekly(
                config, state, now=second_end, window_start=first_end, window_end=second_end,
                feed_loader=feed_loader, cnki_loader=weekly_cnki,
            )

    assert "作为基线观察" in week_one.report_markdown
    assert "合成中文论文8" in week_one.report_markdown
    assert "期刊官方发布渠道：新期次线索" in week_one.report_markdown
    assert "合成中文论文9" in week_two.report_markdown
    assert "合成中文论文8" not in week_two.report_markdown
    assert "中文期刊新期次观察（0）" not in week_two.report_markdown


def test_fresh_profile_with_chinese_sources_off_never_calls_them(
    workspace_tmp_path: Path, monkeypatch,
) -> None:
    config = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(
        config,
        storage=replace(
            config.storage,
            state_database=workspace_tmp_path / "state.sqlite3",
            report_directory=workspace_tmp_path / "reports",
        ),
    )
    assert not config.cnki_space.enabled
    assert not config.wanfang.enabled
    now = datetime.now(UTC).replace(microsecond=0)

    def feed_loader(feed, checked_at):
        return FeedSnapshot(feed.feed_key, feed.category_id, feed.url, checked_at,
                            "sha256:empty", ())

    def unexpected_source(*_args, **_kwargs):
        raise AssertionError("disabled Chinese source was queried")

    search = run_paper_search(
        config, now=now, feed_loader=feed_loader, cnki_loader=unexpected_source,
        wanfang_loader=unexpected_source, wanfang_discovery_loader=unexpected_source,
        allow_development_fixture=True,
    )
    assert search.cnki_candidates == ()
    assert search.cnki_coverage is None
    assert search.wanfang_scan is None
    assert search.wanfang_discovery is None
    pull = run_on_demand(
        config, now=now, lookback_days=30, feed_loader=feed_loader,
        cnki_loader=unexpected_source, wanfang_loader=unexpected_source,
        wanfang_discovery_loader=unexpected_source, allow_development_fixture=True,
    )
    assert pull.stats["cnki_recent_issue_candidates"] == 0

    monkeypatch.setattr(pipeline, "require_production_taxonomy", lambda _snapshot: None)
    start = now - timedelta(days=7)
    with StateStore(config.storage.state_database) as state:
        establish_baseline(
            config, state, now=start - timedelta(days=1), feed_loader=feed_loader,
            allow_development_fixture=True,
        )
        weekly = run_weekly(
            config, state, now=now, window_start=start, window_end=now,
            feed_loader=feed_loader, cnki_loader=unexpected_source,
            wanfang_loader=unexpected_source,
            wanfang_discovery_loader=unexpected_source,
        )
    assert weekly.report_markdown
    assert weekly.stats["cnki_issue_candidates"] == 0
