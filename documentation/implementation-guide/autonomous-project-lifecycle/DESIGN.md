# Autonomous Project Lifecycle — orchestration owns the whole project, you approve & steer

**Status:** 🛠️ IN BUILD. 2026-07-04. Operator steer applied: **personal account**, **full granular
capabilities**, **mounted key file**. **P-APL.0 DONE + App VERIFIED live** (`code-agent-automation` on
`devonpveller`). **P-APL.1a FORK live + validated** (devonpveller/murder). **P-APL.1b COMPOSE live** — operator-plane
git executor (little-coder `/project/submodule` daemon endpoint, 512 tests) + `compose` capability
(bridge, governed hard-gate, 188 tests): "compose <engine>" adds the registered fork-projects as
submodules to an operator-created repo, via a short-lived App token. **Decision:** personal account +
operator creates empty repos by hand (so `create_repo` skipped). Next: live-validate compose, then
P-APL.2 (advisor→Plan) + P-APL.3 (plan executor).

**What this adds:** today the `agent-org` orchestration can do *engineering work inside one existing
repo* (a worker clones a focused repo, edits, commits, pushes). It cannot **create the project
structure itself** — fork/create repos, compose several repos, wire multi-repo builds — and it cannot
**execute a plan the advisor produced**. This design closes both gaps so the orchestration can
**automate the full lifecycle of a project**: from "here's what I want" → scaffold the repos → build
it → maintain it, with the **human as governor only** (approve / steer / clear hard-gates), writing no
code.

**Thesis (restating the corpus North Star).** The governance model
([SAFETY-AND-WORKFLOW](../teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md) §1)
already casts the Human Operator as *final authority who sets the request, approves plans, and clears
hard-gate triggers* — not an implementer. "100% automation, the user only approves and steers" is not
a new goal; it is the design's thesis. What's missing is **capability**, not **governance**: the
org lacks the *primitives* to act on structure, and an *executor* to run a multi-step plan. The
governance machinery to keep those primitives safe already exists and is reused wholesale.

**Precedence.** This doc **defers to** the governance spec (governance > PLAN > TASKS). Where it grants
new powers, it slots them into the *existing* §3 escalation gate — it does **not** invent a parallel
safety story.

---

## 1. The core principle: a governed **Capability plane**, distinct from the worker sandbox

The reason the worker can't fork/create/submodule today is **deliberate and correct** (governance
§2.1 F7 — *do not rely on model alignment to carry safety*): a free-form agent must not hold
irreversible, outward-facing powers. The resolution is **not** to loosen the worker sandbox. It is to
put these powers in a **separate plane** that is the opposite of free-form:

| Plane | Who acts | Nature | Powers | Gate |
|---|---|---|---|---|
| **Worker plane** (exists) | little-coder agent (LLM, free-form) | non-deterministic, sandboxed | edit/build/commit/push **inside one focused repo**; git-proxy-policed; no egress | code-level review/dry-run |
| **Capability plane** (NEW) | the **bridge** — deterministic Python handlers | fixed, auditable, no LLM improvisation | fork / create-repo / add-submodule / compose / set-upstream — via GitHub App + real git | **§3 hard-gate**: irreversible/outward ⇒ **human approves before execute** |

