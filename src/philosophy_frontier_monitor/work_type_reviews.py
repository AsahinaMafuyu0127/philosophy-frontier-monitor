"""Private, evidence-bound decisions after inspecting an individual document."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .models import WorkTypeEvidence
from .normalize import normalize_title
from .work_types import (
    CROSSREF_TYPE_MAP,
    OPENALEX_TYPE_MAP,
    PHILARCHIVE_OAI_TYPE_MAP,
    SUPPORTED_CANONICAL_WORK_TYPES,
)

RECORD_URL = re.compile(r"https://(?:www\.)?philpapers\.org/rec/[A-Za-z0-9._~-]+/?")
FINGERPRINT = re.compile(r"[a-f0-9]{64}")
REVIEWABLE_TYPES = SUPPORTED_CANONICAL_WORK_TYPES.union(
    OPENALEX_TYPE_MAP.values(), CROSSREF_TYPE_MAP.values(), PHILARCHIVE_OAI_TYPE_MAP.values()
)


class WorkTypeReviewError(ValueError):
    """A private document-review entry is invalid or cannot be safely applied."""


@dataclass(frozen=True, slots=True)
class ReviewedWorkType:
    record_url: str
    title: str
    work_type: str
    evidence_fingerprint: str
    evidence_urls: tuple[str, ...]
    reviewed_at: datetime
    note: str
    additional_evidence_fingerprints: tuple[str, ...] = ()

    def applies_to(
        self,
        *,
        record_url: str,
        title: str,
        evidence: tuple[WorkTypeEvidence, ...],
    ) -> bool:
        return (
            self.record_url.rstrip("/") == record_url.rstrip("/")
            and normalize_title(self.title) == normalize_title(title)
            and work_type_evidence_fingerprint(evidence)
            in (self.evidence_fingerprint, *self.additional_evidence_fingerprints)
        )


def work_type_evidence_fingerprint(evidence: tuple[WorkTypeEvidence, ...]) -> str:
    """Bind a decision to the exact source labels and record identities seen."""

    rows = sorted(
        (
            item.source,
            item.raw_type,
            item.normalized_type or "",
            item.source_record_id or "",
            item.method,
        )
        for item in evidence
    )
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_work_type_reviews(path: str | Path) -> dict[str, ReviewedWorkType]:
    """Load explicit document reviews; no network request or profile mutation."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise WorkTypeReviewError("work-type reviews require schema_version 1")
    entries = data.get("reviews")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise WorkTypeReviewError("reviews must be a list of at most 1000 entries")
    reviews: dict[str, ReviewedWorkType] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise WorkTypeReviewError(f"review {index} must be an object")
        record_url = entry.get("record_url")
        title = entry.get("title")
        work_type = entry.get("work_type")
        fingerprint = entry.get("evidence_fingerprint")
        additional_fingerprints = entry.get("additional_evidence_fingerprints", [])
        evidence_urls = entry.get("evidence_urls")
        note = entry.get("note")
        reviewed_at = entry.get("reviewed_at")
        if not isinstance(record_url, str) or not RECORD_URL.fullmatch(record_url):
            raise WorkTypeReviewError(f"review {index} needs an exact PhilPapers record URL")
        if not isinstance(title, str) or not title.strip() or len(title) > 1000:
            raise WorkTypeReviewError(f"review {index} needs a bounded title")
        if work_type not in REVIEWABLE_TYPES:
            raise WorkTypeReviewError(f"review {index} has an unsupported work type")
        if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
            raise WorkTypeReviewError(f"review {index} needs a 64-character evidence fingerprint")
        if (
            not isinstance(additional_fingerprints, list)
            or len(additional_fingerprints) > 7
            or not all(
                isinstance(value, str) and FINGERPRINT.fullmatch(value)
                for value in additional_fingerprints
            )
            or len({fingerprint, *additional_fingerprints}) != 1 + len(additional_fingerprints)
        ):
            raise WorkTypeReviewError(f"review {index} has invalid additional fingerprints")
        if (
            not isinstance(evidence_urls, list)
            or not 1 <= len(evidence_urls) <= 8
            or not all(_valid_evidence_url(value) for value in evidence_urls)
        ):
            raise WorkTypeReviewError(f"review {index} needs 1-8 HTTPS evidence URLs")
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise WorkTypeReviewError(f"review {index} needs a bounded evidence note")
        if not isinstance(reviewed_at, str):
            raise WorkTypeReviewError(f"review {index} needs a review time")
        try:
            instant = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise WorkTypeReviewError(f"review {index} has an invalid review time") from error
        if instant.tzinfo is None:
            raise WorkTypeReviewError(f"review {index} needs a timezone-aware review time")
        if record_url in reviews:
            raise WorkTypeReviewError(f"duplicate review for {record_url}")
        reviews[record_url] = ReviewedWorkType(
            record_url=record_url,
            title=title.strip(),
            work_type=work_type,
            evidence_fingerprint=fingerprint,
            evidence_urls=tuple(evidence_urls),
            reviewed_at=instant,
            note=note.strip(),
            additional_evidence_fingerprints=tuple(additional_fingerprints),
        )
    return reviews


def _valid_evidence_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 1000 or any(c.isspace() for c in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username
