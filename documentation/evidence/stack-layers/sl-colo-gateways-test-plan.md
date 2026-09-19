# sl-colo-gateways - test plan

**Item:** `sl-colo-gateways` (stack-layers PLAN.md section 2.7, wave 1)
**Branch:** `work/sl-colo-gateways`  **Base:** `development` @ b28cbc5
**Developer worktree:** `wt-sl-colo-gateways` (do not test from it - you did not write this,
and you should not inherit its shell state)
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-colo-gateways.json`
**Findings sink:** `documentation/notes/stack-layers-sl-colo-gateways-findings.md`

## What the change is

Two `git mv`s and the pointer repoints that follow them:

| from | to |
|---|---|
| `search-gateway/` | `search/gateway/` (so: `search/gateway/gateway/` = the Python project, `search/gateway/searxng/` = the bind-mounted SearXNG config) |
| `mnemory-gateway/` | `memory/mnemory-gateway/` |

No service, container, image, network or volume was renamed. No image was built or
retagged. No container was restarted. Nothing under `scripts/archive/`,
`documentation/archive/`, `documentation/notes/` or `CLEANUP-PLAN.md` was touched.

## Before you start

Get your own worktree on the branch (do not reuse the developer's):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Open WebUI\ai-stack\scripts\agent-harness\new-worktree.ps1" -Id <your-id> -Base work/sl-colo-gateways
```

**Read file contents out of git, not off the operator's disk:**
`git show work/sl-colo-gateways:<path>` - e.g.
`git show work/sl-colo-gateways:search/docker-compose.yml`. The main checkout at
`D:\Open WebUI\ai-stack` is on a different branch and still has the OLD layout on disk;
reading it will tell you the move did not happen.

**No lease is needed.** Every case below is read-only or renders compose; none of them
starts, stops, builds or restarts anything. If a case tempts you to run `docker compose
up`, `build`, or `restart` - it is the wrong case. T6 exists to prove you did not.

All commands are run from the **root of your worktree** unless the case says otherwise.

---

## T1 - History survives both renames

Anchor criterion 1. Note: the anchor names `search/gateway/app.py`, which does not exist
and never did - the search gateway is a package whose entry module is `main.py`. Findings
section 1 explains; the substituted paths below test the same property (a rename that git
recorded as a rename, whose history `--follow` can walk back past).

```bash
git log --follow --oneline -- search/gateway/gateway/src/gateway/main.py | wc -l
git log --follow --oneline -- search/gateway/searxng/settings.yml | wc -l
git log --follow --oneline -- memory/mnemory-gateway/app.py | wc -l
```

**PASS:** each count is greater than 1, and each listing contains commits predating the
move commit on this branch. Confirm the rename was recorded as a rename, not a
delete-plus-add:

```bash
git show --stat -M --name-status <the move commit> | head -50
```

**PASS:** the lines are `R100` (or `R0xx`) `search-gateway/... -> search/gateway/...` and
`mnemory-gateway/... -> memory/mnemory-gateway/...`, 37 renames in all (34 search, 3
memory), and there are **no** `A`/`D` pairs for the same content.

**FAIL:** a count of 1 or 0; any `A` + `D` pair where a rename was expected.

## T2 - Both planes render, and every build context and bind source resolves

Anchor criterion 2.

```bash
docker compose -f search/docker-compose.yml --env-file .env.example config --format json > search-after.json
docker compose -f memory/docker-compose.yml --env-file .env.example config --format json > memory-after.json
```

**PASS:** both exit 0. Then read the paths out of the render rather than trusting the
source file:

```bash
python - <<'EOF'
import json
for p in ['search-after.json','memory-after.json']:
    d=json.load(open(p)); print('==',p)
    for name,s in sorted(d['services'].items()):
        if 'build' in s: print('  build',name,'->',s['build'].get('context'))
        for v in s.get('volumes',[]) or []:
            if v.get('type')=='bind': print('  bind ',name,'->',v.get('source'))
EOF
```

**PASS:** the search render shows exactly

- `build gateway -> <worktree>\search\gateway\gateway`
- `bind  searxng -> <worktree>\search\gateway\searxng`

and the memory render shows

- `build mnemory-cloud-gateway -> <worktree>\memory\mnemory-gateway`

