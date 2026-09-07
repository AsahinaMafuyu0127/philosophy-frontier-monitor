"""Command-line entry points for inspecting the first working slice."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .bibliographic_cache import BibliographicCache
from .config import WatchlistConfig, load_watchlist
from .diagnostics import diagnose_sources
from .http_retry import (
    BoundedRequestError,
    collect_request_telemetry,
    summarize_request_telemetry,
)
from .interest import build_interest_profile
from .pipeline import (
    CatchUpError,
    OnDemandRunResult,
    establish_baseline,
    run_on_demand,
    run_weekly,
    run_weekly_catch_up,
    write_report_atomic,
)
from .release_check import audit_release
from .scope_estimate import estimate_interest_scope
from .sources.philpapers_rss import discover_feed_request, fetch_feed, parse_feed
from .sources.philpapers_taxonomy import (
    fetch_and_write_taxonomy,
    import_and_write_taxonomy,
    load_credentials,
)
from .state import StateStore
from .taxonomy import expand_selected_categories, load_taxonomy, require_production_taxonomy
from .taxonomy_audit import audit_taxonomy_change


def _emit(payload: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _source_failure_payload(error: BaseException) -> dict[str, Any] | None:
    """Extract one redacted bounded-request failure from an exception chain."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, BoundedRequestError):
            if current.failure_kind == "permanent_http":
                recovery = "检查请求参数、认证、端点或站点访问条件；不要原样立即重试。"
                retry_safe = False
            elif current.failure_kind == "daily_budget_exhausted":
                recovery = "等待额度重置后重试，或检查本机是否正确提供了可用 API key。"
                retry_safe = True
            elif current.failure_kind == "transient_transport":
                recovery = "分别检查网络、DNS、TLS 与代理状态，然后重试。"
                retry_safe = True
            else:
                recovery = "按照服务端等待提示稍后重试；不要提高并发或连续立即重试。"
                retry_safe = True
            return {
                "source": current.source,
                "failure_kind": current.failure_kind,
                "attempts": current.attempts,
                "stop_reason": current.stop_reason,
                "http_status": current.status_code,
                "retry_after_seconds": current.retry_after_seconds,
                "retry_safe": retry_safe,
                "recovery": recovery,
            }
        current = current.__cause__
    return None


def _emit_pull_now_progress(stage: str, completed: int, total: int) -> None:
    """Write count-only progress to stderr without contaminating JSON stdout."""

    labels = {
        "feeds": "分类 feed",
        "oai": "PhilArchive OAI 增量记录",
        "candidates": "候选筛选",
        "doi_batch": "DOI 批量核验",
        "title_batch": "题名批量核验",
        "fallback": "逐篇退回核验",
        "complete": "即时报告",
    }
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        print(
            f"[pull-now] {labels.get(stage, stage)}：{completed}/{total}",
            file=sys.stderr,
            flush=True,
        )
    except OSError:
        # Progress is advisory; a closed stderr must not invalidate the report.
        return


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_fixture() -> Path:
    return _project_root() / "tests" / "fixtures" / "philpapers_categories_small.json"


def _default_watchlist() -> Path:
    return _project_root() / "config" / "watchlist.yaml"


def _default_taxonomy_directory() -> Path:
    return _project_root() / "var" / "taxonomy"


def _default_credential_file() -> Path:
    return _project_root() / "var" / "secrets" / "philpapers-credentials.txt"


def _parse_instant(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} 必须是 ISO 8601 时间，例如 2026-09-07T00:00:00Z") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必须包含时区，例如 Z 或 +08:00")
    return parsed


