from pathlib import Path

import httpx

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.sources.philarchive_oai import iter_records, parse_oai_page

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_oai_datestamp_stays_separate_from_dc_date():
    page = parse_oai_page((FIXTURE_DIR / "oai_page_1.xml").read_text(encoding="utf-8"))

    assert page.records[0].source_datestamp == "2026-09-04"
    assert page.records[0].fields["date"] == ("2018",)
    assert page.records[0].fields["subject"] == ("Philosophy",)
    assert page.resumption_token == "token-page-2"


def test_resumption_request_drops_initial_date_parameters():
    first = (FIXTURE_DIR / "oai_page_1.xml").read_text(encoding="utf-8")
    second = (FIXTURE_DIR / "oai_page_2.xml").read_text(encoding="utf-8")
    requests = []

    def handler(request):
        requests.append(request)
        body = second if "resumptionToken" in request.url.params else first
        return httpx.Response(200, text=body, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = tuple(iter_records(from_date="2026-09-04", client=client))

    assert [record.identifier for record in records] == [
        "oai:philarchive:EXAMPLE-1",
        "oai:philarchive:EXAMPLE-2",
    ]
    assert requests[0].url.params["from"] == "2026-09-04"
    assert requests[1].url.params["resumptionToken"] == "token-page-2"
    assert "from" not in requests[1].url.params
    assert "metadataPrefix" not in requests[1].url.params


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
