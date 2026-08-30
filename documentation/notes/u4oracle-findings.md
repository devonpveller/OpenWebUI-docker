# Findings — U4: frontier-oracle-on-stall (2026-08-30)

Item: FRONTIER-ORACLE-ON-STALL, per PLAN §2's U4 row and ORCHESTRATION-DESIGN §7.
Branch: `work/u4oracle`.

## DECISIONS entries to append

### 2026-08-30 · U4 · class 2 — the stall definition is agent-org's, ported, with two stated differences
DECISION: `oracle_on_stall.evaluate` adopts the burn-down loop's convergence test
          (`orchestrator.py` ~8414): a round whose failure SIGNATURE is not novel
          against every signature seen on this item is not progress, and two such
          rounds in a row is a stall (`stall >= 2`, agent-org's threshold).
          `failure_signature` is `Orchestrator._failure_sig` (orchestrator.py:8845)
          byte for byte.
          TWO DIFFERENCES, deliberate:
          (1) agent-org has a countable metric (compiler errors) so its test is
              `improved OR novel_sig`. A harness round is a tester's pass/fail with
              prose — there is no count to improve, so the signature axis carries
              the whole test rather than a faked `improved`.
          (2) a round must also have MOVED THE BRANCH HEAD. This is the round-level
              analogue of little-coder's FLAIL GUARD (`littlecoder/agent.py:170`),
              and §6's hygiene rule justifies it: a failure that changes while the
              code does not is NOISE, and noise "must never be recorded as a
              constraint". Without it a flaky test resets the stall counter forever
              and the detector never fires on the item that most needs it.
CITED:    §C.2 class 2 — closest to an existing house pattern; PLAN §3 keeps
          agent-org's CDCL constraint learning, so a second dialect of "did this
          round teach us anything" would be a regression, not a unification.
REVERT:   One function, `evaluate()`. Deleting the `moved` term restores the pure
          agent-org test; the tests name each axis separately so the effect of
          dropping one is visible immediately.

### 2026-08-30 · U4 · class 2 — a frontier worker gets `no-oracle-above`, not a self-escalation
DECISION: When the stalled worker already runs on the `claude-code` runner — which
          is exactly what the shipped default `all-cloud` profile means — the stall
          is recorded with outcome `no-oracle-above` and NO escalation.
CITED:    §7 — "the frontier is an oracle invoked on a stall signal, not a better
          worker". claude-code -> claude-code would fill the audit trail while
          changing nothing, and C.7 makes that trail the deliverable's twin: a
          record that reads as an escalation must correspond to one.
CONSEQUENCE: the escalation path is only exercisable under a profile whose worker
          is local (`all-local`, `local-work-cloud-review`) — §7's 95/5 split. The
          drill therefore submits with `-RunnerProfile local-work-cloud-review`.
REVERT:   Delete the `worker.runner == oracle_name` branch in
          `resolve_escalation`; every stall then records an escalation.

### 2026-08-30 · U4 · class 1/2 — the ledger's field is `item_id`, never `item`
DECISION: The escalation ledger names the work item `item_id`.
CITED:    §C.2 class 1, but paid for rather than chosen: `.item` on a .NET
          collection resolves to the `IList.Item` INDEXER. A PowerShell reader
          writing `Where-Object { $_.item -eq $id }` therefore compares a PSMethod
          to a string — false, always, and with no error. See F1: it made this
          drill's own control checks pass while checking nothing.
REVERT:   Rename the key in `record()` and its one test; no consumer outside this
          module reads the ledger yet.

### 2026-08-30 · U4 · class 1 — `queue.ps1 -RunnerProfile`, not `-Profile`
DECISION: The new submit parameter recording which profile an item is worked under
          is `-RunnerProfile`.
CITED:    `$Profile` is a PowerShell AUTOMATIC variable (the profile script path);
          a param of that name shadows it for the whole script scope.
          PSScriptAnalyzer flagged it on the first edit.
REVERT:   Rename the parameter and the one `Set-Field` call; the stored field is
          `profile`, and an item without it means "the surface default", which is
          also what every item queued before today means.

### 2026-08-30 · U4 · class 2 — the detector reads `results[]`, it does not keep its own round store
DECISION: Rounds come from the queue item's existing `results[]` array (verdict,
          sha, reason, evidence) that `queue.ps1 -Fail` has always written.
