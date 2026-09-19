# sl-driver-parity — test plan

**Item:** `sl-driver-parity` (stack-layers wave 2)
**Branch:** `work/sl-driver-parity`, base `development` @ `3a373e2`
**Attempt:** 8. Attempt 7 PASSED and the reviewer's verdict was FITS; it was
requeued under D18 because `development` moved to `3a373e2`
(`sl-colo-inference`). **Re-run every case** - see *Attempt 8* below for what
moved and for the one class-2 defect fixed in this pass (T13).

Attempt 7 in turn fixed **T9 only** (11/12 at attempt 6): the repair for finding F27
shipped `stack.manifest.toml:330` as a path that does not exist, because an
unanchored suffix replacement prefixed a line that was already correct. That is
the FIRST thing to check in T9, and F-T18 records the rule. Everything else in
attempt 6 passed, and the six other citation repairs were re-derived by the
tester and stand.

Attempt 6 itself was a rebase onto `ae915c3` (`sl-colo-gateways`) after a
reviewer requeued attempt 5 (which passed 12/12) under D18. **Re-run every
case.** *Attempt 6* below lists the four shared files and the expected values
that moved. **T11** covers the `sl-ob1-profiles` seam, **T12** the
`sl-frontend-solo` one; F-T14 is T12d, F-T15 and F-T18 are T9.
**Developer worktree:** `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-driver-parity`
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-driver-parity.json`
**Findings sink:** `documentation/notes/stack-layers-sl-driver-parity-findings.md`

Artifacts under test:

| Path | What |
|---|---|
| `scripts/stack/stack.py` | the driver: new `health`, `stats`, `inventory` verbs; plane/`--all` selection on `status`/`up`/`down`; `effective_profiles()` |
| `scripts/stack/stack.ps1` | reduced to a shim (216 → 92 lines, no registry, no probes) |
| `scripts/stack/test_stack.py` | 38 → 76 hermetic tests |
| `scripts/lib/stack-services.curated.json` | **new** — the hand-owned half of the inventory |
| `scripts/lib/stack-services.json` | now GENERATED from the manifest + the sidecar + the compose renders |
| `scripts/checks/check-project-configs.ps1` | its inline row diff replaced by `stack.py inventory --check` |
| `stack.manifest.toml` | `opt_in` profile flag; `inference.local` no longer `pending`; the three OB1 profiles `sl-ob1-profiles` made real are now `opt_in` |
| `scripts/checks/check-watchdog-repair-targets.ps1` | renders with profiles (declared out-of-artifact-list change — finding F15) |
| `.github/workflows/ci.yml` | new `stack-driver` job |
| `scripts/stack/README.md`, `README.md`, `CLAUDE.md`, `documentation/runbooks/SERVICE-LIFECYCLE.md` | docs |

---

## How to run this

**Everything here is read-only against the live stack.** No case starts, stops,
restarts or recreates a container. The live docker calls are `docker ps`,
`docker network inspect`, **five** read-only `docker exec`s (`llm-gateway`,
`tailscale`, `little-coder`, `openbrain-db`, `agent-bridge`),
`docker compose ... ps` and `docker compose ... config`. **No plane lease is required** (MERGE-PROTOCOL
§1 rule 4: read-only inspection of a running plane is not "touching" it).

**`up`, `down` and `restart` are only ever run with `--dry-run`.** If a case
below tempts you to drop that flag, the case is wrong — stop and say so.

Work from the developer worktree root:

```text
cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-driver-parity"
```

`python` is 3.13.3 on this host; CI is 3.12. Both shells are used: PowerShell 5.1
for `.ps1`, Git Bash where a POSIX one-liner is shorter. Where the two differ,
both forms are given.

### Scratch, OUT OF TREE

Two cases need a scratch directory. Put it in your session scratchpad or any
temp dir **outside the repository** — not under the worktree:

```powershell
# PowerShell 5.1
$SL = Join-Path $env:TEMP "sl-driver-parity-test"
Remove-Item -Recurse -Force $SL -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $SL | Out-Null
```

```bash
# Git Bash
SL="${TMPDIR:-/tmp}/sl-driver-parity-test"; rm -rf "$SL"; mkdir -p "$SL"
```

**Never edit `.env`, `OB1/docker/.env` or `agent-org/docker/.env`.**

### Mutating cases run in a SCRATCH COPY of the branch, never in the worktree

T2, T3b, T3c and T8 each need a modified tree. **Do not write in the
developer's worktree** - build a throwaway copy of the branch instead and work
there. `git ls-files` gives exactly what CI would check out; add the two env
files and the submodule by hand:

```bash
# Git Bash, from the developer worktree (READ-ONLY use of it)
WT="D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-driver-parity"
COPY="$SL/branch"; rm -rf "$COPY"; mkdir -p "$COPY"
cd "$WT" && git ls-files -z | xargs -0 -n 200 cp --parents -t "$COPY"
cp -r "$WT/OB1" "$COPY/"          # the submodule, so all eight projects render
cp "$WT/.env" "$COPY/.env"
cp "$WT/agent-org/docker/.env" "$COPY/agent-org/docker/.env"
cp "$WT/OB1/docker/.env" "$COPY/OB1/docker/.env"
cd "$COPY"
```

Every mutation below happens in `$COPY`. The one exception is T2's base-script
extraction, which needs a real repo root for `$PSScriptRoot` - put it in
`$COPY/scripts/stack/` too, not in the worktree. T10 then checks the worktree
is untouched, which it will be because you never wrote to it.

---

## T1 — the probe set is `stack.ps1`'s fifteen, one for one

*Anchor criterion 1. The case that cannot be automated away: read both lists and
judge them.*

Get the old probe list and the new one. **The old one comes from `9f64b84`, the
branch's original base** — `development` has moved since, and while `sl-closeout`
did not touch `stack.ps1`, pinning the blob is what makes the line numbers in the
table below resolve:

```bash
git show 9f64b84:scripts/stack/stack.ps1 | grep -n 'Probe "'
grep -n 'self.probe(' scripts/stack/stack.py
```

Fill in this table by reading both sides. **A probe missing, merged into a
neighbour, or with a weaker pass condition FAILS the item.**

| # | Probe label | `9f64b84:stack.ps1` | `stack.py` `HealthSweep.run()` | Pass condition must be |
|---|---|---|---|---|
| 1 | `0 unhealthy containers (found: …)` | `:128-129` | `docker ps --filter health=unhealthy` | the name list is empty |
| 2 | `anchor: ai-stack_llm-net exists` | `:132-133` | `docker network inspect … --format {{.Name}}` | stdout is exactly `ai-stack_llm-net` |
| 3 | `inference: llm-gateway liveliness` | `:134-136` | `docker exec llm-gateway python -c …` | exit 0, and the URL is **`/health/liveliness`**, never `/health` |
| 4 | `frontend: OWUI http://127.0.0.1:3000/health` | `:137-138` | urllib GET | HTTP 200 |
| 5 | `frontend: 8 tailnet serve routes` | `:139-141` | `docker exec tailscale sh -c "… \| grep -c 'proxy http'"` | the count is **>= 8** |
| 6 | `frontend: owui/ manifest rows drifted…: <n>` | `:155-171` | `HealthSweep.owui_drift()` | the answer is the string `0`; anything non-numeric is `REFUSED - <why>` and FAILS |
| 7 | `memory: cloud door …:8060/health` | `:172-173` | urllib GET | HTTP 200 |
| 8 | `search: gateway …:8085/healthz` | `:174-175` | urllib GET | HTTP 200 |
| 9 | `search: <verdict> - <n> engine(s) answering` | `:181-191` | `HealthSweep.search_engines()` | not `REFUSED` **and** does not start with `DEGRADED`; `unknown` passes |
| 10 | `coder: little-coder daemon :8090/health` | `:192-194` | `docker exec little-coder curl -fsS --max-time 8` | exit 0 |
| 11 | `OB1: open_notebook API :5055/api/config` | `:195-196` | urllib GET | HTTP 200 |
| 12 | `OB1: ops door :8062/health` | `:197-198` | urllib GET | HTTP 200 |
| 13 | `OB1: research-curator …:8816/health` | `:202-203` | urllib GET | HTTP 200 (a 503 `{"ok":false}` and a crash-loop both FAIL) |
| 14 | `OB1: openbrain-db accepting connections` | `:204-206` | `docker exec openbrain-db pg_isready -U postgres -d openbrain -t 5` | exit 0 |
| 15 | `agent-org: mattermost ping` | `:207-209` | `docker exec agent-bridge python -c …` | exit 0 |

