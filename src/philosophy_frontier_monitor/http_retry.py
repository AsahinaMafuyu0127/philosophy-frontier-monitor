"""Bounded, privacy-preserving retry policy for read-only source requests."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Small retry budget suitable for an interactive local monitor."""

    max_attempts: int = 3
    backoff_base_seconds: float = 1.0
    max_total_wait_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must not be negative")
        if self.max_total_wait_seconds < 0:
            raise ValueError("max_total_wait_seconds must not be negative")


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True, slots=True)
class RequestTelemetry:
    """Credential-free facts about one logical source request.

    Only counters, status codes and numeric rate-limit headers are retained.
    URLs, query parameters, response bodies and exception messages are never
    copied into telemetry.
    """

    source: str
    outcome: str
    attempts: int
    total_wait_seconds: float
    status_code: int | None = None
    failure_kind: str | None = None
    stop_reason: str | None = None
    retry_after_seconds: int | None = None
    rate_limit_limit: int | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset_seconds: int | None = None


_ACTIVE_TELEMETRY: ContextVar[list[RequestTelemetry] | None] = ContextVar(
    "pfm_request_telemetry",
    default=None,
)


@contextmanager
def collect_request_telemetry() -> Iterator[list[RequestTelemetry]]:
    """Collect request events for the current execution context only."""

    events: list[RequestTelemetry] = []
    token = _ACTIVE_TELEMETRY.set(events)
    try:
        yield events
    finally:
        _ACTIVE_TELEMETRY.reset(token)


