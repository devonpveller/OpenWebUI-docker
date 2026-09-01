# u8h4 findings — DFU C.9 H4, wiring the verification machinery into CI

Branch `work/u8h4`. Base: `refactor/ai-stack-cleanup` @ `80fcc120c417d3846ef424f26b17870cfefec72e`.
OB1 gitlink under test: `b604d555f37bf79b14d6e5d0db73dec023305917`.
Everything below was measured on 2026-09-01 unless a line says otherwise.

Per PLAN.md §C.7b, this note is where this branch's validated-at sha lives.

---

## 1. What was built

`.github/workflows/ci.yml` gains six jobs plus a gate, and `.github/ci/expected-exit.ps1`
holds the exit-code contract they all read. The trigger list gains `development` and loses
`develop`.

| job | runs | pinned outcome |
|---|---|---|
| `dfu-static-checks` | the contract's own self-test; the drill's `-SelfTestLedger` and `-SelfTestVacuity`; `check-corpus-exposure-producers.ps1` bare and `-SelfTest` | all exit 0 |
| `dfu-boundary-drill` | `drill-personal-plane-exclusion.ps1 -AcceptDispositionedGaps` | exit 0 |
| `dfu-rls-boot-drill` | `drill-rls-boot-assertion.ps1` | exit 0; **3 = CANNOT-CHECK** is a distinct, differently-worded red |
| `dfu-app-role-drill` | `drill-app-role-not-superuser.ps1` | **exit 2 = CANNOT MEASURE, dispositioned**; 0 arrives as a nag |
| `dfu-prove-rls` | `prove-agent-memory-rls.ps1 -SkipLive` | exit 0 |
| `dfu-done` | `dfu-done.ps1 -SkipLive -Json` + census-integrity assertions | **exit 7 = board FAILED, expected**; exit 1 is red |
| `dfu-h4-gate` | `if: always()`, pinned needs list | fails on any non-`success`, and on any needs/pin mismatch |

---

## 2. THE HALF THAT IS BLOCKED, verified rather than relayed

`origin/development`'s `.github/workflows/ci.yml` is blob
`e9ff281aef1ce7ef5f2b16df610160ef668c4cad`, and its trigger reads:

```
on:
  push:
    branches: [main, develop, "feature/**", "refactor/**", "update/**"]
```

`git for-each-ref` over every local and remote ref returns **no ref named `develop`** —
neither `refs/heads/develop` nor `refs/remotes/origin/develop`. GitHub resolves a push
workflow from the ref being **pushed**, so a push to `development` runs `development`'s own
copy of this file, which triggers on a branch that has never existed.

**No edit on a feature branch can make CI run on `development`.** The trigger list in this
branch's ci.yml adds `development`, and that edit only becomes effective once the operator
promotes this line into `development`. H4's *"shown green on a CI run — not a local one"* is
therefore **dependent on that promotion** and is not delivered here. Adding `development` to
some other trigger would not be that proof and was not attempted.

`workflow_dispatch:` was added so the operator can run these jobs on demand the moment the
file lands on the line.

---

## 3. Evidence — every wired command, run locally in the form CI invokes it

Working directory for all of them is the repo root, which is what
`$GITHUB_WORKSPACE` is on a runner. Host: Windows PowerShell 5.1, Docker Desktop.

| command | exit | elapsed |
|---|---|---|
| `.\scripts\checks\drill-personal-plane-exclusion.ps1 -AcceptDispositionedGaps` | **0** | 117s / 104s |
| `.\scripts\checks\drill-personal-plane-exclusion.ps1 -SelfTestLedger` | **0** | <5s |
| `.\scripts\checks\drill-personal-plane-exclusion.ps1 -SelfTestVacuity` | **0** | <5s |
| `.\scripts\checks\drill-rls-boot-assertion.ps1` | **0** (56 passed, 0 failed, 0 blocked) | 268s / 267s |
| `.\scripts\checks\drill-app-role-not-superuser.ps1` | **2** (CANNOT MEASURE) | 1s |
| `.\scripts\checks\prove-agent-memory-rls.ps1 -SkipLive` | **0** (67 checks) | 38s / 37s |
| `.\scripts\checks\check-corpus-exposure-producers.ps1` | **0** (13 sites, 743 files) | ~10s |
| `.\scripts\checks\check-corpus-exposure-producers.ps1 -SelfTest` | **0** | ~10s |
| `.\scripts\checks\dfu-done.ps1 -SkipLive` | **7** (board FAILED) | 124s |
| `.\scripts\checks\dfu-done.ps1 -SkipLive -Json` | **7** | 121s |

Then every `shell: pwsh` block was extracted from the finished ci.yml **verbatim** and run
as its own script, so what was measured is the wiring and not the underlying command:

* all 12 blocks tokenize clean under `[System.Management.Automation.PSParser]`;
* blocks 01-10 executed from the repo root: every one exited 0, each emitting the
  `::notice::` the contract produces for its pinned code (including `app-role-drill exited 2
  as pinned` and `dfu-done exited 7 as pinned`).

The boundary drill's own summary line, quoted rather than re-typed:

```
PERSONAL-PLANE EXCLUSION DRILL: CONTAINMENT GREEN, 25 gap(s), ALL DISPOSITIONED
(106 checks passed, 0 failed)
```

---

## 4. Red-proofs — the new logic can fail, and was made to

A wiring nobody has seen fail is a wiring nobody has tested. All of these ran under
**pwsh 7.4 on Linux**, which is the runtime CI uses, not the Windows host.

**`.github/ci/expected-exit.ps1 -SelfTest`** forces the classifier through every outcome:
`green`, `nag`, `red`, and `no-contract`. Including: `dfu-done` code 99 -> red (a code the
contract does not enumerate never passes), and check name `no-such-check` -> refused. It
also asserts structural properties of the table — every green and nag code carries a
documented meaning, no code is both, no check lacks a green.

**The gate (`dfu-h4-gate`), five cases:**

| case | result |
|---|---|
| all six pinned jobs `success` | exit 0, "H4 GATE PASSED" |
| one job `skipped` | exit 1, "it NEVER RAN, so nothing it watches was measured" |
| one job `failure` | exit 1, "it RAN and objected" |
| a pinned job removed from `needs` | exit 1, "a job removed from needs is a check that stopped running; it does not make the gate smaller" |
| an unpinned job added to `needs` | exit 1, "add them to the pinned list so the gate states what it covers" |

**The `dfu-done` job's census assertions**, forced against a stub emitting controlled JSON
so the real workflow block was the thing under test:

| case | exit |
|---|---|
| good JSON + exit 7 (pinned) | 0 |
| good JSON + exit 0 (board DONE) | 0, with the PULL-THIS-PIN warning |
| `balances: false` | 1 |
| a clause in the `unrecognised` bucket | 1 |
| `integrity.ok: false` (the run moved the audited tree) | 1 |
| output containing no JSON | 1, "NOTHING WAS JUDGED — that is not a pass" |
| good JSON + exit 1 (usage error) | 1 |
| good JSON + exit 99 | 1 |

---

## 5. Runner reality — checked, not assumed

