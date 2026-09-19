# Test plan — `sl-colo-inference`

**Item:** `sl-colo-inference` (stack-layers PLAN 2.7, Part L.1, wave 2).
**Branch:** `work/sl-colo-inference` · **Base:** `development` @ **`be00d53`** (which
contains `sl-inference-split` AND `sl-closeout`).
**Revision 3:** attempt 2 FAILED 11/13 — **T9** (`.env.example:281`, a pointer the rebase
carried in already stale) and **T13c** (F14 named one `compose restart` call site and missed
the two that matter, inside the recovery script's first branch). Both were found by these
cases run exactly as written, which is the plan working; both are fixed in the commit this
revision accompanies. T9 gains a backslash-form sweep and a named `.env.example` bar; T13c
now demands an enumeration of every repair verb rather than a check of the note's list, and
T13e is new. **Revision 2:** attempt 1 passed 12/12 with the plan judged
**inadequate**. This revision rebases the branch from `9f64b84` onto `be00d53` and fixes
what that judgement named: **T8a** stated as its PASS condition the output of a command
that cannot succeed on this host (F13); **T11** ran `validate-lineendings.ps1` in an export
where it checks nothing; **T5b** had no bar for the frontend, the one plane whose mounts
this item changes; and there was no case at all for the post-merge live hazard, now **T13**.
Every bar below that mentions the base means `be00d53`.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-colo-inference.json`
**Findings sink:** `documentation/notes/stack-layers-sl-colo-inference-findings.md`
**Kind of evidence this item owes:** this is a MOVE, not a behaviour change. There is no
RED-to-GREEN repro to offer and none is claimed. What it owes instead is
*verification against the source of truth*: every path that moved is followed everywhere
it is named, every render still resolves to a file that exists, and the one guard that
scans those files still scans them. Two cases below (T6, T8) DO have a red half, because a
guard that cannot go red is the failure this repo keeps finding.

---

## 0. Ground rules for the tester

1. **Work in your OWN scratch worktree or export.** Do not run any of this in
   `D:\Open WebUI\ai-stack` (the operator's checkout) and do not run it in the developer's
   worktree `wt-sl-colo-inference`. Where a case says "export", it means a `git archive`
   of the commit under test unpacked into your own scratch directory.
2. **No lease is required and none should be taken.** Every case is a compose `config`
   render, a unit-test run, a static check or a read-only `docker exec`. Nothing here
   starts, stops, restarts, rebuilds, retags or recreates a container. **If a case seems to
   ask you to touch a running container, you have misread it — stop and say so.**
3. **`docker compose config` renders only.** Never `up`, `build`, `down` or `restart`.
4. Compose used for the developer's runs: **v5.3.0**. Note yours if it differs.

Set up once:

```bash
# your own scratch root - anywhere OUTSIDE the repo and OUTSIDE .claude
SCRATCH=/c/Users/<you>/AppData/Local/Temp/sl-colo-inference-test
W="D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-colo-inference"   # source of the commit only

rm -rf "$SCRATCH"; mkdir -p "$SCRATCH/head" "$SCRATCH/base"
git -C "$W" archive work/sl-colo-inference | tar -x -C "$SCRATCH/head"
git -C "$W" archive development             | tar -x -C "$SCRATCH/base"   # = be00d53
# renders need an env file; .env.example is tracked, so both exports already have it
cp "$W/agent-org/docker/.env" "$SCRATCH/head/agent-org/docker/.env"
cp "$W/agent-org/docker/.env" "$SCRATCH/base/agent-org/docker/.env"
```

**Why the export and not the worktree:** see T6. The gateway-routing check allow-lists
`*\.claude\*`, so it is VACUOUS anywhere under `.claude/worktrees/`. Any result taken from
the worktree for that check is meaningless.

---

## T1 — history survived the move

```bash
git -C "$W" log --follow --oneline -- inference/llm-queue/src/llm_queue/policy.py | wc -l
git -C "$W" log --follow --oneline -- inference/config/litellm.config.yaml        | wc -l
git -C "$W" log --follow --oneline -- inference/llm-queue/tests/test_scheduler.py | wc -l
```

**PASS:** each count is **> 1**, and the listed commits include ones that predate this
item (i.e. `--follow` walked through the rename).
**FAIL:** a count of `1` — the file was recorded as an add+delete, not a rename, and the
history is severed.

Also confirm git itself saw renames:

```bash
git -C "$W" diff --name-status -M development..work/sl-colo-inference | grep -c '^R'
git -C "$W" diff --name-status -M development..work/sl-colo-inference | grep '^R' | awk '{print $2}' | sed 's|/.*||' | sort | uniq -c
```

**PASS:** `40`, split `8 config` / `32 llm-queue`.
**FAIL:** fewer — some file was recorded as an add+delete, not a rename.

Two traps in that one line, both paid for:
- **Use `--name-status`, not `--stat`.** `--stat` elides long paths with `...`, which eats
  the `{old => new}` marker and under-counts (it returned 5 against 40).
- **Diff the RANGE, not `show` a commit.** The branch now carries more than one commit, so
  `git show <branch>` shows only the tip — which is a documentation commit with no renames
  in it at all.

## T2 — `config/` is gone and its contents are all accounted for

```bash
ls -d "$SCRATCH/head/config"                      # expect: No such file or directory
find "$SCRATCH/head/inference/config" -type f | sort
```

**PASS:** `config/` does not exist in the tree, and `inference/config/` holds exactly
these 8 files:

```
inference/config/chat-template.jinja
inference/config/litellm.config.yaml
inference/config/litellm.ui.config.yaml
inference/config/llama-swap.config.yaml
inference/config/litellm/assemble-config.py
inference/config/litellm/custom_callbacks.py
inference/config/litellm/model_list/cloud.openrouter.yaml
inference/config/litellm/model_list/local.yaml
```

**FAIL:** `config/` still exists (even empty, if it is tracked); or any file that was in
`config/` on the base is missing from `inference/config/`; or a file appeared that was not
in `config/` on the base. Cross-check the base side:

```bash
( cd "$SCRATCH/base"; find config -type f | sort | sed 's|^config/|inference/config/|' ) > /tmp/expected
( cd "$SCRATCH/head"; find inference/config -type f | sort ) > /tmp/actual
diff /tmp/expected /tmp/actual     # PASS = no output
```

## T3 — **location-relative paths inside the moved files** (the sl-colo-portal failure class)

`sl-colo-portal` was failed by an `import.meta.url` + `../../` inside a script that moved,
and its plan had no case that read the moved files for such paths. This is that case.

**T3a — sweep.** Over the *moved files only*:

```bash
cd "$SCRATCH/head"
grep -rn '__file__\|os\.path\.dirname\|Path(__file__)\|\$PSScriptRoot\|import\.meta\.url\|sys\.path\|\.\./' \
  inference/llm-queue inference/config
```

**Expected:** exactly four hits, all of them *prose* in comments/metadata, none resolved by
any tool:

| file:line | text | verdict |
|---|---|---|
| `inference/llm-queue/pyproject.toml:4` | `See ../documentation-plans-ai-stack/...` in `description` | repo-root-relative prose; correct |
| `inference/llm-queue/src/llm_queue/__init__.py:9` | `Design: ../documentation-plans-ai-stack/...` (wrapped over two lines, F11) | repo-root-relative prose; correct |
| `inference/config/litellm/model_list/local.yaml:24` | `See ../documentation-plans-ai-stack/...` | repo-root-relative prose; correct |
| `inference/llm-queue/README.md:14` | `../../../documentation-plans-ai-stack/...` **markdown link** | must be `../../../` — see T3b |

**FAIL:** any hit that is code (a path computed from a file's own location) rather than
prose, anywhere in the moved trees.

**Run this on the EXPORT, not on a tree you have pip-installed into.**
`pip install -e ./inference/llm-queue` writes `src/llm_queue.egg-info/` and `__pycache__/`
into the source tree. Both are gitignored (`.gitignore:77`, `:25`) so they cannot be
committed, but `PKG-INFO` copies the `pyproject.toml` description verbatim and will show up
as a fifth hit in this sweep. A `git archive` export has neither.

**T3b — the one link that actually resolves.** `README.md:14` is a markdown link, so it is
file-relative, and the move changed its depth by one.

```bash
cd "$SCRATCH/head/inference/llm-queue"
grep -n 'documentation-plans-ai-stack' README.md
ls -l ../../../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md
```

**PASS:** the link reads `../../../documentation-plans-ai-stack/...` (three levels) and the
`ls` resolves to the real file in the sibling plan store.
**FAIL:** it still reads `../../` (it would resolve to `ai-stack/documentation-plans-ai-stack/`,
which does not exist), or the `ls` does not resolve.

**T3c — read each moved file to the end.** Spot-open, in full, the four files most likely
to hide one: `inference/llm-queue/Dockerfile`, `inference/llm-queue/pyproject.toml`,
`inference/config/litellm/assemble-config.py`, `inference/config/litellm/custom_callbacks.py`.
Confirm no `COPY` / `packages.find` / open() path assumes a location the move changed.
(The Dockerfile's `COPY pyproject.toml ./` and `COPY src ./src` are **build-context**
relative, and the context itself moved with them, so they are unaffected — check this by
reading, not by assuming.)

## T4 — **the assembler's container-path assumptions vs the new host mounts**

The gateway's entrypoint runs `assemble-config.py` inside the container. The host paths it
is fed moved; its *container* paths must not have.

**T4a — the script takes nothing from its own location** (read
`inference/config/litellm/assemble-config.py` lines 50-60):

```python
BASE         = os.environ.get("LITELLM_BASE_CONFIG",     "/app/config.base.yaml")
FRAGMENT_DIR = os.environ.get("LITELLM_FRAGMENT_DIR",    "/app/conf.d")
OUT          = os.environ.get("LITELLM_EFFECTIVE_CONFIG","/app/config.yaml")
```

**PASS:** all three are container-absolute, env-overridable, and no `__file__`/`dirname`
appears anywhere in the file. **FAIL:** any path derived from the script's own location —
then the host move *would* reach it.

**T4b — the container-side targets are unchanged.** Compare base vs head renders on
*targets only*:

```bash
cd "$SCRATCH/base"; docker compose -f inference/docker-compose.yml --env-file .env.example --profile local config --format json \
  | python -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(sorted(v['target'] for s in d['services'].values() for v in (s.get('volumes') or []))))" > /tmp/t-base
