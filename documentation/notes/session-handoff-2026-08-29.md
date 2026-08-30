# Session handoff — the design / orchestration session, 2026-08-29

Disposable. This exists so a fresh session can take over the **operator-facing
design and orchestration role** without re-deriving two days of context. Delete
it once consumed, or once it goes stale.

**This is NOT the implementation handover.** That is `PLAN.md §5`, written for
the Opus session doing the building. This document is for whoever picks up the
role of *designing, orchestrating, and talking to the operator* — the seat this
session occupied.

---

## 1. What this session was

Started as "pin the Mattermost bridge's default model", became: build a
multi-agent work harness, run real agents through it, audit it against the
existing agent-org design, and produce a unification plan now in production.

The seat's job, concretely:

- **Design and plan** — write plans the operator confirms, then keep them true.
- **Orchestrate agents** — dispatch developers/testers/reviewers, relay findings
  between them, adjudicate collisions, and improve the harness from what the
  runs expose.
- **Talk to the operator** — report as statements, bring only real decisions,
  and never let a gate become a bottleneck.

It does **not** do the U0–U7 implementation. A separate Opus session is doing
that, right now, under `PLAN.md §C`.

## 2. State as of handoff (verified, not remembered)

```
branch : refactor/ai-stack-cleanup
HEAD   : d7d1676  docs(plan): §C autonomous execution
tree   : clean
```

**Landed this session** (all through the pipeline except where noted):

| commit | what |
|---|---|
| `d504e9e` | secrets — `env_file` scope + `check-env-file-scope.ps1` (pre-commit #5) |
| *(harness)* | `scripts/worktree` → `scripts/agent-harness`, anchor gate, config/profiles, drill 35→51. **Never pipeline-reviewed** — its commit message says so; retro test/review is still owed |
| `55b31e1` | bridge — `profile:` directive, worktree column, the component-path rename trap |
| `451ebfa` | the dark-factory unification plan |
| `97ee98d` `10700d1` | docs move phase 1 + the correction (one tracked file did move) |
| `fa06397` `e46663d` `fefe492` | the three reviewed items merged; worktrees retired |
| `d7d1676` | **§C autonomous execution** — the standing decision policy |

**In flight — the Opus implementation session** (do not interfere):

```
wt-dfu-inbox   work/dfu-inbox   queue: ready-to-test
wt-dfu-mem0    work/dfu-mem0    no queue item yet — mid-work
```

It is running U0 under §C. If it asks a question §C already answers, the fix
is to sharpen §C — not to answer the question for it a second time.

**Waiting on the operator** (three anchor-drafts from a `wt-podcast-fix`
session neither this seat nor the Opus session owns):
`podcast-delivery-key`, `podcast-script-fallback`, `bridge-bg-task-note`.

**Known, unqueued, real:**
- OB1 wiki compile does not re-arm after a failed compile — the failure path
  (`wiki-service.mjs` ~L1111) schedules nothing, so one transient failure kills
  the backfill loop until an external trigger. Boot race vs PostgREST
  `PGRST002`. Analysis is in the Mattermost thread; the fix is A) retry with
  backoff on failure, B) wait for the schema cache before the boot compile.
- `emergency-recovery.ps1` has 16 bare `docker compose` invocations, ~10 naming
  a service against the 0-service root project — same defect class as the
  watchdog fix, in the script you run when things are already broken. Deserves
  its own item; findings in `documentation/notes/watchdog-findings.md`.
- `agent-org` ao-worker-1/2 still carry `env_file: ../../.env`.
- The **deploy** of the watchdog fix: restarting `TailscaleHealthMonitor` makes
  22 repair paths live for the first time since 2026-08-21. Operator's.

## 3. The documents, in reading order

1. `documentation/implementation-guide/dark-factory-unification/PLAN.md` — the
   live plan. §A/§B/§C are binding; §0's A1–A14 audit verdicts are pinned
   anchors; §5 is the implementation handover.
