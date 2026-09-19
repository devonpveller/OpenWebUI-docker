# Migrating to per-plane `.env` files

**Status:** written 2026-09-19 by stack-layers `sl-env-split` (PLAN §2.7 / L.2,
DECISIONS D10, D17). **This item DEPLOYED NOTHING.** No container was started,
stopped or recreated, and no `.env` on the operator's host was touched. What
landed is the six `<plane>/.env.example` templates, a reduced root
`.env.example`, and every script, check and guard rewired to the new layout.
Performing the migration is an operator step, and this runbook is it.

**Audience:** the operator, on the deploy host, at a moment when a short
inference/frontend outage is acceptable. Step 0 is what makes this reversible.

---

## What changed, in one paragraph

Every compose project now loads **its own** `.env` from **its own project
directory**. `docker compose -f frontend/docker-compose.yml ...` makes
`frontend/` the project directory, so compose reads `frontend/.env` - with no
`--env-file` flag and regardless of your working directory. `OB1/docker` and
`agent-org/docker` always worked this way; the six in-repo planes now do too.
A variable lives in the file of the plane whose service reads it (D10); a value
two planes read is **declared in each** - the files are independent and compose
cannot reference one from another. The root `.env` keeps only what the anchor,
the driver, or a script that is not plane-scoped reads.

`COMPOSE_PROFILES` follows the same rule (D17). It used to be ONE global
assignment in the root `.env` - `local,gpu,tailscale` on this host - because
every plane was handed the same `--env-file`. It is now per plane:

| file | this host's value | why |
|---|---|---|
| `frontend/.env` | `COMPOSE_PROFILES=gpu,tailscale` | the CUDA build + the netns tailnet node |
| `inference/.env` | `COMPOSE_PROFILES=local` | the GPU backends + `llm-queue` + `lm-models-backup` |
| `portal/.env` | **absent, deliberately** | `internet` is passed on the command line by `portal-on.ps1`; exposing the stack must never be a standing setting |
| `memory/.env`, `search/.env`, `coder/.env` | absent | those planes declare no profiles |
| the root `.env` | absent | the anchor declares no services and no profiles |

**The single most dangerous mistake in this migration** is writing
`local,gpu,tailscale` into one plane file and nothing into the others. Compose
ignores profile names a project does not declare, so `frontend/.env` saying
`local,gpu,tailscale` still works for the frontend - but if `inference/.env`
then says nothing, a bare `up` brings the LiteLLM gateway up **with no
backends**, and a frontend `down` leaves `openwebui` and `tailscale` running.
Split the value; do not copy it.

---

## Order (never leaves the running stack without a value)

Write the plane files FIRST, verify every plane renders, and only then trim the
root. At no point between step 1 and step 5 is any plane missing a value: until
step 5 the root `.env` still holds everything it holds today - nothing reads it
any more, so it is simply redundant.

### 0. Snapshot, so this is reversible

```powershell
cd "D:\Open WebUI\ai-stack"
Copy-Item .env ".env.pre-env-split-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
```

`.gitignore` covers `.env.bak*` and `.env.*.bak` at every level, and the
pre-commit secret guard blocks any dotenv-shaped leaf, so the copy cannot reach
a commit. Keep it until step 6 passes.

### 1. Create each plane file from its example

```powershell
foreach ($p in 'frontend','inference','memory','search','coder','portal') {
  Copy-Item "$p\.env.example" "$p\.env"
}
```

### 2. Copy this host's REAL values into them

For every variable in the table at the end of this runbook, take the value from
the root `.env` and put it in the destination file. The examples ship
placeholders, so a file you forget to fill fails loud on its `:?` guard rather
than starting with an empty credential - except where the table says otherwise.

Do not retype secrets by hand. A safe mechanical pass, per plane:

```powershell
# For ONE plane: overwrite each example key with the root .env's value.
$plane = 'frontend'
$root  = @{}
Get-Content .env | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*\s*=' } | ForEach-Object {
  $k, $v = $_ -split '=', 2
  $root[$k.Trim()] = $v
}
(Get-Content "$plane\.env") | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=' -and $root.ContainsKey($Matches[1])) {
    "$($Matches[1])=$($root[$Matches[1]])"
  } else { $_ }
} | Set-Content "$plane\.env" -Encoding ASCII
```

