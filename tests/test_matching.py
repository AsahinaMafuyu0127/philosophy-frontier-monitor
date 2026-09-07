from datetime import UTC, datetime

from philosophy_frontier_monitor.interest import build_interest_profile
from philosophy_frontier_monitor.matching import match_work
from philosophy_frontier_monitor.models import (
    CategoryAssignment,
    CategoryStatus,
    FreshnessStatus,
    MatchDecision,
    WorkRecord,
)

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def make_work(*category_ids, freshness=FreshnessStatus.CONFIRMED_NEW):
    return WorkRecord(
        work_id="pfm:work:test",
        title="A Test Paper",
        authors=("A. Author",),
        observed_at=NOW,
        freshness_status=freshness,
        category_status=CategoryStatus.AVAILABLE,
        category_assignments=tuple(
            CategoryAssignment(category_id, category_id, "fixture", NOW)
            for category_id in category_ids
        ),
    )


def test_any_interest_category_overlap_notifies(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    work = make_work("74924", "11")

    result = match_work(work, profile, now=NOW)

    assert result.decision is MatchDecision.NOTIFY
    assert result.matched_category_ids == frozenset({"74924"})


def test_no_overlap_does_not_notify(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    work = make_work("60928")

    assert match_work(work, profile, now=NOW).decision is MatchDecision.NO_CATEGORY_OVERLAP


def test_old_work_fails_before_category_match(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    work = make_work(
        "74924",
        freshness=FreshnessStatus.NEWLY_INDEXED_OLD_WORK,
    )

    result = match_work(work, profile, now=NOW)

    assert result.matched_category_ids == frozenset({"74924"})
    assert result.decision is MatchDecision.NOT_CONFIRMED_NEW


def test_recent_philpapers_source_arrival_notifies_without_formal_publication(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    work = make_work(
        "74924",
        freshness=FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
    )

    assert match_work(work, profile, now=NOW).decision is MatchDecision.NOTIFY


def test_working_paper_is_a_supported_frontier_work_type(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    original = make_work(
        "74924",
        freshness=FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
    )
    work = WorkRecord(
        work_id=original.work_id,
        title=original.title,
        authors=original.authors,
        observed_at=original.observed_at,
        freshness_status=original.freshness_status,
        category_status=original.category_status,
        category_assignments=original.category_assignments,
        work_type="working-paper",
    )

    assert match_work(work, profile, now=NOW).decision is MatchDecision.NOTIFY


def test_structured_review_article_is_supported_but_book_review_is_not(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    original = make_work("74924")
    review_article = WorkRecord(
        work_id=original.work_id,
        title=original.title,
        authors=original.authors,
        observed_at=original.observed_at,
        freshness_status=original.freshness_status,
        category_status=original.category_status,
        category_assignments=original.category_assignments,
        work_type="review-article",
    )
    book_review = WorkRecord(
        work_id=original.work_id,
        title=original.title,
        authors=original.authors,
        observed_at=original.observed_at,
        freshness_status=original.freshness_status,
        category_status=original.category_status,
        category_assignments=original.category_assignments,
        work_type="book-review",
    )

    assert match_work(review_article, profile, now=NOW).decision is MatchDecision.NOTIFY
    assert match_work(book_review, profile, now=NOW).decision is MatchDecision.UNSUPPORTED_WORK_TYPE


def test_book_is_notified_neither_by_freshness_nor_category_overlap(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=NOW)
    work = make_work("74924")
    work = WorkRecord(
        work_id=work.work_id,
        title=work.title,
        authors=work.authors,
        observed_at=work.observed_at,
        freshness_status=work.freshness_status,
        category_status=work.category_status,
        category_assignments=work.category_assignments,
        work_type="book",
    )

    assert match_work(work, profile, now=NOW).decision is MatchDecision.UNSUPPORTED_WORK_TYPE
