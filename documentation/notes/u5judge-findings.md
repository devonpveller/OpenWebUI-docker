# u5judge findings — turned up while writing the judge_enabled calibration plan

Branch `work/u5judge`. Deliverable:
`documentation/implementation-guide/little-coder/JUDGE-CALIBRATION.md` +
`scripts/checks/check-judge-dryrun.ps1` (U5, dark-factory PLAN.md §2).

Everything below is a TRUE problem with something ELSE, verified by a command,
and deliberately not fixed on this branch (CLAUDE.md: findings go to
`documentation/notes/`, not into the deliverable). Each is one branch's worth
of work. All checks run 2026-08-30 against the live stack.

---

## 1. `augmenter.select` has ZERO production callers — skill retrieval is built but not wired

```
grep -rn "augmenter" little-coder/src/ --include=*.py
  augmenter.py (its own docstring), config.py (a comment), daemon.py (2 comments),
  judge.py:370 (a comment)
grep -rln "augmenter" little-coder/tests/
  test_augmenter.py, test_meta.py, test_skills.py, test_validation.py
grep -rn 'status="active"' little-coder/src/littlecoder/*.py
  augmenter.py:252 (a docstring), efficacy.py:192, meta.py:333
```

`agent.py` never imports or calls it. Nothing loads
`list_skills(skill_dir, status="active")` into the agent's system prompt. So
design §7.4 — the retrieval half of the whole self-improvement loop — is
implemented, unit-tested, and dead.

Two consequences worth separating:

- **Today it is accidental containment.** A poisoned skill library cannot
  reach the agent, which is the only reason §3.2 of JUDGE-CALIBRATION.md can
  describe the blast radius as bounded.
- **It is also the loop's missing half.** Approving a skill today changes
  nothing observable. Any "the agent learned from its journals" claim is
  currently false at the last step, whatever the library holds.

Work: wire `augmenter.select` into the agent's prompt assembly, or write down
that it is deliberately deferred and what depends on it. Do not do both
silently.

## 2. `founding_knowledge_paths` omits `project-context.md` while its comment claims parity

`little-coder/src/littlecoder/config.py:216` says "The list mirrors
`agent.extra_args --append-system-prompt`". It does not:

| Source | Files |
|---|---|
| `agent.extra_args` (`little-coder.config.yaml:36-41`) | `environment.md`, `project-context.md`, `engineering-principles.md` |
| `ObserverConfig.founding_knowledge_paths` (`config.py:217-223`) | `environment.md`, `engineering-principles.md` |

`project-context.md` is the biggest of the three (10,695 bytes vs 2,131 and
2,806 — `docker exec little-coder ls -la /app/agent-knowledge/`). Neither YAML
overrides the default (`grep -n founding_knowledge_paths` on both config files
exits 1), so the default is live on both planes.

Effect: the judge's `baseline_covers` verdict — the compliance-gap vs
knowledge-gap split design §5.6 is built on — is decided against a smaller
floor than the agent actually has. It would mint tier-0 entries restating
instructions the agent already receives. Latent while `judge_enabled: false`;
it becomes the first poisoning mechanism the day the flag flips.

Fix is one line plus a test asserting the two lists are equal. Reported by
`check-judge-dryrun.ps1` as a blocker on both planes, so the regression is
already covered by an executable check; the FIX is the open work.

## 3. ao-worker `skill` / `cohorts` / `polyglot` are container writable layer, not volumes

```
docker inspect ao-worker-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'
  /workspace  /app/config  /var/lib/little-coder/journals  /var/lib/little-coder/sessions
docker inspect ao-worker-1 --format '{{.Created}}'          -> 2026-08-29T19:35:57Z
docker exec ao-worker-1 stat -c '%n %y' /var/lib/little-coder/skill /var/lib/little-coder/cohorts
  skill   2026-08-29 19:35:59      <- container creation time
  cohorts 2026-08-29 19:35:59      <- container creation time
  journals 2026-08-23 17:14:49     <- predates the container (it is a volume)
```

Journals ARE durable (`agent-org/docker/docker-compose.yml:332`, memory-plane
Phase 0.3 — so the half-remembered claim that the pool mounts none of the
expertise volumes is wrong). What is missing is the OUTPUT side. Anything
minted or drafted on the pool is destroyed by the next recreate, silently,
along with the cohort store — which means re-minting from scratch on the next
iteration, forever.

This is a SERVICE-LIFECYCLE item, not a one-liner: two named volumes per
worker, a backup sidecar each (the journals pattern in the same file), and
restore-catalog entries. It gates ever enabling `judge_enabled` on the pool.

