# Delivery pipeline — branch → commit/push → PR → autonomous test → human-gated merge → deploy

**Status:** 📐 **design (2026-07-02)** — the software-delivery layer that turns *governance* into
*shipped work*. **Stage D0 (branch + commit/push on done) and D0.f (fork/upstream onboarding) are
BUILT** (D0.f 2026-07-03 — needs a `little-coder:local` rebuild to go live); D1–D6 are designed here
and **not yet built**. This doc adds the pipeline the governance corpus never specified: the corpus
built *escalation* ("surface a concern up the ladder"); this builds *promotion* ("move a feature
through test → merge → deploy → human testing"). Same org, different machinery.
**Precedence:** governance spec > this doc > PLAN/TASKS. Where they touch (hard-rule #4, review),
this doc obeys the governance model as corrected 2026-07-02 (§3 below).

**Companions:** [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)
(§4.5 stop-gates, §8 #5 irreversible line) · [UX-FLOW.md](UX-FLOW.md) (Stage 4 dry-run) ·
[COMMS-MODEL](COMMS-MODEL-deterministic-routing.md) (effort = thread) ·
[`agent-org/IMPLEMENTATION-NOTES.md`](../../../agent-org/IMPLEMENTATION-NOTES.md) (what's built).

---

## 0. Why this doc exists — the problem it fixes

Three concrete failures the operator identified, all rooted in "workers never publish their work":

1. **Work is destroyed.** little-coder **wipes the workspace on `/project` switch**; a pooled worker
   has one workspace. Uncommitted/unpushed work is *gone* — the worker "did nothing" durably.
2. **No collaboration.** Workers can't see or build on each other's changes.
3. **The A→B hand-off is dead.** If worker A hits a bug in B's area and B fixes it, A never sees the
   fix (it lives in B's workspace) → A is permanently stuck.

The root cause was a **framing error** (corrected 2026-07-02): the governance model lumped **push**
with deploy/delete as "irreversible." It isn't. **A commit + push to a *feature branch* is additive
and reversible** — the little-coder git-proxy already allowed it; only the bridge floor-guard
wrongly blocked it. The gate belongs at **merge-to-`main` / deploy**, not at push.

With that corrected, the delivery pipeline becomes possible: workers publish branches (D0, built),
and a feature is promoted through **PR → autonomous test → human-gated merge → deploy → human
testing** — so the human only sees feature-complete, self-tested work, not bugs the agents should
have caught themselves.

---

## 1. The pipeline (stages D0–D6)

```
 effort work ─▶ D0 commit + push  agent/<effort>        (per-effort branch; BUILT ✅)
                     │
   feature complete  ▼
                D1 open PR: agent/<feature> → main       (the promotion artifact)
                     │
                D2 autonomous test series (on the PR):
                     unit ─▶ integration ─▶ Stage-4 dry-run ─▶ [web] preview-deploy + AI-browser test
                     │            (any red → back to the owning effort; re-ground → fix → re-push)
                D3 differently-goaled review (§4.4, BUILT) attached to the PR — advisory to PM
                     │
                D4 ⛔ HUMAN-GATED MERGE to main   ◀── the elevation to human testing (hard-rule #4)
                     │
                D5 deploy to a preview/staging env (human-gated)
                     │
                D6 human testing  ──(pass)──▶ done / release   ──(fail)──▶ new effort
```

- **A branch per effort; a PR per feature.** Small efforts share a feature branch; a "feature" is
  the operator-intent thread (UX-FLOW §0) — the PM decides when the constituent efforts are
  feature-complete and opens the PR.
- **Everything is additive until D4.** Branches, commits, pushes, PRs, preview deploys are all
  reversible. The single irreversible human gate is **merge to `main`** (D4) and **production
  deploy** (a hardened D5). This is exactly the corrected hard-rule #4 line.
- **The test series is the point.** D2 is what "saves the human from finding issues the agent should
  see": the agents run unit + integration + an isolated dry-run, and — for web products — **deploy
  to a preview and drive it with an AI browser** before anything reaches a human.

---

## 2. Stages in detail

### D0 — commit + push on done ✅ BUILT (2026-07-02)
Each effort works and, on completion, publishes `agent/<effort>` (`orchestrator._publish_effort`):
`checkout -b agent/<effort>; add -A; commit; push -u origin agent/<effort>`. Additive/routine per
the corrected floor. The completion summary reports the branch (`git fetch origin agent/<effort>`).
Makes work durable, visible, hand-off-able. *(This is the foundation D1–D6 build on.)*

### D0.f — fork/upstream onboarding ✅ BUILT (2026-07-03, the MonoGame case)
**The problem, concretely.** The operator's MonoGame use case is working on a **fork** of an
upstream repo. That workflow needs **two remotes**: `origin` = the fork (the worker's push target)
and `upstream` = the parent (fetch-only, to pull in others' changes). But the little-coder git-proxy
**blocks `git remote add`** by design ("remotes are operator-baked — no new remotes mid-task",
`git_proxy._HARD_DENY`/`remote-mutate`), and `git fetch`/`git push` are allowed **only against an
operator-pre-configured remote** (`_guard_remote_arg` → `configured_remotes`). So a worker **cannot
set up a fork workflow itself** — which is *exactly* the MonoGame transcript: the worker tried
`clone` / `init` / `remote add`, each blocked, because it was flailing in the sandbox instead of a
governed `/project` clone. (The onboarding fix already routes new projects through `/project`; this
adds the second remote that a fork needs.)

**The mechanism — bake `upstream` at setup, via the REAL git binary.** Adding `upstream` is an
**operator setup action**, so it rides the *same privileged path little-coder already uses to clone*
(`workspace.clone` runs `self.real_git` directly, outside the proxy — design §12.3). After the clone,
the daemon runs `real_git remote add upstream <parent-url>`. Once baked, `upstream` is a *configured*
remote → the worker may `git fetch upstream` and `git merge --no-ff upstream/<branch>` freely (the
proxy allows fetch/merge to configured remotes), while it still can only **push to `origin`** (its
fork). The worker never gains remote-*mutate* power — the invariant holds; only the operator (bridge)
bakes remotes.

**Grammar + flow (BUILT).**
`/project add <name> <fork-url> --upstream <parent-url> [TOKEN_ENV]` (also onboardable in plain
language — the PO captures a described fork + parent). The bridge:
1. Records `upstream_url` on the `Project` row (additive column `projects.upstream_url` + the same
   self-heal migration as `token_env`) — **the persistent source of truth** (see survival note).
2. On **every** focus, passes `upstream` (+ a read-scoped `upstream_token` if the parent is private)
   to little-coder's `/project` (`ProjectRequest.upstream`/`upstream_token`); the daemon, **after**
   the clone, runs `real_git remote add upstream <url>` (`workspace.add_upstream_remote`).
3. Widens the worker git-egress allowlist to **both** hosts — `projects.hosts()` now yields each
   fork's parent host too, so it survives every egress re-render without a manual allow.
4. **Never** injects the deploy token into the `upstream` URL — `upstream` is fetch/read only; the
   token is `origin`'s push credential. A *private* upstream (e.g. an org parent) gets its own
   **read-scoped** token by the per-owner convention `LC_<PARENT_OWNER>_TOKEN`
   (`orchestrator._project_upstream_token`).
5. Fences the push side belt-and-braces: `add_upstream_remote` sets the push URL to a no-op
   (`remote set-url --push upstream DISABLED-fork-parent-is-fetch-only`) so `git push upstream` fails
   fast rather than reaching the parent — the worker publishes only to `origin` (its fork).

**Survival — the ephemeral-workspace discipline (this is the operator's rebuild concern).** The
`upstream` remote lives in the workspace clone, which little-coder **wipes on every `/project`
switch** and which is a named volume that a rebuild/`down -v` can clear. So it is **never assumed to
persist**: the bridge re-bakes it from the `Project` record (in the bridge's persistent DB) on
**every focus**. Container rebuilds are safe two ways — (a) our extension is the Python `littlecoder`
*wrapper* (it invokes the upstream npm CLI as a subprocess; a `little-coder@<ver>` bump + rebuild
re-`COPY`s our `src/` unchanged, never patching upstream), and (b) the fork's remotes are re-derived
on the next focus, not restored from the old workspace. `add_upstream_remote` is idempotent
(`remote add … || set-url …`) so a re-focus onto an unwiped workspace is also correct. NB — this
project `upstream` is distinct from little-coder's OWN `/admin/upstream/pull` (the fork-parent of the
*tool*, self-improvement).

**Worker sync workflow (once baked, all proxy-allowed):**
`git fetch upstream` → `git merge --no-ff upstream/main` (the proxy *requires* `--no-ff`). Merge
conflicts surface as an ordinary effort (escalate → operator), never a silent stall.

**The cleaner long-term path (API-level, no in-workspace remote surgery).** The **GitHub MCP server**
(or `gh` CLI) does fork + "sync fork" + **cross-fork PR** at the API level — `create_fork`, sync, and
a PR whose head is `fork:branch` and base is `upstream:main` — with no `remote add` in the workspace
at all. That's the recommended substrate once the delivery lane adopts MCP (see the resources note in
`IMPLEMENTATION-NOTES.md`). The real-git `remote add` above is the **substrate-native path built here**
— it works today with the existing little-coder and doesn't wait on the MCP/browser build.

**Note for D1:** when a project is a fork, the promotion PR (D1) is **cross-fork** — head
`fork/agent/<feature>`, base `upstream/main` — and its merge (D4) targets the *upstream* and is
therefore doubly human-gated (it's a contribution to someone else's repo).

### D1 — PR creation (the promotion artifact)
When the PM judges a feature complete, the bridge opens a PR `agent/<feature>` → `main` via the git
host API. The PR body = the intent thread + the plan + the effort branches + the review verdicts.
**Open decision OD-DP1:** mechanism — GitHub REST via a PAT with `pull_request` scope (a *separate*
token from the clone `LC_DEPLOY_TOKEN`, least-privilege), or the `gh` CLI in a worker. The PR is the
place D2/D3 results attach and D4 (merge) is gated.

### D2 — autonomous test series (on the PR)
A **CI-style pipeline** the bridge orchestrates, red-gates each stage:
- **unit + lint** — run the repo's own test/lint (a worker step, or a dedicated runner).
- **integration** — bring the feature up against its dependencies (compose-in-a-sandbox).
- **Stage-4 dry-run** (already designed, UX-FLOW §4) — isolated rehearsal; detect cascading breaks.
- **[web products] preview-deploy + AI-browser test** — deploy the PR to a throwaway preview env
  and drive it with an **AI browser** (navigate, assert flows, capture console/network errors).
  Any red → route back to the owning effort (re-ground → fix → re-push → re-test), never forward.
- **Open decision OD-DP2 (substrate):** the AI browser (Playwright) was **deliberately excluded**
  from little-coder (control-plane decides / open-terminal executes; browser deferred B1/B2). So
  D2's web leg needs a **new capability**: a dedicated browser-testing lane/container (Playwright +
  a vision-capable model), isolated with its own egress. This is the largest new build here.

### D3 — differently-goaled review ✅ BUILT, re-targeted to the PR
`stop_gates.review` (P4.4–4.7) already produces differently-goaled verdicts. D3 **attaches** them to
the PR as advisory input to the PM — **never a merge gate an agent can game** (F4). A flag still
freezes + escalates (built). Reviewers optimize to *find* problems, not approve.

### D4 — human-gated merge to `main` ⛔ (the elevation)
Merge to `main` is **irreversible → human-cleared** (hard-rule #4). The bridge presents the PR
(green tests + review + dry-run) to `#mgmt`; the operator `approve`s → the bridge merges (`--no-ff`,
via the host API). This is UX-FLOW "elevate to human testing," made concrete: the human sees a
feature-complete, self-tested PR, not raw agent output. **No auto-merge**; no agent merge authority.

### D5 — deploy to preview/staging (human-gated)
On merge, deploy to a **preview/staging** environment (never prod without a second explicit gate).
Deploy is irreversible/external → human-cleared. Reuses the D2 preview mechanism.

### D6 — human testing → release
The human tests the merged feature on staging. Pass → release (a final human-gated prod deploy).
Fail → a new effort (the loop closes back to intake).

---

## 3. Governance reconciliation

| Governance point | How this doc obeys it |
|---|---|
| Hard-rule #4 (irreversible = human-gated) | **additive push/PR/preview = routine; merge-to-main + deploy = gated** (the corrected line, §8 #5). |
| Review is advisory, never a merge gate (F4) | D3 attaches verdicts to the PR; **D4 merge is the human's**, not the reviewers'. |
| Escalate up, never around (F3) | a red test / review flag routes **back to the owning effort + up to the PM/operator**, never sideways to force a merge. |
| Stop-gates (§4.5) | D2's stages ARE stop-gates — each red halts promotion until cleared. |
| Bus-only + observable (§5) | PR + test results post to the effort/feature thread; the operator can read/join. |

---

# PART B — Implementation plan (DP.1–DP.6)

Status: ✅ done · ⬜ todo · 🚩 decision-gate. Each phase has a done-when.

- **DP.0 ✅ commit + push on done** — `_publish_effort` + branch-per-effort + corrected floor.
  *Done-when:* an effort against a repo pushes `agent/<effort>` and reports it (BUILT + tested).
- **DP.0f ✅ fork/upstream onboarding** — `/project add … --upstream <url>` (+ NL onboarding). Bridge:
  `Project.upstream_url` (additive column + migration) + `_effort_upstream`/`_project_upstream_token`
  → `router.wake(upstream=…)` → `harness.set_project(upstream=…)` → `ProjectRequest.upstream`;
  little-coder `workspace.add_upstream_remote` runs `real_git remote add upstream` post-clone
  (operator path, §12.3), idempotent, push-fenced; dual-host egress via `projects.hosts()`. Re-baked
  every focus (ephemeral workspace). *Done-when:* ✅ a fork project onboards, the worker can
  `git fetch upstream`/`merge --no-ff upstream/main`, `git push upstream` is refused (only `origin`).
  Tests: `test_fork_upstream.py` (7) + `test_workspace.py::test_add_upstream_remote_*` (3). **Requires
  rebuilding `little-coder:local`** (a daemon/workspace change) to go live. *(Substrate-native;
  independent of the MCP/browser build.)*
- **DP.1 ⬜ Feature model + PR creation** — a "feature" groups efforts (intent thread); the PM opens
  a PR via the host API (OD-DP1 token). → `modules/delivery.py` (new). *Done-when:* completing a
  feature opens a PR `agent/<feature>→main` with the intent+plan+branches in the body.
- **DP.2 ⬜ Test-series orchestrator** — bridge-driven red-gated stages (unit/lint → integration →
  dry-run); each red routes back to the owning effort. *Done-when:* a PR with a failing unit test is
  blocked from D4 and re-opened as a fix effort.
- **DP.3 🚩 AI-browser test lane (web)** — decide + build the browser substrate (OD-DP2): a
  Playwright + vision-model lane, isolated egress, driven by the bridge on a preview deploy.
  *Done-when:* a web PR is deployed to a preview and an AI-browser run asserts a core flow + reports
  console/network errors before any human sees it.
- **DP.4 ⬜ Human-gated merge** — present the green PR to `#mgmt`; `approve` → bridge merges `--no-ff`
  via the host API (still hard-rule #4 gated). *Done-when:* no PR merges to `main` without a
  recorded operator approval; merge is `--no-ff`.
- **DP.5 ⬜ Preview/staging deploy** — deploy the merged feature to staging (human-gated), reusing
  the DP.3 preview mechanism. *Done-when:* a merged feature is reachable on staging for D6.
- **DP.6 ⬜ Human-testing loop** — surface the staging URL + a test checklist to `#mgmt`; pass →
  release effort, fail → new fix effort. *Done-when:* the operator's pass/fail closes or re-opens.

### R (3-place change)
DP.3's browser lane is a **new container** (Playwright + egress) → the 3-place change (compose +
recovery + stack-map). DP.1/DP.2/DP.4–6 are bridge-internal (a new `modules/delivery.py` + a git-host
client) — no new container. A **PR-scoped token** is a new secret (env only).

---

## 4. Open decisions
- **OD-DP1** — PR/merge mechanism + token: GitHub REST (separate least-privilege PAT with
  `pull_request`+`contents`) vs `gh` CLI. Merge stays human-gated regardless.
- **OD-DP2** — AI-browser substrate: a new Playwright+vision lane (recommended, isolated) vs
  re-enabling the browser inside open-terminal (rejected earlier for blast-radius). This is the
  gating decision for the web-testing leg.
- **OD-DP3** — "feature" granularity: 1 effort = 1 PR (simple) vs N efforts = 1 feature PR (matches
  the intent thread; recommended). The PM owns the feature-complete judgment.
- **OD-DP4** — preview/staging + prod environments: where they run, and the second gate for prod.
- **OD-DP5 (resolved for now)** — fork/upstream substrate (D0.f): **BUILT the real-git
  `remote add upstream` at setup** (works today with existing little-coder, substrate-native). The
  **GitHub MCP server / `gh` fork+sync+cross-fork-PR** API path stays the cleaner long-term option
  (no in-workspace remote surgery) and is **not mutually exclusive** — the baked remote works now;
  adopt MCP for PR/sync when the delivery lane lands. Private-upstream auth = a **read-scoped** token
  via the `LC_<PARENT_OWNER>_TOKEN` convention (built).
