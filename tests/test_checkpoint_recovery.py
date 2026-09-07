import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

import philosophy_frontier_monitor.state as state_module
from philosophy_frontier_monitor.models import Notification
from philosophy_frontier_monitor.state import (
    FeedStateUpdate,
    InterestProfileSnapshot,
    ProcessingOutcome,
    SourceObservation,
    StateStore,
    StoredProfileCategory,
    StoredProfileFeed,
    UnresolvedUpdate,
)

START = datetime(2026, 8, 31, tzinfo=UTC)
END = datetime(2026, 9, 7, tzinfo=UTC)
NEXT_END = datetime(2026, 9, 14, tzinfo=UTC)


def observation(source_id="https://philpapers.org/rec/ONE", *, observed_at=END):
    return SourceObservation(
        source="philpapers-rss",
        source_id=source_id,
        display_title="Author, Alice: A Paper",
        stable_url=source_id,
        raw_hash="sha256:record",
        observed_at=observed_at,
        category_ids=frozenset({"74924"}),
    )


def feed(*, checked_at=END, content_hash="sha256:feed-one"):
    return FeedStateUpdate(
        feed_key="philpapers-rss:74924",
        category_id="74924",
        category_url="https://philpapers.org/browse/plato-theaetetus",
        entry_count=1,
        content_hash=content_hash,
        checked_at=checked_at,
    )


def profile_snapshot(
    *,
    version: int = 1,
    effective_from: datetime = START,
    category_name: str = "Plato: Theaetetus",
) -> InterestProfileSnapshot:
    return InterestProfileSnapshot(
        profile_id="pfm:interest:test",
        version=version,
        effective_from=effective_from,
        taxonomy_snapshot_id="taxonomy:test",
        confirmed_categories=(
            StoredProfileCategory(
                category_id="74924",
                category_name=category_name,
                include_descendants=False,
            ),
        ),
        expanded_category_ids=frozenset({"74924"}),
        excluded_category_ids=frozenset(),
        feeds=(
            StoredProfileFeed(
                category_id="74924",
                category_name=category_name,
                url="https://philpapers.org/browse/plato-theaetetus",
            ),
        ),
        inference_mode="adaptive",
        timezone="Asia/Shanghai",
        report_weekday=0,
        schedule_local_time="08:00:00",
    )


def test_completed_checkpoint_round_trips():
    with StateStore(":memory:") as store:
        schema_version = store.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        store.save_completed_checkpoint("philpapers:test", cursor="page-9", window_end=END)
        checkpoint = store.get_checkpoint("philpapers:test")

    assert schema_version == "4"
    assert checkpoint is not None
    assert checkpoint.cursor == "page-9"
    assert checkpoint.window_end == END


def test_schema_two_run_table_is_migrated_without_rebuilding_database(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            run_kind TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            report_path TEXT,
            stats_json TEXT NOT NULL
        );
        """
    )
    monkeypatch.setattr(state_module.sqlite3, "connect", lambda _path: connection)

    with StateStore(":memory:") as store:
        columns = {
            row["name"] for row in store.connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        schema_version = store.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()["value"]

    assert {
        "taxonomy_snapshot_id",
        "interest_profile_id",
        "interest_profile_version",
        "pipeline_version",
        "matching_rule_version",
    }.issubset(columns)
    assert schema_version == "4"


def test_file_schema_upgrade_creates_verified_backup():
    database = Path("var") / f"test-state-upgrade-{uuid.uuid4().hex}.sqlite3"
    backup = None
    try:
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', '3');
            """
        )
        connection.close()

        with StateStore(database) as store:
            backup = store.last_schema_backup
            schema_version = store.connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()["value"]

        assert schema_version == "4"
        assert backup is not None and backup.is_file()
        verification = sqlite3.connect(backup)
        try:
            assert verification.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                verification.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                == "3"
            )
        finally:
            verification.close()
    finally:
        if database.exists():
            database.unlink()
        if backup is not None and backup.exists():
            backup.unlink()


def test_interest_profile_snapshots_are_private_immutable_and_time_addressable():
    first = profile_snapshot(version=1, effective_from=START)
    second = profile_snapshot(version=2, effective_from=NEXT_END)
    with StateStore(":memory:") as store:
        assert store.register_interest_profile(first, recorded_at=START) is True
        assert store.register_interest_profile(first, recorded_at=END) is False
        assert store.register_interest_profile(second, recorded_at=NEXT_END) is True

        before_second = store.interest_profile_at("pfm:interest:test", END)
        after_second = store.interest_profile_at("pfm:interest:test", NEXT_END)
        row = store.connection.execute(
            "SELECT snapshot_json, content_hash FROM interest_profiles "
            "WHERE profile_id = ? AND profile_version = ?",
            ("pfm:interest:test", 1),
        ).fetchone()

        with pytest.raises(ValueError, match="immutable"):
            store.register_interest_profile(
                profile_snapshot(
                    version=1,
                    effective_from=START,
                    category_name="Changed without a new version",
                ),
                recorded_at=NEXT_END,
            )

    assert before_second == first
    assert after_second == second
    assert row is not None
    assert row["content_hash"].startswith("sha256:")
    assert "original_text" not in row["snapshot_json"]
    assert "proposed_categories" not in row["snapshot_json"]