## 4. Every `command_failed` occurrence is a bare exit code — the stderr tail is empty

Measured over both planes' live journals via `check-judge-dryrun.ps1`:
9 of 12 occurrences on the coder plane and 9 of 12 on ao-worker-1 have a
payload-free signal (`exit 1: `, `exit 128: `). The error message is built as
`f"exit {item['exit_code']}: {item['stderr_tail']}"`
(`little-coder/src/littlecoder/agent.py:491`), and `stderr_tail` comes from
the pi-extension activity file — `agent.py:73` reads
`ev.get("stderr_tail", "")`, and `otexec.py:57`/`:79` are the writers.

I did NOT trace which of those paths produces the empty value, so root cause
is open. The consequence is not: the evidence corpus that the entire Observer
→ judge → skills chain is built on carries no information about what actually
failed. No amount of accumulated volume fixes it; it is the single
highest-value change for making expertise minting possible at all.

## 5. `task_shape` is `unknown` for 100% of occurrences — the classifier can never fire

`task_shape.classify` (`little-coder/src/littlecoder/task_shape.py:120-152`)
needs `kind="test_failure"` errors, or ≥3 `write_file`/`edit_file` tool calls,
or ≥3 `read_file` calls, to return anything but `unknown`. Live tool_call
records emit `tool="bash"` for essentially everything, so no branch is
reachable.

