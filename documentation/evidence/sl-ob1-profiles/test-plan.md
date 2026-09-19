# Test plan — `sl-ob1-profiles`

**Item:** gate Open Brain's research, wiki and notebook groups behind compose
profiles; teach the ai-stack manifest, drivers, inventory and docs about them.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-profiles.json`
**Developer worktree:** `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-ob1-profiles`
**ai-stack branch:** tip of `work/sl-ob1-profiles` (base `development` @ 9f64b84)
**OB1 branch:** `work/sl-ob1-profiles`, commit **`fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19`** — **UNPUSHED ON PURPOSE** (case T25).
Destination, for the operator: `feature/integrated-knowledge-system` on the OB1 remote.
**Findings:** `documentation/notes/stack-layers-sl-ob1-profiles-findings.md`

> **ATTEMPT 3.** Attempt 2 FAILED on **T31 criterion 4** only: `profile_closure`
> ran on `cmd_enable`'s product branch and at drive time, but not on the plane
> branch or in `cmd_init`, so `enable ob1` PRINTED two profiles and WROTE one.
> Every state-file writer now goes through one helper, `enable_plane_profiles`,
> and T31 criterion 4 covers all five paths. T21 gains a fourth break (absent
> `OB1/docker/.env` — the CI shape — used to be a silent skip). **No OB1 change
> in this attempt: the OB1 commit is untouched at `fe3e045`.**
>
> *Attempt 2 fixed attempt 1's T14 FAIL: it claimed two cross-group runtime
> references and there are six — the two it missed arrive via `env_file:` and are
> invisible to a grep. T14 is built around the render-based method; T30 and T31
> are new; T11, T16, T19, T21 and T24 changed then.*

> **The tester did not write this.** Read the findings note first — cases T1,
> T13, T14, T20, T21, T25, T30 and T31 exist because of what is in it, and §9
> explains why the no-profile render is 20 services and not the 15 the anchor's
> criterion lists.

**Ground rules.** All commands run from the worktree unless stated; OB1 compose
commands run from `<worktree>\OB1\docker`. **No plane lease is needed** — every
case is a render, a dry-run or a read-only `docker ps`. **Nothing in this plan
starts, stops, recreates or restarts a container.** If a case seems to ask you
to, stop and say so: this item deploys nothing.

Render cases all use:

```bash
docker compose -f docker-compose.yml --env-file .env [PROFILES] config --services | sort
```

A render error — any non-zero exit, any `depends on undefined service`, any
unresolved variable — is a FAIL for that case, not a warning.

---

## T1 - [A1] no-profile render is core only (the anchor's first criterion)

No profiles. Expect **20** services: the 15 core (`openbrain-db`, `-mcp`,
`-ext`, `-gateway`, `-ops-gateway`, `-mcpo`, `-mcpo-ext`, `-postgrest`, `-rest`,
`-entity-worker`, `-suggestion-worker`, `-extract`, `-chunk-worker`,
`-grounding-backfiller`, `-db-backup`) **plus** the 5 always-on scheduled
services (`-cron`, `-gmail-pull`, `-gmail-prune`, `-digest`, `-podcast`), which
arrive via `include:` and are out of scope for this item.

FAIL if ANY of these appear: `openbrain-curator`, `openbrain-research`,
`openbrain-wiki`, `openbrain-wiki-viewer`, `openbrain-workbench`,
`openbrain-wiki-backup`, `surrealdb`, `open_notebook`, `open-notebook-backup`,
`openbrain-idea-refinery`.

## T2 - [A2] `--profile research` renders 22

T1's set plus exactly `openbrain-curator` and `openbrain-research`.

## T3 - [A3] `--profile wiki` renders 24

T1's set plus exactly `openbrain-wiki`, `-wiki-viewer`, `-workbench`,
`-wiki-backup`.

## T4 - [A4] `--profile notebook` renders 23

T1's set plus exactly `surrealdb`, `open_notebook`, `open-notebook-backup`.

## T5 - [A5] `--profile idea-refinery` renders 21

T1's set plus `openbrain-idea-refinery`. This profile PRE-DATES the item
(`docker-compose.scheduled.yml`); the case exists because the task brief
asserted OB1 had no profiles, which was wrong — findings §1.

## T6 - [A6] `research` + `wiki` renders 26

Must equal T2 ∪ T3 exactly — no service appears that neither single profile
produced.

## T7 - [A7] `research` + `notebook` renders 25

Must equal T2 ∪ T4 exactly.

## T8 - [A8] `wiki` + `notebook` renders 27

Must equal T3 ∪ T4 exactly.

## T9 - [A9] all three new profiles render 29

`--profile research --profile wiki --profile notebook`. This is the anchor's
"all 24 main-file services" criterion, plus the 5 scheduled ones.

## T10 - [A10] all four profiles render the full 30-container fleet

`--profile research --profile wiki --profile notebook --profile idea-refinery`.

## T11 - [A11] definitions are unchanged apart from nine `profiles:` keys

Prove the change adds nothing else. **Do not write in the developer's tree** —
attempt 1's plan said `git stash` / `stash pop` inside `<worktree>/OB1`, which
contradicts the tester's read-only mandate. Attempt 1's tester found the right
method and it is now the instruction: render both versions from a scratch copy,
using `git show <sha>:<path>` to materialise the pinned file.

```bash
SC=<scratch>/t11
mkdir -p "$SC" && cp -r <worktree>/OB1/docker "$SC/docker"
# the compose file reads two gitignored recipe .env files by relative path
mkdir -p "$SC/recipes/daily-digest" "$SC/recipes/email-history-import"
cp <worktree>/OB1/recipes/daily-digest/.env          "$SC/recipes/daily-digest/.env"
cp <worktree>/OB1/recipes/email-history-import/.env  "$SC/recipes/email-history-import/.env"
cd "$SC/docker"
git -C <worktree>/OB1 show 5005197:docker/docker-compose.yml           > pinned.yml
git -C <worktree>/OB1 show 5005197:docker/docker-compose.scheduled.yml > docker-compose.scheduled.pinned.yml
sed -i 's/docker-compose.scheduled.yml/docker-compose.scheduled.pinned.yml/' pinned.yml
docker compose -f pinned.yml --env-file .env config > cfg-pinned.yml
docker compose -f docker-compose.yml --env-file .env \
  --profile research --profile wiki --profile notebook config > cfg-new.yml