Then fix the three cases a mechanical copy gets wrong:

1. **`COMPOSE_PROFILES`** - the root value is `local,gpu,tailscale`, and the
   loop above would write all three into every file. Set `frontend/.env` to
   `gpu,tailscale`, `inference/.env` to `local`, and **delete the line** from
   the other four.
2. **`GPU_AISTACK_DEVICE_ID` / `GPU_LLAMA_CPP_DEVICE_ID` /
   `GPU_LLAMA_CPP_EMBED_DEVICE_ID`** - the pre-split `.env.example` assigns each
   of these **twice** (lines 80-82 and 165-167), and a duplicate key is
   last-wins, silently. If your `.env` carries the same duplication, check what
   the containers actually got (`docker inspect openwebui` /
   `llama-cpp-upstream`) before trusting either line.
3. **`LC_LLAMA_API_KEY`** - also assigned twice in the pre-split example
   (lines 220 and 375). Same check. It goes to `coder/.env`, once.

### 3. Verify EVERY plane renders from its own file, with every profile

Read-only. Nothing starts.

```powershell
docker compose -f frontend\docker-compose.yml config -q
docker compose -f frontend\docker-compose.yml --profile gpu --profile tailscale config -q
docker compose -f inference\docker-compose.yml config -q
docker compose -f inference\docker-compose.yml --profile local config -q
docker compose -f memory\docker-compose.yml config -q
docker compose -f search\docker-compose.yml config -q
docker compose -f coder\docker-compose.yml config -q
docker compose -f portal\docker-compose.yml --profile internet config -q
```

Every one must exit 0 **and print no `variable is not set` warning**. A `:?`
refusal names the missing key and the file to put it in.

Then confirm the PROFILE sets are what you meant. This is the check that catches
the `COMPOSE_PROFILES` mistake above, and a clean `config -q` will not:

```powershell
docker compose -f frontend\docker-compose.yml config --services   # openwebui, tailscale + 2 backups
docker compose -f inference\docker-compose.yml config --services  # EIGHT, not four
```

### 4. Let the driver confirm it

```powershell
python scripts\stack\stack.py doctor
```

`doctor` prints, per enabled plane, which env file it found and every declared
key that is blank or missing **in that plane's own file**. It is the
machine-readable form of step 3 and it is the authority here: if `doctor` is
clean, every `keys = [...]` entry in `stack.manifest.toml` is satisfied where
the plane will actually look for it.

```powershell
python scripts\stack\stack.py up --dry-run      # prints commands; starts nothing
```

No printed line may contain `--env-file`.

### 5. Trim the root `.env`

Only now. Delete from the root `.env` every variable the table marks as moved,
leaving `NAS_BACKUP_USER`, `NAS_BACKUP_PASSWORD` and `TEST_VALIDATION_LLM_KEY`.
**The file must still EXIST**: the anchor project's directory is the repo root,
so compose and `stack.py doctor` both look for it there.

Re-run step 3 and step 4 afterwards. A plane that stops rendering here was
reading the root file, and the fix is a missing line in that plane's own file -
not putting it back in the root.

### 6. Re-run the lifecycle surfaces that read env files

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-watchdog-repair-targets.ps1
python scripts\stack\stack.py inventory --check
python scripts\stack\stack.py health
```

`check-watchdog-repair-targets.ps1` renders every watchdog repair target with no
`--env-file` (the inventory's `env_file` is `null` for every project now), so it
is the direct test of "does the watchdog's self-heal still find its services".
A target reported NOT DECLARED here means that plane's `.env` is missing, or
short a profile.

### 7. The restart

Nothing above restarts a container, and a running container keeps the
environment it was started with - so the stack goes on running the OLD values
until you recreate something. That is fine and can wait. When you do:

```powershell
python scripts\stack\stack.py restart <plane>
```

**NETNS RULE unchanged:** never restart `openwebui` alone; the order is
`openwebui` -> (healthy) -> `tailscale`, and the frontend project's `depends_on`
encodes it.

### 8. Worktrees

`scripts/agent-harness/harness.config.json` now lists the six plane files in
`worktree.env_files`. EXISTING worktrees do not have them:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\agent-harness\sync-worktree-env.ps1 -All
```