2. `.../dark-factory-unification/DECISIONS.md` — the Opus session's judgment
   log. **Read it before answering anything** — the answer may already be there.
3. `documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md`
   — the protocol agents follow. Its "cases that keep earning their place" and
   ground rules were each paid for by a real failure.
4. `scripts/agent-harness/MODULE.md` + `harness.config.json` — the module.
5. `agent-org/docs/ORCHESTRATION-DESIGN.md` — the spine the unification
   preserves. Do not redesign what §14 marks BUILT + PROVEN.
6. The memory-plane plan now lives in the **separate private repo**
   `documentation-plans-ai-stack`, at `implementation-guide/agent-memory-plane/`.

## 4. How the operator works (learned, and worth honouring)

- **Away often, on a phone.** Trivial questions cost hours. Bring statements
  with a default and a path forward; reserve real questions for genuinely
  irreversible things.
- **Corrects framing, not just facts.** Two examples that changed the design:
  "review is for merge, clean code and different eyes — not intent" (intent is
  theirs, at the release gate), and "cycles happen when a test reveals an issue
  — that's the point of tests, not a route to review".
- **Values the agent-org design work and is right to.** Mode A/B, CDCL,
  tiered scope, finding→durable-check are theirs and they stay. Frame merges as
  composition, never replacement.
- **Will push back on unclear reasoning** ("I don't understand what openwebui
  has to do with the 3 efforts") — and is usually right. Answer with a
  measurement, not a rationalisation.

## 5. The behavioural rules this seat kept getting wrong

Recorded because they were expensive, and because the seat is not exempt from
the rules it writes for agents:

- **Verify before you relay.** Three times this session an agent's conclusion
  was passed on without opening the file, and twice it was wrong. Once it was a
  tester's "stronger reason" that a developer then disproved.
- **Read to the end of the function; check the stated REASON, not just the
  claim; a truncated search is not a search; your PATH is not the operator's.**
- **Check exit codes before recording outcomes.** A merge failed with exit 2,
  went unchecked, and `-Merged` recorded the pre-merge tip — the queue said
  "merged" while nothing had. `-Merged` now verifies reachability; the habit
  still has to be yours.
- **Read the blob, not the working tree.** `core.autocrlf` made a whole file
  look changed and nearly produced a false stale-pass verdict — while
  performing the very check the rule exists for.
- **No privileged actor.** The harness itself is the only thing in the tree
  that never went through the pipeline. That debt is real and acknowledged in
  its commit message.

## 6. Mattermost — the operator's channel, and its known fragility

Channel `6z9khgkdd7df9q454be6fimw1h` (#claude-sessions), thread root
`i16tx3srz7fub8xdrcq4dpf6jc`. That thread is **claimed** in
`scripts/claude-sessions-bridge/state/claimed-threads.json`, which makes the
bridge skip it — so the bridge will not spawn a session per reply.

**The consequence, and the trap:** because the bridge skips it, the ONLY
delivery path is a one-shot poller:

```
python scripts/mattermost-mcp/mm.py wait --since <ms-epoch> \
  --thread i16tx3srz7fub8xdrcq4dpf6jc --channel 6z9khgkdd7df9q454be6fimw1h \
  --timeout 21600 --interval 20
```

It **exits on the first message** and must be re-armed by hand. Anchor
`--since` on the **last message you actually read**, never on "now" — doing
the latter swallowed an operator message for hours. Read the thread on every
re-arm to catch anything that landed in the gap. The operator has flagged this
as inconsistent and confusing, correctly; the durable fix (a bridge-written
per-thread inbox with a consumed offset) is **U0's third item**, in flight.

## 7. What to do first

1. Read `DECISIONS.md`, then the Mattermost thread tail — in that order.
2. Re-arm the listener from the last message you read.
3. Check `queue.ps1 -List` and `git worktree list` for what the Opus session
   has moved.
4. Do **not** touch `work/dfu-*` or their worktrees — another session owns them.
5. If the Opus session is parked on something §C covers, sharpen §C and tell it
   to consult the ladder; do not just answer the question.
