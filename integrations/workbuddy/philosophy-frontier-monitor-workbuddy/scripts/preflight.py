"""Read-only, privacy-limited local preflight for the WorkBuddy pilot."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli_json(executable: Path, root: Path, command: str) -> dict | None:
    result = subprocess.run(
        [str(executable), command],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, help="override the package's intended local project root"
    )
    args = parser.parse_args()
    package = Path(__file__).resolve().parent.parent
    manifest = json.loads(
        (package / "references" / "package-manifest.json").read_text(encoding="utf-8")
    )
    root = (args.project_root or Path(manifest["source_project_root_hint"])).resolve()
    output = {
        "operation": "workbuddy-local-preflight",
        "package_version": manifest["package_version"],
    }

    package_mismatches = [
        name
        for name, expected in manifest.get("package_files", {}).items()
        if not (package / name.removeprefix(package.name + "/")).is_file()
        or digest(package / name.removeprefix(package.name + "/")) != expected
    ]
    bundled_snapshot_mismatches = [
        name.removeprefix(package.name + "/references/project/")
        for name, expected in manifest["snapshot_files"].items()
        if not (package / name.removeprefix(package.name + "/")).is_file()
        or digest(package / name.removeprefix(package.name + "/")) != expected
    ]
    snapshot_mismatches = [
        name.removeprefix(package.name + "/references/project/")
        for name, expected in manifest["snapshot_files"].items()
        if not (root / name.removeprefix(package.name + "/references/project/")).is_file()
        or digest(root / name.removeprefix(package.name + "/references/project/")) != expected
    ]
    head = None
    commit_error_code = None
    try:
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if git_head.returncode == 0 and git_head.stdout.strip():
            head = git_head.stdout.strip()
        else:
            commit_error_code = "git_rev_parse_failed"
    except FileNotFoundError:
        commit_error_code = "git_executable_not_found"
    except (OSError, subprocess.TimeoutExpired):
        commit_error_code = "git_rev_parse_unavailable"
    tracked_changes = None
    tracked_error_code = None
    try:
        git_status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if git_status.returncode == 0:
            tracked_changes = bool(git_status.stdout.strip())
        else:
            tracked_error_code = "git_status_failed"
    except FileNotFoundError:
        tracked_error_code = "git_executable_not_found"
    except (OSError, subprocess.TimeoutExpired):
        tracked_error_code = "git_status_unavailable"
    commit_matches = None if head is None else head == manifest["source_commit"]
    alignment = {
        "package_files_match_manifest": not package_mismatches,
        "bundled_snapshot_matches_manifest": not bundled_snapshot_mismatches,
        "project_snapshot_matches_package": not snapshot_mismatches,
        "source_commit_matches": commit_matches,
        "git_commit_check_status": "unverified"
        if head is None
        else ("matched" if commit_matches else "mismatched"),
        "git_commit_error_code": commit_error_code,
        "tracked_project_changes_present": tracked_changes,
        "tracked_changes_error_code": tracked_error_code,
        "package_file_mismatches": package_mismatches,
        "bundled_snapshot_mismatches": bundled_snapshot_mismatches,
        "project_snapshot_mismatches": snapshot_mismatches,
    }
    output["alignment"] = alignment
    executable = root / ".venv" / "Scripts" / "pfm.exe"
    doctor = cli_json(executable, root, "doctor") if executable.is_file() else None
    onboarding = cli_json(executable, root, "onboarding-status") if executable.is_file() else None
    output["cli"] = {
        "executable_present": executable.is_file(),
        "doctor_ok": bool(doctor and doctor.get("ok")),
    }
    output["onboarding"] = {
        "status_available": onboarding is not None,
        "profile_exists": onboarding.get("profile_exists") if onboarding else None,
        "needs_research_direction": onboarding.get("needs_research_direction")
        if onboarding
        else None,
        "optional_account_decision_pending": onboarding.get("needs_philpapers_account_decision")
        if onboarding
        else None,
    }

    config_loaded = False
    confirmed_categories = 0
    feed_count = 0
    production_taxonomy = False
    taxonomy_error_code = None
    category_count = None
    baseline_count = None
    baseline_all = False
    state_database_exists = False
    profile_effective_from_recorded = False
    try:
        sys.path.insert(0, str(root / "src"))
        from philosophy_frontier_monitor.config import load_watchlist
        from philosophy_frontier_monitor.taxonomy import load_taxonomy, require_production_taxonomy

        config = load_watchlist(root / "config" / "watchlist.yaml")
        config_loaded = True
        confirmed_categories = len(config.confirmed_categories)
        feed_count = len(config.feeds)
        profile_effective_from_recorded = config.profile_effective_from is not None
        try:
            snapshot = load_taxonomy(config.taxonomy_path)
            category_count = len(snapshot.categories)
            require_production_taxonomy(snapshot)
            production_taxonomy = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            taxonomy_error_code = type(exc).__name__
        db_path = config.storage.state_database
        state_database_exists = db_path.is_file()
        if state_database_exists:
            try:
                with sqlite3.connect(
                    "file:" + db_path.resolve().as_posix() + "?mode=ro", uri=True
                ) as db:
                    baselined = {
                        row[0] for row in db.execute("SELECT feed_key FROM feed_subscriptions")
                    }
                baseline_count = sum(feed.feed_key in baselined for feed in config.feeds)
                baseline_all = feed_count > 0 and baseline_count == feed_count
            except sqlite3.Error:
                baseline_count = None
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        pass
    output["watchlist"] = {
        "config_loaded": config_loaded,
        "confirmed_category_count": confirmed_categories if config_loaded else None,
        "configured_feed_count": feed_count if config_loaded else None,
        "profile_effective_from_recorded": profile_effective_from_recorded
        if config_loaded
        else None,
    }
    output["configured_taxonomy"] = {
        "production_contract_valid": production_taxonomy,
        "category_count": category_count,
        "error_code": taxonomy_error_code,
    }
    output["baseline"] = {
        "state_database_exists": state_database_exists,
        "configured_feeds_baselined": baseline_count,
        "all_configured_feeds_baselined": baseline_all,
    }
    aligned = (
        all(
            alignment[key]
            for key in (
                "package_files_match_manifest",
                "bundled_snapshot_matches_manifest",
                "project_snapshot_matches_package",
            )
        )
        and commit_matches is not False
    )
    search_local = (
        aligned and output["cli"]["doctor_ok"] and config_loaded and confirmed_categories > 0
    )
    pull_local = search_local and production_taxonomy and feed_count > 0
    weekly_local = pull_local and baseline_all and profile_effective_from_recorded
    output["mode_local_prerequisites"] = {
        "historical_search": search_local,
        "pull_now": pull_local,
        "weekly_run": weekly_local,
    }
    output["prerequisite_warnings"] = (
        ["git_commit_unverified"] if commit_matches is None else []
    ) + (["tracked_changes_unverified"] if tracked_changes is None else [])
    output["network_sources_checked"] = False
    output["state_mutated"] = False
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"preflight failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
