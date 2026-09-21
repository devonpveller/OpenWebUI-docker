# sl-gate4-carries - findings

Carries from the `sl-recovery-backups` review (merged `f3eee64`, 2026-09-21).
All measurements below were taken 2026-09-22 on this host, Windows 11 Pro,
PowerShell 5.1, git 2.x, in a scratch clone at `C:\g4\a` made with
`git -c core.longpaths=true clone "D:\Open WebUI\ai-stack" C:\g4\a` and
`git checkout f3eee64`. That clone has no `core.hooksPath`, so nothing here was
masked or helped by the pre-commit chain.

---

## 1. The two reproductions AT BASE (f3eee64), before any change

Both are of gate 4 in `scripts/checks/check-project-configs.ps1`, the
control-character scan added by sl-recovery-backups.

### (a) A staged binary FAILS the commit, blaming a Python string

70-byte 1x1 PNG written with Python, staged. `.gitattributes` declares
`*.png binary`, and `git check-attr binary -- docs-test.png` answers
`binary: set`. Running the check:

```
$ powershell -NoProfile -ExecutionPolicy Bypass -File C:\g4\a\scripts\checks\check-project-configs.ps1
  [configs] CONTROL CHARACTER in staged added line(s):
             docs-test.png : 0x1A in an added line -> <CTRL>
             docs-test.png : 0x00 in an added line -> <CTRL><CTRL><CTRL>
             Tab/LF/CR are fine; nothing else below 0x20 is.
             Usual cause: a backslash escape in a NON-RAW replacement string
             (\b -> 0x08, \a -> 0x07, \f -> 0x0C). Use rb'' / r'' literals.
EXIT = 1
```

The cause is `--text` on gate 4's diff: it forces git to emit a binary blob's
bytes as diff lines, and a PNG's header bytes (`0x1A`, `0x00`) are of course in
the class. Nobody can act on that advice - there is no Python string, and the
file is not text.

### (b) A rename-with-edit that injects 0x08 passes GREEN

`git mv documentation/notes/agent-memory-policy-findings.md
documentation/notes/renamed-policy-findings.md`, then one appended line
containing a literal `0x08`, then `git add`. `git diff --cached --name-status`
says `R099 <old> <new>`. Running the check:

```
  [configs] nothing staged - skip
EXIT = 0
```

It is WORSE than "gate 4 missed it": `--diff-filter=ACM` appears twice in the
file, and the copy at the top (`$staged`, line 28 at base) fed the early exit -
so with a rename as the only staged change the ENTIRE check, all four gates,
exited 0 on "nothing staged". The byte really was there:

```
$ git diff --cached -U0 --text --no-renames --diff-filter=ACM | <grep for 0x08>
b'+A planted line with a backspace\x08byte.'
$ git diff --cached -U0 --text --diff-filter=ACM           # what the gate ran
0 bytes
```

---

## 2. Design choices, and why

### `--no-renames` was REJECTED in favour of no `--diff-filter` at all

Both spellings make the planted byte visible. The difference is what "added
line" then means:

* `--no-renames` respells a move as delete+add, so EVERY line of the moved file
  becomes an added line. It would report lines the commit did not author, and a
  PURE rename of any file carrying one of the eleven pre-existing control bytes
  already in `documentation/` (out of scope for this item by the anchor, and
  still there) would go red for a move that introduced nothing.
* Leaving rename detection at git's default `-M` and simply DROPPING the
  `--diff-filter` keeps the rename as an `R` entry whose hunk holds exactly the
  lines the commit introduced.

  Measured on the same index 2026-09-22, `git diff --cached -U0 --text | grep -c
  '^+'`: **3** as shipped (the `+++` header, the appended blank line, the planted
  line) against **123** with `--no-renames` (a `+++ /dev/null` for the delete
  half, a `+++ b/` for the add half, and all 121 lines of the moved file). The
  source file is 119 lines, re-derived.

  Be precise about what that buys, because the obvious claim is wrong: for THIS
  plant the reported HIT count is 1 either way, since the other 120 lines are
  clean. The difference bites where a moved file ALREADY carries a control byte,
  and that case was measured too: a pure `git mv` of
  `documentation/notes/u8floor-findings.md` - which has a `0x08` at line 100 -
  gives `R100` on `--name-status` and **exit 0**. Under `--no-renames` all 121+
  of its lines would be added lines and the move would go red for a commit that
  introduced nothing.

Dropping the filter entirely, rather than widening it to `ACMR`, is the same
lesson gate 1 in this file already writes down twice: a filter is a list of
change classes somebody has to remember to extend, and the one it forgets is
the one that goes unverified in silence. `D` arrives too and costs nothing - a
deletion has no added lines.

### The binary test is `.gitattributes`, NOT a NUL-byte probe

Measured on this tree (1198 tracked files, 2026-09-22):

| probe | files it matches today |
|---|---|
| `git check-attr binary` = set | **0** |
| NUL byte in the first 8000 bytes | **0** |

So neither probe changes anything about what is committed here now; the choice
is entirely about what a future commit stages. The attribute wins on two
grounds:

1. **A content probe disarms the gate on its worst input.** `0x00` is inside
   gate 4's own class. "Skip any file containing a NUL" means: inject a NUL into
   a text file and the gate that exists to catch injected control bytes falls
   silent about it. An attribute cannot be set by the bytes under inspection.
2. **`.gitattributes` is a declaration somebody made on purpose**, and it shows
   up in the diff when it changes. The skip is reviewable; a content heuristic
   is not.

