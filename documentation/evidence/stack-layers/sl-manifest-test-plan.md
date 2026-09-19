# sl-manifest - test plan

**Item:** `sl-manifest` (stack-layers wave 1)
**Branch:** `work/sl-manifest`, base `development` @ b28cbc5
**Developer worktree:** `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-manifest`
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-manifest.json`
**Findings sink:** `documentation/notes/stack-layers-sl-manifest-findings.md`

Artifacts under test:

| Path | What |
|---|---|
| `stack.manifest.toml` | the manifest (9 planes, 10 products) |
| `scripts/stack/stack.py` | the driver |
| `scripts/stack/test_stack.py` | hermetic pytest suite (37 tests) |
| `scripts/stack/README.md` | schema + every verb's refusal cases |
| `.gitignore` | `.stack/` added |

---

## How to run this

**Everything is read-only.** No case starts, stops, restarts or recreates a
container; the only live docker calls are `docker compose ... ps`,
`docker compose version` and `docker network inspect`-free health-free reads.
**No plane lease is required.**

Work from the developer worktree root:

```text
cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-manifest"
```

Every case gives the **PowerShell 5.1** form and the **Git Bash** form where
they differ. `python` resolves to 3.13.3 on this host; the driver requires
>= 3.11.

### Scratch state, and the rule about `.env`

The driver's real state file is `.stack/state.json`. **Do not create it** -
several cases depend on it being absent. Cases that need a state file write it
to a scratch path with `--state`, and the blank-key case uses a scratch *root*
with `--root`. Everything scratch lives under `.stack/`, which is gitignored, so
`git status` stays clean.

**Never edit the real `.env`, `OB1/docker/.env` or `agent-org/docker/.env`.**
T7 builds its own.

Set up once, and tear down at the end:

```powershell
# PowerShell 5.1
Remove-Item -Recurse -Force .stack -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force .stack\scratch | Out-Null
Copy-Item stack.manifest.toml .stack\scratch\
Set-Content -Encoding ascii .stack\scratch\.env "MULLVAD_WG_PRIVATE_KEY=`nMULLVAD_WG_ADDRESSES=10.64.0.2/32"
```

```bash
# Git Bash
rm -rf .stack && mkdir -p .stack/scratch && cp stack.manifest.toml .stack/scratch/
printf 'MULLVAD_WG_PRIVATE_KEY=\nMULLVAD_WG_ADDRESSES=10.64.0.2/32\n' > .stack/scratch/.env
```

Teardown: `Remove-Item -Recurse -Force .stack` / `rm -rf .stack`.

---

## T1 - the manifest's edges match the compose files, both ways

*Anchor criterion 1. This is the case that cannot be automated away: read each
cited line and judge it.*

The manifest carries every edge as a cited comment above the plane it belongs
to. Confirm **each row below** by opening the file at the line, and then hunt
for an edge that exists in a compose file and is **missing** from the table -
either direction FAILS.

### requires (hard - `up` orders by these; `enable` refuses on them)

| Edge | Evidence (open and read) | What it says |
|---|---|---|
| every plane -> `anchor` | `inference/docker-compose.yml:446-455`, `frontend/docker-compose.yml:290-298`, `memory/docker-compose.yml:138-140`, `search/docker-compose.yml:173-175`, `coder/docker-compose.yml:212-214`, `OB1/docker/docker-compose.yml:1268-1286`, `agent-org/docker/docker-compose.yml:748-750`, `portal/docker-compose.yml:603-606` | each declares `external: true` + `name: ai-stack_*`, the networks the root `docker-compose.yml` owns |
| `memory` -> `inference` | `memory/docker-compose.yml:36`, `:38` | `LLM_BASE_URL=http://llama-cpp:8080/v1`, `EMBED_BASE_URL=http://llama-cpp-embed:8080/v1` - unconditional |
| `coder` -> `inference` | `coder/docker-compose.yml:28`, `:47-48`, `:76`; plus `little-coder/config/little-coder.config.yaml:11` and `little-coder/config/models.json:6` | joins `llm-net` "for llama-cpp inference", exempts `llama-cpp` from the egress proxy; the base URL itself is in the mounted config (see finding F5) |
| `ob1` -> `inference` | `OB1/docker/docker-compose.yml:116`, `:120`, `:406`, `:410`, `:444`, `:493`, `:497`, `:568`, `:571`, `:675`, `:681`, `:846`, `:941`, `:977`, `:1173`; `OB1/docker/docker-compose.scheduled.yml:186`, `:258` | `CHAT_API_BASE` / `EMBEDDING_API_BASE` on the whole fleet |
| `ob1` -> `search` | `OB1/docker/docker-compose.yml:567`, `:637`, `:982`; `OB1/docker/docker-compose.scheduled.yml:190` | `SEARCH_API_BASE` default `http://gateway:8080`; `FETCH_PROXY_URL` default `http://vpn:8888` on research, the grounding backfiller and the digest chain |
| `agent-org` -> `inference` | `agent-org/docker/docker-compose.yml:127`, `:257`, `:372-373`, `:457-458` | `AO_LOCAL_API_BASE: http://llama-cpp:8080/v1`; agent-bridge joins `llm-net`; workers exempt `llama-cpp` from their proxy |
| `portal` -> `frontend` | `portal/docker-compose.yml:601-606` and `config/caddy/Caddyfile:197` | caddy joins `ai-stack_app-net` "to reach openwebui:8080"; `reverse_proxy openwebui:8080` |