Design §5.5 scopes cohorts by `lang` + `task_shape`. With the shape half
constant, the scope key has collapsed to `lang` alone, and design §15
explicitly rejects coarse scoping ("a craft gap recurs across repos; per-repo
never reaches M"). Fix is either a richer tool taxonomy at journal-write time
or a classifier that reads what is actually recorded.

## 6. Journals carry the operator's email in `user_id`; today's judge prompt does not ship it

```
head -3 /var/lib/little-coder/journals/errors.jsonl   (inside the container)
  ..."user_id":"yamaoka01@gmail.com","repo":"https://github.com/..."...
```

`Envelope.user_id` (`journals.py:57`) is the OWUI authn id. The judge prompt
is assembled by `judge._pool_block` (`judge.py:194-203`), which renders ONLY
`source_kind` and `signal_text` — so no identifier reaches the model today,
and the model is local anyway. Verified by reading the emitted prompt from
`check-judge-dryrun.ps1 -EmitPrompts`.

Recorded because it is one prompt edit away from being false: any future
change that renders more envelope fields into the pool block ships an email
address and repo URLs into an LLM context. Relevant to U5's personal-plane
exclusion. A test pinning the pool block's rendered fields would make the
guarantee mechanical rather than incidental.

## 7. The coder plane has been idle since 2026-06-20

`outcomes.jsonl` and `cohort-store.json` in `coder_little-coder-journals` /
`coder_little-coder-cohorts` both stop at 2026-06-20 10:50; `audit.jsonl` is
current only because the daemon writes lifecycle records. So the 12
occurrences there are a three-month-old sample, not a current one. Not a
defect — but it means "run more real work and re-run the dry run"
(JUDGE-CALIBRATION.md §4) is a bigger ask on that plane than on the org pool,
which was still taking tasks on 2026-08-24.

---

## 8. FIX ROUND 2026-08-30 — what two verifiers refuted, and what changed

Both adversarial verifiers refuted this branch. Their verdicts were correct;
each was reproduced by running something before it was fixed. Recorded here in
full, including the parts that are NOT fixed, because a stated limitation beats
a silent one.

### 8.1 The claim was wider than the evidence (both verifiers, decisive)

The claim handed for verification was that this branch satisfies U5's
"Validated by" column. It does not, and cannot: that column names an agent
instructed to bypass hooks and an attempt to reach personal-plane data, and
this branch attempts neither. U5 is three sub-items across three branches; this
is one of them.

FIXED as a claim, not as a capability. JUDGE-CALIBRATION.md now opens with a
"What this does NOT close" table naming which sub-item lives where, and the
probe's module docstring and the wrapper's header say the same. The narrower
claim now made is that *for this sub-item's own subject matter* — the flag —
something is mechanically stopped and the attempt is visible in an audit
record. See 8.3. This branch must not be merged under a "U5 validated" banner.

### 8.2 The commit message carried a number its own tool contradicted

The commit said "12 of 12 occurrences on each plane carry a payload-free
signal". Re-measured 2026-08-30 with the branch's own tool:

```
little-coder : occurrences 12, degenerate_signals 9   (pools 4 + 3 + 2)
ao-worker-1  : occurrences 12, degenerate_signals 9
```

9 of 12 on both planes. Item 4 above said "12 of 12 on the coder plane", also
wrong. Both are corrected. The *separate* claim that `task_shape` is unknown
for 12 of 12 was re-checked and IS true
(`occurrences_with_unknown_task_shape: 12` on both planes) — one number was
wrong, not the section.

Structural fix so it cannot recur the same way: the tool emits the ratio itself
(`totals.degenerate_signals`, and in prose in `would_have_minted`), and the
document labels the live figures as a dated snapshot and gives the command as
the citation instead of asking a reader to trust a transcription.

### 8.3 `judge_enabled: true` changed no verdict and no exit code

Reproduced before fixing: set `judge_enabled: true` in a scratch copy of
`little-coder.config.yaml`, run `check-judge-dryrun.ps1 ... -RequireReady` ->
`READY-FOR-RATING`, exit **0**. The flag was printed and used in no decision.
An artifact whose entire purpose is "do not flip this until a human rates a dry
run" did not notice the flip.

FIXED in two places, because neither covers the other:

- **Run time.** Exit 7 (MISCONFIGURED) when the config under test already has
  the flag on and no valid rating record is supplied. Non-zero regardless of
  `-RequireReady` — measured 7 in both forms.
- **Commit time.** `scripts/checks/check-judge-flag.ps1`, wired into
  `.githooks/pre-commit` as step 6, DENIES a commit that sets the flag truthy
  in any staged YAML unless the same commit stages a valid rating record, and
  appends the outcome to `<git-common-dir>/judge-flag-guard.log`.
  `verify-judge-flag-guard.ps1` proves it in a throwaway repo — 20 cases,
  including `git commit` refused with HEAD unmoved, and the negative control
  (guard removed from the hook -> the identical commit lands). One case flips
  the SHIPPED config in a temp copy, because every other case uses YAML the
  drill wrote itself and would all pass even if the pattern did not match the
  real file. Breaking one character of the pattern turns 11 of 20 cases red.

The rating-record escape exists deliberately: without it the check becomes a
permanent tripwire after an honest enablement, which is the kind of guard
people switch off.

### 8.4 `wrote_nothing` was a hardcoded literal

Reproduced: copied the probe, injected one `write_text` into an observed root,
ran it. It created the file AND reported `"wrote_nothing": true` — and counted
the file it had just written as a skill artifact.

FIXED: measured. sha256 per file over `cohorts`/`skill`/`polyglot` before and
after, `(name, size)` over `journals` where only shrink or disappearance counts
— the live daemon appends while we read, and treating that as a violation would
make the check fire on correct behaviour. A change is exit 8, and the report
names the file. The drill proves the detector fires via the probe's drill-only
`--prove-write-detector` flag.

This also closes the verifier's note that the container-mode read-only evidence
was a manual one-off with no check behind it: the measurement runs inside the
container on every container-mode run.

### 8.5 The drill skipped its most important case and still exited 0

Reproduced exactly as described: move `fixtures/judge-dryrun/MANIFEST.sha256`
aside -> `[SKIP] fixtures-unchanged`, then `ALL 12 CASES PASS`, exit 0. The
count dropped 13 to 12 and nothing read it. The message also named a `-Regen`
switch that did not exist.

FIXED: a missing manifest is a FAILING case (now 2 failing cases, exit 1);
`$EXPECTED_CASES` is asserted in both drills, so a case that stops running is
red rather than quieter; `-Regen` is real and produces a reviewable diff.

### 8.6 Four defects I found in my own fixes, by running them

Recorded because they are the same shapes, in the work meant to close them.

1. **`$regen` silently became `$Regen`.** PowerShell variable names are
   case-insensitive, so assigning the manifest array to `$regen` coerced it to
   the `[switch]$Regen` parameter and wrote the single word `True` into
   MANIFEST.sha256. `check-hook-attestation.ps1` carries a comment about this
   exact trap with `$base`/`$Base`; it caught me anyway. Renamed
   `$manifestLines`. Found by reading the regenerated file, not the code.
2. **A one-element array unrolled on return.** `Get-AuditLines` returned a
   String when the log held one line, so `$audit[-1]` indexed its last
   CHARACTER and an assertion failed for a reason unrelated to the guard. Every
   call site is now `@(Get-AuditLines)`.
3. **A case measured the wrong thing and went red for the wrong reason.** The
   new "does the pattern match the SHIPPED config" case failed on its first
   run (expected 1, got 0) because an earlier case had left a VALID rating
   record in the scratch index — with that staged the guard is right to allow
   anything. The case now clears the record first. A red for the wrong reason
   is one step from a green for the wrong reason.
4. **Every empty file hashed to the empty string.** `Get-NormalizedHash` piped
   `ReadAllBytes` through `Where-Object`, which yields nothing for an empty
   file, and `ComputeHash([byte[]]$null)` returns `""` — so all empty files
   matched each other. Wrapped in `@()`; an empty file now hashes to
   `E3B0C442...`.

### 8.7 NOT fixed — stated, not papered over

- **U5's column still needs `work/u5proxy` and `work/u5pplane`.** Nothing here
  changes that.
- **The flag guard is per-branch and per-checkout.** `core.hooksPath` is
  relative and the hook is version-controlled, so a branch cut before this
  merges does not carry it, and `--no-verify` bypasses it entirely. That second
  gap is `check-hook-attestation.ps1`'s subject, not this one's, and
  `work/u5proxy` measured three further bypass routes that leave no audit line
  at all. I did not attempt to close any of them.
- **`judge-flag-guard.log` has no automated consumer.** It is read by
  `check-judge-flag.ps1 -Audit` and by nothing else. It is a trail, not a
  second guard, and the agent it constrains can delete it.
- **The guard greps rather than parses YAML.** It matches `judge_enabled:`
  followed by a truthy scalar in any staged `*.yaml`/`*.yml`. A flip expressed
  as a YAML anchor, a merge key, or a templated value would not match. Chosen
  because this runs on every commit and a full parse of every staged YAML is
  not free; the shipped schema types the key as a plain boolean
  (`little-coder.schema.json:244`). Logged as a class-3 question below.
- **Exit 7 cannot see a runtime override.** It reads the config file it is
  pointed at. An env-var or in-container edit that never reaches a config file
  is outside both mechanisms.
- **`--prove-write-detector` is a deliberate write path inside a tool that
  advertises itself as read-only.** It is drill-only, unreachable from the
  wrapper (no switch emits it), and refuses without an explicitly named
  existing directory — but it exists, and a reader should know that.

---

## 9. FIX ROUND 2026-08-30 (second) — the neighbouring case, and moving the decision

The reported defect of round 1 was genuinely closed: verifiers reproduced the
GREEN. Then a verifier turned the flag on with **three ordinary YAML
spellings**, and a second did it with **two quote characters**. Neither
defeated the guard; both walked around it, because the guard and the daemon
were two different parsers of the same file.

This round did not patch the pattern. It moved the decision.

### 9.1 The finding I logged and then under-rated

The last entry in this file before today was a class-3 QUESTION saying exactly
this: *"`check-judge-flag.ps1` GREPS staged YAML rather than parsing it … A
flip expressed as a YAML anchor, a merge key or a template would not match.
… **Not a blocker either way.**"*

It was the blocker. The default taken was justified with two reasons that both
turn out to be wrong:

- *"the guard runs on every commit"* — cost. Measured, the parse costs one
  python launch on commits that stage YAML at all, roughly a second. The
  previous version already paid that cost whenever it found a candidate.
- *"the shipped schema types the key as a plain boolean"* — irrelevant. The
  schema constrains what the DAEMON accepts after parsing; it says nothing
  about how many ways a YAML file can spell a value that parses to `True`.
  Measured, there are at least 27 in a 43-row corpus, and the pattern saw 13.

The lesson is not "parse instead of grep". It is that **an approximation
logged as a known limitation is still an unclosed hole**, and calling it "not
a blocker" is a prediction, not a finding. Two verifiers falsified it in ten
minutes each.

### 9.2 RED, measured before anything changed

Eighteen deliberately chosen spellings, run through the daemon's real loader
(`littlecoder.config.load_config`) and through the shipped pattern
(`check-judge-flag.ps1:114`):

```
YES / ON / y / "true" / 'yes' / quoted key / flow mapping / 1 /
!!bool "true" / "judge_\x65nabled"     -> daemon reads ON, pattern sees nothing
```

**10 of 18 were bypasses.** The corpus that shipped grew to 43 rows generated
by mechanism; of those, 27 turn the flag ON for the daemon and the retired
pattern could not see **14**.

### 9.3 The chokepoint

`little-coder/src/littlecoder/judge_gate.py`. `meta_wiring.build_meta_runner`
— the only site in the package that constructs a `Judge` — now calls
`judge_gate.require(config)` instead of reading the flag. A config that asks
for the judge without a valid approving rating record raises
`JudgeNotCalibratedError` and the daemon does not boot.

That changes what a bypass would have to be. The routes that beat the
commit-time guard — `--no-verify`, a branch without the hook, a config edited
straight into the container, a spelling nobody has thought of — all arrive at
`require`, because `require` runs downstream of parsing rather than trying to
predict it.

Proven in a container built from this branch (`little-coder:wt-u5judge`), on
the **shipped** config:

```
shipped config                 -> judge_enabled False, runner.judge None, boots
judge_enabled: YES             -> loader True  -> REFUSED
judge_enabled: ON              -> loader True  -> REFUSED
judge_enabled: "true"          -> loader True  -> REFUSED
YES + a valid rating record    -> runner.judge = Judge
```

### 9.4 Completeness, mechanically

`little-coder/tests/test_judge_gate_chokepoint.py` AST-scans the package and
fails when a new site could decide the question without the gate: any read of
`judge_enabled` outside `judge_gate.py`, any `Judge(...)` outside
`meta_wiring.py`, `build_meta_runner` no longer calling into `judge_gate`, a
second copy of the rating-record rule, and a floor on modules scanned so a
scan that stops matching cannot pass for free.

All three failure modes were observed RED before green:

- a probe module reading the flag and constructing a `Judge` turned two tests
  red, naming `_probe_bypass.py:7`;
- replacing the gate call with `judge_permitted = True` turned the third red.

### 9.5 The differential, over one corpus, run by two executors

`scripts/checks/fixtures/judge-flag-corpus.json` is read by
`little-coder/tests/test_judge_flag_corpus.py` (daemon loader vs the gate's
decider) and by `scripts/checks/verify-judge-flag-guard.ps1` (the shipped
pre-commit guard, end to end in a scratch git repo). One corpus, two
executors, so a disagreement is a test failure rather than a discovery — the
house pattern already used for `harness.config.json`'s two readers and the zod
enum vs the SQL CHECK.

RED observed: replacing the gate's pydantic coercion with a strict
`value is True` check failed 5 rows immediately.

### 9.6 Two defects found by RUNNING this round's own work

Both would have shipped as green-for-the-wrong-reason:

1. **A UTF-8 BOM made every DENY case pass for the wrong reason.** PowerShell
   5.1's `Set-Content -Encoding utf8` writes a BOM; `json.load` rejects it; the
   decider exited 5; the guard failed closed and exited 1 — which is the same
   exit code as a correct denial. Every DENY case in the drill went green while
   the decider had never run. Only the audit-line assertions showed it. Fixed
   twice: the guard writes the request with `WriteAllText`, and the decider
   decodes `utf-8-sig`. The drill now also asserts the audit line says `DENY`
   and not `CANNOT-TELL`.
2. **The drill's `EXPECTED_CASES` was wrong** (28 vs the 29 actually run), and
   said so rather than silently passing. That counter has now caught a
   miscount twice.

### 9.7 NOT closed — stated, not papered over

- **The audit trail is still bypassable.** `--no-verify`, a branch without the
  hook, or a clone without `core.hooksPath` still produce a flip with no audit
  line. What has changed is that such a flip no longer *reaches the judge*.
  The record is lost; the containment is not. Hook-bypass detection is
  `check-hook-attestation.ps1` + `work/u5proxy`, a different sub-item.
- **Nothing stops an edit to `judge_gate.py` itself.** The completeness test
  watches every OTHER site; it cannot watch the file it is defending. Review is
  the only control there, and pretending otherwise would be the
  doc-claims-a-property-the-code-lacks shape this file keeps recording.
- **The rating record is not authenticated.** `rated_by: me` is a valid record.
  It is an operator artifact under review, not a credential.
- **The perimeter scans `*.yaml` / `*.yml` only.** A config renamed to another
  extension would not be seen at commit time. The chokepoint has no such scope
  limit: it tests the value pydantic produced, whatever file it came from.
- **`LC_JUDGE_RATING_RECORD` lets environment control satisfy the gate.** Not a
  new class of power — `daemon.py:1086` already resolves the entire config from
  `LC_CONFIG`, so anyone who can set the daemon's environment could always
  choose its config. Stated because a verifier will find the new variable and
  should not have to work out whether it widened anything.
- **The guard now parses arbitrary staged YAML**, so a maliciously crafted
  alias-expansion file staged by the author would make their own commit slow or
  fail. Self-inflicted and fail-closed; noted rather than mitigated.
- **The running `little-coder` container does not have the gate yet.** It runs
  `little-coder:local`, built before this branch. The runtime chokepoint
  reaches production on the next image rebuild + recreate of that service. The
  proof above used `little-coder:wt-u5judge`, built from this branch and never
  attached to the anchor networks.
- **U5 does not close on this.** `work/u5proxy` and `work/u5pplane` carry the
  other two sub-items. This round closed a defect class inside one of the
  three.
- **The guard now fails closed harder than before.** On a machine with no
  working python, a commit staging any YAML is refused rather than only a
  commit that already looked like a flip. Deliberate; stated in the script
  header; reversible by reverting the script.


## DECISIONS entries to append

## 2026-08-30 · U5 · class 2
DECISION: U5's `judge_enabled` slice ships a PLAN plus a read-only dry-run
          check, and does not flip the flag anywhere. The plan's executable
          component (`scripts/checks/check-judge-dryrun.ps1`) reports what the
          judge WOULD be handed and exits non-zero when it cannot tell; its
          `-RequireReady` form is the enablement gate for later.
CITED:    PLAN.md §2 U5 names a "calibration plan", not an enablement, and
          §C.7 requires an EXECUTABLE check rather than prose. Design §13's
          closing line makes the transition a human decision journaled to
          `audit.jsonl` — automating the flip would delete that decision.
REVERT:   Delete the three scripts and the doc; nothing else references them
          and no runtime config was touched (`judge_enabled: false` unchanged
          in both config files).

## 2026-08-30 · U5 · class 2
DECISION: The dry run EXITS NON-ZERO (4) when the cohort store already holds
          clusters, instead of reporting an approximate answer.
CITED:    §C.7 "verification replaces the operator's reading" plus the A6
          verdict on prose. `clusters.assign` (`clusters.py:144-150`) returns
          UNASSIGNED WITHOUT calling the similarity function when no cluster
          shares the occurrence's scope — so the stub-similarity projection is
          bit-identical to a judge-enabled daemon's ONLY while the store is
          cluster-free. Reporting a routing-dependent result as if it were
          exact is the "check that passes while checking nothing" failure.
REVERT:   Drop the `EXIT_STORE_HAS_CLUSTERS` branch in
          `scripts/checks/lib/judge_dryrun.py`; the report would then be an
          approximation with no marker saying so.

## 2026-08-30 · U5 · class 2
DECISION: A half-remembered claim ("ao-workers do not mount journals/skill/
          cohort volumes") was checked and AMENDED on the record rather than
          repeated: journals ARE a named volume on the pool; `skill`,
          `cohorts` and `polyglot` are not, and live in the container's
          writable layer. The sharper failure — minted artifacts destroyed by
          the next recreate, invisibly — is written up in
          JUDGE-CALIBRATION.md §1.3 and in this note's item 3.
CITED:    §C.1 (amend on the record and continue) + CLAUDE.md's rule to verify
          against the live artifact before declaring anything.
REVERT:   Nothing to revert — the amendment is a corrected reading, and the
          compose files were not modified.

## 2026-08-30 · U5 · class 2 (fix round)
DECISION: The dry run EXITS 7 when `observer.judge_enabled` is already true in
          the config under test and no valid rating record is given; and a
          pre-commit guard (`scripts/checks/check-judge-flag.ps1`, wired as
          `.githooks/pre-commit` step 6) DENIES any commit that sets the flag
          truthy in staged YAML without staging a rating record, appending
          both outcomes to `<git-common-dir>/judge-flag-guard.log`.
CITED:    Two adversarial verifiers independently set `judge_enabled: true`
          and observed the drill still report ALL CASES PASS at exit 0. §C.7
          closes a phase only on an EXECUTABLE check; an instrument that does
          not notice the thing it exists to gate is the "check that passes
          while checking nothing" shape §0 keeps recording. The rating-record
          escape (`rated_by` / `rated_at` / `rated_report` /
          `verdict: approve`, defined once, in
          `judge_dryrun.read_rating_record`) keeps the guard from becoming a
          permanent tripwire after an honest enablement.
REVERT:   Remove step 6 from `.githooks/pre-commit`, delete
          `check-judge-flag.ps1` + `verify-judge-flag-guard.ps1`, and drop the
          `EXIT_ALREADY_ENABLED` branch in `judge_dryrun.py`. No runtime
          config was touched: `judge_enabled: false` is unchanged in every
          config this repository tracks, and no committed fixture sets it true.

## 2026-08-30 · U5 · class 2 (fix round)
DECISION: `wrote_nothing` is MEASURED (sha256 before/after over
          cohorts/skill/polyglot, name+size over journals) rather than
          asserted, and a violation is exit 8. Journals are treated as
          append-only: only shrink or disappearance counts.
CITED:    The field was a hardcoded literal; a copy of the probe with a write
          injected still reported `true`. The journals asymmetry is deliberate
          — the live daemon appends while the probe reads, and a guard that
          fires on correct behaviour is the one that gets switched off, which
          is the reasoning `check-hook-attestation.ps1` already records for its
          own per-branch activation gate.
REVERT:   Replace the `read_only_proof` block and the computed `wrote_nothing`
          with the previous literal. The claim would then be unverified again,
          including in container mode.

## 2026-08-30 · U5 · class 2 (fix round)
DECISION: The skill library is classified absent / empty / populated /
          poisoned with a per-state remedy, replacing the raw `*.md` count.
          POISONED blocks the verdict; EMPTY does not. The "cluster_id not in
          the store" rule fires only for ACTIVE or PENDING artifacts.
CITED:    `skills.iter_skills` swallows `SkillFormatError` per file
          (`skills.py:298-301`), so a raw count cannot distinguish two healthy
          skills from two the loader silently drops — different failures with
          different remedies. The active/pending narrowing is because this dry
          run only ever runs against a zero-cluster store (a non-empty one is
          exit 4), so the unnarrowed rule would fire on every artifact always,
          and a rule that always fires measures nothing.
REVERT:   Restore `skill_library_files` as a count; drop the
          `_classify_skill_library` call and its blocker.

## 2026-08-30 · U5 · class 3 (QUESTION, batched for the operator)
QUESTION: `check-judge-flag.ps1` GREPS staged YAML for `judge_enabled:` set
          truthy rather than parsing it. Default taken: grep, because the
          guard runs on every commit and the shipped schema types the key as
          a plain boolean (`little-coder.schema.json:244`). A flip expressed
          as a YAML anchor, a merge key or a template would not match. The
          stronger form is to parse each staged YAML with the same python the
          validator already shells, at the cost of one python launch per
          staged YAML file. Not a blocker either way.

## 2026-08-30 · U5 · class 2 (fix round 2 — the chokepoint)
DECISION: The question "may the LLM-in-the-loop judge run?" has exactly ONE
          decider: `little-coder/src/littlecoder/judge_gate.py`.
          `meta_wiring.build_meta_runner` (the only site that constructs a
          `Judge`) calls `judge_gate.require(config)` instead of reading
          `observer.judge_enabled`, and that call RAISES
          `JudgeNotCalibratedError` — failing the daemon boot — when the
          config asks for the judge and no valid approving rating record is in
          force at `/app/config/judge-enablement-rating.yaml` (the container
          path `coder/docker-compose.yml:113` produces from
          `little-coder/config/judge-enablement-rating.yaml`).
          `observer.enabled` is deliberately NOT consulted, so an unrated
          `judge_enabled: true` cannot lie dormant and switch on the day
          someone flips the innocuous flag.
CITED:    §C.7 (a phase closes only on an EXECUTABLE check) and §0 A7
          (containment must be mechanical, never model self-restraint). The
          previous round's guard was a PERIMETER with many doors —
          `--no-verify`, a branch without the hook, a config edited inside the
          container, and any YAML spelling the pattern did not anticipate. Two
          verifiers walked through the last of those in ten minutes each.
          Enforcing at the single consumption site makes all four irrelevant
          to the property, because the value tested is the one pydantic
          already produced. Proven in a container built from this branch
          (`little-coder:wt-u5judge`) against the SHIPPED config: `YES`, `ON`
          and `"true"` each read True by the loader and each REFUSED; the same
          config with a valid rating record wires a `Judge`.
REVERT:   One line. In `meta_wiring.build_meta_runner`, replace
          `judge_permitted = require_judge_admission(config)` with a direct
          read of `config.observer.judge_enabled` and drop the import.
          `little-coder/tests/test_judge_gate_chokepoint.py` goes RED on that
          edit, which is the intended cost. The commit-time guard, the corpus
          and the drill are independent of that line. No runtime config was
          touched: `judge_enabled: false` is unchanged in every config this
          repository tracks, and no committed fixture sets it true.

## 2026-08-30 · U5 · class 2 (fix round 2 — the completeness test)
DECISION: The chokepoint's completeness is proved MECHANICALLY, not asserted.
          `little-coder/tests/test_judge_gate_chokepoint.py` AST-scans every
          module in `littlecoder` and fails when a new site could decide the
          question without the gate: any read of `judge_enabled` outside
          `judge_gate.py`, any `Judge(...)` construction outside
          `meta_wiring.py`, `build_meta_runner` no longer calling into
          `judge_gate`, a second copy of the rating-record rule in the
          package, and a floor on how many modules the scan reached.
CITED:    §C.7 — "prose verification is FALSIFIED". A chokepoint a future
          wiring path can simply not call is a convention, and this repository
          has now been walked through by omission three times. There are only
          two ways to answer "is the judge on?" — call the gate or read the
          field — and both are watched. Observed RED before green: a probe
          module reading the flag and constructing a `Judge` turned two tests
          red naming `_probe_bypass.py:7`; replacing the gate call with
          `judge_permitted = True` turned the third red.
REVERT:   Delete `little-coder/tests/test_judge_gate_chokepoint.py`. The gate
          keeps working; only the guarantee that nothing bypasses it by
          omission is lost.

## 2026-08-30 · U5 · class 2 (fix round 2 — the guard stops parsing YAML)
DECISION: `scripts/checks/check-judge-flag.ps1` no longer contains any notion
          of what YAML true looks like. It reads the STAGED bytes and hands
          them to `scripts/checks/lib/judge_flag_decide.py`, a transport that
          asks `littlecoder.judge_gate` — the daemon's own decider — and fails
          closed (DENY + `CANNOT-TELL`) if it cannot reach it. The
          rating-record rule moved to `judge_gate.read_rating_record`;
          `scripts/checks/lib/judge_dryrun.py` now delegates there instead of
          carrying its own copy.
CITED:    A regex approximating a YAML parser is a second and worse parser.
          Measured before the change: of 18 deliberately chosen spellings, 10
          turned the flag ON for the daemon and were invisible to the pattern;
          across the 43-row shipped corpus, 27 turn it ON and the pattern saw
          13. Same drift class as the SQL-CHECK-vs-zod-enum defect this
          repository already fixed by testing two readers against each other.
          Cost accepted, stated in the script header: on a machine with no
          working python a commit staging any YAML is now refused, where
          before only a commit that already looked like a flip was.
REVERT:   `git revert` the change to `check-judge-flag.ps1`, delete
          `scripts/checks/lib/judge_flag_decide.py`, and restore
          `_RATING_REQUIRED` + the inline rule body in `judge_dryrun.py`. The
          runtime chokepoint is independent and survives that revert.

## 2026-08-30 · U5 · class 2 (fix round 2 — one corpus, two executors)
DECISION: `scripts/checks/fixtures/judge-flag-corpus.json` holds 43 YAML
          spellings generated by MECHANISM (scalar case, scalar quoting, key
          quoting and hex escaping, flow style, anchors and aliases, merge
          keys, document structure, whitespace, comments, relocation) plus 12
          OFF controls, each PINNED with what the daemon's real loader
          returns. It is read by two independent executors:
          `little-coder/tests/test_judge_flag_corpus.py` (loader vs the gate's
          decider) and `scripts/checks/verify-judge-flag-guard.ps1` (the
          shipped guard, end to end in a scratch git repo, now 30 cases).
          The retired regex is stored in the corpus and re-run against every
          row, so the reason it was removed stays executable.
CITED:    The house pattern of two things that must agree, tested against each
          other — `harness.config.json`'s two readers, ScopeNode's columns vs
          `models.py`, the zod enum vs the SQL CHECK, the anchor schema's three
          readers. Generated by mechanism rather than collected from what the
          verifiers tried, because "the ones the verifier found" is a patch
          with more rows. Observed RED: replacing the gate's pydantic coercion
          with a strict `value is True` check failed 5 rows immediately.
          The guard is deliberately a SUPERSET of the loader (every document
          of a multi-document stream; the key at any depth), so four rows are
          over-denials and are named in the test — over-denying a commit costs
          a conversation, under-denying is the defect being replaced.
REVERT:   Delete the fixture and both readers' corpus tests. The gate and the
          guard keep working; only the proof that they cannot drift is lost.

## 2026-08-30 · U5 · class 1 (recorded because it was surprising)
DECISION: The guard writes its request file with
          `[System.IO.File]::WriteAllText`, and the decider decodes
          `utf-8-sig`.
CITED:    PowerShell 5.1's `Set-Content -Encoding utf8` writes a BOM;
          `json.load` rejects it; the decider exited 5; the guard failed
          closed and exited 1 — the SAME exit code as a correct denial. Every
          DENY case in the drill went green while the decider had never run.
          Only the audit-line assertions distinguished them, and the drill now
          asserts that corpus denials say `DENY`, not `CANNOT-TELL`. Logged
          because "the check passed" and "the check ran" were indistinguishable
          without that assertion, which is the failure shape this phase keeps
          recording.
REVERT:   Restore `Set-Content -Encoding utf8`; the `utf-8-sig` decode makes it
          work anyway, which is why both halves were fixed.
