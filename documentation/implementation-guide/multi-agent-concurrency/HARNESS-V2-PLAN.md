# PLAN v2 — the agent harness: anchors, runners, profiles, module boundary

Status: PLANNED 2026-08-28. Supersedes nothing in [PLAN.md](PLAN.md) — that plan
built the pipeline (worktrees, queue, roles, leases, human gate) and stands. This
plan fixes what the first real run of that pipeline exposed, and makes the whole
thing a switchable, configurable module.

## 0. What the first soak actually proved

Two work items ran the full loop end to end: developer → test plan → tester →
cycles → human gate → reviewer → merge. The *mechanism* worked. Every check it
was designed to make, it made. Four real defects in the harness were found by the
agents using it, not by me.

The *outcome* was still wrong, and the operator called it:

| artifact | operator-facing | investigative tail |
|---|---|---|
| `search/README.md` | 171 lines | 54 (24%) |
| `coder/README.md` | 202 lines | 169 (46%) |

Nearly half of the coder README is a defect log and a "where the docs disagree"
table. That is not a README. The agents did not fail; **the harness had nothing
that asked whether the artifact was the thing requested.** The test plan verified
that each claim was *true*. The review verified that each claim was *supported*.
Nothing verified *fitness for purpose*, and my own task prompts had told the
agents that finding disagreements was the primary deliverable — so they produced
exactly what was asked for.

A pipeline that validates correctness and never validates intent will reliably
ship a correct answer to the wrong question.

## 1. The anchor (operator's answer, 2026-08-28)

> "the answer for the work is an anchor. The user should still be in
> communication with the agent working. The original prompt should be phrased and
> confirmed with the end user to ensure alignment then generating a plan before
> work begins."

The anchor is a first-class field on the work item, written **before** any work
and **confirmed by the operator**. It is the thing the tester tests against and
the reviewer judges against — not the prompt, which is ephemeral and lives in one
agent's context.

### 1.1 Anchor shape

| field | what it pins down | why it exists |
|---|---|---|
| `goal` | one sentence, the outcome in the operator's words | stops goal drift across test cycles |
| `artifact` | path + kind of the thing that will exist | "a README" vs "a report" is a decision, not a style |
| `audience` | who reads or runs it | the field that would have prevented the coder README |
| `acceptance` | list of objectively checkable criteria | what the tester tests |
| `out_of_scope` | explicit non-goals | where "and also fix what you find" gets refused |
| `findings_sink` | path for incidental discoveries | the knowledge is real; it just is not the artifact |

`findings_sink` is the specific repair for the soak failure. The agents found
genuine drift between docs and compose files. That was valuable. It belonged in a
note, not inside the deliverable. Deleting the finding would have been a loss;
misplacing it was the bug.

### 1.2 Anchor states, added ahead of the existing pipeline

```
anchor-draft  ->  anchor-confirmed  ->  in-progress  ->  ready-to-test  -> ...
   agent            OPERATOR             developer        (unchanged from v1)
```

- `-Propose` creates the item in `anchor-draft` with the anchor the agent wrote.
- `-ConfirmAnchor -By <operator>` is a **human gate**, the second one in the
  pipeline (the first was pre-review). It may also amend fields.
- `-Submit` refuses (exit 5) if the anchor is not confirmed.
- The reviewer gains an explicit fitness verdict: green tests plus an artifact
  that misses the anchor is a `-Reject`, and that is now a supported outcome
  rather than an argument.

### 1.3 Why a gate at the START and not only before review

Because the cheapest moment to correct a misunderstanding is before the work
exists. The pre-review gate catches "the world moved"; the anchor gate catches
"we were never building the same thing." The soak needed the second one.

## 2. Runners: cloud or local, per role

> "make the target model cloud or local targetable for the worker, tester and
> reviewer agents. For now the default for all 3 are cloud-opus."

"Cloud" and "local" are not two values of one model setting — they are two
different execution substrates, and pretending otherwise would produce a config
that lies:

- **cloud** = a Claude Code agent (a subagent here, or `claude -p` from the
  bridge). Model selection is a model alias.
- **local** = the little-coder control plane, which reaches `llama-cpp` through
  LiteLLM. Dispatch is `POST /tasks`; progress is `GET /tasks/{id}/events`.

So the config names a **runner** per role, and the runner carries a model:

```
worker:   { runner: claude-code,  model: opus }
tester:   { runner: claude-code,  model: opus }
reviewer: { runner: claude-code,  model: opus }
```

with `runner: little-coder` as the local alternative. Adding a third substrate
later is a new runner row, not an edit to the pipeline — the roles depend on the
runner interface, not on a concrete runner.

**Scope honesty:** the `claude-code` runner is fully implemented because it is
what runs today. The `little-coder` runner is implemented to dispatch-and-poll
against the daemon API above; it is not the default and is not claimed as proven
until a work item has actually completed through it. The plan says so here so
nobody reads config support as a working feature.

## 3. Profiles

A profile is a named composition of role → runner+model. It is an attribute of
the configuration, switchable at runtime like the model directive already is.

| profile | worker | tester | reviewer |
|---|---|---|---|
| `all-cloud` (default) | cloud opus | cloud opus | cloud opus |
| `all-local` | little-coder | little-coder | little-coder |
| `local-work-cloud-review` | little-coder | little-coder | cloud opus |
| `cloud-work-local-test` | cloud opus | little-coder | cloud opus |

Rules:

- Default profile for every surface is `all-cloud`.
- **Extension sessions are locked to `all-cloud`** (`profile_locked: true`).
  Operator decision: the surface the operator drives interactively should never
  silently degrade.
- Mattermost sessions may switch with a `profile: <name>` directive, the same
  shape as the existing `model:` and `worktree:` directives.

## 4. Configuration — no hardcoded values that deserve a file

> "There shouldn't be hardcoded variables that would benefit from configuration
> files"

Everything below is currently a literal in a script and moves into
`harness.config.json`:

| value | today | moves to |
|---|---|---|
| claim TTL, 60 min | `queue.ps1` param default | `pipeline.claim_ttl_minutes` |
| worktree root `.claude/worktrees` | `new-worktree.ps1` | `worktree.root` |
| branch prefix `work/` | two scripts | `worktree.branch_prefix` |
| env files copied into a worktree | `new-worktree.ps1` array | `worktree.env_files` |
| work-line env var name | `resolve.ps1` | `worktree.base_branch_env` |
| lease names | `lease-names.conf` | stays a policy file, read via config |
| role → state → duty | `queue.ps1` `$RoleRules` | stays code (it is behavior, not tuning) |
| default model | `bridge.py` literal `opus` | `profiles.all-cloud.worker.model` |

Precedence, resolved once in a loader and nowhere else:

```
built-in defaults < harness.config.json < harness.local.json (gitignored) < environment
```

Two readers, one file: `config.ps1` for the scripts and `config.py` for the
bridge. The file is the contract; neither reader owns defaults privately.

## 5. Module boundary and the on/off switch

> "ensure this agent orchestration is isolated as its own module ... turned on or
> off cleanly in both claude sessions ... and or mattermost sessions"

**Rename `scripts/worktree/` to `scripts/agent-harness/`.** The directory holds
the queue, the roles, the leases, the config and the verification drill;
"worktree" names one of five concerns and mislabels the rest. 19 references
across 6 files, all in this repo.

The module owns exactly:

- `scripts/agent-harness/**` — all code and its config
- `.git/agent-worktrees/**` — all shared state (already correctly anchored on
  `--git-common-dir`, not on the script location; see PLAN.md decision log)
- `documentation/implementation-guide/multi-agent-concurrency/**` — the docs
- `.claude/skills/merge-queue/**` — the operator-facing skill (D2)

Its only outward touch-points are a thin adapter in `bridge.py` and a pointer in
`CLAUDE.md`. `MODULE.md` states the public surface and what to remove to remove
the module.

Off switch:

| setting | effect |
|---|---|
| `enabled: false` | module off everywhere; scripts refuse with a reason, the bridge stops offering directives |
| `surfaces.mattermost.enabled: false` | off for bridge sessions only |
| `surfaces.extension.enabled: false` | off for sessions driven from the editor only |

"Off" must mean *inert*, not *degraded*: with the module off, nothing in the
normal workflow changes behavior, and every entry point says plainly that the
harness is disabled rather than failing obscurely.

## 6. Work items queued behind this plan

The defect list found while documenting the coder plane becomes the harness's
next real workload — each is a genuine repo fix and a test of the loop.

