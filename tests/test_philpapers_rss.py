from pathlib import Path

import httpx
import pytest

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.sources.philpapers_rss import (
    PhilPapersFeedError,
    direct_feed_request,
    discover_feed_request,
    fetch_feed,
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


DIRECT_URL = (
    "https://philpapers.org/browse/plato-theaetetus/"
    "?cId=74924&catId=74924&cn=plato-theaetetus&dg=example123"
    "&format=rss&import_options=1&new=1&proOnly=on&search_inside=1&sort=cat"
)


def test_uses_official_generated_rss_without_category_page_request():
    xml = (FIXTURE_DIR / "philpapers_feed_sample.xml").read_text(encoding="utf-8")
    requested_urls = []

    def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(
            200, text=xml, headers={"content-type": "application/rss+xml"}, request=request
        )

    direct = direct_feed_request(
        "https://philpapers.org/browse/plato-theaetetus/", "74924", DIRECT_URL
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        entries = parse_feed(fetch_feed(direct, client=client))

    assert direct.parameters == {}
    assert requested_urls == [DIRECT_URL]
    assert len(entries) == 1


def test_direct_rss_stops_on_html_challenge_without_following_or_retrying():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            text="<html><title>Just a moment...</title></html>",
            headers={"content-type": "text/html", "cf-mitigated": "challenge"},
            request=request,
        )

    direct = direct_feed_request(
        "https://philpapers.org/browse/plato-theaetetus/", "74924", DIRECT_URL
    )
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(PhilPapersFeedError, match="HTTP 403"),
    ):
        fetch_feed(direct, client=client)

    assert calls == 1


@pytest.mark.parametrize(
    "url",
    [
        DIRECT_URL.replace("cId=74924", "cId=5992"),
        DIRECT_URL.replace("cn=plato-theaetetus", "cn=action-theory"),
        DIRECT_URL + "&onlineOnly=on",
        DIRECT_URL.replace("new=1", "new=0"),
        DIRECT_URL.replace("philpapers.org", "example.org"),
        DIRECT_URL.replace("/browse/plato-theaetetus/", "/browse/action-theory/"),
        DIRECT_URL.replace("philpapers.org", "philpapers.org:bad"),
    ],
)
def test_rejects_direct_rss_link_for_wrong_category_or_filtered_scope(url):
    with pytest.raises(PhilPapersFeedError):
        direct_feed_request(
            "https://philpapers.org/browse/plato-theaetetus/", "74924", url
        )


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