### optional (soft - documentation only; never orders, never refuses)

| Edge | Evidence | Why soft |
|---|---|---|
| `frontend` -> `inference` | `frontend/docker-compose.yml:156`, `:158`, `:159` | `LLAMA_CPP_HOST` defaults to the alias but sits behind `LLAMA_CPP_ENABLED` (`:158`); OWUI serves without it |
| `frontend` -> `search` | `frontend/docker-compose.yml:97` | `SEARXNG_QUERY_URL` default `http://gateway:8080/search`; web search off, chat unaffected |
| `ob1` -> `frontend` | `OB1/docker/docker-compose.yml:562` | `OWUI_BASE_URL` default `http://openwebui:8080` on openbrain-research only |
| `portal` -> `ob1` | `config/caddy/Caddyfile:136`, `:143`, `:242`, `:250` | those vhosts 502; the rest of the portal serves |
| `portal` -> `inference` | `config/caddy/Caddyfile:295` and `inference/docker-compose.yml:316-327` | the LiteLLM Admin UI vhost; `llm-gateway-ui` joins `app-net` for exactly this. **Not in the brief's edge list** - see finding F4 |

### Deliberately NOT edges

| Non-edge | Evidence | Why |
|---|---|---|
| `agent-org` -> `coder` | `agent-org/docker/docker-compose.yml:294`, `:351`, `:392`, `:438` | agent-org RUNS `little-coder:local` / `little-coder-open-terminal:local`, images the coder plane BUILDS. No container-to-container traffic. Recorded as a `host` requirement on the agent-org plane instead |
| `digest` as a plane | `OB1/docker/docker-compose.scheduled.yml:21` (`name: open-brain`) and `OB1/docker/docker-compose.yml:15-16` (`include:`) | the scheduled slice is part of the ob1 project; it is a *product* over `inference + search + ob1`, not a plane |

### surfaces (PLAN section 1)

| Product | `surfaces` | PLAN section 1 says |
|---|---|---|
| `research` | `ob1 = ["wiki", "notebook"]` | research -> wiki, notebook |
| `coding-agent` | `frontend = []` | little-coder -> frontend |
| `agent-org` | *(none)* | agent-org -> mattermost is internal |
| `open-brain` | `ob1 = ["wiki"]` | the wiki is a surface |

**Pass:** every cited line says what the table says it says; no compose-file
cross-plane `depends_on` or hardcoded cross-plane URL is missing from the
manifest; no manifest edge is unevidenced.
**Fail:** any citation that does not support its edge; any edge found in a
compose file and absent from the manifest; any manifest edge with no evidence.

---

## T2 - the hermetic test suite

*Anchor criterion 2.*

```powershell
python -m pytest scripts/stack -q
```

```bash
python -m pytest scripts/stack -q
```

**Pass:** `37 passed`, in both shells, in a few seconds. **The suite never
contacts a Docker daemon** - confirm by reading `scripts/stack/test_stack.py`:
the only thing passed as `runner` is `Recorder`, which appends to a list and
returns an exit code. Optional stronger check: stop Docker Desktop and re-run;
the suite must still pass.
**Fail:** any failure; any test that reaches `subprocess`; a suite that passes
only because Docker happens to be running.