Then confirm the count from both sides:

```bash
git show 9f64b84:scripts/stack/stack.ps1 | grep -c 'Probe "'   # expect 15
grep -c 'self.probe(' scripts/stack/stack.py                   # expect 15
```

**Pass:** fifteen rows, each present on both sides with the same pass condition,
and the two headers (`== container health (all projects)` and
`== functional gates`) in the same places.
**Fail:** any probe only on one side; any pass condition loosened (a `>= 8`
turned into `> 0`, a `== 200` turned into "no exception", a `REFUSED` treated as
anything but FAIL); the exit code no longer being the failed count.

*Also confirm BOTH guards* (in `$COPY`, not the worktree):

1. `PS1_PROBES` in `scripts/stack/test_stack.py` pins the fifteen labels, so a
   dropped or renamed probe fails. Delete one entry, run
   `python -m pytest scripts/stack -q -k probes_stack_ps1_ran` — RED.
2. A pinned name list proves nothing about a **weakened** pass condition, which is
   a different guarantee needing a different test. Attempt 1 had no such test and
   the tester proved it: weakening probe 5 from `>= 8` to `>= 0` left the suite
   green. Repeat that mutation now — change `) >= 8,` to `) >= 0,` in
   `HealthSweep.run()` — and
   `test_a_serve_route_count_below_the_threshold_fails` must go RED. That test
   walks one table - `3` and `7` FAIL, `8` and `9` PASS - asserting the probe's
   VERDICT and the exit code for each. (Attempt 2 was read as containing only
   three of the four cases; it contained all four, and the body is now a single
   table so the count is readable in one glance rather than four consecutive
   asserts.) A green suite under that mutation FAILS the item.

---

## T2 — both drivers report the same pass/fail set against the LIVE stack

*Anchor criterion 2. Read-only; no lease.*

**Run the two within a minute of each other** — they are measuring a running
stack, and a container that restarts between them is a difference in the world,
not in the code.

```powershell
# PowerShell 5.1, from the worktree root
git show 9f64b84:scripts/stack/stack.ps1 | Set-Content -Encoding utf8 scripts\stack\stack.base.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.base.ps1 health > "$SL\base.txt" 2>&1
"base exit=$LASTEXITCODE"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 health > "$SL\shim.txt" 2>&1
"shim exit=$LASTEXITCODE"
Remove-Item scripts\stack\stack.base.ps1
Compare-Object (Get-Content "$SL\base.txt") (Get-Content "$SL\shim.txt")
```

```bash
# Git Bash equivalent
git show 9f64b84:scripts/stack/stack.ps1 > scripts/stack/stack.base.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack.base.ps1 health > "$SL/base.txt" 2>&1; echo "base exit=$?"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack.ps1       health > "$SL/shim.txt" 2>&1; echo "shim exit=$?"
rm -f scripts/stack/stack.base.ps1
diff --strip-trailing-cr "$SL/base.txt" "$SL/shim.txt" && echo IDENTICAL
```

