"""Bounded publisher-page discovery of WeChat links, never WeChat-page crawling."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, tzinfo
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

from .official_journals import (
    CATALOG_PATH,
    DEFAULT_DB_PATH,
    OfficialIssue,
    _validate_issue,
    _write_issue,
    load_catalog,
)

MAX_PAGE_BYTES = 750_000
MAX_LINKS_PER_PAGE = 20
CANDIDATE_STATES = ("pending", "blocked", "dismissed", "reviewed")
LONG_BLOCKED_DAYS = 7
POST_ID = re.compile(r"/s/[A-Za-z0-9_-]{8,}\Z")
RAW_POST = re.compile(r"https://mp\.weixin\.qq\.com/s(?:/[A-Za-z0-9_-]+|\?[^\s\"'<>]+)")


def _user_local_time(observed_at: datetime | None, user_timezone: tzinfo | None) -> datetime:
    """Use the configured user zone, or the executing user's OS local zone."""
    instant = observed_at or datetime.now().astimezone()
    if instant.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return instant.astimezone(user_timezone)


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str]] = []
        self.href: str | None = None
        self.label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            self.items.append((self.href, " ".join("".join(self.label).split())[:160]))
            self.href = None


def normalize_post_url(value: str) -> str | None:
    """Keep only a direct, public WeChat article URL; remove sharing trackers."""
    try:
        parsed = urlsplit(unescape(value).strip())
        valid_origin = (
            parsed.scheme == "https" and parsed.hostname == "mp.weixin.qq.com"
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
        )
    except ValueError:
        return None
    if not valid_origin:
        return None
    if POST_ID.fullmatch(parsed.path):
        return urlunsplit(("https", "mp.weixin.qq.com", parsed.path, "", ""))
    if parsed.path != "/s":
        return None
    query = parse_qs(parsed.query)
    required = ("__biz", "mid", "idx", "sn")
    if any(len(query.get(key, [])) != 1 or not query[key][0] for key in required):
        return None
    return urlunsplit((
        "https", "mp.weixin.qq.com", "/s",
        urlencode({key: query[key][0] for key in required}), "",
    ))


def extract_post_links(html: str) -> list[tuple[str, str]]:
    parser = _Anchors()
    parser.feed(html)
    found: dict[str, str] = {}
    for href, label in parser.items:
        url = normalize_post_url(href)
        if url is not None:
            found.setdefault(url, label)
    for raw in RAW_POST.findall(unescape(html)):
        url = normalize_post_url(raw)
        if url is not None:
            found.setdefault(url, "")
    if len(found) > MAX_LINKS_PER_PAGE:
        raise ValueError("publisher_wechat_link_limit")
    return list(found.items())


