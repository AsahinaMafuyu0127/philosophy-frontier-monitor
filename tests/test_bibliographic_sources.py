from datetime import UTC, datetime

import httpx
import pytest

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.identity import IdentityReviewRequired
from philosophy_frontier_monitor.sources.crossref import (
    find_exact_work as find_crossref,
)
from philosophy_frontier_monitor.sources.crossref import (
    parse_crossref_work,
)
from philosophy_frontier_monitor.sources.openalex import (
    OpenAlexError,
    parse_openalex_work,
)
from philosophy_frontier_monitor.sources.openalex import (
    find_exact_work as find_openalex,
)
from philosophy_frontier_monitor.sources.openalex import (
    find_works_by_dois as find_openalex_by_dois,
)
from philosophy_frontier_monitor.sources.openalex import (
    find_works_by_titles as find_openalex_by_titles,
)
from philosophy_frontier_monitor.sources.philpapers_rss import split_display_bibliography


def test_splits_common_philpapers_feed_title():
    result = split_display_bibliography("Smith, Colin C.: Being as Common in Plato's _Theaetetus_")

    assert result.author_text == "Smith, Colin C."
    assert result.title == "Being as Common in Plato's _Theaetetus_"


def test_bibliographic_adapters_mark_malformed_type_fields_unrecognized():
    retrieved_at = datetime(2026, 9, 7, tzinfo=UTC)
    crossref = parse_crossref_work(
        {
            "DOI": "10.1234/malformed-type",
            "title": ["A Paper"],
            "type": {"unexpected": "object"},
        },
        retrieved_at=retrieved_at,
    )
    openalex = parse_openalex_work(
        {
            "id": "https://openalex.org/W-MALFORMED",
            "title": "A Paper",
            "type": ["article"],
        },
        retrieved_at=retrieved_at,
    )

    assert crossref is not None
    assert crossref.work_type == "unrecognized"
    assert openalex is not None
    assert openalex.work_type == "unrecognized"


def test_crossref_accepts_only_exact_title_and_author():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/exact",
                    "title": ["Knowledge and Belief in Plato"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "container-title": ["Example Journal"],
                    "type": "journal-article",
                    "published-online": {"date-parts": [[2026, 9, 3]]},
                },
                {
                    "DOI": "10.1234/similar",
                    "title": ["Knowledge and Belief"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                },
            ]
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_crossref(
            "Knowledge and Belief in Plato", first_author="Smith, Alice", client=client
        )

    assert result is not None
    assert result.doi == "10.1234/exact"
    assert result.publication_date is not None
    assert result.publication_date.value.isoformat() == "2026-09-03"
    assert result.publication_event == "recently_published_online"


def test_crossref_adapter_retries_transient_server_error(monkeypatch):
    calls = 0
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/retry",
                    "title": ["A Retried Paper"],
                    "author": [{"given": "Ada", "family": "Scholar"}],
                }
            ]
        }
    }

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_crossref(
            "A Retried Paper",
            first_author="Scholar, Ada",
            client=client,
        )

    assert calls == 2
    assert result is not None
    assert result.doi == "10.1234/retry"


def test_crossref_accepts_high_confidence_title_and_author_variants():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/variant",
                    "title": ["Knowledge in Plato's Theaetetus"],
                    "author": [{"given": "Maria", "family": "Garcia"}],
                    "type": "journal-article",
                    "published-online": {"date-parts": [[2026, 9, 3]]},
                }
            ]
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_crossref(
            "Knowledge in Plato's Theaitetos",
            first_author="García, María",
            client=client,
        )

    assert result is not None
    assert result.doi == "10.1234/variant"


def test_crossref_returns_earliest_equivalent_version_for_old_work_detection():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/new-journal-version",
                    "title": ["Knowledge and Belief in Plato"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "published-online": {"date-parts": [[2026, 9, 3]]},
                },
                {
                    "DOI": "10.1234/old-manuscript",
                    "title": ["Plato on Knowledge and Belief"],
                    "author": [{"given": "Alice", "family": "Smith"}],
                    "published-online": {"date-parts": [[2020, 4, 2]]},
                },
            ]
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_crossref(
            "Knowledge and Belief in Plato",
            first_author="Smith, Alice",
            client=client,
        )

    assert result is not None
    assert result.doi == "10.1234/old-manuscript"