diff cfg-pinned.yml cfg-new.yml
```

PASS = **exactly nine** additions, each a two-line `profiles:` / `- <name>`
block (3 notebook, 2 research, 4 wiki), and nothing else. 1407 → 1425 lines.
Rendering both from the SAME directory is what keeps absolute paths identical so
the diff is only the profiles. Never `git -C OB1 checkout` — per the OB1-gitlink
memory, a submodule checkout IS a deploy.

Attempt 2 added comments to both OB1 compose files; comments are stripped by
`config`, so this diff must be unchanged from attempt 1. If it is not, say so.

## T12 - [A12] the scheduled compose file still renders

Out of scope but merged into the project by `include:`, so T1–T10 already
exercise it. Confirm explicitly that
`docker compose -f docker-compose.scheduled.yml --env-file .env config -q`
exits 0.

## T13 - [B1] no CORE service depends_on a profiled one

The invariant the whole change rests on. **Enumerate it yourself; do not copy
the table below.**

**Enumerate from the RENDER, not from the files.** Attempt 1 grepped and
under-reported (that is T14's history); a `depends_on` happens to be grep-safe
but the habit is not, so build both this case and T14 from one JSON:

```bash
cd <worktree>/OB1/docker
docker compose -f docker-compose.yml --env-file .env \
  --profile research --profile wiki --profile notebook --profile idea-refinery \
  config --format json > <scratch>/ob1-all.json
