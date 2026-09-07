from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from philosophy_frontier_monitor import pipeline
from philosophy_frontier_monitor.bibliographic_cache import BibliographicCache
from philosophy_frontier_monitor.cli import (
    _deliver_on_demand_report,
    _emit_pull_now_progress,
    build_parser,
)
from philosophy_frontier_monitor.config import FeedConfig, load_watchlist
from philosophy_frontier_monitor.models import (
    CategoryAssignment,
    CategoryStatus,
    DatePrecision,
    DateValue,
    FreshnessStatus,
    WorkRecord,
)
from philosophy_frontier_monitor.pipeline import (
    FeedSnapshot,
    OnDemandRunResult,
    PipelineError,
    ResolutionResult,
    on_demand_window,
    run_on_demand,
)
from philosophy_frontier_monitor.sources.crossref import CrossrefWork
from philosophy_frontier_monitor.sources.openalex import OpenAlexWork
from philosophy_frontier_monitor.sources.philpapers_rss import FeedEntry, FeedRequest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CONFIG = load_watchlist(FIXTURE_DIR / "watchlist_minimal.yaml")
REQUEST_TIME = datetime(2026, 9, 9, 4, tzinfo=UTC)


def entry(
    record_id: str,
    *,
    published_text: str | None,
    description: str = "untrusted and unused",
) -> FeedEntry:
    return FeedEntry(
        source_id=f"https://philpapers.org/rec/{record_id}",
        title=f"Scholar, Ada: Paper {record_id}",
        link=f"https://philpapers.org/rec/{record_id}",
        description=description,
        published_text=published_text,
    )


def loader_with(*entries: FeedEntry):
    def loader(feed, checked_at):
        return FeedSnapshot(
            feed_key=feed.feed_key,
            category_id=feed.category_id,
            category_url=feed.url,
            checked_at=checked_at,
            content_hash="sha256:on-demand-fixture",
            entries=entries,
        )

    return loader


def resolver_for(publication_dates: dict[str, date], calls: list[str]):
    def resolver(candidate, taxonomy, _config, start, end, attempted_at):
        record_id = candidate.source_id.rsplit("/", 1)[-1]
        calls.append(record_id)
        publication_date = DateValue(
            publication_dates[record_id],
            DatePrecision.DAY,
            source="crossref",
        )
        is_new = (
            start
            <= datetime.combine(publication_dates[record_id], datetime.min.time(), tzinfo=UTC)
            < end
        )
        assignment = CategoryAssignment(
            category_id="74924",
            category_name=taxonomy.categories["74924"].category_name,
            assignment_source="philpapers-category-feed",
            retrieved_at=attempted_at,
            source_record_id=candidate.source_id,
            mapping_method="category_feed_membership",
        )
        work = WorkRecord(
            work_id=f"pfm:work:doi:10.1234/{record_id.casefold()}",
            title=f"Paper {record_id}",
            authors=("Ada Scholar",),
            observed_at=candidate.observed_at,
            freshness_status=(
                FreshnessStatus.CONFIRMED_NEW if is_new else FreshnessStatus.NEWLY_INDEXED_OLD_WORK
            ),
            category_status=CategoryStatus.AVAILABLE,
            category_assignments=(assignment,),
            doi=f"10.1234/{record_id.casefold()}",
            source_ids=((candidate.source, candidate.source_id),),
            work_type="journal-article",
            publication_date=publication_date,
            freshness_event="recently_published_online",
            container_title="Journal of On-Demand Tests",
            stable_url=f"https://doi.org/10.1234/{record_id.casefold()}",
        )
        return ResolutionResult(candidate, work)

    return resolver


def no_title_batch(_titles, _config, _attempted_at):
    return ()


class FakeNetworkClient:
    def __init__(self, ordinal: int):
        self.ordinal = ordinal

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