and each of those three absolute paths exists (`ls -d` on each). Both gateway paths are
**inside their plane directory**.

**Two paths in the memory render do NOT exist and that is correct** - both are
pre-existing and untouched by this item (findings section 5):

- `build mnemory -> <the worktree's parent dir>\mnemory` - from a worktree under
  `.claude/worktrees/wt-*` that renders as `...\.claude\worktrees\mnemory`. It is meant to
  be the sibling `mnemory` checkout beside `ai-stack/`, i.e. outside
  this repository by design (`context: ../../mnemory`; the service is pinned
  `image: mnemory:local` + `pull_policy: never` so it is never built). `memory/README.md`
  documents it.
- `bind mnemory-backup -> <worktree>\backups\mnemory` - gitignored artifact output; compose
  creates it on `up`.

Verify they are untouched rather than taking this on trust:

```bash
git diff b28cbc5 -- memory/docker-compose.yml
```

**PASS:** the only hunk is `context: ../mnemory-gateway` -> `context: ./mnemory-gateway`.

**FAIL:** either `config` exits non-zero; a rendered gateway context or the searxng bind
resolves outside its plane directory or to a path that does not exist; the `mnemory` build
context or the backup bind changed.

## T3 - The moved suite passes from its new path, and CI invokes that path

Anchor criterion 3, in the form that works. **Read findings section 2 before running this
case** - the anchor's literal command (`python -m pytest search/gateway -q`) fails, and
failed identically on `development` before the move. T3b reproduces that so you can see it
is not a regression.

**T3a - the suite (this is the pass/fail case):**

```bash
python -m venv .venv-test
./.venv-test/Scripts/python.exe -m pip install -e "./search/gateway/gateway[dev]"
./.venv-test/Scripts/python.exe -m pytest -q search/gateway/gateway/tests
```

(On Linux, `./.venv-test/bin/python`.)

**PASS:** `36 passed, 1 deselected`. The deselected one is the `integration` marker, which
`addopts = "-m 'not integration'"` in the package's own `pyproject.toml` excludes - that is
the configuration being picked up correctly.

**FAIL:** any failure or error; a collection count other than 37; `0 items collected`
(means the path is wrong).

**T3b - the anchor's literal command, and the proof it is pre-existing (informational):**

```bash
./.venv-test/Scripts/python.exe -m pytest search/gateway -q ; echo "---- now the pre-move tree ----"
mkdir -p /tmp/premove && git archive b28cbc5 search-gateway | tar -x -C /tmp/premove
cd /tmp/premove && ../../.venv-test/Scripts/python.exe -m pytest search-gateway -q
```

(Use an absolute path to the venv python for the second run.)

**PASS for this case:** both invocations report the **same** result -
`13 failed, 24 passed`. That is the falsification: the failure belongs to pytest's
rootdir/config discovery from the repo root, not to the move. The developer measured
`13 failed, 24 passed, 2 warnings in 11.66s` (after) and `... in 11.69s` (before) on
2026-09-19.

**FAIL for this case:** the post-move run is worse than the pre-move run (more failures, or
different tests failing). That would mean the move broke something the rootdir issue masks.

**T3c - CI:**

```bash
git show work/sl-colo-gateways:.github/workflows/ci.yml | sed -n '105,113p'
```

**PASS:** the `pytest-search-gateway` job runs
`pip install -e "./search/gateway/gateway[dev]"` and `pytest -q search/gateway/gateway/tests`.
The job **name** is unchanged on purpose (it is a required-check name; findings section 10).
Also confirm the `compose-validate` job still renders both planes (it names
`search/docker-compose.yml` and `memory/docker-compose.yml`, which did not move).

**FAIL:** either CI path still contains `search-gateway/`.

## T4 - Unbounded grep: no live pointer still names an old path

Anchor criterion 4. **Do not pipe this through `head`.**

**T4a - the decidable case.** Old paths, excluding the four directories the anchor exempts,
and excluding the two NEW spellings (`search/gateway/` and `memory/mnemory-gateway/`), which
contain the old strings as substrings. `documentation/evidence/stack-layers/` is excluded
too - it is THIS file, whose whole job is to name the old paths beside the new ones:

