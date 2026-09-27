"""Wanfang query checks distinguish corroboration, incomplete scope, and failure."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from philosophy_frontier_monitor.sources.cnki_space import CnkiSearchTerm, parse_result_page
from philosophy_frontier_monitor.sources.wanfang import (
    WanfangError,
    WanfangRecord,
    fetch_query_page,
    parse_query_response,
    publication_month,
    scan_wanfang_cnki,
    scan_wanfang_discovery,
)

FIXTURES = Path(__file__).parent / "fixtures"
RECORD = parse_result_page((FIXTURES / "cnki_space_list.html").read_text(encoding="utf-8"))[0][0]
RESPONSE = json.loads((FIXTURES / "wanfang_query.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 23, tzinfo=UTC)


def test_independent_discovery_is_bounded_sorted_and_not_cnki_dependent() -> None:
    payload = {
        "numFound": "41",
        "documents": [
            {
                "resourceType": "Periodical",
                "fields": {},
                "Periodical": {
                    "Id": "journal202609009",
                    "Title": ["一个候选"],
                    "PublishDate": "2026-09-10 00:00:00",
                    "PublishYear": 0,
                    "Issue": "09",
                    "PeriodicalTitle": ["示例期刊"],
                },
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["collections"] == ["OpenPeriodicalChi"]
        assert body["query"] == 'Title:"伦理学" AND PublishYear:[2026 TO 2026]'
        assert body["sort"]["sorts"][0] == {"by": "PublishDate", "order": "DESC"}
        assert body["rows"] == 20 and body["start"] == 0
        assert "MetadataOnlineDate" in body["returned_fields"]
        return httpx.Response(200, json=payload)

    with _client(handler) as client:
        scan = scan_wanfang_discovery(
            (CnkiSearchTerm("伦理学", "title"),),
            years=(2026,),
            max_terms=1,
            max_pages=1,
            checked_at=NOW,
            app_key="synthetic-key",
            client=client,
        )
    assert scan.status == "partial" and scan.incomplete_queries == 1
    assert scan.result_pages == 1 and len(scan.records) == 1
    assert publication_month(scan.records[0]) == (2026, 9)


def test_publication_month_rejects_placeholder_and_invalid_date() -> None:
    record = WanfangRecord("id", "title", (), None, 2026, "4", None, (), ())
    assert publication_month(record) is None
    assert publication_month(replace(record, publish_date="2026-01-01 00:00:00")) is None
    assert publication_month(replace(record, publish_date="2026-09-99")) is None
    assert publication_month(replace(record, publish_date="2026-09garbage")) is None
    assert publication_month(replace(record, publish_date="2026-09")) == (2026, 9)
    assert publication_month(replace(record, publish_date="2026年9月")) == (2026, 9)


def test_discovery_failure_is_not_an_empty_success() -> None:
    term = (CnkiSearchTerm("伦理学", "title"),)
    missing = scan_wanfang_discovery(
        term, years=(2026,), max_terms=1, max_pages=1, checked_at=NOW, app_key=""
    )
    assert missing.status == "failed" and missing.failures == ("missing_app_key",)
    with _client(lambda _: httpx.Response(200, text="not JSON")) as client:
        invalid = scan_wanfang_discovery(
            term,
            years=(2026,),
            max_terms=1,
            max_pages=1,
            checked_at=NOW,
            app_key="synthetic-key",
            client=client,
        )
    assert invalid.status == "failed" and invalid.failures == ("unexpected_content_type",)


def test_discovery_reports_terms_omitted_by_request_budget() -> None:
    with _client(lambda _: httpx.Response(200, json=RESPONSE)) as client:
        scan = scan_wanfang_discovery(
            (CnkiSearchTerm("term one"), CnkiSearchTerm("term two")),
            years=(2026,),
            max_terms=1,
            max_pages=1,
            checked_at=NOW,
            app_key="synthetic-key",
            client=client,
        )
    assert scan.status == "partial"
    assert scan.incomplete_queries == 1
    assert scan.result_pages == 1


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_query_uses_subscribed_host_collection_and_bounded_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.wfdata.com/openwanfang/getQuery"
        assert request.headers["x-ca-appkey"] == "synthetic-key"
        body = json.loads(request.content)
        assert body["collections"] == ["OpenPeriodicalChi"]
        assert body["query"] == 'Title:"概念与论证"'
        assert body["rows"] == 20 and body["start"] == 0
        assert "OriginalOrganization" in body["returned_fields"]
        assert "PublishYear" not in body["returned_fields"]
        return httpx.Response(200, json=RESPONSE)

    with _client(handler) as client:
        scan = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="synthetic-key", client=client
        )
    assert scan.status == "success"
    assert scan.checks[0].status == "corroborated"
    assert scan.checks[0].record_id == "journal202609001"
    assert scan.checks[0].original_organizations == ("示例大学哲学系",)


def test_live_ai_hub_flat_periodical_shape_and_string_count() -> None:
    # The trial endpoint returns this shape even though its displayed example
    # uses protobuf-like values in document.fields.
    payload = {
        "numFound": "1",
        "nextCursorMark": "",
        "documents": [
            {
                "resourceType": "Periodical",
                "uid": "synthetic-uid",
                "fields": {},
                "Periodical": {
                    "Id": "journal202609001",
                    "Title": ["概念与论证"],
                    "Creator": ["甲"],
                    "PeriodicalTitle": ["哲学分析"],
                    "PublishYear": 0,
                    "PublishDate": "2026-09",
                    "Issue": "09",
                    "OriginalOrganization": ["示例大学哲学系"],
                    "AuthorOrg": ["甲:示例大学哲学系"],
                },
            }
        ],
    }
    records, found = parse_query_response(payload)
    assert found == 1
    assert records[0].year == 2026
    assert records[0].authors == ("甲",)
    assert records[0].original_organizations == ("示例大学哲学系",)
    with _client(lambda _: httpx.Response(200, json=payload)) as client:
        scan = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert scan.checks[0].status == "corroborated"


def test_missing_key_and_denied_access_do_not_claim_absence() -> None:
    without_key = scan_wanfang_cnki((RECORD,), max_checks=1, checked_at=NOW, app_key="")
    assert without_key.status == "failed" and without_key.failure == "missing_app_key"

    with _client(lambda _: httpx.Response(403, json={"message": "private"})) as client:
        denied = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert denied.status == "failed" and denied.failure == "http_403"
    assert not denied.checks


def test_key_file_supplies_app_key_without_exposing_it(workspace_tmp_path: Path) -> None:
    key_file = workspace_tmp_path / "wanfang-app-key.txt"
    key_file.write_text("synthetic-file-key\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-ca-appkey"] == "synthetic-file-key"
        return httpx.Response(200, json=RESPONSE)

    with _client(handler) as client:
        result = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, key_file=key_file, client=client
        )
    assert result.status == "success"
    assert result.checks[0].status == "corroborated"

    missing = scan_wanfang_cnki(
        (RECORD,), max_checks=1, checked_at=NOW, key_file=workspace_tmp_path / "missing.txt"
    )
    assert missing.failure == "cannot_read_key_file"
    assert not missing.checks


def test_invalid_content_type_and_contract_fail_closed() -> None:
    with _client(lambda _: httpx.Response(200, text="<html>blocked</html>")) as client:
        result = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert result.failure == "unexpected_content_type"
    assert not result.checks
    try:
        parse_query_response({"documents": [], "num_found": 1})
    except WanfangError as error:
        assert str(error) == "missing_documents"
    else:
        raise AssertionError("positive count without records must fail")
    try:
        parse_query_response({"Code": "denied", "documents": [], "num_found": 0})
    except WanfangError as error:
        assert str(error) == "gateway_error"
    else:
        raise AssertionError("gateway errors must not become zero matches")


def test_paginated_query_and_cap_are_disclosed() -> None:
    starts = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = json.loads(request.content)["start"]
        starts.append(start)
        return httpx.Response(200, json={"documents": [], "num_found": 0})

    with _client(handler) as client:
        result = scan_wanfang_cnki(
            (RECORD, RECORD), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert starts == [0]
    assert result.status == "partial" and result.total_candidates == 2
    assert result.checks[0].status == "unverified"

    # A populated result set spanning more than two pages cannot be treated as complete.
    def many(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**RESPONSE, "num_found": 100})

    with _client(many) as client:
        result = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert result.status == "partial" and result.incomplete == 1


def test_metadata_conflict_is_not_corroborration() -> None:
    changed = json.loads(json.dumps(RESPONSE))
    changed["documents"][0]["fields"]["Issue"]["strValue"] = "08"
    item = parse_query_response(changed)[0][0]
    assert item.issue == "08"
    with _client(lambda _: httpx.Response(200, json=changed)) as client:
        result = scan_wanfang_cnki(
            (RECORD,), max_checks=1, checked_at=NOW, app_key="key", client=client
        )
    assert result.checks[0].status == "conflict"
    assert result.checks[0].conflicting_fields == ("issue",)
    assert not result.checks[0].original_organizations


def test_fetch_page_does_not_echo_private_query_or_key_on_error() -> None:
    with _client(lambda _: httpx.Response(429, text="secret response")) as client:
        try:
            fetch_query_page(client, "private title", "private key", start=0)
        except WanfangError as error:
            assert str(error) == "http_429"
        else:
            raise AssertionError("429 must fail")