def test_default_feed_loading_reuses_one_philpapers_connection_pool(monkeypatch):
    second_feed = FeedConfig(
        category_id="74844",
        category_name="Plato: Knowledge and Belief",
        url="https://philpapers.org/browse/plato-knowledge-and-belief",
    )
    config = replace(CONFIG, feeds=(*CONFIG.feeds, second_feed))
    created_clients: list[FakeNetworkClient] = []
    observed_clients: list[FakeNetworkClient] = []

    def client_factory(*_args, **_kwargs):
        client = FakeNetworkClient(len(created_clients) + 1)
        created_clients.append(client)
        return client

    def discover(url, *, client=None):
        observed_clients.append(client)
        category_id = "74844" if "knowledge-and-belief" in url else "74924"
        return FeedRequest(
            category_page_url=url,
            feed_url="https://philpapers.org/utils/feed.pl",
            method="GET",
            category_id=category_id,
            category_slug=url.rsplit("/", 1)[-1],
            parameters={"cId": category_id},
        )

    def fetch(_request, *, client=None):
        observed_clients.append(client)
        return (
            "<rss><channel><item><guid>https://philpapers.org/rec/POOLED</guid>"
            "<title>Scholar, Ada: Pooled Paper</title>"
            "<link>https://philpapers.org/rec/POOLED</link></item></channel></rss>"
        )

    monkeypatch.setattr(pipeline.httpx, "Client", client_factory)
    monkeypatch.setattr(pipeline, "discover_feed_request", discover)
    monkeypatch.setattr(pipeline, "fetch_feed", fetch)

    snapshots = pipeline._load_all_feeds(
        config,
        checked_at=REQUEST_TIME,
        loader=pipeline.load_philpapers_feed,
    )

    assert len(snapshots) == 2
    assert len(created_clients) == 1
    assert observed_clients == [created_clients[0]] * 4


def test_default_fallback_reuses_crossref_and_openalex_connection_pools(monkeypatch):
    created_clients: list[FakeNetworkClient] = []
    crossref_clients: list[FakeNetworkClient] = []
    openalex_clients: list[FakeNetworkClient] = []

    def client_factory(*_args, **_kwargs):
        client = FakeNetworkClient(len(created_clients) + 1)
        created_clients.append(client)
        return client

    def no_crossref(*_args, client=None, **_kwargs):
        crossref_clients.append(client)
        return None

    def no_openalex(*_args, client=None, **_kwargs):
        openalex_clients.append(client)
        return None

    monkeypatch.setattr(pipeline.httpx, "Client", client_factory)
    monkeypatch.setattr(pipeline, "find_crossref_work", no_crossref)
    monkeypatch.setattr(pipeline, "find_openalex_work", no_openalex)

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry("POOL-A", published_text=None),
            entry("POOL-B", published_text=None),
        ),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert result.stats["fallback_queried"] == 2
    assert len(created_clients) == 2
    assert crossref_clients == [created_clients[0]] * 2
    assert openalex_clients == [created_clients[1]] * 2


def test_repeated_pull_reuses_private_bibliographic_cache_without_suppressing_report(
    monkeypatch,
):
    calls: list[str] = []
    crossref = CrossrefWork(
        doi="10.1234/repeated-cache",
        title="Paper REPEATED-CACHE",
        authors=("Ada Scholar",),
        container_title="Journal of Cache Tests",
        work_type="journal-article",
        stable_url="https://doi.org/10.1234/repeated-cache",
        publication_date=DateValue(date(2026, 9, 8), DatePrecision.DAY, "crossref"),
        publication_event="recently_published_online",
        raw={},
    )

    def crossref_lookup(*_args, **_kwargs):
        calls.append("crossref")
        return crossref

    monkeypatch.setattr(pipeline, "find_crossref_work", crossref_lookup)

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(entry("REPEATED-CACHE", published_text=None)),
            "batch_title_resolver": no_title_batch,
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        second = run_on_demand(CONFIG, **kwargs)

    assert calls == ["crossref"]
    assert first.stats["bibliography_cache_hits"] == 0
    assert first.stats["bibliography_cache_writes"] == 1
    assert second.stats["bibliography_cache_hits"] == 1
    assert second.stats["bibliography_cache_writes"] == 0
    assert first.stats["matched"] == 1
    assert second.stats["matched"] == 1
    assert "Paper REPEATED-CACHE" in first.report_markdown
    assert "Paper REPEATED-CACHE" in second.report_markdown


