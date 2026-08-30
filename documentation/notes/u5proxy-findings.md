# U5 — commit-path proxy for worktree agents (findings)

Branch: `work/u5proxy`. Date: 2026-08-30. Phase: dark-factory-unification **U5**
("Containment parity: mechanical guards for worktree/cloud agents — hook-bypass detection
at minimum; **commit-path proxy where feasible**").

Everything below is a measured result. The commands are reproducible; where a claim could
not be established by running something, it says so.

---

## What was built

| Artifact | What it is |
|---|---|
| `.githooks/reference-transaction` | The guard. Refuses to advance any `refs/heads/*` to a commit whose tree is not in the attestation ledger, and appends the refusal to `<git-common-dir>/hook-guard.log` |
| `scripts/agent-harness/verify-commit-path-guard.ps1` | 38-check drill: bypasses red, honest work green, the boundary measured, plus a negative control. `-Audit` prints this machine's refusal log |
| `.githooks/README.md` | Documents the guard, corrects the pre-commit table (it listed 3 of 5 checks), and replaces the "use `--no-verify` for false positives" advice, which the guard now makes impossible |
| `scripts/agent-harness/README.md` | One row for the new drill |

Nothing existing was weakened. `check-hook-attestation.ps1`, `pre-commit`,
`pre-merge-commit` and `commit-msg` are untouched by this branch.

---

## The mechanism, and why this one

`--no-verify` was the problem U5 exists for: it is one word, it skips `pre-commit` and
`commit-msg`, and it leaves **no trace in any git object**, which is why the attestation
ledger had to be invented to detect it after the fact. Detection is not containment — it
runs when a reviewer looks.

agent-org's answer to the same problem is a proxy (`little-coder/git-proxy/git_proxy.py`,
`_PROTECTED_BRANCHES` at line 154): inside the worker container, `git` on `$PATH` *is* the
proxy, so a push to `main` is refused, not reported. That model does not transfer to a
worktree agent on this host — it runs as the operator's own user, and any PATH shim it can
be given it can step around.

The one chokepoint left is inside git itself. Measured on git 2.49.0.windows.1:

```
$ git commit --no-verify   ->  pre-commit SKIPPED
                               reference-transaction STILL RUNS (state=prepared)
exit 1 at state=prepared   ->  "fatal: ref updates aborted by hook", exit 128
                               the branch does not move; the commit is left dangling
```

That is the whole design: `--no-verify` can still create a commit object, but it can no
longer make it history.

### The rule

At `prepared`, for each `refs/heads/*` update, the new commit's tree must appear in
`<git-common-dir>/hook-attest.log` (written by `pre-commit` step 6, and by
`pre-merge-commit` which `exec`s it). Deliberately **not** judged:

- refs outside `refs/heads/*` (`refs/remotes`, `refs/tags`, `refs/stash`, `AUTO_MERGE`);
- ref deletions;
- a commit already reachable from an existing ref — that is a ref *move*, not new content
  (branch creation, `reset` either way, fast-forward, `git worktree add -b`, a pull whose
  objects arrived under `refs/remotes` first);
- anything replayed by the sequencer (`rebase`, `cherry-pick`, `revert`). Git runs no
  `pre-commit` for those, so their trees *cannot* be attested; demanding it would refuse
  every reviewer rebase in MERGE-PROTOCOL.md. These are written to the guard log as
  `SKIP-SEQUENCER` rather than passed silently, and `check-hook-attestation.ps1` still
  reports them at merge time.

---

## Evidence

**In this repository, on this branch** (the hook is live in the worktree because
`core.hooksPath=.githooks`):

```
$ git add .githooks/reference-transaction
$ git commit --no-verify -m "U5: commit-path proxy (bypass attempt)"

COMMIT-PATH GUARD: refusing to advance refs/heads/work/u5proxy
  ...
  Audit record: D:/Open WebUI/ai-stack/.git/hook-guard.log
fatal: ref updates aborted by hook
exit=128
HEAD moved? NO
```

The same content then committed normally, through the hooks, without complaint — so the
refusal was of the bypass, not of the content.

**The drill** — `powershell -NoProfile -File scripts\agent-harness\verify-commit-path-guard.ps1`
→ `ALL 38 CHECKS PASSED`. It covers, each as its own check:

- RED: `commit --no-verify` (exit 128, HEAD unmoved, exactly one audit line),
  `commit --amend --no-verify` with changed content, `merge --no-verify`.
- GREEN: first commit into a fresh repo, the same tree by the honest path, honest amend,
  clean `--no-ff` merge, a genuinely **conflicted** merge resolved with `git commit`,
  branch creation at an older commit, `reset --hard` backwards and forwards, `stash` /
  `stash pop`, a reviewer-style `rebase`, `cherry-pick`, a fast-forward `pull` from a
  second repository.
- Boundary (see below), measured rather than asserted.
- **Negative control**: with the hook file removed, the identical bypass lands. Without
  this, all of the above would be equally consistent with a guard that checks nothing.

**Existing suites, unbroken:**

- `powershell -NoProfile -File scripts\agent-harness\verify-merge-protocol.ps1` → `66/66 checks passed`
- `python -m pytest scripts/agent-harness/test_anchor_schema.py scripts/agent-harness/test_harness_config.py scripts/agent-harness/test_scope_node.py scripts/claude-sessions-bridge/test_worktree.py -q` → `92 passed`

