"""Publisher links are leads until a human reads the original post."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from philosophy_frontier_monitor import cli
from philosophy_frontier_monitor.official_journals import list_issues
from philosophy_frontier_monitor.wechat_discovery import (
    add_candidate,
    discover_publisher_links,
    extract_post_links,
    list_candidates,
    mark_candidate,
    normalize_post_url,
)

POST = "https://mp.weixin.qq.com/s/cOimuAyoAiR-WV87o_I1rw"
BEIJING = ZoneInfo("Asia/Shanghai")


def test_only_direct_wechat_posts_are_normalized() -> None:
    assert normalize_post_url(POST + "?scene=12#part") == POST
    assert normalize_post_url("https://mp.weixin.qq.com.evil.example/s/abcdefgh") is None
    assert normalize_post_url("http://mp.weixin.qq.com/s/abcdefgh") is None
    assert normalize_post_url("https://mp.weixin.qq.com:invalid/s/abcdefgh") is None
    assert normalize_post_url("https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=1") is None
    assert normalize_post_url("https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=1&sn=z") == (
        "https://mp.weixin.qq.com/s?__biz=a&mid=1&idx=1&sn=z"
    )
    assert extract_post_links(
        f'<a href="{POST}?scene=12">目录</a><span>{POST}</span>'
    ) == [(POST, "目录")]


def test_bounded_publisher_scan_persists_candidates_once_per_local_day(tmp_path: Path) -> None:
    calls = 0
    db = tmp_path / "issues.sqlite3"
    now = datetime(2026, 10, 14, 8, tzinfo=BEIJING)

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "mphilosophy.sysu.edu.cn"
        with sqlite3.connect(db) as audit:
            assert audit.execute(
                "SELECT status FROM wechat_link_scans WHERE journal_id=? AND local_date=?",
                ("xiandai-zhexue", "2026-10-14"),
            ).fetchone()[0] == "attempted"
        with httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("a parallel same-day call must not request the page")
        )) as other_client:
            duplicate = discover_publisher_links(
                db, journal_id="xiandai-zhexue", client=other_client, observed_at=now,
            )
        assert duplicate[0]["prior_status"] == "attempted"
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"},
            text=f'<a href="{POST}">2026年第5期目录</a>',
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client, observed_at=now,
        )
        assert result == [{
            "journal": "xiandai-zhexue", "status": "ok", "links_found": 1, "error": None,
        }]
        repeated = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client, observed_at=now,
        )[0]
        assert repeated["status"] == "already_scanned"
        assert repeated["prior_status"] == "ok"
    assert calls == 1
    candidate = list_candidates(db)[0]
    assert candidate["post_url"] == POST
    assert candidate["status"] == "pending"
    assert list_issues(db) == ()
    assert mark_candidate(db, "xiandai-zhexue", POST, status="blocked")["status"] == "blocked"


def test_interrupted_request_keeps_daily_reservation(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    now = datetime(2026, 10, 14, 8, tzinfo=BEIJING)
    calls = 0

    def interrupted(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated process interruption")

    with httpx.Client(transport=httpx.MockTransport(interrupted)) as client:
        with pytest.raises(RuntimeError, match="simulated process interruption"):
            discover_publisher_links(
                db, journal_id="xiandai-zhexue", client=client, observed_at=now,
            )
        repeated = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client, observed_at=now,
        )[0]
    assert calls == 1
    assert repeated["status"] == "already_scanned"
    assert repeated["prior_status"] == "attempted"
    assert list_candidates(db) == []

    tomorrow = datetime(2026, 10, 15, 8, tzinfo=BEIJING)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, text="<p>No links</p>",
    ))) as client:
        resumed = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client, observed_at=tomorrow,
        )[0]
    assert resumed["status"] == "ok"


def test_changed_publisher_url_does_not_reset_journal_daily_allowance(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    now = datetime(2026, 10, 14, 8, tzinfo=BEIJING)
    with sqlite3.connect(db) as audit:
        audit.execute(
            "CREATE TABLE wechat_link_scans ("
            "journal_id TEXT NOT NULL, local_date TEXT NOT NULL, source_url TEXT NOT NULL, "
            "observed_at TEXT NOT NULL, status TEXT NOT NULL, links_found INTEGER NOT NULL, "
            "error TEXT, PRIMARY KEY (journal_id, local_date, source_url))"
        )
        audit.execute(
            "INSERT INTO wechat_link_scans VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("xiandai-zhexue", "2026-10-14", "https://previous.example/",
             now.isoformat(), "failed", 0, "publisher_discovery_http_403"),
        )
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: pytest.fail("a changed URL must not get a second same-day request")
    )) as client:
        repeated = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client, observed_at=now,
        )[0]
    assert repeated["status"] == "already_scanned"
    assert repeated["prior_status"] == "failed"
    assert repeated["error"] == "publisher_discovery_http_403"


@pytest.mark.parametrize("response, expected", [
    (httpx.Response(302, headers={"location": "https://example.org/"}),
     "publisher_discovery_redirect"),
    (httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"PDF"),
     "publisher_discovery_not_html"),
    (httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * 750_001),
     "publisher_discovery_page_too_large"),
])
def test_failed_page_is_recorded_without_a_candidate(
    tmp_path: Path, response: httpx.Response, expected: str,
) -> None:
    db = tmp_path / "issues.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        result = discover_publisher_links(
            db, journal_id="xiandai-zhexue", client=client,
            observed_at=datetime(2026, 10, 14, tzinfo=BEIJING),
        )
    assert result[0]["status"] == "failed"
    assert result[0]["error"] == expected
    assert list_candidates(db) == []


def test_wechat_review_needs_candidate_and_original_post_attestation(
    tmp_path: Path, capsys,
) -> None:
    db = tmp_path / "issues.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/html"},
            text=f'<a href="{POST}">期次目录</a>',
        )
    )) as client:
        discover_publisher_links(db, journal_id="xiandai-zhexue", client=client)
    parser = cli.build_parser()
    base = [
        "journal-watch", "wechat-review", "--db", str(db),
        "--journal", "xiandai-zhexue", "--year", "2026", "--issue", "5",
        "--evidence-url", POST, "--announcement-on", "2026-10-09",
    ]
    with pytest.raises(ValueError, match="confirm-evidence"):
        parser.parse_args(base).handler(parser.parse_args(base))
    assert list_issues(db) == ()
    args = parser.parse_args([*base, "--confirm-evidence"])
    assert args.handler(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["candidate"]["status"] == "reviewed"
    assert output["issue"]["evidence_kind"] == "official-wechat"
    assert list_issues(db, reviewed_only=True)[0].announcement_on == "2026-10-09"


def test_wechat_review_rolls_back_issue_if_queue_update_fails(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    add_candidate(db, "xiandai-zhexue", POST, "https://mphilosophy.sysu.edu.cn/cat/124")
    with sqlite3.connect(db) as audit:
        audit.execute(
            "CREATE TRIGGER reject_review BEFORE UPDATE OF status ON wechat_link_candidates "
            "WHEN NEW.status='reviewed' BEGIN SELECT RAISE(FAIL, 'review_update_blocked'); END"
        )
    args = cli.build_parser().parse_args([
        "journal-watch", "wechat-review", "--db", str(db),
        "--journal", "xiandai-zhexue", "--year", "2026", "--issue", "5",
        "--evidence-url", POST, "--announcement-on", "2026-10-09", "--confirm-evidence",
    ])
    with pytest.raises(sqlite3.IntegrityError, match="review_update_blocked"):
        args.handler(args)
    assert list_issues(db, reviewed_only=True) == ()
    assert list_candidates(db)[0]["status"] == "pending"


def test_session_candidate_keeps_source_and_candidate_account_needs_extra_review(
    tmp_path: Path,
) -> None:
    db = tmp_path / "issues.sqlite3"
    queued = add_candidate(
        db, "shijie-zhexue", POST, "https://phil.hust.edu.cn/info/1048/5500.htm",
    )
    assert queued["status"] == "pending"
    assert queued["source_url"].startswith("https://phil.hust.edu.cn/")
    parser = cli.build_parser()
    base = [
        "journal-watch", "wechat-review", "--db", str(db),
        "--journal", "shijie-zhexue", "--year", "2026", "--issue", "5",
        "--evidence-url", POST, "--announcement-on", "2026-10-09",
        "--confirm-evidence",
    ]
    args = parser.parse_args(base)
    with pytest.raises(ValueError, match="confirm-account-identity"):
        args.handler(args)
    assert list_issues(db) == ()
