# sl-closeout2 — test plan

**Item:** `sl-closeout2` (stack-layers wave 1; the REOPEN of `sl-closeout`,
rejected `-Misfits` at review 2026-09-19) · **Branch:** `work/sl-closeout`
· **Base:** `development` @ b28cbc5 · **Anchor:**
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-closeout2.json`

The branch name and the evidence directory keep the ORIGINAL id: the three
commits the first tester passed still stand, and one fix commit closes the
eighteenth error. T1-T10 are unchanged from the attempt that passed them; T11
and T12 are the two new criteria.

## How to run this

Work from your own worktree of this repo. **Score the branch, not a working
tree** — `core.autocrlf` is on and the operator's checkout carries uncommitted
edits, so read every file as a blob:

```bash
git show work/sl-closeout:README.md
git show work/sl-closeout:.env.example | sed -n '50,70p'
git diff development...work/sl-closeout -- <path>
```

No plane lease is needed: nothing here deploys, restarts or rebuilds anything.
The only command that touches Docker is `docker compose config`, which renders
YAML and starts nothing.

**Verify each CORRECTED claim against the file it describes** — the compose
file, the script, the config — never against
`documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md`. The audit
note is the input to this item, and it is itself wrong in at least one place
(see T2, finding 7): a corrected sentence that merely matches the note is not a
pass.

## Declared conflict with acceptance criterion 6

The anchor's last criterion says "the diff touches no file outside the artifact
list and adds no new document". The branch touches **four** paths the artifact
list does not enumerate. Each is deliberate and each is either named in the
developer's instructions or forced by another criterion; the gate is entitled to
decide whether that is acceptable rather than have this plan quietly test
something softer:

1. `llm-queue/src/llm_queue/__init__.py` — a pre-existing ruff E501 on line 9
   that made `ruff check .` RED on this branch before any doc edit. The same
   criterion requires ruff to pass, so it cannot be satisfied without this.
2. `documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md` — the audit
   note, the evidence for every other change, previously untracked and living
   only in the operator's checkout.
3. `documentation/notes/stack-layers-sl-closeout-findings.md` — the
   `findings_sink` the anchor itself names.
4. `documentation/evidence/sl-closeout/test-plan.md` — this file.

T9 tests the criterion as: nothing outside the artifact list **plus these four**.

---

## T1 - criterion 1, findings 1-4: the four that would break a newcomer

Read each corrected file and the SOURCE it describes.

1. **Finding 1 (gateway posture).** `git show work/sl-closeout:.env.example`
   and `git show work/sl-closeout:config/litellm.config.yaml`. Both headers must
   now say the gateway ENFORCES per-caller virtual keys and that
   `LITELLM_MASTER_KEY` is required. Verify against the config itself:
   `git show work/sl-closeout:config/litellm.config.yaml | grep -n master_key`
   must show `master_key: os.environ/LITELLM_MASTER_KEY` under
   `general_settings`. **FAIL** if either file still says PERMISSIVE or "master
   key intentionally absent", or if a corrected sentence cites a LINE NUMBER for
   `master_key` that is not the line it is on (the developer removed the line
   numbers for exactly this reason; a re-introduced one that is off by two is a
   fail).
2. **Finding 2 (model-backup switch).** `.env.example` must no longer name
   `LM_MODELS_BACKUP_CRON`; it must name `LM_MODELS_BACKUP_INTERVAL` as the
   disable switch. Verify against
   `git show work/sl-closeout:inference/docker-compose.yml | sed -n '380,430p'`
   — the `lm-models-backup` entrypoint must actually exit when that variable is
   empty. **FAIL** if the compose file does not implement the behaviour the
   comment now promises.
3. **Finding 3 (little-coder docs path).** `.env.example` must point at
   `../documentation-plans-ai-stack/implementation-guide/little-coder/`.
   Confirm that directory is non-empty on disk, and that
   `documentation/little-coder/` does not exist on the branch
   (`git ls-tree work/sl-closeout documentation/little-coder` prints nothing).
4. **Finding 4 (gateway source dir).** The `README.md` repo-map row and
   `CLAUDE.md`'s OB1 section must name `mnemory-gateway/`. Verify the directory
   exists (`git ls-tree work/sl-closeout mnemory-gateway/`) and that
   `mnemory-cloud-gateway` is the CONTAINER built from it —
   `git show work/sl-closeout:memory/docker-compose.yml | sed -n '64,76p'` shows
   `context: ../mnemory-gateway`. **FAIL** if either doc now claims the
   directory and the container share a name.

PASS = all four corrected, each confirmed against its source file.

## T2 - criterion 1, findings 5-10: the misleading half

5. **Restore command.** `git show work/sl-closeout:README.md | sed -n '90,100p'`
   must show `-SnapshotRoot` and `-Date`. Check the script:
   `git show work/sl-closeout:scripts/backup/restore-from-snapshot.ps1 | sed -n '28,45p'`
   — both are `Mandatory = $true`, and `-Services` defaults to `all`. **FAIL**
   if the README now marks `-Services` mandatory, or if the PowerShell line
   continuation is broken (the backtick must be the last character on its line).
6. **NAS cadence.** The README must say weekly, and the time it states must
   match
   `git show work/sl-closeout:scripts/backup/install-nas-backup-task.ps1 | sed -n '89p'`
   (`-Weekly -DaysOfWeek Sunday -At 4am`). **FAIL** on any other day or hour.
7. **Pre-commit count.** `README.md` and `scripts/README.md` must agree with
   `git show work/sl-closeout:.githooks/pre-commit`. Count the invocations
   yourself:
   `git show work/sl-closeout:.githooks/pre-commit | grep -c "powershell.exe -NoProfile"`.
   **The audit note says 8 and is wrong** — it counted the header comment, which
   is itself missing check 5d. The docs must state the number YOU count, and
   `scripts/README.md`'s list must name those same scripts in the order the hook
   runs them. **FAIL** if either doc says 3, or 8, or lists a script the hook
   does not invoke.
8. **The archived `.bat`.** Neither `README.md` nor `agent-org/README.md` may
   present `emergency-recovery.bat` as something you can run.
   `git ls-tree work/sl-closeout scripts/recovery/` must not contain it, and it
   must be present under `scripts/archive/`.
9. **CLAUDE.md:16.** The Main row must no longer claim the root file includes
   `compose/<plane>.yml`. Verify: `git ls-tree work/sl-closeout compose/` is
   empty, and `git show work/sl-closeout:docker-compose.yml | grep -c "include:"`
   is 0.
10. **Probe count.** Count for yourself:
    `git show work/sl-closeout:scripts/stack/stack.ps1 | grep -cE "^ +Probe \""`.
    The README's number must equal what you count. Do not count the
    `function Probe {` definition or the commented-out mention. **FAIL** on 12.

PASS = all six corrected, every number independently recounted.

## T3 - criterion 1, findings 11-14: counts and headers in the plane files

11. **Coder volumes.**
    `git show work/sl-closeout:coder/docker-compose.yml | sed -n '/^volumes:/,/^networks:/p'`
    — count the declared volumes. `CLAUDE.md`'s coder row and the compose
    comment must both agree with that count, and the comment's breakdown must
    add up (expertise stores + sessions + workspace). **FAIL** if the comment
    still says "five expertise volumes" while four are declared.
12. **Portal header.** `git show work/sl-closeout:portal/docker-compose.yml |
    sed -n '1,15p'` — lines 13-14 must no longer say networks/volumes live in
    the root file. **That wording is `development`'s, not this branch's:** both
    branches corrected audit finding 12 in different words, and this rebase
    dropped this branch's hunk in favour of `development`'s, which also covers
    the `portal/config/` move (`sl-colo-portal`, merged as adfd9f2). See the
    findings note §12. `git diff development -- portal/docker-compose.yml` must
    therefore be EMPTY. The finding is still verified the same way: read the
    file's tail (`sed -n '590,616p'`) and confirm it declares `edge-net`,
    `auth-net`, `notify-net` (networks: at :592) and four volumes (volumes: at
    :611) itself, taking only `ai-stack_app-net` externally. **FAIL** if the
    header names a network or volume the file does not declare, or if this
    branch re-introduces a competing header.
13. **agent-bridge test count — COUNT IT WITH THE AST, NOT WITH GREP.**
    `grep -c "def test_"` OVER-COUNTS: this suite writes *about* testing, so the
    string appears in prose too (`tests/test_p18_observation.py:392` is one such
    line, and `app/orchestrator.py:1403,1432` are two more, in comments). Attempt
    1 of this item failed exactly there — a grep count of 866 shipped into the
    README when there are 865 definitions. Count definitions:

    ```bash
    python -c "import ast,pathlib; print(sum(1 for p in pathlib.Path('agent-org/agent-bridge/tests').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')))"
    ```

    (Python prints two `SyntaxWarning` lines while parsing the suite; they are
    harmless and the count is the last line — it is 865 on this branch.)

    Both mentions in `git show work/sl-closeout:agent-org/README.md` must equal
    that number AND must name the method they used, so the next reader can
    reproduce it. `pyproject.toml:23-25` sets `testpaths = ["tests"]`, so
    definitions outside that directory are out of scope.
    **FAIL** if the README says 55; **FAIL** if it says 866 (the grep artefact);
    **FAIL** if it states a bare number with no method, or presents a number as
    what `pytest -q` reports — the suite was NOT run (it needs
    `pip install -e .[test]`) and parametrization makes pytest's count higher.
    See the findings note §5.
14. **Watchdog project count.** `scripts/README.md` must match the `projects`
    keys in `git show work/sl-closeout:scripts/lib/stack-services.json`.
    **FAIL** if it says three, or if it lists `portal` among them — the portal
    is not in that inventory.

PASS = all four corrected, each count re-derived from the source.

## T4 - criterion 1, findings 15-17

15. **Backup-interval table.** Every variable in the README's backup table must
    exist in `.env.example`. Check each one against the blob:
    `MNEMORY_BACKUP_INTERVAL`, `OPENWEBUI_BACKUP_INTERVAL`,
    `TAILSCALE_BACKUP_INTERVAL`, `LITTLE_CODER_BACKUP_INTERVAL`,
    `LM_MODELS_BACKUP_INTERVAL`. (`OPENBRAIN_WIKI_BACKUP_INTERVAL` is documented
    as living in `OB1/docker/.env` and is not expected in the root template.)
    **FAIL** on any missing line, or on a default that disagrees with the
    compose file that reads it.
16. **The four unread cron variables.** Covered by T5.
17. **Index framing.**
    `git show work/sl-closeout:documentation/implementation-guide/README.md | sed -n '1,20p'`
    must no longer frame the Phase-2 migration as undecided, and must say
    Phase 2 ran 2026-09-18. Verify the claim it now makes:
    `git ls-tree -d --name-only work/sl-closeout documentation/implementation-guide/`
    must list exactly `multi-agent-concurrency` and `dark-factory-unification`.
    **FAIL** if the paragraph asserts a COUNT of migrated directories — that
    number is not verifiable from this tree and was deliberately removed.

PASS = all three corrected, and the index's new claim confirmed against the tree.

## T5 - criterion 2, the .env.example variables

Run against the blob:

```bash
git show work/sl-closeout:.env.example > envx.txt
for v in LITTLE_CODER_BACKUP_INTERVAL MNEMORY_BACKUP_INTERVAL OPENWEBUI_BACKUP_INTERVAL; do
  grep -n "^$v=" envx.txt || echo "MISSING $v"
done
for v in BACKUP_CRON OPENWEBUI_BACKUP_CRON LC_BACKUP_CRON LITELLM_BACKUP_CRON; do
  grep -n "^$v=" envx.txt && echo "STILL ASSIGNED $v"
done
```

PASS requires: the three INTERVAL lines present with values matching the compose
defaults (`coder/docker-compose.yml:182`, `memory/docker-compose.yml:111`,
`frontend/docker-compose.yml:375` — all `:-86400`); and none of the four cron
names assigned a value. Where a cron name survives as prose, the comment must
name the REAL mechanism, and you must check that mechanism exists —
`llm-gateway-backup` has no interval variable at all, so confirm its entrypoint
is a hardcoded `sleep 86400`:
`git show work/sl-closeout:inference/docker-compose.yml | sed -n '/llm-gateway-backup:/,/lm-models-backup/p'`.

**FAIL** if any of the four is still an assignment. **FAIL** if a replacement
comment names a variable that nothing reads either — re-grep it yourself,
unbounded and including `.env`, from the repo root.

## T6 - criterion 3, the anchor compose file still renders and the orphan is gone

Verbatim from the anchor, run from a checkout of `work/sl-closeout` (the file
must be on disk for `docker compose`):

```bash
docker compose -f docker-compose.yml --env-file .env.example config -q && ! grep -q smolcrawl-data docker-compose.yml
```

Both halves must succeed. Then confirm the volume was **not** deleted from the
daemon: `docker volume ls` must still list `ai-stack_smolcrawl-data`. **FAIL**
if the render errors, if the string appears anywhere in the file (a comment
counts — see the findings note §8), or if the docker volume is gone.

## T7 - criterion 4, the CLEANUP-PLAN v3 close-out ledger

`git show work/sl-closeout:CLEANUP-PLAN.md | sed -n '1,205p'`. The new dated
section must let a reader who reads ONLY it answer "which open items still exist
and where". Check specifically:

- it is dated 2026-09-19 and sits near the top, after the existing ledgers;
- it states per Part what closed;
- it names Part K as what superseded **D.1**;
- it names the plan store as what superseded **C.7** and Part C's routing rules;
- it lists what moved to `stack-layers`: **Part L**, the **D.1 x-anchors**,
  **D-12**, **I.4/J.6**. *A section that omits Part L or D-12 FAILS* — the
  anchor's own words;
- it lists what stays open elsewhere, each with a destination: E.1, H.2,
  F.1/F.2, the M.8 tail, D7;
- it points at
  `../documentation-plans-ai-stack/implementation-guide/stack-layers/PLAN.md`.

Then spot-check three of its factual claims against the tree, not against the
audit note: that no compose file contains an `x-` anchor; the `orchestrator.py`
and `bridge.py` line counts; and that
`scripts/checks/check-project-configs.ps1` really does contain the
stack-services drift verifier the ledger credits D-12 with. **FAIL** on any
claim that does not hold.

## T8 - criterion 5, the index row and plan-store.ps1

1. `git show work/sl-closeout:documentation/implementation-guide/README.md | grep -n "stack-layers"`
   — exactly one status row, marked **@ plan store**, stating PLAN 2026-09-19
   and wave 1 in progress.
2. Run the check **with your current directory inside your worktree** — `cd`
   there first; passing the script by absolute path is not enough:

```powershell
cd "<your worktree>"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\checks\plan-store.ps1" -Store "D:\Open WebUI\documentation-plans-ai-stack"
```

Expect exit 0 and `plan store: clean (versioned, pushed, indexed)`.

**Both the CWD and `-Store` matter, and that is a defect in the script rather
than in this item** (findings note §2). `plan-store.ps1:56` takes `$Root` from
`git rev-parse --show-toplevel`, i.e. from the CURRENT DIRECTORY. `-Store` fixes
only the store lookup (:59-60, which otherwise throws because a worktree's
parent is `.claude/worktrees/`); the seam check reads
`$IndexPath = <$Root>/documentation/implementation-guide/README.md` (:62), so
running it with the CWD in the operator's MAIN checkout scores this branch's row
against the main checkout's index — which will not carry the row until the
branch merges — and exits 1 with `plan store feature 'stack-layers' has NO
status row in the index` (:89). That exit-1 is the wrong CWD, not a fail of this
item; re-run from the worktree. Likewise, running without `-Store` throws, which
is expected.

**FAIL** only if it exits non-zero when run with the CWD in your worktree AND
`-Store` given, or if the seam check then reports stack-layers as having no row.

Note on its first section: the embedded `check-doc-placement.ps1 -All` audits
whatever `$Root` resolved to, so from your worktree it audits YOUR tree, not the
operator's. If you do run it from the main checkout and it lists untracked notes
there, those are the operator's and are not this item's to fix.

## T9 - criterion 6, the scope of the diff, ruff, and the hooks

```bash
git diff --name-only development...work/sl-closeout
ruff check .        # from a checkout of the branch
```

The file list must be exactly the artifact list plus the four declared above
(see "Declared conflict"), and after the 2026-09-19 rebase onto 9f64b84 it is
**fourteen** paths: `README.md`, `CLAUDE.md`, `.env.example`,
`config/litellm.config.yaml`, `scripts/README.md`, `agent-org/README.md`,
`coder/docker-compose.yml`, `documentation/implementation-guide/README.md`,
`CLEANUP-PLAN.md`, `docker-compose.yml`,
`llm-queue/src/llm_queue/__init__.py`, and the three files under
`documentation/notes/` and `documentation/evidence/`.

`portal/docker-compose.yml` is in the ARTIFACT list but must NOT be in the diff:
the rebase dropped this branch's header hunk in favour of `development`'s (T3
case 12, findings note §12), so `git diff development -- portal/docker-compose.yml`
is empty. An artifact-list file the branch does not need to touch is not a
failure; a file outside the list is.

The plan file shows as an ADD, not a rename, in this three-dot diff — the old
path never existed on `development`, so the rename is visible only in the
branch's own commits. What must hold on the branch tip is that
`git ls-tree -r --name-only work/sl-closeout documentation/evidence/stack-layers/`
lists the OTHER items' plans (`sl-colo-portal`, `sl-inference-split`,
`sl-manifest`) and **nothing of this item's** — the rebase's rename detection
tried to drag all three into `sl-closeout/` and was refused. **FAIL** if any of
those three moved.

`ruff check .` must print `All checks passed!`.

Hook evidence: the commits on this branch were made with `core.hooksPath
.githooks` active and without `--no-verify`. Confirm it from the attestation
ledger in the shared git dir — `git rev-parse --git-common-dir`, then
`hook-attest.log` — which must carry a line whose first field is the tree of
each commit on this branch (`git rev-parse <sha>^{tree}`).

**FAIL** on any file outside that list, on any ruff finding, or on a commit
whose tree has no attestation line.

## T10 - what was REMOVED

"Nothing true was silently lost" is only checkable against the previous version.

```bash
git diff development...work/sl-closeout | grep "^-" | grep -v "^---"
```

For every deleted line, one of these must hold, and you should be able to say
which:

- it was replaced in place by a corrected statement (the seventeen findings);
- it was a variable nothing reads, and the deletion is covered by T5;
- it was the orphan volume declaration (T6);
- it was reflowed — same meaning, different line breaks.

**FAIL** if a deleted line carried information that now exists nowhere. In
particular: each removed `BACKUP_CRON` / `OPENWEBUI_BACKUP_CRON` /
`LC_BACKUP_CRON` / `LITELLM_BACKUP_CRON` line must have left behind a comment
telling the reader what to set instead; the old
`# NOTE: the gateway runs PERMISSIVE ...` block must have been REPLACED, not
merely dropped; and nothing may have been removed from `CLEANUP-PLAN.md` except
the `IN EXECUTION` status sentence, which the new header replaces.

## T11 - the new criterion: no root document still presents CLEANUP-PLAN.md as live

This is the eighteenth error and the reason the item was reopened: attempt 1
marked `CLEANUP-PLAN.md` CLOSED while two root documents still pointed at it as
the living plan. Closing a plan is two edits, not one.

1. The anchor's own check, run unbounded from a checkout of the branch:

```bash
git grep -n -i "living" -- README.md CLAUDE.md
```

   **No line about CLEANUP-PLAN may appear.** On this branch the command returns
   exactly one line, `README.md:107` — "one backup sidecar living **in its own
   plane project**" — which is about backup sidecars and has nothing to do with
   the plan. Any hit that names `CLEANUP-PLAN` is a **FAIL**.

2. Read the two rewritten pointers and check they describe the file the new
   header actually describes:

```bash
git show work/sl-closeout:CLAUDE.md | sed -n '212,218p'
git show work/sl-closeout:README.md | sed -n '158p'
git show work/sl-closeout:CLEANUP-PLAN.md | sed -n '9,16p'
```

   Both must say the file is the CLOSED v3 record rather than a worklist, and
   both must name the successor (`stack-layers`, in the plan store). **FAIL** if
   either still implies live work, if either points at a successor path that
   does not exist (check
   `../documentation-plans-ai-stack/implementation-guide/stack-layers/`), or if
   a pointer describes the file differently from the file's own `**Status:**`
   line.

3. Sweep for the same SHAPE, not just the same word — a pointer that routes a
   reader to the closed plan for a decision that has moved:

```bash
git grep -n "CLEANUP-PLAN" -- README.md CLAUDE.md scripts/README.md agent-org/README.md documentation/implementation-guide/README.md SECURITY.md
```

   Expect: `CLAUDE.md:27` and `SECURITY.md:7` (historical attributions of work
   done on a date — correct as they stand), `SECURITY.md:24` (a live deferred
   item, D-1, still routed through the closed plan — **out of this item's
   artifact list**, recorded in the findings note §9, do NOT fail on it),
   `scripts/README.md:15` (rewritten here: the D-12 generator moved to
   `stack-layers`, and the line must no longer read "wire-or-demote ="),
   `documentation/implementation-guide/README.md:58` (the stack-layers row,
   which names what it supersedes — correct). **FAIL** if any line in an
   artifact-list file still presents a CLEANUP-PLAN item as the live place for
   an open decision.

PASS = the grep returns nothing about CLEANUP-PLAN, both pointers describe the
closed record and name the successor, and no artifact-list file routes a live
decision through it.

## T12 - the house path and the audit-note header

1. **Path.** The plan must be at `documentation/evidence/sl-closeout/test-plan.md`
   — the pattern `documentation/evidence/README.md` states and that
   `gate5d/`, `mmthread/`, `passplan/` and a dozen others already follow
   (stack-layers `DECISIONS.md` D16).

```bash
git ls-tree -r --name-only work/sl-closeout documentation/evidence/ | grep -i sl-closeout
```

   Expect exactly `documentation/evidence/sl-closeout/test-plan.md` and nothing
   under `documentation/evidence/stack-layers/`. Then check the file has no
   stale self-reference:
   `git show work/sl-closeout:documentation/evidence/sl-closeout/test-plan.md | grep -n "stack-layers/sl-closeout-test-plan"`
   must return nothing. **FAIL** on a plan still at the feature-directory path,
   on both copies existing, or on a self-reference to the old path.

2. **Audit-note header.** `git show work/sl-closeout:documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md | head -10`
   must carry a header pointing readers at the findings sink for the two
   `[agent]` figures the branch disproved, naming BOTH corrections: 8 checks ->
   10, and 866 -> 865. Verify each against the source rather than against the
   header:

```bash
git show work/sl-closeout:.githooks/pre-commit | grep -c "powershell.exe -NoProfile"
python -c "import ast,pathlib; print(sum(1 for p in pathlib.Path('agent-org/agent-bridge/tests').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')))"
```

   10 and 865. **FAIL** if the header is absent, if it names only one of the two
   figures, if it states a number that does not match the recount, or if it
   edits the audit note's FINDINGS themselves — the note is the dated record of
   what the audit said and its body must still say 8 and 866, with the header
   and the sink carrying the correction.

PASS = the plan is at the house path with no stale copy or self-reference, and
the audit note carries a header naming both disproved figures correctly.
