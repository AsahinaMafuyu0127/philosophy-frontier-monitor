from datetime import UTC, datetime

import httpx
import pytest

from philosophy_frontier_monitor.cli import _source_failure_payload
from philosophy_frontier_monitor.http_retry import (
    BoundedRequestError,
    RetryPolicy,
    collect_request_telemetry,
    request_with_retry,
    summarize_request_telemetry,
)


def response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://example.test/private?api_key=secret")
    return httpx.Response(status_code, headers=headers, request=request)


def test_transient_server_errors_retry_with_bounded_exponential_backoff():
    outcomes = iter((response(503), response(502), response(200)))
    delays: list[float] = []

    result = request_with_retry(
        lambda: next(outcomes),
        source="Example",
        sleep=delays.append,
    )

    assert result.status_code == 200
    assert delays == [1.0, 2.0]


def test_retry_after_is_honored_when_it_fits_the_run_wait_budget():
    outcomes = iter((response(429, headers={"Retry-After": "4"}), response(200)))
    delays: list[float] = []

    result = request_with_retry(
        lambda: next(outcomes),
        source="Example",
        sleep=delays.append,
    )

    assert result.status_code == 200
    assert delays == [4.0]


def test_long_daily_reset_is_reported_without_sleeping_or_retrying_early():
    delays: list[float] = []

    with pytest.raises(BoundedRequestError) as raised:
        request_with_retry(
            lambda: response(
                429,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "43200",
                },
            ),
            source="OpenAlex",
            sleep=delays.append,
        )

    assert raised.value.failure_kind == "daily_budget_exhausted"
    assert raised.value.attempts == 1
    assert raised.value.retry_after_seconds == 43200
    assert raised.value.stop_reason == "server_wait_exceeds_run_budget"
    assert delays == []


def test_http_date_retry_after_is_parsed_against_an_aware_clock():
    outcomes = iter(
        (
            response(503, headers={"Retry-After": "Mon, 07 Sep 2026 00:00:05 GMT"}),
            response(200),
        )
    )
    delays: list[float] = []

    result = request_with_retry(
        lambda: next(outcomes),
        source="Example",
        sleep=delays.append,
        clock=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )

    assert result.status_code == 200
    assert delays == [5.0]


def test_permanent_client_error_is_not_retried():
    calls = 0

    def forbidden() -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(403)

    with pytest.raises(BoundedRequestError) as raised:
        request_with_retry(forbidden, source="Example", sleep=lambda _seconds: None)

    assert calls == 1
    assert raised.value.failure_kind == "permanent_http"
    assert raised.value.stop_reason == "non_retryable_status"


def test_transport_failure_stops_at_attempt_limit_and_redacts_exception_text():
    calls = 0
    delays: list[float] = []

    def disconnected() -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("GET", "https://example.test/?api_key=secret")
        raise httpx.ConnectError("connection failed for api_key=secret", request=request)

    with pytest.raises(BoundedRequestError) as raised:
        request_with_retry(
            disconnected,
            source="Example",
            policy=RetryPolicy(max_attempts=3),
            sleep=delays.append,
        )

    assert calls == 3
    assert delays == [1.0, 2.0]
    assert raised.value.failure_kind == "transient_transport"
    assert "secret" not in str(raised.value)
    assert "example.test" not in str(raised.value)


def test_cli_extracts_redacted_source_failure_and_chinese_recovery_hint():
    bounded = BoundedRequestError(
        source="OpenAlex",
        failure_kind="daily_budget_exhausted",
        attempts=1,
        stop_reason="server_wait_exceeds_run_budget",
        status_code=429,
        retry_after_seconds=43200,
    )
    outer = RuntimeError("provider adapter failed")
    outer.__cause__ = bounded

    payload = _source_failure_payload(outer)

    assert payload is not None
    assert payload["source"] == "OpenAlex"
    assert payload["failure_kind"] == "daily_budget_exhausted"
    assert payload["retry_after_seconds"] == 43200
    assert payload["retry_safe"] is True
    assert "额度重置" in payload["recovery"]


def test_telemetry_distinguishes_first_try_retry_and_numeric_rate_budget():
    first = response(
        200,
        headers={
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Remaining": "73",
            "X-RateLimit-Reset": "40",
        },
    )
    retried = iter((response(503), response(200)))

    with collect_request_telemetry() as events:
        request_with_retry(lambda: first, source="OpenAlex")
        request_with_retry(lambda: next(retried), source="OpenAlex", sleep=lambda _value: None)

    summary = summarize_request_telemetry(events, circuit_skipped=2)

    assert summary["logical_requests"] == 2
    assert summary["first_attempt_successes"] == 1
    assert summary["retried_successes"] == 1
    assert summary["attempts"] == 3
    assert summary["wait_seconds"] == 1.0
    assert summary["circuit_skipped"] == 2
    assert summary["sources"]["OpenAlex"]["rate_limit_remaining"] == 73
    assert "secret" not in str(summary)
    assert "example.test" not in str(summary)


def test_telemetry_marks_long_retry_after_as_deferred_without_secret_values():
    with collect_request_telemetry() as events, pytest.raises(BoundedRequestError):
        request_with_retry(
            lambda: response(429, headers={"Retry-After": "3600"}),
            source="OpenAlex",
            sleep=lambda _value: None,
        )

    summary = summarize_request_telemetry(events)

    assert summary["failed_requests"] == 1
    assert summary["deferred_long_retry_after"] == 1
    assert summary["sources"]["OpenAlex"]["deferred_long_retry_after"] == 1
    assert "secret" not in str(summary)
