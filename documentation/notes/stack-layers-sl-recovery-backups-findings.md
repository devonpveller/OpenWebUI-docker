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
*path-as-written* forms collapsed to **43 distinct real files**.

The last two rows, `access-query.ps1` and `dev-helper.ps1`, are here because
attempt 1 left them out: they are not recovery/backup/restore scripts (the
ARTIFACT line's scope), but the ACCEPTANCE line says *every* script named in
those documents, and this item corrected both of their paths. A row whose path
this change edited belongs in the table that says the paths are right.

**Summary: 43 named, 43 found on disk, 2 broken (both fixed), 14 as-written doc
paths that did not resolve (all corrected).** No named script is missing.

### Parse / dry-run results

`parse` = `[scriptblock]::Create((Get-Content -Raw <f>))` for `.ps1`,
`python -W error::SyntaxWarning -m py_compile` for `.py`, `sh -n` under Git Bash
for `.sh`. `.bat` has no parse-only form and none was invented.

| Script | Exists | Parses | Dry / help form — RESULT |
|---|---|---|---|
| `scripts/recovery/emergency-recovery.ps1` | yes | OK | **none exists** — every mode (`recover`/`nuclear`/`gpu-reset`) mutates. Parse only. |
| `scripts/recovery/gpu_check.py` | yes | OK | **NOT read-only** (corrected 2026-09-20, sl-gate4-carries): `restart_gpu_services()` runs `docker compose restart openwebui llama-cpp-upstream llama-cpp-embed-upstream` at line 146, and `main()` restarts `llama-cpp-upstream` (line 214) and `llama-cpp-embed-upstream` (line 228) on their own. Not run: a bare `python scripts/recovery/gpu_check.py` mutates the running stack. |
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
| `scripts/maintenance/weekly-maintenance.ps1` | yes | OK | **none** — no dry-run form, and a bare run compacts the VHDX. Its `param()` block (lines 25-29) declares THREE parameters, not one (corrected 2026-09-20, sl-gate4-carries): `[switch]$Register`, `[switch]$SkipCompact` ("reclaim + report only" - the closest thing to a safe form) and `[int]$CompactWaitMinutes = 25`. |
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
| `scripts/checks/stack-watchdog.ps1` | yes | OK | `-Mode check` **NOT run**: check mode calls `Repair-*`, which restarts live containers. Verified by parse + `check-watchdog-repair-targets.ps1` + isolated function runs (§3). |
| `scripts/checks/check-watchdog-repair-targets.ps1` | yes | OK | **RAN** `-SkipDocker`: `REPAIR TARGETS OK: 24 container(s)` |
| `scripts/checks/check-openbrain-health.ps1` | yes | OK | not run (probes OB1; owned by the watchdog) |
| `scripts/checks/check-agent-org-health.ps1` | yes | OK | not run (same) |
| `scripts/portal/breach-killswitch.ps1` | yes | OK | `-DryRun` **was BROKEN** — see §2. **RAN** clean after the fix. |
| `scripts/portal/portal-on.ps1` | yes | OK | **RAN** `-WhatIf` (the one mutating call is `ShouldProcess`-guarded; verified before running) |
| `scripts/portal/portal-off.ps1` | yes | OK | **RAN** `-WhatIf` |
| `scripts/portal/portal-status.ps1` | yes | OK | **RAN**: portal up, all services `[OK]` |
| `scripts/sysadmin-mcp/compact-vhdx.ps1` | yes | OK | **none** — compacts a VHDX |
| `scripts/portal/access-query.ps1` | yes | OK | **RAN** parse only; named 6× in incident-response / monitoring-access and its path is one this item corrected, so it belongs here even though it is a log-query tool rather than a recovery script |
| `scripts/checks/dev-helper.ps1` | yes | OK | **RAN** parse only; named 7× in PREVENTION-GUIDE, path also corrected here |
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

**Corrected 2026-09-20 (sl-gate4-carries).** That sweep was WRONG: it found one
unresolved href where an unbounded one finds THREE. Re-run with
`documentation/evidence/sl-gate4-carries/href-sweep.py` — every `](...)` target in
`documentation/runbooks/**/*.md` that is not `http(s):`, `mailto:` or a bare
`#anchor`, resolved against the directory of the file carrying it, no allowlist:
**17 relative hrefs, 3 unresolved at f3eee64**. Two of them were genuine
pre-existing `../` vs `../../` errors in `backup-conventions.md` — line 92
`](../docker-compose.yml)` and line 130 `](../.env.example)`, both resolving into
a `documentation/` directory that holds neither file — and they are the same
one-level-short mistake as the `](../scripts/...)` row above, in the same file,
missed because the original sweep matched only the `../scripts/` spelling. Both
are FIXED (now `../../`). The third is the `UPDATE-MANAGEMENT.md` plan-store link,
which is the false positive the sentence above describes and is confirmed as one:
resolved from `D:\Open WebUI\ai-stack\documentation\runbooks` it lands on an
existing file, and it fails only at the extra depth of `.claude/worktrees/<id>/`.
So the honest statement is: **at main-checkout depth the sweep is now zero
unresolved; inside a nested worktree exactly one survivor remains, and it is
depth-dependent, not broken.**

---

## 2. Two recovery scripts were actually broken

**(a) `scripts/portal/breach-killswitch.ps1` could not run at all.** The `$ts`
assignment at the top of its `try` block and the `timestamp_utc` field of its
alert body (lines 29 and 43 BEFORE this change; 35 and 49 after) used
`Get-Date -AsUTC`, which arrived in PowerShell 7. This host is 5.1, so:

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

**(b) `scripts/recovery/status_check.py`** — the `log_info` in `start_missing_services()` (line 101 after this change; it was 97 before) had `"...scripts\stack\stack.ps1..."`
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
blob and index blob both `d1687154cc7905352769c97795981f302dec4478`, 190 bytes
each (the 196-byte CRLF form of the same content is
`745619174b12b87f3559503248648461a68b5ea2`; both re-derived 2026-09-21 with
`git hash-object --no-filters`, because without that flag the clean filter
makes both spellings return the LF hash), `git diff` empty, yet
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
sentence naming the file instead of a 401 to decode. **`read_env_file` does
return a dict containing the value, so the string is briefly in this process's
memory — attempt 1 claimed "never reaches this process", which is false.** What
holds, and is what matters, is that it is read for a PRESENCE CHECK only and is
never passed as an argument, logged, or printed:
the request is made by a script running INSIDE `llm-gateway` that reads the
container's own environment, which compose populated from that same file
(`LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}`, `inference/compose/gateway.yml:107`).
No secret value reaches an argv, a log line or a probe line — pinned by a test.
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

### CLOSED in attempt 2 — the probe count

Attempt 1 left the count to `sl-docs-posture` to avoid conflicting with it
mid-flight. That item merged (`6979e9e`) **keeping "15"**, so the count was
briefly unowned: `PS1_PROBES` said 16 and seven sentences across six documents
said fifteen. Landing second, this item now owns every one of them — see §10.3
for the full list and what changed in each. `scripts/stack/README.md:306` is the
one that needed more than a digit: it is the probe LIST, and the sixteenth probe
has an entry there describing what it checks and what fails it.

---

## 9. Things seen and deliberately not touched

- `scripts/checks/stack-watchdog.ps1:984` and `:1015` (re-derived after this item's own edits shifted the file ~119 lines) name
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
  stay. **Every line this item ADDS is pure ASCII once a leading BOM is
  discounted** (measured 2026-09-21: 0). The blunt `byte > 127` sweep over the
  added `.ps1` diff returns **6**, not 0 — six first lines whose header comment
  changed, each carrying its file's pre-existing BOM. That is the sweep seeing
  the BOM, not new non-ASCII: **BOM state is unchanged on all nine pre-existing
  `.ps1` files this item touches** (`efbbbf` at base and at tip on all eight
  that had one; the new `check-backup-freshness.ps1` has none), which is what
  this paragraph actually argues for. Attempt 1 stated the expectation as 0 and
  it did not reproduce.

---

## 10. Attempt 2 (2026-09-21) — what attempt 1 got wrong

Attempt 1 was FAILED by the tester on one defect and refuted on three more. All
are fixed here. Rebased onto `development` after `sl-checks-worktree` (5b42432)
and `sl-docs-posture` (6979e9e) landed.

### 10.1 FAIL — four BACKSPACE bytes shipped in three scripts

A `\b` inside a **non-raw** Python replacement string during §1's path rewrite
put `0x08` into three files. Base blobs held zero:

| File | Line | Class |
|---|---|---|
| `scripts/backup/backup-to-nas.ps1` | 48 | comment |
| `scripts/backup/install-nas-backup-task.ps1` | 27 | comment |
| `scripts/backup/set-nas-credential.ps1` | 19 | comment |
| `scripts/backup/set-nas-credential.ps1` | 139 | **executable `Write-Host`** |

The last one matters most: a backspace ERASES the preceding character when
rendered, so an operator setting up NAS credentials was told to run
`.\scriptackup\install-nas-backup-task.ps1`, which does not exist. It also
directly refuted this item's own §1 claim that the scripts' usage headers were
"corrected to their real paths".

**This is the same escape-sequence class as the `\s` bug this item fixed in
`status_check.py` — introduced by the very pass that fixed it, in the same
sitting.** It then happened a THIRD time while writing attempt 2: one `0x08`
landed in this item's own test plan, in the row describing the bug. The new gate
caught that one before it was committed, which is the whole argument for it.

**Why every gate passed.** `check-project-configs.ps1` only TOKENIZES `.ps1`, and
`0x08` is whitespace to the tokenizer — it reported "parse clean" on a file
carrying the bug. `validate-lineendings.ps1` looks only at CR/LF. The anchor's
encoding sweep tests `byte > 127`, and `0x08` is 8. Nothing in the repo had ever
looked BELOW 0x20. That is this workspace's recurring shape: a check that passes
while checking nothing.

**Fix, beyond the four characters:** `check-project-configs.ps1` gains **gate 4**
— any byte in `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]` in an ADDED line of any staged
text file is refused, pre-commit, naming the file, the byte and the line. Proven
both ways: planting the exact shipped bug gives exit 1 (while the same file still
reports "parse clean" beside it), and the shipped tree gives exit 0. It is not
`.ps1`-only — the markdown case above was caught by the same gate.

**ADDED LINES, not whole files, deliberately.** Eleven such bytes already sit in
older files (measured 2026-09-21): `documentation/evidence/podlinks/test-plan.md`
(4× 0x08), `documentation/evidence/sl-env-split/test-plan.md` (0x07, 0x0C),
`documentation/implementation-guide/dark-factory-unification/DECISIONS.md` (2×
0x07), `documentation/notes/stack-layers-sl-env-split-findings.md` (0x07),
`documentation/notes/u8floor-findings.md` (0x08), `documentation/notes/u8h4-findings.md`
(0x07). **So this class has been shipping for months, across at least four
earlier items.** A whole-file rule would fail the next commit that touched any of
them for an unrelated reason; an added-line rule catches what a commit
INTRODUCES, which is the failure mode. Cleaning up the eleven is a separate,
safe change and is NOT done here.

### 10.2 REFUTED — the UTC parser was an hour early for eight months a year

`_parse_iso_z` used `time.mktime(strptime(...)) - time.timezone`. `mktime`
interprets the struct as LOCAL time while `time.timezone` is the STANDARD
offset, so inside DST the pair is 3600 s out. Measured on this host
(`timezone=18000`, `altzone=14400`, `daylight=1`) against `calendar.timegm`:

| stamp | delta |
|---|---|
| `2026-07-04T12:00:00Z` | **-3600 s** |
| `2026-09-20T12:00:00Z` | **-3600 s** |
| `2026-01-15T12:00:00Z` | 0 s |

`_skip_is_current` compares that value against an artifact mtime, so a **real,
current** `PRECHECK SKIP` was judged "superseded" whenever it was less than about
an hour newer than the last artifact — the row went GREEN. The tester reproduced
it against the live red-probe sidecar: a planted artifact 2 h old reported stale;
1 h, 30 min and 5 min did not.

It failed in the safe direction (false green, never false red) and the next
sidecar cycle would catch it — but the shape this whole item exists for is a
recreate with a wrong bind, which is exactly when a sidecar runs minutes after a
success. Fixed with `calendar.timegm`; all three deltas are now 0 s.

**The shipped test passed by a 60-second margin** — a 1 h artifact against a skip
stamped `now + 60 s`, i.e. the bug's own offset plus a minute. Both tests are now
DISCRIMINATING: a skip **30 minutes newer than a 2 h artifact**, a gap smaller
than the offset, so the hour moves the skip to the wrong side of it. Verified by
monkeypatching the old expression back: **4 checks FAIL**, including
`test_evaluate_reports_the_mount` raising `IndexError` because under the bug
there is no stale `lm-models` row at all. Under the fix: **32 passed, 0 failed**.

The PowerShell twin `Get-BackupSkipReason` is unaffected — it compares ISO-8601
`Z` strings lexically and never converts to epoch.

### 10.3 The probe count, now owned end to end

Attempt 1 left this to `sl-docs-posture`, which merged keeping "15". Landing
second, this item owns all of it. Seven sentences updated, plus two the sweep
found that the map did not:

| File | What changed |
|---|---|
| `CLAUDE.md` | driver row: `health` (15 -> **16** probes) |
| `README.md` ×2 | both `stack.py health` comments |
| `inference/README.md` | `# 15 -> 16 probes` |
| `frontend/README.md` | `# 15 -> 16 probes` |
| `documentation/runbooks/SERVICE-LIFECYCLE.md` ×2 | row 5 prose + the command comment near the bottom |
| `scripts/stack/README.md` (:51) | `the 15-probe sweep` -> `the 16-probe sweep` |
| `scripts/stack/README.md` (:306) | **the probe LIST** — a full entry for the sixteenth, not a digit change |
| `scripts/stack/test_stack.py` | section comment (found by sweep, not in the map) |

The list entry states what the probe checks (`.gguf` census -> `/running` -> one
completion) and **what fails it** (empty or missing `/models`; a completion that
is not 200; the upstream not running; `LITELLM_MASTER_KEY` absent). The "what the
sweep touches" list was RE-MEASURED rather than incremented: **seven** `docker
exec`s now (five as before plus two on `llama-cpp-upstream`) and seven HTTP GETs,
with `docker inspect` and the landing completion marked CONDITIONAL. The old
list's "`docker ps` — twice" is now qualified: the second call only fires when
the frontend render has no `tailscale` service, so it is one call on this host.

### 10.4 Three citations that did not reproduce

- **The blob hash.** `.gitattributes` and §6 both said `9c1d75f9…`. It reproduces
  nowhere. Measured 2026-09-21 with `git hash-object --no-filters`: the 190-byte
  LF form is **`d1687154cc7905352769c97795981f302dec4478`** and the 196-byte CRLF
  form is **`745619174b12b87f3559503248648461a68b5ea2`**. `--no-filters` is the
  trap: without it git applies the clean filter and BOTH spellings return the LF
  hash, which is almost certainly how a wrong number came to be written down with
  confidence. The substantive claim was always true and the tester verified it
  independently; only the number was wrong, in a committed file.
- **"Every added `.ps1` line is pure ASCII."** The plan's own command returns
  **6**, not 0 — see §9, now reworded to the claim that actually reproduces.
- **"The key never reaches this process."** False as written: `read_env_file`
  returns a dict of every value, so the string is briefly in memory. The material
  property — never passed as an argument, logged, or printed — holds, and is what
  §8, the code comment and claim C22 now say instead.

### 10.5 A plan defect that would have paged the operator

T3c's RED case set `$PROJECT_DIR` to the live main checkout. `Test-BackupRecency`'s
stale branch is not inert: it posts via `scripts/notify-mattermost.sh` and writes
`logs\.backup-recency-alert`, a **12-hour suppression sentinel**. Anyone running
that case verbatim would have paged the operator with a FALSE stale and left a
sentinel that swallows the next REAL backup alert for twelve hours. The tester
spotted it and used a scratch root instead.

The plan now uses a scratch `$PROJECT_DIR` for the RED case, pre-creates all 13
backup dirs with fresh artifacts so the `lm-models` MOUNT row is the ONLY stale
line, and verifies `Test-Path "$MAIN\logs\.backup-recency-alert"` is False
afterwards. Re-run while writing this: live sentinel **absent**, scratch sentinel
present, one stale row. The notifier is not stubbed and need not be — the scratch
root has no `scripts/` directory, so the path the alerting branch builds cannot
exist, and the call is inside a `try/catch`.

One more trap recorded there: the header lines fed to `Set-Content` must be
NEWLINE-separated inside `@( )`, not comma-separated. With commas, `+` binds
tighter than `,`, the whole array collapses into one space-joined string, and the
generated script fails to parse. Attempt 1's plan had the comma form.

### 10.6 Two inventory rows attempt 1 omitted

`scripts/portal/access-query.ps1` (named 6× in incident-response and
monitoring-access) and `scripts/checks/dev-helper.ps1` (7× in PREVENTION-GUIDE)
were not in the table. They are not recovery/backup/restore scripts — the
ARTIFACT line's scope — but the ACCEPTANCE line says *every* script named in
those documents, and **this item corrected both of their paths**. A script whose
path this change edited belongs in the table that claims the paths are right.
Both added, both parse clean. The table is now **43 rows, 43 present on disk**.