def _fetch_page(client: httpx.Client, url: str) -> str:
    try:
        source = urlsplit(url)
        valid_origin = (
            source.scheme == "https" and bool(source.hostname)
            and source.port in (None, 443) and not source.username and not source.password
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise ValueError("invalid_publisher_discovery_url")
    try:
        with client.stream("GET", url) as response:
            target = urlsplit(str(response.url))
            if response.is_redirect or target.hostname != source.hostname:
                raise ValueError("publisher_discovery_redirect")
            if response.status_code != 200:
                raise ValueError(f"publisher_discovery_http_{response.status_code}")
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("publisher_discovery_not_html")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    raise ValueError("publisher_discovery_page_too_large")
                chunks.append(chunk)
            return b"".join(chunks).decode(
                response.charset_encoding or "utf-8", errors="replace"
            )
    except httpx.TimeoutException as error:
        raise ValueError("publisher_discovery_timeout") from error
    except httpx.HTTPError as error:
        raise ValueError("publisher_discovery_transport_failed") from error


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS wechat_link_scans (
            scan_id INTEGER PRIMARY KEY, journal_id TEXT NOT NULL,
            local_date TEXT NOT NULL, source_url TEXT NOT NULL,
            observed_at TEXT NOT NULL, status TEXT NOT NULL,
            links_found INTEGER NOT NULL, error TEXT)""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(wechat_link_scans)")}
        if "scan_id" not in columns:
            # The previous unique key used a fixed Beijing date. Keep every old
            # observation while allowing a different user zone to reuse that date.
            db.execute("ALTER TABLE wechat_link_scans RENAME TO wechat_link_scans_legacy")
            db.execute("""CREATE TABLE wechat_link_scans (
                scan_id INTEGER PRIMARY KEY, journal_id TEXT NOT NULL,
                local_date TEXT NOT NULL, source_url TEXT NOT NULL,
                observed_at TEXT NOT NULL, status TEXT NOT NULL,
                links_found INTEGER NOT NULL, error TEXT)""")
            db.execute("""INSERT INTO wechat_link_scans
                (journal_id, local_date, source_url, observed_at, status, links_found, error)
                SELECT journal_id, local_date, source_url, observed_at, status, links_found, error
                FROM wechat_link_scans_legacy""")
            db.execute("DROP TABLE wechat_link_scans_legacy")
        db.execute("""CREATE INDEX IF NOT EXISTS wechat_link_scans_journal
            ON wechat_link_scans (journal_id, observed_at)""")
        db.execute("""CREATE TABLE IF NOT EXISTS wechat_link_candidates (
            journal_id TEXT NOT NULL, post_url TEXT NOT NULL, source_url TEXT NOT NULL,
            link_text TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', review_note TEXT,
            PRIMARY KEY (journal_id, post_url))""")
        candidate_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wechat_link_candidates)")
        }
        for column, kind in (
            ("blocked_since", "TEXT"), ("last_checked_at", "TEXT"),
            ("reviewed_at", "TEXT"), ("reviewed_account_name", "TEXT"),
            ("reviewed_announcement_on", "TEXT"), ("reviewed_toc_complete", "INTEGER"),
        ):
            if column not in candidate_columns:
                db.execute(f"ALTER TABLE wechat_link_candidates ADD COLUMN {column} {kind}")
    return db


