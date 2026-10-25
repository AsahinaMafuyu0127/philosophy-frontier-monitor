"""Conservative OpenAlex title lookup used only as bibliographic evidence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ..http_retry import BoundedRequestError, request_with_retry
from ..identity import (
    IdentityComparison,
    IdentityMatchLevel,
    IdentityReviewRequired,
    compare_bibliographic_identity,
)
from ..models import DatePrecision, DateValue
from ..normalize import normalize_doi, normalize_title

API_URL = "https://api.openalex.org/works"
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"
MAX_DOI_BATCH_SIZE = 50
MAX_TITLE_BATCH_SIZE = 20
MAX_BIBLIOGRAPHIC_PAGES = 3


class OpenAlexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAlexWork:
    openalex_id: str
    doi: str | None
    title: str
    authors: tuple[str, ...]
    publication_date: DateValue | None
    work_type: str | None
    stable_url: str
    raw: dict[str, Any]
    retrieved_at: datetime | None = None

    @property
    def cited_by_count(self) -> int | None:
        """Missing or malformed citation data is unknown, never zero."""
        value = self.raw.get("cited_by_count")
        return value if type(value) is int and value >= 0 else None


def _add_access_parameters(params: dict[str, str], mailto: str | None) -> None:
    if mailto:
        params["mailto"] = mailto
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key


def _complete_work_pages(client: httpx.Client, params: dict[str, str]) -> list[dict[str, Any]]:
    """Return a whole bounded result set, or reject incomplete identity evidence.

    OpenAlex reports the total in ``meta.count``. Most lookups fit in one page;
    additional requests are made only when that total exceeds the first page.
    """

    per_page = int(params["per_page"])
    expected_count: int | None = None
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(1, MAX_BIBLIOGRAPHIC_PAGES + 1):
        page_params = params if page == 1 else {**params, "page": str(page)}
        response = request_with_retry(
            lambda page_params=page_params: client.get(API_URL, params=page_params),
            source="OpenAlex",
        )
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("OpenAlex results is not a list")
        items = payload["results"]
        if len(items) > per_page or any(not isinstance(item, dict) for item in items):
            raise ValueError("OpenAlex results have an invalid page shape")
        meta = payload.get("meta")
        if not isinstance(meta, dict) or type(meta.get("count")) is not int:
            raise ValueError("OpenAlex response has no valid result count")
        count = meta["count"]
        if count < 0 or (expected_count is not None and count != expected_count):
            raise OpenAlexError("OpenAlex result count changed during lookup")
        expected_count = count
        if count > per_page * MAX_BIBLIOGRAPHIC_PAGES:
            raise OpenAlexError("OpenAlex lookup exceeds the bounded pagination limit")
        if len(results) + len(items) > count:
            raise ValueError("OpenAlex returned more results than its declared count")
        for item in items:
            identifier = item.get("id")
            if isinstance(identifier, str):
                if identifier in seen_ids:
                    raise OpenAlexError("OpenAlex repeated a work across lookup pages")
                seen_ids.add(identifier)
        results.extend(items)
        if len(results) == count:
            return results
        if len(items) < per_page:
            raise OpenAlexError("OpenAlex returned an incomplete lookup page")
    raise OpenAlexError("OpenAlex lookup exceeds the bounded pagination limit")


def parse_openalex_work(item: dict[str, Any], *, retrieved_at: datetime) -> OpenAlexWork | None:
    openalex_id = item.get("id")
    title = item.get("title")
    if not isinstance(openalex_id, str) or not isinstance(title, str) or not title.strip():
        return None
    authors = tuple(
        authorship.get("author", {}).get("display_name", "").strip()
        for authorship in item.get("authorships", [])
        if authorship.get("author", {}).get("display_name", "").strip()
    )
    raw_date = item.get("publication_date")
    publication_date = None
    if isinstance(raw_date, str):
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError:
            pass
        else:
            publication_date = DateValue(
                parsed_date,
                DatePrecision.DAY,
                "openalex",
                source_record_id=openalex_id,
                retrieved_at=retrieved_at,
            )
    raw_work_type = item.get("type")
    work_type = (
        raw_work_type.strip()
        if isinstance(raw_work_type, str) and raw_work_type.strip()
        else "unrecognized"
        if raw_work_type is not None
        else None
    )
    return OpenAlexWork(
        openalex_id=openalex_id,
        doi=normalize_doi(item.get("doi")),
        title=title.strip(),
        authors=authors,
        publication_date=publication_date,
        work_type=work_type,
        stable_url=(item.get("primary_location") or {}).get("landing_page_url") or openalex_id,
        raw=item,
        retrieved_at=retrieved_at,
    )


def find_exact_work(
    title: str,
    *,
    first_author: str | None = None,
    mailto: str | None = None,
    client: httpx.Client | None = None,
) -> OpenAlexWork | None:
    params = {"search": title, "per_page": "5"}
    _add_access_parameters(params, mailto)
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    retrieved_at = datetime.now(UTC)
    try:
        response = request_with_retry(
            lambda: active_client.get(API_URL, params=params),
            source="OpenAlex",
        )
        items = response.json().get("results", [])
    except BoundedRequestError as error:
        raise OpenAlexError(f"OpenAlex lookup failed: {error}") from error
    except (ValueError, AttributeError) as error:
        raise OpenAlexError("OpenAlex lookup failed: invalid response schema") from error
    finally:
        if owns_client:
            active_client.close()

    candidates: list[OpenAlexWork] = []
    comparisons: list[IdentityComparison] = []
    ambiguous: list[OpenAlexWork] = []
    for item in items:
        parsed = parse_openalex_work(item, retrieved_at=retrieved_at)
        if parsed is None:
            continue
        decision = compare_bibliographic_identity(
            title,
            first_author,
            parsed.title,
            parsed.authors,
        )
        if decision.level is IdentityMatchLevel.EQUIVALENT:
            candidates.append(parsed)
        elif decision.level is IdentityMatchLevel.REVIEW_REQUIRED:
            ambiguous.append(parsed)
            comparisons.append(
                IdentityComparison(
                    source="OpenAlex",
                    record_id=parsed.openalex_id,
                    stable_url=parsed.stable_url,
                    title=parsed.title,
                    authors=parsed.authors,
                    doi=parsed.doi,
                    publication_date=(
                        str(parsed.publication_date.value) if parsed.publication_date else None
                    ),
                    reason=decision.reason,
                    evidence=decision.evidence,
                )
            )
    if comparisons:
        raise IdentityReviewRequired(
            "OpenAlex returned a possible translated or substantially retitled version.",
            comparisons=tuple(comparisons),
            equivalent_works=tuple(candidates),
            ambiguous_works=tuple(ambiguous),
        )
    unique = {candidate.openalex_id: candidate for candidate in candidates}
    dated = [item for item in unique.values() if item.publication_date is not None]
    if dated:
        return min(dated, key=lambda item: str(item.publication_date.value))
    return next(iter(unique.values())) if len(unique) == 1 else None


def find_works_by_dois(
    dois: tuple[str, ...],
    *,
    mailto: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, OpenAlexWork]:
    """Resolve normalized DOI identifiers in bounded OpenAlex OR-filter batches."""

    normalized = tuple(sorted({doi for value in dois if (doi := normalize_doi(value))}))
    if not normalized:
        return {}
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    retrieved_at = datetime.now(UTC)
    resolved: dict[str, OpenAlexWork] = {}
    ambiguous_dois: set[str] = set()
    try:
        for offset in range(0, len(normalized), MAX_DOI_BATCH_SIZE):
            batch = normalized[offset : offset + MAX_DOI_BATCH_SIZE]
            params = {
                "filter": "doi:" + "|".join(batch),
                "per_page": str(len(batch)),
                "select": (
                    "id,doi,title,publication_date,type,authorships,primary_location,cited_by_count"
                ),
            }
            _add_access_parameters(params, mailto)
            for item in _complete_work_pages(active_client, params):
                parsed = parse_openalex_work(item, retrieved_at=retrieved_at)
                if (
                    parsed is None
                    or parsed.doi is None
                    or parsed.doi not in batch
                    or parsed.doi in ambiguous_dois
                ):
                    continue
                existing = resolved.get(parsed.doi)
                if existing is not None and existing.openalex_id != parsed.openalex_id:
                    resolved.pop(parsed.doi, None)
                    ambiguous_dois.add(parsed.doi)
                    continue
                resolved[parsed.doi] = parsed
    except BoundedRequestError as error:
        raise OpenAlexError(f"OpenAlex batch lookup failed: {error}") from error
    except (ValueError, AttributeError) as error:
        raise OpenAlexError("OpenAlex batch lookup failed: invalid response schema") from error
    finally:
        if owns_client:
            active_client.close()
    return resolved


def _title_filter_value(value: str) -> str:
    """Remove filter separators while retaining words for exact title search."""

    return normalize_title(value)


def find_works_by_titles(
    titles: tuple[str, ...],
    *,
    mailto: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[OpenAlexWork, ...]:
    """Search small title batches and return records for strict local matching."""

    queries = tuple(sorted({query for value in titles if (query := _title_filter_value(value))}))
    if not queries:
        return ()
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    retrieved_at = datetime.now(UTC)
    resolved: dict[str, OpenAlexWork] = {}
    try:
        for offset in range(0, len(queries), MAX_TITLE_BATCH_SIZE):
            batch = queries[offset : offset + MAX_TITLE_BATCH_SIZE]
            params = {
                "filter": "title.search.exact:" + "|".join(batch),
                "per_page": "100",
                "select": (
                    "id,doi,title,publication_date,type,authorships,primary_location,cited_by_count"
                ),
            }
            _add_access_parameters(params, mailto)
            for item in _complete_work_pages(active_client, params):
                parsed = parse_openalex_work(item, retrieved_at=retrieved_at)
                if parsed is not None:
                    resolved[parsed.openalex_id] = parsed
    except BoundedRequestError as error:
        raise OpenAlexError(f"OpenAlex batch title lookup failed: {error}") from error
    except (ValueError, AttributeError) as error:
        raise OpenAlexError(
            "OpenAlex batch title lookup failed: invalid response schema"
        ) from error
    finally:
        if owns_client:
            active_client.close()
    return tuple(resolved[key] for key in sorted(resolved))
