# Integration Tasks — LiteLLM Proxy

**Anchored to:** [`guide-LiteLLM-Proxy.md`](guide-LiteLLM-Proxy.md) (source of
truth) and [`integration-plan-LiteLLM-Proxy.md`](integration-plan-LiteLLM-Proxy.md)
(phased execution model).

**For autonomous-agent execution.** Each task is a single, verifiable unit.
Tasks are marked `[AGENT]` (agent runs autonomously), `[OPERATOR]` (human
action required — agent stops and prompts), or `[GATE]` (named pause point
from the plan). Acceptance criteria are runnable commands or observable
state, not narrative.

**Cross-references** like `Guide §X.Y` point into `guide-LiteLLM-Proxy.md`;
the agent must read the referenced section before executing the task.

**Working directory:** `d:\Open WebUI\ai-stack` (the workspace root).
PowerShell unless otherwise noted; Bash blocks marked explicitly.

---

## How to read this list

Each task has:
- **ID** — e.g. `T1.3` (phase.position)
- **Role** — `[AGENT]` / `[OPERATOR]` / `[GATE]`
- **Depends on** — task IDs that must be complete first
- **Action** — exact commands or file edits
- **Acceptance** — runnable verification

The agent must:
1. Execute tasks in ID order within a phase.
2. Verify the Acceptance step before marking complete.
3. Stop at every `[GATE]` and every `[OPERATOR]` task; print the prompt
   text verbatim; wait for explicit go-ahead.
4. On any unexpected state, stop and surface the discrepancy — do not
   improvise.

---

## Transparent-mode task sequence (2026-06-12) — READ FIRST

> Architecture changed to **transparent interposition** (guide §1A, plan §0).
> The per-caller phases (0.0 → 6) below are **superseded** — they are the
> fallback if **TT0** fails. Execute the TT-tasks here instead. Tasks marked
> `[reuse]` reuse a per-caller task body unchanged.

### TT0 — Permissive-logging pre-check `[AGENT]` **(OPTIONAL — guide §1A.3/§1A.8)**
- **Optional:** the TT3 live canary tests this same fact under real load with an
  invisible rollback, so run TT0 only to confirm the LiteLLM-config behaviour at
  **zero outage** before the canary. Skippable if going straight to the canary.
- **Action:** stand a throwaway LiteLLM container pointed at the live llama-cpp,
  with **no `master_key`**, `database_url` to a scratch Postgres,
  `success_callback: ["postgres"]`. Send two requests carrying **different** key
  strings (e.g. `-H "Authorization: Bearer aaa"` and `... bbb`). Query
  `LiteLLM_SpendLogs`.
- **Acceptance:** two rows, with **distinct `api_key` values** reflecting `aaa`
  vs `bbb`. If it FAILS here (or in the TT3 canary), fall back to the per-caller
  plan (Phase 0.0+ below).
- Also run the §19 digest-resolve check (T1.3.5 body) here.

### TT0.1 — Model-id coverage assertion `[AGENT]` [reuse T0.0.1/T0.0.2]
- Confirm the live model ids (`qwen36-27b`, `…:nothink`, `qwen36-35b-a3b`,
  `…:nothink`, the one embed model) — every id ANY caller sends must be in §6.
