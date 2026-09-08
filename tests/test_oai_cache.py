from datetime import UTC, datetime

import pytest

from philosophy_frontier_monitor.oai_cache import OAIHarvestCache
from philosophy_frontier_monitor.sources.philarchive_oai import (
    OAIError,
    OAIRecord,
    OAIWindowSnapshot,
)

ENDPOINT = "https://philarchive.org/oai.pl"
START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 8, tzinfo=UTC)
LATER_END = datetime(2026, 9, 9, tzinfo=UTC)


def record(
    key: str,
    datestamp: str,
    *,
    deleted: bool = False,
    title: str = "Sensitive title not needed by the monitor",
) -> OAIRecord:
    return OAIRecord(
        identifier=f"oai:philarchive.org/rec/{key}",
        source_datestamp=datestamp,
        deleted=deleted,
        fields={
            "date": ("2026",),
            "identifier": (f"https://philarchive.org/rec/{key}",),
            "title": (title,),
            "type": ("info:eu-repo/semantics/article",),
        },
    )


def snapshot(
    start: datetime,
    end: datetime,
    *events: OAIRecord,
) -> OAIWindowSnapshot:
    latest = {event.identifier.rsplit("/", 1)[-1].casefold(): event for event in events}
    active = {key: event for key, event in latest.items() if not event.deleted}
    return OAIWindowSnapshot(
        window_start=start,
        window_end=end,
        checked_at=end,
        records_by_key=active,
        harvested_records=len(events),
        records_in_exact_window=len(events),
        deleted_records=sum(event.deleted for event in latest.values()),
        unkeyed_records=0,
        duplicate_keys=max(0, len(events) - len(latest)),
        overlap_records_excluded=1,
        record_events=events,
        network_harvested_records=len(events) + 1,
    )


def test_cold_start_writes_minimal_records_and_complete_coverage(workspace_tmp_path):
    calls = []

    def loader(start, end, endpoint):
        calls.append((start, end, endpoint))
        return snapshot(start, end, record("ONE", "2026-09-07"))

    path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(path, network_loader=loader) as cache:
        result = cache.load_window(START, END, ENDPOINT)
        row = cache.connection.execute("SELECT fields_json FROM oai_record_events").fetchone()
        coverage = cache.connection.execute(
            "SELECT window_start, window_end FROM oai_coverage"
        ).fetchall()

    assert calls == [(START, END, ENDPOINT)]
    assert result.retrieval_mode == "cold_start"
    assert result.network_harvested_records == 1
    assert result.cache_records_written == 1
    assert result.cache_refresh_windows == 1
    assert tuple(result.records_by_key) == ("one",)
    assert "title" not in row["fields_json"]
    assert "Sensitive title" not in row["fields_json"]
    assert len(coverage) == 1
    assert datetime.fromisoformat(coverage[0]["window_start"]) == START
    assert datetime.fromisoformat(coverage[0]["window_end"]) == END


def test_hot_cache_survives_reopen_without_network(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(
        path,
        network_loader=lambda start, end, endpoint: snapshot(
            start,
            end,
            record("ONE", "2026-09-07"),
        ),
    ) as cache:
        cache.load_window(START, END, ENDPOINT)

    def no_network(*_args):
        raise AssertionError("a fully covered hot window must not use the network")

    with OAIHarvestCache(path, network_loader=no_network) as cache:
        result = cache.load_window(START, END, ENDPOINT)

    assert result.retrieval_mode == "cache_hit"
    assert result.network_harvested_records == 0
    assert result.cache_records_written == 0
    assert result.cache_refresh_windows == 0
    assert tuple(result.records_by_key) == ("one",)
    assert cache.stats.hits == 1


def test_incremental_refresh_fetches_only_tail_and_applies_deletion(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(
        path,
        network_loader=lambda start, end, endpoint: snapshot(
            start,
            end,
            record("ONE", "2026-09-07"),
        ),
    ) as cache:
        cache.load_window(START, END, ENDPOINT)

    calls = []

    def incremental_loader(start, end, endpoint):
        calls.append((start, end, endpoint))
        return snapshot(
            start,
            end,
            record("ONE", "2026-09-08", deleted=True),
            record("TWO", "2026-09-08"),
        )

    with OAIHarvestCache(path, network_loader=incremental_loader) as cache:
        result = cache.load_window(START, LATER_END, ENDPOINT)
        coverage = cache.connection.execute(
            "SELECT window_start, window_end FROM oai_coverage"
        ).fetchall()

    assert calls == [(END, LATER_END, ENDPOINT)]
    assert result.retrieval_mode == "incremental_refresh"
    assert result.deleted_records == 1
    assert tuple(result.records_by_key) == ("two",)
    assert result.cache_coverage_start == START
    assert result.cache_coverage_end == LATER_END
    assert len(coverage) == 1


def test_failed_cold_start_does_not_claim_coverage(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"

    def failed_loader(_start, _end, _endpoint):
        raise OAIError("complete pagination failed")

    with OAIHarvestCache(path, network_loader=failed_loader) as cache:
        with pytest.raises(OAIError, match="complete pagination failed"):
            cache.load_window(START, END, ENDPOINT)
        coverage_count = cache.connection.execute("SELECT COUNT(*) FROM oai_coverage").fetchone()[0]

    assert coverage_count == 0


def test_endpoint_identity_keeps_coverage_separate(workspace_tmp_path):
    calls = []

    def loader(start, end, endpoint):
        calls.append(endpoint)
        return snapshot(start, end)

    with OAIHarvestCache(
        workspace_tmp_path / "oai-cache.sqlite3",
        network_loader=loader,
    ) as cache:
        cache.load_window(START, END, ENDPOINT)
        cache.load_window(START, END, "https://www.philarchive.org/oai.pl")

    assert calls == [ENDPOINT, "https://www.philarchive.org/oai.pl"]
