---
name: philosophy-frontier-monitor-workbuddy
description: Use the locally installed Philosophy Frontier Monitor to search existing philosophy papers, pull recent records, and preview weekly reports with source evidence.
description_zh: 在 WorkBuddy 中调用本机哲学论文监测项目，按来源证据检索已有论文、即时拉取和预览周报。
description_en: Use a local Philosophy Frontier Monitor installation for evidence-based paper search and reports.
version: 0.2.0
author: Asahina Mafuyu
---

# Philosophy Frontier Monitor for WorkBuddy

This local Skill package contains a snapshot of the project's public Skill and its referenced policies. The `pfm` program is installed separately. The ZIP contains no watchlist, private report, state database, or credential.

Before a request, read @references/project/SKILL.md and the policy files it names for the selected mode. The packaged snapshot records the evidence rules used to make this ZIP. Locate the local project root, then compare its root `SKILL.md` SHA-256 with `source_skill_sha256` in @references/package-manifest.json. If they differ, identify the mismatch and rebuild the package from that project or select the matching checkout before running a report. Read the selected project's current policies as well. Do not combine one version's instructions with another version's CLI silently.

Run the CLI from the matching project root. On Windows use `.venv\Scripts\pfm.exe`; on macOS/Linux use `.venv/bin/pfm`. Start with `pfm doctor` and `pfm onboarding-status`, and confirm `doctor` reports that project root. If the virtual environment points to another checkout, reinstall the editable project in the selected root before using private data. If WorkBuddy cannot read the project or run commands, explain the missing access.

Use `pfm search` for historical search under the requested category, year, and type scope; it is not restricted to seven days. Use `pfm pull-now` for an immediate recent window. Run `pfm weekly-run --dry-run` and inspect the private review queue before a committed weekly report. A committed weekly run changes state and must follow the project's prerequisites and the user's requested scope.

Apply the packaged and current project policies to source classification, publication dates, bibliographic identity, work type, and unresolved evidence. Inspect conflicting works individually when the policy requires it. A work-type decision alone does not establish a date or identity. Preserve evidence fingerprints, the selected search or time window, and source coverage limits.

Keep credentials, watchlists, review decisions, state, and private reports local. Do not paste them into chat or upload them with this Skill. This pilot does not configure WorkBuddy scheduling or notifications.