- **Acceptance:** §6 `model_list` covers all live ids (mandatory in transparent
  mode — callers can't be fixed).

### GT0 — Spike reviewed `[GATE] [OPERATOR]`
- Operator reviews TT0 result; replies "proceed transparent" or "fall back".

### TT1 — Backups + branch + .env `[AGENT]` [reuse T0.1–T0.10]
- Same backups + branch as Phase 0, **but `.env` gets only `LITELLM_DB_PASSWORD`**
  (no per-caller `LITELLM_KEY_*`; no `master_key` unless enforcing later).
- **GT1 — operator supplies `LITELLM_DB_PASSWORD`.**

### TT2 — Standup, no aliases `[AGENT]`
- Write `config/litellm.config.yaml` from §6 **with the transparent deltas**:
  `master_key` commented out (permissive); api_base still the **current**
  `llama-cpp`/`llama-cpp-embed` (pre-flip). Add `llm-gateway` + `llm-gateway-db`
  (digest-pinned, `--port 8080`) per §8.3; **no aliases yet**.
- Bring up; verify `/v1/models` lists every id; send a request with a junk key
  and confirm a `LiteLLM_SpendLogs` row carrying that key.
- **Acceptance:** gateway healthy; serves all model ids; logs the presented key.
- **GT2 — operator confirms ledger + model coverage.**

### TT3 — THE FLIP (one commit) `[AGENT]` (guide §8.3-revised / §1A.7)
- In one git commit: rename `llama-cpp`→`llama-cpp-upstream` and
  `llama-cpp-embed`→`llama-cpp-embed-upstream` (keep their host ports 8081/8082
  + healthchecks); set the gateway config api_base → `*-upstream:8080`; add
  gateway `networks.llm-net.aliases: [llama-cpp, llama-cpp-embed]`; change
  gateway `depends_on` → `*-upstream`; **repoint every observability ref to
  `*-upstream`**: `modules/system-health`, `modules/gpu-status/service/gpu_status.py`,
  `scripts/status_check.py`, `scripts/gpu_check.py`, tailscale `LLAMA_CPP_HOST`/
  `LLAMA_CPP_EMBED_*` (`entrypoint.sh`), `scripts/emergency-recovery.{ps1,bat}`.
- **GT3 — operator eyeballs the single flip commit before recreate.**
- **Apply as a live canary (maintenance window, guide §1A.8):** free the old
  names, then bring up the renamed upstreams + aliased gateway together:
  ```powershell
  docker compose stop llama-cpp llama-cpp-embed
  docker compose rm -f llama-cpp llama-cpp-embed
  docker compose up -d --remove-orphans
  ```
  Then **watch** (see Acceptance). The upstream MUST come up — never leave the
  gateway with no backend.
- **Acceptance (watch live):** every caller still healthy; `LiteLLM_SpendLogs`
  fills from multiple source IPs/keys (this confirms permissive logging in situ);
  `docker logs llama-cpp-upstream` shows the gateway as its only client (no caller
  bypasses); observability probes resolve to `*-upstream` and pass. Nudge any
  sticky caller holding an old connection pool with a single `docker restart
  <caller>` (no config change).
- **Rollback drill / worst case:** `git revert <flip-commit>; docker compose up -d
  --remove-orphans` → originals reclaim `llama-cpp`/`llama-cpp-embed`, gateway
  gone, **callers never knew**. Confirm all healthy on the originals.

### TT4 — Verify + soak `[AGENT/OPERATOR]`
- Dark-traffic check inverted: confirm **no** caller hits `*-upstream` directly
  (only the gateway IP does). Soak on source-key/IP attribution.

### TT5 — Lazy keys (optional, ongoing) `[AGENT]` [reuse §7 roster]
- Per caller, on the operator's schedule: change ONLY its key env var to a
  distinct string/virtual key (guide §1A.4); restart that one service; confirm
  its rows separate in the ledger. URL never changes. Optionally, once all keyed:
  set `master_key`, `/key/generate`, apply §15.4 caps.

### TT6 — Pipe module + recovery + docs `[AGENT]` [reuse Phase 4/5 bodies]
- `llm-traffic` module (Phase 4 tasks). Three-place rule (Phase 5) **plus** the
  `*-upstream` renames in recovery scripts + stack-map. Category-F docs.

---

## Phase 0.0 — Pre-flight assumption verification (NEW — run first)

> **⚠️ SUPERSEDED for transparent mode** by the TT-task sequence above. Execute
> the phases below only as the **fallback** if spike **TT0** fails. Their
> backup, standup, pipe-module, and recovery bodies are reused by the TT-tasks
> via `[reuse]` references.

These assertions verify that the codebase still matches what the audit in
Guide §16/§18 captured. Any failure here means the audit needs revision
*before* any backups or edits happen.

### T0.0.1 — Verify llama-swap model list `[AGENT]` (Guide A1)
- **Depends on:** none
- **Action:**
  ```powershell
  $models = (Invoke-RestMethod http://127.0.0.1:8081/v1/models).data | ForEach-Object { $_.id } | Sort-Object
  $expected = @("qwen36-27b", "qwen36-27b:nothink", "qwen36-35b-a3b", "qwen36-35b-a3b:nothink") | Sort-Object
  if (Compare-Object $models $expected) {
    Write-Error "Model list drift: actual=$($models -join ',') expected=$($expected -join ',')"
    exit 1
  }
  ```
- **Acceptance:** no error; sets match exactly. **Failure → stop and update
  Guide §6 model_list before proceeding.**

### T0.0.2 — Verify llama-cpp-embed exposes exactly one model `[AGENT]` (Guide A2)
- **Depends on:** none
- **Action:**
  ```powershell
  $count = (Invoke-RestMethod http://127.0.0.1:8082/v1/models).data.Count
  if ($count -ne 1) { Write-Error "Expected 1 embedding model, got $count"; exit 1 }
  ```
- **Acceptance:** exits 0.

### T0.0.3 — Verify no undiscovered hardcoded llama-cpp URLs `[AGENT]` (Guide A3)
- **Depends on:** none
- **Action:** grep all source files; expected hits are the files listed in
  Guide §16.1 (Category A — 12 files) + §16.7 (Category G — verify-only)
  + §18.2 (confirmed-not-callers). Any other file is a NEW caller.
  ```powershell
  $expected = @(
    "docker-compose.yml",
    "OB1\docker\docker-compose.yml",
    "little-coder\config\little-coder.config.yaml",
    "little-coder\config\models.json",
    "little-coder\config\little-coder.schema.json",
    "little-coder\src\littlecoder\config.py",
    "OB1\recipes\email-history-import\pull-gmail.ts",
    "OB1\recipes\google-activity-import\import-google-activity.mjs",
    "filters\githelper-pipe.py",
    "filters\githelper-pipe-v1-backup.py",
    "scripts\status_check.py",
    "scripts\check-tailscale-health.ps1",
    "scripts\emergency-recovery.ps1",
    "scripts\emergency-recovery.bat",
    "scripts\quick-fixes.bat",
    "scripts\update-stack.bat",
    "scripts\gpu_check.py",
    "modules\system-health\service\system_health.py",
    "modules\gpu-status\service\gpu_status.py",
    "config\llama-swap.config.yaml",
    "scripts\ai_pipes\unified_openwebui_pipe.py",
    "scripts\ai_pipes\tailscale_serve_pipe.py",
    "entrypoint.sh",
    "CLAUDE.md",
    ".env.example",
    ".github\copilot-instructions.md",
    ".claude\skills\stack-map\SKILL.md",
    ".claude\skills\stack-map\references\workspace-stacks.md"
  )
  $found = (Get-ChildItem -Recurse -File -Include *.py,*.ts,*.mjs,*.json,*.yaml,*.yml,*.ps1,*.bat,*.sh,*.md |
    Select-String -Pattern "http://llama-cpp(-embed)?:8080" -List).Path |
    ForEach-Object { $_.Replace((Get-Location).Path + "\", "") }
  $surprises = $found | Where-Object { $_ -notin $expected -and $_ -notlike "documentation\*" -and $_ -notlike "little-coder\src\littlecoder\agent.py" -and $_ -notlike "little-coder\tests\*" }
  if ($surprises) {
    Write-Error "Undiscovered llama-cpp callers: $($surprises -join '; ')"
    exit 1
  }
  ```
- **Acceptance:** `$surprises` is empty. **Failure → audit the new file(s),
  add to Guide §16.1 or §16.7, then re-run.**

### T0.0.4 — (deferred to T0.7) Operator confirms OWUI embedding settings `[OPERATOR]` (Guide A4)
- Coverage merged into T0.7.

### T0.0.5 — Verify no NEW OB1 recipes have llama-cpp defaults `[AGENT]` (Guide A5)
- **Depends on:** none
- **Action:**
  ```powershell
  $hits = (Get-ChildItem -Recurse -File OB1\recipes -Include *.ts,*.mjs,*.py |
    Select-String -Pattern "http://llama-cpp" -List).Path |
    ForEach-Object { $_.Replace((Get-Location).Path + "\", "") }
  $expected = @(
    "OB1\recipes\email-history-import\pull-gmail.ts",
    "OB1\recipes\google-activity-import\import-google-activity.mjs"
  )
  $surprises = $hits | Where-Object { $_ -notin $expected }
  if ($surprises) {
    Write-Error "New OB1 recipes with llama-cpp defaults: $($surprises -join '; ')"
    exit 1
  }
  ```
- **Acceptance:** `$surprises` is empty.

### T0.0.6 — Verify little-coder schema.json is in sync with config.py `[AGENT]` (Guide A6)
- **Depends on:** none
- **Action:**
  ```powershell
  $expected = docker exec little-coder python -m littlecoder.config --schema 2>$null
  $actual = Get-Content -Raw little-coder\config\little-coder.schema.json
  # Compare normalized JSON (key-order + whitespace tolerant)
  $expectedNorm = ($expected | ConvertFrom-Json | ConvertTo-Json -Depth 100)
  $actualNorm = ($actual | ConvertFrom-Json | ConvertTo-Json -Depth 100)
  if ($expectedNorm -ne $actualNorm) {
    Write-Error "little-coder schema.json is OUT OF SYNC with config.py. Run: docker exec little-coder python -m littlecoder.config --schema > little-coder/config/little-coder.schema.json; commit; then re-run cutover."
    exit 1
  }
  ```
- **Acceptance:** no error. **Failure → operator regenerates the schema
  and commits BEFORE cutover so the regeneration commit doesn't tangle
  with our edits.**

### T0.0.7 — Operator reviews tailnet sessions `[OPERATOR]` (Guide A7)
- **Depends on:** none
- **Prompt:**
  > Run: `docker exec tailscale tailscale --socket=/tmp/tailscaled.sock status`
  > Check for any active sessions from machines you don't recognize. If
  > any unknown clients are present, they may be using the
  > `/llama-cpp` or `/llama-cpp-embed` tailnet paths and will need to
  > migrate (or be documented as known-direct callers). Reply
  > "tailnet reviewed" or "found <description of unknown client>".
- **Acceptance:** operator replies "tailnet reviewed" — anything else
  prompts a Guide §16.4 update before continuing.

### G-pre — Pre-flight passed `[GATE]`
- **Depends on:** T0.0.1–T0.0.7
- **Prompt to operator:**
  > All seven pre-flight assertions passed (or were resolved). Proceed
  > to Phase 0 backups? Reply "proceed" or "pause".
- **Acceptance:** operator replies "proceed".

---

## Phase 0 — Pre-flight (backups)

### T0.1 — Verify clean working tree `[AGENT]`
- **Depends on:** G-pre
- **Action:**
  ```powershell
  git status --porcelain
  ```
- **Acceptance:** empty output (no uncommitted changes). If non-empty, stop
  and prompt operator to stash or commit before continuing.

### T0.2 — Create integration branch `[AGENT]`
- **Depends on:** T0.1
- **Action:**
  ```powershell
  git checkout -b feature/litellm-proxy-integration
  ```
- **Acceptance:** `git branch --show-current` returns
  `feature/litellm-proxy-integration`.

### T0.3 — Snapshot OWUI data `[AGENT]`
- **Depends on:** T0.2
- **Action:** the `openwebui-backup` sidecar runs nightly. Trigger an
  on-demand run by exec-ing the backup script directly:
  ```powershell
  docker exec openwebui-backup sh /scripts/backup.sh
  ```
- **Acceptance:**
  ```powershell
  Get-ChildItem .\backups\openwebui\ -Filter "*$(Get-Date -Format yyyyMMdd)*" | Select-Object Name, Length
  ```
  returns at least one file with non-zero size, dated today.

### T0.4 — Snapshot mnemory data `[AGENT]`
- **Depends on:** T0.2
- **Action:**
  ```powershell
  docker exec mnemory-backup sh /scripts/backup.sh
  ```
- **Acceptance:** same shape as T0.3 against `.\backups\mnemory\`.

### T0.5 — Snapshot little-coder sessions `[AGENT]`
- **Depends on:** T0.2
- **Action:**
  ```powershell
  docker exec little-coder-backup sh /scripts/backup.sh
  ```
- **Acceptance:** non-empty backup under `.\backups\little-coder\` dated today.

### T0.6 — Dump OB1 Postgres `[AGENT]`
- **Depends on:** T0.2
- **Action:**
  ```powershell
  $date = Get-Date -Format yyyyMMdd
  docker exec openbrain-db pg_dump -U postgres openbrain | Out-File -Encoding utf8 "backups\openbrain\pre-litellm-$date.sql"
  ```
- **Acceptance:**
  ```powershell
  (Get-Item "backups\openbrain\pre-litellm-$date.sql").Length -gt 10000
  ```
  is `True`. (10 KB minimum sanity floor; a successful dump will be
  much larger.)

### T0.7 — Snapshot current OWUI admin settings `[OPERATOR]`
- **Depends on:** T0.6
- **Prompt to operator:**
  > Open Open WebUI Admin Panel → Settings → Connections. Record the
  > current values of: OpenAI API base URL, OpenAI API key, model list
  > visible in the dropdown. Then go to Settings → Documents and record:
  > Embedding Model Engine, OpenAI API base URL (embeddings), OpenAI
  > API key (embeddings), Embedding Model, Embedding Dimension. Save
  > these to a scratch file outside the repo. Reply "snapshotted" when
  > done.
- **Acceptance:** operator replies "snapshotted" (or equivalent).

### T0.8 — Scaffold `.env.example` additions `[AGENT]`
- **Depends on:** T0.2
- **Action:** append the §8.4 block from the guide to `.env.example`
  (workspace root). Create `OB1/docker/.env.example` if missing and
  append the OB1-side keys (`LITELLM_KEY_OB_MCP`, `LITELLM_KEY_OB_ENTITY`,
  `LITELLM_KEY_OB_WIKI`).
- **Acceptance:**
  ```powershell
  Select-String -Path .env.example -Pattern "LITELLM_MASTER_KEY"
  Select-String -Path OB1\docker\.env.example -Pattern "LITELLM_KEY_OB_"
  ```
  both return at least one match.

### T0.9 — Add placeholder `.env` entries `[AGENT]`
- **Depends on:** T0.8
- **Action:** in both `.env` files (root + `OB1/docker/.env`), append the
  same variable list but with value `__SET_AT_G1__` so the format is in
  place. **Do NOT touch existing entries.** If an entry already exists,
  skip it.
- **Acceptance:**
  ```powershell
  Select-String -Path .env -Pattern "LITELLM_MASTER_KEY"
  ```
  returns one match.

### T0.10 — Commit pre-flight snapshot `[AGENT]`
- **Depends on:** T0.3–T0.9
- **Action:**
  ```powershell
  git add backups/.gitkeep .env.example OB1/docker/.env.example
  git commit -m "[litellm] pre-flight: backups, .env.example scaffold"
  ```
  (`.env` itself is gitignored; do not commit.)
- **Acceptance:** `git log -1 --oneline` shows the new commit.

### G0 — Operator authorizes cutover `[GATE]`
- **Prompt to operator:**
  > Pre-flight complete. Backups exist at:
  > - `backups/openwebui/` (size: <print>)
  > - `backups/mnemory/` (size: <print>)
  > - `backups/openbrain/pre-litellm-<date>.sql` (size: <print>)
  > - `backups/little-coder/` (size: <print>)
  >
  > Branch `feature/litellm-proxy-integration` is checked out.
  > Maintenance-window check: is there a long-running inference workload
  > in flight that should complete first (e.g. wiki recompile, batch
  > entity extraction)?
  >
  > Reply "proceed" to begin Phase 1 standup, or "wait" to pause.
- **Acceptance:** operator replies "proceed."

### G1 — Operator supplies secrets `[GATE]`
- **Depends on:** G0
- **Prompt to operator:**
  > Generate two secrets and provide them. **The agent will write these
  > directly to `.env` and will not echo their values.**
  > - `LITELLM_MASTER_KEY` — `openssl rand -hex 32` (or equivalent)
  > - `LITELLM_DB_PASSWORD` — `openssl rand -hex 32`
  >
  > Paste both in the form:
  > `MASTER=<value>` newline `DBPASS=<value>`
- **Acceptance:** operator provides both. Agent writes them into the
  appropriate `.env` placeholders via `Edit` (replacing `__SET_AT_G1__`)
  and verifies via:
  ```powershell
  Select-String -Path .env -Pattern "LITELLM_MASTER_KEY=__SET_AT_G1__" -Quiet
  ```
  which must return `False`.

---

## Phase 1 — Standup

### T1.1 — Write LiteLLM config `[AGENT]`
- **Depends on:** G1
- **Action:** write `config/litellm.config.yaml` with the content from
  Guide §6. Note: the file uses env-var interpolation (`${LITELLM_MASTER_KEY}`,
  `${LITELLM_DB_PASSWORD}`, `${LC_LLAMA_API_KEY}`) — these resolve at
  container start via the compose `env_file` directive. The §6
  `litellm_settings` block includes `telemetry: false` (guide §19/D10 — no
  anonymous outbound beacon); the companion `LITELLM_LOCAL_MODEL_COST_MAP=True`
  is set as a compose env in §8.3, not here.
- **Acceptance:** `Test-Path config/litellm.config.yaml` is `True`.
  `Get-Content config/litellm.config.yaml | Select-String "model_name: bge-m3"`
  returns one match. `Get-Content config/litellm.config.yaml | Select-String "telemetry: false"`
  returns one match.

### T1.2 — Write LiteLLM backup script `[AGENT]`
- **Depends on:** G1
- **Action:** write `backup/llm-gateway-backup.sh` modelled exactly on
  `backup/mnemory-backup.sh` but `pg_dump`-ing the LiteLLM Postgres
  instead of tarring a volume. Use the same env vars (`BACKUP_DIR`,
  `DATA_DIR`, `RETAIN_DAYS`).
- **Acceptance:** file exists, shebang `#!/bin/sh`, includes
  `pg_dump -h llm-gateway-db -U litellm litellm`.

### T1.3 — Edit docker-compose.yml: add three services `[AGENT]`
- **Depends on:** T1.1, T1.2
- **Action:** insert the `llm-gateway`, `llm-gateway-db`,
  `llm-gateway-backup` service blocks from Guide §8.3 between
  `llama-cpp-embed` and `smolcrawl-pipelines` in `docker-compose.yml`.
  Add `llm-gateway-db-data:` under the top-level `volumes:` block.
- **Acceptance:**
  ```powershell
  docker compose config --services | Select-String "llm-gateway"
  ```
  returns three lines (`llm-gateway`, `llm-gateway-db`,
  `llm-gateway-backup`).

### T1.3.5 — Resolve and digest-pin the gateway image `[AGENT]` (NEW — supply-chain hardening, Guide §19/D9)
- **Depends on:** T1.3
- **Action:** the §8.3 block ships the gateway `image:` line with an
  `__RESOLVED_AT_STANDUP__` digest placeholder so it can never be brought up on
  the floating `:main-stable` tag. Resolve the current stable digest and pin it:
  ```powershell
  docker pull ghcr.io/berriai/litellm:main-stable
  $digest = (docker inspect ghcr.io/berriai/litellm:main-stable --format '{{index .RepoDigests 0}}')
  # $digest looks like: ghcr.io/berriai/litellm@sha256:<64-hex>
  Write-Host "Resolved gateway digest: $digest"
  ```
  **Before pinning,** confirm the resolved release is outside any known LiteLLM
  compromise window — check
  https://github.com/BerriAI/litellm/security/advisories . Then `Edit` the
  `docker-compose.yml` `llm-gateway` `image:` line, replacing
  `ghcr.io/berriai/litellm@sha256:__RESOLVED_AT_STANDUP__` with `$digest`.
  Leave the bump-procedure comment block above the `image:` line intact.
- **Acceptance:**
  ```powershell
  $img = (docker compose config | Select-String "ghcr.io/berriai/litellm").Line.Trim()
  if ($img -notmatch "@sha256:[0-9a-f]{64}") { Write-Error "gateway image not digest-pinned: $img"; exit 1 }
  if ($img -match ":main-stable") { Write-Error "gateway still on a floating tag: $img"; exit 1 }
  Select-String -Path docker-compose.yml -Pattern "__RESOLVED_AT_STANDUP__" -Quiet  # must be False
  ```
  (`docker compose config` renders the resolved image with comments stripped,
  so the bump-procedure comment that mentions `:main-stable` does not trip the
  check.) **Failure → stop; do not bring up the gateway on an unpinned image.**

### T1.4 — Bring up DB first `[AGENT]`
- **Depends on:** T1.3
- **Action:**
  ```powershell
  docker compose up -d llm-gateway-db
  ```
  Poll healthcheck:
  ```powershell
  for ($i = 0; $i -lt 30; $i++) {
    $status = docker inspect --format '{{.State.Health.Status}}' llm-gateway-db 2>$null
    if ($status -eq 'healthy') { break }
    Start-Sleep -Seconds 2
  }
  ```
- **Acceptance:** `docker inspect --format '{{.State.Health.Status}}'
  llm-gateway-db` returns `healthy`.

### T1.5 — Bring up gateway `[AGENT]`
- **Depends on:** T1.4, T1.3.5 (image must be digest-pinned before first run)
- **Action:**
  ```powershell
  docker compose up -d llm-gateway
  ```
  Poll the same way as T1.4.
- **Acceptance:**
  ```powershell
  Invoke-RestMethod http://127.0.0.1:4000/health/liveliness
  ```
  returns a non-error response.

### T1.6 — Verify upstream connectivity `[AGENT]`
- **Depends on:** T1.5
- **Action:**
  ```powershell
  docker exec llm-gateway curl -fsS http://llama-cpp:8080/health
  docker exec llm-gateway curl -fsS http://llama-cpp-embed:8080/health
  ```
- **Acceptance:** both commands exit 0 and return non-empty bodies.

### T1.7 — Verify model registration `[AGENT]`
- **Depends on:** T1.5
- **Action:**
  ```powershell
  $key = (Get-Content .env | Select-String "^LITELLM_MASTER_KEY=").ToString().Split('=')[1]
  $models = Invoke-RestMethod -Uri http://127.0.0.1:4000/v1/models -Headers @{Authorization="Bearer $key"}
  $models.data | ForEach-Object { $_.id }
  ```
- **Acceptance:** output contains `qwen36-27b`, `qwen36-27b:nothink`,
  `qwen36-35b-a3b`, `bge-m3`.

### T1.8 — Generate virtual keys (in memory) `[AGENT]`
- **Depends on:** T1.7
- **Action:** for each alias in Guide §7, POST to `/key/generate`:
  ```powershell
  $aliases = @(
    @{alias="sk-lc-coder";       caller="little-coder";              plane="coder"},
    @{alias="sk-mnemory";        caller="mnemory";                   plane="memory"},
    @{alias="sk-ob-mcp";         caller="openbrain-mcp";             plane="ob1"},
    @{alias="sk-ob-entity";      caller="openbrain-entity-worker";   plane="ob1"},
    @{alias="sk-ob-wiki";        caller="openbrain-wiki";            plane="ob1"},
    @{alias="sk-owui-chat";      caller="openwebui-chat";            plane="core"},
    @{alias="sk-owui-embed";     caller="openwebui-embed";           plane="core"},
    @{alias="sk-owui-githelper"; caller="openwebui-githelper-pipe";  plane="core"},
    @{alias="sk-admin";          caller="admin";                     plane="admin"}
  )
  $issued = @{}
  foreach ($a in $aliases) {
    $body = @{ key_alias = $a.alias; metadata = @{ caller = $a.caller; plane = $a.plane } } | ConvertTo-Json
    $resp = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4000/key/generate -Headers @{Authorization="Bearer $key"} -Body $body -ContentType "application/json"
    $issued[$a.alias] = $resp.key
  }
  ```
  Agent holds `$issued` in memory; does NOT write to disk yet.
- **Acceptance:** `$issued.Count` equals 9. Every value starts with `sk-`.

### T1.9 — Apply starting TPM/RPM caps `[AGENT]`
- **Depends on:** T1.8
- **Action:** for each key, `/key/update` with the caps from Guide §15.4
  (table form). Use the `$issued` map.
- **Acceptance:** for each key,
  `Invoke-RestMethod /key/info?key=<sk-…>` shows `tpm_limit` and
  `rpm_limit` matching the §15.4 row.

### T1.10 — Test request via sk-admin `[AGENT]`
- **Depends on:** T1.9
- **Action:**
  ```powershell
  $admin = $issued["sk-admin"]
  $body = @{
    model = "qwen36-27b:nothink"
    messages = @(@{role="user"; content="reply with the single token: ok"})
    max_tokens = 5
  } | ConvertTo-Json -Depth 4
  Invoke-RestMethod -Method Post -Uri http://127.0.0.1:4000/v1/chat/completions -Headers @{Authorization="Bearer $admin"} -Body $body -ContentType "application/json"
  ```
- **Acceptance:** response has a `choices[0].message.content` field
  containing `ok` (case-insensitive substring).

### T1.11 — Verify spend-log row appears `[AGENT]`
- **Depends on:** T1.10
- **Action:**
  ```powershell
  docker exec llm-gateway-db psql -U litellm -d litellm -c "SELECT api_key, model, total_tokens FROM \`"LiteLLM_SpendLogs\`" ORDER BY created_at DESC LIMIT 1"
  ```
- **Acceptance:** one row returned, `api_key` ends with the last 6 chars
  of `sk-admin`, `model = qwen36-27b:nothink`, `total_tokens > 0`.

### G2 — Operator reviews issued key aliases `[GATE]`
- **Depends on:** T1.11
- **Prompt to operator:**
  > 9 virtual keys generated. Aliases and metadata (key values
  > redacted):
  > - sk-lc-coder     → caller=little-coder              plane=coder
  > - sk-mnemory      → caller=mnemory                    plane=memory
  > - sk-ob-mcp       → caller=openbrain-mcp              plane=ob1
  > - sk-ob-entity    → caller=openbrain-entity-worker    plane=ob1
  > - sk-ob-wiki      → caller=openbrain-wiki             plane=ob1
  > - sk-owui-chat    → caller=openwebui-chat             plane=core
  > - sk-owui-embed   → caller=openwebui-embed            plane=core
  > - sk-owui-githelper → caller=openwebui-githelper-pipe plane=core
  > - sk-admin        → caller=admin                      plane=admin
  >
  > **Pinned gateway image digest** (supply-chain sanity check, Guide §19.3):
  > `<docker inspect llm-gateway --format '{{.Image}}'>`
  > Confirm this `@sha256:` digest is the one you intended and is outside any
  > known LiteLLM compromise window.
  >
  > Reply "approved" to persist these to `.env` files and proceed to
  > Phase 2 (heavy-hitter cutover).
- **Acceptance:** operator replies "approved."

### T1.12 — Persist virtual keys to `.env` files `[AGENT]`
- **Depends on:** G2
- **Action:** atomic write of each `LITELLM_KEY_*` placeholder to its
  real value, in both `.env` (workspace root) and `OB1/docker/.env`.
  Use `Edit` to swap the `__SET_AT_G1__` sentinel for the real key per
  entry. Order: write OB1 keys to OB1 .env first, then write the rest
  to root .env. Each Edit verified by re-grepping for the sentinel
  (must be gone).
- **Acceptance:**
  ```powershell
  Select-String -Path .env, OB1\docker\.env -Pattern "__SET_AT_G1__" -Quiet
  ```
  returns `False`.

### T1.13 — Commit Phase 1 work `[AGENT]`
- **Depends on:** T1.12
- **Action:**
  ```powershell
  git add config/litellm.config.yaml backup/llm-gateway-backup.sh docker-compose.yml
  git commit -m "[litellm] phase 1: standup — gateway, db, backup sidecar; keys issued"
  ```
- **Acceptance:** `git log -1 --oneline` shows the commit.

---

## Phase 2 — Heavy-hitter cutover

### T2.1 — Edit little-coder Python source defaults `[AGENT]` (NEW)
- **Depends on:** T1.13
- **Action:** **Critical** — `little-coder/config/little-coder.schema.json`
  is GENERATED from `little-coder/src/littlecoder/config.py`. Edit the
  Python source FIRST; the schema will be regenerated in T2.1.5.
  Three edits in [little-coder/src/littlecoder/config.py:37-46](little-coder/src/littlecoder/config.py#L37):
  - Line 37 `base_url: str = "http://llama-cpp:8080/v1"` →
    `base_url: str = "http://llm-gateway:4000/v1"`
  - Line 45 `embedding_base_url: str = "http://llama-cpp-embed:8080/v1"` →
    `embedding_base_url: str = "http://llm-gateway:4000/v1"`
  - Line 46 `embedding_model: str = "bge-m3-f16.gguf"` — **leave as-is**
    (LiteLLM §6 model_list registers this alias; changing it would
    require re-embedding any prior content that used this id).
- **Acceptance:**
  ```powershell
  Select-String little-coder\src\littlecoder\config.py -Pattern "llama-cpp(-embed)?:8080" -Quiet
  ```
  returns `False`.

### T2.1.5 — Regenerate little-coder schema.json `[AGENT]` (NEW)
- **Depends on:** T2.1
- **Action:**
  ```powershell
  docker exec little-coder python -m littlecoder.config --schema > little-coder\config\little-coder.schema.json
  ```
- **Acceptance:**
  ```powershell
  Select-String little-coder\config\little-coder.schema.json -Pattern "llm-gateway:4000" -Quiet
  ```
  returns `True`. **And** the schema content matches what the Python
  produces (sanity-check by diffing against another regeneration).

### T2.1.6 — Edit little-coder runtime config files `[AGENT]`
- **Depends on:** T2.1.5
- **Action:** two `Edit` operations (the schema.json was already updated by T2.1.5):
  - `little-coder/config/little-coder.config.yaml:11` —
    `base_url: http://llama-cpp:8080/v1` → `base_url: http://llm-gateway:4000/v1`
  - `little-coder/config/models.json:6` —
    `"baseUrl": "http://llama-cpp:8080/v1"` → `"baseUrl": "http://llm-gateway:4000/v1"`
- **Acceptance:**
  ```powershell
  Select-String -Path little-coder\config\* -Pattern "llama-cpp:8080" -Quiet
  Select-String -Path little-coder\config\* -Pattern "llama-cpp-embed:8080" -Quiet
  ```
  both return `False`.

### T2.2 — Edit docker-compose.yml little-coder block `[AGENT]`
- **Depends on:** T2.1.6
- **Action:** two edits in `docker-compose.yml`:
  - line 689 (`LC_LLAMA_API_KEY=${LC_LLAMA_API_KEY:-llama}`) — confirm
    `.env` `LC_LLAMA_API_KEY` was overwritten by Phase 1; no edit if so.
  - little-coder `depends_on` block (around lines 715–719) — replace
    `llama-cpp` with `llm-gateway`. **Keep `open-terminal` dependency
    as-is** (it is not an inference caller).
- **Acceptance:**
  ```powershell
  $env_lc = docker inspect little-coder --format '{{range .Config.Env}}{{println .}}{{end}}' | Select-String "LC_LLAMA_API_KEY="
  ```
  Value should be the `sk-lc-coder` virtual key (after T2.3 restart).

### T2.3 — Restart little-coder `[AGENT]`
- **Depends on:** T2.2
- **Action:**
  ```powershell
  docker compose up -d little-coder
  ```
  Poll healthcheck.
- **Acceptance:** `docker inspect --format '{{.State.Health.Status}}'
  little-coder` is `healthy`.

### T2.4 — Trigger little-coder smoke test `[AGENT]`
- **Depends on:** T2.3
- **Action:**
  ```powershell
  docker exec little-coder lc task --help
  ```
  (Does not actually run a task; just confirms CLI is responsive. Real
  smoke test is the next step.)
  Then issue a trivial inference via the agent:
  ```powershell
  docker exec little-coder curl -fsS http://llm-gateway:4000/v1/chat/completions `
    -H "Authorization: Bearer $env:LC_LLAMA_API_KEY" `
    -H "Content-Type: application/json" `
    -d '{\"model\":\"qwen36-27b:nothink\",\"messages\":[{\"role\":\"user\",\"content\":\"reply: ok\"}],\"max_tokens\":5}'
  ```
- **Acceptance:**
  ```powershell
  docker exec llm-gateway-db psql -U litellm -d litellm -tAc "SELECT count(*) FROM \`"LiteLLM_SpendLogs\`" WHERE api_key LIKE '%' || (SELECT right(key, 6) FROM \`"LiteLLM_VerificationToken\`" WHERE key_alias='sk-lc-coder')"
  ```
  returns `> 0`.

### T2.5 — Edit OB1 entity-worker env block `[AGENT]`
- **Depends on:** T2.4
- **Action:** four edits in `OB1/docker/docker-compose.yml:224-230`:
  - line 224 `CHAT_API_BASE` → `http://llm-gateway:4000/v1`
  - line 225 `CHAT_API_KEY` → `${LITELLM_KEY_OB_ENTITY}`
  - line 228 `EMBEDDING_API_BASE` → `http://llm-gateway:4000/v1`
  - line 229 `EMBEDDING_API_KEY` → `${LITELLM_KEY_OB_ENTITY}`
- **Acceptance:**
  ```powershell
  Select-String -Path OB1\docker\docker-compose.yml -Pattern "llama-cpp" -Context 0,0 | Select-String "entity-worker" -Context 50,0
  ```
  returns no matches in the entity-worker block.

### T2.6 — Restart entity-worker `[AGENT]`
- **Depends on:** T2.5
- **Action:**
  ```powershell
  docker compose -f OB1\docker\docker-compose.yml up -d openbrain-entity-worker
  ```
- **Acceptance:** container running, no startup errors in
  `docker logs openbrain-entity-worker --tail 20`.

### T2.7 — Trigger entity-worker drain `[AGENT]`
- **Depends on:** T2.6
- **Action:** inspect startup logs for the drain endpoint:
  ```powershell
  docker logs openbrain-entity-worker --tail 20 | Select-String "Listening on"
  ```
  Then trigger:
  ```powershell
  Invoke-RestMethod -Method Post http://127.0.0.1:8810/drain -TimeoutSec 90
  ```
  (Endpoint name may differ; if 404, agent inspects available routes:
  `Invoke-RestMethod http://127.0.0.1:8810/`. If no drain endpoint
  exists, agent waits 5 minutes for the worker to pick up the next
  natural extraction and skips to T2.8.)
- **Acceptance:** see T2.8.

### T2.8 — Verify entity-worker spend-log rows `[AGENT]`
- **Depends on:** T2.7
- **Action:** wait up to 2 minutes for traffic; then:
  ```powershell
  docker exec llm-gateway-db psql -U litellm -d litellm -c "SELECT count(*), max(created_at) FROM \`"LiteLLM_SpendLogs\`" WHERE api_key LIKE (SELECT '%' || right(key, 6) FROM \`"LiteLLM_VerificationToken\`" WHERE key_alias='sk-ob-entity')"
  ```
- **Acceptance:** count > 0, `max(created_at)` within the last 5 minutes.

### G3 — Operator performs OWUI UI flip `[GATE]`
- **Depends on:** T2.8
- **Prompt to operator:**
  > **Open WebUI admin panel cutover required.** Refer to Guide §17.4
  > for the full checklist. Required steps in order:
  >
  > 1. **17.4.1 Chat endpoint** — Admin → Settings → Connections →
  >    set OpenAI API Base URL to `http://llm-gateway:4000/v1`, key to
  >    `<sk-owui-chat value from .env>`. Verify connection. Save.
  > 2. **17.4.2 Embedding endpoint** — Admin → Settings → Documents →
  >    set Embedding base URL to `http://llm-gateway:4000/v1`, key to
  >    `<sk-owui-embed value from .env>`, model `bge-m3`, dimension
  >    `1024`. Save.
  > 3. **17.4.3 Web Search embedding** — Admin → Settings → Web Search
  >    → repoint if override exists. Save.
  > 4. **17.4.4 Per-model overrides** — Admin → Models → check each
  >    custom model for a Base URL override. Repoint any matches.
  > 5. **17.4.5 Filter pipes Valves** — Admin → Functions → for each
  >    filter (especially `githelper-pipe`), open Valves and update
  >    any `http://llama-cpp:8080/v1` to `http://llm-gateway:4000/v1`.
  >    Set its API key to `<sk-owui-githelper value from .env>`.
  > 6. **17.4.6 Tool functions** — Admin → Tools → same Valves audit.
  > 7. **17.4.7 Smoke tests** — new chat, RAG upload, web search.
  >
  > Reply "owui done" when all seven sub-steps are complete.
- **Acceptance:** operator replies "owui done."

### T2.9 — Verify OWUI spend-log rows `[AGENT]`
- **Depends on:** G3
- **Action:**
  ```powershell
  docker exec llm-gateway-db psql -U litellm -d litellm -c "SELECT key_alias, count(*) FROM \`"LiteLLM_SpendLogs\`" sl JOIN \`"LiteLLM_VerificationToken\`" vt ON sl.api_key = vt.token WHERE vt.key_alias IN ('sk-owui-chat','sk-owui-embed','sk-owui-githelper') AND sl.created_at > now() - interval '10 minutes' GROUP BY key_alias"
  ```
- **Acceptance:** at minimum `sk-owui-chat` and `sk-owui-embed` show
  count > 0. `sk-owui-githelper` may be zero if the operator hasn't
  exercised it; surface to operator for verification only, do not block.

### T2.10 — Commit Phase 2 work `[AGENT]`
- **Depends on:** T2.9
- **Action:**
  ```powershell
  git add little-coder/config docker-compose.yml OB1/docker/docker-compose.yml
  git commit -m "[litellm] phase 2: heavy-hitter cutover (little-coder, entity-worker, OWUI)"
  ```
- **Acceptance:** commit exists.

### T2.11 — Stopping-point check `[OPERATOR]`
- **Depends on:** T2.10
- **Prompt to operator:**
  > Phase 2 complete. Three heavy hitters are now logged. The system is
  > in a stable mixed-mode state: `mnemory`, `openbrain-mcp`,
  > `openbrain-wiki`, and the OWUI githelper-pipe (if deployed) still
  > talk to `llama-cpp` directly. The pipe-module added in Phase 4 will
  > need an allowlist for these direct callers.
  >
  > Continue to Phase 3 (remaining callers — full attribution),
  > skip to Phase 4 (stop at heavy-hitter scope), or pause?
  > Reply "phase 3", "phase 4", or "pause".
- **Acceptance:** operator chooses a path.

---

## Phase 3 — Remaining callers (optional)

### T3.1 — Edit mnemory env block `[AGENT]`
- **Depends on:** T2.11 = "phase 3"
- **Action:** four edits in `docker-compose.yml:295-299`:
  - line 295 `LLM_API_KEY=ollama` → `LLM_API_KEY=${LITELLM_KEY_MNEMORY}`
  - line 296 `LLM_BASE_URL` → `http://llm-gateway:4000/v1`
  - line 298 `EMBED_BASE_URL` → `http://llm-gateway:4000/v1`
  - line 299 `EMBED_MODEL=qllama/bge-m3:latest` → `EMBED_MODEL=bge-m3`
    **(critical — see Guide §17.8 risk)**
- **Acceptance:**
  ```powershell
  Select-String -Path docker-compose.yml -Pattern "qllama/bge-m3" -Quiet
  ```
  returns `False`.

### T3.2 — Edit mnemory depends_on `[AGENT]`
- **Depends on:** T3.1
- **Action:** `docker-compose.yml:313-316` — replace the `llama-cpp` +
  `llama-cpp-embed` entries with a single `llm-gateway:` `condition: service_healthy`.
- **Acceptance:** `docker compose config | Select-String "mnemory" -Context 0,15 | Select-String "depends_on" -Context 0,5` shows `llm-gateway` and no `llama-cpp*`.

### T3.3 — Restart mnemory and verify `[AGENT]`
- **Depends on:** T3.2
- **Action:**
  ```powershell
  docker compose up -d mnemory
  ```
  Wait healthy. Issue a known-good mnemory search via MCP (agent uses
  any available MCP client; if none configured, issues a search via
  `Invoke-RestMethod` against the mnemory-gateway).
- **Acceptance:** spend-log row for `sk-mnemory` appears within 5 min.

### T3.4 — Edit openbrain-mcp env block `[AGENT]`
- **Depends on:** T3.3
- **Action:** four edits in `OB1/docker/docker-compose.yml:57-63`:
  - line 57 `EMBEDDING_API_BASE` → gateway
  - line 58 `EMBEDDING_API_KEY` → `${LITELLM_KEY_OB_MCP}`
  - line 61 `CHAT_API_BASE` → gateway
  - line 62 `CHAT_API_KEY` → `${LITELLM_KEY_OB_MCP}`
- **Acceptance:** grep within the openbrain-mcp block returns no `llama-cpp` mentions.

### T3.5 — Restart openbrain-mcp `[AGENT]`
- **Depends on:** T3.4
- **Action:** `docker compose -f OB1\docker\docker-compose.yml up -d openbrain-mcp`
- **Acceptance:** container running.

### T3.6 — Edit openbrain-wiki env block `[AGENT]`
- **Depends on:** T3.5
- **Action:** four edits in `OB1/docker/docker-compose.yml:261-266`:
  - line 261 `LLM_BASE_URL` → gateway
  - line 262 `LLM_API_KEY` → `${LITELLM_KEY_OB_WIKI}`
  - line 264 `EMBEDDING_BASE_URL` → gateway
  - line 265 `EMBEDDING_API_KEY` → `${LITELLM_KEY_OB_WIKI}`
- **Acceptance:** grep within the openbrain-wiki block returns no `llama-cpp` mentions.

### T3.7 — Restart openbrain-wiki `[AGENT]`
- **Depends on:** T3.6
- **Action:** `docker compose -f OB1\docker\docker-compose.yml up -d openbrain-wiki`
- **Acceptance:** container running. The wiki only compiles at 01:00
  local or on `POST /recompile` — agent does not trigger a compile
  (expensive); operator does so at convenience and checks for
  `sk-ob-wiki` rows post-hoc.

### T3.8 — Edit githelper-pipe.py file default `[AGENT]`
- **Depends on:** T3.7
- **Action:** edit `filters/githelper-pipe.py:117-118`:
  ```python
  default="http://llm-gateway:4000/v1",
  description="Backend URL (e.g. http://llm-gateway:4000/v1).",
  ```
  Do **not** edit the `-v1-backup.py` file (historical preservation per
  Guide §16.2).
- **Acceptance:** `Select-String filters\githelper-pipe.py -Pattern "llama-cpp:8080"` returns no matches.

### T3.9 — Prompt operator to update deployed githelper Valves `[OPERATOR]`
- **Depends on:** T3.8
- **Prompt:**
  > File default for `filters/githelper-pipe.py` is updated, but
  > already-deployed pipe instances in OWUI store their own copies of
  > the Valves. Open OWUI Admin → Functions → githelper-pipe → Valves
  > → update TARGET_BASE_URL to `http://llm-gateway:4000/v1` and add
  > TARGET_API_KEY = `<sk-owui-githelper value from .env>`.
  > Reply "valves updated" when done. Reply "skip" if githelper is
  > not deployed in OWUI.
- **Acceptance:** operator replies "valves updated" or "skip."

### T3.9.5 — Edit OB1 operator-run recipe defaults `[AGENT]` (NEW)
- **Depends on:** T3.9
- **Action:** edit the two ad-hoc recipe scripts so future operator runs
  default to the gateway (rather than requiring `LOCAL_LLM_BASE=…`
  overrides each time):
  - [OB1/recipes/email-history-import/pull-gmail.ts:68](OB1/recipes/email-history-import/pull-gmail.ts#L68) —
    `"http://llama-cpp:8080/v1"` → `"http://llm-gateway:4000/v1"`
  - [OB1/recipes/email-history-import/pull-gmail.ts:70](OB1/recipes/email-history-import/pull-gmail.ts#L70) —
    `"http://llama-cpp-embed:8080/v1"` → `"http://llm-gateway:4000/v1"`
  - [OB1/recipes/google-activity-import/import-google-activity.mjs:33](OB1/recipes/google-activity-import/import-google-activity.mjs#L33) —
    same swap on `LLM_BASE` default
  - [OB1/recipes/google-activity-import/import-google-activity.mjs:35](OB1/recipes/google-activity-import/import-google-activity.mjs#L35) —
    same swap on `EMBED_BASE` default
- **Note:** these recipes are operator-run ad-hoc, not container services
  — no restart required. The next time the operator invokes them they
  pick up the new defaults. The operator should also be told they can
  pass `LOCAL_LLM_KEY=<sk-...>` env to attribute these runs to a
  per-recipe virtual key if desired (otherwise traffic shows up as the
  unkeyed `sk-admin` or anonymous, which is acceptable for ad-hoc).
- **Acceptance:**
  ```powershell
  Select-String -Path OB1\recipes\email-history-import\pull-gmail.ts,OB1\recipes\google-activity-import\import-google-activity.mjs -Pattern "llama-cpp(-embed)?:8080" -Quiet
  ```
  returns `False`.

### T3.9.6 — Prompt operator about OB1 recipe attribution (optional) `[OPERATOR]` (NEW)
- **Depends on:** T3.9.5
- **Prompt:**
  > Want per-recipe attribution for the OB1 gmail / google-activity
  > imports? If yes, I can generate `sk-ob-recipe-gmail` and
  > `sk-ob-recipe-google` virtual keys and document the
  > `LOCAL_LLM_KEY=<...>` env var to pass at invocation. If no, ad-hoc
  > runs will show up under `sk-admin` or no key (still gateway-routed,
  > just not attributed by recipe). Reply "generate keys" or "skip".
- **Acceptance:** operator decides. If "generate keys", agent runs the
  `/key/generate` calls from T1.8 with these two extra aliases, updates
  the `.env`, and prints the operator-side env-var snippet to use.

### T3.10 — Dark-traffic verification `[AGENT]`
- **Depends on:** T3.9.6
- **Action:**
  ```powershell
  docker logs llama-cpp --tail 500 | Select-String "POST /v1/" | ForEach-Object { ($_ -split ' ')[2] } | Sort-Object -Unique
  ```
  Resolve each IP to a container:
  ```powershell
  docker network inspect ai-stack_llm-net --format "{{range .Containers}}{{.IPv4Address}}|{{.Name}}{{println}}{{end}}"
  ```
- **Acceptance:** the only IP that appears as a POSTing client in
  `llama-cpp` logs should be the `llm-gateway` container IP. Any other
  IP indicates a missed cutover — surface to operator with the IP→name
  mapping.

### T3.11 — Commit Phase 3 work `[AGENT]`
- **Depends on:** T3.10
- **Action:**
  ```powershell
  git add docker-compose.yml OB1/docker/docker-compose.yml filters/githelper-pipe.py
  git commit -m "[litellm] phase 3: remaining callers cutover; full attribution"
  ```
- **Acceptance:** commit exists.

---

## Phase 4 — Observability

### T4.1 — Scaffold modules/llm-traffic/ `[AGENT]`
- **Depends on:** T2.10 (phase 4 can run after either Phase 2 or Phase 3)
- **Action:** create the directory structure mirroring
  `modules/gpu-status/`:
  - `modules/llm-traffic/manifest.yaml`
  - `modules/llm-traffic/service/llm_traffic.py`
  - `modules/llm-traffic/tests/test_llm_traffic.py`
  Use `modules/gpu-status/manifest.yaml` as the structural template for
  the manifest; capabilities and trigger phrases from Guide §9.1.
- **Acceptance:** `Test-Path modules/llm-traffic/manifest.yaml` is `True`.

### T4.2 — Implement service/llm_traffic.py `[AGENT]`
- **Depends on:** T4.1
- **Action:** the module:
  - Reads `LITELLM_KEY_ADMIN` from environment (mounted via the unified
    pipe — same pattern as other modules).
  - On invocation, parses the user input for time qualifiers
    (`today` / `last 24h` / `last week` / `since boot`); defaults to
    `last 1h`.
  - Queries `http://llm-gateway:4000/spend/logs?start_date=…&end_date=…`,
    `/spend/calculate?…&group_by=api_key`, `/key/info`.
  - Aggregates per-key: requests, tokens in, tokens out, avg latency,
    429 count (from `response_status` column).
  - Renders a markdown table.
  - Returns `{"module_id": "llm-traffic", "content": "<markdown>"}`
    matching the existing module response shape.
  - Includes a "live snapshot" prefix that shows current `/health` +
    last 5 rows.
  - **Carries an allowlist** of known-direct callers if Phase 3 was
    skipped — read from `LLM_TRAFFIC_DIRECT_ALLOWLIST` env var
    (comma-separated container names).
- **Acceptance:** local unit test in `tests/test_llm_traffic.py` passes
  with a mocked gateway response.

### T4.3 — Update unified_openwebui_pipe.py allowlist `[AGENT]`
- **Depends on:** T4.2
- **Action:** edit `scripts/ai_pipes/unified_openwebui_pipe.py:302` to
  add `"llm-traffic"` to the module-id list. Also append a new section
  to the COMMAND LIST docstring (between lines 99 and 104, after the
  Admin help section) matching Guide §9.1.
- **Acceptance:**
  ```powershell
  Select-String scripts\ai_pipes\unified_openwebui_pipe.py -Pattern '"llm-traffic"' -Quiet
  ```
  returns `True`.

### T4.4 — Update core/router.py triggers `[AGENT]`
- **Depends on:** T4.2
- **Action:** edit `core/router.py` to add the trigger keywords from
  Guide §9.1 (route to `modules/llm-traffic`). Pattern matches the
  existing routes for `gpu-status`, `system-health`, etc.
- **Acceptance:** unit test or local invocation of the router with
  `"llm traffic"` input returns the new module.

### T4.5 — Restart openwebui to reload pipe `[AGENT]`
- **Depends on:** T4.3, T4.4
- **Action:**
  ```powershell
  docker compose restart openwebui
  ```
  Wait healthy.
- **Acceptance:** `docker inspect --format '{{.State.Health.Status}}' openwebui` is `healthy`.

### T4.6 — Operator triggers pipe-module smoke test `[OPERATOR]`
- **Depends on:** T4.5
- **Prompt:**
  > In OWUI, send a message containing the phrase `llm traffic`. The
  > pipe should render a markdown table showing per-caller rows
  > (sk-lc-coder, sk-ob-entity, sk-owui-chat at minimum). Reply
  > "rendered" if the table appears; reply with the error otherwise.
- **Acceptance:** operator replies "rendered."

### T4.7 — Apply caller-side retry-loop patches `[AGENT]`
- **Depends on:** T4.6
- **Action:** per Guide §17.7:
  - Patch `filters/githelper-pipe.py` — wrap `requests.post` in a small
    helper that honors `Retry-After`. Commit to the integration branch.
  - For `openbrain-entity-worker` — the worker source lives at
    `../integrations/entity-extraction-worker/` (relative to OB1
    docker dir). Agent creates a sibling branch in that repo, applies
    the retry-loop patch in Deno fetch wrapper, commits **but does NOT
    push** (per Guide §17.7 — upstream review required).
  - `openbrain-wiki` — verify whether the SDK already handles
    `Retry-After`; patch only if not.
- **Acceptance:** patch commits exist; no push performed without
  explicit operator approval.

### T4.8 — Commit Phase 4 work `[AGENT]`
- **Depends on:** T4.7
- **Action:**
  ```powershell
  git add modules/llm-traffic core/router.py scripts/ai_pipes/unified_openwebui_pipe.py filters/githelper-pipe.py
  git commit -m "[litellm] phase 4: llm-traffic module, router triggers, retry patches"
  ```
- **Acceptance:** commit exists.

---

## Phase 5 — Recovery scripts + documentation

### T5.1 — Update emergency-recovery.ps1 `[AGENT]`
- **Depends on:** T4.8
- **Action:** edit `scripts/emergency-recovery.ps1`:
  - Line 30: append `"llm-gateway", "llm-gateway-db", "llm-gateway-backup"`
    to `MainStackServices`.
  - Find the startup sequence (around line 472–493) — insert
    `llm-gateway-db` startup + healthcheck wait between
    `llama-cpp-embed` healthy and the mnemory startup; then
    `llm-gateway` startup + healthcheck wait before mnemory.
  - Find the shutdown sequence (around lines 428–432) — insert
    `Stop-ServiceGracefully "llm-gateway" 30` and `Stop-ServiceGracefully
    "llm-gateway-db" 30` **after** all caller services have been
    stopped (i.e. after mnemory, openwebui, etc.) but **before**
    llama-cpp.
- **Acceptance:**
  ```powershell
  Select-String scripts\emergency-recovery.ps1 -Pattern "llm-gateway"
  ```
  returns ≥ 6 matches.

### T5.2 — Update emergency-recovery.bat `[AGENT]`
- **Depends on:** T5.1
- **Action:** mirror T5.1 changes in `scripts/emergency-recovery.bat`.
- **Acceptance:** equivalent grep returns matches.

### T5.3 — Update quick-fixes.bat `[AGENT]`
- **Depends on:** T5.2
- **Action:** edit `scripts/quick-fixes.bat`:
  - Line 43: append `llm-gateway` to menu option label.
  - Lines 226 / 509: extend the restart lists to include
    `llm-gateway llm-gateway-db`.
  - Add a new menu option (12) for "llm-gateway health check and
    restart" mirroring the existing llama-cpp pair.
- **Acceptance:** menu shows llm-gateway option.

### T5.4 — Update system_health probe `[AGENT]`
- **Depends on:** T5.3
- **Action:** edit `modules/system-health/service/system_health.py`:
  - Add to probe list at lines 38–39:
    ```python
    {"name": "llm-gateway",     "plane": "Core", "host": "llm-gateway",     "port": 4000, "path": "/health/liveliness", "critical": True},
    {"name": "llm-gateway-db",  "plane": "Core", "host": "llm-gateway-db",  "port": 5432, "path": "",                   "critical": True},
    ```
  - Add `"llm-gateway"` to `expected_services` list at line 93.
- **Acceptance:** `system health` pipe trigger in OWUI shows llm-gateway row.

### T5.5 — Update update-stack.bat `[AGENT]`
- **Depends on:** T5.4
- **Action:** add an `llm-gateway` menu choice that follows the **digest-bump**
  procedure (Guide §19.3 / D9), **not** a blind `:main-stable` pull-and-run.
  The option must: `docker pull ghcr.io/berriai/litellm:main-stable`, resolve
  the new digest via
  `docker inspect ghcr.io/berriai/litellm:main-stable --format "{{index .RepoDigests 0}}"`,
  print it for the operator to confirm against the BerriAI/litellm security
  advisories, update the `image:` digest in `docker-compose.yml`, then recreate
  `llm-gateway` and re-run its health check. Mirror the documented
  `portal-alerter` bump discipline already in the compose file.
- **Acceptance:** menu shows the new option, and the option's logic updates the
  pinned `@sha256:` digest rather than leaving `image:` on a floating tag.

### T5.6 — Update status_check.py `[AGENT]`
- **Depends on:** T5.5
- **Action:** add an llm-gateway row to whatever service table the
  script prints. Inspect file structure first.
- **Acceptance:** script run shows llm-gateway in output.

### T5.7 — Update stack-map skill + reference `[AGENT]`
- **Depends on:** T5.6
- **Action:** edit `.claude/skills/stack-map/SKILL.md:75` and
  `.claude/skills/stack-map/references/workspace-stacks.md`:
  - Add `llm-gateway`, `llm-gateway-db`, `llm-gateway-backup` to Main
    core plane list.
  - Add new rows to the planes/containers table with host port 4000 +
    network `llm-net`.
  - Add `llm-gateway-db-data` to the Volumes list.
  - Update the Cross-stack dependency order (around line 137) — insert
    step `2.5: llm-gateway-db → llm-gateway` between current steps 2
    and 3.
- **Acceptance:** `/stack-map` skill output (if runnable) includes
  llm-gateway.

### T5.8 — Update CLAUDE.md `[AGENT]`
- **Depends on:** T5.7
- **Action:** edit `CLAUDE.md`:
  - Line 15: insert `llm-gateway`, `llm-gateway-db`, `llm-gateway-backup`
    into the "core" parenthetical for the Main stack.
  - Line 20: mention "(also wait for `llm-gateway` healthy before OB1
    consumers)."
- **Acceptance:** both edits visible in `git diff CLAUDE.md`.

### T5.9 — Update copilot-instructions.md `[AGENT]`
- **Depends on:** T5.8
- **Action:** edit `.github/copilot-instructions.md:37-38` — add a new
  arrow row `llm-gateway ← (all callers)` ABOVE the existing
  llama-cpp / llama-cpp-embed arrows.
- **Acceptance:** diff visible.

### T5.10 — Update little-coder design docs `[AGENT]`
- **Depends on:** T5.9
- **Action:** in each file in Guide §16.6 Category F for little-coder
  (`Self-improving-little-coder-design.md`, `integration-plan.md`,
  `integration-tasks.md`), rewrite mentions of "talks to llama-cpp"
  to "talks to the gateway, which routes to llama-cpp." **Preserve**
  references to llama-cpp's `/slots` endpoint — slot-occupancy gating
  still polls llama-cpp directly per Guide §15.2.
- **Acceptance:** grep for `http://llama-cpp:8080` in these files returns
  only the slot-polling references.

### T5.11 — Update OB1 integration docs `[AGENT]`
- **Depends on:** T5.10
- **Action:** in `documentation/Systems-of-structured-data/INTEGRATION-PLAN.md`
  and `INTEGRATION-TASKS.md`, rewrite `LLM_BASE_URL→llama-cpp` to
  `LLM_BASE_URL→llm-gateway` per Guide §16.6.
- **Acceptance:** grep clean.

### T5.12 — Update auth security audit doc `[AGENT]`
- **Depends on:** T5.11
- **Action:** edit `documentation/implementation-guide/open-source authentication front ends for ai stack/plan-internet-exposed-front-end.md`
  per Guide §16.6 — add `llm-gateway` to the "must NOT be
  internet-exposed" lists at lines 94, 196, 1147, 1452.
- **Acceptance:** grep for `llm-gateway` in this file returns ≥ 4 matches.

### T5.13 — Update unified_openwebui_pipe.py COMMAND LIST docstring `[AGENT]`
- **Depends on:** T5.12
- **Action:** edit `scripts/ai_pipes/unified_openwebui_pipe.py` lines
  36, 61–62, 86 (header docstring) — add `llm-gateway`, `llm-gateway-db`
  to the core services and tailnet-served lists.
- **Acceptance:** docstring grep includes llm-gateway.

### T5.14 — Exercise recovery script `[AGENT]`
- **Depends on:** T5.1, T5.2 (the actual script changes)
- **Action:** run a dry-run-equivalent — bring down `llm-gateway`,
  `mnemory`, then invoke recovery:
  ```powershell
  docker compose stop llm-gateway mnemory
  .\scripts\emergency-recovery.ps1 recover
  ```
- **Acceptance:** both containers return to healthy; recovery script
  log shows correct ordering (`llm-gateway-db` → `llm-gateway` → `mnemory`).

### T5.15 — Commit Phase 5 work `[AGENT]`
- **Depends on:** T5.14
- **Action:**
  ```powershell
  git add scripts/ modules/system-health/ .claude/ CLAUDE.md .github/ documentation/
  git commit -m "[litellm] phase 5: recovery scripts, stack-map, docs"
  ```
- **Acceptance:** commit exists.

---

## Phase 6 — Soak + sign-off (operator-driven, ≥ 7 days)

### T6.1 — Operator: ad-hoc usage during soak `[OPERATOR]`
- **Depends on:** T5.15
- **Action:** operator uses the stack normally for at least 7 days.
  No agent action required.
- **Acceptance:** time elapses; spend-log accumulates.

### T6.2 — Day-7 capacity-planning queries `[AGENT]`
- **Depends on:** T6.1
- **Action:** run all queries from Guide §15.3 against the spend-log
  Postgres; capture results to a new file
  `documentation/LiteLLM-Proxy/baseline-week-1.md`.
- **Acceptance:** file exists and contains all five query outputs as
  markdown tables.

### T6.3 — Cap-tuning recommendation `[AGENT]`
- **Depends on:** T6.2
- **Action:** based on §15.3 query outputs, propose cap adjustments
  (raise where saturation never observed; lower where 429 storm
  observed). Output a markdown table comparing §15.4 starting values
  to recommendations. Do **not** apply automatically.
- **Acceptance:** recommendation table in
  `baseline-week-1.md`.

### T6.4 — Operator applies cap adjustments `[OPERATOR]`
- **Depends on:** T6.3
- **Prompt:**
  > Capacity-planning data from week 1 attached at
  > `documentation/LiteLLM-Proxy/baseline-week-1.md`. Cap-tuning
  > recommendations are in §3 of that doc. Approve and apply, or skip
  > if defaults look fine.
- **Acceptance:** operator decision recorded.

### G5 — Final sign-off `[GATE]`
- **Depends on:** T6.4
- **Prompt to operator:**
  > Final sign-off checklist:
  > - [ ] No regressions in caller user-facing behavior
  > - [ ] Spend-log has data from every caller (no silent caller)
  > - [ ] Dark-traffic query clean (only gateway IP in llama-cpp logs)
  > - [ ] Recovery script run from T5.14 worked
  > - [ ] Cap-tuning applied or explicitly skipped
  >
  > Reply "signoff" to close the integration branch.
- **Acceptance:** operator replies "signoff."

### T6.5 — Merge integration branch `[AGENT]`
- **Depends on:** G5
- **Action:**
  ```powershell
  git checkout main
  git merge --no-ff feature/litellm-proxy-integration -m "[litellm] integration complete — see documentation/LiteLLM-Proxy/"
  git branch -d feature/litellm-proxy-integration
  ```
- **Acceptance:** `main` contains the merge commit; integration branch
  is deleted (locally). Operator pushes manually per workspace git
  etiquette.

---

## Appendix A — Agent error handling

When any task fails:
1. **Stop immediately.** Do not attempt subsequent tasks.
2. **Capture the failure**: command run, exit code, last 50 lines of
   stderr/stdout, container state if relevant.
3. **Surface to operator** with:
   - Task ID
   - Failure mode (one sentence)
   - Captured detail
   - Rollback action from the plan doc §6 rollback decision tree
4. **Wait for instruction.** Do not retry, do not improvise.

The integration is designed for per-task revertibility; partial state
is recoverable from the prior commit. There is never a reason to push
forward through an unknown error.

## Appendix B — Tasks that intentionally span phases

A few activities have no natural phase home:

- **Optional Tailnet `/llm-gateway` path (Guide §16.4)** — agent does
  not execute autonomously; deferred to a separate operator decision.
  When ready, follow `entrypoint.sh:229-297` as the template for the
  llama-cpp serve path and add a parallel `setup_llm_gateway_serve`
  function.
- **Per-caller retry-loop patches to upstream repos (T4.7)** — branches
  exist post-T4.7 but not pushed. Operator schedules upstream PR cycles.
- **Phase 2 dependency on Phase 4** — the pipe module added in Phase 4
  is helpful for verifying Phase 3 traffic flow; if running in
  full-cutover mode, consider running Phase 4 between Phase 2 and 3 so
  the operator has the `llm traffic` view to validate each Phase 3
  substep.
