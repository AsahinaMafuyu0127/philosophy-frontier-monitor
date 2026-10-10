"""Private feed inventory snapshots for an exact two-pass on-demand review."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .config import WatchlistConfig
from .pipeline import FeedSnapshot
from .sources.philpapers_rss import FeedEntry


def serialize_feed_snapshots(snapshots: tuple[FeedSnapshot, ...]) -> list[dict[str, object]]:
    """Keep the first pass's bounded, normalized feed entries in its private queue."""

    return [
        {
            "feed_key": item.feed_key,
            "category_id": item.category_id,
            "category_url": item.category_url,
            "checked_at": item.checked_at.isoformat(),
            "content_hash": item.content_hash,
            "entries": [asdict(entry) for entry in item.entries],
        }
        for item in snapshots
    ]


def load_review_feed_snapshots(
    path: str | Path,
    config: WatchlistConfig,
    *,
    window_end: datetime,
) -> tuple[FeedSnapshot, ...]:
    """Reject a stale, mismatched, or malformed private first-pass inventory."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("review queue requires schema_version 1")
    if not isinstance(data.get("window_end"), str):
        raise ValueError("review queue needs an exact window end")
    try:
        saved_end = datetime.fromisoformat(data["window_end"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("review queue has an invalid window end") from error
    if saved_end.tzinfo is None or saved_end.astimezone(UTC) != window_end.astimezone(UTC):
        raise ValueError("review queue window does not match --as-of")
    raw_snapshots = data.get("feed_snapshots")
    if not isinstance(raw_snapshots, list) or len(raw_snapshots) != len(config.feeds):
        raise ValueError("review queue lacks the complete first-pass feed inventory")
    by_key: dict[str, FeedSnapshot] = {}
    for raw in raw_snapshots:
        if not isinstance(raw, dict):
            raise ValueError("review queue has a malformed feed snapshot")
        key = raw.get("feed_key")
        if not isinstance(key, str) or key in by_key:
            raise ValueError("review queue has duplicate or invalid feed keys")
        checked_at = raw.get("checked_at")
        try:
            observed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("review queue has an invalid feed observation time") from error
        if observed.tzinfo is None or observed.astimezone(UTC) != saved_end.astimezone(UTC):
            raise ValueError("review queue feed observation does not match the initial window")
        raw_entries = raw.get("entries")
        if not isinstance(raw_entries, list) or len(raw_entries) > 5000:
            raise ValueError("review queue has invalid feed entries")
        entries: list[FeedEntry] = []
        for entry in raw_entries:
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(field), str) and entry[field]
                for field in ("source_id", "title", "link")
            ):
                raise ValueError("review queue has a malformed feed entry")
            if any(
                entry.get(field) is not None and not isinstance(entry[field], str)
                for field in ("description", "published_text")
            ):
                raise ValueError("review queue has a malformed optional feed field")
            entries.append(
                FeedEntry(
                    source_id=entry["source_id"],
                    title=entry["title"],
                    link=entry["link"],
                    description=entry.get("description"),
                    published_text=entry.get("published_text"),
                )
            )
        if not all(isinstance(raw.get(field), str) for field in (
            "category_id", "category_url", "content_hash"
        )):
            raise ValueError("review queue has incomplete feed metadata")
        by_key[key] = FeedSnapshot(
            feed_key=key,
            category_id=raw["category_id"],
            category_url=raw["category_url"],
            checked_at=observed,
            content_hash=raw["content_hash"],
            entries=tuple(entries),
        )
    expected = {feed.feed_key: feed for feed in config.feeds}
    if set(by_key) != set(expected) or any(
        by_key[key].category_id != feed.category_id or by_key[key].category_url != feed.url
        for key, feed in expected.items()
    ):
        raise ValueError("review queue feed inventory does not match the watchlist")
    return tuple(by_key[feed.feed_key] for feed in config.feeds)