```

Every service's `profiles` key is in there, so each edge's owner and target can
be classified without consulting the compose text at all. Cross-check against
the files if you like:

```bash
grep -n -A6 "depends_on:" <worktree>/OB1/docker/docker-compose.yml
grep -n "depends_on" <worktree>/OB1/docker/docker-compose.scheduled.yml   # expect exit 1
```

Classify every block's owner and each target as core / research / wiki /
notebook from the `profiles:` keys. PASS requires that no block owned by a core
service names a profiled target. The developer's enumeration, to be verified:

| Owner | Group | depends_on | Target group | Verdict |
|---|---|---|---|---|
| `openbrain-mcp` | core | `openbrain-db` | core | in-core |
| `openbrain-ext` | core | `openbrain-db` | core | in-core |
| `openbrain-gateway` | core | `openbrain-mcp` | core | in-core |
| `openbrain-ops-gateway` | core | `openbrain-mcp` | core | in-core |
| `openbrain-mcpo` | core | `openbrain-mcp` | core | in-core |
| `openbrain-mcpo-ext` | core | `openbrain-ext` | core | in-core |
| `openbrain-postgrest` | core | `openbrain-db` | core | in-core |
| `openbrain-rest` | core | `openbrain-postgrest` | core | in-core |
| `openbrain-entity-worker` | core | `openbrain-rest` | core | in-core |
| `openbrain-suggestion-worker` | core | `openbrain-db` | core | in-core |
| `openbrain-chunk-worker` | core | `openbrain-db` | core | in-core |
| `openbrain-grounding-backfiller` | core | `openbrain-db` | core | in-core |
| `openbrain-curator` | research | `openbrain-db`, `openbrain-mcp` | core, core | profiled → core, safe |
| `openbrain-research` | research | `openbrain-db`, `openbrain-curator` | core, **research** | in-group |
| `openbrain-wiki` | wiki | `openbrain-rest`, `openbrain-entity-worker` | core, core | profiled → core, safe |
| `openbrain-wiki-viewer` | wiki | `openbrain-wiki` | **wiki** | in-group |
| `openbrain-workbench` | wiki | `openbrain-db`, `openbrain-rest` | core, core | profiled → core, safe |
| `open_notebook` | notebook | `surrealdb` | **notebook** | in-group |

No `depends_on` at all: `openbrain-db`, `openbrain-extract`,
`openbrain-db-backup`, `openbrain-wiki-backup`, `surrealdb`,
`open-notebook-backup`, and every service in the scheduled file.

**Nothing was removed and nothing was set `required: false`, because there was
nothing to fix.** If your enumeration finds an edge this table missed, that is a
FAIL and the table is what was wrong.

## T14 - [B2] every cross-group RUNTIME reference is found and declared

**This case FAILED attempt 1. Re-derive the list; do not check the table below
off.** Attempt 1 enumerated by grepping the two compose files, found two
references, and wrote "two" into three documents. The tester matched the
**rendered** environment values and found six — two of them arrive via
`env_file: ../recipes/email-history-import/.env` and appear nowhere in the
compose text.

**Method (the case is as much about this as about the count).** From the JSON
rendered in T13, match every profiled service's NAME against every service's
`environment` **values**, then do the same for `depends_on`, `network_mode`,
`links`, network aliases and shared named volumes:

```python
import json
d = json.load(open("<scratch>/ob1-all.json"))["services"]
prof = {n: ((s.get("profiles") or [None])[0]) for n, s in d.items()}
for name, s in d.items():
    for k, v in (s.get("environment") or {}).items():
        for target, tp in prof.items():
            if tp and v and target in str(v) and (prof[name] or "core") != tp:
                print(name, prof[name] or "core", k, "->", target, tp)
```

PASS requires all of: your enumeration finds **exactly** the six below; each is
commented **at its own line** in the compose file with what goes dead; all six
appear in `OB1/docker/README.md`'s cross-group table; and the counts in
`OB1/docker/docker-compose.yml`'s header, the README and
`.claude/skills/stack-map/references/workspace-stacks.md` all say **six**, not
two. A seventh you find that is undeclared = FAIL. A document still saying
"two" = FAIL.

| Caller | Caller group | Key | Target | Target profile |
|---|---|---|---|---|
| `openbrain-ext` | core | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` |
| `openbrain-gmail-prune` | core | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` |
| `openbrain-gmail-pull` | core | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` |
| `openbrain-podcast` | core | `RESEARCH_URL` | `openbrain-research` | `research` |
| `openbrain-podcast` | core | `ON_BASE` | `open_notebook` | `notebook` |
| `openbrain-idea-refinery` | `idea-refinery` | `RESEARCH_URL` | `openbrain-research` | `research` |

