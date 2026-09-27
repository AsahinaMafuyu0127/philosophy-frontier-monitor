"""Review evidence never silently becomes new-paper or category proof."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from philosophy_frontier_monitor.cnki_review import (
    BibliographicPeer,
    CnkiReviewError,
    assess_cnki_overlaps,
    assess_cnki_record,
    load_reviewed_evidence,
)
from philosophy_frontier_monitor.sources.cnki_space import parse_result_page

FIXTURES = Path(__file__).parent / "fixtures"
RECORD = parse_result_page((FIXTURES / "cnki_space_list.html").read_text(encoding="utf-8"))[0][0]
EVIDENCE = load_reviewed_evidence(FIXTURES / "cnki_reviewed_evidence.yaml")[RECORD.url]


def test_reviewed_journal_fields_and_affiliation_are_linked_to_source() -> None:
    assessment = assess_cnki_record(RECORD, EVIDENCE)
    assert assessment.status == "corroborated"
    assert assessment.china_affiliated_authors == ("甲",)
    assert assessment.evidence_url == "https://journal.example.org/articles/example-1"


@pytest.mark.parametrize(
    "change,field",
    [({"year": 2025}, "year"), ({"issue": "8"}, "issue"), ({"authors": ("乙",)}, "authors")],
)
def test_conflicting_review_never_claims_affiliation(change: dict, field: str) -> None:
    assessment = assess_cnki_record(replace(RECORD, **change), EVIDENCE)
    assert assessment.status == "conflict"
    assert field in assessment.conflicting_fields
    assert not assessment.china_affiliated_authors


def test_missing_issue_is_only_partial_corroboration() -> None:
    assessment = assess_cnki_record(replace(RECORD, issue=None), EVIDENCE)
    assert assessment.status == "partial"
    assert not assessment.china_affiliated_authors


def test_review_file_rejects_duplicate_without_echo() -> None:
    original = (FIXTURES / "cnki_reviewed_evidence.yaml").read_text(encoding="utf-8")
    duplicate = Path("var") / f"cnki-review-test-{uuid4().hex}.yaml"
    # Duplicate the record as a second list item; the exact private URL stays out of the error.
    try:
        duplicate.write_text(
            original + "  - cnki_url:" + original.split("  - cnki_url:", 1)[1],
            encoding="utf-8",
        )
        with pytest.raises(CnkiReviewError, match="duplicate_cnki_url"):
            load_reviewed_evidence(duplicate)
        duplicate.write_text(
            original.replace(
                "https://journal.example.org/articles/example-1",
                "https://journal.example.org/bad) [injected](https://example.org",
            ),
            encoding="utf-8",
        )
        with pytest.raises(CnkiReviewError, match="invalid_evidence_url"):
            load_reviewed_evidence(duplicate)
    finally:
        duplicate.unlink(missing_ok=True)


def test_same_title_requires_author_and_year_for_strong_overlap() -> None:
    same = BibliographicPeer(RECORD.title, RECORD.authors, 2026, "https://philpapers.org/rec/AAA")
    changed_year = replace(same, year=2025, url="https://philpapers.org/rec/BBB")
    unrelated = replace(same, title="知识与信念", url="https://philpapers.org/rec/CCC")
    overlap = assess_cnki_overlaps((RECORD,), (changed_year, same, unrelated))[0]
    assert overlap.status == "same_bibliography"
    assert overlap.peer_urls == (same.url,)
    possible = assess_cnki_overlaps((RECORD,), (changed_year,))[0]
    assert possible.status == "needs_review"
    assert assess_cnki_overlaps((RECORD,), (unrelated,)) == ()