def _doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []
    network_telemetry: dict[str, Any] | None = None
    if args.expected_openalex_requests < 0:
        raise ValueError("--expected-openalex-requests 不能是负数。")

    version_ok = (3, 12) <= sys.version_info[:2] < (3, 14)
    checks.append(
        {
            "check": "python",
            "status": "pass" if version_ok else "fail",
            "detail": platform.python_version(),
        }
    )

    root = _project_root()
    for relative in ("SKILL.md", "agents/openai.yaml", "pyproject.toml"):
        path = root / relative
        checks.append(
            {
                "check": relative,
                "status": "pass" if path.is_file() and path.stat().st_size else "fail",
                "detail": str(path),
            }
        )

    try:
        yaml.safe_load((root / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        checks.append({"check": "openai-yaml", "status": "fail", "detail": str(error)})
    else:
        checks.append({"check": "openai-yaml", "status": "pass", "detail": "valid YAML"})

    taxonomy_path = Path(args.taxonomy)
    try:
        snapshot = load_taxonomy(taxonomy_path)
    except (OSError, ValueError, KeyError) as error:
        checks.append({"check": "taxonomy", "status": "fail", "detail": str(error)})
    else:
        mode = "offline fixture" if snapshot.fixture else "production snapshot"
        checks.append(
            {
                "check": "taxonomy",
                "status": "pass",
                "detail": f"{snapshot.category_count} categories; {mode}",
            }
        )

    try:
        with StateStore(":memory:") as store:
            store.has_notification("doctor", "doctor")
    except Exception as error:  # pragma: no cover - defensive diagnostic boundary
        checks.append({"check": "sqlite", "status": "fail", "detail": str(error)})
    else:
        checks.append({"check": "sqlite", "status": "pass", "detail": "schema initialized"})

    if args.live:
        selected_sources = tuple(
            dict.fromkeys(args.source or ("philpapers", "philarchive", "openalex", "crossref"))
        )
        with collect_request_telemetry() as events:
            checks.extend(
                item.as_dict()
                for item in diagnose_sources(
                    selected_sources,
                    category_url=args.category_url,
                )
            )
        network_telemetry = summarize_request_telemetry(events)

    payload: dict[str, Any] = {
        "ok": all(item["status"] == "pass" for item in checks),
        "checks": checks,
        "state_mutated": False,
    }
    if network_telemetry is not None:
        payload["network_telemetry"] = network_telemetry
        if args.expected_openalex_requests:
            openalex = network_telemetry["sources"].get("OpenAlex", {})
            remaining = openalex.get("rate_limit_remaining")
            if remaining is None:
                payload["openalex_budget_check"] = {
                    "status": "unknown",
                    "expected_requests": args.expected_openalex_requests,
                    "detail": "服务端未返回可解析的剩余额度头；不能据此承诺宽范围拉取可完成。",
                }
            elif remaining < args.expected_openalex_requests:
                payload["openalex_budget_check"] = {
                    "status": "warn",
                    "expected_requests": args.expected_openalex_requests,
                    "remaining": remaining,
                    "detail": "显式剩余额度小于预计请求数；应缩小范围或等待额度重置。",
                }
            else:
                payload["openalex_budget_check"] = {
                    "status": "pass",
                    "expected_requests": args.expected_openalex_requests,
                    "remaining": remaining,
                }

    _emit(payload)
    return 0 if all(item["status"] == "pass" for item in checks) else 1


def _profile(args: argparse.Namespace) -> int:
    snapshot = load_taxonomy(args.taxonomy)
    research = (
        Path(args.research_file).read_text(encoding="utf-8").strip()
        if args.research_file
        else args.research
    )
    profile = build_interest_profile(research, snapshot)
    _emit(
        {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "original_text": profile.original_text,
            "taxonomy_snapshot_id": profile.taxonomy_snapshot_id,
            "taxonomy_is_fixture": snapshot.fixture,
            "selected_categories": [asdict(item) for item in profile.selected_categories],
            "expanded_category_ids": sorted(profile.expanded_category_ids),
            "ambiguous_candidates": [asdict(item) for item in profile.ambiguous_candidates],
        }
    )
    return 0


def _scope_estimate(args: argparse.Namespace) -> int:
    """Count a proposed additive taxonomy expansion without fetching or mutation."""

    config_path = Path(args.config) if args.config else None
    config = (
        load_watchlist(config_path) if config_path is not None and config_path.is_file() else None
    )
    taxonomy_path = Path(args.taxonomy) if args.taxonomy else None
    if taxonomy_path is None and config is not None:
        taxonomy_path = config.taxonomy_path
    if taxonomy_path is None:
        raise ValueError("需要 --taxonomy，或者一个实际存在且含 taxonomy.path 的 --config。")

    snapshot = load_taxonomy(taxonomy_path)
    current_expanded = frozenset()
    if config is not None:
        current_expanded = expand_selected_categories(
            snapshot,
            {item.category_id: item.include_descendants for item in config.confirmed_categories},
        )

    estimate = estimate_interest_scope(
        snapshot,
        {category_id: args.include_descendants for category_id in dict.fromkeys(args.category_id)},
        current_expanded_category_ids=current_expanded,
    )
    _emit(
        {
            "ok": True,
            "operation": "scope-estimate",
            "taxonomy_snapshot_id": snapshot.snapshot_id,
            "compared_with_existing_config": config is not None,
            **estimate.to_dict(),
            "state_advanced": False,
            "network_accessed": False,
        }
    )
    return 0


def _onboarding_status(args: argparse.Namespace) -> int:
    """Report first-dialogue needs without exposing research text or profile URLs."""

    config_path = Path(args.config)
    if not config_path.is_file():
        _emit(
            {
                "ok": True,
                "operation": "onboarding-status",
                "profile_exists": False,
                "needs_research_direction": True,
                "needs_philpapers_account_decision": True,
                "onboarding_complete": False,
            }
        )
        return 0

    config = load_watchlist(config_path)
    _emit(
        {
            "ok": True,
            "operation": "onboarding-status",
            "profile_exists": True,
            "needs_research_direction": False,
            "needs_philpapers_account_decision": (not config.onboarding.account_decision_recorded),
            "onboarding_complete": config.onboarding.complete,
            "philpapers_account_decision": config.onboarding.philpapers_account_decision,
            "authorized_scopes": config.onboarding.authorized_scopes,
            "public_profile_url_recorded": config.onboarding.public_profile_url is not None,
        }
    )
    return 0


def _feed(args: argparse.Namespace) -> int:
    request = discover_feed_request(args.category_url)
    payload: dict[str, Any] = {
        "category_page_url": request.category_page_url,
        "category_id": request.category_id,
        "category_slug": request.category_slug,
        "feed_generation_url": request.feed_url,
        "method": request.method,
    }
    if args.fetch:
        entries = parse_feed(fetch_feed(request))
        payload["entry_count"] = len(entries)
        payload["sample"] = [asdict(item) for item in entries[: args.sample]]
        payload["date_warning"] = (
            "Feed appearance can evidence recent PhilPapers arrival, not a publication date; "
            "old-work checks remain required."
        )
    _emit(payload)
    return 0


def _taxonomy_fetch(args: argparse.Namespace) -> int:
    credentials = load_credentials(credential_file=args.credential_file)
    result = fetch_and_write_taxonomy(args.output_directory, credentials)
    _emit(
        {
            "ok": True,
            "operation": "taxonomy-fetch",
            "snapshot_path": str(result.path),
            "snapshot_id": result.snapshot_id,
            "retrieved_at": result.retrieved_at,
            "category_count": result.category_count,
            "source_content_hash": result.source_content_hash,
            "credential_values_logged": False,
            "next_step": (
                "已有正式 taxonomy 时，先用 pfm taxonomy-audit 比较当前快照与 snapshot_path；"
                "首次设置按 README 完成配置与基线。"
            ),
        }
    )
    return 0


def _taxonomy_import(args: argparse.Namespace) -> int:
    result = import_and_write_taxonomy(
        args.input,
        args.output_directory,
    )
    _emit(
        {
            "ok": True,
            "operation": "taxonomy-import",
            "snapshot_path": str(result.path),
            "snapshot_id": result.snapshot_id,
            "retrieved_at": result.retrieved_at,
            "category_count": result.category_count,
            "source_content_hash": result.source_content_hash,
            "input_retained": True,
            "next_step": (
                "已有正式 taxonomy 时，先用 pfm taxonomy-audit 比较当前快照与 snapshot_path；"
                "首次设置按 README 完成配置与基线。"
            ),
        }
    )
    return 0


def _taxonomy_audit(args: argparse.Namespace) -> int:
    config = load_watchlist(args.config)
    old = load_taxonomy(args.old)
    new = load_taxonomy(args.new)
    configured = load_taxonomy(config.taxonomy_path)
    if configured.snapshot_id != old.snapshot_id or configured.content_hash != old.content_hash:
        raise ValueError(
            "--old 必须是当前私人配置 taxonomy.path 实际指向的快照；审计命令不会猜测比较基线。"
        )
    if not args.allow_development_fixture:
        require_production_taxonomy(old)
        require_production_taxonomy(new)
    audit = audit_taxonomy_change(old, new, config)
    if audit.activation_ready:
        next_step = (
            "新 taxonomy 未改变当前兴趣范围；如决定启用，仍须更新 taxonomy.path，"
            "增加 interest.version 并填写新的 effective_from。"
        )
    else:
        next_step = (
            "不要启用新 taxonomy；先审查 tracked_impacts 与 feed 覆盖变化。"
            "程序没有执行任何分类迁移或配置修改。"
        )
    _emit(
        {
            "ok": True,
            "operation": "taxonomy-audit",
            **audit.to_dict(),
            "state_advanced": False,
            "next_step": next_step,
        }
    )
    return 0


def _release_check(args: argparse.Namespace) -> int:
    audit = audit_release(args.project_root, private_config=args.private_config)
    _emit(
        {
            "ok": True,
            "operation": "release-check",
            **audit.to_dict(),
            "state_advanced": False,
            "next_step": (
                "发布技术闸门和需要维护者决定的事项均已完成。"
                if audit.release_ready
                else "先处理 blocker_codes；许可证和私密安全报告渠道只在维护者明确选择后补充。"
            ),
        }
    )
    return 0 if audit.release_ready else 1


def _baseline(args: argparse.Namespace) -> int:
    config = load_watchlist(args.config)
    with (
        collect_request_telemetry() as events,
        StateStore(config.storage.state_database) as state,
    ):
        result = establish_baseline(
            config,
            state,
            allow_development_fixture=args.allow_development_fixture,
        )
    _emit(
        {
            "ok": True,
            "operation": "baseline",
            "state_database": str(config.storage.state_database),
            "feed_count": result.feed_count,
            "feed_entry_count": result.feed_entry_count,
            "unique_source_records": result.unique_source_records,
            "completed_at": result.completed_at,
            "meaning": "当前条目已记为历史基线，不会作为新论文推送。",
            "network_telemetry": summarize_request_telemetry(events),
        }
    )
    return 0


def _weekly_run(args: argparse.Namespace) -> int:
    config = load_watchlist(args.config)
    if not config.storage.state_database.is_file():
        raise ValueError(f"状态库不存在：{config.storage.state_database}；请先运行 pfm baseline。")
    window_start = _parse_instant(args.window_start, "--window-start")
    window_end = _parse_instant(args.window_end, "--window-end")
    with (
        collect_request_telemetry() as events,
        StateStore(config.storage.state_database) as state,
    ):
        result = run_weekly(
            config,
            state,
            window_start=window_start,
            window_end=window_end,
            dry_run=args.dry_run,
        )
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "weekly-run",
        "dry_run": result.dry_run,
        "run_id": result.run_id,
        "window_start": result.window_start,
        "window_end": result.window_end,
        "report_path": str(result.report_path) if result.report_path else None,
        "stats": result.stats,
        "network_telemetry": summarize_request_telemetry(
            events,
            circuit_skipped=result.stats.get("bibliographic_source_circuit_skips", 0),
        ),
    }
    if result.dry_run:
        payload["report_markdown"] = result.report_markdown
        payload["state_warning"] = "dry-run 未写周报文件，也未修改数据库或检查点。"
    _emit(payload)
    return 0


def _deliver_on_demand_report(
    result: OnDemandRunResult,
    config: WatchlistConfig,
    delivery: str,
) -> dict[str, Any]:
    """Return either inline Markdown or a private report-file reference."""

    common: dict[str, Any] = {
        "report_delivery": delivery,
        "report_character_count": len(result.report_markdown),
    }
    if delivery == "inline":
        return {
            **common,
            "report_markdown": result.report_markdown,
            "report_path": None,
            "on_demand_report_file_written": False,
        }
    if delivery == "file":
        local_end = result.window_end.astimezone(config.timezone)
        request_suffix = result.request_id.rsplit(":", 1)[-1]
        filename = f"on-demand-{local_end:%Y%m%dT%H%M%S%z}-{request_suffix}.md"
        report_path = write_report_atomic(
            config.storage.report_directory,
            filename,
            result.report_markdown,
        )
        return {
            **common,
            "report_markdown": None,
            "report_path": str(report_path),
            "on_demand_report_file_written": True,
        }
    raise ValueError(f"unsupported on-demand report delivery: {delivery}")


def _pull_now(args: argparse.Namespace) -> int:
    config = load_watchlist(args.config)
    cache_path = config.storage.state_database.parent / "bibliography-cache.sqlite3"
    with collect_request_telemetry() as events:
        if args.no_bibliography_cache:
            result = run_on_demand(
                config,
                lookback_days=args.days,
                max_candidates=args.max_candidates,
                max_fallback_candidates=args.max_fallback_candidates,
                allow_development_fixture=args.allow_development_fixture,
                progress=_emit_pull_now_progress,
            )
        else:
            with BibliographicCache(cache_path) as bibliography_cache:
                result = run_on_demand(
                    config,
                    lookback_days=args.days,
                    max_candidates=args.max_candidates,
                    max_fallback_candidates=args.max_fallback_candidates,
                    bibliography_cache=bibliography_cache,
                    allow_development_fixture=args.allow_development_fixture,
                    progress=_emit_pull_now_progress,
                )
    delivery_payload = _deliver_on_demand_report(result, config, args.report_delivery)
    _emit(
        {
            "ok": True,
            "operation": "pull-now",
            "mode": "on_demand",
            "request_id": result.request_id,
            "window_start": result.window_start,
            "window_end": result.window_end,
            "stats": result.stats,
            "network_telemetry": summarize_request_telemetry(
                events,
                circuit_skipped=result.stats.get("bibliographic_source_circuit_skips", 0),
            ),
            **delivery_payload,
            "state_database_required": False,
            "state_advanced": False,
            "weekly_notifications_written": False,
            "weekly_overlap_allowed": True,
            "bibliography_cache_path": (
                None if args.no_bibliography_cache else str(cache_path.resolve())
            ),
            "bibliography_cache_is_weekly_state": False,
            "bibliography_cache_updated": (
                result.stats["bibliography_cache_writes"] > 0
                or result.stats["bibliography_cache_scheduling_writes"] > 0
            ),
        }
    )
    return 0


def _catch_up(args: argparse.Namespace) -> int:
    config = load_watchlist(args.config)
    if not config.storage.state_database.is_file():
        raise ValueError(f"状态库不存在：{config.storage.state_database}；请先运行 pfm baseline。")
    as_of = _parse_instant(args.as_of, "--as-of")
    with (
        collect_request_telemetry() as events,
        StateStore(config.storage.state_database) as state,
    ):
        schema_backup_created = state.last_schema_backup is not None
        try:
            result = run_weekly_catch_up(
                config,
                state,
                now=as_of,
                dry_run=args.dry_run,
                max_windows=args.max_windows,
            )
        except CatchUpError as error:
            source_failure = _source_failure_payload(error)
            _emit(
                {
                    "ok": False,
                    "operation": "catch-up",
                    "error_type": type(error.__cause__).__name__,
                    "error": str(error),
                    "failed_window": error.failed_window,
                    "completed_windows": [
                        (item.window_start, item.window_end) for item in error.completed_runs
                    ],
                    "interest_profile_registered": error.profile_snapshot_registered,
                    "schema_backup_created": schema_backup_created,
                    "state_advanced": (
                        (
                            bool(error.completed_runs)
                            or error.profile_snapshot_registered
                            or schema_backup_created
                        )
                        and not args.dry_run
                    ),
                    "retry_safe": True,
                    "network_telemetry": summarize_request_telemetry(events),
                    **({"source_failure": source_failure} if source_failure is not None else {}),
                }
            )
            return 1

    completed = [
        {
            "run_id": item.run_id,
            "window_start": item.window_start,
            "window_end": item.window_end,
            "report_path": str(item.report_path) if item.report_path else None,
            "stats": item.stats,
            **({"report_markdown": item.report_markdown} if item.dry_run else {}),
        }
        for item in result.completed_runs
    ]
    _emit(
        {
            "ok": True,
            "operation": "catch-up",
            "status": result.plan.status,
            "dry_run": result.dry_run,
            "as_of": result.plan.as_of,
            "latest_due_window": result.plan.latest_due_window,
            "missing_window_count": len(result.plan.missing_windows),
            "completed_windows": completed,
            "interest_profile_registered": result.profile_snapshot_registered,
            "schema_backup_created": schema_backup_created,
            "state_advanced": (
                (bool(completed) or result.profile_snapshot_registered or schema_backup_created)
                and not result.dry_run
            ),
            "network_telemetry": summarize_request_telemetry(
                events,
                circuit_skipped=sum(
                    item.stats.get("bibliographic_source_circuit_skips", 0)
                    for item in result.completed_runs
                ),
            ),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pfm", description="Philosophy Frontier Monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="validate the local project and optional feed")
    doctor.add_argument("--taxonomy", default=str(_default_fixture()))
    doctor.add_argument(
        "--live",
        action="store_true",
        help="run read-only DNS, TLS, HTTP and response-contract checks for runtime sources",
    )
    doctor.add_argument(
        "--source",
        action="append",
        choices=("philpapers", "philarchive", "openalex", "crossref"),
        help="with --live, limit checks to this source; repeat for several sources",
    )
    doctor.add_argument(
        "--expected-openalex-requests",
        type=int,
        default=0,
        help=(
            "compare an explicit request estimate with a returned OpenAlex remaining-budget header"
        ),
    )
    doctor.add_argument("--category-url", default="https://philpapers.org/browse/plato-theaetetus")
    doctor.set_defaults(handler=_doctor)

    profile = subparsers.add_parser("profile", help="map research text to verified categories")
    research_input = profile.add_mutually_exclusive_group(required=True)
    research_input.add_argument("--research")
    research_input.add_argument(
        "--research-file", help="UTF-8 text file; recommended for Chinese text on older consoles"
    )
    profile.add_argument("--taxonomy", default=str(_default_fixture()))
    profile.set_defaults(handler=_profile)

    scope_estimate = subparsers.add_parser(
        "scope-estimate",
        help="estimate feed breadth and resource implications before expanding interests",
    )
    scope_estimate.add_argument(
        "--category-id",
        action="append",
        required=True,
        help="verified PhilPapers category ID; repeat to estimate several additions",
    )
    scope_estimate.add_argument(
        "--include-descendants",
        action="store_true",
        help="expand every requested category to all active descendants",
    )
    scope_estimate.add_argument(
        "--config",
        default=str(_default_watchlist()),
        help="existing watchlist used only to calculate the net addition",
    )
    scope_estimate.add_argument(
        "--taxonomy",
        help="taxonomy snapshot; defaults to taxonomy.path from an existing config",
    )
    scope_estimate.set_defaults(handler=_scope_estimate)

    feed = subparsers.add_parser("feed", help="discover and optionally read a category RSS feed")
    feed.add_argument("--category-url", required=True)
    feed.add_argument("--fetch", action="store_true")
    feed.add_argument("--sample", type=int, default=1)
    feed.set_defaults(handler=_feed)

    taxonomy_fetch = subparsers.add_parser(
        "taxonomy-fetch",
        help="fetch and validate the authenticated full PhilPapers taxonomy",
    )
    taxonomy_fetch.add_argument(
        "--output-directory",
        default=str(_default_taxonomy_directory()),
        help="private ignored directory for the normalized snapshot",
    )
    taxonomy_fetch.add_argument(
        "--credential-file",
        default=str(_default_credential_file()),
        help="private ignored credential file; environment variables take precedence",
    )
    taxonomy_fetch.set_defaults(handler=_taxonomy_fetch)

    taxonomy_import = subparsers.add_parser(
        "taxonomy-import",
        help="validate and import an official JSON file downloaded in a browser",
    )
    taxonomy_import.add_argument("--input", required=True)
    taxonomy_import.add_argument(
        "--output-directory",
        default=str(_default_taxonomy_directory()),
        help="private ignored directory for the normalized snapshot",
    )
    taxonomy_import.set_defaults(handler=_taxonomy_import)

    taxonomy_audit = subparsers.add_parser(
        "taxonomy-audit",
        help="compare verified taxonomy snapshots and report watchlist impact without mutation",
    )
    taxonomy_audit.add_argument("--old", required=True)
    taxonomy_audit.add_argument("--new", required=True)
    taxonomy_audit.add_argument("--config", default=str(_default_watchlist()))
    taxonomy_audit.add_argument(
        "--allow-development-fixture",
        action="store_true",
        help="tests only; production audits require two complete taxonomy snapshots",
    )
    taxonomy_audit.set_defaults(handler=_taxonomy_audit)

    release_check = subparsers.add_parser(
        "release-check",
        help="audit public-release privacy and packaging readiness without mutation",
    )
    release_check.add_argument(
        "--project-root",
        default=str(_project_root()),
        help="project directory whose prospective public surface will be checked",
    )
    release_check.add_argument(
        "--private-config",
        help=(
            "optional local watchlist used only to detect exact profile duplication; "
            "values are never emitted"
        ),
    )
    release_check.set_defaults(handler=_release_check)

    onboarding_status = subparsers.add_parser(
        "onboarding-status",
        help="report first-dialogue setup needs without printing private profile content",
    )
    onboarding_status.add_argument("--config", default=str(_default_watchlist()))
    onboarding_status.set_defaults(handler=_onboarding_status)

    baseline = subparsers.add_parser(
        "baseline",
        help="record current category-feed contents as pre-existing history",
    )
    baseline.add_argument("--config", default=str(_default_watchlist()))
    baseline.add_argument(
        "--allow-development-fixture",
        action="store_true",
        help=(
            "development only; write a fixture baseline that can support later dry runs, "
            "never a live weekly run"
        ),
    )
    baseline.set_defaults(handler=_baseline)

    weekly = subparsers.add_parser(
        "weekly-run",
        help="collect new feed records, check old-work evidence, and render a weekly report",
    )
    weekly.add_argument("--config", default=str(_default_watchlist()))
    weekly.add_argument("--dry-run", action="store_true")
    weekly.add_argument("--window-start")
    weekly.add_argument("--window-end")
    weekly.set_defaults(handler=_weekly_run)

    pull_now = subparsers.add_parser(
        "pull-now",
        help="immediately verify and report matching papers from a rolling recent window",
    )
    pull_now.add_argument("--config", default=str(_default_watchlist()))
    pull_now.add_argument(
        "--days",
        type=int,
        default=7,
        help="rolling local-time lookback window; allowed range 1-31 days",
    )
    pull_now.add_argument(
        "--max-candidates",
        type=int,
        default=1000,
        help="stop before bibliographic lookups when the candidate set exceeds this limit",
    )
    pull_now.add_argument(
        "--max-fallback-candidates",
        type=int,
        default=50,
        help=(
            "maximum candidates that may require remote individual old-work lookups after "
            "DOI/title batches; fresh-cache candidates do not consume slots, and excess "
            "candidates are reported as deferred"
        ),
    )
    pull_now.add_argument(
        "--allow-development-fixture",
        action="store_true",
        help="development only; production pulls require a complete taxonomy snapshot",
    )
    pull_now.add_argument(
        "--no-bibliography-cache",
        action="store_true",
        help=(
            "disable the separate private exact-lookup cache; does not change weekly "
            "notification or checkpoint state"
        ),
    )
    pull_now.add_argument(
        "--report-delivery",
        choices=("inline", "file"),
        default="inline",
        help=(
            "inline returns full Markdown in JSON; file writes the full report to the private "
            "report directory and returns only its path and statistics"
        ),
    )
    pull_now.set_defaults(handler=_pull_now)

    catch_up = subparsers.add_parser(
        "catch-up",
        help="detect and run every due missing weekly window in chronological order",
    )
    catch_up.add_argument("--config", default=str(_default_watchlist()))
    catch_up.add_argument("--dry-run", action="store_true")
    catch_up.add_argument(
        "--as-of",
        help="diagnostic ISO 8601 time; normal scheduled runs use the current time",
    )
    catch_up.add_argument(
        "--max-windows",
        type=int,
        default=8,
        help="fail before collection when more missing windows require review",
    )
    catch_up.set_defaults(handler=_catch_up)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, RuntimeError, ValueError) as error:
        source_failure = _source_failure_payload(error)
        payload = {
            "ok": False,
            "operation": args.command,
            "error_type": type(error).__name__,
            "error": str(error),
            "state_advanced": False,
        }
        if source_failure is not None:
            payload["source_failure"] = source_failure
        _emit(payload)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
