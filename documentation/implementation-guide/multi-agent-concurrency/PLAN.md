# PLAN — Multi-agent concurrency: worktrees, test isolation, agent-coordinated merges

**Status:** Phase A + D-lock **BUILT** (commit c1ecc30) and Phase B **BUILT** (bridge
worktree-per-thread, default off, awaiting a restart to go live). Phase C (per-plane
`compose.test.yml` + reaper) is still plan-only — deliberately deferred while the wiki
viewer is mid-build. Operator directive: multiple Claude
sessions (VS Code extension AND Mattermost bridge) working simultaneously without
stepping on each other's code or each other's test containers; the agents themselves
coordinate the merges back together.

**Non-negotiables inherited from operator policy (unchanged by this plan):**
`main` untouched/human-promoted; `development` is the live-hosted line and merges into
it require validation + testing evidence; worktree-per-session for git-mutating
sessions (CLAUDE.md, 2026-08-23); OB1 gitlink bumps only to SHAs reachable on the OB1
remote; deploys to prod containers stay explicit, gated steps.

---

## 0. Current state (verified 2026-08-28, not assumed)

| Fact | Where |
|---|---|
| One checkout, one worktree; every bridge session runs `cwd=REPO` in the operator's tree | `bridge.py:103` (`REPO`), `:597` (`Popen(cwd=REPO)`) |
| Worktree-per-session is **policy without a mechanism** — nothing provisions one | CLAUDE.md conventions |
| The extension harness has native `EnterWorktree`/`ExitWorktree` tools: create `.claude/worktrees/<name>`, switch the session in; base ref governed by `worktree.baseRef` (default = origin default branch, NOT `development`) | tool schemas, probed |
| Subagents support `isolation: "worktree"` (auto-cleaned if unchanged) | Agent tool |
| `.claude/worktrees/` is **not** gitignored yet | `git check-ignore` |
| Worktrees materialize tracked files only: ai-stack = 860 files, OB1 = 953 — cheap. Heavy dirs (`backup/`, models) are gitignored → never duplicated | `git ls-files` |
| A worktree is **missing every gitignored runtime file**: `.env` (17 KB live values), `OB1/docker/.env`, `.env.test` | `ls .env*`, `.gitignore` |
| `git worktree add` does **not** populate the OB1 submodule; needs `git submodule update --init` per worktree | git behavior + `.gitmodules` |
| `core.autocrlf=true`; `.gitattributes` pins `*.sh`/`.githooks/*` to LF. Known trap: a fresh worktree checkout CRLF-poisoned `.sh` files consumed by docker builds before the attributes rule existed | repo config; memory `wiki-backfill-churn-loop-fix` |
| Test-container traps already paid for: never `docker rm` by port filter; `.env` drift leaves stale tokens in long-lived containers; backup sidecars can exit-0 with zero artifacts | memories |
| Per-caller LiteLLM **virtual keys + queue lanes exist** (J.1) — the inference plane is already multi-tenant | CLAUDE.md |
| Cross-session claim precedent: `claimed-threads.json` (bridge skips operator messages in claimed threads) | 08-24 session-separation build |
| Two-driver collision damage is documented history (one shared checkout, swept gitlink, duplicate org intents) | memory, 08-23 |
| Bridge caps concurrent turns at `MAX_CONCURRENT=2` | `bridge.py` |

## 1. Direction (the "modern" answer, concretely)

The current industry pattern for this problem is exactly three primitives, none of
which we need to invent:

1. **Worktree per agent** for code isolation — harness-native where the harness
   provides it (extension: `EnterWorktree`; subagents: `isolation:"worktree"`),
   provisioned by the bridge where it doesn't (Mattermost). One convention, two
   provisioners, single shared helper script so behavior can't drift.
2. **Ephemeral per-agent test environments** (the testcontainers philosophy applied
   to this stack): a session never touches prod containers/tags/volumes; it gets its
   own compose *project* with no host ports, labeled for reaping.
3. **A merge queue, not merge-anarchy**: merges into `development` are serialized by
   a lock; the merging agent rebases onto the latest tip, re-validates, merges with
   evidence; conflicts are negotiated agent-to-agent over Mattermost — the one bus
   both entry points already share.