The cost is real and is stated rather than hidden: `.gitattributes` currently
covers six extensions (`*.png *.jpg *.jpeg *.gif *.ico *.db`), so a staged
`.pdf`, `.woff2`, `.wasm` or `.zip` still reaches the scan and still fails. The
failure text now names that case and its one-line fix ("declare the type in
.gitattributes ... and the next run skips it BY NAME on a printed line")
instead of blaming a non-raw Python string. `--text` was KEPT for the same
reason the skip is printed: git's own binary heuristic would summarise such a
file away silently, and a silent narrowing is what this whole file is written
against.

### `git check-attr` is fed from a TEMP FILE via `cmd`, not a PowerShell pipeline

Two measurements forced this:

* `git check-attr binary -- <path>...` with a whole-tree stage dies:
  `WinError 206, The filename or extension is too long` at 1198 paths. So
  `--stdin`.
* But PS 5.1 terminates every line it writes to a native command's stdin with
  **CRLF**, and `git check-attr --stdin` takes the trailing CR as part of the
  path. The pipeline form answered, verbatim:
  `"docs-test.png\r": binary: unspecified` - for a file `.gitattributes` marks
  binary. That is a skip that silently never happens, i.e. the first version of
  the fix was itself a check that passed while checking nothing. Writing the
  paths to a temp file with LF and redirecting it with
  `cmd /c "git check-attr --stdin binary < ""$tmp"""` answers `binary: set`.

### Hits now carry a line number

The `@@ -a,b +c,d @@` header of a `-U0` hunk gives the first NEW-file line
number, so a hit prints `path:line` rather than `path`. Base printed only the
path.

---

## 3. OPEN, deliberately: gates 1-3 still cannot see a rename

Fixed here: the EARLY EXIT no longer uses the rename-blind list (a new
`$stagedAny`, from `git diff --cached --name-only` with no filter), because a
check that exits 0 saying "nothing staged" when something is staged is a lie
regardless of which gate would have caught it.

NOT fixed here, because the anchor puts "any change to what the other gates
enforce" out of scope:

* `$staged` (still `--diff-filter=ACM`) feeds gates 1, 1b, 2 and 3. So
  `git mv old.ps1 new.ps1` plus an edit that breaks its syntax is NOT parsed by
  gate 2; the same move on a `*.yml` does NOT trigger the compose render or
  `stack.py inventory --check`; a renamed `*.json` is not strict-parsed.
* MEASURED, not inferred, on this branch's tip 2026-09-22:
  `git mv scripts/checks/dev-helper.ps1 scripts/checks/dev-helper-moved.ps1`,
  append an unclosed `function Broken {` to the moved file, `git add -A`.
  `git diff --cached --name-status` says `R099`. The check exits **0** and
  prints one line, gate 4's `no control characters in staged added lines` -
  gate 2 never saw the file, and a `.ps1` that cannot be parsed would have been
  committed. (Gate 4 itself is correct here: the rename's hunk really does hold
  no control bytes.)

The fix is one word (`ACMR`, or dropping the filter as gate 4 did) but it
WIDENS what three gates enforce on commit shapes nobody has measured, which is
a change that deserves its own red/green rather than a ride on this one.

## 4. OPEN: `backup-conventions.md` now links a compose file with no services

The two hrefs fixed under item (3) of this work (`:92` and `:130`) resolve
after the fix - they point at the repo-root `docker-compose.yml` and
`.env.example`. The paths are right; the GUIDANCE around them may no longer be.
Since Part K the root compose file is a pure network anchor with **0 services**,
and since sl-env-split every plane owns its own `.env`. So "copy this backup
service block into `docker-compose.yml`" and "add `<SERVICE>_BACKUP_CRON=` to
`.env.example`" now name the two files where a new backup sidecar should
probably NOT go. Fixing the href was in scope for this item; rewriting the
runbook's pattern template to be plane-aware was not, and it is a real piece of
work - `documentation/runbooks/SERVICE-LIFECYCLE.md` is the checklist it would
have to agree with.

---

## 5. Timing, and what the whole-tree stage actually proves

Two different "whole tree" measurements, both taken in this worktree
2026-09-22 with `Measure-Command`-equivalent stopwatches.

**(a) Against the branch base `f3eee64` (`git reset --soft f3eee64; git add -A`)**
- this is the commit shape, and the shape the pre-commit chain sees. Result is in
the test plan, case 4: **exit 0**.

**(b) Against the repository ROOT commit `35511a3`, i.e. every tracked file's
every line staged as added** - 1201 paths, 337,737 diff lines:

| stage | seconds |
|---|---|
| `git diff --cached -U0 --text` (capture) | 2.73 |
| `git check-attr --stdin binary` (1194 paths, one call) | 1.83 |
| the added-line scan itself | 4.65 |
| **gate 4 total** | **9.21** |
| the WHOLE check (9 compose renders + `stack.py inventory --check` + 93 .ps1 parses + 88 .json parses + gate 4) | **44.2** |

Gate 4 is ~21% of the worst case and the compose renders are the rest, so the
gate is not what anybody will notice.

That run exits **1**, and correctly so: it reports 9 control bytes in 8 files
(`documentation/evidence/podlinks/test-plan.md` x3,
`documentation/evidence/sl-env-split/test-plan.md`,
`documentation/implementation-guide/dark-factory-unification/DECISIONS.md` x2,
`documentation/notes/stack-layers-sl-env-split-findings.md`,
`documentation/notes/u8floor-findings.md`, `documentation/notes/u8h4-findings.md`)
- the pre-existing bytes sl-recovery-backups measured and this item's anchor puts
out of scope. Staging every line of the repository as ADDED is precisely the
condition under which "added lines only" stops protecting them, so exit 1 there
is the gate working, not a defect. It is recorded because it is the trap the
next person will fall into: **the whole-tree exit-0 claim is against the branch
base, not against the root commit.**
