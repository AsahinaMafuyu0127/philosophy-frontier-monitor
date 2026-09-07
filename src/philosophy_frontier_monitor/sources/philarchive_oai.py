"""Minimal OAI-PMH client for PhilArchive metadata discovery.

OAI header datestamps are exposed as source-record update evidence only. They
are never mapped to a paper publication date by this module.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree

import httpx

from ..http_retry import BoundedRequestError, request_with_retry

OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DC_NS = "http://purl.org/dc/elements/1.1/"
DEFAULT_ENDPOINT = "https://philarchive.org/oai.pl"
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"


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


def parse_oai_page(xml_text: str) -> OAIPage:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise OAIError("OAI response is not valid XML") from error

    error_element = root.find(f"{{{OAI_NS}}}error")
    if error_element is not None:
        code = error_element.get("code", "unknown")
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