def test_transport_failures_are_never_cached(monkeypatch):
    crossref_calls: list[str] = []
    openalex_calls: list[str] = []

    def crossref_failure(*_args, **_kwargs):
        crossref_calls.append("failure")
        raise pipeline.CrossrefError("offline")

    def openalex_failure(*_args, **_kwargs):
        openalex_calls.append("failure")
        raise pipeline.OpenAlexError("offline")

    monkeypatch.setattr(pipeline, "find_crossref_work", crossref_failure)
    monkeypatch.setattr(pipeline, "find_openalex_work", openalex_failure)

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(entry("FAILED-CACHE", published_text=None)),
            "batch_title_resolver": no_title_batch,
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        second = run_on_demand(CONFIG, **kwargs)

    assert crossref_calls == ["failure", "failure"]
    assert openalex_calls == ["failure", "failure"]
    assert first.stats["bibliography_cache_writes"] == 0
    assert second.stats["bibliography_cache_writes"] == 0
    assert first.stats["unresolved"] == 1
    assert second.stats["unresolved"] == 1


def test_bibliographic_failure_opens_one_run_circuit_and_avoids_request_storm(monkeypatch):
    crossref_calls: list[str] = []
    openalex_calls: list[str] = []

    def crossref_failure(*_args, **_kwargs):
        crossref_calls.append("failure")
        raise pipeline.CrossrefError("Crossref bounded retries exhausted")

    def openalex_failure(*_args, **_kwargs):
        openalex_calls.append("failure")
        raise pipeline.OpenAlexError("OpenAlex daily budget exhausted")

    monkeypatch.setattr(pipeline, "find_crossref_work", crossref_failure)
    monkeypatch.setattr(pipeline, "find_openalex_work", openalex_failure)

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry("CIRCUIT-A", published_text=None),
            entry("CIRCUIT-B", published_text=None),
        ),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert crossref_calls == ["failure"]
    assert openalex_calls == ["failure"]
    assert result.stats["bibliographic_source_circuits_open"] == 2
    assert result.stats["bibliographic_source_circuit_skips"] == 2
    assert result.stats["unresolved"] == 2
    assert "crossref-bibliography" in result.report_markdown
    assert "openalex-bibliography" in result.report_markdown


def test_on_demand_window_is_rolling_seven_local_days():
    start, end = on_demand_window(CONFIG, now=REQUEST_TIME)

    assert start == datetime(2026, 9, 2, 4, tzinfo=UTC)
    assert end == REQUEST_TIME


def test_on_demand_window_preserves_local_time_across_daylight_saving():
    new_york_config = replace(CONFIG, timezone=ZoneInfo("America/New_York"))
    start, end = on_demand_window(
        new_york_config,
        now=datetime(2026, 3, 9, 13, tzinfo=UTC),
    )

    assert start == datetime(2026, 3, 2, 14, tzinfo=UTC)
    assert end == datetime(2026, 3, 9, 13, tzinfo=UTC)
    assert (end - start).total_seconds() == 167 * 60 * 60


def test_on_demand_rejects_unbounded_lookback():
    with pytest.raises(PipelineError, match="between 1 and 31"):
        on_demand_window(CONFIG, now=REQUEST_TIME, lookback_days=32)


def test_on_demand_uses_feed_dates_only_as_candidate_gate_and_has_no_baseline():
    calls: list[str] = []
    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry("OLD-FEED", published_text="Sat, 01 Aug 2026 00:00:00 GMT"),
            entry("RECENT", published_text="Tue, 08 Sep 2026 00:00:00 GMT"),
            entry("UNDATED", published_text=None),
        ),
        resolver=resolver_for(
            {
                "RECENT": date(2026, 9, 8),
                "UNDATED": date(2026, 9, 7),
            },
            calls,
        ),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == ["RECENT", "UNDATED"]
    assert result.stats["unique_current_records"] == 3
    assert result.stats["candidate_records"] == 2
    assert result.stats["candidate_records_without_feed_timestamp"] == 1
    assert result.stats["candidate_records_without_any_feed_date_hint"] == 1
    assert result.stats["skipped_by_feed_hint"] == 1
    assert result.stats["matched"] == 2
    assert "Paper RECENT" in result.report_markdown
    assert "Paper UNDATED" in result.report_markdown
    assert "独立于正式周报" in result.report_markdown
    assert "再次出现在周报中属于正常现象" in result.report_markdown


