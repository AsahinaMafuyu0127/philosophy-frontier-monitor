"""Transparent natural-language to controlled-category mapping.

This first implementation is deliberately rule-based. A language model may
later propose candidates, but only names resolved against the taxonomy can be
selected.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import (
    AmbiguousCandidate,
    InterestProfile,
    MappingSource,
    SelectedCategory,
    TaxonomySnapshot,
)
from .taxonomy import expand_selected_categories, find_by_name


@dataclass(frozen=True, slots=True)
class AliasRule:
    pattern: str
    category_name: str
    source: MappingSource
    include_descendants: bool = False
    requires_plato_context: bool = False


DEFAULT_ALIAS_RULES = (
    AliasRule(r"柏拉图|\bplato\b", "Plato", MappingSource.VERIFIED_ALIAS),
    AliasRule(
        r"认识论|知识论|\bepistemolog(?:y|ical)\b", "Epistemology", MappingSource.VERIFIED_ALIAS
    ),
    AliasRule(
        r"柏拉图.{0,8}(认识论|知识(?:的)?学说)|plato.{0,20}epistemolog",
        "Plato: Epistemology",
        MappingSource.CONTEXTUAL_MAPPING,
    ),
    AliasRule(
        r"知识.{0,8}(信念|意见)|信念.{0,8}知识|knowledge.{0,20}belief",
        "Plato: Knowledge and Belief",
        MappingSource.CONTEXTUAL_MAPPING,
        requires_plato_context=True,
    ),
    AliasRule(
        r"泰阿泰德(?:篇)?|theaetetus",
        "Plato: Theaetetus",
        MappingSource.EXPLICIT_WORK,
    ),
    AliasRule(
        r"巴门尼德篇|《巴门尼德》|plato(?:'s)?\s+parmenides",
        "Plato: Parmenides",
        MappingSource.EXPLICIT_WORK,
    ),
)


def _has_plato_context(text: str) -> bool:
    return bool(re.search(r"柏拉图|\bplato\b", text, flags=re.IGNORECASE))


def build_interest_profile(
    original_text: str,
    snapshot: TaxonomySnapshot,
    *,
    profile_id: str | None = None,
    version: int = 1,
    rules: tuple[AliasRule, ...] = DEFAULT_ALIAS_RULES,
    now: datetime | None = None,
) -> InterestProfile:
    """Build an auditable draft profile from deterministic alias rules."""

    if not original_text.strip():
        raise ValueError("research direction cannot be empty")
    created_at = now or datetime.now(UTC)
    selections: dict[str, SelectedCategory] = {}
    plato_context = _has_plato_context(original_text)

    for rule in rules:
        match = re.search(rule.pattern, original_text, flags=re.IGNORECASE)
        if match is None or (rule.requires_plato_context and not plato_context):
            continue
        category = find_by_name(snapshot, rule.category_name)
        if category is None:
            continue
        selections[category.category_id] = SelectedCategory(
            category_id=category.category_id,
            category_name=category.category_name,
            mapping_source=rule.source,
            evidence=f"规则命中：{match.group(0)}",
            original_fragment=match.group(0),
            include_descendants=rule.include_descendants,
        )

    ambiguous: list[AmbiguousCandidate] = []
    bare_parmenides = re.search(r"(?<!柏拉图的)巴门尼德(?!篇)|\bparmenides\b", original_text, re.I)
    explicit_dialogue = re.search(
        r"巴门尼德篇|《巴门尼德》|plato(?:'s)?\s+parmenides", original_text, re.I
    )
    if bare_parmenides and not explicit_dialogue:
        candidate_names = ("Parmenides", "Plato: Parmenides")
        candidate_ids = tuple(
            category.category_id
            for name in candidate_names
            if (category := find_by_name(snapshot, name)) is not None
        )
        ambiguous.append(
            AmbiguousCandidate(
                original_fragment=bare_parmenides.group(0),
                candidate_category_ids=candidate_ids,
                reason="可能指前苏格拉底哲学家，也可能指柏拉图对话篇；未自动选择。",
            )
        )

    expansion_flags = {
        selection.category_id: selection.include_descendants for selection in selections.values()
    }
    expanded = expand_selected_categories(snapshot, expansion_flags)
    return InterestProfile(
        profile_id=profile_id or f"pfm:interest:{uuid.uuid4()}",
        version=version,
        created_at=created_at,
        effective_from=created_at,
        original_text=original_text,
        taxonomy_snapshot_id=snapshot.snapshot_id,
        selected_categories=tuple(
            sorted(selections.values(), key=lambda selection: selection.category_name)
        ),
        expanded_category_ids=expanded,
        ambiguous_candidates=tuple(ambiguous),
    )
