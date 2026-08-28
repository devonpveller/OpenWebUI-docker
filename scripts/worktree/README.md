# scripts/worktree — per-agent isolation for parallel Claude sessions

Tooling for the worktree-per-session policy (CLAUDE.md, 2026-08-23) that until
2026-08-28 had no mechanism. Two Claude sessions sharing one checkout is how one
session's `git add` sweep captured another's staged OB1 gitlink.

The protocol these scripts serve:
[documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md](../../documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md).
The wider design (test containers, bridge integration):
[PLAN.md](../../documentation/implementation-guide/multi-agent-concurrency/PLAN.md).

| Script | Does |
|---|---|
| `new-worktree.ps1` | Provision `.claude/worktrees/wt-<id>` on `work/<id>` from the work line, init the OB1 submodule, copy runtime env files, CRLF-check, register it |
| `sync-worktree-env.ps1` | Re-copy `.env` / `.env.test` / `OB1/docker/.env` into worktrees when the main checkout's copy is newer (`-WhatIfOnly` reports drift) |
| `remove-worktree.ps1` | Retire a worktree, **refusing** while it holds uncommitted or unmerged work (`-Force` to discard deliberately); `-PruneRegistry` drops rows whose path is gone |
| `common.ps1` | Dot-sourced by the rest: resolves the SHARED coordination state dir, the work line, and stderr-safe git capture. Not run directly |
| `verify-merge-protocol.ps1` | Executable proof of MERGE-PROTOCOL's two-agent path: 21 checks against a scratch line (never `development`), self-cleaning. Run it after changing any script here or the protocol |
| `lease.ps1` | Named exclusive leases — the test queue AND the merge queue in one primitive: `-Acquire` / `-Refresh` / `-Release` / `-Status` / `-Takeover` (exit 3 = held, wait). Names validate against `lease-names.conf` (`-AdHoc` to escape); multi-name requests are sorted + all-or-nothing, so agents cannot deadlock |

`lease.ps1` is deliberately **generic mechanism** with zero repo coupling (its only
native call is git, via `common.ps1`, to locate the shared lock namespace);
`lease-names.conf` is the per-environment **policy** (one lease per
compose plane + `merge`). Porting the toolkit elsewhere = copy the scripts, rewrite
the conf. `AI_STACK_LEASE_DIR` / `AI_STACK_LEASE_NAMES_FILE` override the defaults
(the tests use the former to stay hermetic). (`merge-lock.ps1`, its single-lock
predecessor, was deleted the day after it landed — superseded by `-Name merge`; two
lock implementations coordinating one filesystem is its own smell.)

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
.\scripts\worktree\new-worktree.ps1 -Id wiki-perf -OwnerKind extension -OwnerRef <session>
# EnterWorktree path: <printed path>   ... do the work ...
.\scripts\worktree\lease.ps1 -Acquire -Name open-brain -Owner wiki-perf   # mutating test
#   ... test against the plane, clean up your droppings ...
.\scripts\worktree\lease.ps1 -Release -Name open-brain -Owner wiki-perf
.\scripts\worktree\lease.ps1 -Acquire -Name merge -Owner wiki-perf
#   rebase onto the work line, re-run gates, merge --no-ff with evidence
.\scripts\worktree\lease.ps1 -Release -Name merge -Owner wiki-perf
.\scripts\worktree\remove-worktree.ps1 -Id wiki-perf
```
