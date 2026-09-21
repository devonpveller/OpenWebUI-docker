# Test plan - `sl-gate4-carries`

Branch `work/sl-gate4-carries`, based on `development` at `f3eee64`.
Executed by a tester who did not write the change.

Changed files:

| file | what changed |
|---|---|
| `scripts/checks/check-project-configs.ps1` | gate 4 (control characters in added lines) + the top-of-file early exit |
| `scripts/checks/check-backup-coverage.ps1` | the `wiki-viewer-srv` exclusion Reason and the comment above it |
| `documentation/runbooks/backup-conventions.md` | two relative hrefs (`:92`, `:130`) |
| `documentation/notes/stack-layers-sl-recovery-backups-findings.md` | the href-sweep sentence and two inventory rows |
| `documentation/notes/stack-layers-sl-gate4-carries-findings.md` | NEW - findings sink |
| `documentation/evidence/sl-gate4-carries/href-sweep.py` | NEW - the unbounded href sweep |
| `documentation/evidence/sl-gate4-carries/test-plan.md` | NEW - this file |

---

## ROUND 2 - what changed since attempt 1 failed

Attempt 1 (`f50a80b`) failed testing. Its 9 cases all passed and 38 of its 39
claims re-measured true; it failed on input SHAPE. The gate read the file's
identity out of the diff's `+++ b/<path>` text, which git renders three ways this
plan never tried: C-QUOTED when the path is non-ASCII (`core.quotepath` defaults
to true), TAB-TERMINATED when the path contains a space, and
INDISTINGUISHABLE from an added content line whose own text begins `++ `. Two of
those were DETECTION REGRESSIONS - a planted byte base reported, attempt 1
passed green with no printed skip.

Round 2 does not patch that parser, it deletes it. File identity now comes from
`git diff --cached -M --name-status -z`; `check-attr` is called with `-z` on both
sides; the patch is cut into records at `diff --git ` BY POSITION and paired with
that list by index, with the record header ending at the first `@@` so a `+` line
after it is data whatever it looks like. Cases 9, 10 and 11 are the three
shapes, each against base as well, because two of them are regressions and an
after-shot with no before-shot proves nothing. Cases 12 and 13 cover the two
things the redesign newly introduces (a positional-pairing assertion and a
preview cap). Cases 1-8 are unchanged and MUST be re-run: a redesign that fixes
three shapes and breaks one of the eight is not a pass.

---

## READ THIS BEFORE YOU PLANT ANYTHING

**The thing under test is a pre-commit gate, and it will fire on YOU.** Cases 1-3
ask you to stage a file containing a control byte. If the clone you are working
in has `core.hooksPath` set, `git commit` runs
`scripts/checks/check-project-configs.ps1` on your staged planting and REFUSES
the commit - correctly, that is the whole point of the change. So:

* **Never `git commit` a planted file.** Every case below stages with `git add`
  and then runs the check SCRIPT directly. Nothing here needs a commit.
* A plain `git clone` does NOT inherit `core.hooksPath` (measured 2026-09-20:
  `git config --get core.hooksPath` in a fresh clone of this repo exits 1 with
  no value), so in a scratch clone the hooks are OFF unless you turn them on.
  A **git worktree** of the main checkout is the opposite case: it shares the
  main checkout's config, so the hooks ARE on there. Know which one you are in
  before you are surprised.
* Undo a planting with `git reset` then `git checkout -- .` and
  `git clean -fd`. Case 3 moves a real tracked file with `git mv`; `git reset
  --hard <base>` puts it back.

Build the scratch clone once and reuse it:

```
git -c core.longpaths=true clone "D:\Open WebUI\ai-stack" C:\g4\t
git -C C:\g4\t checkout f3eee64          # BASE, for cases 0a/0b
```

For cases 1-4 the clone needs the BRANCH:

```
git -C C:\g4\t fetch "D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-gate4-carries" work/sl-gate4-carries
git -C C:\g4\t checkout FETCH_HEAD
```

(Or run cases 1-4 in the worktree itself, remembering the hook warning above.)

---

## Case 0a - the binary defect EXISTS at base

**Why:** the fix is only worth having if the defect is real. Prove it before
believing the after-shot.

```
git -C C:\g4\t checkout f3eee64
cd C:\g4\t
python -c "import base64; open('docs-test.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
git add docs-test.png
git check-attr binary -- docs-test.png
powershell -NoProfile -ExecutionPolicy Bypass -File C:\g4\t\scripts\checks\check-project-configs.ps1
echo %ERRORLEVEL%      (cmd)    /    $LASTEXITCODE  (PowerShell)
```