cd "$SCRATCH/head"; docker compose -f inference/docker-compose.yml --env-file .env.example --profile local config --format json \
  | python -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(sorted(v['target'] for s in d['services'].values() for v in (s.get('volumes') or []))))" > /tmp/t-head
diff /tmp/t-base /tmp/t-head
```

**PASS:** no output. In particular `/app/config.base.yaml`, `/app/conf.d`,
`/app/assemble-config.py`, `/app/custom_callbacks.py`, `/app/config.yaml` and
`/etc/llama/chat-template.jinja` all appear on both sides.
**FAIL:** any target added, removed or renamed — the script's defaults would then no longer
match its mounts, and the gateway would fail to assemble at start.

**T4c — the `command:` still points `--config` at the generated file.**

```bash
grep -n 'assemble-config.py\|--config' "$SCRATCH/head/inference/compose/gateway.yml"
```

**PASS:** the entrypoint runs `python /app/assemble-config.py` and `command:` is
`["--config", "/app/config.yaml", "--port", "8080"]` — unchanged from base.
**FAIL:** either drifted.

**T4d — the fragment dir really holds the fragments.** The mount source
`../config/litellm/model_list` resolves (from `inference/compose/`) to
`inference/config/litellm/model_list`. Confirm that directory holds `local.yaml` and
`cloud.openrouter.yaml` (T2 already lists them) and that `local.yaml` still ends with
`x-requires-profile: local`.

## T5 — renders, and every bind source exists

**T5a — the three planes the anchor names render.** From `$SCRATCH/head`:

```bash
docker compose -f inference/docker-compose.yml  --env-file .env.example --profile local config >/dev/null; echo "inference+local rc=$?"
docker compose -f inference/docker-compose.yml  --env-file .env.example                 config >/dev/null; echo "inference      rc=$?"
docker compose -f frontend/docker-compose.yml   --env-file .env.example                 config >/dev/null; echo "frontend       rc=$?"
docker compose -f agent-org/docker/docker-compose.yml --env-file agent-org/docker/.env  config >/dev/null; echo "agent-org      rc=$?"
```

**PASS:** all four `rc=0`.

> **DECLARED CONFLICT WITH THE ANCHOR.** The anchor's acceptance criterion renders the
> frontend as `--profile gpu --profile tailscale`. **`frontend/docker-compose.yml`
> declares no profiles at all** (`grep -n profiles frontend/docker-compose.yml` returns
> nothing, on the base and on the head). Those flags are accepted by compose and select
> nothing, so passing them renders the same thing as passing neither. The case above
> renders without them, deliberately. Flag this to the gate rather than treating the
> criterion's wording as met or unmet by accident.

**T5b — every rendered bind source and build context exists on disk.** For each of the
four renders above, re-run with `--format json` and check each `services.*.volumes[].source`
(where `type == "bind"`) and each `services.*.build.context` with `os.path.exists`.

**PASS:** the only non-existent paths are the **gitignored runtime directories**, and the
identical set is non-existent on the base:

| plane | absent in a fresh export | present in the real deployment root |
|---|---|---|
| inference (`local`) | `data/models/embeddings`, `backups/llm-gateway`, `backups/lm-models` | yes (verified in `D:\Open WebUI\ai-stack` 2026-09-19) |
| inference (no profile) | `backups/llm-gateway` | yes |
| **frontend** | `data/tailscale` (×3 — openwebui `/host_project/data/tailscale`, tailscale `/var/lib/tailscale`, tailscale-backup `/data`), `backups/openwebui`, `backups/tailscale`, and `/dev/net/tun` | the three repo-relative ones: yes (verified 2026-09-19). `/dev/net/tun` is a **Linux device inside the Docker VM** and never exists on the Windows host — absent on base and head alike, and nothing to do with this item |
| agent-org | `agent-org/agent-bridge/secrets`, `backups/agent-bridge-db`, `backups/mattermost-db` | (unchanged by this item) |

**FAIL:** any path under `inference/config/`, `inference/llm-queue/` or `agent-org/config/`
does not exist; or a runtime directory is missing on the head that existed on the base.
The frontend row exists because this is the one plane whose mounts the item CHANGES: base
and head are identical there (six absent, the same six), and without a stated bar a tester
would have had nothing to compare the changed plane against.
Note `LM_MODELS_DIR` in `.env.example` is an absolute host path
(`C:\Users\yamao\.lmstudio\models`) and is not affected by this item either way.

**T5c — normalized render diff for `inference` shows ONLY path changes.** Render base and
head to JSON, `json.dumps(sort_keys=True)`, replace each side's absolute root with
`<ROOT>`, diff.

**PASS (with `--profile local`)**: exactly **eight** changed values — seven
`volumes[].source` and one `build.context`, nothing else:

```
config\llama-swap.config.yaml          -> inference\config\llama-swap.config.yaml
config\chat-template.jinja             -> inference\config\chat-template.jinja
config\litellm.config.yaml             -> inference\config\litellm.config.yaml
config\litellm\model_list              -> inference\config\litellm\model_list
config\litellm\assemble-config.py      -> inference\config\litellm\assemble-config.py
config\litellm\custom_callbacks.py     -> inference\config\litellm\custom_callbacks.py
config\litellm.ui.config.yaml          -> inference\config\litellm.ui.config.yaml
llm-queue (build context)              -> inference\llm-queue
```
(seven bind sources + one build context; without `--profile local` the upstreams and the
queue are absent, leaving five bind sources.)

**FAIL — and this is the load-bearing half of the case:** ANY other key differs.
Specifically check that these are byte-identical across the diff: every `container_name`,
every `image`, every `networks` block **including `aliases`** (`llama-cpp`,
`llama-cpp-embed` must still sit on `llm-gateway`), every `ports`, `environment`,
`depends_on`, `healthcheck`, `profiles`, and the top-level `volumes`/`networks`. A single
changed alias or container name fails the item.

**T5d — agent-org render is unchanged.** Same normalization, base vs head:

**PASS:** `IDENTICAL` — no difference at all. (See F1: agent-org never mounted the shared
`config/`; its `../config/...` is `agent-org/config/`.)
**FAIL:** any difference — then something was repointed that should not have been.

## T6 — the routing check still scans the moved files (green AND red)

**This case must be run from an export outside `.claude/`.** See the finding F3: the
check's allow-list contains `*\.claude\*`, so every file in an agent worktree is
allow-listed and the check passes without reading anything. Confirm that for yourself
first, so you know the trap is real:

```bash
# T6a - the trap (optional but recommended once)
powershell -NoProfile -ExecutionPolicy Bypass -File "$W/scripts/checks/check-llm-gateway-routing.ps1"
# from the worktree this prints OK regardless of content - a green here proves NOTHING
```

**T6b — green on a clean export:**

```bash
cd "$SCRATCH/head"
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1 -Root "$(cygpath -w "$SCRATCH/head")"
```
**PASS:** `OK - no LLM gateway bypasses found`, exit 0.

**T6c — RED when a bypass is planted in a scanned file.** In your export (never the
worktree, never the live tree):

```bash
python - <<'EOF'
import io
p = "<SCRATCH>/head/inference/config/litellm/model_list/local.yaml"
s = io.open(p, encoding="utf-8", newline="").read()
s = s.replace("      api_base: http://llm-queue:8080/v1",
              "      api_base: http://llama-cpp-upstream:8080/v1", 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
EOF
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1 -Root "$(cygpath -w "$SCRATCH/head")"
```
**PASS:** exit **1**, naming
`inference\config\litellm\model_list\local.yaml:<line>`.
**FAIL:** exit 0 — the moved fragment directory is no longer scanned, which is the whole
point of the case.

**T6d — the allow-list entry still covers the BASE config.** Restore `local.yaml`, then
append a planted line to `inference/config/litellm.config.yaml` in the export and re-run.
**PASS:** exit **0** — `'*\inference\config\litellm.config.yaml'` in `$allowPathLike`
matches the file at its new path (the gateway's own forwarding config is a sanctioned
upstream reference).
**FAIL:** exit 1 — the allow-list entry was narrowed past the file it is meant to cover,
and the pre-commit hook would now reject a legitimate gateway config.

Discard the export after this case; do not reuse a planted tree for any other case.

## T7 — the llm-queue suite passes from its new path, installed the way CI installs it

`.github/workflows/ci.yml:96-103` is the source of truth for how this runs. Read it first
and confirm the two lines now say `./inference/llm-queue[dev]` and
`inference/llm-queue/tests`.

In a venv **in your scratch directory, never inside a worktree**:

```bash
python -m venv "$SCRATCH/venv"; "$SCRATCH/venv/Scripts/python.exe" -m pip -q install --upgrade pip
cd "$SCRATCH/head"
"$SCRATCH/venv/Scripts/python.exe" -m pip -q install -e "./inference/llm-queue[dev]"
"$SCRATCH/venv/Scripts/python.exe" -m pytest inference/llm-queue/tests
```

**PASS:** install succeeds; **41 passed**; and the header shows

```
rootdir: <...>/inference/llm-queue
configfile: pyproject.toml
asyncio: mode=Mode.AUTO
```

**FAIL — and check this explicitly, do not accept a bare "41 passed":** if `rootdir`
resolves to the repo root instead of the package directory, the subproject's
`[tool.pytest.ini_options]` (`asyncio_mode = "auto"`, `addopts = "-m 'not integration'"`)
is NOT being applied; async tests would then error and integration tests would not be
deselected. A green count with the wrong rootdir is a different suite than CI runs.

**Baseline for comparison:** the same command against `$SCRATCH/base` with the old path
(`pytest llm-queue/tests`) gives the same 41 and the same rootdir shape.

## T8 — the frontend's `/app/config` mount: was the evidence real?

The anchor says removing it without evidence FAILS. **Try to refute the evidence rather
than confirm it.** Findings F2 lists what was checked; each line below re-checks one.

**8a — the repo-side claim, and the trap that made the first version of this case
worthless.** THE PATTERN MUST BE PASSED WITH `MSYS_NO_PATHCONV=1`. In Git Bash an argument
that looks like an absolute POSIX path is rewritten before the program sees it, so a bare
`git grep '/app/config'` searches for `C:/Program Files/Git/app/config` and **cannot return
a hit in this repository whatever the tree holds**. The first version of this case listed
`expect: no output` as its PASS condition — i.e. it asked you to confirm the output of a
broken command, and a false "zero references" sentence reached the deliverable on the
strength of it (findings F13). Prove the trap to yourself first, then run the real search:

```bash
python -c "import sys; print(sys.argv[1:])" /app/config
#   -> ['C:/Program Files/Git/app/config']   <- the rewrite, demonstrated

cd "$SCRATCH/head"
# (a) the load-bearing bar: the OWUI-side trees
MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- status-pipe owui entrypoint.sh                                                 Dockerfile.openwebui-gpu dockerfile.tailscale
# (b) code outside documentation and this item's own comment block
MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- . ':!OB1' ':!documentation' ':!frontend/docker-compose.yml' | wc -l
# (c) everything, only to prove the grep is not broken
MSYS_NO_PATHCONV=1 git grep -n '/app/config' -- . ':!OB1' | wc -l
```

**PASS — three bars; only (a) is load-bearing:**

| bar | expected | meaning |
|---|---|---|
| (a) the OWUI-side trees | **exactly zero — no output** | none of the repo's `/app/config` references is OWUI's. This is the claim the anchor's criterion actually needs |
| (b) code, excluding docs and the comment block | **27**, across twelve files | a stable figure: it does not move when documentation is edited. A change here means a real consumer appeared or vanished |
| (c) whole repo minus `OB1/` | **NON-ZERO** (100 as written; it was 68 before this revision, because the note and the compose comment now quote the string themselves) | do not use the number as a bar — use it only to prove the search ran. **Zero here means the `MSYS_NO_PATHCONV=1` prefix did not take effect and you have measured nothing** |

Then **classify** the hits rather than counting them. Every one must fall in one of:
`little-coder`'s own `/app/config/little-coder.config.yaml`
(`little-coder/src/littlecoder/config.py:27`, `daemon.py:1086`,
`docker/Dockerfile.agent:46`, `docker/entrypoint-agent.sh:24`), mounted into **different
containers from a different source** by `coder/docker-compose.yml:113` and
`agent-org/docker/docker-compose.yml:325,417`; the unrelated `/app/config.yaml` of the
LiteLLM gateways; `documentation/archive/` tutorials; or this item's own docs and the
`frontend/docker-compose.yml` comment block.

**FAIL:** a hit in bar (a) — `status-pipe/`, `owui/`, `entrypoint.sh` or any OWUI pipe —
that would be a consumer the developer missed, and the mount must then be narrowed rather
than removed. **Also FAIL:** a zero in bar (c), which means the prefix did not take effect
on your shell and the case has measured nothing.

```bash
# 8b - nothing in the DEPLOYED image's code names it (read-only exec, no restart)
MSYS_NO_PATHCONV=1 docker exec openwebui sh -c 'grep -rIn "/app/config" /app/backend/open_webui /app/build'   # expect: no output

# 8c - no env var points at it
MSYS_NO_PATHCONV=1 docker exec openwebui sh -c 'env | grep -i config'     # expect: no output

# 8d - OWUI's own config comes from DATA_DIR, not /app/config
MSYS_NO_PATHCONV=1 docker exec openwebui sh -c 'sed -n "220,226p" /app/backend/open_webui/env.py'
```

**8e — the stored-function refutation.** Repo grep and image grep both miss OWUI *tools,
functions and pipes*, which live in `webui.db`, not on disk. Scan it read-only:

```bash
MSYS_NO_PATHCONV=1 docker exec openwebui python -c "
import sqlite3
con=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True); cur=con.cursor()
for t in [r[0] for r in cur.execute('select name from sqlite_master where type=\"table\"')]:
    for c in [r[1] for r in cur.execute(f'pragma table_info(\"{t}\")')]:
        try: n=cur.execute(f'select count(*) from \"{t}\" where cast(\"{c}\" as text) like \"%/app/config%\"').fetchone()[0]
        except Exception: continue
        if n: print(t,c,n)