```bash
git grep -n -e "search-gateway/" -e "mnemory-gateway/" -- . \
  ':!scripts/archive' ':!documentation/archive' ':!documentation/notes' ':!CLEANUP-PLAN.md' \
  ':!documentation/evidence/stack-layers' \
  | grep -v "search/gateway/" | grep -v "memory/mnemory-gateway/"
```

**PASS:** exactly 4 lines, all in `documentation/evidence/research-trust/TEST-PLAN.md`, and
each one is a historical statement that would become FALSE if repointed:

| line | what it is | why it stays |
|---|---|---|
| 25, 369 | `git show work/research-trust:search-gateway/searxng/settings.yml` | that branch holds the blob at the old path; repointing breaks the command |
| 64 | `cd .../wt-research-trust/search-gateway/gateway` | a worktree that no longer exists; dead at either spelling |
| 462 | "the parent diff touches only `documentation/`, `search-gateway/`, ..." | an assertion about a diff taken in September; true as written |

The two lines in that same file that ARE live procedure (801, the image-bump rule; 815, the
rollback `git checkout`) were repointed - verify:

```bash
git show work/sl-colo-gateways:documentation/evidence/research-trust/TEST-PLAN.md | sed -n '801p;815,816p'
```

**PASS:** both now say `search/gateway/...`.

**FAIL:** any line outside that file; any hit in a compose file, CI workflow, check script,
README, `CLAUDE.md`, `.env.example`, the stack-map reference, `SECURITY.md` or a runbook.

**T4b - the bare-name sweep (manual classification).**

Exclude this item's own two documents (this test plan and the findings note both name the
old paths on purpose, and both would otherwise move the counts):

```bash
EX=(-- . ':!documentation/evidence/stack-layers' ':!documentation/notes/stack-layers-sl-colo-gateways-findings.md')
git grep -n "search-gateway"  "${EX[@]}" | wc -l      # expect 63
git grep -n "mnemory-gateway" "${EX[@]}" | wc -l      # expect 12
git grep -n "search-gateway"  "${EX[@]}"
git grep -n "mnemory-gateway" "${EX[@]}"
```

(Without the exclusion the counts are 105 and 35 - the difference is this plan and the
findings note.)

**PASS:** every remaining hit is one of these, and none of them is a filesystem path:

- the **container** name `search-gateway` (`search/docker-compose.yml:120`,
  `scripts/recovery/emergency-recovery.ps1`, `scripts/checks/stack-watchdog.ps1`,
  `scripts/lib/stack-services.json`, `documentation/CONTAINER-REGISTRY.md`,
  `.claude/skills/stack-map/references/workspace-stacks.md:179`, `search/README.md:36,216`)
- the **image** / Python package name `private-search-gateway` (`search/docker-compose.yml:119`,
  `search/gateway/gateway/pyproject.toml:2`, `.../mcp_server.py:20`,
  `search/gateway/searxng/settings.yml:15`, `search/README.md:109,118`)
- the **CI job** name `pytest-search-gateway` (`.github/workflows/ci.yml:105`)
- the archived plan's **filename** `integration-plan-private-search-gateway.md`
- prose in `llm-queue/src/llm_queue/logging.py:1` ("mirroring the search-gateway setup")
- the four exempt locations: `scripts/archive/`, `documentation/archive/`,
  `documentation/notes/`, `CLEANUP-PLAN.md`
- the NEW correct spellings `search/gateway/...` and `memory/mnemory-gateway/...`

**FAIL:** a service, container, image, network or volume was renamed (the anchor forbids
it); or a path-shaped hit outside the exempt set that T4a's filter happened to swallow.

**T4c - the repointed pointers actually resolve.** For each file:line below, read the blob
and check the target exists.

```bash
for p in search/docker-compose.yml memory/docker-compose.yml .github/workflows/ci.yml \
         search/README.md search/gateway/README.md memory/README.md README.md CLAUDE.md \
         .env.example documentation/implementation-guide/README.md \
         .claude/skills/stack-map/references/workspace-stacks.md \
         openbrain-gateway/app.py little-coder/README.md; do
  echo "== $p"; git show work/sl-colo-gateways:$p | grep -n "gateway" | grep -i "search/gateway\|memory/mnemory-gateway\|\.\./\.\./\.\./documentation-plans"
done
```

