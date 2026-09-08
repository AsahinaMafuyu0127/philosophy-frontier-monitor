"""Factual bilingual Markdown reports without quality judgments or ranking."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from html import escape

from .models import (
    InterestProfile,
    MatchDecision,
    MatchRecord,
    TaxonomySnapshot,
    WorkRecord,
    WorkTypeStatus,
)


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source: str
    status: str
    checked_at: datetime
    detail: str
    detail_en: str | None = None


@dataclass(frozen=True, slots=True)
class HumanReviewItem:
    title: str
    author_text: str | None
    stable_url: str | None
    reason_code: str


UNRESOLVED_REASON_LABELS = {
    "semantic_identity_review_required": "可能是译名或大幅改题版本，需要人工确认作品同一性",
    "identifier_conflict": "结构化来源返回相互冲突的 DOI 标识符",
    "structured_work_type_conflict": "结构化来源在支持论文形式与不支持形式之间冲突",
    "unknown_structured_work_type": "来源返回当前词表尚未识别的结构化作品类型",
    "old_work_check_incomplete": "外部书目来源失败，旧作检查等待程序自动重试",
}

UNRESOLVED_REASON_LABELS_EN = {
    "semantic_identity_review_required": (
        "The item may be a translated or substantially retitled version and requires manual "
        "identity review"
    ),
    "identifier_conflict": "Structured sources returned conflicting DOI identifiers",
    "structured_work_type_conflict": (
        "Structured sources conflict across supported and unsupported work types"
    ),
    "unknown_structured_work_type": (
        "A source returned a structured work type not recognized by the current vocabulary"
    ),
    "old_work_check_incomplete": (
        "An external bibliographic source failed; the old-work check awaits automatic retry"
    ),
}

HUMAN_REVIEW_REASON_CODES = frozenset(
    {
        "semantic_identity_review_required",
        "identifier_conflict",
        "structured_work_type_conflict",
        "unknown_structured_work_type",
    }
)
AUTOMATIC_RETRY_REASON_CODES = frozenset({"old_work_check_incomplete"})


def _unresolved_reason_lines(reason_counts: Mapping[str, int] | None) -> list[str]:
    if not reason_counts:
        return []
    return [
        f"- {label}：{reason_counts[reason_code]} 条"
        for reason_code, label in UNRESOLVED_REASON_LABELS.items()
        if reason_counts.get(reason_code, 0) > 0
    ]


def unresolved_workload_counts(
    reason_counts: Mapping[str, int] | None,
) -> tuple[int, int]:
    """Return human-review and automatic-retry counts without conflating them."""

    counts = reason_counts or {}
    human_review = sum(counts.get(reason_code, 0) for reason_code in HUMAN_REVIEW_REASON_CODES)
    automatic_retry = sum(
        counts.get(reason_code, 0) for reason_code in AUTOMATIC_RETRY_REASON_CODES
    )
    return human_review, automatic_retry


def _human_review_lines(items: tuple[HumanReviewItem, ...]) -> list[str]:
    if not items:
        return []
    lines = ["需要人工复核的候选：", ""]
    for index, item in enumerate(items[:9], start=1):
        reason = UNRESOLVED_REASON_LABELS.get(item.reason_code, item.reason_code)
        lines.extend(
            [
                f"{index}. {_safe_text(item.title)}",
                f"   - 作者：{_safe_text(item.author_text) if item.author_text else '未记录'}",
                f"   - 原因：{_safe_text(reason)}",
                f"   - 来源链接：{_safe_text(item.stable_url) if item.stable_url else '未记录'}",
            ]
        )
    if len(items) > 9:
        lines.append(f"- 另有 {len(items) - 9} 条人工复核候选未在本报告展开。")
    lines.append("")
    return lines


def _safe_text(value: str) -> str:
    """Render remote metadata as one inert Markdown text fragment."""

    collapsed = " ".join(value.split())
    escaped = escape(collapsed, quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "#"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _date_text(work: WorkRecord) -> str:
    if work.publication_date is None:
        return "未记录"
    value = work.publication_date.value
    rendered = value.isoformat() if isinstance(value, datetime) else str(value)
    return (
        f"{rendered}（精度：{work.publication_date.precision.value}；"
        f"来源：{work.publication_date.source}）"
    )


def _availability_text(work: WorkRecord) -> str:
    if work.availability_date is None:
        return "未记录"
    value = work.availability_date.value
    rendered = value.isoformat() if isinstance(value, datetime) else str(value)
    return (
        f"{rendered}（精度：{work.availability_date.precision.value}；"
        f"来源：{work.availability_date.source}）"
    )


def _date_text_en(work: WorkRecord) -> str:
    if work.publication_date is None:
        return "Not recorded"
    value = work.publication_date.value
    rendered = value.isoformat() if isinstance(value, datetime) else str(value)
    return (
        f"{rendered} (precision: {work.publication_date.precision.value}; "
        f"source: {work.publication_date.source})"
    )


def _availability_text_en(work: WorkRecord) -> str:
    if work.availability_date is None:
        return "Not recorded"
    value = work.availability_date.value
    rendered = value.isoformat() if isinstance(value, datetime) else str(value)
    return (
        f"{rendered} (precision: {work.availability_date.precision.value}; "
        f"source: {work.availability_date.source})"
    )


def _work_type_evidence_text(work: WorkRecord) -> str:
    if work.work_type_evidence:
        return "；".join(
            f"{_safe_text(item.source)}：{_safe_text(item.raw_type)}"
            + (
                f" → {_safe_text(item.normalized_type)}"
                if item.normalized_type and item.normalized_type != item.raw_type
                else ""
            )
            for item in work.work_type_evidence
        )
    if work.work_type_status is WorkTypeStatus.DEFAULTED:
        return "来源未提供可用的结构化类型；article 是提醒流候选默认值，不是来源断言"
    return "未记录"


def _work_type_evidence_text_en(work: WorkRecord) -> str:
    if work.work_type_evidence:
        return "; ".join(
            f"{_safe_text(item.source)}: {_safe_text(item.raw_type)}"
            + (
                f" -> {_safe_text(item.normalized_type)}"
                if item.normalized_type and item.normalized_type != item.raw_type
                else ""
            )
            for item in work.work_type_evidence
        )
    if work.work_type_status is WorkTypeStatus.DEFAULTED:
        return (
            "The sources supplied no usable structured work type; article is the alert-stream "
            "candidate default, not a source assertion"
        )
    return "Not recorded"


def _category_names(category_ids: frozenset[str], snapshot: TaxonomySnapshot) -> list[str]:
    return sorted(
        snapshot.categories[category_id].category_name
        if category_id in snapshot.categories
        else f"未知分类 ID {category_id}"
        for category_id in category_ids
    )


def render_weekly_report(
    *,
    profile: InterestProfile,
    snapshot: TaxonomySnapshot,
    works: dict[str, WorkRecord],
    matches: tuple[MatchRecord, ...],
    window_start: datetime,
    window_end: datetime,
    coverage: tuple[SourceCoverage, ...],
    unresolved_count: int = 0,
    unresolved_reason_counts: Mapping[str, int] | None = None,
    machine_deferred_count: int = 0,
    human_review_items: tuple[HumanReviewItem, ...] = (),
    include_english: bool = True,
) -> str:
    """Render only works whose deterministic decision is ``notify``."""

    selected_names = [item.category_name for item in profile.selected_categories]
    notifying = [item for item in matches if item.decision is MatchDecision.NOTIFY]
    lines = [
        "# 哲学前沿论文周报",
        "",
        f"监测窗口：`{window_start.isoformat()}` 至 `{window_end.isoformat()}`（右端不含）",
        f"兴趣配置：`{profile.profile_id}` 第 {profile.version} 版",
        f"taxonomy 快照：`{snapshot.snapshot_id}`",
        f"用户选定分类：{', '.join(selected_names) if selected_names else '无'}",
        "",
        "本报告只按“确认在窗口内新出”与“受控分类集合存在交集”两项规则收录，"
        "不评价论文质量，也不按作者、期刊或引用量排序。",
        "",
        f"## 匹配论文（{len(notifying)}）",
        "",
    ]

    if not notifying:
        successful = any(item.status == "success" for item in coverage)
        failed = any(item.status != "success" for item in coverage)
        if successful and not failed:
            lines.extend(
                [
                    "本周在已成功检查的数据源中，没有发现同时通过新论文门槛和标签门槛的作品。",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "本次没有形成可通知记录；由于至少一个来源未成功检查，这不等于本周没有相关新作。",
                    "",
                ]
            )
    else:
        for index, match in enumerate(notifying, start=1):
            work = works.get(match.work_id)
            if work is None:
                raise ValueError(f"missing WorkRecord for match {match.match_id}")
            authors = (
                "；".join(_safe_text(item) for item in work.authors) if work.authors else "未记录"
            )
            matched_names = _category_names(match.matched_category_ids, snapshot)
            paper_names = _category_names(match.paper_category_ids, snapshot)
            identifier = f"https://doi.org/{work.doi}" if work.doi else work.stable_url
            lines.extend(
                [
                    f"### {index}. {_safe_text(work.title)}",
                    "",
                    f"- 作者：{authors}",
                    "- 发表载体："
                    + (_safe_text(work.container_title) if work.container_title else "未记录"),
                    f"- 作品类型：{_safe_text(work.work_type)}",
                    f"- 类型证据状态：{work.work_type_status.value}",
                    f"- 类型证据：{_work_type_evidence_text(work)}",
                    f"- 新出事件：{work.freshness_event or '已确认，但未记录事件子类'}",
                    f"- 发表日期证据：{_date_text(work)}",
                    f"- 新近可得证据：{_availability_text(work)}",
                    f"- 命中的兴趣分类：{'; '.join(matched_names)}",
                    f"- 已核验的论文分类：{'; '.join(paper_names)}",
                    f"- DOI／稳定链接：{_safe_text(identifier) if identifier else '未记录'}",
                    "",
                ]
            )

    if unresolved_count:
        human_review_count, automatic_retry_count = unresolved_workload_counts(
            unresolved_reason_counts
        )
        unclassified_count = max(
            0,
            unresolved_count - machine_deferred_count - human_review_count - automatic_retry_count,
        )
        lines.extend(
            [
                "## 核验状态",
                "",
                f"另有 {unresolved_count} 条新增或重试记录尚未完成作品类型、作品同一性、旧文重录或"
                "新近可得证据核验，本次未推送；程序已将其保留在限次重试队列中。",
                "",
            ]
        )
        if machine_deferred_count:
            lines.extend(
                [
                    f"- 机器核验积压：{machine_deferred_count} 条。它们只是尚未轮到外部书目核验，"
                    "不要求用户逐篇判断。",
                ]
            )
        if human_review_count:
            lines.append(f"- 需要人工复核：{human_review_count} 条。")
        if automatic_retry_count:
            lines.append(f"- 等待程序自动重试：{automatic_retry_count} 条。")
        if unclassified_count:
            lines.append(f"- 尚未分类的内部核验状态：{unclassified_count} 条。")
        lines.append("")
        reason_lines = _unresolved_reason_lines(unresolved_reason_counts)
        if reason_lines:
            lines.extend(["具体原因：", "", *reason_lines, ""])
        lines.extend(_human_review_lines(human_review_items))

    lines.extend(["## 数据源覆盖", ""])
    if not coverage:
        lines.extend(["- 未提供来源运行记录；不能断言本次监测覆盖完整。", ""])
    else:
        for item in coverage:
            lines.append(
                f"- `{item.source}`：{item.status}；检查时间 `{item.checked_at.isoformat()}`；"
                f"{item.detail}"
            )
        lines.append("")

    lines.extend(
        [
            "## 方法说明",
            "",
            "一篇作品只有在 `freshness_status = confirmed_new`（独立日期确认）或 "
            "`confirmed_source_arrival`（PhilPapers 新近到达且未发现旧作证据），并且其分类 ID 与"
            "当前兴趣集合存在交集时，才会出现在上方列表中。后一状态只说明新近进入研究者可见的"
            "前沿材料流，不把 feed 或抓取时间伪装成正式发表日期。",
            "",
            "## 数据源能力边界",
            "",
            "本报告只能反映运行时成功返回的已配置 PhilPapers 分类 feed，以及当时能够取得的"
            "外部书目元数据。尚未被来源收录、来源更新延迟或关键元数据缺失的作品可能延后出现；"
            "系统会披露失败来源和未完成核验数量，但不承诺对全球哲学新作的穷尽覆盖。",
            "",
        ]
    )
    chinese = "\n".join(lines)
    if not include_english:
        return chinese
    english = _render_weekly_report_en(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=matches,
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=unresolved_count,
        unresolved_reason_counts=unresolved_reason_counts,
        machine_deferred_count=machine_deferred_count,
        human_review_items=human_review_items,
    )
    return f"{chinese}\n\n---\n\n{english}"


def _render_weekly_report_en(
    *,
    profile: InterestProfile,
    snapshot: TaxonomySnapshot,
    works: dict[str, WorkRecord],
    matches: tuple[MatchRecord, ...],
    window_start: datetime,
    window_end: datetime,
    coverage: tuple[SourceCoverage, ...],
    unresolved_count: int = 0,
    unresolved_reason_counts: Mapping[str, int] | None = None,
    machine_deferred_count: int = 0,
    human_review_items: tuple[HumanReviewItem, ...] = (),
) -> str:
    """Render the English version of a weekly report from the same evidence."""

    selected_names = [item.category_name for item in profile.selected_categories]
    notifying = [item for item in matches if item.decision is MatchDecision.NOTIFY]
    lines = [
        "# Philosophy Frontier Weekly Report",
        "",
        f"Monitoring window: `{window_start.isoformat()}` to `{window_end.isoformat()}` "
        "(exclusive end)",
        f"Interest profile: `{profile.profile_id}` version {profile.version}",
        f"Taxonomy snapshot: `{snapshot.snapshot_id}`",
        f"Selected categories: {', '.join(selected_names) if selected_names else 'None'}",
        "",
        "This report includes papers only when they are confirmed as new within the window and "
        "their controlled category set intersects the user's confirmed interest set. It does "
        "not evaluate paper quality or rank by author, journal, or citation count.",
        "",
        f"## Matching papers ({len(notifying)})",
        "",
    ]

    if not notifying:
        successful = any(item.status == "success" for item in coverage)
        failed = any(item.status != "success" for item in coverage)
        if successful and not failed:
            lines.extend(
                [
                    "No work passed both the new-paper and category gates in the sources "
                    "successfully checked for this week.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "No notifiable record was established. Because at least one source could "
                    "not be checked successfully, this does not mean that no relevant new work "
                    "exists this week.",
                    "",
                ]
            )
    else:
        for index, match in enumerate(notifying, start=1):
            work = works.get(match.work_id)
            if work is None:
                raise ValueError(f"missing WorkRecord for match {match.match_id}")
            authors = "; ".join(_safe_text(item) for item in work.authors) or "Not recorded"
            matched_names = _category_names(match.matched_category_ids, snapshot)
            paper_names = _category_names(match.paper_category_ids, snapshot)
            identifier = f"https://doi.org/{work.doi}" if work.doi else work.stable_url
            lines.extend(
                [
                    f"### {index}. {_safe_text(work.title)}",
                    "",
                    f"- Authors: {authors}",
                    "- Venue: "
                    + (
                        _safe_text(work.container_title)
                        if work.container_title
                        else "Not recorded"
                    ),
                    f"- Work type: {_safe_text(work.work_type)}",
                    f"- Work-type evidence status: {work.work_type_status.value}",
                    f"- Work-type evidence: {_work_type_evidence_text_en(work)}",
                    "- Freshness event: "
                    + (work.freshness_event or "Confirmed; subtype not recorded"),
                    f"- Publication-date evidence: {_date_text_en(work)}",
                    f"- Recent-availability evidence: {_availability_text_en(work)}",
                    f"- Matching interest categories: {'; '.join(matched_names)}",
                    f"- Verified paper categories: {'; '.join(paper_names)}",
                    "- DOI or stable link: "
                    + (_safe_text(identifier) if identifier else "Not recorded"),
                    "",
                ]
            )

    if unresolved_count:
        human_review_count, automatic_retry_count = unresolved_workload_counts(
            unresolved_reason_counts
        )
        unclassified_count = max(
            0,
            unresolved_count - machine_deferred_count - human_review_count - automatic_retry_count,
        )
        lines.extend(
            [
                "## Verification status",
                "",
                f"Another {unresolved_count} new or retried records have not completed work-type, "
                "bibliographic-identity, old-work, or recent-availability verification. They were "
                "not notified and remain in the bounded retry queue.",
                "",
            ]
        )
        if machine_deferred_count:
            lines.append(
                f"- Machine-verification backlog: {machine_deferred_count}. These records have not "
                "yet reached external bibliographic verification and do not require item-by-item "
                "user judgment."
            )
        if human_review_count:
            lines.append(f"- Human review required: {human_review_count}.")
        if automatic_retry_count:
            lines.append(f"- Awaiting automatic retry: {automatic_retry_count}.")
        if unclassified_count:
            lines.append(f"- Unclassified internal verification states: {unclassified_count}.")
        lines.append("")
        counts = unresolved_reason_counts or {}
        reason_lines = [
            f"- {label}: {counts[reason_code]}"
            for reason_code, label in UNRESOLVED_REASON_LABELS_EN.items()
            if counts.get(reason_code, 0) > 0
        ]
        if reason_lines:
            lines.extend(["Reasons:", "", *reason_lines, ""])
        if human_review_items:
            lines.extend(["Candidates requiring human review:", ""])
            for index, item in enumerate(human_review_items[:9], start=1):
                reason = UNRESOLVED_REASON_LABELS_EN.get(item.reason_code, item.reason_code)
                lines.extend(
                    [
                        f"{index}. {_safe_text(item.title)}",
                        "   - Authors: "
                        + (_safe_text(item.author_text) if item.author_text else "Not recorded"),
                        f"   - Reason: {_safe_text(reason)}",
                        "   - Source link: "
                        + (_safe_text(item.stable_url) if item.stable_url else "Not recorded"),
                    ]
                )
            if len(human_review_items) > 9:
                lines.append(
                    f"- {len(human_review_items) - 9} additional human-review candidates are not "
                    "expanded in this report."
                )
            lines.append("")

    lines.extend(["## Source coverage", ""])
    if not coverage:
        lines.extend(
            ["- No source-run record was supplied; coverage cannot be claimed complete.", ""]
        )
    else:
        for item in coverage:
            detail = item.detail_en or item.detail
            lines.append(
                f"- `{item.source}`: {item.status}; checked at "
                f"`{item.checked_at.isoformat()}`; {_safe_text(detail)}"
            )
        lines.append("")

    lines.extend(
        [
            "## Method",
            "",
            "A work appears above only when `freshness_status = confirmed_new` (confirmed by "
            "independent date evidence) or `confirmed_source_arrival` (recent PhilPapers arrival "
            "with no old-work evidence), and its category IDs intersect the current interest set. "
            "The latter means that the work recently entered the researcher's visible frontier "
            "stream; it does not present a feed or retrieval time as the formal publication date.",
            "",
            "## Source limitations",
            "",
            "This report reflects only the configured PhilPapers category feeds that returned "
            "successfully at run time and the external bibliographic metadata then available. "
            "Works absent from a source, delayed by source updates, or missing critical metadata "
            "may appear later. Failed sources and unfinished verification are disclosed, but the "
            "system does not claim exhaustive coverage of all new philosophy papers worldwide.",
            "",
        ]
    )
    return "\n".join(lines)


def render_on_demand_report(
    *,
    profile: InterestProfile,
    snapshot: TaxonomySnapshot,
    works: dict[str, WorkRecord],
    matches: tuple[MatchRecord, ...],
    window_start: datetime,
    window_end: datetime,
    coverage: tuple[SourceCoverage, ...],
    unresolved_count: int = 0,
    unresolved_reason_counts: Mapping[str, int] | None = None,
    machine_deferred_count: int = 0,
    human_review_items: tuple[HumanReviewItem, ...] = (),
    oai_narrowing_applied: bool = False,
) -> str:
    """Render a state-independent rolling report for an explicit user request."""

    weekly = render_weekly_report(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=matches,
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=0,
        include_english=False,
    )
    lines = weekly.splitlines()
    lines[0] = "# 哲学前沿论文即时拉取报告"
    lines[2] = (
        f"即时检索窗口：`{window_start.isoformat()}` 至 `{window_end.isoformat()}`（右端不含）"
    )
    for index, line in enumerate(lines):
        if line == "本周在已成功检查的数据源中，没有发现同时通过新论文门槛和标签门槛的作品。":
            lines[index] = (
                "本次即时检索在已成功检查的数据源中，没有发现同时通过新论文门槛和标签门槛的作品。"
            )
    notice = [
        "本报告由用户主动请求生成，独立于正式周报。它不读取周报通知历史，也不写入或推进"
        "基线、重试队列、运行历史和 feed 检查点；同一篇论文以后再次出现在周报中属于正常现象。",
        "",
    ]
    if oai_narrowing_applied:
        notice.extend(
            [
                "本次启用了 PhilArchive OAI 增量筛选：完全没有 feed 时间和书目年份的记录只有在"
                "同一 `/rec/` 记录于窗口内发生变化时才继续核验。OAI 只覆盖开放记录，因此非开放"
                "且完全无日期的 PhilPapers 记录可能不在本次即时报告中；这不是永久的旧作判断。",
                "",
            ]
        )
    lines[9:9] = notice
    if unresolved_count:
        human_review_count, automatic_retry_count = unresolved_workload_counts(
            unresolved_reason_counts
        )
        unclassified_count = max(
            0,
            unresolved_count - machine_deferred_count - human_review_count - automatic_retry_count,
        )
        method_index = lines.index("## 方法说明")
        workload_lines = [
            "## 本次核验状态",
            "",
            f"另有 {unresolved_count} 条候选记录尚未完成作品类型、作品同一性、旧文重录或新近可得"
            "证据核验，"
            "本次不推送，也不写入周报的限次重试队列。下次主动拉取时可以重新核验。",
            "",
        ]
        if machine_deferred_count:
            workload_lines.extend(
                [
                    f"- 机器核验积压：{machine_deferred_count} 条。它们受本次逐篇查询预算限制，"
                    "尚未轮到外部书目核验，不要求用户逐篇判断。",
                ]
            )
        if human_review_count:
            workload_lines.append(f"- 需要人工复核：{human_review_count} 条。")
        if automatic_retry_count:
            workload_lines.append(f"- 等待程序自动重试：{automatic_retry_count} 条。")
        if unclassified_count:
            workload_lines.append(f"- 尚未分类的内部核验状态：{unclassified_count} 条。")
        workload_lines.append("")
        reason_lines = _unresolved_reason_lines(unresolved_reason_counts)
        if reason_lines:
            workload_lines.extend(
                [
                    "具体原因：",
                    "",
                    *reason_lines,
                    "",
                ]
            )
        workload_lines.extend(_human_review_lines(human_review_items))
        lines[method_index:method_index] = workload_lines
    chinese = "\n".join(lines)
    english = _render_on_demand_report_en(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=matches,
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=unresolved_count,
        unresolved_reason_counts=unresolved_reason_counts,
        machine_deferred_count=machine_deferred_count,
        human_review_items=human_review_items,
        oai_narrowing_applied=oai_narrowing_applied,
    )
    return f"{chinese}\n\n---\n\n{english}"


def _render_on_demand_report_en(
    *,
    profile: InterestProfile,
    snapshot: TaxonomySnapshot,
    works: dict[str, WorkRecord],
    matches: tuple[MatchRecord, ...],
    window_start: datetime,
    window_end: datetime,
    coverage: tuple[SourceCoverage, ...],
    unresolved_count: int = 0,
    unresolved_reason_counts: Mapping[str, int] | None = None,
    machine_deferred_count: int = 0,
    human_review_items: tuple[HumanReviewItem, ...] = (),
    oai_narrowing_applied: bool = False,
) -> str:
    """Render the English version of a state-independent on-demand report."""

    weekly = _render_weekly_report_en(
        profile=profile,
        snapshot=snapshot,
        works=works,
        matches=matches,
        window_start=window_start,
        window_end=window_end,
        coverage=coverage,
        unresolved_count=0,
    )
    lines = weekly.splitlines()
    lines[0] = "# Philosophy Frontier On-Demand Report"
    lines[2] = (
        f"On-demand window: `{window_start.isoformat()}` to `{window_end.isoformat()}` "
        "(exclusive end)"
    )
    weekly_zero = (
        "No work passed both the new-paper and category gates in the sources successfully "
        "checked for this week."
    )
    for index, line in enumerate(lines):
        if line == weekly_zero:
            lines[index] = (
                "No work passed both the new-paper and category gates in the sources "
                "successfully checked for this on-demand window."
            )
    notice = [
        "This report was generated by an explicit user request and is independent of the formal "
        "weekly report. It neither reads weekly notification history nor writes or advances the "
        "baseline, retry queue, run history, or feed checkpoints. It is therefore normal for the "
        "same paper to appear in a later weekly report.",
        "",
    ]
    if oai_narrowing_applied:
        notice.extend(
            [
                "PhilArchive OAI incremental narrowing was used. Feed records lacking both a "
                "timestamp and a bibliographic year continued to verification only when the same "
                "`/rec/` record changed within the window. OAI covers open records only, so fully "
                "undated non-open PhilPapers records may be absent from this report; this is not a "
                "permanent old-work judgment.",
                "",
            ]
        )
    lines[9:9] = notice

    if unresolved_count:
        human_review_count, automatic_retry_count = unresolved_workload_counts(
            unresolved_reason_counts
        )
        unclassified_count = max(
            0,
            unresolved_count - machine_deferred_count - human_review_count - automatic_retry_count,
        )
        method_index = lines.index("## Method")
        workload_lines = [
            "## Verification status for this request",
            "",
            f"Another {unresolved_count} candidate records have not completed work-type, "
            "bibliographic-identity, old-work, or recent-availability verification. They were not "
            "reported as matches and were not written to the weekly bounded retry queue; a later "
            "explicit pull may verify them again.",
            "",
        ]
        if machine_deferred_count:
            workload_lines.append(
                f"- Machine-verification backlog: {machine_deferred_count}. The per-item query "
                "budget prevented them from reaching external bibliographic verification in this "
                "run; they do not require item-by-item user judgment."
            )
        if human_review_count:
            workload_lines.append(f"- Human review required: {human_review_count}.")
        if automatic_retry_count:
            workload_lines.append(f"- Awaiting automatic retry: {automatic_retry_count}.")
        if unclassified_count:
            workload_lines.append(
                f"- Unclassified internal verification states: {unclassified_count}."
            )
        workload_lines.append("")
        counts = unresolved_reason_counts or {}
        reason_lines = [
            f"- {label}: {counts[reason_code]}"
            for reason_code, label in UNRESOLVED_REASON_LABELS_EN.items()
            if counts.get(reason_code, 0) > 0
        ]
        if reason_lines:
            workload_lines.extend(["Reasons:", "", *reason_lines, ""])
        if human_review_items:
            workload_lines.extend(["Candidates requiring human review:", ""])
            for index, item in enumerate(human_review_items[:9], start=1):
                reason = UNRESOLVED_REASON_LABELS_EN.get(item.reason_code, item.reason_code)
                workload_lines.extend(
                    [
                        f"{index}. {_safe_text(item.title)}",
                        "   - Authors: "
                        + (_safe_text(item.author_text) if item.author_text else "Not recorded"),
                        f"   - Reason: {_safe_text(reason)}",
                        "   - Source link: "
                        + (_safe_text(item.stable_url) if item.stable_url else "Not recorded"),
                    ]
                )
            if len(human_review_items) > 9:
                workload_lines.append(
                    f"- {len(human_review_items) - 9} additional human-review candidates are not "
                    "expanded in this report."
                )
            workload_lines.append("")
        lines[method_index:method_index] = workload_lines
    return "\n".join(lines)
