"""Deterministic, non-mutating audits between verified taxonomy snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from .config import WatchlistConfig
from .models import Category, TaxonomySnapshot
from .taxonomy import TaxonomyError, expand_selected_categories


class TaxonomyAuditError(ValueError):
    """Raised when an audit cannot establish a trustworthy comparison baseline."""


@dataclass(frozen=True, slots=True)
class CategorySummary:
    category_id: str
    category_name: str
    parent_ids: tuple[str, ...]
    primary_parent_id: str | None

    @classmethod
    def from_category(cls, category: Category) -> CategorySummary:
        return cls(
            category_id=category.category_id,
            category_name=category.category_name,
            parent_ids=tuple(sorted(category.parent_ids, key=_category_sort_key)),
            primary_parent_id=category.primary_parent_id,
        )


@dataclass(frozen=True, slots=True)
class RemovedCategory:
    category: CategorySummary
    exact_name_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModifiedCategory:
    category_id: str
    old_name: str
    new_name: str
    change_types: tuple[str, ...]
    added_parent_ids: tuple[str, ...]
    removed_parent_ids: tuple[str, ...]
    old_primary_parent_id: str | None
    new_primary_parent_id: str | None
    old_active: bool
    new_active: bool


@dataclass(frozen=True, slots=True)
class TrackedCategoryImpact:
    category_id: str
    roles: tuple[str, ...]
    old_name: str | None
    new_name: str | None
    impact_types: tuple[str, ...]
    action: str
    exact_name_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaxonomyAudit:
    old_snapshot_id: str
    new_snapshot_id: str
    old_category_count: int
    new_category_count: int
    added_categories: tuple[CategorySummary, ...]
    removed_categories: tuple[RemovedCategory, ...]
    modified_categories: tuple[ModifiedCategory, ...]
    tracked_impacts: tuple[TrackedCategoryImpact, ...]
    old_expanded_category_ids: tuple[str, ...]
    new_expanded_category_ids: tuple[str, ...]
    expansion_added_ids: tuple[str, ...]
    expansion_removed_ids: tuple[str, ...]
    missing_feed_ids: tuple[str, ...]
    extra_feed_ids: tuple[str, ...]
    activation_status: str

    @property
    def activation_ready(self) -> bool:
        return self.activation_status == "ready"

    def to_dict(self) -> dict[str, object]:
        return {
            "old_snapshot_id": self.old_snapshot_id,
            "new_snapshot_id": self.new_snapshot_id,
            "old_category_count": self.old_category_count,
            "new_category_count": self.new_category_count,
            "change_counts": {
                "added": len(self.added_categories),
                "removed": len(self.removed_categories),
                "modified": len(self.modified_categories),
                "tracked_impacts": len(self.tracked_impacts),
            },
            "added_categories": [
                {
                    "category_id": item.category_id,
                    "category_name": item.category_name,
                    "parent_ids": item.parent_ids,
                    "primary_parent_id": item.primary_parent_id,
                }
                for item in self.added_categories
            ],
            "removed_categories": [
                {
                    "category": {
                        "category_id": item.category.category_id,
                        "category_name": item.category.category_name,
                        "parent_ids": item.category.parent_ids,
                        "primary_parent_id": item.category.primary_parent_id,
                    },
                    "exact_name_candidate_ids": item.exact_name_candidate_ids,
                }
                for item in self.removed_categories
            ],
            "modified_categories": [
                {
                    "category_id": item.category_id,
                    "old_name": item.old_name,
                    "new_name": item.new_name,
                    "change_types": item.change_types,
                    "added_parent_ids": item.added_parent_ids,
                    "removed_parent_ids": item.removed_parent_ids,
                    "old_primary_parent_id": item.old_primary_parent_id,
                    "new_primary_parent_id": item.new_primary_parent_id,
                    "old_active": item.old_active,
                    "new_active": item.new_active,
                }
                for item in self.modified_categories
            ],
            "tracked_impacts": [
                {
                    "category_id": item.category_id,
                    "roles": item.roles,
                    "old_name": item.old_name,
                    "new_name": item.new_name,
                    "impact_types": item.impact_types,
                    "action": item.action,
                    "exact_name_candidate_ids": item.exact_name_candidate_ids,
                }
                for item in self.tracked_impacts
            ],
            "interest_expansion": {
                "old_category_ids": self.old_expanded_category_ids,
                "new_category_ids": self.new_expanded_category_ids,
                "added_ids": self.expansion_added_ids,
                "removed_ids": self.expansion_removed_ids,
            },
            "feed_coverage_for_new_snapshot": {
                "missing_feed_ids": self.missing_feed_ids,
                "extra_feed_ids": self.extra_feed_ids,
            },
            "activation_status": self.activation_status,
            "activation_ready": self.activation_ready,
            "automatic_migration_performed": False,
        }


def _category_sort_key(category_id: str) -> tuple[int, int | str]:
    return (0, int(category_id)) if category_id.isdecimal() else (1, category_id)


def _names_to_ids(snapshot: TaxonomySnapshot) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for category in snapshot.categories.values():
        if category.active:
            grouped.setdefault(category.category_name.casefold().strip(), []).append(
                category.category_id
            )
    return {
        name: tuple(sorted(category_ids, key=_category_sort_key))
        for name, category_ids in grouped.items()
    }


def _modified_category(old: Category, new: Category) -> ModifiedCategory | None:
    change_types: list[str] = []
    if old.category_name != new.category_name:
        change_types.append("renamed")
    if old.parent_ids != new.parent_ids:
        change_types.append("parents_changed")
    if old.primary_parent_id != new.primary_parent_id:
        change_types.append("primary_parent_changed")
    if old.active != new.active:
        change_types.append("active_status_changed")
    if old.source_url != new.source_url:
        change_types.append("source_url_changed")
    if not change_types:
        return None
    return ModifiedCategory(
        category_id=old.category_id,
        old_name=old.category_name,
        new_name=new.category_name,
        change_types=tuple(change_types),
        added_parent_ids=tuple(
            sorted(new.parent_ids.difference(old.parent_ids), key=_category_sort_key)
        ),
        removed_parent_ids=tuple(
            sorted(old.parent_ids.difference(new.parent_ids), key=_category_sort_key)
        ),
        old_primary_parent_id=old.primary_parent_id,
        new_primary_parent_id=new.primary_parent_id,
        old_active=old.active,
        new_active=new.active,
    )


def _require_config_matches_old_snapshot(
    config: WatchlistConfig,
    old: TaxonomySnapshot,
) -> tuple[frozenset[str], dict[str, set[str]]]:
    roles: dict[str, set[str]] = {}
    selections: dict[str, bool] = {}
    for item in config.confirmed_categories:
        category = old.categories.get(item.category_id)
        if category is None or not category.active:
            raise TaxonomyAuditError(
                f"confirmed category {item.category_id} is absent or inactive in old snapshot"
            )
        if category.category_name != item.category_name:
            raise TaxonomyAuditError(
                f"confirmed category name does not match old snapshot: {item.category_id}"
            )
        selections[item.category_id] = item.include_descendants
        roles.setdefault(item.category_id, set()).add("confirmed")

    for item in config.proposed_categories:
        category = old.categories.get(item.category_id)
        if category is None or not category.active:
            raise TaxonomyAuditError(
                f"proposed category {item.category_id} is absent or inactive in old snapshot"
            )
        if category.category_name != item.category_name:
            raise TaxonomyAuditError(
                f"proposed category name does not match old snapshot: {item.category_id}"
            )
        roles.setdefault(item.category_id, set()).add("proposed")

    for category_id in config.excluded_category_ids:
        category = old.categories.get(category_id)
        if category is None or not category.active:
            raise TaxonomyAuditError(
                f"excluded category {category_id} is absent or inactive in old snapshot"
            )
        roles.setdefault(category_id, set()).add("excluded")

    try:
        old_expanded = expand_selected_categories(old, selections)
    except TaxonomyError as error:
        raise TaxonomyAuditError("old interest expansion could not be reproduced") from error
    for category_id in old_expanded:
        roles.setdefault(category_id, set()).add("expanded")
    configured_feed_ids = {item.category_id for item in config.feeds}
    if configured_feed_ids != set(old_expanded):
        raise TaxonomyAuditError(
            "configured feeds do not exactly cover the expanded set in the old snapshot"
        )
    return old_expanded, roles


def audit_taxonomy_change(
    old: TaxonomySnapshot,
    new: TaxonomySnapshot,
    config: WatchlistConfig,
) -> TaxonomyAudit:
    """Compare snapshots and evaluate impact without mutating the watchlist."""

    if new.retrieved_at < old.retrieved_at:
        raise TaxonomyAuditError("new taxonomy snapshot is older than the comparison baseline")
    if new.source != old.source:
        raise TaxonomyAuditError("taxonomy snapshots come from different source contracts")

    old_expanded, roles = _require_config_matches_old_snapshot(config, old)
    selections = {
        item.category_id: item.include_descendants for item in config.confirmed_categories
    }
    missing_selected = [
        category_id
        for category_id in selections
        if category_id not in new.categories or not new.categories[category_id].active
    ]
    if missing_selected:
        new_expanded = expand_selected_categories(
            new,
            {
                category_id: include_descendants
                for category_id, include_descendants in selections.items()
                if category_id not in missing_selected
            },
        )
    else:
        new_expanded = expand_selected_categories(new, selections)
    for category_id in new_expanded:
        roles.setdefault(category_id, set()).add("expanded_new")

    old_ids = set(old.categories)
    new_ids = set(new.categories)
    added_ids = sorted(new_ids.difference(old_ids), key=_category_sort_key)
    removed_ids = sorted(old_ids.difference(new_ids), key=_category_sort_key)
    new_name_ids = _names_to_ids(new)
    added = tuple(CategorySummary.from_category(new.categories[item]) for item in added_ids)
    removed = tuple(
        RemovedCategory(
            category=CategorySummary.from_category(old.categories[item]),
            exact_name_candidate_ids=new_name_ids.get(
                old.categories[item].category_name.casefold().strip(),
                (),
            ),
        )
        for item in removed_ids
    )

    modified_by_id = {
        category_id: change
        for category_id in sorted(old_ids.intersection(new_ids), key=_category_sort_key)
        if (change := _modified_category(old.categories[category_id], new.categories[category_id]))
        is not None
    }

    tracked_impacts: list[TrackedCategoryImpact] = []
    tracked_ids = sorted(roles, key=_category_sort_key)
    for category_id in tracked_ids:
        old_category = old.categories.get(category_id)
        new_category = new.categories.get(category_id)
        impact_types: list[str] = []
        candidates: tuple[str, ...] = ()
        if old_category is None:
            impact_types.append("newly_expanded_category")
        elif new_category is None:
            impact_types.append("removed")
            candidates = new_name_ids.get(old_category.category_name.casefold().strip(), ())
        else:
            modified = modified_by_id.get(category_id)
            if modified is not None:
                impact_types.extend(modified.change_types)
            if not new_category.active:
                impact_types.append("inactive")
        if category_id in new_expanded.difference(old_expanded):
            impact_types.append("entered_expanded_interest_set")
        if category_id in old_expanded.difference(new_expanded):
            impact_types.append("left_expanded_interest_set")
        if not impact_types:
            continue

        if "removed" in impact_types or "inactive" in impact_types:
            action = "block_and_review_category_identity"
        elif "newly_expanded_category" in impact_types or any(
            item in impact_types
            for item in ("entered_expanded_interest_set", "left_expanded_interest_set")
        ):
            action = "review_scope_and_feed_baseline"
        else:
            action = "review_and_create_new_profile_version"
        tracked_impacts.append(
            TrackedCategoryImpact(
                category_id=category_id,
                roles=tuple(sorted(roles[category_id])),
                old_name=None if old_category is None else old_category.category_name,
                new_name=None if new_category is None else new_category.category_name,
                impact_types=tuple(dict.fromkeys(impact_types)),
                action=action,
                exact_name_candidate_ids=candidates,
            )
        )

    configured_feed_ids = {item.category_id for item in config.feeds}
    missing_feed_ids = tuple(
        sorted(new_expanded.difference(configured_feed_ids), key=_category_sort_key)
    )
    extra_feed_ids = tuple(
        sorted(configured_feed_ids.difference(new_expanded), key=_category_sort_key)
    )
    has_blocking_identity_loss = any(
        impact.action == "block_and_review_category_identity" for impact in tracked_impacts
    )
    if has_blocking_identity_loss or missing_selected:
        activation_status = "blocked"
    elif tracked_impacts or missing_feed_ids or extra_feed_ids:
        activation_status = "review_required"
    else:
        activation_status = "ready"

    return TaxonomyAudit(
        old_snapshot_id=old.snapshot_id,
        new_snapshot_id=new.snapshot_id,
        old_category_count=old.category_count,
        new_category_count=new.category_count,
        added_categories=added,
        removed_categories=removed,
        modified_categories=tuple(modified_by_id.values()),
        tracked_impacts=tuple(tracked_impacts),
        old_expanded_category_ids=tuple(sorted(old_expanded, key=_category_sort_key)),
        new_expanded_category_ids=tuple(sorted(new_expanded, key=_category_sort_key)),
        expansion_added_ids=tuple(
            sorted(new_expanded.difference(old_expanded), key=_category_sort_key)
        ),
        expansion_removed_ids=tuple(
            sorted(old_expanded.difference(new_expanded), key=_category_sort_key)
        ),
        missing_feed_ids=missing_feed_ids,
        extra_feed_ids=extra_feed_ids,
        activation_status=activation_status,
    )