---

## T3 - standard library only

*Anchor criterion 3. Run the anchor's check verbatim.*

```powershell
python -c "import ast,sys,pathlib; t=ast.parse(pathlib.Path('scripts/stack/stack.py').read_text()); mods={(n.names[0].name if isinstance(n,ast.Import) else n.module).split('.')[0] for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom))}; bad=[m for m in mods if m not in sys.stdlib_module_names]; print(bad); sys.exit(1 if bad else 0)"
"exit=$LASTEXITCODE"
```

```bash
python -c "import ast,sys,pathlib; t=ast.parse(pathlib.Path('scripts/stack/stack.py').read_text()); mods={(n.names[0].name if isinstance(n,ast.Import) else n.module).split('.')[0] for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom))}; bad=[m for m in mods if m not in sys.stdlib_module_names]; print(bad); sys.exit(1 if bad else 0)"; echo "exit=$?"
```

**Expected:** `[]` and `exit=0`.
**Pass:** empty list, exit 0. **Fail:** any module named, or a non-zero exit.
(`test_driver_imports_only_the_standard_library` in T2 runs the same check.)

---

## T4 - no state file: frontend only

*Anchor criterion 4, first half.*

Precondition: `.stack/state.json` does not exist (the setup block removed it).

```powershell
python scripts\stack\stack.py list
```

```bash
python scripts/stack/stack.py list
```

**Expected (verbatim, `planes:` block):**

```text
manifest: stack.manifest.toml
state:    .stack/state.json  (absent - defaults: frontend)

planes:
  anchor     disabled
  inference  disabled
  frontend   enabled
  memory     disabled
  search     disabled
  coder      disabled
  ob1        disabled
  agent-org  disabled
  portal     disabled  manual: scripts/portal/portal-on.ps1 / scripts/portal/portal-off.ps1

up would start: anchor, frontend
```

followed by the ten products with their descriptions.

**Pass:** `frontend` is the only plane whose status token is `enabled`; every
other plane's status token is `disabled` - the anchor included. (The anchor is
`implicit`: `up` starts it because the frontend requires it, and the
`up would start:` line says so; nothing put it in a state file, so it is not
"enabled". `scripts/stack/README.md`, *Ordering*, explains the distinction.)
**Fail:** any second plane reading `enabled`; a missing plane; an absent
`up would start:` line.

---

## T5 - no state file: `up --dry-run` is the anchor then the frontend, and starts nothing

*Anchor criterion 4, second half.*

```powershell
docker ps --format "{{.Names}}" | Measure-Object -Line     # note the count
python scripts\stack\stack.py up --dry-run
"exit=$LASTEXITCODE"
docker ps --format "{{.Names}}" | Measure-Object -Line     # same count
```

```bash
docker ps --format '{{.Names}}' | wc -l
python scripts/stack/stack.py up --dry-run; echo "exit=$?"
docker ps --format '{{.Names}}' | wc -l
```

**Expected:**

```text
docker compose -f docker-compose.yml --env-file .env up -d
docker compose -f frontend/docker-compose.yml --env-file .env up -d
exit=0
```

**Pass:** exactly those two lines, in that order, and the container count is
unchanged. **Fail:** a third line; the wrong order; any change to the running
container set; a non-zero exit.

---

## T6 - `enable memory` refuses and names inference and the remedy

*Anchor criterion 5, first refusal.*

```powershell
python scripts\stack\stack.py enable memory
"exit=$LASTEXITCODE"
Test-Path .stack\state.json      # must be False
```

```bash
python scripts/stack/stack.py enable memory; echo "exit=$?"
test -f .stack/state.json && echo "STATE WRITTEN - FAIL" || echo "no state file - ok"
```

**Expected:**

```text
# note: 'memory' names both a plane and a product; enabling the PLANE (use `--product memory` for the product)
refused: memory requires inference, which is not enabled (python scripts/stack/stack.py enable inference)
exit=1
```

**Pass:** exit 1; the message names `inference` **and** the command
`python scripts/stack/stack.py enable inference`; no state file was written.
**Fail:** exit 0; a message that does not name inference; a message that names
no remedy command; a state file appearing.

