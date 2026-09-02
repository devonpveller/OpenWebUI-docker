# DFU clause 7 — the four phases whose checks I ran, and the three that stay RED (2026-09-02)

Findings sink for the C.8 clause 7 item (`work/dfuw2e`). Everything below was measured on
2026-09-02 by me, in my own clean clone of work line `refactor/ai-stack-cleanup` at
**`ded1b7b`**:

```
git -c core.longpaths=true clone --branch refactor/ai-stack-cleanup --single-branch <repo> <dest>
git -C <dest> config core.longpaths true          # the -c above does NOT persist
git -C <dest> status --porcelain                  # 0 lines
git -C <dest> rev-parse HEAD                      # ded1b7b763b827acf88c07dfa73b48603a5c78f6
```

`git status --porcelain` was empty before anything ran, and empty again after every run below.

**Nothing here was taken from WALKTHROUGH.md's recorded exit codes.** The walkthrough's
`Clean-clone measurement` lines are 2026-09-01 measurements at `fba111d` by whoever wrote that
file. A directive written off someone else's recorded green is a manufactured audit trail,
which is the precise thing clause 7 exists to prevent. Every exit code below is one I watched
this session, at `ded1b7b`, in the clone described above.

## What clause 7 was missing, per phase, before this item

From `dfu-done.ps1 -Only @(7) -SkipLive` in that clone (exit 1, `CLAUSE 7 [UNMET]`, coverage
10/10). U2 and U8 already passed all three artifacts. The other rows split in two:

| phase | ledger entry | findings note | commit claim | the probe's own words |
|---|---|---|---|---|
| U0 | yes | yes | missing | `... (test_inbox.py) ... (2 commit(s) co-mention both without claiming one validated the other)` |
| U1 | yes | yes | missing | `... (smoke-agent-memory.ps1) ... (2 commit(s) co-mention ...)` |
| U4 | yes | yes | missing | `... (check_quadrant_evidence_reproduces.py, cli.py) ... (9 commit(s) co-mention ...)` |
| U5 | yes | yes | missing | `... (drill-personal-plane-exclusion.ps1) ... (2 commit(s) co-mention ...)` |
| U3 | yes | yes | **impossible** | `this phase names NO runnable check anywhere` |
| U6 | yes | yes | **impossible** | `this phase names NO runnable check anywhere` |
| U7 | yes | yes | **impossible** | `this phase names NO runnable check anywhere` |

The first four moved from "impossible" — which is what
`documentation/notes/dfu-clause-7-audit-trail-2026-09-01.md` recorded at `fba111d` — to
"writable" because WALKTHROUGH.md has since grown a `How to run` marker for each of them.
`Get-NamedArtifacts` reads §2's *Validated by* column **and** the walkthrough's `How to run`
commands for that phase; none of those four columns names a script, so the marker is the whole
of what makes a claim possible.

## The four runs, with the exit code I observed

### U0 — `python -m pytest scripts/claude-sessions-bridge/test_inbox.py -q`

**exit 0**, `20 passed in 10.84s`. Stdlib + pytest; no bridge, no Mattermost, no venv.

This licenses a claim about the second half of U0's column — *"inbox: a kill-the-poller drill
proves no message is lost"*. It licenses **nothing** about the first half, *"each item's own
anchor + tester"*, which is a fact about three merges that already happened and is not
re-runnable. The directive says so.

### U1 — `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/smoke-agent-memory.ps1`

**exit 0**, ending `ALL AGENT-MEMORY SMOKE CHECKS PASSED` — 22 `PASS` lines across the throwaway
initdb chain (29 migrations), the stub embedding endpoint, the built `openbrain-mcp-server:smoke`
image, the REST writeback door, idempotency / 422 / 400 refusals, the plane-agreement invariant
and the exposure boundary. No live plane, no GPU.

**Two things happened before that green, and neither is papered over:**

1. **The first attempt failed for a missing submodule, exit 1** — `Get-Content : Cannot find
   path ...\OB1\docker\docker-compose.yml`, `1 SMOKE CHECK(S) FAILED`. A bare
   `git clone --branch ... --single-branch` leaves `OB1/` empty; the smoke script derives its
   initdb chain from `OB1/docker/docker-compose.yml`. Fixed by
   `git -c protocol.file.allow=always submodule update --init` (`protocol.file.allow` is
   required because git refuses a `file://` submodule by default; the remote clone did not
   finish in 10 minutes on this link, so OB1 was fetched from the local mirror and then
   **verified against the gitlink**: `git ls-tree HEAD OB1` and `git -C OB1 rev-parse HEAD`
   both `b604d555f37bf79b14d6e5d0db73dec023305917`). This is a property of *how a clean clone
   is made*, not of the smoke script, and it is worth writing down because "clean clone" in
   this effort's own instructions does not say "with submodules" and the failure it produces
   is a red that looks like a defect in the thing under test.
2. **The second attempt failed transiently, exit 1** — `Error response from daemon: No such
   container: am-smoke-mcp`, `FAIL server never answered`, `2 SMOKE CHECK(S) FAILED`. That was
   the run that BUILT the `:smoke` image (~4 min). The third run, with the image cached,
   reached `PASS server is answering on :18099` and finished green. **I am reporting the green
   because I saw it, and reporting the flake because I saw that too.** I did not diagnose the
   race; a check that needs a retry to go green is a weaker green than one that does not, and
   whoever owns that script should know. Filed, not fixed — §C.10.

