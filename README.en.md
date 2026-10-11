# Philosophy Frontier Monitor

English | [简体中文](README.md)

**Track new philosophy papers in the research areas you confirm.**

Describe your interests in Chinese or English. The Skill proposes verified PhilPapers categories
for you to review one by one, then produces weekly reports within the scope you confirm. Reports
list titles, authors, date evidence, matching categories, source links, and records awaiting review.

Use `pfm search --config config/watchlist.yaml` to find **existing papers** in your confirmed
categories. You can filter by year, work type, or category and sort by citation counts when available.
Search reads the category feeds returned in that run and operates separately from weekly monitoring.
See the [search guide](references/paper-search.md) for its scope and handling of missing data.

![Four steps from a research interest to a weekly philosophy-paper report](assets/demo/philosophy-frontier-monitor-demo.png)

[Read the complete public sample report](examples/public-demo-weekly-report.md) ·
[Open the report-preview image](assets/demo/public-demo-weekly-report-2026-10-10.png) ·
[Read the full installation guide](references/installation.en.md)

### Public example: an actual pull from three real categories

**Individually reviewed on 10 October 2026.** This sample uses the seven-day window from
2 October 13:08 to 9 October 13:08 China Standard Time (exclusive end). Sources were read
again on 10 October, and conflicting work types were checked individually. The scope comprises
three exact PhilPapers categories: `Moral Responsibility` (4590), `Free Will` (347), and
`Action Theory` (5992). All three official RSS feeds were read successfully.

The rerun found **4 confirmed-new works** and **39 source-change candidates**, for 43 distinct
matching works; **10 further records require human review**. The complete report groups the matches
as **recently published** (4), **recently arrived** (19), and **recently changed** (20).
The four confirmed-new works have in-window publication-date evidence. The other 39 were
classified retrospectively using the records available at the rerun; the report explains
what those source observations establish about arrival.

#### Four confirmed-new works

Dates come from this run's OpenAlex bibliographic check. The original documents were also
inspected for the three records with conflicting work-type metadata.

