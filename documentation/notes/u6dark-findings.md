# U6 dark-factory mode — findings sink (`u6dark`, 2026-08-30)

Item: U6's first three clauses — andon-condition config; `dark` vs `attended` gate
profiles; auto-passed gates leave audit records. (The fourth clause, recall-informed
briefs, is already merged as 3bdf7a8 and was not touched.)

Branch `work/u6dark`, worktree `.claude/worktrees/wt-u6dark`. **Not merged, not pushed.**

Everything below was checked by running the command or reading the file named. Where a
claim is a hypothesis it says so.

---

## 1. What was built, and what proves it

| Artifact | What it is |
|---|---|
| `scripts/agent-harness/harness.config.json` | `andon` block (5 conditions as data), `gate_profiles` block (`attended`/`dark`), `pipeline.gate_profile` — which REPLACED `pipeline.human_gates` |
| `scripts/agent-harness/andon.ps1` | the board: 5 executable predicates, `-Evaluate` / `-List` / `-Baseline`, exit 6 unless the board is `clear` |
| `scripts/agent-harness/gate-audit.ps1` | the append-only gate ledger and `Test-GateAuditComplete` — the executable definition of "complete" |
| `scripts/agent-harness/queue.ps1` | both gates wired: auto-pass under `dark` only on a `clear` board, a refusal record otherwise, a record on every pass either way |
| `scripts/agent-harness/config.ps1` / `config.py` | gate resolution in both readers |
| `scripts/agent-harness/drill-dark-factory.ps1` | the validation: **184 checks, 0 failed** |
| `scripts/agent-harness/test_gate_profiles.py` | 24 tests incl. the PowerShell↔Python anti-drift test and the two doc-vs-code checks of §11.4 |
| `scripts/claude-sessions-bridge/test_queue_narration.py` | 6 tests pinning the one consumer that narrates a gate transition to a human (§8.7) |

Evidence: `drill-dark-factory.ps1` → `184 checks, 0 failed` (exit 0) — 60 at first, 96
after the audit round of §8, 121 after the thinned-board round of §9, 141 after the
`on_fire` round of §10, 184 after the `on_indeterminate` round of §11.
`python -m pytest scripts/agent-harness -q` → `129 passed` **in a checkout with a live
queue** (125 after the §10 round, 121 after §9, 117 before it); in a plain clone it is two fewer passed and two
skipped, and the two are the ones that ask the real queue:
`test_anchor_schema.py::test_every_queued_anchor_validates` (skips on "no queued anchors
under …") and `test_scope_node.py::test_the_live_queue_projects_without_raising` (skips on
"no live queue in this checkout"). Was 105 before this work. The earlier note said "117
passed" flat, which made a number depend on an unstated environment — a verifier running
it in a clone got `115 passed, 2 skipped`, and both skip guards were then read here.
`python -m pytest scripts/claude-sessions-bridge -q` → `130 passed` (was 124).
`python -m ruff check .` → `All checks passed!`

## 2. The andon conditions, and where each came from

None was invented. Each cites an incident from this run's own record, and
`test_gate_profiles.py::test_every_andon_condition_is_fully_declared` fails if one does
not.

| id | incident |
|---|---|
| `operator-checkout-off-branch` | `documentation/notes/drill-rebased-the-work-line-incident.md` — main checkout found detached, mid-rebase, process dead 8 minutes |
| `policy-declared-unread` | `pipeline.human_gates` and `runners.*.status` had zero executable readers; `Resolve-RoleTarget` zero executable callers |
| `git-error-swallowed` | `Invoke-DrillGit` swallowed every git error — a proven contributing defect in the same incident |
| `work-branch-on-remote` | DECISIONS 2026-08-30 "THE ORCHESTRATOR'S OWN THREE ERRORS" #3 — eleven `work/*` branches reached `origin` under an authorisation nobody gave |
| `protected-ref-moved` | PLAN §C.2 class 4 — promoting anything to `main` is the operator's |

### What I am deliberately NOT covering, and why

