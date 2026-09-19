# sl-colo-gateways - test plan (attempt 3)

**Item:** `sl-colo-gateways` (stack-layers PLAN.md section 2.7, wave 1)
**Branch:** `work/sl-colo-gateways`  **Base:** `development` @ b9fff95
**Developer worktree:** `wt-sl-colo-gateways` - do not test from it, and do not write into it
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-colo-gateways.json`
(**AMENDED 2026-09-19** at the release gate - read the amended file, not attempt 1's)
**Findings sink:** `documentation/notes/stack-layers-sl-colo-gateways-findings.md`

## How to read the numbers in this plan

Every expected value below was **measured** by the developer against *this* commit
(`work/sl-colo-gateways`, rebased onto b9fff95) on 2026-09-19, by running the command printed
beside it. Attempt 2's plan quoted numbers carried over from an earlier attempt without
saying so, which the tester raised as P6; there are none of those left in this plan, and the
convention if any ever reappear is a **[prediction]** tag on the line. A quoted value that
does not match is a defect in the plan - record it as such in your evidence rather than
silently accepting or silently failing it.

One thing here is deliberately NOT a measurement: T6d's docker output depends on the live
host, which moves on its own. Judge those by the stated property (uptime predates this item,
no `:wt-*` tag, no rebuild), not by a literal string.

## What changed since attempt 2

Attempt 2 passed 7/7 and the plan was marked inadequate on two counts, both fixed here:

- **P5** - T6c told you to `cd` into a scratch worktree under the scratchpad. That path is
  long enough to hit MAX_PATH; the `cd` failed, and the `git reset --soft` on the next line
  then ran **in the developer's worktree**. T6c now uses a short path and drives every git
  step with `git -C`, so a failed `cd` cannot redirect a command at the wrong repository.
  The sentence claiming the developer had run that recipe at a scratchpad path is gone.
- **P6** - every quoted output in this plan was re-measured against this commit; none is
  carried over unlabelled from an earlier attempt (see the note above).

Also new: the base moved six merges (`sl-colo-portal`, `sl-manifest`, `sl-inference-split`,
`sl-closeout`, `sl-ob1-profiles`, `sl-frontend-solo`), so this branch was rebased onto
b9fff95 and re-submitted per DECISIONS D18. Findings section 13 lists every conflict and its
resolution, and the citation sweep. Two facts this plan asserted before have changed under
the rebase and are corrected here: `ruff check .` is now **clean** (sl-closeout fixed the
llm-queue line - findings 3), and `README.md`'s repo-map row now carries sl-closeout's
clarification as well as this item's repointing (findings 6).

## The change under test

| from (on `development`) | to |
|---|---|
| `search-gateway/gateway/` (pyproject, Dockerfile, `src/`, `tests/`) | `search/gateway/` |
| `search-gateway/searxng/` (limiter.toml, settings.yml) | `search/searxng/` |
| `search-gateway/README.md`, `search-gateway/.gitignore` | `search/gateway/` |
| `mnemory-gateway/` | `memory/mnemory-gateway/` |

`search-gateway/` is gone. No service, container, image, network or volume was renamed. No
image was built or retagged. No container was started, stopped or restarted.

**A5 - the one edit that is not a path.** `ci.yml`'s `pytest-search-gateway` job changed from
`pip install -e ...` / `pytest -q <path>` to `python -m pip install -e ...` /
`python -m pytest <path> -q`. The `python -m` prefixes and the moved `-q` are a *command*
change: they reproduce the amended anchor's criterion-3 command verbatim so the gate and the
CI job cannot drift apart. Everything else in the diff is a path. T3b checks it; T7.2 is
where you decide whether that was the right call.

## Before you start - and where to put your files

Get your own worktree on the branch:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Open WebUI\ai-stack\scripts\agent-harness\new-worktree.ps1" -Id <your-id> -Base work/sl-colo-gateways
```

**Everything you create goes outside a repo tree** - not the venv, not the JSON renders, not
`norm.py`, not the pristine extracts. Attempt 1's plan had you write them into the tree under
test; none of those names is gitignored, and the renders contain interpolated `.env.example`
values. Pick one scratch directory and use it throughout:

```bash
S=<your scratchpad>/slcolo && mkdir -p "$S"
```

**Two path lengths matter, and they are different.** `$S` is fine for files. It is NOT fine
for a git checkout: T6c needs a working tree, and the scratchpad path plus this repo's
deepest file blows MAX_PATH - that is exactly how attempt 2's tester ended up running a
`reset --soft` inside the developer's worktree. T6c uses a short path for that, and drives
git with `git -C`.

The one thing that unavoidably writes into the tree is `pip install -e` (it puts
`src/private_search_gateway.egg-info/` beside the package) and pytest's
`search/gateway/.pytest_cache/`. Both are covered by `search/gateway/.gitignore`. T3 has you
confirm that rather than assume it.

Set these once - the two commits on the branch are referred to positionally, because the
rebase rewrote their SHAs and will do so again if the base moves:

```bash
W=<your worktree>
A1=$(git -C "$W" rev-list --reverse development..work/sl-colo-gateways | head -1)   # the first move
A2=$(git -C "$W" rev-parse work/sl-colo-gateways)                                   # the layout correction
DEV=$(git -C "$W" rev-parse development)                                            # expect b9fff95...
```

**Read file contents out of git:** `git show work/sl-colo-gateways:<path>` - e.g.
`git show work/sl-colo-gateways:search/docker-compose.yml`. The main checkout at
`D:\Open WebUI\ai-stack` is on `development` and still has the OLD layout on disk; reading it
will tell you the move did not happen.

**No lease is needed.** Every case is read-only or renders compose. `docker inspect` in T6 is
read-only. If a case tempts you to run `docker compose up`, `build` or `restart` - it is the
wrong case; T6d exists to prove you did not.

All repo-relative commands run from the **root of your worktree** unless stated.

---

## T1 - History survives both renames

Anchor criterion 1, as amended. The content crossed two renames (`$A1` and `$A2`);
`--follow` must walk both.

```bash
git log --follow --oneline -- search/gateway/src/gateway/main.py | wc -l
git log --follow --oneline -- search/searxng/settings.yml        | wc -l
git log --follow --oneline -- memory/mnemory-gateway/app.py      | wc -l
```

**PASS:** 3, 5 and 2 respectively (measured) - each greater than 1, and the two search
listings reach back to `52b53cb` ("Add private search gateway"). Then confirm git recorded
renames rather than delete-plus-add, in **both** commits:

```bash
git show --stat -M --name-status $A1
git show --stat -M --name-status $A2
```

**PASS (measured on the rebased commits):** `$A1` shows **37** renames - 34 search, 3 memory.
`$A2` shows **32**, all within `search/`: 30 lifting the package out of
`search/gateway/gateway/` and 2 moving `searxng/` to `search/searxng/`.

Neither commit contains a single `D`, and the only two `A` entries in the pair are this
item's own documents, both added by `$A1`:

```bash
{ git show --name-status -M $A1; git show --name-status -M $A2; } | grep -E "^[AD]	"
```

**PASS:** exactly two lines, `A documentation/evidence/stack-layers/sl-colo-gateways-test-plan.md`
and `A documentation/notes/stack-layers-sl-colo-gateways-findings.md`.

**FAIL:** any count of 1 or 0; any `D` at all; an `A` for a file that should have arrived as
a rename; any file reaching HEAD without its pre-move history.

Also confirm the old tree is gone and the new one is exactly right:

```bash
git ls-tree -r --name-only work/sl-colo-gateways -- search-gateway   # expect: nothing
git ls-tree -r --name-only work/sl-colo-gateways -- search | wc -l   # expect: 36
git ls-tree -r --name-only work/sl-colo-gateways -- search
```

**PASS:** no `search-gateway` entries at all; 36 files under `search/` - `README.md`,
`docker-compose.yml`, `gateway/{.gitignore,Dockerfile,README.md,pyproject.toml}`,
`gateway/src/gateway/**` (20), `gateway/tests/**` (8), `searxng/{limiter.toml,settings.yml}`
- and **no** `search/gateway/gateway/` anywhere.

## T2 - Both planes render, and every build context and bind source resolves

