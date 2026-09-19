# U4 close — findings, evidence, and what is still not true

Session: `wt-u4close` (branch `work/u4close`), 2026-08-30.
Task: close `dark-factory-unification` PLAN §2's **U4** *Validated by* column, or argue that
it cannot be met as written and propose a narrowing amendment.

> **U4 Validated by:** *"**Gym:** same anchored item run per quadrant (runner × target),
> outcomes compared; stall → oracle observed firing at least once"*

and §2's preamble, four lines above the phase table, which binds the first word:

> *"'gym' means measured runs in `ai-orchestration-gym`, never live planes or a real target."*

**Verdict: BOTH HALVES ARE NOW MET, at venue `gym`.** Precisely: the `target: self` cells work
in detached worktrees OF THE ARENA; the `target: project` cells work in per-run `git init`
scratch repositories, which is what the target axis MEANS and which the preamble permits (a
scratch repo is neither a live plane nor a real target). The column names both targets, so it
cannot require every cell to be in the arena repo. §0.6 and §3 say which runs were which. No
amendment is proposed — the argument is in §7.

**READ §0.5 FIRST if you read anything.** The first version of this work met both halves and
ran them **against ai-stack**. Everything it measured was real; the place was wrong; and
nothing in the harness, the records, the report, the exit codes or this note could say so.
That was found by a verifier reading a path, not by a check. §0.5 is what changed.

**Three claims in the earlier version of this note were FALSE.** They are corrected in place
and listed here so nobody reconstructs them from a cached copy:

| earlier claim | what was true |
|---|---|
| "Verified end to end in `-Reuse` mode … one escalation, exit 0, probe branch deleted" | **No such run existed.** The only ledger row (22:33:04Z) was written by an ad-hoc `run-rounds.ps1`; the committed script ran at 22:40:43Z and appended nothing, because all six of its queue calls errored and none was checked. `work/u4stall-probe` was never deleted. §3, and the fix in commit `bfa71ce`. |
| "three real commits of three different implementations" | Rounds 1 and 3 were the **same implementation differing by one docstring word**. §3 disclosed it; the summary did not. Corrected in §3 and §0. |
| the two `target:self` cells' acceptance is reproducible from the retained evidence | It was not — only CHANGED files were kept, so the frozen file was missing. Fixed in `6c20c56`; that finding is also what U3's durable check is built from (§8). |

**Five more were FALSE and are corrected here (round 7, all found by verifiers, all
reproduced by execution before being fixed).** They are listed together because they share
one shape — *a mechanism was described by what it was for, not by what it did*:

| earlier claim | what execution showed |
|---|---|
| "admission refuses a record from another venue" (and `matrix.json`'s own `_why`) | It compared the venue **NAME**. Four records re-pointed at `D:/SomeOther/arena-clone`, still named `gym`, were admitted into the arena's own set: COMPARED 4/4, exit 0. §0.6. |
| the report states the venue the run happened in | It rendered **today's** venue object whenever the names agreed. `report --results-dir <the gym set> --repo "D:/Open WebUI/ai-stack"` printed *"Venue: `gym` — SATISFIES a Gym: column"* over ai-stack's path, COMPARED 4/4, exit 0, and wrote it to COMPARISON.md. §0.6. |
| "venue `gym` … satisfies a Gym: column" (preflight and report) | The implemented check is *"a repository root that is not the harness's own"*. `AI_STACK_GYM_REPO` pointed at a throwaway repo named `not-the-arena` printed that verdict at READY 4/4, exit 0. §2's preamble forbids "**a real target**", which no probe can decide. §0.6. |
| "3 of 3 seeds are caught ONLY by the banked check; 0 are also caught by the gate that already existed" (§8, U3) | The counterfactual followed each record's **absolute** `evidence.workspace` back to the untouched originals, so it never inspected the seeded copies. Re-measured on the copies: **2 of 3**, and seed C is caught by the pre-existing gate. §8. |
| "`check_quadrant_evidence_reproduces.py .quadrant/runs` → exit 1" (§4, §8) | Measured **exit 0** — "0 outcome record(s) re-derived; 4 skipped as inadmissible". It is structurally incapable of going red there. §4. |

---

## 0. The one-line answer, with the commands that produce it

```
$ python -m quadrant.cli preflight
harness repo : D:/Open WebUI/ai-stack/.claude/worktrees/wt-u4close
venue        : gym (kind gym) - DECLARED to satisfy a "Gym:" column by
               quadrant/schema.json; that is config, not a measurement
item repo    : D:/Open WebUI/ai-orchestration-gym @ main   (via config quadrant.venues.gym.repo)
identity     : root:f12ba2ecd0ed02c30ce3fa32e1dbe4b8ae7bf31d
NOT CHECKED  : whether this repository is a DISPOSABLE ARENA rather than a real target.
               No probe can decide that ...
  READY x4                                                     exit 0

$ python -m quadrant.cli report --results-dir .quadrant/gym-runs
**COMPARED 4/4**
**Venue: `gym` (kind `gym`) - DECLARED to satisfy a "Gym:" column** (+ an explicit
CHECKED list, an explicit NOT CHECKED list, and what the venue constrains per target)
`D:/Open WebUI/ai-orchestration-gym` @ `main`  - rendered from this set's PIN
| little-coder x self     | completed | 2/2 | 66.0 | 1/2/0 | 1 changed | mechanical |
| little-coder x project  | completed | 2/2 | 64.9 | 1/2/0 | 1 changed | mechanical |
| claude-code  x self     | completed | 2/2 | 27.5 | 1/2/0 | 1 changed | normative  |
| claude-code  x project  | completed | 2/2 | 28.2 | 1/2/0 | 1 changed | normative  |
                                                                       exit 0

$ ./scripts/agent-harness/observe-oracle-on-stall.ps1 -ResultsDir .quadrant/gym-stall -LeaseOwner wt-u4close
  three REAL dispatches of the unsatisfiable item (target: project - see below)
  round 3: STALLED - 2 consecutive rounds with no new information
  ORACLE-ON-STALL: little-coder/local-default -> claude-code/opus, hand back to little-coder
  ledger row e2ea67bcf2582272 - APPENDED BY THIS RUN (0 rows before it started, 1 after)
  probe branch deleted (verified)                              exit 0

$ python scripts/agent-harness/u3_evidence_regression_gym.py   # U3, same arena
  2 of 3 seeds caught ONLY by the banked check; 1 by the pre-existing gate;
  0 by neither                                                 exit 0
```

**WHERE EACH HALF ACTUALLY RAN, because "into the arena" is not true of both.** Half (a)'s
four cells are `target: self` and `target: project` against venue `gym`: the two `self`
cells are detached worktrees **of the arena**, and the two `project` cells are fresh
`git init` scratch repositories created per run. **All three of half (b)'s stall rounds ran
`target: project`** — scratch repos under `.quadrant/gym-stall/*/workspace` **inside this
ai-stack worktree**; the arena repository was never touched by the stall half, and its
`git status`/`worktree list` are unchanged by it. §3 said this correctly and the summary
above did not. That is legitimate under §2's preamble — a per-run `git init` scratch repo is
neither a live plane nor a real target — and the reasoning is now stated where a reader
meets it: the report renders it per target kind (`quadrant/schema.json`
`target_venue_binding`), and §0.6 records the decision.

The little-coder cells' dispatches are confirmed in the daemon's own task records, not only
in the harness's transcripts:

```
$ docker exec little-coder curl -sS http://localhost:8090/tasks/01M1AG441TK3K0YEJ5P2AXVFZW
{"task_id":"01M1AG441TK3K0YEJ5P2AXVFZW","status":"done","channel":"batch",
 "user_id":"quadrant-harness", ... "detail":"2 command(s)",
 "answer":"All 9 tests pass. `slugify` now: 1. Normalizes to NFKD and drops non-ASCII ..."}
$ ... /tasks/01M1AG6FG1YKB6NB9V6426ZFHN                       (the second cell, likewise)
```

---

## 0.5 THE VENUE — the column's first word, and the run that ignored it

**This is the most important finding in this note, and it is about this work.**

The first version of this session produced a complete, evidenced, exit-0 four-quadrant
comparison plus an observed oracle firing. Every mechanism the `quadrant` package owns was
satisfied: records built from the matrix, evidence-gated admission, one item proven by
digest, a pinned append-only declared matrix. Four cells, real dispatches, real acceptance
runs.

It ran against **ai-stack**. `target: self` resolved to the repository the harness lives in,
because `prepare_target` hardcoded `HEAD` and the only repo a hardcoded `HEAD` can name is
the caller's. `preflight` printed `item repo: D:\...\wt-u4close` and nobody read it as a
verdict. `orchestration-gym` appeared zero times in the diff. And
`harness.config.json`'s own restatement of U4's column began at *"same anchored item"* —
**the one clause naming the venue was the one clause missing from the config's copy of the
requirement.**

That is the package's own stated failure mode one layer up. Its four mechanisms all defend
against *a missing quadrant read as a present one*. None of them could represent a **missing
DIMENSION read as a satisfied one**, because the venue was in no record, no schema, no
report and no exit code — only in a path a human had to interpret.

**What now exists (commit `6c20c56`), mechanism 5:**

| | |
|---|---|
| a venue is data | `quadrant.venue` / `quadrant.venues` — a name, a KIND, a repo and a ref; kinds are declared in `quadrant/schema.json` with their rules |
| the gym check | `venue.probe` REFUSES a `gym`-kind venue that resolves to the harness's own repository, compared by **git common dir**, so a worktree of ai-stack is recognised as ai-stack rather than as "a different path" |
| the root check | it also refuses a venue path that is not a repository **ROOT** — see below |
| ordering | the venue is checked BEFORE the runner and the target, so a wrong-place cell is BLOCKED with that reason instead of running to completion and producing evidence |
| `target: self` | now a detached worktree of the VENUE's repo at the VENUE's ref. In the arena that is `main`, the gym's training arena per its own README |
| records | every record carries its venue; the results set pins it in `matrix.json` on first write and cannot move the pin; admission refuses a record from another venue — **which in this commit meant another NAME. See §0.6: it was corrected in round 7 to compare the REPOSITORY.** |
| the report | prints the venue and whether it satisfies a `"Gym:"` column — a reader never infers the place from a path again. **Two defects in this line, both fixed in round 7 (§0.6): it rendered TODAY's venue rather than the run's, and "SATISFIES" asserted a conclusion no probe derives.** |
| pre-venue records | carry no venue and are now REFUSED at admission with that reason. They were real runs in the wrong place; a refusal with a reason is the honest rendering, and deleting them would be worse |
| pinning is not labelling | a results set whose records name no venue does NOT take today's pin. Found by re-rendering the historical set after the mechanism landed: it was being stamped `gym` and headed *"SATISFIES a 'Gym:' column"* over eight records that ran in ai-stack. It now reads **Venue: UNSTATED**, `COMPARED 0/4`, every cell REFUSED — which is what that evidence is |

**The root check, and why it is not pedantry.** git discovers a repository by walking UP. A
wrong venue path therefore does not fail — it silently adopts whatever repository encloses
it. Measured while writing the tests for this module: **`C:/Users/<user>` is itself a git
repository on this machine**, so every path under the user's home — the system temp
directory included — answers `git rev-parse` with the *personal* repo. A mistyped arena path
under a home directory would have made the operator's personal repository the SUBJECT of an
experiment, which PLAN §C.2 puts in class 4. No amount of "it is a git repo" would have
caught it.

Six new mutations in `python -m quadrant.prove_guards` covered this and all bite; five more
arrived with §0.6, for 25/25 - including *delete the venue-violation refusal*, *delete the
repository-root refusal*, *delete the identity comparison*, *render today's venue instead of
the pin*, and *stop rewriting the lock when its `_why` has become false*.

**What is honestly still ai-stack, stated rather than glossed.** The oracle observation's
three round COMMITS are made in the harness repo with `git commit-tree`, on a stamped probe
branch that the script deletes and verifies deleted. That is evidence plumbing for the
detector's "did the code move" axis — the SUBJECT of the experiment (the workspace each
round's runner actually worked in) is a venue-`gym` quadrant workspace. Using little-coder as
a RUNNER is likewise fine; the preamble forbids a live plane or a real repo being the
subject, not being the substrate.

---

## 0.6 THE VENUE CHECK VALIDATED A LABEL, NOT THE THING (round 7)

§0.5 built the venue mechanism. A verifier then showed that three of its four public
sentences were about a **name**, not about a repository — the same class as the `thoughts`
mirror comment that claimed a label made `search_thoughts` enforce a boundary. All three
were reproduced by execution before anything was changed.

**1. Admission compared the NAME.** `record.py` asked `got != venue` where `venue` was a
string. Four records in `.quadrant/gym-runs` had their `venue.repo` edited to
`D:/SomeOther/arena-clone` with `venue.name` left as `gym`; the report over that set printed
**COMPARED 4/4, exit 0**. So `matrix.json`'s `_why` — *"record.admit refuses a record from
any other venue, so re-pointing --repo and re-running into this directory cannot mix two
experiments"* — was false as written: two different repositories under one venue name mixed
freely.

**2. The report rendered TODAY's venue, not the run's.** `cli._emit_report` chose between
the pin and the configuration by comparing names (`rv = v if pinned == v.name else …`), and
`--repo` does not change a name. Measured:

```
$ python -m quadrant.cli report --results-dir <the gym set> --repo "D:/Open WebUI/ai-stack"
**Venue: `gym` (kind `gym`) - SATISFIES a "Gym:" column**
`D:\Open WebUI\ai-stack` @ `main` (via --repo).            COMPARED 4/4    exit 0
```

over the **arena's own records**, and written to `COMPARISON.md`.

**3. The printed verdict claimed more than the check derived.** `venue.py`'s docstring said
plainly that it cannot decide a venue is "safe"; the printed line said *"satisfies a Gym:
column"*. §2's preamble forbids "never live planes or **a real target**"; the implemented
check is `!= the harness repo`. A throwaway repository proves the gap:

```
$ AI_STACK_GYM_REPO=<a repo created for the purpose, named `not-the-arena`> \
      python -m quadrant.cli preflight
venue : gym (kind gym, satisfies a "Gym:" column)            READY 4/4       exit 0
```

**What now exists (this commit), and how each sentence changed:**

| | |
|---|---|
| identity, not label | `venue.identity_of` records the repository's **root commit** reachable from the venue's ref. A name is config and a path is a filename; both can be edited into agreement. A root commit cannot — and a checkout that MOVED still matches, which is correct: it is the same repository. |
| admission | `record._venue_problems` compares identity where the record and the results set's pin both carry one; **refuses** a record with no identity against a pin that has one; and falls back to comparing **every label** (name, kind, repo path, ref) only for a set that predates identity — saying in the refusal that this is the weaker check. |
| the report | renders the **pinned** venue block from `matrix.json`, always. Passing `--repo` at report time now prints a warning naming the axis on which the pin and today's venue differ, and changes nothing in the artifact. |
| the verdict | split into two sentences: *"DECLARED to satisfy a Gym: column … a configuration assertion, not a measurement"*, then an explicit **CHECKED** list and an explicit **NOT CHECKED** list (`venue.what_was_checked`). The first NOT-CHECKED line is the one that matters: no probe can tell a disposable arena from a real target. `preflight` prints it too. |
| the lock's `_why` | `matrix.json` is now rewritten whenever it would DIFFER at all, not only when the cells or the venue move. This one is a consequence of the finding rather than part of it: the false sentence lived in a results set's own artifact, and a lock that only rewrites on a cell change would have carried it on disk after the code was fixed. `updated_utc` is excluded from the comparison so the timestamp keeps meaning "when this comparison last changed". Verified: re-reporting `.quadrant/gym-runs` refreshed its `_why` and left its venue pin untouched. |
| `target: project` | the report now states, per target kind, what the venue constrains: a `self` cell's workspace **is** a worktree of the venue repo; a `project` cell's is a **fresh `git init` scratch repo** created per run and NOT in the venue repo. Legitimate under the preamble, and previously left for the reader to infer from a venue heading printed over every row. |

Five new mutations in `python -m quadrant.prove_guards` cover these and all bite (25/25),
including *the report renders today's venue instead of the pin*, *the identity comparison is
removed*, and *the lock stops rewriting a `_why` that has become false*.

**The decision on `target: project` and the venue, stated rather than assumed.** A `project`
cell's subject is a repository created by the harness for that one run, holding only the
planted item, with a one-commit history, deleted with the results set. §2's preamble forbids
"live planes or a real target"; a scratch repo is neither, so a `project` cell is
**venue-independent by construction** and legitimate. What was wrong was not the cell — it
was printing a venue heading over it with nothing saying so. That is now rendered
(`quadrant/schema.json` `target_venue_binding`), which is why this note does not propose
narrowing the column.

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

### F5. The claude-code records were REUSED — and the arena re-run retired that debt

In the pre-venue comparison, `claude-code x self` and `claude-code x project` were records
`work/u4quad` had produced on 2026-08-30, copied into the results set unmodified. Re-running
them was declined as real external spend for no new information. The cost was stated at the
time: their `evidence.workspace` pointed into `.claude/worktrees/wt-u4quad`, so retiring that
worktree would have dropped the comparison to 2/4.

**The arena run made that moot by re-running them.** Moving venue is new information by
definition — a record from ai-stack is not a data point about a run in the gym, and admission
now refuses it for saying so. Both claude-code cells were dispatched fresh against the arena
($0.4242/1916 tokens and $0.4212/1786 tokens, reported by the runner), and every record in
`.quadrant/gym-runs` has its workspace beside it. Nothing in the 4/4 comparison depends on
another worktree existing.

The pre-venue records remain on disk and are refused at admission for naming no venue. That
is the honest rendering of a real run in the wrong place, and it is why they were not deleted.

### F6. What the comparison does and does not support

n=1 in every cell, and the report says so in every confidence column. It supports *"both
runners completed this item, in the arena, against the same anchored item"* and nothing about
which runner is better. The wall-clock numbers (66.0/64.9 local vs 27.5/28.2 cloud) are one
run each and are not a finding. `quadrant.repeats` is still 1.

The one comparative observation that is NOT about speed and does survive n=1: both runners
produced a 2/2 acceptance and a 1-file change with no out-of-scope hits, in BOTH target
environments — so on this item the target axis did not discriminate at all, and the runner
axis discriminated only on cost. A comparison whose axes do not separate is a real result
about the ITEM (it is too easy to discriminate) and should be read as a note about the
control rather than about the quadrants.

### F7. The evidence a `self` cell retains is now enough to re-check it

`finalize_target` kept only the CHANGED files, so `guards.py unmodified` could not be re-run
in a retained `self` workspace — the frozen file is unchanged by definition and was therefore
dropped. Found by a verifier; fixed in `6c20c56` by retaining the union of the changed files
and every path the plant manifest names. Verified in the arena run by re-running BOTH guards
in both retained `self` workspaces after the fact:

```
$ cd .quadrant/gym-runs/20260830T233043Z-little-coder-self/workspace
$ python .../quadrant/guards.py unmodified --item u4-baseline   ->  1 frozen file(s) unmodified   exit 0
$ python .../quadrant/guards.py tests      --item u4-baseline   ->  9/9 pristine test(s) passed   exit 0
   (and identically for 20260830T233239Z-claude-code-self)
```

That same finding is what U3's durable check is built from — see §8.

---

## 3. Half (b) — the oracle fired on a stall that HAPPENED

### What was real, item by item

- **The rounds were real dispatches.** Three separate runs of `quadrant.cli run --runner
  little-coder --target project --item u4-stall`, each a `POST /tasks` to the live daemon with
  its own task id in the daemon's journals, each answered by qwen36-27b through LiteLLM.
- **WHERE: `target: project`, so the arena was not the subject.** All three rounds worked in
  fresh `git init` scratch repositories at
  `.quadrant/gym-stall/<stamp>-little-coder-project/workspace`, **inside this ai-stack
  worktree**, each holding only the planted item on a one-commit history. The records carry
  `venue: gym` because the comparison was DRIVEN from that venue; nothing about these three
  rounds touched `D:/Open WebUI/ai-orchestration-gym`. §0's summary said "into the arena" and
  that was wrong; this is what happened. It satisfies §2's preamble — a per-run scratch repo
  is neither a live plane nor a real target — and §0.6 records the decision and where a
  reader now meets it.
- **The failures were real.** Each round's `-Reason`/`-Evidence` is the output the item's
  PRISTINE guards actually printed for that attempt, read out of that round's `record.json`
  by the script — not typed into it.
  Every round: `FAIL stall-item/test_normalize.py::test_trims_and_preserves_case:
  AssertionError:` / `2/3 pristine test(s) passed`.
- **The commits were real, and NOT three different implementations.** One commit per round,
  built from that round's ACTUAL artifact with git plumbing (`--no-filters`, so the runner's
  bytes are stored verbatim rather than line-ending-normalised). Rounds 1 and 2 differ in
  substance (`" ".join(text.split()).lower()` vs a `re.sub` version); **round 3 is round 1's
  implementation differing by one word in a docstring.** The head therefore MOVED each round
  — `moved: true` on all three, which is what the detector's second axis reads — but "three
  different implementations" would be a stronger sentence than the bytes support, and an
  earlier summary of this work used it. The detector's verdict does not depend on it: the
  stall is declared on the failure SIGNATURE repeating (`novel: false` on rounds 2 and 3)
  while the code moved, which is precisely the "cycle, not a step" shape.
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

