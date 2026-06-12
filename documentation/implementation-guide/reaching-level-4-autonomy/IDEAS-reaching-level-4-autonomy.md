# Ideas — Reaching Level 4 Autonomy

**Status:** 💡 IDEAS / discovery. Not a build plan, not committed scope. This captures
the *idea* of closing the full software-delivery loop and the autonomy ladder it implies,
with preliminary sketches to argue feasibility.
**Date:** 2026-06-11
**Author of brief:** Operator (PO)

**Builds directly on** (read these first — this doc extends, it does not restate them):
- [../teams-chat-agent-orchestration/PLAN-teams-chat-agent-orchestration.md](../teams-chat-agent-orchestration/PLAN-teams-chat-agent-orchestration.md) — the governed multi-agent org
- [../teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md](../teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md) — **the governing spec; this doc inherits it**
- [../teams-chat-agent-orchestration/TOOLING-selection.md](../teams-chat-agent-orchestration/TOOLING-selection.md) — what to reuse vs. build
- [../../little-coder/Self-improving-little-coder-design.md](../../little-coder/Self-improving-little-coder-design.md) — the worker harness + per-project model

> The orchestration design takes the loop from *human ⇄ agent ⇄ agent → work → PR → review → fix*.
> It **stops at the floor**: push/merge/deploy are named as gate triggers (hard-rule #4) but the
> *mechanism* — and the **redeploy → observe → loop** closure — is undefined. This document is
> about closing that loop, generalized so the ai-stack is a **hub** pointed at *any* repo.

---

## 1. The idea in one paragraph

The ai-stack is becoming a **software-delivery hub** — a governed agent org (PM + worker fleet
over a chat bus) that can be pointed at an arbitrary repo and carry a change all the way around:
**understand → build → push → PR → self-verify the running web build with a browser agent →
fix/iterate → (PO-cleared) merge → redeploy → observe the live result → and loop on what it
learns.** Today the org can do the front half. "Level 4 autonomy" is the name for closing the
**back half** — *ship and self-verify within a bounded, governed operational domain, with the
human as safety driver rather than as a step in every cycle.* The unlock is two missing pieces:
a **per-project deploy/observe contract** (so the generic hub knows how to ship *that* repo) and
a **self-verification layer** (browser + deterministic tests) that lets the org check its own
work against a *running* system instead of trusting a diff.

---

## 2. Why "Level 4" — an autonomy ladder

Borrowing the self-driving framing on purpose: autonomy is meaningful only **relative to a
bounded operational domain** and **who holds final responsibility**. The point of L4 (vs L5) is
that it is *high autonomy inside a geofence*, not autonomy without limits.

| Level | What the org does | Human role | Where we are |
|------|-------------------|-----------|--------------|
| **L0** | In-IDE assist; human drives every action | Driver, hands-on every step | — |
| **L1** | One scoped task at a time, constant supervision | Supervises each task | **little-coder today** (single-task FIFO) |
| **L2** | Multi-agent org does work + hands off; **human clears every gate** | Approves each CONCERN inline | **teams-chat design, as specced** |
| **L3** | Org runs code → PR → self-verify → fix **autonomously up to merge**; human is the merge gate + handles escalations | Eyes-on, hands-off *within* an effort | partial — needs PR/verify mechanized |
| **L4** | Org closes the **full** loop incl. merge → redeploy → observe → iterate, **per-project**, self-verifying and self-recovering, **within a bounded operational domain** | **Safety driver / PO** — sets goals, clears irreversible gates, takes escalations; **not in every loop** | **the target of this doc** |
| **L5** | Unbounded autonomy, no operational-domain limit, human optional | (none required) | **explicitly rejected — see below** |

**L5 is a non-goal, by governance, not by capability.** The governing spec is built on
*propose-not-dispose*, *PO-final-say*, and *pause-until-cleared* (governance §3, §6). A system
that removes the human from irreversible decisions **is** the paper's most dangerous failure mode
(F3: dropped/routed-around brakes). So the ceiling we are designing toward is **L4: maximal
autonomy inside an explicitly bounded, fully-observable, human-final domain.** "Reaching L4" means
the org does *almost everything itself almost all the time* — while every irreversible step and
the domain boundary itself remain the PO's.

---

## 3. The gap L4 closes (relative to the current design)

What the orchestration design already gives us (reuse, don't rebuild):

- Human ⇄ PM ⇄ worker fleet over an observable chat bus; wake-on-mention; escalation gate;
  differently-goaled **LLM** review; audit + learning loop (PLAN §3, governance §3–§6).
- A **per-project hub** already exists at the worker layer: `/project repo:<link>` clones any repo
  with a least-privilege per-project deploy token; a per-repo `AGENTS.md` layer that is
  *authoritative for that repo*; and — for little-coder's *own* repo only — the exact loop shape
  `artifact → PR → operator merge → redeploy` (little-coder design §12.3, §3.7, §10.5).
- The **floor permits the right primitives**: `git push` to a pre-baked named remote, `merge
  --no-ff`, `reset --hard <tag>` (= rollback), all enforced by `git-proxy`
  ([../../little-coder/git-proxy/git_proxy.py](../../little-coder/git-proxy/git_proxy.py)).

What L4 **adds** (the genuinely new ideas in this doc):

1. **Mechanize the gated steps.** PR creation, merge, and redeploy exist as *gate triggers* but
   not as *actions*. L4 gives the bridge concrete `open_pr / request_merge / deploy` handlers,
   each still PO-cleared where irreversible (hard-rule #4).
2. **Generalize ship from one repo to any repo** via a **per-project deploy/observe contract**
   (§5) — the hub stays generic; the *target repo* declares how it builds, previews, deploys,
   smoke-tests, and rolls back.
3. **Self-verification against a running system** (§6) — a browser agent + deterministic tests,
   so the org checks its work against a *live build*, not a diff. This is also the missing
   **"observe"** step.
4. **Autonomous recovery** (§7) — green/red on the live observe step either closes the effort or
   triggers rollback + a fresh effort, with no human in the inner loop.
5. **An Operational Domain Definition (ODD)** (§4) — the explicit, per-project geofence that makes
   "more autonomy" safe rather than reckless.

---

## 4. The Operational Domain Definition (the safety bound that makes L4 ≠ reckless)

The single idea that separates L4 from "let the agents deploy whatever" is an explicit, declared
**operational domain** per project — what the org is *permitted to do unattended*, and where the
hard human gate stays:

- **Repo scope** — which repos the hub may ship at all (allowlist). Default: none until added.
- **Blast radius** — *where* a deploy may land: a hub-spun ephemeral sandbox, a dedicated staging
  target, or (rarely, narrowly) a production target. Each is a different ODD tier.
- **Pre-authorized vs. always-gated actions** — within the ODD, some normally-irreversible steps
  may be *pre-cleared* (e.g. "deploy to *staging* for repo X is pre-authorized; **production is
  always a PO CONCERN**"). This is the concrete answer to the still-open governance §8 #5 ("what
  counts as irreversible/external").
- **Stop conditions** — failed smoke test, healthcheck regression, egress-policy hit, or
  cost/rate cap → autonomy *suspends* and escalates (pause-until-cleared, governance §3).

> Net: autonomy level is **per-project and per-target**. The same hub can be L2 for a production
> repo (human clears everything) and L4 for a sandboxed side-project (ships and self-verifies
> freely) — because the ODD, not the code, sets the ceiling.

---

## 5. The per-project deploy/observe contract (the generalization)

The hub does **not** know how to deploy "a project." Each target repo carries a small contract,
**versioned with the repo** (read the way little-coder already reads `AGENTS.md`):

```yaml
# agent-ops.yml  — lives in the target repo root; the hub reads it, never owns it
odd:
  autonomy: L4            # ceiling the PO granted this repo (per §4)
  deploy_targets: [sandbox, staging]   # production absent = always PO-gated
preview:
  build:  docker compose -f preview.yml up -d   # how to stand up a throwaway instance
  url:    http://localhost:3000                 # where the browser agent points pre-merge
  teardown: docker compose -f preview.yml down -v
verify:
  smoke:  npx playwright test e2e/smoke         # deterministic gate (hard pass/fail)
  agent:  "log in, create a project, confirm it appears on the dashboard"  # NL goal for the browser agent
deploy:
  staging: <project's own deploy command>       # compose up --build | vercel deploy | fly deploy | rsync ...
  url:     https://staging.example.internal     # where 'observe' runs post-deploy
rollback: git reset --hard {{last_good_tag}}     # + re-run deploy; or project's own rollback
```

Properties that keep this safe and reusable:

- **Per-project egress + token.** Each effort runs in its own `lc-egress` allowlist with the
  project's own deploy token (PLAN §3.6; little-coder §3.4/§10.5). A deploy for repo X cannot
  reach beyond X's declared surface — *the floor is the same one that already contains workers.*
- **The contract is steering, not floor.** It says *what to run*; the deterministic floor
  (`git-proxy`, egress, hooks) and the ODD say *what's allowed*. A malicious `agent-ops.yml`
  can't widen its own permissions — same floor/steering split as governance §4.2.
- **The hub stays generic.** New project = new `agent-ops.yml`, not new hub code.

---

## 6. Self-verification layer (browser agent — the "review" and the "observe")

The same capability lands in **two** places, against different targets:

- **Pre-merge (review):** stand up the project's `preview` build in an ephemeral sandbox and test
  it — "test the web stack build with agents." Reuses little-coder's existing rule that *validation
  runs against fresh ephemeral clones, never the focused workspace* (design §557).
- **Post-deploy (observe):** run the same suite against the **live** `deploy.url` — this is the
  automated first pass of *"human observes outcome,"* with the PO confirming the summary.

**Tooling idea (aligned to the local-first, OpenRouter-where-mandatory stance, governance §2.1):**

- **Magnitude** ([github.com/magnitudedev/browser-agent](https://github.com/magnitudedev/browser-agent))
  — vision-first, AI-native E2E *testing* framework with a planner/executor split and a **local VLM
  option (Moondream)**. Best fit for "test a web build with an agent" on a local-first stack.
- **Playwright** ([playwright.dev](https://playwright.dev/)) — the **deterministic floor** under
  the agent: hard pass/fail assertions. The org's own principle is *small-model review paired with
  deterministic checks* (governance §4.7) — so Playwright is the gate, the agent is exploratory
  coverage, and the **agent's verdict alone never merges.**
- **Browser Use** ([github.com/browser-use/browser-use](https://github.com/browser-use/browser-use))
  — fallback if general programmatic web *automation* (beyond testing) is needed.

This layer feeds the existing differently-goaled **LLM reviewer** (governance §4.4) — now the
reviewer judges *against observed behavior*, not just the diff. ("Verify, don't trust" §4.5b.)

---

## 7. The loop, generalized — and how it self-recovers

```
/project repo:<X>                  → existing primitive (clone + per-project deploy token)
  → work happens (worker fleet, goal-grounded)
  → git push branch                → to X's OWN pre-baked remote (git-proxy allows)
  → bridge: open_pr()              → gh pr create on X
  → CI: bring up X.preview (sandbox) → Playwright (gate) + Magnitude (agentic) at X.preview.url
  → LLM reviewer (differently-goaled) + browser report → PM aggregates
  → red?  → re-ground → refactor → loop            (already designed, governance §4.5–4.6)
  → green? → request_merge() → CONCERN to PO        ⛔ hard-rule #4 — PO clears (or ODD pre-auth)
  → merged → bridge runs X.deploy[target]           ⛔ PO-gated unless ODD pre-authorizes target
  → bridge: observe = Playwright+Magnitude at X.deploy.url + healthcheck
       → green → post to #effort-X → effort closes → outcome feeds learning loop (§6)
       → red  → run X.rollback (reset --hard <last_good_tag> + redeploy) → new CONCERN → new effort → loop
```

**Autonomous recovery is the key L4 behavior:** a failed observe doesn't wait for a human to
notice — the org rolls back to the last good tag (a floor-permitted primitive) and opens a fresh
effort. The human is *informed* (escalation/notification), not *required* to act, unless a stop
condition (§4) trips.

---

## 8. How L4 stays inside the governance spec

Nothing here weakens the governing model — it operationalizes it:

- **Irreversible actions stay PO-final.** Merge and production deploy remain hard-rule #4 CONCERNs.
  L4 only *pre-authorizes a narrow, declared subset* (per the ODD) — e.g. sandbox/staging deploys
  for allowlisted repos — and **never** removes the human from production or domain-boundary changes.
- **F3 still holds.** A failed verify/observe is a *blocking* event that escalates; it can never be
  "routed around" to another worker or silently dropped (governance §3 fail-safe).
- **Bus-only + full audit unchanged.** Every PR, deploy, observe result, and rollback is a logged
  bus event mirrored to Open Brain (governance §5) — observe outcomes become first-class learning
  signal (§6).
- **Local-first model posture.** Workers + browser-test VLM run local by default; judgment/reviewer
  roles escalate to OpenRouter only where the P0 capability-floor test mandates (governance §2.1).
- **The org-vs-single-agent question still applies per task** (governance §3.5) — L4 is *permission*
  to close the loop, not an instruction to fan out.

---

## 9. Preliminary build sketch (illustrative — NOT committed)

If this graduates from idea to plan, it slots in as a phase **after** the orchestration spine
(which ends at P6 audit/learning + P7 mobile). Call it:

**P8 — Ship & Observe (per-project, the L4 closure)** — sketch:

- **P8.1** `agent-ops.yml` contract spec + a loader in the bridge (parse, validate, version it;
  treat as steering, enforce ODD as floor).
- **P8.2** Generic bridge handlers: `open_pr`, `request_merge` (→ CONCERN), `deploy[target]`,
  `observe`, `rollback`. All deterministic; coordination lives in the bridge (PLAN §3.5).
- **P8.3** Ephemeral-sandbox runner: stand up / tear down a target repo's `preview` in an isolated
  compose network with the project's egress allowlist.
- **P8.4** Self-verification job: Playwright (gate) + Magnitude (agentic) at `preview.url`
  (pre-merge) and `deploy.url` (post-deploy); results posted to the effort channel + reviewer.
- **P8.5** ODD enforcement: per-repo allowlist, deploy-target tiers, pre-auth vs always-gated,
  stop conditions → suspend-and-escalate.
- **P8.6** Autonomous recovery: tag-on-deploy, rollback-on-red, fresh effort.
- **R.x** 3-place change for any new container (browser-test runner, sandbox) — compose + recovery
  scripts + stack-map.

Hard gate (inherited): do **not** enable any pre-authorized (unattended) deploy until the
escalation gate (P2) and floor (P3) pass their safety tests.

---

## 10. Open questions (for when this becomes a plan)

1. **Where do target environments run?** Hub-spun ephemeral sandbox (recommended default for
   dockerized web stacks) vs. the project's own infra (hub only triggers + observes). Can coexist;
   sets what the runner must do.
2. **Contract format/location** — standalone `agent-ops.yml` (recommended; explicit, lintable) vs.
   a section of the per-repo `AGENTS.md` (reuses a layer little-coder already reads).
3. **ODD granularity & who edits it** — is the ODD itself PO-only (almost certainly yes), and how
   is "autonomy: L4 for staging, L2 for prod" expressed and enforced? (governance §8 #5.)
4. **Production posture** — is unattended production deploy *ever* in the ODD, or is production
   always a PO CONCERN regardless of repo? (Recommend: always gated.)
5. **Observe sufficiency** — what counts as "observed OK"? Smoke pass + healthcheck + agentic goal
   met? How much do we trust the agentic verdict vs. require deterministic confirmation? (§4.7.)
6. **Cost/rate envelope** — browser-agent runs + ephemeral sandboxes consume GPU/compute that
   contends with the rest of the stack (PLAN §3.6 budget). What's the unattended-loop spend cap?
7. **Multi-repo concurrency** — the worker pool is bounded by inference capacity; how many *target
   projects* can be in-loop at once before it's a single-worker queue?

---

## 11. Relationship to the existing doc set

- **This doc = the back half + generalization.** The orchestration docs build the org and take it
  to ~L2/L3 (work → PR → review → fix, every gate human-cleared). This doc defines **L4** — closing
  merge → redeploy → observe → recover, per-project, within an ODD.
- **It changes no governance control** — it consumes them. If anything here conflicts with
  [../teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md](../teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md),
  the governance doc wins.
- **It reuses, not rebuilds:** `/project` switching, per-repo `AGENTS.md`, `git-proxy` push/merge/
  reset primitives, ephemeral validation clones, the LLM reviewer, the audit + learning loop.

---

## Sources

- [Magnitude — open-source vision-first browser agent / AI-native test framework](https://github.com/magnitudedev/browser-agent)
- [Browser Use — programmatic AI browser agent](https://github.com/browser-use/browser-use)
- [Playwright — deterministic browser automation/testing](https://playwright.dev/)
- arXiv:2604.10290 — "AI Organizations are More Effective but Less Aligned than Individual Agents"
  (the governing paper, via the orchestration governance doc)
</content>
</invoke>
