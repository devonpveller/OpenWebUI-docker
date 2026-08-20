# Communication Model — deterministic intent → destination routing

**Status:** 📐 **spec refinement** (2026-07-02). Adopted after real-world channel-sprawl feedback
during the live P5 bring-up. **This doc SUPERSEDES the channel *shape* in PLAN §5.2 and refines
how comms land on the platform (governance §7).** It does **not** alter governance §1–§6 (roles,
the escalation gate, the ladder, observability) — it *implements* them deterministically.
**Precedence unchanged:** governance spec > this doc > PLAN/TASKS. Where governance §3/§5 constrain
(mandatory up-level, pause-until-cleared, bus-only/observable), this doc obeys and makes them concrete.

**Companions:** [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)
(§3 gate, §5 org-constraints, §7 platform) · [PLAN §5.2](PLAN-teams-chat-agent-orchestration.md)
(the taxonomy this refines) · [UX-FLOW §3–§5](UX-FLOW.md) (CONCERN + ladder) ·
[OUTLINE §4](OUTLINE-teams-chat-agent-orchestration.md) (topic-threading analysis).
**Sources:** [research/core-mental-model-teams-communications.md](research/core-mental-model-teams-communications.md)
(human teams-comms mental model) · [research/ai-org-structures-for-software-producing-companies.md](research/ai-org-structures-for-software-producing-companies.md)
(multi-directional comms; context-oriented mediation) · [ANALYSIS-team-topologies-alignment.md](ANALYSIS-team-topologies-alignment.md)
(stream-aligned channels).

---

## 0. Why this doc exists

Two problems the earlier taxonomy didn't solve:

1. **Channel sprawl.** PLAN §5.2 / P5.5 specified a **channel per effort** (`#effort-<name>`). In
   practice this floods the sidebar: every completed task leaves an orphan channel, and the operator
   ends up hiding channels one by one. Because concurrency is GPU-bounded to **~1–2 workers**
   (PLAN §3.6), only ~1–2 efforts are ever *active* — all the sprawl is *dead* efforts.
2. **No deterministic "where does this go?" rule.** Agent messages (dispatch, activity, escalation,
   decision, suggestion) were routed ad-hoc. The design wants coordination to be **deterministic in
   the bridge** (governance §3.5) — including *where each message lands*.

The human teams-comms research gives the missing primitive: **audience × intent deterministically
selects the destination.** Translating it to our (more-observable) agent org solves both problems and
converges with our own OUTLINE (topic/thread-per-effort = "best for cross-effort awareness") and Team
Topologies (a stream-aligned channel per area). Three independent sources, one answer.

---

## 1. The core model (adapted for agents)

> Every communication answers two questions — **who needs to see this? (audience)** and **what
> decision/action does it drive? (intent)** — and the answer *deterministically* selects the
> destination. (research: *core-mental-model-teams-communications*.)

**The agent-org divergence — it collapses to fewer surfaces.** A human org has four privacy tiers
(public channel / private channel / group chat / DM). Governance §5 **forbids private/opaque agent
channels** (bus-only, observable, "the Human Operator can read/join anything"). So for *agents*, three
tiers collapse:

- **DMs & group chats** (private human coordination) → become **threads on the observable bus**; the
  bridge mediates what a human would do privately. Agents get **no** 1:1 privacy.
- **Private channels** → the only semi-private space is **`#mgmt`** (the human ⇄ PO steering channel —
  the research's "steering committee" pattern, and governance §7's designated space).
- Everything else defaults **public** (project channels + threads).

> **Net principle: an agent org is *more* public than a human org, by design.** The research's Golden
> Rule ("default to public; privacy is the exception") taken to its logical end **is** our
> observability = safety requirement (governance §5). The human model reaches our safety posture from
> the usability side.

---

## 2. The deterministic routing table (intent → destination)

The bridge owns this table; it is the concrete form of "coordination lives in the bridge" (§3.5).