**Expected:** `check-attr` says `docs-test.png: binary: set`. The check exits
**1** and prints two `CONTROL CHARACTER` lines for `docs-test.png` (`0x1A`,
`0x00`) plus the advice "Usual cause: a backslash escape in a NON-RAW
replacement string ... Use rb'' / r'' literals."

**Disproved if:** the check exits 0 at base, or does not name `docs-test.png`.
Then the defect this item fixes does not exist and the whole change is
unjustified.

Clean up: `git reset; del docs-test.png`.

## Case 0b - the rename blind spot EXISTS at base

```
git -C C:\g4\t checkout f3eee64
cd C:\g4\t
git mv documentation/notes/agent-memory-policy-findings.md documentation/notes/renamed-policy-findings.md
python -c "p='documentation/notes/renamed-policy-findings.md'; d=open(p,'rb').read(); open(p,'wb').write(d+b'\nA planted line with a backspace\x08byte.\n')"
git add documentation/notes/renamed-policy-findings.md
git diff --cached --name-status
powershell -NoProfile -ExecutionPolicy Bypass -File C:\g4\t\scripts\checks\check-project-configs.ps1
```

**Expected:** `--name-status` says `R099 <old> <new>`. The check prints
`[configs] nothing staged - skip` and exits **0** - it does not merely miss the
byte, it declares nothing staged. Confirm the byte is really there on the same
index:

```
git diff --cached -U0 --text --no-renames --diff-filter=ACM | python -c "import sys; d=sys.stdin.buffer.read(); print(b'\x08' in d)"
git diff --cached -U0 --text --diff-filter=ACM               | python -c "import sys; d=sys.stdin.buffer.read(); print(b'\x08' in d, len(d))"
```
**Expected:** `True` for the first, `False 0` for the second - the filter, not
the scan, is what lost it.

**Disproved if:** base exits 1, or the `--no-renames` diff is also empty (then
the byte was never staged and the case proves nothing).

Clean up: `git reset --hard f3eee64`.

---

## Case 1 - ACCEPTANCE: a staged binary is skipped BY NAME, exit 0

```
cd <branch checkout>
python -c "import base64; open('docs-test.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
git add docs-test.png
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-project-configs.ps1
```

**Expected:** exit **0**, and these two lines:

```
  [configs] binary per .gitattributes - control-character scan skipped: docs-test.png
  [configs] no control characters in staged added lines
```

**Disproved if:** exit is 1; or exit is 0 but the skip line is ABSENT (a silent
skip is the failure mode this gate is written against - a skip nobody can see
is indistinguishable from a scan that found nothing); or the skip names a path
other than the one staged.

**Also try, because the skip must be attribute-driven and not extension-driven:**
rename the same bytes to `docs-test.bin` and stage that instead.
`git check-attr binary -- docs-test.bin` says `unspecified`, so the file is
NOT skipped and the check exits 1 - with the new final three lines telling you
to declare the type in `.gitattributes`. That is the documented cost, not a
regression; if instead it exits 0, the skip is matching on something other than
the attribute and the finding note's reasoning is wrong.
Developer measured: `docs-test.bin: binary: unspecified`, exit 1,
`docs-test.bin:2 : 0x1A` and `docs-test.bin:3 : 0x00`.

Clean up: `git reset; del docs-test.png`.

## Case 2 - ACCEPTANCE: a planted 0x08 in a text file, exit 1, naming file:line

```
python -c "open('planted.txt','wb').write(b'line one\nline two\nthird line with a backspace\x08byte here\nline four\n')"
git add planted.txt
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-project-configs.ps1
```

**Expected:** exit **1** and, verbatim apart from leading spaces:

```
  [configs] CONTROL CHARACTER in staged added line(s):
             planted.txt:3 : 0x08 in an added line -> third line with a backspace<CTRL>byte here
```

The `:3` matters - it is the acceptance criterion's "file:line", it is read off
the `-U0` hunk header, and base printed no line number at all. Check it is the
RIGHT line: the byte is on the third line of the four written above.

**Disproved if:** exit 0; or the line number is absent, or is 1, or is 4 (an
off-by-one in the hunk-header arithmetic would show up as a consistent shift).
Do plant the byte on line 1 and on line 4 as well; developer measured
`p1.txt:1 : 0x08 ... first<CTRL>line` and `p4.txt:4 : 0x08 ... fourth<CTRL>line`,
so the arithmetic holds at both ends of a file, not only in the middle.

Clean up: `git reset; del planted.txt`.

## Case 3 - ACCEPTANCE: a rename-with-edit that adds 0x08, exit 1

Same plant as case 0b, on the branch:

```
git mv documentation/notes/agent-memory-policy-findings.md documentation/notes/renamed-policy-findings.md
python -c "p='documentation/notes/renamed-policy-findings.md'; d=open(p,'rb').read(); open(p,'wb').write(d+b'\nA planted line with a backspace\x08byte.\n')"
git add documentation/notes/renamed-policy-findings.md
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-project-configs.ps1
```

**Expected:** exit **1** with exactly ONE hit:

```
  [configs] CONTROL CHARACTER in staged added line(s):
             documentation/notes/renamed-policy-findings.md:121 : 0x08 in an added line -> A planted line with a backspace<CTRL>byte.
```

`wc -l documentation/notes/agent-memory-policy-findings.md` says **119**
(re-derive it); the plant appends a blank line 120 and the planted line 121, so
`:121` is the right number.

Then check the diff the gate is fed, which is the part that matters and is NOT
visible in the hit count:

```
git diff --cached -U0 --text              | grep -c "^+"      # shipped
git diff --cached -U0 --text --no-renames | grep -c "^+"      # the rejected spelling
```
**Expected: 3 and 123.** Developer measured exactly those. Do not accept "one
hit proves rename detection is on" - for THIS plant the hit count is 1 under
both spellings, because the other 120 lines are clean. The added-line COUNT is
what separates them, and the behavioural consequence is the second half below.

**Disproved if:** exit 0 (the blind spot survives); or exit 1 with many hits /
with line numbers for content the commit did not author (then `-M` was lost and
the note's rationale for rejecting `--no-renames` is not what shipped); or the
line number is not `121`.

**Second half - a PURE rename must stay green:**

```
git reset --hard <branch tip>
git mv documentation/notes/u8floor-findings.md documentation/notes/moved-u8floor.md
git add -A
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-project-configs.ps1
```
`documentation/notes/u8floor-findings.md` is one of the files carrying a
pre-existing `0x08` (line 100). **Expected: exit 0** - moving it introduces
nothing. Developer measured `R100` on `--name-status` and
`[configs] no control characters in staged added lines`, exit 0. If this goes
red, `--no-renames` semantics are in the shipped code and the out-of-scope
pre-existing bytes have been made into everybody's problem.

Clean up: `git reset --hard <branch tip>`.

## Case 4 - ACCEPTANCE: the whole delta staged, exit 0, and timing

```
cd <worktree or branch checkout>
TIP=$(git rev-parse HEAD)
git reset --soft f3eee64
git add -A
git diff --cached --name-only | wc -l
powershell -NoProfile -ExecutionPolicy Bypass -Command "$sw=[Diagnostics.Stopwatch]::StartNew(); & '<checkout>\scripts\checks\check-project-configs.ps1'; $c=$LASTEXITCODE; $sw.Stop(); Write-Host ('TOTAL_SECONDS=' + [math]::Round($sw.Elapsed.TotalSeconds,1)); Write-Host ('EXITCODE=' + $c)"
git reset --soft $TIP
git reset
```

**Expected:** **7** staged paths (the seven in the table at the top of this
file), `EXITCODE=0`, and the developer measured `TOTAL_SECONDS=0.8` (0.5 at
attempt 1 - host noise on a sub-second run, not a signal). (`reset --soft` and `reset` never touch the working tree,
so this is non-destructive; restore HEAD with the recorded `$TIP`.)

**Disproved if:** exit 1, or the path count is not 7 (then the tester is not
looking at the same delta).

**The worst case, separately, because it is the honest timing number.** Staging
the whole repository as added lines:

```
ROOT=$(git rev-list --max-parents=0 HEAD | tail -1)      # 35511a3
git reset --soft $ROOT ; git add -A
# ...same timed invocation...
git reset --soft $TIP ; git reset
```

**Expected:** 1202 staged paths, 338,373 diff lines, and **exit 1** - it
reports 9 control bytes in 8 pre-existing `documentation/` files, which the
anchor puts out of scope and which are only visible because staging every line
as ADDED is exactly the condition under which "added lines only" stops
protecting them. That is the gate working. (Your path count moves by one for
each file added to the branch since; the 9 hits do not.)

Developer's round-2 timings on this host, with round 1's three stages replayed
on the SAME index straight afterwards:

| stage | round 2 | round 1 |
|---|---|---|
| `--name-status -z` | 0.25 | - |
| `check-attr` (one call) | 0.11 | 1.74 |
| patch diff capture | 0.58 | 2.37 |
| the scan | 1.67 | 3.52 |
| **gate 4 total** | **2.61** | **7.63** |

whole check **28.6 s**. Round 2 is ~3x FASTER than the version it replaces,
because `cmd` redirecting into a file that `ReadAllBytes` reads costs far less
than PowerShell capturing a native command's stdout object by object - the same
change that makes the bytes trustworthy.

**Disproved if:** it exits 0 (then the gate is not seeing lines it should), or
gate 4 is SLOWER than round 1's 7.6 s on your host (the redesign is supposed to
cost less, not more; per-file `git diff` invocations would be the obvious way to
get this wrong and are deliberately not used).

