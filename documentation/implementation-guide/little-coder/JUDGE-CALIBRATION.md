# judge_enabled — the calibration plan

**Status:** PLAN. Nothing here enables anything.
`observer.judge_enabled` is `false` in every config in this repo and this
document does not change that. It says what has to be measured, what evidence
would justify flipping it, what breaks if it is flipped uncalibrated, and how
to get back.

**Phase:** U5 of `documentation/implementation-guide/dark-factory-unification/PLAN.md`
— "`judge_enabled` calibration plan for expertise minting (the one-line §13 gap)".

**Executable component:** `scripts/checks/check-judge-dryrun.ps1`
(engine: `scripts/checks/lib/judge_dryrun.py`; proof it can fail:
`scripts/checks/verify-judge-dryrun.ps1`).

---

## 1. The current state, verified

### 1.1 The flag is off, in one place, for both planes

`judge_enabled` is not set in any compose file. `coder/docker-compose.yml:113`
mounts `../little-coder/config:/app/config:ro` and sets
`LC_CONFIG=/app/config/little-coder.config.yaml`, so the value comes from the
YAML and nowhere else:

- `little-coder/config/little-coder.config.yaml:134` → `judge_enabled: false`
- `agent-org/agent-bridge/worker-configs/worker-1/little-coder.config.yaml:137`
  → `judge_enabled: false`. That file is GENERATED and gitignored
  (`agent-org/.gitignore:25`) — `agent-org/scripts/gen-worker-configs.py`
  copies the canonical config and rewrites only the `open_terminal_url` line,
  so the observer block, `judge_enabled` included, is inherited verbatim.
  Verify it in a live checkout, not in a fresh clone; worker-2 is identical.

The consequence is in `little-coder/src/littlecoder/meta_wiring.py:31`:

```python
if not config.observer.enabled or not config.observer.judge_enabled:
    return MetaRunner(..., similarity=default_similarity, judge=None)
```

With `judge=None`, `MetaRunner.iterate` skips `_mint_from_unassigned`
(`meta.py:182`) and `_can_draft()` is false (`meta.py:233`) because drafting
needs both a judge and a `skill_dir`. So: no clusters are ever minted, no
skills are ever drafted, and every occurrence accumulates in the unassigned
pool forever.

Live confirmation (2026-08-30):

```
docker exec little-coder sh -c 'ls /var/lib/little-coder/skill/knowledge/'
# 07f906a1b2fa32e2.md   <- cluster_id: sample-cluster-id, status: retired
```

The library is not literally empty — it holds ONE hand-written test fixture
from 2026-05-23 with a placeholder `cluster_id` and `status: retired`. Minted
entries: zero, on both planes, after three months of running.

### 1.2 The design's §13 is one line, and it is the line that matters

`documentation/implementation-guide/little-coder/Self-improving-little-coder-design.md`
§13 ("Preflight [Observer entry gate]") lists three exit criteria. The third is
the whole calibration:

> 3. Counterfactual + adversarial judge prompt dry-run on real examples +
>    human-rated (including baseline-covered cases, to verify the
>    compliance-vs-knowledge distinction fires — §5.6).

