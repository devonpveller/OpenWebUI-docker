# sl-env-split — test plan

**Item:** `sl-env-split` (stack-layers PLAN §2.7 / L.2; DECISIONS D10, D15,
D16, D17).
**Branch:** `work/sl-env-split`, base `development` at `2f5c451`.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-env-split.json`
**Findings sink:** `documentation/notes/stack-layers-sl-env-split-findings.md`

**Written by the developer; executed by a tester who did not write it.**

---

## Rebase onto `4934529` (2026-09-19)

This item was first queued at `df3e603` on base `2f5c451`. `development` then
moved to **`4934529`** (`sl-colo-frontend` merged), so the branch was rebased
before any tester claimed it - D18 requires the tested commit to be an ancestor
of the merge.

**What `sl-colo-frontend` moved, and what it means for the cases below:**
`Dockerfile.openwebui-gpu`, `dockerfile.tailscale`, `entrypoint.sh` and
`.dockerignore` moved from the repo root into `frontend/`, both `build.context`
values became `.`, and its citations followed (`stack.manifest.toml` now says
`frontend/entrypoint.sh`, and `stack-watchdog.ps1` / `dev-helper.ps1` /
`update-stack.bat` join the new path).

**One conflict, in `CLAUDE.md`'s plane table** - their Frontend row gained a
build-inputs sentence while mine dropped `--env-file` and rewrote the
`COMPOSE_PROFILES` clause. Resolved by keeping **both**: Case 12's reading of
that row should find the per-plane `frontend/.env` wording *and* the
`frontend/Dockerfile.openwebui-gpu` ... sentence. Everything else auto-merged;
**verify the auto-merges rather than trusting them** - `frontend/docker-compose.yml`
must still carry `context: .` at `:159`/`:280` **and** the two reworded
`WEBUI_SECRET_KEY` guards; `stack.manifest.toml` must carry the
`frontend/entrypoint.sh` citations **and** no `env_file` key;
`stack-watchdog.ps1` must join the new entrypoint path at `:459` **and** pass
no `--env-file`.

**`frontend/.env.example` is unchanged by the rebase** - the moved build inputs
introduce no new `${VAR}`, so Case 2 must still report `frontend refs=30
assigns=30`. If it reports anything else, the move added a variable and the
example is short one line.

**Compose line-count neutrality survived the new base** (Case 11's second half):
`git diff --numstat development..HEAD -- '*.yml'` shows equal +/- for all eleven
compose files, and all twelve report the same line count as `development`'s
version. Use `development` (not `HEAD~1`) as `<base>` throughout Case 11, and
see findings section 11 for the 212-reference anchor check already run.

---

## 0. Before you start — the four rules this plan runs under

1. **This item deploys nothing.** No case below starts, stops, recreates or
   `exec`s into a container. If a case seems to ask you to, stop and say so.
   `config`, `config --services`, `--dry-run`, `inventory --check` and the
   `check-*.ps1` scripts are all read-only.
2. **Never touch the operator's real `.env` files, and never print one.** They
   hold live credentials. Every case below works from `.env.example` files, or
   from copies you make in **your own scratch clone**. If a command would read
   the operator's `.env`, the case says so and tells you to skip it.
3. **Work in your own short-path scratch clone** for anything that stages
   files. `git -c core.longpaths=true clone --no-hardlinks <the developer's
   worktree> C:\wt-tester-envsplit`, then copy the working-tree delta in (the
   developer's changes may not be committed when you start; if they are, the
   clone already has them — check `git log --oneline -1`).
4. **A case is PASS only if you ran it.** Where a case says "read to the end",
   that means open the file and read it, not grep it. Record the actual output,
   not "as expected".

Set-up for most cases — in **your clone**, not the developer's worktree:

```powershell
cd C:\wt-tester-envsplit
Copy-Item .env.example .env
foreach ($p in 'frontend','inference','memory','search','coder','portal') {
  Copy-Item "$p\.env.example" "$p\.env"
}
```

That is the CI shape: every project directory has a `.env` made from its own
committed example, and no real secret is anywhere near it.

---

## Case 1 — every plane renders from its OWN example, under EVERY profile

**Acceptance line it answers:** "copying `<plane>/.env.example` to
`<plane>/.env` … renders with no unset-variable warning and no `:?` refusal; a
plane that still needs the root file to render FAILS."

Run **all eleven**, in the clone. `2>&1 >$null` keeps stderr (where compose puts
warnings) and discards the rendered YAML.

```powershell
$targets = @(
  @{ n='anchor';                f='docker-compose.yml';           a=@() }
  @{ n='frontend stock';        f='frontend\docker-compose.yml';  a=@() }
  @{ n='frontend gpu+ts';       f='frontend\docker-compose.yml';  a=@('--profile','gpu','--profile','tailscale') }
  @{ n='frontend gpu only';     f='frontend\docker-compose.yml';  a=@('--profile','gpu') }
  @{ n='inference cloud-only';  f='inference\docker-compose.yml'; a=@() }
  @{ n='inference local';       f='inference\docker-compose.yml'; a=@('--profile','local') }
  @{ n='memory';                f='memory\docker-compose.yml';    a=@() }
  @{ n='search';                f='search\docker-compose.yml';    a=@() }
  @{ n='coder';                 f='coder\docker-compose.yml';     a=@() }
  @{ n='portal (no profile)';   f='portal\docker-compose.yml';    a=@() }
  @{ n='portal internet';       f='portal\docker-compose.yml';    a=@('--profile','internet') }
)
foreach ($t in $targets) {
  $err = & docker compose -f $t.f @($t.a) config 2>&1 1>$null
  "{0,-22} exit={1} stderr={2}" -f $t.n, $LASTEXITCODE, (($err | Out-String).Trim())
}
```

**PASS:** every line `exit=0` and `stderr=` (empty). Any `variable is not set`
warning, or any `is required` / `:?` refusal, is a FAIL naming the plane and the
variable.

**Note the three deliberate profile combinations you are NOT asked to render:**
`frontend` with `stock` *and* `gpu` together (compose refuses it — both define
`container_name: openwebui`, and that refusal is the intended design), and
`agent-org` / `ob1`, which own their env files and are out of scope.

### 1b — the FAILURE direction (this is the case that proves the split is real)

A render must fail when the plane's OWN file is gone, **even though the root
`.env` still exists and still has nothing removed from it in your clone**. This
is what "a plane that still needs the root file to render FAILS" means in
reverse.

```powershell
Rename-Item memory\.env memory\.env.hidden
docker compose -f memory\docker-compose.yml config -q    # expect NON-ZERO
$LASTEXITCODE
Rename-Item memory\.env.hidden memory\.env
```

**PASS:** non-zero, with a message naming `MCP_API_KEY` and `memory/.env`.
A render that still succeeds means something outside the plane is supplying the
value — that is a FAIL, and worth finding.

Repeat for `search` (`MULLVAD_WG_PRIVATE_KEY`), `coder`
(`OPEN_TERMINAL_API_KEY`), `frontend` (`WEBUI_SECRET_KEY`) and `inference`
(`LITELLM_DB_PASSWORD`).

---

## Case 2 — two-way variable diff, per plane, unbounded

**Acceptance line:** "Every `${VAR}` referenced in a plane's compose files has a
line in that plane's `.env.example`, and every line in that `.env.example` is
referenced by a file in that plane … an orphan either way FAILS."

Save this as a scratch script (it writes nothing) and run it from the clone
root. It is deliberately **raw**: it does not know about the four expected
exceptions below, so it cannot hide a real one.

```python
# scratch: envdiff.py   —   python envdiff.py
import re, glob, yaml
planes = {
 "anchor":    (["docker-compose.yml"],                                    ".env.example"),
 "frontend":  (["frontend/docker-compose.yml"],                           "frontend/.env.example"),
 "inference": (["inference/docker-compose.yml"] + sorted(glob.glob("inference/compose/*.yml")),
                                                                          "inference/.env.example"),
 "memory":    (["memory/docker-compose.yml"],                             "memory/.env.example"),
 "search":    (["search/docker-compose.yml"],                             "search/.env.example"),
 "coder":     (["coder/docker-compose.yml"],                              "coder/.env.example"),
 "portal":    (["portal/docker-compose.yml", "portal/local-test.override.yml"],
                                                                          "portal/.env.example"),
}
VAR = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)')
def walk(o, out):
    if isinstance(o, dict):
        for k, v in o.items(): walk(k, out); walk(v, out)
    elif isinstance(o, list):
        for v in o: walk(v, out)
    elif isinstance(o, str):
        # strip compose's $$ escape FIRST: `$${RETAIN_COUNT}` is a CONTAINER-side
        # shell variable, not an interpolation. Parsing YAML first also means a
        # commented-out compose line is never counted as a reference.
        out |= set(VAR.findall(re.sub(r'\$\$', '\x00', o)))
