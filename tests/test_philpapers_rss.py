from pathlib import Path

import httpx

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.sources.philpapers_rss import (
    discover_feed_request,
    parse_feed,
    parse_feed_request,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_reconstructs_rss_request_from_live_page_shape():
    html = (FIXTURE_DIR / "philpapers_category_form.html").read_text(encoding="utf-8")

    request = parse_feed_request(html, "https://philpapers.org/browse/plato-theaetetus")

    assert request.feed_url == "https://philpapers.org/utils/feed.pl"
    assert request.category_id == "74924"
    assert request.category_slug == "plato-theaetetus"
    assert request.parameters["noheader"] == "1"
    assert request.parameters["__action"] == "/browse/plato-theaetetus"
    assert "ap_c1" not in request.parameters
    assert "ap_c2" not in request.parameters


def test_parses_feed_but_leaves_date_as_untrusted_text():
    xml = (FIXTURE_DIR / "philpapers_feed_sample.xml").read_text(encoding="utf-8")

    entries = parse_feed(xml)

    assert len(entries) == 1
    assert entries[0].source_id == "https://philpapers.org/rec/EXAMPLE"
    assert entries[0].published_text == "Sat, 05 Sep 2026 00:00:00 GMT"


def test_category_discovery_retries_one_transient_server_failure(monkeypatch):
    html = (FIXTURE_DIR / "philpapers_category_form.html").read_text(encoding="utf-8")
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = discover_feed_request(
            "https://philpapers.org/browse/plato-theaetetus",
            client=client,
        )

    assert calls == 2
    assert result.category_id == "74924"
