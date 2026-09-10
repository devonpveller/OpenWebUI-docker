# Findings — agent-harness queue.ps1 defects, 2026-09-04

The `findings_sink` for queue item `harnessq`. True problems in `scripts/agent-harness/`
found while fixing the six defects that item names, which are **not** among those six.
Checked against `work/harnessq`; every line below was read in the file at the line cited,
not inferred from a sibling.

---

## F1 — `-Unclaim` verifies nothing, and can silently destroy a recorded pass

`queue.ps1`, the `if ($Unclaim)` handler (currently ~line 1019):

```powershell
if (-not $Id -or -not $Role) { Die "-Unclaim needs -Id and -Role" }
$item = Read-Item $Id
Drop-Claim $Id $Role
$item.state = $RoleRules[$Role].ready
```

Two things are wrong, and they are separate.

**It does not ask who you are.** `-By` is not required and is never compared with the claim
holder, so any agent can release any other agent's claim on any item. Every other verdict
path calls `Assert-Claim` for exactly this reason (`-Pass`, `-Fail`, `-Merged`, `-Requeue`,
`-Reject`); `-Unclaim` is the one that does not. It is the mirror of the reason `-Claim`
uses `CreateNew`: exclusivity that anybody can revoke is not exclusivity.

**It rewrites the state unconditionally, whether or not a claim existed.** `Drop-Claim` is a
no-op when there is no claim file, and the very next line still assigns
`$RoleRules[$Role].ready`. So `-Unclaim -Id <x> -Role tester` against an item sitting at
`test-passed` moves it back to `ready-to-test` — discarding a tester's verdict and the
operator's release gate, with no claim involved and nothing in the output saying so. The
history line reads "released the tester claim", which is not what happened.

**Out of scope for `harnessq`** — its anchor names six defects and this is not one of them,
and the state-machine clause puts "redesigning the queue state machine" out of bounds. But
this is the same class as defect 5 (a command that half-applies and reports success), and it
is reachable by a typo.

Suggested shape when someone picks it up: require `-By`, `Assert-Claim` before dropping, and
only move the state when a claim was actually held.

## F2 — `verify-merge-protocol.ps1` cuts its scratch line from `development`, and the operator's hooks no longer run there

Reproduced 2026-09-04 while running the drill for `harnessq`, and reproduced again with the
UNMODIFIED `queue.ps1` from the main checkout, so it is not caused by that item.

`core.hooksPath` in this repository is an **absolute** path into the operator's checkout:

```
$ git config --show-origin --get core.hooksPath
file:.git/config        D:\Open WebUI\ai-stack\.githooks
```

So every worktree runs the hook that is checked out in the MAIN checkout — currently
`refactor/ai-stack-cleanup` — while `verify-merge-protocol.ps1:100` cuts its scratch line
from `development`:

```powershell
Invoke-DrillGit branch drill/verify-d development
```

That hook invokes `./scripts/checks/check-corpus-exposure-producers.ps1`, which exists on
`refactor/ai-stack-cleanup` and **not** on `development`. The path is CWD-relative, and the
CWD is the drill's worktree, so the hook cannot find it, exits non-zero, and the pre-commit
step reports "Pre-commit validation failed (a corpus insert does not state its plane)!".

Both developers' commits in the drill's step 2 are refused. Nothing is ever committed, so
six checks fail in a cascade — step 2's "two divergent commits exist", step 8's "rebase
produced a real conflict" and "the tested sha is no longer what would land", and all three
of step 11's outcome checks. **The 60 checks that do pass are unaffected**, including every
queue.ps1 assertion, "development NEVER moved", "operator checkout still on its own branch"
and the cleanup checks.

The mismatch is structural, not a one-off: any hook the operator adds ahead of `development`
breaks the drill the same way. The obvious fix is for the drill to cut `drill/verify-d` from
`Resolve-WorkLine` (the same branch every agent worktree is cut from) rather than from the
literal string `development` — but that is a change to a coordination script several agents
depend on, it is not one of the six defects `harnessq` names, and its anchor scopes the
artifact to `queue.ps1` and its test suite. Filed here rather than done.

## F3 — MERGE-PROTOCOL.md does not know about the developer's way back

`documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md:305` documents
`-Requeue` as the reviewer's stale-pass move only. `harnessq` defect 6 adds a second caller —
the developer, from `test-passed` — and the protocol is what agents actually read.

Deliberately **not** changed by `harnessq`: that item's anchor scopes the artifact to
`queue.ps1` and its test suite, and explicitly says documenting a rule elsewhere is not the
item. The tool tells you about the path (`-Requeue` refuses from other states with a message
naming both journeys, and `README.md` carries a one-line note), so nothing is unreachable —
but the protocol doc is the place an agent looks first, and it is now incomplete.

