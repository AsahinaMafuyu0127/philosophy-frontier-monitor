---
name: philosophy-frontier-monitor
description: Onboard a researcher's natural-language philosophy interests, map them to verified PhilPapers categories, and maintain recurring weekly new-paper reports with missed-run catch-up. Use when a user asks how to use, configure, update, inspect, run, or schedule philosophy-paper monitoring by research direction. An immediate rolling pull exists only as an explicitly opted-in experimental mode. Do not use for judging paper quality, ranking papers, or general literature reviews.
---

# Philosophy Frontier Monitor

Use this skill to maintain a transparent, category-based watchlist for newly
published philosophy papers. The researcher, not the skill, judges scholarly
quality and importance.

## Installation and runtime root

For installation, environment setup, or first deployment, read
[installation.md](references/installation.md). Treat the directory containing
this `SKILL.md` as the skill root. Run the platform-specific `pfm` executable
from that root (`.venv\\Scripts\\pfm.exe` on Windows or `.venv/bin/pfm` on
macOS/Linux); do not assume the user's current working directory is the skill
root. If the virtual environment is absent, guide the user through `uv sync`
and `pfm doctor` before any live run. Do not overwrite an existing private
watchlist, state database, taxonomy snapshot, or report while installing or
updating.

## First-dialogue onboarding

At the first substantive interaction, run the read-only `pfm onboarding-status`
against the private watchlist when available, then follow
[first-dialogue-onboarding.md](references/first-dialogue-onboarding.md). If no
private profile exists, the same reply must tell the user that monitoring needs
their natural-language research direction and ask them to provide it. It must
also separately ask whether they want to provide a PhilPapers or PhilPeople
public profile URL and authorize read-only use of publicly displayed
self-declared interests. This applies whether the user asks how the skill works
or directly asks to create a scheduled report.

Account evidence is optional. State that declining or deferring does not reduce
the text-only monitoring workflow. Never ask for a password, cookie, session
token, recovery code, or API key, and never treat taxonomy credentials as
account authorization. A general yes to public-profile inspection does not
authorize public bibliographies, reading lists, or My Works; explain and obtain
the relevant explicit scope before reading each of those sources.

Do not repeat completed onboarding on later conversations. A legacy profile
already supplies the research direction, so ask only for the missing account
decision. Persist the eventual decision and scopes only in the Git-ignored
private watchlist. Do not create a user-specific production subscription,
baseline, or scheduler until the research direction has been mapped to real
taxonomy categories and the user has confirmed the active set. Refusal of
account access is not a reason to stop that mapping.

## Required inputs

Obtain or reuse:

- the researcher's natural-language research direction;
- the reporting timezone, weekday, and local delivery time. Default to Monday
  at 08:00 in the user's known local timezone;
- any explicitly requested category exclusions.

Do not require the user to know PhilPapers category names in advance.

## Workflow

1. Before a production profile or live run, require a complete authenticated
   PhilPapers taxonomy snapshot. If it is absent, direct the user to obtain an
   API ID and API key from PhilPapers. Accept either the local environment
   variables `PHILPAPERS_API_ID` and `PHILPAPERS_API_KEY`, or a labelled private
   credential file under the Git-ignored `var/secrets/` directory, then run
   `pfm taxonomy-fetch`. Never ask the user to paste either value into the
   conversation. If Cloudflare challenges the API client, have the user save
   the same official JSON response in a private browser session and validate it
   with `pfm taxonomy-import`; do not impersonate a browser or reuse cookies to
   bypass the challenge.
2. When a new taxonomy snapshot is obtained, retain the old file and run
   `pfm taxonomy-audit` before changing the private watchlist. Read
   [taxonomy-lifecycle.md](references/taxonomy-lifecycle.md). Never infer a
   category migration from name similarity, and never activate a new snapshot
   while the audit is `review_required` or `blocked`.
3. Read [interest-tag-matching.md](references/interest-tag-matching.md) and
   [interest-inference-policy.md](references/interest-inference-policy.md)
   before creating or changing an interest profile.
