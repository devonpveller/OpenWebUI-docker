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
