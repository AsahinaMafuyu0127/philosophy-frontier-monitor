"""Bounded publisher-page discovery of WeChat links, never WeChat-page crawling."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

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
POST_ID = re.compile(r"/s/[A-Za-z0-9_-]{8,}\Z")
RAW_POST = re.compile(r"https://mp\.weixin\.qq\.com/s(?:/[A-Za-z0-9_-]+|\?[^\s\"'<>]+)")
BEIJING = ZoneInfo("Asia/Shanghai")


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
    db.execute("""CREATE TABLE IF NOT EXISTS wechat_link_scans (
        journal_id TEXT NOT NULL, local_date TEXT NOT NULL, source_url TEXT NOT NULL,
        observed_at TEXT NOT NULL, status TEXT NOT NULL, links_found INTEGER NOT NULL,
        error TEXT, PRIMARY KEY (journal_id, local_date, source_url))""")
    db.execute("""CREATE TABLE IF NOT EXISTS wechat_link_candidates (
        journal_id TEXT NOT NULL, post_url TEXT NOT NULL, source_url TEXT NOT NULL,
        link_text TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', review_note TEXT,
        PRIMARY KEY (journal_id, post_url))""")
    return db


def _reserve_scan(
    db: sqlite3.Connection, journal_id: str, local_date: str,
    source_url: str, timestamp: str,
) -> sqlite3.Row | None:
    """Claim the journal's local-day page allowance before any HTTP request."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT status, links_found, error FROM wechat_link_scans "
            "WHERE journal_id=? AND local_date=? "
            "ORDER BY observed_at, source_url LIMIT 1",
            (journal_id, local_date),
        ).fetchone()
        if prior is None:
            db.execute(
                "INSERT INTO wechat_link_scans "
                "(journal_id, local_date, source_url, observed_at, status, links_found, error) "
                "VALUES (?, ?, ?, ?, 'attempted', 0, NULL)",
                (journal_id, local_date, source_url, timestamp),
            )
    return prior


def list_candidates(
    db_path: Path = DEFAULT_DB_PATH, journal_id: str | None = None,
) -> list[dict]:
    if not db_path.is_file():
        return []
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE (? IS NULL OR journal_id=?) "
            "ORDER BY first_seen_at DESC, journal_id, post_url", (journal_id, journal_id),
        ).fetchall()
    return [dict(row) for row in rows]


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
    timestamp = datetime.now(BEIJING).isoformat(timespec="seconds")
    with _connect(db_path) as db:
        db.execute("""INSERT INTO wechat_link_candidates
            (journal_id, post_url, source_url, link_text, first_seen_at,
             last_seen_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(journal_id, post_url) DO UPDATE SET
            last_seen_at=excluded.last_seen_at""",
            (journal_id, normalized, source_url, label[:160], timestamp, timestamp),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (journal_id, normalized),
        ).fetchone()
    return dict(row)


def discover_publisher_links(
    db_path: Path = DEFAULT_DB_PATH, *, catalog_path: Path = CATALOG_PATH,
    journal_id: str | None = None, client: httpx.Client | None = None,
    observed_at: datetime | None = None,
) -> list[dict]:
    """Read at most one registered publisher page per account per Beijing day."""
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
                now = (observed_at or datetime.now(BEIJING)).astimezone(BEIJING)
                timestamp = now.isoformat(timespec="seconds")
                local_date = now.date().isoformat()
                prior = _reserve_scan(db, journal.id, local_date, source_url, timestamp)
                if prior is not None:
                    results.append({"journal": journal.id, "status": "already_scanned",
                                    "prior_status": prior["status"],
                                    "links_found": prior["links_found"], "error": prior["error"]})
                    continue
                status, links, error = "ok", [], None
                try:
                    links = extract_post_links(_fetch_page(active, source_url))
                except ValueError as exc:
                    status, error = "failed", str(exc)
                with db:
                    for post_url, label in links:
                        db.execute("""INSERT INTO wechat_link_candidates
                            (journal_id, post_url, source_url, link_text, first_seen_at,
                             last_seen_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                            ON CONFLICT(journal_id, post_url) DO UPDATE SET
                            last_seen_at=excluded.last_seen_at""",
                            (journal.id, post_url, source_url, label, timestamp, timestamp),
                        )
                    db.execute(
                        "UPDATE wechat_link_scans SET status=?, links_found=?, error=? "
                        "WHERE journal_id=? AND local_date=? AND source_url=?",
                        (status, len(links), error, journal.id, local_date, source_url),
                    )
                results.append({"journal": journal.id, "status": status,
                                "links_found": len(links), "error": error})
    finally:
        if client is None:
            active.close()
    return results


def review_candidate_issue(
    db_path: Path, issue: OfficialIssue, *, note: str | None = None,
    confirmed: bool = False, catalog_path: Path = CATALOG_PATH,
) -> dict:
    """Promote a confirmed original post and its queue row in one transaction."""
    if not confirmed:
        raise ValueError("reviewed candidate requires original-post confirmation")
    if issue.evidence_kind != "official-wechat" or issue.status != "reviewed":
        raise ValueError("candidate review requires a reviewed official WeChat issue")
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
        db.execute(
            "UPDATE wechat_link_candidates SET status='reviewed', review_note=? "
            "WHERE journal_id=? AND post_url=?",
            (note or "original post and account identity inspected in session",
             issue.journal_id, issue.evidence_url),
        )
        reviewed = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (issue.journal_id, issue.evidence_url),
        ).fetchone()
    return dict(reviewed)


def mark_candidate(
    db_path: Path, journal_id: str, post_url: str, *, status: str,
    note: str | None = None, confirmed: bool = False,
) -> dict:
    if status not in {"blocked", "dismissed", "reviewed"}:
        raise ValueError("invalid candidate status")
    if status == "reviewed" and not confirmed:
        raise ValueError("reviewed candidate requires original-post confirmation")
    normalized = normalize_post_url(post_url)
    if normalized is None:
        raise ValueError("invalid WeChat post URL")
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (journal_id, normalized),
        ).fetchone()
        if row is None:
            raise ValueError("unknown WeChat candidate")
        if row["status"] == "reviewed" and status != "reviewed":
            raise ValueError("reviewed candidate cannot be downgraded")
        db.execute(
            "UPDATE wechat_link_candidates SET status=?, review_note=? "
            "WHERE journal_id=? AND post_url=?", (status, note, journal_id, normalized),
        )
        db.commit()
        updated = db.execute(
            "SELECT * FROM wechat_link_candidates WHERE journal_id=? AND post_url=?",
            (journal_id, normalized),
        ).fetchone()
    return dict(updated)
