# Integration Plan — LiteLLM Proxy

**Source of truth:** [`guide-LiteLLM-Proxy.md`](guide-LiteLLM-Proxy.md). Every
decision and architectural choice in this plan is anchored there; if a
conflict surfaces during execution, the guide wins and this plan is updated.

**Companion document:** [`integration-tasks-LiteLLM-Proxy.md`](integration-tasks-LiteLLM-Proxy.md)
— the granular, agent-executable checklist that this plan groups into phases.

**Cutover date target:** to be set by operator at Gate G0 (see §3).

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

| Phase | What | Agent autonomy | Ends with gate |
|---|---|---|---|
| **0 — Pre-flight** | Backups, branch creation, `.env` scaffolding | Full | G1 (master key + DB password) |
| **1 — Standup** | LiteLLM config, compose additions, gateway up, virtual keys, spend-log verification | Full (after G1) | G2 (review issued keys before they're used) |
| **2 — Heavy-hitter cutover** | little-coder + OWUI + openbrain-entity-worker | Mixed (file edits + restarts agent-side; OWUI UI flips operator-side) | G3 (OWUI UI flip) |
| **3 — Remaining callers (optional, deferrable)** | mnemory + openbrain-mcp + openbrain-wiki + githelper-pipe | Full | None — additive |
| **4 — Observability** | Pipe module, TPM/RPM caps, retry-loop patches | Mixed (module agent-side; cap values operator-set) | None — additive |
| **5 — Recovery + docs** | emergency-recovery scripts, stack-map, CLAUDE.md, all Category F docs | Full | None |
| **6 — Soak + sign-off** | 7-day baseline, dark-traffic query, capacity-planning queries materialize | Operator-driven | G5 (final sign-off) |

The plan is structured so the operator can **stop after Phase 2** with a
working heavy-hitters-only deployment that delivers most of the value
(per §16.8 of the guide — entity-worker is the dominant load by a wide
margin). Phases 3–6 are additive; none of them is a prerequisite for
production use.

## 4. Phase details

### Phase 0 — Pre-flight (≈30 min agent + 5 min operator)

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
- `docker ps` shows both `llm-gateway` and `llm-gateway-db` healthy.
- `curl -fsS http://127.0.0.1:4000/health/liveliness` returns 200.
- `curl http://127.0.0.1:4000/v1/models -H "Authorization: Bearer
  $LITELLM_MASTER_KEY"` returns `qwen36-27b`, `qwen36-27b:nothink`,
  `qwen36-35b-a3b`, `bge-m3`.
- Test request produces a spend-log row.

**Gate G2 — operator reviews issued keys.** Agent prints the alias names
and metadata of every issued key (NOT the key values). Operator confirms
the list is complete and the metadata matches §7. On confirmation, agent
writes the key values into the appropriate `.env` files atomically.

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

**Agent actions, in order (each is one substep, agent restarts the
service and verifies before moving to the next):**
- 3A: `mnemory` — edit `docker-compose.yml:295-299, 313-316`; verify the
  `EMBED_MODEL` rename from `qllama/bge-m3:latest` → `bge-m3` (critical —
  see §17.8); restart.
- 3B: `openbrain-mcp` — edit `OB1/docker/docker-compose.yml:57-63`; restart.
- 3C: `openbrain-wiki` — edit `OB1/docker/docker-compose.yml:261-266`; restart.
- 3D: `filters/githelper-pipe.py:117-118` — edit file default; agent
  prompts operator to also update the deployed Valves in OWUI Admin →
  Functions → githelper (since the file default doesn't override an
  already-deployed pipe's stored config — this is a small operator
  micro-action, not a full gate).

**Acceptance criteria:**
- All four callers appear in the spend-log within 1 hour of restart
  (mcp and wiki are sparse — may not see traffic immediately; operator
  may need to trigger a known workload for mcp).
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