for p, (files, ex) in planes.items():
    refs = set()
    for f in files: walk(yaml.safe_load(open(f, encoding="utf-8").read()), refs)
    refs.discard("VAR")
    assigns = [m.group(1) for m in
               (re.match(r'([A-Za-z_][A-Za-z0-9_]*)=', l.strip()) for l in
                open(ex, encoding="utf-8").read().splitlines()) if m]
    a = set(assigns)
    dupes = sorted({x for x in assigns if assigns.count(x) > 1})
    print(f"{p:10s} refs={len(refs):3d} assigns={len(assigns):3d}")
    if dupes:            print("   DUPLICATE KEYS :", dupes)
    if sorted(refs - a): print("   MISSING from example:", sorted(refs - a))
    if sorted(a - refs): print("   ORPHAN in example   :", sorted(a - refs))
```

**Expected output — and these are the ONLY four lines that may appear.**
Anything else is a FAIL.

```
anchor     refs=  0 assigns=  3
   ORPHAN in example   : ['NAS_BACKUP_PASSWORD', 'NAS_BACKUP_USER', 'TEST_VALIDATION_LLM_KEY']
frontend   refs= 30 assigns= 30
   MISSING from example: ['OWUI_IMAGE']
   ORPHAN in example   : ['COMPOSE_PROFILES']