def test_notification_is_idempotent_by_work_and_type():
    first = Notification(
        notification_id=f"pfm:notification:{uuid.uuid4()}",
        work_id="pfm:work:one",
        match_id="pfm:match:one",
        report_window_start=START,
        report_window_end=END,
        notification_type="weekly_new_papers",
        created_at=END,
        matched_category_ids=frozenset({"74924"}),
    )
    duplicate = Notification(
        notification_id=f"pfm:notification:{uuid.uuid4()}",
        work_id="pfm:work:one",
        match_id="pfm:match:two",
        report_window_start=START,
        report_window_end=END,
        notification_type="weekly_new_papers",
        created_at=END,
        matched_category_ids=frozenset({"74810"}),
    )

    with StateStore(":memory:") as store:
        assert store.record_notification(first) is True
        assert store.record_notification(duplicate) is False
        assert store.has_notification("pfm:work:one", "weekly_new_papers") is True


def test_baseline_marks_existing_feed_entries_as_known_history():
    with StateStore(":memory:") as store:
        store.establish_baseline(feeds=(feed(),), observations=(observation(),))

        assert store.is_feed_baselined("philpapers-rss:74924") is True
        assert store.missing_feed_baselines({"philpapers-rss:74924"}) == frozenset()
        assert store.known_source_ids(
            "philpapers-rss", {"https://philpapers.org/rec/ONE"}
        ) == frozenset({"https://philpapers.org/rec/ONE"})
        assert not store.get_retryable_unresolved(as_of=NEXT_END, max_attempts=8)


def test_unresolved_record_is_preserved_for_a_later_retry():
    unresolved = UnresolvedUpdate(
        source="philpapers-rss",
        source_id="https://philpapers.org/rec/TWO",
        reason_code="bibliographic_match_not_found",
        detail="No exact Crossref or OpenAlex match.",
        attempted_at=END,
        next_retry_at=NEXT_END,
    )
    outcome = ProcessingOutcome(
        source="philpapers-rss",
        source_id="https://philpapers.org/rec/TWO",
        status="unresolved",
        work_id=None,
    )
    with StateStore(":memory:") as store:
        store.establish_baseline(feeds=(feed(),), observations=(observation(),))
        store.commit_weekly_run(
            run_id="pfm:run:unresolved",
            window_start=START,
            window_end=END,
            started_at=END,
            completed_at=END,
            report_path="report.md",
            stats={"unresolved": 1},
            taxonomy_snapshot_id="taxonomy:test",
            interest_profile_id="interest:test",
            interest_profile_version=1,
            pipeline_version="0.1.0",
            matching_rule_version="set_intersection_v1",
            feeds=(feed(),),
            observations=(observation("https://philpapers.org/rec/TWO", observed_at=END),),
            outcomes=(outcome,),
            unresolved=(unresolved,),
            notifications=(),
        )

        assert not store.get_retryable_unresolved(as_of=END, max_attempts=8)
        retryable = store.get_retryable_unresolved(as_of=NEXT_END, max_attempts=8)

    assert len(retryable) == 1
    assert retryable[0].source_id == "https://philpapers.org/rec/TWO"
    assert retryable[0].attempts == 1


def test_final_commit_rolls_back_records_and_checkpoint_together():
    with StateStore(":memory:") as store:
        store.establish_baseline(feeds=(feed(),), observations=(observation(),))
        store.commit_weekly_run(
            run_id="pfm:run:duplicate",
            window_start=START,
            window_end=END,
            started_at=END,
            completed_at=END,
            report_path="first.md",
            stats={},
            taxonomy_snapshot_id="taxonomy:test",
            interest_profile_id="interest:test",
            interest_profile_version=1,
            pipeline_version="0.1.0",
            matching_rule_version="set_intersection_v1",
            feeds=(feed(),),
            observations=(),
            outcomes=(),
            unresolved=(),
            notifications=(),
        )
        original_checkpoint = store.get_checkpoint("philpapers-rss:74924")

        with pytest.raises(sqlite3.IntegrityError):
            store.commit_weekly_run(
                run_id="pfm:run:duplicate",
                window_start=END,
                window_end=NEXT_END,
                started_at=NEXT_END,
                completed_at=NEXT_END,
                report_path="second.md",
                stats={},
                taxonomy_snapshot_id="taxonomy:test",
                interest_profile_id="interest:test",
                interest_profile_version=1,
                pipeline_version="0.1.0",
                matching_rule_version="set_intersection_v1",
                feeds=(feed(checked_at=NEXT_END, content_hash="sha256:feed-two"),),
                observations=(
                    observation("https://philpapers.org/rec/THREE", observed_at=NEXT_END),
                ),
                outcomes=(),
                unresolved=(),
                notifications=(),
            )

        rolled_back_checkpoint = store.get_checkpoint("philpapers-rss:74924")
        known = store.known_source_ids("philpapers-rss", {"https://philpapers.org/rec/THREE"})

    assert rolled_back_checkpoint == original_checkpoint
    assert known == frozenset()
