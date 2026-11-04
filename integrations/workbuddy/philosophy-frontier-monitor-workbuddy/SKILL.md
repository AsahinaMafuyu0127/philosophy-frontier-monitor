---
name: philosophy-frontier-monitor-workbuddy
description: Use the locally installed Philosophy Frontier Monitor to search philosophy papers, pull recent records, and preview weekly reports with source evidence.
description_zh: 在 WorkBuddy 中调用本机哲学论文监测项目，依来源证据检索、即时拉取和预览周报。
description_en: Use a local Philosophy Frontier Monitor installation for evidence-based paper search and reports.
version: 0.2.10
author: Asahina Mafuyu
---

# Philosophy Frontier Monitor for WorkBuddy

This pilot contains the public project Skill and its policies, not the Python program or private data. Locate the intended project root recorded in the package manifest before using the Skill. Read @references/project/SKILL.md and its mode-specific policies before acting.

Read @references/package-manifest.json. Compare the project root `SKILL.md` SHA-256 with `source_skill_sha256`, and compare every `snapshot_files` entry with the corresponding project file. Attempt `git rev-parse HEAD` in the intended checkout. A returned commit that differs from `source_commit` is a mismatch; an unavailable or failed Git check is **unverified**, not a mismatch. Report that uncertainty without claiming the commit matches. If the project and package actually differ, report the exact mismatch and stop the report workflow until they are aligned. Never combine a newer instruction snapshot with an older CLI.

Run `.venv\Scripts\pfm.exe` from the verified project root on Windows. Before a monitoring mode, use the read-only `scripts/preflight.py` as described in @references/preflight.md. It checks the taxonomy actually configured in the private watchlist and the baseline without printing research text or feed URLs. `pfm doctor` uses an offline taxonomy fixture for its own check; that fixture is not the configured taxonomy. Check the selected CLI command's `--help`. An unrecorded optional PhilPapers account decision does not block historical search.

For historical search use `pfm search` with the user's category and year scope. For recent records use `pfm pull-now`. For weekly reports start with `pfm weekly-run --dry-run` and inspect the private review queue before a committed run. Do not silently broaden scope, increase fallback budgets, or treat feed inventory as a complete historical index.

Weekly previews and committed runs must use an already completed window.
The default selects the latest completed week, not the next missing window.
If it already succeeded, inspect that report and test duplicate rejection;
wait for the next complete week rather than requesting an unfinished window.
For acceptance, compare logical SQLite contents, not only table counts.
A formal run cannot take `--review-queue-file`; audit its result without the
preview queue. Check the result's execution timestamps against the window.
Missing execution-time evidence stays unverified. A zero-result commit does
not test nonzero notification writes.

Before explaining any mode's results, follow @references/audit-output.md and run the bundled `scripts/audit_result.py` against the saved final result JSON and corresponding private queue. Include `--record` for named PhilPapers works and `--identity-reviews` only for the decision file actually supplied to that run. Save its audit JSON and Markdown summary in new private files. Use those exact counts, source statuses, and original-field locators when composing the report; do not estimate them or invent alternative field names. Preserve absent, null, false, and zero as different states. Keep confirmed-result supporting queue entries separate from unresolved work. The helper does not decide work identity or publication status. If it rejects mismatched files or malformed JSON, correct the inputs before presenting a final report.

For historical-search conclusions about individual works, follow @references/search-result-evidence.md. Save the exact JSON output of the final run in the private project directory. When discussing named records, run `scripts/audit_search_records.py` against that JSON and its same-run queue. Membership in the result's `items` means a confirmed search match on the returned page; a queue reason such as `confirmed_type` is supporting type-review evidence and does not weaken or replace that final status. A record's absence from `items`, `unconfirmed_records`, and `year_review_candidates` does not identify its exclusion stage. Aggregate `stats` never establish an individual record's fate. Report the CLI's `total_matches` unchanged and distinguish publisher evidence about a work from evidence of how this CLI run processed it.

For suspect work forms, generate a private review queue and inspect the original work or publisher record individually. The queue includes unresolved types and confirmed results on the current search page. An exact, source-fingerprint-bound `--type-reviews` decision may correct an unknown type or an index label such as article when the original journal calls the work an editorial. A type decision does not establish DOI, author, or publication year. For year-bounded historical search, the private queue marks missing feed year hints as `year_review_pending`. Open promising works individually, verify the exact title, author, and publication year against the publisher or proceedings, then use the separate private `--bibliographic-reviews` path. A review can record a missing DOI as null when the publisher has no DOI; it cannot invent citation counts. A `--type-reviews` decision alone never supplies a year. For all bibliography reviews, check the publisher bibliography and feed identity. If a lookup fails, say the result is partial. A revised prose report must agree with a rerun's `total_matches`; if not, disclose the discrepancy and keep the result provisional.

For semantic bibliography identity questions in any mode, read @references/project/references/identity-review-policy.md. Save the same-run private review queue and inspect each `identity_review_pairs` external record, including its source, record ID, title, authors, DOI, date, URL, and evidence fingerprint. A pair decision is `equivalent` or `distinct` only after checking the actual external work. Record every relevant pair in a private decision JSON, then rerun the same mode with `--identity-reviews`; for `pull-now`, also reuse its feed snapshot with `--review-queue-input` and the same `--as-of` window. If a pair changed or remains undecided, keep the item unresolved. A resolved identity does not itself establish a new publication date, supported type, or final match. Report the rerun's actual result and never turn a prose review into a CLI decision.

Keep watchlists, review decisions, credentials, state, and private reports in the local project. The ZIP contains none of them. This pilot does not configure WorkBuddy scheduling or notifications.
