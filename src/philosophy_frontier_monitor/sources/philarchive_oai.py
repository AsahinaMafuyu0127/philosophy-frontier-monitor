"""Minimal OAI-PMH client for PhilArchive metadata discovery.

OAI header datestamps are exposed as source-record update evidence only. They
are never mapped to a paper publication date by this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
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
        raise OAIError(f"OAI protocol error {code}: {(error_element.text or '').strip()}")

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
    return OAIPage(
        response_date=root.findtext(f"{{{OAI_NS}}}responseDate"),
        records=tuple(records),
        resumption_token=token,
    )


def iter_records(
    *,
    from_date: str,
    until_date: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    metadata_prefix: str = "oai_dc",
    client: httpx.Client | None = None,
) -> Iterator[OAIRecord]:
    """Yield every page, using only ``resumptionToken`` after the first request."""

    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=60
    )
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
            yield from page.records
            if page.resumption_token is None:
                return
            params = {"verb": "ListRecords", "resumptionToken": page.resumption_token}
    finally:
        if owns_client:
            active_client.close()


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
) -> OAIWindowSnapshot:
    """Harvest all OAI pages and retain records changed in ``[start, end)``.

    The server query deliberately overlaps the requested window by one day,
    following OAI incremental-harvesting guidance.  Exact filtering and
    deduplication happen locally.  The resulting datestamps are source-record
    change evidence, never publication dates.
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
    latest_by_key: dict[str, tuple[datetime, OAIRecord]] = {}
    for record in iter_records(
        from_date=(start - timedelta(days=1)).date().isoformat(),
        until_date=end.date().isoformat(),
        endpoint=endpoint,
        client=client,
    ):
        harvested += 1
        changed_at = parse_oai_datestamp(record.source_datestamp)
        if not start <= changed_at < end:
            overlap_excluded += 1
            continue
        in_window += 1
        key = oai_record_key(record)
        if key is None:
            unkeyed += 1
            continue
        previous = latest_by_key.get(key)
        if previous is not None:
            duplicate_keys += 1
        if previous is None or changed_at >= previous[0]:
            latest_by_key[key] = (changed_at, record)

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
    )
