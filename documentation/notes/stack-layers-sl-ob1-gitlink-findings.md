# Findings — `sl-ob1-gitlink` (bump the OB1 gitlink to the profiles commit), 2026-09-20

Sink named by the anchor at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-gitlink.json`.
Every number below was produced by running the command named, in the worktree
`.claude/worktrees/wt-sl-ob1-gitlink`, with `OB1` checked out at `fe3e045`.
No container was started, stopped or recreated; every `docker` call used here is
a render (`config`) or a label query (`ps`, `docker ps`).

---

## 1. The bump, and the reachability proof

```
$ git -C OB1 fetch origin
$ git -C OB1 rev-parse origin/feature/integrated-knowledge-system
fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19
$ git -C OB1 merge-base --is-ancestor fe3e045 origin/feature/integrated-knowledge-system
$ echo $?
0
$ git -C OB1 checkout fe3e045
HEAD is now at fe3e045 docker: gate research, wiki and notebook behind compose profiles
```

Gitlink before: `5005197dd3ae85a2a4aba8bf142084cac2824759`.
Gitlink after:  `fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19`.
The remote branch TIP is that commit, so `--is-ancestor` is trivially true; it is
quoted anyway because "reachable" is the property the rule is about, not "is the
tip" — a later OB1 push would move the tip and must not invalidate this pin.

## 2. Render counts at `fe3e045` — the whole point of the bump

`docker compose -f OB1/docker/docker-compose.yml [flags] config --services | wc -l`,
stderr empty on every one:

| Render | Services | Delta |
|---|---|---|
| bare | **20** | — |
| `--profile idea-refinery` | 21 | +1 `openbrain-idea-refinery` |
| `--profile research` | 22 | +2 `openbrain-curator`, `openbrain-research` |
| `--profile notebook` | 23 | +3 `surrealdb`, `open_notebook`, `open-notebook-backup` |
| `--profile wiki` | 24 | +4 `openbrain-wiki`, `-wiki-backup`, `-wiki-viewer`, `-workbench` |
| `idea-refinery` + `research` (**the driver's default set**) | **23** | the three above them |
| all four | **30** | the ten above |

The all-four set is IDENTICAL to what is running: the 30 names from
`docker ps -a --filter label=com.docker.compose.project=open-brain`, sorted, diff
clean against the sorted render.

Each profile's membership matches the manifest description it carries, so no
`[planes.ob1.profiles.*]` description needed a content change — only the notes
around them that said the profiles were not yet in the pinned gitlink.

**Before the bump these four flags were interchangeable with one** (compose
ignores a profile it does not know), which is why sl-ob1-profiles could land
safely and why several documents said "two profiles and four render the same 30".
That is now false in every file that said it.

## 3. BOTH declaration sites work, and they do not fight — measured, not assumed

The anchor left this open ("unless the developer measures that OB1's compose reads
COMPOSE_PROFILES from its own env file"). It does.

- **`COMPOSE_PROFILES` in `OB1/docker/.env`.** A scratch copy of that file with
  `COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` appended, passed as
  `--env-file`, renders **30** — byte-identical service list to the four-flag
  render (`diff` clean). So the plane's own env file is a valid declaration site,
  the same as every other plane since sl-env-split (D17).
- **The driver's state.** `stack.py --state <scratch> init --product research --force`
  prints `ob1  profiles: idea-refinery, research, wiki, notebook`, writes exactly
  those four to the state JSON, and `up --all --dry-run` then emits:
  `docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d`.
- **They compose.** With all four in the WORKTREE's `OB1/docker/.env` and NO ob1
  entry in the state file, `stack.py up ob1 --dry-run` printed all four `--profile`
  flags — `effective_profiles()` unions the plane env's list into whatever the
  driver resolves. (That gitignored file was edited in the worktree only, then
  restored and confirmed with `md5sum -c`. The live host's copy was not touched.)
- **A CLI `--profile` REPLACES `COMPOSE_PROFILES`, confirmed here rather than
  inherited:** with all four in the env file, `--profile research` alone renders
  **22**, not 30. This is why `effective_profiles()` has to union, and why any
  script that passes ONE flag cannot be rescued by the operator's env file.

## 4. What the driver writes, and the gap the bump opens

| Invocation | State written for `ob1` | Flags at drive time | Services |
|---|---|---|---|
| `init --product research --force` | `idea-refinery, research, wiki, notebook` | all four | 30 |
| `init --product research --headless --force` | `idea-refinery, research` | two | **23** |
| `enable ob1` (bare plane) | `idea-refinery, research` | two | **23** |
| no state entry for `ob1` at all | — | `idea-refinery, research` | **23** |

Every count in that column is a RENDER of the flag list beside it, not 20 plus the
deltas in §2. Attempt 1 wrote 22 there — the count for `--profile research` ALONE —
and it propagated to eight shipped files before a tester rendered the pair. See §11.

> **CORRECTION, 2026-09-20 (`sl-docs-posture`): the LANDING STEP is `enable
> research`, not `init --product research --force`.** Every `init … --force` in
> this note is a MEASUREMENT against a `--state <scratch>` file, and stays as
> written because that is what was run. It is not advice for this host, and §3's
> phrasing ("one command either way") read as though it were. On a host that
> already has a `.stack/state.json` the two verbs differ: `cmd_init` builds a
> fresh `State({})` and saves it (REPLACE), `cmd_enable` mutates the state it
> loaded (MERGE). Measured 2026-09-20 against two scratch state files seeded
> with the same six planes (`frontend, inference, memory, search, coder,
> agent-org`): `init --product research --force` left **four**
> (`frontend, inference, ob1, search`) — `memory`, `coder` and `agent-org` gone —
> while `enable research` left **seven**, `ob1` carrying the same four profiles.
> Both print an identical `enabled product research:` summary, because `cmd_init`
> calls `cmd_enable` to do the work. Read 2026-09-20, this host's
> `.stack/state.json` now lists SEVEN planes - `anchor, coder, frontend,
> inference, memory, ob1, search`, i.e. the landing step above has been taken -
> so the wrong verb here would silently un-enable `coder`, `memory` and the
> explicit `anchor` entry. Semantics: `scripts/stack/README.md`, the `init`
> section.

`enable ob1` refuses first until `inference` and `search` are enabled, naming both
and the two commands — worth knowing before reading the refusal as a bug.

**This host is in the last row.** `.stack/state.json` exists in the main checkout
(written 2026-09-19) and lists `anchor, coder, frontend, inference, memory, search`
— **not `ob1`**. `OB1/docker/.env` has no `COMPOSE_PROFILES` line. So after this
branch lands, a `stack.py up` for OB1 passes two profiles and would start 23 of the
30 that are running: the `wiki` four and the `notebook` three would not come up.
Closing that is the ORCHESTRATOR's landing step, not this item's, and it is one
command either way (§3).

Note for anyone reading older notes: several files said "today's host has no
`.stack/state.json`". It has had one since 2026-09-19 18:10. The conclusion those
notes drew is unchanged (the driver resolves ob1's profiles from the manifest alone
either way, because that file does not list `ob1`), but the premise was stale and is
corrected in `stack.manifest.toml` here.

## 5. SCOPE TAKEN ON: `emergency-recovery.ps1` would have started 21 of the 30

`Start-OB1Stack` logs `Starting Open Brain (OB1) stack (30 containers)` — the count
comes from `$Script:OB1Services`, which lists 30 — and then ran:

```powershell
docker compose -f $Script:OB1Compose --profile idea-refinery up -d
```

Against `5005197` that started all 30, because compose ignored the three profiles
it did not know and `idea-refinery` was the only real one. **Against `fe3e045` the
same line renders 21.** `Reset-OB1Stack` (the `nuclear` path) carried the same flag
on both halves of its `down`/`up` pair.

That is the item's own goal failing — "nothing that is up goes down by accident" —
in the one script whose entire job is to put the stack back. It is also a surface
CLAUDE.md's container rule names explicitly as co-moving with a compose change, and
a gitlink bump IS a compose change to the deployed OB1. So it is fixed here, to all
four profiles at all three sites, with the reasoning at the line. This RESTORES the
script's pre-bump behaviour (30 containers); it is not a deployment change.

The operator's `OB1/docker/.env` cannot rescue a line that passes ONE flag even if
they set `COMPOSE_PROFILES` — §3's last bullet measured why. (It DOES rescue a line
that passes none; §5a.)

**Checked and NOT changed** (each verified, not assumed):

- `scripts/checks/stack-watchdog.ps1:872,878` run `up -d surrealdb` and
  `up -d open_notebook`, both now profiled. Naming a service activates that
  service's own profile; the failure mode SERVICE-LIFECYCLE row 8a documents is a
  service that REFERENCES another profile's service, and compose then refuses to
  load the project at all. Measured at `fe3e045`: `docker compose -f … config
  surrealdb`, `… config open_notebook`, `… config openbrain-wiki` and
  `… config openbrain-research` all exit 0. Those repair paths still resolve.
- `docker compose … ps` is a LABEL query, not a render: bare `ps` lists all 30
  running containers at `fe3e045` exactly as it did at `5005197` (measured both).
  So the status/report sites in `emergency-recovery.ps1` (lines ~521, ~934, ~1048)
  need no profiles and were left alone.
- ~~The bare `stop` and bare `down` were left alone: whether a profile-less `down`
  reaches profiled containers cannot be measured without stopping them.~~
  **THAT SENTENCE WAS WRONG, AND IT WAS THE EXPENSIVE KIND OF WRONG — see §11.**
  It is measurable in two minutes in a throwaway compose project, touching nothing
  real. Both sites are fixed in attempt 2; the measurement is below.

## 5a. `stop` and `down` miss profiled containers too — measured, in a throwaway project

Attempt 1 fixed three of five mutating OB1 call sites in
`scripts/recovery/emergency-recovery.ps1` and excused the other two with a claim that
could not survive a two-minute experiment (§11). The experiment:

A throwaway compose project, `name: glx-profile-probe`, two `busybox` services, one
behind `profiles: ["extra"]`. No image build, no network of any real project, and no
container of any real project touched. Compose **v5.3.0**, the version this host runs.

```
$ docker compose --profile extra up -d      -> glx-probe-core running, glx-probe-gated running