Deliberately NOT built: a bespoke orchestrator daemon, auto-merge without gates, or
agent-org integration (its workers already have container isolation + git-proxy;
convergence later, not now).

---

## 2. Phase A — shared conventions + plumbing (no behavior change)

**A1. Ignore the worktree root.** Add `.claude/worktrees/` to `.gitignore` (today a
harness worktree would show up as an untracked dir in every `git status`).

**A2. One provisioning script,** `scripts/worktree/new-worktree.ps1` (ASCII, PS5.1):

```
new-worktree.ps1 -Id <short-id> [-Base development]
  → git worktree add .claude/worktrees/wt-<id> -b work/<id> <Base>
  → git -C <wt> submodule update --init          # OB1 at the pinned SHA
  → copy .env, .env.test, OB1/docker/.env into the worktree  (COPY, not symlink —
    symlinks need privilege on Windows and compose resolves --env-file relative to cwd)
  → verify: no CRLF in tracked *.sh inside the worktree (the docker-build trap);
    print the worktree path + branch
  → register in scripts/worktree/state/worktrees.json
    {id, path, branch, owner: {kind: extension|bridge, session/thread}, created}
```

**A3. Env freshness,** `sync-worktree-env.ps1`: re-copy the three env files when the
source is newer (the `ao-worker-stale-deploy-token` failure class, applied to
worktrees). Bridge runs it before each turn; extension sessions run it on entry.

**A4. Extension-side determinism** — the part the operator flagged as unclear. The
`EnterWorktree` tool's own contract is: *use it when CLAUDE.md/memory instructions
direct you to*. So determinism is a CLAUDE.md amendment, which makes the harness
tool self-triggering:

> Any session about to MUTATE git state (stage, commit, branch, gitlink) must first
> enter its own worktree: `EnterWorktree` (or `new-worktree.ps1` + `EnterWorktree
> path:`). Read-only work in the main checkout stays fine. The operator's own
> explicitly-stated main-checkout work is exempt.

The trigger is "first mutating intent", not session start — cheap reads stay cheap,
and the rule is checkable in review (a commit from the main checkout by a session is
a violation on its face).

**A5. Base ref.** Scripted creation branches from `development` explicitly (policy),
so the harness `worktree.baseRef` default (origin default branch) never decides.
Prefer `new-worktree.ps1` + `EnterWorktree path:` over bare `EnterWorktree name:`
for repo work; bare EnterWorktree remains fine for throwaway experiments.

**Verify A:** create/enter/exit a worktree; `docker compose -f <wt>/inference/...
config` resolves with the copied `.env`; `.sh` files LF-clean; registry row appears.

## 3. Phase B — bridge: worktree-per-thread

**B1. Directive first, default later.** New thread directive `worktree: on|off`
(same grammar as `model:`/`mode:`), env `BRIDGE_WORKTREE_DEFAULT` (start `off`;
flip to `on` after a week of clean use). On a worktree-enabled thread the bridge
calls `new-worktree.ps1 -Id mm-<thread8>` lazily at the thread's first turn, stores
`{worktree, branch}` in the thread's `state.json` entry, and `run_turn` gains a
`cwd=` parameter (today it hardcodes the global).

**B2. Lifecycle.** Reuse across resumes (same thread = same worktree = same branch).
`close`: if the branch is merged and the tree clean → `git worktree remove` +
registry cleanup; if dirty/unmerged → keep, and say so in the close post (the
existing explain-your-removals discipline). `sessions` listing shows the worktree
column from the registry.

**B3. Session awareness.** REMOTE_NOTE addition: "you are in your own worktree
(branch `work/mm-<id>`), cut from development; merge via the merge-queue protocol
(§5); never `cd` back into the main checkout to mutate it."

**B4. What this does NOT change:** `MAX_CONCURRENT` still caps simultaneous turns
(worktrees isolate state, not CPU); the sysadmin persona keeps `cwd=REPO` — its job
is the host, not the codebase (`BRIDGE_WORKTREE_DEFAULT=off` pinned in its launcher).

