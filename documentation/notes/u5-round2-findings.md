# U5 round 2 — every fix closed the case and a verifier walked the neighbouring one

Recorded 2026-08-30 by the orchestrator. Round 3 is in flight against the chokepoint framing
this note argues for. Two of these are live containment defects in unmerged branches; none of
this is on `refactor/ai-stack-cleanup`.

## The pattern, stated first because it is the finding

Three branches, two rounds, six instances of one shape:

> the reported defect was genuinely fixed — verifiers reproduced each GREEN by execution —
> and then a verifier walked straight through the neighbouring case.

| Branch | Closed | Walked through instead |
|---|---|---|
| `u5proxy` | the `--no-verify` retry after a `commit-msg` refusal | the **message channel**, the one route the submit gate does not catch |
| `u5judge` | the reported flag bypass | three ordinary YAML spellings; separately, two quote characters |
| `u5pplane` | the read leak on `inspect` / `list_review_queue` / `recall_trace` | `agent_memory_review` — a different door on the same allow-list |

Enumerate-and-patch loses. There is always another spelling, another door, another channel,
and each round's fix is *correct* — which is what makes the pattern hard to see from inside a
round. The property these guards claim ("the plane is contained", "the commit path is
guarded") is a statement about ALL routes, and closing named routes never establishes it.

## Defect 1 — promote_exposure escalates across the plane boundary (u5pplane)

Confirmed by the orchestrator reading `wt-u5pplane`, not taken from the verifier's report:

- `performReview` (agent-memory-ops.ts ~99) selects
  `COALESCE(metadata->>'exposure','personal') AS exposure FROM agent_memories WHERE id = $1
  FOR UPDATE`. It **selects** exposure to report it and never **filters** on it, and never
  consults `deps.doorExposure`. Grepping the file puts the plane predicate at exactly one
  line (~278), inside `listForReview`.
- `agent_memory_review` is on the ops door's allow-list:
  `OB1/docker/docker-compose.yml:238  GATEWAY_WRITE_TOOLS: agent_memory_writeback,agent_memory_review,agent_memory_report_usage`.
- `promote_exposure` is in `REVIEW_ACTIONS`, and the branch's own test calls it "the ONLY
  action that widens exposure".

So an ops-door caller can take a **personal** memory id and promote it onto the ops plane,
after which every ops read tool returns it entirely legitimately. **This is privilege
escalation, not a read leak** — the containment proven on the read tools is bypassed without
ever being defeated.

Three lines below that allow-list sits the comment "The exposure plane is forced SERVER-SIDE
by performRecall from its own door value and **a caller cannot widen it**." `promote_exposure`
through this path is precisely a caller widening it. That sentence must end up true or gone.

Why it survived my own earlier fix: I closed the READ tools and never asked whether the
review/write door also resolves a memory by id. It does.

## Defect 2 — the attestation digest is lossy, and the column that would catch it is read by nothing (u5proxy)

Reproduced by a verifier in a scratch repo with a real submodule, hooks byte-identical to the
branch (hash-object matched):

`attest-lib.sh:43-46` normalises with a sed that strips scissors-and-below, piped through
`git stripspace --strip-comments`, applied to both sides. Any text that reduction **discards**
is an unguarded rewrite channel against an already-attested tree, because git stores
hash-prefixed lines verbatim under `cleanup=whitespace` — which is what `-m` / `-F` select.
An `--amend --no-verify` whose message puts the lie on a hash-prefixed line and the attested
text on the next line exits 0, writes no hook-guard.log line, and puts the lie in the subject
that `git log --oneline` shows.

And it defeats the compensating control, which no other route does: the tree stays
**attested**, and `check-hook-attestation.ps1:113` parses only column 0. The message digest
column **is read by nothing** (`:198-199` compares only the tree). Against the forged commit
it prints "[OK] every commit's tree was validated", exit 0 — and `queue.ps1:436-456` gates
`-Submit` on exactly that.

Materiality: this defeats specifically the rule `commit-msg` exists to enforce. The verifier
ran the veto first (gitlink staged, message naming a nonexistent SHA, exit 1) and then landed
that identical false claim as the subject via one `--no-verify` amend. That hook was built
after I committed an invented SHA; the guard is real, and this route walks around it.

**A ledger column nothing reads is not a control.**

## Defect 3 — a regex approximating a YAML parser (u5judge)

`check-judge-flag.ps1:114` decides "is the flag being turned on?" with a pattern over staged
text. The daemon decides it with `yaml.safe_load` plus pydantic
(`little-coder/src/littlecoder/config.py:268`, `judge_enabled: bool = False` at `:209`).
A verifier ran the real loader: three ordinary spellings turn it on for the daemon and pass
the guard, with zero audit lines.

Same class as the zod-enum-vs-SQL-CHECK drift this repo already fixed by testing the two
against each other. A regex approximating a parser is not a guard; it is a second, worse
parser.

## Also confirmed vacuous

`verify-commit-path-guard.ps1:130-133` asserts "every refusal in commit-msg comes BEFORE the
attestation block" using a pattern anchored on a bare `exit 1` line — which matches **zero**
lines of `commit-msg`, whose refusals are `return 1` at `:97` and a guarded `|| exit 1` at
`:104`. The check passes reporting "refusal at none". That is the ninth check found green
while checking nothing in this effort.

## For the merger

`git -C .claude/worktrees/wt-u5proxy status --porcelain` shows a modified `OB1` submodule
pointer at `cb08cd5`. Resolve before any gitlink-bearing merge; do not let it ride along.

## DECISIONS entries to append

- **2026-08-30, U5:** all three branches refuted twice. Each fix was correct and each closed
  only its named route. Confirmed defects: `promote_exposure` escalates a personal memory onto
  the ops plane through `agent_memory_review` (no exposure predicate in `performReview`); the
  commit attestation's normalisation is lossy so hash-prefixed text is an unguarded channel,
  and the message-digest ledger column is read by nothing so the submit gate misses it; the
  judge-flag guard is a regex disagreeing with the daemon's YAML loader. Round 3 is briefed to
  build chokepoints plus code-derived completeness tests, not to patch the new routes.
  Revert path: all three are unmerged work branches; nothing to revert on the work line.
- **2026-08-30, method:** ENUMERATE-AND-PATCH LOSES. A guard whose completeness rests on a
  list of closed routes states a property over ALL routes while proving it for some. Standard
  adopted: enforce at a chokepoint that cannot be bypassed by omission, and prove completeness
  with a test derived from a SCAN of the code, which goes RED when a new unguarded site
  appears. A hand-written list of guarded sites rots silently — the same defect one level up.