"
```

**PASS:** the only tables with hits are `chat_message` (`content`, `output`), `chat`
(`chat`) and `file` (`data`) — chat transcripts and uploaded files. Open one hit and
confirm it is the pasted 2025 Ollama-era compose snippet, i.e. **user content**, not a
loaded code path.
**FAIL:** any hit in `function`, `tool`, `model`, `prompt` or `config` — a stored function
reading `/app/config` would mean the mount IS live and removing it breaks that function.
If you find one, the item fails this criterion and the mount must be narrowed instead.

**8f — the compose change is the removal and nothing else:**

```bash
git -C "$W" diff -M development..work/sl-colo-inference -- frontend/docker-compose.yml
```
**PASS:** the only removed line is `- ../config:/app/config:ro`, replaced by a comment
block recording the evidence; `openwebui-data:/app/backend/data`, the three narrow
`/host_project/*` mounts, `network_mode: service:openwebui` on tailscale and the
`depends_on` ordering are untouched.
**FAIL:** any other volume, the netns wiring or the `WEBUI_SECRET_KEY` guard changed.

**Not testable here, and say so in your report:** removing the mount from the compose file
does NOT remove it from the running container. Confirm that the running container still
shows `/app/config` (`docker exec openwebui ls /app/config` succeeds) and that this is
expected — the change lands at the next deliberate recreate of the frontend plane, which
is a gated deploy, not part of this test.

## T9 — no live pointer to an old path survives (the anchor's grep, and what it over-matches)

```bash
cd "$SCRATCH/head"
grep -rn 'llm-queue/\|config/litellm\|config/llama-swap\|config/chat-template' . \
  --exclude-dir=OB1 --exclude-dir=.git
# and the BACKSLASH forms, which a forward-slash grep cannot see (ps1/json/toml write these):
grep -rnF -e 'config\litellm' -e 'config\llama-swap' -e 'config\chat-template' -e '\llm-queue\' . \
  --exclude-dir=OB1 --exclude-dir=.git
```

**REBASE HAZARD — this case failed attempt 2, so run the sweep against the TREE, never
against `git diff`.** The branch was rebased mid-flight and `sl-closeout` had meanwhile
added a paragraph to `.env.example` naming `config/litellm.config.yaml`. The developer's
diff repointed only the pointers that existed on the OLD base, so `.env.example:281`
arrived **already stale from the base** and no case had ever looked at it. A stale pointer
can enter this branch without appearing in its diff. Worth listing what the rebase brought
in, so the sweep has a shortlist to be careful about:

```bash
git -C "$W" diff --name-only 9f64b84..development | sort
```

**PASS:** every hit is in one of these, and nothing else:

| where | why it is allowed |
|---|---|
| `inference/...` | the new home |
| `documentation/notes/...` | findings files; the anchor allows `notes/` |
| `CLEANUP-PLAN.md` | the anchor allows it (`:1190` is this move's own plan row) |
| `documentation/archive/`, `scripts/archive/` | the anchor allows `archive/` |
| `documentation/evidence/...` | this item's own plan + merged items' records; **declared, see below** |
| `agent-org/config/litellm-cloud.config.yaml`, `agent-org/README.md:22,89`, `agent-org/IMPLEMENTATION-NOTES.md:331`, `agent-org/docker/docker-compose.yml:522` | **substring collision, see below** |

**`.env.example` is NOT in that list and never will be** — it is the operator-facing
template, the anchor's artifact line names "`.env.example` comments" explicitly, and its
criterion says a live pointer to an old path FAILS. Both of its `config/litellm` lines must
read `inference/...`: **`:271`** (the OpenRouter fragment, which existed on the old base)
and **`:281`** (the J.1 master_key NOTE, which arrived with the rebase). Check them by
name rather than trusting the sweep:

```bash
grep -n 'config/litellm' "$SCRATCH/head/.env.example"
```

**PASS:** exactly two hits, both `inference/config/litellm...`.
**FAIL:** either one still bare — `ls config/litellm.config.yaml` in the tree then confirms
the file it sends the operator to does not exist.

> **DECLARED — two places the criterion's wording does not fit.**
>
> 1. **`documentation/evidence/stack-layers/*-test-plan.md`** are historical execution
>    records of MERGED items (`sl-inference-split`, `sl-colo-portal`) — the commands a
>    tester actually ran against the tree as it then stood. Rewriting them would make the
>    record claim something that was never run. They were left untouched on purpose. The
>    anchor's exemption list (`archive/`, `notes/`, `CLEANUP-PLAN.md`) does not name
>    `documentation/evidence/`; that is a gap in the wording, not a stale pointer. Put it
>    to the gate.
> 2. **`agent-org/config/litellm-cloud.config.yaml`** contains the literal substring
>    `config/litellm`, so four agent-org lines match the grep. All four are correct:
>    `agent-org/docker/docker-compose.yml` sits in `agent-org/docker/`, so its
>    `../config/...` is `agent-org/config/`, agent-org's own single-file config directory
>    for the profile-gated cloud gateway. Verify rather than take this on trust:
>    `ls agent-org/config/` shows exactly `litellm-cloud.config.yaml`, and the T5d render
>    diff is `IDENTICAL`.

**FAIL:** a hit anywhere else — a live pointer at a path that no longer exists.

**T9b — the pointers that WERE repointed still describe reality.** For each, open the file
it names and confirm the claim, do not just confirm the path exists:

| pointer | check |
|---|---|
| `.github/workflows/ci.yml:102-103` | both lines say `inference/llm-queue`; T7 runs them |
| `scripts/checks/check-llm-gateway-routing.ps1:13` and its `$allowPathLike` entry | T6d proves the entry matches |
| `inference/llm-queue/README.md` "Revert" step 1 | **it now names `inference/config/litellm/model_list/local.yaml`.** Open that file and confirm the two `qwen36-27b` `api_base` lines it tells you to change are actually there — on the base commit this instruction pointed at `litellm.config.yaml`, which has had no `model_list` since `sl-inference-split` (finding F4). A path that exists is not the same as an instruction that works. |
| `inference/llm-queue/README.md` "Development & iteration" | the rebuild command now names `-f inference/docker-compose.yml`. Confirm the bare `docker compose build llm-queue` it replaced really would have failed: the root `docker-compose.yml` is the network anchor and declares **zero** services (finding F5). |
| `documentation/runbooks/queue-eta-notifications.md:105,108` | the two `inference/llm-queue/src/...` files exist and contain `get_queue` / `snapshot()` |
| `.claude/skills/stack-map/references/workspace-stacks.md:143-145,157,158` | the three config paths and `inference/llm-queue/` resolve |
| `SECURITY.md:35`, `.env.example:265`, `README.md:147`, `status-pipe/serve/tailscale_serve_pipe.py:49,1071`, `agent-org/config/litellm-cloud.config.yaml:8` | each named path resolves |

## T10 — what was REMOVED, not only what is present

```bash
git -C "$W" diff -M --stat development..work/sl-colo-inference
git -C "$W" diff -M       development..work/sl-colo-inference -- ':!documentation/notes' ':!documentation/evidence' | grep '^-' | grep -v '^---'
```

Read every removed line. **PASS:** every one is either (a) the old half of a path that was
repointed on the line below it, (b) the `- ../config:/app/config:ro` mount of T8, or (c) a
comment line rewrapped around a longer path. **FAIL:** a removed line that carried
information now recorded nowhere — check the findings file before calling it lost, and if
it is genuinely lost, fail the item.

Specifically confirm these were NOT lost: the `# (paths refreshed 2026-08-21: G.2 ...)`
comment in the routing check (it should still sit above the recovery/checks entries), the
`sits on two internal:true networks` clause in `.env.example`, and the
`llama-cpp-upstream + set llama-swap concurrencyLimit back to 32` revert note in
`local.yaml`.

## T11 — repo-wide checks stay green

**T11a — the two that work in an export:**

```bash
cd "$SCRATCH/head"
python -m ruff check .
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-project-configs.ps1
```

**PASS:** `ruff` → `All checks passed!`; project-configs → `all 7 compose projects render
clean` **and** `stack-services.json inventory matches the compose configs`.

**T11b — line endings, WHICH MUST NOT BE RUN IN A `git archive` EXPORT.**
`validate-lineendings.ps1` enumerates its inputs with `git ls-files`. An export has no
`.git`, so the list is empty and the script prints a SUCCESS having checked nothing — the
same vacuous-green class as F3's routing check, and the plan sent the first tester into it.
Run it where git can answer: a real worktree (your own detached one, or read-only in the
developer's).

```bash
cd <a real worktree of the commit under test>
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/validate-lineendings.ps1
```

**PASS:** `SUCCESS: All tracked shell scripts have Unix line endings` — the message naming
*tracked shell scripts*.
**FAIL, and read the message, do not just read the exit code:**
`SUCCESS: No tracked shell scripts to check` is the vacuous form. Exit 0 either way.

**Note on ruff (finding F11), REVISED for the rebased base:** on the item's original base
`9f64b84`, `ruff check .` was RED — `llm_queue/__init__.py:9` was 103 chars against the
subproject's `line-length = 100` — and the developer re-wrapped it. `sl-closeout` fixed the
same line differently and reached `development` first, so the rebase onto `be00d53`
conflicted there and was resolved in **`development`'s favour**. The branch therefore
carries **none** of the developer's wrap, and ruff's green is now attributable to the base.
Confirm it:

```bash
git -C "$W" diff -M --stat development..work/sl-colo-inference -- '*llm_queue/__init__.py'
```

**PASS:** `0` insertions, `0` deletions — a pure rename. (Without `-M` git prints it as a
new file: the rename's other half is filtered out by the pathspec, which is not a content
change.)
**FAIL:** any content delta — the conflict was resolved the other way and F11 is stale.

## T12 — the live plane was not touched

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -E 'llm-|llama-cpp|openwebui|tailscale'
```

**PASS:** every inference and frontend container shows an uptime that PREDATES the start of
this item's test session, and images are the same tags as before (`llm-queue:local`, the
pinned LiteLLM digest, `openwebui:local`). No `:wt-*` tagged image exists
(`docker images | grep ':wt-'` → empty).
**FAIL:** any restart, recreate or new tag. Nothing in this plan should have produced one;
if you see one, find out which case did and report it before anything else.

## T13 — the live hazard: is the findings note's merge warning TRUE, and complete?

This item deletes a directory that four **running** containers bind out of. Findings **F14**
states the hazard and prescribes the landing step. This case checks F14 against the live
system, because a warning that is wrong is worse than none.

**Read-only throughout. Do NOT start, stop, restart or recreate anything.**

**T13a — the six binds are real, and F14's table is exact.**

```bash
for c in llm-gateway llm-gateway-ui llama-cpp-upstream llama-cpp-embed-upstream llm-queue openwebui; do
  echo "=== $c"
  MSYS_NO_PATHCONV=1 docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}{{end}}' "$c"
done
```

**PASS:** exactly the six rows F14 lists, with those destinations, and **nothing** under
`config/` on `llama-cpp-embed-upstream`, `llm-queue` or `llm-gateway-db`.
**FAIL:** a seventh container or a seventh bind under `config/` that F14 does not name —
the note is then incomplete and the landing step under-scoped. A bind F14 lists that is NOT
present is also a fail: the note would be warning about something that is not there.

**T13b — the note actually says it.** Grep the findings note for the landing step:

```bash
grep -n 'F14\|lease\|recreate\|up -d' "$SCRATCH/head/documentation/notes/stack-layers-sl-colo-inference-findings.md"
```

**PASS:** F14 exists, is flagged in the note's header, names all four containers, states
that a compose-file edit does not rewrite a running container's `HostConfig`, distinguishes
the **recreating** path (`compose up -d`) from the **non-recreating** ones (bare
`docker restart` / `docker start`, `compose restart`, and the automatic
`restart: unless-stopped` after a host or Docker reboot), and prescribes an `inference`
lease + `docker compose -f inference/docker-compose.yml --env-file .env up -d`, with the
frontend's `openwebui` at its next deliberate recreate under the netns rule.
**FAIL:** any of those missing. This is the case whose absence the first plan was failed
for; do not accept a note that gestures at "recreate later".

**T13c — the classification of repair paths, checked against the code and not against the
note.** This is the case that failed attempt 2, and it failed because it was run as
written: F14 had the right rule and the wrong inventory. **Enumerate every call site; do
not accept the note's list.**

```bash
cd "$SCRATCH/head"
# every compose verb issued by the two scripts that repair things
grep -n "Invoke-PlaneCompose" scripts/checks/stack-watchdog.ps1
sed -n '162,185p'             scripts/checks/stack-watchdog.ps1
grep -nE 'docker compose .*(up -d|restart|start)' scripts/recovery/emergency-recovery.ps1
grep -n 'Invoke-MinimalRecovery\|Start-InferenceStack' scripts/recovery/emergency-recovery.ps1
```

**PASS — the classification in F14 must match what those four commands print, in both
directions:**

*Recreating (safe).* `stack-watchdog.ps1:640` repairs an unhealthy container with
`-Action @('up','-d')`, and `Invoke-PlaneCompose` (`:162`, building `$Argv` at `:174`,
running it at `:179`) turns that into `docker compose <plane args> up -d <service>`;
`emergency-recovery.ps1:600` runs `up -d llm-queue llm-gateway`; `Start-InferenceStack`
(`:221-228`, called at `:802`, `:960`, `:1023`) runs `up -d` for the whole plane.

*Reusing the existing container (dangerous).* `stack-watchdog.ps1:747` uses
`-Action @('restart')` — harmless, it targets only `llama-cpp-embed-upstream`, which binds
nothing under `config/`. **And, the ones the first version of F14 missed:**
`emergency-recovery.ps1:578` (`compose restart llama-cpp-upstream llama-cpp-embed-upstream`)
and `:580` (`compose restart openwebui`) inside `Invoke-MinimalRecovery` (`:560`) — both
containers ARE in T13a's table. F14 must name them, must say that `Invoke-MinimalRecovery`
is the **first** branch of both `recover` (`:727`) and `nuclear` (`:905`), and must say that
the healing `up -d` at `:600` is 22 lines later in the same function and covers only
`llm-queue` and `llm-gateway` — never `llama-cpp-upstream`.

*And read to the end of the function before accepting how bad it is.*
`Invoke-MinimalRecovery` closes with `Test-BasicConnectivity` (`:620`), which at `:519`
execs `docker exec llama-cpp-upstream curl -f -s http://localhost:8080/health`. A broken
upstream therefore returns `$false`, the caller falls through to full/nuclear recovery, and
`Start-InferenceStack`'s `up -d` repairs it. F14 must say this too: the script does **not**
leave the plane wedged and does **not** report a false success — it breaks the upstream
first, cycles `openwebui` and its netns companion, waits 60 s, and escalates the operator
into a teardown they did not need. An F14 that claims a permanent wedge is as wrong as one
that claims safety.

**FAIL:** any compose/docker verb that reuses an existing container, issued against a
container in T13a's table, that F14 does not name — or an F14 whose severity does not match
what the function actually does end to end. "It goes through compose" is not the property
that makes a path safe; "it recreates the container" is.

**T13e — the landing step is stated as a precondition, not a suggestion.**

```bash
NOTE="$SCRATCH/head/documentation/notes/stack-layers-sl-colo-inference-findings.md"
grep -n 'BEFORE anyone runs\|COMPOSE_PROFILES\|lease.ps1\|docker inspect' "$NOTE"
grep -n -A6 '^## Revert' "$SCRATCH/head/inference/llm-queue/README.md"
```

**PASS:** F14's landing step says to recreate **before** anyone runs
`emergency-recovery.ps1 recover`/`nuclear`; names the `inference` lease; gives
`docker compose -f inference/docker-compose.yml --env-file .env up -d` **with the
operator's `COMPOSE_PROFILES` in `.env`** (without `local`, `llama-cpp-upstream` is not in
the rendered set and keeps its stale spec — the one way to run the prescribed command and
still miss the container that matters); offers a `docker inspect` verification of the new
sources; and carries the carve-out for `inference/llm-queue/README.md`'s Revert section,
whose `compose restart` is correct because that revert edits file CONTENTS at unchanged
paths. The README says so at its own call site too, so a reader who arrives there without
F14 does not "fix" it.
**FAIL:** the landing step reads as optional or omits the profile caveat; or the README's
`restart` is left unexplained, so the next reader of F14 changes a correct instruction.

**T13d — the plane is already a merge behind, so the recreate deploys two items.**

```bash
MSYS_NO_PATHCONV=1 docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' llm-gateway
```

**PASS:** the running `llm-gateway` shows `/app/config.yaml` and `/app/custom_callbacks.py`
and has **no** `/app/conf.d`, no `/app/assemble-config.py` and no `/app/config.base.yaml` —
i.e. it predates `sl-inference-split`, and F14 says so. Not this item's doing; it means
whoever performs the recreate brings the config-assembly mechanism live at the same time.
**FAIL:** F14 omits it, or the gateway already carries `/app/conf.d` (then F14's closing
paragraph is stale and should be dropped rather than left to mislead).

---

## Case-to-criterion map

| anchor acceptance criterion | cases |
|---|---|
| `git log --follow` count > 1 for `policy.py` and `litellm.config.yaml` | T1 |
| `python -m pytest inference/llm-queue -q` + ci.yml path | T7 |
| the three renders, each bind source existing on disk | T5a, T5b, T4b |
| frontend config mount removed with evidence, or narrowed | T8 (all parts) |
| unbounded grep hits only archive/notes/CLEANUP-PLAN; `config/` gone | T9, T2 |
| routing check still scans the moved files (plant → red) | T6 |
| names/tags/aliases/networks unchanged; live plane untouched | T5c, T5d, T12 |
| *(no anchor criterion)* — the post-merge live hazard the findings note must carry | **T13a-e** |

## Open declarations for the gate

1. **T5a** — the anchor's `--profile gpu --profile tailscale` for the frontend names
   profiles that **do not exist** in `frontend/docker-compose.yml` (base or head).
2. **T9** — the grep exemption list does not cover `documentation/evidence/`, which holds
   merged items' execution records that must not be rewritten.
3. **T9** — `agent-org/config/litellm-cloud.config.yaml` is a substring collision with
   `config/litellm`; the four agent-org hits are correct as written.
4. **F1 (findings)** — the anchor's artifact line "agent-org's mount of config/litellm
   repointed" is based on a premise that is false: agent-org never mounted it. The
   criterion is met vacuously and the agent-org render is byte-identical.
5. **F3 (findings)** — `check-llm-gateway-routing.ps1` is vacuous from any worktree under
   `.claude/`. This affects every item, not just this one; it is not fixed here.
6. **F11 (findings)** — `ruff check .` was red on the item's ORIGINAL base `9f64b84`. After
   the rebase onto `be00d53` the E501 fix comes from `development` (`sl-closeout` fixed the
   same line first and won the conflict), so ruff's green is now attributable to the base
   and the branch carries no wrap of its own. T11's note is revised accordingly.
7. **T13 / F14** — this item cannot be merged and left alone: six bind sources on four
   running containers point into the deleted `config/`. The landing step is a compose
   recreate of the inference plane under its lease, **before anyone runs
   `emergency-recovery.ps1 recover` or `nuclear`** — attempt 2 established that the
   recovery script's first branch `compose restart`s `llama-cpp-upstream`, so until the
   recreate happens the repair tool is itself a way to break the plane. That is an
   operational action for the merger, outside every anchor criterion, and the gate should
   confirm someone owns it.
8. **F15 (findings)** — a rebase can carry a stale pointer INTO a branch without it ever
   appearing in the branch's diff. `.env.example:281` did exactly that. Nothing in this
   repo's checks catches it; only a sweep of the tree does, which is now T9's opening line.