- **A claim crossing a tier unverified (§0 A9).** The orchestrator did this twice this
  run and one relay turned a TRUE statement FALSE. I could not find a mechanically
  decidable form of it. Any detector I could write would be prose ("check whether the
  claim was verified"), and per §0 A6 a condition whose detection is prose is FALSIFIED,
  not implemented. Left out rather than shipped as a stub.
- **The class-4 personal-plane boundary.** A real detector must either query the live
  plane — which this item is not permitted to touch, and the plane must still hold zero
  rows when I finish — or rest on a pattern list of "personal write sites", which is the
  enumerate-and-patch shape DECISIONS 2026-08-30 records as losing. Not shipped.
- **The general "guard that cannot fail" class.** `git-error-swallowed` catches ONE
  mechanically decidable shape: a git CALL SITE whose result is not checked within
  `check_window_lines` lines after it. It does NOT catch an assertion that matches zero
  lines of the file it inspects, a `Test-Path` guard that skips itself, or a check whose
  exit code is discarded by its CALLER. Those are separate detectors nobody has written.
- **`git-error-swallowed`: what the window still misses, stated exactly** (rewritten
  2026-08-30 in the §8 audit round). The unit is now the call site, not the function
  body, and code OUTSIDE any function is scanned — both were real misses. What remains:
  - a check counts if it appears **anywhere in the next `check_window_lines` lines**,
    even if it is checking something else. A `throw` on an unrelated condition three
    lines below a `git push` still clears that push. Narrowing further would need to
    know which variable the check reads, which is data flow, not a scan.
  - a function declared **indented** (nested) is attributed to its enclosing region, so
    its call sites are judged against the enclosing region's line numbers. This bullet
    said "every function in the scanned families is at column 0" and that was false:
    `scripts/checks/smoke-agent-memory.ps1:131` declares `Invoke-Door` indented. It is
    the only one in the two default globs (`grep -n "^[ 	]\+function "` over both,
    2026-08-30) — but "there are none" and "there is one, here" are different sentences
    and only the second was checked.
  - **here-strings** (`@" ... "@`) are not tracked by the noise stripper, so a `git`
    inside one is still seen. That is a false POSITIVE, which is loud, not a miss.
    `andon.ps1` claimed "no scanned file uses one"; `scripts/checks/test-quartz4-offline.ps1`
    is in the default glob and has **eight** (lines 145, 185, 251, 267, 287, 327, 399,
    415 — all SQL for `psql`). None contains the word `git`, so the limitation has cost
    nothing yet; that is a fact about their contents, not a property of the scan, and the
    claim about the corpus had never been put to the corpus.
  - it says nothing about a call whose result IS checked and then ignored.
- **`policy-declared-unread` scope.** It is a path scan over dotted config paths, not a
  data-flow analysis. It proves a key is *referenced*, not that it is *honoured*. The
  match is ANCHORED as of the §8 round; unanchored it reported "read" for any key that is
  a prefix of a live one. Roots are `pipeline` and `andon` — policy knobs, including the
  board's own block, which is where the dead `andon.raise.ledger` was hiding. Named data
  COLLECTIONS keyed by name (`profiles`, `gate_profiles`) are not roots: they are consumed
  generically by a loop, so the scan would say nothing true about them.
  `andon.conditions` is a leaf — the list is read whole — so the scan asks only whether
  the list is read, never whether each condition in it is.

## 3. THE BOARD IS RED ON THIS REPOSITORY RIGHT NOW

`powershell -NoProfile -NonInteractive -File scripts/agent-harness/andon.ps1 -Evaluate
-RunBranch work/u6dark`, run in this worktree on 2026-08-30, exit 6 — **verbatim, whole,
and in the order the tool prints it**:

```
ANDON BOARD: RAISED
  [ok           ] operator-checkout-off-branch   D:\Open WebUI\ai-stack is on 'refactor/ai-stack-cleanup' with no operation in progress
  [ok           ] policy-declared-unread         all 7 policy keys under pipeline, andon are read by harness sources
  [fire         ] git-error-swallowed            git errors are swallowed at 18 call site(s)
      - scripts/agent-harness/new-worktree.ps1:161 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/new-worktree.ps1:184 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/queue.ps1:763 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/queue.ps1:818 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/remove-worktree.ps1:92 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/remove-worktree.ps1:111 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/remove-worktree.ps1:115 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/remove-worktree.ps1:118 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/remove-worktree.ps1:158 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/verify-merge-protocol.ps1:49 in Invoke-DrillGit() runs git and does not check the result within 5 line(s)
      - scripts/agent-harness/verify-merge-protocol.ps1:53 in Get-DrillGit() runs git and does not check the result within 5 line(s)
      - scripts/checks/check-env-file-scope.ps1:43 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/checks/check-env-file-scope.ps1:101 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/checks/check-hook-attestation.ps1:67 in Invoke-GitLines() runs git and does not check the result within 5 line(s)
      - scripts/checks/check-project-configs.ps1:18 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/checks/check-staged-secrets.ps1:27 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/checks/check-staged-secrets.ps1:30 in (top level) runs git and does not check the result within 5 line(s)
      - scripts/checks/validate-lineendings.ps1:16 in (top level) runs git and does not check the result within 5 line(s)
  [ok           ] work-branch-on-remote          checked 1 branch(es); none is on a remote
  [indeterminate] protected-ref-moved            no baseline recorded - run: andon.ps1 -Baseline (expected at D:\Open WebUI\ai-stack\.git\agent-worktrees\audit\andon-baseline.json)
  coverage: 5 declared, 5 evaluated, 0 switched off, 0 of 5 required MISSING
ANDON RAISED: git-error-swallowed, protected-ref-moved
```

**That block used to be abridged and RE-ORDERED.** It put
`verify-merge-protocol.ps1:49/:53` at the top under a "...15 more" elision and shortened
the detail lines, while being labelled as the command's output. The real order is
`Sort-Object FullName` over the two globs: the first two sites are
`new-worktree.ps1:161` and `:184`, and the merge-protocol drill's two functions are
**10th and 11th of 18** — the abridgement inverted that and made the incident's own
functions read as the detector's headline. A fenced block presented as command output
must BE the output; if it is too long, elide with the elision visible and the order
intact, or do not fence it. (`b679c04`'s commit message carries the same error in words —
"its two functions are still what `git-error-swallowed` names first" — and is left as
written, per the rule in this file's own §8.6: editing a landed message to hide an error
is the opposite of an audit trail. §8.8 below is corrected.)

That run is the branch-scoped question,
which is what a gate asks. **Run bare** (`-Evaluate`, no `-RunBranch`),
`work-branch-on-remote` asks the broader question and fires on the eleven `work/*`
branches that reached `origin` on 2026-08-30: `work/dfu-u4, work/dfu-u4-lc, work/pod-key,
work/u3gym, work/u4bidir, work/u4oracle, work/u4quad, work/u5judge, work/u5pplane,
work/u5proxy, work/u6recall`. Both readings are deliberate; only the narrow one gates a
run, because `Invoke-AutoGate` passes `-RunBranches @($item.branch)`. A `dark` run here is
therefore stopped by `git-error-swallowed` and `protected-ref-moved`, not by the eleven.

**This is the mechanism working, not a false alarm.** Every fired condition names the
exact defects the 2026-08-30 record describes. The practical consequence is worth
stating plainly: **a `dark` run cannot auto-pass a gate in this repository until those
are cleared.** That is the andon cord doing its job — but it means "dark-factory mode is
available" is a false way to describe the current state. It is *built and proven*; the
line it would run on is *stopped*.

Clearing them is not this item's work:
- `Invoke-DrillGit` / `Get-DrillGit` belong to the merge-protocol drill and to the
  incident note that already owns them.
- The eleven remote branches are the operator's call. DECISIONS 2026-08-30 says
  explicitly they were NOT deleted because unilateral deletion would be a second
  unauthorised outward action. I did not delete them either.
- `protected-ref-moved` clears with `andon.ps1 -Baseline`, which writes to the shared
  state dir. I did not run it against the real state dir — it is a run-scoped action and
  writing it from a builder's worktree would put a baseline there that no run owns.

## 4. Findings about OTHER things (the reason this file exists)

### 4.1 `check-hook-attestation.ps1` reports INACTIVE — exit 0 — for a branch that does not exist, with a confident wrong reason

Reproduced directly:

```
> scripts/checks/check-hook-attestation.ps1 -Branch "work/does-not-exist-at-all" -Base dev -RepoRoot <repo>
Hook attestation: INACTIVE for 'work/does-not-exist-at-all'.
  That branch's .githooks/pre-commit does not record attestations, so its
  commits cannot have them. Nothing was bypassed - the mechanism is simply
  not present on this branch yet.
EXIT=0
```

The stated reason is fabricated: the branch has no `.githooks/pre-commit` because the
branch does not exist. `queue.ps1 -Submit` treats anything other than exit 1 as a pass
(`if ($attestExit -eq 1) { Die ... }`), so a submit whose branch the checker cannot
resolve satisfies the hook gate silently.

**Scope, honestly:** in ordinary use this is latent, not live. `-Submit` also runs
`git rev-parse $Branch` in the caller's cwd and dies if that fails, and the attestation
runs against `$PSScriptRoot/../..`, which in both the main checkout and a worktree shares
the same ref store. The two diverge only when queue.ps1 is driven from a repository other
than its own — which is exactly what my drill does, and how I noticed. **The cause of the
INACTIVE verdict is established by reproduction; whether any real submit has ever hit it
is NOT established.**

Why it belongs here: the failure shape is "the check could not evaluate, and reported a
pass with an invented explanation". That is the same shape as the `git -C ""` and
`Invoke-DrillGit` degradations, and it is the shape `on_indeterminate: halt` exists to
refuse.

### 4.2 My own first two predicates were themselves checks that checked nothing

Recorded because it is the most useful thing this item learned, and because a reader
should be able to see that the shape recurs even when you are actively hunting it.

1. `git-error-swallowed`, first version, scanned raw text for `git\s`. It reported **four
   functions, all four false positives** — the word "git" inside a comment ("Thin policy
   wrapper over the git fact") — while MISSING `Invoke-DrillGit`, the function the whole
   condition was built from, because it calls `git.exe`. A detector that fires on nothing
   and misses the thing it was built for. Fixed by stripping comments and string literals
   before scanning, and by matching `git(\.exe)?`.
2. `policy-declared-unread`, first version, matched the bare key name in raw source. It
   could not reproduce the `human_gates` incident, because `andon.ps1`'s own header
   discusses `pipeline.human_gates` — the detector's documentation made the defect look
   read. Fixed by stripping comments (but NOT strings — config keys are read *through*
   strings) and matching the dotted path rather than the bare key.

Both were caught by the drill's RED-before-GREEN requirement, not by re-reading the code.
Neither would have been caught by a drill that only asserted GREEN on a clean tree.

### 4.3 The merge-protocol drill was NOT run, deliberately

`verify-merge-protocol.ps1` exercises `queue.ps1`, which this item changed, so not
running it is a real gap in coverage and is named as one rather than glossed.

I did not run it because it operates on the operator's live checkout and is the script
that, on 2026-08-30, left that checkout detached mid-rebase on the live work line. Its
two proven contributing defects are still present (they are what `git-error-swallowed`
fires on). Running it to validate my change would have risked the exact incident the
first condition on my board exists to detect.

What I did instead, and its limits: I read every gate assertion in that drill
(`grep -n "Approve|ConfirmAnchor|exit 5|LASTEXITCODE -eq"`). All of them assert
`attended` behaviour — exit 5 on an unconfirmed anchor, exit 4 for self-service, human
`-By` values. The shipped default is `attended` and those code paths are unchanged except
for an added ledger write; `drill-dark-factory.ps1` part E asserts the same two
behaviours directly. **That is an argument, not a test run.** A tester with a safe way to
run the merge-protocol drill should do so.

### 4.4 `-VerifyAudit` had the same defect I was building against, until I fixed it

First version returned exit 0 when it found items it could not audit (items predating the
ledger), listing them as "unaudited". That is coverage it does not have, reported as
green — the skip-counts-as-a-pass shape. Now exit 7, and an item named explicitly with
`-Id` that cannot be audited is a finding (exit 1), not a shrug. Proven both ways in the
drill (C4).

### 4.5 Eighteen unchecked git call sites across `scripts/checks` and `scripts/agent-harness`

Once `git-error-swallowed` became a CALL-SITE scan (§8.5), it reports 18 sites in the two
default globs, up from 2 functions. They are not noise; a sample was read line by line and
each was a genuine unchecked call, several with the "check that passes while checking
nothing" shape this board exists for:

| site | what a git failure produces |
|---|---|
| `check-staged-secrets.ps1:27,30` | `git ls-files` / `git diff --cached` fail → `$staged` empty → "nothing staged - skip" → **the secret guard passes vacuously** |
| `validate-lineendings.ps1:16` | `git ls-files '*.sh'` fails → `SUCCESS: No tracked shell scripts to check` |
| `check-project-configs.ps1:18` | `git diff --cached` fails → "nothing staged - skip", exit 0 |
| `check-env-file-scope.ps1:43,101` | `git rev-parse` fails → falls back to the CURRENT directory as the repo root; `git diff --cached` fails → "no compose files staged - skipped" |
| `verify-merge-protocol.ps1:49,53` | the 2026-08-30 incident's own two functions |
| `check-hook-attestation.ps1:67` | `Invoke-GitLines`, an adapter of the same shape as `git-io.ps1` |
| `new-worktree.ps1:161,184`, `remove-worktree.ps1:92,111,115,118,158`, `queue.ps1:772,827` | listings that silently read as empty |

**Not mine to fix, and deliberately not fixed here.** Every one is a live pre-commit or
harness script; changing nine files' git handling inside an audit-fix item is the
enumerate-and-patch shape DECISIONS 2026-08-30 records as losing, and none of it is what
this item was anchored to. The `check-hook-attestation.ps1:67` case is arguably the same
adapter-by-contract exemption `git-io.ps1` has (`exclude_files`), with one difference: it
does not state that contract in the file, so it is left flagged rather than excused.

### 4.6 `queue.ps1` contains one non-ASCII byte

`queue.ps1:301` holds a UTF-8 `§` (`0xC2 0xA7`) inside a comment — the only non-ASCII
sequence in the file. CLAUDE.md requires ASCII no-BOM for scripts PowerShell 5.1 parses;
PS 5.1 reads the file as ANSI, so that comment renders as `Â§C.1`. Harmless to execution
(it is a comment) and PRE-EXISTING — it is present at `HEAD~1` — so it is recorded rather
than swept into this commit's diff. Found because a byte-exact edit tool refused the file.

## 5. Deliberate design decisions, with their reasons

- **`gate_profiles` is a separate block from `profiles`, not another key inside it.**
  `profiles` is keyed by ROLE and every reader iterates the three roles; a gate is not a
  role, and folding them in would make `resolve_role`/`describe_profile` iterate keys that
  are not roles. The stronger reason is coupling: an operator switching to a cheaper MODEL
  profile must not thereby also remove the humans from the gates. Two unrelated decisions
  must not share one name — the same reasoning that produced `profile_locked`.
- **`pipeline.human_gates` was REPLACED, not kept alongside.** It had zero executable
  readers in either language (verified before the change), so keeping it would have left
  the new `policy-declared-unread` condition firing on the config that ships it. The
  seed's own note — that the role→state→duty table stays in queue.ps1 because it is
  behaviour — is preserved verbatim in the replacement's `_note`.
- **The default gate profile is `attended`.** A typo that lands on the default must leave
  a human at the gate, not remove one. `test_the_shipped_default_is_attended` pins it.
- **`auto:` is reserved in BOTH directions.** A human `-By` inside it is refused (exit 4)
  so a person cannot hide behind the machine; the auto path never signs as a person, so a
  record cannot read as approval that never happened.
- **`on_indeterminate` defaults to `halt`, and an unavailable board refuses the gate.**
  A condition that could not be evaluated has not passed.
- **No Mattermost raise knob.** Declaring an output nothing writes to would trip this
  work's own `policy-declared-unread` condition. The raise goes to the ledger and stderr,
  both of which are read.

## 6. Validation: what was REAL and what was NOT

The U6 column says "Gym:". **This did not run in `ai-orchestration-gym`, and calling it a
gym run would be the over-claim §C.7 exists to prevent.**

- **REAL:** real git repositories, the real `andon.ps1`, the real `queue.ps1`, the real
  `gate-audit.ps1`, the real config loaders. Every state transition in parts B–E is
  produced by the shipped tools. The `git-error-swallowed` RED is measured against the
  REAL repository and names the real incident's function. 121 checks, 0 failed (60 at the
  first round, 96 after the §8 audit round, 121 after the §9 thinned-board round).
- **NOT the gym:** `ai-orchestration-gym`'s runner drives the agent-org bridge against
  GitHub with a real App installation and mutates remote repositories. U6's mechanism is
  the HARNESS pipeline, which has no gym scenario at all, and a gym run would require
  remote mutations this session was not granted. The "unattended run" here is the
  pipeline driven end to end with nobody at either gate — which is exactly what `dark`
  means — not a multi-agent arena scenario.
- **NOT a real remote:** the push in the `work-branch-on-remote` test targets a BARE
  REPOSITORY ON DISK under `$env:TEMP`. Nothing left the machine. It produces a real
  `refs/remotes/origin/...`, which is the property the detector asks about.
- **NOT the real planes, queue or ledger:** everything runs in scratch repos with
  `AI_STACK_WORKTREE_STATE` and `AI_STACK_HARNESS_CONFIG` redirected.

**Verdict on U6's column: HALF SATISFIED, and it must not be reported as done.** The
mechanism and both halves of the behaviour are proven executably against the real tools;
the word "Gym" in the column is not satisfied. By the rule DECISIONS adopted on
2026-08-30 — a phase is reported DONE only when its *Validated by* column is satisfied and
the evidence is named — this is **U6 clauses 1–3: CODE-COMPLETE, GYM-VALIDATION PARKED**.

## 7. Class-4 compliance for this item

- Nothing merged or promoted to `main`; `main` untouched.
- The personal plane was not read, written, or queried. It held zero rows before this
  item and this item added none.
- No secret values in any commit, note, log or ledger. The gate ledger records principals
  and gate names only.
- Nothing irreversible: no force-push, no history rewrite, no volume or database deletion.
  Every scratch repository is under `$env:TEMP` and removed by the drill.
- No external spend, no external service call, **no push to any remote** (the eleven
  pre-existing remote branches were left exactly as found, and `work/u6dark` is local
  only). The command that establishes *that one fact* is
  `andon.ps1 -Evaluate -Only work-branch-on-remote -RunBranch work/u6dark`, which exits 0
  with `checked 1 branch(es); none is on a remote`. **The whole board is a different
  question and answers RAISED** — see §8.6 for the correction, and §3 for why.

---

## 8. THE AUDIT ROUND — seven refutations against the audit layer, adjudicated individually

The machinery of §1–§6 was independently confirmed: all five conditions fire on a
constructed instance and stay quiet on a clean repository; the halt works end to end at the
real gate for all five (exit 6, item parked, the fired condition on the console and in a
`decision=refused` ledger record); the negative control passes. What was refuted was the
AUDIT layer — what the record SAYS. Each refutation below is adjudicated on its own, with
the RED reproduced before the fix, because adjacency to a verified claim is not evidence
(DECISIONS 2026-08-30, U4 clause 3).

### 8.1 DECISIVE — the board read "clear" when nothing was checked

**REPRODUCED.** On a scratch repository left genuinely DETACHED, with
`andon.enabled: false`, `andon.ps1 -Evaluate` printed `board=clear, conditions=5` and
exited **0**. The per-condition `disabled` status was computed and then discarded by
`Invoke-AndonForGate`, so the ledger record read `andon.status=clear conditions=5` —
byte-indistinguishable from five conditions that looked and found nothing. That is the
rule stated at the top of `gate-audit.ps1`, broken by `gate-audit.ps1`.

**FIXED.** The verdict now carries `coverage {declared, evaluated, disabled,
disabled_ids}` and the repository it looked at, and `clear` is defined narrowly:
≥1 evaluated, none halted, none switched off (§10 narrows it once more, to none FIRED). `partial` and `not-evaluated` are their own
states and both exit 6 (§9 adds `incomplete`, and adds `required` / `missing` /
`missing_ids` to the coverage: every counter here is relative to what the config
DECLARED). The gate record carries all of it, `Test-GateAuditComplete`
re-derives the claim from the counters instead of trusting the word, and
`Format-GateRecord` prints `andon clear; 5/5 evaluated` rather than `andon clear`.

**GREEN:** same command, same repository → `board=not-evaluated`, exit 6, `declared=5
evaluated=0`. Drill: *"andon.enabled=false is NOT-EVALUATED, never 'clear'"*, *"the verdict
states its COVERAGE"*, and three ledger tampers in step C (`evaluated` forced to 0; the
coverage fields stripped; the old `status` tamper) all go RED.

### 8.2 DECISIVE — the documented revert path was a silent kill switch