4. Interpret the research description in adaptive mode. Use limited exploratory
   inference when the description is broad; use minimal inference when it
   already identifies a text, proposition, controversy, comparison, or clear
   exclusions. Do not classify the user as a novice or expert.
5. Keep confirmed categories and reasoned proposals separate. Resolve every
   proposal against the installed taxonomy, explain its relation and likely
   breadth, and ask the user whether to accept, reject, or defer it. A proposal
   never becomes an active feed until the user confirms it.
   Before activating a parent category with descendants, run the read-only
   `pfm scope-estimate` and follow
   [resource-and-scope-warnings.md](references/resource-and-scope-warnings.md).
   Report the expanded and net-new feed counts. An addition of 11 or more feeds
   requires a resource warning and explicit confirmation; an addition above 50
   must also offer narrower child branches. Do not invent an exact token number:
   deterministic taxonomy expansion is local, while API work, report length,
   and model context depend on the later candidate count.
6. Use PhilPapers account evidence only with explicit authorization. Preserve
   the difference between self-declared interests, public bibliographies,
   to-read items, and automatically attributed My Works. Never ask for a
   password or browser cookie.
7. Read [monitoring-policy.md](references/monitoring-policy.md) and
   [date-and-version-semantics.md](references/date-and-version-semantics.md)
   before collecting papers.
   Weekly and on-demand delivery must use the same freshness, old-work,
   republication, bibliographic-identity, supported-work-type, and category-set
   gates. On-demand batching and fallback budgets are performance mechanisms,
   not a separate evidence policy.
8. The public v0.1 stable contract is the baseline, weekly run, and missed-run
   catch-up workflow. `pull-now` remains an experimental cold-start mode because
   real PhilPapers category feeds omit day-level timestamps and DOI coverage is
   sparse. When the user explicitly asks to test it, explain that a broad profile
   may require hundreds of individual bibliographic lookups and obtain explicit
   agreement before raising either safety limit. Then run `pfm pull-now --config
   config/watchlist.yaml`. This separate, read-only mode has a
   default window is the seven rolling local days ending at the request time;
   `--days` may select 1 through 31 days. It requires a verified production
   taxonomy and configured feeds, but does not require a weekly baseline or
   state database. Read every configured feed, use feed timestamps or an
   in-memory bibliography-year and early-work-status hints to bound and
   prioritize candidates. Treat Crossref and OpenAlex as optional bibliographic
   evidence and old-work detectors, not mandatory gatekeepers. If neither has
   indexed a candidate, a new PhilPapers alert entry may still be emitted as
   `confirmed_source_arrival` after the old-work check completes; preserve its
   PhilPapers record ID, do not invent a publication date, and label the report
   as recent source arrival. Include manuscripts, working papers, preprints,
   author-accepted manuscripts, and forthcoming articles when they satisfy the
   same arrival and old-work rules.
   Batch DOI and normalized-title candidates through OpenAlex, then use bounded
   Crossref/OpenAlex individual lookup for unresolved fallbacks. Compare
   bibliographic identity by a hierarchy of shared identifiers, compatible
   author renderings, diacritic-insensitive spelling, title content tokens, and
   high-confidence orthographic similarity. Do not reduce identity to literal
   title or surname equality. A cross-language title pair without a shared
   identifier remains `review_required`; Codex may reason about its meaning and
   ask the user to confirm an alias, but deterministic code must not silently
   merge it. When the fallback count exceeds the configured maximum, prioritize
   candidates with recent dates or explicit early-work status and report the
   remainder as deferred instead of failing the complete pull or pretending it
   was checked. Anonymous OpenAlex access is suitable for casual use; if repeated
   requests exhaust its daily budget, accept `OPENALEX_API_KEY` from the local
   process environment without printing or persisting it.
   Never read or write weekly notification history, retry state, run history,
   or checkpoints in this mode. Repeated on-demand pulls and a later weekly
   report may therefore contain the same paper; this is intentional. Do not
   silently turn an explicit on-demand request into `weekly-run` or `catch-up`.
   Show count-only progress for feed loading, candidate selection, DOI/title
   batches, and individual fallbacks; never include paper titles, descriptions,
   query URLs, or credentials in progress output.
   Reuse bounded HTTP connection pools across category feeds and individual
   bibliographic fallbacks. Read [source-catalog.md](references/source-catalog.md)
   before attributing a delay or omission to a provider. A source limitation may
   qualify completeness or timeliness, but it never excuses silent partial runs,
   fabricated dates, unsafe retries, or credential exposure.
   For 408, 429, selected 5xx, and transport failures, follow
   [source-retry-policy.md](references/source-retry-policy.md): use bounded
   exponential backoff, honor a short `Retry-After`, never wait beyond the run
   budget or retry a permanent 4xx, and open a per-run source circuit after the
   bounded attempt limit. Report the source, failure class, coverage impact, and
   safe retry time without URLs, query text, response bodies, or credentials.
   Before attributing a failed run to a provider, use `pfm doctor --live` (or
   narrow it with repeated `--source`) to distinguish DNS, TLS, HTTP, and
   response-contract failures. Diagnostics must remain read-only. Preserve
   only numeric rate-limit headers and aggregate counters in network telemetry;
   distinguish first-attempt success, retry success, long Retry-After deferral,
   and circuit-skipped work. If a broad pull has an explicit request estimate,
   compare it with a server-reported OpenAlex remainder and warn when the
   remainder is insufficient or unavailable rather than promising completion.
   By default, use the separate private bibliographic cache for per-DOI
   OpenAlex batch results, positive per-title OpenAlex batch candidates, and
   individual Crossref/OpenAlex fallbacks. DOI and individual positive metadata
   expire after 24 hours; successful DOI or individual `not_found` results
   expire after 15 minutes. Positive title-batch candidates expire after one
   hour, and empty title-batch results are never cached, so newly indexed work
   is not hidden by an ambiguous OR query. Never cache transport failures or
   semantic-review outcomes. Every cache hit must still pass the current
   freshness, identity, and category gates. The cache must not read, write, or
   suppress weekly notifications; honor `--no-bibliography-cache` when the user
   declines local caching.
   Full inline Markdown can itself consume substantial model context. When the
   measured candidate count, a previous report, or a deliberately raised safety
   limit indicates a long result, explain `--report-delivery file` before the
   run. Use it only after the user chooses file delivery: it writes the complete
   report to the private report directory while stdout contains only the path,
   character count, and statistics. The default remains `inline`, and file
   delivery must never create weekly notification or checkpoint state.
