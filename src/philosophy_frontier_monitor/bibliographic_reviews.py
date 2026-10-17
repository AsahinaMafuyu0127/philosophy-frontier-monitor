"""Private publisher-checked bibliography corrections for historical search."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .identity import authors_equivalent
from .normalize import normalize_doi, normalize_title
from .work_type_reviews import RECORD_URL, _valid_evidence_url


class BibliographicReviewError(ValueError):
    """A reviewed bibliography cannot be safely used."""


@dataclass(frozen=True, slots=True)
class ReviewedBibliography:
    record_url: str
    source_title: str
    source_author: str
    verified_title: str
    verified_authors: tuple[str, ...]
    doi: str
    publication_year: int | None
    evidence_urls: tuple[str, ...]
    reviewed_at: datetime
    note: str

    def applies_to(
        self, *, record_url: str, title: str, author: str | None,
        doi_hints: tuple[str, ...], feed_year_hints: tuple[int, ...],
    ) -> bool:
        return (
            self.record_url.rstrip("/") == record_url.rstrip("/")
            and normalize_title(self.source_title) == normalize_title(title)
            and authors_equivalent(author, (self.source_author,)) is True
            and authors_equivalent(self.source_author, self.verified_authors) is True
            and (not doi_hints or doi_hints == (self.doi,))
            and (
                self.publication_year is None
                or not feed_year_hints
                or self.publication_year in feed_year_hints
            )
        )


def load_bibliographic_reviews(path: str | Path) -> dict[str, ReviewedBibliography]:
    """Read publisher-backed identity and display facts without changing state."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise BibliographicReviewError("bibliographic reviews require schema_version 1")
    entries = data.get("reviews")
    if not isinstance(entries, list) or len(entries) > 1000:
        raise BibliographicReviewError("reviews must be a list of at most 1000 entries")
    reviews: dict[str, ReviewedBibliography] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise BibliographicReviewError(f"review {index} must be an object")
        url = entry.get("record_url")
        fields = ("source_title", "source_author", "verified_title", "doi", "note")
        if not isinstance(url, str) or not RECORD_URL.fullmatch(url):
            raise BibliographicReviewError(f"review {index} needs a PhilPapers record URL")
        if any(
            not isinstance(entry.get(field), str)
            or not entry[field].strip()
            or len(entry[field]) > 1000
            for field in fields
        ):
            raise BibliographicReviewError(f"review {index} needs bounded source facts")
        doi = normalize_doi(entry["doi"])
        if doi is None:
            raise BibliographicReviewError(f"review {index} needs an exact DOI")
        authors = entry.get("verified_authors")
        if (
            not isinstance(authors, list)
            or not 1 <= len(authors) <= 32
            or not all(
                isinstance(author, str) and 0 < len(author.strip()) <= 200
                for author in authors
            )
        ):
            raise BibliographicReviewError(f"review {index} needs verified authors")
        year = entry.get("publication_year")
        if year is not None and (type(year) is not int or not 1 <= year <= 9999):
            raise BibliographicReviewError(f"review {index} has invalid publication year")
        evidence_urls = entry.get("evidence_urls")
        if (
            not isinstance(evidence_urls, list)
            or not 1 <= len(evidence_urls) <= 8
            or not all(_valid_evidence_url(value) for value in evidence_urls)
        ):
            raise BibliographicReviewError(f"review {index} needs HTTPS evidence URLs")
        try:
            reviewed_at = datetime.fromisoformat(entry["reviewed_at"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError) as error:
            raise BibliographicReviewError(f"review {index} needs a review time") from error
        if reviewed_at.tzinfo is None:
            raise BibliographicReviewError(f"review {index} needs a timezone-aware review time")
        if url in reviews:
            raise BibliographicReviewError(f"duplicate review for {url}")
        reviews[url] = ReviewedBibliography(
            url, entry["source_title"].strip(), entry["source_author"].strip(),
            entry["verified_title"].strip(), tuple(author.strip() for author in authors),
            doi, year, tuple(evidence_urls), reviewed_at, entry["note"].strip(),
        )
    return reviews