$ docker compose stop                       # BARE
   Container glx-probe-core Stopping / Stopped
   docker ps -a -> glx-probe-core exited, glx-probe-gated RUNNING

$ docker compose --profile extra stop       # PROFILED - the fix's shape
   Container glx-probe-gated Stopping / Stopped
   Container glx-probe-core  Stopping / Stopped
   docker ps -a -> both exited

$ docker compose down                       # BARE, both running first
   Container glx-probe-core Stopping / Stopped / Removing / Removed
   Network glx-profile-probe_default Removing
   Network glx-profile-probe_default Resource is still in use      <-- THE POINT
   docker ps -a -> glx-probe-gated STILL RUNNING; the network still exists

$ docker compose --profile extra down       # PROFILED - the fix's shape
   Container glx-probe-gated Stopping / Stopped / Removing / Removed
   Network glx-profile-probe_default Removed
   -> 0 containers, 0 networks

$ printf 'COMPOSE_PROFILES=extra\n' > .env  # the OTHER fix, (b)
$ docker compose up -d ; docker compose stop   # BARE
   -> BOTH stop
$ docker compose up -d ; docker compose down   # BARE
   -> both removed, `Network ... Removed`, 0 containers, 0 networks
```

Probe torn down with `--profile extra down -v` and the directory deleted;
`docker ps -a` for that project is empty.

**So the bare `down` does not merely miss containers — it cannot drop the network.**
Applied to OB1 at `fe3e045`: `Stop-OB1Stack` addressed 20 of 30, and
`Invoke-NuclearRecovery`'s OB1 teardown removed 20 and left ten running. Both
functions carry a comment saying they run FIRST so the root `docker compose down` can
drop `ai-stack_llm-net`. Seven of the ten gated containers hold endpoints on
`ai-stack_llm-net` / `app-net` / `default` (the tester enumerated this with
`docker inspect`; it is why those ten matter and not just that they are ten), so the
drop those functions exist to enable would have failed exactly as the probe's did.

**Honest history** (the tester's class-3 note, kept): at `5005197`
`openbrain-idea-refinery` was already profiled, so the bare `stop` already missed
**one** container. This branch takes that from 1 to 10, seven of them on the anchor
networks. An amplification of an existing bug, not a new class — and the amplification
is what makes it a regression this branch causes.

### The fix: ONE list, eight call sites

`$Script:OB1Profiles` is declared beside `$Script:OB1Compose` and used by **every**
`docker compose -f $Script:OB1Compose ...` in the file — there is no second argument
list for a ninth site to drift from:

| Function | Verb | Was |
|---|---|---|
| `Stop-OB1Stack` | `stop` | **bare** |
| `Start-OB1Stack` | `up -d` | four literal flags (attempt 1) |
| `Reset-OB1Stack` | `down` | four literal flags (attempt 1) |
| `Reset-OB1Stack` | `up -d` | four literal flags (attempt 1) |
| `Invoke-NuclearRecovery` | `down` | **bare** |
| `Test-*` health path | `ps --format json` | bare |
| two report paths | `ps --format "table ..."` | bare |

The three `ps` sites do **not** need the flags — `ps` is a label query and bare and
profiled both report all 30 (measured at `fe3e045`: `ps --format json` yields 30 lines
either way). They carry them anyway so the rule in that file is "every OB1 compose
invocation uses `$Script:OB1Profiles`", with no per-site judgement left for the next
person to get wrong. That is the whole reason attempt 1 failed here: it made a
per-site judgement, on five sites, and got two of them wrong.

Splatting (`$prof = $Script:OB1Profiles; docker compose -f $C @prof stop`) was verified
to pass the flags as separate arguments under PS 5.1 — `@prof config --services` with
a two-profile list rendered 26 (20 + 2 + 4), which is only possible if both flags
arrived.

### And the env-file half, which is the operator's

`OB1/docker/.env` has no `COMPOSE_PROFILES` line, while `frontend/.env` carries
`gpu,tailscale` and `inference/.env` carries `local` (read 2026-09-20). Of the five
profiled planes, `portal` deliberately has none (CLAUDE.md says so) and `agent-org`'s
`workers`/`cloud` are operator-driven slices; OB1 is the one whose absence now bites,
because its bare verbs are in the recovery path. Adding
`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` there repairs every bare verb
at once — proved in the probe above — and is now named as the landing step in
SERVICE-LIFECYCLE row 8a and the stack-map OB1 section alongside the driver state.

**The price, stated rather than discovered later.** `effective_profiles()` unions the
plane env's list into the driver's flags, so on a host whose `OB1/docker/.env` carries
all four, `--headless` becomes a NO-OP for this plane. Measured — and the transcript is
almost comic:

```
$ stack.py init --product research --headless --force
  # --headless: dropped surface profiles ob1:wiki, ob1:notebook
  ob1  profiles: idea-refinery, research
