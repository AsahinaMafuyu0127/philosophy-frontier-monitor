"""Audit saved pfm JSON and a review queue without network access or state changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MODES = {"search", "pull-now", "weekly-run"}
SEARCH_LISTS = ("items", "unconfirmed_records", "year_review_candidates")
URL_KEYS = ("record_url", "review_record_url", "stable_url", "philpapers_url")
SOURCE_STATUS = re.compile(r"^- `([^`]+)`[：:]\s*(success|failed|local_only|partial)\b")


def load_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return data


def field(data: dict, key: str, prefix: str) -> dict:
    state = "absent" if key not in data else ("null" if data[key] is None else "value")
    value = {"state": state, "locator": f"{prefix}.{key}"}
    if state != "absent":
        value["value"] = data[key]
    return value


def object_list(data: dict, key: str, *, required: bool = True) -> list[dict]:
    if key not in data and not required:
        return []
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"missing or invalid {key} list")
    return value


def canonical_url(raw: str) -> str:
    if "://" not in raw:
        raw = "https://philpapers.org/rec/" + raw.strip()
    parts = urlsplit(raw.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    if (
        parts.scheme not in {"http", "https"}
        or host not in {"philpapers.org", "philarchive.org"}
        or not re.fullmatch(r"/rec/[^/]+", path)
    ):
        raise ValueError("expected a PhilPapers/PhilArchive record URL or record ID")
    return urlunsplit(("https", host, path, "", ""))


def links(item: dict, prefix: str) -> list[dict]:
    values = [(key, item[key]) for key in URL_KEYS if isinstance(item.get(key), str)]
    if isinstance(item.get("source_urls"), list):
        values.extend(
            (f"source_urls[{i}]", value)
            for i, value in enumerate(item["source_urls"])
            if isinstance(value, str)
        )
    output = []
    for key, value in values:
        try:
            url = canonical_url(value)
        except ValueError:
            continue
        output.append({"record_url": url, "value": value, "locator": f"{prefix}.{key}"})
    return output


def reason_counts(items: list[dict], key: str) -> dict:
    return dict(sorted(Counter(str(item.get(key, "<absent>")) for item in items).items()))


def instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing result/queue timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("result/queue timestamps must include a timezone")
    return parsed


def bind_queue(result: dict, queue: dict | None, mode: str) -> dict:
    if queue is None:
        return {"status": "not_supplied"}
    if queue.get("schema_version") != 1:
        raise ValueError("unsupported review queue schema_version")
    if mode == "search":
        if queue.get("mode") != "search":
            raise ValueError("queue is not a search queue")
        if instant(queue.get("queried_at")) != instant(result.get("queried_at")):
            raise ValueError("queue and result come from different runs")
        return {"status": "query_time_matched", "basis": ["result.queried_at", "queue.queried_at"]}
    if queue.get("mode") not in {None, mode}:
        raise ValueError("queue mode does not match result")
    if any(
        instant(queue.get(key)) != instant(result.get(key))
        for key in ("window_start", "window_end")
    ):
        raise ValueError("queue and result have different windows")
    return {
        "status": "window_matched",
        "same_run_proven": False,
        "note": "Matching windows alone do not identify a unique run; retain both original files.",
    }


def source_summary(result: dict) -> dict:
    explicit = {}
    for key in ("wanfang_scan", "wanfang_discovery"):
        value = result.get(key)
        if isinstance(value, dict):
            explicit[key] = {
                name: field(value, name, f"result.{key}")
                for name in (
                    "status",
                    "failure",
                    "failures",
                    "total_candidates",
                    "attempted",
                    "checks",
                    "requested_pages",
                    "result_pages",
                    "incomplete",
                    "incomplete_queries",
                )
            }
    report_rows = []
    seen = set()
    report = result.get("report_markdown")
    if isinstance(report, str):
        for number, line in enumerate(report.splitlines(), 1):
            match = SOURCE_STATUS.match(line)
            if match and match[1] not in seen:
                seen.add(match[1])
                report_rows.append(
                    {
                        "source": match[1],
                        "status": match[2],
                        "text": line,
                        "locator": f"result.report_markdown#line={number}",
                    }
                )
    telemetry = result.get("network_telemetry")
    return {
        "source_failures": field(result, "source_failures", "result"),
        "explicit": explicit,
        "official_issue_status": field(result, "official_issue_status", "result"),
        "official_issue_leads": field(result, "official_issue_leads", "result"),
        "cnki_coverage": field(result, "cnki_coverage", "result"),
        "report_coverage": report_rows,
        "network_telemetry": telemetry.get("sources", {}) if isinstance(telemetry, dict) else {},
        "note": "Request success counts do not establish full coverage or a persisted retry queue.",
    }


def display_sections(result: dict) -> dict:
    report = result.get("report_markdown")
    if not isinstance(report, str):
        return {}
    output = {}
    pattern = re.compile(r"^### (Recently published|Recently arrived|Recently changed) \((\d+)\)$")
    for number, line in enumerate(report.splitlines(), 1):
        match = pattern.match(line)
        if match:
            key = match[1].lower().replace(" ", "_")
            entry = {"value": int(match[2]), "locator": f"result.report_markdown#line={number}"}
            if key in output and output[key]["value"] != entry["value"]:
                raise ValueError("conflicting report section counts")
            output[key] = entry
    return output


def weekly_timing(result: dict) -> dict:
    if result.get("operation") != "weekly-run":
        return {"status": "not_applicable"}
    start, end = (instant(result.get(key)) for key in ("window_start", "window_end"))
    observed = datetime.now(UTC)
    if start >= end:
        raise ValueError("weekly window start must precede its end")
    if end > observed:
        raise ValueError("weekly window has not ended at audit time")
    evidence = {key: field(result, key, "result") for key in ("started_at", "completed_at")}
    if any(item["state"] != "value" for item in evidence.values()):
        return {
            "status": "execution_time_unverified",
            "evidence": evidence,
            "note": "A past window today does not prove it had ended when the run started.",
        }
    started, completed = (instant(result[key]) for key in ("started_at", "completed_at"))
    if end > started or completed < started or completed > observed:
        raise ValueError("weekly window or execution timestamps are inconsistent")
    return {"status": "completed_window_verified", "evidence": evidence}


def audit(
    result: dict, queue: dict | None, targets: list[str], reviews: dict | None = None
) -> dict:
    mode = result.get("operation")
    if mode not in MODES or result.get("ok") is not True:
        raise ValueError("expected a successful search, pull-now, or weekly-run result")
    timing = weekly_timing(result)
    binding = bind_queue(result, queue, mode)
    groups = {key: object_list(result, key) for key in SEARCH_LISTS} if mode == "search" else {}
    if mode == "search" and type(result.get("total_matches")) is not int:
        raise ValueError("search result lacks integer total_matches")
    queue_items = object_list(queue, "items") if queue is not None else []
    pair_rows = []
    label_counts = Counter()
    queue_identity_records = 0
    for index, item in enumerate(queue_items):
        pairs = object_list(item, "identity_review_pairs", required=False)
        if pairs:
            queue_identity_records += 1
        for pair_index, pair in enumerate(pairs):
            evidence = pair.get("evidence", [])
            if not isinstance(evidence, list) or any(not isinstance(x, str) for x in evidence):
                raise ValueError("invalid identity pair evidence labels")
            label_counts[tuple(sorted(evidence))] += 1
            pair_rows.append(
                {
                    "record_links": links(item, f"queue.items[{index}]"),
                    "title": item.get("title"),
                    "locator": f"queue.items[{index}].identity_review_pairs[{pair_index}]",
                    "external_record": pair,
                }
            )
    group_fields = {}
    for group, items in {
        **groups,
        **({"queue.items": queue_items} if queue is not None else {}),
    }.items():
        group_fields[group] = {
            key: dict(Counter(field(item, key, "")["state"] for item in items))
            for key in (*URL_KEYS, "source_urls")
        }
    named = []
    for target in targets:
        url = canonical_url(target)
        hits = {
            key: [
                {
                    "locator": f"result.{key}[{i}]",
                    "title": item.get("title"),
                    "links": links(item, f"result.{key}[{i}]"),
                    "reason": field(item, "reason", f"result.{key}[{i}]"),
                }
                for i, item in enumerate(items)
                if url in {link["record_url"] for link in links(item, "")}
            ]
            for key, items in groups.items()
        }
        status = "final_status_not_exposed_by_result_lists"
        basis = None
        if mode == "search":
            for key, label in zip(
                SEARCH_LISTS,
                ("confirmed_search_match", "unconfirmed_this_run", "year_review_pending"),
                strict=True,
            ):
                if hits[key]:
                    status, basis = label, f"result.{key}"
                    break
            else:
                status = "not_located_in_returned_lists_gate_unknown"
        named.append(
            {
                "record_url": url,
                "status": status,
                "status_basis": basis,
                "hits": hits,
                "queue_hits": [
                    f"queue.items[{i}]"
                    for i, item in enumerate(queue_items)
                    if url in {link["record_url"] for link in links(item, "")}
                ],
            }
        )
    overlap = None
    if reviews is not None:
        if reviews.get("schema_version") != 1:
            raise ValueError("unsupported identity reviews schema_version")
        decisions = object_list(reviews, "reviews")
        decision_keys = {
            (
                canonical_url(x["record_url"]),
                x["source"],
                x["external_record_id"],
                x["evidence_fingerprint"],
            )
            for x in decisions
        }
        overlap = sum(
            any(
                (
                    link["record_url"],
                    row["external_record"]["source"],
                    row["external_record"]["record_id"],
                    row["external_record"]["evidence_fingerprint"],
                )
                in decision_keys
                for link in row["record_links"]
            )
            for row in pair_rows
        )
    stats = result.get("stats")
    if not isinstance(stats, dict):
        raise ValueError("result lacks stats object")
    return {
        "schema_version": 1,
        "operation": "pfm-result-audit",
        "mode": mode,
        "queue_binding": binding,
        "weekly_timing": timing,
        "run": {
            key: field(result, key, "result")
            for key in (
                "queried_at",
                "window_start",
                "window_end",
                "request_id",
                "run_id",
                "started_at",
                "completed_at",
                "dry_run",
            )
        },
        "reported_counts": {
            "total_matches": field(result, "total_matches", "result"),
            **{
                key: field(stats, key, "result.stats")
                for key in (
                    "matched",
                    "confirmed_new",
                    "confirmed_source_arrivals",
                    "matched_confirmed_new",
                    "matched_confirmed_source_arrivals",
                    "resolved_works",
                    "unresolved",
                    "human_review_required",
                    "automatic_retry_required",
                    "processed_candidates",
                    "retry_records",
                )
            },
        },
        "computed_counts": {
            "result_lists": {key: len(items) for key, items in groups.items()},
            "unconfirmed_reasons": reason_counts(groups.get("unconfirmed_records", []), "reason"),
            "year_review_reasons": reason_counts(
                groups.get("year_review_candidates", []), "reason"
            ),
            "queue_items": len(queue_items) if queue is not None else None,
            "queue_reasons": reason_counts(
                queue_items, "reason" if mode == "search" else "reason_code"
            ),
            "identity_records": queue_identity_records if queue is not None else None,
            "identity_pairs": len(pair_rows) if queue is not None else None,
            "identity_evidence": [
                {"labels": list(labels), "count": count}
                for labels, count in sorted(label_counts.items())
            ],
            "pairs_missing_record_link": (
                sum(not row["record_links"] for row in pair_rows) if queue is not None else None
            ),
            "pending_pairs_matching_supplied_decisions": overlap,
        },
        "display_section_counts": display_sections(result),
        "url_field_presence": group_fields,
        "records": named,
        "identity_pairs": pair_rows,
        "sources": source_summary(result),
        "state": {
            key: field(result, key, "result")
            for key in (
                "state_mutated",
                "state_advanced",
                "weekly_notifications_written",
                "bibliography_cache_updated",
                "oai_cache_updated",
                "state_warning",
            )
        },
        "interpretation": [
            "Use each value's original locator as evidence; this audit is a derived index.",
            "Absent fields differ from null/false; queue entries do not establish final matches.",
            "Queue confirmed_type entries are supporting evidence for already confirmed results.",
            "year_review_pending: missing feed year hint without applicable publisher-year review.",
            "confirmed_source_arrivals can include OAI changes; display counts are separate.",
            "Pending-pair overlap does not establish which other decisions were applied.",
            "Neither an empty queue nor an identity decision establishes new publication.",
        ],
    }


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def field_text(value: dict) -> str:
    if value["state"] == "absent":
        return "字段未返回"
    return "null" if value["state"] == "null" else cell(value["value"])


def markdown(summary: dict) -> str:
    counts = summary["computed_counts"]
    lines = [
        f"# {summary['mode']} 结果审计",
        "",
        "数值由保存的原始文件计算；括号内为原始字段位置。",
        "",
    ]
    for key, value in summary["run"].items():
        if value["state"] != "absent":
            lines.append(f"- {key}：{field_text(value)}（`{value['locator']}`）。")
    lines.append(f"- 周窗口时间核对：{summary['weekly_timing']['status']}。")
    lines += ["", "## 原始计数", "", "| 字段 | 值 | 依据 |", "|---|---|---|"]
    for key, value in summary["reported_counts"].items():
        lines.append(f"| {key} | {field_text(value)} | `{value['locator']}` |")
    lines += [
        "",
        "## 逐篇定位",
        "",
        "| 记录 | 本轮状态 | 原始列表 | 原始原因 |",
        "|---|---|---|---|",
    ]
    for row in summary["records"]:
        labels = {
            "confirmed_search_match": "已确认检索匹配",
            "unconfirmed_this_run": "本轮未确认",
            "year_review_pending": "年份待补证",
            "not_located_in_returned_lists_gate_unknown": "未定位；具体关口未知",
            "final_status_not_exposed_by_result_lists": "结果未提供可定位最终状态的逐篇列表",
        }
        locations = [hit["locator"] for group in row["hits"].values() for hit in group]
        reasons = [
            hit["reason"]["value"]
            for group in row["hits"].values()
            for hit in group
            if hit["reason"]["state"] == "value"
        ]
        lines.append(
            f"| {row['record_url']} | {labels[row['status']]} | "
            f"{', '.join(locations) or '无'} | {cell(', '.join(reasons)) or '未返回'} |"
        )
    lines += [
        "",
        "## 队列与比较",
        "",
        f"- 队列项：{counts['queue_items']}；含身份比较的记录："
        f"{counts['identity_records']}；比较对数：{counts['identity_pairs']}。",
        f"- 队列原因：`{json.dumps(counts['queue_reasons'], ensure_ascii=False)}`。",
        f"- 比较中缺少记录链接：{counts['pairs_missing_record_link']}。",
        "- confirmed_type 是确认结果的辅助证据，不计为未决冲突。",
        "- 字段不存在、null 和实际值的分布见审计 JSON 的 url_field_presence。",
    ]
    for row in counts["identity_evidence"]:
        lines.append(f"- 比较标签 `{', '.join(row['labels'])}`：{row['count']} 对。")
    if counts["pending_pairs_matching_supplied_decisions"] is not None:
        lines.append(
            "- 本队列中与所提供裁定文件匹配的比较："
            f"{counts['pending_pairs_matching_supplied_decisions']} 对；"
            "该数不推断其他记录已应用的裁定。"
        )
    for key in ("result_lists", "unconfirmed_reasons", "year_review_reasons"):
        lines.append(f"- {key}：`{json.dumps(counts[key], ensure_ascii=False)}`。")
    if counts["year_review_reasons"].get("year_review_pending"):
        lines.append(
            "- year_review_pending：缺少feed年份提示、无适用出版方年份补证；不表示处于年份边界。"
        )
    lines += [
        "",
        "<details><summary>链接字段存在性</summary>",
        "",
        "| 列表 | 字段 | 不存在 | null | 实际值 |",
        "|---|---|---:|---:|---:|",
    ]
    for group, fields in summary["url_field_presence"].items():
        for key, states in fields.items():
            lines.append(
                f"| {group} | {key} | {states.get('absent', 0)} | "
                f"{states.get('null', 0)} | {states.get('value', 0)} |"
            )
    lines += ["", "</details>"]
    lines += ["", "## 来源证据", ""]
    sources = summary["sources"]
    lines.append(f"- source_failures：{field_text(sources['source_failures'])}。")
    for key, values in sources["explicit"].items():
        raw = {name: value["value"] for name, value in values.items() if value["state"] != "absent"}
        lines.append(f"- result.{key}：`{json.dumps(raw, ensure_ascii=False)}`。")
    for key in ("official_issue_status", "official_issue_leads", "cnki_coverage"):
        lines.append(f"- {key}：{field_text(sources[key])}。")
    for row in sources["report_coverage"]:
        lines.append(f"- `{row['source']}`：{row['status']}（{row['locator']}）；{row['text']}。")
    lines += [
        "",
        "| 网络来源 | 逻辑请求 | 首次成功 | 重试成功 | 失败 | 长等待延后 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, values in sources["network_telemetry"].items():
        if isinstance(values, dict):
            cells = [
                field_text(field(values, key, ""))
                for key in (
                    "logical_requests",
                    "first_attempt_successes",
                    "retried_successes",
                    "failed_requests",
                    "deferred_long_retry_after",
                )
            ]
            lines.append(f"| {cell(name)} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "请求成功次数不证明完整来源覆盖；重试遥测不证明写入持久重试队列。",
        "",
        "## 状态与显示分组",
        "",
    ]
    for key, value in summary["state"].items():
        lines.append(f"- {key}：{field_text(value)}（`{value['locator']}`）。")
    for key, value in summary["display_section_counts"].items():
        lines.append(f"- 显示分组 {key}：{value['value']}（{value['locator']}）。")
    lines += [
        "",
        "逐篇最终状态按原始结果列表确定；发表、来源到达、来源变化和身份裁定分别解释。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--record", action="append", default=[])
    parser.add_argument("--identity-reviews", type=Path)
    parser.add_argument("--output", type=Path, help="new private audit JSON; stdout if omitted")
    parser.add_argument("--markdown", type=Path, help="new private evidence summary")
    args = parser.parse_args()
    inputs = [path for path in (args.result, args.queue, args.identity_reviews) if path]
    outputs = [path for path in (args.output, args.markdown) if path]
    if len({p.resolve() for p in outputs}) != len(outputs):
        raise ValueError("output files must be distinct")
    if any(p.exists() or p.resolve() in {i.resolve() for i in inputs} for p in outputs):
        raise ValueError("choose new output files; inputs and existing outputs are preserved")
    summary = audit(
        load_object(args.result),
        load_object(args.queue) if args.queue else None,
        args.record,
        load_object(args.identity_reviews) if args.identity_reviews else None,
    )
    summary["inputs"] = [
        {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in inputs
    ]
    encoded = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    if args.markdown:
        args.markdown.write_text(markdown(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"result audit failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
