from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from philosophy_frontier_monitor import cli
from philosophy_frontier_monitor.config import ConfirmedCategoryConfig, FeedConfig, load_watchlist
from philosophy_frontier_monitor.identity import IdentityReviewRequired
from philosophy_frontier_monitor.models import DatePrecision, DateValue
from philosophy_frontier_monitor.pipeline import FeedSnapshot, PipelineError
from philosophy_frontier_monitor.search import SearchOptions, run_paper_search
from philosophy_frontier_monitor.search_report import render_paper_search
from philosophy_frontier_monitor.sources.crossref import CrossrefWork
from philosophy_frontier_monitor.sources.openalex import (
    OpenAlexError,
    OpenAlexWork,
    find_works_by_dois,
    find_works_by_titles,
)
from philosophy_frontier_monitor.sources.philpapers_rss import FeedEntry

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = load_watchlist(FIXTURES / "watchlist_minimal.yaml")
NOW = datetime(2026, 9, 14, tzinfo=UTC)


def entry(key, title=None, description="2024."):
    return FeedEntry(
        source_id=f"https://philpapers.org/rec/{key}",
        title=f"Scholar, Ada: {title or key}",
        link=f"https://philpapers.org/rec/{key}",
        description=description,
        published_text=None,
    )


def oa(title, count=10, kind="article", year=1990, key=None, doi=None):
    return OpenAlexWork(
        openalex_id=f"https://openalex.org/{key or title}",
        doi=doi,
        title=title,
        authors=("Ada Scholar",),
        work_type=kind,
        publication_date=DateValue(date(year, 1, 1), DatePrecision.DAY, "openalex")
        if year
        else None,
        stable_url=f"https://openalex.org/{key or title}",
        raw={"cited_by_count": count},
    )


def run(entries, works=(), *, config=CONFIG, options=None, **kwargs):
    def loader(feed, checked_at):
        selected = entries.get(feed.category_id, ()) if isinstance(entries, dict) else entries
        return FeedSnapshot(feed.feed_key, feed.category_id, feed.url, checked_at, "hash", selected)

    return run_paper_search(
        config,
        options=options,
        now=NOW,
        feed_loader=loader,
        doi_lookup=kwargs.pop("doi_lookup", lambda *a, **k: {}),
        title_lookup=kwargs.pop("title_lookup", lambda *a, **k: works),
        fallback_lookup=kwargs.pop("fallback_lookup", lambda *a, **k: None),
        allow_development_fixture=True,
        **kwargs,
    )


def two_category_config():
    return replace(
        CONFIG,
        confirmed_categories=(
            *CONFIG.confirmed_categories,
            ConfirmedCategoryConfig("74915", "Plato: Parmenides", False),
        ),
        feeds=(
            *CONFIG.feeds,
            FeedConfig(
                "74915", "Plato: Parmenides", "https://philpapers.org/browse/plato-parmenides"
            ),
        ),
    )


def test_historical_papers_sorted_and_missing_is_not_zero():
    result = run(
        tuple(entry(key) for key in ("Missing", "Zero", "High", "Low")),
        (oa("Missing", None), oa("Zero", 0), oa("High", 50), oa("Low", 3)),
    )
    assert [item.title for item in result.items] == ["High", "Low", "Zero", "Missing"]
    assert all(item.publication_year == 1990 for item in result.items)
    assert result.stats["citation_missing"] == 1
    assert result.items[-1].citation_source is None
    assert result.items[0].citation_retrieved_at == NOW
    assert result.state_mutated is False


@pytest.mark.parametrize("count", [None, -1, True, False, 2.5, "18", {}, []])
def test_malformed_citation_is_unknown(count):
    assert oa("Paper", count).cited_by_count is None


@pytest.mark.parametrize(
    "kind",
    [
        "book",
        "book-chapter",
        "book-review",
        "dissertation",
        "reference-entry",
        "dataset",
        "other",
        None,
        "unrecognized",
    ],
)
def test_nonpapers_and_unknown_types_never_enter_results(kind):
    result = run((entry("Paper"),), (oa("Paper", kind=kind),))
    assert result.total_matches == 0
    assert result.stats["unsupported_type"] + result.stats["type_unverified"] == 1


