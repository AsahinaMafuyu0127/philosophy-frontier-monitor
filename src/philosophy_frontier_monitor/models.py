"""Typed domain model for the monitor.

The types in this module mirror the semantic contracts in ``references/``.
They intentionally do not contain quality scores, relevance scores, or ranks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    SECOND = "second"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    CONFIRMED_NEW = "confirmed_new"
    CONFIRMED_SOURCE_ARRIVAL = "confirmed_source_arrival"
    PREVIOUSLY_NOTIFIED_VERSION_UPDATE = "previously_notified_version_update"
    NEWLY_INDEXED_OLD_WORK = "newly_indexed_old_work"
    SOURCE_RECORD_UPDATED = "source_record_updated"
    REDISCOVERED = "rediscovered"
    BACKFILL = "backfill"
    UNCERTAIN = "uncertain"
    NOT_NEW = "not_new"


class CategoryStatus(StrEnum):
    AVAILABLE = "available"
    AWAITING_CATEGORIES = "awaiting_categories"
    TAXONOMY_UNAVAILABLE = "taxonomy_unavailable"
    MAPPING_UNCERTAIN = "mapping_uncertain"
    NOT_APPLICABLE = "not_applicable"
    EXPIRED_UNCLASSIFIED = "expired_unclassified"


class WorkTypeStatus(StrEnum):
    CONFIRMED = "confirmed"
    COMPATIBLE = "compatible"
    EXPLICIT_LABEL = "explicit_label"
    DEFAULTED = "defaulted"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class MappingSource(StrEnum):
    EXACT_NAME = "exact_name"
    VERIFIED_ALIAS = "verified_alias"
    EXPLICIT_WORK = "explicit_work"
    CONTEXTUAL_MAPPING = "contextual_mapping"
    USER_SELECTED = "user_selected"
    MIGRATED = "migrated"


class InferenceRelation(StrEnum):
    DIRECTLY_IMPLIED = "directly_implied"
    PRIMARY_TEXT = "primary_text"
    CONCEPTUAL_ADJACENCY = "conceptual_adjacency"
    HISTORICAL_RECEPTION = "historical_reception"
    CONTEMPORARY_BRIDGE = "contemporary_bridge"
    METHODOLOGICAL_CONTEXT = "methodological_context"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    DEFERRED = "deferred"
    REJECTED = "rejected"


class MatchDecision(StrEnum):
    NOTIFY = "notify"
    NO_CATEGORY_OVERLAP = "no_category_overlap"
    EXCLUDED_CATEGORY_MATCH = "excluded_category_match"
    NOT_CONFIRMED_NEW = "not_confirmed_new"
    AWAITING_CATEGORIES = "awaiting_categories"
    ALREADY_NOTIFIED = "already_notified"
    UNSUPPORTED_WORK_TYPE = "unsupported_work_type"


@dataclass(frozen=True, slots=True)
class DateValue:
    """A source date with its real precision preserved."""

    value: date | datetime | str
    precision: DatePrecision
    source: str
    source_record_id: str | None = None
    inferred: bool = False
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Category:
    category_id: str
    category_name: str
    parent_ids: frozenset[str] = frozenset()
    child_ids: frozenset[str] = frozenset()
    primary_parent_id: str | None = None
    active: bool = True
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class TaxonomySnapshot:
    snapshot_id: str
    source: str
    retrieved_at: datetime
    source_url: str
    content_hash: str
    complete: bool
    fixture: bool
    parser_version: str
    categories: dict[str, Category]
    source_content_hash: str | None = None
    omitted_root_id: str | None = None
    source_record_count: int | None = None
    excluded_source_records: tuple[dict[str, str], ...] = ()
    duplicate_name_groups: tuple[dict[str, object], ...] = ()

    @property
    def category_count(self) -> int:
        return len(self.categories)


@dataclass(frozen=True, slots=True)
class SelectedCategory:
    category_id: str
    category_name: str
    mapping_source: MappingSource
    evidence: str
    original_fragment: str
    include_descendants: bool = False


@dataclass(frozen=True, slots=True)
class AmbiguousCandidate:
    original_fragment: str
    candidate_category_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ProposedCategory:
    """A verified taxonomy category that is not active until the user confirms it."""

    category_id: str
    category_name: str
    relation: InferenceRelation
    rationale: str
    evidence_sources: tuple[str, ...]
    breadth_note: str
    status: ProposalStatus = ProposalStatus.PENDING


@dataclass(frozen=True, slots=True)
class InterestProfile:
    profile_id: str
    version: int
    created_at: datetime
    effective_from: datetime
    original_text: str
    taxonomy_snapshot_id: str
    selected_categories: tuple[SelectedCategory, ...]
    expanded_category_ids: frozenset[str]
    excluded_category_ids: frozenset[str] = frozenset()
    ambiguous_candidates: tuple[AmbiguousCandidate, ...] = ()
    proposed_categories: tuple[ProposedCategory, ...] = ()
    inference_mode: str = "adaptive"


@dataclass(frozen=True, slots=True)
class CategoryAssignment:
    category_id: str
    category_name: str
    assignment_source: str
    retrieved_at: datetime
    source_record_id: str | None = None
    mapping_method: str = "direct"


@dataclass(frozen=True, slots=True)
class WorkTypeEvidence:
    source: str
    raw_type: str
    normalized_type: str | None
    source_record_id: str | None = None
    method: str = "structured"


@dataclass(frozen=True, slots=True)
class WorkRecord:
    work_id: str
    title: str
    authors: tuple[str, ...]
    observed_at: datetime
    freshness_status: FreshnessStatus
    category_status: CategoryStatus
    category_assignments: tuple[CategoryAssignment, ...] = ()
    doi: str | None = None
    source_ids: tuple[tuple[str, str], ...] = ()
    work_type: str = "article"
    work_type_status: WorkTypeStatus = WorkTypeStatus.DEFAULTED
    work_type_evidence: tuple[WorkTypeEvidence, ...] = ()
    publication_date: DateValue | None = None
    availability_date: DateValue | None = None
    freshness_event: str | None = None
    container_title: str | None = None
    stable_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def category_ids(self) -> frozenset[str]:
        return frozenset(item.category_id for item in self.category_assignments)


@dataclass(frozen=True, slots=True)
class MatchRecord:
    match_id: str
    work_id: str
    interest_profile_id: str
    interest_profile_version: int
    taxonomy_snapshot_id: str
    freshness_status: FreshnessStatus
    paper_category_ids: frozenset[str]
    matched_category_ids: frozenset[str]
    excluded_category_ids: frozenset[str]
    decision: MatchDecision
    decided_at: datetime
    decision_method: str = "set_intersection_v1"


@dataclass(frozen=True, slots=True)
class Notification:
    notification_id: str
    work_id: str
    match_id: str
    report_window_start: datetime
    report_window_end: datetime
    notification_type: str
    created_at: datetime
    matched_category_ids: frozenset[str]
