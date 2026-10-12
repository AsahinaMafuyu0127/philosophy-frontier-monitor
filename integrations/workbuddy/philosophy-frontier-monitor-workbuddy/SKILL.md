---
name: philosophy-frontier-monitor-workbuddy
description: Use the locally installed Philosophy Frontier Monitor to search existing philosophy papers, pull recent records, and preview weekly reports with source evidence.
description_zh: 在 WorkBuddy 中调用本机哲学论文监测项目，按来源证据检索已有论文、即时拉取和预览周报。
description_en: Use a local Philosophy Frontier Monitor installation for evidence-based paper search and reports.
version: 0.1.0
author: Asahina Mafuyu
---

# Philosophy Frontier Monitor for WorkBuddy

This Skill is a local bridge. The project and its `pfm` CLI must already be installed. The ZIP contains instructions only: no program, watchlist, report, or credential.

## Before running

Ask for the local project root if it is not already in this workspace. Check its root `SKILL.md`, `pyproject.toml`, and `pfm` entry point. Read that root Skill and the policies for the requested mode before collecting papers. The repository is authoritative for category, date, work type, bibliographic identity, evidence conflict, and privacy decisions. Adapt Codex-specific installer or scheduler steps to WorkBuddy without changing these evidence rules.

Run commands from the project root using its existing virtual environment: `.venv\Scripts\pfm.exe` on Windows or `.venv/bin/pfm` on macOS/Linux. Start with `pfm doctor` and `pfm onboarding-status`. Confirm that the root reported by `doctor` is the selected project root; an existing virtual environment may still point to a different checkout. Reinstall the editable project in that environment before using private configuration if the roots differ. If WorkBuddy cannot access the repository or execute the CLI, describe the missing access; do not simulate results.

## Route the request

- **Historical search:** use `pfm search` with the confirmed local watchlist and requested year, category, and type filters. Historical search has no automatic seven-day limit.
- **Immediate pull:** use `pfm pull-now` with the confirmed watchlist and recent window; default seven days. Keep unresolved evidence in the private review queue.
- **Weekly report:** run `pfm weekly-run --dry-run` first, inspect its report and queue, and follow the root policy before a committed run. A committed run writes state and requires the user's request for that run.

When structured sources disagree about a work's type, inspect the individual work or publisher record, especially for dates in the recent window. Record a supported decision in the private review file and apply it using `--type-reviews` where appropriate. Preserve the evidence fingerprint and original window or search constraints. A type decision alone never proves publication date or bibliographic identity. Leave cases unresolved when original evidence cannot be inspected. Report observed source coverage and failures.

Keep watchlists, state, review decisions, credentials, and private reports local. Do not paste secrets into chat or package them into this Skill. Ask before sending private content to an external service. This bridge does not configure WorkBuddy scheduling or notifications.