Also confirm the two source-level claims the docs make, since they are the
reason a grep cannot find these:
`recipes/email-history-import/prune-short-term.ts:32` hard-codes
`http://openbrain-wiki:8000/recompile` as the default and `:153` POSTs to it
inside a `try/catch`; and `integrations/openbrain-idea-refinery/index.ts:268`
submits to `RESEARCH_URL` with `:295` polling it.

## T30 - [new in attempt 2] a cross-group reference is documented with its CONSEQUENCE, not just its existence

Attempt 1 documented two references and still failed, so "it is mentioned" is
not the bar. For each of the six, `OB1/docker/README.md` must say **what stops
working** when the target's profile is off. Judge these adversarially — the two
that matter operationally are `openbrain-gmail-prune` (the nightly prune
completes and the vault is never recompiled) and `openbrain-idea-refinery` (its
only engine; the drain can never drain). If a row's consequence column restates
the reference instead of naming an outcome, say so.

Also check the reason given for `openbrain-idea-refinery` in the profile table:
attempt 1 said "deliberately off until a Mattermost bot token is configured",
which is false — the container has been up for days. The corrected text must not
reintroduce that claim.

## T15 - [B3] every profiled service has a stated reason

`OB1/docker/README.md` §"Compose profiles" must name **all nine** profiled
services with a group and a one-line reason. A service assigned with no reason
FAILS per the anchor. Read the reasons adversarially: does each actually
distinguish the service from core, or merely restate the assignment? Say so in
writing if it restates.

## T16 - [C1] the stack unit tests pass

`python -m pytest scripts/stack -q` → **47 passed** (41 in attempt 1; +3 for
profile-`requires` in attempt 2; +3 in attempt 3 for the state-file writers, judged
at T31 criterion 4). Then read the diff of
`scripts/stack/test_stack.py` and judge each edit as a correction or an
accommodation:

- `test_enable_research_pulls_its_planes_and_ob1_profiles`: `assert "PENDING" in out`
  became `not in`. Correct only because the profiles now exist — confirm via T17.
- `test_the_ob1_profiles_are_no_longer_pending` (new)
- `test_digest_needs_the_notebook_profile_not_just_research` (new)
- `test_a_bare_ob1_plane_passes_its_default_profile_and_what_that_needs`
  (renamed twice — in attempt 1 because stack.ps1 went to four profiles, and in
  attempt 2 because the ASSERTION itself changed: a bare plane enable now passes
  `idea-refinery` and `research`. That is a real behaviour change; judge it at
  T31, not here.)
- `test_the_idea_refinery_profile_pulls_the_research_engine_it_calls`,
  `test_profile_requires_is_transitive_and_order_is_the_manifests`,
  `test_a_profile_requiring_an_unknown_profile_is_refused` (new in attempt 2)
- `test_enabling_research_drives_ob1_with_every_profile_the_live_set_needs` (new)

## T17 - [C2] the manifest no longer marks the OB1 profiles pending

`grep -n "pending" stack.manifest.toml` must return **no** `[planes.ob1.*]`
line. `inference.local`, `frontend.gpu` and `frontend.tailscale` must STILL be
pending — those belong to other items and flipping them would be out of scope.

## T18 - [C3] the dry-run flags reproduce the live container set

```bash
python scripts/stack/stack.py --state <scratch>/s.json enable research
python scripts/stack/stack.py --state <scratch>/s.json up --dry-run
```

Use a `--state` path OUTSIDE the repo. The OB1 line must be exactly:

```
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d
```

Then prove those flags equal what is running:

```bash
docker ps --filter "label=com.docker.compose.project=open-brain" --format "{{.Names}}" | sort > live.txt
cd <worktree>/OB1/docker && docker compose -f docker-compose.yml --env-file .env \
  --profile research --profile wiki --profile notebook --profile idea-refinery config --services | sort > render.txt
diff live.txt render.txt
```

PASS = empty diff, 30 lines each.

## T19 - [C4] the other product paths resolve to the right profile sets

Same scratch-state method, one fresh state per row:

| Command | OB1 profiles expected |
|---|---|
| `enable research --headless` | `idea-refinery, research` |
| `enable digest` | `idea-refinery, research, notebook` |
| `enable digest --headless` | `idea-refinery, research, notebook` (unchanged) |
| `enable open-brain` | `idea-refinery, research, wiki` |
| `enable open-brain --headless` | `idea-refinery, research` |

If `digest --headless` drops `notebook`, that is a FAIL — findings §6 explains
why a headless digest without Open Notebook silently produces no episode.

**The last two rows CHANGED in attempt 2** and gained `research`, because
`idea-refinery` now `requires` it (T31). Attempt 1's plan said
`enable open-brain --headless` → `idea-refinery` alone. That old expectation was
the bug: it started a drain with no engine. Verify the new values are the
mechanism working, not an accommodation — T31 is where to do that.

## T31 - [new in attempt 2] `idea-refinery` pulls the research engine it calls, and the decision is argued

New in attempt 2, answering the tester's "decide whether `idea-refinery` implies
`research`". The manifest declares `requires = ["research"]` on the profile and
`stack.py` closes over it (`profile_closure`).

Verify the MECHANISM, not the assertion:

1. `manifest.profile_requires("ob1", "idea-refinery")` == `["research"]`, and
   `idea-refinery` is still the plane's only `default`.
2. A bare `enable ob1` (or `init --planes …,ob1`) dry-run prints
   `--profile idea-refinery --profile research`. Before this it printed
   `idea-refinery` alone — i.e. every invocation started a drain with no engine.
3. The closure is transitive and refuses a typo: `test_profile_requires_is_
   transitive_and_order_is_the_manifests` and `test_a_profile_requiring_an_
   unknown_profile_is_refused` in `scripts/stack/test_stack.py`. Run them, then
   corrupt the manifest yourself (`requires = ["reserch"]`) and confirm
   `Manifest.load` raises a `Refusal` naming `ob1.idea-refinery` — a mechanism
   that cannot be made to fail is not a mechanism.
4. **The state file must contain `research`, from EVERY writer — this criterion
   failed attempt 2.** A state file that omits a prerequisite the driver silently
   supplies is a state file that lies. Attempt 2 applied the closure only on
   `cmd_enable`'s *product* branch, so `enable ob1` printed
   `profiles: idea-refinery, research` and wrote `["idea-refinery"]`; drive time
   was right either way, which is precisely why the suite stayed green.

   Check the **file**, not the printed line, for all five writer paths:

   | Invocation | state `planes.ob1.profiles` must be |
   |---|---|
   | `enable ob1` (plane branch, after enabling its required planes) | `["idea-refinery","research"]` |
   | `init --planes inference,search,ob1` | `["idea-refinery","research"]` |
   | `init --planes … --context ob1=<ctx>` | same, plus `context` set |
   | `enable open-brain --headless` (product branch) | `["idea-refinery","research"]` |
   | `init --product research` | all four |

   Then check the SHAPE of the fix, not just its effect: there is one helper,
   `enable_plane_profiles`, and `State.enable` is called from nowhere else in the
   module (`grep -n "\.enable(" scripts/stack/stack.py` → exactly one hit, inside
   that helper). Three tests pin this —
   `test_enabling_the_PLANE_writes_the_closure_not_just_the_defaults`,
   `test_init_with_planes_writes_the_closure_too`,
   `test_every_state_writer_goes_through_the_one_helper`. **Make them fail**:
   revert the plane branch to `state.enable(target, manifest.default_profiles(target))`
   in a scratch copy and confirm the first and third go red; revert the `cmd_init`
   line and confirm the second and third do. Three writers each resolving profiles
   their own way is the bug; a test that only covers one path is how it shipped.

Then judge the DECISION, which is stated at the manifest line and in findings
§5a. Two alternatives were rejected: marking `research` itself `default` (would
make `--headless` a no-op for the plane — verify that claim: `run_profiles`
unions state with defaults, so a default survives `--headless`), and dropping
`default` from `idea-refinery` (the better long-term fix, but it STOPS a
container that has been up for days, and this item deploys nothing). Say whether
you accept the reasoning. The stated consequence — `enable open-brain --headless`
now pulls the research engine in — must be in the manifest, not discovered by
you.

## T20 - [C5] stack.ps1 does not regress the deployment

