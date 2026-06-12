# Integration Plan — Autonomous Updates with a Security & Impact Gate

**Source of truth:** [`guide-autonomous-updates.md`](guide-autonomous-updates.md).
Every decision here is anchored there; if a conflict surfaces during execution,
the guide wins and this plan is updated.

**Companion:** [`integration-tasks-autonomous-updates.md`](integration-tasks-autonomous-updates.md)
— the granular, agent-executable checklist this plan groups into phases.

---

## 1. "One-shot with planned pauses" honesty

As with the LiteLLM integration, this is **not** "build it and walk away." The
pipeline grants a new service `docker.sock` and the ability to recreate
containers — but **every apply STOPS for a human go-ahead** (guide §D8/§D9).
There is no autonomous-apply stage. The system ships in **research + notify +
STOP** mode (guide §13/S0); the apply path lights up only once a human-approval
channel is wired (interim manual approval → teams-chat). The agent builds the
codeable portion; the operator owns every secret, the policy manifest, and every
apply go-ahead.

The whole system delivers value at S0 (research + claims + email, zero apply
risk) before any apply path exists. Apply, when built, edits the **original
git-tracked files** and rolls back with `git revert` (guide §D7).

## 2. Agent / Operator / Gate division

| Role | What it does |
|---|---|
| `[AGENT]` | Builds the orchestrator + mailer, writes the policy manifest scaffold, reconfigures watchtower, wires research/curator/Gmail clients, runs the P0 spike, updates recovery + stack-map + docs |
| `[OPERATOR]` | Bootstraps the Gmail token (G2); approves watchtower reconfiguration (G3); sets/owns the `managed`/`never` policy manifest (G4); confirms the verdicts are trustworthy + the apply/rollback drill is sound before the apply path goes live (G5); **gives the go-ahead for every individual apply** (interim now, teams-chat later) |
| `[GATE]` | Named stop-and-prompt pause; agent prints the prompt verbatim and waits |

Default on uncertainty: **stop and ask**. This pipeline can mutate the
production stack — a slow, gated rollout beats a fast one.

## 3. Prerequisites

Before Phase 0:

- The **digest-pin convention must be in place** for any service intended for
  management (guide §D11). Services still on floating tags can be *detected* but
  cannot be *safely rolled back*; the LiteLLM §19 digest-pin work is the
  template. Phase 0 audits this.
- `openbrain-research` + `openbrain-curator` are healthy and reachable
  (`GET /health` on each). The gate is inert without them.
- The `open-brain-email` OAuth client exists (it already backs the daily digest)
  and can mint a new `gmail.send` token for the orchestrator.

## 4. Phases

| Phase | What | Autonomy | Ends with |
|---|---|---|---|
| **0 — Spike + pre-flight** | Validate the watchtower-vs-digest-pin detection question (guide §9); confirm research/curator reachable; inventory which services are digest-pinned | Full | G1 (spike result reviewed) |
| **1 — Mailer + research/curator clients** | Build the Gmail sender (copy daily-digest pattern), the research client, the curator client; unit-test each against live `/health` | Full (G2 for token) | G2 (operator bootstraps Gmail token) |
| **2 — Orchestrator core (research + notify + STOP)** | The `update-orchestrator` service: `/hook`, resolve→research→verdict→claim→email. **Applies nothing.** Policy manifest scaffold (all `managed`, no apply path yet) | Full | None |
| **3 — Watchtower as detector** | Switch watchtower to monitor-only + notify the orchestrator (or wire the `tag-digest-detector` fallback per the P0 result) | Mixed | G3 (operator approves watchtower reconfig) |
| **4 — Soak / S0 (research + notify, no apply)** | Real update events flow through; emails + claims accrue; **no applies** — this is the permanent posture until an approval channel exists | Operator-driven | G4 (operator trusts verdicts; sets `managed`/`never` manifest) |
| **5 — Apply engine + human-approval gate (S1, interim approval)** | Build the apply path: backup → **edit original file → git commit** → recreate → health-gate → **git-revert** on fail. Apply fires **only on an explicit human go-ahead** (interim manual trigger, e.g. `POST /approve/<run>`). Still never auto. | Mixed | G5 (verdicts + apply/rollback drill reviewed) |
| **6 — Recovery + docs (three-place rule)** | emergency-recovery, stack-map, system-health probe, CLAUDE.md | Full | None |
| **7 — Teams-chat approval channel (S2)** | Swap the interim manual go-ahead for the **governed teams-chat** approval (D10); message security updates for awareness. The end-state delivery channel — not a new autonomy level. | Mixed | depends on `../teams-chat-agent-orchestration/` being built |