---

## F4 - a MALFORMED worktrees value still refuses everybody (found while fixing the empty one)

Filed by queue item `regempty`, whose anchor scopes it to the EMPTY-but-present registry.
This is the neighbouring shape, verified by running it, and deliberately left alone.

`queue.ps1` `Test-KnownAgent` now reads the registry as: missing -> exempt, unparseable ->
exempt, null -> exempt, zero rows -> exempt, one or more rows -> enforce. "One or more rows"
is `@($rows.PSObject.Properties).Count`, and PowerShell hands out .NET intrinsic members for
anything that is not an object:

```
PS> $rows = ('{"worktrees":"qdev"}'   | ConvertFrom-Json).worktrees; @($rows.PSObject.Properties).Name
Length
PS> $rows = ('{"worktrees":["qdev"]}' | ConvertFrom-Json).worktrees; @($rows.PSObject.Properties).Name
Count Length LongLength Rank SyncRoot IsReadOnly IsFixedSize IsSynchronized
```

So a registry whose `worktrees` is a STRING or an ARRAY - a hand-edit, a half-written file, a
future writer that stores rows as a list - parses fine, counts as populated, and enforces
membership against `Length` / `Count` / `Rank`. Every `-Submit` is refused with exit 4 and a
message telling the developer to provision a worktree they already have: the exact failure
`regempty` fixed, reached by a different door.

Not fixed there because `regempty`'s anchor names one condition and one shape, and its
acceptance forbids trading the availability fix for a weaker authorization check - and the
right answer here is not obvious. Exempting anything non-object turns a corrupt registry into
a silently-open door; the better shape is probably to REFUSE LOUDLY on a registry that is
present but not an object ("your registry is malformed", not "you are not registered"), which
is a new third outcome and its own red-first test. Nobody has hit it yet: every writer in the
tree (`new-worktree.ps1`, `remove-worktree.ps1`) writes a hashtable.

## F5 - once an item is back at `ready-to-test`, NOTHING can revise its plan or its SHA

Found 2026-09-10 while running `podlinks` and `crashloop` through nine and eight
rounds. It bit twice in one session, from opposite directions, and the second
time it left a tester pointed at a stale plan and a superseded commit.

`-Submit`, `-Resubmit` and `-Requeue` all accept `-TestPlan`, and the comment on
the third calls itself "the third door into the same room". There is no door
from `ready-to-test`:

- **From the developer.** `-Resubmit` moves `test-failed` -> `ready-to-test` and
  prints, on success, "if the failure showed the plan missed a case, add it and
  re-submit with `-TestPlan`". A second `-Resubmit` is then refused - "only a
  test-failed item is re-submitted" - so the advice it prints is unreachable by
  the person it is printed to. (Workable once you know: pass `-TestPlan` on the
  FIRST call. Nothing says so.)
- **From the reviewer.** `-Requeue` lands the item at `ready-to-test` and does
  NOT re-read `submitted_sha`. But a reviewer's return exists precisely because
  the DEVELOPER must change something first, so the recorded SHA is stale by
  construction the moment the developer commits the fix - and the developer has
  no verb that updates it, because of the bullet above.

Consequence, measured: after review returned `podlinks` at attempt 10, the queue
held a plan snapshot at the OLD path with the old content, and `submitted_sha`
36db16d while the branch tip was 71762ae with the review fix in it. A tester
executing the queued copy literally would test a superseded commit against a plan
that predates the reason it was returned. The workaround is to tell the tester in
its dispatch that the queued copy is stale and to execute the plan ON THE BRANCH
IT CHECKS OUT - which works only because the plan ships in the repo, and which
puts the developer back in the loop of instructing the tester, exactly what the
queued copy exists to prevent.

FIX SHAPE, smallest first:
1. `-Resubmit` accepts `-TestPlan` from `ready-to-test` when the caller is the
   developer AND the item is unclaimed, revising the plan and `submitted_sha`
   without bumping the attempt. Cheap, and closes both directions.
2. `-Requeue` by a reviewer should leave the item where the developer can act -
   or at minimum blank `submitted_sha` so a tester cannot silently test the
   commit that was returned.
3. A PLAN HASH recorded next to the verdict, so "the plan the tester executed"
   is checkable after the fact rather than assumed. Already wanted for other
   reasons ([[agent-harness-findings-note-audit]]).

Related: F3 records that MERGE-PROTOCOL.md does not describe the developer's way
back. This is the same seam - the pipeline is well specified going forwards and
under-specified going backwards, and every round that returns work travels
backwards.
