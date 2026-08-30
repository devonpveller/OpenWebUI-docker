# U4 close — findings, evidence, and what is still not true

Session: `wt-u4close` (branch `work/u4close`), 2026-08-30.
Task: close `dark-factory-unification` PLAN §2's **U4** *Validated by* column, or argue that
it cannot be met as written and propose a narrowing amendment.

> **U4 Validated by:** *"Gym: same anchored item run per quadrant (runner × target),
> outcomes compared; stall → oracle observed firing at least once"*

**Verdict: BOTH HALVES ARE NOW MET, on executable evidence.** No amendment is proposed;
the argument for that is in §7 below, because "we managed it" is only half a reason and the
other half is "the column was never unmeetable — three branches each held one piece".

---

## 0. The one-line answer, with the commands that produce it

```
$ cd scripts/agent-harness && python -m quadrant.cli report
**COMPARED 4/4**
| little-coder x self     | completed | 2/2 | 72.8 | 1/2/0 | 1 changed | mechanical ... |
| little-coder x project  | completed | 2/2 | 65.4 | 1/2/0 | 1 changed | mechanical ... |
| claude-code x self      | completed | 2/2 | 35.8 | 1/2/0 | 1 changed | normative ...  |
| claude-code x project   | completed | 2/2 | 33.5 | 1/2/0 | 1 changed | normative ...  |
exit 0

$ queue.ps1 -Oracle -Id u4-stall-probe          (AI_STACK_WORKTREE_STATE=.quadrant/stall/state)
2026-08-30 22:33:04Z  u4-stall-probe  escalate  rounds=3 stall=2/2
                      little-coder -> claude-code (hand back to little-coder)
```

---

## 1. Why the column read UNMET: the pieces were on three branches, not missing

The park recorded on 2026-08-30 (`DECISIONS.md`, "U4 · PARKED") is accurate about the
*state*, and the state was: `work/dfu-u4` had a real little-coder dispatch that had carried
one anchored item end to end; `work/u4quad` had a comparison harness that refused to report a
quadrant it had not run; `work/u4oracle` had a mutation-proven stall detector. None of them
could satisfy the column alone, and none of them was merged.

Merging the three into `work/u4close` produced **three conflicts**, all recorded in the merge
commits and repeated here because a conflict resolved wrongly is a defect introduced under
cover of a merge:

| file | what disagreed | resolution, and the check behind it |
|---|---|---|
| `test_anchor_schema.py` | HEAD kept a mid-file `import shutil`; `work/dfu-u4` removed it as dead | took dfu-u4's side — `shutil` is imported at line 18 of the same file, so the mid-file one is a genuine F811. `ruff check scripts/agent-harness` is clean. |
| `harness.config.json` | dfu-u4 replaced `endpoint: http://127.0.0.1:8090` with `transport: docker-exec` + `base_url`/`container`/`lease`; u4quad kept the old block and appended a `fixture` runner | took dfu-u4's little-coder block **plus** u4quad's `fixture` runner. Verified by a recursive key-walk against both parents: nothing missing except `runners.little-coder.endpoint`, which dfu-u4 deleted deliberately. |
| `MODULE.md` | both branches appended a row to the public-surface table at the same anchor | kept **both** rows — they describe different files and neither supersedes the other. |

### The conflict that was NOT textual

The key-walk above found the real one. `quadrant/schema.json` required `endpoint` of every
little-coder runner; dfu-u4 had deleted `endpoint` because that door does not exist on this
machine. After the merge:

```
$ python -m quadrant.cli preflight
MISCONFIGURED: runner 'little-coder' is missing required field(s): endpoint
exit=2
$ python -m pytest -q
1 failed, 205 passed      (test_the_live_config_defines_a_buildable_quadrant_matrix)
```

That is the RED this work started from. It was called out in the merge commit before it was
fixed, rather than found afterwards.

---

## 2. Half (a) — the four quadrants

### F1. The little-coder runner could not be *handed* a workspace, and why

Three measured constraints, each re-verified here rather than relayed:

1. **The task API is not published.** `docker inspect little-coder --format
   '{{json .NetworkSettings.Ports}}'` → `{"9090/tcp":[]}`. `docker exec little-coder curl
   -sS http://localhost:8090/health` → `{"status":"ok",...}`. (The compose file DECLARES
   `127.0.0.1:9091:9090`. The declared and running states still disagree and **the cause is
   still not established** — nothing in this work depends on 9091, and no explanation for the
   disagreement is offered here.)
