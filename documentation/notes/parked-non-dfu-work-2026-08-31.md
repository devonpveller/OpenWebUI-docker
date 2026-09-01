# PARKED: non-DFU work, removed from the worktree set on 2026-08-31

**Operator scope ruling, 2026-08-31:** work that `dfu-done.ps1` will never gate on does not
hold a branch or a worktree open while the DFU plan is finishing. Findings are recorded here;
the work itself is bundled so nothing is destroyed. **These notes are the backlog for after
the plan closes.** Keeping the worktree count trending toward zero is a real completion
signal; churning sideways on out-of-scope work hides it.

Bundles live in `D:\Open WebUI\_notes\parked-work\` (outside the repo — they are multi-MB).
Every bundle below was verified `git bundle verify` -> *"records a complete history"* BEFORE
the branch or worktree it came from was removed. Recover with `git bundle unbundle`.

---

## 1. `work/curatorpool` — deep_research silent-failure fix + research-curator pool

**Removed:** ai-stack branch `work/curatorpool` and worktree `wt-curatorpool`.
**Bundles:** `aistack-curatorpool.bundle` (ai-stack, commit `f71772b`) and
`OB1-curatorpool.bundle` (OB1, commit `22f41b6`, tagged `parked/curatorpool` inside it).

**Why both were needed, and it is the reason to check rather than assume:** `f71772b` bumps
the OB1 gitlink to `22f41b6`, and that commit existed **only inside the curatorpool
worktree's own OB1 checkout** — `git -C OB1 cat-file` in the main checkout answers
`no such commit`. Bundling the ai-stack side alone would have parked a pointer to nothing.

**What it contains:**

| file | change |
|---|---|
| `owui/tools/deep_research.py` | +19 lines — show the findings when *filing* them failed, instead of losing them |
| `OB1` (gitlink) | -> `22f41b6` *"research-curator: a pool that survives its connections dying, and a loss that cannot be silent"* |

**Status:** unmerged, unpushed, unvalidated. Its own commit message cites MERGE-PROTOCOL
line 333 — an unreachable gitlink breaks every fresh `--recurse-submodules` clone — so if
this is ever landed, **push OB1 `22f41b6` first**, then bump.

**Theme worth keeping even if the code is not:** both halves are the same defect class this
workspace keeps finding — *a failure that produces no visible signal*. The deep_research
change exists because findings were lost silently when filing failed; the OB1 change exists
because a dying connection pool lost work silently. Compare the wiki outage below and
§16's "fail-closed is not fail-visibly".

---

## 2. `work/wikilinks` — the wiki_pages silent write outage

**Removed:** ai-stack branch `work/wikilinks` and worktree `wt-wikilinks`. The note it
carried was cherry-picked onto the work line first and is the surviving record:
[wiki-pages-extractlinks-outage-2026-08-31.md](wiki-pages-extractlinks-outage-2026-08-31.md).

**Bundle:** `OB1-wiki-pages-extractlinks-fix.bundle` (OB1 `9b47135` on
`fix/wiki-pages-extractlinks-binding`). That commit also existed only inside the removed
worktree's OB1 checkout.

**The outage is REAL, LIVE and UNFIXED in production.** Confirmed first-hand in the running
container, not relayed:

```
docker exec openbrain-wiki node -e "import('/recipes/_shared/wiki-pages.mjs')..."
  -> ReferenceError: extractLinks is not defined
```

`wiki_pages` writes by day: 08-26: 16 · 08-27: **39,602** · 08-28: **8,285** · 08-29: **0** ·
08-30: **0** · 08-31: **2**. The collapse lands on OB1 `dfc6228` (2026-08-28 12:37 UTC),
which split `extractLinks` into `links.mjs` and left `export { extractLinks } from …` — a
re-export that binds nothing in the module's own scope, while `parseWikiPage` calls it twelve
lines later. `queueWikiPage`'s bare `catch {}` swallowed the ReferenceError, so compilers
kept reporting `compile ok … 26 regenerated, 0 failed` while queueing zero rows.

**How it was found, which is the part worth remembering:** not by monitoring. An agent tried
to close a drill vacuity (`VACUOUS-WIKIPAGES`) and the fixture *refused* to go green. The
vacuous assertion was reporting a genuinely dead write path. An earlier round had dismissed
the same signal as "needs a fixture" — a guess wearing the clothes of a diagnosis.
`node --test recipes/_shared/wiki-pages.test.mjs` was **5 of 10 red since the day it
shipped**; the suite already caught it and nobody ran it.

**Not done, deliberately:** no backfill. ~59,213 pages walk clean against the fixed module,
and running that against production is a deploy decision, not a fix.


---

## 3. `work/gitreach` — the gitlink reachability gate (PARKED under the C.10 scope freeze)

**Removed:** branch `work/gitreach` and worktree `wt-gitreach`.
**Bundle:** `aistack-gitreach.bundle` (10 commits, tip `35fa7be`), verified complete first.

**This is NOT polish, and it is not superseded — it is out of SCOPE.** §C.10 freezes U8 as the
final phase and admits no check beyond what an H-item's *Validated by* names. `gitreach` is not
an H-item, so it parks. Recording the distinction because "abandon if it is only polishing"
does not describe it, and filing it as polish would be a false reason.

**What it builds** (`+1,206` lines across four hook files): `.githooks/pre-push` and
`.githooks/reference-transaction` are NEW, and `commit-msg` gains 249 lines. The capability is
refusing a gitlink pinned at a commit the submodule remote does not have — plus gating the
**ref update**, not just the commit, because a rebase can land an unreachable pin.

**Its value is not theoretical — this exact failure occurred twice on 2026-08-31:**
- `work/u8h3` bumped the gitlink to OB1 `e9be2cd`, which was on no remote; the merge was
  blocked until the orchestrator pushed it.
- `work/u5rls` pinned OB1 `8ccf0e6`, also on no remote.
- And `work/curatorpool` (parked above) pins OB1 `22f41b6`, which exists in **no** checkout but
  the one that was deleted — caught only because the bundle was checked before removal.

The work line already carries a *basic* gitlink mention in `commit-msg`; this branch is the
enforcing version. It is 99 commits behind and therefore UNVALIDATED under §C.7b regardless of
what passed on it.

**Its own history is worth reading before it is resumed** — the round titles record the method
failing in the way this effort keeps recording: *"stop modelling the invariant — run the clone"*,
*"the proof had a modelling step it did not name"*, *"the fix for 'gated nothing' gated nothing"*,
*"retract two claims that were wider than the code"*.

## 4. `work/u8v2` — a verifier's branch, killed as a duplicate

**Removed:** branch `work/u8v2` and worktree `wt-u8v2`. **Bundle:** `aistack-u8v2.bundle`.

§C.10: *"ONE BRANCH PER H-ITEM. Two worktrees on one commit is thrash, not parallelism."* This
held H3's **pre-rebase** round-4 state at `90c7cde`. `git merge-base --is-ancestor` reports it is
NOT contained in `work/u8h3`'s tip — but only because the rebase rewrote the shas, not because
the content is unique. Bundled rather than assumed, because "almost certainly duplicated" is not
grounds for discarding commits.

(`u8h2w` and `v1h2`, also named in §C.10, no longer existed — their verifiers removed their own
scratch worktrees.)
