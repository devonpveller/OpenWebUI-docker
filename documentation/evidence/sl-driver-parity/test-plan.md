# sl-driver-parity — test plan

**Item:** `sl-driver-parity` (stack-layers wave 2)
**Branch:** `work/sl-driver-parity`, base `development` @ `9f64b84`
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
| `stack.manifest.toml` | `opt_in` profile flag; `inference.local` no longer `pending` |
| `.github/workflows/ci.yml` | new `stack-driver` job |
| `scripts/stack/README.md`, `README.md`, `CLAUDE.md`, `documentation/runbooks/SERVICE-LIFECYCLE.md` | docs |

---

## How to run this

**Everything here is read-only against the live stack.** No case starts, stops,
restarts or recreates a container. The live docker calls are `docker ps`,
`docker network inspect`, four read-only `docker exec`s, `docker compose ... ps`
and `docker compose ... config`. **No plane lease is required** (MERGE-PROTOCOL
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

**Never edit `.env`, `OB1/docker/.env` or `agent-org/docker/.env`.** Two cases
(T3b, T6b) deliberately modify a file **inside** the worktree and restore it;
each says exactly how, and T10 proves the tree came back clean. Do them in the
order written.

---

## T1 — the probe set is `stack.ps1`'s fifteen, one for one

*Anchor criterion 1. The case that cannot be automated away: read both lists and
judge them.*

Get the old probe list from the base commit and the new one from the driver:

```bash
git show development:scripts/stack/stack.ps1 | grep -n 'Probe "' 
grep -n 'self.probe(' scripts/stack/stack.py
```

Fill in this table by reading both sides. **A probe missing, merged into a
neighbour, or with a weaker pass condition FAILS the item.**

| # | Probe label | `development` `stack.ps1` | `stack.py` `HealthSweep.run()` | Pass condition must be |
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
git show development:scripts/stack/stack.ps1 | grep -c 'Probe "'      # expect 15
grep -c 'self.probe(' scripts/stack/stack.py                          # expect 15
```

**Pass:** fifteen rows, each present on both sides with the same pass condition,
and the two headers (`== container health (all projects)` and
`== functional gates`) in the same places.
**Fail:** any probe only on one side; any pass condition loosened (a `>= 8`
turned into `> 0`, a `== 200` turned into "no exception", a `REFUSED` treated as
anything but FAIL); the exit code no longer being the failed count.

*Also confirm the guard:* `PS1_PROBES` in `scripts/stack/test_stack.py` lists the
same fifteen labels, so this comparison cannot silently rot. Delete one entry
from that list, run `python -m pytest scripts/stack -q -k probes_stack_ps1_ran`,
and the suite must go RED; restore it.

---

## T2 — both drivers report the same pass/fail set against the LIVE stack

*Anchor criterion 2. Read-only; no lease.*

**Run the two within a minute of each other** — they are measuring a running
stack, and a container that restarts between them is a difference in the world,
not in the code.

```powershell
# PowerShell 5.1, from the worktree root
git show development:scripts/stack/stack.ps1 | Set-Content -Encoding utf8 scripts\stack\stack.base.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.base.ps1 health > "$SL\base.txt" 2>&1
"base exit=$LASTEXITCODE"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stack\stack.ps1 health > "$SL\shim.txt" 2>&1
"shim exit=$LASTEXITCODE"
Remove-Item scripts\stack\stack.base.ps1
Compare-Object (Get-Content "$SL\base.txt") (Get-Content "$SL\shim.txt")
```

```bash
# Git Bash equivalent
git show development:scripts/stack/stack.ps1 > scripts/stack/stack.base.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack.base.ps1 health > "$SL/base.txt" 2>&1; echo "base exit=$?"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stack/stack.ps1       health > "$SL/shim.txt" 2>&1; echo "shim exit=$?"
rm -f scripts/stack/stack.base.ps1
diff --strip-trailing-cr "$SL/base.txt" "$SL/shim.txt" && echo IDENTICAL
```

**The base copy must live at `scripts/stack/stack.base.ps1` inside the worktree
and be deleted immediately after** — the old script resolves the repo root from
`$PSScriptRoot`, so a copy in `$SL` would `Set-Location` into your temp
directory and report nothing but "couldn't find env file". T10 checks it is gone.

**Note on line endings:** the driver writes LF, PowerShell's redirection wrote LF
for the base copy on the developer's run — but compare with
`--strip-trailing-cr` / `Compare-Object` on the lines rather than byte-for-byte,
since a CRLF-only difference is not a behaviour difference.

**Developer's run, 2026-09-19:** both printed the same 19 lines, all fifteen
probes `[OK]`, `ALL HEALTH PROBES PASSED`, exit 0 from each.

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

Compare that list against `git show development:scripts/stack/stack.ps1` lines
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

**Pass:** 0 parse errors, and the `Select-String` returns exactly **two** lines,
neither of them an invocation: the usage comment at `:26` and the `Write-Host`
advice at `:66` telling an operator without python how to run compose by hand.
No `Probe`, no `Invoke-WebRequest`, no `$Projects`, no `& docker`. Read the file
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
import sys; sys.path.insert(0,'scripts/stack'); import stack
m = stack.Manifest.load('stack.manifest.toml')
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
| `documentation/notes/stack-layers-sl-driver-parity-findings.md` | **every** claim: open each cited file at the cited line; re-run each `[measured]` command. A false claim here is worse than one in the artifact (MERGE-PROTOCOL §2) |

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

## Out of scope for this item (do not fail it for these)

- Porting `emergency-recovery.ps1` or `stack-watchdog.ps1` to Python — both keep
  their own ordering and probes (anchor `out_of_scope`).
- Remote docker contexts: `--context` is a passed-through prefix and nothing more.
- Archiving `stack.ps1` — it stays as the shim until every caller has moved.
- The probe set's known gaps, `open-terminal` above all: this item reproduced the
  fifteen probes one for one **including** what they do not cover. See finding F9.
- `ruff check .` from the repo root is RED on `development` for a pre-existing
  `llm-queue` E501 (finding F4). `ruff check scripts/stack` is clean. Do not fail
  this item for the pre-existing one; do fail it for any new violation.
- Everything in `documentation/notes/stack-layers-sl-driver-parity-findings.md` is
  a finding about other files, recorded rather than fixed, per CLAUDE.md — except
  F1 and F6, which this item did fix because the artifact depends on them.