2. **The workspace is a docker volume, not a bind mount.** `little-coder-workspace` at
   `/workspace` in both `little-coder` and `open-terminal`.
3. **The daemon's focus path cannot reach a harness worktree.** `urlnorm.normalize_repo_url`
   requires host + owner/repo and rejects local paths and `file://`;
   `WorkspaceManager.clone` builds `https://{host}/{owner}/{repo}`.

So the transport MIRRORS: `quadrant/lc_docker.py` copies the workspace the target adapter
produced into the container, runs the task, and copies back the files that changed. The
changed set is computed by digesting the tree inside the container before and after —
**the whole changed set, not the item's prefix**, because copying back only `quadrant-item/`
would make `scope.out_of_scope_hits` read empty for every little-coder run by construction.
A column that cannot register a violation is worse than no column.

### F2. Three defects the live runs found, none of them predictable from reading

- **HTTP 422** — `channel must be one of ['batch','cli','owui','validation']`. The adapter
  sent `"quadrant"`. `channel` is a closed set in `TriggerRequest`.
- **HTTP 409 "no project focused"** — not the in-memory focus. `POST /tasks` calls
  `WorkspaceManager.is_focused()`, which requires `<workspace>/.git/HEAD` **on disk**
  (`daemon.py:404-410` explains why: a corrupt workspace once let a task "finish" on a tree
  it could not branch or commit in). Clearing the workspace to mirror ours in removed exactly
  that file. The mirror now gets its own `git init` + baseline commit.
- **Root-owned files** — everything arriving by `docker cp` is root-owned with the host's
  modes, and every command the agent runs executes as uid 1000
  (`docker exec open-terminal id user` → `uid=1000(user)`; the daemon's own clone is owned
  `1000:1000`). The transport now SAMPLES the owner of the existing workspace before clearing
  it and restores it after the copy — sampled, not configured, because it is a property of
  the running images.

### F3. A defect in the *report*, found only because a cell went from blocked to runnable

`report._rows` took `admitted[0]` — the OLDEST admitted record — as the record that speaks for
a cell. The two little-coder cells already had `not_run` records from 15:01Z. Without a
change, they would have gone on reading *"did not produce an outcome"*, `compared: false`,
beside a completed run in the same directory; the only way to finish the comparison would
have been to DELETE the blocked records, which is deleting evidence — the one move this
module refuses everywhere else. An OUTCOME record now outranks a non-outcome, and among
equals the most recent wins. Two tests, RED before the two-line fix.

### F4. `subprocess.run(..., text=True)` decodes with the LOCALE codec

Live: byte `0x9d` (unmapped in cp1252) in a local model's answer killed the reader thread
inside `subprocess`, left `stdout` as `None`, and the cell recorded
`AttributeError: 'NoneType' object has no attribute 'rpartition'` — an error record about the
harness in a table about runners. Milder form, visible in the first successful transcript:
the model's own `Unicode` examples recorded as `ÃœnicÃ¶dÃ©`.

Fixed at a chokepoint (`quadrant/proc.py`), not at the 13 call sites, per DECISIONS.md's
"ENUMERATE-AND-PATCH LOSES" — **and its corollary**: the completeness test SCANS the package
directory for `subprocess.run(` rather than holding a list of files. A new file with a raw
call fails it on the day it is added, whatever the file is called.

### F5. The claude-code records are REUSED, not re-run — and what that costs

`claude-code x self` and `claude-code x project` were produced by `work/u4quad` on
2026-08-30 (real `claude.exe` invocations, real cost blocks: $0.5437/1627 tokens and
$0.4639/2251 tokens). They were **copied into this results set unmodified**; re-running them
would be real external spend (a §C.2 class-4 line) for no new information, and the item digest
is unchanged (`c585bee6fee3043c…`) so admission accepts them.

**The cost, stated rather than worked around:** their `evidence.workspace` still points into
`.claude/worktrees/wt-u4quad`. Removing that worktree makes those two records fail admission
(`evidence.workspace does not exist on disk`) and the comparison drops to 2/4. That is the
honest behaviour of the admission gate and it is not being suppressed. Whoever retires
`wt-u4quad` should re-run those two cells first, or accept the drop.

### F6. What the comparison does and does not support

n=1 in every cell, and the report says so in every confidence column. It supports
*"both runners completed this item"* and nothing about which is better. The wall-clock
numbers (72.8/65.4 local vs 35.8/33.5 cloud) are one run each and are not a finding.
`quadrant.repeats` is still 1.

