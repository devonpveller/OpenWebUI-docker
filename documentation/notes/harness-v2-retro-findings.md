# harness-v2 retro test — findings

Retro test of the three harness-v2 commits that were committed WITHOUT going through
the pipeline (`d504e9e`, `0ebebf4`, `55b31e1`). The commit message of `0ebebf4` states
the omission plainly and says "Retro test/review remains owed"; this is that test.

Executed 2026-08-29 by `wt-dfu-inbox`, who did not write the commits. Tested against the
work line at `8d71ae7`.

Every claim below was checked against the code path, not against the commit prose.

## Baseline (inherited green, re-confirmed)

- `scripts/agent-harness/verify-merge-protocol.ps1` — **51/51 checks passed**.
- `python -m pytest scripts/agent-harness scripts/claude-sessions-bridge -q` — **91 passed**.
  (The dark-factory PLAN §5.4 predicted 90; the extra test is real and belongs to the
  committed harness work. Higher than the stated baseline, so not a regression.)

## Claims VERIFIED

| Claim (source commit) | How it was checked | Result |
|---|---|---|
| The rename `scripts/worktree` -> `scripts/agent-harness` left no runtime path behind (`55b31e1`) | `git grep` over ALL tracked files for both slash and backslash spellings — a complete search, not a truncated one | HOLDS. Every surviving hit is deliberate historical prose (MODULE.md, README.md, two PLANs). See F2 for the one exception. |
| The bridge reaches the harness by a path built from components (`55b31e1`) | Read `bridge.py:147` — `os.path.join(_REPO_ROOT, "scripts", "agent-harness")` | HOLDS, and this is the exact shape a string search-replace misses. It was updated correctly. |
| `env_file` removed from open-terminal, little-coder, the search gateway; tailscale declares its one variable (`d504e9e`) | `git grep env_file` over all tracked compose files | HOLDS. `coder/`, `search/` and `frontend/` each carry the removal plus the reasoning comment. |
| "Known remaining: agent-org's ao-worker-1/2" (`d504e9e`) | Read all three surviving `env_file` sites in `agent-org/docker/docker-compose.yml` | HOLDS, and the scoping is precise. L283/L376 are the two ao-workers on the shared root `../../.env`. L109 is agent-bridge on its OWN local `.env` — a scoped file, not a wildcard grant of the root, so its exclusion is correct rather than an oversight. |
| `check-env-file-scope.ps1` is pre-commit check #5 (`d504e9e`) | Read `.githooks/pre-commit` | HOLDS. Fifth in order: secrets, line endings, gateway routing, project configs, env-file scope. |
| The OFF switch "exits 2 with a sentence naming the setting: inert, not degraded" (`0ebebf4`) | Ran `queue.ps1 -List` with `AI_STACK_HARNESS_ENABLED=0` | HOLDS. Exit code 2, and the message names both `harness.config.json` and the env var. |
| `-Merged` verifies the sha actually contains the branch (`0ebebf4`) | Read `queue.ps1:558-598` to the end of the block; read `Invoke-GitCapture` in `git-io.ps1`; exercised the underlying git primitive on real refs | HOLDS — see the detail below, because it has three separate ways to be wrong and none of them are. |

### Why the `-Merged` guard actually holds

This one was checked hardest, because it is the guard born from a real incident (a merge
that failed with exit 2, an unchecked exit code, and `-Merged` recording the pre-merge tip).

1. The logic is `git merge-base --is-ancestor <branch> <sha>` with `$LASTEXITCODE -ne 0`
   treated as failure — so a git ERROR (a deleted branch, exit 128) fails closed, the same
   as a plain "not an ancestor". Correct direction.
2. `Invoke-GitCapture` flips `$ErrorActionPreference` to `Continue` around the native call
   and restores it in a `finally` that only assigns a variable — so it does not clobber
   `$LASTEXITCODE`. This is the PS5.1 trap the guard depends on NOT hitting, and it does
   not hit it.
3. The primitive was exercised on real refs rather than assumed:
   `git merge-base --is-ancestor 39d2108 fa06397` -> exit 0 (the coder-rm tip IS in its
   merge commit); `git merge-base --is-ancestor 39d2108 591662c` -> exit 1 (it is NOT in
   the pre-merge tip, which is precisely the mistake the guard exists to catch).

## FINDINGS

### F1 — the sha-containment guard has no drill coverage (the substantive finding)

`verify-merge-protocol.ps1` calls `-Merged` exactly four times (L247, L249, L251, L283).
Two are refusal cases about the FITNESS verdict, and two are correct merges with correct
shas. **No drill check ever passes `-Merged` a sha that does not contain the branch.**

So the guard works today (proven above) but is not defended by the harness's own regression
net: an edit that inverted the exit-code test, or that reintroduced the `$LASTEXITCODE`
capture trap, would leave 51/51 green.

This is the plan's own audit verdict A6 turned back on the harness — the lesson that prose
verification is weaker than an executable check, applied to the guard that a real incident
paid for. It is a gap in coverage, not a defect in behaviour; nothing is currently broken.

Fix shape (NOT done here — a tester reports, a developer fixes under its own anchor): one
drill check that drives an item to `ready-review` and calls `-Merged` with the pre-merge
tip, expecting a non-zero exit and the item still NOT in state `merged`.

### F2 — a stale path in a .gitignore comment (cosmetic)

`.gitignore:81` says the worktrees directory is "created by `scripts/worktree/new-worktree.ps1`".
That script is now `scripts/agent-harness/new-worktree.ps1`.

The ignore PATTERNS are correct and were verified behaviourally, not by eye:
`git check-ignore -v .claude/worktrees/wt-dfu-inbox/.env` resolves to `.gitignore:83
.claude/worktrees/`. So this is a comment that will misdirect a reader, not a rule that
fails to ignore. The neighbouring comment about the state directory's move is deliberate
history and should stay.

### F3 — the env_file fix is not yet applied to running containers (open operator action)

`d504e9e` states this plainly rather than hiding it: taking effect requires recreating
open-terminal, little-coder, the search gateway and tailscale, and tailscale shares
openwebui's netns so it is not a casual restart. Recorded here so it is not lost — the
commit is landed but the runtime is still on the old grant. Operator's call, and it needs
a coder + frontend plane lease when it happens.

## VERDICT

**PASS, with one coverage gap (F1) and one cosmetic defect (F2).**

Every behavioural claim the three commits make is true against the code path. Notably, the
commits' self-reported limitations ("known remaining ao-worker-1/2", "not applied to running
containers", "little-coder wired but UNPROVEN") were all accurate — the honesty holds up
under checking, which is the thing most worth knowing about work that skipped review.

Nothing found here argues for reverting or blocking. F1 and F2 are follow-on items and are
recorded here rather than fixed in place, because the tester does not fix their own findings
and because neither belongs in the durable-inbox artifact.