### U4 — two commands under one marker, both run

- `python scripts/agent-harness/quadrant/cli.py report --results-dir documentation/evidence/dfu-u4/quadrant`
  — **exit 0**, `**COMPARED 4/4**`, item digest `c585bee6fee3043c`, all four quadrants
  `completed` at `2/2` acceptance.
- `python scripts/checks/check_quadrant_evidence_reproduces.py --auto` — **exit 0**,
  `7 outcome record(s) re-derived their verdict from the evidence they kept ... 0 skipped as
  inadmissible`, `the 7 run record(s) this checkout COMMITS are all on disk`.

Both are needed: the first renders the comparison, the second is what makes it evidence rather
than a rendering. Round 10's fix held — this is a *different clone at a different sha* from the
one the walkthrough records, and the absolute-path defect that made round 9's green evaporate
(`evidence.workspace does not exist on disk: D:\...\wt-u4close\...\workspace`) did not recur.

Two things a reader should not have to discover by running it:

- `report` **writes** `COMPARISON.md` and `comparison.json` into
  `documentation/evidence/dfu-u4/quadrant/`, a committed path. `git status --porcelain` was
  empty afterwards, i.e. it re-derived byte-identical content — but the command is not
  read-only.
- It also printed a venue-pin notice: this results set is pinned to `gym` at
  `D:\Open WebUI\ai-orchestration-gym`, and the venue that resolved inside my clone differed.
  **The pin stands** — the report renders the pin and refuses records from anywhere else — so
  the `COMPARED 4/4` is about the pinned venue's records, not about my clone.

The report prints its own ceiling and I am not going to restate it more weakly: *"n=1 - not a
basis for a decision"* per quadrant, and `kind: "gym"` is a configuration assertion, not a
measurement.

### U5 — `powershell ... -File scripts/checks/drill-personal-plane-exclusion.ps1 -AcceptDispositionedGaps`

**exit 0**, `PERSONAL-PLANE EXCLUSION DRILL: CONTAINMENT GREEN, 25 gap(s), ALL DISPOSITIONED
(106 checks passed, 0 failed)`. It builds its own throwaway plane on the real 29-migration
initdb chain and touches `openbrain-db` never.

The drill prints its own ceiling and the directive repeats it verbatim rather than rounding it
up: *"This is NOT 'U5's recording half is met' - it is 'nothing changed since the operator
dispositioned these', which is what CI can assert."* U5 is PARKED; the green is the STOPPED
half, and the 25 gaps are the RECORDING half, which is what the park is about.

## The three phases that stay RED, and why no directive was written for them

**U3, U6 and U7 name no runnable check anywhere** — not in §2's *Validated by* column, not in a
`How to run` line in WALKTHROUGH.md. `dfu-done.ps1` says exactly that, per phase:
`this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run'
line in the walkthrough - so no commit message can state 'by which check'`.

This is not a gap I could close by writing better commit messages. A validation directive for a
phase with no check would have to name a script that phase does not name, and
`Get-CommitValidationClaims` would reject it — but more to the point, it would be a sentence
asserting a validation that did not happen. **A phase whose check does not exist, or fails, has
not been validated, and clause 7 must stay red on it.** Each of the three has a recorded,
load-bearing reason:

- **U3** — PARKED. Its discharge is `scripts/agent-harness/u3_evidence_regression_gym.py`,
  which **refuses for want of evidence**: `NO EVIDENCE: ...\.quadrant\gym-runs holds no outcome
  record from venue 'gym'` (exit 2, measured 2026-09-01 and recorded in the
  `2026-09-01 - U3 - the park STANDS` ledger entry). WALKTHROUGH.md therefore records no
  `How to run` for U3 on purpose. Closing it needs a four-quadrant comparison run in the arena,
  which is runner-level work.
- **U6** — its `How to run` marker was **removed**, deliberately, by the round that found the
  commands under it were green while checking nothing: `recall-falsifiability-drill.py` scores
  a mutation RED on `returncode != 0`, so an interpreter that cannot import `sqlalchemy` reads
  as "all twelve mutations red, every guard can fail". Re-adding a marker to make clause 7
  green would re-add the defect. It is not my file in any case.
- **U7** — NOT STARTED. WALKTHROUGH.md says *"There is no `How to run` for U7 and there must
  not be one."* A standing loop that has never run has nothing to validate.

Clause 7 therefore stays **UNMET** after this item, with 7 of 10 subjects green and the three
above red for stated, checkable reasons. That is the true statement about this work line, and
C.8's own rule is that a clause which cannot be met is a REPORT and not a redefinition.

## Filed, not fixed (§C.10)

- `smoke-agent-memory.ps1` needed a retry to go green (see U1 above): the first run after the
  image build reported `server never answered` / `No such container: am-smoke-mcp`. Owner:
  whoever owns that harness. Not diagnosed here.
- A "clean clone" as this effort's instructions describe it is not enough to run U1's or U5's
  check — both need the OB1 submodule initialised, and the documented clone command does not
  initialise it. Neither script says so; the failure surfaces as an unrelated-looking red.