def _first_numeric_header(response: httpx.Response, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = _nonnegative_seconds(response.headers.get(name))
        if value is not None:
            return value
    return None


def _rate_limit_values(response: httpx.Response) -> tuple[int | None, int | None, int | None]:
    return (
        _first_numeric_header(
            response,
            ("x-ratelimit-limit", "x-rate-limit-limit", "ratelimit-limit"),
        ),
        _first_numeric_header(
            response,
            ("x-ratelimit-remaining", "x-rate-limit-remaining", "ratelimit-remaining"),
        ),
        _first_numeric_header(
            response,
            ("x-ratelimit-reset", "x-rate-limit-reset", "ratelimit-reset"),
        ),
    )


def _record_telemetry(event: RequestTelemetry) -> None:
    active = _ACTIVE_TELEMETRY.get()
    if active is not None:
        active.append(event)


def summarize_request_telemetry(
    events: list[RequestTelemetry],
    *,
    circuit_skipped: int = 0,
) -> dict[str, Any]:
    """Return stable aggregate counters suitable for CLI JSON and reports."""

    source_summaries: dict[str, dict[str, Any]] = {}
    for event in events:
        source = source_summaries.setdefault(
            event.source,
            {
                "logical_requests": 0,
                "first_attempt_successes": 0,
                "retried_successes": 0,
                "failed_requests": 0,
                "attempts": 0,
                "wait_seconds": 0.0,
                "deferred_long_retry_after": 0,
                "rate_limit_limit": None,
                "rate_limit_remaining": None,
                "rate_limit_reset_seconds": None,
            },
        )
        source["logical_requests"] += 1
        source["attempts"] += event.attempts
        source["wait_seconds"] = round(source["wait_seconds"] + event.total_wait_seconds, 3)
        if event.outcome == "success" and event.attempts == 1:
            source["first_attempt_successes"] += 1
        elif event.outcome == "success":
            source["retried_successes"] += 1
        else:
            source["failed_requests"] += 1
        if event.stop_reason == "server_wait_exceeds_run_budget":
            source["deferred_long_retry_after"] += 1
        for key in (
            "rate_limit_limit",
            "rate_limit_remaining",
            "rate_limit_reset_seconds",
        ):
            value = getattr(event, key)
            if value is not None:
                source[key] = value

    return {
        "logical_requests": len(events),
        "first_attempt_successes": sum(
            event.outcome == "success" and event.attempts == 1 for event in events
        ),
        "retried_successes": sum(
            event.outcome == "success" and event.attempts > 1 for event in events
        ),
        "failed_requests": sum(event.outcome == "failure" for event in events),
        "attempts": sum(event.attempts for event in events),
        "wait_seconds": round(sum(event.total_wait_seconds for event in events), 3),
        "deferred_long_retry_after": sum(
            event.stop_reason == "server_wait_exceeds_run_budget" for event in events
        ),
        "circuit_skipped": max(0, circuit_skipped),
        "sources": source_summaries,
        "privacy": "仅记录计数、状态码与数字限额头；不记录 URL、查询、正文或凭据。",
    }


class BoundedRequestError(RuntimeError):
    """A redacted source failure with enough structure for safe recovery advice."""

    def __init__(
        self,
        *,
        source: str,
        failure_kind: str,
        attempts: int,
        stop_reason: str,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.source = source
        self.failure_kind = failure_kind
        self.attempts = attempts
        self.stop_reason = stop_reason
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        parts = [
            f"source={source}",
            f"kind={failure_kind.replace('_', ' ')}",
            f"attempts={attempts}",
            f"stop={stop_reason}",
        ]
        if status_code is not None:
            parts.append(f"HTTP {status_code}")
        if retry_after_seconds is not None:
            parts.append(f"retry_after_seconds={retry_after_seconds}")
        super().__init__("; ".join(parts))


def _nonnegative_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return max(0, math.ceil(parsed))


def _retry_after_seconds(response: httpx.Response, *, now: datetime) -> int | None:
    raw_retry_after = response.headers.get("retry-after")
    seconds = _nonnegative_seconds(raw_retry_after)
    if seconds is not None:
        return seconds
    if raw_retry_after:
        try:
            retry_at = parsedate_to_datetime(raw_retry_after)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(
                0, math.ceil((retry_at.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
            )

    if (
        response.status_code == 429
        and response.headers.get("x-ratelimit-remaining", "").strip() == "0"
    ):
        return _nonnegative_seconds(response.headers.get("x-ratelimit-reset"))
    return None


def _http_failure_kind(response: httpx.Response) -> str:
    if response.status_code != 429:
        return (
            "transient_http" if response.status_code in RETRYABLE_STATUS_CODES else "permanent_http"
        )
    if response.headers.get("x-ratelimit-remaining", "").strip() == "0":
        return "daily_budget_exhausted"
    return "rate_limited_or_daily_budget_exhausted"


def request_with_retry(
    request: Callable[[], httpx.Response],
    *,
    source: str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> httpx.Response:
    """Run one idempotent read request with a strict attempt and wait budget.

    Failure messages intentionally omit URLs, parameters, response bodies, and
    transport exception text because those values may contain private queries
    or credentials.
    """

    sleeper = sleep or time.sleep
    current_time = clock or (lambda: datetime.now(UTC))
    total_wait = 0.0

    def failure(
        *,
        failure_kind: str,
        attempts: int,
        stop_reason: str,
        response: httpx.Response | None = None,
        retry_after_seconds: int | None = None,
    ) -> BoundedRequestError:
        status_code = response.status_code if response is not None else None
        limit, remaining, reset = (
            _rate_limit_values(response) if response is not None else (None, None, None)
        )
        _record_telemetry(
            RequestTelemetry(
                source=source,
                outcome="failure",
                attempts=attempts,
                total_wait_seconds=total_wait,
                status_code=status_code,
                failure_kind=failure_kind,
                stop_reason=stop_reason,
                retry_after_seconds=retry_after_seconds,
                rate_limit_limit=limit,
                rate_limit_remaining=remaining,
                rate_limit_reset_seconds=reset,
            )
        )
        return BoundedRequestError(
            source=source,
            failure_kind=failure_kind,
            attempts=attempts,
            stop_reason=stop_reason,
            status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )

    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = request()
        except httpx.TransportError as error:
            if attempt >= policy.max_attempts:
                raise failure(
                    failure_kind="transient_transport",
                    attempts=attempt,
                    stop_reason="attempt_limit",
                ) from error
            delay = policy.backoff_base_seconds * (2 ** (attempt - 1))
            if total_wait + delay > policy.max_total_wait_seconds:
                raise failure(
                    failure_kind="transient_transport",
                    attempts=attempt,
                    stop_reason="retry_wait_budget_exhausted",
                ) from error
            sleeper(delay)
            total_wait += delay
            continue

        if 200 <= response.status_code < 300:
            limit, remaining, reset = _rate_limit_values(response)
            _record_telemetry(
                RequestTelemetry(
                    source=source,
                    outcome="success",
                    attempts=attempt,
                    total_wait_seconds=total_wait,
                    status_code=response.status_code,
                    rate_limit_limit=limit,
                    rate_limit_remaining=remaining,
                    rate_limit_reset_seconds=reset,
                )
            )
            return response

        failure_kind = _http_failure_kind(response)
        if response.status_code not in RETRYABLE_STATUS_CODES:
            raise failure(
                failure_kind=failure_kind,
                attempts=attempt,
                stop_reason="non_retryable_status",
                response=response,
            )

        server_wait = _retry_after_seconds(response, now=current_time())
        if attempt >= policy.max_attempts:
            raise failure(
                failure_kind=failure_kind,
                attempts=attempt,
                stop_reason="attempt_limit",
                response=response,
                retry_after_seconds=server_wait,
            )
        delay = (
            float(server_wait)
            if server_wait is not None
            else policy.backoff_base_seconds * (2 ** (attempt - 1))
        )
        if total_wait + delay > policy.max_total_wait_seconds:
            raise failure(
                failure_kind=failure_kind,
                attempts=attempt,
                stop_reason="server_wait_exceeds_run_budget"
                if server_wait is not None
                else "retry_wait_budget_exhausted",
                response=response,
                retry_after_seconds=server_wait,
            )
        sleeper(delay)
        total_wait += delay

    raise AssertionError("retry loop exited without a response or error")