*(The `# note:` line is expected: `memory` is both a plane and a product name,
and a bare name resolves to the plane - which is what makes this refusal
happen at all. `scripts/stack/README.md`, *Product keys*.)*

---

## T7 - `enable search` with `MULLVAD_WG_PRIVATE_KEY` blank refuses and names the key

*Anchor criterion 5, second refusal. Uses the scratch root from the setup block;
the real `.env` is never touched.*

```powershell
Get-Content .stack\scratch\.env            # MULLVAD_WG_PRIVATE_KEY= is blank
python scripts\stack\stack.py --root .stack\scratch enable search
"exit=$LASTEXITCODE"
```

```bash
cat .stack/scratch/.env
python scripts/stack/stack.py --root .stack/scratch enable search; echo "exit=$?"
```

**Expected:**

```text
# note: 'search' names both a plane and a product; enabling the PLANE (use `--product search` for the product)
refused: search needs these keys before it can be enabled:
  MULLVAD_WG_PRIVATE_KEY is blank in .env
Set them, then re-run.
exit=1
```

**Pass:** exit 1; the message names `MULLVAD_WG_PRIVATE_KEY`, says it is
**blank**, and names the file it looked in. **Fail:** exit 0; a refusal that
does not name the key; a refusal that names no file.

Second half - a key that is entirely **absent** must also refuse, and say so:

```bash
grep -v '^MULLVAD_WG_PRIVATE_KEY=' .stack/scratch/.env > .stack/scratch/.env.tmp && mv .stack/scratch/.env.tmp .stack/scratch/.env
python scripts/stack/stack.py --root .stack/scratch enable search; echo "exit=$?"
```

**Pass:** exit 1 and `MULLVAD_WG_PRIVATE_KEY is missing in .env`.

---

## T8 - `enable research` pulls inference, search, ob1 (research + wiki + notebook) and frontend

*Anchor criterion 5, third clause.*

```powershell
python scripts\stack\stack.py --state .stack\t-research.json enable research
"exit=$LASTEXITCODE"
Get-Content .stack\t-research.json
```

```bash
python scripts/stack/stack.py --state .stack/t-research.json enable research; echo "exit=$?"
cat .stack/t-research.json
```

**Expected:**

```text
enabled product research:
  inference
  frontend
  search
  ob1  profiles: idea-refinery, research, wiki, notebook
# note: PENDING profiles enabled (ob1:research, ob1:wiki, ob1:notebook) - the compose files do not carry them yet, so enabling them changes nothing until the item that adds them lands.
state: .stack/t-research.json
exit=0
```

and the state file naming exactly `frontend`, `inference`, `ob1`, `search`, with
`ob1.profiles` = `idea-refinery, research, wiki, notebook`.

**Pass:** exit 0; the four planes enabled; ob1 carrying the three product
profiles plus `idea-refinery`; `anchor` **not** in the state file (it is
implicit - T5 shows `up` starts it anyway).
**Fail:** a missing plane; a missing profile; a silent enable that does not say
the three profiles are still pending (the compose files do not carry them until
`sl-ob1-profiles` lands - claiming otherwise would be a false green).

---

## T9 - `--headless` omits the wiki and notebook profiles

*Anchor criterion 5, fourth clause.*

```powershell
python scripts\stack\stack.py --state .stack\t-headless.json enable research --headless
Get-Content .stack\t-headless.json
```

```bash
python scripts/stack/stack.py --state .stack/t-headless.json enable research --headless
cat .stack/t-headless.json
```

**Expected:**

```text
# --headless: dropped surface profiles ob1:wiki, ob1:notebook
enabled product research:
  inference
  frontend
  search
  ob1  profiles: idea-refinery, research
# note: PENDING profiles enabled (ob1:research) - the compose files do not carry them yet, so enabling them changes nothing until the item that adds them lands.
state: .stack/t-headless.json
```

**Pass:** the same four planes (the frontend is a `planes` member of `research`,
not only a surface, so `--headless` keeps it); `ob1.profiles` = `idea-refinery,
research` and **no** `wiki` or `notebook`.
**Fail:** `wiki` or `notebook` present; the frontend dropped.

Cross-check that `--headless` *does* drop a plane that is only a surface:

```bash
python scripts/stack/stack.py --state .stack/t-ca.json init --planes inference >/dev/null
python scripts/stack/stack.py --state .stack/t-ca.json enable coding-agent --headless
cat .stack/t-ca.json     # inference + coder, no frontend
```

**Pass:** `inference` and `coder` only. Re-running without `--headless` (into a
fresh `--state` path) adds `frontend`.

---

## T10 - every plane enabled: `up` matches stack.ps1's order, `down` is the reverse

*Anchor criterion 6, first half.*

```powershell
python scripts\stack\stack.py --state .stack\t-all.json init --planes inference,frontend,memory,search,coder,ob1,agent-org,portal
python scripts\stack\stack.py --state .stack\t-all.json up --dry-run
python scripts\stack\stack.py --state .stack\t-all.json down --dry-run
```

```bash
python scripts/stack/stack.py --state .stack/t-all.json init --planes inference,frontend,memory,search,coder,ob1,agent-org,portal
python scripts/stack/stack.py --state .stack/t-all.json up --dry-run
python scripts/stack/stack.py --state .stack/t-all.json down --dry-run
```

**Expected `up`:**

```text
docker compose -f docker-compose.yml --env-file .env up -d
docker compose -f inference/docker-compose.yml --env-file .env up -d
docker compose -f frontend/docker-compose.yml --env-file .env up -d
docker compose -f memory/docker-compose.yml --env-file .env up -d
docker compose -f search/docker-compose.yml --env-file .env up -d
docker compose -f coder/docker-compose.yml --env-file .env up -d
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery up -d
docker compose -f agent-org/docker/docker-compose.yml up -d
# portal is not driven by stack.py - start it with scripts/portal/portal-on.ps1 / scripts/portal/portal-off.ps1
```

**Expected `down`:** the same eight commands with `down` instead of `up -d`, in
exactly reversed order, then the matching `#` comment.

Compare against `scripts/stack/stack.ps1:41-50` (the `$Projects` array) and
`:56-63` (`Invoke-Project`):

- order `anchor, inference, frontend, memory, search, coder, ob1, agent-org` - matches
- `--env-file .env` on every plane **except** ob1 and agent-org (`OwnEnv = $true`) - matches
- `--profile idea-refinery` on ob1 - matches
- portal absent - matches `stack.ps1`'s header, which says the portal is
  deliberately not managed there. The manifest marks the plane `manual` and the
  driver prints the `#` comment instead of a command.

**Pass:** the eight `docker ...` lines are exactly the eight above, in that
order, and `down` is their exact reverse. The trailing `#` comment line is
expected and is not a command.
**Fail:** any order difference; a missing or extra `--env-file`; a missing
`--profile idea-refinery`; a `portal/docker-compose.yml` command line.

---

## T11 - a docker context prefixes the command

*Anchor criterion 6, second half.*

```powershell
python scripts\stack\stack.py --state .stack\t-ctx.json init --planes inference,frontend --context inference=optiplex-1
python scripts\stack\stack.py --state .stack\t-ctx.json up --dry-run
```

```bash
python scripts/stack/stack.py --state .stack/t-ctx.json init --planes inference,frontend --context inference=optiplex-1
python scripts/stack/stack.py --state .stack/t-ctx.json up --dry-run
```

**Expected:**

```text
docker compose -f docker-compose.yml --env-file .env up -d
docker --context optiplex-1 compose -f inference/docker-compose.yml --env-file .env up -d
docker compose -f frontend/docker-compose.yml --env-file .env up -d
```

**Pass:** the inference line and **only** the inference line starts
`docker --context optiplex-1 compose`. **Fail:** the prefix missing, misplaced
(it must come before `compose`), or applied to a plane with no context.

A malformed pair must be refused:

```bash
python scripts/stack/stack.py --state .stack/t-bad.json init --context inference; echo "exit=$?"
```

**Pass:** exit 1 and a message naming `plane=name`.

---

## T12 - `status` is read-only and needs no lease

*Anchor criterion 7, first half. This case touches the LIVE stack; it is
deliberately `ps`-only.*

```powershell
docker ps -a --format "{{.Names}} {{.Status}}" | Sort-Object | Out-File -Encoding ascii .stack\before.txt
python scripts\stack\stack.py status
"exit=$LASTEXITCODE"
docker ps -a --format "{{.Names}} {{.Status}}" | Sort-Object | Out-File -Encoding ascii .stack\after.txt
Compare-Object (Get-Content .stack\before.txt) (Get-Content .stack\after.txt)
```