CITED:    §C.2 class 2 (most reversible, closest to the house pattern). A second
          store would have to be kept in step with the first by whoever remembers
          to, and the stall signal would then be as reliable as that habit.
REVERT:   `failing_rounds()` is the whole adapter; nothing else knows the shape.

### 2026-08-30 · U4 · class 2 — the drill runs in a scratch state namespace, not the live one
DECISION: `verify-oracle-on-stall.ps1` points `AI_STACK_WORKTREE_STATE` at a temp
          directory for its whole run.
CITED:    §C.7 — "the audit trail is the deliverable's twin". `verify-merge-protocol.ps1`
          drives the LIVE queue and deletes its own rows afterwards; that is
          survivable for queue rows and not for an APPEND-ONLY evidence ledger,
          where the cleanup would be a rewrite. A drill must not put invented
          firings into the record the phase is validated against.
REVERT:   Delete the two `$env:AI_STACK_WORKTREE_STATE` lines; the drill then runs
          against the live namespace exactly as the merge-protocol drill does.

---

## F1 — the drill's own control checks passed while checking nothing (fixed)

The first green-looking run had `[PASS] round 1 records no escalation` and
`[PASS] three failing rounds and NO escalation`. Both were vacuous. Two independent
silent-false mechanisms stacked in one expression:

1. **`$json | ConvertFrom-Json` vs `ConvertFrom-Json $json`.** In PS5.1 the PIPELINE
   form emits a JSON array as ONE object, so `@($raw | ConvertFrom-Json)` produced a
   one-element array whose single element was the whole array.
2. **`.item` on that array** resolved to the `IList.Item` indexer, so
   `$_.item -eq $id` compared a PSMethod to a string: false, silently, forever.

It was caught only because the POSITIVE check (`a ledger row exists`) failed on the
same helper — a suite of controls alone would have shipped this. Fixed by parsing
with the parameter form, flattening explicitly, and renaming the field to `item_id`
so the landmine cannot be re-armed by the next reader. A test now pins the name.

This is PLAN §0 A6's class exactly: a check that passes while checking nothing.

## F2 — PS5.1: a one-element array returned from a function has no `.Count`

`(Get-Ledger).Count -eq 1` was FALSE with exactly one row on the ledger. A function
returning a one-element array unrolls it to a scalar `PSCustomObject`, and a
`PSCustomObject` has no `Count` property — so the comparison was `$null -eq 1`.
`@(Get-Ledger).Count` is the correct idiom. Worth knowing anywhere in this repo a
PowerShell helper returns "zero, one or many" and a caller counts them.

## F3 — the two "shared state dir" resolvers disagree (NOT fixed)

`resolve.ps1:25` (`Get-SharedStateDir`) honours `AI_STACK_WORKTREE_STATE`.
`durable_checks.registry_path` (U3) does not — it goes straight to
`git rev-parse --git-common-dir`. So a drill or test that redirects the namespace
gets an isolated queue and an isolated oracle ledger, but writes the durable-check
registry into the LIVE `.git/agent-worktrees/`.

`oracle_on_stall.state_dir` honours the override and says in a comment that
`durable_checks` does not, so nobody copies the wrong half. Not fixed here: it is
another module's file and no current caller redirects the namespace while adding a
durable check. One-line fix when someone owns it.

## F4 — U4's Validated-by column is only PARTLY satisfied. The rest is parked, with the reason.

