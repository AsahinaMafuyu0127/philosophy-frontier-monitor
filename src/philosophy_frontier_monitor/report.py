"""Factual Markdown weekly reports without quality judgments or ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from .models import InterestProfile, MatchDecision, MatchRecord, TaxonomySnapshot, WorkRecord


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source: str
    status: str
    checked_at: datetime
    detail: str


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
        lines.extend(
            [
                "## 尚待核验",
                "",
                f"另有 {unresolved_count} 条新增或重试记录尚未完成作品同一性、旧文重录或新近可得"
                "证据核验，本次未推送；程序已将其保留在限次重试队列中。",
                "",
            ]
        )

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
    lines[9:9] = notice
    if unresolved_count:
        method_index = lines.index("## 方法说明")
        lines[method_index:method_index] = [
            "## 本次未能完成核验",
            "",
            f"另有 {unresolved_count} 条候选记录尚未完成作品同一性、旧文重录或新近可得证据核验，"
            "本次不推送，也不写入周报的限次重试队列。下次主动拉取时可以重新核验。",
            "",
        ]
    return "\n".join(lines)