def _reserve_scan(
    db: sqlite3.Connection, journal_id: str, local_date: str,
    source_url: str, timestamp: str, user_timezone: tzinfo | None,
) -> tuple[sqlite3.Row | None, int | None]:
    """Claim the journal's local-day page allowance before any HTTP request."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        prior = None
        rows = db.execute(
            "SELECT scan_id, local_date, observed_at, status, links_found, error "
            "FROM wechat_link_scans WHERE journal_id=? ORDER BY observed_at, scan_id",
            (journal_id,),
        )
        for row in rows:
            try:
                earlier = datetime.fromisoformat(row["observed_at"])
                earlier_date = (
                    earlier.astimezone(user_timezone).date().isoformat()
                    if earlier.utcoffset() is not None else row["local_date"]
                )
            except ValueError:
                earlier_date = row["local_date"]
            if earlier_date == local_date:
                prior = row
                break
        scan_id = None
        if prior is None:
            scan_id = db.execute(
                "INSERT INTO wechat_link_scans "
                "(journal_id, local_date, source_url, observed_at, status, links_found, error) "
                "VALUES (?, ?, ?, ?, 'attempted', 0, NULL)",
                (journal_id, local_date, source_url, timestamp),
            ).lastrowid
    return prior, scan_id


def _candidate_dict(row: sqlite3.Row) -> dict:
    candidate = dict(row)
    if candidate["reviewed_toc_complete"] is not None:
        candidate["reviewed_toc_complete"] = bool(candidate["reviewed_toc_complete"])
    return candidate


def list_candidates(
    db_path: Path = DEFAULT_DB_PATH, journal_id: str | None = None,
) -> list[dict]:
    if not db_path.is_file():
        return []
    with closing(_connect(db_path)) as db:
        rows = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE (? IS NULL OR journal_id=?) "
            "ORDER BY first_seen_at DESC, journal_id, post_url", (journal_id, journal_id),
        ).fetchall()
    return [_candidate_dict(row) for row in rows]


def _queue_summary(db: sqlite3.Connection, journal_id: str | None, cutoff: str) -> dict:
    summary = dict.fromkeys(CANDIDATE_STATES, 0)
    for row in db.execute(
        "SELECT status, COUNT(*) AS count FROM wechat_link_candidates "
        "WHERE (:journal IS NULL OR journal_id=:journal) GROUP BY status",
        {"journal": journal_id},
    ):
        summary[row["status"]] = row["count"]
    summary["total"] = sum(summary.values())
    blocked = db.execute(
        "SELECT COUNT(CASE WHEN julianday(blocked_since)<=julianday(:cutoff) THEN 1 END), "
        "COUNT(CASE WHEN blocked_since IS NULL THEN 1 END) FROM wechat_link_candidates "
        "WHERE status='blocked' AND (:journal IS NULL OR journal_id=:journal)",
        {"journal": journal_id, "cutoff": cutoff},
    ).fetchone()
    summary["long_blocked"], summary["blocked_age_unknown"] = blocked
    return summary


def candidate_summary(
    db_path: Path = DEFAULT_DB_PATH, journal_id: str | None = None,
    *, observed_at: datetime | None = None,
) -> dict:
    """Count queue states without returning the historical candidate list."""
    cutoff = (_user_local_time(observed_at, UTC) - timedelta(days=LONG_BLOCKED_DAYS)).isoformat()
    if not db_path.is_file():
        return {**dict.fromkeys(CANDIDATE_STATES, 0), "total": 0,
                "long_blocked": 0, "blocked_age_unknown": 0}
    with closing(_connect(db_path)) as db, db:
        db.execute("BEGIN")
        return _queue_summary(db, journal_id, cutoff)


def candidate_queue(
    db_path: Path = DEFAULT_DB_PATH, journal_id: str | None = None, *,
    queue_status: str = "active", limit: int = 20, offset: int = 0,
    observed_at: datetime | None = None,
) -> dict:
    """A bounded worklist: overdue/undated blocks, pending posts, then other blocks."""
    if queue_status not in {"active", "all", *CANDIDATE_STATES}:
        raise ValueError("unknown candidate queue status")
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("candidate queue requires limit 1..100 and nonnegative offset")
    cutoff = (_user_local_time(observed_at, UTC) - timedelta(days=LONG_BLOCKED_DAYS)).isoformat()
    result = {
        "queue_status": queue_status, "limit": limit, "offset": offset,
        "long_blocked_days": LONG_BLOCKED_DAYS, "matched_count": 0, "has_more": False,
        "candidates": [],
    }
    if not db_path.is_file():
        result["summary"] = candidate_summary(db_path, journal_id, observed_at=observed_at)
        return result
    where = (
        "(:journal IS NULL OR journal_id=:journal) AND "
        "(:status='all' OR status=:status OR "
        "(:status='active' AND status IN ('pending','blocked')))"
    )
    params = {"journal": journal_id, "status": queue_status, "cutoff": cutoff,
              "limit": limit, "offset": offset}
    with closing(_connect(db_path)) as db, db:
        db.execute("BEGIN")
        result["summary"] = _queue_summary(db, journal_id, cutoff)
        result["matched_count"] = db.execute(
            f"SELECT COUNT(*) FROM wechat_link_candidates WHERE {where}", params,
        ).fetchone()[0]
        rows = db.execute(
            f"SELECT *, CASE WHEN status='blocked' AND blocked_since IS NULL "
            "THEN 'blocked_age_unknown' WHEN status='blocked' AND "
            "julianday(blocked_since)<=julianday(:cutoff) THEN 'long_blocked' "
            "ELSE status END AS attention_reason "
            f"FROM wechat_link_candidates WHERE {where} ORDER BY "
            "CASE WHEN status='blocked' AND (blocked_since IS NULL OR "
            "julianday(blocked_since)<=julianday(:cutoff)) THEN 0 "
            "WHEN status='pending' THEN 1 WHEN status='blocked' THEN 2 ELSE 3 END, "
            "CASE WHEN status='pending' THEN julianday(first_seen_at) END DESC, "
            "CASE WHEN status='blocked' THEN julianday(COALESCE(blocked_since,first_seen_at)) END, "
            "journal_id, post_url LIMIT :limit OFFSET :offset", params,
        ).fetchall()
        result["candidates"] = [_candidate_dict(row) for row in rows]
    result["has_more"] = offset + len(rows) < result["matched_count"]
    return result


def _store_candidate(
    db: sqlite3.Connection, journal_id: str, post_url: str, source_url: str,
    label: str, timestamp: str,
) -> tuple[dict, bool]:
    """Keep the first discovery source and identify inserts within the write transaction."""
    created = db.execute("""INSERT INTO wechat_link_candidates
        (journal_id, post_url, source_url, link_text, first_seen_at, last_seen_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending') ON CONFLICT(journal_id, post_url) DO NOTHING""",
        (journal_id, post_url, source_url, label[:160], timestamp, timestamp),
    ).rowcount == 1
    if not created:
        db.execute(
            "UPDATE wechat_link_candidates SET last_seen_at=? WHERE journal_id=? AND post_url=?",
            (timestamp, journal_id, post_url),
        )
    row = db.execute(
        "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
        (journal_id, post_url),
    ).fetchone()
    return _candidate_dict(row), created


