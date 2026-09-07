"""Evidence-based bibliographic identity comparisons.

The comparator is deliberately stronger than literal equality and weaker than
unbounded semantic inference.  It can establish high-confidence identity from
identifiers, orthographic variants, reordered title phrases, and compatible
author renderings.  Cross-language translations without a shared identifier
remain review candidates because a deterministic string comparator cannot
prove that two differently worded propositions have the same meaning.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from .normalize import normalize_title

TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "de",
        "der",
        "des",
        "die",
        "das",
        "el",
        "en",
        "et",
        "for",
        "in",
        "la",
        "le",
        "les",
        "of",
        "on",
        "the",
        "to",
        "und",
        "une",
        "un",
    }
)


class IdentityMatchLevel(StrEnum):
    EQUIVALENT = "equivalent"
    REVIEW_REQUIRED = "review_required"
    DISTINCT = "distinct"


class IdentityReviewRequired(RuntimeError):
    """Raised when a source result may be the same work but needs semantic review."""


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    level: IdentityMatchLevel
    reason: str
    evidence: tuple[str, ...]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return normalize_title(without_marks)


def _content_tokens(value: str) -> frozenset[str]:
    return frozenset(token for token in _fold(value).split() if token not in TITLE_STOPWORDS)


def _script_families(value: str) -> frozenset[str]:
    families: set[str] = set()
    for char in value:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        for family in ("LATIN", "GREEK", "CYRILLIC", "CJK", "HIRAGANA", "KATAKANA"):
            if family in name:
                families.add(family)
                break
        else:
            families.add("OTHER")
    return frozenset(families)


def _person_parts(value: str) -> tuple[frozenset[str], str, tuple[str, ...]]:
    folded = _fold(value)
    tokens = tuple(token for token in folded.split() if token)
    if not tokens:
        return frozenset(), "", ()
    raw = unicodedata.normalize("NFKC", value)
    if "," in raw:
        surname_tokens = _fold(raw.split(",", 1)[0]).split()
        surname = surname_tokens[-1] if surname_tokens else ""
        given = tokens[len(surname_tokens) :]
    else:
        surname = tokens[-1]
        given = tokens[:-1]
    initials = tuple(token[0] for token in given if token)
    return frozenset(tokens), surname, initials


def authors_equivalent(expected: str | None, candidates: tuple[str, ...]) -> bool | None:
    """Return True/False when author evidence is comparable, otherwise None."""

    if expected is None or not expected.strip() or not candidates:
        return None
    expected_tokens, expected_surname, expected_initials = _person_parts(expected)
    candidate_tokens, candidate_surname, candidate_initials = _person_parts(candidates[0])
    if not expected_tokens or not candidate_tokens:
        return None
    if not expected_surname or not candidate_surname:
        return None
    if expected_tokens == candidate_tokens:
        return True
    if expected_surname != candidate_surname:
        return False
    if not expected_initials or not candidate_initials:
        return True
    return expected_initials[0] == candidate_initials[0]


def compare_bibliographic_identity(
    expected_title: str,
    expected_author: str | None,
    candidate_title: str,
    candidate_authors: tuple[str, ...],
    *,
    shared_identifier: bool = False,
) -> IdentityDecision:
    """Compare two bibliographies using a hierarchy of identity evidence."""

    author_match = authors_equivalent(expected_author, candidate_authors)
    if shared_identifier:
        if author_match is False:
            return IdentityDecision(
                IdentityMatchLevel.REVIEW_REQUIRED,
                "共同标识符与作者冲突，不能自动合并。",
                ("shared_identifier", "author_conflict"),
            )
        return IdentityDecision(
            IdentityMatchLevel.EQUIVALENT,
            "共同稳定标识符足以确认同一作品，且没有作者冲突。",
            ("shared_identifier",),
        )

    left = _fold(expected_title)
    right = _fold(candidate_title)
    if not left or not right:
        return IdentityDecision(IdentityMatchLevel.DISTINCT, "题目为空，无法比较。", ())
    if left == right:
        if author_match is False:
            return IdentityDecision(
                IdentityMatchLevel.REVIEW_REQUIRED,
                "题目正字法一致，但首位作者冲突。",
                ("orthographic_title", "author_conflict"),
            )
        return IdentityDecision(
            IdentityMatchLevel.EQUIVALENT,
            "题目在大小写、标点和重音折叠后一致，且作者没有冲突。",
            ("orthographic_title",),
        )

    left_tokens = _content_tokens(expected_title)
    right_tokens = _content_tokens(candidate_title)
    union = left_tokens.union(right_tokens)
    token_score = len(left_tokens.intersection(right_tokens)) / len(union) if union else 0.0
    sequence_score = SequenceMatcher(None, left, right, autojunk=False).ratio()
    compatible_author = author_match is True
    enough_content = min(len(left_tokens), len(right_tokens)) >= 3

    if compatible_author and (sequence_score >= 0.92 or (enough_content and token_score >= 0.84)):
        evidence = ["compatible_first_author"]
        if sequence_score >= 0.92:
            evidence.append("high_title_sequence_similarity")
        if enough_content and token_score >= 0.84:
            evidence.append("same_title_content_tokens")
        return IdentityDecision(
            IdentityMatchLevel.EQUIVALENT,
            "作者相容，题目仅有高置信度拼写、词序或虚词差异。",
            tuple(evidence),
        )

    different_scripts = (
        bool(_script_families(expected_title))
        and bool(_script_families(candidate_title))
        and _script_families(expected_title).isdisjoint(_script_families(candidate_title))
    )
    partial_same_script_similarity = enough_content and (
        sequence_score >= 0.65 or token_score >= 0.5
    )
    if compatible_author and (different_scripts or partial_same_script_similarity):
        evidence = ["compatible_first_author"]
        if different_scripts:
            evidence.append("different_title_scripts")
        else:
            evidence.append("partial_title_similarity")
        return IdentityDecision(
            IdentityMatchLevel.REVIEW_REQUIRED,
            "作者相容且题目可能是改题、翻译或较大拼写变体，需要语义复核。",
            tuple(evidence),
        )

    return IdentityDecision(
        IdentityMatchLevel.DISTINCT,
        "没有形成足以确认同一作品的标识符、作者与题目证据组合。",
        (),
    )