The distinction that makes this safe: **a capability is a fixed function the bridge runs, not a prompt
an agent interprets.** `fork_repo(parent, owner)` does exactly one thing, the same way every time,
and it only runs *after* the human clears the hard-gate. The agent can *propose* "we should fork
murder," but it cannot *perform* the fork — it has no code path to. This is the same pattern the
daemon already uses for `add_upstream_remote` / `clone` (operator-setup actions that run **real git**,
bypassing the worker's git-proxy) — generalized into a first-class, governed layer.

> **Why this doesn't reintroduce the paper's "less aligned" risk (arXiv:2604.10290).** The paper's
> danger is *emergent* agent behavior at scale. Capabilities are the antithesis of emergent: a closed
> set of deterministic functions, each hard-gated on the human for anything irreversible, each
> audited. Adding capability to the *governed* plane increases what the org can *do* without
> increasing what any *agent* can do unsupervised.

---

## 2. Credentials — a **GitHub App**, not a PAT (also fixes deferred Bug 5b)

Creating/forking repos needs privileged GitHub access. A long-lived admin **PAT** would be the worst
case for the at-rest-token concern already on record (the deferred "Bug 5b"). A **GitHub App** avoids
it entirely and is the industry standard for automation:

- **You register** a GitHub App (owned by you), **install** it on your account/org, selecting which
  repos it may touch. Installation is revocable in one click.
- **The durable secret is the App private key** — held as a bridge secret (env/mounted file, never in
  git, never in a worker). The private key *alone cannot act*; it only signs a request to **mint an
  installation access token**.
- **Installation tokens are short-lived (~1h), per-installation, and scoped** to the selected repos +
  granted permissions (`Administration: write` for create/fork, `Contents: write`, `Metadata: read`).
  The bridge mints them on demand and lets them expire.
- **Nothing privileged sits at rest in any repo.** `.git/config` never carries the App key; git ops
  authenticate with an ephemeral token injected per-operation.

**Synergy with Bug 5b:** the same mechanism retires the at-rest deploy-token problem for the *worker*
too — a worker's clone/push can use a **short-lived installation token minted per task** instead of a
long-lived PAT baked into the remote URL. So this design supersedes the deferred credential-helper
work with a cleaner answer. (Migrating the worker path to App tokens is Phase 1b, below.)

**Permissions requested (least-privilege):** Administration (create/fork), Contents (read/write),
Metadata (read), Pull requests (write, for PR-based delivery). **`delete_repo` is deliberately NOT
requested** — the org can create but not destroy repos (see §5).

---

## 3. Phase 1 — the capability layer (unblocks your monogame/murder/engine/game setup)

A closed set of governed capabilities, each an NL inlet (model → `OperatorIntent` → governed handler,
the established pattern) and each mapped to a governance gate by blast radius:

| Capability | What it does | Reversible? | Gate |
|---|---|---|---|
| `fork_repo(parent)` | GitHub App forks a parent into your account | yes (delete the fork by hand) | **hard-gate** (creates an external resource) |
| `create_repo(name, private)` | creates a new empty repo | yes (archive) | **hard-gate** |
| `add_submodule(repo, url, path)` | operator-plane real-git submodule add + commit | yes | steering (reversible, in-repo) |
| `compose(repo, [submodules], wiring)` | one call: create repo + add N submodules + set each submodule's upstream + open a wiring PR | mixed | **hard-gate** (bundles creation) |
| `set_upstream(project, url)` | (exists) point a fork at its parent | yes | steering |
| `register_project(...)` | (exists) track a repo as an agent-org project | yes | auto |

**Governance wiring (all reused, none new):** the irreversible ones raise the existing
`Trigger.irreversible_action` → **§3 hard-gate** → the effort **freezes** → the PM presents the exact
action for your **approve / modify / abort** → on approval it executes → audited. No capability
self-clears; the human clears (governance §3.0 FSM-A). This is the *same* flow that already gates a
risky code push.

**Your scaffold, expressed to the PM (Phase 1 delivers this):**
```
You: "Fork MonoGame and murder into my account, then create an engine repo
      'monogame-engine' that composes both as submodules with murder building
      against the monogame source; and a separate 'game' repo that uses the engine."

PM:  proposes the exact capability plan (2 forks, 2 creates, 4 submodule adds,
     1 wiring PR) → you approve → the capability plane executes it → each new repo
     is auto-registered as a project with its upstream. You wrote nothing, clicked approve.
```

**Phase 1b (credential migration):** move the worker clone/push path onto App installation tokens
(retires the at-rest PAT). Independent of the rest of Phase 1; sequenced after the App is live.

---

## 4. Phase 2 — the advisor produces an **executable Plan**, not prose

The Tier-2 advisor (built 2026-07-04) currently answers in prose. To make *"any plan the advisor comes
up with, the orchestration executes"* real:

- When the operator signals intent to act (*"set that up," "do it," "go ahead"*) on an advisory
  answer, the advisor compiles its recommendation into the **`Plan`** schema (already exists:
  `feature_overview`, `implementation_steps`, `stop_gates`, `delegation` DAG).
- Each `DelegationStep` gains a **`capability`** binding: a step is *either* a worker task (`role:
  worker`, existing) *or* a capability call (`role: capability`, `capability: fork_repo`, args…).
- The Plan is presented through the **existing plan-approval gate** (UX-FLOW Stage 3, `_present_plan`)
  — you see the whole plan and approve/modify/abort before anything runs.

No new gate; the advisor just emits into a structure the gate already understands.

---

## 5. Phase 3 — the **plan executor** (runs a multi-step, multi-repo plan)

An approved `Plan` is a DAG of steps. The executor:

1. Runs steps in dependency order (reuses the **idle-wait DAG** already in the scheduler).
2. Dispatches each step to its plane: **worker task** → the existing `delegate()` path (with its
   dry-run/review gates); **capability** → the Phase-1 governed handler (with its hard-gate).
3. Applies the existing **stop-gates** between phases (governance §4.5) — e.g. "pause after
   scaffolding, before the first engine change" — where you steer.
4. On a blocked/failed/refused step: **freeze + escalate** (existing semantics), pausing dependents;
   never silently continues.

**Rollback story (design §15 alignment).** Every step is chosen to be reversible or gated: forks/repos
can be archived (not auto-deleted — §2 withholds `delete_repo`); code changes land on branches / PRs
(additive, revertible); submodule wiring is in-repo and revertible. A plan can be **aborted
mid-flight**, leaving created repos in place for you to keep or remove by hand — the org never
destroys.

---

## 6. Phase 4 — the full loop

End-to-end, hands-off-except-approval:

```
"I want a game built on murder + monogame, both forked so I can extend them."
   → advisor researches the right structure (Tier 2)
   → compiles a Plan (Phase 2): fork×2, create engine + game, compose, scaffold a minimal game
   → you approve the Plan (one gate)
   → executor scaffolds the repos (capability plane) + builds the starter game (worker plane),
      pausing at the stop-gates you set
   → you steer at each gate; the org does the rest; nothing lands irreversibly without you.
```

This is the corpus's own end-state (governance §1 + UX-FLOW), now reachable because the org has both
the **capabilities** and the **executor** to carry a plan across repositories.

---

## 7. Safety analysis (maps to the paper's failure modes → controls, governance §2)

| Risk introduced | Control (all existing governance) |
|---|---|
| Org can now create external resources (repos) | **§3 hard-gate** — human approves each creation before it happens; `Trigger.irreversible_action`. |
| A privileged credential exists | **GitHub App**: no long-lived PAT; short-lived per-repo installation tokens; App key is a bridge secret, never in a worker or a repo; installation revocable in one click. |
| Emergent multi-agent behavior at scale (the paper's core finding) | Capabilities are **deterministic functions, not agent prompts** — a fixed, closed set; the executor is deterministic DAG walking, not an agent deciding what to run. |
| An agent could try to escalate its own powers | It structurally cannot — the worker plane has **no code path** to a capability; only the governed bridge does, post-approval. |
| Destructive mistakes | `delete_repo` is **not a capability**; the org creates + archives, never destroys. Everything else is branch/PR-additive or in-repo-revertible. |
| A bad plan runs unattended | **Plan-approval gate** (you see the whole plan) + **stop-gates** (you steer mid-run) + **freeze-on-failure**. |

Net: the org becomes *more capable* without any *agent* becoming less supervised — the exact property
the governance model exists to preserve.

---

## 8. What's reused vs. genuinely new

**Reused (already built):** the §3 escalation gate + FSMs; `Trigger.irreversible_action` + the
approve/modify/abort decision flow; the plan-approval gate + `Plan` schema; the idle-wait DAG;
dry-run/review/stop-gates; the NL-first intent→handler pattern; the audit sink; `add_upstream_remote`/
`clone` as the operator-plane precedent; the Tier-2 advisor + research client.

**New (this design):** (1) the GitHub App integration + installation-token minter; (2) the capability
handlers (`fork`/`create`/`add_submodule`/`compose`); (3) `capability` as a step type + the
`OperatorIntent` kinds that reach them; (4) the advisor→`Plan` compiler; (5) the plan executor.

---

## 9. Open decisions / risks for operator steer

1. **GitHub App setup is a one-time human step** (register the App, install it, drop its private key
   as a bridge secret). Unavoidable — it's the root of trust. ~15 min. *Everything after is automated.*
2. **Org vs. personal account.** If your forks/repos live under an org, the App installs on the org
   (cleaner perms). Under a personal account works too. **Which?**
3. **`compose` granularity.** One mega-capability that does create+submodules+wiring in a single
   approval, vs. several smaller capabilities each individually approved. Fewer gates = smoother;
   more gates = finer control. **Default proposed: `compose` as one hard-gate, with the plan showing
   every sub-action so approval is still fully informed.**
4. **Where the App private key lives.** Bridge env var vs. a mounted secret file vs. a secrets
   manager. (Env is simplest and matches current convention; a mounted file is a touch better.)
5. **Rate limits / cost.** GitHub App API is generously rate-limited; research/model calls in Phase
   2–4 ride the existing llm-queue governance. No new cost surface beyond model usage.
6. **Build size.** This is ~4 phases across several sessions, not one build. Phase 1 (+1b) is the
   valuable, self-contained first increment and is where implementation would start on approval.

---

## 11. State-awareness & context — the reconciling planner (added 2026-07-04, operator-caught)

The operator identified the deepest gap: the P-APL.2 planner was **stateless about the target repo**
— it reasoned over the registry (which forks exist) but was **blind to the repo's actual contents**,
so it *duplicated* a submodule that already existed instead of reconciling. That is a **direct
deviation from UX-FLOW Stage 1** — *"the planner reads the project's current workspace and drafts a
plan **anchored to concrete workspace reality**; intent op: **anchor**."* The class of gap: the system
makes decisions without grounding in **actual current state**, so it drifts, duplicates, and doesn't
self-maintain. The corpus's own answer is the **intent thread** (§0) + **anchoring** (Stage 1) +
**grounding** (Stage 4).

**Fixed (this pass):**
- **GAP-A — Stage-1 anchor (LIVE).** `capabilities.read_repo_state()` reads each project's ACTUAL
  state (default branch, `.gitmodules`, top-level tree) via the App API (no clone) and the planner
  prompt now carries *"CURRENT STATE … ANCHOR to this; do NOT re-add what exists."* The planner
  reconciles the DELTA (skip what exists, MOVE when the intent wants a different path, empty plan when
  the desired state already holds) — declarative, idempotent, clean. This is the design's Stage-1
  anchor, previously omitted.
- **GAP-B — intent thread to the worker (LIVE).** Plan `worker_task`s now carry the plan's goal + an
  explicit *reconcile-don't-duplicate* directive as the worker's grounded goal (UX-FLOW §0 / Stage 5:
  *"the intent thread rides along as each worker's grounded goal"*).

**Planned follow-ups (same class — anchored to the corpus):**
- **GAP-C — readiness gate on plans (UX-FLOW Stage 2).** A vague *"set up my project"* should trigger
  clarifying questions (F5 — don't guess), not a blind plan. Wire the Stage-2 readiness gate into the
  `plan` intent.
- **GAP-D — plan dry-run/preview (UX-FLOW Stage 4 "ground + dry-run").** Present the reconciled DELTA
  as a *preview* ("these 2 submodules already exist → no change; 1 add; 1 move") before approval —
  measure-twice. The anchor makes this cheap; surface it in the plan card.
- **GAP-E — cross-effort awareness (governance §2 F4).** The planner/executor don't check whether
  another effort is mid-edit on a target repo → auto-escalate a cross-effort conflict, don't collide.
- **GAP-F — advisor→plan link (this doc's original P-APL.2 intent).** `advisory` (research) and `plan`
  are separate intents; the design intends the advisor's grounded answer to *feed* a plan
  (research-then-plan) rather than the operator re-typing the intent.
- **GAP-G — learning loop / estimate (UX-FLOW §6, Stage 3).** Accrue plan outcomes for real estimates
  + to spot recurring drift; cold-start today.

Precedence held: every fix maps to an existing corpus mechanism (anchor / intent-thread / readiness /
ground / cross-effort / learning), not a new safety story.

## 11b. PM delivery verification — "approve → the change actually lands" (added 2026-07-04, operator-caught)

The reconciling planner fixed the *structure/planning* half; the operator then surfaced the *doing*
half: a worker would **investigate well (70–85 commands) but not reliably commit + push its changes**,
and the PM reported *"pushed to branch X"* on the worker's word — or **marked the effort `done`** —
even when nothing had actually landed on the remote. That is the paper's **rubber-stamp failure (F4)**
and a violation of **governance §4.2** (a deliverable without a **checkable acceptance signal** is
*unverified*, not done) and **F8** (*"the PM **is** the monitor agent"*). A worker's pi turn ending
`done` means *its turn ended* — it is **not** evidence the change was delivered.

**Fixed (this pass) — the PM verifies against the remote, not the worker's self-report:**
- **Checkable signal (§4.2, deterministic floor).** `capabilities.read_branch_delivery()` reads the
  effort's branch on the remote via the GitHub App API (own account) and reports whether it **exists**
  and is **ahead of the base** (carries real commits). `landed = verifiable ∧ exists ∧ ahead ≥ 1`.
  This is code (the deterministic floor), not the small model.
- **Monitor → re-engage → escalate (F8, §3 ladder, UX-FLOW Stage 5→6).**
  `_publish_and_verify()` publishes, then verifies. On **verified non-delivery** the PM **re-engages
  the worker ONCE** with a firm, explicit publish instruction (F5 handoff contract — states plainly
  *"NOT complete until pushed"*, and asks it to report `NO CHANGES: <why>` if there genuinely were
  none, so *forgot-to-push* is distinguishable from *nothing-to-do*). If it **still** hasn't landed,
  the PM **escalates UP to the operator** (intent-framed: *"ran but I could not verify the change
  landed — it is **not** marked done; re-run, or confirm it's expected"*) and **does not** mark the
  effort done (it stays visible in `/status`). No false `done`, ever.
- **Honest `unverified` (§4.2).** When the App **can't** read the repo (not its own account),
  verification returns `verifiable=False`; the PM then reports the worker's self-report **labelled as
  unverified** (*"reports it pushed X, which I could not independently verify"*) rather than asserting
  it as fact — and does not block (a repo the App can't see is not a worker failure).

This is the concrete implementation of the PM's monitor role for the execution layer — the missing
verification that made *approve → the change lands* unreliable. It reuses the existing acceptance /
monitor / escalation mechanisms; no new safety story. Precedence held.

## 11c. Worker-health reliability — quarantine, re-route, dead-letter (added 2026-07-04, live-caught)

Validating §11b surfaced the deeper cause of the original "worker didn't push", plus a follow-on
stuck loop. Three interacting reliability holes in the worker pool (machine B):

1. **No worker affinity** — `scheduler.acquire` picked *any* free worker, so a follow-up wake (the
   publish, a re-engage, a next step) could land on a **different worker** than the one holding the
   effort's workspace → it ran `git add/push` in an empty tree ("pushed no branch") or 409'd on a
   busy worker. **Fix:** affinity — a wake for a session returns to the worker bound to it
   (`release()` suspends but keeps `session_id`).
2. **No quarantine of a wedged worker** — a worker whose daemon is stuck (409 busy) or unreachable
   stayed listed *free*, so `acquire` kept picking it and every dispatch 409'd. Combined with (1)
   pinning an effort to that worker, the effort could **never** run — the GPU sat idle. **Fix:**
   `scheduler.quarantine()` sets a self-healing back-off (`quarantined_until`), frees the slot, and
   drops the stale session; `acquire` excludes quarantined workers; `router.wake` quarantines on a
   409/transport error and **re-dispatches on a healthy worker** (re-cloning there) when a `repo` is
   set; a repo-less follow-up can't be moved, so it quarantines + raises and verification arbitrates.
   When *every* worker is quarantined, `acquire` raises NoCapacity → the effort **parks** and
   auto-resumes when a slot frees / a back-off lapses. Boot (`reset_stale`) lifts stale quarantines.
3. **Unbounded event retry** — a handler that always threw (e.g. because it kept re-dispatching to
   the wedged worker) was "kept unprocessed" and **replayed on every catch-up forever**. **Fix:** the
   event gateway caps handler attempts and **dead-letters** a poison event (marks it processed + logs
   loudly) after `event_max_attempts`, so one bad event can't loop.

Config: `worker_quarantine_seconds` (300), `worker_dispatch_max_attempts` (3), `event_max_attempts`
(5). All self-healing (back-off lapses, boot lifts, parked efforts auto-resume) — fail-safe, not
fail-stuck. This is pool-health only; it is **distinct from** the governance freeze (that's the
effort's §3 gate). Precedence held — no change to the safety model, only to pool robustness.

## 11d. Intent-anchored completion — the PM judges vs the operator's target (added 2026-07-04, operator-caught)

Operator caught a false `done`: "*in monogame-engine, wire murder…*" ran, the PM verified a landed
branch and reported **done** — but the change landed on **`murder`** (a submodule repo) while
**`monogame-engine`** (the operator's stated target) got **nothing**. The planner had scoped the
effort to `murder`, the PM verified `murder`, and completion was declared — a **mechanical-effort**
judgment, not an **intent** judgment.

The corpus is explicit (**DELIVERY-PIPELINE §1**): *"a 'feature' is the operator-intent thread — the
PM decides when the constituent efforts are **feature-complete**."* Completion is judged against the
**intent**, not one effort's branch. My PM judged per-effort → the miss.

**Phase 1 — intent-anchored completion flag (LIVE).** At worker-task dispatch the executor records
the registered projects the operator **named** in the intent that the effort is **not** targeting
(`_intent_named_projects`, longest-slug-first). At completion, if such a named target exists, the PM
does **not** report a clean `done`: it emits a **scope check** — *"your request also named
`monogame-engine`, which this effort did not change (it worked on `murder`) … that part is not done"*
— marks the card **needs-attention**, and **keeps the effort in `/status`** (does not set
lifecycle=done). A sub-repo change can no longer masquerade as the whole intent. Grounded in §3.7
(deviation detection) + §4.5 (verify deliverable vs. intent). This is the **safety net**; it does not
itself do the missing work.

**Phase 2 — composition-aware planning + execution (LIVE).** An "in `<engine>`, wire `<submodule>`
against `<sibling>`" intent is inherently multi-repo. Built, all in the bridge (no worker/daemon
change — the submodule bump is a pure App API call):
- **`capabilities.bump_submodule`** — points the engine's submodule at a commit via the **GitHub Git
  Data API**: a tree entry with mode `160000` / type `commit` IS a gitlink, so it creates
  base-tree → new tree (gitlink at the worker's commit) → commit → branch ref. No checkout, no worker.
- **Deterministic augmentation (`_augment_composition`)** — the session pattern (structure in code,
  not the small model). When the intent NAMES an engine that vendors the submodule a worker_task
  targets, it (a) injects the **engine LAYOUT** into the task (the relative path to the sibling
  submodule, computed from the real `.gitmodules` paths — e.g. `../MonoGame`) so the worker writes
  paths that resolve in the vendored tree, and (b) appends a **`submodule_bump`** step (new
  `LifecycleStep` kind) so the plan includes the wiring-back.
- **Coordinated executor (`_run_composition`)** — edit the submodule (worker plane) → **verify** its
  branch landed + read its exact commit → **bump** the engine's pointer to that commit on a paired
  branch (operator plane) → report BOTH branches. If the edit doesn't land a verified commit, the
  engine is **not** bumped (no false wiring). Additive; merge to the engine's `main` stays
  human-gated (D4). The intent-anchor (Phase 1) no longer flags the engine — it IS updated by the bump.

So `in monogame-engine, wire murder…` now produces: a `murder` branch with the csproj change **and** a
`monogame-engine` branch whose `vendor/murder` points at that commit — the engine is wired, on a
branch, for human review + merge.

**Live-caught fixes during Phase-2 validation (2026-07-04):**
- **Bump pairing** — the small model emits its own `submodule_bump` with sloppy fields (blank
  `source`), which failed executor pairing → the augmenter now REPAIRS a model-authored bump
  (fills `source`/`path`), the executor pairs a lone bump↔lone worker-task as a fallback, and an
  unpairable wire-back is *reported*, never silently dropped.
- **Expired token in `origin` (the "worker can't push" class, root-caused).** The deploy token is
  EMBEDDED in origin's URL at clone; a GitHub App installation token lives 1 h. A NOOP re-focus
  hours later (same URL → no re-clone) or a long task pushed with a dead credential — the worker's
  own blocker report confirmed it. Fixes: (a) little-coder `workspace.refresh_origin_auth` +
  the daemon's NOOP branch re-bakes origin's auth with the caller's current token; (b) the bridge's
  publish wake passes `repo` + a current token (NOOP + re-auth; work preserved); (c) the App-token
  cache re-mints when < 45 min of life remain, so a token baked at dispatch has runway for the task.

## 11e. Comms/UX audit vs the corpus — delivery visibility (added 2026-07-04, operator-caught)

Trigger: the composition succeeded but **the operator couldn't see it** — work landed on `agent/*`
branches, `main` looked untouched, and nothing had ever explained the branch-based delivery model
("the branching wasn't communicated by the PM"). A full audit of the implementation against the
teams-chat-agent-orchestration corpus found the comms plumbing **aligned** (intent→destination
routing, intent-framed CONCERNs, bring-back-down echo, effort cards, readiness gate, honest /status,
D0 verified-branch completion, D3 review) and four gaps — all fixed:

- **D1 — PR creation (LIVE).** `capabilities.open_pull_request` (App API, `pull_requests: write` —
  resolves OD-DP1 with the App, no new secret; idempotent — an existing PR is reused). Every
  **verified** delivery now opens a PR whose body carries the goal + branch + verified sha + the
  human-gated-merge invitation; a composition opens **two** (code PR on the submodule repo + wiring
  PR on the engine, cross-linked — a multi-repo feature can't share one PR, so this consciously
  implements OD-DP3's intent per-branch). The PR is the corpus's *promotion artifact* — delivered
  work is now visible in GitHub's UI/notifications, not just on an easy-to-miss branch.
- **D4 — human-gated merge (LIVE).** Each PR registers a pending **merge gate** (persisted,
  rehydrated across restarts). The operator merges **conversationally** — a plain **"merge it"**
  (deterministic catch, never the small model — the phrase is the §3 clearance for the irreversible
  action; one pending → merges + echoes which; several → disambiguates; "merge both" supported) or
  `approve merge-<id>` — and the bridge merges via the host API with a **merge commit** (`--no-ff`
  equivalent). Declining leaves the PR open on GitHub. No auto-merge; no agent authority.
- **Delivery-model explainer (LIVE).** `/help` now has a "How delivery works" section; the dispatch
  ack says up front *"work will land on branch `agent/<effort-id>` (+ a PR for your review) — `main`
  only changes when you merge"*; closures carry the PR link + the same one-line model.
- **Stage-3 Estimate (LIVE).** `LifecyclePlan.estimate` + planner prompt + plan-card render — the
  UX-FLOW plan template's 4th section (Overview/Steps/Delegation/**Estimate**).

**Accepted deviations (documented, not built):** one bot plays PO+PM (the corpus splits them);
per-branch PRs instead of N-efforts→1-feature-PR (OD-DP3) — compositions span repos, so per-branch is
the honest granularity.

## 11f. D2 autonomous checks + D6 human-testing handoff (added 2026-07-04)

Closing the corpus's remaining delivery-pipeline stages, honestly scoped:

- **D2 — the test series red-gates the merge (LIVE).** Each project can carry a **check command**
  (`/project check <name> "<cmd>"`, e.g. `dotnet build Build.sln`; shown in `/project list` 🧪).
  After a delivery's PR opens, the bridge wakes the **affine worker** (its workspace is already on
  the delivered branch) to run the check and report `CHECK: PASS/FAIL`. **Green** → the merge gate is
  presented with a "checks passed (worker-reported)" label — honest about verification depth.
  **Red** → the corpus loop: the failing output routes **back to the owning effort** (fix on the SAME
  branch → re-push → re-verify → re-check, one bounded round); **still red → the merge gate is
  WITHDRAWN** (the PR stays open for human inspection; escalated) — *a red never travels forward*.
  **No check configured** → skipped with an honest, actionable note (never silently pretended).
- **D6 — human-testing handoff (LIVE).** Every successful merge (conversational or pre-authorized)
  appends the D6 handoff: pull `main`, run the project's check locally, try it — *works → done;
  broken → say what's wrong and a fix effort opens through ordinary intake* (the loop closes back to
  Stage 0, as UX-FLOW prescribes).
- **D5 (staging deploy) — environment-dependent, OPEN.** The onboarded projects (a game engine
  composition) have no staging environment to deploy to; when a web-service project lands, D5 =
  human-gated deploy to a preview env (reusing D2's mechanism per the corpus). Not faked.
- **OD-DP2 (AI-browser web-test leg) — OPEN decision**, as the corpus itself marks it: Playwright
  was deliberately excluded from little-coder; the web leg needs a new isolated browser-testing
  container. Unbuilt until a web project needs it.

## 10. Build order (on approval)

1. **P-APL.0** — register + install the GitHub App; land its key as a bridge secret; token-minter
   module + health check. *(operator does the register/install; I build the minter.)*
2. **P-APL.1** — capability handlers (`fork`/`create`/`add_submodule`/`compose`) + their NL inlets +
   hard-gate wiring + audit + tests.
3. **P-APL.1b** — migrate worker clone/push to App installation tokens (retires the at-rest PAT).
4. **P-APL.2** — advisor→`Plan` compiler + `capability` step binding.
5. **P-APL.3** — plan executor (DAG walk across worker + capability steps, gates, freeze-on-failure).
6. **P-APL.4** — the full-loop polish + docs; 3-place change (compose/recovery/stack-map) as needed.

> This doc is for **your review and steering**. Mark it up — especially §9's open decisions — and I'll
> revise, then start at P-APL.0 on your go-ahead.