---

## 3. Half (b) — the oracle fired on a stall that HAPPENED

### What was real, item by item

- **The rounds were real dispatches.** Three separate runs of `quadrant.cli run --runner
  little-coder --target project --item u4-stall`, each a `POST /tasks` to the live daemon with
  its own task id in the daemon's journals, each answered by qwen36-27b through LiteLLM.
- **The failures were real.** Each round's `-Reason`/`-Evidence` is the output the item's
  PRISTINE guards actually printed for that attempt, read out of that round's `record.json`
  by the script — not typed into it.
  Every round: `FAIL stall-item/test_normalize.py::test_trims_and_preserves_case:
  AssertionError:` / `2/3 pristine test(s) passed`.
- **The commits were real.** One commit per round, built from that round's ACTUAL artifact
  with git plumbing (`--no-filters`, so the runner's bytes are stored verbatim rather than
  line-ending-normalised). The three implementations differ
  (`" ".join(text.split()).lower()` / a `re.sub` version / a third with a different
  docstring), so the head genuinely MOVED each round — `moved: true` on all three.
- **The verdicts went through the shipped tool.** `queue.ps1 -Fail`, which is what calls the
  detector.

### What was INDUCED, said plainly

The item was chosen to be unsatisfiable. `quadrant/items/u4-stall`'s
`test_trims_and_lowercases` and `test_trims_and_preserves_case` demand different outputs for
the same input, so no pure function passes both. The runner therefore could not converge —
which is what "a task that will genuinely stall it" means. **A stall induced by choosing an
impossible task is still a real stall; a stall written into a fixture is not, and this is not
that**: nothing wrote a stall state anywhere. Three real failing rounds were produced and the
detector is what decided they constituted a stall.

### The trail, as the detector recorded it

```
round 1  sig=3925e1845fc3353b  sha=8f955ffa5eb8  PROGRESS: new failure on new code
round 2  sig=3925e1845fc3353b  sha=233a4a2b197d  no progress: a failure already seen on this item (a cycle, not a step)
round 3  sig=3925e1845fc3353b  sha=6a309a24b1a8  no progress: a failure already seen on this item (a cycle, not a step)
'u4-stall-probe': STALLED - 2 consecutive round(s) with no new information
                  (3 failing rounds, 1 distinct failure signature(s)).
  ORACLE-ON-STALL: little-coder/local-default -> claude-code/opus, then hand back to little-coder.
  recorded in the ledger.
```

The durable record (`.quadrant/stall/state/oracle-escalations.jsonl`) carries
`stalled_runner: little-coder`, `stalled_model: local-default`,
`profile: local-work-cloud-review`, `oracle_runner: claude-code`, `oracle_model: opus`,
`hand_back_to: little-coder`, `rounds: 3`, `stall: 2`, `signatures_seen: 1`, and the
three-entry trail above. `oracle_on_stall.py pending u4-stall-probe` → `claude-code`.

### Why the failure signature is stable, which is the property that made this observable

`guards.py` executes the item's test functions DIRECTLY rather than under pytest, so a bare
`assert` raises `AssertionError` with an EMPTY message. The guard prints the same line
whatever the implementation returned. Had the item been run under pytest's assertion
rewriting, each round's message would have embedded that round's actual value, every
signature would have been novel, and the detector would have scored three rounds of
"progress" — correctly, on its own terms, and the stall would have been invisible.
**This is a real limitation of the detector as ported, not a trick used to get a firing**:
a failure text that embeds a varying value defeats the novelty axis. It is recorded in §6.

### What was NOT done

The oracle's ROUND was not run. The escalation is `pending`, unconsumed. The column asks for
the oracle to be *observed firing*, and §7 of ORCHESTRATION-DESIGN is explicit that firing
records an escalation rather than swapping the worker (`oracle_on_stall.py`'s own docstring:
"It does NOT swap the worker for a better one"). Serving the round would be a `claude-code`
dispatch — real external spend, a class-4 line — and it is not what the column asks for.

### Isolation

The probe ran with `AI_STACK_WORKTREE_STATE` pointing at `.quadrant/stall/state`, so **no row
was written to the operator's live queue and no line was written to the live escalation
ledger** — verified afterwards: the live state dir has no `oracle-escalations.jsonl` at all
and no queue row matching `stall`. The anchor was confirmed with a `-By` string that says
what it is rather than forging an operator's name — the gate is satisfied mechanically only
because `-Submit` refuses without it, and §C.1 exempts U0–U7 from it.

