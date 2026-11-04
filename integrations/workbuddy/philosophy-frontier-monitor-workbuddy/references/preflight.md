# Read-only local preflight

Run the packaged `scripts/preflight.py` with the project's installed Python before first use, after changing the project or package, and before starting `pull-now` or a weekly monitoring workflow. On this Windows installation:

```powershell
cd D:\philosophy-frontier-monitor
.\.venv\Scripts\python.exe var\workbuddy-pilot-0.2.9\philosophy-frontier-monitor-workbuddy\scripts\preflight.py
```

The helper performs local reads only. It checks the package's public hashes against the intended project, invokes read-only `pfm doctor` and `pfm onboarding-status`, loads the **configured** taxonomy with the project's validation code, and reads configured-feed baseline presence through a read-only SQLite connection. It returns only counts, booleans, and safe error codes; it does not print the research description, category names, feed URLs, credentials, or database contents.

Interpret its results precisely:

- `doctor_ok` means the CLI self-check passed. The fixture mentioned in `doctor` belongs to that self-check; it says nothing about `configured_taxonomy`.
- `alignment.source_commit_matches` is `true` only after Git returns the expected commit, `false` only after it returns a different commit, and `null` if the Git check cannot run or fails. In the last case, `git_commit_check_status` is `unverified`. A `null` value is not evidence of mismatch or of a clean working tree. Inspect `prerequisite_warnings`; exact file hashes and CLI checks remain separate.
- `configured_taxonomy.production_contract_valid` is the check relevant to formal monitoring. It still does not prove that the live network sources are reachable now.
- `baseline.all_configured_feeds_baselined` is a local database check. It does not prove that a later `weekly-run` succeeds or that the schedule and historical profile can be replayed.
- `mode_local_prerequisites` checks local gates only. Never turn one of its `true` values into a claim of complete source coverage or end-to-end acceptance.
- An unrecorded optional public PhilPapers account decision is separate from taxonomy API credentials and does not by itself block historical search.

If a package hash, source snapshot, or successfully read Git commit differs, stop that workflow and report the mismatch. If Git is unverified, report the warning without treating it as a confirmed mismatch. If a local monitoring prerequisite is false, report the specific false field and follow the project installation or taxonomy policy. Do not create a baseline, fetch a taxonomy, run a live weekly report, or change credentials merely because this inspection found a gap.