1. **[The Philosophy of Significance How Importance Is Constituted, Experienced, and Transformed](https://doi.org/10.5281/zenodo.23142894)** — Morteza Niami; article; publication-date evidence: 4 October 2026; Action Theory, Free Will, Moral Responsibility. [Inspected PDF](https://zenodo.org/records/23142895/files/The_Philosophy_of_Significance.pdf?download=1).
2. **[Before Fatelessness: From Fatality and Abolition to Operational Reconfiguration](https://doi.org/10.5281/zenodo.23123292)** — Fujie Naoto; manuscript; publication-date evidence: 3 October 2026; Action Theory, Free Will. [Inspected DOCX](https://zenodo.org/records/23123293/files/fateless_fixed.docx?download=1).
3. **[Free Will and the Significance of Basic Undeservedness](https://doi.org/10.1007/s11406-026-01037-6)** — Gunnar Björnsson; article; publication-date evidence: 3 October 2026; Free Will, Moral Responsibility.
4. **[When the Past Cannot Be Narrated Experience, Trace, Memory, and the Emergence of Narrative](https://doi.org/10.5281/zenodo.23126385)** — Morteza Niami; article; publication-date evidence: 3 October 2026; Action Theory, Free Will. [Inspected PDF](https://zenodo.org/api/records/23126386/files/When%20the%20Past%20Cannot%20Be%20Narrated.pdf/content).

The image shows all four entries and the report groups. The
[complete report](examples/public-demo-weekly-report.md) includes Chinese and English records for
all 43 works, the review queue, and source notes. You can also compare the
[original 9 October snapshot](examples/public-demo-weekly-report-2026-10-09-original.md) and
[9 September snapshot](examples/public-demo-weekly-report-2026-09-09.md).

![Report preview listing all four confirmed-new works from three real PhilPapers categories](assets/demo/public-demo-weekly-report-2026-10-10.png)

<details>
<summary>Coverage and verification limits for this pull</summary>

Each official category RSS feed returned 500 entries. The program read all returned entries,
but exactly 500 does not establish that the source has no item cap or that the feed contains
every work from the seven-day period. PhilArchive OAI returned 148,183 source events in the
window, mainly record changes rather than new publications. This rerun used the OAI cache
without a new OAI network harvest. OpenAlex lookup failures and a rate limit left 21 candidates
awaiting a later automatic retry; 10 others require human review. None of these unresolved
candidates is counted among the 43 matches. The remaining work-type conflict could not be
decided from an accessible original document, so an abstract alone did not override it. This public
example did not enable CNKI or Wanfang or check Chinese journal sites live. The report gives
the full source status and evidence boundaries.

</details>

The CLI produces Markdown reports. A Codex environment with document or presentation tools can
also typeset a report as **Word (.docx)** or **PowerPoint (.pptx)**. Add
`--show-recently-changed` before conversion to include the expanded source-change group.

### What makes it different

- **You control the scope:** natural-language interests become real, reviewable categories before
  anything is monitored.
- **A record of each matching work:** category matches determine inclusion; reputation,
  citation counts, and model scores do not decide what to omit.
- **Distinct kinds of change:** publication, recent availability, source re-entry, and metadata
  updates appear with the source coverage status.
- **Private configuration stays local:** research interests, credentials, SQLite state, caches,
  and reports are excluded from the public repository by default.

### Start installation

You need Codex, Git, [`uv`](https://docs.astral.sh/uv/), and Python 3.12 or 3.13. In Codex, invoke:

```text
$skill-installer Install the Skill from the repository root at
https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor;
use path . and the install name philosophy-frontier-monitor.
```

Then, in a new conversation, ask:

```text
Use $philosophy-frontier-monitor to map my research interests to verified PhilPapers categories.
Let me confirm the categories, then guide me through local setup, the historical baseline, and my
first weekly report. Do not ask me to paste credentials into the conversation.
```

The latest tagged application release is **v0.4.0**; the current code uses internal evidence-pipeline
version **0.6.2**. Scheduled operation has been validated on Windows; macOS and Linux path support
is in place, with scheduled operation still awaiting real-machine validation.

## Technical update: landing-page prose and update archive (12 October 2026)

The Chinese and English landing pages now organize the project's purpose, public example,
features, version, and usage more directly while retaining the source scope and date evidence
needed to read the report. The latest update is visible; 17 earlier updates can be expanded below.
We checked the bilingual sample counts, four entries, version, local links, and collapsible
structure. All 17 historical entries remain intact, and the read-only release audit passed.
This documentation update has no new version tag or GitHub Release.

<details>
<summary>View earlier technical updates (17)</summary>

<details>
<summary>Technical update: full display of four confirmed-new works (10 October 2026)</summary>

## Technical update: full display of four confirmed-new works (10 October 2026)

The landing page now lists all four confirmed-new works with complete titles, authors, work
forms, publication-date evidence, matching categories, and stable links. It also links to the
original documents inspected for the three disputed work forms. The English and Chinese landing
pages and SVG/PNG preview now show the same four works; the preview no longer truncates the first
title or shows just one confirmed-new entry. The 43 matching works, 10 human-review records,
and retrospective-source caveat are unchanged. We checked the entries against both report
languages, rerendered and inspected the preview image, and did not fetch sources again or change
paper records. This is an unreleased branch presentation update; no tag or GitHub Release was made.

</details>

<details>
<summary>Technical update: individual work-type conflict review (10 October 2026)</summary>

## Technical update: individual work-type conflict review (10 October 2026)

Internal evidence-pipeline version `0.6.2` no longer lets a catch-all OpenAlex/Crossref `other`
alone veto a specific paper type from another source. Specific disagreements, such as paper
versus book, dataset, or conference abstract, produce a complete private review queue. It
records each source label, date, and evidence fingerprint. The skill now directs Codex to open
each conflicting work, prioritizing those with evidence inside the seven-day window, inspect
the document or publisher page, and write an evidence-linked private decision. A decision
applies only to the matching PhilPapers record, title, and source-evidence fingerprint; changed
evidence requires renewed review. An on-demand follow-up can repeat the exact initial window,
while weekly work uses a dry run before commitment. A paper-form decision still has to pass
the independent date and newness gates.

Both report languages list every unresolved item with source-specific types, and PhilArchive
OAI type URIs now display safely. A fixed-window rerun after document review raised the public
sample's confirmed-new count from 1 to 4 and reduced work-type conflicts from 15 to 1. The
final rerun has 43 matching works and 10 human-review records. The original 9 October snapshot
is retained separately. The reviewed report is explicitly a 10 October source reread, not an
exact historical feed reconstruction; its 39 source-change classifications are retrospective
projections. Inspected documents and decisions stay in a Git-ignored private directory;
no committed weekly state or private configuration was rewritten. Future
two-pass reviews save the first category-feed inventory in the private queue and reuse it with
`--review-queue-input`, avoiding a new RSS fetch with a backdated observation time. See the
[work-type policy](references/work-type-policy.md). All 447 tests, Ruff, skill validation,
public-sample consistency checks, and the read-only release audit passed.
This is an unreleased code and documentation update, with no new
tag or GitHub Release.

</details>

<details>
<summary>Technical update: direct official RSS and refreshed public report (9 October 2026)</summary>

<a id="technical-update-2026-10-09-official-rss-link"></a>

## Technical update: direct official RSS and refreshed public report (9 October 2026)

The local command-line HTTP 403 on category pages was identified as a Cloudflare challenge.
After the user completed ordinary browser verification, all three category pages generated
official RSS links through their own controls. The optional private-watchlist
`official_rss_url` validates the host, category identity, and unfiltered parameters before
reading these links under the existing evidence rules. An isolated seven-day `pull-now`
completed: the report contains 1 confirmed-new paper, 34 recent source arrivals, and
45 human-review records. Source coverage, unfinished verification, and upstream limits
remain in the report's optional technical notes. Formal weekly state was not advanced.
All 433 tests, Ruff, Skill validation, and the read-only release check passed. See the [complete public report](examples/public-demo-weekly-report.md)
and [source audit](references/source-catalog.md#2026-10-09-访问复核). This is a branch documentation
and sample update, not a new version tag or GitHub Release.

</details>

<details>
<summary>Technical update: public sample refresh status (2026-10-08)</summary>

<a id="technical-update-2026-10-08-public-demo-refresh"></a>

## Technical update: public sample refresh status (2026-10-08)

We retried `pull-now` with the original three public categories and a seven-day window in an isolated configuration. The PhilPapers category page returned HTTP 403, and an independent `pfm feed` check returned the same result. Neither request generated a new report or advanced formal weekly state. The landing page now dates the successful 9 September 2026 snapshot and discloses that this refresh was incomplete, so its historical totals cannot be mistaken for the current window. The original report, preview, and paper records remain unchanged. This is a branch documentation update; it does not create a new version tag or GitHub Release.

</details>

<details>
<summary>Technical update: v0.4.0 release (2026-10-08)</summary>

<a id="technical-update-2026-10-08-v040-release"></a>

## Technical update: v0.4.0 release (2026-10-08)

This release includes the
[accepted bounded Chinese-journal leads workflow](references/chinese-journal-stabilization.md),
shared publisher-date corroboration across the three modes, and corrections to
weekly source status and deduplication coverage in `v0.4.0`.
The application metadata and `uv.lock` agree; the internal evidence pipeline
stays at `0.6.0`. Chinese records remain separately labelled leads. Weekly and
on-demand reports list them only when a supported publication month overlaps
the relevant window.

Confirmed newly published Chinese papers, fully automated coverage of all 15
registered journals, complete database recall, and a fixed indexing delay are
outside this release scope. Opening a public `v0.3.0` schema-4 state database
with this schema-6 release first creates an integrity-checked backup; see
the [upgrade guide](references/installation.en.md#upgrade-checks-for-v040).

The release audit now excludes the `.git` pointer file used by linked worktrees.
All 422 tests, Ruff, skill validation, the read-only release audit, and locked
dependency installation passed. The required source-package files and private-path
exclusions were checked; the wheel installed in a fresh environment and its command
entrypoints worked. See the [release checks](references/v0.4-release-candidate.md).
This update corresponds to the `v0.4.0` tag and GitHub Release.

</details>

<details>
<summary>Technical update: weekly source status and deduplication coverage (2026-10-07)</summary>

<a id="technical-update-2026-10-07-weekly-coverage"></a>

## Technical update: weekly source status and deduplication coverage (2026-10-07)

Weekly reports now distinguish `local_only` reviewed publisher issues from source failures.
When checked network sources succeed and the local publisher-issue store has no newly reviewed
issue, the report no longer claims that a source check failed. CNKI and Wanfang coverage now
states how many eligible issue or article leads were listed previously, reconciling the candidate
total with missing months, outside-window leads, and the current report. Previously listed leads
remain deduplicated. Text corrections to an already successful report retain a private original
and do not rerun its window or advance checkpoints. All 420 tests, Ruff, skill validation, and
the read-only release audit passed. This is a local code and documentation update, without a push,
version tag, or GitHub Release.

</details>

<details>
<summary>Technical update: shared Chinese-journal dates and weekly section order (2026-09-28)</summary>

<a id="technical-update-2026-09-28-weekly-months"></a>

## Technical update: shared Chinese-journal dates and weekly section order (2026-09-28)

Both complete language versions of weekly reports now list Chinese publisher, CNKI, and Wanfang
leads before PhilPapers papers. The first CNKI baseline also applies the publication-month gate:
only month-supported issues overlapping the weekly window display article titles. Earlier issues,
missing months, and conflicting dates remain in coverage counts. Issue numbers, query dates, and
announcement dates cannot supply a publication month. Announcement-only publisher issues are
withheld from weekly reports; explicit issue labels and an earlier release day remain separate facts.

When CNKI lacks dates, `scripts/prepare_weekly_cnki.py --config config/watchlist.yaml` produces a
bounded, read-only candidate list for review against the publisher's same-article or same-issue page.
Weekly preparation remains the default; `--mode pull-now --days N` and
`--mode search --year-from YYYY --year-to YYYY` align preparation with the other two modes.
All three modes share the evidence logic and consistent issue/article fields. Search JSON and Markdown
show reviewed dates, publisher links, and missing/conflicting months. Historical candidates still
follow the requested year scope; they are not automatically described as recent papers.
The private reviewed-bibliography file accepts optional `issue_label_month` and `publication_date`
fields. Only fully matching publisher bibliographies can supplement dates; catalog records cannot.
Month filtering precedes article-level Wanfang checks. A previously undated observation can become
eligible after corroboration, and an older catch-up window cannot consume a future issue early.
Previously eligible issues remain deduplicated. See the
[Chinese-source policy](references/chinese-source-expansion.md#43-月份精度与新期次观察).

All 419 tests and Ruff passed, covering first-scan filtering, earlier and undated exclusions,
bibliographic conflicts, invalid dates, early publication, later corroboration, future-window
eligibility, both language orders, shared evidence across search and immediate results, year scopes,
and read-only weekly state. The program consumes manually verified dates; the preparation
script does not automatically interpret publisher dates, confirm first publication within the week,
or establish interest-category membership. This is a local code and documentation update, without
a push, version tag, or GitHub Release.

</details>

<details>
<summary>Technical update: ninth publisher TOC and stricter arrival evidence (2026-09-28)</summary>

<a id="technical-update-2026-09-28-journal-p2"></a>

## Technical update: ninth publisher TOC and stricter arrival evidence (2026-09-28)

Stability decision: the [Chinese-journal acceptance criteria](references/chinese-journal-stabilization.md) are met for the **bounded Chinese leads service**. This `main` branch update treats it as a stable opt-in capability: `search` returns sourced Chinese metadata candidates, `pull-now` displays only leads with an explicit issue month overlapping its recent window, and `weekly-run` baselines and deduplicates reviewed new issues while disclosing source failures. Automatically collected publisher TOCs still need human evidence review before entering those modes; candidates do not become confirmed newly published papers. The public example leaves Chinese sources off. This contract does not promise comprehensive coverage, a fixed database arrival time, or an interest-paper recall rate. This code push does not create a version tag or GitHub Release.

Bounded collection now includes the [*Confucius Studies* publisher category](https://www.chinakongzi.org/category/kongziyanjiu_qikan/). It reads only the index and the latest [official issue page](https://www.chinakongzi.org/content/6147_589032.html), checking the issue number, 15 author-title entries, and corresponding article headings later on that page. The page's 2026-08-13 date is an announcement day retained as pending evidence. It does not explicitly label the issue month, so issue 4 cannot be converted into an August issue or evidence that its papers were first published in August. Nine of the fifteen registered journals now have independently scannable TOCs (eight textual, one PDF); six still require site-specific manual checks.

Same-article arrival summaries now use a `miss` as the last complete non-hit only when its query scope was saved and marked complete. Older misses without that scope and truncated queries cannot create a falsely precise arrival interval; their original observation rows remain intact. The issue-level `audit` adds `complete_misses` to separate complete non-hits from raw status counts. The [article observation protocol](references/publisher-article-observation.md) explains this accounting and the remaining source limits.

The P2 issue audit now has separate denominators for complete publisher TOC records, manually confirmed research articles, and manually confirmed interest-relevant articles. The narrower denominator stays unknown while any type or interest decision is unreviewed or uncertain. A source-specific research visibility denominator requires a scoped, complete query for every confirmed research article in that issue. `journal-watch catalog` now marks each TOC as `automatic` or `manual`. A [site-by-site review of the six remaining journals](references/publisher-article-observation.md#2026-09-28-六站入口复核与自动采集决定) records their official evidence and re-entry conditions. *Social Sciences in China* has issue pages and an issue 8 official WeChat TOC pointer, but direct access and source-use conditions do not yet support automatic collection here; *Ethics Studies* returns 502, *Religious Studies* has image TOCs, and the other three registered entries lack current complete publisher TOCs. They remain available for manual review; absence from automated collection is not evidence of no new issue. The [source-use notice](THIRD_PARTY_NOTICES.md#中文期刊发布渠道及补充检索) now states access, cadence, retained fields, and public-display limits; Wanfang remains off by default.

On 28 September 2026, a new Git-ignored isolated database scanned one issue from each of nine journals: 132 TOC records, no site failures, and no article-database queries in the default cycle. The first bounded check matched one *Confucius Studies* article on the public CNKI surface. An explicit Wanfang Query check in a separate isolated copy compared a second article: CNKI matched and Wanfang did not, each after a complete one-page query. The publisher's space inside that author's Chinese name had caused a false review status; it is now parsed correctly and covered by a regression test. Only two articles from this issue were checked; the other 13 and all articles from the other journals remain unchecked in this round. Wanfang stays off by default. This sample cannot establish database-wide recall, database-wide omission, or a fixed indexing time. All 402 tests, Ruff, Skill validation, and the read-only release audit passed. This is a `main` branch code and documentation update, without a new tag or GitHub Release.

</details>

<details>
<summary>Technical update: issue-level denominators and an eighth publisher directory (2026-09-27)</summary>

<a id="technical-update-2026-09-27-journal-p2"></a>

## Technical update: issue-level denominators and an eighth publisher directory (2026-09-27)

Building on the accepted P0/P1 Chinese-leads workflow, `pfm journal-watch audit` now gives an issue-level reference denominator only when a complete publisher TOC scan passed the site's page and entry checks and its saved title set still matches that scan. Older and truncated records keep an unknown denominator. The article view records each latest check on public CNKI search or the optional Wanfang Query, including its actual query scope and pages fetched. `classify` lets a reviewer use the registered publisher link to assess work type and local research relevance separately. Unreviewed entries are not counted as research papers or interest matches. This local audit does not promote entries into the verified-paper sections of search, pull-now, or weekly reports.

An eighth publisher source joins the seven textual TOCs: bounded parsing of the [*Modern Philosophy* directory at Sun Yat-sen University](https://mphilosophy.sysu.edu.cn/cat/124). Its [2026 issue 2 announcement](https://mphilosophy.sysu.edu.cn/article/25746) is dated 29 June, while the attached Chinese TOC PDF explicitly calls it the March issue. The system keeps the issue month and announcement day as two distinct pending evidence links; it does not treat the announcement as first publication of each article. PDF reads are limited to the registered HTTPS host, size and page bounds, content type, file header, and an issue-entry count check. Parsing is in memory; the PDF itself is not stored. Seven other registered publishers still need site-specific manual review.

In a Git-ignored isolated database on 27 September 2026, one complete issue from each of eight journals yielded 117 publisher TOC records. Directory-only scans made no database queries. Eight separate bounded title searches, one per journal, all matched on the public CNKI search surface. The other 109 entries were not checked in that round; Wanfang remained off, and no article-level work-type or interest review was completed. This is not a database-wide recall or arrival-time estimate. All 398 tests, Ruff, Skill validation, and the read-only release audit passed. See the [article observation protocol](references/publisher-article-observation.md) and [stabilization criteria](references/chinese-journal-stabilization.md) for commands, evidence, and use boundaries. This local branch update has not been pushed, tagged, or released.

</details>

<details>
<summary>Technical update: publisher TOCs and article arrival observations (2026-09-27)</summary>

<a id="technical-update-2026-09-27-official-journals"></a>

## Technical update: publisher TOCs and article arrival observations (2026-09-27)

The P0/P1 work in the [Chinese-journal stabilization criteria](references/chinese-journal-stabilization.md) is now accepted for the bounded leads workflow. Search labels a failed read of the local publisher evidence store and continues. Automatically collected issues appear in `pfm journal-watch pending`; after checking the exact publisher page, `review --confirm-evidence` promotes the observation. Even a parsed announcement date remains pending until source review. First observation and first review times remain separate, later scans cannot revoke a review, and multiple official links for one issue produce one user-facing lead, reported in the week of its earliest review. Isolated three-mode tests cover enablement, disabled sources, rolling-month selection, weekly baseline, and deduplication. Failure tests cover rate limits, non-HTML responses, redirects, truncated pagination, and site isolation. Fresh isolated database trials found 12 entries in issue 8 of the *Philosophical Research* portal (still pending) and 11 in issue 4 of the [*Zhouyi Studies* publisher directory](https://zhouyi.sdu.edu.cn/info/1033/2558.htm). After checking that the page explicitly states an announcement date of 2026-08-21, only that isolated issue was reviewed. The date does not establish first publication of any article. Chinese metadata candidates remain separate from matched papers; article-level verification and full Chinese-journal coverage remain unfinished. This is an unpushed local branch update, without a new tag or release.

Final checks passed: 391 tests, Ruff, skill validation, and the read-only release audit. The live source trial covered only the two sites above; it does not establish sustained access across all seven collectors or recall of all Chinese philosophy papers.

The [Chinese-source policy](references/chinese-source-expansion.md) now corrects an older statement that the *Philosophical Trends* portal stopped at issue 7 and publisher TOCs were still entirely manual. The seven-site collection saw issue 8; the user-supplied issue 9 WeChat post remains unread and pending. This correction does not promote collected TOCs to reviewed papers.

The new [Chinese-journal stabilization criteria](references/chinese-journal-stabilization.md) separate a bounded Chinese-leads service that can be accepted earlier from a verified-new-paper service that still needs article-level evidence. They require fresh-install acceptance of all three modes, a publisher-evidence review path, honest source-failure handling, and scoped coverage samples. The eight-week same-article study supplies source-arrival evidence; it is not the sole stability gate. This remains an unpushed branch update.

Bounded site-specific collectors now read complete textual tables of contents for seven journals: *Journal of Dialectics of Nature*, *Zhouyi Studies*, *Philosophical Research*, *Philosophical Trends*, *Philosophical Analysis*, *Studies in Dialectics of Nature*, and *Studies in Philosophy of Science and Technology*. `pfm journal-watch run` scans all seven sequentially and only scans publisher directories by default. An explicit positive article-check budget checks the public CNKI Space search surface; the subscribed Wanfang Query endpoint additionally requires `--with-wanfang`. Total and per-site article checks are bounded, and one site failure does not stop the others. Observations remain in a Git-ignored local database. Publisher scans provide leads about new issues; repeat checks of the same article are optional source evaluation. Other users can use search, pull-now, or weekly reports without establishing an observation baseline. A complete search miss followed by an identified hit bounds search visibility; an initial hit and continuing misses are censored observations and do not prove database-wide absence.

The seven latest issues yielded 14, 11, 12, 12, 16, 17, and 18 author-bearing TOC entries, 100 in total. The first round checked five entries from each of the original three journals and one from each of the four added journals: all 19 were visible through public CNKI search, while the earlier explicitly enabled exact-title Wanfang queries matched two and did not match 17. These are bounded observations, not a database-wide miss rate. Repeat checks currently have an initial review point at roughly eight weeks; any Wanfang delay study needs a separate explicit opt-in and scope. The *Philosophical Trends* editorial portal still shows issue 8; the user-supplied WeChat link for issue 9 remains a pending lead because its body could not be read. Eight other catalog entries retain site-specific manual review. All 379 tests, Ruff, and the read-only release check passed; no multi-day arrival interval has yet been measured. See the [observation protocol](references/publisher-article-observation.md). This change was not pushed and did not create a version tag or GitHub Release.

</details>

<details>
<summary>Technical update: publisher-first Chinese journal issue registry (2026-09-27)</summary>

## Technical update: publisher-first Chinese journal issue registry (2026-09-27)

Issue discovery and date evidence for monitored Chinese journals now start with
their editors' or publishers' websites, editorial portals, and verified official
WeChat announcements. CNKI Space and Wanfang provide supplementary discovery and
bibliographic crosschecks. A [catalog of 15 journals](config/official-journals.yaml)
and a private Git-ignored SQLite issue store have been added. `pfm journal-watch catalog`
lists channels; `pfm journal-watch list` shows local observations. Manual entries
retain an official evidence URL and separate issue label month, issue publication
date, announcement date, and first observation. Only reviewed entries appear in
search, on-demand, and weekly issue leads. A reviewed official month may fill a
missing CNKI month for the same journal, year, and issue; conflicting source
months are not overwritten.
Weekly reports mention an issue only in its first review week, avoiding repeated
weekly notices for the same monthly issue.

The journal portal calls *Zhexue Dongtai* monthly, but the supplied
[issue 9 WeChat post](https://mp.weixin.qq.com/s/eeYI8s6Y_f9J3zbJ73XgSg) could not be read
in this environment. It remains a pending lead, without an inferred post date
or September issue label. The catalog records the publisher's 2026 schedules
for *Zhexue Yanjiu* (the 25th of each month) and *Shijie Zhexue* (the 2nd of
odd-numbered months), but a schedule
does not establish an actual issue date. The registry still requires manual
review; it does not yet harvest article tables of contents across journals or
verify individual interest matches. Issue leads do not count as confirmed new
papers. Local verification covered the 15-journal catalog, private-store write and read,
368 tests, Ruff, skill validation, and the release audit. Continuous automated
journal checks were not run. This is a
local branch update, with no push, version tag, or GitHub Release. See the
[source and entry policy](references/chinese-source-expansion.md#24-官方期刊监控库与证据录入).

</details>

<details>
<summary>Technical update: journal issue-month audit baseline (2026-09-27)</summary>

<a id="technical-update-2026-09-27-audit"></a>

## Technical update: journal issue-month audit baseline (2026-09-27)

A private, Git-ignored sample audit now compares journal-hosted issue evidence with
bounded CNKI Space and subscribed Wanfang Query searches, preserving the observation
time. CNKI Space returned all eight sampled articles but none with an explicit month;
Wanfang returned three exact bibliographic matches and one title-suffix variant, while
four had no same-work match in the bounded queries. Two articles from a journal issue
explicitly labelled September would still fail the current CNKI month gate even if
the private discovery terms found them. This audit did not test recall of those terms.
A search miss does
not establish absence from the entire database, and a single snapshot cannot measure
actual indexing delay. A journal's issue date and Wanfang's abstract-online date are
also distinct from an article's first-publication date.

The [source evidence and trial decision](references/chinese-source-expansion.md#23-重点期刊原站目录的月份证据与抽样基线)
document the original journal links, access limits, and date semantics. Broad automated
journal-site intake remains deferred. A small trial using a stable issue page with an
explicit month is the next candidate, followed by repeated observations before expansion.
This branch-only audit and documentation update has no new tag, GitHub Release, or push.

</details>

<details>
<summary>Technical update: independent Wanfang discovery with a personal subscription (2026-09-27)</summary>

<a id="technical-update-2026-09-23"></a>

## Technical update: independent Wanfang discovery with a personal subscription (2026-09-27)

An optional Wanfang discovery path now queries the subscribed AI HUB Query endpoint for
Chinese journal metadata directly, without requiring a matching CNKI Space hit. It uses
private Chinese terms and explicit term, year, and page limits. Historical search lists
these as separate leads; on-demand pulls show only leads whose source-labelled publication
month overlaps the rolling window; the first weekly scan establishes a baseline for eligible
record IDs, and later reports show IDs not previously listed in that window. A later article in an
already observed issue can therefore appear as a new observation. Set
`sources.wanfang.discover: true` in the private watchlist to opt in. The default is two
terms and one page per term and year; calls can consume personal trial quota. See the
[Chinese-source expansion note](references/chinese-source-expansion.md).

A bounded live trial on 2026-09-27 confirmed year-limited search and publication-date sorting
with the existing subscription. It also found issue 4 labelled January 1 and a publication
label later than the query date. Wanfang's `PublishDate` is therefore kept as source metadata,
not treated as an article's first-publication day; January 1 is rejected as reliable month
evidence. Chinese leads remain separate from verified PhilPapers papers, and blocked or
truncated searches are disclosed. Crossref, DOAJ, and journal-hosted contents can supplement
coverage, but none currently demonstrates complete coverage of Chinese philosophy journals
without a database-wide agreement. New regressions cover independent discovery, placeholder
dates, the on-demand window, and weekly record-ID increments. This branch update has no new
tag or GitHub Release.

</details>

<details>
<summary>Technical update: CNKI Space leads and Chinese journal issue observations (2026-09-23)</summary>

## Technical update: CNKI Space leads and Chinese journal issue observations (2026-09-23)

This branch update keeps the long-term target as the **union of global PhilPapers candidates
and supplementary Chinese database candidates**, without filtering existing PhilPapers results
by author affiliation. A private watchlist can now enable `sources.cnki_space` and specify
user-approved Chinese terms and a page limit. The adapter follows the public search method
documented by the third-party [cnki-search MCP](https://github.com/Biogod2020/cnki-search),
using an ordinary HTTP client rather than running its server or impersonating a browser.
See the [Chinese-source expansion note](references/chinese-source-expansion.md) for configuration
and evidence rules.

All three modes can now display Chinese-source leads. `search` lists unverified Chinese
metadata separately from confirmed papers and excludes it from verified totals and citation
sorting. `pull-now` reads only issue leads with an explicit source-labelled month overlapping
its rolling window, without changing weekly state. The first weekly scan
establishes an issue baseline; later reports list only issue keys not previously observed,
committing those observations with the weekly run. Reports preserve source year, issue number,
and first-observation time. Issue 9 is not automatically a September issue, and a day shown
in search metadata is not automatically a paper's first publication date. Blocking, changed
HTML, and page-limit truncation appear as coverage limits rather than zero results.

A minimal redistributable HTML fixture now tests parsing, error pages, content types, page
interruption, and transactional state. A bounded public search was also run using terms held
only in the private watchlist. Systematic journal-site, Wanfang, and VIP record checks, publication-time
author affiliations, and cross-source paper identity still require article-level review beyond
the manually checked candidates described below. CNKI Space is not
the full KNS collection, and bounded pages cannot establish complete Chinese or worldwide
coverage. No new version tag or GitHub Release was created.

A Git-ignored private evidence file now records article-level manual checks against journal
or catalog pages. It marks title, authors, journal, year, and issue corroborated only when
the fields agree, and notes China-based author affiliation only when the article page
supports it. Two private candidates have been checked against a journal publication portal page
and a separate catalog page; only the journal article page supports the affiliation note.
The publication portal is hosted by CNKI, so this does not constitute an independent
Wanfang or VIP check.
Search and reports can also flag strict bibliographic overlap with PhilPapers records
resolved in the current run, without merging records or changing verified paper totals.
Synthetic tests cover agreement, conflicts, missing fields, and overlap. The National
Center for Philosophy and Social Sciences Documentation restricts unlicensed automated
crawling in its user agreement; broad Wanfang coverage and VIP access remain pending.
The [Chinese-source expansion note](references/chinese-source-expansion.md) describes the
private file and its evidence limits.

An optional Wanfang AI HUB metadata cross-check now uses the subscribed Query
endpoint for a bounded set of CNKI leads. When enabled, it reads the AppKey from
`WFDATA_APP_KEY` or a configured Git-ignored local file and compares Chinese journal title, authors,
journal, year, and issue. Search, on-demand pulls, and weekly reports distinguish
agreement, missing fields, conflict, and no corroboration in the checked scope.
`OriginalOrganization` is retained as a publication-time affiliation lead, but
the program does not automatically assign a China-based affiliation to an author.
The absence of a same-title hit does not prove that Wanfang lacks the article. Synthetic JSON tests cover
the request, response variants, errors, and pagination limits. A bounded live
trial using the subscribed API and CNKI leads confirmed authentication and the
actual response shape: `numFound` can be a decimal string, journal metadata can
appear in `Periodical`, and `PublishYear` can be zero while `PublishDate` has a
year. The parser now handles these cases. Private search results and the AppKey
remain outside public files; trial quota, long-term stability, and full coverage
remain unverified.

This branch update also narrows the Chinese section of an on-demand pull as requested:
it no longer lists every issue found in the current year. A lead appears only
when the source explicitly labels an issue month overlapping the selected rolling
window. Year-only and issue-number-only leads, and months outside the window,
contribute to coverage counts without listing their article titles. On-demand
Wanfang checks are limited to the displayed leads. Month precision cannot prove
an article's first publication day, and an empty Chinese section does not establish
that no recent Chinese paper exists; the researcher may request a broader search.
Historical `search` and formal weekly monitoring retain their existing scope.
Synthetic tests cover overlapping months, missing months, and out-of-window issues.
No new version tag or GitHub Release was created.

</details>

<details>
<summary>Technical update: human-review notice in the public example (2026-09-20)</summary>

<a id="technical-update-2026-09-20"></a>

## Technical update: human-review notice in the public example (2026-09-20)

The public example's coverage note now states only that 18 records require human review. Both
README pages and the preview image reflect this change. Review details and technical notes in the
full example are collapsed by default and can be expanded voluntarily; the original paper records
and verification facts are preserved. This documentation and presentation update includes checks
of paper content, collapsible sections, and links, plus a rendered-image review.

</details>

<details>
<summary>Technical update: interest-based paper search and local-time scheduling (2026-09-19)</summary>

<a id="technical-update-2026-09-19"></a>

## Technical update: interest-based paper search and local-time scheduling (2026-09-19)

This `main` branch update adds the independent `pfm search` command. It searches existing papers
using confirmed interests and PhilPapers feed membership, with any/all tag matching and optional
year and paper-type filters. Search requires no monitoring baseline and does not change subscriptions
or weekly notification history. The feature was pushed in
[commit `9249da6`](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/commit/9249da600c79a63c2f55a973bcd98c60cd2361dd);
no separate version tag or GitHub Release has been created for it.

**Without year bounds**, missing feed dates, year hints, or publication dates do not exclude a
verified paper, and all verified matches are returned by default. Explicit result limits still
enable pagination. **With year bounds**, records without feed year hints are excluded before
bibliographic lookup; the final year filter uses bibliographic publication-date evidence.

Available OpenAlex citation counts sort descending, with missing values labelled and placed last;
zero remains distinct from missing. Books, chapters, book reviews, and other non-paper forms are
excluded. Reports mention unresolved paper types or bibliographic identities without counts and
may provide existing source links for optional user review. The skill does not launch follow-up
investigations for these records. Conflicting DOI evidence remains unresolved even when an external
lookup returns just one match.

For scheduling, the skill reads the user's timezone from the session or, when absent, the local OS.
An unqualified time such as “8 a.m.” is interpreted locally. The weekday, time, and timezone are
confirmed together at final task creation; existing subscriptions retain their confirmed timezone.

The feature commit passed **319 tests**, lint and formatting checks, skill validation, and the
release audit. Git history bundles are protected as private files. Search coverage remains limited
to papers available and verified from the selected category feeds, not the complete PhilPapers
index. See the [search contract](references/paper-search.md) and [changelog](CHANGELOG.md).

Future project updates must also appear in the technical updates on both README pages, while
preserving earlier entries. This requirement is recorded in the
[contribution rules](CONTRIBUTING.md#网页技术更新与变更记录).

</details>

<details>
<summary>Technical update: recoverable OAI caching and stable on-demand pulls</summary>

## Technical update: recoverable OAI caching and stable on-demand pulls

Evidence pipeline `0.6.0` adds a recoverable cross-run SQLite cache for PhilArchive OAI harvesting.
`pull-now`, `weekly-run`, and `catch-up` reuse completely harvested windows and request only uncovered
intervals. `--no-oai-cache` temporarily disables this behavior. The private, Git-ignored
`var/oai-cache.sqlite3` remains separate from weekly notifications, baselines, and checkpoints.

The cache stores only record identifiers, source `datestamp` values, deletion markers, the
`dc:identifier`, `dc:date`, and `dc:type` fields used by the program, and opaque pagination state
needed for interruption recovery. It does not store titles, authors, abstracts, full text, or full
OAI responses. A cache hit still reruns exact-window filtering, work-type handling, and the
confirmed-category intersection.

Each successfully parsed OAI page and its successor `resumptionToken` are committed in one SQLite
transaction. An interrupted run resumes from the latest successful page. Coverage is registered
only after the complete sequence is atomically promoted into the formal cache. An expired token or
an upstream `badResumptionToken` response restarts only that original gap.

`pull-now` is stable as of `v0.3.0`, but it remains an explicit, read-only, non-scheduled operation.
It uses the same evidence rules as weekly monitoring and never advances weekly notification
history. Stability means bounded requests, recoverable interruption, state isolation, and honest
coverage reporting; it does not promise a fixed runtime.

</details>

</details>

## 1. What problem does this solve?

Philosophy researchers revisit PhilPapers, journal pages, and bibliographic databases to find new
work in their fields. Philosophy Frontier Monitor turns a research interest into user-confirmed
PhilPapers categories, checks the relevant sources over time, and presents works with their date
and category evidence in a weekly report. Researchers can then choose what to read more closely.

## 2. Features

- **Set the scope:** propose PhilPapers categories from interests expressed in Chinese or English,
  then activate them after individual confirmation.
- **Establish a baseline:** register existing records before weekly monitoring in the user's time
  zone, with chronological catch-up for missed windows.
- **Identify changes:** distinguish new publication, recent manuscript or preprint availability,
  source re-entry, and metadata updates.
- **Build reports:** merge sources for the same work and show bibliography, date evidence,
  matching categories, and source status.
- **Pull or search on demand:** inspect the latest 1–31 local days or search existing papers in
  confirmed categories.
- **Include Chinese-journal leads:** optionally show bounded Chinese metadata and reviewed issue leads.

The main paper section of a weekly report requires both newness evidence and an intersection with
confirmed interest categories. Work-type and date conflicts enter individual review. PhilPapers
category feeds establish category membership, PhilArchive OAI supplies source-record changes, and
Crossref/OpenAlex support bibliographic, work-type, and date checks. Reports distinguish an OAI
record `datestamp` from a work's publication date. See the [monitoring policy](references/monitoring-policy.md)
and [work-type policy](references/work-type-policy.md) for the full rules.

`pull-now` applies the same evidence rules to a requested window and keeps its report separate
from weekly notification state. A private OAI cache stores harvested windows and pagination
checkpoints for reuse and recovery. Upstream bulk updates can make an initial cache large; the
run shows its progress. See the [installation guide](references/installation.en.md) for cache
status and cleanup commands.

## 3. Installation and first use

Requirements are Codex, Git, [`uv`](https://docs.astral.sh/uv/), Python 3.12 or 3.13, and network
access to the configured scholarly sources. Production unattended validation has been performed on
Windows. macOS and Linux path semantics are supported, but `v0.3.0` does not claim equivalent
unattended scheduling validation on those platforms.

Ask Codex's built-in installer to install the repository root as the Skill:

```text
$skill-installer Install the Skill from
https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor.
Use repository path . and installation name philosophy-frontier-monitor.
```

In the installed directory:

```powershell
uv sync --python 3.12
Copy-Item .\config\watchlist.example.yaml .\config\watchlist.yaml
.\.venv\Scripts\pfm.exe doctor
```

The executable is `.venv/bin/pfm` on macOS and Linux. Then:

1. Describe the research direction in ordinary language.
2. Review the proposed verified categories and accept, reject, or defer each one.
3. Obtain the PhilPapers API ID and API key needed for the full taxonomy, keeping both only in a
   private Git-ignored local location or process environment.
4. Establish the historical baseline.
5. Complete a dry run and a production weekly run before creating a recurring schedule.

The default schedule is Monday at 08:00 in the user's local timezone and can be changed. Never paste
API keys, passwords, cookies, or recovery codes into a conversation, issue, screenshot, or command
line argument.

See [Installation and first deployment](references/installation.en.md) and the
[English user guide](references/usage.en.md). The Chinese installation guide is
[安装与首次部署](references/installation.md).

## 4. Current version

The latest tagged application release is [`v0.4.0`](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/releases/tag/v0.4.0).
The current code also includes work-type review improvements beyond that release; its internal
evidence-pipeline version is `0.6.2`.

| Capability | Current status |
|---|---|
| Interest mapping, category confirmation, baseline, and weekly monitoring | Stable |
| On-demand pulls, existing-paper search, and bilingual reports | Stable |
| Cross-run OAI caching, pagination recovery, and incremental refresh | Stable |
| Bounded Chinese metadata and reviewed issue leads | Optional since `v0.4.0` |

Chinese leads appear separately with their source and month evidence. Publisher directories for
nine journals can be scanned separately; the other registered journals still require manual
review. See [Chinese sources](references/chinese-source-expansion.md) for scope and entry rules.

The OAI candidate approach was inspired by `list_recent` in
[`sea9401/philosophy-mcp`](https://github.com/sea9401/philosophy-mcp). This project adds complete
pagination, window filtering, category intersection, separate date semantics, and cross-run
recovery. See [design lineage](references/design-lineage.md) for pinned upstream revisions,
licensing, and the implementation comparison. Changes appear in [CHANGELOG.md](CHANGELOG.md),
and contribution requirements in [CONTRIBUTING.md](CONTRIBUTING.md).

## 5. Security and source boundaries

Private research interests, confirmed categories, credentials, SQLite state, caches, and reports
stay local by default. Git ignores `config/watchlist.yaml`, `var/`, `reports/`, and `.env`. API keys
are read from private files or the process environment. The OAI cache stores minimal record evidence
and a pagination token for recovery; status output shows counts and coverage.

Reports identify the categories and sources actually read, along with date precision and records
awaiting review. The project reads bibliography and metadata by default; researchers decide
separately whether to obtain full texts. See the [security policy](references/security-and-privacy.md),
[source catalog](references/source-catalog.md), [date and version semantics](references/date-and-version-semantics.md),
and [third-party notices](THIRD_PARTY_NOTICES.md).

## 6. Feedback

- Maintainer: **Asahina Mafuyu**
- Contact: [junxuanxie@stu.xjtu.edu.cn](mailto:junxuanxie@stu.xjtu.edu.cn)

Open a [GitHub Issue](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/issues)
for installation questions, reproducible bugs, or suggestions. Include the version, operating
system, command, window length, and a redacted error summary. For on-demand pulls, count and
coverage fields from `pfm oai-cache status` also help. Keep credentials, private configuration,
databases, and complete reports local. Report vulnerabilities involving credentials or private
data through GitHub Private Vulnerability Reporting as described in [SECURITY.md](SECURITY.md).