inference  refs= 51 assigns= 50
   MISSING from example: ['COMPOSE_PROFILES']
memory     refs=  8 assigns=  8
search     refs=  9 assigns=  9
coder      refs=  7 assigns=  9
   ORPHAN in example   : ['LC_SELF_REMOTE_PAT', 'LC_SELF_REMOTE_URL']
portal     refs= 21 assigns= 21
```

**Verify each exception yourself — do not take this table's word for it:**

| reported | why it is not an orphan | how you confirm |
|---|---|---|
| `anchor` 3 orphans | the root file's whole purpose: variables no plane reads. `NAS_BACKUP_*` → `scripts/backup/backup-to-nas.ps1`, `set-nas-credential.ps1`; `TEST_VALIDATION_LLM_KEY` → `scripts/issue-ops/issue_ops.py` | `git grep -n NAS_BACKUP_USER -- scripts` and read the call site |
| `frontend` MISSING `OWUI_IMAGE` | shipped **commented out** (`#OWUI_IMAGE=…`), exactly as the pre-split root file had it; compose has a `:-` default | `grep -n OWUI_IMAGE frontend/.env.example` and `frontend/docker-compose.yml:128` |
| `frontend` / `inference` `COMPOSE_PROFILES` | compose reads it as a **CLI variable**, not via `${…}`, so a reference/assignment asymmetry is inherent. `frontend` assigns it (`=stock`); `inference` ships it commented AND also interpolates it at `inference/compose/gateway.yml:93` | read both files' `COMPOSE_PROFILES` sections to the end |
| `coder` 2 orphans | `LC_SELF_REMOTE_URL` / `_PAT` have **no reader anywhere in the repo**; kept deliberately and labelled `NO READER TODAY` in the file | `git grep -n LC_SELF_REMOTE_URL -- . ':!documentation'` → expect **no hits outside `coder/.env.example`** |

**Also check:** `DUPLICATE KEYS` must be empty for every plane. The pre-split
root file assigned `GPU_AISTACK_DEVICE_ID`, `GPU_LLAMA_CPP_DEVICE_ID`,
`GPU_LLAMA_CPP_EMBED_DEVICE_ID` and `LC_LLAMA_API_KEY` **twice each** (last-wins,
silently). If any plane file reintroduces that, it is a FAIL.

---

## Case 3 — the root `.env.example` holds ONLY non-plane variables

**Acceptance line:** "The root `.env.example` contains only variables read by
`docker-compose.yml`, the driver, or a script that is not plane-scoped, and its
header says so; a plane variable left in it FAILS."

```powershell
Select-String -Path .env.example -Pattern '^[A-Za-z_][A-Za-z0-9_]*=' | ForEach-Object { $_.Line }
```

**PASS:** exactly three lines — `NAS_BACKUP_USER=`, `NAS_BACKUP_PASSWORD=`,
`TEST_VALIDATION_LLM_KEY=`. For **each**, open its reader and read to the end:

- `scripts/backup/backup-to-nas.ps1` (around `:250`) — confirm it reads the
  **root** `.env`, and that the script sweeps every plane's snapshots (so it is
  genuinely not plane-scoped).
- `scripts/backup/set-nas-credential.ps1` (around `:55`) — same.
- `scripts/issue-ops/issue_ops.py` `cmd_t2` — confirm it now passes
  **`<plane>/.env`** for the plane under test and the **root** `.env.test`
  second, and that the comment explains the ordering.

Then read the root `.env.example` **header** and confirm it states the rule and
lists the plane files. A header that does not say the rule is a FAIL even if the
three lines are right.

**Also confirm the root file must still EXIST:** `docker compose config -q`
(anchor) and `python scripts/stack/stack.py doctor` both look for it, and the
runbook says so. Read `documentation/runbooks/env-split-migration.md` step 5.

---

## Case 4 — unbounded `--env-file` grep, and a `--dry-run` with none

**Acceptance line:** "An unbounded `git grep -n -- '--env-file'` finds no live
invocation that passes the root `.env` to a plane … `stack.py up --dry-run`
prints commands without `--env-file`."

```powershell
git grep -n -- '--env-file'
```

Read **every** hit. Classify each; the plan claims the only surviving live
invocations are these, and your job is to find one it missed:

| where | what it passes | legitimate because |
|---|---|---|
| `.github/workflows/ci.yml:78-86` | `<plane>/.env.example` | the committed example is the deterministic CI input, and naming it suppresses the native load of the CI stub |
| `scripts/checks/check-project-configs.ps1` (`$projects`, `$renderTargets`) | `<plane>/.env.example` | same reason; read the comment above `$projects` |
| `scripts/stack/stack.py:1449` (`render_project`) | `render_env_path(...)` → `<plane>/.env.example` | the inventory generator must be machine-independent; read `render_env_path`'s docstring |
| `scripts/issue-ops/issue_ops.py:978,983` | `<plane>/.env` + root `.env.test` | the generated twin compose lives under `.stack/`, so there is no project directory to load from |
| `scripts/checks/stack-watchdog.ps1:140`, `check-watchdog-repair-targets.ps1:175` | `$Proj.env_file` / `$EnvFile`, behind an `if` | the field is `null` for every project now, so the branch never fires — **verify that**, see Case 6 |
| `scripts/stack/ob1-deploy.ps1:96,164` | an optional `-EnvFile` parameter | OB1, out of scope |

**Anything that passes the ROOT `.env` to a plane is a FAIL.** Comments and
prose that merely mention the flag are not invocations — but read them anyway
and flag any that still *describe* the old behaviour as current.

Excluded from the FAIL rule, by the anchor: `scripts/archive/`,
`documentation/archive/`, `documentation/evidence/`, `documentation/notes/`,
`CLEANUP-PLAN.md`.

Then:

```powershell
python scripts\stack\stack.py up --all --dry-run
```

**PASS:** eight `docker compose -f …` lines, **none** containing `--env-file`,
and nothing started (`recorder.commands == []` is the unit-test form; here just
confirm no container state changed with `docker ps`).

---

## Case 5 — the reader sweep: every script that read a plane value from the root

**Acceptance line:** "Every script that read a plane value from the root `.env`
now reads the plane file (the tester follows the worker's sweep list and reads
each call site **to the end**; a script left reading the old location FAILS)."

This is the case that cannot be done by grep. **Open each file and read the
whole function or block**, not the matched line.