def test_crossref_surfaces_cross_language_identity_for_semantic_review():
    payload = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/possible-translation",
                    "title": ["知识与信念"],
                    "author": [{"given": "Xiaoming", "family": "Wang"}],
                    "published-online": {"date-parts": [[2020, 4, 2]]},
                }
            ]
        }
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(IdentityReviewRequired),
    ):
        find_crossref(
            "Knowledge and Belief",
            first_author="Wang, Xiaoming",
            client=client,
        )


def test_openalex_rejects_search_rank_without_exact_title():
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1234/near",
                "title": "A Nearby but Different Paper",
                "publication_date": "2026-09-03",
                "authorships": [{"author": {"display_name": "Alice Smith"}}],
                "primary_location": {"landing_page_url": "https://example.test/near"},
            }
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex(
            "Knowledge and Belief in Plato", first_author="Smith, Alice", client=client
        )

    assert result is None


def test_openalex_surfaces_cross_language_identity_for_semantic_review():
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W-translation",
                "doi": "https://doi.org/10.1234/possible-translation",
                "title": "知识与信念",
                "publication_date": "2020-04-02",
                "authorships": [{"author": {"display_name": "Xiaoming Wang"}}],
                "primary_location": {"landing_page_url": "https://example.test/translation"},
            }
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(IdentityReviewRequired),
    ):
        find_openalex(
            "Knowledge and Belief",
            first_author="Wang, Xiaoming",
            client=client,
        )


def test_openalex_preserves_day_precision():
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W2",
                "doi": "https://doi.org/10.1234/exact",
                "title": "Knowledge and Belief in Plato",
                "publication_date": "2026-09-03",
                "authorships": [{"author": {"display_name": "Alice Smith"}}],
                "primary_location": {"landing_page_url": "https://example.test/exact"},
                "type": "article",
            }
        ]
    }

    def handler(request):
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex(
            "Knowledge and Belief in Plato", first_author="Smith, Alice", client=client
        )

    assert result is not None
    assert result.publication_date is not None
    assert result.publication_date.retrieved_at <= datetime.now(UTC)


def test_openalex_resolves_multiple_dois_with_one_or_filter_request(monkeypatch):
    payload = {
        "meta": {"count": 2},
        "results": [
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1234/one",
                "title": "First Paper",
                "publication_date": "2026-09-03",
                "authorships": [],
                "primary_location": {"landing_page_url": "https://example.test/one"},
            },
            {
                "id": "https://openalex.org/W2",
                "doi": "https://doi.org/10.1234/two",
                "title": "Second Paper",
                "publication_date": "2026-09-04",
                "authorships": [],
                "primary_location": {"landing_page_url": "https://example.test/two"},
            },
        ]
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setenv("OPENALEX_API_KEY", "fake-test-key")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex_by_dois(
            ("10.1234/two", "https://doi.org/10.1234/one"), client=client
        )

    assert len(requests) == 1
    assert requests[0].url.params["api_key"] == "fake-test-key"
    assert "10.1234/one|10.1234/two" in requests[0].url.params["filter"]
    assert set(result) == {"10.1234/one", "10.1234/two"}


