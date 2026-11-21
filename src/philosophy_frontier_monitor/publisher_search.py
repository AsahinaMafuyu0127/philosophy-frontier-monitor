"""Search recorded publisher catalogues independently of database indexing."""

from __future__ import annotations

import json
import sqlite3
from calendar import monthrange
from collections.abc import Collection
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from itertools import groupby
from unicodedata import normalize

from .config import WatchlistConfig, plan_chinese_queries
from .journal_collection import PublisherArticle, _title_digest
from .official_journals import load_catalog


@dataclass(frozen=True, slots=True)
class PublisherPaperLead:
    article: PublisherArticle
    journal: str
    matched_fields: tuple[str, ...]
    label_month: str | None
    issue_published_on: str | None
    date_evidence_urls: tuple[str, ...]
    date_conflict: bool

    @property
    def key(self) -> str:
        article = self.article
        identity = (article.journal_id, article.year, article.issue, article.title)
        return sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PublisherSearchResult:
    status: str
    papers: tuple[PublisherPaperLead, ...] = ()
    journals: tuple[str, ...] = ()
    searched_issues: int = 0
    searched_articles: int = 0
    with_article_text: int = 0
    incomplete_issues: int = 0
    missing_or_conflicting_dates: int = 0
    outside_window: int = 0
    already_reported: int = 0


def _term(value: str) -> str:
    return "".join(normalize("NFKC", value).casefold().split())


def publisher_keywords(
    config: WatchlistConfig, keywords: tuple[str, ...], *,
    category_ids: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Use the same feed terms as CNKI unless there is an explicit publisher query."""
    selected = keywords or tuple(
        term.query for term in plan_chinese_queries(config, category_ids).terms
    )
    if len(selected) > 12 or any(not _term(value) or len(value) > 80 for value in selected):
        raise ValueError("publisher search accepts up to 12 keywords of 1 to 80 characters")
    return tuple(dict.fromkeys(_term(value) for value in selected))


def _identity(row: sqlite3.Row) -> tuple[str, int, str]:
    return row["journal_id"], row["year"], row["issue"]


def _publication_bounds(lead: PublisherPaperLead) -> tuple[date, date] | None:
    if lead.date_conflict:
        return None
    if lead.issue_published_on:
        start = date.fromisoformat(lead.issue_published_on)
        return start, start + timedelta(days=1)
    if lead.label_month:
        start = date.fromisoformat(f"{lead.label_month}-01")
        return start, start + timedelta(days=monthrange(start.year, start.month)[1])
    return None


def search_publisher_papers(
    config: WatchlistConfig,
    terms: tuple[str, ...],
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> PublisherSearchResult | None:
    if not terms:
        return None
    path = config.storage.state_database.parent / "official-journals.sqlite3"
    if not path.is_file():
        return PublisherSearchResult("not_collected")
    try:
        journals = {item.id: item.title for item in load_catalog()}
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"publisher_articles", "publisher_issue_scans"} <= tables:
                return PublisherSearchResult("not_collected")
            # The optional text table is absent in existing observation databases.
            if "publisher_article_text" in tables:
                text_columns = "t.abstract, t.keywords"
                text_join = (
                    "LEFT JOIN publisher_article_text t "
                    "ON (t.journal_id, t.year, t.issue, t.title) = "
                    "(a.journal_id, a.year, a.issue, a.title)"
                )
            else:
                text_columns, text_join = "NULL AS abstract, NULL AS keywords", ""
            rows = db.execute(f"""
                SELECT s.journal_id, s.year, s.issue, s.issue_url AS catalogue_url,
                    s.article_count, s.title_digest, a.title, a.author, a.url,
                    a.first_observed_at, {text_columns}
                FROM publisher_issue_scans s
                LEFT JOIN publisher_articles a
                    ON (a.journal_id, a.year, a.issue) = (s.journal_id, s.year, s.issue)
                    AND a.last_observed_at = s.last_completed_at
                {text_join}
                WHERE (? IS NULL OR s.year >= ?) AND (? IS NULL OR s.year <= ?)
                ORDER BY s.year DESC, s.journal_id, s.issue, a.title
            """, (year_from, year_from, year_to, year_to)).fetchall()
            dates: dict[tuple[str, int, str], list[sqlite3.Row]] = {}
            if "official_issues" in tables:
                for row in db.execute("""
                    SELECT journal_id, year, issue, label_month, issue_published_on, evidence_url
                    FROM official_issues WHERE status = 'reviewed'
                        AND (label_month IS NOT NULL OR issue_published_on IS NOT NULL)
                    ORDER BY evidence_url
                """):
                    dates.setdefault(_identity(row), []).append(row)
        return _select_publisher_papers(rows, dates, journals, terms, window, config)
    except (OSError, sqlite3.DatabaseError, ValueError):
        return PublisherSearchResult("failed")


def _select_publisher_papers(
    rows: list[sqlite3.Row],
    dates: dict[tuple[str, int, str], list[sqlite3.Row]],
    journals: dict[str, str],
    terms: tuple[str, ...],
    window: tuple[datetime, datetime] | None,
    config: WatchlistConfig,
) -> PublisherSearchResult:
    papers = []
    searched_journals: set[str] = set()
    issues = articles = with_text = incomplete = unknown = outside = 0
    for key, group in groupby(rows, _identity):
        journal = journals.get(key[0])
        snapshot = tuple(group)
        if journal is None:
            continue
        if snapshot[0]["title"] is None or len(snapshot) != snapshot[0]["article_count"]:
            incomplete += 1
            continue
        digest = _title_digest([row["title"] for row in snapshot])
        if digest != snapshot[0]["title_digest"]:
            incomplete += 1
            continue
        searched_journals.add(journal)
        issues += 1
        articles += len(snapshot)
        evidence = dates.get(key, ())
        months = {row["label_month"] for row in evidence if row["label_month"]}
        days = {row["issue_published_on"] for row in evidence if row["issue_published_on"]}
        date_urls = tuple(dict.fromkeys(row["evidence_url"] for row in evidence))
        for row in snapshot:
            with_text += bool(row["abstract"] or row["keywords"])
            fields = tuple(
                field for field in ("title", "keywords", "abstract")
                if row[field] and any(term in _term(row[field]) for term in terms)
            )
            if not fields:
                continue
            lead = PublisherPaperLead(
                PublisherArticle(
                    *key, row["title"], row["author"], row["url"], row["catalogue_url"],
                    row["first_observed_at"], row["abstract"], row["keywords"],
                ), journal, fields,
                next(iter(months)) if len(months) == 1 else None,
                next(iter(days)) if len(days) == 1 else None,
                date_urls, len(months) > 1 or len(days) > 1,
            )
            if window is not None:
                bounds = _publication_bounds(lead)
                if bounds is None:
                    unknown += 1
                    continue
                start, end = (datetime.combine(day, time.min, config.timezone) for day in bounds)
                if not (start < window[1] and end > window[0]):
                    outside += 1
                    continue
            papers.append(lead)
    return PublisherSearchResult(
        "local_only", tuple(papers), tuple(sorted(searched_journals)), issues, articles, with_text,
        incomplete, unknown, outside,
    )
