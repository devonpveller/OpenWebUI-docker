# Test plan - sl-inference-split

Anchor: `queue.ps1 -Show -Id sl-inference-split` (canonical copy:
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-inference-split.json`).
Branch `work/sl-inference-split`, worktree `wt-sl-inference-split`, base
`development` @ `b28cbc5`. Plan context: stack-layers `PLAN.md` 2.5-2.6 and
`DECISIONS.md` D11.

**You are testing four claims:**

1. **Nothing moved except which file a definition lives in.** With the `local`
   profile on, the rendered project is the same project it was on `development`:
   same name, same containers, same networks, aliases, healthchecks and bind
   sources. The only differences allowed are the `profiles` keys, the
   `required: false` that profiles force, and the llm-gateway config-assembly
   mechanism enumerated in T1. **Any other difference is a FAIL**, including a
   changed bind source - the `../` -> `../../` rebasing that `include:` requires
   is exactly the kind of thing that goes wrong silently.
2. **With the profile off, the plane is a gateway.** Four services, no upstream,
   no queue. An upstream or the queue in that render is a FAIL.
3. **The gateway never lists a model whose backend is absent.** Blank
   `OPENROUTER_API_KEY` and no `local` profile => `/v1/models` is empty and the
   gateway is still healthy. Key set => the cloud models appear and calling one
   fails at the UPSTREAM (an OpenRouter 401), not at the gateway. Profile on =>
   the five local models appear. A gateway that crashes on a blank key, or lists
   a model with no backend, is a FAIL.
4. **The running plane was not touched.** Same eight container ids before and
   after your whole session.

**NOTHING HERE DEPLOYS.** The inference plane is live and serving the whole
stack. Do not `docker compose up`, `restart`, `rebuild`, `stop` or retag anything
in it, and never attach a test container to an `ai-stack_*` network. Everything
below is either a file render (no daemon writes) or a throwaway on its own
labelled private network. **No plane lease is needed**: T1/T2/T6/T7/T8/T9 touch
no container at all, T3-T5 create only labelled throwaways, and T10 is a
read-only `docker ps`.

**Do not use `.env`.** Every render below uses `--env-file .env.example`, whose
values are documented placeholders. A case that prints a real credential is
itself a FAIL; if you see anything other than
`change-me-to-a-long-random-string` where a password belongs, stop and say so.

**A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
`queue.ps1 -PlanInadequate` naming the case. Never write `PASS (scoped)`,
`SKIPPED`, or a pass on partial evidence.

## Environment

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-inference-split"

Everything runs from that directory. Bash snippets assume the Git-Bash shell;
PowerShell snippets are marked. Set a scratch directory once:

    # bash
    export SCRATCH="$TEMP/sl-inference-split-test"
    mkdir -p "$SCRATCH"

Tools used: `docker compose version` must print **v2.20 or newer** (the
`required: false` key in `depends_on` needs it). Recorded on the developer's
host: `Docker Compose version v5.3.0`. If yours is older, T1 will fail to render
and that is a real finding, not a plan inadequacy.

---

## T0 - capture the live container ids BEFORE anything else

Run this FIRST, before any other case. It is the baseline for T10.

    docker ps --filter label=com.docker.compose.project=inference --format "{{.ID}} {{.Names}}" | sort | tee "$SCRATCH/ps-before.txt"

**PASS:** eight rows. For reference, the developer's snapshot on 2026-09-19 was

    224cadad7922 llama-cpp-upstream
    56f7055cd8ee llm-gateway-backup
    5fbe0fb2fd35 llm-queue
    d40759c3fb0a llama-cpp-embed-upstream
    d5b1fc0d6104 llm-gateway-db
    d83f8e121ecd llm-gateway
    ede14ea67ddd llm-gateway-ui
    f7b83f4699a7 lm-models-backup

The ids will differ on your run if the operator has recreated anything since;
what matters is that T10 shows the SAME ids you capture here.

**FAIL:** fewer than eight rows (the plane is already degraded - stop and report
rather than testing against a broken baseline).

---

## T1 - the profiled render equals development's, modulo three enumerated deltas

*Anchor criterion 1.*

`include:` resolves an included file's relative paths against THAT file's
directory, so every bind and build context inside `inference/compose/` had to be
rewritten from `../` to `../../`. This case is the proof that the rewrite landed
on the same absolute paths.

### Capture the before-render

`git show` alone is not enough: the file must be rendered from
`inference/`, or its relative paths resolve somewhere else. Write the
`development` version beside the new one, render it, then delete it.

    git show development:inference/docker-compose.yml > inference/docker-compose.development.yml
    docker compose -f inference/docker-compose.development.yml --env-file .env.example --profile local config --format json > "$SCRATCH/dev.json"
    docker compose -f inference/docker-compose.yml             --env-file .env.example --profile local config --format json > "$SCRATCH/new.json"
    rm inference/docker-compose.development.yml
    git status --short inference/    # must NOT list docker-compose.development.yml

### Normalize and diff

`docker compose config --format json` emits keys in an arbitrary order, so
compare the canonical form, not the raw bytes:

    python - <<'PY'
    import json, difflib, os
    S = os.environ["SCRATCH"]
    a = json.dumps(json.load(open(S + "/dev.json")), indent=1, sort_keys=True).split("\n")
    b = json.dumps(json.load(open(S + "/new.json")), indent=1, sort_keys=True).split("\n")
    d = [l for l in difflib.unified_diff(a, b, "development", "split", n=0, lineterm="")
         if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    print("\n".join(d))
    print("CHANGED LINES:", len(d))
    PY

### What the diff is allowed to contain

Exactly these three groups, and nothing else. The developer's run produced
**63 changed lines**; a different count is not automatically a fail, but every
line must fall in one of these groups.

| # | Delta | Where | Why it is allowed |
|---|---|---|---|
| 1 | `"profiles": ["local"]` added | `llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue`, `lm-models-backup` | the item's whole point (D11) |
| 2 | `"required": false` on three `depends_on` entries | `llm-gateway` -> the two upstreams and `llm-queue` | compose REFUSES to render a hard dependency on a profile-gated service; without it criterion 2 cannot even be attempted |
| 3 | the config-assembly mechanism, on `llm-gateway` only | `entrypoint` set to `["/bin/sh","-c","python /app/assemble-config.py && exec docker/prod_entrypoint.sh \"$$@\"","--"]`; `environment` gains `COMPOSE_PROFILES: ""` and `OPENROUTER_API_KEY: ""`; the `config/litellm.config.yaml` bind target changes `/app/config.yaml` -> `/app/config.base.yaml`; two binds added (`config/litellm/model_list` -> `/app/conf.d`, `config/litellm/assemble-config.py` -> `/app/assemble-config.py`) | D11 asks for conditional model registration and LiteLLM YAML has no conditionals; see T3-T5 |

**Note on `$$@`:** compose's `config` output re-escapes `$` as `$$`. The shell in
the container receives `"$@"`, i.e. the `command:` arguments. If you want to
confirm that rather than take it on trust, the one-liner is in T1a.

**FAIL** if the diff contains anything else. In particular these would each be a
FAIL on their own:

- any change to a `source` or `target` under `volumes` other than group 3 (a
  mis-rebased `../`);
- any change to `networks`, `aliases`, `healthcheck`, `deploy`, `ports`,
  `container_name`, `image` or `command`;
- `"name"` no longer `inference`;
- a service appearing or disappearing.

### T1a (optional, 2 minutes) - prove the `$$@` escaping rather than trusting it

    mkdir -p "$SCRATCH/argtest" && cd "$SCRATCH/argtest"
    cat > docker-compose.yml <<'EOF'
    name: wt-sl-inference-split-argtest
    services:
      argtest:
        image: alpine:3.21
        labels:
          - "ai-stack.harness.owner=wt-sl-inference-split"
        entrypoint: ["/bin/sh", "-c", "echo ARGS-SEEN: \"$$@\"", "--"]
        command: ["--config", "/app/config.yaml", "--port", "8080"]
    EOF
    MSYS_NO_PATHCONV=1 docker compose run --rm argtest
    docker compose down --remove-orphans
    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-inference-split"

**PASS:** prints `ARGS-SEEN: --config /app/config.yaml --port 8080`.
**FAIL:** prints a literal `$$@` or `$@`, or an empty argument list - that would
mean the real gateway would start LiteLLM with no `--config` and no `--port`.

---

## T2 - without the profile the project is a gateway, and only a gateway

*Anchor criterion 2.*

    docker compose -f inference/docker-compose.yml --env-file .env.example config --services

**PASS:** exactly these four, in any order:

    llm-gateway
    llm-gateway-db
    llm-gateway-ui
    llm-gateway-backup

**FAIL:** `llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue` or
`lm-models-backup` present; a render error; fewer than four.

Then confirm the profile actually switches them ON:

    docker compose -f inference/docker-compose.yml --env-file .env.example --profile local config --services

**PASS:** all eight. **FAIL:** anything else.

And confirm the documented `.env`-driven route works the same as the flag (this
is what the operator will actually use, per the compose header and
`.env.example`):

    printf 'COMPOSE_PROFILES=local\n' > "$SCRATCH/profiles.env"
    docker compose -f inference/docker-compose.yml --env-file .env.example --env-file "$SCRATCH/profiles.env" config --services

**PASS:** all eight. **FAIL:** four - that would mean the operator instruction in
`.env.example` and the compose header is wrong, which is worse than a code bug
because the plane would come up gutted and healthy-looking.

---

## T3 - a labelled throwaway gateway with NO local profile and a BLANK key

*Anchor criterion 3, first half.* This is the cloud-only gateway, stood up for
real.

Everything you create here carries
`--label ai-stack.harness.owner=wt-sl-inference-split`, uses a name no production
container uses, and lives on its own private network. **Never** `--network
ai-stack_llm-net` or any other `ai-stack_*` network.

### Stand it up

    # bash. MSYS_NO_PATHCONV stops Git-Bash rewriting /bin/sh and /app/... paths.
    export MSYS_NO_PATHCONV=1
    WT="D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-inference-split"
    IMG='ghcr.io/berriai/litellm@sha256:c98c9395c56a35b7abacff8269d43ff99aabacb62bbf42a04cc1514fcb9bde4a'

    docker network create --label ai-stack.harness.owner=wt-sl-inference-split wt-sl-inference-split-net

    docker run -d --label ai-stack.harness.owner=wt-sl-inference-split \
      --name wt-sl-inference-split-db --network wt-sl-inference-split-net \
      -e POSTGRES_DB=litellm -e POSTGRES_USER=litellm \
      -e POSTGRES_PASSWORD=change-me-to-a-long-random-string \
      postgres:16-alpine

    docker run -d --label ai-stack.harness.owner=wt-sl-inference-split \
      --name wt-sl-inference-split-gw --network wt-sl-inference-split-net \
      -p 127.0.0.1:4900:8080 \
      -v "$WT/config/litellm.config.yaml:/app/config.base.yaml:ro" \
      -v "$WT/config/litellm/model_list:/app/conf.d:ro" \
      -v "$WT/config/litellm/assemble-config.py:/app/assemble-config.py:ro" \
      -v "$WT/config/litellm/custom_callbacks.py:/app/custom_callbacks.py:ro" \
      -e DATABASE_URL=postgres://litellm:change-me-to-a-long-random-string@wt-sl-inference-split-db:5432/litellm \
      -e LITELLM_MASTER_KEY=sk-wt-scratch-master \
      -e LITELLM_LOCAL_MODEL_COST_MAP=True \
      -e COMPOSE_PROFILES= \
      -e OPENROUTER_API_KEY= \
      --entrypoint /bin/sh "$IMG" \
      -c 'python /app/assemble-config.py && exec docker/prod_entrypoint.sh "$@"' -- \
      --config /app/config.yaml --port 8080

The image digest, the four mounts and the entrypoint/command are taken verbatim
from `inference/compose/gateway.yml`; the only differences are the container
names, the private network, the published port and the scratch credentials.
**Cross-check them against that file before you accept this case** - a plan that
tests a different gateway than the one shipped proves nothing.

### Wait for it, then check

    for i in $(seq 1 40); do
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:4900/health/liveliness)
      [ "$code" = "200" ] && { echo "liveliness 200 after $i polls"; break; }
      sleep 3
    done
    docker logs wt-sl-inference-split-gw 2>&1 | head -10
    curl -s -w '\nHTTP %{http_code}\n' --max-time 5 http://127.0.0.1:4900/health/liveliness
    curl -s -w '\nHTTP %{http_code}\n' --max-time 5 -H "Authorization: Bearer sk-wt-scratch-master" http://127.0.0.1:4900/v1/models

First boot runs prisma migrations, so allow ~60-90s.

**PASS** requires all three:

1. the assembler log shows the reasoning, e.g.

       [assemble-config] COMPOSE_PROFILES=(none)
       [assemble-config] DROP cloud-large (cloud.openrouter.yaml) - env not set: OPENROUTER_API_KEY
       [assemble-config] DROP cloud-small (cloud.openrouter.yaml) - env not set: OPENROUTER_API_KEY
       [assemble-config] LOAD cloud.openrouter.yaml - 0 model(s) registered
       [assemble-config] SKIP local.yaml - needs compose profile 'local', active: (none)
       [assemble-config] wrote /app/config.yaml with 0 model(s): (none)

2. `/health/liveliness` -> `"I'm alive!"` with `HTTP 200`;
3. `/v1/models` -> `{"data":[],"object":"list"}` with `HTTP 200`.

**FAIL:** the container exits or restarts; liveliness never reaches 200 within
~2 minutes; `/v1/models` lists ANY model.

Leave the network and the db running - T4 and T5 reuse them.

---

## T4 - the same gateway with a dummy OpenRouter key

*Anchor criterion 3, second half.*

    docker rm -f wt-sl-inference-split-gw

then re-run the `docker run` from T3 with one line changed:

    -e OPENROUTER_API_KEY=sk-or-v1-dummy-not-a-real-key \

Wait for liveliness the same way, then:

    curl -s --max-time 5 -H "Authorization: Bearer sk-wt-scratch-master" http://127.0.0.1:4900/v1/models
    curl -s -w '\nHTTP %{http_code}\n' --max-time 60 -X POST http://127.0.0.1:4900/v1/chat/completions \
      -H "Authorization: Bearer sk-wt-scratch-master" -H 'Content-Type: application/json' \
      -d '{"model":"cloud-large","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'
    docker ps --filter name=wt-sl-inference-split-gw --format '{{.Names}} {{.Status}}'
    curl -s -o /dev/null -w 'liveliness HTTP %{http_code}\n' --max-time 5 http://127.0.0.1:4900/health/liveliness

**PASS** requires all three:

1. `/v1/models` lists `cloud-large` and `cloud-small`, and NO local model;
2. the chat call returns an **upstream** authentication error, i.e. the error
   body names OpenRouter. The developer's run:

       {"error":{"message":"litellm.AuthenticationError: AuthenticationError: OpenrouterException - {\"error\":{\"message\":\"User not found.\",\"code\":401}}. Received Model Group=cloud-large ...","code":"401"}}
       HTTP 401

3. the container is still `Up` and liveliness is still 200 afterwards.

**FAIL:** a 500 or a stack trace from LiteLLM itself; the container dying; a
local model in the list; a response that succeeded (that would mean the key is
not a dummy - stop and report).

**Known and expected:** this case reaches openrouter.ai from the throwaway's
network. The real `llm-gateway` cannot - both of its networks are
`internal: true`. That is finding F1 in
`documentation/notes/stack-layers-sl-inference-split-findings.md` and it is not a
fail of this case; the case proves the LISTING rule, which is what D11 specifies.

---

## T5 - the same gateway with the local profile on

*Anchor criterion 3, third behaviour.*

    docker rm -f wt-sl-inference-split-gw

re-run the T3 `docker run` with:

    -e COMPOSE_PROFILES=local \
    -e OPENROUTER_API_KEY= \

Wait for liveliness, then:

    docker logs wt-sl-inference-split-gw 2>&1 | head -10
    curl -s --max-time 5 -H "Authorization: Bearer sk-wt-scratch-master" http://127.0.0.1:4900/v1/models

**PASS:** the list is exactly the five model ids that were in
`config/litellm.config.yaml` on `development`, and no cloud model:

    qwen36-27b, qwen36-27b:nothink, bge-m3, bge-m3-f16.gguf, qllama/bge-m3:latest

Cross-check that set against `git show development:config/litellm.config.yaml`
- if a model id changed in the move, that is a FAIL, because every caller in the
stack sends one of those strings.

Also confirm the settings survived the assembly (the assembler rewrites the
config, so this is where a dropped `general_settings` would show):

    docker exec wt-sl-inference-split-gw cat /app/config.yaml | head -25

**PASS:** `master_key: os.environ/LITELLM_MASTER_KEY`, `store_model_in_db: true`,
`success_callback`/`failure_callback` `[postgres]`, `background_health_checks:
false`, the three `pass_through_endpoints` to `llm-queue`, and under
`litellm_settings` `callbacks: custom_callbacks.proxy_handler_instance`,
`drop_params: true`, `telemetry: false`, `request_timeout: 600`,
`num_retries: 3`. **FAIL:** any of those missing or changed - the J.1 virtual-key
setup and the B2 retry behaviour both depend on them.

**FAIL** also if `x-requires-profile` appears in the written config (it is an
assembler directive, not LiteLLM config).

### Cleanup (run it even if a case failed)

    docker rm -f wt-sl-inference-split-gw wt-sl-inference-split-db
    docker network rm wt-sl-inference-split-net
    unset MSYS_NO_PATHCONV

Then prove nothing is left behind:

    docker ps -a --filter label=ai-stack.harness.owner=wt-sl-inference-split
    docker network ls --filter label=ai-stack.harness.owner=wt-sl-inference-split
    docker volume ls --filter label=ai-stack.harness.owner=wt-sl-inference-split

**PASS:** all three list nothing. No named volumes were created by this plan (the
throwaway postgres uses an anonymous volume, removed with `docker rm -f`); if
`docker volume ls -f dangling=true` grew, remove only volumes you can tie to
these two containers, never `docker volume prune`.

---

## T6 - the gateway-routing guard is green

*Anchor criterion 4.*

    # PowerShell
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-llm-gateway-routing.ps1

**PASS:** `[check-llm-gateway-routing] OK - no LLM gateway bypasses found.`,
exit 0.

**FAIL:** any violation. Read it carefully if it fires: the guard allows
`*\config\litellm.config.yaml` by path, and the model lists now live in
`config/litellm/model_list/`, which is NOT on that allow-list. That is correct
and deliberate - those fragments point at `llm-queue`, not at an upstream, so
they have nothing to allow. A violation there would mean a fragment was written
pointing at `llama-cpp-upstream`, which is the exact bypass the guard exists for.

---

## T7 - no user home path left in the plane; `.env.example` documents the variable

*Anchor criterion 5.*

    grep -rn "Users" inference/          ; echo "exit=$?"
    grep -rn "lmstudio" inference/       ; echo "exit=$?"
    grep -n "LM_MODELS_DIR" .env.example

**PASS:**

- the first grep matches **nothing** (exit 1);
- `.env.example` carries `LM_MODELS_DIR=C:\Users\yamao\.lmstudio\models` as the
  documented example value, with the surrounding comment explaining the `:-`
  default and that the path should be absolute;
- the second grep matches only three lines, all of which are container-internal
  or historical: `inference/compose/upstreams.yml:66,75`
  (`/models/lmstudio-community/...`, the publisher's Hugging Face namespace
  inside the mounted store) and `inference/compose/backups.yml:39` (the
  pre-existing "the .lmstudio path is historical" comment).

**FAIL:** any host path (`C:\Users\...`) inside `inference/`; a fourth
`lmstudio` hit; `LM_MODELS_DIR` missing from `.env.example`.

This split reading is deliberate and is written up as finding F10 - judge it,
do not just run the regex. If you think the anchor meant the literal regex, fail
the case and say so; the developer flagged the ambiguity rather than renaming
the operator's model directories to make a grep green.

---

## T8 - the pre-commit hooks and the inventory

*Anchor criterion 6.*

The developer committed with hooks enabled (no `--no-verify`); reproduce the two
that matter for this diff. `check-project-configs.ps1` only acts on STAGED files,
so stage the yml files in a throwaway way and reset:

    # PowerShell, from the worktree
    git add inference/docker-compose.yml inference/compose config/litellm scripts/lib/stack-services.json
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
    git reset

**PASS:** `[configs] all 7 compose projects render clean` and
`[configs] stack-services.json inventory matches the compose configs`, exit 0.

Then check the inventory by eye:

    python -c "import json;inv=json.load(open('scripts/lib/stack-services.json'));print([(r['container'],r.get('profile'),r.get('critical')) for p in inv['planes'].values() for r in p if r.get('project')=='inference'])"

**PASS:** eight inference rows; the four profiled ones carry `"profile":
"local"`; nothing was deleted. The key is the same one `agent-org` rows already
use for `workers`/`cloud`.

**FAIL:** a row removed; a profiled row without the key; the drift verifier
reporting MISSING or WRONG.

**Known limitation, do not fail the case for it:** the drift verifier renders
inference WITHOUT the profile, so it can no longer see those four containers at
all. It reports only MISSING and WRONG rows, never EXTRA ones, so it stays green
- but its coverage of the four is now zero. That is finding F6.

---

## T9 - the header says what the profile owns and how the operator runs it

*Anchor criterion 6, second half.*

Read `inference/docker-compose.yml` (it is now ~60 lines of header plus the
include, volumes and networks) and confirm it states, in the file itself:

1. which four services the `local` profile owns - and that the list matches the
   `profiles: [local]` keys actually present in `inference/compose/*.yml`
   (`grep -n "profiles:" inference/compose/*.yml`);
2. that **the operator's deployment runs with the profile ON**;
3. that it must be set as `COMPOSE_PROFILES=local` in `.env` rather than
   `--profile local`, with the reason (the gateway reads the variable to decide
   which model groups to register);
4. the `include:` gotchas a future editor needs: relative paths resolve against
   the included file's directory, and top-level `networks:`/`volumes:` stay in
   the spine file.

**PASS:** all four present and matching reality. **FAIL:** any claim in the
header that the files contradict - for instance a profile list that does not
match the grep.

Also check the same facts reached the stack-map reference:

    grep -n "profile \`local\`" .claude/skills/stack-map/references/workspace-stacks.md

**PASS:** section 1b's blockquote describes the split and the profile, and the
four container rows are marked `**[profile `local`]**`.

---

## T10 - the live plane was not touched

*Anchor criterion 7.* Run this LAST.

    docker ps --filter label=com.docker.compose.project=inference --format "{{.ID}} {{.Names}}" | sort > "$SCRATCH/ps-after.txt"
    diff "$SCRATCH/ps-before.txt" "$SCRATCH/ps-after.txt" && echo "IDENTICAL"

**PASS:** `IDENTICAL` - the same eight container ids as T0, in the same order.

**FAIL:** any id changed (a container was recreated), a name missing (something
was stopped), or an extra row. A changed id is the serious one: it means
something in this session restarted or rebuilt a production inference container,
which the item forbids.

Also confirm no test container ever joined a production network:

    docker network inspect ai-stack_llm-net --format '{{range .Containers}}{{.Name}} {{end}}'
    docker network inspect inference_llm-backend-net --format '{{range .Containers}}{{.Name}} {{end}}'

**PASS:** no `wt-sl-inference-split-*` name appears in either.

---

## T11 - the split files keep the LF rule the compose file had

`.gitattributes` forces LF on `docker-compose*.yml`. The new group files are not
called that, and `inference/compose/backups.yml` contains a multi-line shell
script under `command:` - a CRLF checkout would hand `sh -c` a literal carriage
return on every line.

    git check-attr text eol -- inference/compose/backups.yml inference/compose/upstreams.yml inference/compose/queue.yml inference/compose/gateway.yml
    git check-attr text eol -- inference/docker-compose.yml

**PASS:** all five report `text: set` and `eol: lf`.

**FAIL:** any `unspecified`. Check `.gitattributes` carries the
`*/compose/*.yml text eol=lf` rule; without it the `lm-models-backup` entrypoint
script breaks on a fresh Windows clone, which is finding F11.

---

## What to report

Per case: PASS / FAIL / plan-inadequate, with the command output pasted. For T1
paste the whole normalized diff, not a summary - the claim is about what is
NOT in it.

Findings worth reading before you judge T4, T7 and T8:
`documentation/notes/stack-layers-sl-inference-split-findings.md` (F1 egress,
F3 the operator's `.env` line, F6 drift coverage, F7 a pre-existing `ruff`
failure on `development`, F10 the `.lmstudio` reading).
