# Claude Code ⟷ Mattermost bridge — design

**Status:** DESIGN (outbound LIVE, inbound not built) · 2026-07-04
**Scope:** ai-stack first; expansion to other repos on this machine theorized in §9.
**One line:** let the operator get pinged by, and remotely re-engage, *this* Claude Code
session from the same Mattermost surface they already watch the agent-org fleet from —
so "close the laptop, steer from your phone" works for the frontier coding agent, not
just the little-coder workers.

---

## 1. Motivation

Today the operator watches two things in Mattermost: the **agent-org fleet** (PO/PM →
little-coder workers) and — after this session — a `#claude-code` channel that Claude
Code posts to. The loop we want to close:

```
Claude Code finishes a turn ──▶ pings #claude-code            (OUTBOUND ✅ built)
operator (anywhere) replies in #claude-code ──▶ Claude Code picks it up,
   acts in the repo, and posts back                            (INBOUND  ⬅ this doc)
```

The value is **surface unification and mobility**. The operator is already in
Mattermost governing the fleet. Adding Claude Code to that surface means one place to
approve/steer *everything* — the local small-model workers **and** the frontier agent —
and it works from a phone. This is the same governance philosophy as agent-org
(Human → agents, pause-until-cleared), applied to the agent the operator pairs with
directly.

