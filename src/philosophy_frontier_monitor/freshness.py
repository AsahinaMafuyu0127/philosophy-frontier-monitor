"""Strict publication-newness gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from .models import DatePrecision, DateValue, FreshnessStatus

PUBLICATION_EVENTS = frozenset(
    {
        "recently_published",
        "recently_published_online",
        "recently_deposited",
        "recently_assigned_to_issue",
    }
)
NON_PUBLICATION_SOURCES = frozenset(
    {"local-system", "philarchive-oai-datestamp", "oai-datestamp", "feed-observed-at"}
)


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    status: FreshnessStatus
    event: str
    reason: str
    evidence: DateValue | None


def _as_utc_instant(value: date | datetime | str, precision: DatePrecision) -> datetime:
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, date):
        instant = datetime.combine(value, time.min, tzinfo=UTC)
    elif precision is DatePrecision.DAY:
        instant = datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)
    else:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        return instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC)


def _coarse_interval(
    value: date | datetime | str,
    precision: DatePrecision,
) -> tuple[datetime, datetime] | None:
    """Return the full possible UTC interval for year/month evidence."""

    if precision is DatePrecision.YEAR:
        year = value.year if isinstance(value, date) else int(str(value)[:4])
        return datetime(year, 1, 1, tzinfo=UTC), datetime(year + 1, 1, 1, tzinfo=UTC)
    if precision is DatePrecision.MONTH:
        if isinstance(value, date):
            year, month = value.year, value.month
        else:
            year_text, month_text = str(value).split("-", 1)
            year, month = int(year_text), int(month_text[:2])
        start = datetime(year, month, 1, tzinfo=UTC)
        end = (
            datetime(year + 1, 1, 1, tzinfo=UTC)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=UTC)
        )
        return start, end
    return None


def assess_freshness(
    evidence: DateValue | None,
    *,
    event: str,
    window_start: datetime,
    window_end: datetime,
) -> FreshnessAssessment:
    """Confirm newness only from precise, non-inferred publication evidence.

    Weekly decisions require day or second precision. Year/month precision can
    be reported but cannot locate an event inside a weekly window.
    """

    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("monitoring window must be timezone-aware")
    if window_start >= window_end:
        raise ValueError("window_start must be earlier than window_end")
    if evidence is None:
        return FreshnessAssessment(
            FreshnessStatus.UNCERTAIN, event, "没有出版或公开日期证据。", None
        )
    if event not in PUBLICATION_EVENTS:
        return FreshnessAssessment(
            FreshnessStatus.SOURCE_RECORD_UPDATED,
            event,
            "该事件不是受支持的发表或公开事件。",
            evidence,
        )
    if evidence.inferred:
        return FreshnessAssessment(
            FreshnessStatus.UNCERTAIN, event, "日期是推断值，不能确认本周新出。", evidence
        )
    if evidence.source.casefold() in NON_PUBLICATION_SOURCES:
        return FreshnessAssessment(
            FreshnessStatus.SOURCE_RECORD_UPDATED,
            event,
            "来源时间表示抓取、feed 出现或 OAI 记录更新，不是发表时间。",
            evidence,
        )
    if evidence.precision not in {DatePrecision.DAY, DatePrecision.SECOND}:
        interval = _coarse_interval(evidence.value, evidence.precision)
        if interval is not None and interval[1] <= window_start.astimezone(UTC):
            return FreshnessAssessment(
                FreshnessStatus.NEWLY_INDEXED_OLD_WORK,
                event,
                "日期精度不足以定位到某一周，但其整个可能区间都早于监测窗口。",
                evidence,
            )
        return FreshnessAssessment(
            FreshnessStatus.UNCERTAIN,
            event,
            "日期精度不足以判断是否落在一周窗口内。",
            evidence,
        )

    instant = _as_utc_instant(evidence.value, evidence.precision)
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if start <= instant < end:
        return FreshnessAssessment(
            FreshnessStatus.CONFIRMED_NEW,
            event,
            "经来源日期确认，事件落在监测窗口内。",
            evidence,
        )
    if instant < start:
        return FreshnessAssessment(
            FreshnessStatus.NEWLY_INDEXED_OLD_WORK,
            event,
            "发表或公开日期早于监测窗口；即使本周才收录，也不是本周新出。",
            evidence,
        )
    return FreshnessAssessment(
        FreshnessStatus.UNCERTAIN,
        event,
        "来源日期晚于当前监测窗口，暂不作为本周新出。",
        evidence,
    )
