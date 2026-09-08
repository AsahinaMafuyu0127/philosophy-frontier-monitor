"""Private cross-run cache for complete PhilArchive OAI harvest windows.

The cache stores only the OAI identifier, source datestamp, deletion marker and
the minimal Dublin Core fields used by the monitor.  Coverage is extended only
after a complete paginated request succeeds, so a partial or failed harvest can
never masquerade as a cache hit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .sources.philarchive_oai import (
    OAIError,
    OAIPage,
    OAIProtocolError,
    OAIRecord,
    OAIWindowSnapshot,
    load_recent_window,
    oai_record_key,
    parse_oai_datestamp,
)

MINIMAL_DC_FIELDS = frozenset({"date", "identifier", "type"})

NetworkLoader = Callable[[datetime, datetime, str], OAIWindowSnapshot]
PageProgress = Callable[[int, int], None]
LEASE_STALE_AFTER = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class OAIHarvestCacheStats:
    hits: int
    cold_starts: int
    incremental_refreshes: int
    network_windows: int
    records_written: int
    resumed_sessions: int
    expired_token_restarts: int
    invalid_token_restarts: int


@dataclass(frozen=True, slots=True)
class OAIHarvestSessionInfo:
    endpoint_hash: str
    window_start: str
    window_end: str
    completed_pages: int
    harvested_records: int
    staged_records: int
    token_present: bool
    token_expiration: str | None
    cursor: int | None
    complete_list_size: int | None
    sequence_complete: bool
    active: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class OAIHarvestCacheInfo:
    path: str | None
    database_size_bytes: int
    record_events: int
    coverage_intervals: int
    incomplete_sessions: tuple[OAIHarvestSessionInfo, ...]


@dataclass(frozen=True, slots=True)
class OAIPruneResult:
    cutoff: str
    record_events_deleted: int
    coverage_intervals_deleted: int
    incomplete_sessions_deleted: int
    database_size_before: int
    database_size_after: int


class OAIHarvestCache:
    """SQLite cache of successfully completed OAI windows and minimal records."""

    def __init__(
        self,
        path: str | Path,
        *,
        network_loader: NetworkLoader | None = None,
        page_progress: PageProgress | None = None,
        oai_client: httpx.Client | None = None,
    ):
        self.path = Path(path) if path != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = str(self.path)
        else:
            database = ":memory:"
        self.connection = sqlite3.connect(database, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oai_record_events (
                endpoint_hash TEXT NOT NULL,
                identifier TEXT NOT NULL,
                source_datestamp TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                deleted INTEGER NOT NULL CHECK (deleted IN (0, 1)),
                fields_json TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (endpoint_hash, identifier, source_datestamp)
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oai_record_events_window
            ON oai_record_events (endpoint_hash, changed_at)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oai_coverage (
                endpoint_hash TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                PRIMARY KEY (endpoint_hash, window_start, window_end),
                CHECK (window_start < window_end)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oai_harvest_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_hash TEXT NOT NULL,
                metadata_prefix TEXT NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                next_token TEXT,
                token_expiration TEXT,
                cursor INTEGER,
                complete_list_size INTEGER,
                completed_pages INTEGER NOT NULL DEFAULT 0,
                harvested_records INTEGER NOT NULL DEFAULT 0,
                overlap_records_excluded INTEGER NOT NULL DEFAULT 0,
                sequence_complete INTEGER NOT NULL DEFAULT 0
                    CHECK (sequence_complete IN (0, 1)),
                lease_owner TEXT,
                lease_pid INTEGER,
                lease_updated_at TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (endpoint_hash, metadata_prefix, window_start, window_end),
                CHECK (window_start < window_end)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS oai_staged_record_events (
                session_id INTEGER NOT NULL REFERENCES oai_harvest_sessions(session_id)
                    ON DELETE CASCADE,
                identifier TEXT NOT NULL,
                source_datestamp TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                deleted INTEGER NOT NULL CHECK (deleted IN (0, 1)),
                fields_json TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (session_id, identifier, source_datestamp)
            )
            """
        )
        self.connection.commit()
        self.network_loader = network_loader
        self.page_progress = page_progress
        self.oai_client = oai_client
        self._lease_owner = uuid.uuid4().hex
        self._hits = 0
        self._cold_starts = 0
        self._incremental_refreshes = 0
        self._network_windows = 0
        self._records_written = 0
        self._resumed_sessions = 0
        self._expired_token_restarts = 0
        self._invalid_token_restarts = 0

    def __enter__(self) -> OAIHarvestCache:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE oai_harvest_sessions
                SET lease_owner = NULL, lease_pid = NULL, lease_updated_at = NULL
                WHERE lease_owner = ?
                """,
                (self._lease_owner,),
            )
        self.connection.close()

    @property
    def stats(self) -> OAIHarvestCacheStats:
        return OAIHarvestCacheStats(
            hits=self._hits,
            cold_starts=self._cold_starts,
            incremental_refreshes=self._incremental_refreshes,
            network_windows=self._network_windows,
            records_written=self._records_written,
            resumed_sessions=self._resumed_sessions,
            expired_token_restarts=self._expired_token_restarts,
            invalid_token_restarts=self._invalid_token_restarts,
        )

    def load_window(
        self,
        window_start: datetime,
        window_end: datetime,
        endpoint: str,
    ) -> OAIWindowSnapshot:
        """Return one exact window, fetching only cache gaps from the network."""

        start, end = _normalize_window(window_start, window_end)
        endpoint_hash = _endpoint_hash(endpoint)
        initial_coverage = self._coverage(endpoint_hash)
        network_snapshots: list[OAIWindowSnapshot] = []
        records_written = 0
        if self.network_loader is None:
            recovered = self._recover_interrupted_sessions(start, end, endpoint_hash, endpoint)
            network_snapshots.extend(recovered)
            records_written += sum(item.records_in_exact_window for item in recovered)
            self._network_windows += len(recovered)
            self._records_written += sum(item.records_in_exact_window for item in recovered)

        coverage_before = self._coverage(endpoint_hash)
        gaps = _missing_intervals(start, end, coverage_before)

        if not gaps:
            if network_snapshots:
                retrieval_mode = "cold_start" if not initial_coverage else "incremental_refresh"
                if retrieval_mode == "cold_start":
                    self._cold_starts += 1
                else:
                    self._incremental_refreshes += 1
            else:
                retrieval_mode = "cache_hit"
                self._hits += 1
        else:
            retrieval_mode = "cold_start" if not initial_coverage else "incremental_refresh"
            if retrieval_mode == "cold_start":
                self._cold_starts += 1
            else:
                self._incremental_refreshes += 1

            for gap_start, gap_end in gaps:
                try:
                    if self.network_loader is not None:
                        snapshot = self.network_loader(gap_start, gap_end, endpoint)
                        written = self._commit_snapshot(endpoint_hash, snapshot)
                    else:
                        snapshot = self._load_gap_resumable(
                            gap_start,
                            gap_end,
                            endpoint_hash,
                            endpoint,
                        )
                        written = snapshot.records_in_exact_window
                except OAIError:
                    raise
                except Exception as error:
                    raise OAIError(f"OAI cache refresh failed: {error}") from error
                if snapshot.window_start != gap_start or snapshot.window_end != gap_end:
                    raise OAIError("OAI cache loader returned a window different from its request")
                records_written += written
                network_snapshots.append(snapshot)
                self._network_windows += 1
                self._records_written += written

        coverage_after = self._coverage(endpoint_hash)
        covering = _covering_interval(start, end, coverage_after)
        if covering is None:
            raise OAIError("OAI cache refresh completed without covering the requested window")
        snapshot = self._snapshot_from_cache(
            endpoint_hash,
            start,
            end,
            retrieval_mode=retrieval_mode,
            network_snapshots=tuple(network_snapshots),
            records_written=records_written,
            coverage=covering,
        )
        return snapshot

    def _recover_interrupted_sessions(
        self,
        requested_start: datetime,
        requested_end: datetime,
        endpoint_hash: str,
        endpoint: str,
    ) -> tuple[OAIWindowSnapshot, ...]:
        """Finish older sessions contained by a newer rolling request."""

        rows = self.connection.execute(
            """
            SELECT session_id, window_start, window_end
            FROM oai_harvest_sessions
            WHERE endpoint_hash = ? AND metadata_prefix = 'oai_dc'
              AND window_end > ? AND window_end <= ? AND window_start < ?
            ORDER BY window_end, window_start
            """,
            (
                endpoint_hash,
                requested_start.isoformat(),
                requested_end.isoformat(),
                requested_end.isoformat(),
            ),
        ).fetchall()
        recovered: list[OAIWindowSnapshot] = []
        for row in rows:
            session_start = datetime.fromisoformat(row["window_start"])
            session_end = datetime.fromisoformat(row["window_end"])
            if _covering_interval(session_start, session_end, self._coverage(endpoint_hash)):
                with self.connection:
                    self.connection.execute(
                        "DELETE FROM oai_harvest_sessions WHERE session_id = ?",
                        (row["session_id"],),
                    )
                continue
            recovered.append(
                self._load_gap_resumable(
                    session_start,
                    session_end,
                    endpoint_hash,
                    endpoint,
                )
            )
        return tuple(recovered)

    def _load_gap_resumable(
        self,
        start: datetime,
        end: datetime,
        endpoint_hash: str,
        endpoint: str,
    ) -> OAIWindowSnapshot:
        session = self._acquire_session(endpoint_hash, start, end)
        session_id = int(session["session_id"])
        try:
            if bool(session["sequence_complete"]):
                return self._finalize_session(session_id, endpoint_hash, start, end)

            resumption_token = session["next_token"]
            expiration = (
                datetime.fromisoformat(session["token_expiration"])
                if session["token_expiration"]
                else None
            )
            if (
                resumption_token is not None
                and expiration is not None
                and expiration <= datetime.now(UTC)
            ):
                self._reset_session(session_id)
                self._expired_token_restarts += 1
                resumption_token = None
                session = self._session(session_id)
            elif resumption_token is not None:
                self._resumed_sessions += 1

            restarted_after_invalid_token = False
            while True:
                page_offset = int(session["completed_pages"])
                record_offset = int(session["harvested_records"])
                try:
                    load_recent_window(
                        start,
                        end,
                        endpoint=endpoint,
                        client=self.oai_client,
                        page_progress=self.page_progress,
                        resumption_token=resumption_token,
                        progress_page_offset=page_offset,
                        progress_record_offset=record_offset,
                        page_observer=lambda page, records, harvested, overlap: (
                            self._checkpoint_page(
                                session_id,
                                page,
                                records,
                                harvested,
                                overlap,
                            )
                        ),
                    )
                    break
                except OAIProtocolError as error:
                    if (
                        error.code != "badResumptionToken"
                        or resumption_token is None
                        or restarted_after_invalid_token
                    ):
                        raise
                    self._reset_session(session_id)
                    self._invalid_token_restarts += 1
                    restarted_after_invalid_token = True
                    resumption_token = None
                    session = self._session(session_id)
            return self._finalize_session(session_id, endpoint_hash, start, end)
        finally:
            self._release_session(session_id)

    def _acquire_session(
        self,
        endpoint_hash: str,
        start: datetime,
        end: datetime,
    ) -> sqlite3.Row:
        now = datetime.now(UTC)
        start_text = start.isoformat()
        end_text = end.isoformat()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                """
                SELECT * FROM oai_harvest_sessions
                WHERE endpoint_hash = ? AND metadata_prefix = 'oai_dc'
                  AND window_start = ? AND window_end = ?
                """,
                (endpoint_hash, start_text, end_text),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO oai_harvest_sessions (
                        endpoint_hash, metadata_prefix, window_start, window_end,
                        lease_owner, lease_pid, lease_updated_at, started_at, updated_at
                    ) VALUES (?, 'oai_dc', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        endpoint_hash,
                        start_text,
                        end_text,
                        self._lease_owner,
                        os.getpid(),
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            else:
                lease_owner = row["lease_owner"]
                lease_pid = row["lease_pid"]
                lease_updated = (
                    datetime.fromisoformat(row["lease_updated_at"])
                    if row["lease_updated_at"]
                    else None
                )
                lease_is_fresh = (
                    lease_updated is not None and now - lease_updated < LEASE_STALE_AFTER
                )
                if (
                    lease_owner
                    and lease_owner != self._lease_owner
                    and lease_is_fresh
                    and lease_pid is not None
                    and _process_is_alive(int(lease_pid))
                ):
                    raise OAIError("another process is already harvesting this OAI cache gap")
                self.connection.execute(
                    """
                    UPDATE oai_harvest_sessions
                    SET lease_owner = ?, lease_pid = ?, lease_updated_at = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        self._lease_owner,
                        os.getpid(),
                        now.isoformat(),
                        now.isoformat(),
                        row["session_id"],
                    ),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return self.connection.execute(
            """
            SELECT * FROM oai_harvest_sessions
            WHERE endpoint_hash = ? AND metadata_prefix = 'oai_dc'
              AND window_start = ? AND window_end = ?
            """,
            (endpoint_hash, start_text, end_text),
        ).fetchone()

    def _session(self, session_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM oai_harvest_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise OAIError("OAI harvest session disappeared before completion")
        return row

    def _checkpoint_page(
        self,
        session_id: int,
        page: OAIPage,
        records: tuple[OAIRecord, ...],
        harvested_records: int,
        overlap_records_excluded: int,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        rows = []
        for record in records:
            fields = {
                name: list(values)
                for name, values in record.fields.items()
                if name in MINIMAL_DC_FIELDS
            }
            rows.append(
                (
                    session_id,
                    record.identifier,
                    record.source_datestamp,
                    parse_oai_datestamp(record.source_datestamp).isoformat(),
                    int(record.deleted),
                    json.dumps(fields, ensure_ascii=False, sort_keys=True),
                    now,
                )
            )
        try:
            with self.connection:
                self.connection.executemany(
                    """
                    INSERT INTO oai_staged_record_events (
                        session_id, identifier, source_datestamp, changed_at,
                        deleted, fields_json, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, identifier, source_datestamp) DO UPDATE SET
                        changed_at = excluded.changed_at,
                        deleted = excluded.deleted,
                        fields_json = excluded.fields_json,
                        cached_at = excluded.cached_at
                    """,
                    rows,
                )
                updated = self.connection.execute(
                    """
                    UPDATE oai_harvest_sessions
                    SET next_token = ?, token_expiration = ?, cursor = ?,
                        complete_list_size = ?, completed_pages = completed_pages + 1,
                        harvested_records = harvested_records + ?,
                        overlap_records_excluded = overlap_records_excluded + ?,
                        sequence_complete = ?, lease_updated_at = ?, updated_at = ?
                    WHERE session_id = ? AND lease_owner = ?
                    """,
                    (
                        page.resumption_token,
                        (
                            page.resumption_expiration.isoformat()
                            if page.resumption_expiration is not None
                            else None
                        ),
                        page.cursor,
                        page.complete_list_size,
                        harvested_records,
                        overlap_records_excluded,
                        int(page.resumption_token is None),
                        now,
                        now,
                        session_id,
                        self._lease_owner,
                    ),
                )
                if updated.rowcount != 1:
                    raise OAIError("OAI harvest session lease was lost during pagination")
        except sqlite3.Error as error:
            raise OAIError(f"OAI page checkpoint failed: {error}") from error

    def _reset_session(self, session_id: int) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                "DELETE FROM oai_staged_record_events WHERE session_id = ?",
                (session_id,),
            )
            self.connection.execute(
                """
                UPDATE oai_harvest_sessions
                SET next_token = NULL, token_expiration = NULL, cursor = NULL,
                    complete_list_size = NULL, completed_pages = 0,
                    harvested_records = 0, overlap_records_excluded = 0,
                    sequence_complete = 0, lease_updated_at = ?, updated_at = ?
                WHERE session_id = ? AND lease_owner = ?
                """,
                (now, now, session_id, self._lease_owner),
            )

    def _finalize_session(
        self,
        session_id: int,
        endpoint_hash: str,
        start: datetime,
        end: datetime,
    ) -> OAIWindowSnapshot:
        session = self._session(session_id)
        if not bool(session["sequence_complete"]):
            raise OAIError("cannot finalize an incomplete OAI harvest session")
        staged = self.connection.execute(
            """
            SELECT identifier, source_datestamp, changed_at, deleted, fields_json, cached_at
            FROM oai_staged_record_events WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        checked_at = datetime.fromisoformat(session["updated_at"])
        try:
            with self.connection:
                self.connection.executemany(
                    """
                    INSERT INTO oai_record_events (
                        endpoint_hash, identifier, source_datestamp, changed_at,
                        deleted, fields_json, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(endpoint_hash, identifier, source_datestamp) DO UPDATE SET
                        changed_at = excluded.changed_at,
                        deleted = excluded.deleted,
                        fields_json = excluded.fields_json,
                        cached_at = excluded.cached_at
                    """,
                    (
                        (
                            endpoint_hash,
                            row["identifier"],
                            row["source_datestamp"],
                            row["changed_at"],
                            row["deleted"],
                            row["fields_json"],
                            row["cached_at"],
                        )
                        for row in staged
                    ),
                )
                self.connection.execute(
                    """
                    INSERT INTO oai_coverage (endpoint_hash, window_start, window_end, checked_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(endpoint_hash, window_start, window_end) DO UPDATE SET
                        checked_at = excluded.checked_at
                    """,
                    (endpoint_hash, start.isoformat(), end.isoformat(), checked_at.isoformat()),
                )
                self._coalesce_coverage(endpoint_hash)
                self.connection.execute(
                    "DELETE FROM oai_harvest_sessions WHERE session_id = ?",
                    (session_id,),
                )
        except sqlite3.Error as error:
            raise OAIError(f"OAI harvest finalization failed: {error}") from error
        return OAIWindowSnapshot(
            window_start=start,
            window_end=end,
            checked_at=checked_at,
            records_by_key={},
            harvested_records=int(session["harvested_records"]),
            records_in_exact_window=len(staged),
            deleted_records=0,
            unkeyed_records=0,
            duplicate_keys=0,
            overlap_records_excluded=int(session["overlap_records_excluded"]),
            network_harvested_records=int(session["harvested_records"]),
        )

    def _release_session(self, session_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE oai_harvest_sessions
                SET lease_owner = NULL, lease_pid = NULL, lease_updated_at = NULL
                WHERE session_id = ? AND lease_owner = ?
                """,
                (session_id, self._lease_owner),
            )

    def inspect(self) -> OAIHarvestCacheInfo:
        """Return count-only cache state without exposing opaque resumption tokens."""

        sessions = self.connection.execute(
            """
            SELECT s.*, COUNT(e.identifier) AS staged_records
            FROM oai_harvest_sessions AS s
            LEFT JOIN oai_staged_record_events AS e ON e.session_id = s.session_id
            GROUP BY s.session_id
            ORDER BY s.updated_at
            """
        ).fetchall()
        session_info = tuple(
            OAIHarvestSessionInfo(
                endpoint_hash=row["endpoint_hash"],
                window_start=row["window_start"],
                window_end=row["window_end"],
                completed_pages=int(row["completed_pages"]),
                harvested_records=int(row["harvested_records"]),
                staged_records=int(row["staged_records"]),
                token_present=row["next_token"] is not None,
                token_expiration=row["token_expiration"],
                cursor=row["cursor"],
                complete_list_size=row["complete_list_size"],
                sequence_complete=bool(row["sequence_complete"]),
                active=(
                    row["lease_owner"] is not None
                    and row["lease_pid"] is not None
                    and _process_is_alive(int(row["lease_pid"]))
                ),
                updated_at=row["updated_at"],
            )
            for row in sessions
        )
        return OAIHarvestCacheInfo(
            path=str(self.path.resolve()) if self.path is not None else None,
            database_size_bytes=self._database_size(),
            record_events=int(
                self.connection.execute("SELECT COUNT(*) FROM oai_record_events").fetchone()[0]
            ),
            coverage_intervals=int(
                self.connection.execute("SELECT COUNT(*) FROM oai_coverage").fetchone()[0]
            ),
            incomplete_sessions=session_info,
        )

    def prune(self, before: datetime) -> OAIPruneResult:
        """Discard cache material older than a cutoff without touching monitor state."""

        if before.tzinfo is None:
            raise OAIError("OAI cache prune cutoff must be timezone-aware")
        cutoff = before.astimezone(UTC)
        active = tuple(item for item in self.inspect().incomplete_sessions if item.active)
        if active:
            raise OAIError("cannot prune the OAI cache while a harvest session is active")
        size_before = self._database_size()
        coverage_before = int(
            self.connection.execute("SELECT COUNT(*) FROM oai_coverage").fetchone()[0]
        )
        try:
            with self.connection:
                deleted_events = self.connection.execute(
                    "DELETE FROM oai_record_events WHERE changed_at < ?",
                    (cutoff.isoformat(),),
                ).rowcount
                old_sessions = self.connection.execute(
                    "SELECT session_id FROM oai_harvest_sessions WHERE window_end <= ?",
                    (cutoff.isoformat(),),
                ).fetchall()
                self.connection.executemany(
                    "DELETE FROM oai_harvest_sessions WHERE session_id = ?",
                    ((row["session_id"],) for row in old_sessions),
                )
                coverage = self.connection.execute(
                    "SELECT endpoint_hash, window_start, window_end, checked_at FROM oai_coverage"
                ).fetchall()
                self.connection.execute("DELETE FROM oai_coverage")
                self.connection.executemany(
                    """
                    INSERT INTO oai_coverage (endpoint_hash, window_start, window_end, checked_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        (
                            row["endpoint_hash"],
                            max(datetime.fromisoformat(row["window_start"]), cutoff).isoformat(),
                            row["window_end"],
                            row["checked_at"],
                        )
                        for row in coverage
                        if datetime.fromisoformat(row["window_end"]) > cutoff
                    ),
                )
            self.connection.execute("VACUUM")
        except sqlite3.Error as error:
            raise OAIError(f"OAI cache prune failed: {error}") from error
        coverage_after = int(
            self.connection.execute("SELECT COUNT(*) FROM oai_coverage").fetchone()[0]
        )
        return OAIPruneResult(
            cutoff=cutoff.isoformat(),
            record_events_deleted=deleted_events,
            coverage_intervals_deleted=coverage_before - coverage_after,
            incomplete_sessions_deleted=len(old_sessions),
            database_size_before=size_before,
            database_size_after=self._database_size(),
        )

    def _database_size(self) -> int:
        if self.path is None or not self.path.exists():
            return 0
        return self.path.stat().st_size

    def _commit_snapshot(
        self,
        endpoint_hash: str,
        snapshot: OAIWindowSnapshot,
    ) -> int:
        """Atomically store a complete snapshot and then extend cache coverage."""

        cached_at = snapshot.checked_at.astimezone(UTC).isoformat()
        events = snapshot.record_events or tuple(snapshot.records_by_key.values())
        rows = []
        for record in events:
            changed_at = parse_oai_datestamp(record.source_datestamp).isoformat()
            fields = {
                name: list(values)
                for name, values in record.fields.items()
                if name in MINIMAL_DC_FIELDS
            }
            rows.append(
                (
                    endpoint_hash,
                    record.identifier,
                    record.source_datestamp,
                    changed_at,
                    int(record.deleted),
                    json.dumps(fields, ensure_ascii=False, sort_keys=True),
                    cached_at,
                )
            )

        try:
            with self.connection:
                self.connection.executemany(
                    """
                    INSERT INTO oai_record_events (
                        endpoint_hash, identifier, source_datestamp, changed_at,
                        deleted, fields_json, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(endpoint_hash, identifier, source_datestamp) DO UPDATE SET
                        changed_at = excluded.changed_at,
                        deleted = excluded.deleted,
                        fields_json = excluded.fields_json,
                        cached_at = excluded.cached_at
                    """,
                    rows,
                )
                self.connection.execute(
                    """
                    INSERT INTO oai_coverage (
                        endpoint_hash, window_start, window_end, checked_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(endpoint_hash, window_start, window_end) DO UPDATE SET
                        checked_at = excluded.checked_at
                    """,
                    (
                        endpoint_hash,
                        snapshot.window_start.astimezone(UTC).isoformat(),
                        snapshot.window_end.astimezone(UTC).isoformat(),
                        cached_at,
                    ),
                )
                self._coalesce_coverage(endpoint_hash)
        except sqlite3.Error as error:
            raise OAIError(f"OAI cache write failed: {error}") from error
        return len(rows)

    def _coalesce_coverage(self, endpoint_hash: str) -> None:
        rows = self.connection.execute(
            """
            SELECT window_start, window_end, checked_at
            FROM oai_coverage
            WHERE endpoint_hash = ?
            ORDER BY window_start, window_end
            """,
            (endpoint_hash,),
        ).fetchall()
        merged: list[tuple[datetime, datetime, datetime]] = []
        for row in rows:
            start = datetime.fromisoformat(row["window_start"])
            end = datetime.fromisoformat(row["window_end"])
            checked_at = datetime.fromisoformat(row["checked_at"])
            if not merged or start > merged[-1][1]:
                merged.append((start, end, checked_at))
                continue
            previous_start, previous_end, previous_checked = merged[-1]
            merged[-1] = (
                previous_start,
                max(previous_end, end),
                max(previous_checked, checked_at),
            )
        self.connection.execute(
            "DELETE FROM oai_coverage WHERE endpoint_hash = ?",
            (endpoint_hash,),
        )
        self.connection.executemany(
            """
            INSERT INTO oai_coverage (endpoint_hash, window_start, window_end, checked_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                (endpoint_hash, start.isoformat(), end.isoformat(), checked.isoformat())
                for start, end, checked in merged
            ),
        )

    def _coverage(self, endpoint_hash: str) -> tuple[tuple[datetime, datetime], ...]:
        rows = self.connection.execute(
            """
            SELECT window_start, window_end
            FROM oai_coverage
            WHERE endpoint_hash = ?
            ORDER BY window_start, window_end
            """,
            (endpoint_hash,),
        ).fetchall()
        return tuple(
            (datetime.fromisoformat(row["window_start"]), datetime.fromisoformat(row["window_end"]))
            for row in rows
        )

    def _snapshot_from_cache(
        self,
        endpoint_hash: str,
        start: datetime,
        end: datetime,
        *,
        retrieval_mode: str,
        network_snapshots: tuple[OAIWindowSnapshot, ...],
        records_written: int,
        coverage: tuple[datetime, datetime],
    ) -> OAIWindowSnapshot:
        rows = self.connection.execute(
            """
            SELECT identifier, source_datestamp, deleted, fields_json
            FROM oai_record_events
            WHERE endpoint_hash = ? AND changed_at >= ? AND changed_at < ?
            ORDER BY changed_at, identifier
            """,
            (endpoint_hash, start.isoformat(), end.isoformat()),
        ).fetchall()
        events = tuple(
            OAIRecord(
                identifier=row["identifier"],
                source_datestamp=row["source_datestamp"],
                deleted=bool(row["deleted"]),
                fields={
                    name: tuple(values) for name, values in json.loads(row["fields_json"]).items()
                },
            )
            for row in rows
        )
        latest_by_key: dict[str, tuple[datetime, OAIRecord]] = {}
        unkeyed = 0
        duplicate_keys = 0
        for record in events:
            key = oai_record_key(record)
            if key is None:
                unkeyed += 1
                continue
            changed_at = parse_oai_datestamp(record.source_datestamp)
            previous = latest_by_key.get(key)
            if previous is not None:
                duplicate_keys += 1
            if previous is None or changed_at >= previous[0]:
                latest_by_key[key] = (changed_at, record)
        active = {
            key: record
            for key, (_changed_at, record) in latest_by_key.items()
            if not record.deleted
        }
        deleted = sum(record.deleted for _changed_at, record in latest_by_key.values())
        network_harvested = sum(item.harvested_records for item in network_snapshots)
        overlap_excluded = sum(item.overlap_records_excluded for item in network_snapshots)
        checked_at = max(
            (item.checked_at for item in network_snapshots),
            default=datetime.now(UTC),
        )
        return OAIWindowSnapshot(
            window_start=start,
            window_end=end,
            checked_at=checked_at,
            records_by_key=active,
            harvested_records=len(events),
            records_in_exact_window=len(events),
            deleted_records=deleted,
            unkeyed_records=unkeyed,
            duplicate_keys=duplicate_keys,
            overlap_records_excluded=overlap_excluded,
            record_events=events,
            retrieval_mode=retrieval_mode,
            network_harvested_records=network_harvested,
            cache_records_written=records_written,
            cache_refresh_windows=len(network_snapshots),
            cache_coverage_start=coverage[0],
            cache_coverage_end=coverage[1],
            cache_resumed_sessions=self._resumed_sessions,
            cache_expired_token_restarts=self._expired_token_restarts,
            cache_invalid_token_restarts=self._invalid_token_restarts,
        )


def _normalize_window(window_start: datetime, window_end: datetime) -> tuple[datetime, datetime]:
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise OAIError("OAI cache window must be timezone-aware")
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if start >= end:
        raise OAIError("OAI cache window start must be earlier than its end")
    return start, end


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _missing_intervals(
    start: datetime,
    end: datetime,
    coverage: tuple[tuple[datetime, datetime], ...],
) -> tuple[tuple[datetime, datetime], ...]:
    gaps: list[tuple[datetime, datetime]] = []
    cursor = start
    for covered_start, covered_end in coverage:
        if covered_end <= cursor or covered_start >= end:
            continue
        if covered_start > cursor:
            gaps.append((cursor, min(covered_start, end)))
        cursor = max(cursor, covered_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return tuple(gaps)


def _covering_interval(
    start: datetime,
    end: datetime,
    coverage: tuple[tuple[datetime, datetime], ...],
) -> tuple[datetime, datetime] | None:
    return next(
        (
            (covered_start, covered_end)
            for covered_start, covered_end in coverage
            if covered_start <= start and covered_end >= end
        ),
        None,
    )


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
