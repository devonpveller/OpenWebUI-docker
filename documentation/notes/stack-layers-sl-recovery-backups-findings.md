# sl-recovery-backups — findings

Measured 2026-09-20/21 on the live host (`SHUYA8873DESKTO`, PowerShell 5.1.26100.9168,
compose v5.3.0, OB1 gitlink `fe3e045`) from the worktree
`.claude/worktrees/wt-sl-recovery-backups`. Everything below was run, not
recalled. Where a check was NOT run, the row says so and why.

---

## 1. Recovery / backup / restore inventory

The name list was derived by grepping `documentation/runbooks/`, every
`README.md`, `SERVICE-LIFECYCLE.md`, `.claude/skills/stack-map/references/workspace-stacks.md`,
`CLAUDE.md` and `scripts/checks/stack-watchdog.ps1` for `\.(ps1|py|sh|bat|cmd)\b`
and filtering to the recovery/backup/restore family. 114 distinct
*path-as-written* forms collapsed to **41 distinct real files**.

**Summary: 41 named, 41 found on disk, 2 broken (both fixed), 14 as-written doc
paths that did not resolve (all corrected).** No named script is missing.

### Parse / dry-run results

`parse` = `[scriptblock]::Create((Get-Content -Raw <f>))` for `.ps1`,
`python -W error::SyntaxWarning -m py_compile` for `.py`, `sh -n` under Git Bash
for `.sh`. `.bat` has no parse-only form and none was invented.

| Script | Exists | Parses | Dry / help form — RESULT |
|---|---|---|---|
| `scripts/recovery/emergency-recovery.ps1` | yes | OK | **none exists** — every mode (`recover`/`nuclear`/`gpu-reset`) mutates. Parse only. |
| `scripts/recovery/gpu_check.py` | yes | OK | read-only by design; not run (touches the GPU probe path) |
| `scripts/recovery/namespace_reset.py` | yes | OK | **none** — destructive |
| `scripts/recovery/nuclear_option.py` | yes | OK | **none** — destructive |
| `scripts/recovery/rebuild_tailscale.py` | yes | OK | **none** — destructive |
| `scripts/recovery/restart_openwebui.py` | yes | OK | **none** — destructive |
| `scripts/recovery/status_check.py` | yes | OK *(after the fix in §2)* | **RAN**: `11/12 checks passed`, exit 0 |
| `scripts/recovery/quick-fixes.bat` | yes | n/a (cmd) | **none** |
| `scripts/recovery/update-stack.bat` | yes | n/a (cmd) | **none** |
| `scripts/archive/emergency-recovery.bat` | yes | n/a (cmd) | archived 2026-08-21, as the docs say — verified present at that path |
| `scripts/backup/backup-to-nas.ps1` | yes | OK | **RAN** `-NasUncRoot ... -DryRun` — works; see the TRAP in §5 |
| `scripts/backup/install-nas-backup-task.ps1` | yes | OK | **none** — registers a scheduled task |
| `scripts/backup/restore-from-snapshot.ps1` | yes | OK | **RAN** plan-only (no `-Apply`): discovered the 2026-09-20 little-coder archive and mapped all five subdirs to their volumes |
| `scripts/backup/set-nas-credential.ps1` | yes | OK | **none** — writes a credential vault |
| `scripts/maintenance/weekly-maintenance.ps1` | yes | OK | **none** — `-Register` is the only switch; a bare run compacts the VHDX |
| `backup/authelia-backup.sh` | yes | OK | container entrypoint; not host-runnable |
| `backup/caddy-backup.sh` | yes | OK | container entrypoint |
| `backup/generic-tar-backup.sh` | yes | OK | container entrypoint; its precheck strings are the basis of §3 |
| `backup/little-coder-backup.sh` | yes | OK | container entrypoint |
| `backup/llm-gateway-backup.sh` | yes | OK | container entrypoint |
| `backup/mnemory-backup.sh` | yes | OK | container entrypoint |
| `backup/mnemory-restore.sh` | yes | OK | container entrypoint |
| `backup/openwebui-backup.sh` | yes | OK | container entrypoint |
| `backup/openwebui-restore.sh` | yes | OK | container entrypoint |
| `backup/pg-backup.sh` | yes | OK | container entrypoint |
| `OB1/docker/backup/open-notebook-backup.sh` | yes | OK | container entrypoint |
| `OB1/docker/backup/openbrain-db-backup.sh` | yes | OK | container entrypoint |
| `OB1/docker/backup/openbrain-wiki-backup.sh` | yes | OK | container entrypoint |
| `scripts/checks/check-backup-coverage.ps1` | yes | OK | **RAN**: was `1 GAPS` (exit 1) on `wiki-viewer-srv`; now `CLEAN` (exit 0) — §4 |
| `scripts/checks/check-backup-freshness.ps1` | **NEW** | OK | **RAN** both ways — §3 |
| `scripts/sysadmin-mcp/check_backups.py` | yes | OK | **RAN** `--dry` and `--check` — §3 |
| `scripts/checks/stack-watchdog.ps1` | yes | OK | `-Mode check` **NOT run**: check mode calls `Repair-*`, which restarts live containers. Verified by parse + `check-watchdog-repair-targets.ps1` + isolated function runs (§3, §6). |
| `scripts/checks/check-watchdog-repair-targets.ps1` | yes | OK | **RAN** `-SkipDocker`: `REPAIR TARGETS OK: 24 container(s)` |
| `scripts/checks/check-openbrain-health.ps1` | yes | OK | not run (probes OB1; owned by the watchdog) |
| `scripts/checks/check-agent-org-health.ps1` | yes | OK | not run (same) |
| `scripts/portal/breach-killswitch.ps1` | yes | OK | `-DryRun` **was BROKEN** — see §2. **RAN** clean after the fix. |
| `scripts/portal/portal-on.ps1` | yes | OK | **RAN** `-WhatIf` (the one mutating call is `ShouldProcess`-guarded; verified before running) |
| `scripts/portal/portal-off.ps1` | yes | OK | **RAN** `-WhatIf` |
| `scripts/portal/portal-status.ps1` | yes | OK | **RAN**: portal up, all services `[OK]` |
| `scripts/sysadmin-mcp/compact-vhdx.ps1` | yes | OK | **none** — compacts a VHDX |
| `scripts/notify-mattermost.sh` | yes | OK | not run (would post) |