def add_candidate(
    db_path: Path, journal_id: str, post_url: str, source_url: str,
    *, label: str = "session discovery", catalog_path: Path = CATALOG_PATH,
) -> dict:
    """Queue a session-discovered exact post with its public discovery source."""
    normalized = normalize_post_url(post_url)
    try:
        source = urlsplit(source_url)
        valid_source = (
            source.scheme == "https" and bool(source.hostname)
            and not source.username and not source.password and source.port in (None, 443)
        )
    except ValueError:
        valid_source = False
    if normalized is None or not valid_source:
        raise ValueError("candidate requires direct post and HTTPS discovery source")
    if not any(
        item.id == journal_id and item.wechat_account
        for item in load_catalog(catalog_path)
    ):
        raise ValueError("journal has no registered WeChat account")
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    with closing(_connect(db_path)) as db, db:
        row, created = _store_candidate(db, journal_id, normalized, source_url, label, timestamp)
    return {**row, "newly_added": created}


def discover_publisher_links(
    db_path: Path = DEFAULT_DB_PATH, *, catalog_path: Path = CATALOG_PATH,
    journal_id: str | None = None, client: httpx.Client | None = None,
    observed_at: datetime | None = None, user_timezone: tzinfo | None = None,
) -> list[dict]:
    """Read at most one registered publisher page per journal per user-local day."""
    catalog = load_catalog(catalog_path)
    if journal_id and journal_id not in {item.id for item in catalog}:
        raise ValueError("unknown official journal")
    journals = [
        item for item in catalog
        if item.wechat_account and (journal_id is None or item.id == journal_id)
    ]
    active = client or httpx.Client(timeout=20, follow_redirects=False)
    results: list[dict] = []
    try:
        with closing(_connect(db_path)) as db:
            for journal in journals:
                source_url = journal.official_url
                now = _user_local_time(observed_at, user_timezone)
                timestamp = now.astimezone(UTC).isoformat(timespec="seconds")
                local_date = now.date().isoformat()
                prior, scan_id = _reserve_scan(
                    db, journal.id, local_date, source_url, timestamp, user_timezone,
                )
                if prior is not None:
                    results.append({"journal": journal.id, "status": "already_scanned",
                                    "prior_status": prior["status"],
                                    "links_found": prior["links_found"], "error": prior["error"],
                                    "new_candidates": []})
                    continue
                status, links, error = "ok", [], None
                try:
                    links = extract_post_links(_fetch_page(active, source_url))
                except ValueError as exc:
                    status, error = "failed", str(exc)
                new_candidates = []
                with db:
                    for post_url, label in links:
                        candidate, created = _store_candidate(
                            db, journal.id, post_url, source_url, label, timestamp,
                        )
                        if created:
                            new_candidates.append(candidate)
                    db.execute(
                        "UPDATE wechat_link_scans SET status=?, links_found=?, error=? "
                        "WHERE scan_id=?",
                        (status, len(links), error, scan_id),
                    )
                results.append({"journal": journal.id, "status": status,
                                "links_found": len(links), "error": error,
                                "new_candidates": new_candidates})
    finally:
        if client is None:
            active.close()
    return results