### It is re-runnable, and that is committed

The first pass was two ad-hoc scripts under `.quadrant/stall/` (gitignored), which is not a
reproduction anybody else can perform. It is now
**`scripts/agent-harness/observe-oracle-on-stall.ps1`**, beside the drill it is deliberately
not:

    .\scripts\agent-harness\observe-oracle-on-stall.ps1 -LeaseOwner <id>   # dispatch N rounds
    .\scripts\agent-harness\observe-oracle-on-stall.ps1 -Reuse             # judge the runs on disk

Verified end to end in `-Reuse` mode against the three rounds above: three fresh commits,
three rounds through `queue.ps1 -Fail`, one escalation on round 3, exit 0, probe branch
deleted. It excludes `error` records from the round set deliberately — a harness fault is not
a round, and feeding one to the detector would sign a tooling failure as evidence about the
runner. It also refuses when two rounds hash to the same commit, because the movement axis
cannot be observed on identical bytes.

It can never be a CI check: it needs the coder plane up and focused, the inference plane up,
the `coder` lease held by the caller, and minutes. `verify-oracle-on-stall.ps1` remains the
mechanism drill (34/34, seconds, no live planes); this is the experiment.

---

## 4. Everything that was run, and what it printed

| command | result |
|---|---|
| `python -m pytest -q` (scripts/agent-harness) | **214 passed** (205 + 1 failed at the merge); `test_quadrant.py` alone: 47 |
| `python -m ruff check scripts/agent-harness` | All checks passed |
| `python -m quadrant.cli preflight` | 4/4 READY |
| `python -m quadrant.cli report` | **COMPARED 4/4**, exit 0 |
| `python -m quadrant.prove_guards` | **14/14 guards proven to bite** |
| `.\verify-oracle-on-stall.ps1` | **34/34 checks passed** (mechanism drill; constructed stall) |
| `.\observe-oracle-on-stall.ps1 -Reuse` | **OBSERVED**, exit 0 (the real stall, re-derived from the runs on disk) |
| `.\verify-dispatch.ps1 -Offline` | **51/51 checks passed** (real transport NOT covered — `-Offline`) |
| `queue.ps1 -Oracle -Id u4-stall-probe` | one `escalate` row, `little-coder -> claude-code` |

`verify-merge-protocol.ps1` was **NOT run**. DECISIONS.md 2026-08-30 records that drill
leaving the operator's checkout in a detached mid-rebase state, and its two proven
contributing defects (`Invoke-DrillGit` swallowing every git error; `git -C ""` silently
running in the current directory) are not fixed. Running it was not needed for this column.

---

## 5. State left behind

- **Held**: nothing. The `coder` lease was acquired for every live run and is RELEASED
  (`lease.ps1 -Status` → "no leases held - all planes free").
- **little-coder**: re-focused on its prior project (`https://github.com/anthropics/skills`)
  after every run, verified by `GET /health` and by `ls /workspace`. Restoring is
  best-effort but never silent: a failure would be written into the run record's `notes`.
- **Branch `work/u4stall-probe`** exists locally, pointing at the third round's commit. It is
  evidence for the trail's shas and is NOT merged anywhere. Delete with
  `git branch -D work/u4stall-probe` when the record is no longer needed; the escalation
  trail then references commits that are only reachable from the reflog.
- **Nothing was pushed.** `git log --oneline origin/work/u4close` does not resolve; this
  branch exists only locally.

---

## 6. Findings that belong to something else (the overflow queue)

- **F6.1 — the detector's novelty axis is defeated by failure text that embeds a value.**
  See §3. Any pipeline that feeds it pytest output (which rewrites assertions to include the
  actual value) will see a novel signature every round and never stall. agent-org avoids this
  because its metric is a compiler-error COUNT, not a message. Owner: U3/U4 follow-up.
- **F6.2 — `little-coder` publishes no ports while `coder/docker-compose.yml` declares
  `127.0.0.1:9091:9090`.** Still unexplained. Measured again today. Not blocking anything.
- **F6.3 — the claude-code quadrant records depend on `wt-u4quad` existing.** §2 F5. Whoever
  retires that worktree inherits this.
- **F6.4 — `verify-merge-protocol.ps1` is still the drill that rebased the live work line**,
  with both contributing defects unfixed. Nobody owns it.