| Intent | Who needs it | Destination (deterministic) | Governance anchor |
|--------|--------------|-----------------------------|-------------------|
| Operator **request** / PO clarify / plan present | you + PO | **`#mgmt`** | §1, UX-FLOW St.0–3 |
| Effort **dispatch** + worker **activity/progress** | project (observable) | **thread in `#proj-<slug>`** | §5 observability |
| Worker **blocked / peer concern** (lateral) | up the ladder | effort **thread**, `@mention` the PM | §4.4, §4.8 |
| **CONCERN** needing a decision | you + PO | surfaced to **`#mgmt`** (+ freezes effort) | §3 |
| Operator **decision** (approve/modify/abort) | recorded | **`#mgmt`** *and* **echoed back down** into the effort thread | §3 |
| **Suggestion** / learning | everyone | **`#suggestions`** | §6 |
| **Incident** (wake-storm, undeliverable, crash) | time-boxed | **`#incidents`** | §5 caps |
| Effort **closure** (done / aborted) | followers | effort **thread** (+ summary to `#mgmt`) | §3, "bring back down" |

**Reading the table:** *audience* picks the surface (`#mgmt` = you+PO; project channel = the work;
function channels = incidents/suggestions); *intent* picks whether it's a record (public), a decision
(surfaced to `#mgmt`), or a delegation (dispatched to a thread). No agent message is routed by vibe.

---

## 3. The flow rules (deterministic behaviors the bridge enforces)

Four rules, lifted from the research and reconciled with governance §3:

1. **Escalation ladder — "move up when it outgrows the current audience."** worker → PM → PO → Human
   (governance §1/§3). The research makes the *trigger* crisp: escalate when the current rung **cannot
   resolve it**, not by discretion. A lateral concern (§4.8) is *raised* in the effort thread and
   *routed up* — never resolved privately.
2. **"Decisions happen in private; decisions get *recorded* in public."** A CONCERN is **decided** in
   `#mgmt` (steering), but its **outcome is recorded** in the effort thread + the audit trail
   (governance §5). Deliberation and record live in different places, deterministically.