def test_on_demand_rechecks_old_reclassification_and_does_not_emit_it():
    calls: list[str] = []
    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry(
                "RECLASSIFIED",
                published_text="Tue, 08 Sep 2026 00:00:00 GMT",
                description="_Old Journal_ 4 (1):1-20. 2020Abstract begins here.",
            )
        ),
        resolver=resolver_for({"RECLASSIFIED": date(2020, 1, 2)}, calls),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == []
    assert result.stats["confirmed_new"] == 0
    assert result.stats["matched"] == 0
    assert "Paper RECLASSIFIED" not in result.report_markdown


def test_on_demand_uses_description_year_only_as_a_broad_candidate_hint():
    calls: list[str] = []
    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry(
                "CURRENT-YEAR",
                published_text=None,
                description="_Test Journal_ 12 (2):1-20. 2026Abstract begins here.",
            ),
            entry(
                "OLDER-YEAR",
                published_text=None,
                description="_Test Journal_ 4 (1):1-20. 2020Abstract begins here.",
            ),
        ),
        resolver=resolver_for({"CURRENT-YEAR": date(2026, 9, 8)}, calls),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == ["CURRENT-YEAR"]
    assert result.stats["candidate_records"] == 1
    assert result.stats["skipped_by_feed_hint"] == 1
    assert result.stats["candidate_records_without_feed_timestamp"] == 1
    assert result.stats["candidate_records_without_any_feed_date_hint"] == 0


def test_repeated_on_demand_requests_may_return_the_same_paper():
    calls: list[str] = []
    kwargs = {
        "now": REQUEST_TIME,
        "feed_loader": loader_with(entry("REPEAT", published_text="Tue, 08 Sep 2026 00:00:00 GMT")),
        "resolver": resolver_for({"REPEAT": date(2026, 9, 8)}, calls),
        "batch_title_resolver": no_title_batch,
        "allow_development_fixture": True,
    }

    first = run_on_demand(CONFIG, **kwargs)
    second = run_on_demand(CONFIG, **kwargs)

    assert first.request_id != second.request_id
    assert first.stats["matched"] == second.stats["matched"] == 1
    assert "Paper REPEAT" in first.report_markdown
    assert "Paper REPEAT" in second.report_markdown
    assert calls == ["REPEAT", "REPEAT"]


def test_on_demand_resolves_description_doi_hints_in_one_batch():
    batch_calls: list[tuple[str, ...]] = []

    def batch_resolver(dois, _config, attempted_at):
        batch_calls.append(dois)
        return {
            "10.1234/batch": OpenAlexWork(
                openalex_id="https://openalex.org/W123",
                doi="10.1234/batch",
                title="Paper BATCH",
                authors=("Ada Scholar",),
                publication_date=DateValue(
                    date(2026, 9, 8),
                    DatePrecision.DAY,
                    source="openalex",
                    retrieved_at=attempted_at,
                ),
                work_type="article",
                stable_url="https://doi.org/10.1234/batch",
                raw={},
            )
        }

    def no_individual_lookups(*_args):
        raise AssertionError("DOI batch result should avoid an individual title lookup")

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry(
                "BATCH",
                published_text=None,
                description=(
                    "_Test Journal_ 2 (1):1-10. 2026"
                    '<a href="https://philpapers.org/go.pl?u='
                    'https%3A%2F%2Fdx.doi.org%2F10.1234%2FBATCH">direct</a>'
                ),
            )
        ),
        resolver=no_individual_lookups,
        batch_doi_resolver=batch_resolver,
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert batch_calls == [("10.1234/batch",)]
    assert result.stats["doi_hints"] == 1
    assert result.stats["batch_resolved_candidates"] == 1
    assert result.stats["fallback_candidates"] == 0
    assert result.stats["matched"] == 1
    assert "Paper BATCH" in result.report_markdown