Anchor criterion 2.

```bash
docker compose -f search/docker-compose.yml --env-file .env.example config --format json > "$S/s-after.json"
docker compose -f memory/docker-compose.yml --env-file .env.example config --format json > "$S/m-after.json"
```

**PASS:** both exit 0. Then read the paths out of the render rather than trusting the source
file:

```bash
cat > "$S/paths.py" <<'EOF'
import json, sys
for p in sys.argv[1:]:
    d = json.load(open(p)); print('==', p)
    for name, s in sorted(d['services'].items()):
        if 'build' in s: print('  build', name, '->', s['build'].get('context'))
        for v in s.get('volumes', []) or []:
            if v.get('type') == 'bind': print('  bind ', name, '->', v.get('source'))
EOF
python "$S/paths.py" "$S/s-after.json" "$S/m-after.json"
```

**PASS - the search render has exactly these two entries, and no others:**

| | rendered path | exists? |
|---|---|---|
| `build gateway` | `<worktree>\search\gateway` | yes |
| `bind searxng` | `<worktree>\search\searxng` | yes |

Both are **inside the plane directory**. `ls -d` each absolute path from the render.

**PASS - the memory render has exactly these four entries, and no others** (complete list;
attempt 1's plan named only some, which is why it is spelled out):

| | rendered path | exists? | why |
|---|---|---|---|
| `build mnemory-cloud-gateway` | `<worktree>\memory\mnemory-gateway` | **yes** | the half of this item in the memory plane |
| `bind mnemory-backup` | `<worktree>\backup\mnemory-backup.sh` | **yes** | tracked, shared backup script, untouched |
| `bind mnemory-backup` | `<worktree>\backups\mnemory` | **no** | gitignored artifact output; compose creates it on `up`. Pre-existing |
| `build mnemory` | `<the worktree's parent>\mnemory` | **no** | `context: ../../mnemory` points *outside* the repo at the sibling `mnemory` checkout; from `.claude/worktrees/wt-*` it renders as `...\.claude\worktrees\mnemory`. Pre-existing, documented in `memory/README.md`, and the service is pinned `image: mnemory:local` + `pull_policy: never` so it is never built. Findings 5 |

Prove the two non-existent ones are not ours:

```bash
git diff $DEV work/sl-colo-gateways -- memory/docker-compose.yml
```

**PASS:** exactly one hunk - `context: ../mnemory-gateway` -> `context: ./mnemory-gateway`.

**FAIL:** either `config` exits non-zero; a rendered gateway context or the searxng bind
resolves outside its plane directory or to a path that does not exist; a fifth entry appears
in the memory render; the `mnemory` context or either backup bind changed.

## T3 - The moved suite passes from its new path, and CI invokes that path

Anchor criterion 3, as amended. **The venv goes outside the worktree.**

```bash
python -m venv "$S/venv"
"$S/venv/Scripts/python.exe" -m pip install -e "./search/gateway[dev]"     # Linux: $S/venv/bin/python
"$S/venv/Scripts/python.exe" -m pytest search/gateway/tests -q
```

**PASS:** `36 passed, 1 deselected` (measured).

Both halves of that line matter:

- `1 deselected` is `addopts = "-m 'not integration'"` from `search/gateway/pyproject.toml`
  taking effect. `37 passed`, or a network error, means the package's config was NOT picked
  up and the result is not the one the criterion asks for.
- the 36 include async tests, which pass only when `asyncio_mode = "auto"` (same file)
  applies. Confirm the mechanism directly: the run writes its cache to
  `search/gateway/.pytest_cache/`, i.e. pytest selected `search/gateway` as rootdir, which is
  where that `pyproject.toml` now lives.

Then confirm the tree is still clean:

```bash
git status --porcelain      # expect: empty
git status --porcelain --ignored=matching | grep "^!!" | grep search/
```

**PASS:** `--porcelain` is empty; the ignored listing shows only
`search/gateway/src/private_search_gateway.egg-info/`, `search/gateway/.pytest_cache/` and
`__pycache__/` directories - all covered by `search/gateway/.gitignore`. Remove them when you
are done.

**FAIL:** any test failure or error; a count other than 36 passed / 1 deselected; any
untracked (`??`) file left in the tree.

**T3b - CI runs exactly that command:**

```bash
git show work/sl-colo-gateways:.github/workflows/ci.yml | sed -n '105,113p'
```

**PASS:** the `pytest-search-gateway` job runs
`python -m pip install -e "./search/gateway[dev]"` then
`python -m pytest search/gateway/tests -q` - the anchor's criterion-3 command, verbatim (this
is the A5 non-path edit, called out at the top). The job **name** is unchanged on purpose: it
is a required-check name. Confirm too that `compose-validate` still renders both planes - it
names `search/docker-compose.yml` and `memory/docker-compose.yml`, neither of which moved.

