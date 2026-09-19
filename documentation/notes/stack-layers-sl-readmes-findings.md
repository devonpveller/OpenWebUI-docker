# stack-layers `sl-readmes` - findings

**Item:** `sl-readmes` (stack-layers wave 4). **Developer:** `wt-sl-readmes`,
branch `work/sl-readmes`, based on `development` at `f291cb3`.
**Written:** 2026-09-19.

This item is DOCUMENTATION ONLY. Its anchor puts "any compose, script, check or
manifest edit" out of scope, so everything true-but-out-of-scope that the
verification pass turned up is here instead. Nothing below was changed.

**Every entry is labelled with how it is known**, because a findings file mixes
three strengths of claim and they are not equal:

- **[source]** - read out of the named file. The file is cited by CONSTRUCT
  (a grep that finds it) rather than by line number, because line numbers go
  stale on the next edit, including this item's.
- **[measured]** - a command was run on 2026-09-19 and this is what it printed.
  It has not been re-run since.
- **[not verifiable here]** - stated so nobody mistakes it for checked.

---

## 1. Script and check discrepancies (the ones the anchor named in advance)

### 1.1 `stack-watchdog.ps1` still describes `--env-file` as the profile source

**[source]** `scripts/checks/stack-watchdog.ps1`, in the comment block above the
tailscale-deployment guard (`grep -n "from --env-file" scripts/checks/stack-watchdog.ps1`):

> That is compose's own answer after applying COMPOSE_PROFILES
> from --env-file, from the process environment, and its own precedence rules

`--env-file` is retired (`sl-env-split`, 2026-09-19): compose loads
`frontend/.env` natively from the project directory and nothing passes the flag.
**The CODE is correct** - it renders the project and reads compose's answer,
which is exactly the right source and is unaffected by where the value comes
from. Only the comment names a retired mechanism. Two lines further down the
same block already says "COMPOSE_PROFILES missing from frontend/.env", so the
file disagrees with itself by three lines.

*Fix:* one comment word. *Why not here:* script edit, out of scope.

### 1.2 `check-project-configs.ps1` has no render target for agent-org - or for the portal

**[source]** `scripts/checks/check-project-configs.ps1`. The file has TWO lists
and they are not the same list:

- the compose-VALIDATION list (`$projects`) covers anchor, inference,
  inference+local, frontend, frontend+gpu+tailscale, memory, search, coder and
  **portal (with `--profile internet`)**;
- the inventory-COVERAGE list (`$renderTargets`) covers inference+local,
  frontend+profiles, memory, search, coder, and open-brain where its env exists.
  **Neither agent-org nor portal is in it.**