The operator can **stop after Phase 4** with a fully-useful system: it watches
for updates, researches every one, records grounded claims, and emails a
pass/stop verdict with a pre-filled manual bump command — autonomous
*research + notification*, manual *apply*. Phases 5–7 add the **human-approved**
apply path (the human is always in the loop; only the approval *channel* and the
apply *mechanics* are automated).

## 5. Phase detail

### Phase 0 — Spike + pre-flight
**Goal:** Resolve the one genuine unknown before building anything.
- Determine empirically whether watchtower (monitor-only) reports an available
  update for a **digest-pinned** container, or treats it as pinned-and-skipped
  (guide §9). Pick the trigger mechanism accordingly (watchtower-native vs
  `tag-digest-detector` fallback).
- Confirm `openbrain-research` + `openbrain-curator` `GET /health` are green and
  that a trivial `POST /research` returns a job id and completes.
- Inventory: which in-scope services are digest-pinned today (only `llm-gateway`
  + `portal-alerter` after the LiteLLM work) vs floating-tag. Output the
  candidate management set.
- **G1 — operator reviews the spike result** and confirms the trigger approach.

### Phase 1 — Mailer + clients
**Goal:** Three tested, reusable clients before the orchestrator wires them.
- `mailer/` — copy `GoogleOAuth` + `GmailClient` from daily-digest; point at a
  new `secrets/google/update-orchestrator/` token path; `setup-token.ts` clone.
- `research-client` — `POST /research` + poll, `x-brain-key` auth, bounded wait.
- `curator-client` — `POST /ingest/research-package` with the §7 claim shapes.
- **G2 — operator bootstraps the Gmail token** (runs `setup-token.ts` once on
  the host, moves `token.json` into `secrets/google/update-orchestrator/`).
  Mirrors the daily-digest bootstrap. *(Note: publish the OAuth app to
  Production to avoid the 7-day refresh-token expiry — `daily-digest-oauth-7day-expiry`.)*

### Phase 2 — Orchestrator core (research + notify + STOP)
**Goal:** The full gate runs end-to-end but **applies nothing** — there is no
apply code path yet.
- Build the `update-orchestrator` service (guide §10): `/hook`, `/check/<svc>`,
  `/health`, `/runs`; docker.sock mount; obnet + narrow Gmail egress; run-log
  volume.