Against `actions/runner-images` `images/ubuntu/Ubuntu2404-Readme.md`, image `20260823.283.1`:
PowerShell **7.6.5**, Docker Client/Server **28.0.4**, Docker Compose **2.38.2**, Git
**2.55.0**, `python-is-python3` (so `python` is on PATH, which `dfu-done`'s walkthrough
commands use).

Four things were probed directly in a `mcr.microsoft.com/powershell:7.4-ubuntu-22.04`
container rather than reasoned about, because each was a candidate blocker:

1. **Windows-style path separators.** These scripts are full of
   `Join-Path $root "OB1\docker\docker-compose.yml"`. On Linux pwsh, `Join-Path`
   **normalises** the backslash: the result is `/…/OB1/docker/docker-compose.yml` and
   `Test-Path` finds it. Also verified in reverse (`$rel -replace '/', '\'` then joined) and
   through `Get-Content`. **Not a blocker.**
   *The one exception:* a raw .NET call given a literal backslash path — e.g.
   `[System.IO.File]::ReadAllBytes("/tmp/a\b\c.txt")` — throws. The same path built through
   `Join-Path` works, and that is how these scripts build theirs.
2. **`$env:TEMP` does not exist on Linux**, and six of these scripts stage fixtures under
   it. `Join-Path $null …` would abort. Fixed in the wiring, not the scripts: every DFU job
   sets `TEMP: ${{ runner.temp }}`. Verified: with `TEMP` set, `$env:TEMP` resolves and
   `New-Item` creates it.
3. **`.ps1` files are CRLF on every platform** (`.gitattributes`: `*.ps1 text eol=crlf`).
   A CRLF `.ps1` runs under Linux pwsh and propagates its exit code — proved with a
   byte-constructed CRLF script that exits 42.
4. **`Expand-Archive`** — used by the boundary drill to unpack the exported gitlink — is
   present.

**OB1 is a PUBLIC repo** (`api.github.com/repos/devonpveller/OB1` -> `"private": false`,
unauthenticated 200; `ai-stack` itself returns 404 unauthenticated) and the pinned commit
`b604d55` is a live ref tip on its remote (`refs/heads/feat/agent-memory-exposure-column`).
So `submodules: recursive` in `actions/checkout` needs no extra credential.

**Verdict: every wired script can run on `ubuntu-latest`, and none needed a change.**

---

## 6. What CI does NOT assert — stated, not discovered later

* **Nothing touches the live plane.** `-SkipLive` on `dfu-done.ps1` leaves clauses 3 and 8
  UNEVALUATED, and `-SkipLive` on `prove-agent-memory-rls.ps1` skips the production
  read-only assertion. There is no `openbrain-db` on a hosted runner; this is a limit, not
  a choice that can be undone by a flag.
* **The boundary drill under `-AcceptDispositionedGaps` asserts "nothing changed since the
  operator dispositioned these 25 gaps"**, not "U5's recording half is met". Each gap prints
  its own GREEN DOES NOT COVER line.
* **The per-clause bucket map of `dfu-done` is deliberately NOT pinned, and §10 shows why
  that was the right call rather than a cautious one.** The map is not stable across
  checkouts of the SAME commit: in the worktree it is `unmet` 1/2/4/5/7 and `unevaluated`
  3/8; in the clean clone of that identical sha, clause 4 moved to `unevaluated` — `unmet`
  1/2/5/7, `unevaluated` 3/4/8. Clause 4 counts unmerged `work/*` branches, and a fresh
  clone has none. A Linux runner will differ again (finding F3). A pinned map would have
  gone red on the clean clone for no defect at all, which is the snapshot H4 exists to
  prevent. What IS pinned instead are platform-independent integrity properties of the run:
  the census balances, no clause lands in `unrecognised`, and the run did not move the
  audited tree. All three held in both checkouts.
* **These jobs have never executed on a Linux runner.** The exit-code contract file and the
  gate block were executed under pwsh 7 on Linux; the DRILLS were measured on Windows
  PowerShell 5.1 only. Closing that gap requires a CI run, which requires section 2's
  promotion.

---

## 7. Serialisation (PROMOTION-RUNBOOK.md), honoured two ways

`prove-agent-memory-rls.ps1` ends with a read-only assertion that the live `openbrain-db`
holds zero personal rows; `dfu-done.ps1` clause 3 **plants** a personal-exposure fixture in
that same database for the duration of its door probes. Concurrent, `prove-rls` exits 1 with
"production is not clean" — a true reading at that instant and a defect in neither script.

1. Both jobs run with `-SkipLive`, which removes the live database from both sides entirely.
2. `dfu-done` declares `needs: [dfu-prove-rls]`, so they cannot overlap even if a flag is
   dropped later.

On GitHub-hosted runners each job is a fresh VM with its own docker daemon and no live
`openbrain-db` at all, so (1) alone is sufficient today. (2) is the insurance for the day
these move to a self-hosted runner that CAN see production — which is the only way the live
clauses could ever run, and therefore the only way the collision could ever be real.
No `|| true` and no retry loop was added; the runbook is explicit that either deletes the
only check watching the live plane.

---

## 8. Findings outside H4's scope — dated lines, per §C.10

**F1 (2026-09-01) — `prove-agent-memory-rls.ps1` has no CANNOT-CHECK exit code.**
Its `catch` increments `$script:Fail` and the script exits **1**, which its own vocabulary
means "a check failed". So an image pull failure, a docker daemon hiccup or a `docker run`
error is reported with the same code as a genuine boundary failure. This is exactly the
distinction `drill-rls-boot-assertion.ps1` and `assert-rls-force.sh` reserve **3** for, and
exactly the confusion H2 spent three rounds removing — it simply was never applied to this
script. The exit-code contract records the ambiguity in `prove-rls`'s `Meaning` for code 1
so a reader is not misled, but the contract cannot invent a distinction the script does not
make. **Not fixed here: H4 wires the machinery, it does not rewrite the checks, and U8 is
scope-frozen.** Owner: whoever next touches that script.

**F2 (2026-09-01) — `check-corpus-exposure-producers.ps1` floors at zero sites, not at a
site count.** Verified: pointed at a one-file tree with `-Root`, it exits **1** with "the
scan examined 1 file(s) and found ZERO corpus insert sites … a clean result here is VACUOUS,
not green". So a completely-excluded population cannot pass. It does **not** floor on the
number of sites, so a partial checkout that still finds one site would report green over a
shrunken population. The wiring pins `submodules: recursive` on the job rather than relying
on the gate to notice; the residual is recorded here rather than fixed.