Its own comment names the agent-org half ("agent-org has never had a render
target either - same treatment") and the check prints an unmissable line for a
project with inventory rows and no render target. **The portal half is
different and is not called out anywhere**: the portal has no rows in
`scripts/lib/stack-services.json` AT ALL and is not in its `projects` map
**[measured:** `python -c` over the generated file, 2026-09-19: `portal rows: []`,
`projects: ai-stack frontend inference memory search coder open-brain agent-org`**]**.
`inventory --check`'s plane-coverage assertion exempts it because
`[planes.portal]` is `manual`. So the twelve portal containers are outside the
sysadmin inventory entirely, and `portal-status.ps1` is what watches them.

*Fix:* either give the portal inventory rows and a render target, or say in the
manifest that a `manual` plane is deliberately uninventoried. *Why not here:*
check + curated-sidecar edit, out of scope. Recorded in `portal/README.md`'s
"Changing this plane" as a stated property rather than left to be discovered.

### 1.3 `search/gateway/` has no `.dockerignore`

**[source]** `ls -a search/gateway/` lists `.gitignore`, `Dockerfile`,
`README.md`, `pyproject.toml`, `src`, `tests` - no `.dockerignore`. The
frontend plane gained its own at `sl-colo-frontend`. Consequence is build
context size and the risk of shipping a stray local file into the image, not a
live defect.

*Why not here:* new file in a plane's build inputs, out of scope.

### 1.4 `update-stack.bat` and `quick-fixes.bat` issue bare compose commands at the anchor

**[source]** `scripts/recovery/update-stack.bat` does `cd /d "%SCRIPT_DIR%\..\.."`
(the repo root) and then `docker compose logs openwebui`, `docker compose exec
-T openwebui ...`, `docker compose exec -T llama-cpp-upstream ...`,
`docker compose ps llama-cpp-upstream llama-cpp-embed-upstream`,
`docker compose build --no-cache openwebui`, `docker compose run --rm ...
openwebui`, and `findstr "image: ghcr.io/ggml-org/llama.cpp" docker-compose.yml`.
Since Part K the root project is the zero-service anchor, so each of those
addresses a service the project does not contain, and the `findstr` searches a
file whose only content is a `networks:` block (that image line lives in
`inference/compose/upstreams.yml`). `scripts/recovery/quick-fixes.bat` has the
same shape.

**They are not referenced by the recovery path**: `emergency-recovery.ps1`
drives every plane with an explicit `-f <plane>/docker-compose.yml`. The
exposure is a human following the menu.

*Fix:* archive both, or rewrite every line with a `-f`. *Why not here:* script
edit. **Acted on to the extent this item may:** `quick-fixes.bat` was removed
from the root README's recovery quick reference, and both are now listed in the
stack-map's recovery table as "present, but not a recovery path".

### 1.5 Thirteen OB1 variables are documented only in the retired root example

**[source]** Carried from `documentation/notes/stack-layers-sl-env-split-findings.md`,
which records `grep -c "^<NAME>=" OB1/docker/.env.example` returning 0 for all
thirteen and leaves the fix "for a follow-up item INSIDE the submodule". The
root `.env.example` now carries a named list of them so the knowledge is not
lost. Unchanged here: the fix belongs in the OB1 repo, and this item does not
touch the submodule.

---

## 2. Portal: four comments that describe something the code does not do

All **[source]**, all found while writing `portal/README.md` against the code
rather than the comments.

### 2.1 `portal-off.ps1` stops ten of the twelve services

`$portalServices` in `scripts/portal/portal-off.ps1` lists `cloudflared`,
`caddy-backup`, `authelia-backup`, `portal-cron`, `integrity-tripwire`,
`authelia-watcher`, `caddy`, `authelia`, `portal-alerter`, `portal-init`.
**`tunnel-watcher` and `authelia-notif-bridge` are not in it**, and the compose
file defines both. Naming services at all is deliberate and correct (the
script's own header explains that a bare `stop` is project-scoped and
`--profile` does not narrow `stop`/`down`/`rm`); the list is simply two short.
After a `portal-off`, `tunnel-watcher` keeps probing a `cloudflared` that is
gone and the notif bridge keeps tailing Authelia's notifier.

*Fix:* two entries. *Why not here:* script edit. Stated in `portal/README.md`
and in the stack-map so an operator checks `ps` rather than trusting it.

### 2.2 `portal-off.ps1` says it references both compose files; it references one

Its comment reads "Both compose files referenced so the local-test override is
also known (idempotent ...)". `$stopArgs` is
`-p portal -f <root>\portal\docker-compose.yml --profile internet stop`. The
override file is not passed. Harmless (the override only adds a port), but the
comment asserts a property the code does not have.

### 2.3 `portal-on.ps1`'s header describes a `local-test` PROFILE and a filename that moved

Header lines under `-Test` say "Uses compose profile `local-test`" and "Applies
`docker-compose.local-test.override.yml`". Neither is true:
`grep -n "local-test" portal/docker-compose.yml` finds exactly one hit, inside a
comment on `cloudflared`, and **no service anywhere declares a `local-test`
profile**. The file is `portal/local-test.override.yml`. The CODE is right - in
test mode it appends `-f portal\local-test.override.yml` and passes no
`--profile` at all.

### 2.4 `portal/local-test.override.yml`'s own header repeats both

Its first line is `# docker-compose.local-test.override.yml` and its usage
block shows
`docker compose -f docker-compose.yml -f docker-compose.local-test.override.yml --profile local-test up -d`.
Following that verbatim fails on the paths, and the `--profile local-test`
would activate nothing.

*Fix for 2.2-2.4:* comment edits in two files. *Why not here:* the anchor puts
compose and script edits out of scope. `portal/README.md` documents the CODE's
behaviour and says plainly that the comments disagree.

---

## 3. Counts and claims that were true when written

### 3.1 `check-project-configs.ps1` says a bare OB1 render emits 20 of 30

**[source]** the comment above its open-brain render target: "a bare render now
emits 20 of its 30 container_names".
**[measured]** 2026-09-19, against the PINNED gitlink `5005197`:

```
docker compose -f OB1/docker/docker-compose.yml config --services | wc -l          -> 29
docker compose -f OB1/docker/docker-compose.yml \
  --profile research --profile wiki --profile notebook --profile idea-refinery \
  config --services | wc -l                                                        -> 30
grep -n "profiles:" OB1/docker/docker-compose.yml OB1/docker/docker-compose.scheduled.yml
  -> one hit: docker-compose.scheduled.yml, profiles: ["idea-refinery"]
```

The comment describes the OB1 **branch**, where `research`/`wiki`/`notebook` are
real; the parent's gitlink has not bumped, so today it is 29 and 30. The check
is not weakened by this - it passes all four profiles regardless, and
`stack.py inventory --check` reports the three as `[declared, not rendered]` on
every run. It is the NUMBER in the prose that is ahead of the tree.

*Corrected in this item's own artifacts* (`CLAUDE.md`, the stack-map reference),
*left alone in the check* - script edit.

### 3.2 `frontend/entrypoint.sh` is 321 lines, not 318

**[source]** `wc -l frontend/entrypoint.sh` -> 321. The stack-map's history
paragraph said "entrypoint.sh rewritten to a 318-line route table", which was a
measurement of 2026-08-20. Rather than re-date a number that will go stale
again, the phrase is now "rewritten around a data-driven route table". **A
claim removed, not silently:** the line count is gone from the document; the
route table's SIZE is knowable from the file and its CONTENT (seven routes plus
the root OWUI serve = the eight `proxy http` mappings `stack.py health` counts)
is now stated in `frontend/README.md`.