| # | file | what it did | what it must do now |
|---|---|---|---|
| 1 | `scripts/portal/breach-killswitch.ps1` `:101-118` | rotated `AUTHELIA_JWT_SECRET` / `AUTHELIA_SESSION_SECRET` in the **root** `.env`, backing it up as `.env.killswitch-<ts>.bak` | rotate in **`portal/.env`**. Read the whole Step 4 block: the backup path, the two regex replacements, and the warning when the file is absent must all name `portal/.env`. **A killswitch that rotates the wrong file reports success and rotates nothing** — that is the defect this case exists for. |
| 2 | `scripts/portal/breach-killswitch.ps1` `:77` | `--env-file <root>/.env` on the compose `stop` | no flag; `portal/` is the project directory |
| 3 | `scripts/portal/portal-on.ps1` `:45-51` | `--env-file <root>/.env` in `$portalBase` | no flag. Confirm `--profile internet` is still passed on the **command line** for the production branch, and that `portal/.env.example` carries **no** `COMPOSE_PROFILES` line |
| 4 | `scripts/portal/portal-off.ps1` `:49-53` | same flag in `$stopArgs` | no flag |
| 5 | `scripts/recovery/emergency-recovery.ps1` | 25 `--env-file .env` invocations across `Start-PlaneStack`, `Stop-PlaneStack`, the GPU/netns repairs, `nuclear`, `status` | none. Read `Start-PlaneStack` and `Stop-PlaneStack` in full — they take `$ComposePath` for **any** plane, so one missed flag breaks every plane |
| 6 | `scripts/checks/stack-watchdog.ps1` | 12 `--env-file .env` invocations (the frontend render, the tailscale self-heal, the two upstream restarts, the alert text) | none. Read the tailscale self-heal block (`:1770-1790`) and `:1848`'s alert message: the message must tell the operator to put the key in **`frontend/.env`** |
| 7 | `scripts/backup/restore-from-snapshot.ps1` | five `ComposeArgs` entries carrying `--env-file .env` | none. Read the whole `$catalog`: `openwebui` and `tailscale` keep `--profile gpu --profile tailscale`; `mnemory`, `little-coder` lose `ComposeArgs` entirely; `lm-models` **gains** `--profile local` (see findings §10 — this is a deliberate behaviour change, judge it) |
| 8 | `backup/mnemory-restore.sh`, `backup/openwebui-restore.sh` | documented `--env-file .env` stop/start commands in their headers | no flag. These are the commands a human copies during a restore |
| 9 | `scripts/recovery/quick-fixes.bat` | 25 `--env-file .env` lines | none. **Also read findings §5**: this file's 28 *bare* `docker compose` calls target the anchor and have been broken since Part K. Confirm the item did **not** claim to fix that |
| 10 | `scripts/stack/stack.py` `tailscale_deployed()` | rendered `frontend/docker-compose.yml --env-file .env` | no flag. Read the docstring: it must still explain why this one uses the **real** `.env` while `render_env_path()` uses the `.example` |
| 11 | `scripts/stack/stack.ps1` `:80` | error text suggesting the flag | no flag |
| 12 | `scripts/agent-harness/sync-worktree-env.ps1` `:26` | hardcoded 3-file list | `Get-HarnessSetting "worktree.env_files"`. Read findings §4 — the hardcoded list had **already** drifted from the config |
| 13 | `.github/workflows/ci.yml` | `cp .env.example .env` only | also one stub per plane directory, in **both** the `compose-validate` and `stack-driver` jobs |

**Scripts the developer swept and found NOT affected — confirm the negative:**
`scripts/backup/backup-to-nas.ps1`, `scripts/backup/set-nas-credential.ps1`
(root `.env`, correctly — non-plane-scoped); `scripts/lib/mm_lib.py`,
`scripts/mattermost-mcp/server.py`, `scripts/notify-mattermost.sh`,
`scripts/claude-sessions-bridge/bridge.py` (root `.env` as a fallback for
`CLAUDE_MM_BOT_TOKEN`, which is host tooling, not a plane);
`scripts/checks/queue-eta-notify.ps1`, `scripts/issue-ops/github_app_auth.py`
(`agent-org/docker/.env`, out of scope); `scripts/checks/check-backup-coverage.ps1`
(uses `docker volume ls`, renders nothing).

**A FAIL here is any file in the first table still naming the root `.env` for a
plane value, or any file in the second list that turns out to do so.**

---

## Case 6 — the secret guard blocks a staged `<plane>/.env`

**Acceptance line (anchor, with `why`):** "the guard blocks a staged
`<plane>/.env` (the tester stages a scratch one and confirms the block, then
unstages)."

