# Integration Plan — LiteLLM Proxy

**Source of truth:** [`guide-LiteLLM-Proxy.md`](guide-LiteLLM-Proxy.md). Every
decision and architectural choice in this plan is anchored there; if a
conflict surfaces during execution, the guide wins and this plan is updated.

**Companion document:** [`integration-tasks-LiteLLM-Proxy.md`](integration-tasks-LiteLLM-Proxy.md)
— the granular, agent-executable checklist that this plan groups into phases.

**Cutover date target:** to be set by operator at Gate G0 (see §3).

---

## 0. Transparent-mode execution (2026-06-12) — READ FIRST, supersedes §1–§6

The architecture changed to **transparent interposition** (guide **§1A**). The
phased per-caller cutover in §3–§4 below is **superseded** — there is no
per-caller migration. Use the phases here as the execution path of record; the
old phases are retained as the fallback if the Phase-T0 spike fails.

**The pivot in one line:** rename the two real inference servers to `*-upstream`,
give the LiteLLM gateway the network aliases `llama-cpp` + `llama-cpp-embed` on
`:8080`, run it permissive. Every caller (current, future, and the five
un-inventoried ones from guide §1A.1) is proxied with **zero caller edits**.

### Phases (transparent)

| Phase | What | Autonomy | Ends with |
|---|---|---|---|
| **T0 — Pre-checks (optional)** | Offline scratch test of permissive-mode key logging (guide §1A.3) + §19 digest resolve. **Optional** — the T3 live canary subsumes the logging check under real load with an invisible rollback (guide §1A.8). Run T0 only if you want the LiteLLM-config fact confirmed at zero outage before the canary. | Full | GT0 (optional) |
| **T1 — Backups + branch + .env** | OWUI/OB1/mnemory backups; branch; `.env` (`LITELLM_DB_PASSWORD` only — no per-caller keys) | Full | GT1 (DB password; master key only if enforcing later) |
| **T2 — Standup (no aliases yet)** | Add `llm-gateway` + `llm-gateway-db` digest-pinned + **permissive**, `--port 8080`, config api_base → the *current* `llama-cpp`/`llama-cpp-embed`. Verify it serves **every** model id (guide §6) and **writes a spend-log row carrying the presented key**. Nothing routes through it yet. | Full | GT2 (operator confirms ledger + model coverage) |
| **T3 — THE FLIP, as a live canary (one commit)** | In a maintenance window: rename `llama-cpp`→`llama-cpp-upstream`, `llama-cpp-embed`→`llama-cpp-embed-upstream`; point gateway api_base at `*-upstream`; add gateway aliases `llama-cpp`+`llama-cpp-embed`; **repoint observability refs** (health, gpu_status, status_check, tailscale `LLAMA_CPP_HOST`, emergency-recovery) to `*-upstream`. `compose up -d --remove-orphans` and **watch the live services** (guide §1A.8). The transparency makes this safe: **any issue → `git revert` + `compose up` brings the originals back and no caller ever knew.** Worst case for a harder problem: the same revert. | Mixed | GT3 (operator eyeballs the flip commit before recreate) |
| **T4 — Verify + soak** | All callers now transparently proxied: confirm the ledger fills from multiple callers, every healthcheck passes, probes hit `*-upstream`. Soak on source-key/IP attribution. | Operator-driven | None |
| **T5 — Lazy keys (optional, ongoing)** | Flip caller key env vars one at a time (guide §1A.4) for clean attribution. Optionally, once all callers carry real virtual keys: enable `master_key`, issue keys, apply §15.4 caps. | Per-caller, additive | None |
| **T6 — Pipe module + recovery + docs** | `llm-traffic` module (§9); three-place rule (recovery scripts + stack-map) including the `*-upstream` renames; Category-F docs | Full | None |

The operator can **stop after T4** with the full ledger (the §1/§15 goal) and
zero per-caller work. T5 (clean per-key attribution) and T6 (pipe UI + docs) are
additive.

### Gate semantics (transparent)