**THAT SENTENCE USED TO CLAIM A RUN THAT NEVER HAPPENED, and the script it described could
not have made it.** The earlier version of this paragraph said the script was "verified end
to end in `-Reuse` mode ... one escalation, exit 0, probe branch deleted". A verifier
disproved every clause: the only ledger row (22:33:04Z) was written by the ad-hoc
`run-rounds.ps1`; the committed script ran at 22:40:43Z and appended NOTHING; and
`work/u4stall-probe` still pointed at `6a309a2`.

The script exited 0 anyway, because **all six of its queue calls were piped to `Out-Null`
with no return-code check** and its success test then read the ledger and found the row a
PREVIOUS run had left. In its DOCUMENTED default invocation. That is the eleventh
check-that-checks-nothing of this effort, and the pattern is always the same: the check ran,
the check was green, the check checked nothing.

Fixed in `bfa71ce`, structurally rather than at the line that lied:
* `Invoke-Queue` / `Invoke-GitOrDie` fail the run on any non-zero exit, naming the step
  (exit 3). There is no path from a failed step to a verdict.
* The verdict is derived from the ledger row **THIS run appended** — ids are snapshot before
  the rounds, and the success line prints the before/after counts and the new row's id. A
  pre-existing row is excluded by construction.
* Each run takes a UTC-stamped item id and probe branch; the deletion is VERIFIED (`git
  branch --list` must come back empty) and every refusal path removes the branch it created.