def review_candidate_issue(
    db_path: Path, issue: OfficialIssue, *, note: str | None = None,
    confirmed: bool = False, catalog_path: Path = CATALOG_PATH,
    account_display_name: str | None = None, toc_complete: bool = False,
) -> dict:
    """Promote a confirmed original post and its queue row in one transaction."""
    if not confirmed:
        raise ValueError("reviewed candidate requires original-post confirmation")
    if issue.evidence_kind != "official-wechat" or issue.status != "reviewed":
        raise ValueError("candidate review requires a reviewed official WeChat issue")
    if not account_display_name or not account_display_name.strip():
        raise ValueError("candidate review requires the original post's account display name")
    if toc_complete is not True or not issue.announcement_on:
        raise ValueError("candidate review requires a complete TOC and stated announcement date")
    _validate_issue(issue, load_catalog(catalog_path))
    with closing(_connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        candidate = db.execute(
            "SELECT status FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (issue.journal_id, issue.evidence_url),
        ).fetchone()
        if candidate is None or candidate["status"] not in {"pending", "blocked"}:
            raise ValueError("wechat-review requires an existing pending or blocked candidate")
        _write_issue(db, issue)
        reviewed_at = datetime.now(UTC).isoformat(timespec="seconds")
        db.execute(
            "UPDATE wechat_link_candidates SET status='reviewed', review_note=?, "
            "reviewed_account_name=?, reviewed_announcement_on=?, reviewed_toc_complete=1, "
            "reviewed_at=?, last_checked_at=? "
            "WHERE journal_id=? AND post_url=?",
            (note or "original post and account identity inspected in session",
             account_display_name.strip(), issue.announcement_on, reviewed_at, reviewed_at,
             issue.journal_id, issue.evidence_url),
        )
        reviewed = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (issue.journal_id, issue.evidence_url),
        ).fetchone()
    return _candidate_dict(reviewed)


def mark_candidate(
    db_path: Path, journal_id: str, post_url: str, *, status: str,
    note: str | None = None,
) -> dict:
    if status not in {"blocked", "dismissed"}:
        raise ValueError("invalid candidate status")
    if not note or not note.strip():
        raise ValueError("blocked or dismissed candidate requires a reason")
    normalized = normalize_post_url(post_url)
    if normalized is None:
        raise ValueError("invalid WeChat post URL")
    with closing(_connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (journal_id, normalized),
        ).fetchone()
        if row is None:
            raise ValueError("unknown WeChat candidate")
        if row["status"] == "reviewed":
            raise ValueError("reviewed candidate cannot be downgraded")
        checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        blocked_since = row["blocked_since"]
        if status == "blocked" and (row["status"] != "blocked" or blocked_since is None):
            blocked_since = checked_at
        db.execute(
            "UPDATE wechat_link_candidates SET status=?, review_note=?, blocked_since=?, "
            "last_checked_at=? WHERE journal_id=? AND post_url=?",
            (status, note.strip(), blocked_since, checked_at, journal_id, normalized),
        )
        updated = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (journal_id, normalized),
        ).fetchone()
    return _candidate_dict(updated)