(That script used to carry its own hardcoded list, three files shorter than the
config's. It reads the config now, so this one command covers all twelve.)

---

## What this migration does NOT change

- **Any variable's value or default.** Every line moved verbatim, comments
  included. Three lines were DELETED rather than moved - `BACKUP_INTERVAL`,
  `RETAIN_COUNT`, `MIN_AGE_SECS` - because they are the CONTAINER-side names:
  every sidecar sets them itself in its own `environment:` block from a prefixed
  host variable (e.g. `- BACKUP_INTERVAL=${MNEMORY_BACKUP_INTERVAL:-86400}`), so
  no compose file ever interpolated the bare names and setting them in an env
  file changed nothing. Identified by `sl-closeout`
  (`documentation/notes/stack-layers-sl-closeout-findings.md` section 3).
- **`OB1/docker/.env` or `agent-org/docker/.env`.** Both already worked this way
  and are out of scope. Note, though, that thirteen OB1 variables were
  *documented* in the root `.env.example` and are read only from
  `OB1/docker/.env` - and `OB1/docker/.env.example` does not carry them. That
  documentation gap is recorded in
  `documentation/notes/stack-layers-sl-env-split-findings.md` and belongs to a
  follow-up item inside the submodule. **Your live `OB1/docker/.env` is
  unaffected; do not "fix" it from this runbook.**
- **Where secrets live.** They are still gitignored dotenv files. The
  `.gitignore` rule `.env` has no leading slash, so it matches at every level
  and already covered `frontend/.env` before this item; the pre-commit secret
  guard matches on the file's LEAF, so it already blocked them too. Both were
  verified rather than assumed, and both now say so in a comment.

---

## Rollback

Before step 5: delete the six `<plane>/.env` files. The root `.env` is untouched
and every plane reads it again the moment the code is reverted.

After step 5: restore the step-0 backup over `.env`, then revert the code (the
plane files are gitignored and can stay - compose only reads them when the code
tells it to).

There is no partial state in which a plane silently runs on the wrong value: a
plane with no env file fails its `:?` guard loudly, and a plane whose file lacks
`COMPOSE_PROFILES` renders visibly fewer services - which is what step 3's
`config --services` line is for.

---

## Every variable, its pre-split line, and where it goes

Line numbers are into the `.env.example` as it stood **before** this item
(`git show <the commit before sl-env-split>:.env.example`, 483 lines, 157
assignments). Your real `.env` does not share those line numbers; match by NAME.

| Variable | line in the pre-split `.env.example` | destination |
|---|---:|---|
| `COMPOSE_PROFILES` | 69 | **SPLIT** - `frontend/.env` (`stock` / `gpu,tailscale`) **and** `inference/.env` (`local`); none in `portal/.env`, none in the root |
| `GPU_LLAMA_CPP_DEVICE_ID` | 80 | `inference/.env` |
| `GPU_LLAMA_CPP_EMBED_DEVICE_ID` | 81 | `inference/.env` |
| `GPU_AISTACK_DEVICE_ID` | 82 | `frontend/.env` |
| `LM_MODELS_DIR` | 108 | `inference/.env` |
| `LLAMA_CPP_CACHE_TYPE_K` | 112 | `inference/.env` |
| `LLAMA_CPP_CACHE_TYPE_V` | 113 | `inference/.env` |
| `LLAMA_CPP_TIMEOUT` | 114 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_MODEL_PATH` | 117 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_CTX_SIZE` | 118 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_N_GPU_LAYERS` | 119 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_N_PARALLEL` | 120 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_BATCH` | 121 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_UBATCH` | 122 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_CACHE_TYPE_K` | 123 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_CACHE_TYPE_V` | 124 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_35B_REASONING_BUDGET` | 125 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_MODEL_PATH` | 128 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_CTX_SIZE` | 129 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_N_GPU_LAYERS` | 130 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_N_PARALLEL` | 131 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_BATCH` | 132 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_UBATCH` | 133 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_CACHE_TYPE_K` | 134 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_CACHE_TYPE_V` | 135 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_REASONING_BUDGET` | 136 | `inference/.env` |
| `MNEMORY_LLM_CONTEXT_SIZE` | 139 | `memory/.env` |
| `TAILSCALE_AUTH_KEY` | 144 | `frontend/.env` |
| `MCP_API_KEY` | 149 | `memory/.env` |
| `MCP_API_KEYS` | 151 | `memory/.env` |
| `BACKUP_RETAIN_COUNT` | 155 | `memory/.env` |
| `OPENWEBUI_BACKUP_RETAIN_COUNT` | 160 | `frontend/.env` |
| `GPU_AISTACK_DEVICE_ID` | 165 | `frontend/.env` |
| `GPU_LLAMA_CPP_DEVICE_ID` | 166 | `inference/.env` |
| `GPU_LLAMA_CPP_EMBED_DEVICE_ID` | 167 | `inference/.env` |
| `GATEWAY_API_KEY` | 174 | `search/.env` |
| `SEARXNG_SECRET_KEY` | 175 | `search/.env` |
| `PROVIDER_PRIORITY` | 178 | `search/.env` - **commented out**, nothing injects it |
| `CACHE_TTL_SECONDS` | 179 | `search/.env` - **commented out**, nothing injects it |
| `REQUEST_TIMEOUT_SECONDS` | 180 | `search/.env` - **commented out**, nothing injects it |
| `CIRCUIT_FAILURE_THRESHOLD` | 181 | `search/.env` - **commented out**, nothing injects it |
| `CIRCUIT_COOLDOWN_SECONDS` | 182 | `search/.env` - **commented out**, nothing injects it |
| `LOG_LEVEL` | 183 | `search/.env` - **commented out**, nothing injects it |
| `LOG_QUERIES` | 184 | `search/.env` - **commented out**, nothing injects it |
| `SEARXNG_IMAGE` | 191 | `search/.env` |
| `SEARCH_REDIS_IMAGE` | 192 | `search/.env` |
| `ENABLE_WEB_SEARCH` | 195 | `frontend/.env` |
| `WEB_SEARCH_ENGINE` | 196 | `frontend/.env` |
| `SEARXNG_QUERY_URL` | 197 | `frontend/.env` |
| `OPEN_TERMINAL_API_KEY` | 203 | `coder/.env` |
| `LITTLE_CODER_VERSION` | 211 | `coder/.env` |
| `LC_ROUTE_EXEC` | 217 | `coder/.env` |
| `LC_LLAMA_API_KEY` | 220 | `coder/.env` |
| `LC_DEPLOY_TOKEN` | 225 | `coder/.env` |
| `LC_BACKUP_RETAIN_COUNT` | 230 | `coder/.env` |
| `LC_SELF_REMOTE_URL` | 238 | `coder/.env` (no reader today) |
| `LC_SELF_REMOTE_PAT` | 239 | `coder/.env` (no reader today) |
| `PUBLIC_DOMAIN` | 246 | `portal/.env` |
| `ACME_EMAIL` | 247 | `portal/.env` |
| `CLOUDFLARE_TUNNEL_TOKEN` | 251 | `portal/.env` |
| `AUTHELIA_JWT_SECRET` | 255 | `portal/.env` |
| `AUTHELIA_SESSION_SECRET` | 256 | `portal/.env` |
| `AUTHELIA_STORAGE_ENCRYPTION_KEY` | 257 | `portal/.env` |
| `DIGEST_TO` | 263 | `portal/.env` |
| `DIGEST_FROM` | 264 | `portal/.env` |
| `DIGEST_WINDOW_HOURS` | 265 | `portal/.env` |
| `ALERT_RATE_LIMIT_PER_MIN` | 269 | `portal/.env` |
| `PORTAL_DIGEST_CRON` | 272 | `portal/.env` |
| `TRIPWIRE_CRON` | 275 | `portal/.env` |
| `TUNNEL_WATCHER_POLL_SEC` | 279 | `portal/.env` |
| `TUNNEL_WATCHER_FAILURES_BEFORE_ALERT` | 280 | `portal/.env` |
| `SURREAL_USER` | 283 | `OB1/docker/.env` - **already there**, never read from the root |
| `SURREAL_PASSWORD` | 284 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPEN_NOTEBOOK_ENCRYPTION_KEY` | 287 | `OB1/docker/.env` - **already there**, never read from the root |
| `CADDY_BACKUP_RETAIN_COUNT` | 290 | `portal/.env` |
| `CADDY_BACKUP_CRON` | 291 | `portal/.env` |
| `AUTHELIA_BACKUP_RETAIN_COUNT` | 292 | `portal/.env` |
| `AUTHELIA_BACKUP_CRON` | 293 | `portal/.env` |
| `POSTGRES_USER` | 312 | `OB1/docker/.env` - **already there**, never read from the root |
| `POSTGRES_PASSWORD` | 313 | `OB1/docker/.env` - **already there**, never read from the root |
| `POSTGRES_DB` | 314 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPENBRAIN_DB_BACKUP_RETAIN_COUNT` | 315 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPENBRAIN_DB_BACKUP_CRON` | 316 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPENBRAIN_WIKI_BACKUP_RETAIN_COUNT` | 319 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPEN_NOTEBOOK_BACKUP_RETAIN_COUNT` | 324 | `OB1/docker/.env` - **already there**, never read from the root |
| `OPEN_NOTEBOOK_BACKUP_CRON` | 325 | `OB1/docker/.env` - **already there**, never read from the root |
| `TAILSCALE_BACKUP_RETAIN_COUNT` | 327 | `frontend/.env` |
| `LM_MODELS_BACKUP_RETAIN_COUNT` | 335 | `inference/.env` |
| `LITELLM_DB_PASSWORD` | 343 | `inference/.env` |
| `LITELLM_BACKUP_RETAIN_DAYS` | 347 | `inference/.env` |
| `OPENROUTER_API_KEY` | 355 | `inference/.env` |
| `LITELLM_MASTER_KEY` | 373 | `inference/.env` |
| `MNEMORY_LLM_API_KEY` | 374 | `memory/.env` |
| `LC_LLAMA_API_KEY` | 375 | `coder/.env` |
| `OPEN_NOTEBOOK_LLM_API_KEY` | 376 | `OB1/docker/.env` - **already there**, never read from the root |
| `WEBUI_SECRET_KEY` | 381 | `frontend/.env` |
| `OWUI_CHAT_LLM_API_KEY` | 384 | `frontend/.env` |
| `MULLVAD_WG_PRIVATE_KEY` | 387 | `search/.env` |
| `MULLVAD_WG_ADDRESSES` | 388 | `search/.env` |
| `LITELLM_UI_MASTER_KEY` | 390 | `inference/.env` |
| `LITELLM_UI_USERNAME` | 391 | `inference/.env` |
| `LITELLM_UI_PASSWORD` | 392 | `inference/.env` |
| `MNEMORY_GATEWAY_KEY` | 394 | `memory/.env` |
| `MNEMORY_CLOUD_USER` | 395 | `memory/.env` |
| `WORKBENCH_KEY` | 397 | `portal/.env` |
| `MULLVAD_COUNTRIES` | 400 | `search/.env` |
| `VPN_IMAGE` | 401 | `search/.env` |
| `SEARCH_NET_SUBNET` | 402 | `search/.env` |
| `ENABLE_CODE_INTERPRETER` | 405 | `frontend/.env` |
| `ENABLE_CODE_EXECUTION` | 406 | `frontend/.env` |
| `LLM_QUEUE_SLOTS` | 409 | `inference/.env` |
| `LLM_QUEUE_MAX_IN_FLIGHT` | 410 | `inference/.env` |
| `LLM_QUEUE_ENFORCE_BUDGET` | 411 | `inference/.env` |
| `LLM_QUEUE_EVENTS_DB_PATH` | 412 | `inference/.env` |
| `LLM_QUEUE_BACKSTOP_DEPTH` | 413 | `inference/.env` |
| `LLM_QUEUE_LOG_LEVEL` | 414 | `inference/.env` |
| `LLM_QUEUE_MAX_TOTAL_CONNECTIONS` | 415 | `inference/.env` |
| `LLM_QUEUE_T_INITIAL_S` | 416 | `inference/.env` |
| `LLM_QUEUE_T_WINDOW` | 417 | `inference/.env` |
| `LLM_QUEUE_UPSTREAM_TIMEOUT_S` | 418 | `inference/.env` |
| `LLAMA_CPP_ENABLED` | 421 | `frontend/.env` |
| `LLAMA_CPP_HOST` | 422 | `frontend/.env` |
| `LLAMA_CPP_PORT` | 423 | `frontend/.env` |
| `LLAMA_CPP_EMBED_ENABLED` | 424 | `frontend/.env` |
| `LLAMA_CPP_EMBED_HOST` | 425 | `frontend/.env` |
| `LLAMA_CPP_EMBED_PORT` | 426 | `frontend/.env` |
| `LLAMA_ARG_CTX_CHECKPOINTS` | 427 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_SPEC_TYPE` | 430 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_N_MAX` | 431 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_CACHE_TYPE_K` | 432 | `inference/.env` |
| `LLAMA_SWAP_QWEN36_27B_SPEC_DRAFT_CACHE_TYPE_V` | 433 | `inference/.env` |
| `OPEN_NOTEBOOK_ENABLED` | 436 | `frontend/.env` |
| `OPEN_NOTEBOOK_HOST` | 437 | `frontend/.env` |
| `OPEN_NOTEBOOK_PORT` | 438 | `frontend/.env` |
| `OPEN_NOTEBOOK_TS_PORT` | 439 | `frontend/.env` |
| `OPEN_NOTEBOOK_API_PORT` | 440 | `frontend/.env` |
| `OPEN_NOTEBOOK_API_TS_PORT` | 441 | `frontend/.env` |
| `QUARTZ_ENABLED` | 442 | `frontend/.env` |
| `QUARTZ_HOST` | 443 | `frontend/.env` |
| `QUARTZ_PORT` | 444 | `frontend/.env` |
| `QUARTZ_TS_PORT` | 445 | `frontend/.env` |
| `BACKUP_INTERVAL` | 448 | **DELETED** - container-side name, nothing interpolates it |
| `RETAIN_COUNT` | 449 | **DELETED** - container-side name, nothing interpolates it |
| `MIN_AGE_SECS` | 450 | **DELETED** - container-side name, nothing interpolates it |
| `TAILSCALE_BACKUP_INTERVAL` | 451 | `frontend/.env` |
| `MNEMORY_BACKUP_INTERVAL` | 452 | `memory/.env` |
| `OPENWEBUI_BACKUP_INTERVAL` | 453 | `frontend/.env` |
| `LITTLE_CODER_BACKUP_INTERVAL` | 454 | `coder/.env` |
| `LM_MODELS_BACKUP_INTERVAL` | 455 | `inference/.env` |
| `LM_MODELS_BACKUP_MIN_AGE_SECS` | 456 | `inference/.env` |
| `LM_MODELS_BACKUP_HEALTH_TCP` | 457 | `inference/.env` |
| `OPENBRAIN_WIKI_BACKUP_INTERVAL` | 458 | `OB1/docker/.env` - **already there**, never read from the root |
| `CADDY_BACKUP_HEALTH_TCP` | 459 | `portal/.env` |
| `AUTHELIA_BACKUP_HEALTH_TCP` | 460 | `portal/.env` |
| `TEST_VALIDATION_LLM_KEY` | 466 | **stays in the root `.env`** (real value in `.env.test`) |
| `NAS_BACKUP_USER` | 482 | **stays in the root `.env`** |
| `NAS_BACKUP_PASSWORD` | 483 | **stays in the root `.env`** |

<!-- 157 assignment lines; generated from `git show <pre-split>:.env.example` -->