$ stack.py up ob1 --dry-run
  docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d
```

The driver says it dropped them and then passes them. On this host, which runs all 30,
that is the right trade. A deployment that genuinely wants a headless OB1 must leave
the env line out and use the driver state alone. Both docs now say this.

## 6. SCOPE TAKEN ON: a gitlink bump staged NO `.yml`, so the compose check sat out

`scripts/checks/check-project-configs.ps1` gates its ENTIRE compose half — the nine
`config -q` renders AND the inventory coverage assertion that sl-ob1-profiles built
— on `$staged -match '\.(yml|yaml)$'`.

A submodule gitlink bump replaces `OB1/docker/docker-compose.yml` and the scheduled
file it includes, wholesale, and stages exactly one path: `OB1`. So on this item's
own delta the check printed, in full:

```
  [configs]   [ ~~ ] mutually exclusive - openwebui: ...
  [configs]   [OK]   scripts/lib/stack-services.json matches ...
  [configs] 3 staged .ps1 file(s) parse clean
  [configs] 2 staged .json file(s) are strict-valid
```

(That transcript is from the tip, not from the moment the hole was found: the run that
found it printed "2 staged .ps1", before `emergency-recovery.ps1` joined the delta.
Corrected because a quoted transcript that does not reproduce is worse than a
paraphrase — the tester caught the drift as class 3.)

No renders. No `rows verified/expected`. Green, on the one commit shape that most
needed the render — the commit that took OB1's bare render from 29 services to 20.
This is the third instance of the same class in this file's history (§5b and §5d of
`stack-layers-sl-ob1-profiles-findings.md` are the other two): **a check that stops
covering something, silently, is worse than one that fails.**

Fixed by adding a staged `OB1` entry to the trigger. Same delta, after:

```
  [configs] all 9 compose projects render clean
  [configs] NOT VERIFIED: project 'agent-org' has 16 inventory row(s) and no render target
  [configs] stack-services.json inventory matches the compose configs
            [rows verified/expected: inference:8/8 frontend:4/4 memory:3/3 search:4/4 coder:4/4 open-brain:30/30]