### As-written doc paths that did not resolve — all corrected

These are pre-subfolder forms: the scripts moved into `scripts/backup`,
`scripts/checks` and `scripts/portal` and the prose did not follow. Each is a
command an operator would copy out of a runbook and watch fail.

| As written | Occurrences | Now |
|---|---|---|
| `.\scripts\stack-watchdog.ps1` | PREVENTION-GUIDE.md ×3 | `.\scripts\checks\stack-watchdog.ps1` |
| `.\scripts\dev-helper.ps1` | PREVENTION-GUIDE.md ×7 | `.\scripts\checks\dev-helper.ps1` |
| `.\scripts\check-backup-coverage.ps1` | backup-conventions.md ×1 | `.\scripts\checks\check-backup-coverage.ps1` |
| `.\scripts\breach-killswitch.ps1` | incident-response.md ×3 | `.\scripts\portal\breach-killswitch.ps1` |
| `.\scripts\portal-status.ps1` | incident-response.md ×2 | `.\scripts\portal\portal-status.ps1` |
| `.\scripts\portal-on.ps1` | incident-response.md ×1 | `.\scripts\portal\portal-on.ps1` |
| `.\scripts\access-query.ps1` | monitoring-access.md ×6 | `.\scripts\portal\access-query.ps1` |
| `](../scripts/...)` markdown hrefs | backup-conventions ×5, monitoring-access ×1, restore-from-snapshot ×1 | `](../../scripts/...)` — from `documentation/runbooks/` it is two levels up, not one; every one of these resolved to a non-existent `documentation/scripts/` |
| `backup/Dockerfile.surreal` + `backup/open-notebook-backup.sh` | backup-conventions.md ×1 | `OB1/docker/backup/...` — they moved with the sidecar into the OB1 project (K.5b) |
| the scripts' OWN usage headers | backup-to-nas, install-nas-backup-task, restore-from-snapshot, set-nas-credential, check-backup-coverage, breach-killswitch | corrected to their real paths |

Re-derived afterwards by resolving every `](...)` href and every `.\...\*.ps1`
literal in `documentation/runbooks/*.md` against disk: **1 unresolved left**, and
it is a false positive — `UPDATE-MANAGEMENT.md`'s `../../../documentation-plans-ai-stack/...`
resolves correctly from the MAIN checkout (whose parent holds the plan store) and
only fails from a worktree three levels deeper.

---

## 2. Two recovery scripts were actually broken

**(a) `scripts/portal/breach-killswitch.ps1` could not run at all.** Lines 29 and
43 used `Get-Date -AsUTC`, which arrived in PowerShell 7. This host is 5.1, so:

```
breach-killswitch.ps1 : A parameter cannot be found that matches parameter name 'AsUTC'.
```

`$ts` is the FIRST statement in the `try` block, so both the documented `-DryRun`
rehearsal and a real incident run died before step 1 — before the final alert,
before stopping cloudflared, before the log snapshot. This is the script
`documentation/runbooks/incident-response.md` sends you to when a breach alert
looks credible. The identical trap was already found and commented in
`scripts/lib/portal-alerter-client.ps1:158` ("PS 5.1 has no Get-Date -AsUTC");
the killswitch never got the same fix. **Fixed** to `[DateTime]::UtcNow.ToString(...)`;
`-DryRun` now walks all five steps.

*How this survived:* nothing exercises it. It has no test, and its dry form was
apparently never run on this host — the inventory's dry-run column is the first
thing that did.

**(b) `scripts/recovery/status_check.py:97`** had `"...scripts\stack\stack.ps1..."`
in a non-raw string. `\s` is not a valid escape: Python 3.12 emits a
`SyntaxWarning` on import and 3.14 makes it a `SyntaxError`. The printed path was
never wrong. **Fixed** by making it a raw string; it now compiles clean under
`-W error::SyntaxWarning`.

---

## 3. Backup freshness: the reason was already written down

`backup/generic-tar-backup.sh` declines to tar broken state *by design*, says why,
and **exits 0**:

```
[<ts>] <PREFIX> PRECHECK SKIP: <DATA_DIR> does not exist
[<ts>] <PREFIX> PRECHECK SKIP: <DATA_DIR> is empty
[<ts>] <PREFIX> PRECHECK SKIP: <HEALTH_TCP> unreachable -- service unhealthy or down
```

Until now that sentence reached the sidecar's own `docker logs` and nowhere else.
`check_backups.py` and the watchdog both watched only artifact AGE, so
`lm-models` read as "350 h old" for two days while its own log said `/data is
empty` — and an age sends the reader hunting for a dead cron instead of a wrong
mount.

**Built:** `check_backups.py` now reads each running sidecar's last 60 log lines
and attaches the reason to the row. The empty/absent-`DATA_DIR` class is a
**failure in its own right, named by mount path**, because a bind pointing where
the data is not never heals; the unreachable-probe class is only a reason printed
beside a stale age, because the next run fixes it. `--check` is `--dry` with a
gate's exit code (1 on stale); the bare alerting invocation still exits 0 on
purpose — a daily task that "fails" until someone fixes a backup is a task the
operator disables. `scripts/checks/check-backup-freshness.ps1` is a thin shim
over it, so there is ONE implementation of "is this backup real".

### The false positive this nearly shipped with

First cut flagged any `PRECHECK SKIP` in the tail. Run against the live host it
reported **`ao-worker-1-journals-backup` and `ao-worker-2-journals-backup` as
producing nothing** — both healthy. Both carry a first-boot
`[2026-08-29T19:33:27Z] ao-worker-1-journals PRECHECK SKIP: /data is empty` in
the same 60 lines as last night's successful tar. A skip now counts only while it
is the sidecar's LAST WORD (newest stamped line in the tail, and newer than the
newest artifact). Verified live: `Get-BackupSkipReason ao-worker-1-journals-backup`
returns empty while `docker logs | grep 'PRECHECK SKIP'` still shows that line.

### Proof

- **GREEN, live**: `check-backup-freshness.ps1 -RepoRoot "D:\Open WebUI\ai-stack"`
  → `all fresh — ok=16 skipped=0`, **exit 0**.
- **RED, synthetic**: a sidecar with a 1-hour-old artifact (well inside its 204 h
  threshold) and a newer `PRECHECK SKIP: /models is empty` → stale, row carries
  `MOUNT /models`, message names `lm-models-backup`. Age alone calls that healthy.
- **RED, watchdog**: `Test-BackupRecency` with `Get-BackupSkipReason` stubbed to
  the lm-models shape → `BACKUP STALE - lm-models: MOUNT /data is empty or absent
  inside lm-models-backup - it is producing nothing`, returns `$false`. lm-models'
  real artifacts are FRESH on this host, so this fires independently of age.
- **GREEN, watchdog**: unstubbed `Test-BackupRecency` on the live host →
  `backup recency OK (13 dirs checked)`, `$true`.
- `python scripts/sysadmin-mcp/test_check_backups.py` → **27 passed, 0 failed**.

