# Item R — "the drill mutates the tree it audits": measured, and it does not

**Subject:** `scripts/checks/verify-dfu-done.ps1`
**Date:** 2026-09-02 · **Base sha:** `4268b08` (`work/dfux0r`, cut from `refactor/ai-stack-cleanup`)
**Verdict:** the premise is FALSE. The drill is read-only against the tree it audits. The
false accusation is `dfu-done.ps1`'s integrity ATTRIBUTION, which item R does not own.
**Change shipped to `verify-dfu-done.ps1`: none.** Nothing in it needed changing.

---

## 1. What was claimed

The item states that `verify-dfu-done.ps1` — U8's own `How to run` marker — MOVES the
audited tree, reported as `git:refs, git:worktrees`, so clause 5's U8 subject cannot be
accounted and the board goes `UNACCOUNTED`.

The item also names the alternative up front: `Get-AuditedFingerprint`
(`scripts/checks/dfu-done.ps1:1565-1590`) reads **repo-wide** `git for-each-ref` and
`git worktree list`, so a CONCURRENT session in a sibling worktree trips it and an innocent
command is blamed. This is the case.

## 2. How it was measured

Not by reading the message. By an external before/after witness on the audited tree, in a
repository with **no siblings**, so no third party could move anything during the window.

- **Clone convention used: §C.7b's bare `--single-branch`** (the shape `dfu-done.ps1`
  itself builds for clauses 1 and 5, per `WALKTHROUGH.md:22-23`), *not* CLAUDE.md's
  `--recurse-submodules`. The two conventions disagree; this one was chosen because it is
  the shape the authority runs the marker in. Consequence: `OB1/` is uninitialised in the
  clone. That is not a confound — the drill ran GREEN with 216 assertions against an empty
  `OB1/`, which is itself the proof that it never reads it.

  ```
  git -c core.longpaths=true clone --single-branch --branch work/dfux0r <worktree> <temp>\clone
  git -C <temp>\clone config core.longpaths true
  ```

  `git status --porcelain` asserted EMPTY before AND after. Clone sha `4268b08`.