Proved by re-running it against the same populated state dir the defect was reproduced in:
RED (`REFUSED: queue -Propose failed`, exit 3, ledger unchanged) → GREEN (three rounds, one
escalation, exit 0, `ledger row bb82d46861008d91 - APPENDED BY THIS RUN (1 row before, 2
after)`) → RED again on a colliding id, leaving no branch behind. `work/u4stall-probe` and
two strays are now deleted.

Measured while fixing it, and now a comment in the file: **splatting an ARRAY to a PowerShell
SCRIPT binds POSITIONALLY** — `& $queue @("-Propose","-Id","x")` sets `$Id = "-Propose"` and
leaves the switch `$false`. The first version of the fix did exactly that, and the new
exit-code check caught it on the first run, which is the whole argument for the check.

The script excludes `error` records from the round set deliberately — a harness fault is not
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
| `python -m pytest -q` (scripts/agent-harness) | **260 passed** |
| `ruff check scripts/agent-harness scripts/checks` | All checks passed |
| `python -m quadrant.cli preflight` | venue `gym` → `ai-orchestration-gym @ main`; **4/4 READY** |
| `python -m quadrant.cli report --results-dir .quadrant/gym-runs` | **COMPARED 4/4**, venue `gym`, exit 0 |
| `python -m quadrant.prove_guards` | **25/25 guards proven to bite** (6 added with the venue, 5 more with the venue's IDENTITY, the pinned rendering and the lock's refreshable `_why`) |
| `observe-oracle-on-stall.ps1 -ResultsDir .quadrant/gym-stall` | **OBSERVED**, exit 0, ledger 0 → 1, probe branch deleted and verified |
| `observe-oracle-on-stall.ps1 -Reuse -Id <existing>` | **REFUSED**, exit 3 — the RED that replaces the false green |
| `u3_evidence_regression_gym.py` | **3/3 seeds caught only by the banked check**, exit 0 |
| `check_quadrant_evidence_reproduces.py --auto` | exit 0 — 7 records re-derived, 7 skipped as inadmissible (re-measured after the round-7 changes: unchanged) |
| `check_quadrant_evidence_reproduces.py .quadrant/runs` | **exit 0** — "0 outcome record(s) re-derived; 4 skipped as inadmissible". **The earlier claim of exit 1 here was FALSE, and structurally so:** `_admissible()` refuses every pre-venue record for naming no venue, as the check's own docstring says, so this invocation cannot go red. Its RED is proven instead by `test_evidence_reproduces.py` (11 tests, one per contract sentence, two of them the drill's own findings) and by the arena drill's seeds A and B. |
| `guards.py {unmodified,tests}` re-run in both gym `self` workspaces | `1 frozen file(s) unmodified` / `9/9 pristine test(s) passed`, exit 0 each |
| `docker exec little-coder curl /tasks/<id>` | both gym little-coder cells present in the daemon's own records, `status: done` |
| `verify-oracle-on-stall.ps1` | **34/34** (mechanism drill; a CONSTRUCTED stall, unchanged) |
| `verify-dispatch.ps1 -Offline` | **51/51** (real transport NOT covered — `-Offline`) |

Round 7 added these, each RED before the fix and GREEN after:

| command | before the fix | after |
|---|---|---|
| `report --results-dir <gym set copy with `venue.repo` re-pointed to `D:/SomeOther/arena-clone`>` | COMPARED **4/4**, exit 0 | COMPARED **0/4**, exit 1, each cell refused with the repository it actually names |
| `report --results-dir <gym set copy> --repo "D:/Open WebUI/ai-stack"` | *"Venue: `gym` — SATISFIES a Gym: column"* over `D:\Open WebUI\ai-stack`, exit 0 | renders the PIN (`ai-orchestration-gym`), prints a warning naming the axis that differs, ai-stack's path appears nowhere |
| `AI_STACK_GYM_REPO=<repo named `not-the-arena`> preflight` | *"satisfies a Gym: column"*, READY 4/4, exit 0 | still READY 4/4 exit 0 — the harness genuinely cannot decide this — but prints DECLARED-not-measured, the repository identity, and the NOT CHECKED line |
| `u3_evidence_regression_gym.py` | 3 of 3 caught only by the banked check | **2 of 3**; 1 caught by the pre-existing gate; 0 by neither |
| `python -m pytest` (scripts/agent-harness) | 248 passed | **260 passed** (+12: 10 in `test_quadrant_venue.py` for identity, the pinned rendering, the per-target note and the lock refresh; 2 in `test_evidence_reproduces.py` for `copy_run`) |
| `python -m quadrant.prove_guards` | 20/20 | **25/25** (5 new: the identity comparison, the identity-pinned refusal, the label fallback, the report rendering the pin, and the lock rewriting a `_why` that has become false) |

`verify-merge-protocol.ps1` was **NOT run**. DECISIONS.md 2026-08-30 records that drill
leaving the operator's checkout in a detached mid-rebase state, and its two proven
contributing defects (`Invoke-DrillGit` swallowing every git error; `git -C ""` silently
running in the current directory) are not fixed.

---

## 5. State left behind

- **Held**: nothing. The `coder` lease was acquired for every live run and RELEASED
  (`lease.ps1 -Status -Name coder` → `FREE`).
- **The arena is exactly as it was found.** No worktree remains (`git -C
  ai-orchestration-gym worktree list` shows only the harness checkout); the U3 sandbox and
  its parent `.gym-sandbox/` are removed on success; `git status` in the gym shows nothing
  this session added. Its pre-existing dirty state (two modified files, `.runlogs/`,
  `gym-0NN.*` logs) was not touched.
- **little-coder**: re-focused on its prior project (`https://github.com/anthropics/skills`)
  after every run. Restoring is best-effort but never silent — a failure is written into the
  record's `notes`.
- **Probe branches: none.** `work/u4stall-probe` (which the earlier note wrongly said was
  deleted) and two strays from refused runs are deleted; the script now deletes and VERIFIES
  its own, on every path.
- **Evidence on disk** (all gitignored): `.quadrant/gym-runs` (the 4/4 comparison),
  `.quadrant/gym-stall` (the three stall rounds plus its own scratch queue and ledger), and
  `.quadrant/runs` + `.quadrant/stall` from the pre-venue runs — kept, refused at admission
  for naming no venue, and reported as skipped by the durable check.
- **Nothing was pushed.** This branch exists only locally.

---

## 6. Findings that belong to something else (the overflow queue)

- **F6.1 — the detector's novelty axis is defeated by failure text that embeds a value.**
  Any pipeline feeding it pytest output (which rewrites assertions to include the actual
  value) sees a novel signature every round and never stalls. agent-org avoids this because
  its metric is a compiler-error COUNT. Owner: U3/U4 follow-up.
- **F6.2 — `little-coder` publishes no ports while `coder/docker-compose.yml` declares
  `127.0.0.1:9091:9090`.** Still unexplained. Measured again today.
- **F6.3 — the PRE-VENUE claude-code records depend on `wt-u4quad` existing.** Now moot for
  the comparison (those records are refused for naming no venue) but the worktree still
  holds their workspaces.
- **F6.4 — `verify-merge-protocol.ps1` is still the drill that rebased the live work line**,
  both contributing defects unfixed. Nobody owns it.
- **F6.5 — the durable-check registry lives in the SHARED git dir; the CHECKS live on
  branches.** `durable_checks.py . run` on this branch is 1/2: `check_stack_services_paths.py`,
  banked by `work/u3gym`, does not exist in this worktree, so it exits 2 and reads as a
  FAILING durable check in every other worktree of this repo until that branch merges. The
  registry is worktree-global by design (a check banked in one worktree must be visible in
  the next); the files it names are not. Owner: whoever lands `work/u3gym`. **Not fixed
  here** — a cross-branch registry policy is a decision, not a patch.
