"""Review evidence never silently becomes new-paper or category proof."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

from philosophy_frontier_monitor.cnki_review import (
    BibliographicPeer,
    CnkiReviewError,
    assess_cnki_overlaps,
    assess_cnki_record,
    load_reviewed_evidence,
)
from philosophy_frontier_monitor.pipeline import _cnki_issues_in_window, _cnki_issues_with_dates
from philosophy_frontier_monitor.sources.cnki_space import CnkiIssue, parse_result_page

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


@pytest.mark.parametrize(
    "dates,expected",
    [
        ({"publication_date": "2026-09-15"}, 9),
        ({"issue_label_month": "2026-09", "publication_date": "2026-08-31"}, 9),
        ({}, None),
    ],
)
def test_exact_publisher_bibliography_supplies_month(tmp_path, dates, expected) -> None:
    payload = yaml.safe_load((FIXTURES / "cnki_reviewed_evidence.yaml").read_text("utf-8"))
    payload["records"][0].update(dates)
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(yaml.safe_dump(payload, allow_unicode=True), "utf-8")
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "state.sqlite3"),
        cnki_space=SimpleNamespace(reviewed_evidence=reviewed),
    )
    issue = CnkiIssue(
        "synthetic", RECORD.venue, RECORD.year, RECORD.issue, None, (RECORD,), (),
        datetime(2026, 9, 28, tzinfo=UTC),
    )
    enriched = _cnki_issues_with_dates(config, (issue,))[0]
    assert enriched.label_month == expected
    assert not enriched.date_conflict
    conflicting = replace(issue, records=(replace(RECORD, authors=("乙",)),))
    assert _cnki_issues_with_dates(config, (conflicting,))[0].label_month is None


def test_conflicting_explicit_month_is_withheld(tmp_path) -> None:
    payload = yaml.safe_load((FIXTURES / "cnki_reviewed_evidence.yaml").read_text("utf-8"))
    payload["records"][0]["issue_label_month"] = "2026-08"
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(yaml.safe_dump(payload, allow_unicode=True), "utf-8")
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "state.sqlite3"),
        cnki_space=SimpleNamespace(reviewed_evidence=reviewed),
    )
    issue = CnkiIssue(
        "synthetic", RECORD.venue, RECORD.year, RECORD.issue, 9, (RECORD,), (),
        datetime(2026, 9, 28, tzinfo=UTC),
    )
    enriched = _cnki_issues_with_dates(config, (issue,))[0]
    assert enriched.date_conflict and enriched.label_month is None
    selected, missing, outside = _cnki_issues_in_window(
        (enriched,), window_start=datetime(2026, 9, 21, tzinfo=UTC),
        window_end=datetime(2026, 9, 28, tzinfo=UTC), timezone=UTC,
    )
    assert (selected, missing, outside) == ((), 1, 0)


@pytest.mark.parametrize("fields,error", [
    ({"publication_date": "2026-02-30"}, "invalid_publication_date_evidence"),
    ({"issue_label_month": "2026-13"}, "invalid_publication_date_evidence"),
    ({"publication_date": "2025-09-15"}, "invalid_publication_date_evidence"),
    ({"publication_date": "2026-09-15", "source_type": "catalog_record",
      "china_affiliated_authors": []},
     "publication_date_needs_publisher_page"),
])
def test_invalid_publisher_date_evidence_is_rejected(tmp_path, fields, error) -> None:
    payload = yaml.safe_load((FIXTURES / "cnki_reviewed_evidence.yaml").read_text("utf-8"))
    payload["records"][0].update(fields)
    reviewed = tmp_path / "reviewed.yaml"
    reviewed.write_text(yaml.safe_dump(payload, allow_unicode=True), "utf-8")
    with pytest.raises(CnkiReviewError, match=error):
        load_reviewed_evidence(reviewed)
