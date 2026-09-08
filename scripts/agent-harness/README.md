# scripts/agent-harness — per-agent isolation for parallel Claude sessions

Tooling for the worktree-per-session policy (CLAUDE.md, 2026-08-23) that until
2026-08-28 had no mechanism. Two Claude sessions sharing one checkout is how one
session's `git add` sweep captured another's staged OB1 gitlink.

Renamed from `scripts/worktree/` on 2026-08-28: the directory holds the queue, the
roles, the leases, the configuration and the verification drill, and "worktree" named
one of them. **The module boundary, its configuration and its off switch are in
[MODULE.md](MODULE.md)** — read that first if you are lifting this into another
distribution, turning it off, or changing which models the roles run on.

The protocol these scripts serve:
[documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md](../../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md).
The wider design (test containers, bridge integration):
[PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/PLAN.md).

| Script | Does |
|---|---|
| `new-worktree.ps1` | Provision `.claude/worktrees/wt-<id>` on `work/<id>` from the work line, init the OB1 submodule, copy runtime env files, CRLF-check, register it |
| `sync-worktree-env.ps1` | Re-copy `.env` / `.env.test` / `OB1/docker/.env` into worktrees when the main checkout's copy is newer (`-WhatIfOnly` reports drift) |
| `remove-worktree.ps1` | Retire a worktree, **refusing** while it holds uncommitted or unmerged work (`-Force` to discard deliberately); `-PruneRegistry` drops rows whose path is gone. On a removal that proceeds it also **reaps that owner's test containers and networks** (see below) |
| `reap.ps1` | Remove the docker resources an owner created to prove out its work: `-Owner <id>` (delete that owner's containers + networks), `-Report` (owned / orphaned / protected / out-of-scope), `-RemoveOrphan <names>` (delete unlabelled leftovers, named out loud), `-WhatIfOnly`. Never touches a compose-managed resource, an image or a volume |
| `verify-reap.ps1` | Executable proof for `reap.ps1`: 33 checks, 9 cases, each building the thing that must SURVIVE next to the thing that must go. Self-cleaning, and its own fixtures carry the ownership label so a killed run is reapable |
| `config.ps1` / `config.py` | The configuration, read the same way from PowerShell and from the bridge: `harness.config.json` < `harness.local.json` < environment. Holds the role/model profiles, the TTLs, the paths, and the on/off switches. `test_harness_config.py` asks both readers the same questions so they cannot drift |
| `anchor.ps1` | The SHAPE of an anchor and whether one is usable. Owns no state; `queue.ps1` asks it whether the anchor it was handed is worth gating on |
| `common.ps1` | Dot-sourced by the rest: resolves the SHARED coordination state dir, the work line, and stderr-safe git capture. Not run directly |
| `verify-merge-protocol.ps1` | Executable proof of MERGE-PROTOCOL's two-agent path: 72 checks against a scratch line (never `development`), self-cleaning. Run it after changing any script here or the protocol. It cuts that scratch line FROM `development`, so it inherits that branch's pre-commit hooks: while `scripts/checks/check-corpus-exposure-producers.ps1` is absent there the hook fails and SIX checks go red (`two divergent commits exist` and the five that depend on those commits existing) - a fact about the base, not about the protocol |
| `verify-queue-defects.ps1` | Executable proof for the `queue.ps1` defects found by USE: D1-D7 (2026-09-04), D8/D9 (2026-09-06) (the per-case `-Pass` rule replayed against the REAL `curator2` evidence in `documentation/evidence/passplan/fixtures/`, and plan-hash drift), D10/D11 (item-file encoding and plan readability), and D12-D17 (2026-09-06: deploy surfaces derived not declared, `-Deployed`'s health evidence, unresolvable commits and OB1 pins, the hand-off flag on terminal states, the attempt bump after an anchor amendment, and the unterminated-fence warning), plus the regression column they must not have broken. 213 checks. Fully hermetic - its own scratch repo and state dir per case, so it can never touch the real queue. `-Script <path>` names WHICH `queue.ps1` to drive: point it at the copy you edited, and at the copy you did not, to see it go red |
| `queue.ps1` | The work pipeline: `-Propose` / `-ConfirmAnchor` / `-Submit` / `-Claim -Role tester|reviewer` / `-Pass` / `-Fail` / `-Approve` / `-Requeue` / `-Merged` / `-Deployed` / `-Reject` / `-List` / `-Show`. `-Merged` derives from the merge range what the item SHIPS, and `-Deployed -By <person> -Evidence <...> [-Surface <one>]` closes those surfaces with health evidence - see [what a merge SHIPS](#what-a-merge-ships-undeployed-and--deployed). Enforces separation of duties (exit 4), the anchor gate (exit 5) and the stale-pass rule. `-Requeue` is also the DEVELOPER's way back from `test-passed` when the artifact itself must change |
| `lease.ps1` | Named exclusive leases for the SHARED RUNTIME only (planes): `-Acquire` / `-Refresh` / `-Release` / `-Status` / `-Takeover` (exit 3 = held, wait). Names validate against `lease-names.conf` (`-AdHoc` to escape); multi-name requests are sorted + all-or-nothing, so agents cannot deadlock |

`lease.ps1` is deliberately **generic mechanism** with zero repo coupling (its only
native call is git, via `common.ps1`, to locate the shared lock namespace);
`lease-names.conf` is the per-environment **policy** (one lease per
compose plane + `merge`). Porting the toolkit elsewhere = copy the scripts, rewrite
the conf. `AI_STACK_LEASE_DIR` / `AI_STACK_LEASE_NAMES_FILE` override the defaults
(the tests use the former to stay hermetic). (Lineage, so nobody re-proposes a dead branch: `merge-lock.ps1` → a `merge` lease →
**no merge lock at all**. Merging needs no mutex - a worktree isolates files and git
refuses two worktrees on one branch - so landing is governed by `queue.ps1`'s separated
roles instead. Leases now cover only the shared runtime.)

## Label what you create, and cleanup happens to you

**One rule.** Anything you `docker run` or `docker network create` to prove out your work
carries your worktree id:

```powershell
docker run    --label ai-stack.harness.owner=<your-wt-id> ...
docker network create --label ai-stack.harness.owner=<your-wt-id> ...
```

`remove-worktree.ps1` then deletes exactly those when your worktree is retired, and
`reap.ps1 -Report` says at any time what test junk exists and whose it is. Both spellings
of your id work (`x` and `wt-x`).

**Why the label and not a cleanup block at the end of your script.** The runs that leak are
the runs that never reach their own last line. Every drill here already tears down in a
PowerShell `finally`, and a `finally` does not run when the script is killed - Ctrl+C, a
crashed turn, or a background task killed when a turn ends. On 2026-09-07 that had left ten
dead containers and two orphan networks on the daemon, the oldest ten days. A label is
recorded at CREATION and survives everything that can kill the creator, so cleanup needs no
cooperation from the thing being cleaned up. Keep your `finally` - it is still the fastest
path - but the label is what makes the leak recoverable.

**What it will never touch.** Compose-managed containers and networks (checked positively,
by the `com.docker.compose.project` label, so writing the ownership label into a plane's
compose file gets a REFUSAL rather than a deleted prod service), docker's built-in networks,
and every image and volume - those are reported and left alone. `docker volume prune` is a
standing hazard in this stack, and a deleted test image costs a rebuild nobody asked for.

**Unlabelled leftovers are never auto-deleted.** They are listed by `-Report` with their
age, and removing one takes `reap.ps1 -RemoveOrphan <name>` - it has to be typed. Something
unlabelled may be an operator's hand-run sidecar, and two of the ten found on 2026-09-07
(`amtest`, `bundlegen`) matched no script in the repository at all.

Runtime state lives in **`<git-common-dir>/agent-worktrees/`** (`worktrees.json`
registry + `locks/<name>.json` leases) - anchored on the repository, NOT on this
folder. That distinction is load-bearing: state resolved from `$PSScriptRoot` meant a
copy of this toolkit inside a worktree got its own private, gitignored lock dir, so
two agents could each be told `ACQUIRED` for `merge` and exclude nobody. Found by the
first soak run. Overrides: `AI_STACK_WORKTREE_STATE`, `AI_STACK_LEASE_DIR`.

**The work line** - the branch agents branch from and land on - resolves as
explicit `-Base` > `AI_STACK_WORK_LINE` > the main checkout's current branch >
`development`. Defaulting to the loaded branch means agents inherit the tooling and
docs on it. When that branch is checked out in the main checkout it cannot be a merge
target (git refuses a second checkout), so provisioning warns and the protocol
hands the merge back to the operator.

**Tests:** `python scripts/claude-sessions-bridge/test_worktree.py` — 16 tests covering
the `worktree:` directive grammar, id derivation, the fail-closed contract, real
provisioning / removal-refusal, and the lease semantics (contention, disjoint planes
not serializing, foreign-release refusal, expiry boundary, multi-name rollback, typo
refusal). Self-skips off Windows; always cleans up.

## Using it from Mattermost

A bridge thread opts in with **`worktree: on`** (persisted per thread, like `model:`).
The bridge then provisions `wt-mm-<thread8>` on first use, runs every turn there, keeps
env copies fresh, and on `close` retires the worktree — *unless* it still holds unlanded
work, in which case it says so and keeps it. Default is off
(`BRIDGE_WORKTREE_DEFAULT`). If provisioning fails, the turn does **not** run: falling
back to the shared checkout would look like isolation while providing none.

## Why a bare `git worktree add` is not enough here (all verified, not assumed)

1. **It materializes tracked files only** — the worktree has no `.env`, `.env.test`
   or `OB1/docker/.env`, so every compose command in it fails or silently takes
   defaults. Measured: bare worktree = 0 of 3 present, `docker compose config` fails.
2. **It does not populate the OB1 submodule** — you get an empty directory.
3. **Base branch** — the harness's `EnterWorktree` branches from the *origin default
   branch*, not the line you actually have loaded. The script resolves and passes the
   base explicitly.

Plus one hole found while testing: `.env.test` is gitignored on some branches but not
on `development`, so a development-based worktree listed a **copied secrets file as
untracked** — one `git add .` from the accident `.gitignore`'s own comment warns
about. The script now adds any such copy to `.git/info/exclude` (the *common* one;
a per-worktree `info/exclude` is **not** honored — verified).

## Gotchas paid for in this code

- **A TRUNCATED SEARCH IS NOT A SEARCH.** An agent put a script out of scope on the
  grounds that it had grepped it and found nothing. The grep was piped through
  `head -20`, twenty comment lines consumed the budget, and "nothing shown" was read as
  "nothing there". The script had sixteen instances of exactly what it was looking for.
  If a search decides a scope boundary, run it unbounded and count the results - and if
  you must truncate, say in your finding that you did.
- **Never `--no-verify`.** The same agent reflexively bypassed the pre-commit hooks on its
  first commit, then reset and re-committed with them running. The reflex is the thing to
  watch for: the hooks are the repo's only automatic guard against secrets, line endings,
  gateway-routing bypasses and env_file scope, and an agent that skips them is removing
  the check that exists precisely because humans and agents forget.

- **An agent's PATH is not the operator's PATH.** A tester concluded that a README
  command was unrunnable because `Get-Command grep` returned nothing in its process; I
  contradicted it because `grep` resolves in mine. Both measurements were correct and both
  conclusions were over-general. `grep.exe` exists at
  `C:\Program Files\Git\usr\bin\grep.exe`, and whether it resolves depends entirely on the
  PATH the shell was launched with - the harness process here gets only
  `C:\Program Files\Git\cmd` (git.exe and friends, no Unix tools). **"The tool is not
  available" is a fact about your process, never about the machine.** Say which shell you
  measured in, and check the one the reader will actually use.

- **Never `2>&1` a native command in PS5.1.** It wraps every stderr line in an
  ErrorRecord, so with `$ErrorActionPreference='Stop'` git's ordinary progress
  chatter ("Preparing worktree...") becomes a terminating error. This script died on
  exactly that on its first run. Trust `$LASTEXITCODE`.
- **CRLF in tracked `*.sh`** inside a worktree breaks docker builds
  (`$'\r': command not found`). `.gitattributes` should prevent it; `new-worktree.ps1`
  verifies rather than assumes (`-StrictCrlf` to fail instead of warn).
- **Keep ids short.** Each worktree carries a full OB1 checkout; Windows MAX_PATH
  plus `node_modules` depth is a real ceiling. Ids are capped at 24 chars.
- **Leases are files, not ports.** Both entry points share this filesystem, and a
  lease must name its owner so a stuck one can be taken over with someone to notify.
- **Lease names are validated fail-closed.** A typo ("openbrain" vs "open-brain")
  would create a second lock for the same plane and protect nothing — unknown names
  are refused unless `-AdHoc` says the new coordination point is deliberate.

## Typical session

```powershell
# agree what the work is FOR before building toward it - the operator confirms, and
# -Submit refuses (exit 5) until they have:
.\scripts\agent-harness\queue.ps1 -Propose -Id wiki-perf -Anchor <anchor.json> -Developer wiki-perf
#   ... operator: queue.ps1 -ConfirmAnchor -Id wiki-perf -By <them> ...
.\scripts\agent-harness\new-worktree.ps1 -Id wiki-perf -OwnerKind extension -OwnerRef <session>
# EnterWorktree path: <printed path>   ... do the work ...
.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain -Owner wiki-perf   # mutating test
#   ... test against the plane, clean up your droppings ...
.\scripts\agent-harness\lease.ps1 -Release -Name open-brain -Owner wiki-perf
# hand it to the pipeline - you do not test or merge your own work:
.\scripts\agent-harness\queue.ps1 -Submit -Id wiki-perf -Branch work/wiki-perf -Developer wiki-perf -TestPlan <path>
#   a tester claims + executes the plan; a reviewer rebases, merges --no-ff, and records it
#   -Merged prints what that merge SHIPS; the item reads [UNDEPLOYED: ...] on -List until
#   a person deploys it and records that, with health evidence:
.\scripts\agent-harness\queue.ps1 -Deployed -Id wiki-perf -By <them> -Evidence <path>
.\scripts\agent-harness\remove-worktree.ps1 -Id wiki-perf
```

## What a merge SHIPS: `[UNDEPLOYED]` and `-Deployed`

`merged` used to be the last thing the board said about an item, and for some items that
is a lie: a merge that bumps the OB1 gitlink does not rebuild the image, and a merge that
changes a file under `owui/` does not paste it into Open WebUI. Since 2026-09-06 `-Merged`
works out what the merge SHIPS and records it, so `-List` can say `merged, not live`:

```text
curatorimg         merged            wt-curatorimg         [UNDEPLOYED: image:openbrain-curator]
curatorpool        closed-outside-gates wt-curatorpool     [UNRESOLVABLE: OB1 22f41b6]
```

Three flags, in the operator's terms:

- **`[UNDEPLOYED: image:<service>, paste:<file>]`** - this merged item is not live yet.
  The list is DERIVED at `-Merged` from `git diff --name-only <first parent>..<merge sha>`,
  never from anything the author typed: an OB1 gitlink move whose OB1 diff touches an
  `integrations/<dir>/` that has a `Dockerfile` becomes `image:<the compose service that
  builds it>`; a changed `owui/` file **that `owui/manifest.csv` lists** becomes
  `paste:<that file>` (the manifest is the file-to-OWUI-id map, so it is the authority on
  what is pasteable at all - a change to the manifest or to `owui/README.md` derives
  nothing, and an unlisted `owui/` file is reported as a NOTE); a changed build context of
  a `:local`-tagged service in this repository becomes `image:<that service>`. Most merges
  derive nothing and record an empty list. Items merged before that date have no surfaces
  and read as plain `merged`.
- **`[UNRESOLVABLE: submitted_sha <sha>]` / `[UNRESOLVABLE: OB1 <sha>]`** - a commit this
  row records, or the OB1 commit pinned at it, exists in no clone the tool can reach (the
  item's worktree, the main checkout, the current repository). That row is not live work,
  so it sorts to the top of the board. `-Show` prints the same under `--- RESOLUTION ---`.
- **`[needs hand-off]`** - the reviewer cannot merge this because the work line is checked
  out elsewhere. It appears only on items still moving; a terminal one has nothing left to
  merge (before 2026-09-06 the flag was written at `-Submit` and never cleared: 32 of the 42
  rows on the live board carried it and 31 of those were terminal, so the one row where it
  was true was one in thirty-two - the count moved three times in a day as the board did,
  and `documentation/notes/deploy-gate-2026-09-06.md` reconciles the three readings).

Closing a surface is a RECORD of a deploy, never a deploy:

```powershell
# who: a person. The reserved auto: namespace is refused here, as at the two gates.
.\scripts\agent-harness\queue.ps1 -Deployed -Id <id> -By <them> -Evidence <path or text>
# one surface at a time, when they land separately:
.\scripts\agent-harness\queue.ps1 -Deployed -Id <id> -By <them> -Evidence <path> -Surface image:openbrain-curator
```

The evidence must carry, for EVERY surface it closes, a line that names the surface and
gives the pin - the running container's `org.opencontainers.image.revision` label or its
image id, or the pasted file's `sha256` - and a health state (`State.Health.Status=healthy`,
or `State.Status=running` for a container with no healthcheck):

```text
openbrain-curator: label org.opencontainers.image.revision=d89c126, State.Health.Status=healthy, RestartCount=0
```

A pin is **hex** - 7-40 hex characters, or a labelled `sha256:<hex>`; an all-digit token
(an epoch, a run number, a ticket id) is not a pin. The check is line-scoped: it asks that a
line naming the surface also carries a pin and a health state, not that the three are about
the same container - deliberately, because separating them is a judgement about prose.

No health state, an `unhealthy` one, or no pin is a refusal with nothing recorded. A
surface closes once. When the last one closes the item reaches the terminal state
`deployed`. `-Deployed` on an item that derived no surfaces is refused rather than
recorded against nothing - there is nothing there that could fail to be live.

`-List` and `-Show` are read-only and stay so: they resolve every recorded commit in one
batched pass and write nothing. On the 42-row live board that has been a couple of seconds
every time it has been measured - **observations, not a bound**: 1.19-1.88 s in-process and
2.00-2.27 s around the child process over three runs each (developer, 2026-09-06), and
1.02-2.18 s / 2.03-2.39 s over eight runs each (tester, same day, different machine state).
Two honest measurers straddled the narrower of those at both ends without anything changing,
which is why no range is quoted as a promise anywhere: the only claim worth holding the tool
to is the one the test plan checks, that it finishes well inside five seconds and mutates
nothing.

## Running it unattended (`dark` gate profile)

`pipeline.gate_profile` decides who passes the two human gates. `attended` (the
default, and what every example above assumes) means a person runs `-ConfirmAnchor`
and `-Approve`. `dark` means both gates self-pass.

The value is not the automation, it is the record. An auto-passed gate must be
distinguishable afterwards from a human-passed one, or the trail reads as approval
that never happened.

```powershell
# once per run: record where the protected refs stood, so a later move is detectable
.\scripts\agent-harness\andon.ps1 -Baseline

# is the line clear? exit 6 means it is not
.\scripts\agent-harness\andon.ps1 -Evaluate

# drive the pipeline with nobody at the gates
.\scripts\agent-harness\queue.ps1 -GateProfile dark -Submit -Id x -Branch work/x -Developer wt-x -TestPlan <path>

# afterwards, the two questions an operator actually has
.\scripts\agent-harness\queue.ps1 -Audit -Id x          # WHICH gates did no human see?
.\scripts\agent-harness\queue.ps1 -VerifyAudit -Id x    # is the trail complete? 0 / 1 / 7
```

Five things are worth knowing before relying on it:

- **The board is currently RED on this repository**, and that is not a bug in the
  board. `git-error-swallowed` reports 18 unchecked git call sites across
  `scripts/checks/*.ps1` and `scripts/agent-harness/*.ps1` — including
  `Invoke-DrillGit` and `Get-DrillGit` in `verify-merge-protocol.ps1`, the incident's
  own functions. `protected-ref-moved` is indeterminate until a run records a
  baseline, and indeterminate is deliberately not a pass. **That sentence was FALSE at run
  time until 2026-08-30**, and it is worth saying where it is written: with
  `on_indeterminate: warn` on that condition - a one-word config edit - the board printed
  `ANDON BOARD: CLEAR` at exit 0 while listing `[indeterminate] protected-ref-moved  no
  baseline recorded`, the dark gate auto-passed signed `auto:dark`, `-VerifyAudit` said
  COMPLETE, and the condition that could not be evaluated was in no ledger field at all.
  What makes it true is not the `halt` default, which a config overrides; it is that `clear`
  is now decided by a bucket census in which the `indeterminate` bucket must be EMPTY. Drill
  step K is the proof. A `dark` run refuses to auto-pass until those clear - which is the
  andon cord working, not a false alarm.
- **`work-branch-on-remote` narrows to the branch the run owns - at BOTH gates now.**
  `Invoke-AutoGate` hands the board the run's branch, so a `dark` run is blocked by *its
  own* branch being on a remote, not by anybody else's. Run bare — `andon.ps1 -Evaluate`
  with no `-RunBranch` — it asks the broader question, and today it names the eleven
  `work/*` branches that reached `origin` on 2026-08-30. Both readings are deliberate; only
  the narrow one gates a run. **This paragraph was FALSE at the ANCHOR gate until
  2026-08-30**, and it is worth saying where it was written: `-Propose` writes `branch = ""`
  and `-Submit` stores the real branch only AFTER the anchor gate has run, so that gate
  passed no branch at all and the board fell back to the broad question — which on this
  repository is those eleven foreign branches, none of them the run's, and none of them a
  run is permitted to delete. A `dark` run could therefore never auto-pass an anchor gate
  here, for a reason the doc said could not block it. The branch was known at that point
  (`-Submit -Branch` is mandatory); it simply was not passed. Drill step M is the proof, in
  four parts: a foreign branch on a remote passes the anchor gate, the run's own branch on a
  remote still halts it, a branch name that does not resolve leaves the condition
  **unevaluated** rather than clear, and a name that is not a name at all — a space, a tab,
  an empty string — is REFUSED rather than skipped. A narrow question is only as good as the
  name it is handed. **Both of those last two halt the gate, and neither of them is a board
  word to quote**: the condition ends unevaluated, and the board that carries it reads
  `raised`, because an unevaluated condition halts under the shipped policy. Until
  2026-08-30 the whitespace case did not halt at all — both loops in the predicate stepped
  over a name that trimmed to nothing while still counting it, so `-Branch ' '` came back
  "checked 1 branch(es); none is on a remote", a clean board and an AUTO-PASSED anchor gate
  for a question nobody asked. It is refused twice now: at the door by `queue.ps1`, which
  used to count `" "` as a supplied branch because PowerShell calls it truthy, and again by
  the board, so neither is a single point of failure.
- **`-VerifyAudit` exit 7 is not a pass.** It means the check found items it could
  not audit (items predating the ledger). Coverage it does not have is not coverage.
  Nor is a green a claim about gates an item never reached — it prints that scope.
- **`auto:` is a reserved principal namespace.** `-ConfirmAnchor` and `-Approve`
  refuse a `-By` inside it (exit 4), and the auto path never signs as a person.
- **The board's words are checked wherever they are written down.** Four sentences in three
  files listed some of the eight verdict words as though they were all of them, so
  `test_gate_profiles.py` now derives every enumeration of board words in the repository
  (`git ls-files`) and requires it to be complete, and derives every citation of a ways-off
  `route` id and requires it to name the state the table below proves. It reads two list
  shapes — an inline run joined by list punctuation, whose items may be quoted, and a block
  of consecutive lines that each introduce a word, bulleted or numbered or as table or
  definition rows or the word alone on its line. That sentence used to read "both list
  shapes" and name neither; on 2026-08-30 an ordered list and a quoted array were planted as
  tracked files and both stayed green, so the shapes and the limits are now written out in
  the test's DISCLOSED LIMITS block, which is the one place to change them. Write three or more board words as a list and you are enumerating the alphabet;
  if you mean a narrower set, say so in words or write it as a mapping.

**Eight ways of switching the board off, or of getting a pass out of it, were tried against
the real gate. Each halts a `dark` run at the first gate under its own board state, recorded
in the ledger. THIS TABLE IS THE ONE PLACE THAT SAYS WHICH WAY GIVES WHICH STATE** - every
other file cites a row by its `route` id instead of restating the mapping, because the
mapping was restated in three files and was wrong in all three (see below):

| route | what you do to the board | board state | gate |
|---|---|---|---|
| `andon-disabled` | `andon.enabled: false` | `not-evaluated` | halts |
| `andon-block-deleted` | delete the whole `andon` block | `incomplete` (all five named) | halts |
| `condition-disabled` | set `enabled: false` on one condition | `partial` (names it) | halts |
| `conditions-deleted` | **delete condition ENTRIES from `andon.conditions`** | `incomplete` (names the missing ids) | halts |
| `on-fire-downgraded` | **set `on_fire: warn`** | `warned` (names what fired) | halts |
| `on-indeterminate-downgraded` | **set `on_indeterminate: warn`** | `indeterminate` (names what could not be evaluated) | halts |
| `action-word-unimplemented` | set either key to a word the board does not implement | refused at evaluation: exit 1, no verdict, and every gate reads that as `unavailable` | halts |
| `outcome-unenumerated` | **an outcome the board does not enumerate at all** (a new status a predicate grows, a new word added to the allowed actions) | `unaccounted` (names the condition AND the word) | halts |

Each row is a drill case driving the real `queue.ps1` (steps F, H, J and K), and the drill
declares the same map in `$script:WaysOffProven`, which its own assertions read - so a row
here whose word differs from the drill's, or which reaches no drill code at all, fails
`test_gate_profiles.py::test_the_ways_off_table_matches_the_drill_that_proves_it`. **That
test compares two written-down copies of the mapping, and it used to claim more than that.**
Until 2026-08-30 it said a row nobody drills is a claim rather than a proof, and it did not
establish it: a phantom row planted in BOTH copies, with zero assertions anywhere, passed
with the whole suite green. What a row is EXERCISED by is checked where it can be - in the
drill: `Check` registers a route the moment a PASSING assertion cites `(route <id>)` in its
label, and **drill step N** fails on any row declared here that no assertion exercised. The
`route` ids exist so the fact can be CITED rather than copied: any line anywhere in the
repository that names a route id and a board word is checked against this table by
`test_every_citation_of_a_way_off_names_the_state_this_table_proves`. Two of these rows had
their ledger claim unchecked until 2026-08-30: `action-word-unimplemented` asserted
`andon.status = unavailable` in the record and no drill check read that field, and the
block-deletion row was written up in three files carrying the state that belongs to
`andon-disabled` (`not-evaluated`). That is why the citation rule exists rather than a fourth
carefully written sentence.

The two downgrade rows say **`warn`** rather than "anything but `halt`", because that is what
is true: `warn` is the only other word `$script:AllowedAndonActions` holds, and any other
literal is refused before a verdict exists - the `action-word-unimplemented` row. It errs
safe, but the earlier wording ("anything but halt") was not accurate.

**Four of these rows were OPEN until 2026-08-30**, and each was closed against a
reproduction:

- *deleting entries* — pruned to one of five on a genuinely detached checkout, the gate
  **auto-passed** at exit 0, ledger `clear`, `-VerifyAudit COMPLETE`. Every counter it
  printed was true, and every one counted against the config's own thinned list. The five
  required ids now live in `config.ps1`/`config.py`, in code where the config cannot edit
  them.
- *downgrading `on_fire`* — the condition FIRED, the board still reported `clear` at exit 0,
  the gate auto-passed signed `auto:dark`, the pass record's `fired` list was **empty** and
  `-VerifyAudit` called the trail COMPLETE. `fired` was derived from `action -eq halt`, so a
  fire that did not halt had nowhere in the ledger to appear. `fired` and `halted` are now
  separate lists in every verdict and every record, and a board with a fire on it is never
  `clear`.
- *downgrading `on_indeterminate`* - **the identical hole on the sibling key**, still open
  after the fix above and reproduced the same day: `protected-ref-moved` with no baseline
  printed `CLEAR` at exit 0, the gate auto-passed signed `auto:dark`, `-VerifyAudit` said
  COMPLETE, and the ledger read `status: clear, fired: [], halted: []` with the unevaluated
  condition absent from the record entirely. Three rounds running a fix had closed one
  outcome key and left its sibling, so this one is not a third list. The verdict was computed
  **by exception**: a halt flag set only for `action = halt`, a fired list only for
  `status = fire`, every other outcome setting NOTHING, and `clear` as whatever was left when
  nothing objected - so any outcome nobody had enumerated silently meant "fine".
- *an outcome nobody enumerated* - closed by the same change, and it is the general form of
  all four rows above. `clear` is now **proven**: every condition lands in exactly one census
  bucket (`evaluated_ok` / `fired` / `indeterminate` / `disabled` / `unrecognised`), the
  buckets must sum to the conditions in scope, and `clear` requires every bucket but
  `evaluated_ok` to be empty with at least one condition in it. A `(status, action)` pair the
  board does not enumerate falls to `unrecognised`, which refuses - **no branch names the new
  word**. Drill step K proves that by introducing an action word and a status word the
  harness has never contained, in a scratch COPY whose `andon.ps1` is asserted byte-identical
  to the shipped one (K2) or differing by exactly one line inside a predicate (K3). The
  census travels into the ledger, so `-VerifyAudit` **re-derives** the verdict from the
  buckets instead of trusting the word `clear`.

**What is NOT closed, and the sentence here used to claim otherwise.** The board's
*membership* is pinned code-side and is tamper-evident. Its *behaviour* is not: what each
condition DOES is still config-controlled, and three routes through it are open —

- **a predicate swap.** The completeness check compares IDS (`andon.ps1`, `$missingIds`), so
  an entry keeping a required id while naming a different implemented predicate is a full
  board of five to every counter. `test_gate_profiles.py` pins the id → predicate map of the
  **committed** config; nothing pins one edited at run time or named by
  `AI_STACK_HARNESS_CONFIG`.
- **a `params` redirect.** `params.repo` on `operator-checkout-off-branch` pointed at a clean
  decoy checkout, or narrowed `globs`/`refs`/`branches`, leaves the detector running and
  looking somewhere harmless. Nothing pins params, at run time or in the committed config.
  It is not refused. It *is* readable afterwards - but **not** from `andon.repo`, which is
  what this bullet used to claim. `andon.repo` is the checkout the BOARD resolved
  (`$ctx.repo_root`); a `params.repo` override does not touch it, so a redirect run recorded
  the real detached checkout in the ledger while the detector examined the decoy. That was a
  true sentence about a field that does not answer the question. The gate record now also
  carries **`andon.looked_at`**, which names, for every condition, the predicate it ran and
  the params it was handed - so both a redirect and a predicate swap are visible to a reader
  of the ledger. Drill step L builds a decoy checkout and asserts exactly that: the gate
  passes, `andon.repo` names the fixture, and `looked_at` names the decoy.
- **id squatting**, which is the first two together: a required id kept on an entry that is
  a different check.

`on_fire` is not in that list any more, and neither is `on_indeterminate` nor deleting
entries. The safe reading of this section is: *the set of conditions cannot be thinned,
switched off, or downgraded into silence - by either outcome key, or by an outcome word
nobody has thought of yet, because `clear` is decided by a census of buckets rather than by
the absence of a flag; a condition that is present can still be pointed somewhere else, and
that redirect is now readable in the ledger rather than refused.*

The revert to prior behaviour is `pipeline.gate_profile: attended`. That is the configured
**default, not a lock**: `queue.ps1 -GateProfile dark` names a profile for one call and
takes the dark path whatever the config says (drill step I drives the same item both ways
— exit 5 attended, exit 6 dark).

The whole mechanism has its own drill: `drill-dark-factory.ps1` shows every condition
firing on a constructed instance and not firing on a clean one, runs the pipeline end
to end with nobody at either gate, proves the completeness check goes red on a tampered
trail, proves that turning the board off — or thinning it by deleting condition entries —
halts rather than opens, proves that neither outcome key can be downgraded into silence and
that an outcome word the board has never heard of is refused rather than ignored, shows that
a `params` redirect is readable from the ledger, proves that the ANCHOR gate asks the narrow
branch question and that a branch name which does not resolve - or is not a name at all -
halts it rather than clearing it (step M), checks that every row of the ways-off table above
was exercised by an assertion that actually ran (step N), and re-runs the clean board
afterwards so a fix that refused everything would be caught. Every WRITE it
makes is to a scratch repository under `$env:TEMP` with the config and state dir
redirected; it makes exactly one READ of a real repository, by name — one case scans
this checkout's own `.ps1` files so the detector is shown naming the incident's
function in the code that actually shipped.
