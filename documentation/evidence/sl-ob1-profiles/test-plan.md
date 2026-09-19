# Test plan — `sl-ob1-profiles`

**Item:** gate Open Brain's research, wiki and notebook groups behind compose
profiles; teach the ai-stack manifest, drivers, inventory and docs about them.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-profiles.json`
**Developer worktree:** `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-ob1-profiles`
**ai-stack branch:** `work/sl-ob1-profiles`, commit **`6fa94a4`** (base `development` @ 9f64b84)
**OB1 branch:** `work/sl-ob1-profiles`, commit **`17cb1572a7f9350df11283ba9172b9ee7e47a6a0`** — **UNPUSHED ON PURPOSE** (case T25)
**Findings:** `documentation/notes/stack-layers-sl-ob1-profiles-findings.md`

> **The tester did not write this.** Read the findings note first — cases T1,
> T13, T20, T21 and T25 exist because of what is in it, and §9 explains why the
> no-profile render is 20 services and not the 15 the anchor's criterion lists.

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

Prove the change adds nothing else:

```bash
git -C <worktree>/OB1 stash
cd <worktree>/OB1/docker && docker compose -f docker-compose.yml --env-file .env config > /tmp/pinned.yml
git -C <worktree>/OB1 stash pop
docker compose -f docker-compose.yml --env-file .env --profile research --profile wiki --profile notebook config > /tmp/new.yml
diff /tmp/pinned.yml /tmp/new.yml
```

PASS = **exactly nine** additions, each a two-line `profiles:` / `- <name>`
block, and nothing else. Developer observed 1407 → 1425 lines, nine blocks, no
other hunk. Use `stash` / `stash pop`, **not** `git -C OB1 checkout` — per the
OB1-gitlink memory, a submodule checkout IS a deploy.

## T12 - [A12] the scheduled compose file still renders

Out of scope but merged into the project by `include:`, so T1–T10 already
exercise it. Confirm explicitly that
`docker compose -f docker-compose.scheduled.yml --env-file .env config -q`
exits 0.

## T13 - [B1] no CORE service depends_on a profiled one

The invariant the whole change rests on. **Enumerate it yourself; do not copy
the table below.**

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

## T14 - [B2] the surviving core→profiled RUNTIME references are declared

Core→profiled references survive as environment URLs. For each, confirm it is
(a) present, (b) commented at its line or in `OB1/docker/README.md`, (c) **not**
a `depends_on`:

- `openbrain-ext` → `WIKI_RECOMPILE_URL: http://openbrain-wiki:8000/recompile`
- `openbrain-podcast` → `RESEARCH_URL: http://openbrain-research:8000` and
  `ON_BASE: http://open_notebook:5055` (scheduled file)

An undocumented third one = FAIL.

## T15 - [B3] every profiled service has a stated reason

`OB1/docker/README.md` §"Compose profiles" must name **all nine** profiled
services with a group and a one-line reason. A service assigned with no reason
FAILS per the anchor. Read the reasons adversarially: does each actually
distinguish the service from core, or merely restate the assignment? Say so in
writing if it restates.

## T16 - [C1] the stack unit tests pass

`python -m pytest scripts/stack -q` → **41 passed**. Then read the diff of
`scripts/stack/test_stack.py` and judge each edit as a correction or an
accommodation:

- `test_enable_research_pulls_its_planes_and_ob1_profiles`: `assert "PENDING" in out`
  became `not in`. Correct only because the profiles now exist — confirm via T17.
- `test_the_ob1_profiles_are_no_longer_pending` (new)
- `test_digest_needs_the_notebook_profile_not_just_research` (new)
- `test_a_bare_ob1_plane_passes_only_the_default_profile` (renamed from
  `test_ob1_keeps_the_idea_refinery_profile_stack_ps1_always_passes`; the old
  name became false when stack.ps1 began passing four — the assertion is
  unchanged)
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
| `enable open-brain` | `idea-refinery, wiki` |
| `enable open-brain --headless` | `idea-refinery` only |

If `digest --headless` drops `notebook`, that is a FAIL — findings §6 explains
why a headless digest without Open Notebook silently produces no episode.

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

Stage the branch's files, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
```

Note the compose + inventory blocks only run when a `.yml`/`.yaml` is staged,
and this branch stages none (OB1's compose lives in the submodule). The
developer therefore ran the inventory verifier's logic directly; reproduce it:
render OB1 with all four profiles, extract `container_name` values, and check
each against `scripts/lib/stack-services.json`. Expect **30** names, zero drift.

**Then break it on purpose**, because a check that cannot be made to fail is not
evidence:

1. Temporarily drop `--profile wiki` from the OB1 render target. Confirm the
   check reports **nothing new** — that silent narrowing is exactly the failure
   mode findings §3 describes, and it is why the profiles were added there.
2. Temporarily delete the `openbrain-idea-refinery` row from
   `scripts/lib/stack-services.json`. Confirm it goes RED with
   `MISSING from stack-services.json`.

Revert both.

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
`documentation/runbooks/SERVICE-LIFECYCLE.md` gains row 8a naming the three
places a profile must be declared. Run `/stack-map` if you want the drift view.

## T25 - [R1] the OB1 commit exists locally and is NOT on any remote

The anchor requires the SHA to be reachable on the OB1 remote BEFORE the gitlink
moves. **Per DECISIONS D4 the push is a human action, so this item neither
pushes nor bumps the gitlink.** The criterion is therefore recorded as
**WAITING**, and the tester verifies the opposite assertion — that the item
stopped in the right place:

```bash
git -C <worktree>/OB1 log -1 --format=%H work/sl-ob1-profiles
#   -> 17cb1572a7f9350df11283ba9172b9ee7e47a6a0
git -C <worktree>/OB1 branch -r --contains 17cb1572a7f9350df11283ba9172b9ee7e47a6a0
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
`work/sl-ob1-profiles`, commit `17cb1572a7f9350df11283ba9172b9ee7e47a6a0`, to be
pushed onto `feature/integrated-knowledge-system`. Only after
`git -C OB1 branch -r --contains 17cb157` lists
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