Think of it as: **agent-org, but the worker is Claude Code (a frontier agent on the
operator's own machine) instead of little-coder (a small local model in a container).**
See §8 for how the two relate and when to reach for which.

---

## 2. What already exists (OUTBOUND — built this session)

- **Channel:** `#claude-code` (id `qqq97fwxd3f8ufenjybrf5w1yr`) on the self-hosted
  Mattermost (`http://localhost:8065`, same server agent-org uses).
- **Notifier:** [`scripts/notify-mattermost.sh`](../../../scripts/notify-mattermost.sh) —
  reads the bot token from `agent-org/docker/.env` **at run time** (never committed),
  JSON-encodes the message, `POST`s to the channel, and is best-effort (a down
  Mattermost never fails the caller). Proven end-to-end.
- **Trigger (awaiting operator approval):** a Claude Code **`Stop` hook** that runs the
  notifier when a turn ends. Editing the agent's own startup config was — correctly —
  gated by the auto-mode classifier as self-modification, so the operator applies it
  themselves. Exact snippet in §10 / handed over in chat.

Outbound is a plain fire-and-forget POST. Inbound is the hard part, because it means
**a remote message causes code to execute on the operator's machine** — that is the
whole governance problem, addressed in §7.

---

## 3. Inbound — problem statement

> An operator message in `#claude-code` (or a project channel) should feed into a
> Claude Code agent bound to the ai-stack repo, which acts under a bounded permission
> model and posts its reply — and any approval requests — back to the same thread.

What "good" looks like:

1. **Continuity** — a Mattermost thread maps to a *persistent* Claude Code session, so
   context (files read, prior turns) carries across messages. Not a fresh agent per
   message.
2. **Bounded authority** — a remote message can never do more than a curated,
   pre-declared set of actions; anything irreversible or outward-facing **stops for an
   explicit in-thread approval**, mirroring agent-org's hard gate.
3. **Only the operator** can drive it (sender allow-list).
4. **Auditable** — every remote-driven turn and every tool action is logged.
5. **Fails safe** — if the bridge/session is down, nothing silently executes; the
   operator just doesn't get a reply.

---

## 4. Candidate mechanisms

Research into current Claude Code capabilities (docs.claude.com, 2026-07) surfaced three
viable paths. Summary of the trade-offs:

| Mechanism | What it is | Continuity | Governance fit | Effort | Verdict |
|---|---|---|---|---|---|
| **Channels MCP** (`claude/channel`) | Native feature (v2.1.80+, research preview): an MCP server declaring the `claude/channel` capability **pushes inbound messages into a *live* session** and sends replies via a `reply` tool. Started with `claude --channels mcp:./mattermost-channel.mjs`. | Native — it's the operator's *actual running session*. | **Best.** Uses Claude Code's own permission modes + auto-mode classifier + a built-in **permission-relay** (approval prompts can be forwarded to the channel for remote approve). Sender allow-list is part of the channel protocol. | Low–med — build one MCP server (Mattermost socket → channel events); the official Telegram/Discord plugins are a working template. | ✅ **Recommended for ai-stack (Phase 1).** |
| **Agent SDK bridge** | A long-running service (Python/TS) that embeds the Claude Agent SDK, subscribes to Mattermost, and calls `query(prompt, resume=session_id)` per message. This is the **agent-org-shaped** path — a `claude-code-bridge` service analogous to `agent-bridge`. | Explicit — you store `session_id` per thread and pass `resume=`. | Strong but **DIY** — you implement the approval UX and allow-list yourself (no built-in permission relay). Full control over routing, multi-repo, per-channel isolation. | Med–high — a new service + session store + its own governance layer. | ✅ **Recommended for always-on / multi-repo (Phase 3).** |
| **Headless `-p` loop** | Poll Mattermost, shell out to `claude -p "<msg>" --resume <id> --output-format stream-json` per message. | On-disk sessions via `--resume`; but each message is a new process. | Weak — stateless between invocations, permission prompts block a non-interactive process; you must pre-authorize via `--permission-mode`/`--allowedTools`, losing interactive gating. | Low to start, brittle to run. | ⚠️ Fallback / prototyping only. |

(Remote Control — `claude remote-control` → claude.ai/code / mobile — was evaluated and
**rejected for this**: it lets *the operator* steer their session from another device,
but it doesn't let an *external service* inject messages, and it routes through
Anthropic's cloud rather than the operator's own Mattermost. It's a parallel, simpler
"I want my session on my phone" answer, not the Mattermost integration.)

### Why Channels first, SDK later

The **Channels MCP** path gives the most governance for the least new surface: it drives
the operator's *real* Claude Code session, inherits every permission mode and the
auto-mode classifier that already gated me this session, and has a **native permission
relay** — the exact "stop and ask the human" primitive agent-org spent effort building,
already wired for remote approval. For ai-stack (one repo, the operator's own machine,
one session) that is the right first cut.

The **Agent SDK bridge** becomes worth its weight when we want *always-on* (a session
that survives the operator closing their terminal) and *multi-repo routing* (one service
fronting Claude Code sessions for many repos) — i.e. when Claude Code stops being "the
agent I'm pairing with right now" and becomes "a fleet member like the little-coders."
That's Phase 3 and it reuses agent-org's Mattermost adapter and governance patterns
almost verbatim (§8).

---

## 5. Recommended architecture

### Chosen inbound model (operator, 2026-07-04): **thread = session**

Each Claude Code session is represented as **one Mattermost thread** in `#claude-code`
(1:1, bidirectional): the operator posts a message in a thread → it is fed into that
thread's session; the session's output posts **back into the same thread when the turn
is ready to send** (final result, not token-by-token streaming).

This is deliberately the **bridge** shape, not the in-session channel plugin — because
"a thread *per* session" means *many* sessions, and the native `claude/channel`
capability only pushes into the *one* session it's loaded in. A thin external
**`claude-code-bridge`** owns the mapping instead:

```
#claude-code
  ├─ thread A  ⟷  session A        bridge keeps:  { thread_root_id → session_id }
  └─ thread B  ⟷  session B
```

- **Root post in the channel** → start a new session, capture its `session_id`, store
  `thread_root_id → session_id`.
- **Reply in a thread** → **resume** that thread's session
  (`claude -p "<msg>" --resume <session_id>`, or the SDK's `resume=`).
- **Turn completes** → post the final result (and any approval request) into that thread.

Properties this buys: **async** (a turn can take minutes; the reply lands "when ready"),
**restart-survivable** (sessions live as on-disk JSONL keyed by `session_id`; the bridge
only persists the small thread→id table), **isolated** (each thread is its own context,
no cross-talk), and **multi-session** (independent threads run independently). Because
Claude Code runs on the frontier API (not local GPU), concurrent threads don't contend
for local inference — the only limiters are operator attention and API cost.

This reframes the §4 verdict on headless: for *live interactive* chat `claude -p` is a
poor fit, but for this *async request→run→reply* model **`claude -p --resume` is a
natural fit** — a v1 can be built on it directly; the Agent SDK is the productionized
version of the same loop (and the path to mid-turn approval, §7).

### (single-session alternative) a Mattermost **channel** MCP server

```
 Mattermost  #claude-code (thread = session)
      │  socket (operator messages)          ▲  reply tool + permission relay
      ▼                                       │
 ┌─────────────────────────────┐   claude/channel   ┌──────────────────────────┐
 │  mattermost-channel.mjs      │◀──────────────────▶│  claude  (running in the │
 │  (MCP server, this repo)     │   channel events    │  ai-stack repo, started  │
 │  · Mattermost WS client      │                     │  with --channels mcp:… ) │
 │  · sender allow-list         │                     │  · normal permission mode│
 │  · maps thread → nothing     │                     │  · auto-mode classifier  │
 │    (session IS the session)  │                     │  · Stop hook → outbound  │
 └─────────────────────────────┘                     └──────────────────────────┘
```

- The operator runs Claude Code in the ai-stack repo as they do now, adding
  `--channels mcp:./tools/mattermost-channel.mjs` (or a `channels` entry in settings).
- The MCP server holds a Mattermost WebSocket connection, filters to `#claude-code`
  messages **from the operator's user id only**, and emits them as channel events.
- Claude reads the event, works under its normal permission model, and calls the
  channel's `reply` tool to post back. Approval prompts (irreversible actions) are
  **relayed to the thread** — the operator approves from Mattermost.
- Outbound (§2) keeps working unchanged: the Stop hook still pings when a turn ends.

This is a **single new file** (the MCP server) plus a launch flag — no new container, no
change to the agent-org project.

### Phase 3 (fleet / multi-repo): a `claude-code-bridge` service

When we want always-on and many repos, promote to an SDK service — see §8/§9. It is a
new compose service in (or beside) the agent-org project that owns Claude Code sessions
the way `agent-bridge` owns little-coder workers.

---

## 6. Session-continuity model

**Committed mapping: Mattermost thread ⟷ Claude Code session, 1:1 (see §5).** The bridge
keeps a `thread_root_id → session_id` table. The first (root) message in a thread starts
a session and captures its `session_id` (from `-p --output-format json`, or the `init`
`SystemMessage` under the SDK); every reply resumes it (`--resume` / SDK `resume=`).
Sessions persist as JSONL on disk, so they survive bridge restarts — the bridge only has
to persist the small thread→id table and owns retention/cleanup. Compaction is Claude
Code's own, transparent to Mattermost.

Guideline: **thread = unit of context.** One thread per line of work; a new thread when
starting something unrelated — exactly how the operator already uses effort-threads in
agent-org.

---

## 7. Safety & governance model (the crux)

Inbound = a remote message triggers local code execution. The model below layers
Claude Code's native controls with agent-org's governance philosophy. Defense in depth,
fail-closed.

1. **Authentication — only the operator.** The channel/bridge hard-filters to the
   operator's Mattermost user id (and the private `#claude-code` channel). A message
   from anyone else is dropped and logged. No pairing token = no delivery.

2. **Bounded authority — permission mode + allow-list floor.** The remote-driven session
   runs in an explicit, conservative mode — **not** `bypassPermissions`. Options, least
   → most autonomous: `default`/`plan` (reads/propose only) · `acceptEdits` (edits, no
   arbitrary Bash) · `auto` (everything *with* the background classifier) · `dontAsk`
   (only pre-approved `allowedTools`). Recommended start: **`default` with an explicit
   `allowedTools` floor** (Read/Glop/Grep/Edit/Write scoped to the repo; Bash gated),
   and require the operator to opt into anything broader per-thread.

3. **Hard gate on irreversible / outward actions.** Mirror agent-org's
   `Trigger.irreversible_action`: pushes, force-push, deploys, deletions, credential
   sends, `git commit --amend` on pushed commits, tunnels — **stop and post an approval
   request to the thread**; execute only on an explicit `approve`. Under the thread =
   session model this lands two ways: **turn-boundary** (headless — a gated tool is
   denied by a `PreToolUse` hook that tells Claude to ask; Claude ends the turn
   *requesting* approval; the operator's `approve` reply resumes the session and it
   proceeds) or **mid-turn** (SDK — a `canUseTool` callback pauses the running turn,
   posts the request, and awaits the in-thread reply before continuing). Turn-boundary
   ships first; mid-turn is smoother (§11). The auto-mode classifier backstops both —
   it already blocks download-and-execute, mass deletion, prod deploys, force-push,
   secret exfiltration, and writes to `.git`/`.claude`.

4. **The auto-mode classifier as a backstop, not the only wall.** It is the same
   research-preview classifier that (correctly) blocked me from editing my own `Stop`
   hook this session. Keep it on for remote sessions — but treat it as the *last* line,
   with the allow-list floor (2) and hard gate (3) in front.

5. **Auditability.** Every remote-driven turn: log `{who, thread, prompt, session_id,
   tools_used, approvals}`. Phase 1 leans on Claude Code's own session JSONL + the
   channel server's message log; Phase 3 writes to the bridge's audit store like
   agent-org does.

6. **Scope containment.** Phase 1 is bound to the **ai-stack repo** the session was
   started in — it cannot roam to other repos. Broadening scope is a deliberate Phase 3
   decision with its own gate (§9), not an accident of configuration.

7. **Fail-closed.** Bridge down, token missing, classifier unavailable → **no
   execution**. The operator simply gets no reply (same posture as the outbound
   notifier, inverted): silence over unsupervised action.

**Non-negotiables carried from the workspace:** the token is read from `.env` at run
time and never committed; nothing routes inference around LiteLLM; no model-health
probing; irreversible actions never fire from fuzzy NL without an explicit `approve`.

---

## 8. Relationship to agent-org

This is deliberately the **same shape** as agent-org, one layer out:

| | agent-org | claude-code bridge |
|---|---|---|
| Worker | little-coder (small local model, in a container) | Claude Code (frontier agent, on the host) |
| Driver | `agent-bridge` (FastAPI) | Phase 1: Claude Code itself via a channel MCP · Phase 3: `claude-code-bridge` |
| Governance | PO/PM + hard gates + pause-until-cleared | permission modes + hard gate + permission relay (§7) |
| Surface | Mattermost project/effort threads | Mattermost `#claude-code` / project threads |

**Reuse:** the Mattermost adapter (WS + REST), the comms/routing model, and the
governance-gate pattern from `agent-bridge` all transfer to a Phase 3 SDK bridge nearly
verbatim. The channel-MCP (Phase 1) reuses less code but *more* of the philosophy.

**When to reach for which worker:**
- **little-coder** — bounded, well-specified coding tasks the fleet runs autonomously
  and cheaply on local inference; the operator governs many in parallel.
- **Claude Code (this bridge)** — open-ended design/architecture work the operator
  pairs on directly, wants frontier reasoning for, and wants to steer conversationally
  from anywhere.

They are complementary, not competing: the operator could even use the Claude Code
session to *drive agent-org* (draft plans, dispatch little-coders) from their phone.

---

## 9. Expansion beyond ai-stack (theorized)

Phase 1 is one repo, one session. Generalizing to "any repo on this machine":

- **Repo registry** — reuse agent-org's `projects` concept: a table of
  `{slug → local path, remote}`. A message names or is routed to a repo.
- **Channel-per-repo** — `#cc-<repo>` channels (or a thread convention) so the operator
  picks the target by *where* they post, exactly like effort-threads pick an effort.
- **Routing** — a Phase 3 `claude-code-bridge` fronts N Claude Code sessions (one per
  repo), maps `channel/thread → repo → session`, and enforces the §7 model per session.
- **Blast-radius note** — multi-repo widens what a remote message can touch. Mitigations:
  per-repo permission floors (a scratch repo may allow more than ai-stack), per-repo
  audit, and keeping the hard gate (§7.3) global. Broadening from ai-stack to another
  repo is an explicit, logged enrollment step — never implicit.

The clean end-state: the operator opens Mattermost, posts in `#cc-<whatever>`, and a
frontier agent bound to that repo — governed identically to the fleet — does the work
and reports back. ai-stack is the proving ground for that pattern.

---

## 10. Build phases

- **P-CCB.0 — Outbound.** ✅ Built this session. Notifier script proven; **Stop hook
  awaiting operator approval** (snippet below).
- **P-CCB.1 — Inbound (ai-stack, thread-per-session bridge on headless).** Build the
  thin `claude-code-bridge`: Mattermost WS → operator-only allow-list → `thread_root_id
  → session_id` table → `claude -p "<msg>" --resume <id>` in the ai-stack repo → post
  the final result back to the thread. Root post starts a session; replies resume it.
  Run under `default` mode + an `allowedTools` floor; gated actions surface as a
  turn-boundary approval request (§7.3). Deliver: reply-from-phone, one thread per line
  of work.
- **P-CCB.2 — Governance hardening.** Formalize the §7 hard-gate list and the
  approve/abort UX in-thread (the `PreToolUse`-hook gate); wire audit logging; document
  the permission floor.
- **P-CCB.3 — Productionize + fleet/multi-repo (SDK bridge).** Move the bridge onto the
  Agent SDK for **mid-turn** approvals (`canUseTool`) and long-lived hosted sessions;
  add a repo registry + channel-per-repo routing — the agent-org-shaped, always-on
  generalization (§8/§9).

**The Stop hook to approve (P-CCB.0), added to `.claude/settings.local.json` as a
sibling of `permissions` — and add that file to `.gitignore`:**

```json
"hooks": {
  "Stop": [
    { "hooks": [ {
      "type": "command", "shell": "bash", "timeout": 15,
      "command": "bash \"d:/Open WebUI/ai-stack/scripts/notify-mattermost.sh\""
    } ] }
  ]
}
```

---

## 11. Open decisions (for the operator to steer)

1. **Phase-1 permission floor** — start read-only (`default`/`plan`, propose-only) and
   opt into edits per-thread, or start at `acceptEdits` (edits auto, Bash/push gated)?
   *(Recommendation: `default` + explicit allow-list; loosen once trust is established.)*
2. ~~**Session model**~~ — **DECIDED (2026-07-04): thread = session, 1:1** (§5/§6).
3. **Dedicated bot identity** — reuse the agent-org bot for `#claude-code`, or a separate
   `claude-code` bot so fleet and pairing traffic are visually distinct?
4. **Approval mechanism** — **turn-boundary** (headless v1: gated tool denied → Claude
   asks → operator `approve` resumes) or **mid-turn** (SDK: `canUseTool` pauses the turn
   and awaits the reply)? *(Recommendation: turn-boundary for P-CCB.1, mid-turn at
   P-CCB.3.)* And: reuse agent-org's `approve <id>` idiom, or a plain in-thread "yes"?
5. **When to move to the SDK (P-CCB.3)** — stay on the headless bridge while it's one
   repo, or jump to the SDK sooner because mid-turn approvals / always-on / multi-repo
   are wanted?

---

*Sources: Claude Code Channels + channels-reference, CLI reference (headless/flags),
Remote Control, permission-modes, and Agent SDK docs (docs.claude.com, retrieved
2026-07-04). Official Telegram/Discord channel plugins in
`anthropics/claude-plugins-official` are the template for the Mattermost channel server.*
