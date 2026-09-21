# check-llm-gateway-routing.ps1 scans nothing from an agent worktree (2026-09-19)

**Status: FIXED 2026-09-20** on `work/sl-checks-worktree` (harness item `sl-checks-worktree`).
What was changed and how it was proved is at the bottom of this note; the sweep of every
other check for the same class is
`documentation/notes/stack-layers-sl-checks-worktree-findings.md`.

**Found by:** the sl-inference-split tester (harness item, stack-layers plan), while trying
to get a real green for the routing guard from `.claude/worktrees/wt-sl-inference-split`.

**Claim [read from source]:** `scripts/checks/check-llm-gateway-routing.ps1:77` carried
`'*\.claude\*'` in its prune/allow list. (This note said `:73` when it was written; the
line is 77 at `a2d3644`, re-derived 2026-09-20. A citation moves - re-derive it, do not
recheck it.) Harness worktrees live at
`.claude/worktrees/wt-<id>/` (`scripts/agent-harness/harness.config.json` -> `worktree.root`),
so every path under a worktree matched the prune pattern: the check examined zero files
and exited 0. The pre-commit hook in a worktree therefore reported the routing guard green
without having looked at anything.

**Observed live [2026-09-19, tester + reviewer briefs]:** planting
`api_base: http://llama-cpp-upstream:8080` in a worktree copy leaves the check green; the
same plant in a `git archive` export outside `.claude` goes red. Workaround used by the
reviewers of this plan: export the branch to a scratch directory outside `.claude` and run
the check there.

**Why it matters:** the merge into `development` is committed from the operator's main
checkout (not under `.claude`), so the merge-time hook is real. But a developer's own
pre-commit in a worktree is vacuous for this one check, and a `dark`-profile run that
trusts the worktree hook would never see a bypass. Compare the same shape in
`documentation/notes/wiki-pages-mirror-extractlinks-outage.md` (a check that passes while
checking nothing).

**Fix (not done here; own item):** the prune should exclude `.claude` *except* the
worktree root, or better, resolve the scan root through `git rev-parse --show-toplevel`
and prune only `.claude/` *relative to that root* (a worktree's toplevel is the worktree
itself, so its `.claude/` subtree is what to skip, not the path above it). Add a
self-test: the check refuses (non-zero) when it scanned zero candidate files, the way
`check-corpus-exposure-producers.ps1` already does.

## Same class, second instance (sl-colo-inference tester, 2026-09-19)

`scripts/checks/validate-lineendings.ps1` run inside a `git archive` export (no `.git`)
prints "No tracked shell scripts to check", SUCCESS, exit 0 - a pass with nothing checked.
It is only meaningful in a real checkout or worktree. A tester following a plan that says
"run it on the export" records a vacuous green. Same fix shape: a check that examined zero
candidates should refuse, not succeed. [observed live 2026-09-19; re-run in a real worktree
where it genuinely passes]

*(Unchanged 2026-09-20. `validate-lineendings.ps1` still exits 0 on an export with no
`.git`; the sl-checks-worktree sweep confirmed it selects the same 24 tracked `*.sh` files
from a worktree as from a scratch clone, so it is not worktree-sensitive and was left
alone. The vacuity-on-export defect is real and remains open - see the findings note.)*

## FIXED 2026-09-20 (harness item sl-checks-worktree)

**What changed in `scripts/checks/check-llm-gateway-routing.ps1`** - three edits, all about
WHERE it looks, none about what it flags (`$badPattern` and `$queueUpstreamAllow` are
untouched):

1. `Test-Allowed` now matches each `$allowPathLike` glob against the path RELATIVE to
   `$Root` (prefixed with a `\` so every existing glob keeps working - `-like` lets `*`
   match the empty string), never against the absolute path. That closes the whole class,
   not only `.claude`: `*\data\*`, `*\documentation\*` and the rest could equally have
   swallowed a tree whose ABSOLUTE path happened to contain that segment.
2. The `'*\.claude\*'` glob is gone from the allow list; `.claude` is now a TRAVERSAL prune
   in `$pruneDirNames`. The walk starts AT `$Root`, so a directory name can only be pruned
   when it sits inside the tree being scanned - from the main checkout that is
   `<repo>\.claude\` (which also stops it walking other sessions' worktrees), and from a
   worktree it is that worktree's own `.claude\`. The `.claude` a worktree hangs off is
   above the root and is not reachable by the walk at all.
3. The scan set is materialised and COUNTED. Both verdict lines print
   `N file(s) scanned under <root>`, and `N = 0` now FAILS: "Nothing was examined, so this
   run proves nothing about the tree." The whole defect was that nothing separated a clean
   tree from an unexamined one except a number nobody printed.
   (`check-corpus-exposure-producers.ps1` prints and asserts the same thing.)

**Proved [measured 2026-09-20 from `.claude/worktrees/wt-sl-checks-worktree`]:**

| run | verdict |
|---|---|
| branch script, root = the worktree | `OK ... 1009 file(s) scanned` |
| branch script, root = a scratch clone at a repo-root path (+ the 8 gitignored `.env` files the worktree carries) | `OK ... 1009 file(s) scanned` - the same number |
| base `a2d3644` script, root = the worktree | `OK - no LLM gateway bypasses found.` over 0 files |
| plant `OPENAI_API_BASE: http://llama-cpp-upstream:8080` in a staged `inference/compose/zz-planted-bypass.yml`, base script, root = the worktree | GREEN, exit 0 - the vacuous pass, reproduced |
| same plant, branch script, root = the worktree | `FAIL - 1 gateway bypass(es) found in 1010 file(s) scanned`, exit 1, naming `inference\compose\zz-planted-bypass.yml:5` |
| same plant copied into the scratch clone, base script | FAIL, exit 1 - so the defect was the PATH, never the pattern |
| branch script, `-Root` an empty directory | `FAIL - scanned 0 files`, exit 1 (base: `OK`, exit 0) |

The 1009-vs-1001 raw difference between the worktree and a fresh clone is the eight
gitignored `.env` files a provisioned worktree carries (`.env`, `agent-org/docker/.env`,
`coder/.env`, `frontend/.env`, `inference/.env`, `memory/.env`, `portal/.env`,
`search/.env`; `.env.test` matches no scan extension). Creating those eight as empty files
in the clone brings it to exactly 1009.