**FAIL:** either CI path still contains `search-gateway` or `search/gateway/gateway`.

**T3c - informational, not a gate.** Attempt 1's criterion (`python -m pytest search/gateway
-q`, pointing pytest at the parent of the package) fails with `13 failed, 24 passed`, and
fails identically on the pre-move layout. Findings 2 has the falsification if you want to
re-run it. It is not a regression and it is not what this criterion asks for any more.

## T4 - Unbounded grep: no live pointer still names an old path

Anchor criterion 4, as amended - the exempt list is `scripts/archive/`,
`documentation/archive/`, `documentation/notes/`, `documentation/evidence/` and
`CLEANUP-PLAN.md`. **Do not pipe any of this through `head`.**

**T4a - old paths, including the backslash spellings.** The two NEW spellings
(`search/gateway` and `memory/mnemory-gateway`) contain the old strings as substrings, so
they are filtered out:

```bash
git grep -nE 'search-gateway[/\\]|mnemory-gateway[/\\]' -- . \
  ':!scripts/archive' ':!documentation/archive' ':!documentation/notes' \
  ':!documentation/evidence' ':!CLEANUP-PLAN.md' \
  | grep -v "search/gateway" | grep -v "memory/mnemory-gateway"
```

**PASS: zero lines** (measured).

**FAIL:** any hit at all - a compose file, CI workflow, check script, README, `CLAUDE.md`,
`.env.example`, `stack.manifest.toml`, the stack-map reference, a runbook, `SECURITY.md`, or
a source docstring still naming an old path.

**T4b - the intermediate (attempt 1) layout is gone too:**

```bash
git grep -nE 'search/gateway/gateway|gateway/searxng' -- . \
  ':!documentation/archive' ':!documentation/notes' ':!documentation/evidence'
```

**PASS: zero lines** (measured).

**T4c - the bare-name sweep (manual classification).** Exclude the exempt directories:

```bash
EX=(-- . ':!scripts/archive' ':!documentation/archive' ':!documentation/notes' ':!documentation/evidence' ':!CLEANUP-PLAN.md')
git grep -n "search-gateway"  "${EX[@]}"
git grep -n "mnemory-gateway" "${EX[@]}"
```

**PASS:** every hit is one of these, and **none is a filesystem path**:

- the **container** name `search-gateway` - `search/docker-compose.yml:120`,
  `scripts/recovery/emergency-recovery.ps1`, `scripts/recovery/quick-fixes.bat`,
  `scripts/checks/stack-watchdog.ps1`, `scripts/lib/stack-services.json`,
  `documentation/CONTAINER-REGISTRY.md`,
  `.claude/skills/stack-map/references/workspace-stacks.md:256`, `search/README.md:36,216`,
  and `frontend/docker-compose.yml:120` ("the search-gateway wiring" - a service, added by
  sl-frontend-solo)
- the **image** / Python package name `private-search-gateway` -
  `search/docker-compose.yml:119`, `search/gateway/pyproject.toml:2`,
  `search/gateway/src/gateway/mcp_server.py:20`, `search/searxng/settings.yml:15`,
  `search/README.md:109,118`
- the **CI job** name `pytest-search-gateway` - `.github/workflows/ci.yml:105`
- the archived plan's **filename** `integration-plan-private-search-gateway.md` -
  `search/gateway/README.md:9`
- descriptive prose in `llm-queue/src/llm_queue/logging.py:1`
- the NEW correct spellings `search/gateway/...` and `memory/mnemory-gateway/...` -
  including `CLAUDE.md:237`, `README.md:151`, `memory/README.md:33,151`,
  `memory/docker-compose.yml:70`, `openbrain-gateway/app.py:8`, `little-coder/README.md:46`,
  `.claude/skills/stack-map/references/workspace-stacks.md:392`

**FAIL:** a service, container, image, network or volume renamed (the anchor forbids it), or
a path-shaped hit T4a's filter swallowed.

**T4d - the repointed pointers resolve.** Read each blob out of git and check the target
exists on disk:

```bash
for p in search/docker-compose.yml memory/docker-compose.yml .github/workflows/ci.yml \
         search/README.md search/gateway/README.md memory/README.md README.md CLAUDE.md \
         .env.example documentation/implementation-guide/README.md \
         .claude/skills/stack-map/references/workspace-stacks.md \
         openbrain-gateway/app.py little-coder/README.md \
         documentation/evidence/research-trust/TEST-PLAN.md; do
  echo "== $p"; git show work/sl-colo-gateways:$p | grep -nE 'search/gateway|search/searxng|memory/mnemory-gateway|documentation-plans'
