"""Deterministic resource estimates for proposed interest-scope changes.

The estimator deliberately counts taxonomy and feed scope instead of inventing a
model-token estimate.  Model context, bibliographic API work, and report length
depend on how many records later arrive in those feeds and therefore cannot be
known from the taxonomy alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import TaxonomySnapshot
from .taxonomy import expand_selected_categories

BROAD_ADDITION_THRESHOLD = 10
VERY_BROAD_ADDITION_THRESHOLD = 50


@dataclass(frozen=True, slots=True)
class ScopeEstimate:
    requested_categories: tuple[dict[str, object], ...]
    requested_expanded_category_count: int
    current_expanded_category_count: int
    projected_expanded_category_count: int
    added_category_feed_count: int
    already_covered_category_count: int
    breadth_level: str
    warning_required: bool
    explicit_confirmation_required: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_categories": list(self.requested_categories),
            "requested_expanded_category_count": self.requested_expanded_category_count,
            "current_expanded_category_count": self.current_expanded_category_count,
            "projected_expanded_category_count": self.projected_expanded_category_count,
            "added_category_feed_count": self.added_category_feed_count,
            "already_covered_category_count": self.already_covered_category_count,
            "breadth_level": self.breadth_level,
            "warning_required": self.warning_required,
            "explicit_confirmation_required": self.explicit_confirmation_required,
            "baseline_required_for_added_feeds": self.added_category_feed_count > 0,
            "resource_impacts": {
                "taxonomy_expansion": ("本地确定性 taxonomy 图遍历；该计算本身不需要模型推理"),
                "category_feeds_per_full_scan": self.projected_expanded_category_count,
                "new_category_feeds": self.added_category_feed_count,
                "bibliographic_api_work": (
                    "取决于实际候选；不能仅从分类数量推导精确的 OpenAlex/Crossref 请求数或 credits"
                ),
                "model_tokens": (
                    "不估算：后续上下文和报告 token 取决于候选论文数；默认预估不打印完整后代清单"
                ),
            },
            "recommended_action": _recommended_action(self),
        }


def estimate_interest_scope(
    snapshot: TaxonomySnapshot,
    requested_selections: dict[str, bool],
    *,
    current_expanded_category_ids: frozenset[str] = frozenset(),
) -> ScopeEstimate:
    """Estimate a proposed additive scope change without fetching or mutating state."""

    if not requested_selections:
        raise ValueError("at least one requested category is required")

    requested_expanded = expand_selected_categories(snapshot, requested_selections)
    current = frozenset(current_expanded_category_ids)
    unknown_current = current.difference(snapshot.categories)
    if unknown_current:
        raise ValueError(
            "current expanded category IDs are absent from this taxonomy snapshot: "
            f"{sorted(unknown_current)}"
        )

    projected = current.union(requested_expanded)
    added = requested_expanded.difference(current)
    already_covered = requested_expanded.intersection(current)

    if len(added) > VERY_BROAD_ADDITION_THRESHOLD:
        breadth_level = "very_broad"
    elif len(added) > BROAD_ADDITION_THRESHOLD:
        breadth_level = "broad"
    else:
        breadth_level = "bounded"

    requested_categories = tuple(
        {
            "category_id": category_id,
            "category_name": snapshot.categories[category_id].category_name,
            "include_descendants": include_descendants,
        }
        for category_id, include_descendants in sorted(requested_selections.items())
    )
    warning_required = breadth_level != "bounded"

    return ScopeEstimate(
        requested_categories=requested_categories,
        requested_expanded_category_count=len(requested_expanded),
        current_expanded_category_count=len(current),
        projected_expanded_category_count=len(projected),
        added_category_feed_count=len(added),
        already_covered_category_count=len(already_covered),
        breadth_level=breadth_level,
        warning_required=warning_required,
        explicit_confirmation_required=warning_required,
    )


def _recommended_action(estimate: ScopeEstimate) -> str:
    if estimate.breadth_level == "very_broad":
        return (
            "不得静默启用。先显示这些计数，解释持续性 feed 范围和取决于候选的成本，"
            "取得明确确认，并提供较窄子分支作为替代方案。"
        )
    if estimate.breadth_level == "broad":
        return "先显示这些计数并取得明确确认，才能修改 watchlist 或建立新增 feed 基线。"
    return "本次新增范围有限；仍须遵守普通标签确认规则。"
