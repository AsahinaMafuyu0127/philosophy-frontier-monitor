"""Conservative Crossref bibliographic lookup and publication-date parsing."""

from __future__ import annotations

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
from ..normalize import normalize_doi

API_URL = "https://api.crossref.org/works"
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"


class CrossrefError(RuntimeError):
    """Raised for transport or schema failures in the Crossref adapter."""


@dataclass(frozen=True, slots=True)
class CrossrefWork:
    doi: str
    title: str
    authors: tuple[str, ...]
    container_title: str | None
    work_type: str | None
    stable_url: str
    publication_date: DateValue | None
    publication_event: str | None
    raw: dict[str, Any]


def _first_text(item: dict[str, Any], field: str) -> str | None:
    value = item.get(field)
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0].strip() or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _authors(item: dict[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for author in item.get("author", []):
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        name = " ".join(part for part in (given, family) if part)
        if name:
            result.append(name)
    return tuple(result)


def _date_from_parts(item: dict[str, Any], field: str, retrieved_at: datetime) -> DateValue | None:
    date_parts = item.get(field, {}).get("date-parts", [])
    if not date_parts or not isinstance(date_parts[0], list):
        return None
    parts = date_parts[0]
    try:
        if len(parts) >= 3:
            value: date | str = date(int(parts[0]), int(parts[1]), int(parts[2]))
            precision = DatePrecision.DAY
        elif len(parts) == 2:
            value = f"{int(parts[0]):04d}-{int(parts[1]):02d}"
            precision = DatePrecision.MONTH
        elif len(parts) == 1:
            value = f"{int(parts[0]):04d}"
            precision = DatePrecision.YEAR
        else:
            return None
    except (TypeError, ValueError):
        return None
    return DateValue(
        value=value,
        precision=precision,
        source="crossref",
        source_record_id=normalize_doi(item.get("DOI")),
        inferred=False,
        retrieved_at=retrieved_at,
    )


def _publication_evidence(
    item: dict[str, Any], retrieved_at: datetime
) -> tuple[DateValue | None, str | None]:
    online = _date_from_parts(item, "published-online", retrieved_at)
    if online is not None:
        return online, "recently_published_online"
    printed = _date_from_parts(item, "published-print", retrieved_at)
    if printed is not None:
        return printed, "recently_assigned_to_issue"
    # Generic Crossref 'published' is retained but deliberately gets no
    # confirmable event subtype; the freshness gate will keep it uncertain.
    generic = _date_from_parts(item, "published", retrieved_at)
    return generic, None


def parse_crossref_work(item: dict[str, Any], *, retrieved_at: datetime) -> CrossrefWork | None:
    doi = normalize_doi(item.get("DOI"))
    title = _first_text(item, "title")
    if doi is None or title is None:
        return None
    publication_date, publication_event = _publication_evidence(item, retrieved_at)
    return CrossrefWork(
        doi=doi,
        title=title,
        authors=_authors(item),
        container_title=_first_text(item, "container-title"),
        work_type=item.get("type"),
        stable_url=f"https://doi.org/{doi}",
        publication_date=publication_date,
        publication_event=publication_event,
        raw=item,
    )


def find_exact_work(
    title: str,
    *,
    first_author: str | None = None,
    mailto: str | None = None,
    client: httpx.Client | None = None,
) -> CrossrefWork | None:
    """Query Crossref and return the earliest high-confidence equivalent record.

    Multiple equivalent DOI records can represent a manuscript and a later
    journal incarnation.  The earliest dated record is intentionally returned
    so that a recent re-publication cannot hide older-work evidence.
    """

    query = " ".join(part for part in (first_author, title) if part)
    params = {
        "query.bibliographic": query,
        "rows": "5",
        "select": (
            "DOI,title,author,container-title,type,URL,published-online,published-print,published"
        ),
    }
    if mailto:
        params["mailto"] = mailto
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    )
    retrieved_at = datetime.now(UTC)
    try:
        response = request_with_retry(
            lambda: active_client.get(API_URL, params=params),
            source="Crossref",
        )
        payload = response.json()
        items = payload.get("message", {}).get("items", [])
    except BoundedRequestError as error:
        raise CrossrefError(f"Crossref lookup failed: {error}") from error
    except (ValueError, AttributeError) as error:
        raise CrossrefError("Crossref lookup failed: invalid response schema") from error
    finally:
        if owns_client:
            active_client.close()

    candidates: list[CrossrefWork] = []
    review_required = False
    for item in items:
        parsed = parse_crossref_work(item, retrieved_at=retrieved_at)
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
            "Crossref returned a possible translated or substantially retitled version."
        )

    unique_by_doi = {candidate.doi: candidate for candidate in candidates}
    dated = [item for item in unique_by_doi.values() if item.publication_date is not None]
    if dated:
        return min(dated, key=lambda item: str(item.publication_date.value))
    return next(iter(unique_by_doi.values())) if len(unique_by_doi) == 1 else None