def test_repeated_pull_uses_per_doi_batch_cache_without_suppressing_paper():
    batch_calls: list[tuple[str, ...]] = []

    def batch_resolver(dois, _config, attempted_at):
        batch_calls.append(dois)
        return {
            "10.1234/batch-cache": OpenAlexWork(
                openalex_id="https://openalex.org/W-BATCH-CACHE",
                doi="10.1234/batch-cache",
                title="Paper BATCH-CACHE",
                authors=("Ada Scholar",),
                publication_date=DateValue(
                    date(2026, 9, 8),
                    DatePrecision.DAY,
                    source="openalex",
                    retrieved_at=attempted_at,
                ),
                work_type="article",
                stable_url="https://doi.org/10.1234/batch-cache",
                raw={},
            )
        }

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(
                entry(
                    "BATCH-CACHE",
                    published_text=None,
                    description="2026 https://doi.org/10.1234/BATCH-CACHE",
                )
            ),
            "batch_doi_resolver": batch_resolver,
            "batch_title_resolver": no_title_batch,
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        second = run_on_demand(CONFIG, **kwargs)

    assert batch_calls == [("10.1234/batch-cache",)]
    assert first.stats["doi_batch_api_values"] == 1
    assert first.stats["doi_batch_cache_hits"] == 0
    assert second.stats["doi_batch_api_values"] == 0
    assert second.stats["doi_batch_cache_hits"] == 1
    assert first.stats["matched"] == second.stats["matched"] == 1
    assert "Paper BATCH-CACHE" in second.report_markdown


def test_doi_batch_transport_failure_is_not_cached():
    calls: list[tuple[str, ...]] = []
    fallback_calls: list[str] = []

    def failed_batch(dois, _config, _attempted_at):
        calls.append(dois)
        raise pipeline.OpenAlexError("temporary failure")

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(
                entry(
                    "DOI-FAIL",
                    published_text=None,
                    description="2026 https://doi.org/10.1234/DOI-FAIL",
                )
            ),
            "batch_doi_resolver": failed_batch,
            "resolver": resolver_for({"DOI-FAIL": date(2026, 9, 8)}, fallback_calls),
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        second = run_on_demand(CONFIG, **kwargs)
        row_count = bibliography_cache.connection.execute(
            "SELECT count(*) FROM exact_lookup_cache"
        ).fetchone()[0]

    assert calls == [("10.1234/doi-fail",), ("10.1234/doi-fail",)]
    assert fallback_calls == ["DOI-FAIL", "DOI-FAIL"]
    assert first.stats["openalex_batch_failures"] == 1
    assert second.stats["openalex_batch_failures"] == 1
    assert first.stats["title_batch_api_values"] == 0
    assert second.stats["title_batch_api_values"] == 0
    assert row_count == 0


def test_on_demand_deduplicates_the_same_work_within_one_report():
    calls: list[str] = []
    underlying = resolver_for({"COPY-A": date(2026, 9, 8), "COPY-B": date(2026, 9, 8)}, calls)

    def same_work_resolver(*args):
        result = underlying(*args)
        assert result.work is not None
        return replace(
            result,
            work=replace(
                result.work,
                work_id="pfm:work:doi:10.1234/same",
                doi="10.1234/same",
                stable_url="https://doi.org/10.1234/same",
            ),
        )

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry("COPY-A", published_text=None),
            entry("COPY-B", published_text=None),
        ),
        resolver=same_work_resolver,
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == ["COPY-A", "COPY-B"]
    assert result.stats["resolved_works"] == 1
    assert result.stats["matched"] == 1


def test_on_demand_candidate_limit_stops_before_resolution():
    calls: list[str] = []
    with pytest.raises(PipelineError, match="safety limit"):
        run_on_demand(
            CONFIG,
            now=REQUEST_TIME,
            max_candidates=1,
            feed_loader=loader_with(
                entry("ONE", published_text=None),
                entry("TWO", published_text=None),
            ),
            resolver=resolver_for({"ONE": date(2026, 9, 8), "TWO": date(2026, 9, 8)}, calls),
            batch_title_resolver=no_title_batch,
            allow_development_fixture=True,
        )

    assert calls == []


def test_on_demand_fallback_limit_defers_excess_without_failing_the_report():
    calls: list[str] = []
    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        max_fallback_candidates=1,
        feed_loader=loader_with(
            entry("ONE", published_text=None),
            entry("TWO", published_text=None),
        ),
        resolver=resolver_for({"ONE": date(2026, 9, 8), "TWO": date(2026, 9, 8)}, calls),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == ["ONE"]
    assert result.stats["fallback_queried"] == 1
    assert result.stats["fallback_deferred"] == 1
    assert result.stats["unresolved"] == 1