**REPRODUCED.** With the `andon` block deleted, a `dark` `-Submit` on a DETACHED checkout
returned **exit 0**, ready-to-test, ledger `andon.status=clear conditions=0`, and
`-VerifyAudit` printed COMPLETE. The sentence in this file — *"REVERT: `andon.enabled:
false`, or delete the `andon` block — every gate behaves as it did before"* — was FALSE
under `dark`: it did not restore prior behaviour, it removed the only thing between an
unattended run and its own approval.

**FIXED**, both ways. Mechanically: a switched-off board is `not-evaluated` and an absent
one is `incomplete` (§9 gave board removal its own state, naming all five ids); either way
the gate refuses (exit 6) and the refusal is in the ledger with `evaluated=0`.
Documentally: **the revert is `pipeline.gate_profile: attended`** — the switch that puts a
human back at the gate, and the configured DEFAULT rather than a lock (§9.2 item 6) — and
the corrected sentence is in the DECISIONS entry below, in `MODULE.md` and in `README.md`.

**GREEN:** drill step F drives the real gate for three cases — `andon.enabled=false`, the
block deleted, and one condition switched off — and asserts exit 6, the item parked at
`anchor-draft`, and a ledger refusal recording `not-evaluated` / `incomplete` /
`partial` with `evaluated` 0 / 0 / 4.

**WHAT THIS DID NOT CLOSE, and §9 does:** neither of those is what somebody actually does
to a condition that is in their way. They delete its ENTRY.

### 8.3 The dead-knob detector shipped a dead knob, in its own block

**REPRODUCED.** `grep -rn` over `scripts/**` for `andon.raise.ledger`: **zero** executable
readers. Only `andon.raise.stderr` was read (one site). The condition's roots were
`["pipeline"]`, so it never looked at its own block.

**FIXED at the reader, not the knob**, and the reason matters: the ledger write must not be
optional, because a run able to switch off the record of its own halt is the failure this
board exists to prevent. So `andon.raise.ledger` is REMOVED, with a `_ledger_note` in its
place saying the raise always goes to the ledger, and `roots` is now
`["pipeline", "andon"]`.

**GREEN:** drill *"a dead knob in the ANDON block is caught by the andon board's own
condition"* — putting `ledger: true` back makes the board fire and name
`andon.raise.ledger`.

### 8.4 The same detector could not detect — unanchored substring match

**REPRODUCED.** With `pipeline` replaced by `{claim_ttl, anchor_require, gate_profil, a}`,
the predicate printed *"ok - all 4 policy keys under pipeline are read by harness
sources"*. Every one is a substring of a live path (`pipeline.claim_ttl_minutes`,
`pipeline.anchor_required`, `pipeline.gate_profile`), and no line mentions any of them.

**FIXED.** The match is anchored — `(?<![\w.])path(?![\w.])` — and the walk reports the
SHALLOWEST unread node: a container counts as read when its own path OR any leaf beneath
it is matched. That is what keeps `andon.raise` alive on the strength of
`andon.raise.stderr` while still reporting `pipeline.human_gates`, the real incident, as
the block it was rather than as two leaves.

**GREEN:** same four keys now fire and all four are named; the pre-U6 `human_gates` case
still fires and is still named exactly.

### 8.5 The git-error detector's disclosed limits were incomplete

**REPRODUCED, both halves.** (a) A fixture whose only content is
`& git.exe push origin HEAD | Out-Null` at file scope — no function anywhere — reported
*"ok: every git-calling function can report a failure"*, and the live in-glob instance
`scripts/checks/check-project-configs.ps1:18` was unflagged. (b) A function whose first
line is `if (-not $Branch) { throw "no branch" }` and whose sixth is a swallowed
`git push` reported ok: the body-wide amnesty.

**FIXED.** Regions are now top-level functions **plus a `(top level)` region** for
everything outside them, and the unit of judgement is the **call site**: a check counts
only if it falls within `check_window_lines` (default 5) lines AFTER the call. Both
fixtures now fire, and `check-project-configs.ps1:18` is named. The residual limits are
listed in §2 above rather than left implied, and the cost was measured rather than
assumed — see §4.5 for what the 18 reported sites actually are.

### 8.6 Claims that commands contradict

**(a) The commit message's last line — REFUTATION UPHELD.** It reads *"work/u6dark is local
only (andon.ps1 -Evaluate -RunBranch work/u6dark: CLEAR)"*. Run, that command prints
**ANDON BOARD: RAISED** and exits 6. The FACT is true and §7 words it correctly; the
citation named a command and reported a verdict it does not give, in the trail §C.7 says
the operator reads instead of the diffs. The command that gives that verdict is
`andon.ps1 -Evaluate -Only work-branch-on-remote -RunBranch work/u6dark` → exit 0,
`checked 1 branch(es); none is on a remote`. §7 is corrected to cite it, and the correction
is stated in this commit's message; the earlier commit message is left as it stands,
because rewriting a message to hide an error is the opposite of an audit trail.

**(b) The drill's hermeticity claim — REFUTATION UPHELD as to the CLAIM, REFUTED as to the
MECHANISM.** The claim *"runs entirely in scratch repositories under `$env:TEMP` and never
touches the operator's checkout"* was false: step A3's last case deliberately scans THIS
checkout's `.ps1` files, read-only, so the detector is shown naming the incident's own
function in shipped code. But the proposed mechanism — that in fixtures B/D/D2/E
`params.repo=""` resolves via `Get-MainCheckout` to `D:\Open WebUI\ai-stack` — does not
hold: `Invoke-Queue` does `Push-Location $fix.repo`, and the working directory propagates
through both child processes (verified in a scratch repo: `queue.ps1` → `andon.ps1` two
levels deep resolved to the fixture, not the operator's checkout). Independently, step B's
board was CLEAR while the operator's repository is RED, which it could not have been had it
been looking there. **Fixed anyway, because a property that holds only through inherited
cwd is not a property:** `New-DarkFixture` now pins `params.repo` to the fixture repo, the
header states the isolation exactly (every WRITE scratch; one named READ of a real
repository), and step B asserts from the LEDGER that the board the gate consulted was
looking at a path under `$env:TEMP`.

**(c) The README's `work-branch-on-remote` sentence — REFUTATION UPHELD.** *"a dark run
will refuse to auto-pass until [the eleven] are cleared"* — conclusion right for this
repository, mechanism wrong: `Invoke-AutoGate` passes `-RunBranches @($item.branch)` and
the predicate narrows to it, so the other ten do not block a run. The README and §3 now
state both readings and which one gates.

### 8.7 Two undisclosed gaps — both CLOSED rather than merely disclosed

**(a) "COMPLETE" was narrower than it read.** `Get-CrossedGates` counted the anchor gate as
crossed only if the item CARRIED an anchor, so with `pipeline.anchor_required: false` an
item ran end to end with no anchor, held one ledger record, and `-VerifyAudit` printed
*"COMPLETE — every crossed gate has a record"*, exit 0. The shipped default is
`anchor_required: true`, so it was never live — but "complete" meant "every gate this item
happened to cross". **Closed for the default:** with `anchor_required: true`, an item past
`anchor-draft` has crossed the anchor gate whether or not an anchor survives on it, so an
anchorless item is a FINDING (exit 1). **Disclosed for the other setting:** with
`anchor_required: false`, `-VerifyAudit` prints that the anchor gate is not a gate for an
anchorless item, and prints the scope of "crossed" with every green. Drill step G proves
both directions on one item.

**(b) The operator-facing narration had no principal.** `bridge.py poll_queue()` is the one
existing consumer that narrates a gate transition into a human channel, and for the state a
`dark` pre-review auto-pass produces it emitted `📋 **x1** — released for review.` —
passive, no principal, and **byte-identical** to the attended case (verified by rendering
the pre-fix template for both: identical strings). Its audit event
`{event, from, to}` carried no principal either. It is DORMANT — every real queue item has
`thread=""`, so `poll_queue` returns before posting — which is exactly why it is pinned by
a test rather than left as a comment: a dormant surface is one `-Thread` argument away.
**Closed:** `gate_principal_note()` renders "⚠️ **No human saw this gate** — auto-passed by
`auto:dark` under gate profile `dark`." for an auto pass, "Released by **profnovice**." for
a human one, and an explicit "names no principal … do not read it as an approval" for an
item that records neither. The audit event carries `gate`, `gate_kind` and `principal`.
`test_queue_narration.py` (6 tests) pins it, including a test derived FROM the state→gate
map rather than a hand-written list, so adding a gate-crossing state without a principal
slot fails. RED proven against the pre-fix `bridge.py`: all 6 fail, and the two renders are
equal.

### 8.8 What this round did NOT change

- No condition was added or removed; the five are the five.
- `attended` behaviour is untouched: step E is unchanged and still passes.
- `verify-merge-protocol.ps1` was still NOT run, for the reason in §4.3. Its two
  functions are still among the 18 sites `git-error-swallowed` reports — but **not
  first**: the order is `Sort-Object FullName`, so `new-worktree.ps1:161` and `:184` come
  first and `verify-merge-protocol.ps1:49/:53` are 10th and 11th. The commit message for
  `b679c04` says "first" and is wrong about it; the run that establishes the real order is
  the verbatim block in §3.
- The eleven remote branches were not deleted and `work/u6dark` was not pushed.
- The 18 call sites of §4.5 were not fixed. They are real, they are other people's files,
  and fixing them is its own item.

## 9. THE THINNED-BOARD ROUND — one real gap, and nine claims commands contradicted

The machinery and the audit layer of §1–§8 were reproduced independently by two verifiers,
each in their own fixture rather than in mine. What survived was **one** defect and a list
of sentences that were narrower, wider or staler than the commands behind them.

### 9.1 DECISIVE — a THINNED board still opened the gates

**The two ways off that §8.2 closed were not the way anyone takes.** An operator or an
agent with a condition in their way does not disable the board; they delete **that
condition's entry** from `andon.conditions`.

**REPRODUCED** on a genuinely detached scratch checkout, `pipeline.gate_profile: dark`,
`andon.enabled: true`, the `andon` block present, every surviving condition keeping its
params — only four of the five entries removed. What the board said, then what the gate did
(**abridged transcript of a five-command repro script, not one command's output** — the
lines are verbatim, the interleaving is mine, and the ledger line is elided at `…`):

```
$ andon.ps1 -Evaluate -RepoRoot <fixture>
ANDON BOARD: CLEAR
  [ok           ] work-branch-on-remote          checked 1 branch(es); none is on a remote
  coverage: 1 declared, 1 evaluated, 0 switched off
  -> exit 0

$ queue.ps1 -Submit -Id thin -Branch work/thin ...
Anchor AUTO-PASSED for 'thin' under gate profile 'dark' - NO HUMAN CONFIRMED IT.
Queued 'thin' for TESTING (branch work/thin -> dev).
  -> exit 0;  item: state=ready-to-test  anchor_confirmed_by=auto:dark

<state>/audit/gates.jsonl
{"schema":2,...,"decision":"passed","kind":"auto","principal":"auto:dark",
 "gate_profile":"dark","andon":{"status":"clear","repo":"…","conditions":1,
 "evaluated":1,"disabled":0,"disabled_ids":[],"fired":[]},"tool":"queue.ps1"}

$ queue.ps1 -VerifyAudit -Id thin
COMPLETE - every gate these item(s) CROSSED has a record, and every record names who
or what passed it.
  -> exit 0
```

Every counter in that record is **true**. `conditions`, `evaluated` and `disabled` are all
relative to what the config declared, and the config declared one. So a thinned board was
neither "absent" nor "switched off" — a third state that reported itself perfectly healthy
with four of five detectors gone. `README.md` and `MODULE.md` said "switching the board off
does not switch the gates back on"; that was true of the two routes enumerated and false of
this one, and under §C.7 that sentence is an audit surface, so it had to become true rather
than narrower.

**AND THE GUARD THAT SHOULD HAVE CAUGHT IT COULD NOT.**
`test_gate_profiles.py::test_every_andon_condition_is_fully_declared` was cited as failing
"if one is not fully declared", but its first line was `assert conds` — non-emptiness.
Demonstrated: with the config thinned 5 → 1, the exact body of that test still passes. The
same vacuity class this whole effort exists to kill, inside the guard protecting the
anti-tamper property.

**FIXED — pin the SET, not its size.** The five required condition ids are declared in
CODE: `config.ps1` `$script:RequiredAndonConditions` and `config.py`
`REQUIRED_ANDON_CONDITIONS`. Not in `harness.config.json`, because a required list kept
beside the conditions is deleted along with the entry it names and the file agrees with
itself either way; in code, retiring a condition is a diff a reviewer sees. This is the
same asymmetry `$script:Predicates` already gives the other direction — the config may not
declare a detector nobody wrote, and now may not silently drop one somebody did. There is
deliberately no environment override.

A board missing any required id is **`incomplete`**: its own board state, the missing ids
NAMED in the verdict, on the console, in the stderr raise and in the ledger record, exit 6,
no auto-pass — the same shape `not-evaluated` and `partial` already had. `incomplete`
outranks the other non-clear words because a verdict from a board that is not the required
board cannot be reported as that board's verdict; the conditions that *are* declared are
still evaluated and still listed, so nothing is hidden by the name. Coverage gains
`required` / `missing` / `missing_ids` (ledger schema 3), and `Test-GateAuditComplete`
re-derives the fact from the record rather than trusting `status` — a record still labelled
`clear` while admitting four missing conditions is a finding, and a schema-2 record that
cannot state the required set at all is a finding, not a pass.

**Board removal now has the right name.** With the whole `andon` block deleted the board is
`incomplete` naming all five, not `not-evaluated` — which also fixes `MODULE.md`'s table
row, which read as covering board removal while covering only "every declared condition
switched off".

**GREEN, RED-proven first.** Drill step H drives the REAL gate for two thinnings — ONE entry
deleted and FOUR deleted — asserting exit 6, the item parked at `anchor-draft`, nothing
signed, a ledger refusal recording `incomplete`, and **the record naming every missing id**.
It also asserts that the OLD counters would have said full coverage (`evaluated=4/4` and
`1/1`, `disabled=0`), so the reason those counters could not catch it survives the fix.
Then the **negative control**, re-run on a fresh fixture built the same way: a full board
still auto-passes at exit 0, still prints `NO HUMAN CONFIRMED IT`, still signs `auto:dark`,
records `0 of 5 required MISSING`, and still verifies COMPLETE — because a fix that refuses
everything is not a fix. RED proof: the same drill file against the pre-fix sources
(`git show HEAD:` into a copy) fails **22** checks, all twelve of step H's halt assertions
among them, while every CONTROL check passes on both sides.

`test_gate_profiles.py` gained the set test, a parameterised red-proof that a thinned board
is a MISSING SET (asserting *in the test* that the old `assert conds` is still satisfied, so
the vacuity cannot come back), a test that a config may neither narrow nor widen the
required set, and the required ids in the PowerShell↔Python anti-drift test.

### 9.2 Claims that commands contradicted

Each was RUN by a verifier and re-run here before being changed.

1. **`andon.ps1`'s `git -C ""` note was widened past its evidence.** It said the empty
   `-C` "silently runs wherever you happen to be and exits 0 — verified 2026-08-30". That
   was verified in BASH. In POWERSHELL — this file's own language and the only way the code
   path is reached — `git -C '' rev-parse --show-toplevel` exits **128**: `fatal: cannot
   change to 'rev-parse': No such file or directory`, direct and splatted (re-run
   2026-08-30). The empty argument is dropped from argv, so `rev-parse` lands in `-C`'s
   slot. The drill incident is real and the refusal stands on its own merits; the comment
   now says which shell the silent behaviour holds in.
2. **`queue.ps1`'s usage said `-VerifyAudit … exit 1 if not`.** Coverage-incomplete exits
   **7** — proven by this item's own drill C and by a verifier against a copy of the live
   queue. The usage was narrower than the tool and read as though 7 were impossible. Now
   `0 complete | 1 findings | 7 items it could not audit`.
3. **A halt's coverage line could never print.** `Stop-OnAndon` guarded it with
   `$andon.PSObject.Properties.Name -contains "evaluated"`, and `$andon` is an
   `[ordered]` hashtable whose PSObject properties are `Count, IsReadOnly, Keys, Values,
   IsFixedSize, SyncRoot, IsSynchronized` — never its keys. **Always false**, so a real dark
   halt reached the operator with no coverage at all: a check that could not fire, inside
   the tool built to refuse checks that cannot fire. Replaced with `Test-AndonField`, which
   handles the hashtable and the PSCustomObject a ledger record parses back into, and the
   missing-ids line is printed beside it.
4. **The §3 fenced block was re-ordered.** See §3: it is now the verbatim, whole output.
   `verify-merge-protocol.ps1:49/:53` are **10th and 11th of 18**, not first;
   `new-worktree.ps1:161` and `:184` are first (`Sort-Object FullName`, deterministic).
   §8.8 is corrected; `b679c04`'s message is left as written, per §8.6.
5. **"No scanned file uses a here-string"** — `scripts/checks/test-quartz4-offline.ps1` is
   in the default glob and has **eight** (lines 145, 185, 251, 267, 287, 327, 399, 415).
   Corrected in `andon.ps1` and in §2.
6. **"Every function in the scanned families is at column 0"** —
   `scripts/checks/smoke-agent-memory.ps1:131` declares `Invoke-Door` indented. It is the
   only one in the two globs, which is a different sentence and the only one now made.
7. **`pytest scripts/agent-harness` → 117 passed** needs a checkout with a live queue. A
   verifier running the pre-§9 tree in a clone got **115 passed, 2 skipped**. Reproduced
   here rather than relayed: the same suite in a checkout with no `agent-worktrees` state
   skips exactly `test_anchor_schema.py:200` ("no queued anchors under …\queue") and
   `test_scope_node.py:210` ("no live queue in this checkout"). The count is therefore two
   lower wherever the state dir is absent — after this round, 119 + 2 rather than 121.
   Stated with its condition in §1 rather than as a bare number.
8. **`MODULE.md`'s `not-evaluated` row** read as covering board removal. Board removal is
   now `incomplete` and has its own row; `not-evaluated` says what it actually covers.
9. **`-GateProfile dark` overrides an attended config for a single call** (verified: same
   item, exit 5 attended, exit 6 dark — drill step I). So
   `pipeline.gate_profile: attended` is the configured DEFAULT, not a lock. Disclosed in
   `README.md`, `MODULE.md` and the DECISIONS entry rather than changed: removing the human
   from a gate being one flag away is the design, and the sentence that mattered was the one
   that let "the revert" be read as "no run can self-pass now".

### 9.3 What this round did NOT change

- No condition was added or removed: the five are the five, and they are now the five the
  code requires.
- `attended` behaviour is untouched — drill step E is unchanged and still passes.
- `verify-merge-protocol.ps1` was still NOT run, for the reason in §4.3.
- The 18 call sites of §4.5 were not fixed; their line numbers in `queue.ps1` moved with
  this diff and the table is updated (763, 818 at the time of §9; 772, 827 after §10 —
  see §10.6).
- The eleven remote branches were not deleted and `work/u6dark` was not pushed.
- U6 clauses 1–3 remain **CODE-COMPLETE, GYM-VALIDATION PARKED**. This round did not run in
  `ai-orchestration-gym` either.

---

## 10. THE `on_fire` ROUND — one real hole, two false universals, one vacuous guard again

The board's membership was made tamper-evident in §9. This round is about what a condition
that IS on the board is allowed to do, and about two sentences in `README.md`/`MODULE.md`
that claimed more than the work had earned.

### 10.1 DECISIVE — a fire that did not halt vanished from the audit record

`on_fire` set to anything but the literal `halt`, on ONE condition, with the board otherwise
untouched: enabled, all five entries present, every `on_indeterminate` still `halt`.

**REPRODUCED HERE, twice, before anything was changed.**

The board itself, on a scratch repo with `git-error-swallowed` firing and its `on_fire` set
to `warn` (verbatim, abridged only by cutting three `[ok]` lines):

```
ANDON BOARD: CLEAR
  [fire         ] git-error-swallowed            git errors are swallowed at 1 call site(s)
      - scripts/checks/bad.ps1:3 in Invoke-SwallowingGit() runs git and does not check the result within 5 line(s)
  coverage: 5 declared, 5 evaluated, 0 switched off, 0 of 5 required MISSING
EXIT=0
```

Then the REAL gate, via `drill-dark-factory.ps1` step J run against the pre-fix sources
(`git checkout HEAD --` on the five source files, the new step kept). 16 of its 20 checks
failed, and these are the ones that matter:

```
[FAIL] the BOARD is not clear: warned, exit 6                          board=clear exit=0
[FAIL] HALT: a downgraded on_fire does NOT auto-pass the anchor gate   exit=0
[FAIL] HALT: the item stays parked at anchor-draft                     state=ready-to-test
[FAIL] HALT: nothing signed the anchor gate                            anchor_confirmed_by='auto:dark'
[FAIL] THE RECORD: the refusal is in the ledger as warned, not clear   status=
[FAIL] CONTROL: the pass record states 0 fired and 0 halted            fired=0 halted=1
[PASS] CONTROL: the trail verifies COMPLETE (exit 0)                   exit=0
```

Read them together: the condition fired, the gate auto-passed at exit 0, the item advanced
to `ready-to-test` signed `auto:dark`, there was **no refusal record at all** (`status=`
means the query found none), the PASS record's `fired` list was **empty**, and
`-VerifyAudit` called the trail **COMPLETE**. (`halted=1` in that line is the pre-fix shape:
the field does not exist, and `@($null).Count` is 1.)

**Why it was invisible rather than merely permitted.** `andon.ps1` set `$raised` from
`action -eq 'halt'`, and `gate-audit.ps1` derived the record's `fired` list the same way. So
"fired" meant "halted" in both, and a fire that did not halt had nowhere to appear. The
per-condition `[fire]` line does reach the console of a manual `-Evaluate` — but an
unattended run has no console reader, and the ledger is the surface U6 clause (c) exists for.
A detector fired and the audit trail said the board was clear.

**FIXED — `fired` means fired.**

- `andon.ps1` tracks fires and halts separately. Coverage gains `fired`/`fired_ids` and
  `halted`/`halted_ids`; a verdict can now be asked what the detectors SAW as well as what
  stopped the line.
- **A board with a fire on it is never `clear`.** `warned` is its own board state — a
  condition FIRED and its `on_fire` is not `halt` — exit 6, no auto-pass, its ids named in
  `why` and on the stderr raise. It outranks `partial`/`not-evaluated` for the headline word
  because a detector that saw something is more urgent than one that was switched off; the
  counters carry both facts regardless, and `Test-GateAuditComplete` refuses on each of them
  independently of the word.
- Ledger schema 3 → 4: `fired` and `halted` are separate arrays in every record.
  `Test-GateAuditComplete` re-derives rather than trusting: an auto-pass whose record admits
  a fire is a finding whatever its status says, and a schema-3 record — `fired` present,
  `halted` absent — cannot answer the question, which is a finding and never a pass.
- `on_fire`/`on_indeterminate` may only be `halt` or `warn`
  (`config.ps1 $script:AllowedAndonActions`, mirrored in `config.py`). Any other word is
  refused at evaluation with exit 1 and no verdict, which every gate reads as `unavailable`.
  Both directions are asserted in step J.

### 10.2 THE POLICY QUESTION — what `warn` may mean under `dark`, and why

Under `attended` a warning has a reader. Under `dark` it does not, so "fired but continue"
is a decision nobody will see in time. Three answers were considered:

1. **`dark` coerces every `on_fire` to `halt`.** Rejected: the config would say one thing
   while the run did another, and the coercion would have to live at the gate, since the
   board is deliberately run as a child process that is not told which profile invoked it.
   A knob whose value is silently overwritten is the `policy-declared-unread` shape.
2. **`dark` refuses to start if any condition declares a non-halt `on_fire`.** Rejected: it
   punishes a declaration that may never fire, and it answers at start time a question that
   is only decidable when a predicate runs.
3. **CHOSEN — narrow what `clear` MEANS.** `clear` is the only word that authorises an
   unattended pass, and a board with a fire on it is not clear. So under `dark`, `warn` and
   `halt` have exactly the same consequence at the gate: refusal, parked item, ledger
   record. `warn` still buys the board's WORD (`warned`, not `raised`) and the separate
   `fired`/`halted` lists — triage for whoever reads the ledger afterwards, and severity a
   human at an attended board can act on. It is not permission for a machine at the time.

Both halves are proved at the real gate in step J: a `warn`-declared condition that FIRES
refuses the gate (exit 6, parked, nothing signed, ledger `warned` naming it), and the same
`warn`-declared board with the condition CLEARED auto-passes at exit 0 and verifies
COMPLETE. A fix that refused everything would not be a fix.

### 10.3 CORRECTED — "by any route through the config" was false

`README.md` said "Switching the board off does not switch the gates back on — **by any route
through the config**. There are four…" and `MODULE.md` said "**So no route through the config
opens the gates.**" The four enumerated routes are real and each is drill-proved. The word
**any** is what made the sentence false, and under §C.7 these sentences are audit surfaces.

Four further routes exist. One of them — `on_fire` — is closed by §10.1 and is now a fifth
row in the README table. The other three are **open**, and both files now say so:

| route | what it does | status |
|---|---|---|
| `on_fire` downgrade | the condition fires and the board still says `clear` | **CLOSED** (10.1) |
| predicate swap | an entry keeps a required id and names a different implemented predicate | **open at run time**; the committed config is pinned |
| `params` redirect | `params.repo` → a clean decoy checkout; narrowed `globs`/`refs`/`branches` | **open** |
| id squatting | the two above together: a required id on an entry that is a different check | **open at run time** |

**Verified rather than relayed, for each:**

- The completeness check compares IDS. `andon.ps1` computes `$missingIds` as required ids
  not present in `$declaredIds` — nothing in it reads `predicate`. The new
  `test_a_predicate_swap_that_keeps_the_id_is_detected` asserts, on a config with
  `operator-checkout-off-branch` re-pointed at `branch-on-remote`, that
  `config.missing_andon_conditions() == []` — i.e. the set check sees a complete board. That
  assertion passes, which is the demonstration.
- `params` genuinely redirects where a detector looks: the drill's own hermeticity rests on
  it. `New-DarkFixture` sets `params.repo` to the fixture repo, and step B asserts from the
  ledger that the board the gate consulted was looking at a path under `$env:TEMP` rather
  than at the operator's checkout. The same lever pointed at a clean decoy is the attack.

**What the pin covers, stated narrowly.** `config.ps1`/`config.py` now carry the required
conditions as an id → predicate MAP, and `test_gate_profiles.py` compares it against
`harness.config.json`. That catches a swap **in the committed config** — what a reviewer
merges. It does not catch one made at run time, or in a config named by
`AI_STACK_HARNESS_CONFIG`, because `andon.ps1` reads only the keys. Nothing pins `params` at
all. A reader should come away with: the SET of conditions cannot be thinned, switched off or
downgraded into silence; a condition that is present can still be pointed somewhere else.

`MODULE.md`'s board table also had the `clear` row reading "none halted", which after 10.1 is
a different fact from "none fired". It now says **none FIRED**, and the paragraph beneath says
why the two are not the same.

### 10.4 THE NEW GUARD WAS THE OLD VACUITY

`test_gate_profiles.py::test_every_andon_condition_is_fully_declared` was §9's fix for
`assert conds`, and it asserted that `predicate` and `on_fire` were **truthy**. Same class,
one level down: a swapped predicate and a downgraded `on_fire` are both non-empty strings.

The guard body is now a helper, `_assert_declared_values`, so a test can prove it FIRES
rather than describe it:

- `on_fire`/`on_indeterminate` must be in `config.ALLOWED_ANDON_ACTIONS`;
- a required id's `predicate` must be the one the code says that id runs;
- `id`/`detects`/`incident` stay truthiness checks — they are prose, and nothing branches on
  their content.

RED-proved by mutation, in tests that stay: the predicate-swap test (parameterised over two
implemented predicates) asserts the OLD truthiness checks still pass on the mutated config,
that the id-set check still reports a complete board, and then that
`_assert_declared_values` raises naming the id and both predicates. The unknown-action test
does the same for `on_fire: "log-it-and-carry-on"`. The two new cross-reader assertions were
themselves mutation-checked: changing one value in `config.ps1`'s map, and adding a word to
its allowed-action list, each fail `test_powershell_and_python_agree_about_the_gates`
(`['halt','warn','shrug'] == ['halt','warn']` and
`{'work-branch-on-remote': 'config-key-unread'} != {…: 'branch-on-remote'}`).

### 10.5 Numbers, and what they depend on

- `drill-dark-factory.ps1`: **141 checks, 0 failed** (121 before; step J adds 20). The same
  file against the pre-fix sources: **141 checks, 16 failed**, all 16 in step J.
- `pytest scripts/agent-harness`: **125 passed** in this worktree (121 before; four new
  tests). Per §9.2 #7 that count needs a checkout with a live queue — without one it is
  123 passed + 2 skipped.
- `pytest scripts/claude-sessions-bridge/test_queue_narration.py`: 6 passed, unchanged.

### 10.6 Line numbers that moved with this diff

`queue.ps1` gained 11 lines, so the citations in §4.5 and §4.6 shift again: the two
unchecked git call sites are now **772 and 827** (were 763, 818), and the single non-ASCII
`§` byte is now at **301** (was 261). Both tables are corrected. The verbatim transcript in
§3 is left as it was — it is a record of a run on the tree of that moment, and editing it
would be the thing it exists to prevent.

### 10.7 What this round did NOT change

- No condition was added, removed, disabled or re-pointed. The shipped config's five
  `on_fire` values are all still `halt`, now pinned by a test.
- No run-time pin on `predicate` or `params` was built. Adding one would change what
  `missing`/`incomplete` mean in the record and needs its own item; the routes are named as
  open instead.
- `attended` behaviour is untouched: step E is unchanged and still passes.
- The 18 unchecked git call sites of §4.5 are still not fixed, `verify-merge-protocol.ps1`
  is still not run, the eleven remote branches are still not deleted, and `work/u6dark` was
  not pushed.
- U6 clauses 1–3 remain **CODE-COMPLETE, GYM-VALIDATION PARKED**. This round did not run in
  `ai-orchestration-gym` either.

---

## 11. THE `on_indeterminate` ROUND - the same defect one key over, and the fix that generalises

§10 closed `on_fire`. This round starts from the report that `on_indeterminate: warn`
reopened the identical hole on the sibling key, and ends somewhere else: the key was never
the defect. The verdict was computed **by exception**, so *any* outcome nobody had
enumerated silently meant "fine". That is the same vacuous-check shape this effort keeps
finding, sitting in the function that decides whether a human is needed.

### 11.1 DECISIVE - a condition that could not be evaluated auto-passed a dark gate

Reproduced end to end before anything was changed, by running the NEW drill steps against
the PRE-FIX sources (every file under `scripts/agent-harness/` restored from `HEAD` into a
scratch directory, the new `drill-dark-factory.ps1` copied in):

```
=== K  an outcome NOBODY ENUMERATED is not a clear board ===
[FAIL] the BOARD is not clear: indeterminate, exit 6                     board=clear exit=0
[FAIL] HALT: a warn-declared INDETERMINATE does NOT auto-pass the gate   exit=0
[FAIL] HALT: the item stays parked at anchor-draft                       state=ready-to-test
[FAIL] HALT: nothing signed the anchor gate                       anchor_confirmed_by='auto:dark'
[FAIL] THE RECORD: the refusal is in the ledger as indeterminate         status=
[FAIL] THE RECORD: the unevaluated condition is NAMED                    indeterminate=
```

`protected-ref-moved` with no baseline - the state `README.md` calls "deliberately not a
pass" - printed `ANDON BOARD: CLEAR` at exit 0 while listing `[indeterminate]
protected-ref-moved  no baseline recorded`. The gate auto-passed, the item advanced to
`ready-to-test` signed `auto:dark`, `-VerifyAudit` said COMPLETE, and `status=` above means
the ledger query found **no refusal record at all**. The condition that could not be
evaluated was in no field of the record: `fired: []`, `halted: []`, and no `indeterminate`
counter existed to re-derive it from.

**Why the sibling survived §10's fix.** `andon.ps1` set `$raised` only for
`$action -eq "halt"` and `$firedIds` only for `$r.status -eq "fire"`. Every other outcome
set NOTHING, and `clear` was the state you got when nothing objected. §10 added a `fired`
list and a `warned` word - one more exception - which is why the round after it had the same
sentence to write with one key renamed.

### 11.2 FIXED - `clear` is proven, not defaulted

The verdict is no longer computed by exception. `config.ps1` declares an OUTCOME TABLE -
every `(status, action)` pair the board knows how to think about, and the census bucket it
counts as - and `andon.ps1` classifies every result through it:

- every condition lands in **exactly one** counted bucket (`evaluated_ok`, `fired`,
  `indeterminate`, `disabled`, `unrecognised`) and is stamped with it, so the verdict, the
  console and the ledger name the same fact;
- the buckets must **sum** to the conditions the run had in scope; a mismatch, or a result
  classified into a bucket nobody declared, is itself a refusal (`unaccounted`);
- `clear` requires **every bucket except `evaluated_ok` to be empty**, with at least one
  condition in it - stated positively, not as the absence of two particular flags;
- a `(status, action)` pair that is **not a key in the table** - an unknown status, an
  unknown action word, or an unknown pairing of two known words - falls to `unrecognised`,
  which is a REFUSING bucket. No branch names the new word.

The census travels into the ledger (`census`, `census_total`, `census_ids`, schema 5), and
`Test-GateAuditComplete` **re-derives** the verdict from it instead of reading the word
`clear`: it loops the buckets the RECORD carries, so a bucket added to the board later is
checked without editing the verifier. A record with no census cannot be re-derived and is a
finding, not a pass.

Two board words were added: `indeterminate` (the sibling of `warned`) and `unaccounted`.
`unaccounted` outranks everything including `incomplete` - a board that cannot say where its
own results went cannot report any board's verdict.

### 11.3 THE PROOF THAT MATTERS - an outcome word the harness has never contained

A fix for `on_indeterminate` alone would have been the third round of the same mistake. So
the drill introduces outcome words the board has never heard of and requires it to refuse
them **without a branch naming either**. Both are done in a scratch COPY of the whole
harness, and which line was changed is asserted rather than described:

- **K2, a new ACTION word.** `quarantine` is added to `$script:AllowedAndonActions` in the
  copy's `config.ps1` (without that it is refused at parse time, which errs safe but proves
  nothing about the verdict) and declared as a firing condition's `on_fire`. The step
  asserts the copy's `andon.ps1` is **byte-identical** to the shipped one and that the word
  appears nowhere in it. Result: `board=unaccounted`, exit 6, census `unrecognised=1`,
  record `work-branch-on-remote (status 'fire', action 'quarantine')`, and the real gate
  refuses at exit 6 with the item parked.
- **K3, a new STATUS word.** One predicate in the copy is made to answer `parked`, a status
  no predicate has ever returned. The step asserts the copy differs from the shipped
  `andon.ps1` by **exactly one line** and that the line sits above `function
  Invoke-AndonEvaluation` - i.e. inside a predicate, not the verdict logic. Result: on a
  fixture the SHIPPED board calls `clear` at exit 0, the one-word copy gives
  `board=unaccounted`, exit 6, `operator-checkout-off-branch (status 'parked', action
  'none')`, the census still sums, and the gate refuses.

Pre-fix, K3 read `board=clear exit=0` and the item advanced. K2 pre-fix was refused (as
`warned`) because the condition also *fired* - the fire caught it, not the action word - and
the record named nothing: `unrecognised=`. That distinction is the honest one: an unknown
action on a firing condition was already caught by §10's `fired` list; an unknown **status**
was not caught at all.

**NEGATIVE CONTROL, run every time**: a genuinely clean full board still auto-passes at
exit 0 signed `auto:dark`, with `-VerifyAudit` COMPLETE (step K1's control, and step B).
A board that refuses everything is not a fix.

### 11.4 Claims that commands contradicted, each corrected

Eleven were reported. Each was checked against the file or the command before being
touched; all eleven were true reports.

| # | claim | disposition |
|---|---|---|
| 1 | `andon.ps1` "INDETERMINATE IS NOT A PASS ... it does not get to be the board's own behaviour" | CORRECTED in place: the header now says the sentence was false at run time, and that what makes it true is the census, not the `halt` default a config overrides |
| 2 | `README.md` "indeterminate is deliberately not a pass" | same correction, with the reproduction quoted where the claim is made |
| 3 | `README.md` "the set of conditions cannot be thinned, switched off, or downgraded into silence" | `on_indeterminate` added to the closed list, and the safe-reading sentence now says *by either outcome key, or by an outcome word nobody has thought of yet* |
| 4 | `MODULE.md` raised row "...or it could not be evaluated" | table rewritten: `raised` is "action was halt", and `indeterminate` is its own row |
| 5 | `MODULE.md` "A condition that could not be evaluated has not passed" | kept, with "that was false at run time until 2026-08-30" and what makes it true now |
| 6 | `README.md` "the gate record does carry `andon.repo`, so the redirect is visible afterwards" | **MADE TRUE**, not just corrected. `andon.repo` is `$ctx.repo_root` and a `params.repo` override does not touch it - verified by reading `Predicate-GitCheckoutState`. The record now also carries `looked_at`: every condition's predicate and the params it was handed. Drill step L builds a decoy checkout and asserts the gate passes, `andon.repo` names the fixture, and `looked_at` names the decoy |
| 7 | `harness.config.json` "`gate_profile` is the ONLY knob here that decides whether a human sees a gate" | CORRECTED. `anchor_required: false` removes the anchor gate outright for an anchorless item under ANY profile, `attended` included - no anchor, no human, no gate record, no andon consultation (`queue.ps1` line 485 and the `elseif ($anchorRequired)` at 640) |
| 8 | `MODULE.md` "The verdict has five states" above a SIX-row table | CORRECTED to eight above an eight-row table, and made a DURABLE CHECK: `test_the_MODULE_verdict_table_matches_the_board` parses the table and compares it against the `$board = "..."` literals in `andon.ps1` plus `$script:AndonBucketBoard`'s values |
| 9 | `queue.ps1` exit-6 enumeration omits `warned` | CORRECTED: every non-`clear` word is listed, with a note that the code has always refused anything that is not the literal `clear` |
| 10 | `MODULE.md` says schema 3 while `gate-audit.ps1` is at 4 | CORRECTED to 5 with what each number buys, and made a DURABLE CHECK: `test_the_MODULE_ledger_schema_number_matches_the_code` |
| 11 | `README.md` "set `on_fire` to anything but halt -> warned" - true only for `warn` | CORRECTED: the table rows now say `on_fire: warn` / `on_indeterminate: warn`, with a following row for a word the board does not implement (exit 1, no verdict, gate reads `unavailable`) |

### 11.5 What this round did NOT close

Unchanged from §10, and still named as open in `README.md` and `MODULE.md`:

- **a predicate swap** in an uncommitted config or one named by `AI_STACK_HARNESS_CONFIG`.
  `test_gate_profiles.py` pins the id -> predicate map of the COMMITTED config only.
- **a `params` redirect.** Still not refused - a condition can be pointed at a decoy. It is
  now *readable* from the ledger (`looked_at`), which is what README claimed and did not
  have, but reading it is a human act.
- **id squatting**, the two together.

And the census itself has a limit worth stating: it proves every condition landed in a
bucket and that only `evaluated_ok` is non-empty. It says nothing about whether the
predicate behind an id was the right one, or looked in the right place. Membership and
accounting are tamper-evident; behaviour is not.

### 11.6 Validation

- `drill-dark-factory.ps1` -> **184 checks, 0 failed** (141 before this round; steps K and L
  are new). Against the PRE-FIX sources with the same drill: **184 checks, 27 failed** - 26
  of them in K and L, plus one unrelated artifact of running the drill from a scratch copy
  (step A3's last case scans "this checkout", and a scratch copy is not the real checkout).
- `python -m pytest scripts/agent-harness -q` -> **129 passed** (125 before) in a checkout
  with a live queue; two of those are environment-dependent skips in a plain clone, as §1
  records. `test_gate_profiles.py` alone: 24 passed (20 before).
- RED-proved individually, each reverted afterwards: setting one shipped
  `on_indeterminate` to `warn` fails `test_no_shipped_condition_downgrades_on_indeterminate`;
  the pre-fix `config.py` fails `test_an_unenumerated_outcome_lands_in_a_REFUSING_bucket`
  and `test_powershell_and_python_agree_about_the_gates` (`no attribute 'ANDON_BUCKETS'`);
  changing MODULE's "eight" to "seven" and its schema 5 to 4 fails the two new doc checks.
- Not run in `ai-orchestration-gym`. U6 clauses 1-3 remain **CODE-COMPLETE,
  GYM-VALIDATION PARKED**.


---

## DECISIONS entries to append

> The orchestrator appends these at merge and verifies each sentence. Nothing here is
> banked that section 1–6 above does not support with a command or a file.

```
## 2026-08-30 · U6 · class 2 — gate profiles live BESIDE `profiles`, not inside it
DECISION: `dark` vs `attended` is a new top-level `gate_profiles` block, selected by
          `pipeline.gate_profile`. It is NOT a new key inside the existing `profiles`
          block.
CITED:    §C.2 class 2 (choosing between two defensible designs); most reversible option
          closest to an existing house pattern.
WHY:      `profiles` is keyed by ROLE and every reader iterates the three roles, so gate
          keys inside it would make `resolve_role`/`describe_profile` walk keys that are
          not roles. The stronger reason is COUPLING: an operator switching to a cheaper
          model profile must not thereby also remove the humans from the gates. One name
          must not carry two unrelated decisions - the reasoning that produced
          `profile_locked` on the extension surface.
DEFAULT:  `attended`, and pinned by a test. A typo that lands on the default must leave a
          human at the gate, not remove one.
REVERT:   Delete the `gate_profiles` block and `pipeline.gate_profile`; the gate handlers
          in queue.ps1 fall back to their `attended` branches, which are the pre-existing
          code paths unchanged.

## 2026-08-30 · U6 · class 2 — `pipeline.human_gates` REPLACED, not kept
DECISION: The `human_gates: {anchor, pre_review}` pair was removed from
          harness.config.json, config.ps1 and config.py, and `pipeline.gate_profile`
          took its place.
EVIDENCE: `human_gates` had ZERO executable readers in either language before this change
          (grep across *.ps1/*.py/*.json: only the two defaults mirrors and the config
          file itself). It read as policy and governed nothing.
WHY NOT KEEP BOTH: the new `policy-declared-unread` andon condition would fire on the
          config that ships it - correctly. A board whose first act is to report the file
          declaring it is a board nobody will leave on.
PRESERVED: the seed's own note - that the role->state->duty table stays in queue.ps1
          because it is BEHAVIOUR, not tuning - is carried verbatim into the
          replacement's `_note`.
REVERT:   Restore the three-line block in all three files; nothing read it, so nothing
          breaks either way. That is the point.

## 2026-08-30 · U6 · ANDON BOARD BUILT, AND IT IS RED ON THIS REPOSITORY
FINDING:  Five conditions ship, each citing the 2026-08-30 incident it was mined from,
          each with an executable predicate in andon.ps1 and each proven FIRING on a
          constructed instance and NOT firing on a clean one (drill-dark-factory.ps1,
          141 checks, 0 failed - 60 at first, 96 after the audit round below, 121 after
          the thinned-board round, 141 after the on_fire round). A condition naming a
          predicate that does not exist is
          REFUSED, so the config cannot declare a detector nobody wrote.
STATE:    `andon.ps1 -Evaluate` on the work line is RAISED. `git-error-swallowed` names 18
          call sites, among them verify-merge-protocol.ps1:49 Invoke-DrillGit() and :53
          Get-DrillGit() - the incident's own functions. (Sites, not functions, and :49/:53,
          not :47/:51; they are 10th and 11th in the tool's own order, not first. The
          verbatim run is in u6dark-findings.md section 3.) `work-branch-on-remote` names the eleven work/*
          branches on origin. `protected-ref-moved` is indeterminate until a run records
          a baseline, and indeterminate is deliberately NOT a pass.
CONSEQUENCE, stated plainly: a `dark` run CANNOT auto-pass a gate in this repository
          until those clear. The mechanism is built and proven; the line it would run on
          is stopped. "Dark-factory mode is available" would be a false description of
          the current state.
NOT MINE TO CLEAR: the drill's git helpers belong to the incident note that owns them;
          the eleven remote branches are the operator's call, and DECISIONS already
          records why they were not deleted unilaterally.
REVERT:   `pipeline.gate_profile: attended` in harness.config.json - that is the switch
          that puts a human back at both gates, and it is the ONLY revert that restores
          prior behaviour. NOT `andon.enabled: false` and NOT deleting the `andon` block:
          under `dark` those remove the thing WATCHING the machine, not the machine, and
          an earlier draft of this entry said the opposite. Proven: with the block deleted,
          a dark -Submit on a detached checkout returned exit 0 / ready-to-test with the
          ledger reading `clear conditions=0`. It now halts at exit 6 with `incomplete` in
          the ledger, naming all five missing ids (drill step F). Nor is DELETING CONDITION
          ENTRIES a revert: that is `incomplete` too (drill step H). And `attended` is the
          configured DEFAULT, not a lock - `-GateProfile dark` overrides it for one call
          (drill step I).

## 2026-08-30 · U6 · AUTO-PASSED GATES ARE DISTINGUISHABLE, AND THE CHECK HAS TEETH
DECISION: Every gate pass writes an append-only record to <state>/audit/gates.jsonl
          naming a PRINCIPAL and a KIND (human|auto). An auto-pass principal must live in
          the reserved `auto:` namespace, which -ConfirmAnchor and -Approve refuse as a
          human -By (exit 4); an auto record must additionally name the gate profile that
          authorised it and the andon verdict at that moment. A gate the board REFUSED to
          auto-pass also writes a record.
WHY:      The failure to design against is a record that says a gate "passed" without
          saying who or what passed it - worse than no record, because it reads as human
          approval.
COMPLETENESS IS EXECUTABLE: `queue.ps1 -VerifyAudit` (gate-audit.ps1
          Test-GateAuditComplete). Crossed gates are derived from the ITEM'S OWN STATE,
          never from the ledger, which is what makes a MISSING record detectable. Proven
          RED on six tampers: auto relabelled human, a deleted record, an auto pass
          claiming the board was raised, an auto pass whose coverage counters say nothing
          was evaluated, an auto record with no coverage at all, and an item it cannot
          audit. Proven GREEN again once restored, so it is not stuck red.
EXIT 7:   an item -VerifyAudit could not audit is COVERAGE INCOMPLETE, not a pass. The
          first version returned 0 - the same skip-counts-as-a-pass shape the board
          refuses - and was fixed before landing.
REVERT:   Remove the Write-GateRecord calls and the -Audit/-VerifyAudit handlers from
          queue.ps1; the ledger is an additive file nothing else reads, and the item's
          `gates` field is additive too.

## 2026-08-30 · U6 · CODE-COMPLETE, GYM-VALIDATION PARKED (not "complete")
FINDING:  §2's U6 column says "Gym: an unattended run that hits each andon condition
          halts-and-raises; one that hits none lands with a complete audit trail". Both
          BEHAVIOURS are proven executably against the real shipped tools in isolated
          scratch repositories - a raised board halts the gate (exit 6) with the item
          parked and the refusal in the ledger, and a clean run lands with a trail that
          -VerifyAudit certifies complete. The word GYM is not satisfied: this did not run
          in ai-orchestration-gym, whose runner drives the agent-org bridge against GitHub
          and mutates remote repositories.
STATUS:   **U6 clauses 1-3 = CODE-COMPLETE, GYM-VALIDATION PARKED.** Reported this way
          under the rule adopted 2026-08-30: a phase is DONE only when its Validated by
          column is satisfied and the evidence is named; otherwise PARKED, with what is
          missing. "Code-complete" is not a synonym for done.
ALSO NOT RUN: verify-merge-protocol.ps1, which exercises the queue.ps1 this item changed.
          Not run deliberately - it operates on the operator's live checkout and is the
          script that rebased the live work line on 2026-08-30, and its two proven
          contributing defects are still present. A reasoned argument that attended
          behaviour is unchanged is in u6dark-findings.md §4.3; an argument is not a test
          run, and a tester with a safe way to run that drill should.
REVERT:   n/a - a status statement.

## 2026-08-30 · method · A DETECTOR MUST BE SHOWN FIRING, OR IT IS NOT A DETECTOR
FINDING:  Both of this item's file-scanning predicates were, in their first version,
          checks that checked nothing - written by someone actively hunting that exact
          failure. `git-error-swallowed` reported FOUR functions, all four the word "git"
          inside a COMMENT, while missing Invoke-DrillGit (it calls git.exe). And
          `policy-declared-unread` could not reproduce the human_gates incident, because
          andon.ps1's OWN HEADER discusses that key path - the detector's documentation
          made the defect look read.
CAUGHT BY: the drill's requirement to prove RED on a constructed instance before GREEN on
          a clean one. Neither would have been caught by a drill that only asserted GREEN.
RULE:     a check whose only demonstrated behaviour is passing has demonstrated nothing.
          The constructed-failure case is not extra coverage; it is the only evidence that
          the passing case means anything.
REVERT:   n/a - method.

## 2026-08-30 · U6 · AUDIT LAYER — a board that did not look may not report a clear line
FINDING:  The machinery was confirmed by two independent verifiers - all five conditions
          fire on a constructed instance and stay quiet on a clean repository, the halt
          works end to end at the real gate for all five, the negative control passes. What
          was wrong was what the RECORD SAID. With `andon.enabled: false`, on a checkout
          that was genuinely DETACHED, `andon.ps1 -Evaluate` printed `board=clear,
          conditions=5` and exited 0, and the ledger recorded `andon.status=clear
          conditions=5` - indistinguishable from five conditions that looked and found
          nothing. The per-condition `disabled` status was computed and then discarded.
          gate-audit.ps1's own header states the rule that broke: an auto-pass must carry
          the andon verdict at that moment, and a record that cannot state it is incomplete
          by definition.
DECISION: `clear` now means something narrow and checkable - at least one condition
          evaluated, none halted, none switched off (narrowed once more by the on_fire
          entry below, to none FIRED). `partial` (some evaluated, some
          switched off) and `not-evaluated` (nothing evaluated at all) are their own board
          states, both exit 6, and both refuse an unattended gate. Every verdict and every
          gate record carries `coverage {declared, evaluated, disabled, disabled_ids}` and
          the repository the board was looking at; Test-GateAuditComplete re-derives the
          claim from those counters instead of trusting the word `clear`. A LATER ENTRY
          BELOW adds the fifth state, `incomplete`, and two more coverage fields: these
          counters are all relative to what the config DECLARED, which a board thinned by
          deleting condition entries satisfies perfectly.
PROVEN:   RED first, on a detached scratch repo - `clear/5/exit 0`. GREEN after -
          `not-evaluated`, exit 6, `declared=5 evaluated=0`. Three new ledger tampers go
          RED (evaluated forced to 0; the coverage fields stripped; the pre-existing status
          tamper). drill-dark-factory.ps1: 96 checks at that round, 121 after the
          thinned-board round, 141 after the on_fire round, 0 failed.
REVERT:   revert the andon.ps1 / gate-audit.ps1 diff; the ledger is additive and older
          records simply lack the coverage fields, which the verifier reports as a finding
          rather than a pass.

## 2026-08-30 · U6 · CORRECTION — the documented revert path was a SILENT KILL SWITCH
FINDING:  This item's own findings note offered "andon.enabled: false, or delete the andon
          block - every gate behaves as it did before" as the revert. REPRODUCED FALSE:
          with the block deleted, a `dark` -Submit on a DETACHED checkout returned exit 0 /
          ready-to-test, the ledger recorded `clear conditions=0`, and -VerifyAudit printed
          COMPLETE. Under `dark` that switch does not restore prior behaviour - it removes
          the only thing standing between an unattended run and its own approval.
WHY IT MATTERS MORE THAN THE BUG: a revert path is read in a hurry, by someone who has
          already decided to stop. One that does something other than what it says is worse
          than none.
DECISION: The revert to prior behaviour is `pipeline.gate_profile: attended`. A
          switched-off board is `not-evaluated` and an ABSENT one is `incomplete` (see the
          thinned-board entry below, which gave board removal its own state): either way
          the gate REFUSES (exit 6), the item parks, and the refusal is in the ledger.
          Corrected in the entry above, in MODULE.md and in README.md. `attended` is the
          configured DEFAULT, not a lock - `queue.ps1 -GateProfile dark` names a profile
          for a single call and takes the dark path regardless (verified: same item, exit 5
          attended, exit 6 dark; drill step I). Removing the human from a gate is one flag
          away by design, and this entry says so rather than leaving "the revert" to be
          read as "no run can self-pass now".
PROVEN:   drill step F drives the REAL gate for three cases - `andon.enabled=false`, the
          andon block deleted, and one condition switched off - asserting exit 6, the item
          parked at anchor-draft, and a ledger refusal recording not-evaluated /
          incomplete / partial with evaluated 0 / 0 / 4. Step I proves the override: the
          same item, exit 5 under the attended config and exit 6 under `-GateProfile dark`.
REVERT:   n/a - a correction. The behaviour it documents reverts with the entry above.

## 2026-08-30 · method · A DETECTOR'S OWN BLOCK IS NOT EXEMPT, AND ITS MATCH MUST BE ANCHORED
FINDING:  Three defects in one condition family, all found by pointing the detectors at
          themselves. (1) `andon.raise.ledger` had ZERO executable readers - the dead-knob
          detector shipped a dead knob inside its own block, which its roots (`["pipeline"]`)
          could not see. (2) The dotted path was matched as an UNANCHORED SUBSTRING, so
          `pipeline.claim_ttl`, `pipeline.anchor_require`, `pipeline.gate_profil` and even
          `pipeline.a` all reported "read" - each is a substring of a live key - and the
          predicate printed "ok - all 4 policy keys under pipeline are read" for keys
          referenced nowhere. (3) `git-error-unchecked` scanned function bodies only, so a
          top-level `& git.exe push origin HEAD | Out-Null` reported ok and a live in-glob
          instance at scripts/checks/check-project-configs.ps1:18 went unflagged; and an
          unrelated `throw` ANYWHERE in a body amnestied every git call in it.
DECISION: (1) the knob is REMOVED rather than given a reader - the ledger write must not be
          optional, because a run able to switch off the record of its own halt is the
          failure the board exists to prevent - and `andon` is now a root, so the condition
          covers its own block. (2) the match is anchored, and the walk reports the
          SHALLOWEST unread node, so a container counts as read when its own path OR any
          leaf beneath it is. (3) the unit of judgement is the CALL SITE, within
          `check_window_lines` (default 5) lines, and code outside any function is scanned
          as a `(top level)` region.
COST, MEASURED NOT ASSUMED: the call-site rule reports 18 unchecked git call sites in the
          two default globs, up from 2 functions. A sample was read line by line and each
          was genuine - `git ls-files` in validate-lineendings.ps1 whose failure prints
          "SUCCESS: No tracked shell scripts", `git diff --cached` in check-staged-secrets.ps1
          whose failure means "nothing staged - skip" and passes the secret guard vacuously.
          They are listed in u6dark-findings.md section 4.5. NOT FIXED HERE: nine live
          pre-commit and harness scripts, none of them this item's anchor, and fixing them
          inside an audit-fix item is the enumerate-and-patch shape that loses.
PROVEN:   each of the three RED on a constructed instance before the fix and GREEN after,
          all three now permanent drill checks.
REVERT:   restore `andon.raise.ledger` and `roots: ["pipeline"]`, and revert the two
          predicates; the board goes back to reporting 2 functions and the dead knob.

## 2026-08-30 · U6 · "COMPLETE" NARROWED, AND THE ONE NARRATION SURFACE GIVEN A PRINCIPAL
FINDING:  Two gaps neither refuted nor disclosed. (a) Get-CrossedGates counted the anchor
          gate as crossed only if the item CARRIED an anchor, so with
          `pipeline.anchor_required=false` an item ran end to end with NO anchor, held one
          ledger record, and -VerifyAudit printed "COMPLETE - every crossed gate has a
          record", exit 0. The shipped default is `true`, so it was never live - but
          "complete" meant "every gate this item happened to cross", never "the pipeline's
          gates were accounted for". (b) `scripts/claude-sessions-bridge/bridge.py`
          poll_queue() is the one existing consumer that narrates a gate transition to a
          human, and for the state a dark pre_review auto-pass produces it emitted
          "released for review." - passive, no principal, BYTE-IDENTICAL to the attended
          case where a person typed `release:` (verified by rendering the pre-fix template
          for both). Its audit event {event, from, to} named no principal either. By this
          item's own rule - a record that says a gate passed without saying who passed it
          reads as human approval - that surface violated it. It is DORMANT (every real
          queue item has thread=""), which is why it was pinned rather than left to wake up.
DECISION: (a) with `anchor_required=true` an item past anchor-draft has crossed the anchor
          gate whether or not an anchor survives on it, so an anchorless item is a FINDING;
          with `anchor_required=false` -VerifyAudit states in words that the anchor gate is
          not a gate for an anchorless item, and every green prints the scope of "crossed".
          (b) the narration and the audit event both carry the principal: an auto pass reads
          "No human saw this gate - auto-passed by `auto:dark` under gate profile `dark`",
          a human one names the person, and an item recording neither says so explicitly.
PROVEN:   drill step G proves both directions of (a) on one item (exit 0 with the disclosure
          under `false`, exit 1 naming the missing anchor pass under `true`).
          test_queue_narration.py, 6 tests, proven RED against the pre-fix bridge.py; one is
          derived FROM the state->gate map rather than a hand-written list, so a new
          gate-crossing state with no principal slot fails. Bridge suite 124 -> 130.
REVERT:   (a) restore the `$hasAnchor -and` guard in Get-CrossedGates. (b) drop the
          {principal} slot and gate_principal_note(); the narration is additive.

## 2026-08-30 · process · A COMMIT MESSAGE MAY NOT NAME A COMMAND AND REPORT A VERDICT IT DOES NOT GIVE
FINDING:  The U6 commit message ends "work/u6dark is local only (andon.ps1 -Evaluate
          -RunBranch work/u6dark: CLEAR)". Run, that command prints ANDON BOARD: RAISED and
          exits 6. The FACT is true and the findings note words it correctly; the CITATION
          named a command and reported a verdict it does not give - in the trail C.7 says
          the operator reads INSTEAD of the diffs. The command that establishes that one
          fact is `andon.ps1 -Evaluate -Only work-branch-on-remote -RunBranch work/u6dark`
          (exit 0, "checked 1 branch(es); none is on a remote").
RULE:     an evidence citation is checked by RUNNING it, not by remembering what it showed
          when it was run against a different question. This is the same shape as the U4
          clause 3 false sentence: one verified claim and one unverified claim in the same
          confident voice, and a reader cannot separate them.
NOT DONE: the earlier commit message was NOT rewritten. Editing a message to hide an error
          is the opposite of an audit trail; the correction is in the next message and in
          the note.
ALSO ADJUDICATED, and the refutation was WRONG on the mechanism: the drill's "never touches
          the operator's checkout" claim IS false - step A3 deliberately scans this
          checkout's .ps1 files, read-only - but not for the reason offered. In fixtures
          B/D/D2/E `params.repo=""` does NOT resolve to the operator's checkout, because
          Invoke-Queue does Push-Location and the working directory propagates through both
          child processes (verified two levels deep in a scratch repo), and because step B's
          board was CLEAR while the operator's repository is RED. Fixed anyway - a property
          that holds only through an inherited working directory is not a property:
          params.repo is now pinned per fixture and step B asserts from the LEDGER that the
          board the gate consulted was looking under $env:TEMP.
REVERT:   n/a - corrections.

## 2026-08-30 · U6 · A BOARD MISSING A CONDITION IS NOT A CLEAR BOARD — the third way off
FINDING:  Two ways of switching the andon board off were closed: `andon.enabled: false`
          and deleting the `andon` block both report a non-clear state and halt. There was
          a THIRD, and it is the one an operator or an agent actually reaches for - DELETE
          THE CONDITION'S ENTRY from `andon.conditions`. REPRODUCED on a genuinely detached
          scratch checkout with the board otherwise untouched (enabled, block present,
          surviving params intact), pruned to one of five: `ANDON BOARD: CLEAR`, andon exit
          0, the dark anchor gate AUTO-PASSED at exit 0 to ready-to-test signed `auto:dark`,
          ledger `status=clear conditions=1 evaluated=1 disabled=0`, `-VerifyAudit`
          COMPLETE. Every counter was TRUE, because every counter was relative to the
          config's own thinned list. README.md and MODULE.md said "switching the board off
          does not switch the gates back on" - true of the two enumerated routes, false of
          this one, and under C.7 that sentence is an audit surface.
          The guard that should have caught it could not: test_gate_profiles.py's
          `test_every_andon_condition_is_fully_declared` asserted only `assert conds`
          (NON-EMPTY), so four of five could be deleted and it stayed green. The same
          vacuity class this effort exists to kill, inside the anti-tamper guard.
DECISION: PIN THE SET, NOT ITS SIZE. The five required condition ids are declared in CODE -
          config.ps1 `$script:RequiredAndonConditions` and config.py
          `REQUIRED_ANDON_CONDITIONS` - and NOT in harness.config.json, because a required
          list kept beside the conditions is deleted along with the entry it names and the
          file agrees with itself either way. In code, retiring a condition is a diff a
          reviewer sees. Same asymmetry `$script:Predicates` already provides in the other
          direction: the config may not declare a detector nobody wrote, and may not
          silently drop one somebody did. No environment override.
          A board missing any required id is `incomplete`: its own board state, the missing
          ids NAMED in the verdict, on the console, in the stderr raise and in the ledger,
          exit 6, no auto-pass - the shape `not-evaluated` and `partial` already had. It
          outranks the other non-clear words because a verdict from a board that is not the
          required board cannot be reported as that board's verdict; declared conditions are
          still evaluated and still listed. Coverage gains `required`/`missing`/`missing_ids`
          (ledger schema 3; the on_fire entry below takes the shipped schema to 4) and
          Test-GateAuditComplete RE-DERIVES the fact from the record:
          a record still labelled `clear` while admitting missing conditions is a finding,
          and a schema-2 record that cannot state the required set is a finding, not a pass.
          Board REMOVAL is now `incomplete` naming all five rather than `not-evaluated`,
          which is also the correction to MODULE.md's board table.
PROVEN:   RED FIRST. Drill step H drives the REAL gate for two thinnings - ONE entry
          deleted and FOUR deleted - asserting exit 6, the item parked at anchor-draft,
          nothing signed, a ledger refusal recording `incomplete`, and the record NAMING
          every missing id; it also asserts the old counters would have said full coverage
          (4/4 and 1/1 evaluated, 0 disabled), so the reason they could not catch it
          survives the fix. The same drill file run against the PRE-FIX sources fails 22
          checks. NEGATIVE CONTROL, re-run after the fix on a fresh fixture built the same
          way: a full board still auto-passes at exit 0, still prints NO HUMAN CONFIRMED IT,
          signs `auto:dark`, records 0 of 5 required MISSING, and verifies COMPLETE - and
          those CONTROL checks pass on both sides of the fix. Ledger tampers: a record
          labelled clear but admitting 4 missing goes RED; a schema-2 shaped record goes
          RED; restore, GREEN. test_gate_profiles.py adds the set test, a parameterised
          red-proof that pins the OLD vacuity inside the new test, a test that a config may
          neither narrow nor widen the required set, and the required ids in the
          PowerShell/Python anti-drift test. At that round: drill-dark-factory.ps1 121
          checks, 0 failed; pytest scripts/agent-harness 121 passed. After the on_fire
          round below: 141 checks and 125 passed, both still 0 failed.
REVERT:   revert the config.ps1/config.py constant and the andon.ps1/gate-audit.ps1 diff;
          the ledger is additive and older records simply lack `required`/`missing`, which
          the verifier reports as a finding rather than a pass. Reverting restores the
          thinned-board hole - it is not a knob for a reason.

## 2026-08-30 · method · A CHECK THAT CANNOT FIRE IS THE DEFECT, WHEREVER IT SITS
FINDING:  Three of this round's nine corrections are the same shape as the work itself, in
          the work itself. (1) `Stop-OnAndon`'s coverage line was guarded by
          `$andon.PSObject.Properties.Name -contains "evaluated"`, and `$andon` is an
          [ordered] hashtable whose PSObject properties are Count/IsReadOnly/Keys/Values/
          IsFixedSize/SyncRoot/IsSynchronized - never its keys. ALWAYS FALSE, so a real dark
          halt printed no coverage at all. (2) the andon condition-set guard asserted
          non-emptiness. (3) `queue.ps1`'s usage said `-VerifyAudit ... exit 1 if not` while
          coverage-incomplete exits 7 - the usage was narrower than the tool and read as
          though 7 were impossible.
RULE:     a guard is not verified by reading it. It is verified by making the thing it
          guards against and watching it fire. Every one of these was found by a verifier
          RUNNING the command, not by re-reading the file - including on this item, whose
          entire subject is checks that check nothing.
ALSO:     four claims were wider, staler or shallower than their evidence and are corrected
          in the note - `git -C ""` exits 128 in POWERSHELL (the silent-exit-0 behaviour was
          verified in bash, and this file's code path is PowerShell); a fenced block
          presented as output had been RE-ORDERED to put the incident's own functions first
          when they are 10th and 11th of 18; "no scanned file uses a here-string" (one has
          eight); "every function in the scanned families is at column 0" (one is indented);
          and "117 passed" needs a checkout with a live queue (a clone gives 115 + 2 skipped).
DISCLOSED, NOT CHANGED: `-GateProfile dark` overrides an attended config for a single call
          (same item: exit 5 attended, exit 6 dark), so `pipeline.gate_profile: attended` is
          the configured DEFAULT, not a lock. That is the design - what needed fixing was
          the sentence that let "the revert" be read as "no run can self-pass now".
REVERT:   n/a - corrections, plus one guard (Test-AndonField) that reverts with its diff.

## 2026-08-30 · U6 · A FIRE THAT DOES NOT HALT MUST STILL BE IN THE RECORD
FINDING:  `on_fire` set to anything but the literal `halt`, on ONE condition, with the board
          otherwise untouched (enabled, all five entries present, every on_indeterminate
          `halt`): the condition FIRED and the board reported `clear` at exit 0. REPRODUCED
          twice before anything was changed - the board alone on a scratch repo
          (`ANDON BOARD: CLEAR` with `[fire] git-error-swallowed` listed under it, exit 0),
          and the REAL gate via drill step J run against the pre-fix sources: the dark
          anchor gate AUTO-PASSED at exit 0, the item reached ready-to-test signed
          `auto:dark`, NO refusal record was written, the PASS record's `fired` list was
          EMPTY, and `-VerifyAudit` returned COMPLETE. `andon.ps1` set `$raised` from
          `action -eq 'halt'` and `gate-audit.ps1` derived the record's `fired` list the
          same way, so "fired" meant "halted" in both and a fire that did not halt had
          nowhere in the ledger to appear. The `[fire]` line reaches the console of a manual
          `-Evaluate`; an unattended run has no console reader, and the ledger is the surface
          U6 clause (c) exists for. The clause inverted: a detector fired and the audit trail
          said the board was clear.
DECISION: FIRED MEANS FIRED. The board tracks fires and halts separately - coverage gains
          `fired`/`fired_ids` and `halted`/`halted_ids`, ledger schema 3 -> 4 carries both as
          separate arrays - and a board with a fire on it is NEVER `clear`. A fire that does
          not halt is its own board state, `warned`: exit 6, ids named in `why` and on the
          stderr raise, no auto-pass. Test-GateAuditComplete RE-DERIVES it: an auto-pass
          record admitting a fire is a finding whatever its status word says, and a schema-3
          record (`fired` present, `halted` absent) cannot answer the question, which is a
          finding and never a pass. `on_fire`/`on_indeterminate` may only be `halt` or `warn`
          (config.ps1 $script:AllowedAndonActions, mirrored in config.py); any other word is
          refused at evaluation with exit 1 and no verdict, which every gate reads as
          unavailable.
          THE POLICY, argued rather than assumed. Coercing `on_fire` to `halt` under `dark`
          was rejected: the config would say one thing while the run did another, and the
          board is deliberately a child process that is not told which profile invoked it.
          Refusing to START when a condition declares a non-halt action was rejected: it
          punishes a declaration that may never fire, at a time when the question is not yet
          decidable. What was chosen narrows what `clear` MEANS, at the one point that
          matters. Under `dark`, `warn` and `halt` therefore have the same consequence at the
          gate; what `warn` buys is the WORD and the record - triage for a human reading
          afterwards, never permission for a machine at the time.
PROVEN:   RED FIRST, at the REAL gate. Drill step J (20 checks) fails 16 against the pre-fix
          sources and passes 20 after; the whole file is 141 checks, 0 failed (121 before).
          It asserts the fire is genuine, that the recorded ACTION is still the configured
          `warn` and not a rewritten `halt`, board `warned` + exit 6, the verdict separating
          fired_ids from halted_ids, the gate refusing at exit 6 with the item parked at
          anchor-draft and nothing signed, the ledger refusal recording `warned` with `fired`
          NAMING the condition and `halted` EMPTY, an unimplemented on_fire refused (exit 1,
          no verdict) and the gate reading that as unavailable - and a NEGATIVE CONTROL: the
          same `warn`-declared board with the condition CLEARED auto-passes at exit 0 and
          verifies COMPLETE. Ledger tampers: a record labelled `clear` that admits a fire
          goes RED and NAMES it; a schema-3 shaped record goes RED; restore, GREEN.
REVERT:   revert the andon.ps1/gate-audit.ps1/config.ps1/config.py/queue.ps1 diff. The ledger
          is additive: older records simply lack `halted`, which the verifier reports as a
          finding rather than a pass. Reverting restores the hole - a per-condition word that
          opens an unattended gate.

## 2026-08-30 · method · NAME THE ROUTES YOU CLOSED, NOT "ANY ROUTE"
FINDING:  README.md said "Switching the board off does not switch the gates back on - BY ANY
          ROUTE THROUGH THE CONFIG. There are four", and MODULE.md said "So no route through
          the config opens the gates." The four enumerated routes are real and each is
          drill-proved. The word ANY is what made both sentences false, and under C.7 those
          sentences are audit surfaces. Four further routes exist: an `on_fire` downgrade
          (closed by the entry above), a PREDICATE SWAP, a `params` REDIRECT, and ID
          SQUATTING (the last two combined). VERIFIED HERE, not relayed: `andon.ps1` computes
          its missing set from required ids against declared ids and reads no `predicate`, and
          the new predicate-swap test asserts `missing_andon_conditions() == []` on a config
          whose `operator-checkout-off-branch` runs `branch-on-remote` - a complete board to
          every counter; and `params` genuinely redirects where a detector looks, which is
          what the drill's own hermeticity rests on (New-DarkFixture sets `params.repo` to the
          fixture and step B asserts from the ledger that the gate's board looked there).
DECISION: state the CLOSED routes and the OPEN ones by name. README.md now lists five closed
          routes as drill cases with their board states, then names the three that remain
          open and what each does. MODULE.md's claim becomes "the board's MEMBERSHIP is
          tamper-evident; its BEHAVIOUR is not": the SET of conditions cannot be thinned,
          switched off or downgraded into silence, but a condition that is present can still
          be pointed somewhere else. The required conditions become an id -> predicate MAP in
          config.ps1/config.py and test_gate_profiles.py compares it against the shipped
          config - which pins the COMMITTED config, what a reviewer merges, and NOT a
          run-time swap or one via AI_STACK_HARNESS_CONFIG. That limit is written where the
          pin is, in both readers and in both documents. MODULE.md's board table also said
          `clear` means "none halted"; after the entry above that is a different fact from
          "none fired", and it now says none FIRED.
RULE:     a universal claim is a liability with a short life. It takes one counter-example to
          falsify and a verifier ten minutes to find one - three of the four extra routes
          here were found that way. A narrow sentence that names what was TESTED survives.
PROVEN:   the guard that should have caught the swap was vacuous in the same class as the one
          it replaced: `assert conds` became "assert `predicate` and `on_fire` are TRUTHY",
          and a swapped predicate and a downgraded action are both non-empty strings. The
          guard body is now `_assert_declared_values` - allowed literals for the actions, the
          code-declared predicate for a required id - called by the shipped-config test and
          RED-proved by two mutation tests that first assert the OLD checks still pass, then
          require it to raise naming the id and both predicates. The two new cross-reader
          assertions were mutation-checked too: one changed map value and one added allowed
          action each fail test_powershell_and_python_agree_about_the_gates. pytest
          scripts/agent-harness: 125 passed (121 before; 123 + 2 skipped without a live
          queue). No behaviour changed in this entry - a map, a test helper, three prose
          corrections.
REVERT:   n/a for the documents. The id -> predicate map and the allowed-action list revert
          with their diff; reverting them restores a guard that accepts any non-empty string.

## 2026-08-30 · U6 · `CLEAR` IS PROVEN, NOT DEFAULTED - the verdict stops being computed by exception
FINDING:  `on_indeterminate: warn` on ONE condition reopened the IDENTICAL hole the entry
          above closed on `on_fire`. REPRODUCED before anything was changed, by running the
          new drill steps against every `scripts/agent-harness/` file restored from HEAD:
          `protected-ref-moved` with no baseline - the state README calls "deliberately not a
          pass" - printed `ANDON BOARD: CLEAR` at exit 0 while listing `[indeterminate]
          protected-ref-moved  no baseline recorded`; the dark gate AUTO-PASSED, the item
          advanced to ready-to-test signed `auto:dark`, `-VerifyAudit` said COMPLETE, and the
          ledger query for a refusal record found NONE. The condition that could not be
          evaluated was in no field of the record: `fired: []`, `halted: []`, and no
          `indeterminate` counter existed for Test-GateAuditComplete to re-derive it from.
          THE KEY WAS NEVER THE DEFECT. `$raised` was set only for `action -eq halt` and
          `$firedIds` only for `status -eq fire`; every other outcome set NOTHING, and `clear`
          was the state left when nothing objected - so any outcome nobody enumerated silently
          meant "fine". That is the vacuous-check shape this effort keeps finding, sitting in
          the function that decides whether a human is needed. Three rounds running a fix
          closed one outcome key and left its sibling.
DECISION: the verdict is decided by a BUCKET CENSUS, not by flags. config.ps1 declares an
          outcome table - every (status, action) pair the board knows how to think about and
          the bucket it counts as - beside the required-condition set and for the same reason
          (gate-audit.ps1 must read the same declaration). andon.ps1 classifies every result
          through it: exactly one counted bucket per condition, stamped onto the result; the
          buckets must SUM to the conditions in scope; `clear` requires every bucket except
          `evaluated_ok` to be EMPTY with at least one condition in it - stated positively,
          not as the absence of two flags; and a pair that is not a key in the table falls to
          `unrecognised`, a REFUSING bucket, with no branch naming the new word. Two board
          words added: `indeterminate` (the sibling of `warned`) and `unaccounted`, which
          outranks everything including `incomplete` - a board that cannot say where its own
          results went cannot report any board's verdict. `fired` and `halted` remain
          reported lists; neither decides the verdict any more.
PROVEN:   ledger schema 5 carries `census` / `census_total` / `census_ids`, and
          Test-GateAuditComplete RE-DERIVES the verdict from them instead of reading the word
          `clear` - looping the buckets the RECORD carries, so a bucket added later is checked
          without editing the verifier. A record with no census cannot be re-derived and is a
          finding. RED first, GREEN after: the same drill against pre-fix sources gives
          `184 checks, 27 failed`; against the fix, `184 checks, 0 failed`.
          NEGATIVE CONTROL asserted in the same step: a genuinely clean full board still
          auto-passes at exit 0 signed `auto:dark` with -VerifyAudit COMPLETE. A board that
          refuses everything is not a fix.
REVERT:   the census is one block in andon.ps1's Invoke-AndonEvaluation plus the table in
          config.ps1/config.py; reverting them restores a verdict computed by exception and
          re-opens both downgrade keys. Ledger records written under schema 5 stay readable -
          older code reads the counters it knows and ignores the census.

## 2026-08-30 · method · CLOSE THE CLASS, AND PROVE IT WITH A CASE NOBODY ANTICIPATED
FINDING:  the previous two rounds each fixed the reported instance. `andon.enabled: false`
          was closed, then deleting entries, then `on_fire`, then `on_indeterminate` - four
          fixes, one defect. A verifier put it exactly right: "the same sentence as the
          round-4 finding with one key renamed". Fixing the reported key again would have
          been the third round of the same mistake, and the fifth key would have arrived.
DECISION: when a fix is for one instance of a shape, the test is not the instance. The drill
          now INTRODUCES AN OUTCOME THE CODE DOES NOT PRODUCE and requires the board to refuse
          it with no branch naming it: step K2 adds an ACTION word (`quarantine`) to the
          allowed vocabulary, step K3 makes one predicate answer a STATUS word (`parked`) no
          predicate has ever returned. Both are done in a scratch COPY of the whole harness,
          and WHICH LINE CHANGED IS ASSERTED, not described - K2 asserts the copy's andon.ps1
          is byte-identical to the shipped one; K3 asserts it differs by exactly one line and
          that the line sits above `function Invoke-AndonEvaluation`, i.e. inside a predicate.
          If closing a new case had needed a new branch in the verdict logic, these two steps
          are what would have said so.
PROVEN:   K2 gives `board=unaccounted`, exit 6, census `unrecognised=1`, record
          `work-branch-on-remote (status 'fire', action 'quarantine')`, gate refused, item
          parked. K3 gives `board=unaccounted` on a fixture the SHIPPED board calls `clear` at
          exit 0, record `operator-checkout-off-branch (status 'parked', action 'none')`, gate
          refused. Pre-fix, K3 read `board=clear exit=0` and the item advanced. Pre-fix K2 was
          refused as `warned` - because the condition also FIRED, which §10's list caught -
          and named nothing (`unrecognised=`); that distinction is stated in the note rather
          than claimed as a second win.
RULE:     a fix that only closes the reported key is a report, not a fix. Invent the next
          case and make it fail first.
REVERT:   n/a - drill steps K2/K3 and the mirrored bucket table in config.py revert with
          their diff.

## 2026-08-30 · U6 · A PARAMS REDIRECT IS NOW READABLE FROM THE LEDGER - the claim made true
FINDING:  README.md said a `params.repo` redirect was "visible afterwards" because "the gate
          record does carry `andon.repo`". VERIFIED FALSE by reading the code, not relayed:
          `andon.repo` is `$ctx.repo_root` from Resolve-RepoRoot, while `params.repo` is read
          per condition by Predicate-GitCheckoutState via Get-Param - the two are unrelated,
          so a redirect run recorded the real checkout while the detector examined a decoy. A
          true sentence about a field that does not answer the question.
DECISION: make it true rather than delete it. Every condition result now carries the params it
          ran with (`_`-prefixed documentation keys dropped - the shipped config holds
          paragraphs of prose that nothing reads at run time and that would bury the one field
          an auditor wants), and the gate record carries `looked_at`: for every condition, the
          predicate it ran and those params. A redirect AND a predicate swap are now readable
          by a reader of the ledger. Neither is REFUSED, and README still says so.
PROVEN:   drill step L builds a decoy scratch checkout, points `operator-checkout-off-branch`
          at it, and asserts all three facts from the real ledger: the gate PASSES (the
          redirect is not refused), `andon.repo` names the fixture and not the decoy, and
          `looked_at` names the decoy plus the predicate behind every id. Against pre-fix
          sources both `looked_at` checks fail with `looked_at=`.
REVERT:   one field in Invoke-AndonForGate and Get-CondParams in andon.ps1; reverting them
          restores a record that cannot show where a condition looked.

## 2026-08-30 · method · A PROSE NUMBER BESIDE A TABLE IS A CLAIM THE TABLE CAN CONTRADICT
FINDING:  MODULE.md said "The verdict has five states" above a SIX-row table - the uncounted
          sixth was `warned`, added the same morning by the round that wrote the sentence
          above it. It also described the ledger as schema 3 and called only a schema-2 record
          a finding, while gate-audit.ps1 was already at 4 and treated schema-3 as one. Both
          are audit surfaces under C.7: the operator reads these instead of the diffs.
DECISION: correct both, and make the class of error DURABLE-CHECKED rather than re-read.
          `test_the_MODULE_verdict_table_matches_the_board` parses the table and compares its
          rows against the board words the CODE can produce - the `$board = "..."` literals in
          andon.ps1 plus the values of `$script:AndonBucketBoard` - and against the prose
          count, and asserts exactly one row exits 0.
          `test_the_MODULE_ledger_schema_number_matches_the_code` compares MODULE's stated
          schema against `$script:GateLedgerSchema`.
PROVEN:   RED-proved and reverted: "eight" -> "seven" and "schema 5" -> "schema 4" each fail
          their test. The words are read from andon.ps1 with a regex that asserts it matched
          something, so a rename that empties it fails rather than passing vacuously - the
          check-that-checks-nothing shape this file keeps recording.
REVERT:   n/a for the prose. The two tests revert with their diff.

## 2026-08-30 · U6 · `gate_profile` IS NOT THE ONLY KNOB THAT DECIDES WHETHER A HUMAN SEES A GATE
FINDING:  harness.config.json's `pipeline._note` said `gate_profile` "is the ONLY knob here
          that decides whether a human sees a gate". Disproved by reading queue.ps1:
          `pipeline.anchor_required: false` removes the anchor gate outright for an item
          created without an anchor, under ANY profile including `attended` - the item goes
          to ready-to-test with no anchor, no human, no gate record and no andon consultation
          (the `elseif ($anchorRequired)` that would Die is simply not taken, and
          Get-CrossedGates then counts no anchor gate for it).
DECISION: correct the note in place and state the split: `gate_profile` decides WHO passes a
          gate; `anchor_required` decides whether the anchor gate EXISTS. The behaviour is
          not changed - it is a deliberate escape hatch, `-VerifyAudit` already says so in
          words rather than counting a narrower complete, and this is class 4 work.
RULE:     "the only X" in a config note is a universal with a short life, same as "any route
          through the config" was. Say what the knob does; let the next one say what it does.
REVERT:   n/a - one string.

```