`scripts/stack/stack.ps1`'s `ob1` row must pass **all four** profiles. Confirm
the file parses
(`[System.Management.Automation.PSParser]::Tokenize((Get-Content scripts\stack\stack.ps1 -Raw), [ref]$e)`)
and is ASCII, no BOM, CRLF.

The divergence from stack.py's bare-plane default is **deliberate** and argued
in findings §7. Decide for yourself whether the reasoning holds and say so
either way. The tempting wrong fix is marking the three `default = true` so the
drivers match — that makes `--headless` a no-op for this plane.

## T21 - [C6] check-project-configs passes, and can still be made to FAIL

**Stage in a throwaway clone under YOUR OWN scratch dir, never in the
developer's worktree** — this case writes to the index and edits the script and
the inventory. Attempt 1's plan just said "stage the branch's files"; attempt 1's
tester correctly cloned instead. Setup (a clone has an empty submodule, so the
OB1 compose and the two gitignored recipe `.env` files must be copied in):

```bash
SC=<scratch>/checkrepo
git clone -q --local --no-hardlinks --branch work/sl-ob1-profiles <worktree> "$SC"
mkdir -p "$SC/OB1" && cp -r <worktree>/OB1/docker "$SC/OB1/docker"
mkdir -p "$SC/OB1/recipes/daily-digest" "$SC/OB1/recipes/email-history-import"
cp <worktree>/OB1/recipes/daily-digest/.env         "$SC/OB1/recipes/daily-digest/.env"
cp <worktree>/OB1/recipes/email-history-import/.env "$SC/OB1/recipes/email-history-import/.env"
cd "$SC" && echo "# arm the compose gate" > trigger.yml && git add trigger.yml
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
```

(The compose + inventory blocks only run when a `.yml`/`.yaml` is staged, and
this branch stages none — OB1's compose lives in the submodule — hence the
throwaway `trigger.yml`.)

As shipped, expect exit 0 and a line that now states its own coverage:

```
[configs] stack-services.json inventory matches the compose configs
          [rows verified/expected: inference:8/8 frontend:4/4 memory:3/3 search:4/4 coder:4/4 open-brain:30/30]
```

`inference:8/8` is new in attempt 2 and is itself a finding: that target
previously passed no `--profile local` and had been verifying 4 of its 8 rows
silently (findings §5b).

**Then break it three ways**, because a check that cannot be made to fail is not
evidence. Measure the exit code directly — do not pipe the run through `grep`
and read `$?`, which reports grep's status:

1. **Drop `--profile wiki` from the OB1 render target.** Must exit **1** with
   `UNVERIFIED rows for open-brain … 26 container(s), the inventory holds 30 …
   (add --profile wiki)`. In attempt 1 this was green and silent — the whole
   reason the guard exists. **Note for your judgement:** the developer's FIRST
   version of this guard derived the expected set from the profiles the target
   passes, so this break still passed at 26/26. It now derives from every
   inventory row for the project. Check that the code does what that sentence
   says; an expectation computed from the thing it polices is not a guard.
2. **Delete the `openbrain-idea-refinery` row** from
   `scripts/lib/stack-services.json`. Must exit **1** with
   `MISSING from stack-services.json` (the compose→json direction, which already
   worked).
3. **Point the OB1 target at a compose file that does not exist.** Must exit
   **1** with `RENDER PRODUCED NOTHING for open-brain … 30 inventory row(s) went
   unverified`. Previously the code did `if (-not $raw) { continue }` and went
   green.
4. **Rename `OB1/docker/.env` out of the way** — the CI shape, since that file is
   gitignored. New in attempt 3. The `Test-Path` guard then drops the open-brain
   render target entirely, and before this fix the check printed its green line
   having verified 30 fewer rows with nothing said. Must now print
   `NOT VERIFIED: project 'open-brain' has 30 inventory row(s) and no render
   target (OB1\docker\.env absent …)` and the coverage list must lose its
   `open-brain:30/30` entry. **Exit stays 0** — deliberately: CI legitimately
   cannot render OB1, so a red here would cry wolf on every run. Judge that call;
   if you think it should be red, say so.

   The same pass prints `NOT VERIFIED: project 'agent-org' has 16 inventory
   row(s) and no render target` on EVERY run, including the green one. That is a
   real pre-existing gap (agent-org has never had a render target) which attempt
   1's tester could only find by instrumenting the script. Confirm it appears.