| # | fix | why it is a good harness test |
|---|---|---|
| 2 | `Repair-OpenTerminal` + 3 siblings target the empty anchor project | narrow, verifiable, has a real failing repro |
| 3 | `stack.ps1 health` cannot see a dead executor | requires a design choice, not just an edit |
| 4 | `CLEANUP-PLAN.md` K.4 record is wrong and was copied into 8+ docs | touches overlapping code; tests conflict handling |
| 5 | `little-coder-backup.sh` header says four volumes, five are mounted | trivial; a control for cycle-time measurement |
| 6 | `test_git_proxy.py` docstring describes a mount that does not exist | tests whether an agent will correct a *comment* honestly |

Plus the two carried from v1: **B2** (worktree column in the bridge's `sessions`
listing) and **D2** (the `/merge-queue` skill).

## 7. Secrets: what happened and what blocks it

Separate from the harness, but found by it.

**The exposure.** `env_file: ../.env` injects every variable in the root `.env`
into the container's environment. It is not about file access — `printenv` inside
`open-terminal` returns the Cloudflare tunnel token, the Authelia secrets, the
Mullvad key, the Tailscale auth key and the Mattermost bot tokens. Four services
carry the line: `open-terminal`, `little-coder`, the search `gateway`, and
`tailscale`.

**Why it was there.** Traced to the commit that introduced open-terminal
(`0bc099e`, "Add Open Terminal service and health checks"). The service was
written as:

```yaml
env_file:
  - .env
environment:
  - API_KEY=${OPEN_TERMINAL_API_KEY}
```

The `environment:` line is resolved by the compose CLI from the project
environment — it never needed `env_file:` at all. The `env_file:` line was
redundant on the day it was written, copied from the `openwebui` service above
it, which legitimately consumes a large slice of `.env`. It then survived two
moves (the compose split, and K.4 when the coder plane became its own project)
because a move preserves lines; it does not audit them.

**Why it got worse without changing.** `.env` grew from a handful of variables to
111 spanning every plane. The grant was written as "the whole file" and the file
became the whole stack. *A wildcard grant is safe until the thing it wildcards
grows* — and nothing in the repo was watching the size of the blast radius.

**Measured blast radius, coder plane:** 107 variables reach the containers only
via `env_file`. The little-coder source references **none** of them. The one grep
hit is `LOG_LEVEL` inside a vendored `.venv` copy of `mcpo`, whose service was
retired 2026-08-20.

**The fix.**

1. Delete `env_file:` from `open-terminal`, `little-coder` and the search
   `gateway`. All three already declare every variable they use explicitly; the
   backup sidecar in the same file is already written this way, so the correct
   pattern is present in the tree.
2. `tailscale` is the one genuine consumer: `TAILSCALE_AUTH_KEY`, `STATE_DIR`,
   `TS_SOCK` and eight Mattermost / LiteLLM-UI serve-route variables arrive only
   through `env_file`. Declare those eleven explicitly, then delete the line.
3. Add a pre-commit check that fails on `env_file:` pointing at the root `.env`,
   naming this incident. The rule is the durable part: **a service names the
   variables it needs.**

**On worktrees, which is a different question.** A worktree materialises tracked
files; `.env` is untracked, so git never places it in one. `new-worktree.ps1`
*copies* it in deliberately, so agents can bring a plane up. That copy can be
narrowed to a filtered subset per plane, and tracked paths can be excluded with
per-worktree `git sparse-checkout`. Neither is the container exposure, which
lives entirely in compose.

## 8. Order

1. Config module + loader (`config.ps1`, `config.py`, `harness.config.json`) and
   the rename to `scripts/agent-harness/`.
2. Anchor: states, verbs, gate, and the tester/reviewer duties that reference it.
3. Runners and profiles on top of the config.
4. Rework `coder/README.md` and `search/README.md` through the anchor — the
   honest test of whether the fix works.
5. Defects 2–6 as agent work items, observing and adjusting the harness as they
   run.
6. Secrets containment + the pre-commit check.
7. B2 and D2.

## 9. Later (operator, 2026-08-28) — the refactor trigger

Not built now. When a file passes ~1–2k lines or holds conflicting
responsibilities, that is a call for a refactor: agents land their in-flight
work, then a single agent refactors the file in its own worktree through this
same pipeline. Raised by the harness as an advisory to an overseer, not enforced
as a gate — the judgement of "conflicting responsibilities" is not mechanical.