**Cost:** the guard's only non-trivial call is `git for-each-ref --contains=<sha>`.
Worst case in this repo (an unreachable commit, so every ref is walked): **0.109s**.

---

## The honest boundary — stopped mechanically vs. detect-only

| Attack | Result | Basis |
|---|---|---|
| `git commit --no-verify` | **STOPPED** | measured, drill step 2 + real repo |
| `git commit --amend --no-verify` (content changed) | **STOPPED** | drill step 4 |
| `git merge --no-verify` (divergent branches) | **STOPPED** | drill step 5 |
| `git update-ref refs/heads/<b> <unattested>` | **STOPPED** | drill step 11c |
| Deleting the ledger, then `--no-verify` | **STOPPED** (fails closed, `reason=no-ledger`) | drill step 11a |
| `git -c core.hooksPath=/nonexistent commit` | **NOT stopped** — detect-only | drill step 11b |
| Park under a non-`refs/heads` ref, then move the branch | **NOT stopped** — detect-only | drill step 11d |
| A commit replayed by `rebase` / `cherry-pick` | **NOT stopped** — logged + detect-only | drill step 8 |

"Detect-only" means `check-hook-attestation.ps1 -Branch … -Base …` reports the commit as
UNATTESTED at merge time, which is where it was before this work. What changed is that the
*cheap, natural, trace-free* route is gone: `--no-verify` is one word an agent reaches for
without deciding anything, and each surviving route is a deliberate, unusual, separately
visible command.

There is no way to close the remaining routes on this machine, because there is no
privilege boundary between the agent and git's configuration. Closing them would require
the commit path to run somewhere the agent is not — a container with the git-proxy model,
or a server-side hook on a remote it cannot bypass. Both are real options and both are
larger than U5.

---

## Findings raised, not fixed here

1. **`verify-merge-protocol.ps1` cannot exercise hooks that live on the work line.**
   It branches its scratch line from `development` (`git branch drill/verify-d
   development`) and provisions its drill worktrees from there, so those worktrees carry
   `development`'s `.githooks`. A hook added on `refactor/ai-stack-cleanup` gets no
   coverage from that drill until it reaches `development`. Not a defect in either script,
   but it means `verify-commit-path-guard.ps1` has to be run separately, and there is no
   aggregate runner that would notice if nobody did.
2. **A deleted ledger makes `check-hook-attestation.ps1` report INACTIVE**, i.e. pass.
   The file lives at `<git-common-dir>/hook-attest.log` and any agent can remove it.
   Its "no ledger = not active yet" branch is correct for adoption and wrong for an
   established repo. Candidate fix: treat *branch hook attests* + *ledger missing* as
   suspicious rather than inactive. Verified by reading
   `scripts/checks/check-hook-attestation.ps1` (the `if (-not (Test-Path $ledgerPath))`
   block) — not by running that case.
3. **`.githooks/README.md` had drifted**: it documented 3 of `pre-commit`'s 5 checks and
   said nothing about `pre-merge-commit`, `commit-msg`, or attestation. Fixed on this
   branch because the same file had to change anyway.
4. **PLAN.md §2's U5 row is not updated by this branch.** Deliberate — parallel branches
   editing one file is an add/add conflict. U5 also asks for `judge_enabled` calibration
   and personal-plane exclusion, neither of which this item covers.

---

## DECISIONS entries to append

## 2026-08-30 · U5 · class 2 — the commit-path proxy is a `reference-transaction` hook
DECISION: The "commit-path proxy where feasible" that U5 asks for is
          `.githooks/reference-transaction`, which aborts a `refs/heads/*` update
          whose new commit carries an unattested tree. It is not a PATH shim in
          the mould of `little-coder/git-proxy/git_proxy.py`.
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
REVERT:   Delete `.githooks/reference-transaction`. Nothing else references it;
          `pre-commit`, `pre-merge-commit`, `commit-msg` and
          `check-hook-attestation.ps1` are unchanged and keep working.

## 2026-08-30 · U5 · class 2 — the reachability exemption is kept wide on purpose
DECISION: A commit already reachable from ANY existing ref is treated as a ref
          move and not judged - including refs outside `refs/heads/*`. This
          leaves a measured residual: parking a crafted commit under e.g.
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
          which is cheaper. Both are covered by drill step 11.
REVERT:   In `.githooks/reference-transaction`, replace the bare
          `git for-each-ref --contains=` call with one that passes explicit ref
          patterns.

## 2026-08-30 · U5 · class 2 — sequencer replays are skipped and logged, not blocked
DECISION: While `rebase-merge`, `rebase-apply`, `CHERRY_PICK_HEAD` or
          `REVERT_HEAD` exist in the per-worktree git dir, the guard records
          `SKIP-SEQUENCER` and allows the update.
CITED:    §C.2 class 2. MERGE-PROTOCOL.md requires the reviewer to rebase before
          merging.
EVIDENCE: Git runs no `pre-commit` for replayed commits, so their trees can never
          be attested; enforcing would refuse every reviewer rebase. The skip is
          written to the guard log rather than passed silently, and
          `check-hook-attestation.ps1` still reports those commits as UNATTESTED
          at merge time - the after-the-fact net is not removed, it is what
          remains for this case.
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