**PASS:** every path named resolves (`ls -d` / `ls` on each). Spot-check in particular:

- `search/gateway/README.md` line 8 - `../../../documentation-plans-ai-stack/...`
  (**three** levels; the README moved one directory deeper. At two levels it would resolve
  to `ai-stack/documentation-plans-ai-stack/`, a plausible-looking path inside this repo.
  Check the sibling plan store actually sits at that resolved location.)
- `search/gateway/README.md` line 9 - `../../documentation/archive/implementation-guide/web-search/integration-plan-private-search-gateway.md`
- `README.md:147` - the repo-map row now reads `search/gateway/`, `memory/mnemory-gateway/`.
  (It previously said `mnemory-cloud-gateway/`, a directory that never existed - findings
  section 6.)
- `openbrain-gateway/app.py:8` - `../memory/mnemory-gateway/app.py`. This is the one file
  outside `search/` and `memory/` whose *source* was edited; it is a one-line docstring
  pointer, `openbrain-gateway/` itself did not move (anchor out-of-scope), and leaving the
  line would have failed T4a.

**FAIL:** any repointed link that does not resolve; any edit to `openbrain-gateway/` beyond
that single comment line (`git diff b28cbc5 -- openbrain-gateway/` must be one line).

## T5 - Normalized render diff: only the paths moved

Anchor criterion 5 - container names, image tags and network memberships unchanged.

The two renders come from different directories, so every absolute path in them carries a
different prefix. `norm.py` below sorts the keys (compose does not emit them in a stable
order) **and** substitutes each render's own repo root with `<ROOT>`, so what is left is
only where the compose files genuinely differ. This exact recipe was run by the developer
on 2026-09-19 and produced the result stated below.

Write `norm.py` anywhere outside the repo:

```python
import json
import sys

BS = chr(92)  # backslash

root, path = sys.argv[1], sys.argv[2]
s = json.dumps(json.load(open(path)), sort_keys=True, indent=2)
for r in {root, root.replace("/", BS)}:
    s = s.replace(json.dumps(r)[1:-1], "<ROOT>")  # match the JSON-escaped form
print(s)
```

Then, from your worktree:

```bash
W=/tmp/sl                        # scratch, anywhere outside the repo
mkdir -p $W/base

# BEFORE - development's own tree, extracted pristine
git archive b28cbc5 | tar -x -C $W/base
( cd $W/base
  docker compose -f search/docker-compose.yml --env-file .env.example config --format json > $W/s-base.json
  docker compose -f memory/docker-compose.yml --env-file .env.example config --format json > $W/m-base.json )

# AFTER - this branch
docker compose -f search/docker-compose.yml --env-file .env.example config --format json > $W/s-after.json
docker compose -f memory/docker-compose.yml --env-file .env.example config --format json > $W/m-after.json

# normalize with each render's OWN root, then diff
BASE=$(cd $W/base && pwd -W)     # pwd -W gives the Windows form compose emitted; use pwd on Linux
WT=$(pwd -W)
python $W/norm.py "$BASE" $W/s-base.json  > $W/s-base.norm.json
python $W/norm.py "$WT"   $W/s-after.json > $W/s-after.norm.json
python $W/norm.py "$BASE" $W/m-base.json  > $W/m-base.norm.json
python $W/norm.py "$WT"   $W/m-after.json > $W/m-after.norm.json
echo "== search"; diff -u $W/s-base.norm.json $W/s-after.norm.json
echo "== memory"; diff -u $W/m-base.norm.json $W/m-after.norm.json
```

**PASS:** the search diff has exactly two changed values -

- `services.gateway.build.context` : `<ROOT>\search-gateway\gateway` -> `<ROOT>\search\gateway\gateway`
- `services.searxng.volumes[0].source` : `<ROOT>\search-gateway\searxng` -> `<ROOT>\search\gateway\searxng`

and the memory diff has exactly two -

- `services.mnemory-cloud-gateway.build.context` : `<ROOT>\mnemory-gateway` -> `<ROOT>\memory\mnemory-gateway`
- `services.mnemory.build.context` : **an unsubstituted absolute path on both sides, and
  they differ.** This one is expected and is NOT a change to the compose file: the context
  is `../../mnemory`, which points *outside* the repo root, so `<ROOT>` cannot absorb it and
  each render names its own parent directory. Confirm the compose file itself is untouched
  here with `git diff b28cbc5 -- memory/docker-compose.yml` (one hunk, the cloud-gateway
  context). Findings section 5.

