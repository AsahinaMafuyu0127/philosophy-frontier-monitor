"""Contract tests for bounded public CNKI Space metadata discovery."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from philosophy_frontier_monitor.sources.cnki_space import (
    CnkiSearchTerm,
    CnkiSpaceError,
    fetch_result_page,
    parse_result_page,
    scan_cnki_space,
)
from philosophy_frontier_monitor.state import StateStore

FIXTURE = Path(__file__).parent / "fixtures" / "cnki_space_list.html"
HTML = FIXTURE.read_text(encoding="utf-8")
NOW = datetime(2026, 9, 23, tzinfo=UTC)


def test_issue_number_is_not_invented_as_month() -> None:
    records, pages = parse_result_page(HTML)
    assert pages == 2
    assert len(records) == 2
    assert records[0].title == "概念与论证"
    assert records[0].venue == "哲学分析"
    assert records[0].authors == ("甲",)
    assert (records[0].year, records[0].issue, records[0].label_month) == (2026, "9", None)
    altered_badge = HTML.replace("CNKI阅读", "CNKI文献", 1)
    altered_records, _ = parse_result_page(altered_badge)
    assert altered_records[0].title == "概念与论证"


def test_month_comes_only_from_issue_label() -> None:
    with_other_date = HTML.replace("<span>期刊</span>", "<span>2026年8月1日 期刊</span>", 1)
    records, _ = parse_result_page(with_other_date)
    assert (records[0].issue, records[0].label_month) == ("9", None)

    month_only = HTML.replace("2026年09期", "2026年9月", 1)
    records, _ = parse_result_page(month_only)
    assert (records[0].year, records[0].issue, records[0].label_month) == (
        2026,
        "9月",
        9,
    )


def test_issue_month_can_be_supported_by_a_later_record_in_same_issue() -> None:
    html = HTML.replace(
        'ZXFX202609002"><span>2026年09期',
        'ZXFX202609002"><span>2026年09期（2026年9月）',
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_cnki_space(
            (CnkiSearchTerm("哲学"),),
            years=(2026,),
            max_pages=1,
            checked_at=NOW,
            client=client,
        )

    assert len(scan.issues) == 1
    assert scan.issues[0].label_month == 9


@pytest.mark.parametrize(
    "html,reason",
    [
        ("<h1>安全验证</h1>", "blocked"),
        ("<div class='list-item'>broken</div>", "pagination_missing"),
        ('<script>$("#hidTotalPageCount").val("1");</script>', "records_missing"),
    ],
)
def test_error_and_challenge_pages_never_mean_zero_results(html: str, reason: str) -> None:
    with pytest.raises(CnkiSpaceError, match=reason):
        parse_result_page(html)


def test_fetch_rejects_wrong_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="warmup", headers={"content-type": "text/html"})
        return httpx.Response(200, json={"records": []})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CnkiSpaceError, match="unexpected_content_type"),
    ):
        fetch_result_page(client, CnkiSearchTerm("哲学"), year=2026, page=1)


def test_pagination_failure_preserves_observed_first_page_without_claiming_completion() -> None:
    pages_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="warmup", headers={"content-type": "text/html"})
        page = int(dict(httpx.QueryParams(request.content.decode()))["Page"])
        pages_seen.append(page)
        if page == 2:
            return httpx.Response(403, text="blocked", headers={"content-type": "text/html"})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_cnki_space(
            (CnkiSearchTerm("哲学"),),
            years=(2026,),
            max_pages=2,
            checked_at=NOW,
            client=client,
        )
    assert pages_seen == [1, 2]
    assert scan.status == "partial"
    assert scan.result_pages == 1
    assert len(scan.records) == 2
    assert len(scan.issues) == 1
    assert scan.failures == ("title:2026:2:blocked",)


def test_bounded_scan_reports_truncated_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_cnki_space(
            (CnkiSearchTerm("哲学"),),
            years=(2026,),
            max_pages=1,
            checked_at=NOW,
            client=client,
        )
    assert scan.status == "partial"
    assert scan.incomplete_queries == ("title:2026:page_limit",)
    assert scan.issues[0].records[0].url.startswith("https://www.cnki.com.cn/Article/")


def test_issue_checkpoint_and_first_seen_commit_atomically() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        scan = scan_cnki_space(
            (CnkiSearchTerm("哲学"),),
            years=(2026,),
            max_pages=1,
            checked_at=NOW,
            client=client,
        )
    issue = scan.issues[0]

    def commit(store: StateStore, issue_key: str, run_id: str) -> None:
        store.commit_weekly_run(
            run_id=run_id,
            window_start=datetime(2026, 9, 14, tzinfo=UTC),
            window_end=datetime(2026, 9, 21, tzinfo=UTC),
            started_at=NOW,
            completed_at=NOW,
            report_path="report.md",
            stats={},
            taxonomy_snapshot_id="taxonomy:test",
            interest_profile_id="interest:test",
            interest_profile_version=1,
            pipeline_version="0.6.0",
            matching_rule_version="set_intersection_v1",
            feeds=(),
            observations=(),
            outcomes=(),
            unresolved=(),
            notifications=(),
            cnki_issues=(replace(issue, key=issue_key),),
            cnki_scan_completed=True,
        )

    with StateStore(":memory:") as store:
        assert not store.known_cnki_issue_keys({issue.key})
        commit(store, issue.key, "run-one")
        assert store.known_cnki_issue_keys({issue.key}) == {issue.key}
        checkpoint = store.get_checkpoint("cnki-space-issues")
        assert checkpoint is not None
        with pytest.raises(sqlite3.IntegrityError):
            commit(store, "rollback-key", "run-one")
        assert not store.known_cnki_issue_keys({"rollback-key"})
        assert store.get_checkpoint("cnki-space-issues") == checkpoint
