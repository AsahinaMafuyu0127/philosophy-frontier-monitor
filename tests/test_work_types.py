from philosophy_frontier_monitor.models import WorkTypeStatus
from philosophy_frontier_monitor.work_types import (
    WorkTypeSignal,
    is_supported_work_type,
    resolve_work_type,
)


def signal(source: str, raw_type: str) -> WorkTypeSignal:
    return WorkTypeSignal(source=source, raw_type=raw_type, source_record_id="fixture")


def test_crossref_and_openalex_article_labels_normalize_without_conflict():
    result = resolve_work_type(
        (
            signal("crossref", "journal-article"),
            signal("openalex", "article"),
        )
    )

    assert result.work_type == "article"
    assert result.status is WorkTypeStatus.CONFIRMED
    assert result.usable is True


def test_openalex_distinguishes_review_article_from_book_review():
    review_article = resolve_work_type((signal("openalex", "review"),))
    book_review = resolve_work_type((signal("openalex", "book-review"),))

    assert review_article.work_type == "review-article"
    assert is_supported_work_type(review_article.work_type) is True
    assert book_review.work_type == "book-review"
    assert is_supported_work_type(book_review.work_type) is False


def test_philarchive_oai_controlled_uri_is_structured_type_evidence():
    article = resolve_work_type((signal("philarchive-oai", "info:eu-repo/semantics/article"),))
    book = resolve_work_type((signal("philarchive-oai", "info:eu-repo/semantics/book"),))

    assert article.work_type == "article"
    assert article.status is WorkTypeStatus.CONFIRMED
    assert book.work_type == "book"
    assert is_supported_work_type(book.work_type) is False


def test_philarchive_oai_review_stays_unknown_without_subtype_evidence():
    result = resolve_work_type((signal("philarchive-oai", "info:eu-repo/semantics/review"),))

    assert result.work_type == "unknown"
    assert result.status is WorkTypeStatus.UNKNOWN


def test_equivalent_preprint_vocabularies_are_compatible():
    result = resolve_work_type(
        (
            signal("crossref", "posted-content"),
            signal("openalex", "preprint"),
        )
    )

    assert result.work_type == "preprint"
    assert result.status is WorkTypeStatus.CONFIRMED
    assert result.usable is True


def test_supported_and_unsupported_structured_types_fail_closed():
    result = resolve_work_type(
        (
            signal("crossref", "journal-article"),
            signal("openalex", "book-chapter"),
        )
    )

    assert result.work_type == "conflict"
    assert result.status is WorkTypeStatus.CONFLICT
    assert result.usable is False


def test_unknown_structured_type_is_not_defaulted_to_article():
    result = resolve_work_type((signal("openalex", "future-unknown-type"),))

    assert result.work_type == "unknown"
    assert result.status is WorkTypeStatus.UNKNOWN
    assert result.evidence[0].raw_type == "future-unknown-type"
    assert result.evidence[0].normalized_type is None
    assert result.usable is False


def test_one_unknown_source_withholds_even_when_another_source_says_article():
    result = resolve_work_type(
        (
            signal("crossref", "journal-article"),
            signal("openalex", "future-unknown-type"),
        )
    )

    assert result.work_type == "unknown"
    assert result.status is WorkTypeStatus.UNKNOWN
    assert result.usable is False


def test_missing_structured_type_preserves_source_arrival_default():
    result = resolve_work_type(())

    assert result.work_type == "article"
    assert result.status is WorkTypeStatus.DEFAULTED
    assert result.evidence == ()
    assert result.usable is True


def test_untrusted_type_value_is_redacted_and_not_used():
    result = resolve_work_type((signal("openalex", "article\n# injected heading"),))

    assert result.work_type == "unknown"
    assert result.status is WorkTypeStatus.UNKNOWN
    assert result.evidence[0].raw_type == "unrecognized"


def test_legacy_supported_values_remain_accepted():
    assert is_supported_work_type("journal-article") is True
    assert is_supported_work_type("working_paper") is True
    assert is_supported_work_type("book") is False
