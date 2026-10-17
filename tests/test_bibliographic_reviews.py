import json

import pytest

from philosophy_frontier_monitor.bibliographic_reviews import (
    BibliographicReviewError,
    load_bibliographic_reviews,
)


def _entry():
    return {
        "record_url": "https://philpapers.org/rec/EXAMPLE",
        "source_title": "A Complete Article Title",
        "source_author": "Ada Scholar",
        "verified_title": "A Complete Article Title",
        "verified_authors": ["Ada Scholar"],
        "doi": "10.5840/example2026",
        "publication_year": 2026,
        "evidence_urls": ["https://example.org/publisher/article"],
        "reviewed_at": "2026-10-17T12:00:00+08:00",
        "note": "Publisher page states the full bibliography.",
    }


def test_publisher_review_requires_matching_feed_identity(tmp_path):
    path = tmp_path / "bibliographic.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [_entry()]}), encoding="utf-8")
    review = load_bibliographic_reviews(path)["https://philpapers.org/rec/EXAMPLE"]
    params = dict(
        record_url=review.record_url, title=review.source_title,
        author=review.source_author, doi_hints=(), feed_year_hints=(2026,),
    )
    assert review.applies_to(**params)
    assert not review.applies_to(**{**params, "title": "Another work"})
    assert not review.applies_to(**{**params, "doi_hints": ("10.5840/other",)})
    assert not review.applies_to(**{**params, "feed_year_hints": (2025,)})


def test_publisher_review_accepts_year_without_doi_but_rejects_feed_doi(tmp_path):
    entry = _entry()
    entry["doi"] = None
    path = tmp_path / "bibliographic.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [entry]}), encoding="utf-8")
    review = load_bibliographic_reviews(path)[entry["record_url"]]
    assert review.applies_to(
        record_url=review.record_url, title=review.source_title,
        author=review.source_author, doi_hints=(), feed_year_hints=(),
    )
    assert not review.applies_to(
        record_url=review.record_url, title=review.source_title,
        author=review.source_author, doi_hints=("10.5840/another",),
        feed_year_hints=(),
    )


def test_publisher_review_cannot_omit_both_doi_and_year(tmp_path):
    entry = _entry()
    entry["doi"] = None
    entry["publication_year"] = None
    path = tmp_path / "bibliographic.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [entry]}), encoding="utf-8")
    with pytest.raises(BibliographicReviewError, match="DOI or publication year"):
        load_bibliographic_reviews(path)


@pytest.mark.parametrize("field,value", [
    ("record_url", "https://other.example/rec/EXAMPLE"),
    ("doi", "example"),
    ("verified_authors", []),
    ("publication_year", "2026"),
    ("evidence_urls", ["http://example.org/paper"]),
    ("reviewed_at", "2026-10-17"),
])
def test_invalid_bibliographic_review_is_rejected(tmp_path, field, value):
    entry = _entry()
    entry[field] = value
    path = tmp_path / "bibliographic.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [entry]}), encoding="utf-8")
    with pytest.raises(BibliographicReviewError):
        load_bibliographic_reviews(path)
