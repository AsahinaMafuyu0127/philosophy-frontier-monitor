"""Deterministic category-set matching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .models import (
    CategoryStatus,
    FreshnessStatus,
    InterestProfile,
    MatchDecision,
    MatchRecord,
    WorkRecord,
)

SUPPORTED_WORK_TYPES = frozenset(
    {
        "article",
        "journal-article",
        "journal_article",
        "posted-content",
        "preprint",
        "manuscript",
        "submitted-manuscript",
        "submitted_manuscript",
        "working-paper",
        "working_paper",
        "forthcoming-article",
        "author-accepted-manuscript",
        "author_accepted_manuscript",
        "proceedings-article",
    }
)


def match_work(
    work: WorkRecord,
    profile: InterestProfile,
    *,
    already_notified: bool = False,
    now: datetime | None = None,
) -> MatchRecord:
    """Apply gates in policy order, then compute an exact set intersection."""

    paper_ids = work.category_ids
    matched = paper_ids.intersection(profile.expanded_category_ids)
    excluded = paper_ids.intersection(profile.excluded_category_ids)

    if work.work_type.casefold() not in SUPPORTED_WORK_TYPES:
        decision = MatchDecision.UNSUPPORTED_WORK_TYPE
    elif work.freshness_status not in {
        FreshnessStatus.CONFIRMED_NEW,
        FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
    }:
        decision = MatchDecision.NOT_CONFIRMED_NEW
    elif work.category_status is not CategoryStatus.AVAILABLE:
        decision = MatchDecision.AWAITING_CATEGORIES
    elif excluded:
        decision = MatchDecision.EXCLUDED_CATEGORY_MATCH
    elif not matched:
        decision = MatchDecision.NO_CATEGORY_OVERLAP
    elif already_notified:
        decision = MatchDecision.ALREADY_NOTIFIED
    else:
        decision = MatchDecision.NOTIFY

    return MatchRecord(
        match_id=f"pfm:match:{uuid.uuid4()}",
        work_id=work.work_id,
        interest_profile_id=profile.profile_id,
        interest_profile_version=profile.version,
        taxonomy_snapshot_id=profile.taxonomy_snapshot_id,
        freshness_status=work.freshness_status,
        paper_category_ids=paper_ids,
        matched_category_ids=frozenset(matched),
        excluded_category_ids=frozenset(excluded),
        decision=decision,
        decided_at=now or datetime.now(UTC),
    )
