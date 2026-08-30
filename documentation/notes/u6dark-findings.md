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
| `scripts/agent-harness/drill-dark-factory.ps1` | the validation: **96 checks, 0 failed** |
| `scripts/agent-harness/test_gate_profiles.py` | 12 tests incl. the PowerShell↔Python anti-drift test |
| `scripts/claude-sessions-bridge/test_queue_narration.py` | 6 tests pinning the one consumer that narrates a gate transition to a human (§8.7) |

Evidence: `drill-dark-factory.ps1` → `96 checks, 0 failed` (exit 0) — 60 before the audit
round of §8 added 36 more.
`python -m pytest scripts/agent-harness -q` → `117 passed` (was 105 before this work).
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
    its call sites are judged against the enclosing region's line numbers. Every
    function in the scanned families is at column 0.
  - **here-strings** (`@" ... "@`) are not tracked by the noise stripper, so a `git`
    inside one is still seen. That is a false POSITIVE, which is loud, not a miss.
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

`powershell -File scripts/agent-harness/andon.ps1 -Evaluate` from the work line:

```
ANDON BOARD: RAISED
  [ok           ] operator-checkout-off-branch   D:\Open WebUI\ai-stack is on 'refactor/ai-stack-cleanup'
  [ok           ] policy-declared-unread         all 7 policy keys under pipeline, andon are read
  [fire         ] git-error-swallowed            18 call site(s)
      - scripts/agent-harness/verify-merge-protocol.ps1:49 in Invoke-DrillGit()
      - scripts/agent-harness/verify-merge-protocol.ps1:53 in Get-DrillGit()
      - scripts/checks/check-project-configs.ps1:18 in (top level)
      - ...15 more, listed in §4.5
  [ok           ] work-branch-on-remote          checked 1 branch(es); none is on a remote
  [indeterminate] protected-ref-moved            no baseline recorded
  coverage: 5 declared, 5 evaluated, 0 switched off
```

That run is `andon.ps1 -Evaluate -RunBranch work/u6dark` — the branch-scoped question,
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
| `new-worktree.ps1:161,184`, `remove-worktree.ps1:92,111,115,118,158`, `queue.ps1:735,790` | listings that silently read as empty |

**Not mine to fix, and deliberately not fixed here.** Every one is a live pre-commit or
harness script; changing nine files' git handling inside an audit-fix item is the
enumerate-and-patch shape DECISIONS 2026-08-30 records as losing, and none of it is what
this item was anchored to. The `check-hook-attestation.ps1:67` case is arguably the same
adapter-by-contract exemption `git-io.ps1` has (`exclude_files`), with one difference: it
does not state that contract in the file, so it is left flagged rather than excused.

### 4.6 `queue.ps1` contains one non-ASCII byte

`queue.ps1:261` holds a UTF-8 `§` (`0xC2 0xA7`) inside a comment — the only non-ASCII
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
≥1 evaluated, none halted, none switched off. `partial` and `not-evaluated` are their own
states and both exit 6. The gate record carries all of it, `Test-GateAuditComplete`
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

**FIXED**, both ways. Mechanically: an absent or switched-off board is `not-evaluated`, the
gate refuses (exit 6), and the refusal is in the ledger saying `evaluated=0`. Documentally:
**the revert is `pipeline.gate_profile: attended`** — that is the switch that puts a human
back at the gate — and the corrected sentence is in the DECISIONS entry below, in
`MODULE.md` and in `README.md`.

**GREEN:** drill step F drives the real gate for three cases — `andon.enabled=false`, the
block deleted, and one condition switched off — and asserts exit 6, the item parked at
`anchor-draft`, and a ledger refusal recording `not-evaluated` / `not-evaluated` /
`partial` with `evaluated` 0 / 0 / 4.

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
- `verify-merge-protocol.ps1` was still NOT run, for the reason in §4.3 — and its two
  functions are still what `git-error-swallowed` names first.
- The eleven remote branches were not deleted and `work/u6dark` was not pushed.
- The 18 call sites of §4.5 were not fixed. They are real, they are other people's files,
  and fixing them is its own item.

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
          96 checks, 0 failed - 60 before the audit round below). A condition naming a
          predicate that does not exist is
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
REVERT:   `pipeline.gate_profile: attended` in harness.config.json - that is the switch
          that puts a human back at both gates, and it is the ONLY revert that restores
          prior behaviour. NOT `andon.enabled: false` and NOT deleting the `andon` block:
          under `dark` those remove the thing WATCHING the machine, not the machine, and
          an earlier draft of this entry said the opposite. Proven: with the block deleted,
          a dark -Submit on a detached checkout returned exit 0 / ready-to-test with the
          ledger reading `clear conditions=0`. It now halts at exit 6 with `not-evaluated`
          in the ledger (drill step F, three cases).

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
          evaluated, none halted, none switched off. `partial` (some evaluated, some
          switched off) and `not-evaluated` (nothing evaluated at all) are their own board
          states, both exit 6, and both refuse an unattended gate. Every verdict and every
          gate record carries `coverage {declared, evaluated, disabled, disabled_ids}` and
          the repository the board was looking at; Test-GateAuditComplete re-derives the
          claim from those counters instead of trusting the word `clear`.
PROVEN:   RED first, on a detached scratch repo - `clear/5/exit 0`. GREEN after -
          `not-evaluated`, exit 6, `declared=5 evaluated=0`. Three new ledger tampers go
          RED (evaluated forced to 0; the coverage fields stripped; the pre-existing status
          tamper). drill-dark-factory.ps1: 96 checks, 0 failed.
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
DECISION: The revert to prior behaviour is `pipeline.gate_profile: attended`, and only
          that. An absent or switched-off board is `not-evaluated`: the gate REFUSES (exit
          6), the item parks, and the refusal is in the ledger saying 0 conditions were
          evaluated. Corrected in the entry above, in MODULE.md and in README.md.
PROVEN:   drill step F drives the REAL gate for three cases - `andon.enabled=false`, the
          andon block deleted, and one condition switched off - asserting exit 6, the item
          parked at anchor-draft, and a ledger refusal recording not-evaluated /
          not-evaluated / partial with evaluated 0 / 0 / 4.
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
```