- **F6.5 — PLAN §0's A11 row now understates the evidence.** It says "PARTIALLY PROVEN … Still
  untested: … whether the profile GOVERNS anything". After this work, `dispatch.ps1` still has
  no consumer in `queue.ps1`, so the *profile-governs-dispatch* half is STILL true and should
  not be edited away — but "the oracle-on-stall path (nothing escalates a `status: timeout`)"
  is now partly answered: an escalation is resolved through `config.resolve_role`, the same
  profile mechanism, and fired for real. This note does not edit PLAN.md; the orchestrator
  owns that.

---

## 7. Why NO amendment to the U4 column is proposed

The brief allowed an amendment if a half is "genuinely unmeetable as written". It is not, and
the reason matters more than the outcome:

- **The runner axis was never blocked by the column.** It was blocked by ONE configuration
  key — a schema that demanded `endpoint` of a runner reached by `docker exec`. The column
  asked for a real thing and the harness could not do it; the harness was what needed
  changing.
- **The target axis is real for both runners.** `little-coder x self` mirrored a 988-file
  checkout of this repository; `little-coder x project` mirrored a scratch repo. Those are
  genuinely different environments, which is what the axis measures. `dfu-u4-findings.md` F6
  concluded that `target: self` was "not available to the local runner as written" — that
  conclusion was correct about the route it considered (focus the daemon on a git-host URL)
  and wrong as a general claim, because a workspace can be mirrored rather than cloned.
- **"Observed firing at least once" is satisfiable without an oracle round**, by the
  mechanism's own definition of firing.

Amending the column would have narrowed a requirement that was met the same afternoon. The
honest failure here would have been to amend first and discover that second.

---

## DECISIONS entries to append

*(The orchestrator appends these. This branch does not touch DECISIONS.md.)*