## Case 5 - ACCEPTANCE: backup coverage prints no size digits and still exits 0

```
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-backup-coverage.ps1
```

**Expected:** exit **0**, ending `==> Coverage: CLEAN`. The `wiki-viewer-srv`
line reads:

```
  [SKIP] wiki-viewer-srv           - excluded: Wiki viewer SERVING TREE (build-<n> snapshots + a current symlink; size measured in the comment above) - a cache the builder regenerates from the vault; ...
```

Then, mechanically: pipe the whole output through a search for `7.6` and for
`GB` - **zero hits**. And read the source: the comment above the
`wiki-viewer-srv` entry still carries `Measured 2026-09-21 ... 7.6 GB, six
build-<n>/ snapshot trees`, dated.

**Disproved if:** the exit code is not 0; or any size figure survives in the
PRINTED line; or the dated measurement has been deleted from the comment rather
than moved into it (dropping the number entirely would lose the evidence, which
is the opposite of the intent).

## Case 6 - ACCEPTANCE: the href sweep, re-derived

Do not take the developer's sweep on trust; the point of this case is that the
PREVIOUS item's sweep was the thing that was wrong.

```
python <checkout>\documentation\evidence\sl-gate4-carries\href-sweep.py "D:\Open WebUI\ai-stack"
python <checkout>\documentation\evidence\sl-gate4-carries\href-sweep.py <checkout>
```

**Expected:** 17 relative hrefs checked in both runs.
* Against the MAIN checkout at `D:\Open WebUI\ai-stack` (which has not got this
  branch yet): **2 unresolved**, `backup-conventions.md:92` and `:130` - the
  two this branch fixes. After the branch is merged, re-run it there and expect
  **0**.
* Against this branch's worktree/checkout: **1 unresolved**, and only one -
  `UPDATE-MANAGEMENT.md:15`, the `../../../documentation-plans-ai-stack/...`
  plan-store link. Confirm the survivor's reason rather than accepting it:
  ```
  python -c "import os; p=os.path.normpath(os.path.join(r'D:\Open WebUI\ai-stack\documentation\runbooks','../../../documentation-plans-ai-stack/implementation-guide/update-owui-to-0-11-0/UPGRADE-PLAN.md')); print(p, os.path.exists(p))"
  ```
  **Expected `True`** - it resolves from the main checkout and fails only at the
  extra depth of `.claude/worktrees/<id>/`. That is the whole reason it is
  allowed to survive.

Also confirm the two fixed links by hand:
`sed -n '92p;130p' documentation/runbooks/backup-conventions.md` shows
`](../../docker-compose.yml)` and `](../../.env.example)`, and both files exist
at the repo root.

Then read the corrected sentence in
`documentation/notes/stack-layers-sl-recovery-backups-findings.md` and check it
says what your sweep said - not what the developer's said.

**Disproved if:** your sweep finds a different number of unresolved hrefs than
the note claims; or the sweep script has an exclusion list, a directory skip, or
a spelling assumption hidden in it (read it - it is 78 lines, and the defect it
replaces was exactly such an assumption); or `:92`/`:130` still resolve into
`documentation/`.

## Case 7 - ACCEPTANCE: the two corrected inventory rows

Read the scripts, do not grep for the developer's words.

```
sed -n '136,160p' scripts/recovery/gpu_check.py        # restart_gpu_services()
sed -n '205,235p' scripts/recovery/gpu_check.py        # the two single-service restarts
sed -n '25,29p'   scripts/maintenance/weekly-maintenance.ps1
```

**Expected:**
* `gpu_check.py:146` is
  `["docker", "compose", "restart", "openwebui", "llama-cpp-upstream", "llama-cpp-embed-upstream"],`
  and `:214` / `:228` restart `llama-cpp-upstream` and `llama-cpp-embed-upstream`
  individually. The corrected row in the findings note must therefore NOT say
  "read-only by design"; it must cite line 146.
* `weekly-maintenance.ps1` lines 25-29 are `param(`, `[switch]$Register,`,
  `[switch]$SkipCompact,`, `[int]$CompactWaitMinutes = 25`, `)`. The corrected
  row must name all three and must not say `-Register` is the only one. Note
  the row calls them three PARAMETERS, two switches and an int - if it calls
  all three "switches", that is a second inaccuracy replacing the first.