done
```

**PASS:** every path named resolves. Spot-check in particular:

- `search/docker-compose.yml` - `context: ./gateway`, bind `./searxng:/etc/searxng:rw`
- `search/gateway/README.md:8` - `../../../documentation-plans-ai-stack/...`, **three**
  levels (that README moved one directory deeper than `search-gateway/README.md` was; at two
  levels it resolves to `ai-stack/documentation-plans-ai-stack/`, a plausible-looking path
  inside this repo). Check the sibling plan store really sits at the resolved location.
- `search/gateway/README.md` Development block - `pip install -e ".[dev]"` and
  `ruff check src tests`, because that README now sits *in* the package rather than above it.
  Run both from `search/gateway/` and confirm they work.
- `README.md:151` - the repo-map row reads
  `` `search/gateway/`, `memory/mnemory-gateway/` (builds the `mnemory-cloud-gateway`
  container) ``. This line was the rebase's only conflict: sl-closeout had corrected the
  directory name on development, this item repoints both paths, and the resolution keeps both
  (findings 6 and 13). Confirm neither intent was dropped.
- `CLAUDE.md:236-239` - sl-closeout added "`openbrain-gateway/` ... beside its twin
  `mnemory-gateway/`", which this move falsifies (it is no longer beside it). Reworded to
  "its twin `memory/mnemory-gateway/` ... moved into the memory plane". Judge whether that is
  the minimum change that keeps the sentence true.
- `openbrain-gateway/app.py:8` - `../memory/mnemory-gateway/app.py`. This is the one file
  outside `search/` and `memory/` whose *source* was edited; it is a single docstring line,
  `openbrain-gateway/` itself did not move (anchor out-of-scope), and leaving it would fail
  T4a. `git diff $DEV work/sl-colo-gateways -- openbrain-gateway/` must be exactly that line.
- `documentation/evidence/research-trust/TEST-PLAN.md` lines 801 and 815 - the image-bump
  rule and the rollback `git checkout`, repointed because they are **live procedure**
  (research-trust is "merged, awaiting deploy" in the index). The four other old-path lines in
  that file (25, 64, 369, 462) are a completed record of what was run and are left alone -
  criterion 4 exempts `documentation/evidence/` for exactly that reason. Findings 7 also
  corrects attempt 1's false justification for two of them; re-run
  `git rev-parse --verify work/research-trust` (it fails - the branch is deleted) to confirm
  the correction is itself correct.

## T5 - Normalized render diff: only the contexts and the searxng bind

Anchor criterion 5, as amended: *only* `build.context` values and the searxng bind source may
differ; container names, image tags and network memberships must be identical.

The two renders come from different directories, so every absolute path carries a different
prefix. `norm.py` sorts the keys (compose does not emit them in a stable order) **and**
substitutes each render's own repo root with `<ROOT>`. **Write it in your scratch, not the
repo.**

```python
# $S/norm.py
import json
import sys

