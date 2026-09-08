"""Private cross-run cache for complete PhilArchive OAI harvest windows.

The cache stores only the OAI identifier, source datestamp, deletion marker and
the minimal Dublin Core fields used by the monitor.  Coverage is extended only
after a complete paginated request succeeds, so a partial or failed harvest can
never masquerade as a cache hit.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .sources.philarchive_oai import (
    OAIError,
    OAIRecord,
    OAIWindowSnapshot,
    load_recent_window,
    oai_record_key,
    parse_oai_datestamp,
)

MINIMAL_DC_FIELDS = frozenset({"date", "identifier", "type"})

NetworkLoader = Callable[[datetime, datetime, str], OAIWindowSnapshot]
PageProgress = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class OAIHarvestCacheStats:
    hits: int
    cold_starts: int
    incremental_refreshes: int
    network_windows: int
    records_written: int


class OAIHarvestCache:
    """SQLite cache of successfully completed OAI windows and minimal records."""

    def __init__(
        self,
        path: str | Path,
        *,
        network_loader: NetworkLoader | None = None,
        page_progress: PageProgress | None = None,
    ):
        self.path = Path(path) if path != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = str(self.path)
        else:
            database = ":memory:"
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
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
        self.connection.commit()
        self.network_loader = network_loader
        self.page_progress = page_progress
        self._hits = 0
        self._cold_starts = 0
        self._incremental_refreshes = 0
        self._network_windows = 0
        self._records_written = 0

    def __enter__(self) -> OAIHarvestCache:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @property
    def stats(self) -> OAIHarvestCacheStats:
        return OAIHarvestCacheStats(
            hits=self._hits,
            cold_starts=self._cold_starts,
            incremental_refreshes=self._incremental_refreshes,
            network_windows=self._network_windows,
            records_written=self._records_written,
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
        coverage_before = self._coverage(endpoint_hash)
        gaps = _missing_intervals(start, end, coverage_before)
        network_snapshots: list[OAIWindowSnapshot] = []
        records_written = 0

        if not gaps:
            retrieval_mode = "cache_hit"
            self._hits += 1
        else:
            retrieval_mode = "cold_start" if not coverage_before else "incremental_refresh"
            if retrieval_mode == "cold_start":
                self._cold_starts += 1
            else:
                self._incremental_refreshes += 1

            for gap_start, gap_end in gaps:
                try:
                    snapshot = (
                        self.network_loader(gap_start, gap_end, endpoint)
                        if self.network_loader is not None
                        else load_recent_window(
                            gap_start,
                            gap_end,
                            endpoint=endpoint,
                            page_progress=self.page_progress,
                        )
                    )
                except OAIError:
                    raise
                except Exception as error:
                    raise OAIError(f"OAI cache refresh failed: {error}") from error
                if snapshot.window_start != gap_start or snapshot.window_end != gap_end:
                    raise OAIError("OAI cache loader returned a window different from its request")
                written = self._commit_snapshot(endpoint_hash, snapshot)
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