Note the anchor says the renders should "differ only in build.context values"; the searxng
**bind source** also changes, because the SearXNG config moved with the tree. That is the
intended second half of the move, not drift.

**PASS also requires** that these are IDENTICAL on both sides, for every service in both
planes: `container_name`, `image`, `networks`, `ports`, `volumes` (other than the one source
above), `environment`, `healthcheck`, `depends_on`, and the top-level `name`, `networks` and
`volumes` blocks. A diff of the four values above proves it; anything longer, check each
extra hunk against this list.

**FAIL:** any change to a container name, image tag, network name or membership, published
port, volume name, or the compose project `name`.

## T6 - Hooks, lint, and nothing live was touched

Anchor criterion 6.

```bash
# the hooks ran on the developer's commit; re-prove them
git config core.hooksPath      # expect .githooks
git show work/sl-colo-gateways --stat | tail -30
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\validate-lineendings.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-llm-gateway-routing.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-doc-placement.ps1
python -m ruff check search/gateway memory/mnemory-gateway openbrain-gateway
python -m ruff check .
```

**PASS:**

- the commit was made **with** hooks (no `--no-verify`; the commit exists and the hooks are
  installed at `.githooks`)
- `ruff check search/gateway memory/mnemory-gateway openbrain-gateway` -> "All checks passed!"
- `ruff check .` -> **one** error, and it is
  `E501 llm-queue/src/llm_queue/__init__.py:9:101 Line too long (103 > 100)`.
  **This is pre-existing on `development`** and unrelated to this item - it was introduced
  by cfa7d4e (the 2026-09-18 plan-store move) lengthening a docstring path. Confirm it is
  not ours: `git diff b28cbc5 --stat -- llm-queue/` must be **empty**. Findings section 3.
  A *second* ruff error, or that error appearing in a file this item touched, is a FAIL.

**No-touch proof.** These must all still be true:

```bash
docker image ls private-search-gateway --format "{{.Repository}}:{{.Tag}} {{.CreatedAt}}"
docker image ls mnemory --format "{{.Repository}}:{{.Tag}} {{.CreatedAt}}"
docker ps --filter name=search- --filter name=searxng --filter name=mnemory \
          --format "{{.Names}} {{.Status}}"
```

**PASS:** `private-search-gateway:local` and `mnemory:local` carry their pre-existing
creation timestamps (no `:wt-*` tag was created either, because nothing was built), and
`search-vpn`, `search-redis`, `searxng`, `search-gateway`, `mnemory`,
`mnemory-cloud-gateway`, `mnemory-backup` show uptimes that predate this item's work.

**FAIL:** any of those images rebuilt or retagged; any of those containers restarted.

## T7 - Intent (the anchor, not just the checks)

Read the anchor and answer these in your evidence, in your own words:

1. Are the search and memory planes now **self-contained** - is there anything left at the
   repo root that either plane's compose file builds or binds, other than the two
   deliberate exceptions (`../../mnemory`, out of repo; `../backup/mnemory-backup.sh` +
   `../backups/mnemory`, the shared backup convention)?
2. Did anything change **besides** paths? Behaviour, allow-lists, policy, engine config,
   `.env` handling - all out of scope. `git diff b28cbc5 -M --name-status` and then read
   the non-rename hunks: 37 renames (34 search, 3 memory), 13 modified files, one
   modified-in-rename (`search/gateway/README.md`, `R094` - its two relative links gained a
   `../`), and two added (this plan and the findings note). No hunk that is not a path.
3. `search/gateway/gateway/` nests "gateway" twice. That is what the anchor's artifact
   line literally specifies and it is what was built; findings section 4 records the
   alternative. Is the literal reading the right call, or should this go back? That is a
   judgement for the reviewer and the operator, not a test failure.

---

## Evidence to record

For each case: the command you ran, its full output (T4a and T4b unbounded), and PASS/FAIL.
Write anything you find that is true and outside this item into
`documentation/notes/stack-layers-sl-colo-gateways-findings.md` - it already has ten
sections; add yours rather than starting a new file.