def test_native_paper_with_no_citations_survives_source_failure():
    def unavailable(*args, **kwargs):
        raise OpenAlexError("source error must not expose a query or credential")

    result = run(
        (entry("Paper", description="Unpublished manuscript. 2005."),), title_lookup=unavailable
    )
    assert result.total_matches == 1
    assert result.items[0].citation_count is None
    assert result.items[0].work_type == "manuscript"
    assert result.source_failures
    assert "credential" not in str(result.source_failures)
    assert result.stats["fallback_requests"] == 0


def test_crossref_fallback_retains_paper_when_openalex_has_no_record():
    work = CrossrefWork(
        "10.1234/test",
        "Paper",
        ("Ada Scholar",),
        "Journal",
        "journal-article",
        "https://doi.org/10.1234/test",
        None,
        None,
        {},
    )
    result = run((entry("Paper"),), fallback_lookup=lambda *a, **k: work)
    assert result.total_matches == 1
    assert result.items[0].citation_count is None
    assert result.items[0].doi == "10.1234/test"


@pytest.mark.parametrize("missing", ["type", "year"])
def test_crossref_fills_incomplete_openalex_metadata_without_losing_citations(missing):
    work = CrossrefWork(
        "10.1234/test",
        "Paper",
        ("Ada Scholar",),
        "Journal",
        "journal-article",
        "https://doi.org/10.1234/test",
        DateValue(date(2015, 1, 1), DatePrecision.DAY, "crossref"),
        None,
        {},
    )
    primary = oa(
        "Paper",
        count=15,
        doi="10.1234/test",
        kind=None if missing == "type" else "article",
        year=None if missing == "year" else 2015,
    )
    result = run(
        (entry("Paper"),),
        (primary,),
        options=SearchOptions(year_from=2000),
        fallback_lookup=lambda *a, **k: work,
    )
    assert result.total_matches == 1
    assert result.items[0].citation_count == 15
    assert result.items[0].publication_year == 2015
    assert result.stats["fallback_requests"] == 1


def test_crossref_type_conflict_cannot_be_hidden_by_existing_citation_count():
    work = CrossrefWork(
        "10.1234/test",
        "Paper",
        ("Ada Scholar",),
        None,
        "book",
        "https://doi.org/10.1234/test",
        DateValue(date(2015, 1, 1), DatePrecision.DAY, "crossref"),
        None,
        {},
    )
    result = run(
        (entry("Paper"),),
        (oa("Paper", year=None, doi="10.1234/test"),),
        options=SearchOptions(year_from=2000),
        fallback_lookup=lambda *a, **k: work,
    )
    assert result.total_matches == 0
    assert result.stats["type_unverified"] == 1


def test_year_hint_in_description_does_not_become_publication_date():
    result = run((entry("Paper", description="Manuscript. Discusses an edition from 2005."),))
    assert result.total_matches == 1
    assert result.items[0].publication_year is None


def test_shared_identifier_with_conflicting_types_is_withheld_after_merging():
    entries = (
        entry("A", "First title", "2024. https://doi.org/10.1234/test"),
        entry("B", "Second title", "2024. https://doi.org/10.1234/test"),
    )

    def fallback(title, **kwargs):
        return CrossrefWork(
            "10.1234/test",
            title,
            ("Ada Scholar",),
            None,
            "book" if title == "First title" else "journal-article",
            "https://doi.org/10.1234/test",
            None,
            None,
            {},
        )

    result = run(entries, fallback_lookup=fallback)
    assert result.total_matches == 0
    assert result.stats["type_unverified"] == 1
    assert result.unconfirmed_records[0].reason == "type_unverified"
    assert len(result.unconfirmed_records[0].source_urls) == 2


def test_fallback_budget_is_disclosed_and_unknown_not_defaulted():
    result = run((entry("A"), entry("B")), options=SearchOptions(max_fallbacks=0))
    assert result.total_matches == 0
    assert result.stats["fallback_not_reached"] == 2
    assert result.stats["type_unverified"] == 2


def test_explicit_review_does_not_use_bibliographic_requests():
    def unexpected(*a, **k):
        pytest.fail("explicit book review should be rejected before lookup")

    result = run((entry("Review", "Review of Plato"),), title_lookup=unexpected)
    assert result.total_matches == 0
    assert result.stats["unsupported_type"] == 1


def test_all_labels_use_merged_membership_and_any_is_union():
    config = two_category_config()
    entries = {"74924": (entry("Both"), entry("One")), "74915": (entry("Both"),)}
    works = (oa("Both"), oa("One"))
    result = run(entries, works, config=config, options=SearchOptions(match="all"))
    assert [item.title for item in result.items] == ["Both"]
    assert set(result.items[0].matched_category_ids) == {"74924", "74915"}
    assert run(entries, works, config=config).total_matches == 2


