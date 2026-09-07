from dataclasses import replace

from philosophy_frontier_monitor.cli import build_parser
from philosophy_frontier_monitor.models import Category
from philosophy_frontier_monitor.scope_estimate import estimate_interest_scope


def test_scope_estimate_counts_descendants_without_listing_every_expanded_category(taxonomy):
    estimate = estimate_interest_scope(
        taxonomy,
        {"6266": True},
        current_expanded_category_ids=frozenset({"74924"}),
    )
    payload = estimate.to_dict()

    assert estimate.requested_expanded_category_count == 6
    assert estimate.added_category_feed_count == 5
    assert estimate.projected_expanded_category_count == 6
    assert estimate.breadth_level == "bounded"
    assert estimate.explicit_confirmation_required is False
    assert "expanded_category_ids" not in payload
    assert "不估算" in payload["resource_impacts"]["model_tokens"]


def test_scope_estimate_requires_warning_for_very_broad_addition(taxonomy):
    categories = dict(taxonomy.categories)
    new_ids = frozenset(str(90000 + index) for index in range(60))
    categories["6266"] = replace(
        categories["6266"],
        child_ids=categories["6266"].child_ids.union(new_ids),
    )
    categories.update(
        {
            category_id: Category(
                category_id=category_id,
                category_name=f"Synthetic Plato branch {category_id}",
                parent_ids=frozenset({"6266"}),
                primary_parent_id="6266",
            )
            for category_id in new_ids
        }
    )
    broad_taxonomy = replace(taxonomy, categories=categories)

    estimate = estimate_interest_scope(broad_taxonomy, {"6266": True})

    assert estimate.requested_expanded_category_count == 66
    assert estimate.added_category_feed_count == 66
    assert estimate.breadth_level == "very_broad"
    assert estimate.warning_required is True
    assert estimate.explicit_confirmation_required is True
    assert estimate.to_dict()["baseline_required_for_added_feeds"] is True


def test_cli_exposes_read_only_scope_estimate_command():
    args = build_parser().parse_args(
        ["scope-estimate", "--category-id", "6266", "--include-descendants"]
    )

    assert args.command == "scope-estimate"
    assert args.category_id == ["6266"]
    assert args.include_descendants is True
