"""Curated publisher channels and separately reviewed issue observations.

The public catalog contains only publication channels. The private SQLite store
keeps observations, including tentative claims, outside the Git repository.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "official-journals.yaml"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "var" / "official-journals.sqlite3"
JOURNAL_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


@dataclass(frozen=True, slots=True)
class OfficialJournal:
    id: str
    title: str
    scope: str
    official_url: str
    channel: str
    review: str
    frequency: str | None = None
    schedule: str | None = None
    frequency_evidence_url: str | None = None


@dataclass(frozen=True, slots=True)
class OfficialIssue:
    journal_id: str
    title: str
    year: int
    issue: str
    evidence_url: str
    evidence_kind: str
    label_month: str | None
    issue_published_on: str | None
    announcement_on: str | None
    observed_at: str
    status: str
    reviewed_at: str | None = None


def _https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
    )


def load_catalog(path: Path = CATALOG_PATH) -> tuple[OfficialJournal, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("journals"), list)
    ):
        raise ValueError("invalid official-journal catalog")
    journals: list[OfficialJournal] = []
    seen: set[str] = set()
    for entry in raw["journals"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid official-journal entry")
        journal = OfficialJournal(**entry)
        if (
            not JOURNAL_ID.fullmatch(journal.id)
            or journal.id in seen
            or not journal.title.strip()
            or not _https_url(journal.official_url)
            or journal.review not in {"manual", "verify-issue-entry"}
            or (
                journal.frequency_evidence_url is not None
                and not _https_url(journal.frequency_evidence_url)
            )
        ):
            raise ValueError(f"invalid official-journal entry: {journal.id}")
        seen.add(journal.id)
        journals.append(journal)
    return tuple(journals)


def catalog_as_dicts(path: Path = CATALOG_PATH) -> list[dict[str, str | None]]:
    return [asdict(journal) for journal in load_catalog(path)]


def _valid_date(value: str | None) -> bool:
    if value is None:
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_issue(issue: OfficialIssue, catalog: tuple[OfficialJournal, ...]) -> None:
    journals = {journal.id: journal for journal in catalog}
    if issue.journal_id not in journals or issue.title != journals[issue.journal_id].title:
        raise ValueError("unknown or mismatched official journal")
    if issue.year < 1900 or issue.year > 2100 or not re.fullmatch(r"[0-9]{1,3}", issue.issue):
        raise ValueError("invalid journal year or issue number")
    if issue.label_month is not None:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", issue.label_month):
            raise ValueError("invalid explicitly labelled issue month")
        if int(issue.label_month[:4]) != issue.year:
            raise ValueError("issue label month conflicts with issue year")
    if not _valid_date(issue.issue_published_on) or not _valid_date(issue.announcement_on):
        raise ValueError("invalid day-precision evidence")
    if not _https_url(issue.evidence_url):
        raise ValueError("issue evidence must be an HTTPS URL")
    if issue.evidence_kind not in {"journal-site", "publisher-site", "official-wechat"}:
        raise ValueError("invalid official evidence kind")
    if issue.evidence_kind == "official-wechat":
        if urlsplit(issue.evidence_url).hostname != "mp.weixin.qq.com":
            raise ValueError("official WeChat evidence must link to mp.weixin.qq.com")
    elif (
        urlsplit(issue.evidence_url).hostname
        != urlsplit(journals[issue.journal_id].official_url).hostname
    ):
        raise ValueError("journal-site evidence must use the registered official host")
    if issue.status not in {"pending", "reviewed"}:
        raise ValueError("invalid evidence status")
    if issue.status == "reviewed" and not any(
        (issue.label_month, issue.issue_published_on, issue.announcement_on)
    ):
        raise ValueError("reviewed issue needs explicit date or month evidence")


def record_issue(db_path: Path, issue: OfficialIssue, *, catalog_path: Path = CATALOG_PATH) -> None:
    _validate_issue(issue, load_catalog(catalog_path))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS official_issues (
                journal_id TEXT NOT NULL, year INTEGER NOT NULL, issue TEXT NOT NULL,
                title TEXT NOT NULL, evidence_url TEXT NOT NULL, evidence_kind TEXT NOT NULL,
                label_month TEXT, issue_published_on TEXT, announcement_on TEXT,
                observed_at TEXT NOT NULL, status TEXT NOT NULL, reviewed_at TEXT,
                PRIMARY KEY (journal_id, year, issue, evidence_url)
            )"""
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(official_issues)")}
        if "reviewed_at" not in columns:
            db.execute("ALTER TABLE official_issues ADD COLUMN reviewed_at TEXT")
            db.execute("UPDATE official_issues SET reviewed_at=observed_at WHERE status='reviewed'")
        db.execute(
            """INSERT INTO official_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(journal_id, year, issue, evidence_url) DO UPDATE SET
                label_month=CASE WHEN official_issues.status='reviewed'
                    AND excluded.status='pending' THEN official_issues.label_month
                    ELSE excluded.label_month END,
                issue_published_on=CASE WHEN official_issues.status='reviewed'
                    AND excluded.status='pending' THEN official_issues.issue_published_on
                    ELSE excluded.issue_published_on END,
                announcement_on=CASE WHEN official_issues.status='reviewed'
                    AND excluded.status='pending' THEN official_issues.announcement_on
                    ELSE excluded.announcement_on END,
                status=CASE WHEN official_issues.status='reviewed'
                    THEN official_issues.status ELSE excluded.status END,
                reviewed_at=CASE
                    WHEN official_issues.status='reviewed'
                    THEN official_issues.reviewed_at
                    ELSE excluded.reviewed_at END""",
            (
                issue.journal_id,
                issue.year,
                issue.issue,
                issue.title,
                issue.evidence_url,
                issue.evidence_kind,
                issue.label_month,
                issue.issue_published_on,
                issue.announcement_on,
                issue.observed_at,
                issue.status,
                issue.reviewed_at,
            ),
        )