**F3 (2026-09-01) — `dfu-done.ps1`'s walkthrough commands include a Windows-only path.**
The run's own executed-command list contains
`agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest agent-org/agent-bridge/tests/test_recall_seams.py -q`
(clauses 1 and 5, phase U2). `\.venv\Scripts\` is the Windows venv layout; a Linux runner
has `.venv/bin/`. That command will fail on the runner. It does not change the exit code —
the board is already FAILED and those clauses are already `unmet` — but it means a CI run of
`dfu-done` asserts strictly less than a Windows run of it, and the difference is invisible
in the exit code. This is why §6 pins the run's integrity properties instead of the clause
map. Owner: WALKTHROUGH.md's U2 row.

**F4 (2026-09-01) — hosted-runner resource envelope for the boundary drill is untested.**
It exports the gitlink, builds three images (openbrain-mcp, openbrain-gateway,
extensions-server) and runs roughly ten containers plus a pgvector database. Measured 104-117s
on this machine, which has 81 containers already running. GitHub's standard hosted runner is
2-core with ~14 GB free disk. `timeout-minutes: 60` is set so a hang is a timeout rather than
a six-hour burn, but the disk and CPU envelope will only be known from the first real run.

**F5 (2026-09-01, round 2) — `check-corpus-exposure-producers.ps1`'s allow-list is written
in backslashes and does not match on Linux.** Measured, with the planted producer and the
gate's own contradicting disclosure quoted, in **§12** below. The drift is in the safe
direction, but the same tree can be green on the Windows pre-commit hook and red in CI, and
the gate's printed statement of its own coverage is platform-dependent. Owner: U5's gate.

---

## 9. Not in scope, not touched

* `scripts/checks/dfu-done.ps1` — **not edited** (concurrent branch `u8floor` owns it).
* `PLAN.md`, `DECISIONS.md` — not edited.
* No production container, network, image tag or database was created, changed or removed.
  Every drill run used its own throwaway containers on throwaway networks; `-SkipLive` kept
  both live-plane paths shut.

---

## 10. Clean-clone re-run (§C.7b)

**Validated on branch `work/u8h4`** (base `refactor/ai-stack-cleanup` @ `80fcc12`), at commit
`7b1c207ce4b96a36182a690d8932ce864c4b62da`.

That commit was later amended to add THIS section, which changes the commit sha while
changing none of the wiring. So the anchor recorded here is the **tree hash of the artifact
under test**, which the amendment cannot move:

```
git rev-parse HEAD:.github   ->   689ca91690c995ddbed98e273de9d215f8bad0e5
```

Measured identical at `7b1c207`, at the amended commit, and in the clone the transcript
below came from. A commit sha alone would have gone stale the moment this paragraph was
written; the tree hash is what the run actually exercised. Re-verify it before reading any
row below as current.

```
git -c core.longpaths=true clone --recurse-submodules <worktree> D:/h4cc
```

Asserted before anything ran, per the clean-clone rule:

| property | value |
|---|---|
| `git status --porcelain` | **0 lines (EMPTY)** |
| `git rev-parse HEAD` | `7b1c207ce4b96a36182a690d8932ce864c4b62da` |
| tracked files (parent) | 1087 |
| `git -C OB1 rev-parse HEAD` | `b604d555f37bf79b14d6e5d0db73dec023305917` — equals the recorded gitlink |
| `git -C OB1 status --porcelain` | **0 lines** |
| tracked files (OB1) | 981 |

The `shell: pwsh` blocks were then re-extracted **from the clean clone's own ci.yml** (it
parses as YAML there, 12 blocks, 14 jobs) and each was run from the clone root:

| block | exit | elapsed |
|---|---|---|
| `powershell-parse` (the pre-existing job, since ci.yml changed) | 0 | 3s |
| `dfu-static-checks` step 1 — the contract's self-test | 0 | 1s |
| `dfu-static-checks` step 2 — `-SelfTestLedger` | 0 | 1s |
| `dfu-static-checks` step 3 — `-SelfTestVacuity` | 0 | 1s |
| `dfu-static-checks` step 4 — corpus producers | 0 | 18s |
| `dfu-static-checks` step 5 — corpus producers `-SelfTest` | 0 | 1s |
| `dfu-boundary-drill` | 0 | 103s |
| `dfu-rls-boot-drill` | 0 (56 passed, 0 failed, 0 blocked) | 267s |
| `dfu-app-role-drill` | 0 (drill exited 2, classified as pinned) | 1s |
| `dfu-prove-rls` | 0 | 37s |
| `dfu-done` | 0 (board exited 7, classified as pinned) | 119s |

The `$GITHUB_STEP_SUMMARY` the run produced, in full — one row per wired check, each
naming the exit code and what the contract says it means:

```
| boundary-drill-selftest-ledger     | 0 | PASS | a CLOSED gap contributes 0 failures; ...
| boundary-drill-selftest-vacuity    | 0 | PASS | an empty universe cannot reach PASS
| corpus-exposure-producers          | 0 | PASS | every RECOGNISED corpus insert site states its plane
| corpus-exposure-producers-selftest | 0 | PASS | the gate's own planted cases classify as recorded
| app-role-drill                     | 2 | PASS | CANNOT MEASURE - ... EXPECTED TODAY.
| prove-rls                          | 0 | PASS | every green had a red beside it and both agreed
| boundary-drill                     | 0 | PASS | containment green and every gap that fired is dispositioned
| rls-boot-drill                     | 0 | PASS | every scenario behaved as required
| dfu-done                           | 7 | PASS | the plan is NOT met, and the run says which clauses.
```

**One difference from the worktree run, and it is the useful one.** `dfu-done`'s census in
the clean clone was `unmet 4 / unevaluated 3` against the worktree's `unmet 5 /
unevaluated 2`: **clause 4 moved from `unmet` to `unevaluated`**, because it counts unmerged
`work/*` branches and a fresh clone has none. Same commit, same script, different bucket.
Had the wiring pinned the per-clause map, this clean-clone run — the one C.7b requires
before a merge — would have gone red for no defect. The three properties that ARE pinned
(census balances, zero `unrecognised`, `integrity.ok`) held identically in both.

**Still not proven from a GitHub runner:** that a real Actions run goes green. Section 2
establishes why a CI run cannot be triggered from a feature branch, and that is unchanged.
What section 11 below adds, and what round 1 did NOT have, is that the wired steps have now
been run on **Linux under pwsh 7.4** in GitHub's exact step wrapper, from the clean clone —
which is where round 1's green was found to be false.

---

# ROUND 2 — 2026-09-01

## 11. THE BLOCKER: the wiring turned "the check did not run" into GREEN

Both verifiers found this independently and the orchestrator reproduced the mechanism. It is
the defect this whole H4 item exists to prevent, built into the thing meant to prevent it.

### 11.1 The mechanism, measured twice

Every round-1 step had the shape `& ./check.ps1` then `$c = $LASTEXITCODE` then
`-Code $c`, with `expected-exit.ps1` declaring `[int]$Code = -1` and refusing anything below
zero. **That sentinel could never fire.** Measured in
`mcr.microsoft.com/powershell:7.4-ubuntu-22.04`, and again under Windows PowerShell 5.1:

```
[int]$null  ->  0            (the binder converts before any test in the script body runs)
```

So a check that never reached an `exit` arrived as the integer **0**, and every check whose
`Green` set contains 0 — eight of the nine — **passed**.

**A second measurement changed the design, and it is the one a type fix alone does not
close.** In the same rig, a script that runs one successful native command and then dies
inside its own trap leaves the CALLER holding:

```
victim: LASTEXITCODE after bash = 0
HARNESS ERROR: The term 'cmd' is not recognized ...
caller: LASTEXITCODE is null? False  value=[0]
caller: $? = True
```

`$LASTEXITCODE` is **stale, not null**, and `$?` is `True`. Nothing in the calling scope can
tell that from a check that deliberately exited 0. Every drill here shells out to `docker`
before it can fail, so the stale case is the likely one, not the exotic one. This is why the
fix is a wrapper and a child process rather than a cast.

### 11.2 It was live, and it was this drill

`scripts/checks/drill-app-role-not-superuser.ps1` cleaned up with
`cmd /c "docker rm -f $dbName 2>nul"`. **cmd.exe does not exist on ubuntu-latest.** Every
abort path in that drill is `Cleanup; exit 2` and the trap was itself
`Say ...; Cleanup; exit 2`, so the failure recursed and the drill died without reaching any
exit statement.

Reproduced here with the drill sitting where it really lives (so `$PSScriptRoot` resolves),
under pwsh 7.4 on Linux, at gitlink `b604d55`:

```
== 0. initdb chain
  compose mounts 29 init files
  staged 29
  ABORT: CANNOT MEASURE - the migration under test is not in this checkout:
         /tmp/repo/OB1/docker/init-app-role.sql
HARNESS ERROR: The term 'cmd' is not recognized as a name of a cmdlet, ...
   captured $c = []  isnull=True
```

The drill printed the paragraph that says it could not measure, then died before it could
exit 2. Round 1's wiring turned that empty `$c` into `-Code 0` and annotated
*"the drill RAN and every probe passed ... PULL THIS PIN"*, job GREEN.

### 11.3 The counter-proof: 9 of 10 round-1 steps were green with the script gone

Not asserted — run. The round-1 step bodies were extracted from `a319cdf`'s **own** `ci.yml`
in a separate clean clone, each script renamed away, and each step run in GitHub's exact
`shell: pwsh` wrapper (`$ErrorActionPreference='stop'` prepended, `. '<file>'`,
`if ((Test-Path -LiteralPath variable:/LASTEXITCODE)) { exit $LASTEXITCODE }` appended):

```
GREEN = STILL BROKEN  step01  the exit-code contract's own self-test              exit 0
GREEN = STILL BROKEN  step02  gap-ledger self-test                                exit 0
GREEN = STILL BROKEN  step03  vacuity-guard self-test                             exit 0
GREEN = STILL BROKEN  step04  corpus exposure producers                           exit 0
GREEN = STILL BROKEN  step05  corpus exposure producers - planted cases           exit 0
GREEN = STILL BROKEN  step06  drill-personal-plane-exclusion -AcceptDisposition..  exit 0
GREEN = STILL BROKEN  step07  drill-rls-boot-assertion.ps1                        exit 0
GREEN = STILL BROKEN  step08  drill-app-role-not-superuser.ps1                    exit 0
GREEN = STILL BROKEN  step09  prove-agent-memory-rls.ps1 -SkipLive                exit 0
RED-BUT-WRONG-WORDS   step10  dfu-done.ps1 -SkipLive                              exit 1
```

`dfu-done` survived only because its JSON/census assertions refuse independently of the
code — exactly as reported. The GitHub suffix is the mechanism: with no `LASTEXITCODE` it
does not `exit` at all, and the step succeeds.

**And dfu-done's survival was narrower than the counter-proof shows.** The rename is only
one shape. Measured with a `dfu-done` stub that emits a VALID, balancing, `unrecognised: 0`,
`integrity.ok: true` JSON object, runs one successful native command, and THEN dies inside
its own trap exactly the way the app-role drill did on ubuntu:

```
ROUND 1:  dfu-done.ps1 -SkipLive -Json exited 0
          board=FAILED done=False balances=True total=9
          ::warning::dfu-done exited 0, which is BETTER than the pinned state. every clause
          MET. The board is DONE. PULL THIS PIN: move 0 into Green, drop 7, and hand over
          (PLAN.md C.10, 'THE STOP IS REAL').
          >>> STEP EXIT = 0

ROUND 2:  ::error::dfu-done DID NOT RUN: it terminated without reaching an exit statement.
          >>> STEP EXIT = 1
```

Every census assertion passed in the round-1 run — they were reading real JSON from a run
that never finished — and the stale 0 landed on `dfu-done`'s NAG entry. **CI would have
told the operator the board was DONE and to hand over.** The one job the verifiers found to
be safe was safe only against the shape where the JSON is missing too.

### 11.4b The one thing the fix CHANGES about how a check runs

The shim's top-level `trap` is what detects "died without reaching an exit", and a trap
anywhere on the call stack makes a **statement-terminating** error unwind to it instead of
being written and stepped over. That is a real change to how the wired checks execute, so it
was measured in both directions rather than waved at — pwsh 7.4/Linux, child script under
`$ErrorActionPreference = "Continue"`:

| in the check | without an enclosing trap | with one (the shim) |
|---|---|---|
| `Copy-Item` on a missing path | written, script continues | **unchanged** — non-terminating |
| `Get-Content` on a missing path | written, script continues | **unchanged** |
| a native command exiting 3 | continues | **unchanged** — not an error |
| `1/0` | written, script continues, `exit 7` reached | **unwinds** -> DID NOT RUN |
| `[int]::Parse("nope")` | continues | **unwinds** -> DID NOT RUN |

So a check that throws an unhandled terminating exception halfway and then carries on to
print a verdict now reports DID NOT RUN instead of that verdict. **Deliberate.** It is the
same defect `drill-app-role-not-superuser.ps1`'s own comments already name — *"Exit 2 by
luck rather than by check"* — and a verdict from a half-executed run is precisely what H4
exists to stop CI reading as green. It is also the safe direction: the failure mode this
introduces is a red that should have been green, never the reverse. Named here so that if
one of the four heavy jobs goes red on its first Linux run for this reason, the cause is on
record rather than rediscovered.

**And it is load-bearing, not optional.** Without the trap the shim cannot see the blocker
at all: measured, a child that dies this way returns to the caller "normally", leaving the
stale code. Removing the trap restores the green bug.

### 11.5 Red-proof, the way the verifiers broke it — 11 of 11 red

From the **clean clone** at `e449e02` (`git status --porcelain` empty, `.github` tree
`b62c3b857d30fc9a1e5cfcae7ec93eb0f2dd9711`), step bodies re-extracted from the CLONE's own
`ci.yml`, each driven script renamed away, each step run in GitHub's exact wrapper:

```
PASS  step01  the exit-code contract, printed             exit 1  "could not even be printed"
PASS  step02  the exit-code contract's own self-test      exit 1  "expected-exit-selftest DID NOT RUN"
PASS  step03  gap-ledger self-test                        exit 1  "boundary-drill-selftest-ledger DID NOT RUN"
PASS  step04  vacuity-guard self-test                     exit 1  "boundary-drill-selftest-vacuity DID NOT RUN"
PASS  step05  corpus exposure producers                   exit 1  "corpus-exposure-producers DID NOT RUN"
PASS  step06  corpus exposure producers - planted cases   exit 1  "corpus-exposure-producers-selftest DID NOT RUN"
PASS  step07  boundary drill -AcceptDispositionedGaps     exit 1  "boundary-drill DID NOT RUN"
PASS  step08  drill-rls-boot-assertion.ps1                exit 1  "rls-boot-drill DID NOT RUN"
PASS  step09  drill-app-role-not-superuser.ps1            exit 1  "app-role-drill DID NOT RUN"
PASS  step10  prove-agent-memory-rls.ps1 -SkipLive        exit 1  "prove-rls DID NOT RUN"
PASS  step11  dfu-done.ps1 -SkipLive                      exit 1  "dfu-done DID NOT RUN"

# RED-PROOF PASSED: all 11 wired steps go red saying the check did not run.
```

Four further shapes, proved against the wrapper directly rather than by renaming — because
the point is that the NEXT shape of "never reached exit" fails too, not that one enumerated
list of shapes does:

| shape | outcome |
|---|---|
| the OLD drill verbatim, on ubuntu (dies in its trap on `cmd` after printing its verdict) | RED, *"DID NOT RUN: it terminated without reaching an exit statement"* |
| a check that dies BEFORE running anything native (the sentinel path) | RED, same wording |
| a check that RAN and reported a red code (3, `rls-boot-drill`) | RED, and worded as CANNOT-CHECK, not as a broken boundary |
| a check that RAN and reported a better-than-pinned code (0, `app-role-drill`) | GREEN + *PULL THIS PIN* — the nag rule survives the rewrite |

### 11.6 The drill actually runs on Linux now

`cmd /c "... 2>nul"` is replaced by `Invoke-DockerQuiet` — `& docker` with PowerShell's own
`2>&1 | Out-Null`, inside `try/catch/finally`, so a missing docker AND a container that is
not there are both swallowed there rather than becoming the run's verdict. `Cleanup` cannot
raise. The `trap` cannot re-enter itself (`$script:inTrap`).

Measured under pwsh 7.4 on Linux with a docker CLI and the host socket, against the recorded
gitlink `b604d55`, from the clean clone with `OB1/docker` placed as `submodules: recursive`
would place it:

```
== 0. initdb chain
  compose mounts 29 init files
  staged 29
  ABORT: CANNOT MEASURE - the migration under test is not in this checkout:
DRILL EXIT CODE = [2]  isnull=False
::notice::app-role-drill exited 2 as pinned: CANNOT MEASURE - ... EXPECTED TODAY.
>>> STEP EXIT = 0   (2.2s)
```

A real exit code, through the Cleanup path, classified as the pinned state.

### 11.7 The wired steps that DO run on Linux, run — from the clean clone

Same clone, same wrapper, real runs (not red-proofs):

| step | job | exit | step | sec |
|---|---|---|---|---|
| the exit-code contract, printed | dfu-static-checks | - | 0 | 0.7 |
| the contract's own self-test | dfu-static-checks | 0 | 0 | 1.3 |
| gap-ledger self-test | dfu-static-checks | 0 | 0 | 1.4 |
| vacuity-guard self-test | dfu-static-checks | 0 | 0 | 1.3 |
| corpus exposure producers | dfu-static-checks | 0 | 0 | 12.8 |
| corpus exposure producers - planted cases | dfu-static-checks | 0 | 0 | 1.6 |
| drill-app-role-not-superuser.ps1 | dfu-app-role-drill | 2 | 0 | 2.2 |

**NOT run on Linux, and named rather than implied:** `dfu-boundary-drill`,
`dfu-rls-boot-drill`, `dfu-prove-rls` and `dfu-done`. Each builds images or a `--shared`
clone and takes minutes; they were measured on Windows in section 3 and in the clean-clone
re-run in section 10. With the wiring fixed, a Linux-only breakage in any of them is now
RED and says so, instead of green. That is the difference this round was for.

### 11.8 A defect found in my own wrapper while proving it

`& $script @("-SelfTest")` splats **positionally**: the string `-SelfTest` landed in
`expected-exit.ps1`'s first positional parameter (`-Check`) instead of setting the switch.
The wiring reported it RED — correctly, because the mangled call reported no usable code —
which is the behaviour working, but the mangling was still a defect. Arguments are now
splatted as a hashtable, and `run-check.ps1` **refuses** any argument that is not a bare
switch rather than guessing how to pass it. Recorded because "the wrapper reported red" is
not the same as "the wrapper was right".

## 12. Windows-only assumptions in the wired scripts — the sweep

> **CORRECTED 2026-09-01 (round 3). The scope sentence below was WRONG, and the wrong
> scope is how the round-2 sweep missed the round-3 blocker.** It read "all six wired
> scripts". The wired set is **eight**: the seven `-Script` targets in `ci.yml`
> (`expected-exit.ps1`, `check-corpus-exposure-producers.ps1`, `dfu-done.ps1`,
> `drill-app-role-not-superuser.ps1`, `drill-personal-plane-exclusion.ps1`,
> `drill-rls-boot-assertion.ps1`, `prove-agent-memory-rls.ps1`) plus `run-check.ps1`
> itself, which runs in every one of those steps. `dfu-done.ps1` was omitted because
> another branch owned the file — an OWNERSHIP boundary silently became a MEASUREMENT
> boundary, the table said "all", and `dfu-done.ps1` held the one blocking defect
> (`[WindowsIdentity]::GetCurrent()`, unconditional, first call of the main body). A sweep
> that names its own scope wrongly is how the next one misses. The eight are now enumerated
> mechanically from `ci.yml` rather than listed by hand:
> `grep -oE "-Script '[^']+'" .github/workflows/ci.yml | sort -u`, plus the wrapper.

Asked for after `$env:TEMP` turned up once. Grepped all **eight** wired scripts plus
`scripts/checks/lib/ob-initdb.ps1` for `cmd`, `2>nul`, `$env:TEMP`, `.exe`, backslash
literals, `Get-CimInstance`/`Get-WmiObject`. Measured, not assumed:

| assumption | where | verdict |
|---|---|---|
| `cmd /c "... 2>nul"` | `drill-app-role-not-superuser.ps1` lines 108-109, 194-196 (pre-fix) | **THE BLOCKER — FIXED this round.** The only occurrences in any wired script. |
| `Join-Path $repo "OB1\docker"` and similar backslash literals | all four drills, `prove-agent-memory-rls.ps1` | **NOT a problem.** Measured on pwsh 7.4/Linux: `Join-Path '/repo' 'OB1\docker'` -> `/repo/OB1/docker`. pwsh normalises the separator. |
| `$env:TEMP` for staging dirs | boundary drill (5 sites), rls-boot drill, app-role drill, prove-rls, corpus gate self-test | **Handled**, and already was: every H4 job sets `TEMP: ${{ runner.temp }}`. |
| `Get-CimInstance` / `Get-WmiObject` / `.exe` / COM | none | absent from all wired scripts. |
| `ob-initdb.ps1` (dot-sourced by three drills) | - | clean: `& docker` throughout, no `cmd`, no shell redirects. |
| `[WindowsIdentity]::GetCurrent()`, `Get-Acl`/`Set-Acl`, `Invoke-Native -Exe "cmd.exe"` | `dfu-done.ps1` — **the script this table failed to cover** | **THE ROUND-3 BLOCKER — FIXED 2026-09-01.** See §15. |
| `run-check.ps1` (the wrapper, in every step) | - | clean: no Windows-only call. Its host-shell discovery is `[System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName` with a `Get-Command pwsh`/`powershell` fallback, and its marker path is `[System.IO.Path]::GetTempPath()` — both cross-platform. Exercised on Linux as the wrapper of all ten invocations in §15.4. |

**One real difference found, and it is NOT fixed here (§C.10 — a dated line, not a change):**

**`check-corpus-exposure-producers.ps1`'s allow-list is written in backslashes and does not
match on Linux.** `$allowPathLike` holds `'*\documentation\*'`, `'*\docs\*'`,
`'*\scriptsrchive\*'`, `'*
ode_modules\*'`, and `-like` is a literal glob — on Linux the
paths contain `/`, so none of them match. Measured 2026-09-01 in the clean clone, by
planting one unlabelled producer at `documentation/zz-redproof-producer.mjs`:

```
[check-corpus-exposure-producers] FAIL - 1 of 2 recognised corpus insert site(s) do not state a plane:
  /documentation/zz-redproof-producer.mjs:2
GATE EXIT = 1
```

while the same gate's own disclosure block, printed in the same run, says:

```
  - anything under a path on the ALLOW-LIST below - notably documentation\ and docs\.
    A producer living under either is never scanned.
```

That sentence is **false on Linux**. The drift is in the safe direction (the gate scans
MORE, not less), so this is not an exposure hole — but it means **the same tree can be green
on the Windows pre-commit hook and red in CI**, and the gate's printed statement of its own
coverage is platform-dependent. It also means the `-SelfTest` case that plants a producer
under `docs\` to record the miss is recording a miss that does not occur on the runner.
Fixing it is a change to U5's gate, owned elsewhere, and out of H4's scope. Sink: this file.

Related and unmeasured: the same script's `$f.Substring($ScanRoot.Length).TrimStart('')`
will leave a leading `/` on Linux relative paths. Cosmetic in the report; not chased.

## 13. What round 2 did NOT touch

- `scripts/checks/dfu-done.ps1` — u8floor owns it. Its step is rewired (it now goes through
  `run-check.ps1` in `-CodeFile` form, so the census assertions still run BEFORE the contract
  is consulted), but the script is unchanged.
- `PLAN.md`, `DECISIONS.md` — not edited.
- The six jobs, the single contract table, the cannot-check wording, the nag-not-fail rule,
  the `prove-rls` -> `dfu-done` serialisation, the `.github` tree hash as the validation
  anchor, and the decision NOT to pin the per-clause map: all kept exactly as accepted.
- Nothing in production was started, stopped or written to. The Linux runs used throwaway
  containers of the `mcr.microsoft.com/powershell` image; the only host-daemon calls the
  drill made were `docker rm -f wt-u8h1-h1db` / `docker network rm wt-u8h1-h1net` on names
  that do not exist.

## 14. Round-2 validation anchor

| | |
|---|---|
| branch | `work/u8h4` |
| commit validated | `e3388a2` (`e449e02` = the fix; `e3388a2` = the semantic-change documentation + this note) |
| `.github` tree hash | `49d29ad34624bb429e4399266cf86d7b11528669` |
| clean clone | `git -c core.longpaths=true clone --no-local -b work/u8h4`; `git -c core.longpaths=true status --porcelain` **empty** before any run |
| counter-proof clone | same method, checked out at `a319cdf` (round 1), porcelain empty |
| Linux host | `mcr.microsoft.com/powershell:7.4-ubuntu-22.04`, plus a one-layer image adding `docker:cli` for the drill and the host docker socket |
| step bodies | re-extracted from each CLONE's own `.github/workflows/ci.yml` with PyYAML; never hand-copied |
| step invocation | GitHub's `shell: pwsh` wrapper, verbatim: `$ErrorActionPreference='stop'` prepended, `. '<file>'`, `if ((Test-Path -LiteralPath variable:/LASTEXITCODE)) { exit $LASTEXITCODE }` appended |
| red-proof | 11 of 11 wired steps red, each saying the check did not run |
| real runs | 7 steps run for real on Linux from the clone, all as pinned (see §11.7) |

**The clean-clone method bit again, and the existing note called it.** The second clone lived
at a path one character longer than the first, which pushed
`documentation/implementation-guide/teams-chat-agent-orchestration/Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md`
past MAX_PATH: `git status --porcelain` reported it MODIFIED with `Filename too long`.
`-c core.longpaths=true` on the `clone` command does NOT persist into the cloned repo's
config, so **every later git command in the clone needs it too** — with it, porcelain is
empty and nothing is missing. Same defect class as
`documentation/notes/clean-clone-maxpath-validation-trap.md`, one step further along: that
note is about the clone silently losing files, this is about the VERIFICATION command in the
clone silently reporting a dirty tree. An agent asserting "porcelain empty" from a bare
`git status` in a deep clone is asserting something the tool could not measure.
---

## 15. Round 3 (2026-09-01) — §C.10: `dfu-done.ps1` could not reach an exit code on Linux

### 15.1 The defect, and why round 2 could not see it

`scripts/checks/dfu-done.ps1` called
`[System.Security.Principal.WindowsIdentity]::GetCurrent()` inside
`Clear-DfuTreeProtection`, which the main body calls **unconditionally, as its first
action**. On Linux pwsh that call raises *"Windows Principal functionality is not
supported on this platform"*. There was no `$IsWindows` gate anywhere in the file.

Two directions, both reproduced here from a clone rather than taken on report:

| stack | outcome |
|---|---|
| no trap on the stack | the error is written and stepped over; the run continues and exits 7 |
| **with** a trap on the stack | it unwinds at that line and **never reaches an exit statement** |

`.github/ci/run-check.ps1` installs exactly such a trap in its shim, which is how CI
invokes the file. Measured 2026-09-01 in a `mcr.microsoft.com/powershell:7.4-ubuntu-22.04`
container plus a one-layer image adding `git`, against the **unpatched** file at the
round-2 tip, through the real `-Stdout`/`-CodeFile` step body:

```
RUN-CHECK SHIM: the check terminated WITHOUT reaching an exit statement: Exception calling
"GetCurrent" with "0" argument(s): "Windows Principal functionality is not supported on this platform."
[ERROR] dfu-done DID NOT RUN: it terminated without reaching an exit statement. ...
=== run-check rc = 1 ===
STEP RESULT: RED (run-check says the check did not behave as pinned / did not run)
```

`dfu-h4-gate` `needs: dfu-done`, so the whole H4 section reds. **Invisible on Windows**,
where the same call succeeds — which is why round 2's own measurement of this file showed
green. The measurement was real; the platform it was taken on was not the one CI runs.

### 15.2 What was changed, and the honest cost

A single `Get-DfuPlatform` decides once, **feature-detected rather than inferred from the
OS name**: `$IsWindows` via `Test-Path Variable:IsWindows` (5.1 has no such variable), AND
`Get-Command Get-Acl`/`Set-Acl`, AND a trial `GetCurrent()` in a try/catch. Four functions
gate on it: `Clear-DfuTreeProtection`, `New-DfuDenyRule` (which *throws* rather than
returning nothing, so a future caller that forgets the gate fails loudly),
`Protect-AuditedArtifacts`, `Unprotect-AuditedArtifacts`.

**The tree protection is NOT disabled on Windows.** `verify-dfu-done.ps1` still passes and
the Windows JSON still reports `write_lock = "applied per command (Deny ACE for the running
identity)"` with two locked directories on every one of the six executed commands.

**Off Windows it is a declared no-op, and the reason is stated in the run output and in the
JSON rather than skipped silently.** There is no POSIX equivalent of "deny the identity this
process is already running as": a file mode cannot deny the owner, who can chmod it back
with one call, and modes do not constrain root at all. Shipping one under that name would be
a check that is green while checking nothing. What survives is the half the file itself
calls load-bearing and which is platform-independent — the pre-run snapshot (no clause ever
reads anything the run could have created) and the before/after fingerprint (an effect that
gets through is still reported and still vetoes the board). **Off Windows the containment is
detect-and-refuse instead of prevent-and-detect.** That is weaker; it is why it is printed:

```
   containment: write-lock NOT APPLIED - not Windows - a Deny ACE for the running identity
     has no POSIX equivalent ...
   containment: the pre-run snapshot and the before/after fingerprint DID run, so an effect
     is still detected and still vetoes the board - detect-and-refuse, not prevent-and-detect.
```

### 15.3 The interpreter, and a false red it would have produced

`Invoke-AuditedCommand` hard-coded `Invoke-Native -Exe "cmd.exe"`. It now takes the
platform's shell (`/bin/sh -c` off Windows) and **records which one ran** on each
executed-command entry.

The substitution changes what a non-zero exit MEANS, so `New-CommandProbeBody` now returns
**indeterminate, never `fail`, for a non-zero exit under a substituted interpreter** — and
still `pass` for a real 0. This is not defensive padding; it was measured. All six
walkthrough commands the run executes exited **127** under `/bin/sh` in the probe container
(`python` absent; one of them is literally
`agent-org/agent-bridge/.venv/Scripts/python.exe`). Without the rule the run would have
reported six red probes asserting the walkthrough's named checks are broken — a fact about
the interpreter presented as a fact about the subject, this effort's recurring defect.

### 15.4 The proofs

**Linux, under run-check's trap, real `-Stdout`/`-CodeFile` step body** — `dfu-done`
reached **exit 7** and the step classified it as the pinned green:

```
RUN-CHECK: dfu-done RAN and reported exit 7; classification deferred to the caller.
=== run-check rc = 0 ===
dfu-done.ps1 -SkipLive -Json exited 7
board=failed done=False balances=True total=8
census: {"unrecognised":0,"unmet":5,"unevaluated":2,"manual_pending":1,"met":0}
platform: {"os":"Unix 6.6.87.2","ps":"7.4.6","windows":false,"acl_supported":false,...}
integrity.ok = True
[NOTICE] dfu-done exited 7 as pinned: the plan is NOT met, and the run says which clauses.
=== expected-exit rc = 0 ===
STEP RESULT: GREEN (classified as the pinned outcome)
```

All three step assertions hold on Linux: `balances=True`, `unrecognised=0`,
`integrity.ok=True`. The non-JSON report was also run on Linux **under
`Set-StrictMode -Version Latest`** (which run-check's shim inherits into the check) and
printed `INTEGRITY: the audited tree is byte-identical before and after this run.` and
`EXIT=7`.

**Windows did not regress:** `dfu-done.ps1 -SkipLive -Json` reached **exit 7** in 122s,
`board=failed`, identical census, `integrity.ok=True`, write-lock applied.
`verify-dfu-done.ps1` reached **DRILL GREEN — 216 assertions, 0 failed, 8 of 8 declared
clauses with a constructed failing case**, exit 0, 563s.

**A claim I wrote and then had to withdraw, kept here because withdrawing it is the
finding.** I first compared the Windows run *in the worktree* against the Linux run *in a
scratch clone*, got 8 of 8 identical buckets, and wrote "per-clause buckets are IDENTICAL
on both platforms". That was a comparison of two **different trees**, so it was not a
platform comparison at all. Run properly — the same clone `f387e5e`, both platforms —
**clause 4 differs**:

| | Windows | Linux |
|---|---|---|
| clause 4 | `unevaluated` | `unmet` |
| census | `unmet 4, unevaluated 3, manual_pending 1` | `unmet 5, unevaluated 2, manual_pending 1` |

and the reason is the probe environment, not the script: in the container `clean-repo`
reports 475 dirty paths (the clone was checked out by Windows git and read through a bind
mount by Linux git, so every LF-in-index file looks modified), `gitlink-reachable-on-remote`
is indeterminate because `origin` is a `D:\` path the container cannot reach, and the andon
board could not run. A hosted runner has none of those three conditions and would differ
again.

So the map is environment-dependent as well as platform-dependent, which is a **stronger**
reason for `ci.yml`'s existing decision not to pin it than the one that decision was made
on. What the step asserts instead — census balances, `unrecognised == 0`, `integrity.ok` —
held on **every** run here, Windows and Linux, worktree and clone.

### 15.5 The re-sweep — run, not grepped, and it found what the grep did not

A grep is an enumeration of the ways a script can be Windows-only, and the next way is not
in the list. So the eight wired scripts were swept by **running every wired invocation on
Linux through `run-check.ps1`** and asking the only question that matters: did it reach an
exit code?

| check | wrapper rc | reached exit code | did-not-run |
|---|---|---|---|
| `expected-exit-selftest` | 0 | 0 | no |
| `boundary-drill-selftest-ledger` | 0 | 0 | no |
| `boundary-drill-selftest-vacuity` | 0 | 0 | no |
| `corpus-exposure-producers` | 0 | 0 | no |
| `corpus-exposure-producers-selftest` | 0 | 0 | no |
| `boundary-drill` | 0 | 1 | no |
| `rls-boot-drill` | 1 | **(none)** | **YES** |
| `app-role-drill` | 0 | 2 | no |
| `prove-rls` | 1 | **(none)** | **YES** |
| `dfu-done` | 0 | **7** | no |

The §12 grep found neither of the two. Both are recorded below as dated findings, not fixed
here (§C.10 says fix only the blocker).

### 15.6 FINDING (not fixed) — two drills unwind through their own `docker` cleanup

`drill-rls-boot-assertion.ps1` and `prove-agent-memory-rls.ps1` both end in a
`finally`/`Cleanup` that calls bare `docker` (`prove-agent-memory-rls.ps1:126-130`, invoked from the
`finally` at `:731-738`; `drill-rls-boot-assertion.ps1`'s `finally` at `:736-752`, whose bare
`docker` calls are `:738`, `:745` and `:746`). Where `docker` is not on
PATH that is a command-not-found **terminating** error raised in the cleanup path — after
the drill has already decided — so under run-check's trap it unwinds past the `exit 3` /
`exit 1` it was about to reach. This is precisely the shape `run-check.ps1`'s own header
documents for `drill-app-role-not-superuser.ps1`.

**It does NOT reproduce on `ubuntu-latest`,** which ships Docker 28.0.4; it reproduced here
only because the probe image has no docker client. And run-check catches it correctly, so it
is **not a false green**. The real cost is a lost distinction: the annotation says
"DID NOT RUN" where the drill meant `exit 3 = CANNOT CHECK, the boundary was never
exercised`, which is the sentence H2 spent three rounds separating from "the boundary is
broken". Owner: whoever next touches those drills. Sink: this file.

*(The `prove-rls` row's first line, `OB1 is dirty ... dirty=875`, is an artifact of this
probe — the submodule was checked out on Windows and read through a bind mount. Not a
finding.)*

### 15.7 Two Windows-only path rewrites also fixed, because they are in the blocking file

`dfu-done.ps1` rewrote forward slashes to backslashes in two places before a `Test-Path`.
Off Windows a backslash is an ordinary filename character, so both answered FALSE for paths
that exist:

- `New-CleanCheckout`: the `.gitmodules` submodule path was rewritten before looking for the
  local mirror — so the mirror is never found and the clone falls back to the network.
- clause 2's disposition ledger: the ledger's `findings_sink` was rewritten before
  `Test-Path` — so it reports `dispositioned 'follow-on' but its findings sink does not
  exist` for a sink that is there. A fact about the platform, reported as a fact about the
  disposition.

Both now pass the forward-slashed path to `Join-Path`, which handles it on both platforms.
Same class as §12's row saying backslash literals are harmless: they are harmless where pwsh
*normalises* them, and harmful where the code *creates* them.

### 15.8 One more StrictMode trap, caught before it shipped

`run-check.ps1` sets `Set-StrictMode -Version Latest`, and `& $target` runs the check in a
child scope that **inherits** it. Reading a never-assigned variable is a terminating error
there — the same failure class this whole round is fixing. `$script:DfuPlatformCache` is
therefore declared `= $null` at top level before its function, exactly as `$script:DfuSnap`
and `$script:DfuSandbox` already are. Proven by the Linux non-JSON run above, which was
launched with `Set-StrictMode -Version Latest` explicitly.

### 15.9 Round-3 validation anchor

| | |
|---|---|
| branch | `work/u8h4`, rebased onto `refactor/ai-stack-cleanup` @ `6c17dfa` (H1, H2, H3 and u8floor all landed) |
| rebase conflict | `.github/workflows/ci.yml` only — the `develop` -> `development` rename landed independently on the work line as `5e5ac6f`; the H4 comment was kept and re-attributed to it, and the resolved file was re-parsed with PyYAML (14 jobs; push branches `[main, development, feature/**, refactor/**, update/**]`) |
| Linux host | `mcr.microsoft.com/powershell:7.4-ubuntu-22.04` plus one layer adding `git`, tagged `dfu-linux-probe:wt-u8h4` — a test tag, never `:local`, never attached to an `ai-stack_*` network |
| production touched | none. No container of the live stack was started, stopped or written to; every dfu-done run used `-SkipLive` |
| clean clone | `git -c core.longpaths=true clone --no-local -b work/u8h4 --single-branch`, then **`git config core.longpaths true` INSIDE the clone** before any other git command |
| commit validated | **`23394369f6ce938ebb53dc6af0ae07130d0c4bfd`** (the branch tip). `f387e5e` is the code commit and was validated first; `2339436` adds only this note plus PowerShell/YAML comment text, and `scripts/checks/dfu-done.ps1` is **blob `09a8f1e3934978a75444ae2df616a688a8f12d59` in both** — so the runs below were re-done from `2339436` and neither could differ. |
| `.github` tree hash | `49f318a708ef8940ab487675fd4a126bd0559892` at `2339436` (`9f09a9f10921d5ec7f89c7da2b14f88ddde2414e` at `f387e5e`; the delta is comment text, and PyYAML parses 14 jobs at both) |
| `dfu-done.ps1` blob | `09a8f1e3934978a75444ae2df616a688a8f12d59` |
| `git status --porcelain` | **EMPTY — 0 lines**, both before and after `submodule update --init --recursive`. `OB1` HEAD = `b604d555f37bf79b14d6e5d0db73dec023305917` = the recorded gitlink, and `OB1`'s own porcelain is 0 lines too. (`git submodule status` prints a leading `-` until `submodule.OB1.url` is registered in the clone's config, even with the working tree correctly populated — the marker is about registration, not about state.) |
| Linux, from that clone | `run-check rc = 0`; **dfu-done exit 7**; `board=failed balances=True unrecognised=0 integrity.ok=True`; `expected-exit rc = 0`; **STEP RESULT: GREEN**. All six walkthrough commands came back `indeterminate` with `/bin/sh: 1: python: not found` attached — not `fail` |
| Windows, from that clone | **exit 7** in 120.2s; `board=failed balances=True unrecognised=0 integrity.ok=True`; `write_lock = applied per command (Deny ACE for the running identity)`; 2 locked directories and `interpreter_native = True` on every command |
| Windows drill | `verify-dfu-done.ps1` **DRILL GREEN — 216 assertions, 0 failed, 8 of 8 declared clauses with a constructed failing case**, exit 0, 563s, against the same `dfu-done.ps1` blob |

**A near-miss I want on the record, because I nearly wrote the wrong cause down.** After
`git config core.longpaths true` in the clone, `git submodule update --init --recursive`
failed with `Failed to clone 'OB1' a second time, aborting`. Adding
`-c core.longpaths=true -c protocol.file.allow=always` made it work, and I wrote it up as
"the MAX_PATH trap, one level deeper" — plausible, consistent with the round-2 note, and
**not what the error said**. Re-run to get the real message:

```
fatal: transport 'file' not allowed
fatal: clone of 'D:/.../OB1' into submodule path '.../v3/clone/OB1' failed
```

`protocol.file.allow` had been set with `git config` in the parent clone. **A submodule's
clone is a separate git process and does not read the superproject's config for this**;
`-c` does reach it, because `-c` travels to child processes through `GIT_CONFIG_PARAMETERS`.
So the real rule is: **`-c` for anything a submodule operation must honour, not
`git config` in the parent** — which is what `dfu-done.ps1`'s own `New-CleanCheckout`
already does (`git -c protocol.file.allow=always -c core.longpaths=true submodule update`),
and is why that path works. The lesson is the same one this file keeps recording in other
forms: an explanation that fits is not a measurement. I had the fix and reasoned backwards
to a cause instead of reading the error.

**And a second near-miss, in the editing rather than the engineering.** Filling this table
in, I anchored an edit on the row text `| clean clone | ...clone --no-local -b work/u8h4`.
That row exists in **§14's anchor table too**, `str.index` returned the first match, and the
replacement silently deleted 231 lines — the rest of §14 and all of §15.1-15.8. Caught by
comparing the working file's line count against `git show HEAD:`'s before committing, not by
reading the diff. In a note that repeats a table shape per round, a row is not a unique
anchor; the section heading is. Recorded because "the edit applied and printed the right
result" was true and the file was still wrong.
