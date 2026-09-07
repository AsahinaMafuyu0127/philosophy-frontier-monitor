"""Conservative OpenAlex title lookup used only as bibliographic evidence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ..http_retry import BoundedRequestError, request_with_retry
from ..identity import (
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


def _add_access_parameters(params: dict[str, str], mailto: str | None) -> None:
    if mailto:
        params["mailto"] = mailto
    api_key = os.environ.get("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key


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
    return OpenAlexWork(
        openalex_id=openalex_id,
        doi=normalize_doi(item.get("doi")),
        title=title.strip(),
        authors=authors,
        publication_date=publication_date,
        work_type=item.get("type"),
        stable_url=item.get("primary_location", {}).get("landing_page_url") or openalex_id,
        raw=item,
    )


def find_exact_work(
    title: str,
    *,
    first_author: str | None = None,
    mailto: str | None = None,
    client: httpx.Client | None = None,
) -> OpenAlexWork | None:
    params = {"search": title, "per-page": "5"}
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
    review_required = False
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
            review_required = True
    if review_required:
        raise IdentityReviewRequired(
            "OpenAlex returned a possible translated or substantially retitled version."
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
                "per-page": str(len(batch)),
                "select": ("id,doi,title,publication_date,type,authorships,primary_location"),
            }
            _add_access_parameters(params, mailto)
            response = request_with_retry(
                lambda params=params: active_client.get(API_URL, params=params),
                source="OpenAlex",
            )
            items = response.json().get("results", [])
            if not isinstance(items, list):
                raise ValueError("OpenAlex results is not a list")
            for item in items:
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
    pending = [
        queries[offset : offset + MAX_TITLE_BATCH_SIZE]
        for offset in range(0, len(queries), MAX_TITLE_BATCH_SIZE)
    ]
    try:
        while pending:
            batch = pending.pop(0)
            params = {
                "filter": "title.search.exact:" + "|".join(batch),
                "per-page": "100",
                "select": ("id,doi,title,publication_date,type,authorships,primary_location"),
            }
            _add_access_parameters(params, mailto)
            response = request_with_retry(
                lambda params=params: active_client.get(API_URL, params=params),
                source="OpenAlex",
            )
            payload = response.json()
            items = payload.get("results", [])
            if not isinstance(items, list):
                raise ValueError("OpenAlex results is not a list")
            count = payload.get("meta", {}).get("count", len(items))
            if isinstance(count, int) and count > 100 and len(batch) > 1:
                midpoint = len(batch) // 2
                pending[0:0] = [batch[:midpoint], batch[midpoint:]]
                continue
            for item in items:
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