In **your clone** (never the developer's worktree, never the main checkout):

```powershell
git add -f portal\.env frontend\.env
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-staged-secrets.ps1
$LASTEXITCODE            # <- read THIS, not a piped tail's exit code
git restore --staged portal\.env frontend\.env
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-staged-secrets.ps1
$LASTEXITCODE
```

**PASS:** first run exits **1** and prints `ENV FILE STAGED: frontend/.env` and
`ENV FILE STAGED: portal/.env`; second run exits **0** and prints
`staged files clean (N scanned)`.

Then read `scripts/checks/check-staged-secrets.ps1` rule 1 to the end and
confirm **why** it works: it matches the file's **leaf**, not its path. The item
added a comment saying so and changed no logic — judge whether the comment is
true. Also confirm `<plane>/.env.example` is *allowed* (the `$allowNames` /
`*.env.example` arms) — the six new example files must be committable.

And:

```powershell
git check-ignore -v frontend\.env inference\.env memory\.env search\.env coder\.env portal\.env
git check-ignore -v frontend\.env.example     # expect NO output, exit 1
```

**PASS:** all six resolve to the `.env` rule; the example does not. Read the new
comment at the top of `.gitignore` and confirm it explains the no-leading-slash
reason correctly.

---

## Case 7 — a throwaway worktree renders every plane

**Acceptance line:** "`new-worktree.ps1` provisions a worktree that renders
every plane … a worktree missing a plane env FAILS."

This one needs the **operator's host** (the env files it copies are the real
ones) and it writes a worktree. Ask before running it, and remove the worktree
afterwards.

```powershell
cd "D:\Open WebUI\ai-stack"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent-harness\new-worktree.ps1 -Id envsplitchk -Base work/sl-env-split
```

In the path it prints:

```powershell
Get-ChildItem -Force frontend\.env, inference\.env, memory\.env, search\.env, coder\.env, portal\.env | Select-Object FullName, Length
foreach ($p in 'frontend','inference','memory','search','coder','portal') {
  docker compose -f "$p\docker-compose.yml" config -q; "$p exit=$LASTEXITCODE"
}
```

**PASS:** all six files present and non-empty; all six renders exit 0. **Do not
print any file's contents** — `Length` is the evidence you need.

Then remove it: `scripts\agent-harness\remove-worktree.ps1 -Id envsplitchk` (or
`git worktree remove`), and confirm it is gone.

**If you cannot get operator sign-off to provision a worktree**, say the case
was NOT RUN. Do **not** substitute "the config lists six files, so it would
work" — reading the list is Case 8, and a list is not a provisioning.

---

## Case 8 — the harness config, and the two scripts that consume it

```powershell
python -c "import json;d=json.load(open('scripts/agent-harness/harness.config.json',encoding='utf-8'));print('\n'.join(d['worktree']['env_files']))"
python -m pytest scripts/agent-harness/test_harness_config.py -q
```

**PASS:** twelve entries — `.env`, `.env.test`, the six plane files,
`OB1/docker/.env`, the two OB1 recipe files, `agent-org/docker/.env`; and the
config tests green.

Read `scripts/agent-harness/new-worktree.ps1:52` and `sync-worktree-env.ps1`'s
`$EnvFiles` line and confirm **both** now read the same setting. Read the
`_env_files_note` array and judge whether its new paragraph is true.

---

## Case 9 — the migration runbook is complete and correctly ordered

**Acceptance line:** "`documentation/runbooks/env-split-migration.md` lists every
variable with its source line and destination file, and states the order (write
plane files first, verify renders, then trim the root); the live stack is NOT
migrated by this item and no container is restarted."

Read it to the end. Then verify the table **mechanically** — the risk is a
runbook that looks complete and has dropped a variable:

```python
# scratch: from the clone root
import re, subprocess
old = subprocess.run(["git","show","<pre-split-commit>:.env.example"],
                     capture_output=True, text=True).stdout.splitlines()
names = [m.group(1) for m in
         (re.match(r'([A-Za-z_][A-Za-z0-9_]*)=', l.strip()) for l in old) if m]
doc = open("documentation/runbooks/env-split-migration.md", encoding="utf-8").read()
missing = [n for n in names if f"| `{n}` |" not in doc]
print("assignments in the pre-split file:", len(names))
print("MISSING FROM THE TABLE:", missing)
```

(`<pre-split-commit>` is this branch's merge-base with `development`, or just
`HEAD~1` if the item is one commit.)

**PASS:** 157 assignments, `MISSING FROM THE TABLE: []`.

Then check the table's *destinations* against Case 2's answer: every name whose
destination is `<plane>/.env` must actually appear in that plane's
`.env.example`, and no name may land somewhere its plane does not read.