### 3.3 The old root README said the pre-commit hook runs "10 checks"

**[source]** `grep -n "^# --- [0-9]" .githooks/pre-commit` returns ELEVEN
numbered blocks: 1 secrets, 2 line endings, 2b doc placement, 3 gateway
routing, 3b corpus write contract, 4 project configs, 5 env_file scope, 5b/5c/5d
three OB1 gitlink checks, 6 attestation. Whether that is "10" depends on
whether you count the three gitlink checks as one and whether attestation is a
"check". **A claim removed, not silently:** the new README says "pre-commit
checks (.githooks/pre-commit)" with no number, and `SERVICE-LIFECYCLE.md` now
lists them by name instead, which cannot drift into being wrong by one.

---

## 4. Test-environment hazards, for whoever runs this item's plan next

### 4.1 `docker compose` v5.3.0 has no `--label` flag

**[measured]** `docker compose --help` on 2026-09-19 lists
`--all-resources --ansi --compatibility --dry-run --env-file -f --parallel
--profile --progress --project-directory -p`. There is no `--label`. A harness
instruction to label a test project's containers has to put the label in an
override file's per-service `labels:` instead. This item's test plan does that,
and `docker ps --filter "label=ai-stack.harness.owner=wt-sl-readmes"` then works.

### 4.2 A scratch clone collides with the live stack twice, and both must be overridden

**[measured]** `frontend/docker-compose.yml` declares `name: frontend` and both
openwebui definitions declare `container_name: openwebui`. So from a scratch
clone on THIS host:

- without `-p <something else>`, `docker compose -f frontend/docker-compose.yml
  up -d` writes into the **live `frontend` project**;
- with `-p` but without a `container_name` override, the render is correct but
  `up` fails because the name `openwebui` is taken by the running container.

The second is a loud, safe failure. The first is not. Both are handled by the
override this item's test plan ships.

### 4.3 `stack.py up` would run the ANCHOR, and the anchor's project name comes from the directory

**[measured]** In a scratch clone, `python scripts/stack/stack.py up --dry-run`
prints exactly two lines:

```
docker compose -f docker-compose.yml up -d
docker compose -f frontend/docker-compose.yml up -d
```

The first has no `-p`, and `docker-compose.yml` declares no `name:`, so the
project name is the clone DIRECTORY's name. Cloning into a directory called
`ai-stack` and running that line would address the live anchor project and its
`ai-stack_*` networks. **On a host already running the stack, do not run the
anchor line of the quickstart.** It is not needed for the proof either: the
`stock` profile uses only the project-local `owui-net` and needs no anchor
network (compose does not require an UNUSED external network to exist). The
test plan says this in the case itself rather than in a preamble.

### 4.4 The quickstart rehearsal, as actually run

**[measured]** 2026-09-19, from `D:\qs-sl\qs`, a local clone of
`work/sl-readmes`:

```
cp frontend/.env.example frontend/.env          # ships COMPOSE_PROFILES=stock
python scripts/stack/stack.py init              # -> wrote .stack/state.json; frontend enabled, alone
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml config --services
    -> openwebui-stock, openwebui-backup                (2, and nothing else)
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml up -d
    -> Container qs-openwebui Healthy; qs-openwebui-backup Started
curl -sS -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:13000/health
    -> HTTP 200      body: {"status":true}
docker compose -p wt-sl-readmes-qs ... down -v   -> network, volume and both containers removed
```

The render's networks and volume were `wt-sl-readmes-qs_owui-net` and
`wt-sl-readmes-qs_openwebui-data`; no `ai-stack_*` network was named, created or
joined, and the live frontend was untouched throughout.

The documented refusals were exercised in the same clone:

```
stack.py enable memory
  -> # note: 'memory' names both a plane and a product; acting on the PLANE (...)
  -> refused: memory requires inference, which is not enabled (python scripts/stack/stack.py enable inference)
stack.py enable research     (before any keys)
  -> refused: product research needs these keys before it can be enabled:
       LITELLM_DB_PASSWORD / LITELLM_MASTER_KEY  in inference/.env
       MULLVAD_WG_PRIVATE_KEY / MULLVAD_WG_ADDRESSES in search/.env
       MCP_ACCESS_KEY / POSTGRES_PASSWORD / OPS_GATEWAY_KEY / OPENBRAIN_GATEWAY_KEY in OB1/docker/.env
stack.py enable research     (throwaway keys supplied)
  -> inference, frontend, search, ob1  with ob1 profiles idea-refinery, research, wiki, notebook
  -> up --dry-run then prints anchor, inference, frontend, search, then OB1 with those four profiles
     and NO --profile local on inference
stack.py enable open-brain --headless  -> "# --headless: dropped surface profiles ob1:wiki"
stack.py enable coding-agent --headless -> "# --headless: dropped surface planes frontend"
```

Nothing was started for any of those; `enable` only writes the state file.

---

## 5. Ledger: what was removed from README.md / CLAUDE.md, and why

The anchor requires that no claim be silently dropped. Every statement that
left one of the two rewritten files is here.

| Removed from | Claim | Disposition |
|---|---|---|
| README.md | The ten-row "Topology - compose projects around a network anchor" table | **Moved**, shortened, to the "The layout" section, one row per plane, each linking that plane's README. The per-plane detail it used to carry lives in those READMEs now. |
| README.md | Quickstart loop copying all six `<plane>/.env.example` files | **Moved** to "Add one thing": a fresh clone needs two env files, and `enable` names the rest when you ask for them. The env-split rule itself is kept verbatim in "The layout". |
| README.md | "pre-commit: 10 checks" | **Removed as unreliable** - see 3.3. |
| README.md | `scripts\recovery\quick-fixes.bat` in the recovery quick reference | **Removed as broken** - see 1.4; recorded in the stack-map's recovery table instead. |
| README.md | The `.bat` twin of emergency-recovery "was archived 2026-08-21" | **Moved** to the stack-map's recovery table, where the two surviving `.bat` files are also described. |
| README.md | The backup table's "cron-timed" row listing llm-gateway-backup as a sleep-loop | **Kept and corrected**: llm-gateway-backup's 86400 s sleep is hard-coded in its entrypoint and is NOT a variable, which the row now says. |
| CLAUDE.md | Open Brain "~29 containers" | **Corrected** to 30, with the pinned-gitlink caveat - see 3.1. |
| CLAUDE.md | Driver row listing only status/up/down/restart/health/stats/inventory | **Extended**, not replaced: `list`, `enable`, `disable`, `doctor`, `init` and `--dry-run` were missing, and the shim does not forward three of them. |
| stack-map reference | "entrypoint.sh rewritten to a 318-line route table" | **Count removed** - see 3.2. |
| stack-map reference | Section 1's Volumes block listing plane volumes as the anchor's | **Replaced** by a per-project table; the anchor declares none. The volume-name trap it implied is now stated explicitly. |
| stack-map reference | Portal/Backups "internet, local-test" profile cells | **Corrected to what the compose file says** - ten of twelve portal services carry no profile at all. |
| stack-map reference | "Legacy linear equivalent (PowerShell version preferred)" row | **Replaced** by a row naming the two surviving `.bat` files and why they are not a recovery path. |
| stack-map SKILL.md | Quick map listing `watchtower`, `tor`, `mcpo`, `smolcrawl-pipelines`, `llama-cpp`, `llama-cpp-embed` as containers | **Replaced** by the nine projects, with an explicit "retired, do not re-add" line naming each - the two llama-cpp names because they are network ALIASES on `llm-gateway` now, not containers. |

---

## 6. Left alone on purpose

- **`CLEANUP-PLAN.md`** still contains live-reading `--env-file` sentences
  (`grep -n -- "--env-file" CLEANUP-PLAN.md` -> 5 hits). It is a CLOSED
  historical record of what was planned, its own header says so, and rewriting
  history to match the present would destroy the thing it is for. The anchor's
  `--env-file` criterion covers "a touched README, CLAUDE.md, the stack-map
  reference or SERVICE-LIFECYCLE.md", and this is none of them.
- **`documentation/evidence/*/test-plan.md`** likewise - those are the executed
  plans of past items and must keep saying what was run.
- **`inference/compose/upstreams.yml`**'s header still shows a
  `--env-file inference/.env.example` verification command **[source]**. It
  describes a render done against the EXAMPLE file, which is still the right way
  to render for a check (and is what `check-project-configs.ps1` does), so it is
  not even wrong - but it is the one `--env-file` a reader of the inference plane
  will meet. Compose-file edit, out of scope.
- **`agent-org/README.md`** and the OB1 tree: explicitly out of scope.
