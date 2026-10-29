"""Official issue evidence must stay distinct from database and article dates."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from philosophy_frontier_monitor import cli
from philosophy_frontier_monitor.cnki_dates import _official_months_for_cnki
from philosophy_frontier_monitor.official_journals import (
    issues_in_window,
    list_issues,
    load_catalog,
    new_observation,
    record_issue,
    review_observed_issue,
    wechat_discovery_plan,
)
from philosophy_frontier_monitor.pipeline import _official_issues_first_reviewed_in_window
from philosophy_frontier_monitor.sources.cnki_space import CnkiIssue


def test_curated_catalog_has_verifiable_primary_channels() -> None:
    catalog = load_catalog()
    titles = {journal.title for journal in catalog}
    assert len(catalog) >= 12
    assert {"哲学研究", "哲学动态", "世界哲学"} <= titles
    assert all(journal.official_url.startswith("https://") for journal in catalog)


def test_wechat_discovery_plan_has_sourced_accounts_and_no_network(capsys) -> None:
    plan = wechat_discovery_plan()
    assert {item["journal_id"] for item in plan} == {
        "xiandai-zhexue", "zhexue-dongtai", "zhexue-yanjiu",
        "shijie-zhexue", "zhouyi-yanjiu", "kongzi-yanjiu",
        "zhongguo-shehui-kexue", "zhongguo-zhexueshi",
    }
    assert all(item["identity_url"].startswith("https://") for item in plan)
    assert all(
        not item["verified_post"]
        or item["verified_post"].startswith("https://mp.weixin.qq.com/s/")
        for item in plan
    )
    assert next(item for item in plan if item["journal_id"] == "shijie-zhexue")[
        "identity_status"
    ] == "candidate"
    assert all(item["status"] == "discovery_only" for item in plan)
    args = cli.build_parser().parse_args(["journal-watch", "wechat-discovery-plan"])
    assert args.handler(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["network_queries"] == 0
    assert output["evidence_status"] == "unverified_until_original_post_review"
    assert output["wechat_discovery_plan"] == plan


def test_catalog_reports_automatic_and_manual_toc_channels(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["journal-watch", "catalog"])
    assert args.handler(args) == 0
    journals = json.loads(capsys.readouterr().out)["journals"]
    assert len(journals) == 15
    assert sum(journal["toc_collection"] == "automatic" for journal in journals) == 9
    assert sum(journal["toc_collection"] == "manual" for journal in journals) == 6
    assert next(journal for journal in journals if journal["id"] == "lunlixue-yanjiu")[
        "toc_collection"
    ] == "manual"


def test_pending_wechat_observation_does_not_become_a_recent_issue(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    pending = new_observation(
        "zhexue-dongtai",
        2026,
        "9",
        "https://mp.weixin.qq.com/s/eeYI8s6Y_f9J3zbJ73XgSg",
        "official-wechat",
    )
    record_issue(db, pending)
    assert len(list_issues(db)) == 1
    assert list_issues(db, reviewed_only=True) == ()
    assert issues_in_window(list_issues(db), date(2026, 9, 1), date(2026, 9, 30)) == ()


def test_reviewed_month_from_publisher_enriches_missing_cnki_month_only(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    official = new_observation(
        "ziran-bianzhengfa-tongxun",
        2026,
        "9",
        "https://jdn.ucas.ac.cn/home/journal/cataloglist/cid/349",
        "journal-site",
        label_month="2026-09",
        status="reviewed",
    )
    record_issue(db, official)
    reviewed = list_issues(db, reviewed_only=True)
    assert issues_in_window(reviewed, date(2026, 8, 29), date(2026, 9, 27)) == reviewed
    assert issues_in_window(reviewed, date(2026, 10, 1), date(2026, 10, 31)) == ()

    cnki = CnkiIssue(
        key="fixture",
        venue="自然辩证法通讯",
        year=2026,
        issue="9",
        label_month=None,
        records=(),
        queries=(),
        observed_at=datetime(2026, 9, 27, tzinfo=UTC),
    )
    assert _official_months_for_cnki((cnki,), reviewed)[0].label_month == 9
    conflicting = _official_months_for_cnki((replace(cnki, label_month=8),), reviewed)[0]
    assert conflicting.label_month is None
    assert conflicting.date_conflict


def test_wechat_host_cannot_be_substituted_for_official_evidence(tmp_path: Path) -> None:
    issue = new_observation(
        "zhexue-dongtai", 2026, "9", "https://example.org/post", "official-wechat"
    )
    with pytest.raises(ValueError, match=r"mp\.weixin\.qq\.com"):
        record_issue(tmp_path / "issues.sqlite3", issue)
    assert not (tmp_path / "issues.sqlite3").exists()


def test_reviewed_observation_needs_an_explicit_date_or_month(tmp_path: Path) -> None:
    issue = new_observation(
        "zhexue-dongtai",
        2026,
        "9",
        "https://mp.weixin.qq.com/s/eeYI8s6Y_f9J3zbJ73XgSg",
        "official-wechat",
        status="reviewed",
    )
    with pytest.raises(ValueError, match="explicit date or month"):
        record_issue(tmp_path / "issues.sqlite3", issue)


def test_collected_issue_review_preserves_first_seen_and_cannot_be_downgraded(
    tmp_path: Path,
) -> None:
    db = tmp_path / "issues.sqlite3"
    pending = replace(
        new_observation(
            "zhexue-dongtai", 2026, "8",
            "https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paper_list/type_benqi",
            "journal-site",
        ),
        observed_at="2026-09-20T08:00:00+08:00",
    )
    record_issue(db, pending)
    with pytest.raises(ValueError, match="explicit date or month"):
        review_observed_issue(db, pending.journal_id, pending.year, pending.issue,
                              pending.evidence_url)
    assert list_issues(db)[0].status == "pending"

    reviewed = review_observed_issue(
        db, pending.journal_id, pending.year, pending.issue, pending.evidence_url,
        label_month="2026-08",
    )
    assert reviewed.status == "reviewed"
    assert reviewed.observed_at == pending.observed_at
    assert reviewed.reviewed_at is not None
    assert issues_in_window((reviewed,), date(2026, 8, 1), date(2026, 8, 31)) == (reviewed,)

    record_issue(db, replace(pending, observed_at="2026-09-28T08:00:00+08:00"))
    after_rescan = list_issues(db)[0]
    assert after_rescan.status == "reviewed"
    assert after_rescan.label_month == "2026-08"
    assert after_rescan.observed_at == pending.observed_at
    assert after_rescan.reviewed_at == reviewed.reviewed_at


def test_review_requires_exact_previously_observed_source(tmp_path: Path) -> None:
    db = tmp_path / "issues.sqlite3"
    with pytest.raises(ValueError, match="existing observed issue"):
        review_observed_issue(
            db, "zhexue-dongtai", 2026, "8", "https://zxdt.cbpt.cnki.net/other",
            label_month="2026-08",
        )
    assert not db.exists()


def test_journal_watch_pending_and_review_cli(tmp_path: Path, capsys) -> None:
    db = tmp_path / "issues.sqlite3"
    pending = new_observation(
        "zhexue-dongtai", 2026, "8",
        "https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paper_list/type_benqi",
        "journal-site",
    )
    record_issue(db, pending)
    parser = cli.build_parser()
    base = ["journal-watch", "review", "--db", str(db), "--journal", pending.journal_id,
            "--year", str(pending.year), "--issue", pending.issue,
            "--evidence-url", pending.evidence_url, "--label-month", "2026-08"]
    with pytest.raises(ValueError, match="confirm-evidence"):
        parser.parse_args(base).handler(parser.parse_args(base))
    assert list_issues(db)[0].status == "pending"

    args = parser.parse_args(["journal-watch", "pending", "--db", str(db)])
    assert args.handler(args) == 0
    assert '"status": "pending"' in capsys.readouterr().out

    reviewed_args = parser.parse_args([*base, "--confirm-evidence"])
    assert reviewed_args.handler(reviewed_args) == 0
    assert '"status": "reviewed"' in capsys.readouterr().out
    assert list_issues(db)[0].label_month == "2026-08"


def test_reviewed_observe_requires_explicit_source_confirmation(tmp_path: Path, capsys) -> None:
    db = tmp_path / "issues.sqlite3"
    parser = cli.build_parser()
    base = [
        "journal-watch", "observe", "--db", str(db),
        "--journal", "zhexue-dongtai", "--year", "2026", "--issue", "9",
        "--evidence-url", "https://mp.weixin.qq.com/s/eeYI8s6Y_f9J3zbJ73XgSg",
        "--evidence-kind", "official-wechat", "--announcement-on", "2026-09-27",
        "--status", "reviewed",
    ]
    args = parser.parse_args(base)
    with pytest.raises(ValueError, match="confirm-evidence"):
        args.handler(args)
    assert not db.exists()

    confirmed = parser.parse_args([*base, "--confirm-evidence"])
    assert confirmed.handler(confirmed) == 0
    assert '"status": "reviewed"' in capsys.readouterr().out
    assert list_issues(db)[0].announcement_on == "2026-09-27"


def test_weekly_uses_first_review_date_and_does_not_repeat_monthly_issue(tmp_path: Path) -> None:
    db = tmp_path / "official-journals.sqlite3"
    pending = new_observation(
        "ziran-bianzhengfa-tongxun",
        2026,
        "9",
        "https://jdn.ucas.ac.cn/home/column/lists/cid/349",
        "journal-site",
    )
    record_issue(db, replace(pending, observed_at="2026-09-01T08:00:00+08:00"))
    reviewed = replace(
        pending,
        label_month="2026-09",
        status="reviewed",
        reviewed_at="2026-09-27T08:00:00+08:00",
    )
    record_issue(db, reviewed)
    record_issue(db, replace(reviewed, reviewed_at="2026-09-29T08:00:00+08:00"))
    second_source = replace(
        reviewed,
        evidence_url="https://jdn.ucas.ac.cn/home/journal/cataloglist/cid/349?issue=9",
        reviewed_at="2026-09-29T08:00:00+08:00",
    )
    record_issue(db, second_source)
    search_issues, search_status = cli._search_official_issues(db, 2026, 2026)
    assert search_status == "local_only"
    assert len(search_issues) == 1
    earliest = next(item for item in list_issues(db) if item.evidence_url == pending.evidence_url)
    assert earliest.observed_at == "2026-09-01T08:00:00+08:00"
    assert earliest.reviewed_at == "2026-09-27T08:00:00+08:00"
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "state.sqlite3"),
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    first_week = _official_issues_first_reviewed_in_window(
        config,
        datetime(2026, 9, 21, tzinfo=UTC),
        datetime(2026, 9, 28, tzinfo=UTC),
    )
    later_week = _official_issues_first_reviewed_in_window(
        config,
        datetime(2026, 9, 28, tzinfo=UTC),
        datetime(2026, 10, 5, tzinfo=UTC),
    )
    assert len(first_week) == 1
    assert later_week == ()


@pytest.mark.parametrize("dates", [
    {"announcement_on": "2026-09-25"},
    {"issue_published_on": "2026-09-15"},
    {"label_month": "2026-08", "announcement_on": "2026-09-25"},
])
def test_first_reviewed_issue_is_visible_without_publication_month_gate(tmp_path, dates):
    observed = new_observation(
        "ziran-bianzhengfa-tongxun", 2026, "9",
        "https://jdn.ucas.ac.cn/home/column/lists/cid/349", "journal-site",
    )
    reviewed = replace(observed, status="reviewed",
                       reviewed_at="2026-09-25T08:00:00+08:00", **dates)
    record_issue(tmp_path / "official-journals.sqlite3", reviewed)
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "state.sqlite3"),
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    selected = _official_issues_first_reviewed_in_window(
        config, datetime(2026, 9, 21, tzinfo=UTC), datetime(2026, 9, 28, tzinfo=UTC),
    )
    assert len(selected) == 1
    assert selected[0].announcement_on == dates.get("announcement_on")
    assert _official_issues_first_reviewed_in_window(
        config, datetime(2026, 9, 28, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC),
    ) == ()


def test_september_wechat_issue_first_reviewed_in_october_keeps_its_dates(tmp_path):
    issue = replace(
        new_observation(
            "xiandai-zhexue", 2026, "5",
            "https://mp.weixin.qq.com/s/synthetic-issue-five", "official-wechat",
            label_month="2026-09", announcement_on="2026-10-09", status="reviewed",
        ),
        reviewed_at="2026-10-10T12:00:00+08:00",
    )
    record_issue(tmp_path / "official-journals.sqlite3", issue)
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "state.sqlite3"),
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    selected = _official_issues_first_reviewed_in_window(
        config, datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC)
    )
    assert len(selected) == 1
    assert selected[0].label_month == "2026-09"
    assert selected[0].announcement_on == "2026-10-09"
    assert _official_issues_first_reviewed_in_window(
        config, datetime(2026, 9, 28, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)
    ) == ()
