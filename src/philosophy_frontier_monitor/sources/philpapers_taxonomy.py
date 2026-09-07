"""Authenticated PhilPapers taxonomy retrieval and normalization.

The official endpoint places credentials in query parameters.  This module
therefore never exposes request URLs or exception strings from the HTTP client.
Only a validated, credential-free normalized snapshot may be written to disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..taxonomy import load_taxonomy, require_production_taxonomy

API_URL = "https://philpapers.org/philpapers/raw/categories.json"
API_ID_ENV = "PHILPAPERS_API_ID"
API_KEY_ENV = "PHILPAPERS_API_KEY"
CREDENTIAL_FILE_ENV = "PHILPAPERS_CREDENTIAL_FILE"
DEFAULT_USER_AGENT = "PhilosophyFrontierMonitor/0.1"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MIN_PRODUCTION_CATEGORIES = 2_000
PARSER_VERSION = "0.1.0"
OMITTED_ROOT_ID = "1"


class PhilPapersTaxonomyError(RuntimeError):
    """Raised when retrieval or validation cannot establish a safe snapshot."""


class TaxonomyCredentialError(PhilPapersTaxonomyError):
    """Raised when local environment credentials are absent or malformed."""


class TaxonomyContractError(PhilPapersTaxonomyError):
    """Raised when the remote response does not meet the documented contract."""


@dataclass(frozen=True, slots=True, repr=False)
class PhilPapersCredentials:
    """Credentials whose representations are deliberately always redacted."""

    api_id: str = field(repr=False)
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return "PhilPapersCredentials(api_id=<redacted>, api_key=<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class TaxonomyFetchResult:
    path: Path
    snapshot_id: str
    retrieved_at: datetime
    category_count: int
    source_content_hash: str


def credentials_from_environment(
    environ: Mapping[str, str] | None = None,
) -> PhilPapersCredentials:
    """Read credentials without accepting command-line secret values."""

    source = os.environ if environ is None else environ
    api_id = source.get(API_ID_ENV, "").strip()
    api_key = source.get(API_KEY_ENV, "").strip()
    missing = [name for name, value in ((API_ID_ENV, api_id), (API_KEY_ENV, api_key)) if not value]
    if missing:
        raise TaxonomyCredentialError(
            "missing required local environment variable(s): " + ", ".join(missing)
        )
    return _validated_credentials(api_id, api_key, source="environment variables")


def _validated_credentials(api_id: str, api_key: str, *, source: str) -> PhilPapersCredentials:
    for field_name, value in (("API ID", api_id), ("API key", api_key)):
        if (
            not value
            or len(value) > 512
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise TaxonomyCredentialError(
                f"PhilPapers {field_name} in {source} has an invalid format"
            )
    return PhilPapersCredentials(api_id=api_id, api_key=api_key)


def credentials_from_file(path: str | Path) -> PhilPapersCredentials:
    """Read a small private text file without ever returning its raw contents."""

    credential_path = Path(path)
    try:
        if not credential_path.is_file():
            raise TaxonomyCredentialError("private PhilPapers credential file was not found")
        if credential_path.stat().st_size > 4_096:
            raise TaxonomyCredentialError(
                "private PhilPapers credential file is unexpectedly large"
            )
        text = credential_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise TaxonomyCredentialError(
            "private PhilPapers credential file must be UTF-8 text"
        ) from error

    values: dict[str, str] = {}
    patterns = (
        (
            "api_key",
            re.compile(r"^(?:PHILPAPERS_API_KEY|API\s+KEY)\s*(?:[:=：]\s*)?(.+)$", re.I),
        ),
        (
            "api_id",
            re.compile(
                r"^(?:PHILPAPERS_API_ID|API\s+ID|USER\s+ID)\s*(?:[:=：]\s*)?(.+)$",
                re.I,
            ),
        ),
    )
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        matched = False
        for field_name, pattern in patterns:
            match = pattern.fullmatch(line)
            if match is None:
                continue
            if field_name in values:
                raise TaxonomyCredentialError(
                    f"private PhilPapers credential file repeats a field at line {line_number}"
                )
            values[field_name] = match.group(1).strip()
            matched = True
            break
        if not matched:
            raise TaxonomyCredentialError(
                f"private PhilPapers credential file has an unrecognized line {line_number}"
            )

    missing = [name for name in ("api_id", "api_key") if not values.get(name)]
    if missing:
        raise TaxonomyCredentialError(
            "private PhilPapers credential file is missing required labelled field(s): "
            + ", ".join(missing)
        )
    return _validated_credentials(
        values["api_id"],
        values["api_key"],
        source="private credential file",
    )


def load_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    credential_file: str | Path | None = None,
) -> PhilPapersCredentials:
    """Prefer a complete environment pair, otherwise read the private file."""

    source = os.environ if environ is None else environ
    if source.get(API_ID_ENV) or source.get(API_KEY_ENV):
        return credentials_from_environment(source)
    selected_file = credential_file or source.get(CREDENTIAL_FILE_ENV)
    if selected_file is None:
        raise TaxonomyCredentialError(
            "no PhilPapers credentials were found in environment variables or a private file"
        )
    return credentials_from_file(selected_file)


def fetch_taxonomy_bytes(
    credentials: PhilPapersCredentials,
    *,
    client: httpx.Client | None = None,
    maximum_bytes: int = MAX_RESPONSE_BYTES,
) -> bytes:
    """Fetch the fixed HTTPS endpoint with bounded memory and redacted failures."""

    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be positive")
    owns_client = client is None
    active_client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
        timeout=httpx.Timeout(30.0),
    )
    try:
        try:
            with active_client.stream(
                "GET",
                API_URL,
                params={"apiId": credentials.api_id, "apiKey": credentials.api_key},
                headers={"Accept": "application/json"},
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise PhilPapersTaxonomyError(
                        "PhilPapers taxonomy endpoint returned an unexpected redirect"
                    )
                if (
                    response.status_code == 403
                    and response.headers.get("cf-mitigated", "").casefold() == "challenge"
                ):
                    raise PhilPapersTaxonomyError(
                        "Cloudflare challenged the authenticated PhilPapers API request "
                        "(HTTP 403); use the documented browser-download import path or "
                        "contact PhilPapers"
                    )
                if response.status_code in {401, 403}:
                    raise PhilPapersTaxonomyError(
                        "PhilPapers rejected the taxonomy request or site protection blocked it "
                        f"(HTTP {response.status_code})"
                    )
                if response.status_code == 429:
                    raise PhilPapersTaxonomyError(
                        "PhilPapers taxonomy endpoint rate-limited the request (HTTP 429)"
                    )
                if response.status_code != 200:
                    raise PhilPapersTaxonomyError(
                        "PhilPapers taxonomy endpoint returned "
                        f"an unexpected status (HTTP {response.status_code})"
                    )

                content_type = response.headers.get("content-type", "").casefold()
                if "json" not in content_type:
                    raise TaxonomyContractError(
                        "PhilPapers taxonomy response is not JSON; no snapshot was written"
                    )
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        too_large = int(declared_length) > maximum_bytes
                    except ValueError:
                        too_large = True
                    if too_large:
                        raise TaxonomyContractError(
                            "PhilPapers taxonomy response exceeds the configured size limit"
                        )

                chunks: list[bytes] = []
                byte_count = 0
                for chunk in response.iter_bytes():
                    byte_count += len(chunk)
                    if byte_count > maximum_bytes:
                        raise TaxonomyContractError(
                            "PhilPapers taxonomy response exceeds the configured size limit"
                        )
                    chunks.append(chunk)
                if not chunks:
                    raise TaxonomyContractError("PhilPapers taxonomy response is empty")
                return b"".join(chunks)
        except httpx.HTTPError as error:
            # httpx errors may embed the full credential-bearing request URL.
            raise PhilPapersTaxonomyError(
                "PhilPapers taxonomy request failed at the HTTPS transport layer; "
                "the request URL was suppressed"
            ) from error
    finally:
        if owns_client:
            active_client.close()


def _read_field(item: Mapping[str, Any], aliases: tuple[str, ...], index: int) -> Any:
    present = [name for name in aliases if name in item]
    if len(present) != 1:
        raise TaxonomyContractError(
            f"taxonomy entry {index} must contain exactly one of: {', '.join(aliases)}"
        )
    return item[present[0]]


def _documented_fields(item: Any, index: int) -> tuple[Any, Any, Any, Any]:
    """Return name, ID, parents, and primary parent in official documented order."""

    if isinstance(item, Mapping):
        return (
            _read_field(item, ("name", "category_name"), index),
            _read_field(item, ("id", "category_id", "catId"), index),
            _read_field(item, ("parents", "parent_ids", "parentIds"), index),
            _read_field(
                item,
                (
                    "primaryParent",
                    "primary_parent",
                    "primary_parent_id",
                    "primaryParentId",
                ),
                index,
            ),
        )
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
        if len(item) != 4:
            raise TaxonomyContractError(
                f"taxonomy entry {index} must have four documented positional fields"
            )
        return item[0], item[1], item[2], item[3]
    raise TaxonomyContractError(f"taxonomy entry {index} has an unsupported JSON shape")


def _category_id(value: Any, index: int, field_name: str) -> str:
    if isinstance(value, bool):
        raise TaxonomyContractError(f"taxonomy entry {index} has invalid {field_name}")
    text = str(value).strip() if isinstance(value, (str, int)) else ""
    if not text.isdecimal() or int(text) < 1:
        raise TaxonomyContractError(f"taxonomy entry {index} has invalid {field_name}")
    return str(int(text))


def _category_name(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise TaxonomyContractError(f"taxonomy entry {index} has a non-text name")
    name = value.strip()
    if (
        not name
        or len(name) > 500
        or "<" in name
        or ">" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise TaxonomyContractError(f"taxonomy entry {index} has an unsafe category name")
    return name


def _parent_ids(value: Any, index: int) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    raw_values: Sequence[Any]
    if isinstance(value, str):
        raw_values = tuple(part.strip() for part in value.split(",") if part.strip())
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw_values = value
    elif isinstance(value, int) and not isinstance(value, bool):
        raw_values = (value,)
    else:
        raise TaxonomyContractError(f"taxonomy entry {index} has invalid parent IDs")
    parsed = tuple(_category_id(item, index, "parent ID") for item in raw_values)
    return tuple(dict.fromkeys(item for item in parsed if item != OMITTED_ROOT_ID))


def _primary_parent(value: Any, index: int) -> str | None:
    if value is None or value == "":
        return None
    parsed = _category_id(value, index, "primary parent ID")
    return None if parsed == OMITTED_ROOT_ID else parsed


def normalize_taxonomy_payload(
    raw_bytes: bytes,
    *,
    retrieved_at: datetime | None = None,
    minimum_categories: int = MIN_PRODUCTION_CATEGORIES,
) -> dict[str, Any]:
    """Parse the documented feed shapes into the internal snapshot schema."""

    if minimum_categories < 1:
        raise ValueError("minimum_categories must be positive")
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TaxonomyContractError(
            "PhilPapers taxonomy response is not valid UTF-8 JSON"
        ) from error

    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, Mapping) and isinstance(payload.get("categories"), list):
        entries = payload["categories"]
    else:
        raise TaxonomyContractError("PhilPapers taxonomy JSON has an unsupported top-level shape")
    if len(entries) < minimum_categories:
        raise TaxonomyContractError(
            "PhilPapers taxonomy response is incomplete: "
            f"expected at least {minimum_categories} categories, received {len(entries)}"
        )

    parsed_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(entries):
        raw_name, raw_id, raw_parents, raw_primary = _documented_fields(item, index)
        category_id = _category_id(raw_id, index, "category ID")
        if category_id == OMITTED_ROOT_ID:
            raise TaxonomyContractError(
                "taxonomy unexpectedly includes documented omitted root ID 1"
            )
        if category_id in seen_ids:
            raise TaxonomyContractError(f"taxonomy contains duplicate category ID {category_id}")
        seen_ids.add(category_id)
        parent_ids = _parent_ids(raw_parents, index)
        primary_parent_id = _primary_parent(raw_primary, index)
        if primary_parent_id is not None and primary_parent_id not in parent_ids:
            raise TaxonomyContractError(
                f"taxonomy entry {index} has a primary parent absent from its parent list"
            )
        if not isinstance(raw_name, str):
            raise TaxonomyContractError(f"taxonomy entry {index} has a non-text name")
        parsed_rows.append(
            {
                "category_id": category_id,
                "category_name": (_category_name(raw_name, index) if raw_name.strip() else None),
                "parent_ids": list(parent_ids),
                "primary_parent_id": primary_parent_id,
                "active": True,
                "source_index": index,
            }
        )

    parent_references = {parent_id for row in parsed_rows for parent_id in row["parent_ids"]}
    categories: list[dict[str, Any]] = []
    excluded_source_records: list[dict[str, str]] = []
    for row in parsed_rows:
        if row["category_name"] is None:
            if row["category_id"] in parent_references:
                raise TaxonomyContractError(
                    f"unnamed taxonomy entry {row['source_index']} is referenced as a parent"
                )
            excluded_source_records.append(
                {
                    "category_id": row["category_id"],
                    "reason": "empty_category_name_unreferenced_leaf",
                }
            )
            continue
        row.pop("source_index")
        categories.append(row)

    categories.sort(key=lambda item: int(item["category_id"]))
    category_ids_by_name: dict[str, list[str]] = {}
    display_names: dict[str, str] = {}
    for category in categories:
        name_key = category["category_name"].casefold().strip()
        display_names.setdefault(name_key, category["category_name"])
        category_ids_by_name.setdefault(name_key, []).append(category["category_id"])
    duplicate_name_groups = [
        {
            "category_name": display_names[name_key],
            "category_ids": sorted(category_ids, key=int),
        }
        for name_key, category_ids in sorted(category_ids_by_name.items())
        if len(category_ids) > 1
    ]
    source_digest = hashlib.sha256(raw_bytes).hexdigest()
    instant = retrieved_at or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("retrieved_at must include a timezone")
    instant = instant.astimezone(UTC)
    timestamp = instant.strftime("%Y%m%dT%H%M%SZ")
    return {
        "schema_version": "0.1",
        "snapshot_id": f"philpapers:{timestamp}:{source_digest[:12]}",
        "source": "philpapers-taxonomy-json-api",
        "retrieved_at": instant.isoformat().replace("+00:00", "Z"),
        "source_url": API_URL,
        "source_content_hash": "sha256:" + source_digest,
        "source_record_count": len(parsed_rows),
        "category_count": len(categories),
        "excluded_source_records": excluded_source_records,
        "duplicate_name_groups": duplicate_name_groups,
        "complete": True,
        "fixture": False,
        "parser_version": PARSER_VERSION,
        "omitted_root_id": OMITTED_ROOT_ID,
        "notes": (
            "Normalized from the authenticated official category feed. Root category ID 1 "
            "is omitted by the source; references to it were removed from normalized parents. "
            "Unusable source records and duplicate names are retained as explicit audit metadata."
        ),
        "categories": categories,
    }


def write_taxonomy_snapshot(
    payload: Mapping[str, Any],
    output_directory: str | Path,
    *,
    minimum_categories: int = MIN_PRODUCTION_CATEGORIES,
) -> TaxonomyFetchResult:
    """Validate a temporary file completely, then atomically publish the snapshot."""

    output_dir = Path(output_directory).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_id = str(payload["snapshot_id"])
    identifier_match = re.fullmatch(
        r"philpapers:(\d{8}T\d{6}Z):([0-9a-f]{12})",
        snapshot_id,
    )
    if identifier_match is None:
        raise TaxonomyContractError("normalized taxonomy has an invalid snapshot ID")
    timestamp, safe_suffix = identifier_match.groups()
    final_path = output_dir / f"philpapers-taxonomy-{timestamp}-{safe_suffix}.json"
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if final_path.exists():
        if final_path.read_bytes() == encoded:
            existing = load_taxonomy(final_path)
            require_production_taxonomy(existing)
            return TaxonomyFetchResult(
                path=final_path,
                snapshot_id=existing.snapshot_id,
                retrieved_at=existing.retrieved_at,
                category_count=existing.category_count,
                source_content_hash=existing.source_content_hash or "",
            )
        raise TaxonomyContractError(
            "taxonomy snapshot path collision; the existing historical snapshot was retained"
        )
    temporary_path = output_dir / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary_path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        snapshot = load_taxonomy(temporary_path)
        require_production_taxonomy(snapshot)
        if snapshot.category_count < minimum_categories:
            raise TaxonomyContractError(
                "normalized taxonomy is incomplete: "
                f"expected at least {minimum_categories} categories, "
                f"received {snapshot.category_count}"
            )
        temporary_path.replace(final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return TaxonomyFetchResult(
        path=final_path,
        snapshot_id=snapshot.snapshot_id,
        retrieved_at=snapshot.retrieved_at,
        category_count=snapshot.category_count,
        source_content_hash=snapshot.source_content_hash or "",
    )


def fetch_and_write_taxonomy(
    output_directory: str | Path,
    credentials: PhilPapersCredentials,
    *,
    client: httpx.Client | None = None,
    retrieved_at: datetime | None = None,
    minimum_categories: int = MIN_PRODUCTION_CATEGORIES,
) -> TaxonomyFetchResult:
    """Retrieve, normalize, validate, and atomically save one complete snapshot."""

    raw_bytes = fetch_taxonomy_bytes(credentials, client=client)
    payload = normalize_taxonomy_payload(
        raw_bytes,
        retrieved_at=retrieved_at,
        minimum_categories=minimum_categories,
    )
    return write_taxonomy_snapshot(
        payload,
        output_directory,
        minimum_categories=minimum_categories,
    )


def import_and_write_taxonomy(
    input_file: str | Path,
    output_directory: str | Path,
    *,
    retrieved_at: datetime | None = None,
    minimum_categories: int = MIN_PRODUCTION_CATEGORIES,
) -> TaxonomyFetchResult:
    """Validate a browser-downloaded official JSON file and save a snapshot."""

    source_path = Path(input_file)
    if not source_path.is_file():
        raise TaxonomyContractError("browser-downloaded taxonomy JSON file was not found")
    size = source_path.stat().st_size
    if size < 1:
        raise TaxonomyContractError("browser-downloaded taxonomy JSON file is empty")
    if size > MAX_RESPONSE_BYTES:
        raise TaxonomyContractError(
            "browser-downloaded taxonomy JSON file exceeds the configured size limit"
        )
    raw_bytes = source_path.read_bytes()
    payload = normalize_taxonomy_payload(
        raw_bytes,
        retrieved_at=retrieved_at,
        minimum_categories=minimum_categories,
    )
    return write_taxonomy_snapshot(
        payload,
        output_directory,
        minimum_categories=minimum_categories,
    )