§2's U4 column reads: *"Gym: same anchored item run per quadrant (runner x target),
outcomes compared; stall->oracle observed firing at least once"*.

- **stall -> oracle observed firing at least once — SATISFIED, and executable.**
  `verify-oracle-on-stall.ps1` drives real `queue.ps1` rounds (real anchor gate,
  real tester claims, a real branch whose head really moves) until the detector
  fires, then reads the firing back off the ledger through the module's own CLI:
  25/25 checks, including the control item that must NOT fire. Proven RED->GREEN
  three ways: mutating `progress = True` (never fires) reddens 8 unit tests;
  mutating `progress = False` (always fires) reddens 8 different ones, the controls;
  removing the one-line wiring from `queue.ps1 -Fail` takes the drill from 25/25 to
  11/18. A green suite that survives both mutations is not testing a tautology.

- **the per-quadrant gym runs — NOT SATISFIED, and cannot be from this item.**
  A quadrant run needs the same anchored item DISPATCHED to each runner. Nothing in
  the harness dispatches to any runner: `config.resolve_role` resolves role+profile
  to a runner and model, and no code path then submits work to one.

  Verified in this worktree, and stated precisely because the loose version is
  wrong: `grep -rn 8090 scripts/agent-harness/{*.ps1,*.py,*.json,*.md}` returns
  EXACTLY ONE hit — `harness.config.json:47`, the endpoint DECLARATION. No `.ps1`
  and no `.py` mentions the port at all, which is the claim that matters: the
  address is configured and nothing dials it. (Before this branch, the only files
  naming `little-coder` at all were `harness.config.json`, `lease-names.conf`,
  `MODULE.md` and two test files.) The dispatcher is the other half of U4 and is a
  separate item.

  **Parked, per §C.7**, not papered over: the escalation this item builds is a
  DECISION plus a durable record plus `pending()`, the handle a dispatcher reads.
  What it does not do is run the oracle's round, because there is nothing to run it
  with. Do not read a green drill as "the oracle worked the item".

## F5 — the live `little-coder` container publishes NO ports at all (verified here)

Sharper than "the API port is unpublished". Verified on this machine, 2026-08-30:

- `docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'` ->
  `{"9090/tcp":[]}`. `docker port little-coder` prints nothing.
- `coder/docker-compose.yml`, rendered, DOES declare `127.0.0.1:9091 -> 9090` for
  the metrics port. The running container (created 2026-08-23) does not have it.
  So the live container diverges from its own compose file: it was started before
  that block, or without it.
- From the host: `:8090` and `:9091` both refuse. From inside:
  `docker exec little-coder curl -fsS http://localhost:8090/health` returns
  `{"status":"ok","version":"0.1.0",...}` — the daemon is healthy, just unreachable.

Consequences for whoever builds the dispatcher: `harness.config.json`'s
`runners.little-coder.endpoint = "http://127.0.0.1:8090"` is unreachable from where
the harness runs. Either the coder plane publishes the API port (a plane change,
with the lifecycle checklist that implies) or the dispatcher goes through
`docker exec` / `lc-net`. That choice belongs to the dispatcher item, so this file
records the constraint rather than pre-empting it. Not changed here — editing that
config while a sibling U4 item may be editing the same file would trade a documented
constraint for a merge conflict.

## F6 — PLAN §0's A11 row understates the gap (proposed amendment, not applied)

A11 reads: *"The harness ran 100% frontier (its `little-coder` runner is wired,
`status: unproven`, and honest about it)."*

"Wired" is doing more work than the code supports: what exists is CONFIG RESOLUTION
(`config.ps1:188` / `config.py`'s `resolve_role`), and no dispatch of any kind for
either runner. The row's verdict (UNTESTED and preserved) is unaffected — this is a
precision correction, not a contradiction.

Not applied to PLAN.md from this branch: three sibling worktrees are live on U5 and
PLAN.md is the file they all cite. `MODULE.md` now states the limitation at the
place a reader would act on it.