**Verify B:** two bridge threads editing the same file produce two clean branches,
zero cross-contamination (the RED case is documented 08-23 history); `close` on a
merged thread removes; `close` on a dirty one keeps + reports.

## 4. Phase C — per-agent test containers

**C1. The rule** (CLAUDE.md + REMOTE_NOTE): work sessions never mutate prod
containers, `:local` image tags, or prod volumes. Testing happens in the session's
own compose project; touching prod is a *deploy*, which stays an explicit gated step
(allow-list / operator approval — exactly the machinery fixed on 08-28).

**C2. Per-session compose project.** For the plane under test:

```
docker compose -p test-<wt-id> -f <plane>/docker-compose.yml \
  -f <plane>/compose.test.yml --env-file <wt>/.env up -d <services>
```

where each plane grows one `compose.test.yml` override that (a) removes ALL host
port publishing, (b) remaps named volumes to `test-<wt-id>-*` ephemerals, (c) labels
everything `com.ai-stack.test-session=<wt-id>`. Probing happens container-to-
container (`docker exec` + in-network curl) — the pattern the wiki sidecar work
already proved. Start with the planes agents actually iterate on (OB1 wiki viewer,
search, coder); the inference plane is explicitly NOT duplicated (§C4).

**C3. Image tags.** Test builds tag `<image>:wt-<id>`, never `:local`. The deploy
step (post-merge, gated) is the only place `:local` is retagged — same shape as the
wiki deploy that motivated the allow-list.

**C4. Shared planes stay shared, deliberately.** GPU/inference: one 3090 — test
traffic goes through the gateway on the session's own virtual key/lane (J.1 already
provides admission + fairness). Live Mattermost, OWUI, prod SurrealDB: read
freely, mutate never; tests needing prod-shaped data use a copy-volume snapshot
recipe, not the live volume. This is an honest limit, not a gap: duplicating the
GPU plane is impossible and duplicating stateful prod stores per-session is not
worth the disk.

**C5. Reaper.** `scripts/checks/test-reaper.ps1`: remove containers/volumes/images
carrying `com.ai-stack.test-session` older than 24 h or whose registry entry is
gone; report-only mode first run; wired beside the existing watchdog checks. By
label only — never by port/name pattern (documented trap). Also reaps stale
registry rows and prunable worktrees (`git worktree prune`).

**Verify C:** two sessions run the same plane's test stack concurrently (distinct
projects, zero port conflicts); reaper dry-run lists exactly the labeled leftovers
and nothing else; prod `docker ps` diff is empty throughout.

## 5. Phase D — agent-coordinated merge queue

**D1. The lock.** `scripts/worktree/state/merge-lock.json`, taken by atomic
create-exclusive; contents `{owner, worktree, thread/session, taken_at, ttl_min:30}`.
Holder refreshes `taken_at` while working; a lock past TTL may be taken over after
posting a takeover note to the owner's thread. Filesystem beats a port here: both
entry points share the disk, and the file carries metadata a port can't. (Precedent:
bridge lock ports 4829x + `claimed-threads.json`.)

**D2. The protocol** (a doc first, `/merge-queue` skill once stable — agents follow
it from REMOTE_NOTE/CLAUDE.md):

1. **Self-validate in your own worktree**: ruff/targeted tests/compose `config`
   sanity for touched planes, plus whatever the task's own RED→GREEN evidence is.
2. **Acquire the merge lock** (wait politely; poll ≤1/min).
3. `git fetch` + **rebase your branch onto current `development`**; re-run the gates
   (the rebase is where cross-agent breakage surfaces — that's the point).
4. **Merge `--no-ff` into `development`** with the evidence in the merge commit
   message (the branch-policy requirement, made mechanical).
5. Release the lock; **announce** in your own #claude-sessions thread: branch,
   files, evidence, whether a deploy is still pending.
6. Clean up: remove the worktree if done (`close` handles bridge threads).

**D3. Conflict coordination — who talks to whom, and how.** Ordered escalation:

- **Mechanical conflicts** (adjacent edits, both-added): the merging agent resolves
  during rebase. First-merged wins the base; **the later merger adapts** — never
  re-litigate landed work in a rebase.
- **Semantic overlap** (both touched the same subsystem with competing intents):
  the merging agent posts into the *other* session's Mattermost thread describing
  the conflict and its proposed resolution. The bridge's follow/auto-wake machinery
  wakes that session with the post as its next prompt — this is the existing
  mechanism, not new code. The two converge in-thread; the merger implements the
  agreed resolution. Extension sessions are reachable the same way: every worktree
  registry row carries its owner's MM thread (extension sessions open one in
  #claude-sessions when they take a worktree — one root post, its id in the
  registry). Mattermost is the coordination bus for *both* kinds; SendMessage/
  ListAgents is explicitly NOT used for this (documented 08-23: injecting into a
  possibly-mid-turn headless peer derails it).
