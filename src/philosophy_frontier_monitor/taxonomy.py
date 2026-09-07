"""Load, validate, search, and expand a controlled PhilPapers taxonomy."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .models import Category, TaxonomySnapshot


class TaxonomyError(ValueError):
    """Raised when a taxonomy snapshot violates its structural contract."""


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_taxonomy(path: str | Path) -> TaxonomySnapshot:
    """Load a JSON snapshot and verify all graph references.

    A fixture is suitable for offline development only. Call
    :func:`require_production_taxonomy` before a live monitoring run.
    """

    snapshot_path = Path(path)
    raw_bytes = snapshot_path.read_bytes()
    payload = json.loads(raw_bytes)
    categories: dict[str, Category] = {}

    for item in payload["categories"]:
        category_id = item["category_id"]
        if category_id in categories:
            raise TaxonomyError(f"duplicate category_id: {category_id}")
        parents = frozenset(item.get("parent_ids", []))
        categories[category_id] = Category(
            category_id=category_id,
            category_name=item["category_name"],
            parent_ids=parents,
            primary_parent_id=item.get("primary_parent_id"),
            active=item.get("active", True),
            source_url=item.get("source_url"),
        )

    _validate_names(categories)
    _validate_parent_references(categories)
    categories = _derive_children(categories)
    _validate_acyclic(categories)

    declared_count = payload.get("category_count")
    if declared_count is not None and declared_count != len(categories):
        raise TaxonomyError(
            f"declared category_count {declared_count} does not match {len(categories)} records"
        )

    return TaxonomySnapshot(
        snapshot_id=payload["snapshot_id"],
        source=payload["source"],
        retrieved_at=_parse_utc(payload["retrieved_at"]),
        source_url=payload["source_url"],
        content_hash="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        complete=bool(payload["complete"]),
        fixture=bool(payload.get("fixture", False)),
        parser_version=payload["parser_version"],
        categories=categories,
        source_content_hash=payload.get("source_content_hash"),
        omitted_root_id=payload.get("omitted_root_id"),
        source_record_count=payload.get("source_record_count"),
        excluded_source_records=tuple(payload.get("excluded_source_records", ())),
        duplicate_name_groups=tuple(payload.get("duplicate_name_groups", ())),
    )


def _validate_names(categories: dict[str, Category]) -> None:
    for category in categories.values():
        key = category.category_name.casefold().strip()
        if not key:
            raise TaxonomyError(f"empty category_name for {category.category_id}")


def _validate_parent_references(categories: dict[str, Category]) -> None:
    for category in categories.values():
        missing = category.parent_ids.difference(categories)
        if missing:
            raise TaxonomyError(
                f"{category.category_id} references missing parents: {sorted(missing)}"
            )
        if (
            category.primary_parent_id is not None
            and category.primary_parent_id not in category.parent_ids
        ):
            raise TaxonomyError(f"primary parent of {category.category_id} is not in parent_ids")


def _derive_children(categories: dict[str, Category]) -> dict[str, Category]:
    children: dict[str, set[str]] = {category_id: set() for category_id in categories}
    for category in categories.values():
        for parent_id in category.parent_ids:
            children[parent_id].add(category.category_id)
    return {
        category_id: replace(category, child_ids=frozenset(children[category_id]))
        for category_id, category in categories.items()
    }


def _validate_acyclic(categories: dict[str, Category]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(category_id: str) -> None:
        if category_id in visiting:
            raise TaxonomyError(f"taxonomy cycle detected at {category_id}")
        if category_id in visited:
            return
        visiting.add(category_id)
        for child_id in categories[category_id].child_ids:
            visit(child_id)
        visiting.remove(category_id)
        visited.add(category_id)

    for category_id in categories:
        visit(category_id)


def require_production_taxonomy(snapshot: TaxonomySnapshot) -> None:
    """Reject partial or synthetic snapshots before a live collection run."""

    if snapshot.fixture:
        raise TaxonomyError("test fixture taxonomy cannot be used for live monitoring")
    if not snapshot.complete:
        raise TaxonomyError("incomplete taxonomy cannot be used for live monitoring")
    if any(category_id.startswith("fixture:") for category_id in snapshot.categories):
        raise TaxonomyError("synthetic fixture category IDs cannot be used in production")
    identity = re.fullmatch(
        r"philpapers:(\d{8}T\d{6}Z):([0-9a-f]{12})",
        snapshot.snapshot_id,
    )
    source_hash = re.fullmatch(
        r"sha256:([0-9a-f]{64})",
        snapshot.source_content_hash or "",
    )
    if identity is None or source_hash is None:
        raise TaxonomyError("production taxonomy lacks a verifiable snapshot identity")
    if identity.group(2) != source_hash.group(1)[:12]:
        raise TaxonomyError("taxonomy snapshot ID does not match its source content hash")


def find_by_name(snapshot: TaxonomySnapshot, name: str) -> Category | None:
    """Return one unambiguous exact-name match, never an arbitrary duplicate."""

    matches = find_all_by_name(snapshot, name)
    return matches[0] if len(matches) == 1 else None


def find_all_by_name(snapshot: TaxonomySnapshot, name: str) -> tuple[Category, ...]:
    """Return every active exact-name match for parent-context disambiguation."""

    key = name.casefold().strip()
    return tuple(
        sorted(
            (
                category
                for category in snapshot.categories.values()
                if category.active and category.category_name.casefold().strip() == key
            ),
            key=lambda category: int(category.category_id),
        )
    )


def descendants(snapshot: TaxonomySnapshot, category_id: str) -> frozenset[str]:
    """Return all active descendants, excluding the starting category."""

    if category_id not in snapshot.categories:
        raise TaxonomyError(f"unknown category_id: {category_id}")
    found: set[str] = set()
    queue = deque(snapshot.categories[category_id].child_ids)
    while queue:
        current_id = queue.popleft()
        if current_id in found:
            continue
        current = snapshot.categories[current_id]
        if current.active:
            found.add(current_id)
            queue.extend(current.child_ids)
    return frozenset(found)


def expand_selected_categories(
    snapshot: TaxonomySnapshot,
    selections: dict[str, bool],
) -> frozenset[str]:
    """Expand only categories whose explicit flag requests descendants."""

    expanded: set[str] = set()
    for category_id, include_descendants in selections.items():
        category = snapshot.categories.get(category_id)
        if category is None or not category.active:
            raise TaxonomyError(f"unknown or inactive selected category: {category_id}")
        expanded.add(category_id)
        if include_descendants:
            expanded.update(descendants(snapshot, category_id))
    return frozenset(expanded)