BS = chr(92)  # backslash

root, path = sys.argv[1], sys.argv[2]
s = json.dumps(json.load(open(path)), sort_keys=True, indent=2)
for r in {root, root.replace("/", BS)}:
    s = s.replace(json.dumps(r)[1:-1], "<ROOT>")  # match the JSON-escaped form
print(s)
```

```bash
mkdir -p "$S/base"
git -C "$W" archive $DEV | tar -x -C "$S/base"              # development, pristine
( cd "$S/base"
  docker compose -f search/docker-compose.yml --env-file .env.example config --format json > "$S/s-base.json"
  docker compose -f memory/docker-compose.yml --env-file .env.example config --format json > "$S/m-base.json" )

BASE=$(cd "$S/base" && pwd -W)   # pwd -W gives the Windows form compose emitted; plain pwd on Linux
WTP=$(pwd -W)
python "$S/norm.py" "$BASE" "$S/s-base.json"  > "$S/s-base.norm.json"
python "$S/norm.py" "$WTP"  "$S/s-after.json" > "$S/s-after.norm.json"   # from T2
python "$S/norm.py" "$BASE" "$S/m-base.json"  > "$S/m-base.norm.json"
python "$S/norm.py" "$WTP"  "$S/m-after.json" > "$S/m-after.norm.json"
echo "== search"; diff -u "$S/s-base.norm.json" "$S/s-after.norm.json"
echo "== memory"; diff -u "$S/m-base.norm.json" "$S/m-after.norm.json"
```

**PASS - the search diff has exactly two changed values:**

- `services.gateway.build.context` : `<ROOT>\search-gateway\gateway` -> `<ROOT>\search\gateway`
- `services.searxng.volumes[0].source` : `<ROOT>\search-gateway\searxng` -> `<ROOT>\search\searxng`

**PASS - the memory diff has exactly two:**

- `services.mnemory-cloud-gateway.build.context` : `<ROOT>\mnemory-gateway` -> `<ROOT>\memory\mnemory-gateway`
- `services.mnemory.build.context` : **an unsubstituted absolute path on both sides, and they
  differ.** Expected, and not a change to the compose file: that context is `../../mnemory`,
  which points *outside* the repo root, so `<ROOT>` cannot absorb it and each render names its
  own parent directory. T2 already proved the compose file's only memory hunk is the
  cloud-gateway context. Findings 5.

**PASS also requires** these to be IDENTICAL on both sides for every service in both planes:
`container_name`, `image`, `networks`, `ports`, `volumes` (other than the one source above),
`environment`, `healthcheck`, `depends_on`, and the top-level `name`, `networks` and
`volumes` blocks. A diff of only those four values proves it; anything longer, check each
extra hunk against this list.

**FAIL:** any change to a container name, image tag, network name or membership, published
port, volume name, or a compose project `name`.

## T6 - Hooks, lint, and nothing live was touched

Anchor criterion 6, in four parts.

**T6a - proof the hooks ran, including across the rebase.** `.githooks/pre-commit` attests
the TREE plus the hook's own blob hash to an append-only ledger in the shared git dir; that
is what `--no-verify` cannot fake. A rebase produces NEW trees (conflict resolution included),
so the pre-rebase attestations do not cover these commits - both were re-committed through
the hook after the rebase.

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "$W/scripts/checks/check-hook-attestation.ps1" -Branch work/sl-colo-gateways -Base development
```

**PASS:** exit 0 - every commit in `development..work/sl-colo-gateways` is attested. Then
read back WHICH hook gated them, because "a hook ran" is not "the hook containing the checks
ran":

```bash
LEDGER=$(git -C "$W" rev-parse --git-common-dir)/hook-attest.log
for c in $A1 $A2; do T=$(git -C "$W" rev-parse $c^{tree}); echo "== $c"; grep "^$T " "$LEDGER"; done
git -C "$W" cat-file -p <the 4th field of either line> | grep -c "scripts/checks/"
```

**PASS:** a ledger line per tree, and the hook blob contains 10 `scripts/checks/`
invocations. A `?` in that field, or a hash that resolves to nothing, means the gating hook
was an uncommitted local edit - report it.

