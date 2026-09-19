# Findings — hook attestation (U5 slice 1), 2026-08-29

The anchor's `findings_sink`. Checked against `work/hookattest` at `8c8e979`.

---

## F1 — the drill cannot exercise a change to the hooks it runs under

`verify-merge-protocol.ps1` resolves `$queue` from `$PSScriptRoot`, so it *does* test the
branch's own `queue.ps1`. But it creates its drill worktrees with `new-worktree.ps1 -Base
drill/verify-d`, cut from the **main checkout** — and `core.hooksPath` lives in the shared
`.git/config` as `.githooks`, resolved relative to each worktree. So a drill worktree always
carries the **merged** hooks, never the branch's.

That means any change to `.githooks/` is structurally invisible to the drill that is
supposed to validate it. This item hit it head-on: the first run flagged the drill's own
honest commits, because their hook could not attest.

Two candidate fixes, neither free:

1. Have the drill copy the branch's `.githooks/` into its worktrees before committing.
   Tried, reverted: both drill worktrees then produce an *identical* hook-adoption commit
   (same tree, parent, message, author) and therefore the same sha, which breaks the
   "two divergent commits exist" premise the whole scenario rests on. Fixable with distinct
   content per worktree, but it also changes what step 2 is testing.
2. Accept the limit and cover hook changes with a separate, explicit procedure — what this
   item does (TESTPLAN case 2 runs the real hook on the branch).

Worth an item of its own. Until then: **a change under `.githooks/` is not covered by the
drill, and a green drill must not be read as covering one.**

---

## F2 — three green signals, none of which asked the question (recurring)

This is the second time today the same shape appeared. For little-coder's backups it was
coverage/freshness/sentinel all true while the restore was impossible. Here it is: the
pre-commit hooks existed, were documented, were in `core.hooksPath`, and MERGE-PROTOCOL said
never to bypass them — and none of that could tell you whether they ran.

The general lesson, which is A7 restated: a control you cannot *interrogate after the fact*
is a control you are trusting, not enforcing. Worth carrying into U5's remaining bullets —
when checking the personal-plane exclusion, the question is not "is it configured" but
"what would show me it held?"

---

## F3 — `--no-verify` is not the only bypass, and this closes only that one

`git commit --no-verify` skips pre-commit. It is the one an agent actually reached for, and
it is what this closes. It is NOT the only way to commit content the hooks never saw:

- `git commit-tree` / plumbing writes commits with no hook involvement at all. (This item
  used exactly that to prove tree-based attestation — so the technique is demonstrably
  available to anything running in the worktree.)
- `core.hooksPath` can be reconfigured locally, or `git -c core.hooksPath=/dev/null commit`.
- A commit made in a clone that never ran `git config core.hooksPath .githooks`.

All three produce unattested trees, so **the checker catches them anyway** — it asks "was
this content validated?", not "did you use the right flag". That is the right shape, and it
is why attestation is over the tree rather than a flag or a commit message.

What it does NOT do is *prevent* any of them. The guard is detective at the submission
chokepoint, not preventive at commit time. The preventive half is the "commit-path proxy
where feasible" that U5's first bullet names and this item scoped out. agent-org's workers
already have it (git-proxy); worktree sessions do not.

---

## F4 — the ledger is unsigned and locally writable

`hook-attest.log` is a plain file in `.git/`. Anything that can run `git commit` can also
append a tree hash to it, so this stops a *careless* bypass, not a *determined* one.

That is a deliberate proportionality call, not an oversight: the failure being addressed is
an agent reaching for a convenient flag, and the fix costs one append per commit. Making it
tamper-evident (an HMAC keyed outside the worktree, or attesting to an append-only store off
the machine) is a materially bigger design with key-management of its own.

**State it plainly wherever this is described** — a guard oversold is worse than a guard
absent, because people stop looking.