```bash
docker ps -a --format '{{.Names}} {{.Status}}' | sort > .stack/before.txt
python scripts/stack/stack.py status; echo "exit=$?"
docker ps -a --format '{{.Names}} {{.Status}}' | sort > .stack/after.txt
diff .stack/before.txt .stack/after.txt && echo "unchanged"
```

With no state file (frontend enabled by default) the expected output is:

```text
== frontend
docker compose -f frontend/docker-compose.yml --env-file .env ps
NAME               IMAGE             ...
openwebui          openwebui:local   ... Up ... (healthy)  127.0.0.1:3000->8080/tcp
openwebui-backup   alpine:3.21       ...
tailscale          tailscale:local   ... Up ... (healthy)
tailscale-backup   alpine:3.21       ...
```

(the exact uptimes will differ).

**Pass:** the only docker subcommand issued is `ps` (it is echoed above its
output); container names and statuses are identical before and after; no
`up`/`down`/`restart`/`recreate` anywhere; no lease was taken.
**Fail:** any container created, removed, restarted or with a changed uptime; a
`--force-recreate`; any docker verb other than `ps` in the echoed command.

`grep -n '"ps"' scripts/stack/stack.py` should show `cmd_status` as the only
place `ps` is built, and `test_status_only_ever_runs_ps` in T2 pins it.

---

## T13 - `init` writes a state file and refuses to overwrite one without `--force`

*Anchor criterion 7, second half.*

```powershell
python scripts\stack\stack.py --state .stack\t-init.json init --planes frontend
Get-Content .stack\t-init.json
python scripts\stack\stack.py --state .stack\t-init.json init --planes inference,frontend
"exit=$LASTEXITCODE"
Get-Content .stack\t-init.json          # unchanged
python scripts\stack\stack.py --state .stack\t-init.json init --planes inference,frontend --force
Get-Content .stack\t-init.json          # now both
```

```bash
python scripts/stack/stack.py --state .stack/t-init.json init --planes frontend
cat .stack/t-init.json
python scripts/stack/stack.py --state .stack/t-init.json init --planes inference,frontend; echo "exit=$?"
cat .stack/t-init.json
python scripts/stack/stack.py --state .stack/t-init.json init --planes inference,frontend --force
cat .stack/t-init.json
```

**Expected refusal:**

```text
refused: .stack/t-init.json already exists (re-run with --force to overwrite it)
exit=1
```

**Pass:** the first `init` writes `{"version": 1, "planes": {"frontend": ...}}`;
the second exits 1, names `--force`, and leaves the file **byte-identical**; the
third rewrites it with both planes.
**Fail:** an overwrite without `--force`; a partially-written file after the
refusal; an exit 0 on the refusal.

`init --product` must run the same checks `enable` does:

```bash
python scripts/stack/stack.py --root .stack/scratch --state .stack/scratch/t.json init --product research; echo "exit=$?"
```

**Pass:** exit 1, with one line per key the scratch root cannot satisfy, each
naming the key, whether it is `blank` or `missing`, the env file it looked in
and which plane reads it - `MULLVAD_WG_PRIVATE_KEY is blank in .env (read by
plane search)` among them, alongside the inference, frontend and ob1 keys the
scratch root has no values for - and **no** state file written.

---

## T14 - ruff, hooks, and the README

*Anchor criterion 8.*

```powershell
ruff check scripts/stack
ruff check .
```

