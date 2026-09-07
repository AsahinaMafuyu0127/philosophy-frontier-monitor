"""Fail-closed baseline and weekly monitoring pipeline.

Remote feed text is treated as untrusted data. This module extracts only a
verified PhilPapers record URL and display title; it never executes, follows,
or sends feed descriptions to a model.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

import httpx

from .bibliographic_cache import BibliographicCache, BibliographicCacheStats
from .config import ConfirmedCategoryConfig, FeedConfig, WatchlistConfig
from .freshness import assess_freshness
from .identity import (
    IdentityMatchLevel,
    IdentityReviewRequired,
    compare_bibliographic_identity,
)
from .matching import match_work
from .models import (
    CategoryAssignment,
    CategoryStatus,
    DatePrecision,
    DateValue,
    FreshnessStatus,
    InferenceRelation,
    InterestProfile,
    MappingSource,
    MatchDecision,
    MatchRecord,
    Notification,
    ProposalStatus,
    ProposedCategory,
    SelectedCategory,
    TaxonomySnapshot,
    WorkRecord,
)
from .normalize import normalize_doi, normalize_title
from .report import SourceCoverage, render_on_demand_report, render_weekly_report
from .sources.crossref import DEFAULT_USER_AGENT as CROSSREF_USER_AGENT
from .sources.crossref import CrossrefError, CrossrefWork
from .sources.crossref import find_exact_work as find_crossref_work
from .sources.openalex import (
    DEFAULT_USER_AGENT as OPENALEX_USER_AGENT,
)
from .sources.openalex import (
    MAX_DOI_BATCH_SIZE,
    MAX_TITLE_BATCH_SIZE,
    MAX_TITLE_SPLIT_DEPTH,
    OpenAlexError,
    OpenAlexWork,
)
from .sources.openalex import find_exact_work as find_openalex_work
from .sources.openalex import find_works_by_dois as find_openalex_works_by_dois
from .sources.openalex import find_works_by_titles as find_openalex_works_by_titles
from .sources.philpapers_rss import (
    DEFAULT_USER_AGENT as PHILPAPERS_USER_AGENT,
)
from .sources.philpapers_rss import (
    FeedEntry,
    discover_feed_request,
    fetch_feed,
    parse_feed,
    split_display_bibliography,
)
from .state import (
    FeedStateUpdate,
    InterestProfileSnapshot,
    ProcessingOutcome,
    SourceObservation,
    StateStore,
    StoredProfileCategory,
    StoredProfileFeed,
    UnresolvedUpdate,
)
from .taxonomy import expand_selected_categories, load_taxonomy, require_production_taxonomy

SOURCE_NAME = "philpapers-rss"
NOTIFICATION_TYPE = "weekly_new_papers"
PIPELINE_VERSION = "0.2.2"
MATCHING_RULE_VERSION = "set_intersection_v1"
RECORD_PATH = re.compile(r"/rec/(?!\.{1,2}/?$)[A-Za-z0-9._~-]+/?")
FEED_YEAR_HINT = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
FEED_DOI_HINT = re.compile(r"https?://(?:dx\.)?doi\.org/[^\s\"'<>?&]+", re.IGNORECASE)
FEED_WORK_TYPE_HINTS = (
    ("author-accepted-manuscript", re.compile(r"\bauthor(?:'s)? accepted manuscript\b", re.I)),
    ("working-paper", re.compile(r"\bworking[ -]paper\b", re.I)),
    ("submitted-manuscript", re.compile(r"\b(?:submitted|unpublished) manuscript\b", re.I)),
    ("preprint", re.compile(r"\bpre[ -]?print\b", re.I)),
    ("manuscript", re.compile(r"\bmanuscript\b", re.I)),
    ("forthcoming-article", re.compile(r"\b(?:forthcoming|to appear)\b", re.I)),
)
EXPLICIT_UNSUPPORTED_TITLE_HINTS = (
    (
        "review",
        re.compile(
            r"""
            ^\s*[\[(]?\s*(?:
                (?:book\s+)?reviews?(?:\s+essay)?(?:\s+of\b|\s*:)
                |rezension(?:\s+(?:zu|von)\b|\s*:)
                |compte\s+rendu(?:\s+de\b|\s*:)
                |reseña(?:\s+de\b|\s*:)
                |recensione(?:\s+di\b|\s*:)
                |resenha(?:\s+de\b|\s*:)
                |书评\s*[:：]
            )
            """,
            re.I | re.X,
        ),
    ),
)
MAX_ON_DEMAND_CANDIDATES = 1000
MAX_ON_DEMAND_FALLBACK_CANDIDATES = 50


class PipelineError(RuntimeError):
    """Raised when a run cannot safely claim complete coverage."""


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    feed_key: str
    category_id: str
    category_url: str
    checked_at: datetime
    content_hash: str
    entries: tuple[FeedEntry, ...]


@dataclass(frozen=True, slots=True)
class MergedCandidate:
    source: str
    source_id: str
    display_title: str
    stable_url: str
    category_ids: frozenset[str]
    observed_at: datetime
    raw_hash: str
    feed_dates: tuple[datetime, ...] = ()
    feed_year_hints: tuple[int, ...] = ()
    doi_hints: tuple[str, ...] = ()
    work_type_hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    candidate: MergedCandidate
    work: WorkRecord | None
    reason_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class BaselineResult:
    feed_count: int
    feed_entry_count: int
    unique_source_records: int
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class WeeklyRunResult:
    run_id: str
    window_start: datetime
    window_end: datetime
    dry_run: bool
    report_path: Path | None
    report_markdown: str
    stats: dict[str, int]


@dataclass(frozen=True, slots=True)
class OnDemandRunResult:
    request_id: str
    window_start: datetime
    window_end: datetime
    report_markdown: str
    stats: dict[str, int]


@dataclass(frozen=True, slots=True)
class CatchUpPlan:
    as_of: datetime
    latest_due_window: tuple[datetime, datetime]
    missing_windows: tuple[tuple[datetime, datetime], ...]
    status: str


@dataclass(frozen=True, slots=True)
class CatchUpResult:
    plan: CatchUpPlan
    dry_run: bool
    completed_runs: tuple[WeeklyRunResult, ...]
    profile_snapshot_registered: bool


class CatchUpError(PipelineError):
    """Report a failed catch-up without hiding already committed windows."""

    def __init__(
        self,
        message: str,
        *,
        plan: CatchUpPlan,
        completed_runs: tuple[WeeklyRunResult, ...],
        failed_window: tuple[datetime, datetime],
        profile_snapshot_registered: bool,
    ) -> None:
        super().__init__(message)
        self.plan = plan
        self.completed_runs = completed_runs
        self.failed_window = failed_window
        self.profile_snapshot_registered = profile_snapshot_registered


FeedLoader = Callable[[FeedConfig, datetime], FeedSnapshot]
CandidateResolver = Callable[
    [MergedCandidate, TaxonomySnapshot, WatchlistConfig, datetime, datetime, datetime],
    ResolutionResult,
]
BatchDoiResolver = Callable[
    [tuple[str, ...], WatchlistConfig, datetime],
    dict[str, OpenAlexWork],
]
BatchTitleResolver = Callable[
    [tuple[str, ...], WatchlistConfig, datetime],
    tuple[OpenAlexWork, ...],
]
ProgressReporter = Callable[[str, int, int], None]
ReportWriter = Callable[[Path, str, str], Path]


def previous_completed_week(
    *,
    now: datetime,
    timezone,
    report_weekday: int,
) -> tuple[datetime, datetime]:
    """Return the previous completed local-week window as UTC instants."""

    if now.tzinfo is None:
        raise PipelineError("current time must be timezone-aware")
    if report_weekday not in range(7):
        raise PipelineError("report_weekday must be between 0 and 6")
    local_now = now.astimezone(timezone)
    days_since_boundary = (local_now.weekday() - report_weekday) % 7
    end_date = local_now.date() - timedelta(days=days_since_boundary)
    start_date = end_date - timedelta(days=7)
    end_local = datetime.combine(end_date, time.min, tzinfo=timezone)
    start_local = datetime.combine(start_date, time.min, tzinfo=timezone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def latest_due_week(
    *,
    now: datetime,
    timezone,
    report_weekday: int,
    report_time: time,
) -> tuple[datetime, datetime]:
    """Return the latest regular week whose scheduled delivery time has passed."""

    if now.tzinfo is None:
        raise PipelineError("current time must be timezone-aware")
    if report_weekday not in range(7):
        raise PipelineError("report_weekday must be between 0 and 6")
    if report_time.tzinfo is not None:
        raise PipelineError("report_time must be a local wall-clock time")
    local_now = now.astimezone(timezone)
    days_since_boundary = (local_now.weekday() - report_weekday) % 7
    end_date = local_now.date() - timedelta(days=days_since_boundary)
    scheduled_local = datetime.combine(end_date, report_time, tzinfo=timezone)
    if local_now < scheduled_local:
        end_date -= timedelta(days=7)
    return _weekly_window_ending_on(end_date, timezone)


def _weekly_window_ending_on(
    end_date: date,
    timezone,
) -> tuple[datetime, datetime]:
    start_date = end_date - timedelta(days=7)
    end_local = datetime.combine(end_date, time.min, tzinfo=timezone)
    start_local = datetime.combine(start_date, time.min, tzinfo=timezone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _scheduled_delivery_for_window(
    window_end: datetime,
    config: WatchlistConfig,
) -> datetime:
    end_date = window_end.astimezone(config.timezone).date()
    local_delivery = datetime.combine(
        end_date,
        config.schedule.local_time,
        tzinfo=config.timezone,
    )
    return local_delivery.astimezone(UTC)


def plan_weekly_catch_up(
    config: WatchlistConfig,
    state: StateStore,
    *,
    now: datetime | None = None,
) -> CatchUpPlan:
    """Derive every missing regular week up to the latest scheduled delivery."""

    as_of = now or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise PipelineError("catch-up time must be timezone-aware")
    latest = latest_due_week(
        now=as_of,
        timezone=config.timezone,
        report_weekday=config.report_weekday,
        report_time=config.schedule.local_time,
    )
    successful = state.successful_run_windows(run_kind="weekly")
    successful_set = {(start.astimezone(UTC), end.astimezone(UTC)) for start, end in successful}
    if not successful:
        missing = (latest,)
    else:
        earliest_start = min(start.astimezone(UTC) for start, _end in successful)
        end_date = latest[1].astimezone(config.timezone).date()
        candidates: list[tuple[datetime, datetime]] = []
        while True:
            candidate = _weekly_window_ending_on(end_date, config.timezone)
            if candidate[0] < earliest_start:
                break
            candidates.append(candidate)
            end_date -= timedelta(days=7)
        missing = tuple(
            candidate for candidate in reversed(candidates) if candidate not in successful_set
        )
    return CatchUpPlan(
        as_of=as_of,
        latest_due_window=latest,
        missing_windows=missing,
        status="pending_catch_up" if missing else "up_to_date",
    )


def _load_runtime(
    config: WatchlistConfig,
    *,
    now: datetime,
    allow_development_fixture: bool,
) -> tuple[TaxonomySnapshot, InterestProfile]:
    snapshot = load_taxonomy(config.taxonomy_path)
    if (snapshot.fixture or not snapshot.complete) and not (
        allow_development_fixture and config.allow_fixture_for_dry_run
    ):
        require_production_taxonomy(snapshot)

    selected: list[SelectedCategory] = []
    expansion_flags: dict[str, bool] = {}
    for configured in config.confirmed_categories:
        category = snapshot.categories.get(configured.category_id)
        if category is None or not category.active:
            raise PipelineError(
                f"configured category is missing or inactive: {configured.category_id}"
            )
        if category.category_name != configured.category_name:
            raise PipelineError(
                "configured category name does not match taxonomy: "
                f"{configured.category_id} is {category.category_name!r}, "
                f"not {configured.category_name!r}"
            )
        expansion_flags[configured.category_id] = configured.include_descendants
        selected.append(
            SelectedCategory(
                category_id=configured.category_id,
                category_name=configured.category_name,
                mapping_source=MappingSource.USER_SELECTED,
                evidence="用户已确认的 watchlist 分类",
                original_fragment=configured.category_name,
                include_descendants=configured.include_descendants,
            )
        )

    for category_id in config.excluded_category_ids:
        category = snapshot.categories.get(category_id)
        if category is None or not category.active:
            raise PipelineError(f"excluded category is missing or inactive: {category_id}")

    proposed: list[ProposedCategory] = []
    for configured in config.proposed_categories:
        category = snapshot.categories.get(configured.category_id)
        if category is None or not category.active:
            raise PipelineError(
                f"proposed category is missing or inactive: {configured.category_id}"
            )
        if category.category_name != configured.category_name:
            raise PipelineError(
                "proposed category name does not match taxonomy: "
                f"{configured.category_id} is {category.category_name!r}, "
                f"not {configured.category_name!r}"
            )
        proposed.append(
            ProposedCategory(
                category_id=configured.category_id,
                category_name=configured.category_name,
                relation=InferenceRelation(configured.relation),
                rationale=configured.rationale,
                evidence_sources=configured.evidence_sources,
                breadth_note=configured.breadth_note,
                status=ProposalStatus(configured.status),
            )
        )

    expanded_ids = expand_selected_categories(snapshot, expansion_flags)
    configured_feed_ids = {feed.category_id for feed in config.feeds}
    missing_feeds = expanded_ids.difference(configured_feed_ids)
    extra_feeds = configured_feed_ids.difference(expanded_ids)
    if missing_feeds or extra_feeds:
        raise PipelineError(
            "feed categories must exactly cover the expanded interest set; "
            f"missing={sorted(missing_feeds)}, extra={sorted(extra_feeds)}"
        )

    for feed in config.feeds:
        category = snapshot.categories[feed.category_id]
        if category.category_name != feed.category_name:
            raise PipelineError(
                f"feed category name mismatch for {feed.category_id}: "
                f"{feed.category_name!r} != {category.category_name!r}"
            )

    profile = InterestProfile(
        profile_id=config.profile_id,
        version=config.profile_version,
        created_at=now,
        effective_from=config.profile_effective_from or now,
        original_text=config.original_text,
        taxonomy_snapshot_id=snapshot.snapshot_id,
        selected_categories=tuple(selected),
        expanded_category_ids=expanded_ids,
        excluded_category_ids=config.excluded_category_ids,
        proposed_categories=tuple(proposed),
        inference_mode=config.inference_mode,
    )
    return snapshot, profile


def _profile_state_snapshot(
    config: WatchlistConfig,
    taxonomy: TaxonomySnapshot,
    profile: InterestProfile,
) -> InterestProfileSnapshot:
    """Build the minimal private snapshot needed for deterministic historical runs."""

    if config.profile_effective_from is None:
        raise PipelineError(
            "interest.effective_from is required before a baseline or formal weekly run "
            "can persist an immutable interest-profile snapshot"
        )
    return InterestProfileSnapshot(
        profile_id=profile.profile_id,
        version=profile.version,
        effective_from=config.profile_effective_from,
        taxonomy_snapshot_id=taxonomy.snapshot_id,
        confirmed_categories=tuple(
            StoredProfileCategory(
                category_id=item.category_id,
                category_name=item.category_name,
                include_descendants=item.include_descendants,
            )
            for item in config.confirmed_categories
        ),
        expanded_category_ids=profile.expanded_category_ids,
        excluded_category_ids=profile.excluded_category_ids,
        feeds=tuple(
            StoredProfileFeed(
                category_id=item.category_id,
                category_name=item.category_name,
                url=item.url,
            )
            for item in config.feeds
        ),
        inference_mode=profile.inference_mode,
        timezone=str(config.timezone),
        report_weekday=config.report_weekday,
        schedule_local_time=config.schedule.local_time.isoformat(),
    )


def _config_for_stored_profile(
    current: WatchlistConfig,
    stored: InterestProfileSnapshot,
    *,
    taxonomy_path: Path,
) -> WatchlistConfig:
    """Reconstruct a past active profile without restoring private free text."""

    current_schedule = (
        str(current.timezone),
        current.report_weekday,
        current.schedule.local_time.isoformat(),
    )
    stored_schedule = (
        stored.timezone,
        stored.report_weekday,
        stored.schedule_local_time,
    )
    if current_schedule != stored_schedule:
        raise PipelineError(
            "catch-up crosses a historical schedule boundary; automatic window reconstruction "
            "requires the same timezone, weekday, and local delivery time"
        )
    return replace(
        current,
        profile_id=stored.profile_id,
        profile_version=stored.version,
        profile_effective_from=stored.effective_from,
        original_text="（历史运行画像；原始研究描述未复制到状态库）",
        confirmed_categories=tuple(
            ConfirmedCategoryConfig(
                category_id=item.category_id,
                category_name=item.category_name,
                include_descendants=item.include_descendants,
            )
            for item in stored.confirmed_categories
        ),
        proposed_categories=(),
        inference_mode=stored.inference_mode,
        excluded_category_ids=stored.excluded_category_ids,
        taxonomy_path=taxonomy_path,
        feeds=tuple(
            FeedConfig(
                category_id=item.category_id,
                category_name=item.category_name,
                url=item.url,
            )
            for item in stored.feeds
        ),
    )


def _taxonomy_path_for_profile(
    current_path: Path,
    current_snapshot: TaxonomySnapshot,
    required_snapshot_id: str,
) -> Path:
    """Resolve a retained production snapshot by its credential-free stable ID."""

    if current_snapshot.snapshot_id == required_snapshot_id:
        return current_path.resolve()
    match = re.fullmatch(
        r"philpapers:(\d{8}T\d{6}Z):([0-9a-f]{12})",
        required_snapshot_id,
    )
    if match is None:
        raise PipelineError("historical interest profile requires an unavailable taxonomy snapshot")
    timestamp, content_suffix = match.groups()
    directory = current_path.resolve().parent
    candidate = (directory / f"philpapers-taxonomy-{timestamp}-{content_suffix}.json").resolve()
    if candidate.parent != directory or not candidate.is_file():
        raise PipelineError(
            "historical interest profile requires a retained taxonomy snapshot that "
            "is not present beside the current snapshot"
        )
    historical = load_taxonomy(candidate)
    require_production_taxonomy(historical)
    if historical.snapshot_id != required_snapshot_id:
        raise PipelineError("retained taxonomy filename and verified snapshot ID do not agree")
    return candidate


def load_philpapers_feed(
    feed: FeedConfig,
    checked_at: datetime,
    *,
    client: httpx.Client | None = None,
) -> FeedSnapshot:
    request = discover_feed_request(feed.url, client=client)
    if request.category_id != feed.category_id:
        raise PipelineError(
            f"PhilPapers page {feed.url} resolved to cId={request.category_id}, "
            f"expected {feed.category_id}"
        )
    xml_text = fetch_feed(request, client=client)
    return FeedSnapshot(
        feed_key=feed.feed_key,
        category_id=feed.category_id,
        category_url=feed.url,
        checked_at=checked_at,
        content_hash="sha256:" + hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
        entries=parse_feed(xml_text),
    )


def _canonical_record_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"philpapers.org", "www.philpapers.org"}
        or RECORD_PATH.fullmatch(parsed.path) is None
    ):
        raise PipelineError("feed entry does not contain an allowed PhilPapers /rec/ URL")
    path = parsed.path.rstrip("/")
    return urlunparse(("https", "philpapers.org", path, "", "", ""))


def _candidate_hash(
    source_id: str,
    display_title: str,
    category_ids: frozenset[str],
) -> str:
    value = "\x1f".join((source_id, display_title, *sorted(category_ids)))
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_feed_date(value: str | None) -> datetime | None:
    """Parse a feed timestamp only for bounded candidate selection.

    This timestamp is never accepted as proof of publication newness.  A
    selected candidate must still be resolved against Crossref/OpenAlex.
    """

    if value is None or not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_feed_year_hint(description: str | None) -> int | None:
    """Extract one bibliography year without retaining or exposing description text."""

    if description is None:
        return None
    match = FEED_YEAR_HINT.search(description[:512])
    return int(match.group()) if match is not None else None


def _parse_feed_doi_hint(description: str | None) -> str | None:
    """Extract a validated DOI URL without retaining remote description text."""

    if description is None:
        return None
    decoded = unquote(unescape(description[:4096]))
    match = FEED_DOI_HINT.search(decoded)
    if match is None:
        return None
    return normalize_doi(match.group())


def _parse_feed_work_type_hint(description: str | None) -> str | None:
    """Extract only an explicit early-work status from the bibliography prefix."""

    if description is None:
        return None
    prefix = unescape(description[:512])
    for work_type, pattern in FEED_WORK_TYPE_HINTS:
        if pattern.search(prefix):
            return work_type
    return None


def merge_feed_snapshots(snapshots: tuple[FeedSnapshot, ...]) -> tuple[MergedCandidate, ...]:
    """Merge the same PhilPapers record across category feeds."""

    merged: dict[str, MergedCandidate] = {}
    for snapshot in snapshots:
        for entry in snapshot.entries:
            source_id = _canonical_record_url(entry.link)
            current = merged.get(source_id)
            if current is None:
                feed_date = _parse_feed_date(entry.published_text)
                year_hint = _parse_feed_year_hint(entry.description)
                doi_hint = _parse_feed_doi_hint(entry.description)
                work_type_hint = _parse_feed_work_type_hint(entry.description)
                merged[source_id] = MergedCandidate(
                    source=SOURCE_NAME,
                    source_id=source_id,
                    display_title=entry.title,
                    stable_url=source_id,
                    category_ids=frozenset({snapshot.category_id}),
                    observed_at=snapshot.checked_at,
                    raw_hash="",
                    feed_dates=(feed_date,) if feed_date is not None else (),
                    feed_year_hints=(year_hint,) if year_hint is not None else (),
                    doi_hints=(doi_hint,) if doi_hint is not None else (),
                    work_type_hints=(work_type_hint,) if work_type_hint is not None else (),
                )
                continue
            if normalize_title(current.display_title) != normalize_title(entry.title):
                raise PipelineError(f"conflicting titles for PhilPapers record {source_id}")
            feed_date = _parse_feed_date(entry.published_text)
            year_hint = _parse_feed_year_hint(entry.description)
            doi_hint = _parse_feed_doi_hint(entry.description)
            work_type_hint = _parse_feed_work_type_hint(entry.description)
            merged[source_id] = replace(
                current,
                category_ids=current.category_ids.union({snapshot.category_id}),
                observed_at=max(current.observed_at, snapshot.checked_at),
                feed_dates=tuple(
                    sorted(
                        set(current.feed_dates).union(
                            {feed_date} if feed_date is not None else set()
                        )
                    )
                ),
                feed_year_hints=tuple(
                    sorted(
                        set(current.feed_year_hints).union(
                            {year_hint} if year_hint is not None else set()
                        )
                    )
                ),
                doi_hints=tuple(
                    sorted(
                        set(current.doi_hints).union({doi_hint} if doi_hint is not None else set())
                    )
                ),
                work_type_hints=tuple(
                    sorted(
                        set(current.work_type_hints).union(
                            {work_type_hint} if work_type_hint is not None else set()
                        )
                    )
                ),
            )

    completed = []
    for candidate in merged.values():
        completed.append(
            replace(
                candidate,
                raw_hash=_candidate_hash(
                    candidate.source_id,
                    candidate.display_title,
                    candidate.category_ids,
                ),
            )
        )
    return tuple(sorted(completed, key=lambda item: item.source_id))


def _feed_updates(snapshots: tuple[FeedSnapshot, ...]) -> tuple[FeedStateUpdate, ...]:
    return tuple(
        FeedStateUpdate(
            feed_key=item.feed_key,
            category_id=item.category_id,
            category_url=item.category_url,
            entry_count=len(item.entries),
            content_hash=item.content_hash,
            checked_at=item.checked_at,
        )
        for item in snapshots
    )


def _observations(candidates: tuple[MergedCandidate, ...]) -> tuple[SourceObservation, ...]:
    return tuple(
        SourceObservation(
            source=item.source,
            source_id=item.source_id,
            display_title=item.display_title,
            stable_url=item.stable_url,
            raw_hash=item.raw_hash,
            observed_at=item.observed_at,
            category_ids=item.category_ids,
        )
        for item in candidates
    )


def _assignments(
    candidate: MergedCandidate,
    snapshot: TaxonomySnapshot,
    retrieved_at: datetime,
) -> tuple[CategoryAssignment, ...]:
    return tuple(
        CategoryAssignment(
            category_id=category_id,
            category_name=snapshot.categories[category_id].category_name,
            assignment_source="philpapers-category-feed",
            retrieved_at=retrieved_at,
            source_record_id=candidate.source_id,
            mapping_method="category_feed_membership",
        )
        for category_id in sorted(candidate.category_ids)
    )


def _work_id(doi: str | None, openalex: OpenAlexWork | None) -> str:
    if doi:
        return f"pfm:work:doi:{doi}"
    if openalex is None:
        raise PipelineError("cannot create a stable work ID without DOI or OpenAlex ID")
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, openalex.openalex_id)
    return f"pfm:work:openalex:{identifier}"


def _source_work_id(candidate: MergedCandidate) -> str:
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, candidate.source_id)
    return f"pfm:work:philpapers:{identifier}"


def _native_arrival_eligible(
    candidate: MergedCandidate,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """Return whether PhilPapers supplies enough recent-arrival evidence.

    A stale bibliography year blocks native acceptance even if the feed record
    itself has a recent update timestamp.  This is the first old-backfill gate.
    """

    window_years = set(range(window_start.year, window_end.year + 1))
    if candidate.feed_year_hints and not window_years.intersection(candidate.feed_year_hints):
        return False
    return bool(
        candidate.work_type_hints
        or window_years.intersection(candidate.feed_year_hints)
        or any(window_start <= value < window_end for value in candidate.feed_dates)
    )


def _resolve_philpapers_arrival(
    candidate: MergedCandidate,
    snapshot: TaxonomySnapshot,
    window_start: datetime,
    window_end: datetime,
    attempted_at: datetime,
) -> ResolutionResult:
    """Build a work from a current PhilPapers alert without inventing publication dates."""

    display = split_display_bibliography(candidate.display_title)
    publication_date = None
    if len(candidate.feed_year_hints) == 1:
        publication_date = DateValue(
            str(candidate.feed_year_hints[0]),
            DatePrecision.YEAR,
            "philpapers-rss-bibliography",
            source_record_id=candidate.source_id,
            retrieved_at=attempted_at,
        )
    if candidate.feed_dates:
        availability_date = DateValue(
            min(candidate.feed_dates),
            DatePrecision.SECOND,
            "philpapers-rss-entry-date",
            source_record_id=candidate.source_id,
            retrieved_at=attempted_at,
        )
    else:
        availability_date = DateValue(
            candidate.observed_at,
            DatePrecision.SECOND,
            "philpapers-rss-current-alert-observation",
            source_record_id=candidate.source_id,
            retrieved_at=attempted_at,
        )

    if candidate.feed_year_hints and max(candidate.feed_year_hints) < window_start.year:
        status = FreshnessStatus.NEWLY_INDEXED_OLD_WORK
        event = "newly_indexed_old_work"
    else:
        status = FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL
        event = "recently_arrived_in_philpapers_alert"

    return ResolutionResult(
        candidate,
        WorkRecord(
            work_id=_source_work_id(candidate),
            title=display.title,
            authors=(display.author_text,) if display.author_text else (),
            observed_at=candidate.observed_at,
            freshness_status=status,
            category_status=CategoryStatus.AVAILABLE,
            category_assignments=_assignments(candidate, snapshot, attempted_at),
            source_ids=((candidate.source, candidate.source_id),),
            work_type=candidate.work_type_hints[0] if candidate.work_type_hints else "article",
            publication_date=publication_date,
            availability_date=availability_date,
            freshness_event=event,
            stable_url=candidate.stable_url,
        ),
    )


def _resolve_explicit_unsupported_work(
    candidate: MergedCandidate,
    snapshot: TaxonomySnapshot,
    attempted_at: datetime,
) -> ResolutionResult | None:
    """Resolve an explicitly labelled unsupported bibliographic form locally."""

    display = split_display_bibliography(candidate.display_title)
    work_type = next(
        (
            candidate_type
            for candidate_type, pattern in EXPLICIT_UNSUPPORTED_TITLE_HINTS
            if pattern.search(display.title)
        ),
        None,
    )
    if work_type is None:
        return None
    availability_date = DateValue(
        min(candidate.feed_dates) if candidate.feed_dates else candidate.observed_at,
        DatePrecision.SECOND,
        "philpapers-rss-entry-date"
        if candidate.feed_dates
        else "philpapers-rss-current-alert-observation",
        source_record_id=candidate.source_id,
        retrieved_at=attempted_at,
    )
    return ResolutionResult(
        candidate,
        WorkRecord(
            work_id=_source_work_id(candidate),
            title=display.title,
            authors=(display.author_text,) if display.author_text else (),
            observed_at=candidate.observed_at,
            freshness_status=FreshnessStatus.UNCERTAIN,
            category_status=CategoryStatus.AVAILABLE,
            category_assignments=_assignments(candidate, snapshot, attempted_at),
            source_ids=((candidate.source, candidate.source_id),),
            work_type=work_type,
            availability_date=availability_date,
            freshness_event="explicit_unsupported_bibliographic_form",
            stable_url=candidate.stable_url,
        ),
    )


def _enrich_native_arrival(
    native: ResolutionResult,
    candidate: MergedCandidate,
    *,
    crossref: CrossrefWork | None,
    openalex: OpenAlexWork | None,
    publication_date: DateValue | None = None,
) -> ResolutionResult:
    if native.work is None:
        return native
    doi = crossref.doi if crossref is not None else openalex.doi if openalex else None
    title = (
        crossref.title
        if crossref is not None
        else openalex.title
        if openalex is not None
        else native.work.title
    )
    authors = (
        crossref.authors
        if crossref is not None and crossref.authors
        else openalex.authors
        if openalex is not None and openalex.authors
        else native.work.authors
    )
    work_type = (
        crossref.work_type
        if crossref is not None and crossref.work_type
        else openalex.work_type
        if openalex is not None and openalex.work_type
        else native.work.work_type
    )
    source_ids = set(native.work.source_ids)
    if crossref is not None:
        source_ids.add(("crossref", crossref.doi))
    if openalex is not None:
        source_ids.add(("openalex", openalex.openalex_id))
    return replace(
        native,
        work=replace(
            native.work,
            work_id=_work_id(doi, openalex),
            title=title,
            authors=authors,
            doi=doi,
            source_ids=tuple(sorted(source_ids)),
            work_type=work_type,
            publication_date=publication_date or native.work.publication_date,
            container_title=(
                crossref.container_title if crossref is not None else native.work.container_title
            ),
            stable_url=(
                f"https://doi.org/{doi}"
                if doi
                else openalex.stable_url
                if openalex is not None
                else candidate.stable_url
            ),
        ),
    )


def resolve_bibliography(
    candidate: MergedCandidate,
    snapshot: TaxonomySnapshot,
    config: WatchlistConfig,
    window_start: datetime,
    window_end: datetime,
    attempted_at: datetime,
    *,
    crossref_client: httpx.Client | None = None,
    openalex_client: httpx.Client | None = None,
    bibliography_cache: BibliographicCache | None = None,
    unavailable_sources: dict[str, str] | None = None,
    circuit_skip_counts: dict[str, int] | None = None,
) -> ResolutionResult:
    """Resolve exact bibliography and obtain independent publication evidence."""

    explicit_unsupported = _resolve_explicit_unsupported_work(
        candidate,
        snapshot,
        attempted_at,
    )
    if explicit_unsupported is not None:
        return explicit_unsupported

    display = split_display_bibliography(candidate.display_title)
    crossref: CrossrefWork | None = None
    openalex: OpenAlexWork | None = None
    lookup_failures: list[str] = []
    identity_review_sources: list[str] = []
    cached_crossref = (
        bibliography_cache.lookup_crossref(
            display.title,
            display.author_text,
            now=attempted_at,
        )
        if bibliography_cache is not None
        else None
    )
    if cached_crossref is not None and cached_crossref.hit:
        crossref = cached_crossref.value
    elif unavailable_sources is not None and "crossref" in unavailable_sources:
        if circuit_skip_counts is not None:
            circuit_skip_counts["crossref"] = circuit_skip_counts.get("crossref", 0) + 1
        lookup_failures.append(unavailable_sources["crossref"])
    else:
        try:
            crossref = find_crossref_work(
                display.title,
                first_author=display.author_text,
                mailto=config.crossref_mailto,
                client=crossref_client,
            )
        except IdentityReviewRequired:
            identity_review_sources.append("Crossref")
        except CrossrefError as error:
            detail = str(error)
            lookup_failures.append(detail)
            if unavailable_sources is not None:
                unavailable_sources.setdefault("crossref", detail)
        else:
            if bibliography_cache is not None:
                bibliography_cache.store_crossref(
                    display.title,
                    display.author_text,
                    crossref,
                    now=attempted_at,
                )

    needs_openalex = (
        crossref is None
        or crossref.publication_date is None
        or crossref.publication_event is None
        or crossref.publication_date.precision not in {DatePrecision.DAY, DatePrecision.SECOND}
    )
    if needs_openalex:
        cached_openalex = (
            bibliography_cache.lookup_openalex(
                display.title,
                display.author_text,
                now=attempted_at,
            )
            if bibliography_cache is not None
            else None
        )
        if cached_openalex is not None and cached_openalex.hit:
            openalex = cached_openalex.value
        elif unavailable_sources is not None and "openalex" in unavailable_sources:
            if circuit_skip_counts is not None:
                circuit_skip_counts["openalex"] = circuit_skip_counts.get("openalex", 0) + 1
            lookup_failures.append(unavailable_sources["openalex"])
        else:
            try:
                openalex = find_openalex_work(
                    display.title,
                    first_author=display.author_text,
                    mailto=config.openalex_mailto,
                    client=openalex_client,
                )
            except IdentityReviewRequired:
                identity_review_sources.append("OpenAlex")
            except OpenAlexError as error:
                detail = str(error)
                lookup_failures.append(detail)
                if unavailable_sources is not None:
                    unavailable_sources.setdefault("openalex", detail)
            else:
                if bibliography_cache is not None:
                    bibliography_cache.store_openalex(
                        display.title,
                        display.author_text,
                        openalex,
                        now=attempted_at,
                    )

    if identity_review_sources:
        return ResolutionResult(
            candidate,
            None,
            reason_code="semantic_identity_review_required",
            detail=(
                "A bibliographic service returned a possible translation or substantially "
                "retitled version. Semantic identity review is required before notification "
                f"({', '.join(identity_review_sources)})."
            ),
        )

    if crossref is None and openalex is None:
        if lookup_failures:
            return ResolutionResult(
                candidate,
                None,
                reason_code="old_work_check_incomplete",
                detail=(
                    "The old-work check could not complete because a bibliographic service "
                    "failed: " + "; ".join(lookup_failures) + "."
                ),
            )
        return _resolve_philpapers_arrival(
            candidate,
            snapshot,
            window_start,
            window_end,
            attempted_at,
        )

    if (
        crossref is not None
        and openalex is not None
        and crossref.doi
        and openalex.doi
        and crossref.doi != openalex.doi
    ):
        return ResolutionResult(
            candidate,
            None,
            reason_code="identifier_conflict",
            detail="Exact-title sources returned conflicting DOI identifiers.",
        )

    if (
        crossref is not None
        and crossref.publication_date is not None
        and crossref.publication_event is not None
    ):
        publication_date = crossref.publication_date
        publication_event = crossref.publication_event
    elif openalex is not None and openalex.publication_date is not None:
        publication_date = openalex.publication_date
        publication_event = "recently_published"
    else:
        native = _resolve_philpapers_arrival(
            candidate,
            snapshot,
            window_start,
            window_end,
            attempted_at,
        )
        return _enrich_native_arrival(
            native,
            candidate,
            crossref=crossref,
            openalex=openalex,
        )

    freshness = assess_freshness(
        publication_date,
        event=publication_event,
        window_start=window_start,
        window_end=window_end,
    )
    if freshness.status is FreshnessStatus.UNCERTAIN:
        native = _resolve_philpapers_arrival(
            candidate,
            snapshot,
            window_start,
            window_end,
            attempted_at,
        )
        return _enrich_native_arrival(
            native,
            candidate,
            crossref=crossref,
            openalex=openalex,
            publication_date=publication_date,
        )

    doi = crossref.doi if crossref is not None else openalex.doi if openalex else None
    title = crossref.title if crossref is not None else openalex.title
    authors = (
        crossref.authors
        if crossref is not None and crossref.authors
        else openalex.authors
        if openalex is not None
        else ()
    )
    work_type = (
        crossref.work_type
        if crossref is not None and crossref.work_type
        else openalex.work_type
        if openalex is not None and openalex.work_type
        else "article"
    )
    stable_url = (
        f"https://doi.org/{doi}"
        if doi
        else openalex.stable_url
        if openalex is not None
        else candidate.stable_url
    )
    source_ids = {(candidate.source, candidate.source_id)}
    if crossref is not None:
        source_ids.add(("crossref", crossref.doi))
    if openalex is not None:
        source_ids.add(("openalex", openalex.openalex_id))

    work = WorkRecord(
        work_id=_work_id(doi, openalex),
        title=title,
        authors=authors,
        observed_at=candidate.observed_at,
        freshness_status=freshness.status,
        category_status=CategoryStatus.AVAILABLE,
        category_assignments=_assignments(candidate, snapshot, attempted_at),
        doi=doi,
        source_ids=tuple(sorted(source_ids)),
        work_type=work_type,
        publication_date=publication_date,
        freshness_event=publication_event,
        container_title=crossref.container_title if crossref is not None else None,
        stable_url=stable_url,
    )
    return ResolutionResult(candidate, work)


def load_openalex_doi_batch(
    dois: tuple[str, ...],
    config: WatchlistConfig,
    _attempted_at: datetime,
    *,
    client: httpx.Client | None = None,
) -> dict[str, OpenAlexWork]:
    """Load DOI-addressed works with bounded OpenAlex requests."""

    return find_openalex_works_by_dois(dois, mailto=config.openalex_mailto, client=client)


def load_openalex_title_batch(
    titles: tuple[str, ...],
    config: WatchlistConfig,
    _attempted_at: datetime,
    *,
    client: httpx.Client | None = None,
) -> tuple[OpenAlexWork, ...]:
    """Load title-addressed works for strict local title/author matching."""

    return find_openalex_works_by_titles(titles, mailto=config.openalex_mailto, client=client)


def _candidate_matches_openalex(
    candidate: MergedCandidate,
    openalex: OpenAlexWork,
) -> bool:
    display = split_display_bibliography(candidate.display_title)
    shared_identifier = openalex.doi is not None and openalex.doi in candidate.doi_hints
    decision = compare_bibliographic_identity(
        display.title,
        display.author_text,
        openalex.title,
        openalex.authors,
        shared_identifier=shared_identifier,
    )
    return decision.level is IdentityMatchLevel.EQUIVALENT


def _resolve_batched_openalex(
    candidate: MergedCandidate,
    openalex: OpenAlexWork,
    snapshot: TaxonomySnapshot,
    window_start: datetime,
    window_end: datetime,
    attempted_at: datetime,
) -> ResolutionResult | None:
    """Resolve a high-confidence equivalent work without making OpenAlex mandatory."""

    if not _candidate_matches_openalex(candidate, openalex):
        return None
    if openalex.publication_date is None:
        native = _resolve_philpapers_arrival(
            candidate,
            snapshot,
            window_start,
            window_end,
            attempted_at,
        )
        if native.work is None:
            return native
        return replace(
            native,
            work=replace(
                native.work,
                work_id=_work_id(openalex.doi, openalex),
                title=openalex.title,
                authors=openalex.authors or native.work.authors,
                doi=openalex.doi,
                source_ids=tuple(
                    sorted(
                        {
                            *native.work.source_ids,
                            ("openalex", openalex.openalex_id),
                        }
                    )
                ),
                work_type=openalex.work_type or native.work.work_type,
                stable_url=openalex.stable_url,
            ),
        )
    freshness = assess_freshness(
        openalex.publication_date,
        event="recently_published",
        window_start=window_start,
        window_end=window_end,
    )
    if freshness.status is FreshnessStatus.UNCERTAIN:
        native = _resolve_philpapers_arrival(
            candidate,
            snapshot,
            window_start,
            window_end,
            attempted_at,
        )
        if native.work is None:
            return native
        return replace(
            native,
            work=replace(
                native.work,
                work_id=_work_id(openalex.doi, openalex),
                title=openalex.title,
                authors=openalex.authors or native.work.authors,
                doi=openalex.doi,
                source_ids=tuple(
                    sorted(
                        {
                            *native.work.source_ids,
                            ("openalex", openalex.openalex_id),
                        }
                    )
                ),
                work_type=openalex.work_type or native.work.work_type,
                publication_date=openalex.publication_date,
                stable_url=openalex.stable_url,
            ),
        )
    return ResolutionResult(
        candidate,
        WorkRecord(
            work_id=_work_id(openalex.doi, openalex),
            title=openalex.title,
            authors=openalex.authors,
            observed_at=candidate.observed_at,
            freshness_status=freshness.status,
            category_status=CategoryStatus.AVAILABLE,
            category_assignments=_assignments(candidate, snapshot, attempted_at),
            doi=openalex.doi,
            source_ids=tuple(
                sorted(
                    {
                        (candidate.source, candidate.source_id),
                        ("openalex", openalex.openalex_id),
                    }
                )
            ),
            work_type=openalex.work_type or "article",
            publication_date=openalex.publication_date,
            freshness_event=freshness.event,
            container_title=None,
            stable_url=openalex.stable_url,
        ),
    )


def _merge_work(left: WorkRecord, right: WorkRecord) -> WorkRecord:
    if left.work_id != right.work_id or left.doi != right.doi:
        raise PipelineError("attempted to merge incompatible work records")
    assignments = {
        item.category_id: item for item in (*left.category_assignments, *right.category_assignments)
    }
    freshness_status = left.freshness_status
    freshness_event = left.freshness_event
    publication_date = left.publication_date
    if (
        left.freshness_status != right.freshness_status
        or left.freshness_event != right.freshness_event
        or left.publication_date != right.publication_date
    ):
        freshness_status = FreshnessStatus.UNCERTAIN
        freshness_event = "conflicting_resolution_evidence"
        publication_date = None
    return replace(
        left,
        observed_at=min(left.observed_at, right.observed_at),
        freshness_status=freshness_status,
        freshness_event=freshness_event,
        publication_date=publication_date,
        category_assignments=tuple(assignments[key] for key in sorted(assignments)),
        source_ids=tuple(sorted(set(left.source_ids).union(right.source_ids))),
    )


def write_report_atomic(directory: Path, filename: str, content: str) -> Path:
    """Durably write a report before database checkpoints may advance."""

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    temporary = directory / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target.resolve()


def _load_all_feeds(
    config: WatchlistConfig,
    *,
    checked_at: datetime,
    loader: FeedLoader,
    feeds: tuple[FeedConfig, ...] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[FeedSnapshot, ...]:
    selected_feeds = config.feeds if feeds is None else feeds
    loaded_snapshots: list[FeedSnapshot] = []
    total = len(selected_feeds)

    def load_selected(active_loader: FeedLoader) -> None:
        for index, feed in enumerate(selected_feeds, start=1):
            loaded_snapshots.append(active_loader(feed, checked_at))
            if progress is not None:
                progress(index, total)

    if loader is load_philpapers_feed:
        # Reuse one connection pool for page discovery, feed generation, and
        # all configured categories. This changes transport cost, not evidence.
        with httpx.Client(
            headers={"User-Agent": PHILPAPERS_USER_AGENT},
            follow_redirects=True,
            timeout=30,
        ) as client:
            load_selected(
                lambda feed, observed_at: load_philpapers_feed(
                    feed,
                    observed_at,
                    client=client,
                )
            )
    else:
        load_selected(loader)
    snapshots = tuple(loaded_snapshots)
    expected_keys = {feed.feed_key for feed in selected_feeds}
    actual_keys = {snapshot.feed_key for snapshot in snapshots}
    if actual_keys != expected_keys:
        raise PipelineError("feed loader did not return exactly the configured feeds")
    snapshots_by_key = {snapshot.feed_key: snapshot for snapshot in snapshots}
    for feed in selected_feeds:
        loaded = snapshots_by_key[feed.feed_key]
        if (
            loaded.category_id != feed.category_id
            or loaded.category_url != feed.url
            or loaded.checked_at.tzinfo is None
        ):
            raise PipelineError(f"feed loader returned inconsistent metadata for {feed.feed_key}")
    return snapshots


def establish_baseline(
    config: WatchlistConfig,
    state: StateStore,
    *,
    now: datetime | None = None,
    feed_loader: FeedLoader = load_philpapers_feed,
    allow_development_fixture: bool = False,
) -> BaselineResult:
    """Record current feed contents as history without notifying them."""

    completed_at = now or datetime.now(UTC)
    if completed_at.tzinfo is None:
        raise PipelineError("baseline time must be timezone-aware")
    taxonomy, profile = _load_runtime(
        config,
        now=completed_at,
        allow_development_fixture=allow_development_fixture,
    )
    profile_snapshot = _profile_state_snapshot(config, taxonomy, profile)
    feed_keys = {feed.feed_key for feed in config.feeds}
    missing_keys = state.missing_feed_baselines(feed_keys)
    if not missing_keys:
        raise PipelineError("baseline already exists for every configured feed")
    missing_feeds = tuple(feed for feed in config.feeds if feed.feed_key in missing_keys)

    snapshots = _load_all_feeds(
        config,
        checked_at=completed_at,
        loader=feed_loader,
        feeds=missing_feeds,
    )
    candidates = merge_feed_snapshots(snapshots)
    state.establish_baseline(
        feeds=_feed_updates(snapshots),
        observations=_observations(candidates),
        interest_profile_snapshot=profile_snapshot,
    )
    return BaselineResult(
        feed_count=len(snapshots),
        feed_entry_count=sum(len(item.entries) for item in snapshots),
        unique_source_records=len(candidates),
        completed_at=completed_at,
    )


def _retry_candidate(
    record,
    current: dict[tuple[str, str], MergedCandidate],
    observed_at: datetime,
) -> MergedCandidate:
    existing = current.get((record.source, record.source_id))
    if existing is not None:
        return existing
    return MergedCandidate(
        source=record.source,
        source_id=record.source_id,
        display_title=record.display_title,
        stable_url=record.stable_url,
        category_ids=record.category_ids,
        observed_at=observed_at,
        raw_hash=_candidate_hash(record.source_id, record.display_title, record.category_ids),
    )


def on_demand_window(
    config: WatchlistConfig,
    *,
    now: datetime,
    lookback_days: int = 7,
) -> tuple[datetime, datetime]:
    """Return a rolling local-time window ending at the request instant."""

    if now.tzinfo is None:
        raise PipelineError("on-demand request time must be timezone-aware")
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int):
        raise PipelineError("lookback_days must be an integer")
    if not 1 <= lookback_days <= 31:
        raise PipelineError("lookback_days must be between 1 and 31")
    local_end = now.astimezone(config.timezone)
    local_start = local_end - timedelta(days=lookback_days)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def _resolve_candidates_with_connection_reuse(
    candidates: tuple[MergedCandidate, ...],
    snapshot: TaxonomySnapshot,
    config: WatchlistConfig,
    window_start: datetime,
    window_end: datetime,
    attempted_at: datetime,
    *,
    resolver: CandidateResolver,
    bibliography_cache: BibliographicCache | None = None,
    unavailable_sources: dict[str, str] | None = None,
    circuit_skip_counts: dict[str, int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[ResolutionResult, ...]:
    """Resolve candidates while pooling default bibliographic connections."""

    resolved: list[ResolutionResult] = []
    total = len(candidates)

    def resolve_all(
        *,
        crossref_client: httpx.Client | None = None,
        openalex_client: httpx.Client | None = None,
    ) -> None:
        for index, candidate in enumerate(candidates, start=1):
            if resolver is resolve_bibliography:
                result = resolve_bibliography(
                    candidate,
                    snapshot,
                    config,
                    window_start,
                    window_end,
                    attempted_at,
                    crossref_client=crossref_client,
                    openalex_client=openalex_client,
                    bibliography_cache=bibliography_cache,
                    unavailable_sources=unavailable_sources,
                    circuit_skip_counts=circuit_skip_counts,
                )
            else:
                result = resolver(
                    candidate,
                    snapshot,
                    config,
                    window_start,
                    window_end,
                    attempted_at,
                )
            resolved.append(result)
            if progress is not None and (index == total or index % 10 == 0):
                progress(index, total)

    if resolver is resolve_bibliography and candidates:
        # Crossref and OpenAlex remain logically independent; each gets one
        # reusable connection pool instead of up to one new client per paper.
        with (
            httpx.Client(
                headers={"User-Agent": CROSSREF_USER_AGENT},
                follow_redirects=True,
                timeout=30,
            ) as crossref_client,
            httpx.Client(
                headers={"User-Agent": OPENALEX_USER_AGENT},
                follow_redirects=True,
                timeout=30,
            ) as openalex_client,
        ):
            resolve_all(
                crossref_client=crossref_client,
                openalex_client=openalex_client,
            )
    else:
        resolve_all()
    return tuple(resolved)


def run_on_demand(
    config: WatchlistConfig,
    *,
    now: datetime | None = None,
    lookback_days: int = 7,
    max_candidates: int = MAX_ON_DEMAND_CANDIDATES,
    max_fallback_candidates: int = MAX_ON_DEMAND_FALLBACK_CANDIDATES,
    feed_loader: FeedLoader = load_philpapers_feed,
    resolver: CandidateResolver = resolve_bibliography,
    batch_doi_resolver: BatchDoiResolver = load_openalex_doi_batch,
    batch_title_resolver: BatchTitleResolver = load_openalex_title_batch,
    bibliography_cache: BibliographicCache | None = None,
    allow_development_fixture: bool = False,
    progress: ProgressReporter | None = None,
) -> OnDemandRunResult:
    """Produce a read-only rolling report independent of weekly state.

    The command deliberately neither reads nor writes weekly notification,
    baseline, run, retry, or checkpoint state.  Consequently a paper may
    appear both here and in a later weekly report.
    """

    requested_at = now or datetime.now(UTC)
    cache_stats_before = (
        bibliography_cache.stats
        if bibliography_cache is not None
        else BibliographicCacheStats(0, 0, 0, 0)
    )
    bibliographic_source_failures: dict[str, str] = {}
    circuit_skip_counts: dict[str, int] = {}
    openalex_batch_failures = 0
    window_start, window_end = on_demand_window(
        config,
        now=requested_at,
        lookback_days=lookback_days,
    )
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int):
        raise PipelineError("max_candidates must be an integer")
    if max_candidates < 1:
        raise PipelineError("max_candidates must be a positive integer")
    if isinstance(max_fallback_candidates, bool) or not isinstance(max_fallback_candidates, int):
        raise PipelineError("max_fallback_candidates must be an integer")
    if max_fallback_candidates < 1:
        raise PipelineError("max_fallback_candidates must be a positive integer")

    snapshot, profile = _load_runtime(
        config,
        now=requested_at,
        allow_development_fixture=allow_development_fixture,
    )
    if progress is not None:
        progress("feeds", 0, len(config.feeds))
    feed_snapshots = _load_all_feeds(
        config,
        checked_at=requested_at,
        loader=feed_loader,
        progress=(
            (lambda completed, total: progress("feeds", completed, total))
            if progress is not None
            else None
        ),
    )
    current_candidates = merge_feed_snapshots(feed_snapshots)
    window_years = set(
        range(
            window_start.astimezone(config.timezone).year,
            window_end.astimezone(config.timezone).year + 1,
        )
    )
    candidates = tuple(
        item
        for item in current_candidates
        if (
            False
            if item.feed_year_hints and not window_years.intersection(item.feed_year_hints)
            else any(window_start <= feed_date < window_end for feed_date in item.feed_dates)
            if item.feed_dates
            else True
        )
    )
    if progress is not None:
        progress("candidates", len(candidates), len(current_candidates))
    if len(candidates) > max_candidates:
        raise PipelineError(
            f"on-demand candidate count {len(candidates)} exceeds the safety limit of "
            f"{max_candidates}; narrow the lookback window or explicitly raise the limit"
        )

    explicit_unsupported_results = tuple(
        result
        for item in candidates
        if (
            result := _resolve_explicit_unsupported_work(
                item,
                snapshot,
                requested_at,
            )
        )
        is not None
    )
    explicit_unsupported_source_ids = {
        result.candidate.source_id for result in explicit_unsupported_results
    }
    bibliographic_candidates = tuple(
        item for item in candidates if item.source_id not in explicit_unsupported_source_ids
    )

    doi_values = tuple(
        sorted({item.doi_hints[0] for item in bibliographic_candidates if len(item.doi_hints) == 1})
    )
    cached_doi_works: dict[str, OpenAlexWork] = {}
    doi_values_to_query: list[str] = []
    doi_batch_cache_hits = 0
    for doi in doi_values:
        cached = (
            bibliography_cache.lookup_openalex_doi(doi, now=requested_at)
            if bibliography_cache is not None
            else None
        )
        if cached is not None and cached.hit:
            doi_batch_cache_hits += 1
            if cached.value is not None:
                cached_doi_works[doi] = cached.value
        else:
            doi_values_to_query.append(doi)
    if progress is not None:
        progress("doi_batch", 0, len(doi_values))
    fresh_doi_works: dict[str, OpenAlexWork] = {}
    doi_batch_succeeded = not doi_values_to_query
    if doi_values_to_query:
        try:
            fresh_doi_works = batch_doi_resolver(
                tuple(doi_values_to_query),
                config,
                requested_at,
            )
        except OpenAlexError as error:
            bibliographic_source_failures.setdefault("openalex", str(error))
            openalex_batch_failures += 1
        else:
            doi_batch_succeeded = True
    if bibliography_cache is not None and doi_batch_succeeded:
        for doi in doi_values_to_query:
            bibliography_cache.store_openalex_doi(
                doi,
                fresh_doi_works.get(doi),
                now=requested_at,
            )
    batched_works = {**cached_doi_works, **fresh_doi_works}
    if progress is not None:
        progress("doi_batch", len(doi_values), len(doi_values))
    doi_batch_request_upper_bound = (
        len(doi_values_to_query) + MAX_DOI_BATCH_SIZE - 1
    ) // MAX_DOI_BATCH_SIZE
    resolution_results: list[ResolutionResult] = list(explicit_unsupported_results)
    title_candidates: list[MergedCandidate] = []
    doi_batch_resolved_count = 0
    for candidate in sorted(bibliographic_candidates, key=lambda item: item.source_id):
        batched_matches = [
            resolved
            for doi in candidate.doi_hints
            if (openalex := batched_works.get(doi)) is not None
            and (
                resolved := _resolve_batched_openalex(
                    candidate,
                    openalex,
                    snapshot,
                    window_start,
                    window_end,
                    requested_at,
                )
            )
            is not None
        ]
        if len(batched_matches) == 1:
            resolution_results.append(batched_matches[0])
            doi_batch_resolved_count += 1
        else:
            title_candidates.append(candidate)
    title_query_by_key: dict[str, str] = {}
    for item in title_candidates:
        title = split_display_bibliography(item.display_title).title
        title_query_by_key.setdefault(normalize_title(title), title)
    title_queries = tuple(title_query_by_key[key] for key in sorted(title_query_by_key))
    cached_title_works: dict[str, OpenAlexWork] = {}
    title_queries_to_query: list[str] = []
    title_batch_cache_hits = 0
    title_batch_attempt_cache_hits = 0
    for title in title_queries:
        cached = (
            bibliography_cache.lookup_openalex_title_candidates(title, now=requested_at)
            if bibliography_cache is not None
            else None
        )
        if cached is not None and cached.hit:
            title_batch_cache_hits += 1
            for work in cached.value or ():
                cached_title_works[work.openalex_id] = work
        elif (
            bibliography_cache is not None
            and bibliography_cache.was_openalex_title_batch_attempted(
                title,
                now=requested_at,
            )
        ):
            title_batch_attempt_cache_hits += 1
        else:
            title_queries_to_query.append(title)
    if progress is not None:
        progress("title_batch", 0, len(title_queries))
    fresh_title_works: tuple[OpenAlexWork, ...] = ()
    title_batch_api_values = 0
    title_batch_succeeded = not title_queries_to_query
    if title_queries_to_query and "openalex" not in bibliographic_source_failures:
        title_batch_api_values = len(title_queries_to_query)
        try:
            fresh_title_works = batch_title_resolver(
                tuple(title_queries_to_query),
                config,
                requested_at,
            )
        except OpenAlexError as error:
            bibliographic_source_failures.setdefault("openalex", str(error))
            openalex_batch_failures += 1
        else:
            title_batch_succeeded = True
    elif title_queries_to_query:
        circuit_skip_counts["openalex"] = circuit_skip_counts.get("openalex", 0) + 1
    if bibliography_cache is not None:
        for title in title_queries_to_query:
            exact_works = tuple(
                work
                for work in fresh_title_works
                if normalize_title(work.title) == normalize_title(title)
            )
            bibliography_cache.store_openalex_title_candidates(
                title,
                exact_works,
                now=requested_at,
            )
        if title_batch_succeeded:
            bibliography_cache.record_openalex_title_batch_attempts(
                tuple(title_queries_to_query),
                now=requested_at,
            )
    title_works_by_id = {
        **cached_title_works,
        **{work.openalex_id: work for work in fresh_title_works},
    }
    title_works = tuple(title_works_by_id[key] for key in sorted(title_works_by_id))
    if progress is not None:
        progress("title_batch", len(title_queries), len(title_queries))
    initial_title_batches = (
        len(title_queries_to_query) + MAX_TITLE_BATCH_SIZE - 1
    ) // MAX_TITLE_BATCH_SIZE
    title_batch_request_upper_bound = initial_title_batches * (2 ** (MAX_TITLE_SPLIT_DEPTH + 1) - 1)
    fallback_candidates: list[MergedCandidate] = []
    title_batch_resolved_count = 0
    for candidate in title_candidates:
        batched_matches = [
            resolved
            for openalex in title_works
            if _candidate_matches_openalex(candidate, openalex)
            and (
                resolved := _resolve_batched_openalex(
                    candidate,
                    openalex,
                    snapshot,
                    window_start,
                    window_end,
                    requested_at,
                )
            )
            is not None
        ]
        unique_matches = {
            item.work.work_id: item for item in batched_matches if item.work is not None
        }
        if len(unique_matches) == 1:
            resolution_results.append(next(iter(unique_matches.values())))
            title_batch_resolved_count += 1
        else:
            fallback_candidates.append(candidate)
    prioritized_fallback = sorted(
        fallback_candidates,
        key=lambda item: (
            not _native_arrival_eligible(item, window_start, window_end),
            item.source_id,
        ),
    )
    fallback_cache_ready: list[MergedCandidate] = []
    fallback_remote_candidates: list[MergedCandidate] = []
    if resolver is resolve_bibliography and bibliography_cache is not None:
        for item in prioritized_fallback:
            display = split_display_bibliography(item.display_title)
            if bibliography_cache.can_resolve_fallback_without_remote(
                display.title,
                display.author_text,
                now=requested_at,
            ):
                fallback_cache_ready.append(item)
            else:
                fallback_remote_candidates.append(item)
        fallback_remote_candidates.sort(
            key=lambda item: (
                (
                    last_attempt := bibliography_cache.fallback_last_attempt(
                        item.source_id,
                        now=requested_at,
                    )
                )
                is not None,
                last_attempt or datetime.min.replace(tzinfo=UTC),
                not _native_arrival_eligible(item, window_start, window_end),
                item.source_id,
            )
        )
    else:
        fallback_remote_candidates.extend(prioritized_fallback)
    fallback_remote_selected = tuple(fallback_remote_candidates[:max_fallback_candidates])
    fallback_to_resolve = tuple(fallback_cache_ready) + fallback_remote_selected
    fallback_deferred_count = len(fallback_remote_candidates) - len(fallback_remote_selected)
    fallback_total = len(fallback_to_resolve)
    if progress is not None:
        progress("fallback", 0, fallback_total)
    fallback_results = _resolve_candidates_with_connection_reuse(
        fallback_to_resolve,
        snapshot,
        config,
        window_start,
        window_end,
        requested_at,
        resolver=resolver,
        bibliography_cache=bibliography_cache,
        unavailable_sources=bibliographic_source_failures,
        circuit_skip_counts=circuit_skip_counts,
        progress=(
            (lambda completed, total: progress("fallback", completed, total))
            if progress is not None
            else None
        ),
    )
    resolution_results.extend(fallback_results)
    if resolver is resolve_bibliography and bibliography_cache is not None:
        result_by_source_id = {result.candidate.source_id: result for result in fallback_results}
        bibliography_cache.record_fallback_attempts(
            tuple(
                item.source_id
                for item in fallback_remote_selected
                if (result := result_by_source_id.get(item.source_id)) is not None
                and result.reason_code != "old_work_check_incomplete"
            ),
            now=requested_at,
        )
    works: dict[str, WorkRecord] = {}
    unresolved_count = fallback_deferred_count
    for result in tuple(resolution_results):
        if result.work is None:
            if result.reason_code is None or result.detail is None:
                raise PipelineError("unresolved result lacks a reason and detail")
            unresolved_count += 1
            continue
        current_work = works.get(result.work.work_id)
        works[result.work.work_id] = (
            result.work if current_work is None else _merge_work(current_work, result.work)
        )

    matches = tuple(
        match_work(work, profile, already_notified=False, now=requested_at)
        for work in sorted(
            works.values(), key=lambda item: (normalize_title(item.title), item.work_id)
        )
    )
    coverage = tuple(
        SourceCoverage(
            source=item.feed_key,
            status="success",
            checked_at=item.checked_at,
            detail=f"分类 feed 完整读取；条目数 {len(item.entries)}",
        )
        for item in feed_snapshots
    ) + tuple(
        SourceCoverage(
            source=f"{source}-bibliography",
            status="failed",
            checked_at=requested_at,
            detail=detail,
        )
        for source, detail in sorted(bibliographic_source_failures.items())
    )
    report_markdown = render_on_demand_report(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=matches,
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=unresolved_count,
    )
    stats = {
        "feed_entries": sum(len(item.entries) for item in feed_snapshots),
        "unique_current_records": len(current_candidates),
        "candidate_records": len(candidates),
        "candidate_records_without_feed_timestamp": sum(not item.feed_dates for item in candidates),
        "candidate_records_without_any_feed_date_hint": sum(
            not item.feed_dates and not item.feed_year_hints for item in candidates
        ),
        "explicit_unsupported_candidates": len(explicit_unsupported_results),
        "skipped_by_feed_hint": len(current_candidates) - len(candidates),
        "doi_hints": len(doi_values),
        "candidate_records_with_conflicting_doi_hints": sum(
            len(item.doi_hints) > 1 for item in candidates
        ),
        "doi_batch_cache_hits": doi_batch_cache_hits,
        "doi_batch_api_values": len(doi_values_to_query),
        "doi_batch_request_upper_bound": doi_batch_request_upper_bound,
        "doi_batch_resolved_candidates": doi_batch_resolved_count,
        "title_batch_queries": len(title_queries),
        "title_batch_cache_hits": title_batch_cache_hits,
        "title_batch_attempt_cache_hits": title_batch_attempt_cache_hits,
        "title_batch_api_values": title_batch_api_values,
        "title_batch_request_upper_bound": title_batch_request_upper_bound,
        "title_batch_resolved_candidates": title_batch_resolved_count,
        "openalex_batch_failures": openalex_batch_failures,
        "bibliographic_source_circuits_open": len(bibliographic_source_failures),
        "bibliographic_source_circuit_skips": sum(circuit_skip_counts.values()),
        "philpapers_native_resolved_candidates": sum(
            result.work is not None
            and result.work.freshness_status is FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL
            for result in resolution_results
        ),
        "batch_resolved_candidates": doi_batch_resolved_count + title_batch_resolved_count,
        "fallback_candidates": len(fallback_candidates),
        "fallback_cache_ready": len(fallback_cache_ready),
        "fallback_processed": len(fallback_to_resolve),
        "fallback_remote_candidates": len(fallback_remote_candidates),
        "fallback_queried": len(fallback_remote_selected),
        "fallback_deferred": fallback_deferred_count,
        "openalex_logical_request_upper_bound": (
            doi_batch_request_upper_bound
            + title_batch_request_upper_bound
            + len(fallback_remote_selected)
        ),
        "crossref_logical_request_upper_bound": len(fallback_remote_selected),
        "resolved_works": len(works),
        "confirmed_new": sum(
            item.freshness_status is FreshnessStatus.CONFIRMED_NEW for item in works.values()
        ),
        "confirmed_source_arrivals": sum(
            item.freshness_status is FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL
            for item in works.values()
        ),
        "matched": sum(item.decision is MatchDecision.NOTIFY for item in matches),
        "unresolved": unresolved_count,
    }
    cache_stats_after = (
        bibliography_cache.stats
        if bibliography_cache is not None
        else BibliographicCacheStats(0, 0, 0, 0)
    )
    stats.update(
        {
            "bibliography_cache_enabled": bibliography_cache is not None,
            "bibliography_cache_hits": cache_stats_after.hits - cache_stats_before.hits,
            "bibliography_cache_misses": cache_stats_after.misses - cache_stats_before.misses,
            "bibliography_cache_writes": cache_stats_after.writes - cache_stats_before.writes,
            "bibliography_cache_expired": cache_stats_after.expired - cache_stats_before.expired,
            "bibliography_cache_scheduling_writes": (
                cache_stats_after.scheduling_writes - cache_stats_before.scheduling_writes
            ),
        }
    )
    if progress is not None:
        progress("complete", 1, 1)
    return OnDemandRunResult(
        request_id=f"pfm:on-demand:{uuid.uuid4()}",
        window_start=window_start,
        window_end=window_end,
        report_markdown=report_markdown,
        stats=stats,
    )


def run_weekly(
    config: WatchlistConfig,
    state: StateStore,
    *,
    now: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    dry_run: bool = False,
    feed_loader: FeedLoader = load_philpapers_feed,
    resolver: CandidateResolver = resolve_bibliography,
    report_writer: ReportWriter = write_report_atomic,
    before_commit: Callable[[], None] | None = None,
    defer_uncertain_until_later_window: bool = False,
) -> WeeklyRunResult:
    """Run one complete week; mutate state only after the report is on disk."""

    fixed_now = now is not None
    started_at = now or datetime.now(UTC)
    if started_at.tzinfo is None:
        raise PipelineError("run time must be timezone-aware")
    snapshot, profile = _load_runtime(
        config,
        now=started_at,
        allow_development_fixture=dry_run,
    )
    profile_snapshot = None if dry_run else _profile_state_snapshot(config, snapshot, profile)
    if (window_start is None) != (window_end is None):
        raise PipelineError("window_start and window_end must be provided together")
    if window_start is None or window_end is None:
        window_start, window_end = previous_completed_week(
            now=started_at,
            timezone=config.timezone,
            report_weekday=config.report_weekday,
        )
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise PipelineError("monitoring window must be timezone-aware")
    window_start = window_start.astimezone(UTC)
    window_end = window_end.astimezone(UTC)
    if window_start >= window_end:
        raise PipelineError("window_start must be earlier than window_end")

    feed_keys = {feed.feed_key for feed in config.feeds}
    missing_baselines = state.missing_feed_baselines(feed_keys)
    if missing_baselines:
        raise PipelineError(f"baseline required for feeds: {sorted(missing_baselines)}")
    if not dry_run and state.has_successful_run(
        run_kind="weekly",
        window_start=window_start,
        window_end=window_end,
    ):
        raise PipelineError("this weekly window was already committed successfully")

    feed_snapshots = _load_all_feeds(config, checked_at=started_at, loader=feed_loader)
    current_candidates = merge_feed_snapshots(feed_snapshots)
    current_by_key = {(item.source, item.source_id): item for item in current_candidates}
    current_ids = {item.source_id for item in current_candidates}
    known_ids = state.known_source_ids(SOURCE_NAME, current_ids)
    new_candidates = {
        (item.source, item.source_id): item
        for item in current_candidates
        if item.source_id not in known_ids
    }

    retries = state.get_retryable_unresolved(
        as_of=started_at,
        max_attempts=config.max_unresolved_attempts,
    )
    to_process = dict(new_candidates)
    for retry in retries:
        key = (retry.source, retry.source_id)
        to_process.setdefault(key, _retry_candidate(retry, current_by_key, started_at))

    bibliographic_source_failures: dict[str, str] = {}
    circuit_skip_counts: dict[str, int] = {}
    resolution_results = _resolve_candidates_with_connection_reuse(
        tuple(sorted(to_process.values(), key=lambda item: item.source_id)),
        snapshot,
        config,
        window_start,
        window_end,
        started_at,
        resolver=resolver,
        unavailable_sources=bibliographic_source_failures,
        circuit_skip_counts=circuit_skip_counts,
    )

    deferred_keys: set[tuple[str, str]] = set()
    if defer_uncertain_until_later_window:
        for result in resolution_results:
            if result.work is None or result.work.freshness_status is FreshnessStatus.UNCERTAIN:
                deferred_keys.add((result.candidate.source, result.candidate.source_id))
    effective_results = tuple(
        result
        for result in resolution_results
        if (result.candidate.source, result.candidate.source_id) not in deferred_keys
    )

    works: dict[str, WorkRecord] = {}
    source_to_work: dict[tuple[str, str], str] = {}
    unresolved_updates: list[UnresolvedUpdate] = []
    outcomes: list[ProcessingOutcome] = []
    for result in effective_results:
        key = (result.candidate.source, result.candidate.source_id)
        if result.work is None:
            if result.reason_code is None or result.detail is None:
                raise PipelineError("unresolved result lacks a reason and detail")
            unresolved_updates.append(
                UnresolvedUpdate(
                    source=result.candidate.source,
                    source_id=result.candidate.source_id,
                    reason_code=result.reason_code,
                    detail=result.detail,
                    attempted_at=started_at,
                    next_retry_at=started_at + timedelta(days=config.unresolved_retry_days),
                )
            )
            outcomes.append(
                ProcessingOutcome(
                    source=result.candidate.source,
                    source_id=result.candidate.source_id,
                    status="unresolved",
                    work_id=None,
                )
            )
            continue
        source_to_work[key] = result.work.work_id
        current_work = works.get(result.work.work_id)
        works[result.work.work_id] = (
            result.work if current_work is None else _merge_work(current_work, result.work)
        )

    matches: list[MatchRecord] = []
    match_by_work: dict[str, MatchRecord] = {}
    notifications: list[Notification] = []
    for work in sorted(
        works.values(), key=lambda item: (normalize_title(item.title), item.work_id)
    ):
        match = match_work(
            work,
            profile,
            already_notified=state.has_notification(work.work_id, NOTIFICATION_TYPE),
            now=started_at,
        )
        matches.append(match)
        match_by_work[work.work_id] = match
        if match.decision is MatchDecision.NOTIFY:
            notifications.append(
                Notification(
                    notification_id=f"pfm:notification:{uuid.uuid4()}",
                    work_id=work.work_id,
                    match_id=match.match_id,
                    report_window_start=window_start,
                    report_window_end=window_end,
                    notification_type=NOTIFICATION_TYPE,
                    created_at=started_at,
                    matched_category_ids=match.matched_category_ids,
                )
            )

    for (source, source_id), work_id in sorted(source_to_work.items()):
        outcomes.append(
            ProcessingOutcome(
                source=source,
                source_id=source_id,
                status=match_by_work[work_id].decision.value,
                work_id=work_id,
            )
        )

    coverage = tuple(
        SourceCoverage(
            source=item.feed_key,
            status="success",
            checked_at=item.checked_at,
            detail=f"分类 feed 完整读取；条目数 {len(item.entries)}",
        )
        for item in feed_snapshots
    ) + tuple(
        SourceCoverage(
            source=f"{source}-bibliography",
            status="failed",
            checked_at=started_at,
            detail=detail,
        )
        for source, detail in sorted(bibliographic_source_failures.items())
    )
    report_markdown = render_weekly_report(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=tuple(matches),
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=len(unresolved_updates),
    )
    stats = {
        "feed_entries": sum(len(item.entries) for item in feed_snapshots),
        "unique_current_records": len(current_candidates),
        "new_source_records": len(new_candidates),
        "retry_records": len(retries),
        "processed_candidates": len(to_process),
        "resolved_works": len(works),
        "confirmed_new": sum(
            item.freshness_status is FreshnessStatus.CONFIRMED_NEW for item in works.values()
        ),
        "confirmed_source_arrivals": sum(
            item.freshness_status is FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL
            for item in works.values()
        ),
        "matched": sum(
            item.decision in {MatchDecision.NOTIFY, MatchDecision.ALREADY_NOTIFIED}
            for item in matches
        ),
        "notified": len(notifications),
        "unresolved": len(unresolved_updates),
        "deferred_to_later_window": len(deferred_keys),
        "bibliographic_source_circuits_open": len(bibliographic_source_failures),
        "bibliographic_source_circuit_skips": sum(circuit_skip_counts.values()),
    }

    run_id = f"pfm:run:{uuid.uuid4()}"
    report_path: Path | None = None
    if not dry_run:
        local_start = window_start.astimezone(config.timezone).date().isoformat()
        local_end = window_end.astimezone(config.timezone).date().isoformat()
        filename = f"weekly-{local_start}--{local_end}.md"
        report_path = report_writer(config.storage.report_directory, filename, report_markdown)
        if before_commit is not None:
            before_commit()
        completed_at = started_at if fixed_now else datetime.now(UTC)
        state.commit_weekly_run(
            run_id=run_id,
            window_start=window_start,
            window_end=window_end,
            started_at=started_at,
            completed_at=completed_at,
            report_path=str(report_path),
            stats=stats,
            taxonomy_snapshot_id=snapshot.snapshot_id,
            interest_profile_id=profile.profile_id,
            interest_profile_version=profile.version,
            pipeline_version=PIPELINE_VERSION,
            matching_rule_version=MATCHING_RULE_VERSION,
            feeds=_feed_updates(feed_snapshots),
            observations=_observations(
                tuple(
                    candidate
                    for candidate in current_candidates
                    if (candidate.source, candidate.source_id) not in deferred_keys
                )
            ),
            outcomes=tuple(outcomes),
            unresolved=tuple(unresolved_updates),
            notifications=tuple(notifications),
            interest_profile_snapshot=profile_snapshot,
        )

    return WeeklyRunResult(
        run_id=run_id,
        window_start=window_start,
        window_end=window_end,
        dry_run=dry_run,
        report_path=report_path,
        report_markdown=report_markdown,
        stats=stats,
    )


def run_weekly_catch_up(
    config: WatchlistConfig,
    state: StateStore,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    max_windows: int = 8,
    feed_loader: FeedLoader = load_philpapers_feed,
    resolver: CandidateResolver = resolve_bibliography,
    report_writer: ReportWriter = write_report_atomic,
) -> CatchUpResult:
    """Run all due gaps chronologically without consuming later-week records early."""

    if max_windows < 1:
        raise PipelineError("max_windows must be a positive integer")
    as_of = now or datetime.now(UTC)
    plan = plan_weekly_catch_up(config, state, now=as_of)
    if len(plan.missing_windows) > max_windows:
        raise PipelineError(
            f"catch-up requires {len(plan.missing_windows)} windows, exceeding "
            f"the safety limit of {max_windows}; review the backlog before increasing the limit"
        )
    for _window_start, window_end in plan.missing_windows:
        delivery = _scheduled_delivery_for_window(window_end, config)
        if state.interest_profile_at(config.profile_id, delivery) is None and (
            config.profile_effective_from is None
            or config.profile_effective_from.astimezone(UTC) > delivery
        ):
            raise PipelineError(
                "catch-up crosses an interest-profile version boundary; a historical "
                "interest-profile snapshot is required for a window that was already due "
                "before the current profile became effective"
            )

    current_taxonomy, current_profile = _load_runtime(
        config,
        now=as_of,
        allow_development_fixture=dry_run,
    )
    current_profile_snapshot = _profile_state_snapshot(
        config,
        current_taxonomy,
        current_profile,
    )
    profile_snapshot_registered = False
    if not dry_run:
        profile_snapshot_registered = state.register_interest_profile(
            current_profile_snapshot,
            recorded_at=as_of,
        )

    completed: list[WeeklyRunResult] = []
    for index, (window_start, window_end) in enumerate(plan.missing_windows):
        try:
            delivery = _scheduled_delivery_for_window(window_end, config)
            stored_profile = state.interest_profile_at(config.profile_id, delivery)
            if stored_profile is None and current_profile_snapshot.effective_from <= delivery:
                stored_profile = current_profile_snapshot
            if stored_profile is None:
                raise PipelineError(
                    "catch-up crosses an interest-profile version boundary; a historical "
                    "interest-profile snapshot is required for a window that was already due "
                    "before the current profile became effective"
                )
            profile_taxonomy_path = _taxonomy_path_for_profile(
                config.taxonomy_path,
                current_taxonomy,
                stored_profile.taxonomy_snapshot_id,
            )
            window_config = _config_for_stored_profile(
                config,
                stored_profile,
                taxonomy_path=profile_taxonomy_path,
            )
            result = run_weekly(
                window_config,
                state,
                now=as_of,
                window_start=window_start,
                window_end=window_end,
                dry_run=dry_run,
                feed_loader=feed_loader,
                resolver=resolver,
                report_writer=report_writer,
                defer_uncertain_until_later_window=index < len(plan.missing_windows) - 1,
            )
        except Exception as error:
            raise CatchUpError(
                str(error),
                plan=plan,
                completed_runs=tuple(completed),
                failed_window=(window_start, window_end),
                profile_snapshot_registered=profile_snapshot_registered,
            ) from error
        completed.append(result)

    return CatchUpResult(
        plan=plan,
        dry_run=dry_run,
        completed_runs=tuple(completed),
        profile_snapshot_registered=profile_snapshot_registered,
    )