def test_on_demand_fallback_budget_prioritizes_recent_and_early_work_evidence():
    calls: list[str] = []
    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        max_fallback_candidates=1,
        feed_loader=loader_with(
            entry("A-WEAK", published_text=None),
            entry(
                "Z-EARLY",
                published_text=None,
                description="Working paper. 2026 draft.",
            ),
        ),
        resolver=resolver_for(
            {"A-WEAK": date(2026, 9, 8), "Z-EARLY": date(2026, 9, 8)},
            calls,
        ),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert calls == ["Z-EARLY"]
    assert result.stats["fallback_deferred"] == 1


def test_on_demand_resolves_candidates_with_batched_title_and_author_match():
    title_calls: list[tuple[str, ...]] = []

    def title_batch(titles, _config, attempted_at):
        title_calls.append(titles)
        return (
            OpenAlexWork(
                openalex_id="https://openalex.org/W-TITLE",
                doi="10.1234/title",
                title="Paper TITLE",
                authors=("Ada Scholar",),
                publication_date=DateValue(
                    date(2026, 9, 8),
                    DatePrecision.DAY,
                    source="openalex",
                    retrieved_at=attempted_at,
                ),
                work_type="article",
                stable_url="https://doi.org/10.1234/title",
                raw={},
            ),
        )

    def no_fallback(*_args):
        raise AssertionError("title batch should avoid fallback lookup")

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry(
                "TITLE",
                published_text=None,
                description="_Test Journal_ 2 (1):1-10. 2026No DOI here.",
            )
        ),
        resolver=no_fallback,
        batch_title_resolver=title_batch,
        allow_development_fixture=True,
    )

    assert title_calls == [("Paper TITLE",)]
    assert result.stats["title_batch_resolved_candidates"] == 1
    assert result.stats["fallback_candidates"] == 0
    assert result.stats["matched"] == 1


def test_repeated_pull_uses_positive_per_title_batch_cache():
    title_calls: list[tuple[str, ...]] = []

    def title_batch(titles, _config, attempted_at):
        title_calls.append(titles)
        return (
            OpenAlexWork(
                openalex_id="https://openalex.org/W-TITLE-CACHE",
                doi="10.1234/title-cache",
                title="Paper TITLE-CACHE",
                authors=("Ada Scholar",),
                publication_date=DateValue(
                    date(2026, 9, 8),
                    DatePrecision.DAY,
                    source="openalex",
                    retrieved_at=attempted_at,
                ),
                work_type="article",
                stable_url="https://doi.org/10.1234/title-cache",
                raw={},
            ),
        )

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(entry("TITLE-CACHE", published_text=None)),
            "batch_title_resolver": title_batch,
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        second = run_on_demand(CONFIG, **kwargs)

    assert title_calls == [("Paper TITLE-CACHE",)]
    assert first.stats["title_batch_api_values"] == 1
    assert first.stats["title_batch_cache_hits"] == 0
    assert second.stats["title_batch_api_values"] == 0
    assert second.stats["title_batch_cache_hits"] == 1
    assert first.stats["matched"] == second.stats["matched"] == 1


def test_title_batch_transport_failure_and_empty_result_are_not_cached():
    calls: list[tuple[str, ...]] = []
    fallback_calls: list[str] = []

    def failed_title_batch(titles, _config, _attempted_at):
        calls.append(titles)
        if len(calls) == 1:
            raise pipeline.OpenAlexError("temporary title failure")
        return ()

    with BibliographicCache(":memory:") as bibliography_cache:
        kwargs = {
            "now": REQUEST_TIME,
            "feed_loader": loader_with(entry("TITLE-FAIL", published_text=None)),
            "batch_title_resolver": failed_title_batch,
            "resolver": resolver_for({"TITLE-FAIL": date(2026, 9, 8)}, fallback_calls),
            "bibliography_cache": bibliography_cache,
            "allow_development_fixture": True,
        }
        first = run_on_demand(CONFIG, **kwargs)
        run_on_demand(CONFIG, **kwargs)
        run_on_demand(CONFIG, **kwargs)

    assert calls == [
        ("Paper TITLE-FAIL",),
        ("Paper TITLE-FAIL",),
        ("Paper TITLE-FAIL",),
    ]
    assert fallback_calls == ["TITLE-FAIL", "TITLE-FAIL", "TITLE-FAIL"]
    assert first.stats["openalex_batch_failures"] == 1