- **F6.6 — `%TEMP%` is inside a git repository on this machine.** `C:/Users/<user>` is itself
  a repo, so `git rev-parse` from any path under the home directory answers with the user's
  personal repository. The venue probe now refuses a non-root path for exactly this reason
  (§0.5), but anything else in this workspace that resolves a repo from a user-supplied path
  shares the hazard. Owner: U5 (personal-plane exclusion).
- **F6.7 — `evidence.workspace` is an absolute path written at run time.** Anything that
  reads it after evidence has been copied, archived or moved is reading the wrong tree.
  Fixed in the durable check; and the U3 drill, which is such a reader, is fixed by
  `copy_run` rewriting the sandbox copies' evidence paths into the copy (§8). **`record.admit`
  still resolves the path as written**, which is deliberate and is the remaining half: a
  record is a statement about a run, and admission checking whether the tree it names exists
  TODAY is a different question from whether the tree an auditor was handed is intact. That
  is why the drill relocates the paths rather than teaching admission to guess. Owner: U3/U4
  follow-up if a general "evidence is relative to its record" migration is ever wanted — it
  would touch every existing record, which is a decision, not a patch.
- **F6.8 — `record.admit` and the banked check cover different halves of the same question,
  and nothing states the pairing except this note.** Admission catches *the evidence is
  gone*; the check catches *the evidence is there and no longer yields the verdict*. Measured
  by the corrected counterfactual (§8). Neither file names the other as its complement, so a
  future reader can plausibly delete one believing the other covers it. Owner: U3/U4
  follow-up.

---

## 7. Why NO amendment to the U4 column is proposed

The brief allowed an amendment if a half is "genuinely unmeetable as written". Neither is —
including, decisively, the clause this work first ignored:

- **The venue clause was never unmeetable. It was unimplemented.** The harness already took
  a repo path; the same four cells, pointed at the arena, satisfy the column as written. The
  cost of meeting it was one config key and a probe — far less than the cost of amending the
  one clause that separates an experiment from a demonstration.
- **The runner axis was never blocked by the column.** It was blocked by ONE configuration
  key — a schema that demanded `endpoint` of a runner reached by `docker exec`.
- **The target axis is real for both runners**, and `self` reads correctly in the arena: the
  org works on the repository it is pointed at, which in a gym run is the gym's own. That
  reading is now enforced rather than assumed.
- **"Observed firing at least once" is satisfiable without an oracle round**, by the
  mechanism's own definition of firing.

Amending the column would have narrowed a requirement that was met the same day. The honest
failure here would have been to amend first and discover that second — and the earlier
version of this note came close, by declaring the column met while reading it without its
first word.

---

## 8. U3 discharged in the same arena run

`DECISIONS.md:484` records U3 as **CODE-COMPLETE, VALIDATION-PARKED** and routes its parked
seeded-regression gym run to U4's quadrants, because it is runner-level work. U3's column
also begins "Gym:". Closing U4 on non-gym runs would have stranded U3 silently; a verifier
flagged exactly that, and the earlier version of this branch never mentioned U3.

> **U3 Validated by (first half):** *"Gym: a seeded regression must be caught by a check born
> from a **tester** finding in a prior round (gym-007's shape, new source)"*

**The finding** — real, prior round, a tester and not the operator, which is the new source
U3 asks for. The U4 verification round of 2026-08-30, on this branch's own work:

> "For the two `target:self` cells the preserved workspace holds only CHANGED files, so
> `guards.py unmodified` now fails there (`test_slugify.py` MISSING) — those cells'
> acceptance is NOT reproducible from the retained evidence, though it passed when run."

It blocked nothing. Every gate was green, because `record.admit` asks whether
`evidence.workspace` EXISTS and it did. That is A5's evaporation shape exactly: a true
finding that costs nothing to ignore, on an item that is already passing.

**The check it banks** — `scripts/checks/check_quadrant_evidence_reproduces.py`, banked
content-addressed in the shared registry — is the general rule rather than the bug: *a record
of a check is not a check; if the artifacts a run kept cannot re-produce the verdict it
claims, the verdict is a self-report with a directory next to it.* It re-runs every
acceptance command in the retained workspace and requires the recorded exit code.

**The gym run** (`u3_evidence_regression_gym.py`), venue-gated — it refuses unless
`quadrant.venue` resolves to a gym-kind repo that is not this one — seeding into a sandbox
inside the arena checkout:

```
  seed                                                  pre-existing   this check
  -  PRISTINE copy (the control)                        n/a            green
  A  frozen file dropped from the retained workspace    missed         caught
  B  retained artifact edited after the verdict         missed         caught
  C  retained workspace removed entirely                CAUGHT         skipped
  2 of 3 caught ONLY by the banked check; 1 by the gate that already
  existed; 0 by neither.                                               exit 0
```

**THE COUNTERFACTUAL MEASURED A DIRECTORY IT DID NOT SEED, and the corrected number is
smaller.** Found by a verifier. `record.admit` resolves each record's **absolute**
`evidence.workspace`, so against a plain `copytree` sandbox it walked back to the untouched
originals in `.quadrant/gym-runs` and found them intact — for every seed, including seed C,
which this drill's own docstring says the pre-existing gate is *expected* to catch. "0 are
also caught by the gate that already existed" was therefore a measurement of a gate pointed
at a different directory. **Same class as this drill's FIRST counterfactual**, which proved
"nothing pre-existing catches either seed" by GREPPING and was disproved by a verifier
running a pre-existing script; the fix then was to execute the gate, and the fix now is to
execute it against the tree the seeds are in.

`copy_run` is the correction: a run directory is copied **and** its record's `evidence.*`
paths are rewritten into the copy, so the sandbox describes the tree in hand — an evidence
set as an auditor actually receives one. Originals are never touched, and only paths inside
the source run directory are relocated. Re-measured, seed C is **caught by the pre-existing
gate**, and the banked check **skips** it — correctly, and by its own documented rule: a
record admission refuses is in no comparison, so re-deriving its verdict would be re-deriving
a number nobody may use. The two gates are complementary: admission catches *the evidence is
gone*; the banked check catches *the evidence is there and no longer yields the verdict* —
which is the shape of the tester finding, and the shape nothing caught before.

What that costs, said rather than glossed: with the paths rewritten the drill no longer
demonstrates the check's robustness to a **stale** absolute path (record and sibling now
agree). That property keeps its own named regression tests in `test_evidence_reproduces.py`,
and `copy_run` itself now has two (proven RED by reverting it to a plain `copytree`).

The drill's pass condition changed with the measurement: it was "every seed goes red in the
banked check", which can only hold while the counterfactual is aimed elsewhere. It is now
"every seed is caught by at least one gate, and the report says which".

**PRECISELY WHICH PARTS ARE REAL** — the question that has to be answered plainly here:

| | |
|---|---|
| REAL | the finding: a prior round's verifier report, quoted rather than paraphrased |
| REAL | the check: it goes RED on seeded regressions of the shape the finding names (drill seeds A and B, in the arena) and on the named cases in `test_evidence_reproduces.py`; GREEN on the gym runs. **CORRECTED:** an earlier version of this row claimed it goes red "unprompted on the historical `.quadrant/runs` evidence, on the verifier's exact two cells". It does not and cannot — those four records are refused at admission for naming no venue, so the check skips them and exits 0 (measured, §4). |
| REAL | the bank: `durable_checks.add` into the shared git-dir registry, content-addressed |
| REAL | the seeded evidence: a COPY of actual gym-venue runs produced by real dispatches |
| REAL | the venue: the sandbox is inside the arena checkout, and the drill refuses outside a gym venue |
| REAL | the counterfactual: `record.admit` executed against every seed **in the seeded copies** — see the correction below |
| **NOT** | an agent-org / `gym_runner.py` scenario cycle. No worker built the regression and no PR was scored; the seeding is deterministic so the loop is RE-RUNNABLE rather than a transcript. The gym's runner drives the ORG through a scenario; this drives a CHECK through a regression, in the gym's arena. |
| **NOT** | a claim about any runner. The seeds are edits to retained evidence. |
| **NOT** | U3's second half. "Drills green in both systems" was already met (harness 66/66, agent-org 9/9) and this run does not re-establish it. |
| **THE NUMBER** | **2 of 3**, not 3 of 3. Seeds A and B (a frozen file dropped from the retained workspace; the retained artifact edited after the verdict) are caught ONLY by the banked check. Seed C (the retained workspace removed) is caught by `record.admit`, which already existed, and is skipped by the banked check as inadmissible. 0 of 3 are caught by neither. The larger number came from a counterfactual that inspected the untouched originals rather than the seeded copies; see the paragraph above. |

**The drill found two defects in its own check** — which is the drill earning its keep, and
the reason to run one rather than reason about it:

1. `evidence.workspace` is an ABSOLUTE path. The check followed it, read straight past the
   seeded sandbox into the untouched original, and reported everything reproducible. Seeds
   A and B were MISSED on the first run.
2. Falling back to that path when the sibling was missing then let seed C — a deleted
   workspace — resolve to the original and read as reproducible. MISSED on the second run.

Both have named regression tests in `test_evidence_reproduces.py` (9 tests, run anywhere in
about a second; the drill itself needs the arena and real run evidence).

---

## 9. Proposed plan changes

**This branch does not edit `PLAN.md`.** Its earlier version carried an A11 rewrite that
arrived with the `work/dfu-u4` merge; `work/u4bidir` (8 commits, on origin) has already
amended the SAME row in materially different words and records the harness half as PARKED
with the park asserted by a test. Two branches rewriting one row with neither acknowledging
the other is a guaranteed conflict, and a builder editing the row it is judged against is the
failure the anchor exists to prevent. **`PLAN.md` is UNTOUCHED BY THIS BRANCH** — the true
statement, and the one the three-dot diff shows:

```
$ git diff refactor/ai-stack-cleanup...HEAD \
      -- documentation/implementation-guide/dark-factory-unification/PLAN.md
(empty)
```

**Two earlier sentences here were wrong, and a verifier caught both.** "`git diff
refactor/ai-stack-cleanup -- PLAN.md` is empty" is a **vacuous pathspec** — the file is at
`documentation/implementation-guide/dark-factory-unification/PLAN.md`, so that command prints
empty whatever the tree contains, and proves nothing. And "byte-identical to the work line"
is **false**: the work line is AHEAD on that file (it carries §C.7's convergence criterion,
added 2026-08-30 in `c995a9b`), so the two-dot diff on the real path is not empty — it shows
this branch's older copy. Untouched-by-this-branch and identical-to-the-work-line are
different claims, and only the first is true. What follows is for the orchestrator to
adjudicate against u4bidir's version.

**P1 — §0's A11 row.** Both lines of work want to move it. What THIS work adds, as facts
rather than as proposed wording:
- the oracle-on-stall path is no longer untested: an escalation was resolved through
  `config.resolve_role` — the same profile mechanism — and fired for real, twice, once in
  the arena;
- `dispatch.ps1` still has **no consumer** in `queue.ps1` or anywhere else, so *"whether the
  profile GOVERNS anything"* remains true and must not be edited away;
- the local runner completed a real anchored item in **both** target environments, in the
  arena.

**P2 — §2's preamble deserves a pointer to its enforcement.** The preamble binds "gym" and
nothing pointed at a mechanism, which is how a four-cell comparison came to satisfy the
column's other clauses in the wrong place. A parenthetical naming `quadrant.venue` and
`quadrant/venue.py` would make the constraint findable from the sentence that states it.
*Suggested, not made.*

**P3 — §C.7 could name the venue as an audit input.** "The audit trail is the deliverable's
twin" is what makes an unattended run legible; an audit trail that does not say WHERE the
work was measured cannot be checked against a column that names a place. Every record and
report now carries it; the plan does not ask for it.
*Suggested, not made.*

**P4 — no change proposed to §2's U4 or U3 *Validated by* columns.** Both were met as
written, in the arena.

---

## DECISIONS entries to append

*(The orchestrator appends these. This branch does not touch DECISIONS.md.)*

```
## 2026-08-30 · U4 · CLOSED — both halves met at venue `gym`, after a VENUE VIOLATION
FINDING:  U4 was parked because the three pieces that satisfy its column lived on three
          unmerged branches (work/dfu-u4's dispatch, work/u4quad's comparison harness,
          work/u4oracle's stall detector). Merging them exposed one semantic clash
          (quadrant/schema.json demanded `endpoint` of a runner reached by `docker exec`),
          RED and stated in the merge commit before it was fixed.
THE DEFECT THAT MATTERED MORE: the first version of that work met both halves and ran them
          AGAINST ai-stack. `target: self` resolved to the repository the harness lives in,
          because prepare_target hardcoded HEAD and the only repo a hardcoded HEAD can name
          is the caller's. §2's preamble binds U4's first word - "'gym' means measured runs
          in ai-orchestration-gym, never live planes or a real target" - and
          harness.config.json's own restatement of the column began at "same anchored item",
          dropping the one clause that names the venue. Every mechanism the quadrant package
          owns was satisfied; none of them could represent a MISSING DIMENSION read as a
          satisfied one. Found by a verifier reading a path, not by a check.
FIXED (6c20c56), mechanism 5: a venue is data (quadrant.venue / quadrant.venues, kinds and
          their rules in quadrant/schema.json); venue.probe REFUSES a gym-kind venue that
          resolves to the harness's own repository, compared by GIT COMMON DIR so a worktree
          of ai-stack is recognised as ai-stack; it also refuses a venue path that is not a
          repository ROOT, because git discovers upward - measured: C:/Users/<user> is itself
          a git repo on this machine, so a mistyped arena path under the home directory would
          have made the operator's PERSONAL repository the subject of an experiment (§C.2
          class 4). The venue is checked BEFORE the runner and the target; every record
          carries it; the results set pins it in matrix.json and cannot move the pin;
          admission refuses a record from another venue; the report states whether the venue
          SATISFIES a "Gym:" column. Pre-venue records are refused for naming none - real
          runs in the wrong place, rendered as refusals rather than deleted.
HALF (a): python -m quadrant.cli report --results-dir .quadrant/gym-runs -> COMPARED 4/4,
          venue gym (D:/Open WebUI/ai-orchestration-gym @ main), exit 0. All four cells were
          run fresh against the arena. The two little-coder cells are REAL dispatches to the
          local model - task ids 01M1AG441TK3K0YEJ5P2AXVFZW and 01M1AG6FG1YKB6NB9V6426ZFHN,
          confirmed in the daemon's OWN task records (status done, channel batch,
          user_id quadrant-harness), not only in the harness's transcripts. Acceptance is the
          item's PRISTINE guards run host-side: 9/9 tests + 1 frozen file unmodified per cell.
          Both claude-code cells were re-dispatched too ($0.4242/1916 tok, $0.4212/1786 tok),
          which retired the earlier reuse of work/u4quad's records: nothing in the comparison
          now depends on another worktree existing.
