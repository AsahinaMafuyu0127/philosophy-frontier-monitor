"""Normalize structured bibliographic work types without content inference."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import WorkTypeEvidence, WorkTypeStatus

SAFE_TYPE_VALUE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# These are document forms that satisfy the project's default "paper" scope.
# Quality, importance, and topical relevance are decided nowhere in this module.
SUPPORTED_CANONICAL_WORK_TYPES = frozenset(
    {
        "article",
        "conference-paper",
        "data-paper",
        "forthcoming-article",
        "manuscript",
        "preprint",
        "review-article",
        "software-paper",
        "working-paper",
    }
)

# Backward-compatible values that can still occur in fixtures or older records.
SUPPORTED_LEGACY_WORK_TYPES = frozenset(
    {
        "author-accepted-manuscript",
        "author_accepted_manuscript",
        "journal-article",
        "journal_article",
        "posted-content",
        "proceedings-article",
        "submitted-manuscript",
        "submitted_manuscript",
        "working_paper",
    }
)

OPENALEX_TYPE_MAP = {
    "article": "article",
    "book": "book",
    "book-chapter": "book-chapter",
    "book-review": "book-review",
    "conference-abstract": "conference-abstract",
    "conference-paper": "conference-paper",
    "data-paper": "data-paper",
    "dataset": "dataset",
    "dissertation": "dissertation",
    "editorial": "editorial",
    "erratum": "erratum",
    "letter": "letter",
    "libguides": "libguides",
    "other": "other",
    "paratext": "paratext",
    "peer-review": "peer-review",
    "preprint": "preprint",
    "reference-entry": "reference-entry",
    "report": "working-paper",
    "retraction": "retraction",
    "review": "review-article",
    "software": "software",
    "software-paper": "software-paper",
    "standard": "standard",
    "supplementary-materials": "supplementary-materials",
}

CROSSREF_TYPE_MAP = {
    "book": "book",
    "book-chapter": "book-chapter",
    "book-part": "book-chapter",
    "book-section": "book-chapter",
    "book-series": "book",
    "book-set": "book",
    "book-track": "book-chapter",
    "component": "other",
    "database": "dataset",
    "dataset": "dataset",
    "dissertation": "dissertation",
    "edited-book": "book",
    "grant": "grant",
    "journal": "journal-container",
    "journal-article": "article",
    "journal-issue": "journal-container",
    "journal-volume": "journal-container",
    "monograph": "book",
    "other": "other",
    "peer-review": "peer-review",
    "posted-content": "preprint",
    "proceedings": "proceedings-container",
    "proceedings-article": "conference-paper",
    "proceedings-series": "proceedings-container",
    "reference-book": "book",
    "reference-entry": "reference-entry",
    "report": "working-paper",
    "report-component": "other",
    "report-series": "report-container",
    "standard": "standard",
}

PHILPAPERS_HINT_TYPE_MAP = {
    "author-accepted-manuscript": "manuscript",
    "forthcoming-article": "forthcoming-article",
    "manuscript": "manuscript",
    "preprint": "preprint",
    "submitted-manuscript": "manuscript",
    "working-paper": "working-paper",
}

LOCAL_EXPLICIT_TYPE_MAP = {"book-review": "book-review"}

PHILARCHIVE_OAI_TYPE_MAP = {
    "article": "article",
    "book": "book",
    "bookpart": "book-chapter",
    "bachelorthesis": "dissertation",
    "masterthesis": "dissertation",
    "doctoralthesis": "dissertation",
    "workingpaper": "working-paper",
    "preprint": "preprint",
}

SOURCE_TYPE_MAPS = {
    "crossref": CROSSREF_TYPE_MAP,
    "openalex": OPENALEX_TYPE_MAP,
    "philarchive-oai": PHILARCHIVE_OAI_TYPE_MAP,
    "philpapers-rss-description": PHILPAPERS_HINT_TYPE_MAP,
    "philpapers-title-label": LOCAL_EXPLICIT_TYPE_MAP,
}

TYPE_PREFERENCE = {
    "book-review": 0,
    "book-chapter": 1,
    "book": 2,
    "review-article": 3,
    "data-paper": 4,
    "software-paper": 5,
    "conference-paper": 6,
    "forthcoming-article": 7,
    "article": 8,
    "preprint": 9,
    "manuscript": 10,
    "working-paper": 11,
}


@dataclass(frozen=True, slots=True)
class WorkTypeSignal:
    source: str
    raw_type: str
    source_record_id: str | None = None
    method: str = "structured"


@dataclass(frozen=True, slots=True)
class WorkTypeResolution:
    work_type: str
    status: WorkTypeStatus
    evidence: tuple[WorkTypeEvidence, ...]

    @property
    def usable(self) -> bool:
        return self.status not in {WorkTypeStatus.CONFLICT, WorkTypeStatus.UNKNOWN}


def _safe_raw_type(value: str) -> str | None:
    normalized = value.strip().casefold().replace("_", "-")
    if len(normalized) > 64 or not SAFE_TYPE_VALUE.fullmatch(normalized):
        return None
    return normalized


def normalize_source_work_type(source: str, raw_type: str) -> str | None:
    """Map one provider's controlled type to the project's canonical vocabulary."""

    if source == "philarchive-oai":
        prefix = "info:eu-repo/semantics/"
        normalized_uri = raw_type.strip().casefold()
        if normalized_uri.startswith(prefix):
            raw_type = normalized_uri.removeprefix(prefix)
    safe_raw = _safe_raw_type(raw_type)
    if safe_raw is None:
        return None
    mapping = SOURCE_TYPE_MAPS.get(source)
    if mapping is None:
        return None
    return mapping.get(safe_raw)


def resolve_work_type(
    signals: tuple[WorkTypeSignal, ...],
    *,
    default_type: str = "article",
) -> WorkTypeResolution:
    """Resolve controlled source labels and fail closed on admission conflicts."""

    evidence = tuple(
        WorkTypeEvidence(
            source=signal.source,
            raw_type=_safe_raw_type(signal.raw_type) or "unrecognized",
            normalized_type=normalize_source_work_type(signal.source, signal.raw_type),
            source_record_id=signal.source_record_id,
            method=signal.method,
        )
        for signal in signals
        if isinstance(signal.raw_type, str) and signal.raw_type.strip()
    )
    return resolve_work_type_evidence(evidence, default_type=default_type)


def resolve_work_type_evidence(
    evidence: tuple[WorkTypeEvidence, ...],
    *,
    default_type: str = "article",
) -> WorkTypeResolution:
    """Resolve already-normalized evidence when duplicate work records are merged."""

    recognized = {item.normalized_type for item in evidence if item.normalized_type is not None}
    if not evidence:
        return WorkTypeResolution(default_type, WorkTypeStatus.DEFAULTED, ())
    if any(item.normalized_type is None for item in evidence):
        return WorkTypeResolution("unknown", WorkTypeStatus.UNKNOWN, evidence)

    supported = recognized.intersection(SUPPORTED_CANONICAL_WORK_TYPES)
    unsupported = recognized.difference(SUPPORTED_CANONICAL_WORK_TYPES)
    if supported and unsupported:
        return WorkTypeResolution("conflict", WorkTypeStatus.CONFLICT, evidence)

    selected = min(recognized, key=lambda item: (TYPE_PREFERENCE.get(item, 100), item))
    status = WorkTypeStatus.CONFIRMED if len(recognized) == 1 else WorkTypeStatus.COMPATIBLE
    if any(item.method == "explicit-bibliographic-label" for item in evidence):
        status = WorkTypeStatus.EXPLICIT_LABEL
    return WorkTypeResolution(selected, status, evidence)


def is_supported_work_type(value: str) -> bool:
    """Accept canonical types and the explicit legacy vocabulary only."""

    normalized = _safe_raw_type(value)
    return normalized in SUPPORTED_CANONICAL_WORK_TYPES.union(SUPPORTED_LEGACY_WORK_TYPES)
