# Installation and first deployment

[简体中文](installation.md) | English

## 1. Supported environment

`philosophy-frontier-monitor` is a Codex Skill with a local Python application. Installing the Skill
makes its files discoverable to Codex; collecting papers also requires a Python environment and a
private configuration in the same installation root.

Requirements:

- Codex desktop, Codex CLI, or an IDE integration;
- Git;
- `uv`;
- Python `>=3.12,<3.14`; and
- network access to the selected scholarly sources.

Unattended production scheduling has been validated on Windows. Paths and file semantics support
macOS and Linux, but `v0.3.0` does not claim equivalent unattended-run validation there.

## 2. Install with Skill Installer

The repository root is the Skill directory, so the installer must use path `.` and the installation
name `philosophy-frontier-monitor`:

```text
$skill-installer Install the Skill from
https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor.
Use repository path . and installation name philosophy-frontier-monitor.
```

Equivalent installer arguments are:

```text
--repo AsahinaMafuyu0127/philosophy-frontier-monitor
--path .
--name philosophy-frontier-monitor
```

The installer normally stops if the destination already exists, preventing silent replacement of
private configuration. Use the Skill in the next conversation; restart Codex if it does not appear.

## 3. Create the Python environment

Run this from the installed directory containing `SKILL.md`:

```powershell
uv sync --python 3.12
```

This creates the project-local `.venv` from `pyproject.toml` and `uv.lock`.

```text
Windows:      .venv\Scripts\pfm.exe
macOS/Linux:  .venv/bin/pfm
```

If `.venv` is absent, synchronize dependencies and run `doctor` before attempting a live weekly or
on-demand run.

## 4. Create the private watchlist

Windows:

```powershell
Copy-Item .\config\watchlist.example.yaml .\config\watchlist.yaml
```

macOS or Linux:

```bash
cp ./config/watchlist.example.yaml ./config/watchlist.yaml
```

`config/watchlist.yaml`, `var/`, and `reports/` are Git-ignored. Do not copy a private profile back
into the public example or upload the entire runtime directory when requesting support.

### Keep large caches off a constrained Windows system drive

OAI and bibliographic caches are created beside `storage.state_database`. A bulk upstream metadata
update can make the first OAI cache substantially larger than an ordinary incremental run. On
Windows, if another spacious local drive is available, avoid placing the private runtime directory
on a space-constrained `C:` system drive. For example:

```yaml
storage:
  state_database: "F:/philosophy-frontier-monitor-private/state.sqlite3"
  report_directory: "F:/philosophy-frontier-monitor-private/reports"
```

Use an explicit path owned by the user and keep it private. The Skill and CLI provide this advice
before `pull-now`; they never move existing configuration, databases, caches, or reports without
authorization.

Check current cache size and interruption state without revealing its opaque token:

```powershell
.\.venv\Scripts\pfm.exe oai-cache status --config config\watchlist.yaml
```

Old, rebuildable OAI cache material can be removed only with an explicit timezone-aware cutoff and
confirmation:

```powershell
.\.venv\Scripts\pfm.exe oai-cache prune `
  --config config\watchlist.yaml `
  --before 2026-09-01T00:00:00Z `
  --confirm
```

Pruning is refused while a harvest is active and never changes the weekly baseline, checkpoints,
notifications, or `state.sqlite3`.

## 5. Taxonomy and credentials

A production profile requires a verified full PhilPapers taxonomy. The Skill guides the user in
obtaining the PhilPapers API ID and API key. Store credentials only under the Git-ignored
`var/secrets/` directory or inject them into the current process environment. Never paste them into
a conversation, issue, screenshot, README, command-line argument, or commit.

If a PhilPapers command-line request is blocked by a Cloudflare challenge, the user may save the
same official JSON response in a normal private browser session and validate it with
`taxonomy-import`. Do not impersonate a browser, reuse cookies, disable TLS, or bypass access
controls.

An OpenAlex key is not required to create an interest profile, although broad bibliographic
verification may consume more credits. Keep an optional key in the same private locations.

## 6. First-run order

Complete the initial deployment in this order:

```text
Describe the research direction
  -> verify and confirm PhilPapers categories
  -> import the full taxonomy
  -> establish the historical baseline
  -> run a dry run
  -> run one production weekly window
  -> verify the report and checkpoint
  -> create the recurring Codex schedule
```

The baseline does not notify historical papers already present in current feeds. A production
weekly checkpoint advances only after the report file and the full SQLite transaction succeed.
Complete failure-recovery testing before scheduling. The default is Monday at 08:00 in the user's
local timezone; synchronize any changed timezone, weekday, or time between the watchlist and the
scheduled task.

Every weekly and on-demand Markdown report contains a complete Chinese section and a complete
English section generated from the same evidence and counts.

## 7. Run from another working directory

When invoked, the Skill resolves the directory containing its own `SKILL.md` as the runtime root.
Commands, private configuration, and report paths are resolved from that root. If the user keeps a
separate data directory, pass the configuration path explicitly; do not search other projects for
private configuration.

## 8. Upgrade to v0.3.0

`v0.3.0` does not provide an overwrite-in-place automatic update. Review the release, back up the
private directory and databases, then update the known installation path and verify its schemas.
Do not delete the entire Skill directory to update it.

Upgrading from `v0.2.0` does not require rebuilding `state.sqlite3`, the historical baseline, or
notification history. The existing OAI cache gains interruption-session and staged-page tables in
place. `--no-oai-cache` remains available for a temporary direct-network run.