def test_temporary_category_and_exclusion_do_not_change_configuration():
    config = replace(two_category_config(), excluded_category_ids=frozenset({"74915"}))
    entries = {"74924": (entry("Both"), entry("One")), "74915": (entry("Both"),)}
    result = run(
        entries,
        (oa("Both"), oa("One")),
        config=config,
        options=SearchOptions(category_ids=("74924",)),
    )
    assert [item.title for item in result.items] == ["One"]
    assert result.stats["excluded_category"] == 1
    assert len(config.confirmed_categories) == 2


def test_parent_expansion_is_one_group_for_all_match():
    ids = ("74810", "74844")
    names = ("Plato: Epistemology", "Plato: Knowledge and Belief")
    config = replace(
        CONFIG,
        confirmed_categories=(ConfirmedCategoryConfig(ids[0], names[0], True),),
        feeds=tuple(
            FeedConfig(cid, name, f"https://philpapers.org/browse/{cid}")
            for cid, name in zip(ids, names, strict=True)
        ),
    )
    result = run(
        {"74844": (entry("Paper"),)},
        (oa("Paper"),),
        config=config,
        options=SearchOptions(match="all"),
    )
    assert result.total_matches == 1
    assert result.items[0].matched_category_ids == ("74844",)


def test_shared_doi_merges_records_before_all_tag_filter():
    config = two_category_config()
    entries = {"74924": (entry("A", "Paper"),), "74915": (entry("B", "Paper"),)}
    result = run(
        entries,
        (oa("Paper", doi="10.1234/paper"),),
        config=config,
        options=SearchOptions(match="all"),
    )
    assert result.total_matches == 1
    assert len(result.items[0].source_urls) == 2
    assert result.items[0].citation_count == 10  # never sum duplicate counts


def test_ambiguous_openalex_versions_withheld_instead_of_highest_citation():
    result = run((entry("Paper"),), (oa("Paper", 100, key="W1"), oa("Paper", 5, key="W2")))
    assert result.total_matches == 0
    assert result.stats["identity_unverified"] == 1


def test_conflicting_feed_dois_stay_unconfirmed_even_with_one_openalex_match():
    entries = {
        "74924": (entry("Paper", description="2024. https://doi.org/10.1234/first"),),
        "74915": (entry("Paper", description="2024. https://doi.org/10.1234/second"),),
    }
    result = run(
        entries,
        (oa("Paper", doi="10.1234/first"),),
        config=two_category_config(),
    )
    assert result.total_matches == 0
    assert result.unconfirmed_records[0].reason == "identity_unverified"
    assert result.stats["fallback_requests"] == 0


def test_unrelated_lookup_cannot_supply_citations_or_paper_type():
    result = run(
        (entry("Paper", description="2024. https://doi.org/10.1234/right"),),
        (oa("Paper", doi="10.1234/wrong"),),
    )
    assert result.total_matches == 0


def test_year_type_filters_and_result_pagination():
    works = (
        oa("Old", 900, year=1980),
        oa("New", 2, year=2020),
        oa("Unknown", 20, year=None),
        oa("Review", 4, kind="review", year=2021),
        oa("Third", 0, year=2022),
    )
    entries = tuple(entry(w.title) for w in works)
    options = SearchOptions(year_from=2000, work_types=("article",), limit=1)
    result = run(entries, works, options=options)
    assert result.total_matches == 2
    assert result.items[0].title == "New"
    assert result.next_offset == 1
    assert result.stats["year_unknown"] == 1
    following = run(entries, works, options=replace(options, offset=1))
    assert following.items[0].title == "Third"
    assert following.next_offset is None


def test_candidate_limit_fails_before_remote_metadata_not_silent_truncation():
    def unexpected(*a, **k):
        pytest.fail("metadata lookup must not start")

    with pytest.raises(PipelineError, match="2 candidates"):
        run(
            (entry("A"), entry("B")),
            options=SearchOptions(max_candidates=1),
            title_lookup=unexpected,
        )


