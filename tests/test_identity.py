from philosophy_frontier_monitor.identity import (
    IdentityMatchLevel,
    authors_equivalent,
    compare_bibliographic_identity,
)


def test_author_identity_handles_diacritics_initials_and_name_order():
    assert authors_equivalent("García, María J.", ("Maria J Garcia",)) is True


def test_malformed_empty_surname_is_unknown_instead_of_crashing_or_matching():
    assert authors_equivalent(", Anonymous", ("Anonymous",)) is None


def test_title_identity_accepts_spelling_variant_with_compatible_author():
    decision = compare_bibliographic_identity(
        "Knowledge in Plato's Theaitetos",
        "García, María",
        "Knowledge in Plato's Theaetetus",
        ("Maria Garcia",),
    )

    assert decision.level is IdentityMatchLevel.EQUIVALENT
    assert "compatible_first_author" in decision.evidence


def test_title_identity_accepts_reordered_content_words_not_just_literal_name():
    decision = compare_bibliographic_identity(
        "Plato on Knowledge and Belief",
        "Smith, Alice",
        "Knowledge and Belief in Plato",
        ("Alice Smith",),
    )

    assert decision.level is IdentityMatchLevel.EQUIVALENT
    assert "same_title_content_tokens" in decision.evidence


def test_shorter_nearby_title_is_not_automatically_treated_as_same_work():
    decision = compare_bibliographic_identity(
        "Knowledge and Belief in Plato",
        "Smith, Alice",
        "Knowledge and Belief",
        ("Alice Smith",),
    )

    assert decision.level is not IdentityMatchLevel.EQUIVALENT


def test_cross_language_title_with_same_author_requires_semantic_review():
    decision = compare_bibliographic_identity(
        "Knowledge and Belief",
        "Wang, Xiaoming",
        "知识与信念",
        ("Xiaoming Wang",),
    )

    assert decision.level is IdentityMatchLevel.REVIEW_REQUIRED
    assert "different_title_scripts" in decision.evidence


def test_shared_identifier_overrides_title_rendering_but_not_author_conflict():
    equivalent = compare_bibliographic_identity(
        "Knowledge and Belief",
        "Wang, Xiaoming",
        "知识与信念",
        ("Xiaoming Wang",),
        shared_identifier=True,
    )
    conflict = compare_bibliographic_identity(
        "Knowledge and Belief",
        "Wang, Xiaoming",
        "知识与信念",
        ("Different Author",),
        shared_identifier=True,
    )

    assert equivalent.level is IdentityMatchLevel.EQUIVALENT
    assert conflict.level is IdentityMatchLevel.REVIEW_REQUIRED
