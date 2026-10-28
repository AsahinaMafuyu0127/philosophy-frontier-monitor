"""Apply reviewed publisher date evidence to bounded CNKI candidates.

This module enriches source metadata only. It does not decide whether a paper
is new, relevant to an interest category, or present in another database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from .cnki_review import CnkiReviewError, assess_cnki_record, load_reviewed_evidence
from .config import WatchlistConfig
from .normalize import normalize_title
from .official_journals import list_issues
from .sources.cnki_space import CnkiIssue, CnkiScan


def _merge_date_evidence(
    issue: CnkiIssue,
    *,
    labels: set[int],
    day_months: set[int],
    days: set[str],
    urls: tuple[str, ...],
) -> CnkiIssue:
    """Prefer an explicit issue label; expose disagreement instead of overwriting it."""
    months = labels or day_months
    if issue.label_month is not None and not labels:
        months = {issue.label_month}
    if issue.label_month is not None:
        months.add(issue.label_month)
    conflict = issue.date_conflict or len(months) > 1
    return replace(
        issue,
        label_month=next(iter(months)) if len(months) == 1 and not conflict else None,
        date_evidence_urls=tuple(dict.fromkeys((*issue.date_evidence_urls, *urls))),
        publication_date=next(iter(days)) if len(days) == 1 else issue.publication_date,
        date_conflict=conflict,
    )


def _official_months_for_cnki(
    issues: tuple[CnkiIssue, ...], official_issues
) -> tuple[CnkiIssue, ...]:
    def identity(title, year, number):
        return (normalize_title(title), year, number.lstrip("0") or "0")

    reviewed_by_identity = {}
    for item in official_issues:
        if item.status == "reviewed":
            reviewed_by_identity.setdefault(
                identity(item.title, item.year, item.issue), []
            ).append(item)

    enriched = []
    for issue in issues:
        evidence = reviewed_by_identity.get(identity(issue.venue, issue.year, issue.issue), ())
        enriched.append(_merge_date_evidence(
            issue,
            labels={int(item.label_month[-2:]) for item in evidence if item.label_month},
            day_months={
                int(item.issue_published_on[5:7]) for item in evidence
                if item.issue_published_on and int(item.issue_published_on[:4]) == issue.year
            },
            days={item.issue_published_on for item in evidence if item.issue_published_on},
            urls=tuple(
                item.evidence_url for item in evidence
                if item.label_month or item.issue_published_on
            ),
        ))
    return tuple(enriched)


def enrich_cnki_issues_with_dates(config: WatchlistConfig, issues: tuple[CnkiIssue, ...]):
    """Use publisher evidence for the exact bibliography, never an observation date."""
    db_path = config.storage.state_database.parent / "official-journals.sqlite3"
    try:
        official = list_issues(db_path, reviewed_only=True)
    except (OSError, ValueError, sqlite3.DatabaseError):
        official = ()
    issues = _official_months_for_cnki(issues, official)
    if config.cnki_space.reviewed_evidence is None:
        return issues
    try:
        reviewed = load_reviewed_evidence(config.cnki_space.reviewed_evidence)
    except CnkiReviewError:
        return issues  # The bibliography assessment reports the safe source error.
    enriched = []
    for issue in issues:
        evidence = tuple(
            reviewed[record.url] for record in issue.records
            if record.url in reviewed
            and assess_cnki_record(record, reviewed[record.url]).status == "corroborated"
        )
        enriched.append(_merge_date_evidence(
            issue,
            labels={
                int(item.issue_label_month[-2:]) for item in evidence
                if item.issue_label_month
            },
            day_months={
                int(item.publication_date[5:7]) for item in evidence if item.publication_date
            },
            days={item.publication_date for item in evidence if item.publication_date},
            urls=tuple(
                item.evidence_url for item in evidence
                if item.issue_label_month or item.publication_date
            ),
        ))
    return tuple(enriched)


def enrich_cnki_scan_with_dates(config: WatchlistConfig, scan: CnkiScan) -> CnkiScan:
    """Keep issue and article views aligned with the same reviewed publisher dates."""
    represented = {record.url for issue in scan.issues for record in issue.records}
    # Some discovery clients supply flat records without issue groups. Complete
    # bibliographies can still use exact reviewed evidence; undated identities cannot.
    fallback = tuple(
        CnkiIssue(record.url, record.venue, record.year, record.issue, record.label_month,
                  (record,), (), scan.checked_at)
        for record in scan.records
        if record.url not in represented and record.year is not None and record.issue is not None
    )
    issues = enrich_cnki_issues_with_dates(config, (*scan.issues, *fallback))
    issues = tuple(replace(
        issue, records=tuple(replace(
            record, label_month=issue.label_month, date_evidence_urls=issue.date_evidence_urls,
            publication_date=issue.publication_date, date_conflict=issue.date_conflict,
        ) for record in issue.records),
    ) for issue in issues)
    records = {record.url: record for issue in issues for record in issue.records}
    return replace(scan, issues=issues,
                   records=tuple(records.get(record.url, record) for record in scan.records))