- Implement resolve → 2 research jobs → verdict step (llama-cpp) → ingest claims
  → email. Decision is **recorded + emailed only** ("would apply, awaiting human
  go-ahead" / "blocked" / "needs-human").
- `config/update-policy.yaml` scaffold — every in-scope service `managed`, DBs +
  locally-built `never` (guide §11), with usage profiles for the candidate set.
- Add the service to `docker-compose.yml`; bring it up; `/check/<svc>` a
  digest-pinned service by hand and confirm a real email + claim land.

### Phase 3 — Watchtower as detector
**Goal:** Real update availability auto-triggers the gate.
- Per the P0 result: either set `WATCHTOWER_MONITOR_ONLY=true` +
  `WATCHTOWER_NOTIFICATION_URL=generic://update-orchestrator:PORT/hook` and
  relax managed services' labels to `watchtower.monitor-only=true`; **or** ship
  the `tag-digest-detector` inside the orchestrator on a poll loop.
- Keep `enable=false` for out-of-scope (`never`) services.
- **G3 — operator approves the watchtower reconfiguration** (it changes a
  long-standing "auto-update everything off" posture into "detect + notify").

### Phase 4 — Soak / S0 (research + notify, no apply)
**Goal:** Trust-building on real events, zero apply risk. This is the permanent
posture until the apply path (Phase 5) is built and the operator chooses to
enable it.
- Let real update events flow for a soak period. Emails + claims accumulate.
- Operator reviews verdict quality: are `clear`/`blocked`/`needs-human` calls
  sound? Any false `clear`? Any noisy `needs-human`?
- **G4 — operator confirms verdicts are trustworthy and authors the real
  `managed`/`never` policy manifest** (scope + usage profiles). No apply path
  exists yet; nothing can be applied regardless.

### Phase 5 — Apply engine + human-approval gate (S1, interim approval)
**Goal:** A **human-approved** apply path — never autonomous.
- Implement the apply engine (guide §12): pre-apply backup → **edit the managed
  digest in the original git-tracked file → `git commit`** → recreate one
  service → health-gate → **`git revert`** on failure → email outcome → update
  claim.
- The apply fires **only on an explicit human go-ahead.** Interim channel: a
  `POST /approve/<run-id>` operator action (or an email-reply token). One
  approval = one apply; nothing fires without it.
- Replicate the `post-update-hook.sh` tailscale-reattach for `S=openwebui`.
- **G5 — operator reviews verdict quality + a forced apply/rollback drill**
  (force a health failure, confirm clean `git revert` to the prior digest)
  before the apply path is allowed to act on real approvals.

### Phase 6 — Recovery + docs
**Goal:** Three-place rule satisfied; no doc contradicts reality.
- `emergency-recovery.{ps1,bat}` inventory + ordering (orchestrator last up,
  early down; must not trigger applies during a recovery).
- stack-map skill + reference; `modules/system-health` probe; CLAUDE.md row;
  copilot-instructions.

### Phase 7 — Teams-chat approval channel (S2)
**Goal:** Move the human go-ahead from the interim manual trigger to the
**governed teams-chat** system. Still human-approved — only the delivery channel
changes.
- When `../teams-chat-agent-orchestration/` is built: implement the `teams-chat`
  approval sink (D10) — the verdict + staged change are posted into the governed
  chat for a human go/no-go; on approval the same Phase-5 apply engine runs.
  Security-motivated updates are messaged for awareness.
- Not started until that system exists; the guide reserves the pluggable-sink
  seam so this is an adapter, not a rewrite. An operator who wants no apply
  before teams-chat simply never enables the Phase-5 interim approval and waits
  for this phase.

## 6. Gate semantics

| Gate | Agent must NOT, without approval |
|---|---|
| G1 | Build anything before the trigger mechanism is decided |
| G2 | Generate/use the Gmail token until the operator bootstraps it |
| G3 | Reconfigure watchtower (changes the stack's update posture) |
| G4 | Bring any service into scope (`never` → `managed`) without the operator |
| G5 | Let the apply path act on real approvals before a reviewed apply + `git revert` drill |
| (every apply) | Apply **any** update without an explicit per-apply human go-ahead — this is not a one-time gate but a standing rule (D8/D9) |

Gates are **stop-and-prompt**. "Proceed"/"approved" advance; ambiguity = stop.
The per-apply go-ahead is never waived — there is no "trusted enough to skip the
human" state.

## 7. Rollback / failure posture

| Failure | Response |
|---|---|
| Spike inconclusive (P0) | Default to the `tag-digest-detector` fallback; do not depend on unverified watchtower digest behavior. |
| Research or curator unreachable mid-run | Gate **fails closed**: `needs-human` + hold + email. Never auto-`clear`. |
| Orchestrator crash-loops | It applies nothing in S0; in S1 a crash mid-apply leaves a clean git state (commit-before-recreate, guide §10) — recover via emergency-recovery; the run log + `git log` are the audit trail. |
| Human-approved update unhealthy | Auto `git revert` to prior digest (guide §12); email + claim record the rollback. |
| Watchtower reconfig regresses something | Revert watchtower env to the prior `enable=false` posture; the orchestrator falls back to manual `/check`. |
| Orchestrator corrupts an original file | The edit is committed as a discrete git revision before recreate (guide §D7/§10); `git revert`/`git checkout` restores it; never an uncommitted partial edit. |

Every phase boundary is a stable state; S0 (Phases 2–4) is itself a complete,
shippable product with no apply risk.

## 8. References

See [`guide-autonomous-updates.md`](guide-autonomous-updates.md) §18.
