import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from philosophy_frontier_monitor.cli import _review_queue_path, build_parser
from philosophy_frontier_monitor.models import WorkTypeEvidence, WorkTypeStatus
from philosophy_frontier_monitor.pipeline import FeedSnapshot
from philosophy_frontier_monitor.review_feed_snapshots import (
    load_review_feed_snapshots,
    serialize_feed_snapshots,
)
from philosophy_frontier_monitor.sources.philpapers_rss import FeedEntry
from philosophy_frontier_monitor.work_type_reviews import (
    WorkTypeReviewError,
    load_work_type_reviews,
    work_type_evidence_fingerprint,
)
from philosophy_frontier_monitor.work_types import resolve_work_type_evidence


def _evidence():
    return (
        WorkTypeEvidence("philarchive-oai", "article", "article", "oai:example"),
        WorkTypeEvidence("openalex", "dataset", "dataset", "https://openalex.org/W1"),
    )


def test_review_fingerprint_is_order_independent_and_detects_changed_labels():
    evidence = _evidence()
    assert work_type_evidence_fingerprint(evidence) == work_type_evidence_fingerprint(
        tuple(reversed(evidence))
    )
    assert work_type_evidence_fingerprint(evidence) != work_type_evidence_fingerprint(
        (evidence[0], WorkTypeEvidence("openalex", "article", "article", "https://openalex.org/W1"))
    )


def test_private_document_review_requires_source_links_and_exact_evidence(tmp_path):
    evidence = _evidence()
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": [
                    {
                        "record_url": "https://philpapers.org/rec/EXAMPLE",
                        "title": "Example paper",
                        "work_type": "manuscript",
                        "evidence_fingerprint": work_type_evidence_fingerprint(evidence),
                        "evidence_urls": ["https://example.org/full-paper.pdf"],
                        "reviewed_at": "2026-10-10T09:00:00+08:00",
                        "note": "Inspected the complete manuscript and its title page.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    review = load_work_type_reviews(path)["https://philpapers.org/rec/EXAMPLE"]
    assert review.reviewed_at == datetime(2026, 10, 10, 1, tzinfo=UTC)
    assert review.applies_to(
        record_url=review.record_url, title="Example paper", evidence=evidence
    )
    assert not review.applies_to(
        record_url=review.record_url, title="Another paper", evidence=evidence
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_url", "https://evil.example/rec/EXAMPLE"),
        ("work_type", "article\n# injected"),
        ("evidence_urls", ["http://example.org/paper.pdf"]),
        ("reviewed_at", "2026-10-10"),
    ],
)
def test_invalid_review_entry_is_rejected(tmp_path, field, value):
    entry = {
        "record_url": "https://philpapers.org/rec/EXAMPLE",
        "title": "Example paper",
        "work_type": "article",
        "evidence_fingerprint": "a" * 64,
        "evidence_urls": ["https://example.org/full-paper.pdf"],
        "reviewed_at": "2026-10-10T09:00:00+08:00",
        "note": "Read the complete text.",
    }
    entry[field] = value
    path = tmp_path / "review.json"
    path.write_text(json.dumps({"schema_version": 1, "reviews": [entry]}), encoding="utf-8")
    with pytest.raises(WorkTypeReviewError):
        load_work_type_reviews(path)


def test_inspected_document_type_resolves_original_source_conflict():
    resolved = resolve_work_type_evidence(
        (*_evidence(), WorkTypeEvidence(
            "document-review",
            "manuscript",
            "manuscript",
            "https://example.org/full-paper.pdf",
            "document-inspection",
        ))
    )
    assert resolved.work_type == "manuscript"
    assert resolved.status is WorkTypeStatus.REVIEWED


def test_review_can_bind_two_observed_source_combinations(tmp_path):
    full = _evidence()
    partial = full[:1]
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps({"schema_version": 1, "reviews": [{
            "record_url": "https://philpapers.org/rec/EXAMPLE",
            "title": "Example paper",
            "work_type": "article",
            "evidence_fingerprint": work_type_evidence_fingerprint(full),
            "additional_evidence_fingerprints": [work_type_evidence_fingerprint(partial)],
            "evidence_urls": ["https://example.org/full-paper.pdf"],
            "reviewed_at": "2026-10-10T09:00:00+08:00",
            "note": "Inspected this article and both observed source sets.",
        }]}),
        encoding="utf-8",
    )
    review = load_work_type_reviews(path)["https://philpapers.org/rec/EXAMPLE"]
    assert review.applies_to(record_url=review.record_url, title=review.title, evidence=full)
    assert review.applies_to(record_url=review.record_url, title=review.title, evidence=partial)
    assert not review.applies_to(
        record_url=review.record_url,
        title=review.title,
        evidence=(WorkTypeEvidence("openalex", "book", "book", "W2"),),
    )


def test_review_files_stay_in_private_state_directory(tmp_path):
    config = SimpleNamespace(
        storage=SimpleNamespace(state_database=tmp_path / "private" / "state.db")
    )
    private_file = tmp_path / "private" / "reviews.json"
    assert _review_queue_path(str(private_file), config) == private_file.resolve()
    with pytest.raises(ValueError, match="private state directory"):
        _review_queue_path(str(tmp_path / "public.json"), config)
    with pytest.raises(ValueError, match="private state directory"):
        _review_queue_path(str(Path(tmp_path / "private" / "reviews.md")), config)


def test_pull_and_weekly_expose_two_pass_review_options():
    parser = build_parser()
    pull = parser.parse_args(
        [
            "pull-now", "--as-of", "2026-10-09T05:08:21+00:00",
            "--review-queue-file", "queue.json", "--review-queue-input", "first.json",
            "--type-reviews", "reviews.json",
        ]
    )
    weekly = parser.parse_args(
        [
            "weekly-run", "--dry-run", "--review-queue-file", "queue.json",
            "--type-reviews", "reviews.json",
        ]
    )
    assert pull.as_of == "2026-10-09T05:08:21+00:00"
    assert pull.type_reviews == weekly.type_reviews == "reviews.json"
    assert pull.review_queue_file == weekly.review_queue_file == "queue.json"
    assert pull.review_queue_input == "first.json"


def test_private_review_queue_preserves_exact_first_pass_feed_inventory(tmp_path):
    observed = datetime(2026, 10, 9, 5, 8, tzinfo=UTC)
    config = SimpleNamespace(feeds=(SimpleNamespace(
        feed_key="philpapers-rss:347",
        category_id="347",
        url="https://philpapers.org/browse/free-will/",
    ),))
    snapshot = FeedSnapshot(
        feed_key="philpapers-rss:347",
        category_id="347",
        category_url=config.feeds[0].url,
        checked_at=observed,
        content_hash="a" * 64,
        entries=(FeedEntry(
            "https://philpapers.org/rec/EXAMPLE",
            "Example paper",
            "https://philpapers.org/rec/EXAMPLE",
            "Working paper.",
            None,
        ),),
    )
    path = tmp_path / "queue.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "window_end": observed.isoformat(),
        "feed_snapshots": serialize_feed_snapshots((snapshot,)),
    }), encoding="utf-8")
    assert load_review_feed_snapshots(path, config, window_end=observed) == (snapshot,)
    with pytest.raises(ValueError, match="window"):
        load_review_feed_snapshots(path, config, window_end=datetime(2026, 10, 10, tzinfo=UTC))