**FAIL:** the attestation check exits non-zero, i.e. a tree here was never seen by a hook.

**T6b - the checks that need no staging** (they run over the repo or over tracked files, so a
clean worktree is valid input):

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/validate-lineendings.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-llm-gateway-routing.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-corpus-exposure-producers.ps1
python -m ruff check search/gateway memory/mnemory-gateway openbrain-gateway
python -m ruff check .
```

**PASS:** the three PowerShell checks exit 0; both ruff runs print **"All checks passed!"**
(measured). Note the change from attempt 2: `ruff check .` used to report one pre-existing
E501 in `llm-queue/src/llm_queue/__init__.py:9`; `sl-closeout` (259c972) shortened that
docstring path and it is clean now. Findings 3 keeps the record because the shape recurs -
a path repoint in a docstring tripping a *subtree's* lint config - and this item repoints one
docstring too.

**FAIL:** any ruff error at all; any of the three checks non-zero.

**T6c - the staged-aware checks.** `check-staged-secrets.ps1`, `check-doc-placement.ps1`,
`check-project-configs.ps1` and `check-env-file-scope.ps1` read `git diff --cached` and
no-op on a clean tree (`check-project-configs` prints `[configs] nothing staged - skip`), so
running them from your worktree establishes nothing. Give them the whole item as staged
input, in a **scratch clone at a SHORT path**:

```bash
G=/c/slc          # MUST be short. The scratchpad path plus this repo's deepest file
                  # exceeds MAX_PATH; that is how attempt 2's reset --soft ended up
                  # running in the developer's worktree.
rm -rf "$G"
git -c core.longpaths=true clone --no-hardlinks --shared \
    --branch work/sl-colo-gateways "D:/Open WebUI/ai-stack" "$G"
git -C "$G" reset --soft "$DEV"          # stages the whole item; -C, so no cd can misfire
git -C "$G" diff --cached --name-only | wc -l
```

**Check before going further:** `git -C "$G" rev-parse --show-toplevel` must print `$G`, and
`git -C "$W" status --porcelain` must still be empty. If either is wrong, stop - you are
about to run in the wrong repository.

`check-project-configs.ps1` and `check-staged-secrets.ps1` take no root parameter and work
from the current directory, so they need a `cd` - safe here because `$G` is short. The other
two take `-Root`:

```bash
cd "$G"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-staged-secrets.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-doc-placement.ps1  -Root "$G"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-env-file-scope.ps1 -Root "$G"
cd "$W"
rm -rf "$G"
```

**PASS:** all four exit 0; `git -C "$G" diff --cached --name-only | wc -l` prints **52**
(renames count as an add and a delete here). Measured by the developer on 2026-09-19, on
this commit, through this recipe:

```
[secrets] staged files clean (15 scanned)
[configs] all 8 compose projects render clean
[configs] NOT VERIFIED: project 'agent-org' has 16 inventory row(s) and no render target
[configs] NOT VERIFIED: project 'open-brain' has 30 inventory row(s) and no render target
          (OB1\docker\.env absent - gitignored, so this is expected off the deploy host)
[configs] stack-services.json inventory matches the compose configs
          [rows verified/expected: inference:8/8 frontend:4/4 memory:3/3 search:4/4 coder:4/4]
SUCCESS: no new planning material staged into the code repo
env_file scope: no new shared-.env grants staged.
```

The `[configs]` lines are the point of the exercise: with `.yml` staged the check does the
real work instead of printing `nothing staged - skip`, and `memory:3/3` and `search:4/4` are
the two planes this item touches confirming their service inventory is unchanged. The two
`NOT VERIFIED` lines are pre-existing and expected in a clone (no `OB1/docker/.env`, no
agent-org render target) - they do not fail the check.

The remaining three hook checks (`check-ob1-recipe-tests`, `check-ob1-deno-recipes`,
`check-ob1-integration-images`) key off a **staged OB1 gitlink**. This item stages none
(`git diff $DEV work/sl-colo-gateways --stat -- OB1` is empty), so they skip by design - note
that rather than running them.

**T6d - no-touch proof** (`docker inspect` / `image ls` are read-only):

```bash
docker image ls private-search-gateway --format "{{.Repository}}:{{.Tag}} {{.CreatedAt}}"
docker image ls mnemory --format "{{.Repository}}:{{.Tag}} {{.CreatedAt}}"
docker image ls --format "{{.Repository}}:{{.Tag}}" | grep ":wt-"
docker ps --filter name=search- --filter name=searxng --filter name=mnemory \
          --format "{{.Names}} {{.Status}}"
