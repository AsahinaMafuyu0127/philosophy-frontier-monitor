# English user guide

This guide covers the ordinary local workflow after installation. For environment creation,
credentials, and upgrades, see [Installation and first deployment](installation.en.md).

## 1. Working directory and private files

Run `pfm` from the directory containing `SKILL.md`, or pass an explicit private configuration path.
Do not search unrelated projects for a watchlist. These paths are private and Git-ignored by
default:

- `config/watchlist.yaml`: interests, confirmed categories, source settings, and schedule;
- `var/state.sqlite3`: weekly baseline, checkpoints, retry state, and notifications;
- `var/bibliography-cache.sqlite3`: bounded bibliographic cache and rotation state;
- `var/oai-cache.sqlite3`: OAI events, coverage, staged pages, and interruption checkpoints; and
- `reports/`: weekly and explicitly file-delivered on-demand reports.

Do not paste or upload these files when asking for help. Supply redacted errors and count-only cache
status instead.

## 2. Diagnose the installation

Run local configuration and dependency checks:

```powershell
.\.venv\Scripts\pfm.exe doctor
```

Run explicit live source diagnostics only when network verification is needed:

```powershell
.\.venv\Scripts\pfm.exe doctor --live
```

Live diagnostics are read-only. A failed source must be reported as a coverage failure; do not
disable TLS or bypass access controls to make the check pass.

## 3. Confirm the research profile

Describe the research direction in ordinary Chinese or English. The Skill maps it to candidates in
a verified PhilPapers taxonomy. Review each proposed category, its relation to the description, and
its likely breadth. A proposal does not become active until the user confirms it.

Before adding a parent category with descendants, inspect the expanded scope:

```powershell
.\.venv\Scripts\pfm.exe scope-estimate `
  --category-id <verified-category-id> `
  --include-descendants `
  --config config\watchlist.yaml
```

The runtime matching rule is deterministic category-set intersection. It does not rank quality,
importance, author reputation, journal prestige, or citations.

## 4. Establish the historical baseline

After the taxonomy and confirmed feeds are ready:

```powershell
.\.venv\Scripts\pfm.exe baseline --config config\watchlist.yaml
```

The baseline records current feed entries as pre-existing history. It does not generate a flood of
historical-paper notifications. Verify the resulting counts before scheduling weekly runs.

## 5. Run the weekly monitor

Use a dry run first:

```powershell
.\.venv\Scripts\pfm.exe weekly-run --config config\watchlist.yaml --dry-run
```

Then run the production weekly window:

```powershell
.\.venv\Scripts\pfm.exe weekly-run --config config\watchlist.yaml
```

A production checkpoint advances only after the report is written and the complete SQLite
transaction succeeds. Every Markdown report contains a complete Chinese section and a complete
English section derived from the same works, evidence, source status, and counts.

Within each language, matching papers appear in three fixed sections: recently published,
recently arrived, and recently changed. The first is ordered by publication evidence; the other
two are ordered by recent-availability or OAI record-change evidence. Recently changed is visibly
switchable but collapsed by default, and source coverage follows the complete matching-paper block.
Use `--show-recently-changed` with `weekly-run`, `catch-up`, or `pull-now` when the source Markdown
should begin with that section expanded, such as before conversion to Word or PowerPoint.

## 6. Run an on-demand pull

An on-demand pull is explicit, read-only with respect to weekly state, and uses a rolling window of
seven local days by default:

```powershell
.\.venv\Scripts\pfm.exe pull-now --config config\watchlist.yaml
```

Select 1–31 local days with `--days`:

```powershell
.\.venv\Scripts\pfm.exe pull-now --config config\watchlist.yaml --days 14
```

For long reports, write one private bilingual Markdown file instead of returning the report inline:

```powershell
.\.venv\Scripts\pfm.exe pull-now `
  --config config\watchlist.yaml `
  --report-delivery file
```

To write a conversion-ready report with the optional record-change group expanded:

```powershell
.\.venv\Scripts\pfm.exe pull-now `
  --config config\watchlist.yaml `
  --report-delivery file `
  --show-recently-changed
```

`pull-now` does not create weekly notifications or advance the baseline, run history, retry queue,
or feed checkpoints. A paper may therefore appear again in the next eligible weekly report.

Before network access, the command prints storage advice. OAI and bibliographic caches may grow
substantially during an upstream bulk metadata update. On Windows, prefer a spacious non-system
drive over a constrained `C:` drive when one is available. Change `storage.state_database` in the
private watchlist before moving data; the Skill does not have permission to relocate existing files
unless the user explicitly requests it.

## 7. Understand an on-demand result

The report separates three unfinished-work categories:

- **Not reached by remote verification in this run:** the bounded per-item request budget ended
  before these candidates were processed. This is a per-run boundary, not a persistent backlog,
  and no user judgment is requested.
- **Human review required:** structured identifiers, work types, or semantic identity evidence
  conflict and cannot be resolved deterministically.
- **Awaiting automatic retry:** a source failed or deferred the request; the program should retry
  later under its bounded policy.

An OAI `datestamp` means that a source record was created, modified, or deleted. It is never the
paper's publication date. Fully undated, non-open PhilPapers records may remain outside on-demand OAI
narrowing; the project does not scrape paywalled or protected record pages to pursue them.

## 8. Inspect or clean the OAI cache

Inspect count-only status:

```powershell
.\.venv\Scripts\pfm.exe oai-cache status --config config\watchlist.yaml
```

Status includes file size, formal event count, coverage count, interrupted-session count, completed
pages, staged records, and whether a token exists. It never prints the token.

Remove rebuildable data older than an explicit cutoff only after reviewing the target:

```powershell
.\.venv\Scripts\pfm.exe oai-cache prune `
  --config config\watchlist.yaml `
  --before 2026-09-01T00:00:00Z `
  --confirm
```

Pruning is refused during an active harvest. It creates future network gaps but does not modify
weekly state, the baseline, checkpoints, or notification history.

## 9. Catch up missed weekly windows

Inspect and run missed windows in chronological order:

```powershell
.\.venv\Scripts\pfm.exe catch-up --config config\watchlist.yaml
```

Do not replace catch-up with a large on-demand window. Catch-up preserves weekly window and
notification semantics; `pull-now` is an independent user-requested report.

## 10. Report problems safely

For an ordinary reproducible problem, provide:

- package version and operating system;
- the command and on-demand window length;
- a redacted error summary;
- source success or failure counts;
- machine-backlog, human-review, and automatic-retry counts; and
- count-only `oai-cache status` fields when relevant.

Never provide an API key, password, cookie, resumption token, private watchlist, cache database,
complete report, or authenticated URL. Use GitHub Private Vulnerability Reporting for a security
issue that could expose credentials or private research data.
