# U5 — commit-path proxy for worktree agents (findings)

Branch: `work/u5proxy`. First round 2026-08-30; **fix round 2026-08-30 after two
adversarial verifiers REFUTED it.** Phase: dark-factory-unification **U5** ("Containment
parity: mechanical guards for worktree/cloud agents — hook-bypass detection at minimum;
**commit-path proxy where feasible**").

Everything below is a measured result. The commands are reproducible; where a claim could
not be established by running something, it says so.

**Scope, stated first because the first round's framing over-claimed it.** U5's *What*
column names three deliverables and its *Validated by* column names two attacks ("bypass
hooks / reach personal-plane data"). **This branch is the hook-bypass half of the commit-path
sub-item only.** It does not touch the personal plane, does not implement `judge_enabled`
calibration, and cannot satisfy U5's Validated-by column alone — `work/u5pplane` and
`work/u5judge` hold the other parts. Any merge banner saying "U5 validated" on this branch
alone would be false.

---

## What the refutations found, and what this round did about it

| # | Refutation | Status |
|---|---|---|
| 1 | **The bypass still worked.** `pre-commit` attested the tree BEFORE `commit-msg` ran, and the ledger entry survived a `commit-msg` abort — so a message refused for naming a bogus gitlink SHA could be re-committed with `--no-verify` and it LANDED, with no guard-log line. Same for a message-only `--amend --no-verify`. | **FIXED.** The attester moved to `commit-msg` (the last hook that can veto) and now records **(tree, message)**. Both shapes are refused, both write an audit line. RED→GREEN transcript below. |
| 2 | `.githooks/README.md` claimed `--amend --no-verify` was "refused" and `--no-verify` "no longer works". Measurably false. | **FIXED**, in both directions: the hole is closed, and the page now states a six-row boundary table every row of which the drill executes. |
| 3 | The "two routes survive" enumeration was falsified by three more measured routes (delete the hook; a worktree whose branch lacks it; forged sequencer state). | **FIXED as documentation + measurement.** R3/R4/R5 are named, measured by the drill, and their residue is stated as open. R3/R4 are **not closed** — see "What I could not close". |
| 4 | Both READMEs said "33 checks" while the drill emitted 38. | **FIXED, and made non-recurring**: the drill's last check reads its own count out of both READMEs and fails if they disagree. |
| 5 | The drill was PATH-dependent: under Git-Bash's PATH, `git.exe` is `…\Git\mingw64\bin\git.exe`, step 10 found no POSIX `sh`, and the drill exited 1 for a **non-guard** reason. | **FIXED.** The resolver walks the ancestors of every root git reports (`Get-Command git.exe` and `git --exec-path`) instead of assuming a depth. |
| 6 | The drill could not run concurrently with itself. | **Partly a mis-attribution, and hardened anyway.** The scratch root was already `$PID`-scoped, but the run *deleted whatever it found there first*, so a PID collision would tear down a peer. The root is now unique per **invocation** (PID + ticks + random) and nothing is matched by pattern. Two simultaneous runs measured below. |
| 7 | `hook-guard.log` "has no consumer". | **Accepted as accurate and answered by scope**: the consumer of the *audit* is `check-hook-attestation.ps1`, which `queue.ps1 -Submit` runs (queue.ps1:407). The guard log is the *refusal* record. The boundary table now states, per route, whether a guard line is written **and** whether the commit is still caught at the submit gate — R1–R5 all are. |

---

## The defect that mattered, and the fix

### The hole (RED — reproduced against the hooks as shipped at `f327434`)

Fixture built from the shipped hooks themselves: `reference-transaction` copied verbatim
from `f327434`, `pre-commit`'s attestation block extracted by its own marker, a stand-in
`commit-msg` veto.

```
git commit -m "REJECTME OB1 -> deadbee1, pushed before this bump"
  -> commit-msg: REJECTME        after reject: head-moved=NO
git commit --no-verify -m "<the identical message>"
  -> after --no-verify retry: exit=0  head-moved=YES
git commit --amend --no-verify -m "rewritten: OB1 -> deadbee1"
  -> head-moved=YES  before=625e35ee… after=752b0e77…
cat .git/hook-guard.log
  -> (NO GUARD LOG AT ALL - unstopped AND unaudited)
```

`commit-msg` is the hook that enforces the gitlink-SHA rule `CLAUDE.md` makes hard, and
PLAN.md §C.7 makes the commit message the operator's audit surface. The one channel that
was wide open was the one the audit reads.

### The fix is ordering, not more checks

- `.githooks/attest-lib.sh` (**new**) — the one definition of the (tree, message)
  attestation and of the message normalisation, sourced by both the attester and the guard
  so they cannot drift.
- `.githooks/commit-msg` — **is now the attester**, as its last act, and it records
  `<tree> <message-digest>`. Its gitlink check became a function (`_check_gitlink_shas`)
  so that refusal is the only early exit.
- `.githooks/pre-commit` — no longer attests. Reaching the attester requires passing every
  check in front of it, and `--no-verify` skips `pre-commit` and `commit-msg` together.
- `.githooks/reference-transaction` — requires the **pair**, and names a message rewrite
  in the log as `reason=message-not-attested`.
- `scripts/checks/check-hook-attestation.ps1` — its activation gate reads the branch's
  `commit-msg` **or** `pre-commit`. Without this the gate would have gone silently
  INACTIVE the moment attestation moved, which on a check whose whole job is noticing
  absence is the worst available failure.

### GREEN (same shapes, current hooks — drill steps 11 and 11.5)

```
[PASS] the REAL commit-msg refuses a message naming a SHA that does not exist (HEAD unmoved) exit=1
[PASS] and the refused tree was NOT attested (nothing survives the abort)
[PASS] the --no-verify RETRY with the identical message is REFUSED exit=128
[PASS] and the retry IS audited (this route used to write no line at all) deny=5
[PASS] the honest bump naming the REAL SHA lands exit=0
[PASS] message-only amend bypass exits 128 exit=128
[PASS] the record names the cause: reason=message-not-attested
```

### Message normalisation, and why it does not false-positive

The attester sees the message **file**; the guard sees the message git **stored**. The same
reduction (cut a `commit -v` scissors block, `git stripspace --strip-comments`) is applied
to both, and it is idempotent, so a stored message reduces to itself. Measured green on
`-m`, `-F` with comment lines and trailing blanks, `--amend --no-edit`, a **partial** commit
(`git commit -- path`, where git hands the hooks a temporary index), a clean merge (git
invokes `commit-msg` with `.git/MERGE_MSG` — probed, not assumed) and a conflicted merge
resolved by `git commit --no-edit`. None writes a guard line.

---

## The fixture was lying, and a mutation caught it

The first-round drill **reassembled** `commit-msg`: it extracted the attestation block by
marker and pasted it beneath a hand-written veto. That fixture is blind by construction to
the two properties that matter most about that file — where the attestation sits relative
to the refusal, and whether the code above it exits early.

Proof: a mutant that moved the attestation block **above** the veto (i.e. re-introduced the
exact defect this round exists to close) left the drill at **62/62 GREEN**.

Chasing that mutant found a **live bug** of the same shape in the fix itself: the gitlink
check began `[ -z "$SUBS" ] && exit 0`, which is nearly every commit. Spliced in beneath
it, the attester would have run on **gitlink bumps only**, and `reference-transaction`
would then have refused every ordinary commit in the repo. Neither problem was visible by
reading; both were visible to a mutation.

The fixture now copies `commit-msg` **verbatim** and arms the real gitlink veto with a real
submodule and a real bogus SHA, plus an honest-bump control so the refusal is of the claim
and not of gitlink bumps in general. With that fixture the same mutant goes red.

---

## Evidence (commands, and what they returned)

| Command | Result |
|---|---|
| `powershell -NoProfile -File scripts\agent-harness\verify-commit-path-guard.ps1` | `ALL 66 CHECKS PASSED`, exit 0 |
| two of the above, launched simultaneously | both `ALL 66 CHECKS PASSED`, exit 0, distinct scratch roots |
| `powershell -NoProfile -File scripts\checks\check-hook-attestation.ps1 -Branch work/u5proxy -Base refactor/ai-stack-cleanup` | `[OK] every commit's tree was validated`, exit 0 |
| `powershell -NoProfile -File scripts\checks\validate-lineendings.ps1` | `SUCCESS: All tracked shell scripts have Unix line endings` |
| PATH probe under Git Bash | `git=C:\Program Files\Git\mingw64\bin\git.exe`; the OLD resolver → `…\mingw64\usr\bin\sh.exe` `exists=False`; the new one resolves `C:\Program Files\Git\usr\bin\sh.exe` |

**Mutation score 5/5** (each mutant run from an isolated copy of `.githooks`, drill
unchanged):

| Mutant | Change | Result |
|---|---|---|
| M1 | pair check → tree-only (the shipped rule) | 3 FAIL — exactly the message-binding checks |
| M2 | attest **before** the veto (the shipped ordering) | 4 FAIL — the retry lands, the refused tree is attested |
| M3 | guard is a no-op | 23 FAIL |
| M4 | `_check_gitlink_shas \|\| exit 1` → `\|\| true` | 5 FAIL |
| M5 | message digest is a constant | 20 FAIL — honest work breaks, which is the safe direction |

M2 is the important one: **it was green before the fixture was made verbatim.**

---

## The honest boundary — measured, per route

Every row is executed by drill step 12 (R3 by step 13, which is also the negative control).
"Caught at the gate" means `check-hook-attestation.ps1` reports the commit UNATTESTED, and
`queue.ps1 -Submit` runs that check.

| # | Route | Lands | Guard line | Caught at the gate | Status |
|---|---|---|---|---|---|
| — | `git commit --no-verify` | no | `DENY` | — | **STOPPED** |
| — | `git commit --amend --no-verify` (content changed) | no | `DENY` | — | **STOPPED** |
| — | `git commit --no-verify` **retry after a `commit-msg` refusal** | no | `DENY` | — | **STOPPED (this round)** |
| — | `git commit --amend --no-verify -m "<new message>"` | no | `DENY reason=message-not-attested` | — | **STOPPED (this round)** |
| — | `git merge --no-verify` (divergent branches) | no | `DENY` | — | **STOPPED** |
| — | `git update-ref refs/heads/<b> <unattested>` | no | `DENY` | — | **STOPPED** |
| — | delete the ledger, then `--no-verify` | no | `DENY reason=no-ledger` | — | **STOPPED** (fails closed) |
| R1 | `git -c core.hooksPath=/nonexistent commit …` | yes | none | yes | open — operator's escape hatch |
| R2 | park under a non-`refs/heads` ref, then move the branch | yes | none | yes | open by choice |
| R3 | `rm .githooks/reference-transaction`, then `--no-verify` | yes | none | yes | **open** |
| R4 | commit in a worktree whose **branch** lacks the hook | yes | none | yes | **open** |
| R5 | forge `CHERRY_PICK_HEAD`, then `--no-verify` | yes | `SKIP-SEQUENCER` | yes | **open** |
| R6 | `git commit --amend --no-verify --no-edit` | yes | none | n/a | bounded on purpose |

R6 is allowed because tree **and** message are unchanged, so the attestation genuinely
covers the new object; it re-parents and re-dates and cannot do more. The bound is
load-bearing and is tested: the same shape on an **unattested** commit is refused, so R6
cannot launder a commit that arrived by R1/R3/R5.

The first round's claim that "each surviving route is a deliberate, unusual, separately
visible command" was **wrong about R3 and R5** — two words and one line respectively, both
leaving no working-tree trace. That sentence is gone. What is true, and is now what the
documentation says, is narrower: R1–R5 all leave the commit **UNATTESTED**, so what they
escape is the guard **log**, not the audit.

---

## What I could not close (stated mechanically, not softened)

1. **R3 and R4 — the guard is a file inside the tree it guards.** `core.hooksPath` is
   `.githooks`, a **relative** path resolved against each worktree's own top level. So the
   guarded party can delete the hook (R3), and any worktree whose branch does not carry the
   file is unguarded (R4) — today that is every branch cut before this merges. The obvious
   fix, an **absolute** `core.hooksPath`, was considered and **rejected**: it would point
   every worktree at one branch's copy of the hooks, so changing a hook on a branch would
   not take effect in that branch's worktree, and a fresh clone would need a machine-specific
   path. It would also, if set today, silently disable this guard everywhere (the main
   checkout does not carry the file until this merges). **Left open.** Compensating control:
   both leave the commit UNATTESTED at the submit gate, and both are measured by the drill
   so the residue cannot quietly change size.
2. **R5 — a forged sequencer file is indistinguishable from a real one** to a hook that can
   only read `.git`. The exemption itself is not optional: git runs no `pre-commit` for
   replayed commits, so a rebase can never be attested and demanding it would refuse every
   reviewer rebase in MERGE-PROTOCOL.md. **Left open**, logged, and caught at the gate.
3. **There is no privilege boundary at all on this machine.** The agent runs as the
   operator's own user with the real `git.exe`. Closing R1–R5 properly requires the commit
   path to execute somewhere the agent is not — a container with the `git-proxy` model, or
   a server-side hook on a remote it cannot bypass. Both are real options; both are larger
   than U5 and neither was attempted here.
4. **`check-hook-attestation.ps1` still reports INACTIVE when the ledger is missing**, i.e.
   passes. Any agent can delete `<git-common-dir>/hook-attest.log`. The guard fails closed
   on the same condition, so the two mechanisms fail in opposite directions — but the
   *detector* remains defeatable and this branch did not change that. Verified by reading
   `scripts/checks/check-hook-attestation.ps1:149`, **not** by running that case.
5. **`verify-merge-protocol.ps1` gives this hook no coverage.** It branches its scratch line
   from `development`, so its drill worktrees carry `development`'s `.githooks`. Not a
   defect in either script; it means this drill has to be run separately and nothing would
   notice if nobody did.
6. **Nothing was pushed to any remote other than this work branch, and PLAN.md §2's U5 row
   is untouched** (parallel branches editing one file is an add/add conflict).

---

## One operational hazard for whoever merges this

The guard and the attester must come from the **same commit**. A half-merged `.githooks/`
— new `reference-transaction` beside an old `pre-commit`-based attester — denies
*everything*, because the old attester writes a tree with no message digest and the new
guard requires the pair. It fails in the safe direction and it is loud, and the refusal
text now names this case explicitly, but a reviewer resolving a `.githooks/` conflict
file-by-file could produce it. Resolve `.githooks/` as a set.

Sibling worktrees are unaffected until they merge the line: `core.hooksPath` is relative,
so a branch that does not carry these files keeps the old `pre-commit` attester and has no
`reference-transaction` at all. Confirmed live — `work/u4bidir` wrote a legacy three-field
ledger line at 15:14 while this branch wrote a four-field one at 15:15, from the same
shared ledger.

---

## Corrections to the first round's own record

- "Nothing existing was weakened. `check-hook-attestation.ps1`, `pre-commit`,
  `pre-merge-commit` and `commit-msg` are untouched by this branch" — **no longer true and
  should not be relied on**: `pre-commit`, `commit-msg` and `check-hook-attestation.ps1`
  are all modified by this round, deliberately and for the reasons above.
- "38-check drill" / "33 checks" — the drill now emits **66**, and asserts that number
  against both READMEs.
- The first round's evidence (`verify-merge-protocol.ps1` 66/66, 92 pytest passes) was
  measured before this branch merged `refactor/ai-stack-cleanup`, so it was taken against a
  stale base. This round merged the line first; those two suites were **not** re-run here
  and their numbers are therefore **not** re-asserted.

---

## DECISIONS entries to append

## 2026-08-30 · U5 · class 2 — the commit-path proxy is a `reference-transaction` hook
DECISION: The "commit-path proxy where feasible" that U5 asks for is
          `.githooks/reference-transaction`, which aborts a `refs/heads/*` update
          whose new commit does not match an attested (tree, message) pair. It is
          not a PATH shim in the mould of `little-coder/git-proxy/git_proxy.py`.
CITED:    §C.2 class 2 (choosing between two defensible designs; pick the most
          reversible, closest to an existing house pattern). §C.7 (a phase closes
          only on an EXECUTABLE check).
EVIDENCE: Measured on git 2.49.0.windows.1, 2026-08-30: `git commit --no-verify`
          skips `pre-commit` but still runs `reference-transaction`, and exiting
          non-zero at `state=prepared` produces "fatal: ref updates aborted by
          hook", exit 128, with the branch unmoved. A PATH shim was rejected
          because a worktree agent runs as the operator's own user and can call
          the real `git.exe` by absolute path - it would be a rule, not a
          mechanism, which is the class §0 A7 already records as FALSIFIED.
REVERT:   Delete `.githooks/reference-transaction`. Nothing else requires it;
          `pre-commit`, `pre-merge-commit`, `commit-msg` and
          `check-hook-attestation.ps1` keep working without it.

## 2026-08-30 · U5 · class 2 — the attester is `commit-msg`, and it records the MESSAGE too
DECISION: The attestation ledger is written by `.githooks/commit-msg` as its last
          act, not by `pre-commit`, and each line is `<tree> <message-digest>
          <iso8601> <branch>`. `.githooks/reference-transaction` requires the
          PAIR. `.githooks/attest-lib.sh` holds the single definition of the
          digest and of the message normalisation, sourced by both.
CITED:    §C.2 class 2. §C.7 (an executable check, RED before GREEN).
EVIDENCE: Two verifiers reproduced the hole and so did this branch: with
          attestation in `pre-commit` (the FIRST hook), a tree stayed attested
          after `commit-msg` refused the message, so `git commit --no-verify`
          with the identical message LANDED (exit 0, HEAD moved) and wrote NO
          guard-log line - unstopped and unaudited; a message-only
          `--amend --no-verify` did the same. Both are now exit 128 with a DENY
          record (`reason=message-not-attested` for the amend), drill steps 11
          and 11.5. Normalisation measured green on `-m`, `-F` with comments and
          trailing blanks, `--amend --no-edit`, a partial commit, a clean merge
          and a conflicted-merge resolution. Mutation score 5/5.
REVERT:   Move the block below the `--- ATTESTATION` marker in
          `.githooks/commit-msg` back to the end of `.githooks/pre-commit`, and
          restore the `grep -q "^$tree "` form in
          `.githooks/reference-transaction`. Drill steps 11 / 11.5 then fail,
          which is the intended signal.

## 2026-08-30 · U5 · class 2 — `core.hooksPath` stays RELATIVE, so R3/R4 stay open
DECISION: `core.hooksPath` remains `.githooks` (a relative, in-tree path). The
          consequence is accepted and documented: an agent can delete the guard
          in its own worktree (R3), and a worktree whose branch does not carry
          the hook is unguarded (R4). Neither is closed by this branch.
CITED:    §C.2 class 2 (most reversible option) and §C.7 (park with a written
          reason rather than paper over).
EVIDENCE: An absolute `core.hooksPath` would close both, and was rejected on
          three measured consequences: every worktree would run ONE branch's
          copy of the hooks, so changing a hook on a branch would not take
          effect where it was changed; a fresh clone would need a
          machine-specific path, breaking the documented one-line activation;
          and setting it today would disable this guard everywhere, because the
          main checkout does not carry the file until this branch merges. The
          compensating control is that R3 and R4 both leave the commit
          UNATTESTED, which `check-hook-attestation.ps1` reports and
          `queue.ps1 -Submit` runs - and both are measured by drill step 12/13,
          so the residue cannot change size unnoticed.
REVERT:   `git config core.hooksPath "<abs path>/.githooks"` after the hook is
          present on every active branch. Drill row R4 would then fail, which is
          the intended signal that the boundary moved.

## 2026-08-30 · U5 · class 2 — the reachability exemption is kept wide on purpose
DECISION: A commit already reachable from ANY existing ref is treated as a ref
          move and not judged - including refs outside `refs/heads/*`. This
          leaves a measured residual (R2): parking a crafted commit under e.g.
          `refs/pre/x` and then moving a branch to it succeeds.
CITED:    §C.2 class 2 (most reversible, closest to a house pattern) and
          `check-hook-attestation.ps1`'s own paid-for lesson - "a guard that
          false-positives on legitimate work gets switched off, and then it
          protects nothing".
EVIDENCE: Narrowing the exemption to `refs/heads` + `refs/remotes` would only
          push the escape to `git tag`, and would start refusing an honest
          `git checkout -b rel <tag>` where the tagged commit is on no branch.
          The residual costs an attacker an extra deliberate `git update-ref`;
          the same attacker already has `git -c core.hooksPath=/nonexistent`,
          which is cheaper. Both are covered by drill step 12.
REVERT:   In `.githooks/reference-transaction`, replace the bare
          `git for-each-ref --contains=` call with one that passes explicit ref
          patterns.

## 2026-08-30 · U5 · class 2 — sequencer replays are skipped and logged, not blocked
DECISION: While `rebase-merge`, `rebase-apply`, `CHERRY_PICK_HEAD` or
          `REVERT_HEAD` exist in the per-worktree git dir, the guard records
          `SKIP-SEQUENCER` and allows the update. A FORGED one of those files
          (R5) therefore also passes, and that residue is accepted.
CITED:    §C.2 class 2. MERGE-PROTOCOL.md requires the reviewer to rebase before
          merging. §C.7 (park with a written reason).
EVIDENCE: Git runs no `pre-commit` for replayed commits, so their trees can never
          be attested; enforcing would refuse every reviewer rebase. A hook that
          can only read `.git` cannot distinguish a forged `CHERRY_PICK_HEAD`
          from a real one, so the forgery is priced rather than closed: it costs
          an extra command, it writes a `SKIP-SEQUENCER` line, and the commit is
          still reported UNATTESTED at the submit gate. Measured by drill step 12.
REVERT:   Delete the `seq` block in `.githooks/reference-transaction`; drill
          step 8 will then fail, which is the intended signal.

## 2026-08-30 · U5 · class 3 — `--no-verify` is no longer the documented escape hatch
DECISION: `.githooks/README.md`'s false-positive advice becomes: fix the check,
          then rephrase the example, then - as a deliberate operator action -
          `git -c core.hooksPath=/nonexistent commit`, declaring it in the
          submission because it will be reported UNATTESTED.
CITED:    §C.2 class 3 (a default taken and recorded). The old advice is simply
          no longer executable.
REVERT:   Restore the previous paragraph; it only becomes true again if the
          guard is removed.

## 2026-08-30 · U5 · class 3 — the drill's fixture uses the hooks VERBATIM
DECISION: `verify-commit-path-guard.ps1` copies `reference-transaction`,
          `attest-lib.sh` and `commit-msg` verbatim and drives the real
          gitlink-SHA veto with a real submodule, instead of reassembling
          `commit-msg` from a marker-extracted block placed under a hand-written
          veto. Only `pre-commit` remains a stub.
CITED:    §C.7 (only an executable check counts) and CLAUDE.md ("a check that
          passes while checking nothing").
EVIDENCE: With the reassembled fixture, a mutant that moved the attestation block
          ABOVE the veto - re-creating the exact hole this round closes - left
          the drill 62/62 GREEN. Chasing that mutant also exposed a live bug: the
          gitlink check returned early whenever no submodule was staged, so the
          spliced attester would have attested gitlink bumps only and the guard
          would have refused every ordinary commit. With the verbatim fixture the
          same mutant fails 4 checks.
REVERT:   None wanted; reverting re-creates a fixture that cannot see the
          property under test.
