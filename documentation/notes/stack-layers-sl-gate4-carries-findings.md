# sl-gate4-carries - findings

Carries from the `sl-recovery-backups` review (merged `f3eee64`, 2026-09-21).
Measurements below were taken 2026-09-20 (round 1) and 2026-09-21 (round 2) on this host, Windows 11 Pro,
PowerShell 5.1, git 2.x, in a scratch clone at `C:\g4\a` made with
`git -c core.longpaths=true clone "D:\Open WebUI\ai-stack" C:\g4\a` and
`git checkout f3eee64`. That clone has no `core.hooksPath`, so nothing here was
masked or helped by the pre-commit chain.

---

## 1. The two reproductions AT BASE (f3eee64), before any change

Both are of gate 4 in `scripts/checks/check-project-configs.ps1`, the
control-character scan added by sl-recovery-backups.

### (a) A staged binary FAILS the commit, blaming a Python string

70-byte 1x1 PNG written with Python, staged. (The anchor calls it a 69-byte PNG;
the base64 blob both rounds and the tester used decodes to **70** bytes -
`len(base64.b64decode(...))`, re-derived 2026-09-21. Cosmetic drift in the
anchor text, recorded so the next reader does not chase the difference.) `.gitattributes` declares
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

  Measured on the same index 2026-09-20, `git diff --cached -U0 --text | grep -c
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

Measured on this tree - 1198 tracked files at `f3eee64` (2026-09-20), 1201 at
this branch tip (re-derived 2026-09-21), same answer both times:

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

### File identity comes from `--name-status -z`, never from the `+++` header (ROUND 2)

This is the thing round 1 got wrong and round 2 fixes; the measurements are
in section 6. The gate now asks three separate questions and answers none of
them by reading a human-readable rendering:

| question | how |
|---|---|
| which paths are staged | `git diff --cached -M --name-status -z` - NUL-separated, never C-quoted, never tab-terminated, and an `R`/`C` entry carries both halves so the destination is unambiguous |
| which of them are binary | `git check-attr -z --stdin binary`, fed those exact bytes |
| which lines were added | ONE patch diff, cut into records at `diff --git ` BY POSITION and paired with the list above by index; a record’s header ends at its first `@@`, so a `+` line after that is DATA even when it reads like a header |

Two properties make this sound rather than merely different:

* **A bare `diff --git ` line cannot be content.** Inside a hunk every line
  carries a `+`, `-`, space or `\` prefix, so an added line whose text is
  `diff --git a/x b/y` arrives as `+diff --git a/x b/y`. The record boundary
  is unforgeable, which is what makes positional pairing safe.
* **The pairing is ASSERTED, not assumed.** `diff --git` record count against
  `--name-status` entry count; a mismatch prints
  `CONTROL-CHARACTER SCAN CANNOT ATTRIBUTE ITS DIFF` and FAILS, rather than
  labelling every path after the divergence with the wrong name. Measured
  equal (1202 = 1202) on the whole-repository stage.

Bytes are carried as ISO-8859-1 strings end to end - that mapping is
byte <-> char and lossless, so no decoder can swallow the 0x08 the gate is
looking for, and a path written back out to `check-attr` is the exact bytes
git produced. Paths are re-decoded as UTF-8 only to PRINT them: on this
CP437 console `café.png` and `naïve.txt` print as their real names.

### `git check-attr` is fed from a TEMP FILE via `cmd`, not a PowerShell pipeline

Three measurements forced this:

* `git check-attr binary -- <path>...` with a whole-tree stage dies:
  `WinError 206, The filename or extension is too long` at 1198 paths. So
  `--stdin`.
* But PS 5.1 terminates every line it writes to a native command’s stdin
  with **CRLF**, and `git check-attr --stdin` takes the trailing CR as part
  of the path. The pipeline form answered, verbatim:
  `"docs-test.png\\r": binary: unspecified` - for a file `.gitattributes`
  marks binary. That is a skip that silently never happens, i.e. the first
  version of the fix was itself a check that passed while checking nothing.
* The tester then measured that it is WORSE than that: PS 5.1 writes a
  **UTF-8 BOM** into that stdin as well, so the first path of the batch came
  back as `"\\357\\273\\277foo.png\\r"`. Two separate corruptions, at the
  front and the back of the same string - which is the whole argument
  against letting the shell touch the bytes.

So the paths are written to a temp file with `[System.IO.File]::WriteAllBytes`
and `cmd` does the redirect. Round 2 added `-z` to BOTH sides of the call
(`git check-attr -z --stdin binary`): NUL-separated paths in, NUL-separated
`path, attribute, value` triples out, so no line-ending convention and no
quoting rule sits between git and git.

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
* MEASURED, not inferred, on this branch's tip 2026-09-20:
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

Two different "whole tree" measurements, both taken in this worktree with
`Measure-Command`-equivalent stopwatches - (a) 2026-09-20, re-run 2026-09-21,
(b) round 1 on 2026-09-20 and round 2 on 2026-09-21.

**(a) Against the branch base `f3eee64` (`git reset --soft f3eee64; git add -A`)**
- this is the commit shape, and the shape the pre-commit chain sees. Result is in
the test plan, case 4: **exit 0**.

**(b) Against the repository ROOT commit `35511a3`, i.e. every tracked file's
every line staged as added.**

Round 2's four stages, and round 1's three replayed on the SAME index
immediately afterwards (1202 paths, 338,373 diff lines):

| stage | round 2 | round 1 |
|---|---|---|
| `git diff --cached -M --name-status -z` | 0.25 | - |
| `git check-attr` (one call) | 0.11 | 1.74 |
| patch diff capture | 0.58 | 2.37 |
| the added-line scan itself | 1.67 | 3.52 |
| **gate 4 total** | **2.61** | **7.63** |

Round 2 is **~3x faster**, and the redesign is why, rather than an
optimisation being smuggled in: round 1 captured git's output through a
PowerShell pipeline (`@(& git diff ...)`), which re-encodes every line
through the console codepage and grows the array one object at a time.
Round 2 lets `cmd` redirect into a temp file and reads it with
`ReadAllBytes` + one `Split`. That same change is what makes the bytes
trustworthy, so the speed is a side effect of the correctness fix and not a
trade against it.

Whole check on that stage: **28.6 s** - gate 4 is ~9% of it and the nine
`docker compose config` renders are very nearly all the rest.

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

---

## 6. ROUND 2 - the header parser was three defects, two of them regressions

Attempt 1 (`f50a80b`) shipped a gate that read the path out of the diff’s
`+++ b/<path>` line with a `Substring(6)`. Every case in its own test plan
passed; the tester broke it on input SHAPE. Re-measured here 2026-09-21, one
index per shape, the ONLY variable being which version of
`check-project-configs.ps1` sits in the tree - base `f3eee64` (v0), attempt 1
`f50a80b` (v1), this attempt (v2):

| staged shape | v0 (base) | v1 (attempt 1) | v2 (this) |
|---|---|---|---|
| `café.png` (binary attr set) + `naïve.txt` carrying `0x08` | exit 1, 3 hits, **filenames printed blank** | **exit 0, 0 hits** | exit 1, 1 hit `naïve.txt:2`; PNG skipped by name |
| `a file with spaces.png` (attr set) + `spaced text file.txt` carrying `0x08` | exit 1, 3 hits, **labels carry a stray TAB** | exit 1, 3 hits, same stray TAB | exit 1, 1 hit `spaced text file.txt:2`; PNG skipped by name |
| `evade1.txt` (`++ /dev/null`) + `evade2.txt` (`++ b/docs-test.png`), each carrying `0x08`, `docs-test.png` staged alongside | exit 1, 4 hits (evade2’s **misattributed** to `docs-test.png`) | **exit 0, 0 hits** | exit 1, 2 hits, each on its own file |

The two **exit 0** cells are the finding. A control byte the gate’s own base
reported, attempt 1 passed green with no printed skip - the exact silence
this item exists to abolish, reintroduced by the fix for it. The mechanism
was the same both times: anything the `+++` pattern failed to recognise set
the path to empty and LATCHED the skip flag on for the rest of that file.

The third row is not a regression, but it is the headline defect surviving:
git TAB-terminates `+++ b/<path>` when the path contains a space, so
`check-attr` was asked about `<path>\t`, answered `unspecified`, and a staged
PNG named `a file with spaces.png` still failed pre-commit with the
raw-string advice. Same failure as the CRLF one in section 2, arriving from
git's header format instead of from PowerShell's stdin.

### What round 2 changed, and what it deliberately did not

Changed: file identity (section 2's new first subsection), `-z` on
`check-attr`, and a 120-character cap on the printed preview. The cap is new
and is a consequence of the lossless decoding: a binary file git was not told
is binary renders as ONE line under `--text`, so the 70-byte PNG previewed in
full and a 5 MB font would have printed 5 MB. The `file:line` is the
actionable part; the preview is orientation.

Unchanged, all re-measured on this attempt: the class
(`[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]`, still 2 occurrences, byte-identical to
base), `--text`, `-M` with no `--diff-filter`, the attribute-only binary test
and its printed skip line, and the rename-aware early exit. Every case of
attempt 1's plan re-runs to the same output, and `core.quotepath=false`
produces results identical to the default `true` - the `-z` interfaces have
no quoting mode, which is the point of using them.

### Still OPEN after round 2

Sections 3 and 4 stand unchanged: gates 1-3 still take the
`--diff-filter=ACM` list and still cannot see a rename, and
`backup-conventions.md`'s pattern template still points at a compose file
with no services. Neither was touched.