- **No convergence / destructive disagreement** (one wants to delete what the other
  depends on): stop, `AskUserQuestion`/ask in-thread — the operator is the
  tiebreaker. Never resolved by force-push; `development` history is append-only.

**D4. Gitlink discipline in merges.** A merge carrying an `OB1` gitlink bump must
verify the SHA is reachable on the OB1 remote first (existing rule; agent-org
already gates deliveries on exactly this — reuse the check's shape).

**D5. Unchanged operator gates.** Evidence-bearing merges to `development`: yes,
agents do them (that is this phase). `main` promotion: human. Prod deploys: gated
allow-list step, post-merge. Pushes: only when asked (git-handling boundary).

**Verify D:** staged two-agent drill — both branches touch the same file with
conflicting edits; agent 1 merges; agent 2's rebase conflicts, negotiates via
thread wake, lands adapted; lock never held by two owners (audit the state file
history); a third session confirms `development` green after both.

## 6. Rollout order

**A → D2-as-doc → B → C → D-automation.** The protocol doc is useful the day it
exists (manual worktrees already happen); bridge provisioning makes it default;
test isolation unlocks true parallel testing; the skill/automation lands last,
after the drill in §5 has been run at least once for real. Each phase ships with
its RED case demonstrated first (the shared-checkout collision and port-collision
RED cases are already documented history — cite, don't re-stage the damage).

## 7. Risks and honest limits

- **Submodule worktrees:** each worktree owns a full OB1 checkout — fine at 953
  files, but OB1 *builds* (node_modules) are per-worktree too; the reaper and
  `close` keep disk bounded. Keep ids short (`wt-<8>`) — Windows MAX_PATH plus
  node_modules depth is a real ceiling.
- **CRLF in worktrees:** `.gitattributes` now covers `*.sh`/hooks; any NEW
  docker-consumed text file type that breaks gets an attributes rule, not a
  per-worktree config fork (`core.autocrlf` is repo-shared unless
  `extensions.worktreeConfig` — avoid enabling that; one more config plane to
  drift).
- **`.env` drift** between checkout and worktrees — mitigated by sync-on-turn
  (A3), same class as the ao-worker stale-token incident.
- **Deploy verification isn't parallelizable:** anything that must be proven
  through the real caddy/tailnet chain (`:8444`) happens post-merge in the deploy
  step, serialized by nature. Parallel test envs cover pre-merge confidence, not
  prod verification.
- **The bridge is still one process per persona** — worktrees parallelize sessions,
  not one thread's turns; `MAX_CONCURRENT` is the real throughput knob and stays
  operator-set.
- **Lock liveness:** an agent dying mid-merge leaves a TTL'd lock; takeover posts
  to the dead owner's thread, and the half-done rebase lives only in that owner's
  worktree — `development` is never left mid-merge (rebase happens on the work
  branch, merge is a single fast operation).

## 8. Decision log

- Mattermost (not SendMessage) as the inter-agent bus — both entry points share
  it, it's auditable, and the 08-23 peer-injection incident rules the alternative
  out.
- Copy env files, don't symlink or re-point `--env-file` at the main checkout —
  Windows privilege + relative-path resolution both bite; copies + freshness sync
  is dumb and correct.
- File-based merge lock, not a port — metadata + shared filesystem beats a
  liveness-only signal for a queue where the holder needs to be identifiable.
- Later-merger-adapts as the conflict default — makes merge order irrelevant to
  correctness and removes the incentive to race for the lock.