```

**PASS:** `private-search-gateway:local` and `mnemory:local` carry pre-existing creation
timestamps; no `:wt-*` tag exists for either; `search-vpn`, `search-redis`, `searxng`,
`search-gateway`, `mnemory`, `mnemory-cloud-gateway`, `mnemory-backup` show uptimes
predating this item's work.

**Expected and NOT a failure:** `docker inspect searxng` shows it still bind-mounting
`D:\Open WebUI\ai-stack\search-gateway\searxng` - the pre-move path - because the container
has been running since 2026-09-13 and nothing here recreated it. That is findings 11, the
deploy step: **the orchestrator performs the recreate under the `search` lease at landing.**
It is not part of this test pass and not the tester's job.

**FAIL:** any of those images rebuilt or retagged; any of those containers restarted.

## T7 - Intent (the anchor, not just the checks)

Answer these in your evidence, in your own words:

1. Are the search and memory planes now **self-contained**? Is there anything left at the
   repo root that either plane's compose file builds or binds, other than the deliberate
   exceptions T2 enumerates (`../../mnemory`, out of repo; `../backup/mnemory-backup.sh` and
   `../backups/mnemory`, the shared backup convention)?
2. Did anything change **besides** paths? Check the blobs, not the hunks:

   ```bash
   git diff $DEV work/sl-colo-gateways -M --name-status | grep -v "^R100"
   ```

   **PASS:** 13 `M`, 2 `A` and exactly one non-R100 rename, `R091 search-gateway/README.md
   -> search/gateway/README.md` (its two relative links gained a `../`, and its dev commands
   moved with the package). Every other moved file is `R100` - byte-identical. Confirm a few
   by hash, including both searxng files, which must not have been touched at all:

   ```bash
   for f in searxng/settings.yml searxng/limiter.toml; do
     git rev-parse $DEV:search-gateway/$f work/sl-colo-gateways:search/${f}
   done
   git rev-parse $DEV:search-gateway/gateway/src/gateway/main.py \
                 work/sl-colo-gateways:search/gateway/src/gateway/main.py
   git rev-parse $DEV:mnemory-gateway/app.py \
                 work/sl-colo-gateways:memory/mnemory-gateway/app.py
   ```

   **PASS:** each pair prints the same hash twice. Then read the 13 `M` hunks. The **only**
   non-path edit you should find is the A5 `python -m` / `-q` change in `ci.yml` - everything
   else is a path. Say whether you agree that pinning the CI job to the anchor's exact
   command was the right call, or whether it should have stayed `pip` / `pytest -q`.
3. The layout is now `search/gateway` (the Python project) + `search/searxng` (the engine's
   config beside the compose file that binds it). Does it read right - would someone opening
   `search/` know what each directory is without being told?
4. The rebase resolved one conflict and reworded one sentence sl-closeout had just added
   (findings 13). Read both: `README.md:151` and `CLAUDE.md:236-239`. Did keeping "both
   intents" actually keep both, and is the CLAUDE.md rewording the minimum needed to stop it
   being false?
5. Findings 7, 9, 10 and 11 exist because a previous tester refuted or extended what the
   developer wrote. Check 7's correction yourself
   (`git rev-parse --verify work/research-trust`) - it is the one place this note asserts a
   git fact, and attempt 1 got that exact claim wrong.

---

## Evidence to record

For each case: the command, its full output (T4a-T4c unbounded), and PASS/FAIL. Flag any
quoted value that did not match. Anything true and outside this item goes into
`documentation/notes/stack-layers-sl-colo-gateways-findings.md` - it has thirteen sections;
add yours rather than starting a new file. Clean up `$S` and remove the scratch clone `$G`
when you are done.