@pytest.mark.parametrize("bounds", [{"year_from": 2000}, {"year_to": 2026}])
def test_no_feed_year_hint_is_final_exclusion_before_all_metadata_and_budget(bounds):
    def unexpected(*args, **kwargs):
        pytest.fail("undated feed records must not cause bibliographic lookups")

    entries = (
        entry("Plain", description=""),
        entry("DOI", description="https://doi.org/10.1234/paper"),
        entry("Manuscript", description="Unpublished manuscript."),
        replace(entry("FeedDate", description=""), published_text="Mon, 14 Sep 2026 00:00:00 GMT"),
    )
    result = run(
        entries,
        options=SearchOptions(max_candidates=1, **bounds),
        doi_lookup=unexpected,
        title_lookup=unexpected,
        fallback_lookup=unexpected,
    )
    assert result.total_matches == 0
    assert result.stats["no_feed_year_hint"] == len(entries)
    assert result.stats["fallback_requests"] == 0
    assert result.stats["type_unverified"] == result.stats["identity_unverified"] == 0
    assert result.unconfirmed_records == ()
    report = render_paper_search(result)
    assert "尚未确认的记录" not in report
    assert "<details>" not in report


def test_year_hint_gate_uses_merged_feed_evidence():
    result = run(
        {"74924": (entry("Paper", description=""),), "74915": (entry("Paper"),)},
        (oa("Paper"),),
        config=two_category_config(),
        options=SearchOptions(match="all", year_from=1980),
    )
    assert result.total_matches == 1
    assert result.stats["no_feed_year_hint"] == 0


def test_unbounded_tag_search_includes_all_undated_papers_beyond_default_page_size():
    entries = tuple(
        entry(f"PAPER{i}", f"Study of {chr(65 + i) * 8}", description="") for i in range(25)
    )
    works = tuple(oa(f"Study of {chr(65 + i) * 8}", count=i, year=None) for i in range(25))
    result = run(entries, works)
    assert result.total_matches == len(result.items) == 25
    assert result.next_offset is None
    assert result.items[0].citation_count == 24
    assert all(item.publication_year is None for item in result.items)
    assert result.stats["no_feed_year_hint"] == result.stats["fallback_requests"] == 0
    report = render_paper_search(result)
    assert "本次不限制年份" in report
    assert "没有 feed 年份提示的记录直接排除" not in report


def test_unbounded_search_retains_undated_manuscript_but_still_excludes_nonpapers():
    result = run(
        (
            entry("Native", description="Unpublished manuscript."),
            entry("Book", description=""),
            entry("Unverified", description=""),
        ),
        (oa("Book", kind="book"),),
        options=SearchOptions(max_fallbacks=0),
    )
    assert [item.title for item in result.items] == ["Native"]
    assert result.items[0].publication_year is None
    assert result.items[0].citation_count is None
    assert result.stats["unsupported_type"] == 1
    assert [record.title for record in result.unconfirmed_records] == ["Unverified"]


def test_unbounded_search_respects_explicit_pagination_and_bounded_default_is_preserved():
    entries = tuple(entry(f"PAPER{i}", f"Study of {chr(65 + i) * 8}") for i in range(25))
    works = tuple(oa(f"Study of {chr(65 + i) * 8}", count=i, year=2024) for i in range(25))
    page = run(entries, works, options=SearchOptions(limit=7, offset=3))
    assert len(page.items) == 7
    assert page.items[0].citation_count == 21
    assert page.total_matches == 25
    assert page.next_offset == 10
    bounded = run(entries, works, options=SearchOptions(year_from=2020))
    assert len(bounded.items) == bounded.next_offset == 20
    assert bounded.total_matches == 25


def test_unconfirmed_report_mentions_existence_without_diagnostic_counts():
    result = run(
        (entry("Unknown"), entry("Ambiguous"), entry("Confirmed")),
        (oa("Ambiguous", key="W1"), oa("Ambiguous", key="W2"), oa("Confirmed", count=17)),
        options=SearchOptions(max_fallbacks=0),
    )
    assert {record.reason for record in result.unconfirmed_records} == {
        "type_unverified",
        "identity_unverified",
    }
    assert {record.title for record in result.unconfirmed_records} == {"Unknown", "Ambiguous"}
    stats = {key: 123456789 for key in result.stats}
    stats.update(citation_available=1, citation_missing=0)
    report = render_paper_search(replace(result, stats=stats))
    assert "123456789" not in report
    assert "存在论文类型或书目身份尚未确认的记录" in report
    assert "自行核对" in report
    assert "不再为这些记录继续补查或重新运行检索" in report
    assert "https://philpapers.org/rec/Unknown" in report
    assert "https://philpapers.org/rec/Ambiguous" in report
    assert "引用量：17" in report
    assert "共 **1** 篇" in report


