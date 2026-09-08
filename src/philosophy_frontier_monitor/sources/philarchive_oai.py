"""Minimal OAI-PMH client for PhilArchive metadata discovery.

OAI header datestamps are exposed as source-record update evidence only. They
are never mapped to a paper publication date by this module.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import unquote
from xml.etree import ElementTree

import httpx

from ..http_retry import BoundedRequestError, request_with_retry

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
DEFAULT_ENDPOINT = "https://philarchive.org/oai.pl"
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"
RECORD_KEY = re.compile(r"/rec/([^/?#]+)", re.IGNORECASE)


class OAIError(RuntimeError):
    pass


class OAIProtocolError(OAIError):
    """One explicit protocol-level error returned by an OAI repository."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        suffix = f": {detail}" if detail else ""
        super().__init__(f"OAI protocol error {code}{suffix}")


@dataclass(frozen=True, slots=True)
class OAIRecord:
    identifier: str
    source_datestamp: str
    deleted: bool
    fields: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class OAIPage:
    response_date: str | None
    records: tuple[OAIRecord, ...]
    resumption_token: str | None
    resumption_expiration: datetime | None = None
    complete_list_size: int | None = None
    cursor: int | None = None


PageObserver = Callable[[OAIPage, tuple[OAIRecord, ...], int, int], None]


@dataclass(frozen=True, slots=True)
class OAIWindowSnapshot:
    """A complete incremental harvest narrowed to one exact UTC window.

    ``records_by_key`` contains only the latest non-deleted record for each
    PhilArchive ``/rec/`` key.  Counts retain deleted, unkeyed, duplicate and
    overlap records so callers can audit why the harvest differs from the
    final candidate intersection.
    """

    window_start: datetime
    window_end: datetime
    checked_at: datetime
    records_by_key: dict[str, OAIRecord]
    harvested_records: int
    records_in_exact_window: int
    deleted_records: int
    unkeyed_records: int
    duplicate_keys: int
    overlap_records_excluded: int
    record_events: tuple[OAIRecord, ...] = ()
    retrieval_mode: str = "network"
    network_harvested_records: int = 0
    cache_records_written: int = 0
    cache_refresh_windows: int = 0
    cache_coverage_start: datetime | None = None
    cache_coverage_end: datetime | None = None
    cache_resumed_sessions: int = 0
    cache_expired_token_restarts: int = 0
    cache_invalid_token_restarts: int = 0


def parse_oai_page(xml_text: str) -> OAIPage:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise OAIError("OAI response is not valid XML") from error

    error_element = root.find(f"{{{OAI_NS}}}error")
    if error_element is not None:
        code = error_element.get("code", "unknown")
        if code == "noRecordsMatch":
            return OAIPage(
                response_date=root.findtext(f"{{{OAI_NS}}}responseDate"),
                records=(),
                resumption_token=None,
            )
        raise OAIProtocolError(code, (error_element.text or "").strip())

    records: list[OAIRecord] = []
    for record_element in root.findall(f".//{{{OAI_NS}}}record"):
        header = record_element.find(f"{{{OAI_NS}}}header")
        if header is None:
            continue
        identifier = header.findtext(f"{{{OAI_NS}}}identifier")
        datestamp = header.findtext(f"{{{OAI_NS}}}datestamp")
        if not identifier or not datestamp:
            continue
        fields: dict[str, list[str]] = {}
        metadata = record_element.find(f"{{{OAI_NS}}}metadata")
        if metadata is not None:
            for element in metadata.iter():
                if element.tag.startswith(f"{{{DC_NS}}}") and element.text:
                    name = element.tag.split("}", 1)[1]
                    fields.setdefault(name, []).append(element.text.strip())
        records.append(
            OAIRecord(
                identifier=identifier,
                source_datestamp=datestamp,
                deleted=header.get("status") == "deleted",
                fields={name: tuple(values) for name, values in fields.items()},
            )
        )

    token_element = root.find(f".//{{{OAI_NS}}}resumptionToken")
    token = None
    if token_element is not None and token_element.text and token_element.text.strip():
        token = token_element.text.strip()
    expiration = None
    complete_list_size = None
    cursor = None
    if token_element is not None:
        expiration_text = token_element.get("expirationDate")
        if expiration_text:
            try:
                expiration = datetime.fromisoformat(expiration_text.replace("Z", "+00:00"))
            except ValueError as error:
                raise OAIError("invalid OAI resumptionToken expirationDate") from error
            if expiration.tzinfo is None:
                raise OAIError("OAI resumptionToken expirationDate lacks a timezone")
            expiration = expiration.astimezone(UTC)
        complete_list_size = _optional_nonnegative_integer(
            token_element.get("completeListSize"),
            "completeListSize",
        )
        cursor = _optional_nonnegative_integer(token_element.get("cursor"), "cursor")
    return OAIPage(
        response_date=root.findtext(f"{{{OAI_NS}}}responseDate"),
        records=tuple(records),
        resumption_token=token,
        resumption_expiration=expiration,
        complete_list_size=complete_list_size,
        cursor=cursor,
    )


