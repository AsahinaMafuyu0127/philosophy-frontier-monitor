"""Fresh isolated Chinese-leads journey through all three public modes."""

from __future__ import annotations

import json
import runpy
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import httpx
import pytest

import philosophy_frontier_monitor.pipeline as pipeline
from philosophy_frontier_monitor import cli
from philosophy_frontier_monitor.config import CnkiSpaceConfig, load_watchlist
from philosophy_frontier_monitor.journal_collection import collect_publisher, import_reviewed_toc
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
from philosophy_frontier_monitor.publisher_search import search_publisher_papers
from philosophy_frontier_monitor.search import SearchOptions, run_paper_search
from philosophy_frontier_monitor.search_report import render_paper_search
from philosophy_frontier_monitor.sources.cnki_space import (
    CnkiIssue,
    CnkiRecord,
    CnkiScan,
    CnkiSearchTerm,
)
from philosophy_frontier_monitor.state import StateStore

FIXTURES = Path(__file__).parent / "fixtures"


def _empty_feed(feed, checked_at):
    return FeedSnapshot(feed.feed_key, feed.category_id, feed.url, checked_at, "sha256:empty", ())


@pytest.mark.parametrize("mode", ["weekly-run", "catch-up"])
def test_cli_weekly_preview_preserves_pre_upgrade_state(mode, workspace_tmp_path, monkeypatch):
    original = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    database = workspace_tmp_path / "state.sqlite3"
    reports = workspace_tmp_path / "reports"
    config = replace(original, storage=replace(
        original.storage, state_database=database, report_directory=reports,
    ))
    end = datetime(2026, 9, 14, tzinfo=UTC)
    start = end - timedelta(days=7)
    with StateStore(database) as state:
        establish_baseline(
            config, state, now=start - timedelta(days=1), feed_loader=_empty_feed,
            allow_development_fixture=True,
        )
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE publisher_article_reports")
        connection.execute("UPDATE schema_meta SET value = '6' WHERE key = 'schema_version'")
    before = database.read_bytes()
    monkeypatch.setattr(cli, "load_watchlist", lambda _path: config)
    monkeypatch.setattr(cli, "run_weekly", partial(run_weekly, feed_loader=_empty_feed, now=end))
    monkeypatch.setattr(cli, "run_weekly_catch_up", partial(
        pipeline.run_weekly_catch_up, feed_loader=_empty_feed,
    ))
    dates = (["--window-start", start.isoformat(), "--window-end", end.isoformat()]
             if mode == "weekly-run" else ["--as-of", end.isoformat()])
    assert cli.main([mode, "--no-oai-cache", *dates, "--publisher-keyword", " "]) == 1
    assert database.read_bytes() == before
    assert cli.main([mode, "--no-oai-cache", "--dry-run", *dates]) == 0
    assert database.read_bytes() == before
    assert not (database.parent / "backups").exists()
    assert not reports.exists()


