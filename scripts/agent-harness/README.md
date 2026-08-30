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
| `remove-worktree.ps1` | Retire a worktree, **refusing** while it holds uncommitted or unmerged work (`-Force` to discard deliberately); `-PruneRegistry` drops rows whose path is gone |
| `config.ps1` / `config.py` | The configuration, read the same way from PowerShell and from the bridge: `harness.config.json` < `harness.local.json` < environment. Holds the role/model profiles, the TTLs, the paths, and the on/off switches. `test_harness_config.py` asks both readers the same questions so they cannot drift |
| `anchor.ps1` | The SHAPE of an anchor and whether one is usable. Owns no state; `queue.ps1` asks it whether the anchor it was handed is worth gating on |
| `common.ps1` | Dot-sourced by the rest: resolves the SHARED coordination state dir, the work line, and stderr-safe git capture. Not run directly |
| `verify-merge-protocol.ps1` | Executable proof of MERGE-PROTOCOL's two-agent path: 45 checks against a scratch line (never `development`), self-cleaning. Run it after changing any script here or the protocol |
| `queue.ps1` | The work pipeline: `-Propose` / `-ConfirmAnchor` / `-Submit` / `-Claim -Role tester|reviewer` / `-Pass` / `-Fail` / `-Approve` / `-Requeue` / `-Merged` / `-Reject` / `-List`. Enforces separation of duties (exit 4), the anchor gate (exit 5) and the stale-pass rule |
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
.\scripts\agent-harness\remove-worktree.ps1 -Id wiki-perf
```

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

Four things are worth knowing before relying on it:

- **The board is currently RED on this repository**, and that is not a bug in the
  board. `git-error-swallowed` reports 18 unchecked git call sites across
  `scripts/checks/*.ps1` and `scripts/agent-harness/*.ps1` — including
  `Invoke-DrillGit` and `Get-DrillGit` in `verify-merge-protocol.ps1`, the incident's
  own functions. `protected-ref-moved` is indeterminate until a run records a
  baseline, and indeterminate is deliberately not a pass. A `dark` run refuses to
  auto-pass until those clear — which is the andon cord working, not a false alarm.
- **`work-branch-on-remote` narrows to the branch the run owns.** At a gate,
  `Invoke-AutoGate` passes only `$item.branch`, so a `dark` run is blocked by *its
  own* branch being on a remote, not by anybody else's. Run bare —
  `andon.ps1 -Evaluate` with no `-RunBranch` — it asks the broader question, and today
  it names the eleven `work/*` branches that reached `origin` on 2026-08-30. Both
  readings are deliberate; only the narrow one gates a run.
- **`-VerifyAudit` exit 7 is not a pass.** It means the check found items it could
  not audit (items predating the ledger). Coverage it does not have is not coverage.
  Nor is a green a claim about gates an item never reached — it prints that scope.
- **`auto:` is a reserved principal namespace.** `-ConfirmAnchor` and `-Approve`
  refuse a `-By` inside it (exit 4), and the auto path never signs as a person.

**Five ways of switching the board off were tried against the real gate, and each halts a
`dark` run at the first gate under its own board state, recorded in the ledger:**

| what you do to the board | board state | gate |
|---|---|---|
| `andon.enabled: false` | `not-evaluated` | halts |
| delete the whole `andon` block | `incomplete` (all five named) | halts |
| set `enabled: false` on one condition | `partial` (names it) | halts |
| **delete condition ENTRIES from `andon.conditions`** | `incomplete` (names the missing ids) | halts |
| **set `on_fire` to anything but `halt`** | `warned` (names what fired) | halts |

Each row is a drill case driving the real `queue.ps1` (steps F, H and J). **The last two
rows were OPEN until 2026-08-30**, and both were closed against a reproduction:

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
  The gate record does carry `andon.repo`, so the redirect is *visible afterwards* to a
  reader who checks it — it is not refused.
- **id squatting**, which is the first two together: a required id kept on an entry that is
  a different check.

`on_fire` is not in that list any more, and neither is deleting entries. The safe reading of
this section is: *the set of conditions cannot be thinned, switched off, or downgraded into
silence; a condition that is present can still be pointed somewhere else.*

The revert to prior behaviour is `pipeline.gate_profile: attended`. That is the configured
**default, not a lock**: `queue.ps1 -GateProfile dark` names a profile for one call and
takes the dark path whatever the config says (drill step I drives the same item both ways
— exit 5 attended, exit 6 dark).

The whole mechanism has its own drill: `drill-dark-factory.ps1` shows every condition
firing on a constructed instance and not firing on a clean one, runs the pipeline end
to end with nobody at either gate, proves the completeness check goes red on a tampered
trail, proves that turning the board off — or thinning it by deleting condition entries —
halts rather than opens, and re-runs the clean board afterwards so a fix that refused
everything would be caught. Every WRITE it
makes is to a scratch repository under `$env:TEMP` with the config and state dir
redirected; it makes exactly one READ of a real repository, by name — one case scans
this checkout's own `.ps1` files so the detector is shown naming the incident's
function in the code that actually shipped.