```
## 2026-08-30 · U4 · CLOSED — both halves of the column met, on executable evidence
FINDING:  U4 was parked because the three pieces that satisfy its column lived on three
          unmerged branches (work/dfu-u4's dispatch, work/u4quad's comparison harness,
          work/u4oracle's stall detector), and because merging them exposed one semantic
          clash: quadrant/schema.json demanded `endpoint` of a little-coder runner while
          dispatch.ps1 had already replaced that door with `docker exec`.
          RED, stated in the merge commit before it was fixed:
            python -m quadrant.cli preflight -> MISCONFIGURED ... missing 'endpoint', exit 2
            python -m pytest -q              -> 1 failed, 205 passed
HALF (a): python -m quadrant.cli report -> COMPARED 4/4, exit 0. The two little-coder cells
          are REAL dispatches to the local model, task ids 01M1ABK4XG05T9Y1XSPB7WTS3Q and
          01M1ABNT71A16D636NVQAY1QA2 in the daemon's own journals; acceptance is the item's
          PRISTINE guards run host-side, 9/9 tests + 1 frozen file unmodified in each cell.
          The two claude-code cells are work/u4quad's records, REUSED unmodified rather than
          re-run (re-running is class-4 spend for no new information) - and their
          evidence.workspace still points into wt-u4quad, so retiring that worktree drops the
          comparison to 2/4. Recorded, not worked around.
HALF (b): the oracle fired on a stall that HAPPENED. quadrant/items/u4-stall is an
          unsatisfiable item (two tests demand different outputs for one input); three real
          dispatches produced three real failing rounds with one failure signature over three
          real commits of the three different implementations little-coder wrote, and
          queue.ps1 -Fail escalated on round 3:
            little-coder/local-default -> claude-code/opus, hand back to little-coder
          rounds=3 stall=2/2, signatures_seen=1, three-entry trail, in the ledger.
          INDUCED, and said plainly: the task was chosen to be impossible. A stall induced by
          choosing an impossible task is a real stall; nothing wrote a stall state anywhere.
          The oracle's ROUND was not run - firing records an escalation rather than swapping
          the worker (§7), and serving it is a class-4 claude-code dispatch.
NOT AMENDED: the column was never unmeetable. The runner axis was blocked by one config key.
          Amending would have narrowed a requirement that was met the same afternoon.
EVIDENCE: 214 pytest passed; ruff clean; prove_guards 14/14 bite; verify-oracle-on-stall
          34/34; verify-dispatch -Offline 51/51. verify-merge-protocol.ps1 was NOT run - the
          drill that rebased the live work line still has both contributing defects unfixed.
STATE:    coder lease released; little-coder re-focused on its prior project; nothing pushed.
REVERT:   `git branch -D work/u4close work/u4stall-probe` and delete .quadrant/ - the branch
          is not merged, and the evidence directories are gitignored.

## 2026-08-30 · U4 · class 2 — the quadrant's little-coder transport MIRRORS a workspace
DECISION: quadrant/lc_docker.py copies the run workspace into little-coder's container
          workspace, runs the task, and copies back every file that changed - the changed set
          computed by digesting the tree inside the container before and after.
WHY:      little-coder cannot be HANDED a workspace. Its API is unpublished (docker exec
          only), its workspace is a docker volume rather than a bind mount, and its focus path
          clones from a git-host URL only (urlnorm.py rejects local paths and file://). The
          alternative - a remote round-trip per run - would make every quadrant run depend on
          a push credential the local runner does not have (403, dfu-u4-findings F5).
WHY THE WHOLE CHANGED SET: copying back only the item's prefix would make
          scope.out_of_scope_hits read empty for every little-coder run by construction. A
          column that cannot register a violation reads as a measurement and is not one.
COST, recorded in every record's notes: the host workspace's .git is not carried across, so
          the mirror gets a fresh `git init` + baseline commit and the runner sees no history.
          That is also load-bearing - POST /tasks is 409 without <workspace>/.git/HEAD on disk.
REVERT:   set runners.little-coder.transport to "http", base_url to http://127.0.0.1:8090, and
          publish 127.0.0.1:8090:8090 in coder/docker-compose.yml. Both the adapter and
          dispatch.ps1 keep the http path executable.

## 2026-08-30 · U4 · class 2 — a cell that was BLOCKED and later RAN reads as COMPARED
DECISION: report._rows now lets an OUTCOME record outrank a non-outcome one, and among equals
          the most recent wins. It used to take the oldest admitted record.
WHY:      the two little-coder cells had `not_run` records from before the transport existed.
          Under the old rule they would have read "did not produce an outcome" beside a
          completed run in the same directory, and the only way to complete the comparison
          would have been to DELETE the blocked records - deleting evidence, which this module
          refuses everywhere else. The second half matters alone: two blocked records now
          report the LATER reason, so nobody fixes a door that stopped being the problem.
RED FIRST: test_a_later_real_run_outranks_an_earlier_blocked_record_for_the_same_cell and
          test_the_most_recent_blocking_reason_is_the_one_reported, both failing before the
          two-line change.
REVERT:   restore `first = admitted[0][0]` in quadrant/report.py and drop the two tests.

## 2026-08-30 · method · A LOCALE CODEC IS A SILENT CORRUPTOR, AND THEN A LOUD ONE
FINDING:  every subprocess in the quadrant package used text=True, which decodes with the
          LOCALE codec (cp1252 here) while everything it reads is UTF-8. The mild form shipped
          unnoticed - a model's `Unicode` examples recorded as `ÃœnicÃ¶dÃ©` in a transcript
          that was read and approved. The loud form was byte 0x9d: the reader thread raised
          inside subprocess, stdout became None, and the cell recorded
          `AttributeError: 'NoneType' object has no attribute 'rpartition'` - an error about
          the harness, in a table about runners, caused by the runner's choice of characters.
RULE:     one chokepoint (quadrant/proc.py), and the completeness proof is a SCAN of the
          package directory for `subprocess.run(`, not a list of files. DECISIONS.md's own
          corollary: a completeness test whose enumeration is hand-written is a list with a
          spell-checker.
REVERT:   delete quadrant/proc.py, restore `import subprocess` and the direct calls in
          adapters.py, cli.py, lc_docker.py, matrix.py, prove_guards.py, and drop the three
          tests.

## 2026-08-30 · U4 · class 3 (QUESTION for the operator, default taken)
QUESTION: the u4-stall item is deliberately unsatisfiable and lives in
          quadrant/items/u4-stall, beside the comparison items. Should stall probes live in a
          separate directory so nobody adds one to `quadrant.runners`' comparison by accident?
DEFAULT TAKEN: keep it beside u4-baseline. `quadrant.item` names u4-baseline explicitly, the
          probe is only reachable with `--item u4-stall`, and both its item.json `_why` and
          its test module's docstring say in the first paragraph that it must never be made
          passable. A second directory is more structure than the risk earns at n=1 item.
REVERT:   move the directory and add an items_dir argument to quadrant.cli.
```
