from datetime import UTC, date, datetime, timedelta

from philosophy_frontier_monitor.bibliographic_cache import BibliographicCache
from philosophy_frontier_monitor.models import DatePrecision, DateValue
from philosophy_frontier_monitor.sources.crossref import CrossrefWork
from philosophy_frontier_monitor.sources.openalex import OpenAlexWork

NOW = datetime(2026, 9, 9, 4, tzinfo=UTC)


def crossref_work() -> CrossrefWork:
    return CrossrefWork(
        doi="10.1234/cache",
        title="A Cached Paper",
        authors=("Ada Scholar",),
        container_title="Journal of Cache Tests",
        work_type="journal-article",
        stable_url="https://doi.org/10.1234/cache",
        publication_date=DateValue(
            date(2026, 9, 8),
            DatePrecision.DAY,
            "crossref",
            source_record_id="10.1234/cache",
            retrieved_at=NOW,
        ),
        publication_event="recently_published_online",
        raw={"discarded": "the cache stores only minimized fields"},
    )


def openalex_work(*, openalex_id: str = "https://openalex.org/W123") -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id=openalex_id,
        doi="10.1234/cache",
        title="A Cached Paper",
        authors=("Ada Scholar",),
        publication_date=DateValue(
            date(2026, 9, 8),
            DatePrecision.DAY,
            "openalex",
            source_record_id=openalex_id,
            retrieved_at=NOW,
        ),
        work_type="article",
        stable_url=openalex_id,
        raw={"discarded": "batch responses are minimized too"},
    )


def test_positive_cache_round_trip_uses_minimized_metadata(workspace_tmp_path):
    path = workspace_tmp_path / "bibliography-cache.sqlite3"
    with BibliographicCache(path) as cache:
        cache.store_crossref("A Cached Paper", "Ada Scholar", crossref_work(), now=NOW)
        lookup = cache.lookup_crossref(
            "A Cached Paper",
            "Ada Scholar",
            now=NOW + timedelta(hours=1),
        )
        row = cache.connection.execute(
            "SELECT query_hash, payload_json FROM exact_lookup_cache"
        ).fetchone()

        assert lookup.hit is True
        assert lookup.value is not None
        assert lookup.value.doi == "10.1234/cache"
        assert lookup.value.publication_date is not None
        assert lookup.value.publication_date.value == date(2026, 9, 8)
        assert lookup.value.raw == {}
        assert row["query_hash"] != "A Cached Paper"
        assert "discarded" not in row["payload_json"]


def test_negative_cache_is_short_lived(workspace_tmp_path):
    with BibliographicCache(workspace_tmp_path / "bibliography-cache.sqlite3") as cache:
        cache.store_openalex("Not Yet Indexed", "Ada Scholar", None, now=NOW)

        immediate = cache.lookup_openalex("Not Yet Indexed", "Ada Scholar", now=NOW)
        expired = cache.lookup_openalex(
            "Not Yet Indexed",
            "Ada Scholar",
            now=NOW + timedelta(minutes=16),
        )

        assert immediate.hit is True
        assert immediate.value is None
        assert expired.hit is False
        assert cache.stats.expired == 1


def test_doi_batch_cache_round_trip_and_negative_expiry(workspace_tmp_path):
    with BibliographicCache(workspace_tmp_path / "bibliography-cache.sqlite3") as cache:
        cache.store_openalex_doi("https://doi.org/10.1234/CACHE", openalex_work(), now=NOW)
        cache.store_openalex_doi("10.1234/not-indexed", None, now=NOW)

        found = cache.lookup_openalex_doi("10.1234/cache", now=NOW + timedelta(hours=1))
        absent = cache.lookup_openalex_doi("10.1234/not-indexed", now=NOW)
        expired_absent = cache.lookup_openalex_doi(
            "10.1234/not-indexed",
            now=NOW + timedelta(minutes=16),
        )

        assert found.hit is True
        assert found.value is not None
        assert found.value.openalex_id == "https://openalex.org/W123"
        assert found.value.raw == {}
        assert absent.hit is True
        assert absent.value is None
        assert expired_absent.hit is False


def test_title_batch_cache_is_positive_only_and_expires_after_one_hour(workspace_tmp_path):
    with BibliographicCache(workspace_tmp_path / "bibliography-cache.sqlite3") as cache:
        cache.store_openalex_title_candidates("A Cached Paper", (), now=NOW)
        uncached_empty = cache.lookup_openalex_title_candidates(
            "A Cached Paper",
            now=NOW,
        )

        cache.store_openalex_title_candidates(
            "A Cached Paper",
            (openalex_work(),),
            now=NOW,
        )
        found = cache.lookup_openalex_title_candidates(
            "A Cached Paper",
            now=NOW + timedelta(minutes=59),
        )
        expired = cache.lookup_openalex_title_candidates(
            "A Cached Paper",
            now=NOW + timedelta(hours=1),
        )

        assert uncached_empty.hit is False
        assert found.hit is True
        assert found.value is not None
        assert found.value[0].raw == {}
        assert expired.hit is False