def test_legacy_publisher_titles_use_the_reviewed_issue_date(workspace_tmp_path: Path) -> None:
    config = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(config, storage=replace(
        config.storage, state_database=workspace_tmp_path / "state.sqlite3",
    ))
    db = workspace_tmp_path / "official-journals.sqlite3"
    post = "https://mp.weixin.qq.com/s/example-catalogue"
    identity = ("xiandai-zhexue", 2026, "5")
    record_issue(db, new_observation(
        *identity, post, "official-wechat", status="reviewed", label_month="2026-11",
        issue_published_on="2026-11-02", announcement_on="2026-11-10",
    ))
    source = workspace_tmp_path / "toc.json"
    source.write_text(json.dumps({
        "journal": identity[0], "year": identity[1], "issue": identity[2],
        "source_url": post, "label_month": "2026-11", "announcement_on": "2026-11-10",
        "complete_toc": True, "article_count": 5,
        "articles": [{"title": f"经验与解释方法{i}", "author": "作者甲"} for i in range(5)],
    }), encoding="utf-8")
    import_reviewed_toc(source, db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP TABLE publisher_article_text")
    before = db.read_bytes()
    day = datetime(2026, 11, 2, tzinfo=config.timezone)
    next_day = day + timedelta(days=1)
    match = search_publisher_papers(config, ("经验",), window=(day, next_day))
    assert match is not None and len(match.papers) == 5
    assert match.with_article_text == 0 and match.papers[0].date_evidence_urls == (post,)
    after_publication = search_publisher_papers(
        config, ("经验",), window=(next_day, next_day + timedelta(days=7)),
    )
    assert after_publication is not None and after_publication.outside_window == 5
    assert not after_publication.papers
    assert db.read_bytes() == before

    record_issue(db, new_observation(
        *identity, "https://mphilosophy.sysu.edu.cn/article/100001", "publisher-site",
        status="reviewed", issue_published_on="2026-11-08",
    ))
    historical = search_publisher_papers(config, ("经验",))
    assert historical is not None and len(historical.papers) == 5
    assert all(lead.date_conflict for lead in historical.papers)
    conflict = search_publisher_papers(config, ("经验",), window=(day, next_day))
    assert conflict is not None and conflict.missing_or_conflicting_dates == 5
    assert not conflict.papers

    # A catalogue scan whose article rows no longer belong to its snapshot is a coverage gap.
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE publisher_articles SET last_observed_at = first_observed_at || '0'")
    incomplete = search_publisher_papers(config, ("经验",))
    assert incomplete is not None and incomplete.incomplete_issues == 1
    assert incomplete.searched_articles == 0 and not incomplete.papers


def test_publisher_abstract_finds_article_without_cnki_in_all_modes(
    workspace_tmp_path: Path, monkeypatch, capsys,
) -> None:
    original = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
    config = replace(
        original,
        cnki_space=CnkiSpaceConfig(True, (), 1),
        feeds=(replace(original.feeds[0], chinese_terms=("合成专题", "比较解释")),),
        storage=replace(
            original.storage,
            state_database=workspace_tmp_path / "state.sqlite3",
            report_directory=workspace_tmp_path / "reports",
        ),
    )
    end = datetime.now(UTC).replace(day=16, hour=12, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    year, month = end.year, end.month
    db = workspace_tmp_path / "official-journals.sqlite3"
    title = "比较方法与历史解释"
    index = f'<a href="../info/1033/9876.htm">《周易研究》{year}年第3期</a>'
    issue_html = (
        f"<h2>《周易研究》{year}年第3期</h2><span>日期：{year}-{month:02d}-12</span>"
        '<div id="vsb_content"><section style="text-align: center; line-height: 2;">'
        f"<p>{title}</p><p>测试作者</p></section>"
        "<p>摘要：文章讨论合成专题并比较两种解释方法。</p><p>关键词：比较解释</p></div>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=(
            issue_html if request.url.path.endswith("9876.htm") else index
        ))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        issue = collect_publisher("zhouyi-yanjiu", db, client=client)

    def empty_cnki(terms, **kwargs):
        assert terms == (CnkiSearchTerm("合成专题", "theme"), CnkiSearchTerm("比较解释", "theme"))
        return CnkiScan((), (), kwargs["checked_at"], "success", 1, 1, (), ())

    offline = {"feed_loader": _empty_feed, "cnki_loader": empty_cnki}
    search = run_paper_search(
        config, now=end, allow_development_fixture=True, options=SearchOptions(
            year_from=year, year_to=year, publisher_terms=("比较解释",),
        ), **offline,
    )
    assert search.cnki_candidates == () and search.total_matches == 0
    assert search.publisher_papers is not None
    assert len(search.publisher_papers.papers) == 1
    assert search.publisher_papers.papers[0].matched_fields == ("keywords",)
    assert search.publisher_papers.papers[0].issue_published_on is None
    assert title in render_paper_search(search)
    out_of_year = run_paper_search(
        config, now=end, options=SearchOptions(year_to=year - 1),
        allow_development_fixture=True, **offline,
    )
    assert out_of_year.publisher_papers is not None and out_of_year.publisher_papers.papers == ()

    undated = run_on_demand(config, now=end, allow_development_fixture=True, **offline)
    assert undated.stats["publisher_article_leads"] == 0
    assert "出版时间待核 1 条" in undated.report_markdown
    prepare_path = FIXTURES.parents[1] / "scripts" / "prepare_weekly_cnki.py"
    prepare = runpy.run_path(str(prepare_path))["main"]
    monkeypatch.setitem(prepare.__globals__, "load_watchlist", lambda _path: config)
    monkeypatch.setitem(prepare.__globals__, "scan_cnki_space", empty_cnki)
    monkeypatch.setattr(sys, "argv", [str(prepare_path), "--mode", "search"])
    assert prepare() == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["state_mutated"] is False and prepared["issues"] == []
    assert prepared["publisher_date_reviews"][0]["title"] == title
    assert prepared["publisher_date_reviews"][0]["issue_url"] == issue["issue_url"]
    past_end = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setitem(
        prepare.__globals__, "latest_due_week",
        lambda **_: (past_end - timedelta(days=7), past_end),
    )
    monkeypatch.setattr(sys, "argv", [str(prepare_path)])
    assert prepare() == 0
    # A catalogue collected after the completed window can still supply evidence for it.
    assert json.loads(capsys.readouterr().out)["publisher_date_reviews"][0]["title"] == title
    review_observed_issue(db, "zhouyi-yanjiu", year, "3", issue["issue_url"],
                          label_month=f"{year}-{month:02d}")
    immutable_pub = db.read_bytes()
    pull = run_on_demand(config, now=end, allow_development_fixture=True, **offline)
    assert pull.stats["publisher_article_leads"] == 1
    assert pull.stats["cnki_recent_issue_candidates"] == 0
    assert pull.report_markdown.count(title) == 2
    assert "刊方摘要节选" in pull.report_markdown
    assert "Publisher abstract excerpt" in pull.report_markdown
    assert pull.report_markdown.index("## 刊方论文线索") < pull.report_markdown.index("## 匹配论文")
    assert not config.storage.state_database.exists()

    monkeypatch.setattr(pipeline, "require_production_taxonomy", lambda _snapshot: None)
    next_end = end + timedelta(days=7)
    with StateStore(config.storage.state_database) as state:
        establish_baseline(
            config, state, now=start - timedelta(days=1), feed_loader=_empty_feed,
            allow_development_fixture=True,
        )
        first = run_weekly(config, state, now=end, window_start=start, window_end=end, **offline)
        second = run_weekly(config, state, now=next_end, window_start=end, window_end=next_end,
                            **offline)
    assert first.stats["publisher_article_leads"] == 1
    assert first.report_markdown.count(title) == 2
    assert "刊方论文线索（1）" in first.report_markdown
    assert "Publisher article leads (1)" in first.report_markdown
    assert second.stats["publisher_article_leads"] == 0
    assert "先前周报已列 1 条" in second.report_markdown
    assert title not in second.report_markdown
    assert db.read_bytes() == immutable_pub


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
    assert "期刊官方发布渠道：已复核期次" in search_text

    pull = run_on_demand(
        config, now=now, lookback_days=30, feed_loader=feed_loader,
        cnki_loader=lambda *_args, **kwargs: cnki_scan((first,), kwargs["checked_at"]),
        allow_development_fixture=True,
    )
    assert "合成中文论文8" in pull.report_markdown
    assert "期刊官方发布渠道：本窗口首次核实的期次" in pull.report_markdown
    assert pull.stats["cnki_recent_issue_candidates"] == 1

    monkeypatch.setattr(pipeline, "require_production_taxonomy", lambda _snapshot: None)
    reviewed_at = datetime.fromisoformat(reviewed.reviewed_at).astimezone(UTC)
    first_start = reviewed_at - timedelta(days=1)
    first_end = first_start + timedelta(days=7)
    second_end = first_end + timedelta(days=7)
    second_month = (second_end - timedelta(microseconds=1)).astimezone(config.timezone)
    second = replace(
        second, year=second_month.year, label_month=second_month.month,
        records=(replace(second.records[0], year=second_month.year,
                         label_month=second_month.month),),
    )
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
    assert "期刊官方发布渠道：本周首次核实的期次" in week_one.report_markdown
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