**Disproved if:** the cited line numbers do not hold the cited code (re-derive
them; the file may have moved), or the row still claims read-only / a single
switch.

**A free finding to check while you are in there:** `gpu_check.py:146` restarts
`openwebui` WITHOUT `tailscale`, which CLAUDE.md forbids ("never restart
openwebui alone - tailscale shares its netns; order is openwebui -> tailscale").
That is not this item's to fix and is not claimed as fixed; confirm it is not
claimed anywhere in the changed files.

## Case 8 - GATES

```
cd <worktree>
git log --format='%H %s' f3eee64..HEAD
git log --format=%B f3eee64..HEAD | grep -c 'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-hook-attestation.ps1 -Branch work/sl-gate4-carries -Base f3eee64
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\validate-lineendings.ps1
ruff check .
python -c "b=open('scripts/checks/check-project-configs.ps1','rb').read(); print('bom',b[:3]==b'\xef\xbb\xbf','nonascii',sum(1 for c in b if c>127),'crlf',b.count(b'\r\n'),'lf',b.count(b'\n'))"
grep -n '&&\|??\|?:' scripts/checks/check-project-configs.ps1
```

**Expected:** every commit on the branch carries the `Co-Authored-By` trailer;
attestation reports no unattested tree; line endings pass; `ruff check .` says
`All checks passed!`; `check-project-configs.ps1` reports `bom False nonascii 0`
and equal CRLF/LF counts (every LF is part of a CRLF - no stray lone LF, no
stray lone CR); and no `&&`, `??` or `?:` appears in it.

`check-backup-coverage.ps1` is the exception and is DELIBERATE: it already had a
UTF-8 BOM and em-dashes before this change, and that was left alone rather than
re-encoded in the same commit as a content fix. Confirm it is unchanged in that
respect: `python -c "b=open('scripts/checks/check-backup-coverage.ps1','rb').read(); print(b[:3]==b'\xef\xbb\xbf', sum(1 for c in b if c>127))"` should print
`True 12` on BOTH `f3eee64` and the branch tip.

**Disproved if:** any of the above fails, or the BOM/non-ASCII count of
`check-backup-coverage.ps1` CHANGED (a silent re-encode riding along inside a
one-line fix).

---

## Case 9 - REGRESSION GUARD: a non-ASCII (C-quoted) path

**Why:** `core.quotepath` defaults to true here (`git config --get core.quotepath`
returns nothing in the main checkout and in a fresh clone), so git renders such a
path as `+++ "b/na\\303\\257ve.txt"`. Attempt 1 could not match that and
latched its skip flag on.

Plant, on BASE and then on the BRANCH, the same index both times:

```
python -c "import base64; open('caf\u00e9.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
python -c "open('na\u00efve.txt','wb').write(b'alpha\nbad\x08byte\n')"
git add -A
powershell -NoProfile -ExecutionPolicy Bypass -File <checkout>\scripts\checks\check-project-configs.ps1
```

**Expected:**

| version | result |
|---|---|
| base `f3eee64` | **exit 1**, 3 hits, and the file names print BLANK (`  : 0x08 in an added line -> bad<CTRL>byte`) |
| attempt 1 `f50a80b` | **exit 0**, 0 hits, NO skip line - the regression |
| this branch | **exit 1**, exactly 1 hit `naïve.txt:2 : 0x08 ...`, plus `binary per .gitattributes - control-character scan skipped: café.png` |

The name renders in the console's codepage (CP437 here: `0x82` for `é`,
`0x8b` for `ï`), so it reads correctly on the console and will look like
mojibake if you pipe it through a UTF-8 tool. Check the BYTES if in doubt; what
must not happen is a blank, a `?`, or a missing file.

**Then prove it is not configuration-dependent** - the whole point of `-z`:

```
git -c core.quotepath=false ...     # or set GIT_CONFIG_PARAMETERS="'core.quotepath=false'"
```
and re-run. **Expected: identical output**, both skip line and hit. Attempt 1
passed under `quotepath=false` and failed under the default, which is how the
defect hid.

**Disproved if:** the branch exits 0; or the PNG is not skipped; or the hit
count is 3 (then the PNG is being scanned as well and the skip is not working);
or the two `quotepath` settings disagree.

## Case 10 - REGRESSION GUARD: a path containing a SPACE

