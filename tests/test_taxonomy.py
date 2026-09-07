from dataclasses import replace
from datetime import timedelta

import pytest

from philosophy_frontier_monitor.config import (
    ConfirmedCategoryConfig,
    load_watchlist,
)
from philosophy_frontier_monitor.models import Category
from philosophy_frontier_monitor.taxonomy import (
    TaxonomyError,
    descendants,
    find_all_by_name,
    find_by_name,
    require_production_taxonomy,
)
from philosophy_frontier_monitor.taxonomy_audit import audit_taxonomy_change

CONFIG = load_watchlist("tests/fixtures/watchlist_minimal.yaml")


def changed_snapshot(taxonomy, categories, *, suffix):
    return replace(
        taxonomy,
        snapshot_id=f"philpapers-fixture:{suffix}",
        retrieved_at=taxonomy.retrieved_at + timedelta(days=1),
        categories=categories,
    )


def test_loads_multi_parent_category_and_derives_children(taxonomy):
    category = find_by_name(taxonomy, "Plato: Epistemology")

    assert category is not None
    assert category.parent_ids == frozenset({"6266", "11"})
    assert "74844" in category.child_ids


def test_descendants_follow_hierarchy_without_adding_ancestors(taxonomy):
    result = descendants(taxonomy, "74810")

    assert result == frozenset({"74844"})
    assert "6266" not in result
    assert "11" not in result


def test_fixture_is_rejected_for_live_monitoring(taxonomy):
    with pytest.raises(TaxonomyError, match="fixture"):
        require_production_taxonomy(taxonomy)


def test_unique_name_lookup_is_a_special_case_of_all_name_lookup(taxonomy):
    matches = find_all_by_name(taxonomy, "Plato: Epistemology")

    assert len(matches) == 1
    assert find_by_name(taxonomy, "Plato: Epistemology") == matches[0]


def test_taxonomy_audit_allows_unrelated_additions_without_changing_watchlist(taxonomy):
    categories = dict(taxonomy.categories)
    categories["99999"] = Category("99999", "An Unrelated New Category")
    new = changed_snapshot(taxonomy, categories, suffix="unrelated-addition")

    audit = audit_taxonomy_change(taxonomy, new, CONFIG)

    assert audit.activation_status == "ready"
    assert audit.activation_ready is True
    assert [item.category_id for item in audit.added_categories] == ["99999"]
    assert audit.tracked_impacts == ()
    assert audit.missing_feed_ids == ()


def test_taxonomy_audit_requires_review_for_tracked_name_change(taxonomy):
    categories = dict(taxonomy.categories)
    categories["74924"] = replace(
        categories["74924"],
        category_name="Plato: Theaetetus (renamed)",
    )
    new = changed_snapshot(taxonomy, categories, suffix="tracked-rename")

    audit = audit_taxonomy_change(taxonomy, new, CONFIG)

    assert audit.activation_status == "review_required"
    assert audit.activation_ready is False
    impact = next(item for item in audit.tracked_impacts if item.category_id == "74924")
    assert "renamed" in impact.impact_types
    assert impact.action == "review_and_create_new_profile_version"
    assert audit.to_dict()["automatic_migration_performed"] is False


def test_taxonomy_audit_blocks_removed_tracked_id_even_with_exact_name_candidate(taxonomy):
    categories = dict(taxonomy.categories)
    removed = categories.pop("74924")
    categories["99999"] = replace(removed, category_id="99999")
    categories["74818"] = replace(
        categories["74818"],
        child_ids=frozenset(
            {"99999" if item == "74924" else item for item in categories["74818"].child_ids}
        ),
    )
    new = changed_snapshot(taxonomy, categories, suffix="tracked-replacement")

    audit = audit_taxonomy_change(taxonomy, new, CONFIG)

    assert audit.activation_status == "blocked"
    impact = next(item for item in audit.tracked_impacts if item.category_id == "74924")
    assert impact.action == "block_and_review_category_identity"
    assert impact.exact_name_candidate_ids == ("99999",)
    assert audit.to_dict()["automatic_migration_performed"] is False


def test_taxonomy_audit_detects_new_descendant_that_needs_feed_baseline(taxonomy):
    expanded_config = replace(
        CONFIG,
        confirmed_categories=(
            ConfirmedCategoryConfig(
                category_id="74924",
                category_name="Plato: Theaetetus",
                include_descendants=True,
            ),
        ),
    )
    categories = dict(taxonomy.categories)
    categories["99999"] = Category(
        category_id="99999",
        category_name="A New Theaetetus Subtopic",
        parent_ids=frozenset({"74924"}),
        primary_parent_id="74924",
    )
    categories["74924"] = replace(
        categories["74924"],
        child_ids=frozenset({*categories["74924"].child_ids, "99999"}),
    )
    new = changed_snapshot(taxonomy, categories, suffix="new-descendant")

    audit = audit_taxonomy_change(taxonomy, new, expanded_config)

    assert audit.activation_status == "review_required"
    assert audit.expansion_added_ids == ("99999",)
    assert audit.missing_feed_ids == ("99999",)
    impact = next(item for item in audit.tracked_impacts if item.category_id == "99999")
    assert impact.action == "review_scope_and_feed_baseline"
