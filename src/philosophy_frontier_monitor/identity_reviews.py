"""Private, pair-specific decisions about ambiguous external bibliographies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .identity import IdentityComparison, IdentityReviewRequired
from .work_type_reviews import FINGERPRINT, RECORD_URL, _valid_evidence_url


class IdentityReviewError(ValueError):
    """An identity decision file is invalid."""


@dataclass(frozen=True, slots=True)
class ReviewedIdentity:
    record_url: str
    source: str
    external_record_id: str
    evidence_fingerprint: str
    decision: str
    evidence_urls: tuple[str, ...]
    reviewed_at: datetime
    note: str


def identity_evidence_fingerprint(
    record_url: str,
    title: str,
    author: str | None,
    comparison: IdentityComparison,
    *,
    source_doi_hints: tuple[str, ...] = (),
) -> str:
    """Bind a decision to both records and every displayed comparison fact."""

    payload = json.dumps(
        {
            "record_url": record_url.rstrip("/"),
            "title": title,
            "author": author or "",
            "source_doi_hints": sorted(source_doi_hints),
            "comparison": asdict(comparison),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_identity_review(
    error: IdentityReviewRequired,
    *,
    record_url: str,
    title: str,
    author: str | None,
    reviews: dict[tuple[str, str, str], ReviewedIdentity] | None,
    source_doi_hints: tuple[str, ...] = (),
) -> tuple[object | None, tuple[IdentityComparison, ...]]:
    """Apply every current pair decision, or retain the unresolved comparisons.

    The oldest dated equivalent is used for the old-work check. A decision only
    settles identity; subsequent date, type, category and window gates still run.
    """

    if not error.comparisons or len(error.comparisons) != len(error.ambiguous_works):
        return None, error.comparisons
    equivalent = list(error.equivalent_works)
    pending: list[IdentityComparison] = []
    for comparison, work in zip(error.comparisons, error.ambiguous_works, strict=True):
        review = (reviews or {}).get(
            (record_url.rstrip("/"), comparison.source, comparison.record_id)
        )
        fingerprint = identity_evidence_fingerprint(
            record_url, title, author, comparison, source_doi_hints=source_doi_hints
        )
        if review is None or review.evidence_fingerprint != fingerprint:
            pending.append(comparison)
        elif review.decision == "equivalent":
            equivalent.append(work)
    if pending:
        return None, tuple(pending)
    unique = {
        (type(work).__name__, getattr(work, "doi", None), getattr(work, "openalex_id", None)): work
        for work in equivalent
    }
    dated = [
        work for work in unique.values() if getattr(work, "publication_date", None) is not None
    ]
    if dated:
        return min(dated, key=lambda work: str(work.publication_date.value)), ()
    return next(iter(unique.values())) if len(unique) == 1 else None, ()


def load_identity_reviews(path: str | Path) -> dict[tuple[str, str, str], ReviewedIdentity]:
    """Read audited pair decisions from the private state directory."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise IdentityReviewError("identity reviews require schema_version 1")
    entries = data.get("reviews")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise IdentityReviewError("reviews must be a list of at most 1000 entries")
    reviews: dict[tuple[str, str, str], ReviewedIdentity] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise IdentityReviewError(f"review {index} must be an object")
        url = entry.get("record_url")
        source = entry.get("source")
        external_id = entry.get("external_record_id")
        fingerprint = entry.get("evidence_fingerprint")
        decision = entry.get("decision")
        evidence_urls = entry.get("evidence_urls")
        note = entry.get("note")
        if not isinstance(url, str) or not RECORD_URL.fullmatch(url):
            raise IdentityReviewError(f"review {index} needs a PhilPapers record URL")
        if source not in {"Crossref", "OpenAlex"}:
            raise IdentityReviewError(f"review {index} needs a supported source")
        if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 500:
            raise IdentityReviewError(f"review {index} needs an external record ID")
        if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint):
            raise IdentityReviewError(f"review {index} needs an evidence fingerprint")
        if decision not in {"equivalent", "distinct"}:
            raise IdentityReviewError(f"review {index} needs equivalent or distinct")
        if (
            not isinstance(evidence_urls, list)
            or not 1 <= len(evidence_urls) <= 8
            or not all(_valid_evidence_url(value) for value in evidence_urls)
        ):
            raise IdentityReviewError(f"review {index} needs 1-8 HTTPS evidence URLs")
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise IdentityReviewError(f"review {index} needs a bounded evidence note")
        try:
            reviewed_at = datetime.fromisoformat(entry["reviewed_at"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as exc:
            raise IdentityReviewError(f"review {index} needs a review time") from exc
        if reviewed_at.tzinfo is None:
            raise IdentityReviewError(f"review {index} needs a timezone-aware review time")
        key = (url.rstrip("/"), source, external_id)
        if key in reviews:
            raise IdentityReviewError(f"duplicate identity review for {key}")
        reviews[key] = ReviewedIdentity(
            record_url=url.rstrip("/"),
            source=source,
            external_record_id=external_id,
            evidence_fingerprint=fingerprint,
            decision=decision,
            evidence_urls=tuple(evidence_urls),
            reviewed_at=reviewed_at,
            note=note.strip(),
        )
    return reviews