def test_on_demand_reports_count_only_progress_without_titles():
    events: list[tuple[str, int, int]] = []

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry(
                "PROGRESS-SECRET-TITLE",
                published_text=None,
            )
        ),
        resolver=resolver_for({"PROGRESS-SECRET-TITLE": date(2026, 9, 8)}, []),
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
        progress=lambda stage, completed, total: events.append((stage, completed, total)),
    )

    assert result.stats["matched"] == 1
    assert ("feeds", 0, 1) in events
    assert ("feeds", 1, 1) in events
    assert ("candidates", 1, 1) in events
    assert ("fallback", 1, 1) in events
    assert events[-1] == ("complete", 1, 1)
    assert "PROGRESS-SECRET-TITLE" not in repr(events)


@pytest.mark.parametrize(
    ("description", "expected_type"),
    [
        ("Preprint. Draft shared for comments.", "preprint"),
        ("Working paper. Please cite the latest version.", "working-paper"),
        ("Unpublished manuscript.", "submitted-manuscript"),
        ("Forthcoming in Test Journal.", "forthcoming-article"),
    ],
)
def test_on_demand_pushes_new_early_work_without_waiting_for_external_indexing(
    description,
    expected_type,
):
    def philpapers_native(candidate, taxonomy, _config, start, end, attempted_at):
        return pipeline._resolve_philpapers_arrival(candidate, taxonomy, start, end, attempted_at)

    result = run_on_demand(
        CONFIG,
        now=REQUEST_TIME,
        feed_loader=loader_with(
            entry("EARLY", published_text=None, description=description),
        ),
        resolver=philpapers_native,
        batch_title_resolver=no_title_batch,
        allow_development_fixture=True,
    )

    assert result.stats["matched"] == 1
    assert result.stats["philpapers_native_resolved_candidates"] == 1
    assert expected_type in result.report_markdown
    assert "recently_arrived_in_philpapers_alert" in result.report_markdown
    assert "philpapers-rss-current-alert-observation" in result.report_markdown


def test_cli_progress_uses_stderr_and_keeps_stdout_clean(capsys):
    _emit_pull_now_progress("fallback", 20, 266)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "逐篇退回核验" in captured.err
    assert "20/266" in captured.err


def test_cli_exposes_pull_now_as_a_distinct_command():
    args = build_parser().parse_args(["pull-now", "--days", "7"])
    file_args = build_parser().parse_args(["pull-now", "--days", "7", "--report-delivery", "file"])

    assert args.command == "pull-now"
    assert args.days == 7
    assert args.max_candidates == 1000
    assert args.max_fallback_candidates == 50
    assert args.no_bibliography_cache is False
    assert args.report_delivery == "inline"
    assert file_args.report_delivery == "file"


def test_cli_file_delivery_writes_full_private_report_and_returns_no_markdown(
    workspace_tmp_path,
):
    config = replace(
        CONFIG,
        storage=replace(CONFIG.storage, report_directory=workspace_tmp_path / "reports"),
    )
    result = OnDemandRunResult(
        request_id="pfm:on-demand:12345678",
        window_start=datetime(2026, 9, 2, 4, tzinfo=UTC),
        window_end=REQUEST_TIME,
        report_markdown="# Private report\n\nPaper title stays in the file.\n",
        stats={"matched": 1},
    )

    payload = _deliver_on_demand_report(result, config, "file")
    report_path = Path(payload["report_path"])

    assert payload["report_delivery"] == "file"
    assert payload["report_markdown"] is None
    assert payload["on_demand_report_file_written"] is True
    assert payload["report_character_count"] == len(result.report_markdown)
    assert report_path.parent == (workspace_tmp_path / "reports").resolve()
    assert report_path.read_text(encoding="utf-8") == result.report_markdown
