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
12 of 12 occurrences on the coder plane and 9 of 12 on ao-worker-1 have a
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
