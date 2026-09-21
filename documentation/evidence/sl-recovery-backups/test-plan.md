# Test plan — `sl-recovery-backups`

Branch `work/sl-recovery-backups`, worktree `wt-sl-recovery-backups`.
Anchor: `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-recovery-backups.json`.
Findings sink: `documentation/notes/stack-layers-sl-recovery-backups-findings.md`.

**Leases.** T2b and T8b start labelled test containers — take the plane lease
named in the case first (`scripts/agent-harness/lease.ps1 -Acquire -Name <plane>`).
Every other case is read-only and needs none. Never attach a test container to an
`ai-stack_*` network; tag test images `:wt-sl-recovery-backups`; remove
everything afterwards.

**Two traps, both measured, both real:**

1. `backup-to-nas.ps1 -DryRun` run from a WORKTREE resolves its source to
   `<worktree>/backups`, which does not exist — and on that failure it
   **dispatches live alerts to Mattermost and Telegram**. T1 runs it against the
   main checkout for exactly this reason.
2. `stack-watchdog.ps1 -Mode check` is **not** read-only: check mode calls
   `Repair-*`, which restarts live containers. Do not run it to verify T3/T8.
   T3c and T8c exercise the functions in isolation instead.

Let `WT` = `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-recovery-backups`,
`MAIN` = `D:\Open WebUI\ai-stack`.

---

## Claims introduced by this change

Every sentence this item adds to the repo, as a checkable claim. A tester who
cannot reproduce one has found a defect.

