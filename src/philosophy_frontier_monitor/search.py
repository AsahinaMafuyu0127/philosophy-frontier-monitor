"""Read-only historical paper search over verified interest-category feeds.

No monitoring freshness gates, state store, or cache writes occur here. Results
describe the current feed inventory, never the complete PhilPapers index.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx

from .config import WatchlistConfig
from .identity import IdentityMatchLevel, IdentityReviewRequired, compare_bibliographic_identity
from .models import DateValue, WorkTypeEvidence
from .pipeline import (
    EXPLICIT_UNSUPPORTED_TITLE_HINTS,
    FeedLoader,
    MergedCandidate,
    PipelineError,
    ProgressReporter,
    _bibliographic_error_opens_circuit,
    _load_all_feeds,
    _load_runtime,
    load_philpapers_feed,
    merge_feed_snapshots,
)
from .sources.crossref import CrossrefError, CrossrefWork
from .sources.crossref import find_exact_work as find_crossref_work
from .sources.openalex import (
    DEFAULT_USER_AGENT,
    MAX_DOI_BATCH_SIZE,
    MAX_TITLE_BATCH_SIZE,
    OpenAlexError,
    OpenAlexWork,
    find_works_by_dois,
    find_works_by_titles,
)
from .sources.philpapers_rss import split_display_bibliography
from .taxonomy import expand_selected_categories
from .work_types import (
    SUPPORTED_CANONICAL_WORK_TYPES,
    WorkTypeSignal,
    is_supported_work_type,
    resolve_work_type,
    resolve_work_type_evidence,
)


@dataclass(frozen=True, slots=True)
class SearchOptions:
    category_ids: tuple[str, ...] = ()
    match: str = "any"
    year_from: int | None = None
    year_to: int | None = None
    work_types: tuple[str, ...] = ()
    limit: int | None = None
    offset: int = 0
    max_candidates: int = 1000
    max_fallbacks: int = 100

    def validate(self) -> None:
        if self.match not in {"any", "all"}:
            raise ValueError("match must be any or all")
        for year in (self.year_from, self.year_to):
            if year is not None and not 1 <= year <= 9999:
                raise ValueError("years must be between 1 and 9999")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must not exceed year_to")
        if (
            (self.limit is not None and self.limit < 1)
            or self.offset < 0
            or self.max_candidates < 1
            or self.max_fallbacks < 0
        ):
            raise ValueError("invalid search result or request budget")
        if set(self.work_types).difference(SUPPORTED_CANONICAL_WORK_TYPES):
            raise ValueError("search accepts supported paper types only")

    @property
    def filters_years(self) -> bool:
        return self.year_from is not None or self.year_to is not None


@dataclass(frozen=True, slots=True)
class PaperSearchItem:
    title: str
    authors: tuple[str, ...]
    doi: str | None
    openalex_id: str | None
    source_urls: tuple[str, ...]
    category_ids: tuple[str, ...]
    matched_category_ids: tuple[str, ...]
    work_type: str
    type_evidence: tuple[WorkTypeEvidence, ...]
    publication_year: int | None
    year_source: str | None
    citation_count: int | None
    citation_source: str | None
    citation_retrieved_at: datetime | None


@dataclass(frozen=True, slots=True)
class UnconfirmedSearchRecord:
    """Existing source evidence for optional user review, never a retry queue."""

    title: str
    source_urls: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class PaperSearchResult:
    queried_at: datetime
    profile_id: str
    profile_version: int
    taxonomy_snapshot_id: str
    options: SearchOptions
    selected_categories: tuple[tuple[str, str], ...]
    expanded_category_ids: tuple[str, ...]
    category_names: dict[str, str]
    excluded_category_ids: tuple[str, ...]
    items: tuple[PaperSearchItem, ...]
    total_matches: int
    next_offset: int | None
    stats: dict[str, int]
    limitations: tuple[str, ...]
    source_failures: tuple[str, ...]
    unconfirmed_records: tuple[UnconfirmedSearchRecord, ...] = ()
    coverage: str = "current_category_feed_inventory"
    state_mutated: bool = False


def _identity(candidate: MergedCandidate, work: OpenAlexWork | CrossrefWork) -> IdentityMatchLevel:
    if candidate.doi_hints and work.doi and work.doi not in candidate.doi_hints:
        return IdentityMatchLevel.DISTINCT
    display = split_display_bibliography(candidate.display_title)
    return compare_bibliographic_identity(
        display.title,
        display.author_text,
        work.title,
        work.authors,
        shared_identifier=work.doi is not None and work.doi in candidate.doi_hints,
    ).level


def _signals(candidate: MergedCandidate) -> tuple[WorkTypeSignal, ...]:
    display = split_display_bibliography(candidate.display_title)
    explicit = tuple(
        WorkTypeSignal(
            "philpapers-title-label", kind, candidate.source_id, "explicit-bibliographic-label"
        )
        for kind, pattern in EXPLICIT_UNSUPPORTED_TITLE_HINTS
        if pattern.search(display.title)
    )
    return explicit + tuple(
        WorkTypeSignal(
            "philpapers-rss-description", kind, candidate.source_id, "explicit-bibliographic-label"
        )
        for kind in candidate.work_type_hints
    )


def _year(value: DateValue | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value.value)[:4])
    except ValueError:
        return None


def _make_item(
    candidate: MergedCandidate,
    work: OpenAlexWork | CrossrefWork | None,
    queried_at: datetime,
    supplementary: CrossrefWork | None = None,
) -> tuple[PaperSearchItem | None, str | None]:
    signals = _signals(candidate)
    for metadata in (work, supplementary):
        if metadata is not None and metadata.work_type:
            source = "openalex" if isinstance(metadata, OpenAlexWork) else "crossref"
            record_id = metadata.openalex_id if isinstance(metadata, OpenAlexWork) else metadata.doi
            signals += (WorkTypeSignal(source, metadata.work_type, record_id),)
    resolved = resolve_work_type(signals, default_type="unknown")
    if not resolved.usable or resolved.work_type == "unknown":
        return None, "type_unverified"
    display = split_display_bibliography(candidate.display_title)
    year = _year(work.publication_date) if work else None
    year_source = work.publication_date.source if work and year is not None else None
    if year is None and supplementary:
        year = _year(supplementary.publication_date)
        year_source = supplementary.publication_date.source if year is not None else None
    count = work.cited_by_count if isinstance(work, OpenAlexWork) else None
    return PaperSearchItem(
        title=work.title if work else display.title,
        authors=work.authors if work else ((display.author_text,) if display.author_text else ()),
        doi=work.doi
        if work
        else (candidate.doi_hints[0] if len(candidate.doi_hints) == 1 else None),
        openalex_id=work.openalex_id if isinstance(work, OpenAlexWork) else None,
        source_urls=(candidate.stable_url,),
        category_ids=tuple(sorted(candidate.category_ids)),
        matched_category_ids=(),
        work_type=resolved.work_type,
        type_evidence=resolved.evidence,
        publication_year=year,
        year_source=year_source,
        citation_count=count,
        citation_source="OpenAlex" if count is not None else None,
        citation_retrieved_at=(work.retrieved_at or queried_at)
        if isinstance(work, OpenAlexWork) and count is not None
        else None,
    ), None


def _merge_items(items: list[PaperSearchItem], stats: Counter) -> list[PaperSearchItem]:
    """Union proven identifiers before evaluating all-tag and exclusion filters."""
    groups: list[PaperSearchItem] = []
    for item in items:
        matches = [
            index
            for index, previous in enumerate(groups)
            if (item.doi and item.doi == previous.doi)
            or (item.openalex_id and item.openalex_id == previous.openalex_id)
            or set(item.source_urls).intersection(previous.source_urls)
        ]
        for index in reversed(matches):
            previous = groups.pop(index)
            resolution = resolve_work_type_evidence(
                previous.type_evidence + item.type_evidence, default_type="unknown"
            )
            # Conflicting citation observations are unknown, never added together.
            counts = {c for c in (item.citation_count, previous.citation_count) if c is not None}
            count = next(iter(counts)) if len(counts) == 1 else None
            years = {y for y in (item.publication_year, previous.publication_year) if y is not None}
            year = next(iter(years)) if len(years) == 1 else None
            item = replace(
                item,
                doi=item.doi or previous.doi,
                openalex_id=item.openalex_id or previous.openalex_id,
                source_urls=tuple(sorted(set(item.source_urls + previous.source_urls))),
                category_ids=tuple(sorted(set(item.category_ids + previous.category_ids))),
                work_type=resolution.work_type,
                type_evidence=resolution.evidence,
                publication_year=year,
                year_source=(item.year_source or previous.year_source) if year else None,
                citation_count=count,
                citation_source="OpenAlex" if count is not None else None,
                citation_retrieved_at=(item.citation_retrieved_at or previous.citation_retrieved_at)
                if count is not None
                else None,
            )
            stats["duplicate_records_merged"] += 1
        groups.append(item)
    return groups


def run_paper_search(
    config: WatchlistConfig,
    *,
    options: SearchOptions | None = None,
    now: datetime | None = None,
    feed_loader: FeedLoader = load_philpapers_feed,
    doi_lookup: Callable = find_works_by_dois,
    title_lookup: Callable = find_works_by_titles,
    fallback_lookup: Callable = find_crossref_work,
    progress: ProgressReporter | None = None,
    allow_development_fixture: bool = False,
) -> PaperSearchResult:
    """Search configured interests; dependencies may be injected for offline tests."""
    options = options or SearchOptions()
    options.validate()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("search time must include a timezone")
    if allow_development_fixture and feed_loader is load_philpapers_feed:
        raise ValueError("development taxonomy requires an injected offline feed loader")
    taxonomy, profile = _load_runtime(
        config, now=now, allow_development_fixture=allow_development_fixture
    )
    selected_ids = options.category_ids or tuple(c.category_id for c in config.confirmed_categories)
    if not selected_ids:
        raise ValueError("search needs confirmed interest categories")
    if set(selected_ids).difference(profile.expanded_category_ids):
        raise ValueError("temporary categories must belong to the configured interest scope")
    flags = {c.category_id: c.include_descendants for c in config.confirmed_categories}
    groups = tuple(
        expand_selected_categories(
            taxonomy, {category_id: flags.get(category_id, False)}
        ).intersection(profile.expanded_category_ids)
        for category_id in dict.fromkeys(selected_ids)
    )
    scope = frozenset().union(*groups)
    # Read the configured membership feeds, including known exclusions, before
    # narrowing the candidates. A remote feed is not an exhaustive category set.
    feeds = _load_all_feeds(
        config,
        checked_at=now,
        loader=feed_loader,
        progress=(lambda done, total: progress("feeds", done, total)) if progress else None,
    )
    merged = merge_feed_snapshots(feeds)
    candidates = [c for c in merged if c.category_ids.intersection(scope)]
    stats = Counter(
        {
            "feeds_checked": len(feeds),
            "source_records": len(merged),
            "scope_candidates": len(candidates),
            "no_feed_year_hint": 0,
            "type_unverified": 0,
            "unsupported_type": 0,
            "identity_unverified": 0,
            "fallback_not_reached": 0,
            "fallback_requests": 0,
            "excluded_category": 0,
            "year_unknown": 0,
            "filtered_out": 0,
            "duplicate_records_merged": 0,
            "excluded_categories_not_loaded": len(
                profile.excluded_category_ids.difference(feed.category_id for feed in feeds)
            ),
        }
    )
    # Only a year-bounded search excludes missing feed year hints. An undated
    # record remains eligible for a tag-only search, subject to paper evidence.
    if options.filters_years:
        stats["no_feed_year_hint"] = sum(not c.feed_year_hints for c in candidates)
        candidates = [c for c in candidates if c.feed_year_hints]
    candidates = [c for c in candidates if not _reject_explicit(c, stats)]
    if len(candidates) > options.max_candidates:
        raise PipelineError(
            f"search has {len(candidates)} candidates, exceeding max_candidates="
            f"{options.max_candidates}; narrow categories or explicitly raise the budget"
        )
    failures: set[str] = set()
    openalex: dict[str, OpenAlexWork] = {}
    items: list[PaperSearchItem] = []
    unconfirmed: list[UnconfirmedSearchRecord] = []

    def record_unconfirmed(candidate: MergedCandidate, reason: str) -> None:
        unconfirmed.append(
            UnconfirmedSearchRecord(
                split_display_bibliography(candidate.display_title).title,
                (candidate.stable_url,),
                reason,
            )
        )

    openalex_blocked = False
    crossref_blocked = False
    with httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT}, follow_redirects=True, timeout=30
    ) as client:
        dois = tuple(sorted({d for c in candidates for d in c.doi_hints}))
        for offset in range(0, len(dois), MAX_DOI_BATCH_SIZE):
            try:
                found = doi_lookup(
                    dois[offset : offset + MAX_DOI_BATCH_SIZE],
                    mailto=config.openalex_mailto,
                    client=client,
                )
                openalex.update((w.openalex_id, w) for w in found.values())
            except OpenAlexError as error:
                failures.add("OpenAlex: DOI lookup failed; citation data may be missing")
                openalex_blocked = _bibliographic_error_opens_circuit(error)
            if progress:
                progress("doi_batch", min(offset + MAX_DOI_BATCH_SIZE, len(dois)), len(dois))
            if openalex_blocked:
                break
        titles = tuple(
            sorted(
                {
                    split_display_bibliography(c.display_title).title
                    for c in candidates
                    if not any(
                        _identity(c, w) is IdentityMatchLevel.EQUIVALENT for w in openalex.values()
                    )
                }
            )
        )
        if not openalex_blocked:
            for offset in range(0, len(titles), MAX_TITLE_BATCH_SIZE):
                try:
                    found = title_lookup(
                        titles[offset : offset + MAX_TITLE_BATCH_SIZE],
                        mailto=config.openalex_mailto,
                        client=client,
                    )
                    openalex.update((w.openalex_id, w) for w in found)
                except OpenAlexError as error:
                    failures.add("OpenAlex: title lookup failed; citation data may be missing")
                    openalex_blocked = _bibliographic_error_opens_circuit(error)
                if progress:
                    progress(
                        "title_batch", min(offset + MAX_TITLE_BATCH_SIZE, len(titles)), len(titles)
                    )
                if openalex_blocked:
                    break
        for index, candidate in enumerate(candidates, 1):
            equivalent = [
                w
                for w in openalex.values()
                if _identity(candidate, w) is IdentityMatchLevel.EQUIVALENT
            ]
            work: OpenAlexWork | CrossrefWork | None = None
            if len(equivalent) > 1 or len(candidate.doi_hints) > 1:
                stats["identity_unverified"] += 1
                record_unconfirmed(candidate, "identity_unverified")
                continue
            if len(equivalent) == 1:
                work = equivalent[0]
            supplementary = None
            # Metadata is needed to establish paper form; missing citation
            # counts alone never trigger extra individual requests.
            native, _ = _make_item(candidate, work, now)
            if native is None or (
                options.filters_years
                and native.publication_year is None
                and is_supported_work_type(native.work_type)
            ):
                if crossref_blocked or stats["fallback_requests"] >= options.max_fallbacks:
                    stats["fallback_not_reached"] += 1
                else:
                    display = split_display_bibliography(candidate.display_title)
                    stats["fallback_requests"] += 1
                    try:
                        fallback = fallback_lookup(
                            display.title,
                            first_author=display.author_text,
                            mailto=config.crossref_mailto,
                            client=client,
                        )
                        if fallback is not None and (
                            _identity(candidate, fallback) is not IdentityMatchLevel.EQUIVALENT
                            or (work is not None and work.doi and fallback.doi != work.doi)
                        ):
                            stats["identity_unverified"] += 1
                            record_unconfirmed(candidate, "identity_unverified")
                            continue
                        if work is None:
                            work = fallback
                        else:
                            supplementary = fallback
                    except IdentityReviewRequired:
                        stats["identity_unverified"] += 1
                        record_unconfirmed(candidate, "identity_unverified")
                        continue
                    except CrossrefError as error:
                        failures.add("Crossref: paper metadata lookup failed")
                        crossref_blocked = _bibliographic_error_opens_circuit(error)
            item, reason = _make_item(candidate, work, now, supplementary)
            if item is not None:
                items.append(item)
            elif reason:
                stats[reason] += 1
                record_unconfirmed(candidate, reason)
            if progress:
                progress("candidates", index, len(candidates))
    if progress:
        progress("candidates", len(candidates), len(candidates))
    matched: list[PaperSearchItem] = []
    for item in _merge_items(items, stats):
        paper_ids = frozenset(item.category_ids)
        if paper_ids.intersection(profile.excluded_category_ids):
            stats["excluded_category"] += 1
            continue
        matches = [bool(paper_ids.intersection(group)) for group in groups]
        if not (all(matches) if options.match == "all" else any(matches)):
            stats["filtered_out"] += 1
            continue
        if not is_supported_work_type(item.work_type):
            stats[
                "type_unverified"
                if item.work_type in {"unknown", "conflict"}
                else "unsupported_type"
            ] += 1
            if item.work_type in {"unknown", "conflict"}:
                unconfirmed.append(
                    UnconfirmedSearchRecord(item.title, item.source_urls, "type_unverified")
                )
            continue
        if options.work_types and item.work_type not in options.work_types:
            stats["filtered_out"] += 1
            continue
        if options.filters_years:
            if item.publication_year is None:
                stats["year_unknown"] += 1
                continue
            if (options.year_from is not None and item.publication_year < options.year_from) or (
                options.year_to is not None and item.publication_year > options.year_to
            ):
                stats["filtered_out"] += 1
                continue
        matched.append(
            replace(item, matched_category_ids=tuple(sorted(paper_ids.intersection(scope))))
        )
    matched.sort(
        key=lambda item: (
            item.citation_count is None,
            -(item.citation_count or 0),
            -(item.publication_year or 0),
            item.title.casefold(),
            item.source_urls,
        )
    )
    stats["citation_missing"] = sum(item.citation_count is None for item in matched)
    stats["citation_available"] = len(matched) - stats["citation_missing"]
    limit = options.limit if options.limit is not None else (20 if options.filters_years else None)
    page_end = options.offset + limit if limit is not None else None
    page = tuple(matched[options.offset : page_end])
    next_offset = options.offset + len(page)
    return PaperSearchResult(
        queried_at=now,
        profile_id=config.profile_id,
        profile_version=config.profile_version,
        taxonomy_snapshot_id=taxonomy.snapshot_id,
        options=options,
        selected_categories=tuple(
            (cid, taxonomy.categories[cid].category_name) for cid in dict.fromkeys(selected_ids)
        ),
        expanded_category_ids=tuple(sorted(scope)),
        category_names={cid: taxonomy.categories[cid].category_name for cid in sorted(scope)},
        excluded_category_ids=tuple(sorted(profile.excluded_category_ids)),
        items=page,
        total_matches=len(matched),
        next_offset=next_offset if next_offset < len(matched) else None,
        stats=dict(stats),
        source_failures=tuple(sorted(failures)),
        unconfirmed_records=tuple(unconfirmed),
        limitations=(
            "范围是已配置 PhilPapers 分类 feed 本次返回的记录，不保证覆盖完整历史索引。",
            "标签交集及排除仅依据所读取 feed 的成员关系，不能证明一篇论文的完整分类集合。",
            "引用量来自 OpenAlex；已知值降序，缺失置后。同值按年份降序、题名和来源链接排列。",
            "只纳入有论文类型证据的记录；未知或冲突类型不默认视为论文。",
            (
                "本次限制年份：没有 feed 年份提示的记录直接排除，不进入补查或自行核对材料；"
                "年份提示不代替正式发表日期。"
                if options.filters_years
                else "本次不限制年份：符合标签且已确认论文类型与身份的记录均可纳入，"
                "不因缺少 feed 日期、年份提示或正式发表年份而排除。"
            ),
            "offset 是本次结果的偏移；重新运行会重新查询，来源变化可能影响跨次分页。",
        ),
    )


def _reject_explicit(candidate: MergedCandidate, stats: Counter) -> bool:
    if any(s.source == "philpapers-title-label" for s in _signals(candidate)):
        stats["unsupported_type"] += 1
        return True
    return False
