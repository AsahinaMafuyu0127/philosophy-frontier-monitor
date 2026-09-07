"""SQLite persistence for checkpoints and notification idempotency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Notification

SCHEMA_VERSION = 4


@dataclass(frozen=True, slots=True)
class Checkpoint:
    source: str
    cursor: str | None
    window_end: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class FeedStateUpdate:
    feed_key: str
    category_id: str
    category_url: str
    entry_count: int
    content_hash: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class SourceObservation:
    source: str
    source_id: str
    display_title: str
    stable_url: str
    raw_hash: str
    observed_at: datetime
    category_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    source: str
    source_id: str
    status: str
    work_id: str | None


@dataclass(frozen=True, slots=True)
class UnresolvedUpdate:
    source: str
    source_id: str
    reason_code: str
    detail: str
    attempted_at: datetime
    next_retry_at: datetime


@dataclass(frozen=True, slots=True)
class RetryableRecord:
    source: str
    source_id: str
    display_title: str
    stable_url: str
    category_ids: frozenset[str]
    attempts: int
    reason_code: str


@dataclass(frozen=True, slots=True)
class StoredProfileCategory:
    category_id: str
    category_name: str
    include_descendants: bool


@dataclass(frozen=True, slots=True)
class StoredProfileFeed:
    category_id: str
    category_name: str
    url: str


@dataclass(frozen=True, slots=True)
class InterestProfileSnapshot:
    """Minimal immutable state needed to reproduce an active matching profile.

    The user's free-text research description and unconfirmed proposals are
    intentionally excluded. They remain in the private watchlist rather than
    being copied into the operational state database.
    """

    profile_id: str
    version: int
    effective_from: datetime
    taxonomy_snapshot_id: str
    confirmed_categories: tuple[StoredProfileCategory, ...]
    expanded_category_ids: frozenset[str]
    excluded_category_ids: frozenset[str]
    feeds: tuple[StoredProfileFeed, ...]
    inference_mode: str
    timezone: str
    report_weekday: int
    schedule_local_time: str


class StateStore:
    """Small local state store; private research text is not stored here."""

    def __init__(self, path: str | Path) -> None:
        self.last_schema_backup: Path | None = None
        if str(path) == ":memory:":
            self.path: Path | None = None
            self.connection = sqlite3.connect(":memory:")
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._backup_before_schema_upgrade()
        self._initialize()

    def _backup_before_schema_upgrade(self) -> None:
        """Create and verify a recoverable copy before changing an existing schema."""

        if self.path is None or not self.path.is_file() or self.path.stat().st_size == 0:
            return
        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if table is None:
            return
        row = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return
        try:
            existing_version = int(row[0])
        except (TypeError, ValueError) as error:
            raise RuntimeError("state database has an invalid schema version") from error
        if existing_version >= SCHEMA_VERSION:
            return

        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_directory / (
            f"{self.path.stem}-pre-schema{SCHEMA_VERSION}-{timestamp}{self.path.suffix}"
        )
        backup_connection = sqlite3.connect(backup_path)
        try:
            self.connection.backup(backup_connection)
        finally:
            backup_connection.close()
        verification = sqlite3.connect(backup_path)
        try:
            result = verification.execute("PRAGMA integrity_check").fetchone()
        finally:
            verification.close()
        if result is None or result[0] != "ok":
            raise RuntimeError("state database backup failed integrity verification")
        self.last_schema_backup = backup_path.resolve()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    source TEXT PRIMARY KEY,
                    cursor TEXT,
                    window_end TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    work_id TEXT NOT NULL,
                    match_id TEXT NOT NULL,
                    report_window_start TEXT NOT NULL,
                    report_window_end TEXT NOT NULL,
                    notification_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    matched_category_ids TEXT NOT NULL,
                    UNIQUE(work_id, notification_type)
                );
                CREATE TABLE IF NOT EXISTS feed_subscriptions (
                    feed_key TEXT PRIMARY KEY,
                    category_id TEXT NOT NULL,
                    category_url TEXT NOT NULL,
                    baseline_completed_at TEXT NOT NULL,
                    last_successful_scan_at TEXT NOT NULL,
                    last_entry_count INTEGER NOT NULL,
                    last_content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_records (
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    display_title TEXT NOT NULL,
                    stable_url TEXT NOT NULL,
                    raw_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    processing_status TEXT NOT NULL,
                    work_id TEXT,
                    last_processed_at TEXT,
                    PRIMARY KEY(source, source_id)
                );
                CREATE TABLE IF NOT EXISTS source_record_categories (
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    category_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY(source, source_id, category_id),
                    FOREIGN KEY(source, source_id)
                        REFERENCES source_records(source, source_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS unresolved_records (
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_attempted_at TEXT NOT NULL,
                    next_retry_at TEXT NOT NULL,
                    PRIMARY KEY(source, source_id),
                    FOREIGN KEY(source, source_id)
                        REFERENCES source_records(source, source_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    run_kind TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_path TEXT,
                    stats_json TEXT NOT NULL,
                    taxonomy_snapshot_id TEXT NOT NULL,
                    interest_profile_id TEXT NOT NULL,
                    interest_profile_version INTEGER NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    matching_rule_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS interest_profiles (
                    profile_id TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    effective_from TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    taxonomy_snapshot_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY(profile_id, profile_version),
                    UNIQUE(profile_id, effective_from)
                );
                CREATE INDEX IF NOT EXISTS interest_profiles_by_effective_time
                ON interest_profiles(profile_id, effective_from, profile_version);
                CREATE UNIQUE INDEX IF NOT EXISTS one_successful_weekly_run
                ON runs(run_kind, window_start, window_end)
                WHERE status = 'success';
                """
            )
            self._add_column_if_missing(
                "runs",
                "taxonomy_snapshot_id",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._add_column_if_missing(
                "runs",
                "interest_profile_id",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._add_column_if_missing(
                "runs",
                "interest_profile_version",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._add_column_if_missing(
                "runs",
                "pipeline_version",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self._add_column_if_missing(
                "runs",
                "matching_rule_version",
                "TEXT NOT NULL DEFAULT 'unknown'",
            )
            self.connection.execute(
                """
                INSERT INTO schema_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("schema_version", str(SCHEMA_VERSION)),
            )

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        """Apply the small additive migration needed by pre-release schemas."""

        columns = {
            row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Create one explicit all-or-nothing SQLite transaction."""

        if self.connection.in_transaction:
            raise RuntimeError("nested StateStore transactions are not supported")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get_checkpoint(self, source: str) -> Checkpoint | None:
        row = self.connection.execute(
            "SELECT source, cursor, window_end, completed_at FROM checkpoints WHERE source = ?",
            (source,),
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            source=row["source"],
            cursor=row["cursor"],
            window_end=datetime.fromisoformat(row["window_end"]),
            completed_at=datetime.fromisoformat(row["completed_at"]),
        )

    def has_successful_run(
        self,
        *,
        run_kind: str,
        window_start: datetime,
        window_end: datetime,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM runs
            WHERE run_kind = ? AND window_start = ? AND window_end = ?
              AND status = 'success'
            """,
            (run_kind, window_start.isoformat(), window_end.isoformat()),
        ).fetchone()
        return row is not None

    def successful_run_windows(
        self,
        *,
        run_kind: str,
    ) -> tuple[tuple[datetime, datetime], ...]:
        """Return committed windows in chronological order."""

        rows = self.connection.execute(
            """
            SELECT window_start, window_end
            FROM runs
            WHERE run_kind = ? AND status = 'success'
            ORDER BY window_start, window_end
            """,
            (run_kind,),
        ).fetchall()
        return tuple(
            (datetime.fromisoformat(row["window_start"]), datetime.fromisoformat(row["window_end"]))
            for row in rows
        )

    def latest_successful_profile_version(self, *, run_kind: str) -> int | None:
        row = self.connection.execute(
            """
            SELECT interest_profile_version
            FROM runs
            WHERE run_kind = ? AND status = 'success'
            ORDER BY window_end DESC, completed_at DESC
            LIMIT 1
            """,
            (run_kind,),
        ).fetchone()
        return None if row is None else int(row["interest_profile_version"])

    @staticmethod
    def _interest_profile_document(snapshot: InterestProfileSnapshot) -> dict[str, object]:
        if not snapshot.profile_id.strip():
            raise ValueError("interest profile id must not be empty")
        if snapshot.version < 1:
            raise ValueError("interest profile version must be positive")
        if snapshot.effective_from.tzinfo is None:
            raise ValueError("interest profile effective_from must be timezone-aware")
        if not snapshot.taxonomy_snapshot_id.strip():
            raise ValueError("taxonomy snapshot id must not be empty")
        if snapshot.report_weekday not in range(7):
            raise ValueError("report weekday must be between 0 and 6")

        confirmed_ids = [item.category_id for item in snapshot.confirmed_categories]
        feed_ids = [item.category_id for item in snapshot.feeds]
        if len(confirmed_ids) != len(set(confirmed_ids)):
            raise ValueError("interest profile contains duplicate confirmed categories")
        if len(feed_ids) != len(set(feed_ids)):
            raise ValueError("interest profile contains duplicate feeds")
        if set(feed_ids) != set(snapshot.expanded_category_ids):
            raise ValueError("interest profile feeds must equal the expanded category set")
        if not set(confirmed_ids).issubset(snapshot.expanded_category_ids):
            raise ValueError("confirmed categories must be present in the expanded category set")
        if snapshot.excluded_category_ids.intersection(snapshot.expanded_category_ids):
            raise ValueError("excluded categories cannot also be active feed categories")

        return {
            "profile_id": snapshot.profile_id,
            "profile_version": snapshot.version,
            "effective_from": snapshot.effective_from.astimezone(UTC).isoformat(),
            "taxonomy_snapshot_id": snapshot.taxonomy_snapshot_id,
            "confirmed_categories": [
                {
                    "category_id": item.category_id,
                    "category_name": item.category_name,
                    "include_descendants": item.include_descendants,
                }
                for item in snapshot.confirmed_categories
            ],
            "expanded_category_ids": sorted(snapshot.expanded_category_ids),
            "excluded_category_ids": sorted(snapshot.excluded_category_ids),
            "feeds": [
                {
                    "category_id": item.category_id,
                    "category_name": item.category_name,
                    "url": item.url,
                }
                for item in snapshot.feeds
            ],
            "inference_mode": snapshot.inference_mode,
            "timezone": snapshot.timezone,
            "report_weekday": snapshot.report_weekday,
            "schedule_local_time": snapshot.schedule_local_time,
        }

    @classmethod
    def _serialize_interest_profile(cls, snapshot: InterestProfileSnapshot) -> tuple[str, str]:
        document = cls._interest_profile_document(snapshot)
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_hash = "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return serialized, content_hash

    @staticmethod
    def _deserialize_interest_profile(snapshot_json: str) -> InterestProfileSnapshot:
        payload = json.loads(snapshot_json)
        return InterestProfileSnapshot(
            profile_id=payload["profile_id"],
            version=int(payload["profile_version"]),
            effective_from=datetime.fromisoformat(payload["effective_from"]),
            taxonomy_snapshot_id=payload["taxonomy_snapshot_id"],
            confirmed_categories=tuple(
                StoredProfileCategory(
                    category_id=item["category_id"],
                    category_name=item["category_name"],
                    include_descendants=bool(item["include_descendants"]),
                )
                for item in payload["confirmed_categories"]
            ),
            expanded_category_ids=frozenset(payload["expanded_category_ids"]),
            excluded_category_ids=frozenset(payload["excluded_category_ids"]),
            feeds=tuple(
                StoredProfileFeed(
                    category_id=item["category_id"],
                    category_name=item["category_name"],
                    url=item["url"],
                )
                for item in payload["feeds"]
            ),
            inference_mode=payload["inference_mode"],
            timezone=payload["timezone"],
            report_weekday=int(payload["report_weekday"]),
            schedule_local_time=payload["schedule_local_time"],
        )

    def _register_interest_profile(
        self,
        snapshot: InterestProfileSnapshot,
        *,
        recorded_at: datetime,
    ) -> bool:
        if recorded_at.tzinfo is None:
            raise ValueError("interest profile recorded_at must be timezone-aware")
        serialized, content_hash = self._serialize_interest_profile(snapshot)
        try:
            self.connection.execute(
                """
                INSERT INTO interest_profiles(
                    profile_id, profile_version, effective_from, recorded_at,
                    taxonomy_snapshot_id, snapshot_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.profile_id,
                    snapshot.version,
                    snapshot.effective_from.astimezone(UTC).isoformat(),
                    recorded_at.astimezone(UTC).isoformat(),
                    snapshot.taxonomy_snapshot_id,
                    serialized,
                    content_hash,
                ),
            )
        except sqlite3.IntegrityError as error:
            row = self.connection.execute(
                """
                SELECT content_hash FROM interest_profiles
                WHERE profile_id = ? AND profile_version = ?
                """,
                (snapshot.profile_id, snapshot.version),
            ).fetchone()
            if row is not None and row["content_hash"] == content_hash:
                return False
            raise ValueError(
                "interest profile versions are immutable; increment the version "
                "when active categories, feeds, taxonomy, or schedule semantics change"
            ) from error
        return True

    def register_interest_profile(
        self,
        snapshot: InterestProfileSnapshot,
        *,
        recorded_at: datetime | None = None,
    ) -> bool:
        """Persist one immutable runtime profile; return False if already identical."""

        timestamp = recorded_at or datetime.now(UTC)
        with self.transaction():
            return self._register_interest_profile(snapshot, recorded_at=timestamp)

    def get_interest_profile(
        self,
        profile_id: str,
        version: int,
    ) -> InterestProfileSnapshot | None:
        row = self.connection.execute(
            """
            SELECT snapshot_json FROM interest_profiles
            WHERE profile_id = ? AND profile_version = ?
            """,
            (profile_id, version),
        ).fetchone()
        if row is None:
            return None
        return self._deserialize_interest_profile(row["snapshot_json"])

    def interest_profile_at(
        self,
        profile_id: str,
        instant: datetime,
    ) -> InterestProfileSnapshot | None:
        """Return the last profile already effective at a scheduled delivery instant."""

        if instant.tzinfo is None:
            raise ValueError("profile lookup instant must be timezone-aware")
        row = self.connection.execute(
            """
            SELECT snapshot_json FROM interest_profiles
            WHERE profile_id = ? AND effective_from <= ?
            ORDER BY effective_from DESC, profile_version DESC
            LIMIT 1
            """,
            (profile_id, instant.astimezone(UTC).isoformat()),
        ).fetchone()
        if row is None:
            return None
        return self._deserialize_interest_profile(row["snapshot_json"])

    def save_completed_checkpoint(
        self,
        source: str,
        *,
        cursor: str | None,
        window_end: datetime,
        completed_at: datetime | None = None,
    ) -> None:
        """Advance a checkpoint only after a caller completed all pages."""

        if window_end.tzinfo is None:
            raise ValueError("window_end must be timezone-aware")
        finished = completed_at or datetime.now(UTC)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO checkpoints(source, cursor, window_end, completed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    cursor = excluded.cursor,
                    window_end = excluded.window_end,
                    completed_at = excluded.completed_at
                """,
                (source, cursor, window_end.isoformat(), finished.isoformat()),
            )

    def has_notification(self, work_id: str, notification_type: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM notifications WHERE work_id = ? AND notification_type = ?",
            (work_id, notification_type),
        ).fetchone()
        return row is not None

    def record_notification(self, notification: Notification) -> bool:
        """Insert once; return False when this work/type was already notified."""

        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO notifications(
                        notification_id, work_id, match_id, report_window_start,
                        report_window_end, notification_type, created_at,
                        matched_category_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notification.notification_id,
                        notification.work_id,
                        notification.match_id,
                        notification.report_window_start.isoformat(),
                        notification.report_window_end.isoformat(),
                        notification.notification_type,
                        notification.created_at.isoformat(),
                        json.dumps(sorted(notification.matched_category_ids)),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def is_feed_baselined(self, feed_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM feed_subscriptions WHERE feed_key = ?",
            (feed_key,),
        ).fetchone()
        return row is not None

    def missing_feed_baselines(self, feed_keys: set[str]) -> frozenset[str]:
        if not feed_keys:
            return frozenset()
        placeholders = ",".join("?" for _ in feed_keys)
        rows = self.connection.execute(
            f"SELECT feed_key FROM feed_subscriptions WHERE feed_key IN ({placeholders})",
            tuple(sorted(feed_keys)),
        ).fetchall()
        present = {row["feed_key"] for row in rows}
        return frozenset(feed_keys.difference(present))

    def known_source_ids(self, source: str, source_ids: set[str]) -> frozenset[str]:
        if not source_ids:
            return frozenset()
        found: set[str] = set()
        ordered_ids = sorted(source_ids)
        # Stay well below SQLite's host-parameter limit.
        for offset in range(0, len(ordered_ids), 400):
            batch = ordered_ids[offset : offset + 400]
            placeholders = ",".join("?" for _ in batch)
            rows = self.connection.execute(
                f"""
                SELECT source_id FROM source_records
                WHERE source = ? AND source_id IN ({placeholders})
                """,
                (source, *batch),
            ).fetchall()
            found.update(row["source_id"] for row in rows)
        return frozenset(found)

    def get_retryable_unresolved(
        self,
        *,
        as_of: datetime,
        max_attempts: int,
    ) -> tuple[RetryableRecord, ...]:
        rows = self.connection.execute(
            """
            SELECT sr.source, sr.source_id, sr.display_title, sr.stable_url,
                   ur.attempts, ur.reason_code
            FROM unresolved_records ur
            JOIN source_records sr
              ON sr.source = ur.source AND sr.source_id = ur.source_id
            WHERE ur.attempts < ? AND ur.next_retry_at <= ?
            ORDER BY sr.source, sr.source_id
            """,
            (max_attempts, as_of.isoformat()),
        ).fetchall()
        result = []
        for row in rows:
            category_rows = self.connection.execute(
                """
                SELECT category_id FROM source_record_categories
                WHERE source = ? AND source_id = ?
                ORDER BY category_id
                """,
                (row["source"], row["source_id"]),
            ).fetchall()
            result.append(
                RetryableRecord(
                    source=row["source"],
                    source_id=row["source_id"],
                    display_title=row["display_title"],
                    stable_url=row["stable_url"],
                    category_ids=frozenset(item["category_id"] for item in category_rows),
                    attempts=row["attempts"],
                    reason_code=row["reason_code"],
                )
            )
        return tuple(result)

    def _upsert_observation(
        self,
        observation: SourceObservation,
        *,
        initial_status: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO source_records(
                source, source_id, display_title, stable_url, raw_hash,
                first_seen_at, last_seen_at, processing_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                display_title = excluded.display_title,
                stable_url = excluded.stable_url,
                raw_hash = excluded.raw_hash,
                last_seen_at = excluded.last_seen_at
            """,
            (
                observation.source,
                observation.source_id,
                observation.display_title,
                observation.stable_url,
                observation.raw_hash,
                observation.observed_at.isoformat(),
                observation.observed_at.isoformat(),
                initial_status,
            ),
        )
        for category_id in sorted(observation.category_ids):
            self.connection.execute(
                """
                INSERT OR IGNORE INTO source_record_categories(
                    source, source_id, category_id, first_seen_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    observation.source,
                    observation.source_id,
                    category_id,
                    observation.observed_at.isoformat(),
                ),
            )

    def establish_baseline(
        self,
        *,
        feeds: tuple[FeedStateUpdate, ...],
        observations: tuple[SourceObservation, ...],
        interest_profile_snapshot: InterestProfileSnapshot | None = None,
    ) -> None:
        """Atomically mark all current feed records as pre-existing history."""

        if not feeds:
            raise ValueError("at least one feed is required for a baseline")
        with self.transaction():
            if interest_profile_snapshot is not None:
                self._register_interest_profile(
                    interest_profile_snapshot,
                    recorded_at=max(feed.checked_at for feed in feeds),
                )
            for observation in observations:
                self._upsert_observation(observation, initial_status="baseline")
            for feed in feeds:
                self.connection.execute(
                    """
                    INSERT INTO feed_subscriptions(
                        feed_key, category_id, category_url,
                        baseline_completed_at, last_successful_scan_at,
                        last_entry_count, last_content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(feed_key) DO UPDATE SET
                        category_id = excluded.category_id,
                        category_url = excluded.category_url,
                        last_successful_scan_at = excluded.last_successful_scan_at,
                        last_entry_count = excluded.last_entry_count,
                        last_content_hash = excluded.last_content_hash
                    """,
                    (
                        feed.feed_key,
                        feed.category_id,
                        feed.category_url,
                        feed.checked_at.isoformat(),
                        feed.checked_at.isoformat(),
                        feed.entry_count,
                        feed.content_hash,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO checkpoints(source, cursor, window_end, completed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        cursor = excluded.cursor,
                        window_end = excluded.window_end,
                        completed_at = excluded.completed_at
                    """,
                    (
                        feed.feed_key,
                        feed.content_hash,
                        feed.checked_at.isoformat(),
                        feed.checked_at.isoformat(),
                    ),
                )

    def commit_weekly_run(
        self,
        *,
        run_id: str,
        window_start: datetime,
        window_end: datetime,
        started_at: datetime,
        completed_at: datetime,
        report_path: str,
        stats: dict[str, int],
        taxonomy_snapshot_id: str,
        interest_profile_id: str,
        interest_profile_version: int,
        pipeline_version: str,
        matching_rule_version: str,
        feeds: tuple[FeedStateUpdate, ...],
        observations: tuple[SourceObservation, ...],
        outcomes: tuple[ProcessingOutcome, ...],
        unresolved: tuple[UnresolvedUpdate, ...],
        notifications: tuple[Notification, ...],
        interest_profile_snapshot: InterestProfileSnapshot | None = None,
    ) -> None:
        """Commit a completed report and every associated state change together."""

        unresolved_keys = {(item.source, item.source_id) for item in unresolved}
        with self.transaction():
            if interest_profile_snapshot is not None:
                self._register_interest_profile(
                    interest_profile_snapshot,
                    recorded_at=completed_at,
                )
            for observation in observations:
                self._upsert_observation(observation, initial_status="pending")

            for outcome in outcomes:
                self.connection.execute(
                    """
                    UPDATE source_records
                    SET processing_status = ?, work_id = ?, last_processed_at = ?
                    WHERE source = ? AND source_id = ?
                    """,
                    (
                        outcome.status,
                        outcome.work_id,
                        completed_at.isoformat(),
                        outcome.source,
                        outcome.source_id,
                    ),
                )
                if (outcome.source, outcome.source_id) not in unresolved_keys:
                    self.connection.execute(
                        "DELETE FROM unresolved_records WHERE source = ? AND source_id = ?",
                        (outcome.source, outcome.source_id),
                    )

            for item in unresolved:
                self.connection.execute(
                    """
                    INSERT INTO unresolved_records(
                        source, source_id, reason_code, detail, attempts,
                        last_attempted_at, next_retry_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(source, source_id) DO UPDATE SET
                        reason_code = excluded.reason_code,
                        detail = excluded.detail,
                        attempts = unresolved_records.attempts + 1,
                        last_attempted_at = excluded.last_attempted_at,
                        next_retry_at = excluded.next_retry_at
                    """,
                    (
                        item.source,
                        item.source_id,
                        item.reason_code,
                        item.detail,
                        item.attempted_at.isoformat(),
                        item.next_retry_at.isoformat(),
                    ),
                )

            for notification in notifications:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO notifications(
                        notification_id, work_id, match_id, report_window_start,
                        report_window_end, notification_type, created_at,
                        matched_category_ids
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        notification.notification_id,
                        notification.work_id,
                        notification.match_id,
                        notification.report_window_start.isoformat(),
                        notification.report_window_end.isoformat(),
                        notification.notification_type,
                        notification.created_at.isoformat(),
                        json.dumps(sorted(notification.matched_category_ids)),
                    ),
                )

            for feed in feeds:
                self.connection.execute(
                    """
                    UPDATE feed_subscriptions
                    SET last_successful_scan_at = ?, last_entry_count = ?,
                        last_content_hash = ?
                    WHERE feed_key = ?
                    """,
                    (
                        feed.checked_at.isoformat(),
                        feed.entry_count,
                        feed.content_hash,
                        feed.feed_key,
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO checkpoints(source, cursor, window_end, completed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source) DO UPDATE SET
                        cursor = excluded.cursor,
                        window_end = excluded.window_end,
                        completed_at = excluded.completed_at
                    """,
                    (
                        feed.feed_key,
                        feed.content_hash,
                        window_end.isoformat(),
                        completed_at.isoformat(),
                    ),
                )

            self.connection.execute(
                """
                INSERT INTO runs(
                    run_id, run_kind, window_start, window_end, started_at,
                    completed_at, status, report_path, stats_json,
                    taxonomy_snapshot_id, interest_profile_id,
                    interest_profile_version, pipeline_version,
                    matching_rule_version
                ) VALUES (?, 'weekly', ?, ?, ?, ?, 'success', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    window_start.isoformat(),
                    window_end.isoformat(),
                    started_at.isoformat(),
                    completed_at.isoformat(),
                    report_path,
                    json.dumps(stats, sort_keys=True),
                    taxonomy_snapshot_id,
                    interest_profile_id,
                    interest_profile_version,
                    pipeline_version,
                    matching_rule_version,
                ),
            )