@pytest.mark.parametrize("mode", ["mismatch", "review_required"])
def test_crossref_identity_failure_keeps_existing_source_for_optional_review(mode):
    def fallback(*args, **kwargs):
        if mode == "review_required":
            raise IdentityReviewRequired("ambiguous")
        return CrossrefWork(
            "10.1234/other",
            "Unrelated",
            ("Someone Else",),
            None,
            "journal-article",
            "https://doi.org/10.1234/other",
            None,
            None,
            {},
        )

    result = run((entry("Paper"),), fallback_lookup=fallback)
    assert result.total_matches == 0
    assert result.unconfirmed_records[0].reason == "identity_unverified"
    assert result.unconfirmed_records[0].source_urls == ("https://philpapers.org/rec/Paper",)
    assert result.stats["fallback_requests"] == 1


def test_unconfirmed_source_title_is_inert_and_not_a_confirmed_paper():
    title = "<script>alert(1)</script> [fake](https://bad.example)"
    result = run((entry("Unverified", title),), options=SearchOptions(max_fallbacks=0))
    report = render_paper_search(result)
    assert result.total_matches == 0
    assert "<script>" not in report
    assert "[fake](https://bad.example)" not in report
    assert "https://philpapers.org/rec/Unverified" in report


@pytest.mark.parametrize(
    "options",
    [
        SearchOptions(year_from=2025, year_to=2000),
        SearchOptions(work_types=("book",)),
        SearchOptions(limit=0),
        SearchOptions(offset=-1),
        SearchOptions(category_ids=("74915",)),
    ],
)
def test_invalid_search_fails_before_feeds(options):
    def unexpected(*a):
        pytest.fail("feed lookup must not start")

    with pytest.raises(ValueError):
        run_paper_search(
            CONFIG, options=options, feed_loader=unexpected, allow_development_fixture=True
        )


def test_feed_failure_does_not_claim_empty_results():
    def unavailable(*a):
        raise PipelineError("source unavailable")

    with pytest.raises(PipelineError, match="source unavailable"):
        run_paper_search(CONFIG, feed_loader=unavailable, allow_development_fixture=True)


def test_report_shows_missing_scope_and_inert_remote_title():
    title = "<script>alert(1)</script> [fake](https://bad.example)"
    result = run((entry("A", title),), (oa(title, None),))
    report = render_paper_search(result)
    assert "<script>" not in report
    assert "引用量：缺失" in report
    assert "不保证覆盖完整历史索引" in report


def test_openalex_batch_requests_citations_for_both_lookup_routes():
    calls = []

    def handler(request):
        calls.append(request)
        assert "cited_by_count" in request.url.params["select"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Paper",
                        "doi": "10.1234/paper",
                        "type": "article",
                        "primary_location": None,
                        "cited_by_count": 12,
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        by_doi = find_works_by_dois(("10.1234/paper",), client=client)
        by_title = find_works_by_titles(("Paper",), client=client)
    assert by_doi["10.1234/paper"].cited_by_count == by_title[0].cited_by_count == 12
    assert len(calls) == 2


@pytest.mark.parametrize("arguments, expected_size", [([], 25), (["--limit", "1"], 1)])
def test_cli_structured_result_and_no_files_written(
    monkeypatch, capsys, workspace_tmp_path, arguments, expected_size
):
    config = replace(
        CONFIG,
        storage=replace(CONFIG.storage, state_database=workspace_tmp_path / "absent.sqlite3"),
    )
    entries = tuple(
        entry(f"PAPER{i}", f"Study of {chr(65 + i) * 8}", description="") for i in range(25)
    )
    works = tuple(oa(f"Study of {chr(65 + i) * 8}", count=i, year=None) for i in range(25))
    monkeypatch.setattr(cli, "load_watchlist", lambda path: config)
    monkeypatch.setattr(
        cli,
        "run_paper_search",
        lambda config, options, **kwargs: run(entries, works, config=config, options=options),
    )
    assert cli.main(["search", *arguments]) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["items"]) == expected_size
    assert payload["items"][0]["citation_count"] == 24
    assert payload["weekly_notifications_written"] is False
    assert list(workspace_tmp_path.iterdir()) == []
