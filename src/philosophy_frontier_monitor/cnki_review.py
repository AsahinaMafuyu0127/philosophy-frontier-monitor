"""Conservative assessment of privately reviewed Chinese journal evidence.

The evidence file records human inspection of a journal or catalog page. This
module never fetches those pages or turns a bibliography check into a new-paper
or interest-category decision.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .sources.cnki_space import CnkiRecord


class CnkiReviewError(ValueError):
    """A private review file is absent, malformed, or exceeds its bounded size."""


@dataclass(frozen=True, slots=True)
class CnkiReviewedEvidence:
    cnki_url: str
    evidence_url: str
    source_type: str
    title: str
    authors: tuple[str, ...]
    venue: str
    year: int
    issue: str
    china_affiliated_authors: tuple[str, ...]
    reviewed_at: datetime
    issue_label_month: str | None = None
    publication_date: str | None = None


@dataclass(frozen=True, slots=True)
class CnkiAssessment:
    cnki_url: str
    status: str
    evidence_url: str
    source_type: str
    china_affiliated_authors: tuple[str, ...]
    conflicting_fields: tuple[str, ...]
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class BibliographicPeer:
    title: str
    authors: tuple[str, ...]
    year: int | None
    url: str


@dataclass(frozen=True, slots=True)
class CnkiOverlap:
    cnki_url: str
    status: str
    peer_urls: tuple[str, ...]


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise CnkiReviewError(f"invalid_{field}")
    return value.strip()


def _url(value: object, field: str) -> str:
    text = _text(value, field)
    if any(character.isspace() or character in '<>()[]`"' for character in text):
        raise CnkiReviewError(f"invalid_{field}")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise CnkiReviewError(f"invalid_{field}") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CnkiReviewError(f"invalid_{field}")
    return text


def _names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise CnkiReviewError(f"invalid_{field}")
    return tuple(_text(item, field) for item in value)


def _same(left: str, right: str) -> bool:
    def compact(value: str) -> str:
        return "".join(
            character
            for character in unicodedata.normalize("NFKC", value).casefold()
            if character.isalnum()
        )

    return bool(compact(left)) and compact(left) == compact(right)


def _issue(value: str) -> str:
    return value.lstrip("0") if value.isdecimal() else value


def load_reviewed_evidence(path: Path) -> dict[str, CnkiReviewedEvidence]:
    """Read an opt-in private file without leaking its content in errors."""

    try:
        if path.stat().st_size > 1_000_000:
            raise CnkiReviewError("file_too_large")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise CnkiReviewError("cannot_read_file") from error
    except yaml.YAMLError as error:
        raise CnkiReviewError("invalid_yaml") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise CnkiReviewError("invalid_version")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) > 200:
        raise CnkiReviewError("invalid_records")
    reviewed: dict[str, CnkiReviewedEvidence] = {}
    for item in records:
        if not isinstance(item, dict):
            raise CnkiReviewError("invalid_record")
        cnki_url = _url(item.get("cnki_url"), "cnki_url")
        parsed_cnki = urlsplit(cnki_url)
        if (
            parsed_cnki.hostname != "www.cnki.com.cn"
            or not parsed_cnki.path.startswith("/Article/CJFDTOTAL-")
            or not parsed_cnki.path.endswith(".htm")
            or parsed_cnki.query
        ):
            raise CnkiReviewError("invalid_cnki_url")
        evidence_url = _url(item.get("evidence_url"), "evidence_url")
        if urlsplit(evidence_url).hostname == parsed_cnki.hostname:
            raise CnkiReviewError("evidence_not_separate_source")
        source_type = _text(item.get("source_type"), "source_type")
        if source_type not in {"journal_article", "journal_issue", "catalog_record"}:
            raise CnkiReviewError("invalid_source_type")
        raw_year = item.get("year")
        if (
            isinstance(raw_year, bool)
            or not isinstance(raw_year, int)
            or not 1800 <= raw_year <= 2200
        ):
            raise CnkiReviewError("invalid_year")
        authors = _names(item.get("authors"), "authors")
        if not authors:
            raise CnkiReviewError("missing_authors")
        affiliated = _names(item.get("china_affiliated_authors", []), "affiliated_authors")
        if source_type != "journal_article" and affiliated:
            raise CnkiReviewError("affiliation_needs_article_page")
        if any(not any(_same(name, author) for author in authors) for name in affiliated):
            raise CnkiReviewError("affiliation_author_mismatch")
        try:
            reviewed_at = datetime.fromisoformat(_text(item.get("reviewed_at"), "reviewed_at"))
        except ValueError as error:
            raise CnkiReviewError("invalid_reviewed_at") from error
        if reviewed_at.tzinfo is None:
            raise CnkiReviewError("reviewed_at_needs_timezone")
        if cnki_url in reviewed:
            raise CnkiReviewError("duplicate_cnki_url")
        issue_label_month = item.get("issue_label_month")
        publication_date = item.get("publication_date")
        try:
            if issue_label_month is not None:
                labelled = date.fromisoformat(str(issue_label_month) + "-01")
                if labelled.year != raw_year or labelled.strftime("%Y-%m") != issue_label_month:
                    raise ValueError
            if publication_date is not None:
                published = date.fromisoformat(str(publication_date))
                if published.year != raw_year or published.isoformat() != publication_date:
                    raise ValueError
        except (TypeError, ValueError) as error:
            raise CnkiReviewError("invalid_publication_date_evidence") from error
        if (issue_label_month or publication_date) and source_type == "catalog_record":
            raise CnkiReviewError("publication_date_needs_publisher_page")
        reviewed[cnki_url] = CnkiReviewedEvidence(
            cnki_url=cnki_url,
            evidence_url=evidence_url,
            source_type=source_type,
            title=_text(item.get("title"), "title"),
            authors=authors,
            venue=_text(item.get("venue"), "venue"),
            year=raw_year,
            issue=_text(item.get("issue"), "issue"),
            china_affiliated_authors=affiliated,
            reviewed_at=reviewed_at,
            issue_label_month=issue_label_month,
            publication_date=publication_date,
        )
    return reviewed


def assess_cnki_record(record: CnkiRecord, evidence: CnkiReviewedEvidence) -> CnkiAssessment:
    """Require matching title, authors, journal, year, and issue before affirmation."""

    conflicts = []
    if not _same(record.title, evidence.title):
        conflicts.append("title")
    if len(record.authors) != len(evidence.authors) or any(
        not _same(left, right) for left, right in zip(record.authors, evidence.authors, strict=True)
    ):
        conflicts.append("authors")
    if not _same(record.venue, evidence.venue):
        conflicts.append("venue")
    if record.year is not None and record.year != evidence.year:
        conflicts.append("year")
    if record.issue is not None and _issue(record.issue) != _issue(evidence.issue):
        conflicts.append("issue")
    if record.url != evidence.cnki_url:
        conflicts.append("url")
    if conflicts:
        status = "conflict"
    elif record.year is None or record.issue is None:
        status = "partial"
    else:
        status = "corroborated"
    return CnkiAssessment(
        cnki_url=record.url,
        status=status,
        evidence_url=evidence.evidence_url,
        source_type=evidence.source_type,
        china_affiliated_authors=(
            evidence.china_affiliated_authors if status == "corroborated" else ()
        ),
        conflicting_fields=tuple(conflicts),
        reviewed_at=evidence.reviewed_at,
    )


def assess_cnki_records(
    records: tuple[CnkiRecord, ...], evidence: dict[str, CnkiReviewedEvidence]
) -> tuple[CnkiAssessment, ...]:
    return tuple(
        assess_cnki_record(record, evidence[record.url])
        for record in records
        if record.url in evidence
    )


def assess_cnki_overlaps(
    records: tuple[CnkiRecord, ...], peers: tuple[BibliographicPeer, ...]
) -> tuple[CnkiOverlap, ...]:
    """Flag exact-title overlap in the inspected PhilPapers subset, without merging."""

    overlaps = []
    for record in records:
        matches = [peer for peer in peers if _same(record.title, peer.title)]
        if not matches:
            continue
        exact = [
            peer
            for peer in matches
            if record.authors
            and peer.authors
            and _same(record.authors[0], peer.authors[0])
            and record.year is not None
            and peer.year == record.year
        ]
        status = "same_bibliography" if exact else "needs_review"
        links = tuple(sorted({peer.url for peer in (exact or matches) if peer.url}))[:3]
        overlaps.append(CnkiOverlap(record.url, status, links))
    return tuple(overlaps)