```

The red/green pair above IS the proof the gate is load-bearing: identical staged
delta, the only difference being the two-line change to the trigger.

Note the `NOT VERIFIED: project 'agent-org'` line, printed on every run since
sl-ob1-profiles §5d: agent-org's 16 rows still have no render target. Pre-existing,
still another item's surface, and now visible on every gitlink bump too.

## 7. What `inventory --check` did, and why the JSON barely moved

`inventory --check` already exits 0 at `fe3e045` with **no** `[ ~~ ] declared, not
rendered` rows — the mechanism sl-ob1-profiles built does exactly what its docstring
promised: `unpinned_profiles()` returns the empty set the moment the render carries
the profiles, and the ten rows move from "accepted declaration" to "derived from the
render" with no change to their content.

So `inventory --write` produced **no change to any service row**. The only diff in
`scripts/lib/stack-services.json` is the `_comment` block, which is copied from
`scripts/lib/stack-services.curated.json` and said "OB1's research / wiki / notebook
are exactly that today (gitlink 5005197 declares only idea-refinery)". That sentence
is now false and is rewritten in the SIDECAR (the generated file is not hand-edited).

**That is worth stating plainly, because the acceptance criterion could be misread**:
"rows carry their profile and are `rendered` rather than `declared, not rendered`" is
a statement about how the value was DERIVED, not about a field in the file. There is
no `rendered` field. The observable difference is the absence of the `[ ~~ ]` lines
and of the follow-up paragraph `cmd_inventory` prints after them.

## 8. Every stale sentence this bump created, and where it was

Found by `grep -rn 5005197` and `grep -rn "declared, not rendered"` across the repo,
excluding `OB1/` and the historical records in `documentation/evidence/` and
`documentation/notes/` (those are dated accounts of what was true then; rewriting
them would be falsifying a record):

| File | Was | Now |
|---|---|---|
| `CLAUDE.md` OB1 row | "bare render gives 29 and `--profile idea-refinery` gives 30" | 20 bare / 30 with all four, + where the operator declares them |
| `stack.manifest.toml` `pending` flag doc | "see the ob1 tables" as the live example | no plane is in that state today |
| `stack.manifest.toml` above the three profile tables | "AFTER THE GITLINK BUMPS that stops being true" | the measured post-bump numbers |
| `stack.manifest.toml` `idea-refinery` description | "scripts/stack/stack.ps1 passes this" | `default = true`, so both drivers do (stack.ps1 is a shim) |
| `scripts/stack/stack.ps1` header | "gitlink 5005197 … two profiles and four render the same 30" | bare 20 / two 23 / four 30, and the one-time operator step |
| `scripts/stack/stack.py` `unpinned_profiles` docstring | "how research/wiki/notebook are described today" | how they WERE; empty for every plane now |
| `scripts/stack/README.md` (two places) | "not yet in the pinned submodule" | landed; the mechanism has no current user |
| `scripts/stack/test_stack.py` (two comments) | "changes NOTHING about what starts today" | it does now; the test still asserts the DEFAULT closure |
| `scripts/lib/stack-services.curated.json` `_comment` | "gitlink 5005197 declares only idea-refinery" | bumped; all ten rows derived again |
| `documentation/runbooks/SERVICE-LIFECYCLE.md` row 8a + the profile-gating section | "only the last is in the PINNED gitlink" | all four, + the D17 declaration sites |
| `.claude/skills/stack-map/references/workspace-stacks.md` §2 profiles block | "29 of the 30", "[declared, not rendered] until the gitlink bumps" | the full measured table + both declaration sites |
| `.claude/skills/stack-map/references/workspace-stacks.md` §2 Volumes | "Four, at the pinned gitlink `5005197`" | four, at `fe3e045` — verified unchanged by the bump |

The volumes line was re-measured rather than just re-dated: the four-profile render
at `fe3e045` declares exactly `openbrain-db-data`, `openbrain-wiki-data`,
`wiki-assets`, `wiki-viewer-srv`.

`documentation/evidence/sl-readmes/test-plan.md` and `.../sl-readmes2/test-plan.md`
contain assertions ("CLAUDE.md OB1 row: 29 bare and 30 with idea-refinery, both
against pin 5005197"; "at pin 5005197 only idea-refinery exists") that this bump
falsifies. They are TEST PLANS OF MERGED ITEMS — a record of what was checked on a
given day — and are left alone deliberately. A tester re-running them today should
expect those two rows to fail, and should read this note rather than the plan.

## 9. Out of scope, found anyway

- **`OB1/docker/README.md` is inside the submodule.** It documents the profiles and
  is correct at `fe3e045` (sl-ob1-profiles wrote it there). Editing it would be an
  OB1 change made through the parent, which the anchor forbids. Untouched.
- **`scripts/checks/plan-store.ps1` still cannot run from a worktree** — it resolves
  the plan store as a sibling of the repo ROOT, and a worktree's root is
  `…\.claude\worktrees\wt-<id>`. Reported by sl-ob1-profiles §10 on 2026-09-19 and
  still true on 2026-09-20; re-confirmed here only to record that a second item hit
  the same wall.
- **`stack-layers` still has no row in `documentation/implementation-guide/README.md`.**
  Also reported by sl-ob1-profiles (§11) and also not fixed here, for the reason given
  there: writing that row means asserting the whole feature's state, and this item
  touched one slice of it.
- **`ruff check .` is GREEN on this base.** sl-ob1-profiles §8 reported a pre-existing
  `E501` in `llm-queue/src/llm_queue/__init__.py`; it is gone at `f9b18f2`. Recorded
  so a reviewer who read that note does not go looking for it.
- **`[planes.inference.profiles.local]` is no longer `pending`** (sl-ob1-profiles §12
  carried it as open). At `f9b18f2` the manifest says "NOT `pending`: sl-inference-split
  landed it" and `check-project-configs.ps1` passes `--profile local`. Closed by
  someone else; recorded so it is not carried forward a third time.

## 10. What a fresh clone cannot do, and why none of it is this item's fault

The anchor's verification runs in a short-path scratch clone
(`git -c core.longpaths=true clone --recurse-submodules … C:\gl\a`). That worked —
`git rev-parse HEAD:OB1` and `git -C OB1 rev-parse HEAD` both returned `fe3e045`
after `git submodule update --init`, which is T1's whole point. But four gitignored
files the clone does not have each produce a refusal that looks like a failure of
this branch and is not. Every one was reproduced at `5005197` too:

| Missing in a clone | What refuses | Same at 5005197? |
|---|---|---|
| `OB1/recipes/daily-digest/.env`, `OB1/recipes/email-history-import/.env` | `inventory --check` -> "`config --profiles` exited 1 … env file … not found" | YES — identical, naming the other recipe |
| the ROOT `.env` | `inventory --check` -> `[FAIL] projects.ai-stack` (`file: null` vs `docker-compose.yml`) | yes (host shape, not pin) |
| `<plane>/.env` | `init --product research` refuses on missing keys; seeding the examples still leaves `LITELLM_MASTER_KEY` and `MULLVAD_WG_ADDRESSES` blank | yes |
| `agent-org/docker/.env` | `[ -- ] NOT VERIFIED - agent-org` (non-fatal) | yes |

**One of those is worth more than a setup note.** `docker compose … config --services`
exits **0** on a tree with a missing `env_file:` target, while `config --profiles`
exits **1** on the same tree. So the render half of a verification can pass while
`inventory --check` refuses, on identical inputs — and the difference is which
subcommand was used, not which files are present. Anyone debugging "the renders work
but the driver refuses" will otherwise look for the fault in the driver.

With those seeded (the recipe envs can be EMPTY files — compose only needs them to
exist), the clone reproduces every number in this note:
bare 20, all four 30, `diff` clean against the 30 running container names,
`inventory --check` exit 0 with no `declared, not rendered` line,
`init --product research --force` writing all four profiles, `up --all --dry-run`
emitting all four `--profile` flags on the OB1 line, `ruff` clean, 107 driver tests
green, and `check-project-configs.ps1` reporting `open-brain:30/30`.

The red/green pair for §6's gate was re-run there against the identical staged delta:
the BASE version of `check-project-configs.ps1` (`git show f9b18f2:…`) exits 0 having
printed no render lines at all; the branch version prints
`all 9 compose projects render clean` and `open-brain:30/30`.

## 11. Attempt 1 FAILED. Both findings were the same mistake in two materials

Tester `wt-tester-gitlink`, evidence `C:\tgl\sl-ob1-gitlink-evidence-a1.md`, tip
`bdec3ab`. All twelve plan cases PASSED as written and the item still failed, which is
the part worth keeping: **the plan was the defect**.

### F1 — the headline number was arithmetic, not a measurement

I rendered `bare`, and each profile ALONE, and `all four`. I never rendered
`idea-refinery + research` — the set the driver actually passes on this host. I added
20 + 1 + 2 in my head, wrote **22**, and shipped it to eight files. 22 is the count for
`--profile research` alone, which I *had* measured; I reused a real number for a
different set.

The render is **23**. My own sentences convict the arithmetic: they say `wiki` and
`notebook` gate **seven** running containers, and 30 − 7 = 23.

Nothing operational changed — the operator still owes the same one command and the
missing seven are still seven — but the acceptance criterion is literally "a stale
sentence FAILS", and this was the item's headline number in `CLAUDE.md`,
`stack.manifest.toml`, `stack.ps1`, `stack/README.md`, `test_stack.py`,
`SERVICE-LIFECYCLE.md`, the stack-map reference, and this note.

**The rule that would have caught it, now in the plan and in two of the docs:
render the set you are about to describe. Never derive a count by adding deltas.**
A delta table is a summary of renders; it is not a calculator.

### F2 — I declared the decisive test impossible instead of trying it

§5 of attempt 1 said a profile-less `down` "cannot be measured without stopping them,
and this item touches no container". The tester built a two-service throwaway project
and had the answer in about two minutes, touching nothing real. §5a above reproduces it.

Two things make this worse than a missed test:

1. **I had already asserted the answer.** My own `Reset-OB1Stack` comment said a
   missing profile on the `down` half "is the difference between tearing the project
   down and leaving part of it behind" — stated as fact, two functions away from the
   bare `down` I excused as unmeasurable. If it was solid enough to write into a
   comment, it was solid enough to act on; if it was not, it did not belong in a
   comment.
2. **I wrote the impossibility into the plan's "Anything NOT claimed" block**, which
   is meant to stop a reader inferring what was not checked. Used that way it stopped
   the tester's *expectations* rather than the tester — a fence around the one case
   that mattered. A scope fence must say "out of scope", never "unmeasurable", unless
   unmeasurable has itself been tested.

**The generalisation.** "Touch no container" is a constraint on WHICH containers, not
on whether behaviour can be observed. A throwaway project is two files and two minutes
and is not a shared resource. Before writing "cannot be measured", cost the experiment
that would settle it — the honest sentence is almost always "I did not measure this",
and that one invites the tester to.

### What both have in common

Each was a place where I substituted a plausible inference for an observation I could
cheaply have made, and then wrote the inference down in a form that looked measured.
Findings notes and code comments are where that is hardest to spot afterwards, because
the surrounding sentences *are* measured. The class is recorded in
`documentation/notes/agent-harness-findings-note-audit.md` (2026-09-05) as "a check
that accepts the author's enumeration is not a check"; this is the same thing one level
down — a note that accepts the author's arithmetic.

### What attempt 2 changed

- 22 -> 23 in eight files plus this note, each re-rendered rather than recomputed, and
  the occurrences that legitimately mean `--profile research` alone (stack-map's render
  table, §2 and §3 here) deliberately left at 22.
- `emergency-recovery.ps1`: one `$Script:OB1Profiles` list, every OB1 compose
  invocation using it (§5a). The two bare sites are fixed; the `ps` sites carry it for
  uniformity.
- The landing step named explicitly in SERVICE-LIFECYCLE row 8a and the stack-map OB1
  section — `COMPOSE_PROFILES` in `OB1/docker/.env` **and** the driver state — with the
  `--headless` no-op consequence stated (§5a).
- §5's false premise struck through rather than deleted, because the mistake is the
  finding.
- The plan: a case that RENDERS the two-profile set, the throwaway-project method in
  place of the impossibility claim, the two missing claim rows, and corrections to
  T2/T3's "stderr empty" and T11's "0 stray CR" (both of which the tester showed were
  wrong about the environment, not about the deliverable).

### Carried from the tester, not fixed here

- **C3-c**: the attempt-1 commit message claims "OB1 recipe tests 54/54 and `deno check`
  clean for the staged gitlink". Those are the pre-commit hook's own output on that
  commit (`check-ob1-recipe-tests.ps1`, `check-ob1-deno-recipes.ps1`, both gated on a
  staged OB1 gitlink) and they re-ran green on this round's gitlink commit. The tester
  correctly notes they did not re-measure it; it is hook output, not a claim about OB1's
  behaviour.
- **C3-d**: `scripts/stack/README.md` still closes with "Per-plane `.env` files
  (`sl-env-split`): this item still encodes the single root `.env`", stale since D10.
  Pre-existing, untouched by this branch, and not in its surface. Recorded so it is not
  lost.