def _optional_nonnegative_integer(value: str | None, field: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise OAIError(f"invalid OAI resumptionToken {field}") from error
    if parsed < 0:
        raise OAIError(f"invalid OAI resumptionToken {field}")
    return parsed


def iter_pages(
    *,
    from_date: str | None = None,
    until_date: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    metadata_prefix: str = "oai_dc",
    resumption_token: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[OAIPage]:
    """Yield complete OAI pages, treating a resumption token as opaque state."""

    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=60
    )
    if resumption_token is not None:
        params = {"verb": "ListRecords", "resumptionToken": resumption_token}
    else:
        if from_date is None:
            raise OAIError("an initial OAI request requires from_date")
        params = {"verb": "ListRecords", "metadataPrefix": metadata_prefix, "from": from_date}
        if until_date:
            params["until"] = until_date
    try:
        while True:
            try:
                response = request_with_retry(
                    lambda params=params: active_client.get(endpoint, params=params),
                    source="PhilArchive OAI",
                )
            except BoundedRequestError as error:
                raise OAIError(f"OAI request failed: {error}") from error
            page = parse_oai_page(response.text)
            yield page
            if page.resumption_token is None:
                return
            params = {"verb": "ListRecords", "resumptionToken": page.resumption_token}
    finally:
        if owns_client:
            active_client.close()


def iter_records(
    *,
    from_date: str,
    until_date: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    metadata_prefix: str = "oai_dc",
    client: httpx.Client | None = None,
    page_progress: Callable[[int, int], None] | None = None,
) -> Iterator[OAIRecord]:
    """Yield every record, using only ``resumptionToken`` after the first request."""

    harvested_records = 0
    for completed_pages, page in enumerate(
        iter_pages(
            from_date=from_date,
            until_date=until_date,
            endpoint=endpoint,
            metadata_prefix=metadata_prefix,
            client=client,
        ),
        start=1,
    ):
        harvested_records += len(page.records)
        if page_progress is not None:
            page_progress(completed_pages, harvested_records)
        yield from page.records


def parse_oai_datestamp(value: str) -> datetime:
    """Parse OAI day or second granularity without treating it as publication time."""

    normalized = value.strip()
    try:
        if len(normalized) == 10:
            return datetime.combine(date.fromisoformat(normalized), time.min, tzinfo=UTC)
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise OAIError(f"invalid OAI datestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise OAIError(f"OAI datestamp lacks a timezone: {value!r}")
    return parsed.astimezone(UTC)


def record_key(value: str) -> str | None:
    """Return a case-insensitive key shared by PhilArchive and PhilPapers records."""

    match = RECORD_KEY.search(unquote(value.strip()))
    if match is None:
        return None
    key = match.group(1).strip().rstrip("/")
    return key.casefold() if key else None


def oai_record_key(record: OAIRecord) -> str | None:
    """Extract the stable ``/rec/`` key from the OAI header or dc:identifier."""

    for value in (record.identifier, *record.fields.get("identifier", ())):
        key = record_key(value)
        if key is not None:
            return key
    return None


def load_recent_window(
    window_start: datetime,
    window_end: datetime,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    client: httpx.Client | None = None,
    page_progress: Callable[[int, int], None] | None = None,
    resumption_token: str | None = None,
    progress_page_offset: int = 0,
    progress_record_offset: int = 0,
    page_observer: PageObserver | None = None,
) -> OAIWindowSnapshot:
    """Harvest all OAI pages and retain records changed in ``[start, end)``.

    PhilArchive exposes second-granularity datestamps, so the server query
    deliberately overlaps the requested window by one second following OAI
    incremental-harvesting guidance.  Its inclusive ``until`` parameter is
    set to the last whole second before the half-open local boundary.  Exact
    filtering and deduplication still happen locally.  The resulting
    datestamps are source-record change evidence, never publication dates.
    """

    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise OAIError("OAI window must be timezone-aware")
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if start >= end:
        raise OAIError("OAI window start must be earlier than its end")

    harvested = 0
    in_window = 0
    deleted = 0
    unkeyed = 0
    duplicate_keys = 0
    overlap_excluded = 0
    exact_records: list[OAIRecord] = []
    latest_by_key: dict[str, tuple[datetime, OAIRecord]] = {}
    query_start = start.replace(microsecond=0) - timedelta(seconds=1)
    query_end = (end - timedelta(microseconds=1)).replace(microsecond=0)
    completed_pages = 0
    for page in iter_pages(
        from_date=query_start.isoformat().replace("+00:00", "Z"),
        until_date=query_end.isoformat().replace("+00:00", "Z"),
        endpoint=endpoint,
        client=client,
        resumption_token=resumption_token,
    ):
        completed_pages += 1
        page_exact_records: list[OAIRecord] = []
        page_overlap_excluded = 0
        for record in page.records:
            harvested += 1
            changed_at = parse_oai_datestamp(record.source_datestamp)
            if not start <= changed_at < end:
                overlap_excluded += 1
                page_overlap_excluded += 1
                continue
            in_window += 1
            exact_records.append(record)
            page_exact_records.append(record)
            key = oai_record_key(record)
            if key is None:
                unkeyed += 1
                continue
            previous = latest_by_key.get(key)
            if previous is not None:
                duplicate_keys += 1
            if previous is None or changed_at >= previous[0]:
                latest_by_key[key] = (changed_at, record)
        if page_observer is not None:
            page_observer(
                page,
                tuple(page_exact_records),
                len(page.records),
                page_overlap_excluded,
            )
        if page_progress is not None:
            page_progress(
                progress_page_offset + completed_pages,
                progress_record_offset + harvested,
            )

    active: dict[str, OAIRecord] = {}
    for key, (_changed_at, record) in latest_by_key.items():
        if record.deleted:
            deleted += 1
        else:
            active[key] = record
    return OAIWindowSnapshot(
        window_start=start,
        window_end=end,
        checked_at=datetime.now(UTC),
        records_by_key=active,
        harvested_records=harvested,
        records_in_exact_window=in_window,
        deleted_records=deleted,
        unkeyed_records=unkeyed,
        duplicate_keys=duplicate_keys,
        overlap_records_excluded=overlap_excluded,
        record_events=tuple(exact_records),
        retrieval_mode="network",
        network_harvested_records=harvested,
    )