Revert all four, then delete the clone.

## T22 - [C7] the inventory rows carry the right profiles

`scripts/lib/stack-services.json` must parse; must carry `"profile"` on exactly
the nine profiled containers plus the new `openbrain-idea-refinery` row; and
`projects["open-brain"]` must list all four profiles with a note. Cross-check
every `profile` value against the `profiles:` key on that service in the compose
file — any mismatch is a FAIL.

## T23 - [C8] ruff's one error is pre-existing, not this branch's

`ruff check .` reports exactly one error:
`E501 llm-queue/src/llm_queue/__init__.py:9`. Confirm the attribution yourself
(`git log --oneline -1 -- llm-queue/src/llm_queue/__init__.py` → `cfa7d4e`, the
plan-store move, which is on the base). If the error is in a file this branch
touched, that is a FAIL. Background: findings §8.

## T24 - [C9] the docs match the code

`.claude/skills/stack-map/references/workspace-stacks.md` OB1 section carries
the profile block and the nine ``**[profile `x`]**`` row markers, in the same
style the inference plane already uses for `local`.
`documentation/runbooks/SERVICE-LIFECYCLE.md` gains row 8a (FOUR places a profile
must be declared, the fourth being cross-project consumers) and row 8b (enumerate
cross-profile references from the render, never a grep). Run `/stack-map` for the
drift view.

**Carried from T14:** the stack-map OB1 section must say **six** cross-group
references and carry the table. A document still saying "two" fails this case as
well as T14.

## T25 - [R1] the OB1 commit exists locally and is NOT on any remote

The anchor requires the SHA to be reachable on the OB1 remote BEFORE the gitlink
moves. **Per DECISIONS D4 the push is a human action, so this item neither
pushes nor bumps the gitlink.** The criterion is therefore recorded as
**WAITING**, and the tester verifies the opposite assertion — that the item
stopped in the right place:

```bash
git -C <worktree>/OB1 log -1 --format=%H work/sl-ob1-profiles
#   -> fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19
git -C <worktree>/OB1 branch -r --contains fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19
#   -> EMPTY. Any remote branch listed = something was pushed = FAIL.
git -C <worktree>/OB1 branch -r --contains 5005197
#   -> origin/feature/integrated-knowledge-system  (the parent, still the pin)
```

## T26 - [R2] the ai-stack gitlink is still 5005197

```bash
git -C <worktree> ls-tree HEAD OB1
#   -> 160000 commit 5005197dd3ae85a2a4aba8bf142084cac2824759  OB1
git -C <worktree> log development..HEAD --name-only -- OB1
#   -> EMPTY: no commit on this branch touches the OB1 path
```

`git -C <worktree> status --short` WILL show ` M OB1` because the submodule's
worktree HEAD moved to the new branch. That is expected and correct — the
gitlink in the index and tree is unchanged. A commit on this branch touching
`OB1` is a FAIL.

## T27 - [R3] the operator handoff is stated and not performed

The plan must tell the operator exactly what to push, and the tester must NOT
push it. Confirm this item's report and this plan both name: OB1 branch
`work/sl-ob1-profiles`, commit `fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19`, to be
pushed onto `feature/integrated-knowledge-system`. Only after
`git -C OB1 branch -r --contains fe3e045` lists
`origin/feature/integrated-knowledge-system` may a FOLLOW-UP commit run
`git add OB1` in ai-stack. **Do not do any of that while testing.**

## T28 - [D1] the live open-brain containers are unchanged

`docker ps --filter "label=com.docker.compose.project=open-brain" --format "{{.Names}}" | sort`
must return the same 30 names as case T18's `render.txt`. Check uptimes too: a
container whose uptime reset during testing means something in this plan
restarted it, which is a FAIL of the plan as much as of the item.

## T29 - [D2] no state file was left inside the worktree

`ls <worktree>/.stack` → absent. It is gitignored, but its presence would mean a
case ran `stack.py` without `--state` pointing outside the repo.
