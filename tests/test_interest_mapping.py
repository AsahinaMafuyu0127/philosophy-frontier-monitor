from datetime import UTC, datetime

from philosophy_frontier_monitor.interest import build_interest_profile

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def selected_names(profile):
    return {selection.category_name for selection in profile.selected_categories}


def test_maps_chinese_plato_epistemology_and_explicit_dialogues(taxonomy):
    profile = build_interest_profile(
        "我研究柏拉图关于知识的学说，尤其关注《泰阿泰德》和《巴门尼德》。",
        taxonomy,
        now=NOW,
    )

    assert selected_names(profile) == {
        "Plato",
        "Plato: Epistemology",
        "Plato: Theaetetus",
        "Plato: Parmenides",
    }
    assert not profile.ambiguous_candidates


def test_bare_parmenides_is_not_silently_disambiguated(taxonomy):
    profile = build_interest_profile("我研究巴门尼德的存在论。", taxonomy, now=NOW)

    assert selected_names(profile) == set()
    assert len(profile.ambiguous_candidates) == 1
    assert set(profile.ambiguous_candidates[0].candidate_category_ids) == {
        "60928",
        "74915",
    }


def test_only_taxonomy_verified_names_can_be_selected(taxonomy):
    profile = build_interest_profile("我研究柏拉图的量子知识涟漪。", taxonomy, now=NOW)

    assert selected_names(profile) == {"Plato"}