- **Transcripts and witness files were written OUTSIDE the clone**
  (`%TEMP%\dfux0r-witness\`), so the measurement could not turn its own clean-repo probe
  red on its own output — the trap the item names.

- The witness records seven probes, a superset of `Get-AuditedFingerprint`'s git set:
  `for-each-ref refs/heads refs/tags`, `status --porcelain`, `worktree list --porcelain`,
  `submodule status --recursive`, plus `stash list`, an unrestricted `for-each-ref`, and a
  full recursive file listing (path + length) of the working tree excluding `.git`.

- The marker was then run exactly as `WALKTHROUGH.md:885` records it:
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/verify-dfu-done.ps1 -Target scripts/checks/dfu-done.ps1`

## 3. Result — zero delta

Run: exit 0, `DRILL GREEN - 216 assertions, 0 failed. 8 of 8 declared clauses have a
constructed failing case.` Wall clock **13.6 minutes**.

| probe | before to after |
|---|---|
| `git:refs` (heads + tags, 12 refs) | **IDENTICAL** |
| `git:status` (porcelain) | **IDENTICAL** (empty both ends) |
| `git:worktrees` | **IDENTICAL** |
| `git:submodule` | **IDENTICAL** |
| `git:stash` | **IDENTICAL** |
| all refs, unrestricted | **IDENTICAL** |
| full file listing, 1121 files | **IDENTICAL** |

SHA-256 of every witness file matched. Scratch directories in `%TEMP%` matching `dfu-*`:
**55 before, 55 after** — the run left nothing behind either.

**The witness is not blind — positive control.** After the measurement, the clone was
deliberately given a branch (`control/witness-proof`), a worktree, and an untracked file.
The same witness reported `DELTA DETECTED` on `git_refs`, `git_worktrees`, `git_status` and
`files`. Those artifacts were then removed and the witness re-run: IDENTICAL to `before`,
porcelain 0 lines. Without this control, "zero delta" would be indistinguishable from an
instrument that measures nothing — this effort's own recurring class.

**Why there was nothing to fix.** The remedy the item prescribes is already implemented.
Every fixture the drill builds is rooted in `[System.IO.Path]::GetTempPath()`
(`New-Scratch`, `verify-dfu-done.ps1:78-82`; 54 `New-FixtureRepo` + 4 `New-Scratch` call
sites, no other root), released by `Remove-Scratch` in 62 `finally` blocks, and every one of
the 60+ `Invoke-Target` call sites passes `RepoRoot = $fx.root` — a fixture, never the real
repository. `dfu-done.ps1:2012-2030` records that the same defect was already found and
fixed once in `New-CleanCheckout`, by replacing `git worktree add` with a clone.

**Red-prove — the probe still catches a real mutation.** Not added; it already exists and it
passed in this run. Step V2 (`verify-dfu-done.ps1:1988-2049`) points a walkthrough command
at `git -C <fixture> branch work/drill-injected`, asserts as a positive control that the
branch really landed, and then asserts that the containment probe reports `fail` naming
`git:refs`, that the command probe reports `CHANGED THE AUDITED TREE`, and that the run's
integrity record is false. All of those are inside the 216 green assertions. The probe is
working; it is the blame line that is wrong.

## 4. The mechanism of the false accusation, confirmed

`Get-AuditedFingerprint` is taken immediately before and after each executed command
(`dfu-done.ps1:1949-1963`), and `git:refs` / `git:worktrees` are properties of the whole
repository, not of the command. Any sibling worktree moving during that window is attributed
to whatever command held it.

Two facts make this the explanation here rather than a hypothesis:

- **The window is the largest in the walkthrough.** 13.6 minutes for this marker, against
  seconds for every other command. Exposure scales with the window, so this marker is the
  most probable scapegoat in the document — which is exactly what was observed.
- **The neighbours are real and concurrent.** At the time of writing the audited repository
  carried `work/dfux0r`, `work/dfux0s` and `work/pod-key`, with `wt-dfux0r` and `wt-dfux0s`
  created within the same minute (`2026-09-02 08:38:08`). Two agents were running in
  parallel by design.

This is the second recorded instance of the same reporting defect.
`documentation/notes/u8floor-findings.md:229-286` records the first, with the same control
(clean clone: `INTEGRITY: the audited tree is byte-identical`; shared worktree, same sha,
minutes later: `git:refs, git:worktrees` blamed on a `pytest` invocation) and a proposed fix
that was correctly declared out of scope there too.

## 5. What would close it

### 5a. The reporting fix — `dfu-done.ps1`, not this item's file

Split the fingerprint into a `self` class (the three documents, `documentation/notes`, this
checkout's own `git status --porcelain` and `HEAD`) and a `repo` class (`git:refs`,
`git:worktrees`, `git:submodule`). Both keep the veto — nothing is relaxed. The split is
what lets the per-command line stop asserting causation it cannot show: for a `repo`-class
key, say the repository-wide refs moved during this command's window and that a concurrent
worktree can do that without this run touching anything. This is finding 6 of
`u8floor-findings.md`, unchanged, and it is still not implemented.

**Do not implement it as an exclusion by path.** `dfu-done.ps1:2018-2026` already records
why: excluding a scratch checkout by name was "the same defect with a filter in front of
it". Attribution, not leniency.

### 5b. The operational unblock, and the trap in it

Running the final board from a clean clone removes the false accusation — that is the
control in §3 and in `u8floor-findings.md`. **But it silently guts clause 4**, and this was
measured, not reasoned:

```
work/* branches in the audited repo:                work/dfux0r, work/dfux0s, work/pod-key
work/* branches in a --single-branch clone of it:   <none - population is EMPTY>
worktrees in that clone:                            1 (the main checkout)
```

With an empty population, `dfu-done.ps1:4290-4311` takes the `$unmerged.Count -eq 0` branch
and `no-unmerged-work-branches` returns **pass** — "0 branch(es) measured" — while three work
branches are outstanding. `no-worktrees` (`:4319-4331`) counts `Select-Object -Skip 1` on a
one-entry list and also returns **pass**, while two worktrees are live. The note strings do
say `0 branch(es) measured`, so the text is honest; the *verdict* is green, and the board
rolls up verdicts.

**So the tension is structural, and it is the real reason 5a is not optional polish.**
Clause 4's worktree and work-branch subjects can only be measured in the shared checkout —
a clone can never contain the siblings. That is precisely the environment in which the
integrity veto false-fires. As things stand the board can have a true clause 4 or a quiet
integrity record, never both. Fixing the attribution is what makes both true at once.

**If the board is run from a clone anyway** (as an interim), clause 4's two probes must be
read as UNEVALUATED regardless of the green they print, and that has to be stated on the
board rather than left for a reader to notice.

## 6. Residual, not defects

- Nine `dfu-drill-*` directories were already in `%TEMP%` before this run, from earlier
  interrupted runs (a kill before `finally`). This run added none. Outside the audited tree,
  so no clause is affected; a `%TEMP%` sweep is housekeeping.
- `verify-dfu-done.ps1:2458`, `:2489` and `:2535` call `Invoke-Target` with **no `RepoRoot`**,
  so under `-Live` the target runs against the real repository rather than a fixture. The
  walkthrough marker does not pass `-Live`, so it is not this accusation's cause, and it was
  not measured here. Anyone adding `-Live` to the marker should expect the whole class of
  effects `-SkipLive` currently avoids.