**The base copy must live at `scripts/stack/stack.base.ps1` inside `$COPY`** (a
real repo root, never the developer's worktree) — the old script resolves the repo root from
`$PSScriptRoot`, so a copy in `$SL` would `Set-Location` into your temp
directory and report nothing but "couldn't find env file". T10 checks it is gone.

**Note on line endings:** the driver writes LF, PowerShell's redirection wrote LF
for the base copy on the developer's run — but compare with
`--strip-trailing-cr` / `Compare-Object` on the lines rather than byte-for-byte,
since a CRLF-only difference is not a behaviour difference.

**Developer's run, 2026-09-19:** both printed the same 19 lines, all fifteen
probes `[OK]`, `ALL HEALTH PROBES PASSED`, exit 0 from each.

**What this case can and cannot show.** On a healthy stack every probe is
`[OK]`, so this compares the two drivers on the GREEN path only - it cannot
exercise a FAIL path, and you must not break a plane to make it. The FAIL
shapes are covered by reading (T1's table) and by the hermetic suite
(`test_health_exit_code_is_the_number_of_failed_probes`,
`test_one_dead_plane_costs_its_own_probes_and_not_the_rest_of_the_sweep`,
`test_healthz_alone_cannot_pass_search`,
`test_a_probe_that_throws_is_one_failed_probe_not_a_crash`,
`test_a_serve_route_count_below_the_threshold_fails`). Say in your evidence that
the live comparison was green-path only.

**Pass:** the same `[OK]`/`[FAIL]` verdict for the same probe label on both
sides, the same summary line, and the same exit code.
**Fail:** any probe disagreeing — **unless** you can show the live state changed
between the two runs (name the container and show `docker inspect` timestamps
straddling them). "It was probably a blip" is a FAIL.

---

## T3 — the generated inventory equals the committed one, and a hand edit is caught

*Anchor criterion 3.*

### T3a — green on what is committed

```text
python scripts/stack/stack.py inventory --check
```

**Pass:** exit 0 and one line —
`[OK]   scripts/lib/stack-services.json matches the manifest, the sidecar and the compose renders`.
**Fail:** any `[FAIL]` line; a non-zero exit; **or** a `NOT VERIFIED` line on
this host (OB1 is checked out here, so every one of the eight projects must
render — a skip here would mean the submodule is missing, not that the check
passed).

### T3b — red on one mutated row

This edits a file in the worktree and restores it. Back it up **outside** the
tree first.

```powershell
Copy-Item scripts\lib\stack-services.json "$SL\inv.keep.json"
python -c "import json,pathlib; p=pathlib.Path('scripts/lib/stack-services.json'); d=json.loads(p.read_text(encoding='utf-8')); [r.update(project='memory') for r in d['planes']['core'] if r['container']=='llm-gateway']; p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')"
python scripts\stack\stack.py inventory --check
"exit=$LASTEXITCODE"
Copy-Item "$SL\inv.keep.json" scripts\lib\stack-services.json -Force
python scripts\stack\stack.py inventory --check
"restored exit=$LASTEXITCODE"
```

**Pass:** the mutated run exits **non-zero** and prints a line naming the row —
`planes.core[llm-gateway]: committed {…"project": "memory"…} != generated
{…"project": "inference"…}` — and the restored run exits 0.
**Fail:** a zero exit on the mutated file (this is the criterion's explicit
"a zero exit FAILS"); a failure message that does not name which row differs.

### T3c — the generator cannot invent a row, and cannot hide a missing one

```text
python -c "import json,pathlib; p=pathlib.Path('scripts/lib/stack-services.curated.json'); d=json.loads(p.read_text(encoding='utf-8')); d['planes']['openbrain']=[r for r in d['planes']['openbrain'] if r['container']!='openbrain-curator']; p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')"
python scripts/stack/stack.py inventory --write
```

**Pass:** exit non-zero, a line
`MISSING from scripts/lib/stack-services.curated.json: openbrain-curator (project open-brain)`,
and **`scripts/lib/stack-services.json` unchanged on disk** (`git diff --stat`
shows nothing for it) — `--write` refuses rather than guessing which plane group
a container joins.
Restore the sidecar with `git checkout -- scripts/lib/stack-services.curated.json`
(it is committed by then) and re-run T3a.

### T3d — how the sidecar was derived, and what actually changed

The sidecar was extracted mechanically from the committed inventory: every
`critical` / `host_health` / `stale_pool_guard` / `note`, the plane grouping and
the row order, copied verbatim; the `projects` map reduced to `plane` + `note` +
`profiles`, since everything else about a project now comes from the manifest.
Confirm the content delta is only what the developer claims:

```bash
git show development:scripts/lib/stack-services.json > "$SL/before.json"
python - <<'EOF'
import json, os
before = json.load(open(os.environ['SL'] + '/before.json', encoding='utf-8'))
after = json.load(open('scripts/lib/stack-services.json', encoding='utf-8'))
print('projects identical:', before['projects'] == after['projects'])
b = {r['container']: r for g in before['planes'].values() for r in g}
a = {r['container']: r for g in after['planes'].values() for r in g}
print('rows before/after:', len(b), len(a))
for c in sorted(set(b) | set(a)):
    if b.get(c) != a.get(c):
        print('DIFF', c); print('  before', json.dumps(b.get(c))); print('  after ', json.dumps(a.get(c)))
EOF
```

**Pass:** `projects identical: True`, 68 rows before / 69 after, and **exactly
two** `DIFF` lines:

1. `openbrain-idea-refinery` — absent before, added. It is a real running
   container (`docker ps --filter name=openbrain-idea-refinery`) that the old
   verifier could not see because it rendered without profiles; finding F2.
2. `searxng` — the redundant `"service": "searxng"` (identical to the container
   name) dropped; finding F5.

Plus the `_comment` header, rewritten to say the file is generated.
**Fail:** any third difference; any row whose `critical`, `host_health`,
`stale_pool_guard` or `note` changed; a changed row ORDER.

---

## T4 — every consumer still reads the file, unchanged in shape

*Anchor criterion 4.*

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-watchdog-repair-targets.ps1
"exit=$LASTEXITCODE"
```

**Pass:** exit 0 and `REPAIR TARGETS OK: 24 container(s) all resolve to a project
that declares them.` (24 on the developer's run; the number may differ if the
watchdog's call sites changed, but every row must say `declared`.)
**Fail:** any `[FAIL]`; any `NOT DECLARED`; any `(render failed)`.

`status_check.py`'s inventory load:

```bash
python -c "
import sys; sys.path.insert(0,'scripts/recovery')
import status_check
inv = status_check.load_inventory()
print('projects:', len(inv['projects']))
print('groups  :', {g: len(v) for g, v in inv['planes'].items()})
print('rows    :', sum(len(v) for v in inv['planes'].values()))
print('no critical flag:', [r['container'] for v in inv['planes'].values() for r in v if 'critical' not in r])
print('stale_pool_guard:', [r['container'] for v in inv['planes'].values() for r in v if r.get('stale_pool_guard')])
"
```

**Pass:** 8 projects; groups `core 8, memory 2, search 4, coder 3, notebook 2,
openbrain 25, backups 13, agent-org 12`; 69 rows; **no** row without a `critical`
flag; `stale_pool_guard: ['openbrain-mcp']` — the guard `status_check.py:440-452`
depends on.
**Fail:** a missing key, a `None` where a bool is expected, a group that vanished,
or an exception. (A `SyntaxWarning` about `\s` from `status_check.py:97` is
pre-existing and not a failure.)

Also read the watchdog's use of the file and confirm nothing it needs was
dropped: `scripts/checks/stack-watchdog.ps1:107-158` reads `projects[*].file`,
`.env_file` and each row's `container`, `project`, `service`. All four are still
emitted; `file: null` still marks only `ai-stack`.

---

## T5 — the hermetic suite

*Anchor criterion 5.*

```text
python -m pytest scripts/stack -q
```

**Pass:** 76 passed, and **no docker daemon is contacted** — confirm by reading
`FakeHost` and `FakeCompose` in `scripts/stack/test_stack.py`: every `docker`
call and every HTTP GET is injected through `stack.main(..., capture=…, http=…)`.
The suite covers, among others: the fifteen probe labels in order; the exit code
being the failure count; a dead frontend costing three probes and not the sweep;
`REFUSED` reading as FAIL with the reason in the label; `/healthz` alone not
passing `search`; inventory generation against a fixture render; `--check` red on
a mutated and on a deleted row; `--write` refusing an unlisted container; a stale
`service` in the sidecar; ports in both directions; the `opt_in`/`default`/
`pending` accounting.
**Fail:** any failure; any test that reaches a real daemon; a test that asserts
only "exit code non-zero" without asserting what was said.

Sanity-check the suite is not vacuous — break one thing and see it go red:

```bash
python - <<'EOF'
import pathlib
p = pathlib.Path('scripts/stack/stack.py'); s = p.read_text(encoding='utf-8')
p.write_text(s.replace('"OB1: ops door :8062/health"', '"OB1: ops door"', 1), encoding='utf-8')
EOF
python -m pytest scripts/stack -q -k probes_stack_ps1_ran   # must FAIL
git checkout -- scripts/stack/stack.py
python -m pytest scripts/stack -q                            # 76 passed again
```

---

## T6 — the shim is a shim, proven by output

*Anchor criterion 6.*

### T6a — same output as the driver

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 status > "$SL\shim-status.txt" 2>&1
python scripts\stack\stack.py status --all > "$SL\py-status.txt" 2>&1
Compare-Object (Get-Content "$SL\shim-status.txt") (Get-Content "$SL\py-status.txt")

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 up --dry-run
python scripts\stack\stack.py up --all --dry-run
```

**Pass:** `status` output identical (modulo line endings). `up --dry-run` prints
these eight lines from **both**, and **runs nothing**:

```text
docker compose -f docker-compose.yml --env-file .env up -d
docker compose -f inference/docker-compose.yml --env-file .env up -d
docker compose -f frontend/docker-compose.yml --env-file .env up -d
docker compose -f memory/docker-compose.yml --env-file .env up -d
docker compose -f search/docker-compose.yml --env-file .env up -d
docker compose -f coder/docker-compose.yml --env-file .env up -d
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery up -d
docker compose -f agent-org/docker/docker-compose.yml up -d
```

Compare that list against `git show 9f64b84:scripts/stack/stack.ps1` lines
41-50 (the `$Projects` registry) and `:52-60` (`Invoke-Project`): the same eight projects, the same order, the same `--env-file` on the six
that take one and none on `ob1`/`agent-org`, and the same single
`--profile idea-refinery`. **The portal is absent from both** (it is `manual`).

Then the refusal, which must come from the driver and not from a second copy of
the rule:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 restart
"exit=$LASTEXITCODE"
```

**Pass:** exit 1 and the driver's sentence, beginning
`refused: \`restart all\` would restart every plane at once.`
**Fail:** a different wording (that would mean the shim reimplemented it), or
exit 0.

And one plane:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 up coder --dry-run
```

**Pass:** a `# note: coder requires anchor, inference; this starts only coder`
line and exactly one `docker compose -f coder/...` line.

### T6b — nothing else is left in the shim

```powershell
$errs = $null
[System.Management.Automation.PSParser]::Tokenize((Get-Content scripts\stack\stack.ps1 -Raw), [ref]$errs) | Out-Null
$errs.Count      # must be 0
Select-String -Path scripts\stack\stack.ps1 -Pattern 'Probe|Invoke-WebRequest|docker |\$Projects' 
```

**Pass:** 0 parse errors, and every `Select-String` hit is a COMMENT or a
`Write-Host` string - never an invocation. `Select-String` is case-insensitive,
so `Probe` also matches the word "probes" in prose; read each hit rather than
counting them. On the developer's run there were four, all in the header block
or in the `Write-Host` advice at `:66` that tells an operator without python how
to run compose by hand. What must NOT appear: a live `Probe` call, an
`Invoke-WebRequest`, a `$Projects` array, or an `& docker` invocation. Read the file
top to bottom (92 lines) and confirm it contains no logic that could drift from
the driver — the only executable statements are the python-on-PATH guard, the
`$Action`→argv mapping, and `& python $Driver @Argv; exit $LASTEXITCODE`.
**Fail:** any live probe or plane list surviving in the `.ps1`.

### T6c — the scheduled tasks are unaffected

```powershell
Get-ScheduledTask |
  Where-Object { $_.TaskName -match 'Stack|Weekly' } |
  ForEach-Object { $_.TaskName + ' :: ' + ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) }
Select-String -Path scripts\checks\stack-watchdog.ps1, scripts\maintenance\weekly-maintenance.ps1 -Pattern 'stack\.ps1'
```

**Pass:** `StackWatchdog` invokes `scripts\checks\stack-watchdog.ps1` and
`AI-Stack Weekly Maintenance` invokes `scripts\maintenance\weekly-maintenance.ps1`
**directly**, and the `Select-String` finds **no** occurrence of `stack.ps1` in
either script — so neither task can be affected by the shim.
**Fail:** either task invoking `stack.ps1`, or either script calling it, with no
corresponding handling in the shim.

---

## T7 — the CI job, run locally, on a checkout that has no OB1

*Anchor criterion 7.*

Read `.github/workflows/ci.yml`'s `stack-driver` job, then run its exact steps.
Because OB1 is a submodule that CI does **not** check out, the honest local
rehearsal is a tracked-files-only copy:

```bash
CI="$SL/cisim"; rm -rf "$CI"; mkdir -p "$CI"
git ls-files -z | xargs -0 -n 200 cp --parents -t "$CI"
ls -d "$CI/OB1" 2>&1            # must say: No such file or directory
cp "$CI/.env.example" "$CI/.env"
cp "$CI/agent-org/docker/.env.example" "$CI/agent-org/docker/.env"
cd "$CI" && python -m pytest scripts/stack -q && python scripts/stack/stack.py inventory --check; echo "exit=$?"
cd -
```

**Pass:** 76 passed; `inventory --check` exits **0** and prints
`[ -- ] NOT VERIFIED - open-brain: OB1/docker/docker-compose.yml is not on disk`
followed by the `[OK]` line. The skip must be **printed and named** — an
unrenderable project that passes *silently* is a FAIL even though the exit code
is 0.
**Fail:** a non-zero exit; a crash on the missing submodule; a silent pass with
no `NOT VERIFIED` line; the job's steps differing from what you ran.

Also check the job's shape against its neighbours: python 3.12 like `ruff` and
`pytest-llm-queue`, `runs-on: ubuntu-latest`, and the `cp .env.example .env`
stub with the same justification `compose-validate` gives.

### Pre-commit hooks

```powershell
git add -A ; git reset      # no-op; just to be sure nothing is staged
git stash list              # note it, so you can tell you changed nothing
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
```

With **nothing staged** this prints `[configs] nothing staged - skip` and exits 0.
To see the real path, stage the item's files read-only and re-run:

```powershell
git add scripts/lib/stack-services.json scripts/lib/stack-services.curated.json stack.manifest.toml
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
"exit=$LASTEXITCODE"
git reset
```

**Pass:** exit 0, a `[configs]   [OK]   scripts/lib/stack-services.json matches…`
line (the driver's own output, forwarded), and the staged-JSON validation line.
**Fail:** the inventory verification not running at all when only the inventory
inputs are staged — that was the pre-change gap (the old block sat inside the
`*.yml` trigger), and the fix is the point of `--- 1b.` in that script.

---

## T8 — the profile rules: the driver can never start FEWER containers

*Not a lettered anchor criterion; it is the divergence `sl-ob1-profiles` raised,
and the reason `stack.manifest.toml` and the driver changed beyond the brief.
Fail the item if any of this does not hold.*

First reproduce the measurement the design rests on (read-only):

```bash
grep -n COMPOSE_PROFILES .env                                   # expect COMPOSE_PROFILES=local,gpu,tailscale
docker compose -f inference/docker-compose.yml --env-file .env config --services | wc -l
docker compose -f inference/docker-compose.yml --env-file .env --profile idea-refinery config --services | wc -l
```

**Pass:** 8 services without the flag, **4 with it** — `--profile` REPLACES
`COMPOSE_PROFILES` rather than adding to it (finding F1). If your run shows 8
and 8, compose's behaviour has changed since v5.3.0; say so, because the
`effective_profiles()` union then guards nothing and the reviewer needs to know.

Then confirm the driver's response:

- `grep -n 'def effective_profiles' -A 30 scripts/stack/stack.py` — whenever the
  flag set is non-empty, the plane env's `COMPOSE_PROFILES` is unioned in.
- `python -m pytest scripts/stack -q -k "unioned or flags_each_plane"` — green.
- On the live tree, `python scripts/stack/stack.py up --all --dry-run` passes
  **no** `--profile` to `inference` (so compose reads `COMPOSE_PROFILES=local`
  itself, exactly as `development`'s `stack.ps1` did) and exactly
  `--profile idea-refinery` to `ob1`.

And the gate that forces the next item to declare itself:

```bash
python -c "
import sys, pathlib; sys.path.insert(0,'scripts/stack'); import stack
m = stack.Manifest.load(pathlib.Path('stack.manifest.toml'))   # a Path, not a str
for p in m.order:
    print(p, 'default=', m.default_profiles(p), 'opt_in=', m.opt_in_profiles(p),
          'pending=', m.pending_profiles(p), 'UNACCOUNTED=', m.unaccounted_profiles(p))
"
```

**Pass:** every plane's `UNACCOUNTED` list is empty; `ob1` has
`default=['idea-refinery']`; `inference` has `opt_in=['local']` and **no**
`pending` (it shipped at `9f64b84` — finding F6); `frontend` still has
`pending=['gpu','tailscale']`; `agent-org` has `opt_in=['workers','cloud']`;
`portal` has `opt_in=['internet']`.
**Fail:** any unaccounted profile; `inference.local` still marked `pending`;
`agent-org`'s slices marked anything but `opt_in` (`development`'s `stack.ps1`
header says in as many words that they are not managed here).

Finally, prove the gate bites — the `sl-ob1-profiles` rehearsal:

```bash
cp stack.manifest.toml "$SL/manifest.keep.toml"
printf '\n[planes.ob1.profiles.newslice]\ndescription = "services that run today"\n' >> stack.manifest.toml
python scripts/stack/stack.py inventory --check; echo "exit=$?"
cp "$SL/manifest.keep.toml" stack.manifest.toml
python scripts/stack/stack.py inventory --check; echo "restored exit=$?"
```

**Pass:** the first run exits non-zero with
`PROFILE 'newslice' … declares neither \`default = true\` … \`opt_in = true\` … nor \`pending = true\``;
the restored run exits 0.

---

## T9 — the docs say what the code does

Check each claim against the file, not against its neighbours:

| Doc | Claim to verify |
|---|---|
| `scripts/stack/README.md` | the three profile flags table matches `Manifest.unaccounted_profiles()`; the `--profile` vs `COMPOSE_PROFILES` measurement matches T8; the `health` section's four rules each appear in the code; the selection table matches `select_planes()`; the `inventory` table's "comes from" column matches `Inventory.build()` |
| `README.md:69` | now says **15** probes and points at the driver (it said 12) |
| `CLAUDE.md` | the new **Driver** row names the verbs the driver actually has; the container rule now names `stack-services.curated.json` + `inventory --write` |
| `documentation/runbooks/SERVICE-LIFECYCLE.md` rows 5 and 8 | row 5 sends you to `HealthSweep.run()` **and** `PS1_PROBES`; row 8 sends you to the sidecar, then `--write`. Follow row 8 literally for an imaginary service and confirm the instructions are sufficient |
| `documentation/notes/stack-layers-sl-driver-parity-findings.md` | **every** claim: open each cited file at the cited line; re-run each `[measured]` command. A false claim here is worse than one in the artifact (MERGE-PROTOCOL §2). F4 records a claim true at `9f64b84` and false at `be00d53`; F11–F14 came from attempt 1, F18–F21 from the ob1-profiles rebase, F22–F25 from this one |

**Every citation names the construct it points at, and the blob when that is not
HEAD** (attempt 4's F-T15: F2 and F7 cited `check-project-configs.ps1:67-96` and
`:67-77` "(pre-change)", which is not a revision — the file has been rewritten
twice since). Check each with `git show <blob>:<path> | sed -n '<n>p'`:

Each row gives the construct to **grep for**, so the check is "does this line
still contain the thing the text says it does", not "does the file have that many
lines". Run each as `git show <blob>:<path> | sed -n '<n>p'` and match the string.

| citation | blob | grep for |
|---|---|---|
| `check-project-configs.ps1:67` / `:77` / `:96` | `9f64b84` | `$renderTargets = @(` / `}` closing the conditional `open-brain` / `}` closing the name loop |
| `stack-watchdog.ps1:107` / `:145` / `:162` / `:170` | HEAD | `function Get-RepairTargetMap` / `$Service = if ($Row.service)` / `function Invoke-PlaneCompose` / `no compose project owns it` |
| `check-watchdog-repair-targets.ps1:214` | HEAD | `$svc = if ($row.service)`. **`:196` also appears in F16's text as the OLD value, quoted deliberately** |
| `inference/docker-compose.yml:83-85` / `:88-90` | HEAD | `name: ai-stack_llm-net` / `name: ai-stack_app-net` |
| `inference/compose/upstreams.yml:24` / `:54` / `:100-106` / `:145` / `:152-158` | HEAD | `MODEL STORE: ${LM_MODELS_DIR}` / `${LM_MODELS_DIR:-../../data/models/gguf}:/models:ro` / `driver: nvidia` / `LLAMA_ARG_MODEL=/models/bge-m3-f16.gguf` / the second `driver: nvidia` |
| `inference/compose/gateway.yml:153` / `:162-164` | HEAD | `llm-gateway-ui:` / the `networks:` key then `- llm-net`, `- app-net` |
| `OB1/docker/docker-compose.scheduled.yml:186` / `:190` / `:253` / `:254` / `:258` | HEAD | `CHAT_API_BASE:` / `FETCH_PROXY_URL:` / `MATTERMOST_URL:` / `MATTERMOST_TOKEN:` / `CHAT_API_BASE:` again (the scheduled slice sets it on two services; **neither of these two lines is `EMBEDDING_API_BASE`**, which the manifest's prose at `[planes.ob1]` names alongside it - that variable lives in `OB1/docker/docker-compose.yml`, not the scheduled file) |
| `OB1/integrations/openbrain-idea-refinery/index.ts:268` / `:295` | HEAD | `fetch(\`${RESEARCH_URL}/research\`` / `/research/jobs/${jobId}` |

**A sweep over this branch reports some citations that do not resolve, and every
one of them is deliberate.** Two kinds, both labelled where they appear:

* **quotations of what was wrong** - F16's old `:196`, F27's left-hand column,
  and F-T18's quoted `s.replace(...)` call that broke a correct line. This plan
  quotes the last two again, in T9 and in *Attempt 6*.
* **blob-pinned citations whose PATH also belongs to that blob** - F4's
  `llm-queue` line, whose package `sl-colo-inference` moved into the inference
  plane on `3a373e2`. Read those with `git show <blob>:<path>`; opening the
  working tree finds the wrong file or none.

**Every other citation must resolve.** On the developer's final run: 123
matches, 109 resolving, 14 in those two categories. The count moves whenever the
prose ABOUT them is edited, which is why the criterion is "every live citation
resolves" and not a number to hit.
| `test_stack.py:716` | `5133de9` | `assert sweep(FakeHost(serve_routes="9"), root)[0] == 0` |
| `memory/README.md:126` / `:183` / `:186` | `be00d53` | the three `$Projects` / `stack-services.json` lines this branch repointed |
| `llm-queue/src/llm_queue/__init__.py:9` | `9f64b84` | the 103-character docstring line (68 at `be00d53`). **The path is the one that existed at those blobs**; `sl-colo-inference` moved it to `inference/llm-queue/...` on `3a373e2`, so resolve it with `git show`, not against the working tree |

**Attempt 6 shipped a path that does not exist.** Check this first:

```bash
sed -n '330p' stack.manifest.toml
grep -rn 'docker-compose\.OB1/' . --exclude-dir=.git
```

**Pass:** line 330 reads
`# optional agent-org: OB1/docker/docker-compose.scheduled.yml:253` — ONE prefix —
and the grep finds nothing anywhere in the tree. `:253` must be
`MATTERMOST_URL: ${IDEA_REFINERY_MM_URL:-…}`.
**Fail:** a doubled prefix; any other path built by concatenating a prefix onto a
path that already had one.

That line was **correct at `ae915c3`** and was broken by the repair for F27: an
unanchored `s.replace("scheduled.yml:253", "OB1/docker/…scheduled.yml:253")`
matched the tail of the already-qualified path. F27's table had also claimed five
bare `scheduled.yml` refs where there were four (`:253` was never bare), and the
phantom fifth is exactly the line the repair damaged. Confirm the table now says
four, on three lines (`274`, `278`, `290` at `ae915c3`), and that **F-T18** and
F16 both carry the rule: a bulk replacement across a path must be anchored, and a
repair is an edit like any other — re-run the sweep over its own output.

**The four claims attempt 1 failed on — check these first, they are the case:**

1. **`SERVICE-LIFECYCLE.md` row 8, EXECUTED not read.** In `$COPY`: add a service
   with a `container_name` to `memory/docker-compose.yml` (inside `services:`,
   above the `volumes:` block), then do exactly what row 8 says — a sidecar row in
   the right plane group, with `critical` and a `host_health`, and **no `project`
   field** — and run `python scripts/stack/stack.py inventory --write`.
   **Pass:** `wrote scripts/lib/stack-services.json`, exit 0, and the generated row
   carries `"project": "memory"`, filled in from the render. **Fail:** any refusal
   — the row's audience is the next person to add a service, and they will follow
   it literally.
   Then the refusal that must REMAIN: add a row for a container that is in no
   compose file at all and give it no `project`. **Pass:** exit non-zero,
   ``NO `project` for <name>`` naming both reasons (not declared anywhere / its
   project cannot be rendered here).
2. **`SERVICE-LIFECYCLE.md` row 4** must no longer send a new-plane author to
   `stack.ps1`'s `$Projects` registry — this item deleted it. It should name a
   `[planes.<name>]` table in `stack.manifest.toml`. Then sweep the tree:
   `grep -rn '\$Projects' --include=*.md .` The hits that are FINE, and why: this
   plan itself (it quotes the old registry deliberately); `SERVICE-LIFECYCLE.md:23`
   (it now says the registry no longer exists); and
   `documentation/evidence/stack-layers/sl-manifest-test-plan.md:496`, a dated
   evidence artifact of a completed run, which records what was true then and is
   not rewritten. Any OTHER live doc is a defect - attempt 2 left two, both in
   `memory/README.md`, now fixed.
   *(Attempt 2's plan excused `coder-plane-findings.md:164` here. That file
   contains no `$Projects` at all - it says "the project registry's `Note` string"
   in prose - so the excuse named a hit that does not exist while the two real ones
   went unlisted. Right verdict, wrong reason, which MERGE-PROTOCOL 2 warns about
   by name.)*
3. **`scripts/stack/README.md`'s `health` section counts.** Count them yourself in
   `HealthSweep`: `docker exec` calls and HTTP GETs. The README must say **five**
   and **seven** (attempt 1 said four and six; the missing GET was the second
   `search` probe, the one the same README spends a paragraph justifying).
4. **`scripts/stack/README.md`'s `inventory` "comes from" table** must account for
   `project` — attempt 1 omitted it from both columns.

**Pass:** every claim checked and true; every cited line number resolving to what
the text says is there.
**Fail:** any citation that does not resolve; any `[measured]` claim that does not
reproduce (say so rather than guessing why); any finding stated more confidently
than its provenance label supports.

---

## T10 — the repository is left clean

```powershell
Remove-Item -Recurse -Force $SL
git status --short
git diff --stat
```

```bash
rm -rf "$SL"; git -C . status --short; git -C . diff --stat
```

**Pass:** `git status --short` is empty — in particular no
`scripts/stack/stack.base.ps1` (T2), no modified `scripts/lib/stack-services.json`
(T3b), no modified `stack.manifest.toml` (T8), no modified
`scripts/lib/stack-services.curated.json` (T3c), no `.stack/` directory, and no
change to `.env`, `OB1/docker/.env` or `agent-org/docker/.env` (`git diff --stat`
on them empty, mtimes unchanged).
**Fail:** any leftover.

---

---

## Attempt 2 — what changed since the failed run

| Change | Why |
|---|---|
| `_row()` fills `project` from the render when the sidecar omits it; a row in NO render with no `project` is refused by name | T9(1) — makes `SERVICE-LIFECYCLE.md` row 8 true as written. Where `project` IS recorded it is still audited against the render |
| `SERVICE-LIFECYCLE.md` row 4 points at `stack.manifest.toml`, not the deleted `$Projects` registry | T9(2) |
| `scripts/stack/README.md` health counts corrected to five `docker exec`s and seven GETs, and the `inventory` table gains a `project` row | T9(3) and T9(4) |
| `test_a_serve_route_count_below_the_threshold_fails` added | finding F-T2: `>= 8` -> `>= 0` left the suite green |
| `test_an_unknown_key_in_a_profile_table_is_tolerated` added | finding F-T1: `sl-ob1-profiles` adds a `requires` key to profile tables; this item's gate must not refuse it |
| `check-watchdog-repair-targets.ps1` renders with every declared profile | finding F-T3: it was the last inventory consumer that could not see a profiled row |
| Plan: mutating cases moved to a scratch copy; T8's snippet takes a `Path`; T6b no longer claims "exactly two lines"; T2 states it is green-path only | plan defects P1-P4 |
| Rebased onto `be00d53` (`sl-closeout`), keeping both intents in `README.md` and `CLAUDE.md` | the work line moved |

## Attempt 3 — what changed since the second failed run

| Change | Why |
|---|---|
| `check-watchdog-repair-targets.ps1:196` -> `:214` in finding F5 | T9(a): this branch's own F12 fix added 18 lines to that file and moved the line it cited. Every citation in the note and this plan was re-derived against the current tree, not just that one |
| the boundary test is one table (`3`/`7` FAIL, `8`/`9` PASS), asserting the verdict as well as the exit code | T9(b): the claim was TRUE - `serve_routes="9"` is asserted at `5133de9:test_stack.py:716` - but four consecutive asserts invited stopping at the third. The shape changed, not the claim; F14 records the check |
| `memory/README.md:126`, `:183`, `:186` repointed at the manifest, `HealthSweep.run()`/`PS1_PROBES`, and the curated sidecar + `inventory --write` | T9(c): the only live plane README this item falsified. Recorded as F17 |
| this plan's `$Projects` sweep now gives the grep and lists every excused hit with its reason | T9(c): attempt 2 excused a hit that does not exist and missed two that do |
| F15 declares `check-watchdog-repair-targets.ps1` as an out-of-artifact-list change, with the reason | reviewer note F-T8 - a reviewer should meet it as a decision, not discover it |

**Merge order** (reviewer note F-T9): `sl-ob1-profiles` is in review on base
`9f64b84`. It flips OB1's `research`/`wiki`/`notebook` from `pending = true` and
adds a `requires` key to profile tables. This item's gate tolerates unknown keys
(`test_an_unknown_key_in_a_profile_table_is_tolerated`), so `requires` is free;
what binds is that a profile declared `pending` while it exists in the compose
file is refused, by design. **Whichever item lands second rebases and flips all
three in the same commit.** If this one lands second, that is a three-line
manifest edit plus `inventory --write`, and the tester should re-run T3a and T8.

80 tests now (was 76).

---

---

## T11 — the sl-ob1-profiles merge: both items' behaviours survive

*New for attempt 4. Everything here is read-only.*

### T11a — every OB1 profile is accounted for, and none is `default`

```bash
python -c "
import sys, pathlib; sys.path.insert(0,'scripts/stack'); import stack
m = stack.Manifest.load(pathlib.Path('stack.manifest.toml'))
for p in m.order:
    print(p, 'default=', m.default_profiles(p), 'opt_in=', m.opt_in_profiles(p),
          'pending=', m.pending_profiles(p), 'UNACCOUNTED=', m.unaccounted_profiles(p))
print('closure:', m.profile_closure('ob1', {'idea-refinery'}))
"
```

**Pass:** every plane's `UNACCOUNTED` is empty — in particular `ob1`, which was
the collision (`sl-ob1-profiles` made the three real and this item's gate demands
a deployment flag). `ob1` shows `default=['idea-refinery']`,
`opt_in=['research','wiki','notebook']`, `pending=[]`, and the closure is
`{'idea-refinery','research'}`.
**Fail:** any `UNACCOUNTED` entry; `wiki` or `notebook` marked `default` (that
would make `enable open-brain --headless` a no-op for the plane, defeating the
surfaces split); the `requires` closure not reaching `research`.

### T11b — `[declared, not rendered]`, and that it is bounded

```bash
git ls-tree development OB1                                     # expect 5005197...
docker compose -f OB1/docker/docker-compose.yml config --profiles   # expect: idea-refinery only
python scripts/stack/stack.py inventory --check ; echo "exit=$?"
```

**Pass:** exit **0**, with three `[ ~~ ] declared, not rendered` lines naming
`research`, `wiki` and `notebook`, nine more naming the container rows that
declare them, and the closing line telling the operator to run
`init --product research --force` at the gitlink bump. The `[OK]` line follows.
**Fail:** a non-zero exit (the pinned submodule is not drift); OR silence — a
pass with no `declared, not rendered` output would mean the rule became a blanket
exemption. Also confirm the bound by reading `is_pinned_submodule`: the submodule
set comes from `.gitmodules`, not from a hard-coded `ob1`.

Then prove it cannot launder a mistake, in `$COPY`:

```bash
python -c "
import json,pathlib
p=pathlib.Path('scripts/lib/stack-services.curated.json'); d=json.loads(p.read_text(encoding='utf-8'))
for r in d['planes']['openbrain']:
    if r['container']=='openbrain-wiki': r['profile']='wikki'
p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'
',encoding='utf-8')"
python scripts/stack/stack.py inventory --check ; echo "exit=$?"
```

**Pass:** exit non-zero with ``STALE `profile` for openbrain-wiki`` — a profile
the MANIFEST never declared is drift even on a pinned-submodule plane.

### T11c — the shim still starts today's thirty OB1 containers

```bash
python scripts/stack/stack.py up --all --dry-run | grep OB1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack.ps1 up --dry-run | grep OB1
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research config --services | wc -l
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook config --services | wc -l
docker ps --format '{{.Names}}' | grep -cE 'openbrain|open_notebook|surrealdb|open-notebook'
```

**Pass:** both drivers print
`docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research up -d`;
both renders give **30**; **30** containers are running. `sl-ob1-profiles`'
four-profile registry row has no landing place in a shim, and this is the proof
that dropping it starts the same set — not an argument that it should.
**Fail:** the two renders disagreeing; either differing from the running count;
the OB1 line missing `research` (the `requires` closure is what puts it there).

*Read the reasoning at `scripts/stack/stack.ps1`'s header and finding F18 — and
note what F18 says about the gitlink bump. That is a deployment step this item
deliberately does not take.*

### T11d — sl-ob1-profiles' coverage guard is still there and still counts to 30

The guard only runs when a `*.yml` is staged, so do this in `$COPY`:

```bash
git init -q . ; git add -- . ':!OB1' >/dev/null   # NOT `git add -A`: OB1 is a plain
                                                 # directory in this copy, not a
                                                 # submodule, so -A stages all of it
echo "" >> memory/docker-compose.yml
git add memory/docker-compose.yml scripts/lib/stack-services.json
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1
```

**Pass:** BOTH checks run — the coverage line
`[rows verified/expected: inference:8/8 frontend:4/4 memory:3/3 search:4/4 coder:4/4 open-brain:30/30]`,
the `NOT VERIFIED: project 'agent-org' ... no render target` line, AND this item's
`[OK] scripts/lib/stack-services.json matches ...`.
**Fail:** either missing. They answer different questions (finding F20) and
deleting one was the tempting wrong move.

### T11e — both suites, green together

```bash
python -m pytest scripts/stack -q      # expect 95 passed
```

**Pass:** 95. Four expectations moved deliberately in the merge and each says why
in its own docstring: the pinned per-plane profile flags (`ob1` now gets two), the
`effective_profiles` assertion, the unknown-key test (`requires` stopped being
unknown, so it moved to a key that is), and a new `requires` test exercising
closure plus the unknown-profile refusal.
**Fail:** any failure; any expectation changed without a stated reason.

---

---

## T12 — the sl-frontend-solo merge: a plane whose profiles exclude each other

*New for attempt 5. Read-only.*

### T12a — the tailnet probe knows whether it applies

`sl-frontend-solo` made `stack.ps1 health`'s tailnet probe deployment-aware. The
shim replaced that script, so the logic had to come across or it would have been
silently dropped — the whole failure class this item exists for.

```bash
python -m pytest scripts/stack -q -k "tailnet or skip_decision or unreadable or running_tailscale or exact_name"
python scripts/stack/stack.py health
```

**Pass:** those cases green, and on THIS host (which runs the `tailscale`
profile) the probe RUNS — fifteen `[OK]` lines, no `[skip]`. Read
`HealthSweep.tailscale_deployed()` and check all three branches against
`git show f2bb38f:scripts/stack/stack.ps1`'s version: it reads the RENDER (not
`.env`), it probes anyway when the render is unreadable, and it probes anyway
when a container named exactly `tailscale` is running while absent from the
render. Both fail-open paths print a `[warn]` naming the situation.
**Fail:** the probe silently dropped on a deployment that has tailscale; a
`[skip]` counted as a failure; the decision made by parsing `.env` instead of
rendering; a name check that would accept `stt-tts-tailscale`.

### T12b — the frontend's three profiles cannot all be rendered together

```bash
docker compose -f frontend/docker-compose.yml --env-file .env.example --profile stock --profile gpu --profile tailscale config -q
docker compose -f frontend/docker-compose.yml --env-file .env.example --profile tailscale config -q
python scripts/stack/stack.py inventory --check ; echo "exit=$?"
```

**Pass:** the first says
`services.openwebui: container name "openwebui" is already in use` (`stock` and
`gpu` are two definitions of one container); the second says
`service "tailscale" depends on undefined service "openwebui"` (it needs `gpu`);
and `inventory --check` still exits **0** — the generator falls back to one
render per profile *closure* and unions them. Confirm from `render_project` that
the fallback triggers only on failure and re-raises if any individual render
fails.
**Fail:** a non-zero exit; a crash; a fallback that swallows a genuinely broken
compose file (T12c).

### T12c — the fallback is for exclusivity, not a blanket retry

```bash
python -m pytest scripts/stack -q -k "fall_back_to_a_render_per_closure or any_other_reason"
```

**Pass:** both green. The second injects a YAML syntax error and asserts the
generator still REFUSES.

### T12d — one container, two definitions; and the F-T14 fix

```bash
python scripts/stack/stack.py inventory --check | grep "mutually exclusive"
python -m pytest scripts/stack -q -k "bogus_service_on_a_declared"
```

**Pass:** a `[ ~~ ] mutually exclusive - openwebui: produced by 2 mutually
exclusive services (openwebui, openwebui-stock)` line, and the `openwebui` row in
`scripts/lib/stack-services.json` carries **neither** `service` nor `profile` —
which key is right depends on the deployment, which is what `sl-frontend-solo`'s
own note on that row says.

For F-T14, prove the fix bites. In `$COPY`, put the STALE loop back under the
`else` it used to sit in (`for key in ([] if accepted_profile else (...)):`) and
re-run that case: it must go RED. Restore it. **The defect: a row whose `profile`
was accepted as a gitlink declaration had its `service` and `profiles` unchecked
too, so a bogus `service` on `openbrain-wiki` passed on this host and failed in
CI — a check whose answer depends on which machine runs it.**
**Fail:** the mutually-exclusive line missing; a `service` or `profile` on the
`openwebui` row; the STALE case staying green under that mutation.

### T12e — the inventory still equals `b9fff95`'s, minus the declared differences

```bash
python - <<'EOF'
import json, subprocess
from pathlib import Path
theirs = json.loads(subprocess.run(["git","show","b9fff95:scripts/lib/stack-services.json"],
                                   capture_output=True).stdout.decode("utf-8"))
mine = json.loads(Path("scripts/lib/stack-services.json").read_text(encoding="utf-8"))
tr = {r["container"]: r for g in theirs["planes"].values() for r in g}
mr = {r["container"]: r for g in mine["planes"].values() for r in g}
print("rows:", len(tr), len(mr), "| same set:", set(tr)==set(mr),
      "| projects identical:", theirs["projects"]==mine["projects"])
for c in sorted(set(tr)&set(mr)):
    if tr[c]!=mr[c]: print("  DIFF", c)
EOF
```

**Pass:** 69 rows both sides, same container set, `projects identical: True`, and
exactly **four** `DIFF` lines, each declared: `openbrain-idea-refinery` (note
carries both items' provenance), `searxng` (redundant `service` dropped —
finding F5), `tailscale` and `tailscale-backup` (which gain the
`profile: tailscale` the generator derives and the hand-kept file omitted).
**Fail:** any fifth difference; a changed `critical`, `host_health` or
`stale_pool_guard`; a row gained or lost.

### T12f — the coverage line includes the frontend

Run T11d's staged-scratch recipe. **Pass:** the coverage list carries
`frontend:4/4` alongside `inference:8/8 … open-brain:30/30`, because
`sl-frontend-solo` added a frontend render target with its two profiles.
**Fail:** `frontend` missing from the list, or a count below 4/4.

---

---

## Attempt 6 - what the `ae915c3` rebase touched

`sl-colo-gateways` moved the two gateway source trees into their planes
(`search-gateway/` -> `search/gateway/`, `mnemory-gateway/` ->
`memory/mnemory-gateway/`). **Git merged all four shared files with no
conflict**, which is not the same as being right, so each was opened and checked;
each kept both intents:

| file | theirs | mine | result |
|---|---|---|---|
| `.github/workflows/ci.yml` | `pytest-search-gateway` now installs `./search/gateway[dev]` and runs `search/gateway/tests` | the `stack-driver` job | both present; run both in T7 |
| `CLAUDE.md` | the "its twin ... moved into the memory plane" sentence and its pointers | the **Driver** row and the container-rule edit | both present |
| `README.md` | the repo-map row naming `search/gateway/` and `memory/mnemory-gateway/` | the shim pointer under `stack.ps1 health` | both present |
| `memory/README.md` | path repoints at `:33` and `:151` | the `$Projects` / inventory repoints at `:126`, `:183`, `:186` | both present; mine now sit at `:127`, `:184-186`, `:192` |

**Expected values that moved, and the cases they belong to:**

* **T7** - `ci.yml` carries TWO recently-added jobs now. Run both locally:
  `python -m pip install -e "./search/gateway[dev]"` +
  `python -m pytest search/gateway/tests -q` (theirs), and the `stack-driver`
  steps (mine). A failure in theirs is not this item's - say so rather than
  skipping it.
* **T9** - `README.md`'s repo-map row and `memory/README.md`'s line numbers are
  the ones above. The findings note's `memory/README.md` citations stay pinned to
  `be00d53` and are correct there.
* **T12** - untouched by this rebase; re-run it anyway.

**Also fixed in this pass** (both from attempt 5's tester):

* **F-T16** - `scripts/stack/README.md`'s enumeration of what `health` touches
  omitted the `docker compose ... config --services` render that
  `sl-frontend-solo`'s tailnet guard added. The list is itemised now and carries
  it, plus the second `docker ps`. Count them yourself in `HealthSweep`.
* **F-T17** - why `health` renders with `.env` and the inventory with
  `.env.example` is stated at BOTH call sites (`tailscale_deployed` and
  `render_env_path`) and in the README: one asks what this host DEPLOYS, the
  other what the compose file DECLARES, and an answer that changed with the
  machine would make the generated inventory unreproducible.

**And an inherited-citation sweep.** Every `path:line` in every file this branch
touches was re-derived by CONSTRUCT, in the path form and the bare-basename form
(`scheduled.yml:186` resolves to no file at all - the name is
`docker-compose.scheduled.yml`). **Every live citation in the branch resolves**:
the final sweep reports 123 matches, 109 resolving and 14 inside the labelled
categories above. It was run THREE times - before the repairs (86 found, 79
resolving, 7 broken), after them, and again after the attempt-7 fix, because the
repairs are edits too and attempt 6 shipped a broken path by not doing the third
run (F-T18). Seven were broken
before this pass, every one inherited in `stack.manifest.toml`: five
`inference/docker-compose.yml` citations pointing past the end of a file
`sl-inference-split` left 91 lines long, and two unresolvable basenames. Spot-check
the re-derived ones - `inference/docker-compose.yml:78-80` is
`name: ai-stack_llm-net`, `inference/compose/upstreams.yml:145` is
`LLAMA_ARG_MODEL=/models/bge-m3-f16.gguf`, `inference/compose/gateway.yml:152` is
`llm-gateway-ui:` - and treat any citation that does not resolve as a defect.

---

---

## Attempt 8 - what the `3a373e2` rebase touched

`sl-colo-inference` moved the inference plane's own source and config inside it:
`llm-queue/` -> `inference/llm-queue/`, `config/*` -> `inference/config/*`, and
`config/` is gone. One conflict, in `stack.manifest.toml`'s frontend profile
tables; everything else merged cleanly and was checked by hand.

| file | theirs | mine | result |
|---|---|---|---|
| `stack.manifest.toml` | frontend citations renumbered for a 484-line file (`:257-263`, `:290`, ...) | the `opt_in` flags and `tailscale requires = ["gpu"]` | **CONFLICT**, resolved: their renumbered descriptions verbatim, my flags and the `requires` edge, and the edge's own comment repointed to their `:290` |
| `.github/workflows/ci.yml` | `pytest-llm-queue` -> `./inference/llm-queue[dev]` and `inference/llm-queue/tests` | the `stack-driver` job | all THREE jobs present; T7 runs all three |
| `README.md` | the repo-map row drops `llm-queue/` | the shim pointer at `:69-70` | both present |
| `.env.example`, `workspace-stacks.md` | inference repoints | untouched by this branch | theirs, unchanged |

**Expected values that moved:**

* **T7** - `ci.yml` now has three recently-changed jobs. Run all three locally:
  `pytest-llm-queue` at its NEW path (`./inference/llm-queue[dev]`),
  `pytest-search-gateway`, and `stack-driver`.
* **T9** - the inference citations this branch repaired moved a second time,
  because that item added five lines to `inference/docker-compose.yml` (91 ->
  96) and shifted `inference/compose/gateway.yml`. The citation table has the new
  values; re-derive them by construct, do not trust the table. Finding F31.
* **T3/T12e** - the inventory is unchanged by the move (the generator derives
  from container names, and none changed), but `inventory --check` must still be
  green and the `llm-queue` build context is now `../llm-queue` relative to
  `inference/compose/queue.yml`.

---

## T13 - "I cannot check this here" must not print like "this is wrong"

*New for attempt 8. Read-only.*

`agent-org/docker/docker-compose.yml` carries service-level `env_file:` entries,
which `docker compose config` STATS whatever `--env-file` the CLI passes. So on
any machine without `agent-org/docker/.env` - gitignored, so every machine but
the deploy host - the render exits 1. `inventory --check` used to raise, and the
pre-commit hook then printed
`INVENTORY DRIFT - regenerate with: ... inventory --write`: wrong twice, because
nothing had drifted and `--write` refuses by the same path, so the remedy it
named could not work.

Reproduce the old shape and confirm the new one, in a scratch copy:

```bash
COPY="$SL/noenv"; rm -rf "$COPY"; mkdir -p "$COPY"
git ls-files -z | xargs -0 -n 200 cp --parents -t "$COPY"
cp "$COPY/.env.example" "$COPY/.env"          # root .env only - NOT agent-org's
cd "$COPY" && python scripts/stack/stack.py inventory --check ; echo "exit=$?"
```

**Pass:** exit **0**, with a line naming the project AND the file -
`[ -- ] NOT VERIFIED - agent-org: agent-org/docker/.env is absent - gitignored,
so this is expected off the deploy host` - and the `[OK]` line after it. Confirm
with `python -c "import json; ..."` that agent-org's sixteen rows are still in
the generated file: skipping the RENDER must not drop the ROWS, because the
watchdog routes repairs through them.
**Fail:** a non-zero exit; a bare refusal with no project named; agent-org's rows
missing from the output.

Then confirm the exemption is one condition and not a blanket retry:

```bash
printf '\n  bogus: {{{\n' >> "$COPY/memory/docker-compose.yml"
cd "$COPY" && python scripts/stack/stack.py inventory --check ; echo "exit=$?"
```

**Pass:** exit **non-zero**, naming the YAML error. A compose file that EXISTS
and will not render is still a hard refusal.
**Fail:** exit 0; a `NOT VERIFIED` line for `memory`.

```bash
python -m pytest scripts/stack -q -k "gitignored_env or skipped_project or exists_and_will_not_render"
```

**Pass:** three green. Finding F30 has the reasoning; the shape it belongs to -
F13 (missing tool), F19 (pinned submodule), F30 (gitignored file) - is that only
one of the three kinds of "cannot check" ever exits non-zero.

---

## Out of scope for this item (do not fail it for these)

- Porting `emergency-recovery.ps1` or `stack-watchdog.ps1` to Python — both keep
  their own ordering and probes (anchor `out_of_scope`).
- Remote docker contexts: `--context` is a passed-through prefix and nothing more.
- Archiving `stack.ps1` — it stays as the shim until every caller has moved.
- The probe set's known gaps, `open-terminal` above all: this item reproduced the
  fifteen probes one for one **including** what they do not cover. See finding F9.
- Adding `requires = ["gpu"]` to the frontend's `tailscale` profile IS this
  item's change and is in scope: the generator needs a machine-readable answer to
  "which profiles render together", and `sl-frontend-solo`'s description already
  said it twice in prose. It changes nothing operationally — no frontend profile
  is `default`. Finding F23.
- The OB1 **gitlink bump** and the one-time `stack.py init --product research`
  that follows it (finding F18). This item deploys nothing.
- `ruff check .` from the repo root is now **clean** — the pre-existing `llm-queue`
  E501 that attempt 1's plan told you to expect was fixed by `sl-closeout`, which
  is in this branch's new base. Finding F4 records both states. Any violation is
  now this item's to answer for.
- Everything in `documentation/notes/stack-layers-sl-driver-parity-findings.md` is
  a finding about other files, recorded rather than fixed, per CLAUDE.md — except
  F1 and F6, which this item did fix because the artifact depends on them.
