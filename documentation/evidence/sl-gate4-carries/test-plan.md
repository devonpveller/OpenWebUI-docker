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

## READ THIS BEFORE YOU PLANT ANYTHING

**The thing under test is a pre-commit gate, and it will fire on YOU.** Cases 1-3
ask you to stage a file containing a control byte. If the clone you are working
in has `core.hooksPath` set, `git commit` runs
`scripts/checks/check-project-configs.ps1` on your staged planting and REFUSES
the commit - correctly, that is the whole point of the change. So:

* **Never `git commit` a planted file.** Every case below stages with `git add`
  and then runs the check SCRIPT directly. Nothing here needs a commit.
* A plain `git clone` does NOT inherit `core.hooksPath` (measured 2026-09-22:
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
file), `EXITCODE=0`, and the developer measured `TOTAL_SECONDS=0.5`. (`reset --soft` and `reset` never touch the working tree,
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

**Expected:** 1201 staged paths, 337,737 diff lines, and **exit 1** - it
reports 9 control bytes in 8 pre-existing `documentation/` files, which the
anchor puts out of scope and which are only visible because staging every line
as ADDED is exactly the condition under which "added lines only" stops
protecting them. That is the gate working. Developer's timings on this host:
gate 4 alone **9.21 s** (2.73 capture + 1.83 `check-attr` + 4.65 scan), whole
check **44.2 s** - the rest is the nine `docker compose config` renders.

**Disproved if:** it exits 0 (then the gate is not seeing lines it should), or
gate 4's share is a large multiple of 9 s (then the `check-attr` batching or
the per-line scan regressed and pre-commit has become expensive).

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

## Case 9 - the claims this item makes

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

**About the tree as measured 2026-09-22:**
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
25. The whole delta staged against `f3eee64` exits 0 in ~0.5 s over 7 paths.
    (Case 4)
26. Gate 4 alone costs 9.21 s on a 1201-path / 337,737-line diff, ~21% of the
    44.2 s whole check. (Case 4, worst case)

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