```python
for line in doc.splitlines():
    m = re.match(r'\| `([A-Za-z_][A-Za-z0-9_]*)` \| \d+ \| (.*) \|', line)
    if not m: continue
    name, dest = m.groups()
    for p in ("frontend","inference","memory","search","coder","portal"):
        if f"`{p}/.env`" in dest:
            body = open(f"{p}/.env.example", encoding="utf-8").read()
            if not re.search(rf'^#?{name}=', body, re.M):
                print("NOT IN THE FILE IT NAMES:", name, "->", p)
```

**PASS:** no output. (`COMPOSE_PROFILES`, the three DELETED names, the seven
NOT-WIRED search tunables and the thirteen OB1 names have prose destinations and
are skipped by the regex — check those six groups by hand against findings
§1, §2 and §7.)

Finally, confirm the **order claim**: the runbook must write plane files (step
1-2), verify (3-4), and only then trim the root (5). A runbook that trims first
leaves the stack without values and is a FAIL regardless of how complete its
table is.

---

## Case 10 — the checks, the driver, and the hooks

All in the clone, with the Case 0 set-up applied.

```powershell
# whole-delta staged, so the staged-aware checks actually see this item
git add -- . ':!OB1'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
$LASTEXITCODE
```

**PASS:** `all 9 compose projects render clean` (**nine**, not eight — the item
adds an `inference (local)` target so the four profile-gated inference services
stop being silently unverified), `stack-services.json inventory matches the
compose configs [rows verified/expected: inference:8/8 frontend:4/4 memory:3/3
search:4/4 coder:4/4]`, `[OK] scripts/lib/stack-services.json matches …`,
`staged .ps1 file(s) parse clean`, `staged .json file(s) are strict-valid`, exit
0.

**Prove the check is not vacuous** before you believe it: temporarily break one
plane example (`(Get-Content memory\.env.example) -replace '^MCP_API_KEY=.*','MCP_API_KEY=' | Set-Content memory\.env.example`),
re-stage, re-run, and confirm it goes RED naming `memory`. Restore afterwards.

```powershell
ruff check .
python -m pytest scripts\stack -q
python scripts\stack\stack.py inventory --check
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-llm-gateway-routing.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-env-file-scope.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-doc-placement.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\validate-lineendings.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-watchdog-repair-targets.ps1
```

**PASS:** ruff `All checks passed!`; **107 tests** green (the count is unchanged
— this item rewrote expectations, it did not add cases; if you think a new case
was owed, say so, that is a legitimate review finding); `inventory --check`
`[OK]`; routing `OK - no LLM gateway bypasses found`; env-file-scope `no new
shared-.env grants staged`; doc-placement `no new planning material staged`;
line-endings `SUCCESS`; repair targets `REPAIR TARGETS OK: 24 container(s)`.

Two notes on those last two:
- `validate-lineendings.ps1` only checks tracked `*.sh`, so it is nearly
  vacuous here — say so rather than counting it as coverage.
- `check-watchdog-repair-targets.ps1` is the **load-bearing** one: it renders
  every watchdog repair target with **no** `--env-file`, so 24/24 is the direct
  evidence that the watchdog's self-heal still resolves after the split. Read
  `:175` and confirm the `if ($EnvFile)` branch is simply never taken now.

Then, on the driver:

```powershell
python scripts\stack\stack.py doctor
```

**PASS:** each enabled plane reports `env <plane>/.env (compose loads it from
the project dir)`. **Prove it detects a blank key**: blank `WEBUI_SECRET_KEY` in
the clone's `frontend/.env`, re-run, confirm `WEBUI_SECRET_KEY is blank in
frontend/.env`, restore.

---

## Case 11 — the tree-wide citation sweep, BOTH forms

**The lesson this case encodes:** nine sibling items have now been bitten by
`path:line` citations going stale, and a suffix match is not an anchored one.

Run the sweep on the item's **output**, not on its inputs. For every file whose
line count changed in `git diff --numstat HEAD~1` (or against the merge-base),
search for citations to it in **both** the full-path form and the bare-basename
form, across every tracked file outside `OB1/`.

```python
# scratch: citesweep.py
import re, subprocess, os
out = subprocess.run(["git","diff","--numstat","<base>"], capture_output=True, text=True).stdout
changed = [l.split("\t")[2] for l in out.splitlines()
           if l.split("\t")[0] != "-" and l.split("\t")[0] != l.split("\t")[1]]
cons = set()
for c in changed:
    cons.add(re.escape(c)); cons.add(re.escape(os.path.basename(c)))