| Gate | Agent must NOT, without approval |
|---|---|
| GT0 | Build anything before the permissive-logging spike is proven (or the per-caller fallback is chosen) |
| GT1 | Generate/use the DB password (and master key, only if enforcing) until the operator provides it |
| GT2 | Proceed to the flip before the standup gateway demonstrably serves every model id **and** logs the presented key |
| GT3 | Run the rename+alias+repoint recreate before the operator has eyeballed the single flip commit |

### Rollback (transparent)

Every phase boundary is stable. The flip (T3) is **one git commit**; rollback is
`git revert` of it + `docker compose up -d --remove-orphans` — the aliases move
off the gateway and the original `llama-cpp`/`llama-cpp-embed` reclaim their
names. **Because no caller config ever changed, this rollback is invisible to
every caller** (guide §1A.8) — which is what makes running the flip as a live
canary safe. No per-caller unwinding. If the (optional) T0 pre-check **or** the
T3 canary shows permissive logging doesn't work / the integration misbehaves
unfixably, the per-caller plan (§3–§4) is the documented fallback path.

---

## 1. Purpose & "one-shot" honesty

This plan is structured so an autonomous agent can drive the *codeable*
portion of the cutover end-to-end without operator hand-holding, then stop
cleanly at a small number of human gates for the things that genuinely
require a human (admin-UI clicks, irreversible decisions, secret
generation). Each gate is named, numbered, and has an explicit "what the
operator must do" block.

**This is not "one shot, walk away."** It is "one shot with five planned
pauses." The pauses exist because:

- LiteLLM virtual keys are credentials — they require operator review
  before being written to `.env` files.
- OWUI's connection settings live in a database, not a file; only the
  admin UI can update them safely.
- The `Retry-After` retry-loop patches for `openbrain-entity-worker` and
  `openbrain-wiki` are upstream code changes that should be reviewed
  before being committed.
- The dark-traffic acceptance test (§17.9 of the guide) is a judgement
  call about whether anything was missed.
- The soak period is by definition operator time, not agent time.

Anything that isn't behind one of those gates is fair game for the agent.

## 2. Agent / Operator division of labor

| Role | What it does | Where it appears |
|---|---|---|
| `[AGENT]` | Reads files, edits files, runs `docker compose` commands, runs `curl` to the gateway admin API, writes the spend-log verification queries, runs grep-based smoke tests, commits to git | Most tasks |
| `[OPERATOR]` | Approves the cutover at G0; provides `LITELLM_MASTER_KEY` + `LITELLM_DB_PASSWORD` at G1; clicks through OWUI Admin at G3; confirms dark-traffic clean at G4; signs off after soak at G5 | Five gates |
| `[GATE]` | A named pause point. Agent MUST stop and ask the operator before proceeding. No exceptions. | G0–G5 |

The agent's default behavior on uncertainty: **stop and ask**, never
improvise. This is a production stack with valuable data — better a slow
cutover than a fast restore-from-backup.

## 3. Phases overview

> **⚠️ SUPERSEDED by §0 (transparent mode).** The per-caller phases below (heavy-
> hitter cutover, remaining callers, OWUI UI flips) are **not executed** in
> transparent mode — they are the fallback path used only if the §0 Phase-T0
> permissive-logging spike fails. Backups (Phase 0), gateway standup (Phase 1),
> observability/pipe module (Phase 4), and recovery+docs (Phase 5) carry over
> into the §0 phases. Use §0 as the path of record.

