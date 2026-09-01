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

**Still not proven, and not provable from here:** that any of this runs on `ubuntu-latest`.
Section 5 establishes that nothing blocks it and section 2 establishes why a CI run cannot
be triggered from a feature branch. The first real evidence arrives with the operator's
promotion.