| # | Claim | Where asserted | Case |
|---|---|---|---|
| C1 | Every script named in the runbooks, READMEs, SERVICE-LIFECYCLE, the stack-map reference and the watchdog exists on disk; all **43** parse (41 in the recovery/backup/restore family, plus `access-query.ps1` and `dev-helper.ps1` whose paths this item corrected) | findings §1 | T1 |
| C2 | 14 as-written doc paths did not resolve and now do | findings §1 | T1b |
| C3 | `breach-killswitch.ps1` could not run on PS 5.1 (`Get-Date -AsUTC`); it can now | findings §2a | T1c |
| C4 | `status_check.py`'s `log_info` in `start_missing_services()` emitted a SyntaxWarning (`\s`); it no longer does | findings §2b | T1d |
| C5 | A current `PRECHECK SKIP` naming an empty/absent DATA_DIR is a FAILURE that names the mount, whatever the artifact age says | `check_backups.py`, `stack-watchdog.ps1` | T2, T2b, T3c |
| C6 | An unreachable-`HEALTH_TCP` skip is a reason beside a stale age, never a failure by itself | `check_backups.py` | T2c |
| C7 | A skip superseded by a later line from the same container is NOT current — and both live `ao-worker-*-journals-backup` are in exactly that state | `check_backups.py`, `Get-BackupSkipReason` | T2d |
| C8 | `--check` exits 1 on stale; the bare alerting invocation still exits 0 | `check_backups.py` | T2e |
| C9 | Both backup gates are green on the live host | — | T2a |
| C10 | `wiki-viewer-srv` holds 7.6 GB of `build-<n>` snapshots + a `current` symlink; it is the builder's regenerable output | `check-backup-coverage.ps1` | T4 |
| C11 | The viewer's restore path is the two image tars in `backups/wiki-viewer/`, per its RESTORE.md; content is covered by `openbrain-wiki` + `openbrain-db` | findings §4 | T4b |
| C12 | `check-backup-coverage.ps1` exits 0 | — | T4 |
| C13 | OB1 at `fe3e045` renders 20 bare / 23 with research+idea-refinery / 30 with all four; 10 gated; the wiki+notebook pair is 7 of them | `emergency-recovery.ps1` comments | T5 |
| C14 | 7 of the 10 gated containers hold `ai-stack_*` endpoints; `surrealdb`, `open-notebook-backup`, `openbrain-wiki-backup` do not | `emergency-recovery.ps1` comments | T5b |
| C15 | No line of `emergency-recovery.ps1` changed except those two comments | — | T5c |
| C16 | The four smolcrawl artifacts moved to `backups/archive/smolcrawl/` byte-identical; none deleted; smolcrawl was in no expectation list | `backups/archive/smolcrawl/ARCHIVED.md` | T6 |
| C17 | Authelia rewrites `.healthcheck.env` at runtime; it is a FILE bind, so it cannot be untracked | findings §6 | T7 |
| C18 | The drift is purely line endings (`core.autocrlf=true` checkout CRLF vs Authelia's LF); `eol=lf` fixes it | `.gitattributes` | T7b |
| C19 | The serving-depth probe passes and names the resident model on a healthy host | `stack.py` | T8 |
| C20 | It FAILS, naming the host bind, against an empty models mount — and does not attempt a completion | `stack.py` | T8b, T8c |
| C21 | With nothing resident it makes exactly ONE completion, through `llm-gateway`, never the upstream | `stack.py` | T8d |
| C22 | The caller key is read for a PRESENCE CHECK only and is never passed as an argument, logged, or printed (it IS briefly in process memory — `read_env_file` returns every value) | `stack.py` | T8e |
| C23 | `health` is now 16 probes, `PS1_PROBES` is pinned to 16, and the exit code is still the number of failures | `test_stack.py` | T8f |
| C24 | `$ExpectedBackupRecency` is 13 rows and `_EXPECTED` is 15; the two ao-worker journal dirs are the difference | `stack-watchdog.ps1` comment | T3b |
| C25 | `portal-alerter` `/alert` returns HTTP 500 today | findings §3 | T9 |
| C26 | Every sentence stating the probe COUNT now says sixteen, and the probe LIST carries an entry for the sixteenth | 7 documents | T10 |

### Claims added by attempt 2 (2026-09-21)

| # | Claim | Where asserted | Case |
|---|---|---|---|
| C27 | Four `0x08` BACKSPACE bytes shipped in attempt 1 at `backup-to-nas.ps1:48`, `install-nas-backup-task.ps1:27`, `set-nas-credential.ps1:19` and `:139`; base blobs held zero; all four are now repaired to `.\scripts\backup\…` | the three scripts | G1 |
| C28 | A control character in an added line is now refused PRE-COMMIT by `check-project-configs.ps1` gate 4 — and the same file still reports `parse clean`, which is why the parse gate never caught it | `check-project-configs.ps1` | G1 |
| C29 | That gate scans ADDED LINES, not whole files, because 11 such bytes already sit in older `documentation/evidence` and `documentation/notes` files and a whole-file rule would fail unrelated commits | `check-project-configs.ps1` comment | G1b |
| C30 | `_parse_iso_z` used `mktime - time.timezone` and read every stamp **3600 s early inside DST** (measured: `timezone=18000`, `altzone=14400`; delta −3600 s in July and September, 0 s in January) | `check_backups.py` | T2g |
| C31 | That hour made `_skip_is_current` call a real skip "superseded" whenever the gap to the artifact was under an hour; `calendar.timegm` fixes it | `check_backups.py` | T2g |
| C32 | The new tests DISCRIMINATE: a skip 30 min newer than a 2 h artifact. Under the old expression `test_stamp_parsing_is_utc_all_year` and `test_evaluate_reports_the_mount` fail (4 checks); under the new they pass (32/32) | `test_check_backups.py` | T2g |
| C33 | `health` touches **seven** `docker exec`s now (five as before, plus two on `llama-cpp-upstream`) and seven HTTP GETs; `docker inspect` and the landing completion are conditional | `scripts/stack/README.md` | T8g |
| C34 | The `.healthcheck.env` blobs are `d1687154…` (190 bytes, LF) and `745619174b12…` (196 bytes, CRLF), both via `git hash-object --no-filters`; attempt 1's `9c1d75f9` reproduces nowhere | `.gitattributes`, findings §6 | T7c |
| C35 | The caller key IS briefly in process memory (`read_env_file` returns every value); what holds is that it is read for a presence check and never passed as an argument, logged, or printed | `stack.py`, findings §8 | T8e |
| C36 | The blunt `byte > 127` sweep returns **6**, all pre-existing BOMs on changed first lines; BOM state is unchanged on all nine pre-existing `.ps1` files | findings §9 | G2 |
| C37 | T3c's RED case must use a scratch `$PROJECT_DIR`: against `$MAIN` it pages the operator and writes a 12 h suppression sentinel into the live tree | this plan, T3c | T3c |

### T2g — the timezone fix, both directions

```bash
cd "$WT"
# the deltas
python -c "
import calendar, time, sys; sys.path.insert(0,'scripts/sysadmin-mcp'); import check_backups as cb
for s in ('2026-07-04T12:00:00Z','2026-09-20T12:00:00Z','2026-01-15T12:00:00Z'):
    print(s, '%+d s' % (cb._parse_iso_z(s) - calendar.timegm(time.strptime(s,'%Y-%m-%dT%H:%M:%SZ'))))"
```
**Expected:** `+0 s` on all three.

```bash
# the tests must FAIL under the old expression, or they do not discriminate
cd "$WT" && python - <<'EOF'
import sys, time, io, contextlib
sys.path.insert(0, 'scripts/sysadmin-mcp')
import check_backups as cb
cb._parse_iso_z = lambda s: time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
import test_check_backups as t
for name in ("test_stamp_parsing_is_utc_all_year", "test_evaluate_reports_the_mount"):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): getattr(t, name)()
        print(f"{name}: completed")
    except Exception as e:
        print(f"{name}: RAISED {type(e).__name__} (the stale row it asserts is absent)")
    for l in buf.getvalue().splitlines():
        if "FAIL" in l: print("   ", l.strip())
print("OLD expression:", t._failed, "check(s) FAILED")
EOF
```
**Expected:** **4 checks FAILED** — the two DST deltas, the 30-min-gap
currency check, and `test_evaluate_reports_the_mount` raising `IndexError`
because under the bug there is no stale `lm-models` row **at all** (the false
green this fixes). Then `python scripts/sysadmin-mcp/test_check_backups.py`
alone: **32 passed, 0 failed**.
**Disproves this criterion:** the old expression passing either test — that
would mean the margin is not discriminating and the bug could ship again.

### T7c — the blob hashes reproduce

```bash
cd "$WT" && python -c "
d=open('portal/config/authelia/.healthcheck.env','rb').read()
open('lf.tmp','wb').write(d.replace(b'
',b'
'))
open('crlf.tmp','wb').write(d.replace(b'
',b'
').replace(b'
',b'
'))"
git hash-object --no-filters lf.tmp crlf.tmp && rm -f lf.tmp crlf.tmp
```
**Expected:** `d1687154cc7905352769c97795981f302dec4478` then
`745619174b12b87f3559503248648461a68b5ea2`.
**`--no-filters` is load-bearing:** without it git applies the clean filter and
BOTH spellings return the LF hash — which is how attempt 1 came to publish a
number that reproduced nowhere.

### T8g — what the sweep touches

```bash
cd "$WT" && python - <<'EOF'
import io, sys, collections
sys.path.insert(0, 'scripts/stack')
import stack
calls, gets = [], []
def cap(cmd, cwd):
    calls.append(list(cmd)); return stack.subprocess_capture(cmd, cwd)
def http(url, timeout=8):
    gets.append(url); return stack.urllib_get(url, timeout)
code = stack.main(["--root", ".", "health"], stdout=io.StringIO(), capture=cap, http=http)
execs = [c[2] for c in calls if c[:2] == ["docker","exec"]]
print("exit:", code, "| execs:", len(execs), sorted(collections.Counter(execs).items()))
print("HTTP GETs:", len(gets))
EOF
```
**Expected:** `execs: 7` including **two** on `llama-cpp-upstream`, `HTTP GETs: 7`,
matching the list in `scripts/stack/README.md`.

---

## T1 — the inventory is complete and its dry-run column reproduces

**Re-derive the name list yourself** (do not trust the table's row set):

```bash
cd "$WT"
grep -rnoE '[A-Za-z0-9_./\\-]+\.(ps1|py|sh|bat|cmd)' \
  documentation/runbooks/ CLAUDE.md README.md \
  .claude/skills/stack-map/references/workspace-stacks.md \
  scripts/checks/stack-watchdog.ps1 $(git ls-files '*README.md') \
  | sed 's/.*://' | sort -u
```

Filter to the recovery/backup/restore family and compare against the table in
findings §1. **Expected:** every such script appears as a row. A named script
absent from the table FAILS.

**Then re-run the parse column:**

```powershell
# .ps1
[scriptblock]::Create((Get-Content -Raw <file>))
# .py
python -W error::SyntaxWarning -c "import py_compile,sys; py_compile.compile(sys.argv[1],doraise=True)" <file>
```
```bash
sh -n <file>     # .sh, under Git Bash
```

**Expected:** 41/41 exist; every `.ps1` and `.py` parses; every `.sh` passes
`sh -n`. **Disproves:** any parse failure, or a file named in a doc that is not
on disk.

**Note:** running `sh -n` from the PowerShell tool gives a FALSE PASS — `sh` is
not on PowerShell's PATH, so `$LASTEXITCODE` goes stale. Use Git Bash.

**Reproduce two dry runs** (both safe, both from `$WT`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\backup\restore-from-snapshot.ps1" `
  -SnapshotRoot "$MAIN\backups" -Date 2026-09-20 -Services little-coder
```
**Expected:** `Mode : PLAN ONLY`, then five `[FOUND] little-coder : …` lines
mapping one archive to `coder_little-coder-{journals,skill,cohorts,polyglot,sessions}`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\checks\check-watchdog-repair-targets.ps1" -SkipDocker
```
**Expected:** `REPAIR TARGETS OK: 24 container(s)`, exit 0.

### T1b — the corrected doc paths resolve

```bash
cd "$MAIN"        # NOT the worktree: one plan-store link is relative to the repo's parent
python - <<'EOF'
import io, os, re, glob
for path in glob.glob("documentation/runbooks/*.md"):
    t = io.open(path, encoding="utf-8").read()
    for m in re.finditer(r"\]\((\.\.?[^)\s]*\.(?:ps1|py|sh|bat|md))\)", t):
        tgt = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
        if not os.path.exists(tgt): print("HREF", path, m.group(1))
    for m in re.finditer(r"\.\\(?:[A-Za-z0-9_.\-]+\\)*[A-Za-z0-9_.\-]+\.(?:ps1|py|bat)", t):
        tgt = m.group(0)[2:].replace("\\", "/")
        if not os.path.exists(tgt): print("CMD ", path, m.group(0))
EOF
```
**Expected:** no output. **Disproves:** any line printed.

### T1c — the killswitch dry run works (C3)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\portal\breach-killswitch.ps1" -DryRun
```
**Expected:** all five steps print, each prefixed `[DRY RUN]`; nothing stops.
**To see the bug it fixes:** `git -C "$WT" stash` is overkill — instead run
`powershell -NoProfile -Command "Get-Date -AsUTC"` and confirm it errors with
`A parameter cannot be found that matches parameter name 'AsUTC'` on this host's
PowerShell 5.1. That error was the killswitch's first statement.
**Disproves:** the dry run throwing, or any container stopping.

### T1d — `status_check.py` compiles warning-free (C4)

```bash
cd "$WT" && python -W error::SyntaxWarning -c \
  "import py_compile; py_compile.compile('scripts/recovery/status_check.py', doraise=True); print('clean')"
```
**Expected:** `clean`. **Disproves:** a `SyntaxWarning` about `\s`.

---

## T2 — the freshness guard goes red on a planted empty DATA_DIR

**Lease:** none for T2/T2a/T2c–e (read-only). **T2b takes no plane lease** — it
creates its own private network and container and touches no plane — but it DOES
start a container, so label it.

### T2a — GREEN on the live host (C9)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\checks\check-backup-freshness.ps1" -RepoRoot "$MAIN"
```
**Expected:** `all fresh — ok=16 skipped=0 (none)`, `Freshness: CLEAN`, **exit 0**.
This is the anchor's "exit 0 on the live host once lm-models has produced an
artifact after its repair".

Also confirm the refusal path (the check reads HOST state, not repo state):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\checks\check-backup-freshness.ps1"
```
**Expected:** `REFUSED: no backups directory at <worktree>\backups …`, **exit 2**
— not a false green and not a wall of false stale rows.

### T2b — RED against a planted empty DATA_DIR (C5) — the acceptance case

Build a labelled sidecar that mounts an empty directory the way the broken
lm-models sidecar did, on a private network, and point the checker at a scratch
root so no live artifact masks the result.

```powershell
$id   = 'wt-sl-recovery-backups'         # use YOUR tester id
$root = "$env:TEMP\freshness-red"
New-Item -ItemType Directory -Force "$root\backups\lm-models" | Out-Null
New-Item -ItemType Directory -Force "$root\logs" | Out-Null
New-Item -ItemType Directory -Force "$root\empty-data" | Out-Null

docker network create "redprobe-$id" | Out-Null
docker run -d --name lm-models-backup-red --label "ai-stack.harness.owner=$id" `
  --network "redprobe-$id" `
  -v "${root}\empty-data:/data:ro" -v "${root}\backups\lm-models:/backups" `
  -e DATA_DIR=/data -e BACKUP_DIR=/backups -e PREFIX=lm-models `
  -v "$WT\backup\generic-tar-backup.sh:/scripts/backup.sh:ro" `
  alpine:3.21 sh -c "while true; do sh /scripts/backup.sh; sleep 3600; done"

Start-Sleep -Seconds 3
docker logs lm-models-backup-red        # expect: lm-models PRECHECK SKIP: /data is empty
```

Now point the checker's expectation at that container name. The shipped
`_EXPECTED` names `lm-models-backup`, so either rename the test container to
`lm-models-backup` (**do not** — that name is taken by the live sidecar) or drive
`evaluate()` directly, which is what the unit tests do:

```bash
cd "$WT" && python - <<'EOF'
import sys, os
sys.path.insert(0, 'scripts/sysadmin-mcp')
import check_backups as cb
root = os.path.join(os.environ['TEMP'], 'freshness-red')
cb._REPO_ROOT, cb._BACKUPS = root, os.path.join(root, 'backups')
cb._EXPECTED = [("lm-models", "lm-models-backup-red", 204)]
cb._running_containers = lambda: {"lm-models-backup-red"}
res = cb.evaluate()
print(cb.build_message(res))
print("STALE:", [s['name'] for s in res['stale']])
EOF
```
**Expected:** the `lm-models` row is stale, carries ``MOUNT `/data` ``, and names
`lm-models-backup-red` as producing nothing. `check-backup-freshness.ps1` over
the same root exits **1**.
**Disproves this criterion:** a green result, or a row that reports only an age
with no mount named.

**Teardown (required):**
```powershell
docker rm -f lm-models-backup-red; docker network rm "redprobe-$id"
Remove-Item -Recurse -Force "$env:TEMP\freshness-red"
```

### T2c — a transient probe skip is a reason, not a failure (C6)

```bash
cd "$WT" && python -c "
import sys; sys.path.insert(0,'scripts/sysadmin-mcp'); import check_backups as cb
s = cb.precheck_skip('x', '[2026-09-20T07:29:00Z] mnemory PRECHECK SKIP: mnemory-cloud-gateway:8060 unreachable -- service unhealthy or down')
print(s)
assert s['mount'] is None
print('no mount -> reason only, not a standalone failure')"
```
**Expected:** `mount` is `None`. **Disproves:** a mount path extracted from an
`unreachable` reason, which would make every brief outage a hard backup failure.

### T2d — the superseded-skip rule, against the LIVE host (C7)

```bash
docker logs --tail 60 ao-worker-1-journals-backup 2>&1 | grep 'PRECHECK SKIP'
```
**Expected:** a `/data is empty` line dated 2026-08-29 — i.e. the marker IS in
the tail.

```bash
cd "$WT" && python -c "
import sys; sys.path.insert(0,'scripts/sysadmin-mcp'); import check_backups as cb
print('skip:', cb.precheck_skip('ao-worker-1-journals-backup'))"
```
**Expected:** `{}` — because later successful `tar ->` lines supersede it.
**Disproves this criterion:** a non-empty result, which would report a healthy
sidecar as producing nothing (the false positive this design exists to avoid).

### T2e — the two exit contracts (C8)

```bash
cd "$MAIN"
python scripts/sysadmin-mcp/check_backups.py --check ; echo "check=$?"   # 0 while healthy
python scripts/sysadmin-mcp/check_backups.py --dry   ; echo "dry=$?"     # 0
```
**Expected:** both 0 on a healthy host. Re-run `--check` against the T2b scratch
root to see it return 1 while `--dry` returns 0 on the same input.

### T2f — unit tests

```bash
cd "$WT" && python scripts/sysadmin-mcp/test_check_backups.py
```
**Expected:** `27 passed, 0 failed`, exit 0.

---

## T3 — the watchdog's backup section

### T3b — the two list sizes (C24)

```bash
cd "$WT"
grep -c "Dir = '" scripts/checks/stack-watchdog.ps1          # expect 13
python -c "
import sys; sys.path.insert(0,'scripts/sysadmin-mcp'); import check_backups as cb
print(len(cb._EXPECTED))"                                     # expect 15
```
**Expected:** 13 and 15; the difference is `ao-worker-1-journals` and
`ao-worker-2-journals`. **Disproves:** the watchdog comment now saying 13 while
the list is some other number.

### T3c — RED and GREEN on `Test-BackupRecency` in isolation (C5)

Do **not** run `stack-watchdog.ps1 -Mode check` (it repairs). Extract the three
pieces and drive them:

```powershell
$wt = $WT
$lines = Get-Content "$wt\scripts\checks\stack-watchdog.ps1"
function Slice($pattern){
  $s = ($lines | Select-String -Pattern $pattern | Select-Object -First 1).LineNumber - 1
  $e = $s; while ($lines[$e] -ne '}' -and $lines[$e] -ne ')') { $e++ }
  $lines[$s..$e]
}
# GREEN: real log reader, live artifacts
$g = @('$PROJECT_DIR = "' + $MAIN + '"',
       'function Write-LogEntry { param($Message,$Level="INFO") Write-Host "  [$Level] $Message" }') +
     (Slice '^\$ExpectedBackupRecency') + (Slice '^function Get-BackupSkipReason') + (Slice '^function Test-BackupRecency')
$g | Set-Content "$env:TEMP\tbr_green.ps1" -Encoding ASCII
. "$env:TEMP\tbr_green.ps1"; @(Test-BackupRecency) | Select-Object -Last 1
```
**Expected:** `backup recency OK (13 dirs checked)` and `True`.

**READ THIS BEFORE THE RED CASE — attempt 1's plan got it wrong and the tester
caught it.** `Test-BackupRecency`'s stale branch is not inert: it posts to
Mattermost via `scripts/notify-mattermost.sh` and writes
`logs\.backup-recency-alert` under `$PROJECT_DIR` — a **12-hour suppression
sentinel**. Pointing `$PROJECT_DIR` at the live main checkout for the RED case,
as attempt 1's plan did, pages the operator with a **false** STALE and leaves
that sentinel in the live tree, where it will swallow the next REAL backup alert
for twelve hours. So the RED case uses a **scratch `$PROJECT_DIR`**. The notifier is not stubbed
and does not need to be: the alerting branch builds its script path as
`$PROJECT_DIR/scripts/notify-mattermost.sh`, the scratch root has no `scripts/`
directory, and the call is inside a `try/catch` — so the post cannot fire, and
the sentinel lands in the scratch tree. Only the GREEN case above may point at
`$MAIN`, because its stale set is empty and the alerting branch is never reached.

**Verified while writing this plan:** with the scratch root, `Test-Path
"$MAIN\logs\.backup-recency-alert"` stayed **False** and the sentinel appeared
under `$scratch\logs` instead.

```powershell
# RED: scratch PROJECT_DIR (NOT $MAIN), reader stubbed to the lm-models shape.
# lm-models' real artifacts are FRESH, so a pass here would prove the mount
# branch is dead code.
#
# The header lines are NEWLINE-separated inside @( ), not comma-separated: with
# commas, `+` binds tighter than `,` and the whole array collapses into ONE
# space-joined string that PowerShell then refuses to parse. (Measured while
# writing this; the GREEN block above has the same shape for the same reason.)
$scratch = "$env:TEMP\tbr-red-root"
New-Item -ItemType Directory -Force "$scratch\logs" | Out-Null
# All 13 dirs, so the ONLY stale row is the one under test. With just lm-models
# you get twelve "backup dir missing" lines drowning the signal.
foreach ($d in 'agent-bridge-db','authelia','caddy','little-coder','llm-gateway',
                'lm-models','mattermost-db','mnemory','open-notebook',
                'openbrain-db','openbrain-wiki','openwebui','tailscale') {
  New-Item -ItemType Directory -Force "$scratch\backups\$d" | Out-Null
  Set-Content "$scratch\backups\$d\$d-fresh.tar.gz" 'x'      # fresh artifact
}
$hdr = @(
  ('$PROJECT_DIR = "' + $scratch + '"')
  'function Write-LogEntry { param($Message,$Level="INFO") Write-Host "  [$Level] $Message" }'
  'function Get-BackupSkipReason { param([string]$Container) if($Container -eq "lm-models-backup"){return "/data is empty"} return "" }'
)
$r = $hdr + (Slice '^\$ExpectedBackupRecency') + (Slice '^function Test-BackupRecency')
$r | Set-Content "$env:TEMP\tbr_red.ps1" -Encoding ASCII
. "$env:TEMP\tbr_red.ps1"; @(Test-BackupRecency) | Select-Object -Last 1
```
**Expected:** exactly ONE stale row -
`[ERROR] BACKUP STALE - lm-models: MOUNT /data is empty or absent inside
lm-models-backup - it is producing nothing (PRECHECK SKIP: /data is empty)` -
and `False`.
**Disproves:** `True`, or a message that reports an age instead of the mount.

**Confirm nothing leaked into the live tree, and clean up:**
```powershell
Test-Path "$MAIN\logs\.backup-recency-alert"   # MUST be False
Get-ChildItem "$scratch\logs"                  # the sentinel, if any, is HERE
Remove-Item -Recurse -Force $scratch, "$env:TEMP\tbr_red.ps1", "$env:TEMP\tbr_green.ps1"
```
(The scratch root has no `scripts\notify-mattermost.sh`, so the notifier path
cannot fire even if the stub is dropped — belt and braces.)

---

## T4 — `wiki-viewer-srv` (C10, C11, C12)

```powershell
docker volume ls --format '{{.Name}}' | Select-String 'wiki-viewer'
docker run --rm --network none -v open-brain_wiki-viewer-srv:/v:ro alpine `
  sh -c 'ls -la /v; du -sh /v'
docker inspect openbrain-wiki-viewer --format '{{json .Mounts}}' | ConvertFrom-Json |
  Format-Table Type,Name,Destination,RW -AutoSize
```
**Expected:** ~7.6 G, `build-<n>` trees plus `current -> /srv/build-<n>`, mounted
rw at `/srv` in `openbrain-wiki-viewer`.

**Note:** a bare, unlabelled, EMPTY `wiki-viewer-srv` volume may also be listed —
it was created accidentally by a path-mangled command during development and is
recorded in findings §4. It is not the real one.

### T4b — the restore path is real (C11)

Read `backups/wiki-viewer/RESTORE.md` and confirm: the "What this does NOT cover
(by design)" section names `/srv` as a regenerable cache; the folder holds two
`docker save` tars plus sha256 sentinels; paths A/B/C describe retag, `docker
load`, and rebuild. Verify a sentinel without loading anything:

```bash
cd "$MAIN/backups/wiki-viewer" && sha256sum -c wiki-viewer-image-20260828T192500Z.tar.gz.sha256
```
**Expected:** `OK`. **Disproves this criterion:** an exclusion with no restore
path, or a failing sentinel.

### T4c — the gate

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$WT\scripts\checks\check-backup-coverage.ps1"
```
**Expected:** `[SKIP] wiki-viewer-srv - excluded: …`, `Coverage: CLEAN`, **exit 0**.
Run it from `$MAIN`'s copy after the merge too, where the `./backups/*` WARN
disappears.

---

## T5 — the two `emergency-recovery.ps1` comments (C13, C14, C15)

Render OB1 yourself. `COMPOSE_PROFILES` must be set to a value no service
declares — an EMPTY string lets `OB1/docker/.env` win and you will measure 30 for
the bare case.

```powershell
$c = "$WT\OB1\docker\docker-compose.yml"
$env:COMPOSE_PROFILES = '__none__'
$bare = @((docker compose -f $c config --services) | Where-Object {$_ -match '\S'})
$ri   = @((docker compose -f $c --profile research --profile idea-refinery config --services) | Where-Object {$_ -match '\S'})
$all  = @((docker compose -f $c --profile research --profile wiki --profile notebook --profile idea-refinery config --services) | Where-Object {$_ -match '\S'})
"bare=$($bare.Count) research+idea=$($ri.Count) all=$($all.Count)"
"gated=$((@($all | Where-Object { $bare -notcontains $_ })).Count)"
"wiki+notebook pair=$((@($all | Where-Object { $ri -notcontains $_ })).Count)"
```
**Expected:** `bare=20 research+idea=23 all=30`, `gated=10`, `wiki+notebook pair=7`.

### T5b — how many of the ten hold anchor endpoints (C14)

```powershell
$cfg = (docker compose -f $c --profile research --profile wiki --profile notebook --profile idea-refinery config --format json | Out-String | ConvertFrom-Json)
$netmap = @{}; foreach($p in $cfg.networks.PSObject.Properties){ $n=$p.Value.name; if(-not $n){$n=$p.Name}; $netmap[$p.Name]=$n }
$gated = @($all | Where-Object { $bare -notcontains $_ })
$hold = @($gated | Where-Object {
  $nets = @($cfg.services.$_.networks.PSObject.Properties.Name)
  @($nets | ForEach-Object { $netmap[$_] } | Where-Object { $_ -like 'ai-stack_*' }).Count -gt 0 })
"hold=$($hold.Count) of $($gated.Count)"; $hold -join ', '
```
**Expected:** `hold=7 of 10`; the three that do not are `surrealdb`,
`open-notebook-backup`, `openbrain-wiki-backup`.
**Disproves:** any other count — then the corrected comments are wrong too.

### T5c — nothing else changed in that script (C15)

```bash
git -C "$WT" diff development -- scripts/recovery/emergency-recovery.ps1
```
**Expected:** exactly two hunks, both inside comment blocks
(`Reset-OB1Stack`'s "leaves … down after a recreate" and `Invoke-NuclearRecovery`'s
"removes 20 of 30"). **Disproves:** any change to an executable line.
Also confirm the header comment at `$Script:OB1Profiles` ("seven of the ten gated
OB1 containers hold endpoints") is UNCHANGED — it was already correct.

---

## T6 — smolcrawl archived, nothing deleted (C16)

```powershell
Get-ChildItem "$MAIN\backups\archive\smolcrawl" |
  ForEach-Object { "{0} {1} {2}" -f $_.Name,$_.Length,(Get-FileHash $_.FullName -Algorithm SHA256).Hash.Substring(0,16) }
Test-Path "$MAIN\backups\smolcrawl"
```
**Expected:** four artifacts plus `ARCHIVED.md`; the four hashes match the table
in `ARCHIVED.md`; the old directory is gone.

```bash
cd "$WT" && grep -rn "smolcrawl" scripts/sysadmin-mcp/check_backups.py scripts/checks/stack-watchdog.ps1
```
**Expected:** no output — it was never in either expectation list, so nothing had
to be dropped. **Disproves:** a missing artifact, a changed hash, or an
expectation row still naming smolcrawl.

---

## T7 — the tracked file Authelia rewrites (C17, C18)

### T7a — the role

```bash
cd "$WT" && grep -n -B3 "healthcheck.env" portal/docker-compose.yml
```
**Expected:** a FILE bind `./config/authelia/.healthcheck.env:/app/.healthcheck.env`
and the comment saying Authelia writes it during startup and the host file is a
placeholder. This is why untracking it is wrong: a fresh clone would have no
file and Docker would create a DIRECTORY in its place.

### T7b — the mechanism, in a scratch clone (C18)

```bash
cd "$env:TEMP" && rm -rf attr-test && git clone --no-local --depth 1 --branch work/sl-recovery-backups "$MAIN" attr-test
cd attr-test
git check-attr -a -- portal/config/authelia/.healthcheck.env          # expect: text: set / eol: lf
od -c portal/config/authelia/.healthcheck.env | head -2                # expect \n, no \r
# simulate Authelia: rewrite with LF
python -c "p='portal/config/authelia/.healthcheck.env'; d=open(p,'rb').read().replace(b'\r\n',b'\n'); open(p,'wb').write(d)"
git status --porcelain -- portal/config/authelia/
```
**Expected:** `eol: lf`, LF bytes on checkout, and **no output** from the final
`git status`. Repeat the rewrite twice more — still clean.
**Disproves:** ` M` after a rewrite.

**Landing (ORCHESTRATOR, under the portal lease — not the tester):** after
merging, run once in `$MAIN`:
```powershell
git -C "$MAIN" update-index --refresh -- portal/config/authelia/.healthcheck.env
```
then `scripts\portal\portal-on.ps1`, `scripts\portal\portal-off.ps1`, and confirm
`git -C "$MAIN" status` shows nothing under `portal/config/authelia/`. The
refresh is needed because the merge may not rewrite the file (its content does
not change), leaving one stale stat entry behind.

---

## T8 — the inference serving-depth probe

### T8 — GREEN on the live host (C19)

```powershell
Push-Location "$WT"; python scripts\stack\stack.py health; "EXIT=$LASTEXITCODE"; Pop-Location
```
**Expected:** 16 probe lines, all `[OK]`, `ALL HEALTH PROBES PASSED`, exit 0, and
the line `inference: serving depth: <model> resident on llama-cpp-upstream
(/running)` naming whatever is loaded.

Cross-check it is not lying:
```powershell
docker exec llama-cpp-upstream curl -s --max-time 10 http://localhost:8080/running
docker exec llama-cpp-upstream sh -c "find /models -maxdepth 4 -name '*.gguf' | wc -l"
```
**Expected:** the same model name; a non-zero count (19 on this host 2026-09-21).

### T8b — RED against a labelled test upstream with an empty models dir (C20) — the acceptance case

**Lease:** none needed (nothing live is touched), but the container MUST be
labelled and on a private network, and the image MUST be tagged `:wt-<id>`.

```powershell
$id = 'wt-sl-recovery-backups'                         # use YOUR tester id
docker network create "depth-$id" | Out-Null
docker tag ghcr.io/mostlygeek/llama-swap:cuda "llama-swap:$id"
New-Item -ItemType Directory -Force "$env:TEMP\empty-models" | Out-Null
docker run -d --name "llama-cpp-upstream-$id" --label "ai-stack.harness.owner=$id" `
  --network "depth-$id" --entrypoint sh "llama-swap:$id" -c "sleep 3600"
docker exec "llama-cpp-upstream-$id" sh -c "mkdir -p /models; find /models -maxdepth 4 -name '*.gguf' | wc -l"
```
**Expected:** `0` — the store is empty, exactly the 2026-09-19 shape.

Now point the probe at it. The container name is a constant in the probe, so
drive `HealthSweep` with a capture that rewrites the name:

```bash
cd "$WT" && python - <<'EOF'
import io, subprocess, sys
sys.path.insert(0, 'scripts/stack')
import stack
TEST = "llama-cpp-upstream-wt-sl-recovery-backups"   # YOUR test container
def capture(cmd, cwd):
    cmd = [TEST if a == "llama-cpp-upstream" else a for a in cmd]
    return stack.subprocess_capture(cmd, cwd)
sweep = stack.HealthSweep(stack.Console(io.StringIO()), stack.Path("."), capture, stack.urllib_get)
label, ok = sweep.inference_serving_depth()
print("OK?", ok); print(label)
assert not ok, "A PROBE THAT PASSES ON AN EMPTY MOUNT FAILS THIS CRITERION"
EOF
```
**Expected:** `OK? False` and a label containing `holds NO .gguf files` **and the
host bind path**. **Disproves this criterion:** `OK? True`.

**Teardown (required):**
```powershell
docker rm -f "llama-cpp-upstream-$id"; docker network rm "depth-$id"
docker rmi "llama-swap:$id"; Remove-Item -Recurse -Force "$env:TEMP\empty-models"
```

### T8c — the empty mount short-circuits before any completion (C20)

```bash
cd "$WT" && python -m pytest scripts/stack -q -k "empty_models_mount"
```
**Expected:** passes — the test asserts no `_LANDING_COMPLETION` call was made.
A probe that spends a 600 s timeout against a provably empty store is a probe
that pages the operator with the wrong sentence.

### T8d — the completion branch (C21)

Unit (no docker):
```bash
cd "$WT" && python -m pytest scripts/stack -q -k "nothing_resident or landing_completion"
```
**Expected:** passes, including "ONE completion, not one per retry" and
`docker exec llm-gateway` (never the upstream).

Live (safe — a 3-token request against the already-resident model):
```bash
cd "$WT" && python -c "
import sys, subprocess; sys.path.insert(0,'scripts/stack'); import stack
r = subprocess.run(['docker','exec','llm-gateway','python','-c',stack._LANDING_COMPLETION],
                   capture_output=True, text=True)
print(r.stdout.strip())"
```
**Expected:** `OK a 3-token completion through the gateway returned 200 in <n>s
(<model> loaded)`. **Disproves:** a `FAIL …` line while chat works in OWUI.

### T8e — the key never leaks (C22)

```bash
cd "$WT" && python -m pytest scripts/stack -q -k "never_prints_the_key or caller_key"
grep -n "LITELLM_MASTER_KEY" scripts/stack/stack.py
```
**Expected:** the tests pass; `stack.py` reads `inference/.env` only for a
presence check and the value appears in no `docker` argv. Confirm by eye that the
in-container script uses `os.environ.get("LITELLM_MASTER_KEY")`.
**Disproves:** the key value in `host.calls`, or a `-e KEY=…` on the exec.

### T8f — probe count and exit contract (C23)

```bash
cd "$WT" && python -m pytest scripts/stack -q
grep -c '"' scripts/stack/test_stack.py   # not the check; use the next one
python -c "
import re,io
t = io.open('scripts/stack/test_stack.py', encoding='utf-8').read()
body = t.split('PS1_PROBES = [',1)[1].split(']',1)[0]
print('PS1_PROBES entries:', len([l for l in body.splitlines() if l.strip().startswith('\"')]))"
```
**Expected:** `114 passed`; `PS1_PROBES entries: 16`. The exit-code contract is
pinned by `test_health_exit_code_is_the_number_of_failed_probes` (3 broken things
→ exit 3) and by T8b's exit 1.

---

## T9 — the alerter finding is still true (C25)

```powershell
docker exec portal-alerter wget -q -O- --post-data='{"severity":"info","event":"selftest","timestamp_utc":"2026-09-21T00:00:00Z","log_line":"tester probe"}' `
  --header='Content-Type: application/json' http://127.0.0.1:8080/alert; "EXIT=$LASTEXITCODE"
```
**Expected:** a non-zero exit / HTTP 500, matching findings §3. This is an
OBSERVATION, not something this item fixes — if it now returns 200, say so and
the finding is stale.

---

## T10 — the probe count, everywhere (C26)

Attempt 1 deferred this to `sl-docs-posture`; that item merged keeping "15", so
this item owns all of it now.

```bash
cd "$WT" && grep -rniE "15[- ](functional[- ])?probe|fifteen (functional )?probe"   --include=*.md --include=*.py --include=*.ps1 .   | grep -v '\.claude/worktrees' | grep -v documentation/evidence
```
**Expected:** every surviving hit is HISTORICALLY correct — a sentence saying the
other fifteen probes were green through the outage. **Nothing claiming the sweep
IS fifteen.** Then confirm each of the eight sentences in findings §10.3 says
sixteen, and that `scripts/stack/README.md` carries a real ENTRY for the
sixteenth probe — naming what it checks and what fails it — not just a changed
digit.

```bash
cd "$WT" && sed -n '/^#### The sixteenth/,/^#### What the sweep touches/p' scripts/stack/README.md
```
**Expected:** the entry names the `.gguf` census, `/running`, the one completion,
and a "What fails it" sentence covering all four failure modes.
**Disproves:** any document still asserting a count of fifteen, or a list entry
that says only that a sixteenth probe exists.
---

## Gates

```bash
cd "$WT"
ruff check .
python -m pytest scripts/stack -q
python scripts/sysadmin-mcp/test_check_backups.py
python scripts/stack/stack.py inventory --check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/validate-lineendings.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-hook-attestation.ps1 \
  -Branch work/sl-recovery-backups -Base development
```
**Expected:** all green; `114 passed`; `32 passed, 0 failed`; attestation `[OK]`
on all commits. (`check-hook-attestation.ps1` takes mandatory `-Branch`/`-Base`;
without them it exits 1 on a binding error, which is not a verdict.)

### G1 — CONTROL CHARACTERS in added lines (the attempt-1 failure)

Attempt 1 shipped four BACKSPACE bytes (`0x08`) from a `\b` in a non-raw
replacement string — one of them on an executable `Write-Host` that told the
operator to run `.\scriptackup\install-nas-backup-task.ps1`. **Every gate passed:**
the `.ps1` parse gate treats `0x08` as whitespace, the line-ending check looks
only at CR/LF, and the encoding sweep below tests `byte > 127` — and `0x08` is 8.

The rule now lives in **`scripts/checks/check-project-configs.ps1`, gate 4**, so
it runs pre-commit on every commit. Verify it both ways:

```bash
# GREEN — the tree as shipped
cd "$WT"
python - <<'EOF'
import subprocess, os
files = subprocess.run(["git","diff","--name-only","development"],
                       capture_output=True, text=True).stdout.split()
bad = []
for f in files:
    if not os.path.isfile(f): continue
    d = open(f, "rb").read()
    for i, c in enumerate(d):
        if c < 0x20 and c not in (0x09, 0x0a, 0x0d):
            bad.append((f, d[:i].count(b"\n") + 1, hex(c)))
print("control characters in changed files:", len(bad))
for b in bad: print("  ", b)
EOF
```
**Expected:** `0`. **Disproves:** any hit — the attempt-1 bug, or a new one.

```powershell
# RED — plant the exact shipped bug and watch the pre-commit gate refuse it
cd $WT
python -c "open('scripts/checks/_ctrl_red.ps1','wb').write(rb'# .\scripts' + b'\x08' + rb'ackup\x.ps1' + b'\r\n')"
git add scripts/checks/_ctrl_red.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
"exit = $LASTEXITCODE"
git rm -q --cached scripts\checks\_ctrl_red.ps1; Remove-Item scripts\checks\_ctrl_red.ps1
```
**Expected:** `[configs] CONTROL CHARACTER in staged added line(s):` naming the
file and `0x08`, **exit 1** — and note it ALSO prints `parse clean` for the same
file, which is the point. **Disproves this gate:** exit 0.

Also confirm the four originals are actually repaired, rendered rather than
merely present:
```bash
cd "$WT"
sed -n '48p' scripts/backup/backup-to-nas.ps1 | cat -v
sed -n '27p' scripts/backup/install-nas-backup-task.ps1 | cat -v
sed -n '19p;139p' scripts/backup/set-nas-credential.ps1 | cat -v
```
**Expected:** every one reads `.\scripts\backup\…` with no `^H`, and
`scripts\backup\install-nas-backup-task.ps1` / `scripts\backup\set-nas-credential.ps1`
both exist on disk.

### G2 — BOM / non-ASCII, stated so it reproduces

Attempt 1's expectation here was `0` and the real answer is **6**: six added
`.ps1` first lines carry their file's **pre-existing** BOM because the header
comment on that line changed. The substantive claim is that BOM state does not
change and no new non-ASCII content appears, so check exactly that:

```bash
cd "$WT"
# (a) non-ASCII CONTENT, discounting a leading BOM on the line
git diff development -- '*.ps1' | grep '^+' | grep -v '^+++' | python -c "
import sys
bom = b'+\xef\xbb\xbf'
bad = []
for l in sys.stdin.buffer:
    body = b'+' + l[len(bom):] if l.startswith(bom) else l
    if any(c > 127 for c in body): bad.append(body)
print('added .ps1 lines with non-ASCII CONTENT:', len(bad))
for l in bad[:5]: print('   ', l[:80])"

# (b) BOM state per changed .ps1, base vs tip
for f in $(git diff --name-only development -- '*.ps1'); do
  b=$(git show development:$f 2>/dev/null | head -c3 | od -An -tx1 | tr -d ' \n')
  t=$(head -c3 "$f" | od -An -tx1 | tr -d ' \n')
  printf "%-46s base=%-6s tip=%-6s %s\n" "$f" "${b:0:6}" "${t:0:6}" \
    "$([ "$b" = "$t" ] && echo SAME || echo CHANGED)"
done
```
**Expected:** (a) `0`. (b) `SAME` on all eight pre-existing BOM'd files
(`efbbbf` -> `efbbbf`), `SAME` on `check-project-configs.ps1` (never had one),
and the only `CHANGED` is the NEW `check-backup-freshness.ps1`, which has no BOM
at all. **Disproves:** a BOM added or removed from a pre-existing file, or any
non-ASCII content in an added line.

The pre-existing BOMs stay on purpose: `stack-watchdog.ps1` (57 non-ASCII chars),
`emergency-recovery.ps1` (936) and `check-backup-coverage.ps1` (9) contain UTF-8,
and PS 5.1 reads a BOM-less file as ANSI — stripping the BOM would mangle them.

### G3 — no stale probe count anywhere

```bash
cd "$WT" && grep -rniE "15[- ](functional[- ])?probe|fifteen (functional )?probe" \
  --include=*.md --include=*.py --include=*.ps1 . \
  | grep -v '\.claude/worktrees' | grep -v documentation/evidence
```
**Expected:** only sentences where "fifteen" is HISTORICALLY correct — i.e. the
ones saying the other fifteen probes were green during the outage. No line
claiming the sweep *is* fifteen. Cross-check the live count:
```bash
cd "$WT" && python scripts/stack/stack.py health 2>&1 | grep -cE '^\s+\[(OK|FAIL)\]'
```
**Expected:** `16`.