9. Before the first weekly run, fetch every configured category feed completely
   and establish one baseline. Baseline entries are prior history and must not
   be emitted as newly published papers. When the user later confirms new
   categories, establish an incremental baseline only for feeds that do not
   already have one; preserve all existing feed checkpoints.
10. On later runs, collect every configured feed completely, merge category
   evidence, and send only previously unseen or explicitly retryable records to
   the old-work and identity checks. Use external publication dates when they
   exist, but do not postpone a newly available PhilPapers manuscript merely
   because Crossref or OpenAlex has not indexed it.
11. Match only by deterministic set intersection after the newness gate passes.
12. Deduplicate works and suppress already-notified works using persistent local
   state. Keep unresolved bibliography/date cases in the bounded retry queue.
13. Write the factual weekly bibliography successfully before committing source
   records, notifications, run history, and all feed checkpoints in one SQLite
   transaction.
14. Persist every active interest version as an immutable local runtime snapshot.
    Store only the confirmed and expanded category sets, exclusions, category
    feeds, taxonomy identifier, effective time, and schedule semantics needed
    to replay matching. Do not duplicate the researcher's free-text description,
    unconfirmed proposals, rationales, or account evidence in SQLite. During
    catch-up, select the last profile effective at each window's scheduled
    delivery time; stop before network access if that profile, its taxonomy, or
    compatible schedule semantics are unavailable.