**Pass (this item's files):** `ruff check scripts/stack` prints
`All checks passed!`.

**`ruff check .` - read this before judging.** It reports **one** error, and it
is **pre-existing and unrelated**:

```text
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9
```

Prove it predates this branch without touching the tree:

```bash
git show b28cbc5:llm-queue/src/llm_queue/__init__.py | sed -n 9p | awk '{print length}'   # 103
git -C . log -1 --format='%h %s' -- llm-queue/src/llm_queue/__init__.py
git status --short -- llm-queue          # empty: this item did not touch it
```

It is a docstring path the plan-store migration lengthened past that
subproject's own `line-length = 100` (`llm-queue/pyproject.toml:40-46`), and it
fails the CI `ruff` job (`.github/workflows/ci.yml:47-54`) on `development`
itself. Written up as finding **F1**; deliberately not fixed here (another
module's file). **Fail** only if `ruff check .` reports anything **other** than
that one E501, or if `ruff check scripts/stack` is not clean.

**Hooks** - they ran at commit time (no `--no-verify`); confirm:

```bash
git -C . log -1 --format='%H %s'
git -C . config core.hooksPath          # .githooks
```

Re-run them over the commit if you want independent proof:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\validate-lineendings.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\check-llm-gateway-routing.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\check-doc-placement.ps1
```

**Pass:** each exits 0.

**README** (`scripts/stack/README.md`) - check it documents, and that each claim
is true of the code:

| Must document | Where |
|---|---|
| every manifest plane key | *Plane keys* table (`compose`, `env_file`, `lease`, `requires`, `optional`, `implicit`, `manual`, `host`, `keys`, `ports`, `profiles` with `description`/`default`/`pending`) |
| every product key | *Product keys* table |
| the state file | *The state file* section, with the JSON shape and `context` |
| every verb with its refusal cases | *Verbs, with their refusal cases* - `list`, `status`, `up`, `down`, `restart`, `enable`, `disable`, `doctor`, `init` |
| examples | *Quick start* |

**Fail:** a verb with a refusal the README does not mention; a documented
refusal the code does not implement (check `restart all`, `restart portal`,
`disable inference` while memory is on, `init` without `--force`); a manifest
key used by the code and absent from the table.

---

## T15 - the extra verbs behave as documented

*Not a separate anchor criterion; it covers the refusals the README promises so
"documents every verb with its refusal cases" is verified, not asserted.*

```bash
python scripts/stack/stack.py restart all;                      echo "exit=$?"   # refuses, names emergency-recovery.ps1
python scripts/stack/stack.py restart portal;                   echo "exit=$?"   # refuses, names portal-on.ps1
python scripts/stack/stack.py restart frontend --dry-run;       echo "exit=$?"   # one line, ends in `restart`
python scripts/stack/stack.py enable nope;                      echo "exit=$?"   # refuses, lists planes and products
python scripts/stack/stack.py --state .stack/t-dis.json init --planes inference,frontend,memory >/dev/null
python scripts/stack/stack.py --state .stack/t-dis.json disable inference; echo "exit=$?"  # refuses, names memory
python scripts/stack/stack.py doctor;                           echo "exit=$?"   # report, exit 0 on this host
```

**Pass:** `restart all` exits 1 naming `emergency-recovery.ps1`; `restart portal`
exits 1 naming `portal-on.ps1`; `restart frontend --dry-run` prints exactly
`docker compose -f frontend/docker-compose.yml --env-file .env restart` and
starts nothing; `enable nope` exits 1 and lists the nine planes and ten
products; `disable inference` exits 1 naming `memory`; `doctor` reports docker,
compose, python, the manifest, and per enabled plane its compose file, env file,
blank keys and host requirements.
**Fail:** any of these silently succeeding, or a refusal that does not name its
cause.

---

## T16 - the repository is left clean

```powershell
Remove-Item -Recurse -Force .stack
git status --short
```

```bash
rm -rf .stack
git -C . status --short
```

**Pass:** `git status --short` is empty (every scratch artifact lived under the
gitignored `.stack/`, and the real `.env` files were never edited).
**Fail:** any modified tracked file; any untracked leftover; any change to
`.env`, `OB1/docker/.env` or `agent-org/docker/.env`
(`git diff --stat` on them must be empty, and their mtimes unchanged).

---

## Out of scope for this item (do not fail it for these)

- Health probes, `stats`, the generated services inventory, and the `stack.ps1`
  shim - `sl-driver-parity`.
- `stack.ps1`, `emergency-recovery.ps1` and the watchdog are **unmodified** by
  design; confirm with `git diff b28cbc5 --stat` that none appears.
- Compose profiles marked `pending` in the manifest do not exist in the compose
  files yet (`sl-frontend-solo`, `sl-inference-split`, `sl-ob1-profiles`).
- Per-plane `.env` files - `sl-env-split`. This item encodes today's single root
  `.env`.
- Everything in `documentation/notes/stack-layers-sl-manifest-findings.md` is a
  finding about *other* files, recorded rather than fixed, per CLAUDE.md.