HALF (b): the oracle fired on a stall that HAPPENED. quadrant/items/u4-stall
          is unsatisfiable by construction (two tests demand different outputs for one input);
          three real dispatches produced three real failing rounds with one failure signature,
          over three real commits of what the runner actually wrote, and queue.ps1 -Fail
          escalated on round 3: little-coder/local-default -> claude-code/opus, hand back to
          little-coder; rounds=3 stall=2/2 signatures_seen=1, three-entry trail, in the ledger.
          PRECISION: rounds 1 and 2 differ in substance; round 3 is round 1's implementation
          differing by one docstring word. The head MOVED each round, which is what the
          detector's second axis reads - but "three different implementations" is a stronger
          sentence than the bytes support and an earlier summary of this work used it.
          INDUCED, said plainly: the task was chosen to be impossible. A stall induced by an
          impossible task is a real stall; nothing wrote a stall state anywhere. The oracle's
          ROUND was not run - firing records an escalation rather than swapping the worker.
          WHERE, precisely: all three rounds ran `target: project` - fresh `git init` scratch
          repositories under .quadrant/gym-stall/*/workspace INSIDE the ai-stack worktree,
          each holding only the planted item on a one-commit history. The records name venue
          `gym` because the comparison was driven from it; the ARENA REPOSITORY WAS NOT
          TOUCHED by the stall half. That satisfies section 2's preamble - a per-run scratch
          repo is neither a live plane nor a real target - and the report now states it per
          target kind rather than leaving a venue heading to imply otherwise. An earlier
          summary of this work said "into the arena"; that was true of half (a)'s `self`
          cells only. The three round COMMITS are made in the harness repo on a stamped probe
          branch that the script deletes and verifies deleted; that is evidence plumbing for
          the "did the code move" axis.
NOT AMENDED: neither half was unmeetable, including the venue clause - it was unimplemented,
          not impossible. The harness already took a repo path; the same four cells pointed at
          the arena satisfy the column as written.
EVIDENCE: 260 pytest passed; ruff clean; prove_guards 25/25 bite (11 added by this branch, all
          venue/identity/evidence, including "delete the venue-violation refusal", "retain
          only the changed files", "delete the identity comparison" and "render today's venue
          instead of the results set's pin");
          verify-oracle-on-stall 34/34; verify-dispatch -Offline 51/51.
          verify-merge-protocol.ps1 was NOT run - the drill that rebased the live work line
          still has both contributing defects unfixed.
STATE:    coder lease released; little-coder re-focused on its prior project; the ARENA is
          exactly as found (no worktree, no sandbox, nothing added to its git status);
          no probe branches remain; nothing pushed.
REVERT:   `git branch -D work/u4close` and delete .quadrant/ - the branch is not merged and
          the evidence directories are gitignored. To revert the venue mechanism alone,
          delete quadrant/venue.py, the `venue`/`venues` keys and the schema's venue block;
          every consumer degrades to "Venue: UNSTATED" rather than breaking.

## 2026-08-30 · U3 · DISCHARGED — the parked seeded-regression gym run, in the same arena
FINDING:  DECISIONS.md's own "U3 · CORRECTION" entry routes U3's parked gym run to U4's
          quadrants. U3's column also begins "Gym:", so closing U4 on non-gym runs would have
          stranded U3 silently. A verifier flagged exactly that; the branch had never
          mentioned U3.
THE FINDING THE CHECK IS BORN FROM (a TESTER, prior round, not the operator - the new source
          U3's column asks for): the U4 verification round of 2026-08-30 reported that the two
          target:self cells' preserved workspace held only CHANGED files, so `guards.py
          unmodified` failed there (test_slugify.py MISSING) - acceptance that had genuinely
          passed was no longer reproducible from the retained evidence. It blocked nothing:
          record.admit asks whether evidence.workspace EXISTS, and it did. A5's evaporation
          shape exactly.
THE CHECK: scripts/checks/check_quadrant_evidence_reproduces.py, banked content-addressed in
          the SHARED git-dir registry, banking the general rule rather than the bug - A RECORD
          OF A CHECK IS NOT A CHECK: if the artifacts a run kept cannot re-produce the verdict
          it claims, the verdict is a self-report with a directory next to it. It re-runs every
          acceptance command in the retained workspace and requires the recorded exit code.
          It goes RED on seeded regressions of the shape the finding names and on the named
          cases in test_evidence_reproduces.py, and GREEN on the gym runs. CORRECTION: an
          earlier draft claimed it goes red "unprompted on the historical .quadrant/runs
          evidence". It cannot - those records are refused at admission for naming no venue,
          so the check skips them and exits 0 (measured).
THE GYM RUN: u3_evidence_regression_gym.py, venue-gated (it refuses unless quadrant.venue
          resolves to a gym-kind repo that is not this one) and seeding into a sandbox inside
          the ARENA checkout. Control green; seeds A (frozen file dropped), B (retained
          artifact edited after the verdict) and C (retained workspace removed) all caught,
          0 of 3 caught by neither gate. exit 0.
COUNTERFACTUAL EXECUTED AGAINST THE SEEDED COPIES, which is a CORRECTION and makes the number
          SMALLER: 2 of 3, not 3 of 3. record.admit follows each record's ABSOLUTE
          evidence.workspace, so against a plain copytree sandbox it inspected the untouched
          originals and reported every seed missed - including seed C, which the drill's own
          docstring says the pre-existing gate is expected to catch. Same class as this
          drill's FIRST counterfactual (proved by GREPPING, disproved by a verifier running a
          pre-existing script): running the real gate is not enough if it is pointed
          elsewhere. copy_run now rewrites a sandbox copy's evidence paths into the copy.
          Re-measured: seed C is CAUGHT by record.admit and SKIPPED by the banked check -
          correctly, since a record admission refuses is in no comparison. The two gates are
          complementary; the banked check's unique value is seeds A and B, which are the shape
          of the tester finding. The drill's pass condition changed with it: "every seed is
          caught by at least one gate, and the report says which".
THE DRILL FOUND TWO DEFECTS IN ITS OWN CHECK, which is the drill earning its keep:
          evidence.workspace is an ABSOLUTE path, so the check read past the seeded sandbox
          into the untouched original (seeds A and B MISSED, run 1); and the fallback to that
          path let a DELETED workspace resolve to the original (seed C MISSED, run 2). A
          record naming a workspace inside its own run directory is now checked THERE, and its
          absence is the finding. Both have named regression tests.
NOT CLAIMED: an agent-org / gym_runner.py scenario cycle - no worker built the regression and
          no PR was scored; the seeding is deterministic so the loop is re-runnable rather
          than a transcript. Nor U3's second half ("drills green in both systems"), which was
          already met (harness 66/66, agent-org 9/9) and is not re-established here.
REVERT:   delete scripts/checks/check_quadrant_evidence_reproduces.py,
          scripts/agent-harness/u3_evidence_regression_gym.py and test_evidence_reproduces.py,
          and remove the banked row from .git/agent-worktrees/durable-checks.json.

## 2026-08-30 · U4 · class 2 — THE VENUE CHECK VALIDATED A LABEL, NOT THE THING
FINDING:  the venue mechanism (6c20c56) fixed a missing dimension and then described itself
          by what it was for rather than by what it did. Three sentences, all reproduced by
          EXECUTION before anything was changed:
          (1) record.admit compared the venue NAME. Four records in .quadrant/gym-runs with
              `venue.repo` edited to D:/SomeOther/arena-clone and `venue.name` left as `gym`
              were admitted into the arena's own results set: COMPARED 4/4, exit 0. So
              matrix.json's own `_why` - "record.admit refuses a record from any other venue,
              so re-pointing --repo and re-running into this directory cannot mix two
              experiments" - was FALSE: two different repositories under one name mixed.
          (2) the report rendered TODAY's venue, not the run's. cli._emit_report chose between
              the pin and the configuration by comparing names, and --repo does not change a
              name: `report --results-dir <the gym set> --repo "D:/Open WebUI/ai-stack"`
              printed "Venue: `gym` - SATISFIES a Gym: column" over ai-stack's path, COMPARED
              4/4, exit 0, and wrote it to COMPARISON.md.
          (3) the printed verdict claimed more than the check derived. Section 2's preamble
              forbids "live planes or a real target"; the implemented check is "a repository
              ROOT that is not the harness's own". $AI_STACK_GYM_REPO pointed at a throwaway
              repository named `not-the-arena` printed "satisfies a Gym: column", READY 4/4,
              exit 0. venue.py's docstring said this plainly; the printed verdict did not, and
              the printed verdict is what is read.
          Same class as the `thoughts` mirror comment that claimed a label made
          search_thoughts enforce a boundary.
FIXED:    IDENTITY, NOT LABEL. venue.identity_of records the repository's root commit
          reachable from the venue's ref - a name is config and a path is a filename, and both
          can be edited into agreement; a root commit cannot, and a checkout that MOVED still
          matches, which is correct. record._venue_problems compares identity where the record
          and the pin both carry one; REFUSES a record with no identity against a pin that has
          one (otherwise deleting a field buys the weaker check); and compares EVERY label
          (name, kind, repo path, ref) only for a set predating identity, saying so in the
          refusal. The report renders the PINNED venue block always, and --repo at report time
          prints a warning naming the axis that differs and changes nothing. The verdict is
          split into DECLARED (config) + an explicit CHECKED list + an explicit NOT CHECKED
          list, whose first line is that no probe can tell a disposable arena from a real
          target; preflight prints it too. And the report states, per target kind, what the
          venue constrains - because a `target: project` cell's subject is a fresh `git init`
          scratch repo, not the venue repo.
DECIDED:  a `target: project` cell is VENUE-INDEPENDENT by construction and is LEGITIMATE
          under the preamble - a per-run scratch repository, one commit long, deleted with the
          results set, is neither a live plane nor a real target. What was wrong was printing
          a venue heading over it with nothing saying so. Now rendered
          (quadrant/schema.json target_venue_binding). No column amendment follows.
ALSO FIXED, because the fix alone would not have reached it: matrix.json is rewritten whenever
          it would DIFFER, not only when the declared cells or the venue move. The false `_why`
          lived in each results set's own artifact, so correcting the code would have left the
          sentence on disk. updated_utc is excluded from the comparison so it still means "when
          this comparison last changed". The venue PIN is still taken once and never moved -
          asserted by the same test.
EVIDENCE: all three cases RED before and GREEN after (commands and outputs in the note, 0.6
          and section 4); 259 pytest passed; ruff clean; prove_guards 24/24 with four new
          mutations - delete the identity comparison, delete the identity-pinned refusal,
          delete the label fallback, render today's venue instead of the pin.
REVERT:   drop `identity` from venue.Venue.as_record and record._venue_problems' identity
          branches (admission falls back to comparing every label), and restore `rv = v if
          pinned == v.name else (pinned or None)` in cli._emit_report. Existing results sets
          are unaffected either way: .quadrant/gym-runs predates identity and is compared by
          label in both directions.

## 2026-08-30 · U3 · class 2 — A COUNTERFACTUAL MUST INSPECT THE TREE IT SEEDED
FINDING:  u3_evidence_regression_gym.py executed the pre-existing gate (record.admit) rather
          than asserting what it would say - the correction work/u3gym paid for - and pointed
          it at the wrong directory. record.admit resolves each record's ABSOLUTE
          evidence.workspace, so against a plain copytree sandbox it walked back to the
          untouched originals in .quadrant/gym-runs and found them intact for EVERY seed,
          including seed C, which the drill's own docstring says the gate is expected to
          catch. "0 are also caught by the gate that already existed" was a measurement of a
          gate aimed elsewhere. Found by a verifier.
FIXED:    copy_run copies a run directory AND rewrites the copy's evidence.* paths into the
          copy, so the sandbox describes the tree in hand - an evidence set as an auditor
          receives one. Originals untouched; only paths inside the source run directory are
          relocated; the rewrite is disclosed in the copied record's notes.
RE-MEASURED, and the true number is SMALLER: 2 of 3 seeds are caught ONLY by the banked check
          (A: a frozen file dropped from the retained workspace; B: the retained artifact
          edited after the verdict). Seed C is CAUGHT by record.admit and SKIPPED by the
          banked check - correctly and by its own documented rule, since a record admission
          refuses is in no comparison. 0 of 3 are caught by neither. The two gates are
          complementary: admission catches "the evidence is gone", the banked check catches
          "the evidence is there and no longer yields the verdict" - the shape of the tester
          finding, and the shape nothing caught before.
ALSO:     the drill's pass condition was "every seed goes red in the banked check", which can
          only hold while the counterfactual is aimed elsewhere. It is now "every seed is
          caught by at least one gate, and the report says which".
COST, STATED: with the paths rewritten the drill no longer demonstrates the check's robustness
          to a STALE absolute path. That keeps its own named regression tests in
          test_evidence_reproduces.py, and copy_run now has two of its own (proven RED by
          reverting it to a plain copytree).
REVERT:   restore shutil.copytree in place of copy_run and the previous pass condition; the
          drill then reports 3 of 3 again, which is the number that was wrong.

## 2026-08-30 · method · A CHECK THAT PRINTS A VERDICT MUST DERIVE IT FROM WHAT IT DID
FINDING:  observe-oracle-on-stall.ps1, in -Reuse mode - its DOCUMENTED default invocation -
          exited 0 printing "OBSERVED: the oracle fired once on a REAL stall" while all six of
          its queue calls had errored. Every call was piped to Out-Null with no return-code
          check; the ledger was never appended to; and the success test read the ledger and
          found the row a PREVIOUS run had left. Reproduced by a verifier. The ELEVENTH
          check-that-checks-nothing of this effort.
RULE, and it generalises past this file: a verdict must be derived from state THIS RUN
          produced, never from ambient state that a previous run could also have produced.
          Two mechanisms, structural rather than a patch to the line that lied: (1) every
          external call goes through a wrapper that fails the run on a non-zero exit, naming
          the step - there is no path from a failed step to a verdict; (2) the ledger's row
          ids are snapshot BEFORE the rounds and the success line is built from the row that
          is NEW afterwards, printing the before/after counts and that row's id.
ALSO:     each run takes a stamped item id and probe branch (the documented invocation used to
          collide with itself); the branch deletion is VERIFIED rather than assumed; and every
          refusal path removes the branch it created.
MEASURED WHILE FIXING IT: splatting an ARRAY to a PowerShell SCRIPT binds POSITIONALLY -
          `& $script @("-Propose","-Id","x")` sets $Id = "-Propose" and leaves the switch
          $false. The first version of the fix did exactly that and the new exit-code check
          caught it on the first run, which is the argument for the check in one sentence.
RED -> GREEN, both against the state dir the defect was reproduced in: -Reuse with the
          colliding id -> REFUSED, exit 3, ledger unchanged; -Reuse with the default ->
          three rounds, one escalation, exit 0, "ledger row bb82d46861008d91 - APPENDED BY
          THIS RUN (1 row before it started, 2 after)", probe branch deleted and verified.
CORRECTION TO THE RECORD: the earlier report claimed a verified end-to-end -Reuse run with
          the probe branch deleted. No such run existed and work/u4stall-probe still pointed
          at 6a309a2. It is deleted now, along with two strays.
REVERT:   restore the Out-Null calls and the ledger-wide success test in
          scripts/agent-harness/observe-oracle-on-stall.ps1.

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