def test_openalex_searches_multiple_titles_with_one_or_filter_request():
    payload = {
        "meta": {"count": 2},
        "results": [
            {
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1234/one",
                "title": "First, Paper",
                "publication_date": "2026-09-03",
                "authorships": [],
                "primary_location": {"landing_page_url": "https://example.test/one"},
            },
            {
                "id": "https://openalex.org/W2",
                "doi": "https://doi.org/10.1234/two",
                "title": "Second Paper",
                "publication_date": "2026-09-04",
                "authorships": [],
                "primary_location": {"landing_page_url": "https://example.test/two"},
            },
        ],
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex_by_titles(("First, Paper", "Second Paper"), client=client)

    assert len(requests) == 1
    assert requests[0].url.params["filter"] == ("title.search.exact:first paper|second paper")
    assert {item.openalex_id for item in result} == {
        "https://openalex.org/W1",
        "https://openalex.org/W2",
    }


def test_openalex_title_lookup_reads_later_page_even_when_all_titles_appear_first():
    requests = []

    def item(index: int, title: str):
        return {
            "id": f"https://openalex.org/W{index}",
            "doi": None,
            "title": title,
            "publication_date": "2026-09-04",
            "authorships": [],
            "primary_location": {},
        }

    def handler(request):
        requests.append(request)
        page = request.url.params.get("page", "1")
        items = (
            [
                item(index, "First Paper" if index != 2 else "Second Paper")
                for index in range(1, 101)
            ]
            if page == "1"
            else [item(101, "Second Paper")]
        )
        return httpx.Response(200, json={"meta": {"count": 101}, "results": items})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex_by_titles(("First Paper", "Second Paper"), client=client)

    assert len(requests) == 2
    assert all(
        request.url.params["filter"] == "title.search.exact:first paper|second paper"
        for request in requests
    )
    assert requests[0].url.params.get("page") is None
    assert requests[1].url.params["page"] == "2"
    assert len(result) == 101
    assert "https://openalex.org/W101" in {work.openalex_id for work in result}


def test_openalex_title_lookup_rejects_short_page_with_unread_records():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"meta": {"count": 101}, "results": []})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(OpenAlexError, match="incomplete lookup page"),
    ):
        find_openalex_by_titles(("First Paper",), client=client)
    assert len(requests) == 1


def test_openalex_title_lookup_rejects_missing_total_count():
    with (
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": []}, request=request)
        )) as client,
        pytest.raises(OpenAlexError, match="invalid response schema"),
    ):
        find_openalex_by_titles(("First Paper",), client=client)


def test_openalex_title_lookup_reports_bounded_overflow():
    requests = []

    def handler(request):
        requests.append(request)
        start = (int(request.url.params.get("page", "1")) - 1) * 100
        items = [
            {"id": f"https://openalex.org/W{index}", "title": "Common Title"}
            for index in range(start, start + 100)
        ]
        return httpx.Response(200, json={"meta": {"count": 301}, "results": items})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(OpenAlexError, match="bounded pagination limit"),
    ):
        find_openalex_by_titles(("Common Title",), client=client)
    assert len(requests) == 1


def test_openalex_doi_lookup_does_not_accept_first_page_of_duplicate_identity():
    requests = []

    def handler(request):
        requests.append(request)
        page = request.url.params.get("page", "1")
        work_id = "W1" if page == "1" else "W2"
        return httpx.Response(
            200,
            json={
                "meta": {"count": 2},
                "results": [{
                    "id": f"https://openalex.org/{work_id}",
                    "doi": "https://doi.org/10.1234/shared",
                    "title": "Shared DOI",
                }],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = find_openalex_by_dois(("10.1234/shared",), client=client)

    assert len(requests) == 2
    assert requests[1].url.params["page"] == "2"
    assert result == {}


def test_openalex_rate_limit_error_does_not_reveal_api_key(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": "rate limit"}, request=request)

    monkeypatch.setenv("OPENALEX_API_KEY", "fake-secret-that-must-not-leak")
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(OpenAlexError) as raised,
    ):
        find_openalex_by_titles(("A Test Paper",), client=client)

    assert "HTTP 429" in str(raised.value)
    assert "daily budget" in str(raised.value)
    assert "fake-secret-that-must-not-leak" not in str(raised.value)


def test_openalex_daily_budget_reset_does_not_sleep_or_retry_early(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "43200",
            },
            request=request,
        )

    monkeypatch.setattr(
        http_retry.time,
        "sleep",
        lambda _seconds: pytest.fail("long daily reset must not sleep in an interactive run"),
    )
    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(OpenAlexError) as raised,
    ):
        find_openalex_by_titles(("A Test Paper",), client=client)

    assert calls == 1
    assert "retry_after_seconds=43200" in str(raised.value)
    assert "server_wait_exceeds_run_budget" in str(raised.value)