15. Enable an external weekly scheduler only after a manual baseline, dry run,
    live run, and failure-recovery test have all passed. The default schedule is
    Monday 08:00 in the user's timezone. If that run is missed because the
    computer or Codex is unavailable, the desired contract is to run at the
    later opportunity when Codex can run. No login-instant guarantee is required.
    Do not assume a scheduler provides missed-run replay: on every later
    ordinary invocation, run `pfm catch-up` to inspect successful windows and
    process any due gap before non-urgent work. An explicit `pull-now` request
    remains separate: deliver it without consuming or silently replacing the
    due weekly run, then report any weekly gap separately. This command uses `schedule.local_time`,
    processes multiple missing windows chronologically, and stops for review
    before collecting when the default eight-window safety limit is exceeded.
    Do not interrupt explicitly identified high-priority work; run
    concurrently when safe or defer to the next available opportunity. On
    failure, notify the user in Codex with the cause and affected coverage.
    Do not replace this with repeated ad hoc `weekly-run` calls or silently
    process only the most recent week.
16. Before preparing a public repository, read
    [release-readiness.md](references/release-readiness.md) and run
    `pfm release-check`. Treat credentials, the private watchlist, runtime state,
    reports, and complete taxonomy snapshots as private. The check is read-only:
    do not initialize Git, choose a license, create a remote repository, or
    publish anything without the user's corresponding decision or authorization.

## Non-negotiable invariants

- Never invent a PhilPapers category or present a model-generated keyword as an
  official category.
- Never use an inferred or account-derived proposal in weekly matching before
  the user confirms it.
- Never silently activate a broad descendant expansion. Estimate its net-new
  category-feed scope first, warn at the documented boundary, and obtain
  explicit confirmation before changing the watchlist or establishing baselines.
- Never treat an OAI datestamp, indexing time, recategorization time, or feed
  appearance as proof of formal publication. A current PhilPapers alert may
  instead support the narrower `confirmed_source_arrival` status only after the
  old-work check finds no earlier-work evidence.
- Never substitute semantic similarity, quality scoring, author reputation,
  journal prestige, citation counts, or a hidden ranking model for category-set
  intersection.
- Never download or retain full text by default.
- Never report a partial source run as complete. State failed sources and the
  affected coverage explicitly.
- Never advance a feed checkpoint when collection, report writing, or the final
  state transaction fails. A retry may fetch duplicate records; deduplication
  must make that safe.
- Never run a live weekly report with the bundled incomplete taxonomy fixture.
- Never describe feed timestamps or local observation times as publication
  dates. They may support recent source arrival, which must be labelled
  separately from `confirmed_new` publication evidence.
- Never retain or expose a feed description used to extract an on-demand year
  or DOI hint. These deterministic hints are request-cost bounds, not
  publication dates or report fields.
- Never write an authenticated taxonomy response, credential-bearing URL, API
  ID, or API key to a snapshot, log, report, fixture, or version control.
- Never expose a private research profile, credentials, or local state in logs
  or reports.

## Reference routing

- Read [metadata-model.md](references/metadata-model.md) when changing stored
  fields, identifiers, evidence records, or report contracts.
- Read [taxonomy-lifecycle.md](references/taxonomy-lifecycle.md) before comparing,
  activating, migrating, retaining, or deleting taxonomy snapshots.
- Read [source-catalog.md](references/source-catalog.md) before adding or
  changing a remote source.
- Read [security-and-privacy.md](references/security-and-privacy.md) before
  changing network access, logging, retention, configuration, or automation.
- Read [release-readiness.md](references/release-readiness.md) before initializing
  Git, selecting tracked files, preparing a public package, or publishing.
- Read [interest-inference-policy.md](references/interest-inference-policy.md)
  when deciding inference depth, using account evidence, or presenting
  cross-branch category proposals.
- Read [resource-and-scope-warnings.md](references/resource-and-scope-warnings.md)
  before activating descendants, enumerating a large taxonomy branch, raising
  on-demand safety limits, or asking the model to process a large result set.
- Read [first-dialogue-onboarding.md](references/first-dialogue-onboarding.md)
  before prompting a new user, recording a PhilPapers account decision, changing
  an authorization scope, or handling consent withdrawal.

If a required taxonomy, feed, or publication-date source is unavailable, keep
the affected record in an explicit unresolved state. Do not guess in order to
complete a report.