def list_issues(db_path: Path, *, reviewed_only: bool = False) -> tuple[OfficialIssue, ...]:
    if not db_path.is_file():
        return ()
    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_issues'"
        ).fetchone() is None:
            return ()
        rows = db.execute(
            "SELECT * FROM official_issues WHERE (? = 0 OR status = 'reviewed') "
            "ORDER BY observed_at DESC, journal_id, year DESC, issue DESC",
            (int(reviewed_only),),
        ).fetchall()
    return tuple(OfficialIssue(**dict(row)) for row in rows)


def review_observed_issue(
    db_path: Path, journal_id: str, year: int, issue: str, evidence_url: str, *,
    label_month: str | None = None, issue_published_on: str | None = None,
    announcement_on: str | None = None, catalog_path: Path = CATALOG_PATH,
) -> OfficialIssue:
    """Promote one already observed source without replacing its first-seen evidence."""
    matches = [
        item for item in list_issues(db_path)
        if (item.journal_id, item.year, item.issue, item.evidence_url)
        == (journal_id, year, issue, evidence_url)
    ]
    if len(matches) != 1:
        raise ValueError("review requires one existing observed issue and exact evidence URL")
    previous = matches[0]
    if previous.status == "reviewed":
        raise ValueError("issue already reviewed; use observe to correct explicit evidence")
    reviewed = replace(
        previous,
        label_month=label_month or previous.label_month,
        issue_published_on=issue_published_on or previous.issue_published_on,
        announcement_on=announcement_on or previous.announcement_on,
        status="reviewed",
        reviewed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    record_issue(db_path, reviewed, catalog_path=catalog_path)
    return next(
        item for item in list_issues(db_path)
        if (item.journal_id, item.year, item.issue, item.evidence_url)
        == (journal_id, year, issue, evidence_url)
    )


def issues_in_window(
    issues: tuple[OfficialIssue, ...], start: date, end: date
) -> tuple[OfficialIssue, ...]:
    """Select issue-level evidence without inventing a day for month labels."""
    selected: list[OfficialIssue] = []
    for issue in issues:
        if issue.status != "reviewed":
            continue
        day_match = any(
            start <= date.fromisoformat(value) <= end
            for value in (issue.issue_published_on, issue.announcement_on)
            if value is not None
        )
        month_match = False
        if issue.label_month is not None:
            year, month = map(int, issue.label_month.split("-"))
            month_match = (start.year, start.month) <= (year, month) <= (end.year, end.month)
        if day_match or month_match:
            selected.append(issue)
    return tuple(selected)


def distinct_issue_leads(issues: tuple[OfficialIssue, ...]) -> tuple[OfficialIssue, ...]:
    """Show one lead per journal issue while retaining all evidence in the store."""
    seen: set[tuple[str, int, str]] = set()
    selected: list[OfficialIssue] = []
    for issue in issues:
        key = (issue.journal_id, issue.year, issue.issue)
        if key not in seen:
            seen.add(key)
            selected.append(issue)
    return tuple(selected)


def render_issue_leads(issues: tuple[OfficialIssue, ...], *, english: bool = False) -> str:
    lines = [
        "## Publisher issue leads" if english else "## 期刊官方发布渠道：新期次线索",
        "",
    ]
    lines.append(
        "These reviewed issue leads keep announcement dates, issue label months, and issue "
        "publication dates separate. An issue does not establish that individual articles "
        "match interests or first appeared in this window."
        if english
        else "这些是经过人工核对的期次线索；公告日期、期次标示月份与刊出日期分别保留。"
        "期次目录不自动证明逐篇论文符合兴趣标签或首次发表于本窗口。"
    )
    lines.append("")
    if not issues:
        lines.append(
            "There is no reviewed publisher issue evidence in the local store for this window. "
            "This does not establish that no new issue exists."
            if english
            else "本地尚无本窗口内已复核的官方期次证据；这不表示期刊没有新期。"
        )
    for issue in issues:
        if english:
            lines.append(
                f"- [{issue.title}, {issue.year} issue {issue.issue}]({issue.evidence_url}); "
                f"label month: {issue.label_month or 'not stated'}; "
                f"issue date: {issue.issue_published_on or 'not stated'}; "
                f"announcement date: {issue.announcement_on or 'not stated'}; "
                f"first local observation: {issue.observed_at}; "
                f"reviewed: {issue.reviewed_at or 'not stated'}."
            )
        else:
            lines.append(
                f"- [{issue.title} {issue.year} 年第 {issue.issue} 期]({issue.evidence_url})；"
                f"期次标示月份：{issue.label_month or '未明示'}；"
                f"刊出日：{issue.issue_published_on or '未明示'}；"
                f"官方公告日：{issue.announcement_on or '未明示'}；"
                f"本地首次记录：{issue.observed_at}；"
                f"核实时间：{issue.reviewed_at or '未记录'}。"
            )
    return "\n".join(lines) + "\n"


def new_observation(
    journal_id: str,
    year: int,
    issue: str,
    evidence_url: str,
    evidence_kind: str,
    *,
    label_month: str | None = None,
    issue_published_on: str | None = None,
    announcement_on: str | None = None,
    status: str = "pending",
    catalog_path: Path = CATALOG_PATH,
) -> OfficialIssue:
    journal = next((item for item in load_catalog(catalog_path) if item.id == journal_id), None)
    if journal is None:
        raise ValueError("unknown official journal")
    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return OfficialIssue(
        journal_id=journal_id,
        title=journal.title,
        year=year,
        issue=issue,
        evidence_url=evidence_url,
        evidence_kind=evidence_kind,
        label_month=label_month,
        issue_published_on=issue_published_on,
        announcement_on=announcement_on,
        observed_at=observed_at,
        status=status,
        reviewed_at=observed_at if status == "reviewed" else None,
    )
