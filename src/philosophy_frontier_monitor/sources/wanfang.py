"""Bounded Wanfang AI HUB journal discovery and CNKI metadata checks.

Only the subscribed Query endpoint is used. The returned search metadata is
compared field by field; it is not publication timing or category evidence.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .cnki_space import CnkiRecord, CnkiSearchTerm

QUERY_URL = "https://api.wfdata.com/openwanfang/getQuery"
KEY_ENV = "WFDATA_APP_KEY"
RETURNED_FIELDS = (
    "Id",
    "Title",
    "Creator",
    "PeriodicalTitle",
    "PublishDate",
    "Issue",
    "DOI",
    "OriginalOrganization",
    "AuthorOrg",
    "Type",
)
DISCOVERY_FIELDS = (*RETURNED_FIELDS, "MetadataOnlineDate", "PublishYear")
PAGE_ROWS = 20
MAX_PAGES = 2
MAX_RESPONSE_BYTES = 2_000_000


class WanfangError(RuntimeError):
    """A safe source error code; never contains a query or credential."""


@dataclass(frozen=True, slots=True)
class WanfangRecord:
    record_id: str
    title: str
    authors: tuple[str, ...]
    venue: str | None
    year: int | None
    issue: str | None
    doi: str | None
    original_organizations: tuple[str, ...]
    author_orgs: tuple[str, ...]
    publish_date: str | None = None
    metadata_online_date: str | None = None


@dataclass(frozen=True, slots=True)
class WanfangCheck:
    cnki_url: str
    status: str
    record_id: str | None
    conflicting_fields: tuple[str, ...] = ()
    original_organizations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WanfangScan:
    checks: tuple[WanfangCheck, ...]
    checked_at: datetime
    status: str
    attempted: int
    total_candidates: int
    incomplete: int
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class WanfangDiscoveryScan:
    records: tuple[WanfangRecord, ...]
    checked_at: datetime
    status: str
    requested_pages: int
    result_pages: int
    incomplete_queries: int
    failures: tuple[str, ...]


def publication_month(record: WanfangRecord) -> tuple[int, int] | None:
    """Read a source-labelled month, excluding a common year-only placeholder.

    Wanfang returns 1 January for some later-numbered issues. That value is
    not sufficient evidence that the issue or paper appeared in January.
    """

    raw = (record.publish_date or "").strip()
    normalized = raw.replace("年", "-").replace("月", "-").replace("日", "").rstrip("-")
    match = re.fullmatch(
        r"((?:19|20)\d{2})[-/](0?[1-9]|1[0-2])"
        r"(?:[-/](\d{1,2})(?:[ T]\d{2}:\d{2}(?::\d{2})?Z?)?)?",
        normalized,
    )
    if match is None:
        return None
    year, month = int(match[1]), int(match[2])
    try:
        date(year, month, int(match[3]) if match[3] else 1)
    except ValueError:
        return None
    if month == 1 and match[3] is not None and int(match[3]) == 1:
        return None
    return year, month


def _compact(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum()
    )


def _same(left: str, right: str) -> bool:
    return bool(_compact(left)) and _compact(left) == _compact(right)


def _values(fields: dict, field: str) -> tuple[str, ...]:
    item = fields.get(field)
    if isinstance(item, list):
        entries = item
    elif isinstance(item, dict):
        listed = item.get("listValue")
        if isinstance(listed, dict) and isinstance(listed.get("values"), list):
            entries = listed["values"]
        else:
            entries = [item]
    else:
        entries = [item]
    result = []
    for entry in entries[:30]:
        value = (
            entry.get("stringValue", entry.get("strValue", entry.get("numberValue")))
            if isinstance(entry, dict)
            else entry
        )
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            cleaned = str(value).strip()
            if cleaned and len(cleaned) <= 500:
                result.append(cleaned)
    return tuple(result)


def parse_query_response(payload: object) -> tuple[tuple[WanfangRecord, ...], int]:
    """Parse the published Document.FieldsEntry wrapper, not a flat JSON guess."""

    if not isinstance(payload, dict):
        raise WanfangError("invalid_json_shape")
    gateway_code = payload.get("Code", payload.get("code"))
    if gateway_code is not None and str(gateway_code).lower() not in {"success", "200", "0"}:
        raise WanfangError("gateway_error")
    found = payload.get("num_found", payload.get("numFound"))
    if isinstance(found, str) and found.isdecimal() and len(found) <= 12:
        found = int(found)
    documents = payload.get("documents")
    if isinstance(found, bool) or not isinstance(found, int) or found < 0:
        raise WanfangError("invalid_count")
    if not isinstance(documents, list) or len(documents) > PAGE_ROWS:
        raise WanfangError("invalid_documents")
    if found and not documents:
        raise WanfangError("missing_documents")
    records = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("fields"), dict):
            raise WanfangError("invalid_document")
        fields = document["fields"]
        resource_type = (
            document.get("resource_type")
            or document.get("resourceType")
            or next(iter(_values(fields, "Type")), "")
        )
        if resource_type != "Periodical":
            raise WanfangError("unexpected_resource_type")
        if not fields and isinstance(document.get("Periodical"), dict):
            fields = document["Periodical"]
        record_id = next(iter(_values(fields, "Id")), "")
        if not record_id:
            record_id = str(document.get("uid", ""))
        titles = _values(fields, "Title")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", record_id) or not titles:
            raise WanfangError("missing_identity")
        publication = next(iter(_values(fields, "PublishDate")), "")
        year_text = next(iter(_values(fields, "PublishYear")), "")
        year_match = re.match(r"^(?:19|20)\d{2}", year_text) or re.match(
            r"^(?:19|20)\d{2}", publication
        )
        records.append(
            WanfangRecord(
                record_id=record_id,
                title=titles[0],
                authors=_values(fields, "Creator"),
                venue=next(iter(_values(fields, "PeriodicalTitle")), None),
                year=int(year_match.group()) if year_match else None,
                issue=next(iter(_values(fields, "Issue")), None),
                doi=next(iter(_values(fields, "DOI")), None),
                original_organizations=_values(fields, "OriginalOrganization"),
                author_orgs=_values(fields, "AuthorOrg"),
                publish_date=publication or None,
                metadata_online_date=next(iter(_values(fields, "MetadataOnlineDate")), None),
            )
        )
    return tuple(records), found


def _query_title(title: str) -> str:
    # Quoting keeps a remote title from adding PQ operators to the query.
    cleaned = title.replace("\\", " ").replace('"', " ").strip()
    if not cleaned or len(cleaned) > 250:
        raise WanfangError("invalid_title")
    return f'Title:"{cleaned}"'


def fetch_query_page(
    client: httpx.Client,
    title: str,
    app_key: str,
    *,
    start: int,
    query_expression: str | None = None,
    newest_first: bool = False,
    returned_fields: tuple[str, ...] = RETURNED_FIELDS,
) -> tuple[tuple[WanfangRecord, ...], int]:
    if not app_key or not 0 <= start < PAGE_ROWS * MAX_PAGES:
        raise WanfangError("invalid_request")
    try:
        response = client.post(
            QUERY_URL,
            headers={"X-Ca-AppKey": app_key, "Content-Type": "application/json"},
            json={
                "collections": ["OpenPeriodicalChi"],
                "query": query_expression if query_expression is not None else _query_title(title),
                "returned_fields": list(returned_fields),
                "start": start,
                "rows": PAGE_ROWS,
                **(
                    {"sort": {"sorts": [{"by": "PublishDate", "order": "DESC"}]}}
                    if newest_first
                    else {}
                ),
            },
        )
    except httpx.HTTPError as error:
        raise WanfangError("transport_failed") from error
    if urlsplit(str(response.url)).hostname != "api.wfdata.com" or response.is_redirect:
        raise WanfangError("unexpected_redirect")
    if response.status_code >= 400:
        raise WanfangError(f"http_{response.status_code}")
    if "application/json" not in response.headers.get("content-type", "").lower():
        raise WanfangError("unexpected_content_type")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise WanfangError("response_too_large")
    try:
        payload = response.json()
    except ValueError as error:
        raise WanfangError("invalid_json") from error
    return parse_query_response(payload)


def _discovery_query(term: CnkiSearchTerm, year: int | None) -> str:
    cleaned = term.query.replace("\\", " ").replace('"', " ").strip()
    if not cleaned or len(cleaned) > 80 or term.field not in {"title", "theme"}:
        raise WanfangError("invalid_term")
    expression = f'Title:"{cleaned}"' if term.field == "title" else f'"{cleaned}"'
    return f"{expression} AND PublishYear:[{year} TO {year}]" if year else expression


def scan_wanfang_discovery(
    terms: tuple[CnkiSearchTerm, ...],
    *,
    years: tuple[int | None, ...],
    max_terms: int,
    max_pages: int,
    checked_at: datetime,
    app_key: str | None = None,
    key_file: Path | None = None,
    client: httpx.Client | None = None,
) -> WanfangDiscoveryScan:
    """Search the subscribed Chinese journal collection with explicit limits."""

    if not 1 <= max_terms <= 3 or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError("invalid discovery bounds")
    key, key_error = _read_key(app_key, key_file)
    if key_error:
        return WanfangDiscoveryScan((), checked_at, "failed", 0, 0, 0, (key_error,))
    active = client or httpx.Client(timeout=20, follow_redirects=False)
    records: dict[str, WanfangRecord] = {}
    requested = result_pages = 0
    incomplete = max(0, len(terms) - max_terms) * len(years)
    failures: list[str] = []
    try:
        for term in terms[:max_terms]:
            for year in years:
                expression = _discovery_query(term, year)
                for page in range(max_pages):
                    requested += 1
                    try:
                        batch, total = fetch_query_page(
                            active,
                            "",
                            key,
                            start=page * PAGE_ROWS,
                            query_expression=expression,
                            newest_first=True,
                            returned_fields=DISCOVERY_FIELDS,
                        )
                    except WanfangError as error:
                        failures.append(str(error))
                        break
                    result_pages += 1
                    for record in batch:
                        if year is None or record.year == year:
                            records.setdefault(record.record_id, record)
                    if len(batch) < PAGE_ROWS and page * PAGE_ROWS + len(batch) < total:
                        incomplete += 1
                        break
                    if (page + 1) * PAGE_ROWS >= total:
                        break
                    if page + 1 == max_pages:
                        incomplete += 1
    finally:
        if client is None:
            active.close()
    status = "failed" if not result_pages else "partial" if failures or incomplete else "success"
    return WanfangDiscoveryScan(
        tuple(records.values()),
        checked_at,
        status,
        requested,
        result_pages,
        incomplete,
        tuple(failures),
    )


def _read_key(app_key: str | None, key_file: Path | None) -> tuple[str, str | None]:
    if app_key is not None:
        key = app_key
    elif key_file is not None:
        try:
            if key_file.stat().st_size > 4096:
                return "", "key_file_too_large"
            key = key_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return "", "cannot_read_key_file"
    else:
        key = os.environ.get(KEY_ENV, "")
    if any(character.isspace() for character in key):
        return "", "invalid_app_key"
    return (key, None) if key else ("", "missing_app_key")


def _compare(cnki: CnkiRecord, record: WanfangRecord) -> WanfangCheck:
    conflicts = []
    if (
        cnki.authors
        and record.authors
        and (
            len(record.authors) < len(cnki.authors)
            or any(
                not _same(a, b)
                for a, b in zip(cnki.authors, record.authors[: len(cnki.authors)], strict=True)
            )
        )
    ):
        conflicts.append("authors")
    if cnki.venue and record.venue and not _same(cnki.venue, record.venue):
        conflicts.append("venue")
    if cnki.year is not None and record.year is not None and cnki.year != record.year:
        conflicts.append("year")
    if cnki.issue and record.issue and cnki.issue.lstrip("0") != record.issue.lstrip("0"):
        conflicts.append("issue")
    complete = bool(
        cnki.authors
        and record.authors
        and cnki.venue
        and record.venue
        and cnki.year is not None
        and record.year is not None
        and cnki.issue
        and record.issue
    )
    status = "conflict" if conflicts else "corroborated" if complete else "partial"
    return WanfangCheck(
        cnki_url=cnki.url,
        status=status,
        record_id=record.record_id,
        conflicting_fields=tuple(conflicts),
        original_organizations=record.original_organizations if status == "corroborated" else (),
    )


def scan_wanfang_cnki(
    records: tuple[CnkiRecord, ...],
    *,
    max_checks: int,
    checked_at: datetime,
    app_key: str | None = None,
    key_file: Path | None = None,
    client: httpx.Client | None = None,
) -> WanfangScan:
    """Check a small CNKI subset against Wanfang without claiming absence."""

    if isinstance(max_checks, bool) or not 1 <= max_checks <= 10:
        raise ValueError("max_checks must be between 1 and 10")
    key, key_error = _read_key(app_key, key_file)
    if key_error:
        return WanfangScan((), checked_at, "failed", 0, len(records), 0, key_error)
    own_client = client is None
    active = client or httpx.Client(timeout=20, follow_redirects=False)
    checks = []
    incomplete = 0
    attempted = 0
    failure = None
    try:
        for cnki in records[:max_checks]:
            attempted += 1
            matches = []
            for page in range(MAX_PAGES):
                try:
                    found_records, total = fetch_query_page(
                        active, cnki.title, key, start=page * PAGE_ROWS
                    )
                except WanfangError as error:
                    failure = str(error)
                    break
                matches.extend(item for item in found_records if _same(cnki.title, item.title))
                if len(found_records) < PAGE_ROWS and page * PAGE_ROWS + len(found_records) < total:
                    incomplete += 1
                    break
                if (page + 1) * PAGE_ROWS >= total:
                    break
                if page + 1 == MAX_PAGES:
                    incomplete += 1
            if failure:
                break
            if matches:
                options = [_compare(cnki, item) for item in matches]
                checks.append(
                    next(
                        (item for item in options if item.status == "corroborated"),
                        next((item for item in options if item.status == "partial"), options[0]),
                    )
                )
            else:
                checks.append(WanfangCheck(cnki.url, "unverified", None))
    finally:
        if own_client:
            active.close()
    status = (
        "failed"
        if failure and not checks
        else "partial"
        if failure or incomplete or len(records) > max_checks
        else "success"
    )
    return WanfangScan(
        checks=tuple(checks),
        checked_at=checked_at,
        status=status,
        attempted=attempted,
        total_candidates=len(records),
        incomplete=incomplete,
        failure=failure,
    )
