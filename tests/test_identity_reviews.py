"""Identity decisions stay attached to the exact external comparison."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest

from philosophy_frontier_monitor import cli, pipeline
from philosophy_frontier_monitor.config import load_watchlist
from philosophy_frontier_monitor.identity import IdentityComparison, IdentityReviewRequired
from philosophy_frontier_monitor.identity_reviews import (
    IdentityReviewError,
    ReviewedIdentity,
    identity_evidence_fingerprint,
    load_identity_reviews,
    resolve_identity_review,
)
from philosophy_frontier_monitor.models import DatePrecision, DateValue
from philosophy_frontier_monitor.search import SearchOptions, run_paper_search
from philosophy_frontier_monitor.sources.crossref import CrossrefWork
from philosophy_frontier_monitor.sources.crossref import find_exact_work as find_crossref
from philosophy_frontier_monitor.sources.openalex import OpenAlexWork
from philosophy_frontier_monitor.sources.openalex import find_exact_work as find_openalex
from philosophy_frontier_monitor.sources.philpapers_rss import FeedEntry
from philosophy_frontier_monitor.state import StateStore

RECORD_URL = "https://philpapers.org/rec/TEST"
TITLE = "Knowledge and Belief"
AUTHOR = "Wang, Xiaoming"
WHEN = datetime(2026, 10, 18, tzinfo=UTC)
COMPARISON = IdentityComparison(
    "Crossref",
    "10.1234/translation",
    "https://doi.org/10.1234/translation",
    "知识与信念",
    ("Xiaoming Wang",),
    "10.1234/translation",
    "2020-04-02",
    "Possible translation",
    ("compatible_first_author", "different_title_scripts"),
)
OLD_WORK = CrossrefWork(
    "10.1234/translation",
    "知识与信念",
    ("Xiaoming Wang",),
    None,
    "journal-article",
    COMPARISON.stable_url,
    DateValue(date(2020, 4, 2), DatePrecision.DAY, "crossref"),
    "recently_published_online",
    {},
)


def review(decision="equivalent", *, fingerprint=None):
    return ReviewedIdentity(
        RECORD_URL,
        "Crossref",
        COMPARISON.record_id,
        fingerprint or identity_evidence_fingerprint(RECORD_URL, TITLE, AUTHOR, COMPARISON),
        decision,
        (COMPARISON.stable_url,),
        WHEN,
        "Publisher confirms this relationship.",
    )


def exception(comparison=COMPARISON):
    return IdentityReviewRequired(
        "ambiguous",
        comparisons=(comparison,),
        ambiguous_works=(OLD_WORK,),
    )


def test_pair_decision_requires_current_external_facts():
    key = (RECORD_URL, "Crossref", COMPARISON.record_id)
    accepted, pending = resolve_identity_review(
        exception(),
        record_url=RECORD_URL,
        title=TITLE,
        author=AUTHOR,
        reviews={key: review()},
    )
    assert accepted == OLD_WORK and pending == ()
    changed = replace(COMPARISON, publication_date="2025-04-02")
    accepted, pending = resolve_identity_review(
        exception(changed),
        record_url=RECORD_URL,
        title=TITLE,
        author=AUTHOR,
        reviews={key: review()},
    )
    assert accepted is None and pending == (changed,)
    accepted, pending = resolve_identity_review(
        exception(),
        record_url=RECORD_URL,
        title=TITLE,
        author=AUTHOR,
        source_doi_hints=(COMPARISON.doi,),
        reviews={key: review()},
    )
    assert accepted is None and pending == (COMPARISON,)
    accepted, pending = resolve_identity_review(
        exception(),
        record_url=RECORD_URL,
        title=TITLE,
        author=AUTHOR,
        reviews={key: review("distinct")},
    )
    assert accepted is None and pending == ()


def test_all_ambiguous_external_records_need_decisions():
    second = replace(COMPARISON, record_id="10.1234/second", doi="10.1234/second")
    error = IdentityReviewRequired(
        "two possible versions",
        comparisons=(COMPARISON, second),
        ambiguous_works=(OLD_WORK, replace(OLD_WORK, doi="10.1234/second")),
    )
    accepted, pending = resolve_identity_review(
        error,
        record_url=RECORD_URL,
        title=TITLE,
        author=AUTHOR,
        reviews={(RECORD_URL, "Crossref", COMPARISON.record_id): review("distinct")},
    )
    assert accepted is None and pending == (second,)


def test_review_file_validation_and_private_queue(tmp_path):
    path = tmp_path / "identity-reviews.json"
    fields = {
        "record_url": RECORD_URL,
        "source": "Crossref",
        "external_record_id": COMPARISON.record_id,
        "evidence_fingerprint": identity_evidence_fingerprint(
            RECORD_URL, TITLE, AUTHOR, COMPARISON
        ),
        "decision": "equivalent",
        "evidence_urls": [COMPARISON.stable_url],
        "reviewed_at": WHEN.isoformat(),
        "note": "Publisher confirms the translated title.",
    }
    path.write_text(json.dumps({"schema_version": 1, "reviews": [fields]}), encoding="utf-8")
    assert (
        load_identity_reviews(path)[(RECORD_URL, "Crossref", COMPARISON.record_id)].decision
        == "equivalent"
    )
    fields["decision"] = "assume_new"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [fields]}), encoding="utf-8")
    with pytest.raises(IdentityReviewError):
        load_identity_reviews(path)
    for mode in ("weekly-run", "pull-now", "search"):
        assert (
            "--identity-reviews"
            in cli.build_parser()._subparsers._group_actions[0].choices[mode].format_help()
        )


@pytest.mark.parametrize("source", ["crossref", "openalex"])
def test_source_exception_preserves_exact_comparison(source):
    if source == "crossref":
        payload = {
            "message": {
                "items": [
                    {
                        "DOI": COMPARISON.record_id,
                        "title": [COMPARISON.title],
                        "author": [{"given": "Xiaoming", "family": "Wang"}],
                        "published-online": {"date-parts": [[2020, 4, 2]]},
                    }
                ]
            }
        }
        lookup = find_crossref
    else:
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "doi": "https://doi.org/10.1234/translation",
                    "title": COMPARISON.title,
                    "publication_date": "2020-04-02",
                    "authorships": [{"author": {"display_name": "Xiaoming Wang"}}],
                    "primary_location": {"landing_page_url": "https://example.org/work"},
                }
            ]
        }
        lookup = find_openalex
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload, request=request)
            )
        ) as client,
        pytest.raises(IdentityReviewRequired) as raised,
    ):
        lookup(TITLE, first_author=AUTHOR, client=client)
    item = raised.value.comparisons[0]
    assert item.source == ("Crossref" if source == "crossref" else "OpenAlex")
    assert item.title == COMPARISON.title
    assert item.authors == ("Xiaoming Wang",)
    assert item.doi == COMPARISON.doi
    assert item.publication_date == "2020-04-02"
    assert len(raised.value.ambiguous_works) == 1


def test_weekly_resolver_keeps_old_publication_gate_after_identity_review(monkeypatch):
    config = load_watchlist("tests/fixtures/watchlist_minimal.yaml")
    feed = config.feeds[0]
    entry = FeedEntry(RECORD_URL, f"{AUTHOR}: {TITLE}", RECORD_URL, "2026.", None)
    snapshot = pipeline.FeedSnapshot(
        feed.feed_key, feed.category_id, feed.url, WHEN, "fixture", (entry,)
    )
    candidate = pipeline.merge_feed_snapshots((snapshot,))[0]
    taxonomy = pipeline.load_taxonomy(config.taxonomy_path)
    monkeypatch.setattr(
        pipeline, "find_crossref_work", lambda *a, **kw: (_ for _ in ()).throw(exception())
    )
    monkeypatch.setattr(pipeline, "find_openalex_work", lambda *a, **kw: None)
    start = datetime(2026, 10, 11, tzinfo=UTC)
    first = pipeline.resolve_bibliography(candidate, taxonomy, config, start, WHEN, WHEN)
    assert first.reason_code == "semantic_identity_review_required"
    assert first.identity_comparisons == (COMPARISON,)
    second = pipeline.resolve_bibliography(
        candidate,
        taxonomy,
        config,
        start,
        WHEN,
        WHEN,
        identity_reviews={(RECORD_URL, "Crossref", COMPARISON.record_id): review()},
    )
    assert second.reason_code != "semantic_identity_review_required"
    assert second.work is not None
    assert second.work.publication_date.value == date(2020, 4, 2)


def test_search_queue_has_comparator_and_reviewed_old_work_stays_out_of_year(tmp_path):
    config = load_watchlist("tests/fixtures/watchlist_minimal.yaml")
    entry = FeedEntry(RECORD_URL, f"{AUTHOR}: {TITLE}", RECORD_URL, "2026.", None)

    def feed_loader(feed, checked_at):
        return pipeline.FeedSnapshot(
            feed.feed_key, feed.category_id, feed.url, checked_at, "fixture", (entry,)
        )

    def fallback(*args, **kwargs):
        raise exception()

    options = SearchOptions(year_from=2026, year_to=2026)
    common = dict(
        options=options,
        now=WHEN,
        feed_loader=feed_loader,
        doi_lookup=lambda *a, **kw: {},
        title_lookup=lambda *a, **kw: (),
        fallback_lookup=fallback,
        allow_development_fixture=True,
    )
    first = run_paper_search(config, **common)
    assert first.total_matches == 0
    unresolved = first.unconfirmed_records[0]
    assert unresolved.identity_comparisons == (COMPARISON,)
    assert unresolved.identity_fingerprints == (
        identity_evidence_fingerprint(RECORD_URL, TITLE, AUTHOR, COMPARISON),
    )
    queue = tmp_path / "queue.json"
    cli._write_search_review_queue(queue, first)
    saved = json.loads(queue.read_text(encoding="utf-8"))
    pair = next(item for item in saved["items"] if item["reason"] == "identity_unverified")
    assert pair["identity_review_pairs"][0]["record_id"] == COMPARISON.record_id
    assert (
        pair["identity_review_pairs"][0]["evidence_fingerprint"]
        == unresolved.identity_fingerprints[0]
    )
    second = run_paper_search(
        config,
        **common,
        identity_reviews={(RECORD_URL, "Crossref", COMPARISON.record_id): review()},
    )
    assert second.total_matches == 0
    assert second.stats["year_unknown"] == 0
    assert second.stats["identity_unverified"] == 0


def test_search_openalex_batch_ambiguity_needs_pair_review():
    config = load_watchlist("tests/fixtures/watchlist_minimal.yaml")
    entry = FeedEntry(RECORD_URL, f"{AUTHOR}: {TITLE}", RECORD_URL, "2026.", None)

    def loader(feed, checked_at):
        return pipeline.FeedSnapshot(
            feed.feed_key, feed.category_id, feed.url, checked_at, "fixture", (entry,)
        )

    work = OpenAlexWork(
        "https://openalex.org/W123",
        None,
        "知识与信念",
        ("Xiaoming Wang",),
        DateValue(date(2026, 4, 2), DatePrecision.DAY, "openalex"),
        "article",
        "https://openalex.org/W123",
        {},
    )
    common = dict(
        options=SearchOptions(year_from=2026, year_to=2026),
        now=WHEN,
        feed_loader=loader,
        doi_lookup=lambda *a, **kw: {},
        title_lookup=lambda *a, **kw: (work,),
        fallback_lookup=lambda *a, **kw: None,
        allow_development_fixture=True,
    )
    first = run_paper_search(config, **common)
    assert first.total_matches == 0
    comparison = first.unconfirmed_records[0].identity_comparisons[0]
    assert comparison.record_id == work.openalex_id
    fingerprint = identity_evidence_fingerprint(RECORD_URL, TITLE, AUTHOR, comparison)
    accepted = ReviewedIdentity(
        RECORD_URL,
        "OpenAlex",
        work.openalex_id,
        fingerprint,
        "equivalent",
        (work.stable_url,),
        WHEN,
        "The publisher identifies the translated title.",
    )
    second = run_paper_search(
        config, **common, identity_reviews={(RECORD_URL, "OpenAlex", work.openalex_id): accepted}
    )
    assert second.total_matches == 1
    assert second.items[0].publication_year == 2026


def test_weekly_and_pull_now_save_the_same_external_comparison(monkeypatch, tmp_path):
    config = load_watchlist("tests/fixtures/watchlist_minimal.yaml")
    entry = FeedEntry(
        RECORD_URL,
        f"{AUTHOR}: {TITLE}",
        RECORD_URL,
        "2026.",
        "Sat, 17 Oct 2026 12:00:00 GMT",
    )

    def loader(feed, checked_at):
        return pipeline.FeedSnapshot(
            feed.feed_key, feed.category_id, feed.url, checked_at, "fixture", (entry,)
        )

    def empty_loader(feed, checked_at):
        return pipeline.FeedSnapshot(
            feed.feed_key, feed.category_id, feed.url, checked_at, "empty", ()
        )

    monkeypatch.setattr(
        pipeline, "find_crossref_work", lambda *a, **kw: (_ for _ in ()).throw(exception())
    )
    monkeypatch.setattr(pipeline, "find_openalex_work", lambda *a, **kw: None)
    decisions = {(RECORD_URL, "Crossref", COMPARISON.record_id): review()}
    with StateStore(":memory:") as state:
        pipeline.establish_baseline(
            config,
            state,
            now=datetime(2026, 10, 10, tzinfo=UTC),
            feed_loader=empty_loader,
            allow_development_fixture=True,
        )
        weekly = pipeline.run_weekly(
            config,
            state,
            now=WHEN,
            window_start=datetime(2026, 10, 11, tzinfo=UTC),
            window_end=WHEN,
            dry_run=True,
            feed_loader=loader,
        )
        reviewed_weekly = pipeline.run_weekly(
            config,
            state,
            now=WHEN,
            window_start=datetime(2026, 10, 11, tzinfo=UTC),
            window_end=WHEN,
            dry_run=True,
            feed_loader=loader,
            identity_reviews=decisions,
        )
    assert weekly.human_review_items[0].identity_comparisons == (COMPARISON,)
    assert reviewed_weekly.human_review_items == ()
    assert reviewed_weekly.stats["matched"] == 0
    weekly_queue = tmp_path / "weekly.json"
    cli._write_review_queue(weekly_queue, weekly)
    assert (
        json.loads(weekly_queue.read_text(encoding="utf-8"))["items"][0]["identity_review_pairs"][
            0
        ]["record_id"]
        == COMPARISON.record_id
    )

    pulled = pipeline.run_on_demand(
        config,
        now=WHEN,
        feed_loader=loader,
        batch_doi_resolver=lambda *a: {},
        batch_title_resolver=lambda *a: (),
        allow_development_fixture=True,
    )
    reviewed_pull = pipeline.run_on_demand(
        config,
        now=WHEN,
        feed_loader=loader,
        batch_doi_resolver=lambda *a: {},
        batch_title_resolver=lambda *a: (),
        allow_development_fixture=True,
        identity_reviews=decisions,
    )
    assert pulled.human_review_items[0].identity_comparisons == (COMPARISON,)
    assert reviewed_pull.human_review_items == ()
    assert reviewed_pull.stats["matched"] == 0
    pull_queue = tmp_path / "pull.json"
    cli._write_review_queue(pull_queue, pulled)
    assert json.loads(pull_queue.read_text(encoding="utf-8"))["items"][0]["identity_fingerprints"][
        0
    ] == identity_evidence_fingerprint(RECORD_URL, TITLE, AUTHOR, COMPARISON)
