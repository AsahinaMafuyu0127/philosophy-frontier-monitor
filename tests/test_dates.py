from datetime import UTC, date, datetime

import pytest

from philosophy_frontier_monitor.freshness import assess_freshness
from philosophy_frontier_monitor.models import DatePrecision, DateValue, FreshnessStatus

WINDOW_START = datetime(2026, 8, 31, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 7, tzinfo=UTC)


def test_day_precision_publication_inside_window_is_confirmed_new():
    evidence = DateValue(date(2026, 9, 3), DatePrecision.DAY, source="publisher", inferred=False)

    assessment = assess_freshness(
        evidence,
        event="recently_published_online",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert assessment.status is FreshnessStatus.CONFIRMED_NEW


def test_old_publication_seen_this_week_is_not_new():
    evidence = DateValue(date(2018, 4, 1), DatePrecision.DAY, source="crossref")

    assessment = assess_freshness(
        evidence,
        event="recently_published_online",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert assessment.status is FreshnessStatus.NEWLY_INDEXED_OLD_WORK


def test_oai_datestamp_cannot_prove_publication_newness():
    evidence = DateValue(date(2026, 9, 4), DatePrecision.DAY, source="philarchive-oai-datestamp")

    assessment = assess_freshness(
        evidence,
        event="recently_published_online",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert assessment.status is FreshnessStatus.SOURCE_RECORD_UPDATED


def test_year_precision_is_not_fabricated_into_a_week():
    evidence = DateValue("2026", DatePrecision.YEAR, source="crossref")

    assessment = assess_freshness(
        evidence,
        event="recently_published_online",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert assessment.status is FreshnessStatus.UNCERTAIN


def test_coarse_month_can_still_prove_that_a_work_is_old():
    evidence = DateValue("2025-04", DatePrecision.MONTH, source="crossref")

    assessment = assess_freshness(
        evidence,
        event="recently_assigned_to_issue",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )

    assert assessment.status is FreshnessStatus.NEWLY_INDEXED_OLD_WORK


def test_rejects_naive_monitoring_window():
    evidence = DateValue(date(2026, 9, 3), DatePrecision.DAY, source="publisher")

    with pytest.raises(ValueError, match="timezone-aware"):
        assess_freshness(
            evidence,
            event="recently_published_online",
            window_start=datetime(2026, 8, 31),
            window_end=datetime(2026, 9, 7),
        )
