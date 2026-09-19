"""Render historical search with visible scope, missing data, and pagination."""

from __future__ import annotations

from .report import _safe_text
from .search import PaperSearchResult


def render_paper_search(result: PaperSearchResult) -> str:
    options = result.options
    selected = "；".join(_safe_text(name) for _, name in result.selected_categories)
    lines = [
        "# 兴趣论文检索",
        "",
        f"检索时间：{result.queried_at.isoformat()}",
        "",
        f"兴趣标签：{selected}",
        "",
        f"匹配方式：{'同时满足所有所选标签' if options.match == 'all' else '满足任一所选标签'}；"
        f"年份：{options.year_from or '不限'}—{options.year_to or '不限'}；"
        f"论文类型：{', '.join(options.work_types) or '项目支持的论文类型'}。",
        "",
        f"本次核验结果中符合条件的论文共 **{result.total_matches}** 篇；"
        f"本页显示 **{len(result.items)}** 篇。",
        "",
        f"引用量已取得 {result.stats['citation_available']} 篇，"
        f"引用量缺失 {result.stats['citation_missing']} 篇（统计范围为全部匹配结果）。",
        "",
    ]
    if result.excluded_category_ids:
        lines += [f"排除分类 ID：{', '.join(result.excluded_category_ids)}。", ""]
    for index, item in enumerate(result.items, start=options.offset + 1):
        citation = (
            f"{item.citation_count}（OpenAlex；取得时间：{item.citation_retrieved_at.isoformat()}）"
            if item.citation_count is not None
            else "缺失（未取得可用数据）"
        )
        lines += [
            f"## {index}. {_safe_text(item.title)}",
            "",
            f"作者：{_safe_text('; '.join(item.authors)) if item.authors else '缺失'}",
            "",
            f"年份：{item.publication_year if item.publication_year is not None else '缺失'}"
            f"；年份来源：{item.year_source or '缺失'}；类型：{item.work_type}。",
            "",
            f"引用量：{citation}",
            "",
            "匹配分类："
            + "；".join(
                f"{_safe_text(result.category_names[cid])}（{cid}）"
                for cid in item.matched_category_ids
            )
            + "。",
            "",
            "来源："
            + "、".join(
                f"[PhilPapers 记录 {i}]({url})" for i, url in enumerate(item.source_urls, 1)
            ),
            "",
        ]
    if result.next_offset is not None:
        lines += [f"尚有后续结果；下一页使用 `--offset {result.next_offset}`。", ""]
    lines += ["## 覆盖范围与缺失情况", ""]
    lines += [f"- {line}" for line in result.limitations]
    unresolved = []
    for key, label in (
        ("type_unverified", "论文类型"),
        ("identity_unverified", "书目身份"),
    ):
        if result.stats.get(key, 0):
            unresolved.append(label)
    if unresolved:
        lines += [
            f"- 存在{'或'.join(unresolved)}尚未确认的记录，未纳入论文结果。"
            "如有需要，用户可根据原始来源自行核对论文类型与书目身份；"
            "这是可选事项，skill 不再为这些记录继续补查或重新运行检索。"
        ]
    if result.stats.get("year_unknown", 0):
        lines += [
            "- 有记录虽带 feed 年份提示，但缺少筛选所需的正式发表年份，未纳入年份范围内的结果。"
        ]
    if result.stats.get("excluded_categories_not_loaded", 0):
        lines += ["- 部分排除分类未读取成员记录，无法完整核验这些排除项。"]
    lines += [f"- 来源异常：{_safe_text(failure)}。" for failure in result.source_failures]
    if result.unconfirmed_records:
        lines += ["", "<details>", "<summary>自行核对原始记录（可选）</summary>", ""]
        for record in result.unconfirmed_records:
            reason = "论文类型未确认" if record.reason == "type_unverified" else "书目身份未确认"
            sources = "、".join(f"[PhilPapers 原始记录]({url})" for url in record.source_urls)
            lines += [f"- {_safe_text(record.title)}：{reason}；{sources}。"]
        lines += ["", "以上仅列出本次已经取得的来源材料，供自行核对。", "", "</details>"]
    lines += ["", "检索不写入订阅配置、周报通知历史或缓存。", ""]
    return "\n".join(lines)
