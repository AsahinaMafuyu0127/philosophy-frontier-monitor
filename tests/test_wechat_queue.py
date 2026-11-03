"""Daily worklists retain discovery and review evidence without network I/O."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from philosophy_frontier_monitor import cli
from philosophy_frontier_monitor.official_journals import list_issues, new_observation
from philosophy_frontier_monitor.wechat_discovery import (
    add_candidate,
    candidate_queue,
    candidate_summary,
    discover_publisher_links,
    list_candidates,
    mark_candidate,
    review_candidate_issue,
)

NOW = datetime(2026, 11, 3, 4, tzinfo=UTC)
JOURNAL = "xiandai-zhexue"
SOURCE = "https://mphilosophy.sysu.edu.cn/cat/124"


def _post(name: str) -> str:
    return f"https://mp.weixin.qq.com/s/{name}_article_123"


def _seed(
    db: Path, name: str, status: str, *, first: datetime = NOW,
    blocked_since: datetime | None = None, journal: str = JOURNAL,
) -> str:
    """Queue-only fixtures, including legacy reviewed rows with no audit detail."""
    post = _post(name)
    add_candidate(db, journal, post, SOURCE)
    with sqlite3.connect(db) as saved:
        saved.execute(
            "UPDATE wechat_link_candidates SET status=?, first_seen_at=?, blocked_since=? "
            "WHERE journal_id=? AND post_url=?",
            (status, first.isoformat(), blocked_since.isoformat() if blocked_since else None,
             journal, post),
        )
    return post


def test_worklist_is_filtered_bounded_and_prioritizes_old_blocks(tmp_path: Path) -> None:
    db = tmp_path / "queue.sqlite3"
    old_pending = _seed(db, "oldpending", "pending", first=NOW - timedelta(days=2))
    new_pending = _seed(db, "newpending", "pending")
    long_block = _seed(db, "longblocked", "blocked", blocked_since=NOW - timedelta(days=7))
    recent_block = _seed(db, "recentblock", "blocked", blocked_since=NOW - timedelta(days=6))
    unknown_block = _seed(db, "unknownblock", "blocked", first=NOW - timedelta(days=20))
    dismissed = _seed(db, "dismissed", "dismissed")
    reviewed = _seed(db, "reviewed", "reviewed")
    _seed(db, "otherjournal", "pending", journal="shijie-zhexue")

    first = candidate_queue(db, JOURNAL, limit=2, observed_at=NOW)
    assert first["summary"] == {
        "pending": 2, "blocked": 3, "dismissed": 1, "reviewed": 1, "total": 7,
        "long_blocked": 1, "blocked_age_unknown": 1,
    }
    assert first["matched_count"] == 5 and first["has_more"]
    assert [item["post_url"] for item in first["candidates"]] == [unknown_block, long_block]
    assert [item["attention_reason"] for item in first["candidates"]] == [
        "blocked_age_unknown", "long_blocked",
    ]
    second = candidate_queue(db, JOURNAL, limit=2, offset=2, observed_at=NOW)
    assert [item["post_url"] for item in second["candidates"]] == [new_pending, old_pending]
    last = candidate_queue(db, JOURNAL, limit=2, offset=4, observed_at=NOW)
    assert [item["post_url"] for item in last["candidates"]] == [recent_block]
    assert not last["has_more"]
    assert candidate_queue(db, JOURNAL, queue_status="all")["matched_count"] == 7
    for status, post in (("dismissed", dismissed), ("reviewed", reviewed)):
        selected = candidate_queue(db, JOURNAL, queue_status=status)
        assert [item["post_url"] for item in selected["candidates"]] == [post]
    assert candidate_summary(db)["total"] == 8


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"queue_status": "invalid"},
])
def test_invalid_worklist_arguments_do_not_create_a_database(tmp_path: Path, kwargs) -> None:
    db = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError):
        candidate_queue(db, **kwargs)
    assert not db.exists()


def test_empty_queue_is_read_only_and_has_explicit_counts(tmp_path: Path) -> None:
    db = tmp_path / "missing.sqlite3"
    result = candidate_queue(db)
    assert result["candidates"] == [] and result["matched_count"] == 0
    assert result["summary"]["total"] == 0
    assert result["limit"] == 20 and not result["has_more"]
    assert not db.exists()


def test_default_page_does_not_dump_a_growing_history(tmp_path: Path) -> None:
    db = tmp_path / "queue.sqlite3"
    for number in range(23):
        _seed(db, f"pending{number:03d}", "pending", first=NOW + timedelta(seconds=number))
    first = candidate_queue(db, observed_at=NOW)
    second = candidate_queue(db, offset=20, observed_at=NOW)
    assert len(first["candidates"]) == 20 and first["has_more"]
    assert len(second["candidates"]) == 3 and not second["has_more"]
    assert len({item["post_url"] for item in first["candidates"] + second["candidates"]}) == 23


def test_legacy_candidates_keep_unknown_review_and_block_dates(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db) as saved:
        saved.execute("""CREATE TABLE wechat_link_candidates (
            journal_id TEXT NOT NULL, post_url TEXT NOT NULL, source_url TEXT NOT NULL,
            link_text TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', review_note TEXT,
            PRIMARY KEY (journal_id, post_url))""")
        for status in ("blocked", "reviewed"):
            saved.execute(
                "INSERT INTO wechat_link_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (JOURNAL, _post(status), SOURCE, "旧目录", NOW.isoformat(), NOW.isoformat(),
                 status, "old note"),
            )
    result = candidate_queue(db, queue_status="all", observed_at=NOW + timedelta(days=40))
    assert result["summary"]["blocked_age_unknown"] == 1
    assert result["summary"]["long_blocked"] == 0
    for item in result["candidates"]:
        assert item["reviewed_at"] is None and item["reviewed_toc_complete"] is None
        assert item["blocked_since"] is None and item["review_note"] == "old note"
        assert item["source_url"] == SOURCE


def test_discovery_returns_real_inserts_only_and_preserves_previous_state(tmp_path: Path) -> None:
    db = tmp_path / "queue.sqlite3"
    previous = _seed(db, "previous", "pending")
    mark_candidate(db, JOURNAL, previous, status="blocked", note="verification page")
    fresh = _post("fresh")
    body = f'<a href="{previous}">old</a><a href="{fresh}">new</a>'
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/html"}, text=body,
    ))) as client:
        first = discover_publisher_links(db, journal_id=JOURNAL, client=client,
                                         observed_at=NOW, user_timezone=UTC)[0]
        repeated = discover_publisher_links(db, journal_id=JOURNAL, client=client,
                                            observed_at=NOW, user_timezone=UTC)[0]
        tomorrow = discover_publisher_links(
            db, journal_id=JOURNAL, client=client,
            observed_at=NOW + timedelta(days=1), user_timezone=UTC,
        )[0]
    assert first["links_found"] == 2
    assert [item["post_url"] for item in first["new_candidates"]] == [fresh]
    assert repeated["new_candidates"] == tomorrow["new_candidates"] == []
    old = next(item for item in list_candidates(db) if item["post_url"] == previous)
    assert old["status"] == "blocked" and old["review_note"] == "verification page"
    assert old["first_seen_at"] == NOW.isoformat() and old["source_url"] == SOURCE
    assert candidate_summary(db)["total"] == 2
    assert list_issues(db) == ()


def test_session_duplicate_is_not_new_and_keeps_its_first_source(tmp_path: Path) -> None:
    db = tmp_path / "queue.sqlite3"
    first = add_candidate(db, JOURNAL, _post("session"), SOURCE)
    mark_candidate(db, JOURNAL, _post("session"), status="dismissed", note="wrong issue")
    again = add_candidate(db, JOURNAL, _post("session"), "https://example.org/discovery")
    assert first["newly_added"] is True and again["newly_added"] is False
    assert again["source_url"] == SOURCE and again["status"] == "dismissed"


def test_discovery_cli_reports_delta_and_counts_without_dumping_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    db = tmp_path / "queue.sqlite3"
    _seed(db, "history", "dismissed")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/html"}, text=f'<a href="{_post("newpost")}">目录</a>',
    ))) as client:
        def discover(db_path, **kwargs):
            return discover_publisher_links(db_path, **kwargs, client=client, observed_at=NOW)

        monkeypatch.setattr(cli, "discover_publisher_links", discover)
        args = cli.build_parser().parse_args([
            "journal-watch", "wechat-discover", "--journal", JOURNAL, "--db", str(db),
            "--timezone", "UTC",
        ])
        assert args.handler(args) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["new_candidates_count"] == first["scans"][0]["new_candidates_count"] == 1
        assert first["candidate_summary"]["dismissed"] == 1
        assert "candidates" not in first
        assert first["new_candidates"][0]["post_url"] == _post("newpost")
        assert args.handler(args) == 0
        repeated = json.loads(capsys.readouterr().out)
        assert repeated["new_candidates"] == [] and repeated["new_candidates_count"] == 0
    selected = cli.build_parser().parse_args([
        "journal-watch", "wechat-candidates", "--db", str(db), "--queue-status", "dismissed",
        "--limit", "1",
    ])
    assert selected.handler(selected) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["network_queries"] == 0 and output["matched_count"] == 1
    assert output["candidates"][0]["status"] == "dismissed"


def test_block_updates_retain_continuous_block_start_and_require_a_reason(tmp_path: Path) -> None:
    db = tmp_path / "queue.sqlite3"
    post = _seed(db, "blocked", "blocked", blocked_since=NOW - timedelta(days=9))
    old_since = (NOW - timedelta(days=9)).isoformat()
    updated = mark_candidate(db, JOURNAL, post, status="blocked", note="still challenged")
    assert updated["blocked_since"] == old_since
    assert updated["last_checked_at"].endswith("+00:00")
    with pytest.raises(ValueError, match="reason"):
        mark_candidate(db, JOURNAL, post, status="dismissed", note=" ")
    with pytest.raises(ValueError, match="invalid candidate status"):
        mark_candidate(db, JOURNAL, post, status="reviewed", note="skip original review")
    assert list_candidates(db)[0]["status"] == "blocked"


@pytest.mark.parametrize("missing", ["account", "toc", "announcement"])
def test_review_requires_structured_original_post_attestation(tmp_path: Path, missing: str) -> None:
    db = tmp_path / "queue.sqlite3"
    post = _seed(db, "review", "pending")
    issue = new_observation(
        JOURNAL, 2026, "5", post, "official-wechat", label_month="2026-09",
        announcement_on=None if missing == "announcement" else "2026-10-09", status="reviewed",
    )
    with pytest.raises(ValueError):
        review_candidate_issue(
            db, issue, confirmed=True,
            account_display_name=None if missing == "account" else "现代哲学杂志",
            toc_complete=missing != "toc",
        )
    assert list_candidates(db)[0]["status"] == "pending"
    assert list_issues(db) == ()


def test_review_cli_normalizes_shared_url_and_keeps_reviewed_rows_immutable(
    tmp_path: Path, capsys,
) -> None:
    db = tmp_path / "queue.sqlite3"
    post = _seed(db, "review", "pending")
    args = cli.build_parser().parse_args([
        "journal-watch", "wechat-review", "--db", str(db), "--journal", JOURNAL,
        "--year", "2026", "--issue", "5", "--evidence-url", post + "?scene=12",
        "--announcement-on", "2026-10-09", "--account-name", "现代哲学杂志",
        "--toc-complete", "--confirm-evidence",
    ])
    assert args.handler(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["issue"]["evidence_url"] == post
    assert result["candidate"]["reviewed_toc_complete"] is True
    assert result["candidate"]["reviewed_announcement_on"] == "2026-10-09"
    with pytest.raises(ValueError, match="cannot be downgraded"):
        mark_candidate(db, JOURNAL, post, status="dismissed", note="queue cleanup")
    assert list_issues(db, reviewed_only=True)[0].label_month is None
