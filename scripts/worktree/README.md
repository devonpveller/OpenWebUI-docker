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
| `new-worktree.ps1` | Provision `.claude/worktrees/wt-<id>` on `work/<id>` from `development`, init the OB1 submodule, copy runtime env files, CRLF-check, register it |
| `sync-worktree-env.ps1` | Re-copy `.env` / `.env.test` / `OB1/docker/.env` into worktrees when the main checkout's copy is newer (`-WhatIfOnly` reports drift) |
| `remove-worktree.ps1` | Retire a worktree, **refusing** while it holds uncommitted or unmerged work (`-Force` to discard deliberately); `-PruneRegistry` drops rows whose path is gone |
| `merge-lock.ps1` | Serialize merges into `development`: `-Acquire` / `-Refresh` / `-Release` / `-Status` / `-Takeover` (exit 3 = held, wait) |

Runtime state lives in `state/` (gitignored, like the bridge's): `worktrees.json`
registry + `merge-lock.json`.

## Why a bare `git worktree add` is not enough here (all verified, not assumed)

1. **It materializes tracked files only** — the worktree has no `.env`, `.env.test`
   or `OB1/docker/.env`, so every compose command in it fails or silently takes
   defaults. Measured: bare worktree = 0 of 3 present, `docker compose config` fails.
2. **It does not populate the OB1 submodule** — you get an empty directory.
3. **Base branch** — the harness's `EnterWorktree` branches from the *origin default
   branch*, not `development`, which is this repo's work line. The script passes the
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
- **The lock is a file, not a port.** Both entry points share this filesystem, and a
  lock must name its owner so a stuck one can be taken over with someone to notify.

## Typical session

```powershell
.\scripts\worktree\new-worktree.ps1 -Id wiki-perf -OwnerKind extension -OwnerRef <session>
# EnterWorktree path: <printed path>   ... do the work, test in -p test-wiki-perf ...
.\scripts\worktree\merge-lock.ps1 -Acquire -Owner wiki-perf -Thread <mm-thread>
#   rebase onto development, re-run gates, merge --no-ff with evidence
.\scripts\worktree\merge-lock.ps1 -Release -Owner wiki-perf
.\scripts\worktree\remove-worktree.ps1 -Id wiki-perf
```
