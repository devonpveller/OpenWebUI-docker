# Test plan — `sl-colo-inference`

**Item:** `sl-colo-inference` (stack-layers PLAN 2.7, Part L.1, wave 2).
**Branch:** `work/sl-colo-inference` · **Base:** `development` @ `9f64b84` (which already
contains `sl-inference-split`).
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
git -C "$W" archive 9f64b84                | tar -x -C "$SCRATCH/base"
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
git -C "$W" show --name-status -M work/sl-colo-inference | grep -c '^R'
git -C "$W" show --name-status -M work/sl-colo-inference | grep '^R' | awk '{print $2}' | sed 's|/.*||' | sort | uniq -c
```

**PASS:** `40`, split `8 config` / `32 llm-queue`.
**FAIL:** fewer — some file was recorded as an add+delete, not a rename.
(Use `--name-status`, not `--stat`: `--stat` elides long paths with `...` and
under-counts.)

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
| agent-org | `agent-org/agent-bridge/secrets`, `backups/agent-bridge-db`, `backups/mattermost-db` | (unchanged by this item) |

**FAIL:** any path under `inference/config/`, `inference/llm-queue/` or `agent-org/config/`
does not exist; or a runtime directory is missing on the head that existed on the base.
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

```bash
# 8a - nothing in this repo names the path
cd "$SCRATCH/head" && git -C "$W" grep -n '/app/config' -- . ':!OB1'     # expect: no output

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
git -C "$W" diff -M 9f64b84..work/sl-colo-inference -- frontend/docker-compose.yml
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
```

**PASS:** every hit is in one of these, and nothing else:

| where | why it is allowed |
|---|---|
| `inference/...` | the new home |
| `documentation/notes/...` | findings files; the anchor allows `notes/` |
| `CLEANUP-PLAN.md` | the anchor allows it (`:1190` is this move's own plan row) |
| `documentation/archive/`, `scripts/archive/` | the anchor allows `archive/` |
| `documentation/evidence/stack-layers/sl-*-test-plan.md` | **declared, see below** |
| `agent-org/config/litellm-cloud.config.yaml`, `agent-org/README.md:22,89`, `agent-org/IMPLEMENTATION-NOTES.md:331`, `agent-org/docker/docker-compose.yml:522` | **substring collision, see below** |

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
git -C "$W" diff -M --stat 9f64b84..work/sl-colo-inference
git -C "$W" diff -M       9f64b84..work/sl-colo-inference -- ':!documentation/notes' ':!documentation/evidence' | grep '^-' | grep -v '^---'
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

```bash
cd "$SCRATCH/head"
python -m ruff check .
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-project-configs.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/validate-lineendings.ps1
```

**PASS:** `ruff` → `All checks passed!`; project-configs → `all 7 compose projects render
clean` **and** `stack-services.json inventory matches the compose configs`; line endings →
`SUCCESS`.

**Note on ruff, so the green is not over-read (finding F11):** `ruff check .` was **RED on
the base commit** — `llm-queue/src/llm_queue/__init__.py:9` was 103 chars against the
subproject's `line-length = 100`, and the fix for it lives on the unmerged `sl-closeout`
branch. This item re-wrapped that docstring line because the file became this item's.
So green here is partly attributable to that wrap, not to the move. Confirm the wrap is
docstring-only (`git diff -M 9f64b84..work/sl-colo-inference -- inference/llm-queue/src/llm_queue/__init__.py`)
and touches no code.

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
6. **F11 (findings)** — `ruff check .` was red on the base; this item's green is partly a
   pre-existing E501 fix in a file it moved.