### Open, measured, NOT fixed

- **The two freshness lists disagree.** `check_backups.py::_EXPECTED` has **15**
  rows; `stack-watchdog.ps1::$ExpectedBackupRecency` has **13** — the two
  `ao-worker-*-journals` dirs are watched by the former and not the latter. I did
  not add them: the watchdog list has no container gate (unlike `check_backups.py`),
  so adding them would raise a false STALE whenever agent-org's `workers` profile
  is down. Closing it properly means gating per row. The watchdog's comment said
  "all 14 backups/<dir> trees"; corrected to 13 with a pointer to this.
- **`portal-alerter` /alert still returns HTTP 500.** Observed 2026-09-20T21:17
  during the `backup-to-nas.ps1 -DryRun`:
  `wget POST to /alert returned exit 1; body: ... HTTP/1.1 500 Internal Server Error`.
  This is the exact channel whose failure `check_backups.py`'s own comment blames
  for the 2026-09-13 NAS miss going unnoticed, and it is still broken today. The
  escalation path worked (`alert delivered via: mattermost, telegram`), so it is
  degraded, not silent. Not this item's to fix.

---

## 4. `wiki-viewer-srv` — excluded, with the measurement

`check-backup-coverage.ps1` exited 1 on `[GAP] wiki-viewer-srv - used by
[openbrain-wiki-viewer] but no backup container references it`.

**What the volume holds** (read-only, `docker run --rm --network none -v
open-brain_wiki-viewer-srv:/v:ro alpine`):

```
7.6G  /v
drwx------  build-0/     drwxr-xr-x  build-1/    (359 entries)
drwxr-xr-x  build-762/   build-763/  build-957/  build-958/
lrwxrwxrwx  current -> /srv/build-1
```

Six `build-<n>` snapshot trees plus a `current` symlink, mounted read-write at
`/srv` in `openbrain-wiki-viewer` (`docker inspect`). It is the wiki BUILDER's
output.

**Decision: `$intentionallyExcluded`**, and the reason was already on record —
`backups/wiki-viewer/RESTORE.md` says so under *"What this does NOT cover (by
design)"*: *"The built site (`/srv`, ~9.7 GB of hardlinked snapshots). It is a
CACHE: the builder regenerates it from the vault, and it goes stale within
hours. Not worth freezing."* (9.7 GB then, 7.6 GB now.)

**How a restore actually works**, so the exclusion is not a shrug:

- the **viewer VERSION** restores from the two `docker save` tars in
  `backups/wiki-viewer/` (`…20260828T192500Z` = graph v7.4.1, current;
  `…20260828T161500Z` = Batch A) via RESTORE.md paths A (retag, seconds), B
  (`docker load` from the tar) or C (rebuild from the OB1 tag);
- the **content** is covered twice over: the vault by `openbrain-wiki-backup`,
  `wiki_pages` by the whole-DB `openbrain-db` backup;
- the serving tree rebuilds itself from those two afterwards.

`check-backup-coverage.ps1` now exits **0** — `Coverage: CLEAN`.

*Housekeeping:* an empty, unlabelled `wiki-viewer-srv` volume (no project prefix)
exists on the host because my first listing command was mangled by MSYS path
translation into creating one. It is empty, referenced by nothing, and invisible
to the coverage check (no compose-project label). `docker volume rm wiki-viewer-srv`
removes it; the sandbox declined that call, so it is left for the operator. Do
**not** confuse it with `open-brain_wiki-viewer-srv`, the real 7.6 GB one.

---

## 5. `backups/smolcrawl/` archived

Four artifacts (2× tar.gz + 2× sha256, newest 2026-08-20) from a service retired
2026-08-20/21. Moved to `backups/archive/smolcrawl/` with `ARCHIVED.md` recording
the provenance, the sha256 of each file before and after (byte-identical), why
they moved, and how to restore one anyway. **Nothing deleted.** The now-empty
`backups/smolcrawl/` directory was removed.