**Why:** git TAB-terminates `+++ b/<path>` when the path has a space
(`cat -A` shows `+++ b/a file with spaces.png^I$`). Attempt 1 kept the tab, so
`check-attr` was asked about `<path>` + TAB, answered `unspecified`, and the
staged PNG was NOT skipped - the item's headline defect, unfixed for that shape.

```
python -c "import base64; open('a file with spaces.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
python -c "open('spaced text file.txt','wb').write(b'one\ntwo\x08three\n')"
git add -A ; <run the check>
```

**Expected:**

| version | result |
|---|---|
| base `f3eee64` | exit 1, 3 hits, every label carrying a stray TAB |
| attempt 1 `f50a80b` | exit 1, 3 hits, same stray TAB - the PNG still fails |
| this branch | **exit 1, exactly 1 hit**, `spaced text file.txt:2 : 0x08 ...` with NO tab, and `... scan skipped: a file with spaces.png` |

Pipe the branch output through `cat -A` (or check the bytes) and confirm there
is no `^I` before the `:`. Then do the same on a TRACKED spaced path, which this
repo really has:

```
python -c "p='documentation/archive/AI/Tutorial Docker Compose Setup for Open WebUI.md'; d=open(p,'rb').read(); open(p,'wb').write(d+b'\nplanted\x08byte\n')"
git add -A ; <run the check>
```
**Expected:** exit 1,
`documentation/archive/AI/Tutorial Docker Compose Setup for Open WebUI.md:349 : 0x08 ...`
- a `path:line` you can paste into an editor. Developer measured exactly that.

