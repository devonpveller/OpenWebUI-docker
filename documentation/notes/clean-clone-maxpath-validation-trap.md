# A "clean clone" on Windows can be missing 1,108 files, and `git clone` exits 0

**Found 2026-08-31** by a `u8floor` verifier, **reproduced and widened by the orchestrator.**
This is a defect in §C.7b's *method*, not in any one branch, so it can invalidate a validation
claim made by any agent in this effort.

## Reproduced

```
$ cd <deep scratchpad path>
$ git clone --quiet --branch refactor/ai-stack-cleanup --single-branch "D:/Open WebUI/ai-stack" clonetest
warning: Clone succeeded, but checkout failed.
You can inspect what was checked out with 'git status'
and retry with 'git restore --source=HEAD :/'
EXIT=0                      <-- note the exit code

$ git -C clonetest status --porcelain | wc -l
1108
```

**1,108 tracked files absent from the working tree, and the command reports success.** The
warning goes to stderr; `$LASTEXITCODE` / `$?` is **0**. Any script that gates on the exit code
sees a healthy clone.

Missing files by top directory: `documentation` 250, `scripts` 230, `agent-org` 207,
`little-coder` 99, `.claude` 78, `search-gateway` 33, `llm-queue` 33, `config` 29.
It is **partial, not total** — `scripts/checks/` still had 25 files present — which is what makes
it dangerous: a suite can run, and pass, against a tree missing a third of the repo.

## Cause

The longest tracked path is **144 characters**:

```
documentation/implementation-guide/teams-chat-agent-orchestration/Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md
```

`core.longpaths` is **unset** (neither repo nor system). 144 + a deep clone target exceeds
Windows MAX_PATH (260). The standard agent scratchpad
(`C:\Users\yamao\AppData\Local\Temp\claude\<project>\<uuid>\scratchpad\...`) is ~100 characters
before the repo path starts, so **cloning into the scratchpad is exactly the failing case** — and
the scratchpad is where agents are told to put temporary files.

## Why this matters to §C.7b

§C.7b says a result without isolation is not a result: one run, one checkout, a recorded sha.
That rule silently assumes **the checkout is complete**. A clone that drops 1,108 files and
returns 0 satisfies the letter of the rule and none of its purpose. A green from such a tree
means "nothing failed among the files that happened to be present."

**The practice partially self-corrected, by luck and by one agent noticing.** Verifiers who used
short targets (`D:/tmp-u8h1-adv`, `D:/tmp-u8h1-adv2`, `D:/av1`, `C:\av2`) were unaffected, and a
U4 verifier explicitly recorded choosing `C:\av2` because "a long scratchpad path hits a
pre-existing 'Filename too long'". That was one agent's alertness, not a guarantee.

## What to do

1. **Clone with the flag:** `git -c core.longpaths=true clone …`, or set `core.longpaths=true`
   in the clone before checkout. (A repo-local setting in the SOURCE repo does not help — the
   clone gets a fresh config.)
2. **Prefer a short target** (`D:\av1`) over the scratchpad for full-repo clones.
3. **Never trust the exit code alone.** After any clone used for validation, assert
   `git status --porcelain` is EMPTY before running a suite. A clone is not verified by its exit
   code; it is verified by its working tree matching its index.
4. Consider shortening the one 144-character path. It is a documentation file and the name is
   carrying a whole sentence.

## Open

Nobody has audited which validations in this effort were run from a scratchpad clone. That audit
is worth doing before any "validated at sha X" claim is treated as load-bearing, and it is
cheaper than re-running everything.