It appeared in **no** expectation list — not `check_backups.py::_EXPECTED`, not
`$ExpectedBackupRecency` — so nothing had to be dropped. The two surviving
references (`check-backup-coverage.ps1`'s `smolcrawl-data` volume row and
`restore-from-snapshot.ps1`'s catalog comment) already say RETIRED and were left
alone; the VOLUME's fate is CLEANUP-PLAN v3's open soak-delete, an operator call.

**TRAP for the tester:** `backup-to-nas.ps1 -DryRun` run from a WORKTREE computes
its source as `<worktree>/backups`, which does not exist, and on that failure it
**dispatches real alerts** — one reached Mattermost and Telegram at
2026-09-20T21:17 during this work. Run it against the main checkout, or expect
the alert.

---

## 6. `portal/config/authelia/.healthcheck.env`

**What Authelia does with it:** `portal/docker-compose.yml:189` binds it as a
FILE — `./config/authelia/.healthcheck.env:/app/.healthcheck.env` — with the
comment *"Without this, read_only:true fails because Authelia writes to
/app/.healthcheck.env during startup. Host file is empty placeholder — Authelia
populates it."* So the container REWRITES it on every start, and the host file
**must exist before `up`** or Docker creates a directory in its place.

That rules out untracking it: a fresh clone would have no file, the bind would
become a directory, and Authelia's read-only root would fail on the write. It has
to stay tracked and stay writable.

**The drift was purely line endings**, verified rather than assumed — worktree
blob and index blob both `9c1d75f9…`, 190 bytes each, `git diff` empty, yet
`git status` shows ` M` and `git update-index --refresh` says `needs update`.
Cause: `core.autocrlf=true` with no attribute checks the file out as CRLF (196
bytes); Authelia, a Linux process, writes it back as LF (190); the index entry's
recorded size never matches again.

**Fix:** `.gitattributes` gains
`portal/config/authelia/.healthcheck.env text eol=lf`, so the bytes git checks
out are the bytes Authelia writes. Kept as `text` rather than `-text` so a real
content change (a port, a scheme) is still a readable diff.

**Verified in the worktree:** attribute applied (`check-attr` → `eol: lf`),
`git add --renormalize`, one `git update-index --refresh`, then the rewrite
simulated by rewriting the file with LF — `git status` **clean**, and clean again
after a second rewrite.

**LANDING STEP for the main checkout:** the merge will not necessarily rewrite the
file (content is unchanged), so the stale stat entry can persist for one cycle.
Run once after merging:

```powershell
git -C "D:\Open WebUI\ai-stack" update-index --refresh -- portal/config/authelia/.healthcheck.env
```

Then `portal-on.ps1` / `portal-off.ps1` and confirm `git status` is clean under
`portal/config/authelia/`.

---

## 7. `emergency-recovery.ps1`'s two numbers — one was wrong, one was right

Measured from the rendered config at the pinned gitlink `fe3e045`, with
`COMPOSE_PROFILES` neutralised (set to a profile no service declares, because an
empty string lets `OB1/docker/.env` win):

| Render | Services |
|---|---|
| no profiles | **20** |
| `research` + `idea-refinery` | **23** |
| all four | **30** |

So the gated set is **10**: `openbrain-curator`, `openbrain-research`,
`surrealdb`, `open_notebook`, `openbrain-wiki`, `openbrain-wiki-viewer`,
`open-notebook-backup`, `openbrain-wiki-backup`, `openbrain-idea-refinery`,
`openbrain-workbench`. The wiki+notebook pair alone accounts for **7** of them.

Mapping each of the ten to its networks in the rendered config (compose key →
real name: `llm-net` → `ai-stack_llm-net`, `app-net` → `ai-stack_app-net`,
`search-gw-net` → `ai-stack_default`):

**7 of the 10 hold `ai-stack_*` endpoints** — curator, research, open_notebook,
wiki, wiki-viewer, idea-refinery, workbench. The other three (`surrealdb`,
`open-notebook-backup`, `openbrain-wiki-backup`) sit on `open-brain_default`
only and block nothing of the anchor's.

- `Reset-OB1Stack`'s *"silently leaves seven containers down after a recreate"*
  was **WRONG** — a profile-less `up` leaves **ten** down. Seven is the
  wiki+notebook contribution, not the whole gated set. Corrected, with the
  measurement.
- `Invoke-NuclearRecovery`'s *"removes 20 of 30 and leaves TEN containers holding
  endpoints on ai-stack_llm-net / app-net / default"* was **half right**: ten are
  left, but only **seven** hold anchor endpoints. Corrected.
- The header comment at `$Script:OB1Profiles` (*"seven of the ten gated OB1
  containers hold endpoints"*) is **already correct** and was left alone.

No other line of that script changed.

---

## 8. Inference serving depth — the probe the outage asked for

**The outage:** 2026-09-19 18:54 → 2026-09-21 00:57, ~30 h. `llama-cpp-upstream`
was recreated with `LM_MODELS_DIR` unset, so compose bound its default
`../../data/models/gguf` — an empty directory — at `/models`. Every one of the
fifteen `stack.py health` probes stayed GREEN throughout: the container was
healthy, the anchor network existed, and LiteLLM's `/health/liveliness` answered
200. llama-swap's `/health` answers **without loading a model**. The first real
chat returned `500 upstream command exited prematurely`.

**Built:** a sixteenth probe, in the cheapest order that cannot be fooled.

1. Count `.gguf` under the upstream's `/models`. Zero is a FAIL that names the
   **host** path of the bind (`docker inspect`), checked FIRST so an empty store
   produces a diagnosis rather than a 500.
2. Read llama-swap `/running`. A resident model is proof of serving depth at zero
   cost, and it is the normal case.
3. Only if nothing is resident: **one** completion, `max_tokens 3`, **through the
   gateway** (never around it). Timeout 600 s, because a cold load is minutes —
   257 s measured on this host 2026-09-21.

**Key handling.** `inference/.env` is read (via the existing `read_env_file`) only
to confirm `LITELLM_MASTER_KEY` is CONFIGURED, so an unmigrated host gets a
sentence naming the file instead of a 401 to decode. The value is never handled:
the request is made by a script running INSIDE `llm-gateway` that reads the
container's own environment, which compose populated from that same file
(`LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}`, `inference/compose/gateway.yml:107`).
Nothing secret reaches this process, its argv, or a probe line — pinned by a test.
(`llm-gateway` publishes no host port by deliberate design, so `docker exec` is
the only route in anyway.)

**Proof**

- **GREEN, live**: `python scripts/stack/stack.py health` → 16 probes, all `[OK]`,
  exit 0, with
  `inference: serving depth: qwen36-27b resident on llama-cpp-upstream (/running)`.
- **Completion branch, live**: the in-container script run directly →
  `OK a 3-token completion through the gateway returned 200 in 1s (qwen36-27b loaded)`
  (1 s, not 257 s, because the model was already resident — the 257 s figure is
  the orchestrator's cold-load measurement from the repair).
- **RED, faked**: an empty `/models` → `[FAIL] inference: serving depth:
  llama-cpp-upstream's /models holds NO .gguf files (host bind: …)`, exit 1, and
  the probe does **not** go on to spend a cold-load timeout.
- `python -m pytest scripts/stack -q` → **114 passed**.

The watchdog gets the mount half only (`Test-InferenceServingDepth`), not the
completion: it cycles every few minutes and a cold load per cycle would be worse
than the bug. The completion belongs to `stack.py health`, which an operator runs
once after a recreate — which is how SERVICE-LIFECYCLE row 5 now words it.

### Open — a count this change makes stale in two files I do not own

`PS1_PROBES` is now 16 and `stack.py`'s own header comment says so. Two documents
still say fifteen and are owned by the parallel item `sl-docs-posture`:

- `scripts/stack/README.md:306` — "The fifteen functional probes…"
- `CLAUDE.md` — "`health` (15 probes, exit code = failures)"

Left untouched deliberately to avoid conflicting with that item mid-flight.
**Whoever lands second must update both to sixteen.**

---

## 9. Things seen and deliberately not touched

- `scripts/checks/stack-watchdog.ps1:941` and `:972` name
  `scripts\check-openbrain-health.ps1` / `scripts\check-agent-org-health.ps1` in
  COMMENTS; both live in `scripts\checks\`. The runtime `Join-Path $SCRIPT_DIR …`
  calls are correct, so only the comments are stale. `sl-checks-worktree` owns
  the file-selection logic in `scripts/checks/*.ps1`; left for it.
- `scripts/recovery/emergency-recovery.ps1:42` and `:1111` name
  `scripts/portal-on.ps1` (real path `scripts/portal/portal-on.ps1`). The anchor
  says change nothing else in that script, so they stand. Same class as the
  as-written paths fixed in §1.
- `scripts/portal/portal-on.ps1`'s header carries the same pre-subfolder form for
  itself. Not a backup/restore script; left.
- Pre-existing BOM + non-ASCII in three `.ps1` files I edited
  (`stack-watchdog.ps1` 57 non-ASCII chars, `emergency-recovery.ps1` 936,
  `check-backup-coverage.ps1` 9). Stripping a BOM from a file containing UTF-8
  would make PS 5.1 read it as ANSI and mangle those characters, so the BOMs
  stay. **Every line this item ADDS is pure ASCII** — verified over the whole
  `.ps1` diff.