That is the entire written plan for minting expertise: one sentence naming a
dry run that was never built. §14's calibration table repeats it as a tunable
("Counterfactual judge prompt wording + few-shot | Dry-run human rating |
§5, §10.1") without adding a procedure. The config comment
(`little-coder.config.yaml:121-124`) points at the same missing artifact:
"OFF pending the operator's prompt-calibration dry-run (open item #2, design
§13)". The carry-over list in
`documentation/archive/implementation-guide/little-coder/integration-tasks.md:14`
is the fourth pointer to it: "Flip `observer.judge_enabled: true` ... after
dry-running the judge prompt (open item #2)."

Four places tell the operator to run a dry run. None of them says what it is,
what it measures, or what result would justify the flip. §2 of this document
is that.

### 1.3 The ao-worker pool: journals yes, minting no — and the failure is silent

The claim carried in the session memory was that the ao-worker pool "does not
mount journals/skill/cohort volumes". Checked against both compose files, that
is **half wrong, and the wrong half is the dangerous one.**

`docker inspect ao-worker-1 --format '{{range .Mounts}}...'` (2026-08-30):

| destination | ao-worker-1 | little-coder |
|---|---|---|
| `/var/lib/little-coder/journals` | `agent-org_ao-worker-1-journals` (volume) | `coder_little-coder-journals` (volume) |
| `/var/lib/little-coder/sessions` | `agent-org_ao-worker-1-sessions` (volume) | `coder_little-coder-sessions` (volume) |
| `/var/lib/little-coder/skill` | **not mounted** | `coder_little-coder-skill` (volume) |
| `/var/lib/little-coder/cohorts` | **not mounted** | `coder_little-coder-cohorts` (volume) |
| `/var/lib/little-coder/polyglot` | **not mounted** | `coder_little-coder-polyglot` (volume) |

Journals ARE durable on the pool — `agent-org/docker/docker-compose.yml:332`
added them under memory-plane Phase 0.3, and the comment there says exactly
why. So the pool DOES accrue the evidence corpus.

What it does not have is anywhere to put the output. `skill/` and `cohorts/`
exist inside the container but live in its writable layer:

```
docker inspect ao-worker-1 --format 'Created={{.Created}}'
  Created=2026-08-29T19:35:57Z
docker exec ao-worker-1 stat -c '%n mtime=%y' /var/lib/little-coder/{skill,cohorts,journals}
  /var/lib/little-coder/skill    mtime=2026-08-29 19:35:59   <- container creation
  /var/lib/little-coder/cohorts  mtime=2026-08-29 19:35:59   <- container creation
  /var/lib/little-coder/journals mtime=2026-08-23 17:14:49   <- predates it (volume)
```

The two directories were re-created with the container; the journals volume
outlived it. So if `judge_enabled` were flipped on the pool today, minting
would appear to work — clusters and drafts would be written, the report would
show `drafted_skill_ids`, the metrics would move — and every artifact would be
destroyed by the next `docker compose up -d` that recreates the worker. The
cohort store would reset to zero clusters at the same moment, so the next
iteration would re-mint from scratch: an infinite, invisible re-minting loop
that burns GPU and produces nothing. **That is a prerequisite, not a nuance:
the pool must not have the judge enabled until `skill` and `cohorts` are named
volumes with a backup sidecar, exactly as the journals were.**

---

## 2. The dry run

`scripts/checks/check-judge-dryrun.ps1` answers the one question §13 leaves
unspecified: *given the journals we actually have, what would the judge be
handed, and does it look like something a craft gap can be minted from?*

```powershell
.\scripts\checks\check-judge-dryrun.ps1                       # the little-coder plane
.\scripts\checks\check-judge-dryrun.ps1 -Container ao-worker-1 # the org pool
.\scripts\checks\check-judge-dryrun.ps1 -EmitPrompts -OutDir .\dryrun
.\scripts\checks\check-judge-dryrun.ps1 -RequireReady          # as an enablement gate
```

### 2.1 What it measures

It runs the REAL projection — `littlecoder.cohorts.rebuild` with
`littlecoder.meta.default_similarity`, the same call the daemon makes — over
the journals on disk, and reports, per `(lang, task_shape)` unassigned pool:

| Measure | Why it decides the flip |
|---|---|
| `pool_size` | `Judge.mint_clusters` returns immediately when the pool is below `min_pool_size` (`judge.py:298`). Below it, enabling the judge changes nothing at all. |
| `distinct_signals` | A pool of N identical strings is one signal seen N times. The judge is asked to find *why these cohere*; with one distinct string the only honest answer is "they are the same line". |
| `degenerate_ratio` | Fraction of signals carrying no payload — `exit 1: ` with an empty stderr tail, or the synthesized `task ended fail (no signal)`. This is the poisoning predictor (§3.2). |
| `task_shape` distribution | Design §5.5 scopes clusters by `lang` + `task_shape`. If every occurrence is `unknown`, the scope key has collapsed and clusters cannot be shape-separated. |
| founding-knowledge parity | `baseline_covers` is only sound if the judge sees the same floor the agent reads (§3.3). |
| polyglot corpus | §13 exit criterion 2 (measured baseline variance, §8.3) cannot be satisfied against an empty corpus. |

With `-EmitPrompts` it also assembles the actual prompt via
`littlecoder.judge.build_messages` — the real function, so the artifact a human
rates in §2.4 is the true prompt and not a paraphrase — and writes it to
`-OutDir` as the rating packet.

### 2.2 What it deliberately does NOT measure

It never asks what the judge would *answer*. That requires an LLM call, and a
"dry run" that mints is not a dry run. The report carries this explicitly:

```json
"would_mint": null,
"would_mint_note": "UNKNOWABLE without an LLM call, by design. ..."
```

It writes nothing. Proven, not asserted — md5 + mtimes of
`/var/lib/little-coder/cohorts/*` and `/var/lib/little-coder/skill/knowledge/*`
are identical before and after a `-EmitPrompts` run, and the drill re-hashes
the whole fixture tree against `MANIFEST.sha256` after driving every branch.
The probe is fed to the container on stdin (`docker exec -i ... python -`), so
not even the probe file is copied in.

### 2.3 When it refuses to answer

Non-zero exit is reserved for "cannot tell", and "cannot tell" is never
rendered as a pass:

| Exit | Meaning |
|---|---|
| 0 | a verdict was produced (`READY-FOR-RATING` or `NOT-READY`) |
| 1 | verdict `NOT-READY` **and** `-RequireReady` was passed (the gate form) |
| 3 | no readable journal evidence at that path |
| 4 | the cohort store already holds clusters — see below |
| 5 | `littlecoder` not importable, or the config unreadable |
| 6 | the container is not running / the probe returned nothing |

Exit 4 is the subtle one and the reason the fidelity claim is defensible.
`clusters.assign` returns `UNASSIGNED` **without ever calling the similarity
function** when no existing cluster shares the occurrence's `(lang,
task_shape)` scope (`little-coder/src/littlecoder/clusters.py:144-150`). So
while the store holds zero clusters, this stub-similarity projection is
bit-identical to what a judge-enabled daemon's `EmbeddingSimilarity`
projection would produce. The moment one cluster exists, routing depends on
embeddings the dry run does not compute — and the script exits 4 rather than
quietly reporting an approximation as the answer.

### 2.4 The human half — §13 exit criterion 3

The dry run gets you to `READY-FOR-RATING`; it cannot get you past it. The
remaining step is the one §13 actually asks for, and it is a human one:

1. Run with `-EmitPrompts -OutDir <dir>`. Each mintable pool yields the exact
   system + user messages `Judge.mint_clusters` would send.
2. For each, the rater answers on paper, before any model does:
   - Is there one craft gap here, or several, or none?
   - Is it already covered by the founding knowledge shown in the prompt?
     (This is the `baseline_covers` call — the compliance-vs-knowledge
     distinction §5.6 depends on, and the one §13 names explicitly.)
   - What tier-0 body would you write from it?
3. Only then run the same prompts against the model, by hand, through the
   gateway — no config change, no daemon involvement.
4. Score agreement. The rater's answers are the ground truth; the model's are
   the candidate.

**The bar (proposed, operator-adjustable — this is the number §14 says must be
measured rather than guessed):** across at least 10 rated pools, the judge must
(a) return zero clusters on every pool the rater called noise, and (b) agree
with the rater's `baseline_covers` verdict on at least 8 of 10. A false
`baseline_covers: false` is the expensive direction — it mints a tier-0 entry
that re-teaches the agent something it was already told, which is how the
library fills with restatements of the founding knowledge.

---

## 3. Measured today: what would actually happen if you flipped it now

`check-judge-dryrun.ps1`, both planes, 2026-08-30:

```
little-coder : 315 records, 12 occurrences, 3 pools, judge invoked on 3, MINTABLE 0
ao-worker-1  : 358 records, 12 occurrences, 1 pool,  judge invoked on 1, MINTABLE 0
```

The whole corpus, verbatim from the emitted prompt:

```
--- POOL ---
[0] (command_failed) exit 1:
[1] (command_failed) exit 128:
[2] (command_failed) exit 128:
[3] (command_failed) exit 128:
```

Degenerate ratio is 1.00 on that pool, 0.60 and 0.67 on the other two, 0.75 on
the org pool. `task_shape` is `unknown` for 12 of 12 occurrences on each plane.
Verdict on both planes: **NOT-READY**.

### 3.1 Failure mode A — the EMPTY library

The mild failure. `min_pool_size` is 3 (`judge.py:273`) and the judge is
instructed that "returning zero clusters with `pool_too_noisy: true` is a
perfectly valid answer". A well-behaved judge looking at four copies of
`exit 128:` returns nothing. The flag is on, iterations run, embeddings are
computed on every task end (`auto_iterate_on_task_end: true`,
`little-coder.config.yaml:135`), GPU is spent, and the library stays empty.

Cost: wasted inference and a false sense that the loop is live. Detection: the
Observer report shows `minted_cluster_ids: []` forever. Recoverable by
noticing, which nobody does — this is precisely the state the flag has been
protecting against for three months, except with the GPU bill.

### 3.2 Failure mode B — the POISONED library

The serious one, and a different failure with a different signature: the
library is not empty, it is *wrong*, and everything downstream consumes it.

Four mechanisms, each measured above rather than imagined:

1. **Exit codes become "craft gaps".** The signals carry no payload — the
   message is `f"exit {rc}: {stderr_tail}"` (`agent.py:491`) and the tail is
   empty in 12 of 12 live cases. A judge under instruction to find coherence
   in a scope-consistent pool can find it: "the agent does not check command
   exit codes in Python bugfix tasks" is a plausible-sounding cluster to mint
   from four `exit 1:` lines. It is not a craft gap. It is the absence of one.
2. **The clusters are immortal.** `cluster_id` is immutable by design
   (`clusters.py:1-20`) and cohort history keys on it. A junk cluster minted
   once acquires a `discriminator`, and from the next projection onward every
   new occurrence that scores above `similarity_floor` against that
   discriminator is routed INTO it (`cohorts.py:_route_occurrence`). A junk
   cluster does not sit still; it eats the pool that real clusters would have
   been minted from.
3. **`baseline_covers` is decided against the wrong baseline.** The agent
   reads three founding-knowledge files (`little-coder.config.yaml:36-41`:
   `environment.md`, `project-context.md`, `engineering-principles.md`). The
   judge is given two — `ObserverConfig.founding_knowledge_paths`
   (`config.py:217-223`) omits `project-context.md`, and its comment claims
   "the list mirrors `agent.extra_args --append-system-prompt`" while it does
   not. Missing files are skipped silently by `_founding_knowledge_block`
   (`judge.py:177-192`). So the judge systematically judges a smaller floor
   than the agent has, calls covered gaps uncovered, and mints tier-0
   knowledge that restates instructions the agent already receives at every
   task start. The dry run reports this as a blocker on both planes.
4. **The scope key has collapsed.** With `task_shape` `unknown` for 100% of
   occurrences, a "Python" cluster is minted across bugfix, refactor and
   investigation work indiscriminately — the exact failure §5.5 rejects
   per-repo scoping to avoid.

Blast radius — and here the honest answer is smaller than the obvious one,
because the retrieval half of the loop is not wired yet:

- **Damage that lands TODAY, no matter what else is wired.** The cohort store
  is corrupted. A junk cluster acquires a `discriminator`, and from the next
  projection on, every occurrence scoring above `similarity_floor` against it
  is routed INTO it (`cohorts._route_occurrence`) instead of into the pool a
  real cluster would have been minted from. `_prior_interventions`
  (`meta.py:540`) then BLOCKS a second draft for that cluster. So one junk
  mint does not sit still: it eats the evidence for the genuine gap that
  shared its scope, and it forecloses the draft that would have addressed it.
  This is the poisoning proper, and no downstream wiring is required for it.
- **Damage that is currently held back by an ACCIDENT.** The full feedback
  loop — library → agent's system prompt → shifted failures → new junk
  occurrences → next mint — needs the augmenter. Checked 2026-08-30:
  `augmenter.select` has ZERO production callers. The only importers of
  `littlecoder.augmenter` are `little-coder/tests/test_augmenter.py` and the
  module's own docstring; `agent.py` never calls it. Skill retrieval into the
  agent's context is DESIGNED (§7.4) and BUILT but NOT WIRED. So a poisoned
  library cannot reach the agent today.

That second point is containment by omission, not by design, and it must not
be read as a safety margin: the wiring is ordinary chapter-4/5 work, and on the
day it lands, a library poisoned months earlier becomes live instruction with
no new decision taken. Every artifact will carry a real `cluster_id`, real
counters and a real approval record, so it will be indistinguishable from
earned expertise by inspection.

Two barriers therefore stand between a bad mint and the agent's context, and
only one of them is deliberate:

1. `auto_merge_tier_0` is absent from both YAML files and defaults to `False`
   (`config.py:230`), so drafts land `status: pending` and a human approves
   before they are ever eligible. **Enabling `judge_enabled` and
   `auto_merge_tier_0` in one change removes the deliberate barrier.** They
   are two separate decisions and the second is out of scope here.
2. The augmenter is unwired — an accident, and the one to stop relying on.

### 3.3 Why the two failures need different responses

- Empty library → the corpus is too small or too clean. Wait, run more real
  work, re-run the dry run. Nothing is damaged.
- Poisoned library → the corpus is rich enough to look mintable but too
  degenerate to be. Waiting makes it WORSE, because junk clusters accumulate
  and absorb the pool. Stop, roll back (§5), and fix the signal (§4).

Reading a NOT-READY verdict as "not enough data yet, keep going" when the real
finding is "the data is structurally unusable" is how B gets reached while
believing A is being avoided.

---

## 4. What would justify enabling it

Each item is checkable, and the first four are checked by the script.

| # | Criterion | Checked by | Today |
|---|---|---|---|
| 1 | ≥ 3 pools with `pool_size` ≥ 5 | dry run `pools[].pool_size` | 0 |
| 2 | Every such pool has `distinct_signals` ≥ 3 | dry run | 0 |
| 3 | `degenerate_ratio` ≤ 0.34 on every mintable pool | dry run | 1.00 / 0.60 / 0.67 / 0.75 |
| 4 | `task_shape` is not `unknown` for ≥ 50% of occurrences | dry run | 0% |
| 5 | Founding-knowledge parity: judge reads exactly what the agent reads | dry run blocker | FAILS both planes |
| 6 | Polyglot corpus non-empty (§13 criterion 2, §8.3) | dry run blocker | empty |
| 7 | Human rating passes the §2.4 bar on ≥ 10 pools | human, on the emitted packet | not run |
| 8 | **Pool only:** `skill` + `cohorts` are named volumes with a backup sidecar | `docker inspect` (§1.3) | FAILS — writable layer |
| 9 | The transition is journaled to `audit.jsonl` (§13's closing line) | `lc admin` / audit record | n/a |

Criteria 1–6 are the gate form: `check-judge-dryrun.ps1 -RequireReady` exits 1
until they hold. 7–9 are human and are not automatable without automating away
the decision §13 deliberately keeps human.

### 4.1 The prerequisite work criteria 3–5 imply

None of this is enablement; it is what has to be fixed before enablement is a
question worth asking.

- **Signal payload.** `stderr_tail` arrives empty from the pi-extension
  activity file (`agent.py:73` reads `ev.get("stderr_tail", "")`; `otexec.py:57`
  and `:79` set it). Until an error record carries the failing command's actual
  stderr, every `command_failed` occurrence is a bare exit code and no amount
  of accumulated volume makes the pool mintable. This is the single highest-value
  fix: it converts the corpus from noise into evidence.
- **`task_shape` inference.** `classify` (`task_shape.py:120-152`) needs
  `test_failure` errors or ≥3 `write_file`/`edit_file` tool calls to leave
  `unknown`. Live journals emit `tool="bash"` for essentially everything, so
  the classifier can never fire. Either the tool taxonomy gets richer or the
  classifier learns to read what is actually written.
- **Founding-knowledge parity.** One line: add
  `founding_knowledge_paths` to `observer:` listing the same three files the
  agent's `--append-system-prompt` args name, and make
  `gen-worker-configs.py` carry it. A test asserting the two lists are equal
  keeps them from drifting apart again.

---

## 5. Rollback

The flip itself is one line of YAML, and reverting it is symmetric. What is
not symmetric is anything the judge minted while it was on.

**Reverting the flag** (`little-coder/config/little-coder.config.yaml:134`,
plus the two generated worker configs):

```powershell
# 1. flip it back
#    observer.judge_enabled: true -> false
# 2. the config is a read-only bind mount, so a restart is enough - no rebuild
docker compose -f coder/docker-compose.yml --env-file .env restart little-coder
# 3. confirm the judge is not wired
.\scripts\checks\check-judge-dryrun.ps1     # judge_enabled : False
```

**Reverting the artifacts** — the part that matters. In severity order:

1. **Drafted skills** are files under `/var/lib/little-coder/skill/<kind>/`.
   With `auto_merge_tier_0` off they are `status: pending`, and with the
   augmenter unwired (§3.2) nothing was served to the agent either way; setting `status: retired` in the frontmatter is enough,
   and `_prior_interventions` (`meta.py:540`) then frees the cluster again.
   Do NOT delete them: a retired artifact is the evidence of what went wrong.
2. **Minted clusters** live in `cohort-store.json`. The store is a DERIVED
   index, rebuildable from journals by design (`cohorts.py:1-12`) — with
   `judge=None`, `MetaRunner._rebuild_carrying_clusters` re-projects onto the
   prior store, which is what carries junk clusters forward. Removing the
   checkpoint file makes the next iteration rebuild a clean, cluster-free store
   from the untouched journals. Take a copy first (it is the evidence of the
   failed run), then remove it and let the next iteration rebuild.
3. **Journals are never rolled back.** They are the append-only ground truth
   and nothing the judge does writes to them. This is why the rollback is safe
   at all: the evidence corpus survives every mistake made downstream of it.
4. **`audit.jsonl`** records approvals and is append-only. A wrong approval is
   corrected by a later record, never by editing the file.

**Backups** — the `little-coder-backup` sidecar
(`coder/docker-compose.yml:167-198`) archives `journals`, `skill`, `cohorts`,
`polyglot` and `sessions` nightly to `backups/little-coder/`, retaining
`LC_BACKUP_RETAIN_COUNT` (default 2). **That is a ~48-hour window.** A poisoned
mint discovered on day 3 has no clean archive to restore, so recovery is
rebuild-from-journals (step 2) rather than restore-from-backup. Before the flip
day, take a manual archive of `skill` and `cohorts` and keep it outside the
retention window.

**Rollback does not apply to the ao-worker pool** until §1.3 is fixed: with
`skill` and `cohorts` in the container's writable layer there is nothing to
roll back and nothing to inspect — the evidence of the failure is destroyed by
the same recreate that ends it.

---

## 6. Provenance

Every claim above was produced by a command, not by reading:

```powershell
.\scripts\checks\check-judge-dryrun.ps1                          # little-coder plane
.\scripts\checks\check-judge-dryrun.ps1 -Container ao-worker-1   # org pool
.\scripts\checks\check-judge-dryrun.ps1 -EmitPrompts -OutDir <d> # rating packet
.\scripts\checks\verify-judge-dryrun.ps1                         # 13/13 cases
docker inspect ao-worker-1 --format '{{range .Mounts}}{{.Type}} {{.Name}} -> {{.Destination}}{{"\n"}}{{end}}'
docker exec ao-worker-1 stat -c '%n mtime=%y' /var/lib/little-coder/skill /var/lib/little-coder/cohorts /var/lib/little-coder/journals
```

`verify-judge-dryrun.ps1` exists because a check nobody has watched fail is not
known to check anything. It drives every branch of the dry run against
synthetic fixtures and asserts the exit code of each: the three CANNOT-TELL
paths (3, 4, 5), the container-unreachable path (6), the NOT-READY gate (1),
and a healthy corpus that reaches `READY-FOR-RATING` with a mintable pool (0) —
without which the drill would only prove the check knows how to say no. It then
re-hashes the fixture tree to prove the dry run wrote nothing.
