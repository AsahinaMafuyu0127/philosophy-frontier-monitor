import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from philosophy_frontier_monitor import http_retry
from philosophy_frontier_monitor.cli import main
from philosophy_frontier_monitor.oai_cache import OAIHarvestCache, _endpoint_hash
from philosophy_frontier_monitor.sources.philarchive_oai import (
    OAIError,
    OAIRecord,
    OAIWindowSnapshot,
)

ENDPOINT = "https://philarchive.org/oai.pl"
START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 8, tzinfo=UTC)
LATER_END = datetime(2026, 9, 9, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def oai_response(
    *records: tuple[str, str],
    token: str | None = None,
    expiration: str | None = None,
    cursor: int | None = None,
    complete_list_size: int | None = None,
) -> str:
    record_xml = "".join(
        f"""
        <record>
          <header><identifier>oai:philarchive.org/rec/{key}</identifier>
            <datestamp>{datestamp}</datestamp></header>
          <metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
            xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:date>2026</dc:date>
            <dc:identifier>https://philarchive.org/rec/{key}</dc:identifier>
            <dc:type>info:eu-repo/semantics/article</dc:type>
          </oai_dc:dc></metadata>
        </record>"""
        for key, datestamp in records
    )
    attributes = ""
    if expiration is not None:
        attributes += f' expirationDate="{expiration}"'
    if cursor is not None:
        attributes += f' cursor="{cursor}"'
    if complete_list_size is not None:
        attributes += f' completeListSize="{complete_list_size}"'
    token_text = token or ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <responseDate>2026-09-08T00:00:00Z</responseDate>
      <request verb="ListRecords">https://philarchive.org/oai.pl</request>
      <ListRecords>{record_xml}<resumptionToken{attributes}>{token_text}</resumptionToken></ListRecords>
    </OAI-PMH>"""


def bad_token_response() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
    <OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
      <responseDate>2026-09-08T00:00:00Z</responseDate>
      <request verb="ListRecords">https://philarchive.org/oai.pl</request>
      <error code="badResumptionToken">Expired</error>
    </OAI-PMH>"""


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


def test_interrupted_pages_resume_from_persisted_token(workspace_tmp_path, monkeypatch):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    first_requests = []
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    def first_handler(request):
        first_requests.append(request)
        if "resumptionToken" not in request.url.params:
            return httpx.Response(
                200,
                text=oai_response(
                    ("ONE", "2026-09-07T00:00:00Z"),
                    token="resume-page-2",
                    expiration="2026-09-09T00:00:00Z",
                    cursor=0,
                    complete_list_size=2,
                ),
                request=request,
            )
        raise httpx.ReadError("simulated interruption", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(first_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        with pytest.raises(OAIError, match="OAI request failed"):
            cache.load_window(START, END, ENDPOINT)
        info = cache.inspect()
        coverage = cache.connection.execute("SELECT COUNT(*) FROM oai_coverage").fetchone()[0]

    assert coverage == 0
    assert len(info.incomplete_sessions) == 1
    assert info.incomplete_sessions[0].completed_pages == 1
    assert info.incomplete_sessions[0].staged_records == 1
    assert info.incomplete_sessions[0].token_present is True

    resumed_requests = []

    def resumed_handler(request):
        resumed_requests.append(request)
        assert request.url.params["resumptionToken"] == "resume-page-2"
        assert "from" not in request.url.params
        return httpx.Response(
            200,
            text=oai_response(("TWO", "2026-09-07T01:00:00Z")),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(resumed_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        result = cache.load_window(START, END, ENDPOINT)
        final_info = cache.inspect()
        stats = cache.stats

    assert len(resumed_requests) == 1
    assert tuple(result.records_by_key) == ("one", "two")
    assert result.network_harvested_records == 2
    assert final_info.incomplete_sessions == ()
    assert final_info.coverage_intervals == 1
    assert stats.resumed_sessions == 1


def test_newer_rolling_window_finishes_older_session_before_fetching_tail(
    workspace_tmp_path,
    monkeypatch,
):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    def interrupted_handler(request):
        if "resumptionToken" not in request.url.params:
            return httpx.Response(
                200,
                text=oai_response(
                    ("ONE", "2026-09-07T00:00:00Z"),
                    token="resume-old-window",
                    expiration="2026-09-09T00:00:00Z",
                ),
                request=request,
            )
        raise httpx.ReadError("stop old rolling window", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
        pytest.raises(OAIError),
    ):
        cache.load_window(START, END, ENDPOINT)

    requests = []

    def resumed_then_tail_handler(request):
        requests.append(request)
        if "resumptionToken" in request.url.params:
            return httpx.Response(
                200,
                text=oai_response(("TWO", "2026-09-07T01:00:00Z")),
                request=request,
            )
        return httpx.Response(
            200,
            text=oai_response(("THREE", "2026-09-08T01:00:00Z")),
            request=request,
        )

    rolling_start = START + timedelta(hours=1)
    with (
        httpx.Client(transport=httpx.MockTransport(resumed_then_tail_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        result = cache.load_window(rolling_start, LATER_END, ENDPOINT)

    assert len(requests) == 2
    assert requests[0].url.params["resumptionToken"] == "resume-old-window"
    assert "resumptionToken" not in requests[1].url.params
    assert tuple(result.records_by_key) == ("one", "two", "three")
    assert result.cache_resumed_sessions == 1
    assert result.cache_refresh_windows == 2
    assert result.cache_records_written == 3


def test_expired_persisted_token_restarts_original_gap(workspace_tmp_path, monkeypatch):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    def interrupted_handler(request):
        if "resumptionToken" not in request.url.params:
            return httpx.Response(
                200,
                text=oai_response(
                    ("OLD", "2026-09-07T00:00:00Z"),
                    token="already-expired",
                    expiration="2026-09-07T01:00:00Z",
                ),
                request=request,
            )
        raise httpx.ReadError("stop after first page", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
        pytest.raises(OAIError),
    ):
        cache.load_window(START, END, ENDPOINT)

    requests = []

    def restarted_handler(request):
        requests.append(request)
        assert "resumptionToken" not in request.url.params
        return httpx.Response(
            200,
            text=oai_response(("NEW", "2026-09-07T02:00:00Z")),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(restarted_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        result = cache.load_window(START, END, ENDPOINT)
        stats = cache.stats

    assert len(requests) == 1
    assert tuple(result.records_by_key) == ("new",)
    assert stats.expired_token_restarts == 1


def test_bad_persisted_token_restarts_original_gap(workspace_tmp_path, monkeypatch):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    def interrupted_handler(request):
        if "resumptionToken" not in request.url.params:
            return httpx.Response(
                200,
                text=oai_response(
                    ("OLD", "2026-09-07T00:00:00Z"),
                    token="invalid-on-resume",
                    expiration="2026-09-09T00:00:00Z",
                ),
                request=request,
            )
        raise httpx.ReadError("stop after first page", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(interrupted_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
        pytest.raises(OAIError),
    ):
        cache.load_window(START, END, ENDPOINT)

    requests = []

    def restarted_handler(request):
        requests.append(request)
        if "resumptionToken" in request.url.params:
            return httpx.Response(200, text=bad_token_response(), request=request)
        return httpx.Response(
            200,
            text=oai_response(("NEW", "2026-09-07T02:00:00Z")),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(restarted_handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        result = cache.load_window(START, END, ENDPOINT)
        stats = cache.stats

    assert len(requests) == 2
    assert "resumptionToken" in requests[0].url.params
    assert "resumptionToken" not in requests[1].url.params
    assert tuple(result.records_by_key) == ("new",)
    assert stats.invalid_token_restarts == 1


def test_completed_staging_finalizes_after_restart_without_network(workspace_tmp_path, monkeypatch):
    path = workspace_tmp_path / "oai-cache.sqlite3"

    def handler(request):
        return httpx.Response(
            200,
            text=oai_response(("ONE", "2026-09-07T00:00:00Z")),
            request=request,
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):

        def fail_finalize(*_args):
            raise OAIError("crash before finalization")

        monkeypatch.setattr(cache, "_finalize_session", fail_finalize)
        with pytest.raises(OAIError, match="crash before finalization"):
            cache.load_window(START, END, ENDPOINT)
        assert cache.inspect().incomplete_sessions[0].sequence_complete is True

    def no_network(_request):
        raise AssertionError("a completed staged sequence must finalize without network")

    with (
        httpx.Client(transport=httpx.MockTransport(no_network)) as client,
        OAIHarvestCache(path, oai_client=client) as cache,
    ):
        result = cache.load_window(START, END, ENDPOINT)

    assert tuple(result.records_by_key) == ("one",)


def test_live_session_blocks_a_second_writer_for_the_same_gap(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    first = OAIHarvestCache(path)
    second = OAIHarvestCache(path)
    try:
        first._acquire_session(_endpoint_hash(ENDPOINT), START, END)
        with pytest.raises(OAIError, match="another process"):
            second.load_window(START, END, ENDPOINT)
    finally:
        second.close()
        first.close()


def test_prune_refuses_while_a_harvest_session_is_active(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(path) as cache:
        cache._acquire_session(_endpoint_hash(ENDPOINT), START, END)
        with pytest.raises(OAIError, match="while a harvest session is active"):
            cache.prune(datetime(2026, 9, 5, tzinfo=UTC))


def test_status_and_prune_expose_no_token_value_and_preserve_newer_coverage(workspace_tmp_path):
    path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(
        path,
        network_loader=lambda start, end, endpoint: snapshot(
            start,
            end,
            record("OLD", "2026-09-02T00:00:00Z"),
            record("NEW", "2026-09-07T00:00:00Z"),
        ),
    ) as cache:
        cache.load_window(START, END, ENDPOINT)
        before = cache.inspect()
        result = cache.prune(datetime(2026, 9, 5, tzinfo=UTC))
        after = cache.inspect()

    assert before.record_events == 2
    assert result.record_events_deleted == 1
    assert after.record_events == 1
    assert after.coverage_intervals == 1


def test_oai_cache_cli_status_and_confirmed_prune(workspace_tmp_path, capsys):
    config_path = workspace_tmp_path / "watchlist.yaml"
    config_path.write_text(
        (FIXTURE_DIR / "watchlist_minimal.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    cache_path = workspace_tmp_path / "oai-cache.sqlite3"
    with OAIHarvestCache(
        cache_path,
        network_loader=lambda start, end, endpoint: snapshot(
            start,
            end,
            record("OLD", "2026-09-02T00:00:00Z"),
            record("NEW", "2026-09-07T00:00:00Z"),
        ),
    ) as cache:
        cache.load_window(START, END, ENDPOINT)

    assert main(["oai-cache", "status", "--config", str(config_path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["exists"] is True
    assert status["record_events"] == 2
    assert status["incomplete_sessions"] == []
    assert "next_token" not in json.dumps(status)

    assert (
        main(
            [
                "oai-cache",
                "prune",
                "--config",
                str(config_path),
                "--before",
                "2026-09-05T00:00:00Z",
            ]
        )
        == 1
    )
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["state_advanced"] is False

    assert (
        main(
            [
                "oai-cache",
                "prune",
                "--config",
                str(config_path),
                "--before",
                "2026-09-05T00:00:00Z",
                "--confirm",
            ]
        )
        == 0
    )
    pruned = json.loads(capsys.readouterr().out)
    assert pruned["record_events_deleted"] == 1
    assert pruned["weekly_state_advanced"] is False
