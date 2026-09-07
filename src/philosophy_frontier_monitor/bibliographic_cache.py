"""Private, bounded cache for successful bibliographic lookup outcomes.

The cache is independent of weekly notification and checkpoint state.  It may
save repeated remote lookups, but it never suppresses a paper from a report.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import DatePrecision, DateValue
from .normalize import normalize_doi, normalize_title
from .sources.crossref import CrossrefWork
from .sources.openalex import OpenAlexWork

POSITIVE_CACHE_TTL = timedelta(hours=24)
NEGATIVE_CACHE_TTL = timedelta(minutes=15)
TITLE_BATCH_POSITIVE_CACHE_TTL = timedelta(hours=1)
TITLE_BATCH_ATTEMPT_CACHE_TTL = timedelta(hours=1)
FALLBACK_ATTEMPT_RETENTION = timedelta(days=31)


@dataclass(frozen=True, slots=True)
class CacheLookup[T]:
    hit: bool
    value: T | None


@dataclass(frozen=True, slots=True)
class BibliographicCacheStats:
    hits: int
    misses: int
    writes: int
    expired: int
    scheduling_writes: int = 0


class BibliographicCache:
    """SQLite cache that stores minimized metadata, never credentials or URLs queried."""

    def __init__(self, path: str | Path):
        self.path = Path(path) if path != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = str(self.path)
        else:
            database = ":memory:"
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exact_lookup_cache (
                source TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('found', 'not_found')),
                payload_json TEXT,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (source, query_hash)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fallback_attempt_log (
                source_id_hash TEXT PRIMARY KEY,
                attempted_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._expired = 0
        self._scheduling_writes = 0

    def __enter__(self) -> BibliographicCache:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @property
    def stats(self) -> BibliographicCacheStats:
        return BibliographicCacheStats(
            hits=self._hits,
            misses=self._misses,
            writes=self._writes,
            expired=self._expired,
            scheduling_writes=self._scheduling_writes,
        )

    def lookup_crossref(
        self,
        title: str,
        first_author: str | None,
        *,
        now: datetime,
    ) -> CacheLookup[CrossrefWork]:
        cached = self._lookup_key(
            "crossref",
            _query_hash(title, first_author),
            now=now,
        )
        if not cached.hit or cached.value is None:
            return CacheLookup(cached.hit, None)
        if not isinstance(cached.value, dict):
            raise ValueError("cached Crossref payload is not an object")
        return CacheLookup(True, _crossref_from_payload(cached.value))

    def store_crossref(
        self,
        title: str,
        first_author: str | None,
        work: CrossrefWork | None,
        *,
        now: datetime,
    ) -> None:
        self._store_key(
            "crossref",
            _query_hash(title, first_author),
            _crossref_payload(work) if work is not None else None,
            now=now,
        )

    def lookup_openalex(
        self,
        title: str,
        first_author: str | None,
        *,
        now: datetime,
    ) -> CacheLookup[OpenAlexWork]:
        cached = self._lookup_key(
            "openalex",
            _query_hash(title, first_author),
            now=now,
        )
        if not cached.hit or cached.value is None:
            return CacheLookup(cached.hit, None)
        if not isinstance(cached.value, dict):
            raise ValueError("cached OpenAlex payload is not an object")
        return CacheLookup(True, _openalex_from_payload(cached.value))

    def store_openalex(
        self,
        title: str,
        first_author: str | None,
        work: OpenAlexWork | None,
        *,
        now: datetime,
    ) -> None:
        self._store_key(
            "openalex",
            _query_hash(title, first_author),
            _openalex_payload(work) if work is not None else None,
            now=now,
        )

    def lookup_openalex_doi(
        self,
        doi: str,
        *,
        now: datetime,
    ) -> CacheLookup[OpenAlexWork]:
        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError(f"invalid DOI cache key: {doi!r}")
        cached = self._lookup_key(
            "openalex-doi-batch",
            _identifier_hash("doi", normalized),
            now=now,
        )
        if not cached.hit or cached.value is None:
            return CacheLookup(cached.hit, None)
        if not isinstance(cached.value, dict):
            raise ValueError("cached OpenAlex DOI payload is not an object")
        return CacheLookup(True, _openalex_from_payload(cached.value))

    def store_openalex_doi(
        self,
        doi: str,
        work: OpenAlexWork | None,
        *,
        now: datetime,
    ) -> None:
        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError(f"invalid DOI cache key: {doi!r}")
        self._store_key(
            "openalex-doi-batch",
            _identifier_hash("doi", normalized),
            _openalex_payload(work) if work is not None else None,
            now=now,
        )

    def lookup_openalex_title_candidates(
        self,
        title: str,
        *,
        now: datetime,
    ) -> CacheLookup[tuple[OpenAlexWork, ...]]:
        cached = self._lookup_key(
            "openalex-title-batch",
            _query_hash(title, None),
            now=now,
        )
        if not cached.hit:
            return CacheLookup(False, None)
        if not isinstance(cached.value, list):
            raise ValueError("cached OpenAlex title-batch payload is not a list")
        if not all(isinstance(item, dict) for item in cached.value):
            raise ValueError("cached OpenAlex title-batch item is not an object")
        return CacheLookup(
            True,
            tuple(_openalex_from_payload(item) for item in cached.value),
        )

    def store_openalex_title_candidates(
        self,
        title: str,
        works: tuple[OpenAlexWork, ...],
        *,
        now: datetime,
    ) -> None:
        """Cache only positive exact-title batches for one hour.

        Empty title results are deliberately not cached: a broad OR query
        cannot prove that one individual title is absent from OpenAlex.
        """

        if not works:
            return
        self._store_key(
            "openalex-title-batch",
            _query_hash(title, None),
            [_openalex_payload(work) for work in works],
            now=now,
            positive_ttl=TITLE_BATCH_POSITIVE_CACHE_TTL,
        )

    def was_openalex_title_batch_attempted(
        self,
        title: str,
        *,
        now: datetime,
    ) -> bool:
        """Return whether this title recently participated in a successful batch call.

        The marker is scheduling metadata, not evidence that OpenAlex lacks the
        work. It therefore does not return or cache a negative bibliographic result.
        """

        return self._peek_key(
            "openalex-title-batch-attempt",
            _query_hash(title, None),
            now=now,
        ).hit

    def record_openalex_title_batch_attempts(
        self,
        titles: tuple[str, ...],
        *,
        now: datetime,
    ) -> None:
        """Record a successful batch attempt for one hour using hashed title keys."""

        _require_aware(now)
        normalized_titles = tuple(
            sorted({normalize_title(title) for title in titles if normalize_title(title)})
        )
        if not normalized_titles:
            return
        cached_at = now.astimezone(UTC)
        expires_at = cached_at + TITLE_BATCH_ATTEMPT_CACHE_TTL
        payload_json = json.dumps({"attempted": True}, sort_keys=True)
        self.connection.executemany(
            """
            INSERT INTO exact_lookup_cache (
                source, query_hash, status, payload_json, cached_at, expires_at
            ) VALUES (?, ?, 'found', ?, ?, ?)
            ON CONFLICT(source, query_hash) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json,
                cached_at = excluded.cached_at,
                expires_at = excluded.expires_at
            """,
            (
                (
                    "openalex-title-batch-attempt",
                    _query_hash(title, None),
                    payload_json,
                    cached_at.isoformat(),
                    expires_at.isoformat(),
                )
                for title in normalized_titles
            ),
        )
        self.connection.execute(
            """
            DELETE FROM exact_lookup_cache
            WHERE source = 'openalex-title-batch-attempt' AND expires_at <= ?
            """,
            (cached_at.isoformat(),),
        )
        self.connection.commit()
        self._scheduling_writes += len(normalized_titles)

    def can_resolve_fallback_without_remote(
        self,
        title: str,
        first_author: str | None,
        *,
        now: datetime,
    ) -> bool:
        """Return whether the individual fallback is complete in fresh cache entries.

        This planning lookup deliberately does not change cache hit/miss counters.
        The normal resolver performs and accounts for the actual cache reads later.
        """

        crossref = self._peek_key(
            "crossref",
            _query_hash(title, first_author),
            now=now,
        )
        if not crossref.hit:
            return False
        crossref_work = None
        if crossref.value is not None:
            if not isinstance(crossref.value, dict):
                raise ValueError("cached Crossref payload is not an object")
            crossref_work = _crossref_from_payload(crossref.value)
        needs_openalex = (
            crossref_work is None
            or crossref_work.publication_date is None
            or crossref_work.publication_event is None
            or crossref_work.publication_date.precision
            not in {DatePrecision.DAY, DatePrecision.SECOND}
        )
        if not needs_openalex:
            return True
        openalex = self._peek_key(
            "openalex",
            _query_hash(title, first_author),
            now=now,
        )
        return openalex.hit

    def fallback_last_attempt(
        self,
        source_id: str,
        *,
        now: datetime,
    ) -> datetime | None:
        """Read private rotation metadata without exposing the source identifier."""

        _require_aware(now)
        row = self.connection.execute(
            """
            SELECT attempted_at
            FROM fallback_attempt_log
            WHERE source_id_hash = ?
            """,
            (_identifier_hash("philpapers-record", source_id),),
        ).fetchone()
        if row is None:
            return None
        attempted_at = datetime.fromisoformat(row["attempted_at"])
        if attempted_at < now.astimezone(UTC) - FALLBACK_ATTEMPT_RETENTION:
            return None
        return attempted_at

    def record_fallback_attempts(
        self,
        source_ids: tuple[str, ...],
        *,
        now: datetime,
    ) -> None:
        """Remember completed remote-fallback slots so later pulls advance fairly."""

        _require_aware(now)
        if not source_ids:
            return
        attempted_at = now.astimezone(UTC)
        self.connection.executemany(
            """
            INSERT INTO fallback_attempt_log (source_id_hash, attempted_at)
            VALUES (?, ?)
            ON CONFLICT(source_id_hash) DO UPDATE SET
                attempted_at = excluded.attempted_at
            """,
            (
                (
                    _identifier_hash("philpapers-record", source_id),
                    attempted_at.isoformat(),
                )
                for source_id in source_ids
            ),
        )
        self.connection.execute(
            "DELETE FROM fallback_attempt_log WHERE attempted_at < ?",
            ((attempted_at - FALLBACK_ATTEMPT_RETENTION).isoformat(),),
        )
        self.connection.commit()
        self._scheduling_writes += len(source_ids)

    def _peek_key(
        self,
        source: str,
        query_hash: str,
        *,
        now: datetime,
    ) -> CacheLookup[Any]:
        """Inspect a cache key for request planning without changing cache statistics."""

        _require_aware(now)
        row = self.connection.execute(
            """
            SELECT status, payload_json, expires_at
            FROM exact_lookup_cache
            WHERE source = ? AND query_hash = ?
            """,
            (source, query_hash),
        ).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) <= now.astimezone(UTC):
            return CacheLookup(False, None)
        if row["status"] == "not_found":
            return CacheLookup(True, None)
        return CacheLookup(True, json.loads(row["payload_json"]))

    def _lookup_key(
        self,
        source: str,
        query_hash: str,
        *,
        now: datetime,
    ) -> CacheLookup[Any]:
        _require_aware(now)
        row = self.connection.execute(
            """
            SELECT status, payload_json, expires_at
            FROM exact_lookup_cache
            WHERE source = ? AND query_hash = ?
            """,
            (source, query_hash),
        ).fetchone()
        if row is None:
            self._misses += 1
            return CacheLookup(False, None)
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now.astimezone(UTC):
            self.connection.execute(
                "DELETE FROM exact_lookup_cache WHERE source = ? AND query_hash = ?",
                (source, query_hash),
            )
            self.connection.commit()
            self._expired += 1
            self._misses += 1
            return CacheLookup(False, None)
        self._hits += 1
        if row["status"] == "not_found":
            return CacheLookup(True, None)
        return CacheLookup(True, json.loads(row["payload_json"]))

    def _store_key(
        self,
        source: str,
        query_hash: str,
        payload: Any | None,
        *,
        now: datetime,
        positive_ttl: timedelta = POSITIVE_CACHE_TTL,
    ) -> None:
        _require_aware(now)
        cached_at = now.astimezone(UTC)
        expires_at = cached_at + (positive_ttl if payload is not None else NEGATIVE_CACHE_TTL)
        self.connection.execute(
            """
            INSERT INTO exact_lookup_cache (
                source, query_hash, status, payload_json, cached_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, query_hash) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json,
                cached_at = excluded.cached_at,
                expires_at = excluded.expires_at
            """,
            (
                source,
                query_hash,
                "found" if payload is not None else "not_found",
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if payload is not None
                else None,
                cached_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        self.connection.commit()
        self._writes += 1


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("bibliographic cache time must be timezone-aware")


def _query_hash(title: str, first_author: str | None) -> str:
    normalized = f"{normalize_title(title)}\n{normalize_title(first_author or '')}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _identifier_hash(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\n{value}".encode()).hexdigest()


def _date_payload(value: DateValue | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value.value, datetime):
        value_type = "datetime"
        rendered = value.value.isoformat()
    elif isinstance(value.value, date):
        value_type = "date"
        rendered = value.value.isoformat()
    else:
        value_type = "text"
        rendered = value.value
    return {
        "value": rendered,
        "value_type": value_type,
        "precision": value.precision.value,
        "source": value.source,
        "source_record_id": value.source_record_id,
        "inferred": value.inferred,
        "retrieved_at": value.retrieved_at.isoformat() if value.retrieved_at else None,
    }


def _date_from_payload(payload: dict[str, Any] | None) -> DateValue | None:
    if payload is None:
        return None
    value_type = payload["value_type"]
    if value_type == "datetime":
        value: date | datetime | str = datetime.fromisoformat(payload["value"])
    elif value_type == "date":
        value = date.fromisoformat(payload["value"])
    elif value_type == "text":
        value = str(payload["value"])
    else:
        raise ValueError(f"unsupported cached date type: {value_type}")
    retrieved_at = (
        datetime.fromisoformat(payload["retrieved_at"])
        if payload.get("retrieved_at") is not None
        else None
    )
    return DateValue(
        value=value,
        precision=DatePrecision(payload["precision"]),
        source=payload["source"],
        source_record_id=payload.get("source_record_id"),
        inferred=bool(payload.get("inferred", False)),
        retrieved_at=retrieved_at,
    )


def _crossref_payload(work: CrossrefWork) -> dict[str, Any]:
    return {
        "doi": work.doi,
        "title": work.title,
        "authors": list(work.authors),
        "container_title": work.container_title,
        "work_type": work.work_type,
        "stable_url": work.stable_url,
        "publication_date": _date_payload(work.publication_date),
        "publication_event": work.publication_event,
    }


def _crossref_from_payload(payload: dict[str, Any]) -> CrossrefWork:
    return CrossrefWork(
        doi=payload["doi"],
        title=payload["title"],
        authors=tuple(payload["authors"]),
        container_title=payload.get("container_title"),
        work_type=payload.get("work_type"),
        stable_url=payload["stable_url"],
        publication_date=_date_from_payload(payload.get("publication_date")),
        publication_event=payload.get("publication_event"),
        raw={},
    )


def _openalex_payload(work: OpenAlexWork) -> dict[str, Any]:
    return {
        "openalex_id": work.openalex_id,
        "doi": work.doi,
        "title": work.title,
        "authors": list(work.authors),
        "publication_date": _date_payload(work.publication_date),
        "work_type": work.work_type,
        "stable_url": work.stable_url,
    }


def _openalex_from_payload(payload: dict[str, Any]) -> OpenAlexWork:
    return OpenAlexWork(
        openalex_id=payload["openalex_id"],
        doi=payload.get("doi"),
        title=payload["title"],
        authors=tuple(payload["authors"]),
        publication_date=_date_from_payload(payload.get("publication_date")),
        work_type=payload.get("work_type"),
        stable_url=payload["stable_url"],
        raw={},
    )
