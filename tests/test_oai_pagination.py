from datetime import UTC, datetime
from pathlib import Path

import httpx

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.sources.philarchive_oai import (
    OAIProtocolError,
    iter_records,
    load_recent_window,
    parse_oai_page,
    record_key,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_oai_datestamp_stays_separate_from_dc_date():
    page = parse_oai_page((FIXTURE_DIR / "oai_page_1.xml").read_text(encoding="utf-8"))

    assert page.records[0].source_datestamp == "2026-09-04"
    assert page.records[0].fields["date"] == ("2018",)
    assert page.records[0].fields["subject"] == ("Philosophy",)
    assert page.resumption_token == "token-page-2"


def test_resumption_token_optional_attributes_are_parsed():
    page = parse_oai_page(
        """<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <responseDate>2026-09-08T00:00:00Z</responseDate>
          <request verb="ListRecords">https://philarchive.org/oai.pl</request>
          <ListRecords>
            <resumptionToken expirationDate="2026-09-08T01:00:00Z"
              completeListSize="138181" cursor="1000">opaque-token</resumptionToken>
          </ListRecords>
        </OAI-PMH>"""
    )

    assert page.resumption_token == "opaque-token"
    assert page.resumption_expiration == datetime(2026, 9, 8, 1, tzinfo=UTC)
    assert page.complete_list_size == 138181
    assert page.cursor == 1000


def test_bad_resumption_token_has_a_machine_readable_protocol_code():
    try:
        parse_oai_page(
            """<?xml version="1.0" encoding="UTF-8"?>
            <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
              <responseDate>2026-09-08T00:00:00Z</responseDate>
              <request verb="ListRecords">https://philarchive.org/oai.pl</request>
              <error code="badResumptionToken">Expired</error>
            </OAI-PMH>"""
        )
    except OAIProtocolError as error:
        assert error.code == "badResumptionToken"
    else:  # pragma: no cover - a protocol error is required
        raise AssertionError("badResumptionToken should raise OAIProtocolError")


def test_resumption_request_drops_initial_date_parameters():
    first = (FIXTURE_DIR / "oai_page_1.xml").read_text(encoding="utf-8")
    second = (FIXTURE_DIR / "oai_page_2.xml").read_text(encoding="utf-8")
    requests = []
    progress = []

    def handler(request):
        requests.append(request)
        body = second if "resumptionToken" in request.url.params else first
        return httpx.Response(200, text=body, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = tuple(
            iter_records(
                from_date="2026-09-04",
                client=client,
                page_progress=lambda pages, records: progress.append((pages, records)),
            )
        )

    assert [record.identifier for record in records] == [
        "oai:philarchive:EXAMPLE-1",
        "oai:philarchive:EXAMPLE-2",
    ]
    assert requests[0].url.params["from"] == "2026-09-04"
    assert requests[1].url.params["resumptionToken"] == "token-page-2"
    assert "from" not in requests[1].url.params
    assert "metadataPrefix" not in requests[1].url.params
    assert progress == [(1, 1), (2, 2)]


def test_oai_page_retries_transient_server_failure_without_losing_page(monkeypatch):
    final_page = (FIXTURE_DIR / "oai_page_2.xml").read_text(encoding="utf-8")
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=final_page, request=request)

    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = tuple(iter_records(from_date="2026-09-04", client=client))

    assert calls == 2
    assert [record.identifier for record in records] == ["oai:philarchive:EXAMPLE-2"]


def test_record_key_joins_philpapers_and_philarchive_identifiers():
    assert record_key("https://philpapers.org/rec/ABCDEF-2") == "abcdef-2"
    assert record_key("https://philarchive.org/rec/ABCDEF-2") == "abcdef-2"
    assert record_key("oai:philarchive.org/rec/ABCDEF-2") == "abcdef-2"


def test_recent_window_follows_all_pages_and_applies_exact_local_window():
    first = (
        (FIXTURE_DIR / "oai_page_1.xml")
        .read_text(encoding="utf-8")
        .replace("oai:philarchive:EXAMPLE-1", "oai:philarchive.org/rec/EXAMPLE-1")
    )
    second = (
        (FIXTURE_DIR / "oai_page_2.xml")
        .read_text(encoding="utf-8")
        .replace("oai:philarchive:EXAMPLE-2", "oai:philarchive.org/rec/EXAMPLE-2")
    )

    requests = []

    def handler(request):
        requests.append(request)
        body = second if "resumptionToken" in request.url.params else first
        return httpx.Response(200, text=body, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        snapshot = load_recent_window(
            datetime(2026, 9, 5, tzinfo=UTC),
            datetime(2026, 9, 6, tzinfo=UTC),
            client=client,
        )

    assert snapshot.harvested_records == 2
    assert snapshot.records_in_exact_window == 1
    assert snapshot.overlap_records_excluded == 1
    assert tuple(snapshot.records_by_key) == ("example-2",)
    assert requests[0].url.params["from"] == "2026-09-04T23:59:59Z"
    assert requests[0].url.params["until"] == "2026-09-05T23:59:59Z"


def test_no_records_match_is_a_successful_empty_page():
    page = parse_oai_page(
        """<?xml version="1.0" encoding="UTF-8"?>
        <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
          <responseDate>2026-09-05T00:00:00Z</responseDate>
          <request verb="ListRecords">https://philarchive.org/oai.pl</request>
          <error code="noRecordsMatch">No matches</error>
        </OAI-PMH>"""
    )

    assert page.records == ()
    assert page.resumption_token is None