3. **"Always bring the audience back down."** ⭐ *New behavior* the current build lacks: when a CONCERN
   is cleared, the resolution is **echoed back into the originating effort thread** so anyone who
   followed the work gets **closure**. This is the difference between an org that feels coherent and
   one where escalations vanish. It also satisfies governance §3 ("the decision propagates down and
   unfreezes; it is logged").
4. **Default public; privacy is the exception** = observability = safety (§5). Only `#mgmt` is
   semi-private (you ⇄ PO). Agents never get an opaque channel.

---

## 4. Taxonomy — channel = project, effort = thread (supersedes PLAN §5.2)

| Layer | Maps to | Count | Lifetime |
|-------|---------|-------|----------|
| **`#mgmt`** | you ⇄ PO — requests, steering, CONCERN decisions, completion summaries | 1 | permanent |
| **`#proj-<slug>`** | one **stable channel per project** (repo/product) the org works on | few | project lifetime |
| **effort / task** | a **root post + thread** inside its project channel; all effort activity threads under it | unbounded | thread goes inactive when done |
| **`#incidents`** | time-boxed operational events (wake-storm, crashes) | 1 | permanent |
| **`#suggestions`** | worker suggestion pool → learning loop (§6) | 1 | permanent |

**Why this kills sprawl:** the sidebar shows `#mgmt` + a handful of project channels + `#incidents`/
`#suggestions` — and **never grows with task volume**. Completed efforts become inactive *threads*, not
orphan channels. The operator is a member of the project channel once, so there's **no per-task invite
problem** — they follow the threads they care about (Collapsed Reply Threads). This is the standard
Teams/Slack shape and matches the research's `[category]/[project]-[stream]` convention.

**Fit with the existing architecture (not a rewrite):** the bridge already maps
**`{channel, thread} ⇄ {effort, session}`** (OUTLINE §5; `SessionMap` is keyed on `thread_id`). An
effort simply becomes `(project_channel_id, root_post_id)` instead of a fresh channel; the little-coder
`--session <id>` stays the effort id. Worker activity posts as **replies** (`root_id = root_post_id`).

**Project resolution:** an effort belongs to a project = its repo. v1 (throwaway/sandbox) uses a single
default project channel (`#proj-sandbox`); when a real repo is set (`AO_DEFAULT_REPO` or per-effort),
the channel is `#proj-<repo-slug>`. One project channel per repo the org touches.

---

## 5. Reconciliation with the spec (what changes, what doesn't)

| Doc | Statement | Status after this doc |
|-----|-----------|----------------------|
| governance §1–§6 | roles, gate, ladder, grounding, observability, learning loop | **unchanged** — this doc *implements* them |
| governance §5 | bus-only, observable, no opaque agent channels | **reinforced** ("more public by design") |
| governance §7 | "work efforts = channels; error hand-offs = threads" | **refined**: efforts = **threads** in a project channel; the "= channels" was the seed of the sprawl |
| PLAN §5.2 / TASKS P5.5 | `#effort-<name>` per effort | **superseded**: `#proj-<slug>` per project + thread per effort |
| OUTLINE §4 | topic/thread-per-effort "best for cross-effort awareness" | **promoted** from runner-up rationale to the chosen shape |
| UX-FLOW §3/§4 | intent-framed CONCERN + ladder | **unchanged** — routed per §2/§3 here |

> **Precedence guard:** nothing here weakens the escalation gate, the mandatory up-level, or the
> pause-until-cleared fail-safe. If any routing choice ever conflicts with governance §3/§5, governance
> wins and this doc is corrected.

---

# PART B — Implementation plan

**Status: ✅ BUILT + tested 2026-07-02** (73 agent-bridge tests green; +8 for the comms model).
Build record in [`agent-org/IMPLEMENTATION-NOTES.md`](../../../agent-org/IMPLEMENTATION-NOTES.md)
("Comms model (CM.1–CM.6) — BUILT"). **Operator step:** rebuild + restart `agent-bridge` to pick
up the taxonomy; the DB migration is additive + self-healing (no manual ALTER).

Phases **CM.1 → CM.6** (Comms Model). This is a **delta** over the *built* system (effort = channel,
worker streaming, `#mgmt` summaries, operator-added-to-channels). Status keys mirror TASKS:
⬜ todo · ✅ done · 🧪 needs test · 🚀 operator. Target paths are the live modules.

> **Build order rationale:** CM.1 (thread taxonomy) is the load-bearing refactor; CM.2 (the router)
> centralizes routing so CM.3–CM.5 are config, not scattered edits; CM.4 (bring-back-down) is the
> highest-value *behavior* gap. CM.6 is polish.

### CM.1 ✅ — Project channels + effort-as-thread (supersede per-effort channels)
- ⬜ Add project resolution: `effort → project → channel` (`#proj-<slug>`; default `#proj-sandbox`).
  → `agent-org/agent-bridge/app/modules/router.py`, `config.py` (`default_project` / repo-slug).
- ⬜ Replace `ensure_effort_channel` with `ensure_project_channel(project)` + `open_effort_thread(effort)`:
  post an **effort-card root post** (`🧵 Effort: <name> — goal … · status: active`) in the project
  channel; its post id = the effort's `thread_id`. Persist `(project_channel_id, root_post_id)` on the
  effort/`SessionMap`.
- ⬜ Route all effort activity (dispatch, worker command stream, answer, review) as **thread replies**
  (`thread_id = root_post_id`) via the existing `router.wake` streaming callback.
- ⬜ Add the operator to the **project channel** once (not per effort) — reuse `chat.add_member`.
- *Done-when:* two efforts in the same project appear as **two threads in one `#proj-<slug>` channel**;
  no new channel is created per effort; the sidebar count is stable across many tasks.

### CM.2 ✅ — Deterministic comms router (the §2 table as one primitive)
- ⬜ New `modules/comms_router.py`: `resolve(intent, effort_id=…, …) -> (channel_id, thread_id|None)`
  implementing the §2 table. All posting flows (orchestrator, router, gate, learning loop) call it
  instead of choosing channels inline.
- ⬜ Intents enumerated: `operator_reply`, `effort_dispatch`, `worker_activity`, `escalation`,
  `concern`, `decision`, `suggestion`, `incident`, `closure`.
- *Done-when:* a unit test asserts each intent resolves to the destination in the §2 table; no module
  posts to a hard-coded channel outside the router.

### CM.3 ✅ — Escalation ladder + CONCERN routing (worker → PM → PO → Human)
- ⬜ A worker that ends non-`done`, hits a **floor/git-proxy denial**, or reports a block → the bridge
  posts an **escalation** in the effort thread `@mention`-ing the PM (lateral raise, §4.8), and if it's
  a §3 hard-gate trigger, raises a **CONCERN to `#mgmt`** (existing `gate.freeze` + `raise_concern`).
- ⬜ The CONCERN post **links back** to the effort thread (permalink) so the decider has context.
- *Done-when:* an injected worker failure surfaces as a thread escalation **and** (for a hard-gate case)
  a `#mgmt` CONCERN that references the effort thread; the effort is frozen (gate unchanged).

### CM.4 ✅ — "Bring the audience back down" — decision closure ⭐
- ⬜ On `gate.clear` (operator decision), the bridge **echoes the resolution into the originating effort
  thread** ("✅ Operator approved — resuming" / "⛔ Aborted") in addition to the `#mgmt` + audit records.
  → `orchestrator.apply_operator_decision`.
- ⬜ Update the effort-card root post status (`active → frozen → active/done/aborted`) — needs
  `chat.update_post` (see CM.6).
- *Done-when:* clearing a CONCERN posts closure to the effort thread; a follower of that thread sees the
  outcome without opening `#mgmt`.

### CM.5 ✅ — Function channels (`#incidents`, `#suggestions`)
- ⬜ Ensure `#incidents` + `#suggestions` exist (create-or-get on boot); add the operator.
- ⬜ Route `learning.add_suggestion` posts to `#suggestions`; route wake-storm / undeliverable-wake /
  crash notices to `#incidents` (currently DB-only / `#mgmt`).
- *Done-when:* a worker suggestion appears in `#suggestions`; a tripped wake-storm cap posts to
  `#incidents` (and still freezes per §3).

### CM.6 ✅ — Effort-card status + polish (optional)
- ⬜ Add `ChatAdapter.update_post(post_id, message)` (Mattermost `PUT /posts/{id}`; Fake records it) so
  the effort-card root post reflects live status; pin the card optionally.
- ⬜ Throttle worker-activity streaming (batch rapid commands) if a task is command-heavy, to keep
  threads readable (the research's "notification discipline").
- *Done-when:* the effort-card shows current status; a command-heavy task doesn't flood the thread.

### R (3-place change) — none new
- No new **containers** (this is bridge-internal). Compose / recovery / stack-map unchanged. Only the
  `agent-bridge` image is rebuilt. Record the taxonomy change in `IMPLEMENTATION-NOTES.md`.

---

## 6. Sources
- research/core-mental-model-teams-communications.md — audience × intent → feature; escalation ladder;
  decision-flow; "bring the audience back down"; default-public.
- research/ai-org-structures-for-software-producing-companies.md — multi-directional comms (down/lateral/
  up), context-oriented mediation, operational visibility.
- OUTLINE §4 (topic-threading), ANALYSIS-team-topologies-alignment (stream-aligned channels),
  SAFETY-AND-WORKFLOW-governance-model §3/§5/§7.
