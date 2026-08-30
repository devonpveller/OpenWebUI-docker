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
| `scripts/agent-harness/andon.ps1` | the board: 5 executable predicates, `-Evaluate` / `-List` / `-Baseline`, exit 6 when raised |
| `scripts/agent-harness/gate-audit.ps1` | the append-only gate ledger and `Test-GateAuditComplete` — the executable definition of "complete" |
| `scripts/agent-harness/queue.ps1` | both gates wired: auto-pass under `dark`, refusal under a raised board, a record on every pass either way |
| `scripts/agent-harness/config.ps1` / `config.py` | gate resolution in both readers |
| `scripts/agent-harness/drill-dark-factory.ps1` | the validation: **60 checks, 0 failed** |
| `scripts/agent-harness/test_gate_profiles.py` | 12 tests incl. the PowerShell↔Python anti-drift test |

Evidence: `drill-dark-factory.ps1` → `60 checks, 0 failed` (exit 0).
`python -m pytest scripts/agent-harness -q` → `117 passed` (was 105 before this work).

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
  mechanically decidable shape: a function that runs git and can neither see nor report
  its failure. It does NOT catch an assertion that matches zero lines of the file it
  inspects, a `Test-Path` guard that skips itself, or a check whose exit code is
  discarded by its CALLER. Those are separate detectors nobody has written.
- **`policy-declared-unread` scope.** It is an identifier scan over dotted config paths,
  not a data-flow analysis. It proves a key is *referenced*, not that it is *honoured*.
  Its roots default to `pipeline` only — scalar policy knobs. Named data collections
  (`profiles`, `gate_profiles`, `andon.conditions`) are consumed generically by a loop,
  so the scan would say nothing true about them.

## 3. THE BOARD IS RED ON THIS REPOSITORY RIGHT NOW

`powershell -File scripts/agent-harness/andon.ps1 -Evaluate` from the work line:

```
ANDON BOARD: RAISED
  [ok           ] operator-checkout-off-branch   D:\Open WebUI\ai-stack is on 'refactor/ai-stack-cleanup'
  [ok           ] policy-declared-unread         all 3 policy keys under pipeline are read
  [fire         ] git-error-swallowed            2 function(s)
      - scripts/agent-harness/verify-merge-protocol.ps1:47 Invoke-DrillGit()
      - scripts/agent-harness/verify-merge-protocol.ps1:51 Get-DrillGit()
  [fire         ] work-branch-on-remote          11
      - work/dfu-u4, work/dfu-u4-lc, work/pod-key, work/u3gym, work/u4bidir, work/u4oracle,
        work/u4quad, work/u5judge, work/u5pplane, work/u5proxy, work/u6recall  (all on origin)
  [indeterminate] protected-ref-moved            no baseline recorded
```

**This is the mechanism working, not a false alarm.** Both fired conditions name the
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
  REAL repository and names the real incident's function. 60 checks, 0 failed.
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
  only — which `andon.ps1 -Evaluate -RunBranch work/u6dark` confirms).

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
          60 checks, 0 failed). A condition naming a predicate that does not exist is
          REFUSED, so the config cannot declare a detector nobody wrote.
STATE:    `andon.ps1 -Evaluate` on the work line is RAISED. `git-error-swallowed` names
          verify-merge-protocol.ps1:47 Invoke-DrillGit() and :51 Get-DrillGit() - the
          incident's own functions. `work-branch-on-remote` names the eleven work/*
          branches on origin. `protected-ref-moved` is indeterminate until a run records
          a baseline, and indeterminate is deliberately NOT a pass.
CONSEQUENCE, stated plainly: a `dark` run CANNOT auto-pass a gate in this repository
          until those clear. The mechanism is built and proven; the line it would run on
          is stopped. "Dark-factory mode is available" would be a false description of
          the current state.
NOT MINE TO CLEAR: the drill's git helpers belong to the incident note that owns them;
          the eleven remote branches are the operator's call, and DECISIONS already
          records why they were not deleted unilaterally.
REVERT:   `andon.enabled: false` in harness.config.json, or delete the `andon` block -
          `Get-AndonConditions` returns empty and every gate behaves as it did before.

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
          RED on four tampers: auto relabelled human, a deleted record, an auto pass
          claiming the board was raised, and an item it cannot audit. Proven GREEN again
          once restored, so it is not stuck red.
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
```
