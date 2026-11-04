"""Read-only, exact-URL membership audit for a private pfm search result."""

import argparse
import json
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def record_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = f"https://philpapers.org/rec/{value}"
    parts = urlsplit(value)
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    if (
        parts.scheme not in {"http", "https"}
        or host
        not in {"philpapers.org", "www.philpapers.org", "philarchive.org", "www.philarchive.org"}
        or not path.startswith("/rec/")
        or path.count("/") != 2
    ):
        raise ValueError(f"expected a PhilPapers/PhilArchive record URL or record ID: {value}")
    return urlunsplit(("https", host.removeprefix("www."), path, "", ""))


def urls(item: dict) -> set[str]:
    values = []
    for key in ("record_url", "review_record_url"):
        if isinstance(item.get(key), str):
            values.append(item[key])
    if isinstance(item.get("source_urls"), list):
        values.extend(value for value in item["source_urls"] if isinstance(value, str))
    found = set()
    for value in values:
        with suppress(ValueError):
            found.add(record_url(value))
    return found


def records(data: dict, key: str) -> list[dict]:
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"missing or invalid {key} list")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--result", required=True, type=Path, help="private JSON output of the final pfm search run"
    )
    parser.add_argument("--queue", type=Path, help="private review queue from that same run")
    parser.add_argument(
        "--record",
        action="append",
        required=True,
        help="record ID or exact PhilPapers URL; repeatable",
    )
    args = parser.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict) or result.get("operation") != "search":
        raise ValueError("result is not pfm search JSON")
    groups = {
        key: records(result, key)
        for key in ("items", "unconfirmed_records", "year_review_candidates")
    }
    if not isinstance(result.get("total_matches"), int):
        raise ValueError("result lacks integer total_matches")
    queue_items = None
    if args.queue:
        queue = json.loads(args.queue.read_text(encoding="utf-8-sig"))
        if not isinstance(queue, dict) or queue.get("mode") != "search":
            raise ValueError("queue is not a search review queue")
        if datetime.fromisoformat(queue["queried_at"]) != datetime.fromisoformat(
            result["queried_at"]
        ):
            raise ValueError("queue and result come from different runs")
        queue_items = records(queue, "items")

    output = {
        "operation": "search-record-audit",
        "queried_at": result["queried_at"],
        "total_matches": result["total_matches"],
        "returned_page_items": len(groups["items"]),
        "source_failures_present": bool(result.get("source_failures")),
        "records": [],
    }
    for raw in args.record:
        target = record_url(raw)
        hits = {
            key: [item for item in group if target in urls(item)] for key, group in groups.items()
        }
        queue_hits = (
            [item for item in queue_items if target in urls(item)]
            if queue_items is not None
            else None
        )
        if hits["items"]:
            status = "confirmed_on_returned_page"
            status_basis = "result.items"
        elif hits["unconfirmed_records"]:
            status = "unconfirmed_this_run"
            status_basis = "result.unconfirmed_records"
        elif hits["year_review_candidates"]:
            status = "year_review_pending"
            status_basis = "result.year_review_candidates"
        else:
            status = "not_located_in_returned_artifacts_gate_unknown"
            status_basis = "absence_from_returned_result_lists; exclusion_gate_untraced"
        output["records"].append(
            {
                "record_url": target,
                "status": status,
                "status_basis": status_basis,
                "confirmed_search_match_on_returned_page": bool(hits["items"]),
                "memberships": {key: bool(value) for key, value in hits.items()},
                "unconfirmed_reasons": sorted(
                    {item.get("reason", "") for item in hits["unconfirmed_records"]}
                ),
                "year_review_reasons": sorted(
                    {item.get("reason", "") for item in hits["year_review_candidates"]}
                ),
                "review_queue": {
                    "role": "supporting_review_evidence_not_final_search_status",
                    "reasons": None
                    if queue_hits is None
                    else sorted({item.get("reason", "") for item in queue_hits}),
                },
            }
        )
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"search record audit failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