pat = "(" + "|".join(sorted(cons, key=len, reverse=True)) + r")[`'\"]?:(\d+)"
def old(f): return subprocess.run(["git","show",f"<base>:{f}"],capture_output=True,text=True).stdout.splitlines()
for f in subprocess.run(["git","ls-files"],capture_output=True,text=True).stdout.splitlines():
    if f.startswith("OB1"): continue
    try: lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
    except Exception: continue
    for i, line in enumerate(lines, 1):
        for m in re.finditer(pat, line):
            tgt, num = m.group(1), int(m.group(2))
            o = old(tgt); n = open(tgt,encoding="utf-8",errors="replace").read().splitlines() if os.path.exists(tgt) else []
            ol = o[num-1] if 0 < num <= len(o) else "<past end>"
            nl = n[num-1] if 0 < num <= len(n) else "<past end>"
            if ol == nl: continue              # still points at the same content
            moved = [j+1 for j,x in enumerate(n) if x == ol]
            print(f"{f}:{i} -> {tgt}:{num}  {'MOVED->'+str(moved) if moved else 'GONE'}")
```

**How to judge each hit** — this is the part that cannot be automated:

- **`MOVED`, and the citing file is live code or a living doc** → must be
  repointed. The item claims six such repoints (findings §8); verify each by
  opening the citing line and the new target line.
- **`MOVED`/`GONE`, and the citing file is `documentation/evidence/*` or
  `documentation/notes/*`** → must **NOT** be repointed. Those are dated records
  of what someone observed. Confirm the item left them alone.
- **`GONE` and it was already wrong before this item** → the item lists seven
  such (findings §8, second table). Spot-check at least three against
  `git show <base>:<target>` to confirm the "already stale" claim rather than
  accepting it.
- **A bare-basename hit in a different directory** → false positive. The sweep
  produced exactly one (`owui/README.md:63` matching `README.md:63`). If you
  find another the item acted on, that is a FAIL.

**Separately, confirm the compose files did NOT move any line.** `stack.manifest.toml`
cites them at 99 anchors:

```powershell
foreach ($f in 'docker-compose.yml','frontend\docker-compose.yml','inference\docker-compose.yml',
               'inference\compose\upstreams.yml','inference\compose\queue.yml','inference\compose\gateway.yml',
               'inference\compose\backups.yml','memory\docker-compose.yml','search\docker-compose.yml',
               'coder\docker-compose.yml','portal\docker-compose.yml') {
  $o = (git show "<base>:$($f -replace '\\','/')" | Measure-Object -Line).Lines
  $n = (Get-Content $f | Measure-Object -Line).Lines
  "{0,-38} old={1,-5} new={2,-5} {3}" -f $f, $o, $n, $(if ($o -eq $n) {'OK'} else {'CHANGED - re-derive its citations'})
}
```

**PASS:** every line `OK`. A `CHANGED` means every `stack.manifest.toml`
citation into that file needs re-deriving, and the item did not do it.

---

## Case 12 — read the compose guard messages

Every `${VAR:?...}` guard must name the **plane file**, not `--env-file`:

```powershell
git grep -n ':?set' -- '*.yml'
```

**PASS:** five guards — `frontend` (**two**, the `stock` and `gpu` definitions;
both must name `frontend/.env`), `inference/compose/gateway.yml`, `memory`,
`search`, `coder`. None may say `--env-file`, `../.env`, or `.env` unqualified.

Then read each plane compose file's **header block** and confirm its
"Drive it with …" line no longer shows `--env-file`, and that its
`COMPOSE_PROFILES` paragraph describes the **per-plane** value (D17), not the
old global one. The frontend's must say `gpu,tailscale`; the inference's must
say `local`; neither may still claim `local,gpu,tailscale`.

---

## Reporting

For each case: **PASS / FAIL / NOT RUN**, with the command you ran and the
output you saw. For any FAIL, say which acceptance line it breaks and whether it
is a defect in the item or in something the item merely touched.

Please also give a judgement on two things the plan cannot decide for you:

1. **findings §10** — the `--profile local` added to the `lm-models` restore
   entry. In scope, or scope creep in a disaster-recovery path?
2. **findings §5 and §7** — two real defects the item found and deliberately did
   **not** fix (`update-stack.bat`'s bare compose calls; `OB1/docker/.env.example`
   missing thirteen variables). Correctly deferred, or should one of them have
   blocked this item?