**Disproved if:** any printed label contains a tab; or the PNG is not skipped;
or the line number is not 349 (re-derive it - the file's length is the authority).

## Case 11 - REGRESSION GUARD: an added line whose text looks like a header

**Why:** a content line beginning `++ ` reaches the diff as `+++ ...` and attempt
1 ate it as a file header, silencing the rest of the hunk. `git grep '^++ '`
finds 0 such lines in the tree today, so this is evasion/bad luck rather than an
accident waiting - but the gate is a protection check.

```
python -c "open('evade1.txt','wb').write(b'harmless\n++ /dev/null\nsneaky\x08byte\nlast\n')"
python -c "open('evade2.txt','wb').write(b'harmless\n++ b/docs-test.png\nsneaky\x08byte\nlast\n')"
python -c "import base64; open('docs-test.png','wb').write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))"
git add -A ; <run the check>
```

**Expected:**

| version | result |
|---|---|
| base `f3eee64` | exit 1, 4 hits - and evade2's byte MISATTRIBUTED to `docs-test.png` |
| attempt 1 `f50a80b` | **exit 0**, 0 hits - two planted bytes pass |
| this branch | **exit 1, 2 hits**: `evade1.txt:3` and `evade2.txt:3`, each on its OWN file, plus the PNG skipped by name |

**Disproved if:** the branch exits 0; or either hit is attributed to
`docs-test.png` (the misattribution base made); or only one of the two is found
(then one `++ ` line is still being consumed).

## Case 12 - the positional pairing, and its assertion

The redesign pairs patch record N with `--name-status` entry N. If that ever
drifted, every label after the divergence would be wrong, so the gate asserts it.

**(a) It holds on the hardest index available.** With the whole repository
staged (case 4's worst case), instrument or simply observe: developer measured
**1202 `diff --git` records against 1202 `--name-status` entries**, and no
`CANNOT ATTRIBUTE` line. Reproduce the two counts yourself:

```
git diff --cached -M -U0 --text | grep -c "^diff --git "
git diff --cached -M --name-status -z | python -c "import sys; f=sys.stdin.buffer.read().split(b'\x00'); n=0; i=0
while i < len(f):
    if not f[i]: i+=1; continue
    n+=1; i += 3 if f[i][:1] in (b'R', b'C') else 2
print(n)"
```
**Expected: equal.** Do it on a MIXED index too - an add, a modify, a delete, a
rename-with-edit and a pure rename in one commit - since that is where a naive
pairing would break. Developer measured 7 = 7 on exactly such an index (a spaced
PNG, a non-ASCII PNG, a rename, a delete, and three adds).

**(b) The assertion is not decorative.** Read it in
`check-project-configs.ps1` and confirm a mismatch FAILS (`$failed++`) rather
than warning. `grep -n "CANNOT ATTRIBUTE" scripts/checks/check-project-configs.ps1`.

**Disproved if:** the counts differ on any index while the check still prints a
confident `path:line`; or the mismatch branch only warns.

## Case 13 - the preview cap

Lossless byte handling means a binary file git was NOT told is binary now
renders as one very long line. Case 1's second half (`docs-test.bin`) shows it:

**Expected:** the `0x00` hit's preview ends with ` ...[truncated]` and the whole
line is bounded, while `docs-test.bin:3` - the actionable part - is intact and
exact. Developer measured the preview cut at 120 characters.

**Disproved if:** an entire binary file is printed (then the cap is not
applied), or the cap has eaten the `path:line` prefix.

## Case 14 - the claims this item makes

Every sentence below is something this work asserts. Each is either measurable
or it should not have been written. Tick or break each one.

**About the base (f3eee64):**
1. A staged 70-byte PNG, with `*.png binary` in `.gitattributes`, made
   `check-project-configs.ps1` exit 1. (Case 0a)
2. Its failure message advised using raw Python string literals, for a file with
   no strings. (Case 0a)
3. The reported bytes were `0x1A` and `0x00`. (Case 0a)
4. `git mv` + an edit appending `0x08`, staged alone, made the check exit 0.
   (Case 0b)
5. It exited 0 printing `nothing staged - skip`, i.e. all four gates were
   skipped, not just gate 4. (Case 0b)
6. The cause is `--diff-filter=ACM`, which appears twice - at the top-of-file
   `$staged` and on gate 4's own diff. (Case 0b, and read the base file)
7. `--no-renames` on the same index does see the byte. (Case 0b)

**About the tree as measured 2026-09-20:**
8. The repo tracks 1198 files.
9. ZERO of them have `git check-attr binary` = set.
10. ZERO of them contain a NUL byte in their first 8000 bytes.
11. `.gitattributes` declares `binary` for exactly six extensions: `*.png`,
    `*.jpg`, `*.jpeg`, `*.gif`, `*.ico`, `*.db`. (read `.gitattributes`)
12. `git check-attr binary -- <1198 paths>` fails with `WinError 206` (command
    line too long) - which is why `--stdin` is used.
13. A PowerShell pipeline into `git check-attr --stdin` yields
    `"<path>\r": binary: unspecified`, because PS 5.1 writes CRLF to a native
    command's stdin - which is why a temp file redirected by `cmd` is used.
    Reproduce it in one line, from PowerShell in any checkout of this repo -
    the path need not exist, `check-attr` matches pathnames:
    ```
    @('foo.png') | & git check-attr --stdin binary      -> "foo.png
": binary: unspecified
    git check-attr binary -- foo.png                    -> foo.png: binary: set
    ```
    Two answers for the same path; the pipeline's is the wrong one.
14. Nine control bytes sit in eight pre-existing `documentation/` files. (Case 4,
    worst case)
15. `documentation/notes/u8floor-findings.md` carries one of them, at line 100.
    (Case 3, second half)

**About the changed gate:**
16. A binary-attributed staged file is skipped and the skip is PRINTED with the
    path. (Case 1)
17. A file whose bytes are binary but whose type `.gitattributes` does not cover
    is NOT skipped, and the failure text now tells the author to declare it.
    (Case 1, second half)
18. A planted `0x08` in a text file gives exit 1 with `path:line`. (Case 2)
19. A rename-with-edit gives exit 1 with exactly ONE hit at `:121`. (Case 3)
19b. On that same index the shipped diff has 3 `+`-prefixed lines and the
    `--no-renames` diff has 123; and the hit COUNT is 1 under both, so the hit
    count alone does not prove rename detection is on. (Case 3)
20. A PURE rename of a file containing a pre-existing control byte stays exit 0.
    (Case 3, second half)
21. The class is unchanged from base:
    `[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]`. (diff the two versions)
22. `--text` is still passed. (read the file)
23. The early exit no longer uses a rename-blind path list. (read the file;
    Case 0b vs Case 3)
24. Gates 1-3 STILL cannot see a rename, deliberately, and this is written down
    in the findings note rather than fixed. (read
    `documentation/notes/stack-layers-sl-gate4-carries-findings.md` §3, then
    verify by staging a rename-with-syntax-error of a `.ps1` and observing gate
    2 does not parse it. Developer measured exactly that:
    `git mv scripts/checks/dev-helper.ps1 scripts/checks/dev-helper-moved.ps1`,
    append an unclosed `function Broken {`, `git add -A` -> `--name-status`
    says `R099`, the check exits **0** and prints only gate 4's green line.
    Gate 2 never saw the file.)
25. The whole delta staged against `f3eee64` exits 0 in ~0.8 s over 7 paths.
    (Case 4)
26. SUPERSEDED by claim 56 - attempt 1's figure. Round 2's is 2.61 s on a
    1202-path / 338,373-line diff, ~9% of the 28.6 s whole check. (Case 4)

**About the documentation changes:**
27. `check-backup-coverage.ps1` exits 0 and its `wiki-viewer-srv` line prints no
    size digits. (Case 5)
28. The dated `7.6 GB` measurement survives in the comment above. (Case 5)
29. `backup-conventions.md:92` and `:130` were `../` and resolved into a
    `documentation/` directory holding neither file. (Case 6, against the main
    checkout, which is still at base)
30. They are now `../../` and both targets exist. (Case 6)
31. An unbounded sweep finds 17 relative hrefs in `documentation/runbooks/`.
    (Case 6)
32. At base it found 3 unresolved; the previous item's note said 1. (Case 6 +
    read the note)
33. After the fix: 0 unresolved at main-checkout depth, 1 inside a nested
    worktree. (Case 6)
34. That 1 survivor is the plan-store link and it resolves from the main
    checkout. (Case 6, the `os.path.exists` one-liner)
35. `gpu_check.py` is not read-only: line 146 runs
    `docker compose restart openwebui llama-cpp-upstream llama-cpp-embed-upstream`.
    (Case 7)
36. It also restarts each upstream alone at `:214` and `:228`. (Case 7)
37. `weekly-maintenance.ps1`'s `param()` block at lines 25-29 declares
    `-Register`, `-SkipCompact` and `-CompactWaitMinutes` - three parameters,
    two of them switches. (Case 7)
38. `backup-conventions.md`'s pattern template now points at a root compose file
    with 0 services and a root `.env.example`, which is an OPEN issue recorded in
    §4 of the findings note and NOT claimed as fixed. (read §4, read the runbook)

**About this plan:**
39. A plain `git clone` of this repo does not inherit `core.hooksPath`; a git
    worktree of the main checkout does. (`git config --get core.hooksPath` in
    each)

**ROUND 2 - everything this attempt adds as a claim:**
40. `core.quotepath` is unset (default true) in the main checkout and in a fresh
    clone. (`git config --get core.quotepath`)
41. At base, the non-ASCII index gives exit 1 with BLANK file names. (Case 9)
42. At attempt 1 `f50a80b`, the same index gives **exit 0, 0 hits, no skip
    line** - a detection regression. (Case 9)
43. On this branch it gives exit 1, one hit `naïve.txt:2`, and the PNG skipped
    by name. (Case 9)
44. `core.quotepath=false` and the default `true` produce identical output on
    this branch. (Case 9)
45. Git tab-terminates `+++ b/<path>` when the path has a space. (`cat -A` on
    the raw diff)
46. At base AND at attempt 1, the spaced-path index gives 3 hits with a stray
    TAB in every label, and the spaced PNG is NOT skipped. (Case 10)
47. On this branch: 1 hit, no tab, PNG skipped. (Case 10)
48. The tracked spaced path
    `documentation/archive/AI/Tutorial Docker Compose Setup for Open WebUI.md`
    reports as `...md:349` with no tab. (Case 10)
49. `git grep '^++ '` finds 0 such lines in the tree. (Case 11)
50. At base the `++ ` index gives 4 hits with evade2's byte misattributed to
    `docs-test.png`; at attempt 1 it gives **exit 0**; on this branch 2 hits,
    correctly attributed. (Case 11)
51. `diff --git` record count equals `--name-status -z` entry count: 1202 = 1202
    on the whole-repository stage, 7 = 7 on a mixed add/modify/delete/rename
    index. (Case 12)
52. A count mismatch FAILS the check rather than warning. (Case 12, read the
    source)
53. A bare `diff --git ` line cannot be produced by content, because every line
    inside a hunk carries a `+`, `-`, space or `\\` prefix. (read any diff)
54. `git check-attr -z --stdin binary` takes NUL-separated paths and emits
    NUL-separated `path, attribute, value` triples. (run it)
55. The preview is capped at 120 characters with ` ...[truncated]`. (Case 13)
56. Round-2 gate 4 costs 2.61 s where round 1 cost 7.63 s on the same
    whole-repository index - ~3x faster, and no per-file `git diff` is used.
    (Case 4)
57. Re-measured at this branch tip: 1201 tracked files, 0 with the binary
    attribute set, 0 carrying a NUL in their first 8000 bytes (1198 / 0 / 0 at
    `f3eee64`). (`git ls-files`, `check-attr --stdin`, read the bytes; `OB1` is
    a gitlink, not a file)
58. Cases 1-8 and 0a/0b all re-run to the same outputs they had at attempt 1.
    (re-run them)
59. Nothing in section 3 or section 4 of the findings note changed: gates 1-3
    still cannot see a rename, and the runbook still points at a compose file
    with no services. (read them; re-run case 3c)
