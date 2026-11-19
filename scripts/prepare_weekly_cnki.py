"""Prepare bounded Chinese-journal candidates for publisher-date review in all three modes."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from philosophy_frontier_monitor.cnki_dates import enrich_cnki_scan_with_dates
from philosophy_frontier_monitor.config import load_watchlist
from philosophy_frontier_monitor.pipeline import (
    _load_cnki_issues,
    latest_due_week,
    on_demand_window,
)
from philosophy_frontier_monitor.publisher_search import publisher_keywords, search_publisher_papers
from philosophy_frontier_monitor.search import SearchOptions
from philosophy_frontier_monitor.sources.cnki_space import CnkiScan, scan_cnki_space


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/watchlist.yaml")
    parser.add_argument("--mode", choices=("weekly", "pull-now", "search"), default="weekly")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)
    parser.add_argument("--publisher-keyword", action="append", default=[])
    args = parser.parse_args()
    config = load_watchlist(Path(args.config))
    try:
        terms = publisher_keywords(config, tuple(args.publisher_keyword))
    except ValueError as error:
        parser.error(str(error))
    now = datetime.now(UTC)
    if args.mode != "pull-now" and args.days != 7:
        parser.error("--days requires --mode pull-now")
    if args.mode != "search" and (args.year_from is not None or args.year_to is not None):
        parser.error("year filters require --mode search")
    start = end = None
    scan = None
    if args.mode == "search":
        options = SearchOptions(year_from=args.year_from, year_to=args.year_to)
        try:
            options.validate()
        except ValueError as error:
            parser.error(str(error))
        years = (
            tuple(range(args.year_from, args.year_to + 1))
            if args.year_from is not None and args.year_to is not None
            and args.year_to - args.year_from <= 1 else (None,)
        )
        if config.cnki_space.enabled:
            try:
                scan = scan_cnki_space(config.cnki_space.terms, years=years,
                                       max_pages=config.cnki_space.max_pages, checked_at=now)
            except Exception as error:
                scan = CnkiScan((), (), now, "failed", 0, 0, (),
                                (f"adapter:{type(error).__name__}",))
    else:
        if args.mode == "weekly":
            start, end = latest_due_week(
                now=now, timezone=config.timezone, report_weekday=config.report_weekday,
                report_time=config.schedule.local_time,
            )
        else:
            start, end = on_demand_window(config, now=now, lookback_days=args.days)
        scan = _load_cnki_issues(
            config, window_start=start, window_end=end, checked_at=now, loader=scan_cnki_space,
        )
    scan = enrich_cnki_scan_with_dates(config, scan) if scan else None
    issues = scan.issues if scan else ()
    if args.mode == "search":
        issues = tuple(issue for issue in issues
                       if (args.year_from is None or issue.year >= args.year_from)
                       and (args.year_to is None or issue.year <= args.year_to))
    publisher = search_publisher_papers(
        config, terms, year_from=args.year_from, year_to=args.year_to,
    )
    publisher_date_reviews = []
    for lead in publisher.papers if publisher else ():
        if (lead.label_month or lead.issue_published_on) and not lead.date_conflict:
            continue
        article = lead.article
        if start is not None and end is not None:
            try:
                observed = datetime.fromisoformat(article.first_observed_at)
            except ValueError:
                continue
            if observed.tzinfo is None or not start <= observed < end:
                continue
        publisher_date_reviews.append({
            "journal": lead.journal, "journal_id": article.journal_id,
            "year": article.year, "issue": article.issue, "title": article.title,
            "author": article.author, "article_url": article.url,
            "issue_url": article.issue_url, "date_conflict": lead.date_conflict,
            "date_evidence_urls": lead.date_evidence_urls,
        })
    print(json.dumps({
        "operation": "prepare-chinese-journal-dates", "mode": args.mode,
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "year_from": args.year_from, "year_to": args.year_to,
        "source_status": scan.status if scan else "disabled",
        "state_mutated": False,
        "publisher_status": publisher.status if publisher else "not_requested",
        "publisher_date_reviews": publisher_date_reviews,
        "issues": [{
            "venue": issue.venue, "year": issue.year, "issue": issue.issue,
            "label_month": issue.label_month, "publication_date": issue.publication_date,
            "date_evidence_urls": issue.date_evidence_urls,
            "date_conflict": issue.date_conflict,
            "needs_date_review": issue.label_month is None or issue.date_conflict,
            "records": [{"title": record.title, "authors": record.authors,
                         "cnki_url": record.url} for record in issue.records],
        } for issue in issues],
    }, ensure_ascii=False, indent=2))
    return int(scan is not None and scan.status == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
