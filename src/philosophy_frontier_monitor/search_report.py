"""Render historical search with visible scope, missing data, and pagination."""

from __future__ import annotations

from urllib.parse import quote

from .official_journals import OfficialIssue, render_issue_leads
from .report import _safe_text
from .search import PaperSearchResult


def render_paper_search(
    result: PaperSearchResult, *, official_issues: tuple[OfficialIssue, ...] = (),
    official_issue_status: str = "local_only",
) -> str:
    if official_issue_status not in {"local_only", "failed"}:
        raise ValueError("invalid official-issue source status")
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
    lines += [
        (
            "官方期刊监控库读取失败；本次未能列出已复核期次，不能据此认为期刊没有新期。"
            if official_issue_status == "failed" else
            "官方期刊监控库：本次只读取本地已复核期次，未逐站实时巡查；"
            "未列出不表示期刊没有新期。"
        ),
        "",
    ]
    if official_issues:
        lines += [render_issue_leads(official_issues).rstrip(), ""]
    if result.cnki_coverage is not None:
        lines += ["## 知网空间补充候选", ""]
        lines += [
            "以下题录由中文检索词发现，尚未完成作者发表时机构、论文类型、兴趣分类与跨库"
            "作品同一性核验，因此不计入上方已核验论文数或引用量排序。期号不等于月份。",
            "缺少知网日期时使用同篇、同期的刊方证据补充。标示月份、出版日与观察日分别处理；"
            "缺月或日期冲突的历史候选可保留，但不据此认定为近期论文。",
            "",
        ]
        if result.cnki_candidates:
            assessments = {item.cnki_url: item for item in result.cnki_assessments}
            overlaps = {item.cnki_url: item for item in result.cnki_overlaps}
            wanfang = (
                {item.cnki_url: item for item in result.wanfang_scan.checks}
                if result.wanfang_scan is not None
                else {}
            )
            for record in result.cnki_candidates[:30]:
                if record.year and record.label_month and not record.date_conflict:
                    issue = f"{record.year}年{record.label_month}月（来源标示）"
                    if record.issue and not record.issue.endswith("月"):
                        issue += f"、第{record.issue}期"
                elif record.year and record.issue:
                    issue = f"{record.year}年第{record.issue}期"
                else:
                    issue = str(record.year) if record.year else "年份未核实"
                lines.append(
                    f"- [{_safe_text(record.title)}]({record.url})；"
                    f"{_safe_text(record.venue)}；{issue}。"
                )
                if record.date_conflict:
                    lines.append("  - 日期证据冲突：月份未定，不据此归入近期窗口。")
                elif record.label_month is None:
                    lines.append("  - 出版月份未核实；仅保留已有年份／期号，不推算月份。")
                if record.date_evidence_urls:
                    links = "、".join(
                        f"[刊方日期证据]({url})" for url in record.date_evidence_urls
                    )
                    day = record.publication_date or "未明示日级日期"
                    lines.append(f"  - {links}；刊方标示出版日期：{day}。")
                assessment = assessments.get(record.url)
                if assessment is not None:
                    if assessment.status == "corroborated":
                        checked = "题名、作者、刊名、年份及期次已据所列来源人工核对"
                        if assessment.china_affiliated_authors:
                            checked += "；来源页面显示中国机构署名：" + _safe_text(
                                "、".join(assessment.china_affiliated_authors)
                            )
                    elif assessment.status == "conflict":
                        checked = "人工证据与知网题录存在字段冲突，需复核"
                    else:
                        checked = "人工证据尚不足以核对全部书目字段"
                    lines.append(f"  - {checked}：[来源页面]({assessment.evidence_url})。")
                overlap = overlaps.get(record.url)
                if overlap is not None:
                    label = (
                        "与本次所读 PhilPapers 已核验记录的题名、首作者和年份一致；尚未自动合并"
                        if overlap.status == "same_bibliography"
                        else "在本次所读 PhilPapers 已核验记录中有同题条目，身份仍待复核"
                    )
                    links = "、".join(
                        f"[PhilPapers {index}]({url})"
                        for index, url in enumerate(overlap.peer_urls, 1)
                    )
                    lines.append(f"  - {label}：{links}。")
                wanfang_check = wanfang.get(record.url)
                if wanfang_check is not None:
                    if wanfang_check.status == "corroborated":
                        label = "万方期刊题录的题名、作者、刊名、年份及期次相符"
                    elif wanfang_check.status == "partial":
                        label = "万方同题记录存在，但字段不足以完整核对"
                    elif wanfang_check.status == "conflict":
                        label = "万方同题记录存在字段冲突，需人工复核"
                    else:
                        label = "本次有限万方检索未核对到同题记录，不代表万方未收录"
                    identity = (
                        f"；万方记录 ID：`{wanfang_check.record_id}`"
                        if wanfang_check.record_id
                        else ""
                    )
                    lines.append(f"  - {label}{identity}。")
            if len(result.cnki_candidates) > 30:
                lines.append(f"- 另有 {len(result.cnki_candidates) - 30} 条候选未展开。")
        else:
            lines.append("本次未取得可列出的中文期刊候选；这不证明相关论文不存在。")
        lines += ["", f"检索覆盖：{_safe_text(result.cnki_coverage)}", ""]
        if result.wanfang_scan is not None:
            scan = result.wanfang_scan
            lines += [
                f"万方交叉核对：{scan.status}；尝试 {scan.attempted}/{scan.total_candidates} 条；"
                f"分页未完成 {scan.incomplete} 条；"
                f"失败原因：{scan.failure or '无'}。这只核对书目，不确认首次发表或兴趣分类。",
                "",
            ]
    if result.wanfang_discovery is not None:
        scan = result.wanfang_discovery
        lines += [
            "## 万方独立发现的中文期刊候选",
            "",
            "这些题录由已订阅的万方 Query 接口直接发现，不依赖知网空间命中。"
            "尚未核验发表时机构、兴趣分类、首次发表时间和跨来源作品同一性，"
            "不计入上方已核验论文。",
            "",
        ]
        for record in scan.records[:30]:
            link = "https://d.wanfangdata.com.cn/periodical/" + quote(record.record_id, safe="")
            lines.append(
                f"- [{_safe_text(record.title)}]({link})；"
                f"{_safe_text(record.venue or '刊名未记录')}；"
                f"{record.year or '年份未知'}年"
                f"{('第' + _safe_text(record.issue) + '期') if record.issue else ''}；"
                f"万方原始出版时间标示：`{_safe_text(record.publish_date or '未知')}`。"
            )
        if len(scan.records) > 30:
            lines.append(f"- 另有 {len(scan.records) - 30} 条候选未展开。")
        if not scan.records:
            lines.append("本次有限检索未取得候选；不能据此认定万方未收录。")
        lines += [
            "",
            f"万方发现覆盖：{scan.status}；完成 "
            f"{scan.result_pages}/{scan.requested_pages} 页；"
            f"截断 {scan.incomplete_queries} 项；失败 {len(scan.failures)} 项。",
            "",
        ]
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