| Phase | What | Agent autonomy | Ends with gate |
|---|---|---|---|
| **0.0 — Pre-flight verification (added in re-audit)** | Run Guide §18 assertions A1–A7: confirm model list, no undiscovered callers, OB1 recipe inventory unchanged, little-coder schema in sync, tailnet sessions reviewed | Full (one operator review at A7) | G-pre (operator approves to begin Phase 0) |
| **0 — Pre-flight backups** | Backups, branch creation, `.env` scaffolding | Full | G1 (master key + DB password) |
| **1 — Standup** | LiteLLM config (with ALL model aliases per re-audit §6), compose additions, **image digest-pin + offline hardening (guide §19)**, gateway up, virtual keys, spend-log verification | Full (after G1) | G2 (review issued keys + pinned digest before they're used) |
| **2 — Heavy-hitter cutover** | little-coder (Python source → schema regen → runtime configs) + OWUI + openbrain-entity-worker | Mixed (file edits + restarts agent-side; OWUI UI flips operator-side) | G3 (OWUI UI flip) |
| **3 — Remaining callers (optional, deferrable)** | mnemory + openbrain-mcp + openbrain-wiki + githelper-pipe + **OB1 operator-run recipes** (gmail + google-activity imports — added in re-audit) | Full | None — additive |
| **4 — Observability** | Pipe module, TPM/RPM caps, retry-loop patches | Mixed (module agent-side; cap values operator-set) | None — additive |
| **5 — Recovery + docs** | emergency-recovery scripts, stack-map, CLAUDE.md, all Category F docs | Full | None |
| **6 — Soak + sign-off** | 7-day baseline, dark-traffic query, capacity-planning queries materialize | Operator-driven | G5 (final sign-off) |

The plan is structured so the operator can **stop after Phase 2** with a
working heavy-hitters-only deployment that delivers most of the value
(per §16.8 of the guide — entity-worker is the dominant load by a wide
margin). Phases 3–6 are additive; none of them is a prerequisite for
production use.

## 4. Phase details

### Phase 0.0 — Pre-flight verification (≈10 min agent + 5 min operator, added in re-audit)

**Goal:** Confirm the codebase state still matches what Guide §16/§18
captured during the audit. Any drift caught here prevents wasted backup
time and unsafe edits.

**Agent actions** (Guide §18.1 A1–A7, implemented as tasks T0.0.1–T0.0.7):
- A1 — `curl http://127.0.0.1:8081/v1/models` returns exactly the four
  expected model IDs (`qwen36-27b`, `qwen36-27b:nothink`,
  `qwen36-35b-a3b`, `qwen36-35b-a3b:nothink`).
- A2 — `curl http://127.0.0.1:8082/v1/models` returns exactly 1 embedding
  model.
- A3 — Repository grep for `http://llama-cpp(-embed)?:8080` returns only
  files listed in Guide §16.1 / §16.7 / §18.2. No new file = no missed
  caller.
- A5 — `OB1/recipes/` grep returns only the two known recipes
  (`email-history-import`, `google-activity-import`).
- A6 — little-coder `python -m littlecoder.config --schema` matches the
  committed `little-coder.schema.json`.
- A7 — Operator reviews `tailscale status` for surprising off-host
  sessions.

**Acceptance criteria:**
- All seven assertions pass without intervention, OR every failure is
  resolved (Guide §6 updated, new caller added to §16.1, schema
  regenerated, etc.) before proceeding.

**Gate G-pre — operator approves to begin Phase 0.** Any A1–A7 failure
that required mid-flight resolution is acknowledged. Operator confirms
readiness to begin backups.

### Phase 0 — Pre-flight backups (≈30 min agent + 5 min operator)

**Goal:** Reach a state where the agent has a clean working tree, backups
exist, and the only thing blocking standup is the operator providing two
secrets.

**Agent actions:**
- Create a new git branch `feature/litellm-proxy-integration` off `main`.
- Run on-demand backup of OWUI data via the existing `openwebui-backup`
  sidecar pattern (one-shot the script, don't wait for the cron).
- Snapshot `mnemory-data` + `little-coder-sessions` volumes to
  `backups/<volume>/pre-litellm-<date>.tar.gz`.
- `pg_dump` the OB1 database to `backups/openbrain/pre-litellm-<date>.sql`.
- Append the new env var stubs from §8.4 to **both** `.env.example` files
  (root and `OB1/docker/.env.example` — create the OB1 one if it doesn't
  exist).
- Create `.env` entries with placeholder values (`__SET_AT_G1__`) so
  later substitution is mechanical.

**Acceptance criteria:**
- `git status` shows the new branch.
- All five backup files exist with non-zero size.
- `.env` files have the new variables present (as placeholders).

**Gate G0 — operator authorizes the cutover.** Operator confirms backups
are real (size, location), that the maintenance window is appropriate,
and that no critical inference workloads are in flight (e.g. a multi-hour
wiki recompile). Agent waits for explicit "proceed."

**Gate G1 — operator supplies secrets.** Operator generates and provides:

- `LITELLM_MASTER_KEY` — `openssl rand -hex 32`
- `LITELLM_DB_PASSWORD` — `openssl rand -hex 32`

Agent receives these via prompt (never paste into the agent's reading
context; agent writes them directly to `.env` and never echoes the
values back).

### Phase 1 — Standup (≈20 min agent)

**Goal:** Gateway and DB are running, healthy, and serving the configured
models. **No callers route through it yet.**

**Agent actions:**
- Write `config/litellm.config.yaml` from §6 of the guide.
- Write `backup/llm-gateway-backup.sh` mirroring `backup/mnemory-backup.sh`.
- Edit `docker-compose.yml`: insert the three new services
  (`llm-gateway`, `llm-gateway-db`, `llm-gateway-backup`) from §8.3 of
  the guide between the existing `llama-cpp-embed` and `smolcrawl-pipelines`
  blocks. Add `llm-gateway-db-data` to the `volumes:` block.
- **Resolve and digest-pin the gateway image (supply-chain hardening, guide
  §19 / D9):** pull `ghcr.io/berriai/litellm:main-stable`, resolve its
  `sha256` digest, and replace the `__RESOLVED_AT_STANDUP__` placeholder in
  the §8.3 `image:` line with `ghcr.io/berriai/litellm@sha256:<resolved>`.
  Confirm the resolved release is outside any known LiteLLM compromise window
  (check the BerriAI/litellm security advisories) before pinning. The §6
  config already sets `telemetry: false` and the §8.3 env sets
  `LITELLM_LOCAL_MODEL_COST_MAP=True`, so the gateway — on the internal-only
  `llm-net` — makes no outbound internet calls.
- `docker compose up -d llm-gateway-db` → wait healthy.
- `docker compose up -d llm-gateway` → wait healthy.
- Run the §17.2 upstream-connectivity checks.
- Run the §17.2 model-list check.
- Generate all virtual keys from §7 via `/key/generate`, **store each
  returned value in memory only**, do not write to `.env` yet.
- Apply the §15.4 starting caps to each key via `/key/update`.
- Issue one test request through `sk-admin`, confirm a `LiteLLM_SpendLogs`
  row appears.

**Acceptance criteria:**
- `docker compose config` for `llm-gateway` shows an `@sha256:` image
  reference (digest-pinned), **not** the floating `:main-stable` tag.
- `docker ps` shows both `llm-gateway` and `llm-gateway-db` healthy.
- `curl -fsS http://127.0.0.1:4000/health/liveliness` returns 200.
- `curl http://127.0.0.1:4000/v1/models -H "Authorization: Bearer
  $LITELLM_MASTER_KEY"` returns `qwen36-27b`, `qwen36-27b:nothink`,
  `qwen36-35b-a3b`, `bge-m3`.
- Test request produces a spend-log row.

**Gate G2 — operator reviews issued keys + pinned digest.** Agent prints the
alias names and metadata of every issued key (NOT the key values), plus the
**pinned image digest** (`docker inspect llm-gateway --format '{{.Image}}'`)
for a supply-chain sanity check. Operator confirms the list is complete, the
metadata matches §7, and the digest is the one they intended (guide §19.3). On
confirmation, agent writes the key values into the appropriate `.env` files
atomically.

### Phase 2 — Heavy-hitter cutover (≈45 min agent + 15 min operator)

**Goal:** The three callers that account for the dominant share of GPU
demand (per the §1 motivation) are now logged in the spend ledger.

**Substep 2A — little-coder (agent-only):**
- Edit `little-coder/config/little-coder.config.yaml:11` → gateway URL.
- Edit `little-coder/config/models.json:6` → gateway URL.
- Edit `little-coder/config/little-coder.schema.json:102, 116` → gateway
  URLs (defaults).
- Edit `docker-compose.yml`: replace `LC_LLAMA_API_KEY=llama` with the
  virtual key reference; change `little-coder.depends_on` from
  `llama-cpp` to `llm-gateway`.
- `docker compose up -d little-coder` → wait healthy.
- Trigger a known-good `lc task` flow.
- Verify spend-log row tagged `sk-lc-coder` appears.

**Substep 2B — openbrain-entity-worker (agent-only):**
- Edit `OB1/docker/docker-compose.yml:224-230` → gateway URL + virtual key.
- `docker compose -f OB1/docker/docker-compose.yml up -d
  openbrain-entity-worker` → wait healthy.
- Trigger drain endpoint (`POST 127.0.0.1:8810/drain` or whatever the
  current HTTP trigger is — agent must inspect the worker's startup logs
  to confirm).
- Verify spend-log rows tagged `sk-ob-entity` appear.

**Substep 2C — OWUI (operator-driven UI clicks):**
- Agent prints the §17.4 checklist with the exact values to enter.
- **Gate G3 — operator performs the OWUI UI flips.** Agent waits.
- Operator returns when 17.4.1 through 17.4.7 are complete.
- Agent runs the §17.4.7 smoke tests and confirms spend-log rows for
  `sk-owui-chat` and `sk-owui-embed`.

**Acceptance criteria:**
- Three new caller aliases appear in `SELECT DISTINCT api_key FROM
  "LiteLLM_SpendLogs" WHERE created_at > <phase-start>`.
- All three callers are still functionally healthy (their existing
  healthchecks pass).
- A dark-traffic spot-check at the end of Phase 2 should show the
  gateway IP + three pre-cutover IPs (mnemory, openbrain-mcp,
  openbrain-wiki) still hitting llama-cpp directly. This is **expected**
  if Phase 3 hasn't run.

**Stopping point:** If the operator chooses to defer Phase 3, the system
is in a stable mixed-mode state. The pipe-module (added in Phase 4) will
need the known-direct-callers allowlist populated; document this in
`integration-tasks-LiteLLM-Proxy.md` task T4.x.

### Phase 3 — Remaining callers (≈30 min agent, optional)

**Goal:** All inference traffic flows through the gateway. The
dark-traffic query in §17.9 returns only the gateway IP.

**Note on mnemory `EMBED_MODEL` change:** Guide §6 now registers
`qllama/bge-m3:latest` as a LiteLLM alias for the bge-m3 upstream, so
this rename is no longer strictly required for cutover safety — mnemory
will work with the original `qllama/bge-m3:latest` value pointed at the
gateway. The rename to `bge-m3` is still preferred for long-term
normalization but can be deferred to a Phase 7 cleanup if it complicates
the cutover window.

**Agent actions, in order (each is one substep, agent restarts the
service and verifies before moving to the next):**
- 3A: `mnemory` — edit `docker-compose.yml:295-299, 313-316`; either
  rename `EMBED_MODEL` to `bge-m3` (preferred) or leave as
  `qllama/bge-m3:latest` (works because §6 registers the alias); restart.
- 3B: `openbrain-mcp` — edit `OB1/docker/docker-compose.yml:57-63`; restart.
- 3C: `openbrain-wiki` — edit `OB1/docker/docker-compose.yml:261-266`; restart.
- 3D: `filters/githelper-pipe.py:117-118` — edit file default; agent
  prompts operator to also update the deployed Valves in OWUI Admin →
  Functions → githelper.
- **3E (re-audit addition):** edit OB1 operator-run recipes —
  `OB1/recipes/email-history-import/pull-gmail.ts:68,70` and
  `OB1/recipes/google-activity-import/import-google-activity.mjs:33,35`
  — file defaults swap to the gateway. No restart required (these are
  ad-hoc scripts, not container services).
- **3F (re-audit addition, optional):** operator decides whether to
  generate per-recipe virtual keys (`sk-ob-recipe-gmail`,
  `sk-ob-recipe-google`) for attribution, or let ad-hoc runs land under
  `sk-admin` / unattributed.

**Acceptance criteria:**
- All four service callers appear in the spend-log within 1 hour of
  restart (mcp and wiki are sparse — may not see traffic immediately;
  operator may need to trigger a known workload for mcp).
- OB1 recipe defaults grep-clean of `llama-cpp(-embed)?:8080`.
- Dark-traffic query returns ONLY the gateway IP.

### Phase 4 — Observability (≈45 min agent + 15 min operator)

**Goal:** Operator-facing surfaces (pipe module + caps) are live. Retry
patches in callers prevent future timeout storms.

**Agent actions:**
- 4A: Scaffold `modules/llm-traffic/` mirroring `modules/gpu-status/`
  (manifest.yaml, service/, tests/) per §9.3.
- 4B: Implement `service/llm_traffic.py` — queries `/spend/logs`,
  `/spend/calculate`, `/key/info`; renders markdown table.
- 4C: Edit `core/router.py` to add the trigger phrases from §9.1.
- 4D: Edit `scripts/ai_pipes/unified_openwebui_pipe.py:302` to add
  `"llm-traffic"` to the module-id allowlist; update the COMMAND LIST
  docstring per §9.1.
- 4E: Restart `openwebui` so the unified pipe reloads.
- 4F: Operator triggers `llm traffic` in OWUI; agent verifies the
  module renders the expected view.
- 4G: Apply retry-loop patches to `openbrain-entity-worker` and
  `filters/githelper-pipe.py` per §17.7. **Each is an upstream code
  change — agent makes the edit, commits to a feature branch, but does
  NOT push without operator approval** (the entity-worker patch goes to
  the OB1 integrations repo, which has its own contribution rules per
  the OB1 CLAUDE.md).

**Acceptance criteria:**
- `llm traffic` trigger in OWUI returns the markdown table.
- `llm caps` trigger returns current TPM/RPM values per key.
- Retry-loop branches exist with commits ready for review.

### Phase 5 — Recovery scripts + documentation (≈30 min agent)

**Goal:** The three-place rule from CLAUDE.md is satisfied; no documentation
contradicts the deployed reality.

**Agent actions per §16.5 and §16.6:**
- Edit `scripts/emergency-recovery.ps1`: add new services to
  `MainStackServices`, insert into startup/shutdown sequences.
- Mirror in `scripts/emergency-recovery.bat`.
- Edit `scripts/quick-fixes.bat` menu + helpers.
- Edit `modules/system-health/service/system_health.py:38-39, 93` — add
  llm-gateway probe.
- Edit `scripts/update-stack.bat` — add llm-gateway update menu item.
- Edit `scripts/status_check.py` — add llm-gateway row.
- Edit `.claude/skills/stack-map/SKILL.md:75` and
  `.claude/skills/stack-map/references/workspace-stacks.md` — add new
  rows + dependency order + volume.
- Edit `CLAUDE.md:15, 20` — add llm-gateway to core plane + bring-up note.
- Edit `.github/copilot-instructions.md:37-38` — add llm-gateway arrow.
- Edit each documentation file in Category F per the line numbers in
  §16.6 of the guide.
- Exercise `scripts/emergency-recovery.ps1 recover` once to verify the
  new ordering brings the gateway up correctly.

**Acceptance criteria:**
- `/stack-map` skill output includes llm-gateway.
- `system health` pipe trigger probes the gateway successfully.
- One successful recovery script run.
- Doc grep `Grep -r "llama-cpp:8080" documentation/` returns only the
  guide doc (which references it for historical context).

### Phase 6 — Soak + sign-off (≥7 days, operator-driven)

**Goal:** Real-world workloads accumulate; baseline saturation rate
becomes measurable; the capacity-planning queries from §15.3 return
useful data.

**Operator actions:**
- Day-7 review: run the §15.3 queries, log results to
  `documentation/LiteLLM-Proxy/baseline-week-1.md` (new file, operator-owned).
- Adjust TPM/RPM caps from §15.4 starting values based on observed
  traffic.
- Schedule Phase 2 work for any optional tightening (Prometheus
  dashboards from §13, additional retry patches, etc.).

**Gate G5 — final sign-off.** Operator confirms:
- No regressions in any caller's user-facing behavior.
- Spend-log has data from every caller (no silent caller).
- Dark-traffic query is clean.
- Recovery script run during Phase 5 worked.

On G5 pass, the agent closes the integration branch into `main` and
deletes the `feature/litellm-proxy-integration` branch.

## 5. Gate semantics

| Gate | What the agent must NOT do without operator approval |
|---|---|
| G0 | Begin any non-backup work |
| G1 | Generate or use master key / DB password until operator provides them |
| G2 | Write virtual keys to `.env` (the keys are credentials; operator reviews the alias→metadata mapping first) |
| G3 | Skip ahead past OWUI substep 2C while operator is doing the UI flip |
| G5 | Merge the branch back into `main` |

Gates are **stop-and-prompt**. The agent prints the gate's prompt text
verbatim and waits for an explicit go-ahead. "Proceed", "go", "approved"
are valid; anything ambiguous is treated as "stop."

## 6. Rollback decision tree

The plan's strength is per-caller revertibility. Failure modes and
responses:

| Failure | Decision | Action |
|---|---|---|
| Phase 1 standup fails (gateway won't come up) | Revert | `docker compose down llm-gateway llm-gateway-db -v`; revert the compose-yml edits; investigate; retry. No callers were affected. |
| Phase 2 substep fails (caller can't reach gateway) | Per-caller revert | Revert the one env var change for that caller; restart it; investigate. Other callers + the gateway remain in their post-step state. |
| Phase 2 OWUI UI flip causes chat breakage | Operator-driven revert | Operator restores the previous OWUI connection values noted in §17.1; hard refresh. No agent action needed. |
| Phase 3 causes mnemory dimensions mismatch (the §17.8 `EMBED_MODEL` rename was missed) | Per-caller revert + fix | Revert mnemory env; fix the `EMBED_MODEL` value; re-apply. |
| Phase 4 pipe module crashes openwebui pipe loader | Revert pipe-yml | Revert the unified_openwebui_pipe.py allowlist change; restart openwebui. Gateway + cutover remain in place. |
| Soak reveals systematic latency regression (>10% above baseline) | Investigate before reverting | Compare LiteLLM `/metrics` against the llama-cpp `/metrics` baseline. Likely culprit: a missed `drop_params: true` or a malformed model alias. Patch in place. |
| Catastrophic (data loss in `llm-gateway-db`) | Restore from backup | Phase 0 backups + LiteLLM's spend-log is non-authoritative for any business state, so worst case: lose telemetry history, no functional impact. |

At any point during execution, the agent can be instructed to "rollback
to <phase boundary>." The plan is structured so that every phase boundary
is a stable state — there is no half-step where the system is broken
with no escape.

## 7. References

- Source-of-truth: [guide-LiteLLM-Proxy.md](guide-LiteLLM-Proxy.md)
- Task checklist: [integration-tasks-LiteLLM-Proxy.md](integration-tasks-LiteLLM-Proxy.md)
- Workspace conventions: [`../../CLAUDE.md`](../../CLAUDE.md) (three-place rule, git etiquette)
- Stack map: [`../../.claude/skills/stack-map/references/workspace-stacks.md`](../../.claude/skills/stack-map/references/workspace-stacks.md)
- Comparable prior integration patterns: [`../web-search/integration-plan-private-search-gateway.md`](../web-search/integration-plan-private-search-gateway.md), [`../little-coder/integration-plan.md`](../little-coder/integration-plan.md)
