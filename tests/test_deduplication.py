from datetime import UTC, datetime

from philosophy_frontier_monitor.deduplicate import DeduplicationKind, compare_works
from philosophy_frontier_monitor.models import CategoryStatus, FreshnessStatus, WorkRecord

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def work(work_id, title, *, doi=None, authors=("A. Author",), source_ids=()):
    return WorkRecord(
        work_id=work_id,
        title=title,
        authors=authors,
        observed_at=NOW,
        freshness_status=FreshnessStatus.CONFIRMED_NEW,
        category_status=CategoryStatus.AVAILABLE,
        doi=doi,
        source_ids=source_ids,
    )


def test_same_source_id_is_automatic_merge():
    left = work("left", "One", source_ids=(("philpapers", "ABC"),))
    right = work("right", "Different rendering", source_ids=(("philpapers", "ABC"),))

    assert compare_works(left, right).kind is DeduplicationKind.AUTOMATIC_MERGE


def test_normalized_doi_is_automatic_merge_without_conflict():
    left = work("left", "One", doi="https://doi.org/10.1234/ABC")
    right = work("right", "One revised", doi="doi:10.1234/abc")

    assert compare_works(left, right).kind is DeduplicationKind.AUTOMATIC_MERGE


def test_same_doi_with_author_conflict_requires_review():
    left = work("left", "One", doi="10.1234/abc", authors=("Alice",))
    right = work("right", "One", doi="10.1234/abc", authors=("Bob",))

    assert compare_works(left, right).kind is DeduplicationKind.CANDIDATE_DUPLICATE


def test_same_title_never_automatically_merges():
    left = work("left", "Knowledge and Belief")
    right = work("right", "Knowledge & Belief", authors=("Different Author",))

    assert compare_works(left, right).kind is DeduplicationKind.CANDIDATE_DUPLICATE


def test_spelling_and_author_rendering_variants_become_reviewable_duplicate():
    left = work(
        "left",
        "Knowledge in Plato's Theaitetos",
        authors=("García, María",),
    )
    right = work(
        "right",
        "Knowledge in Plato's Theaetetus",
        authors=("Maria Garcia",),
    )

    decision = compare_works(left, right)

    assert decision.kind is DeduplicationKind.CANDIDATE_DUPLICATE
    assert "compatible_first_author" in decision.evidence
