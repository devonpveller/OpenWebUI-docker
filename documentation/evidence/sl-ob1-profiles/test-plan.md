# Test plan — `sl-ob1-profiles`

**Item:** gate Open Brain's research, wiki and notebook groups behind compose
profiles; teach the ai-stack manifest, drivers, inventory and docs about them.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-profiles.json`
**Developer worktree:** `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-ob1-profiles`
**ai-stack branch:** `work/sl-ob1-profiles` (base `development` @ 9f64b84)
**OB1 branch:** `work/sl-ob1-profiles` — commit **`17cb1572a7f9350df11283ba9172b9ee7e47a6a0`**, **UNPUSHED ON PURPOSE** (see case R1)
**Findings:** `documentation/notes/stack-layers-sl-ob1-profiles-findings.md`

> **The tester did not write this.** Read the findings note first — cases T3,
> T7 and R1 exist because of what is in it, and §9 tells you why the
> no-profile render is 20 services and not the 15 the anchor's criterion lists.

## How to run everything here

All commands run from the worktree unless stated. OB1 compose commands run
from `<worktree>\OB1\docker`. **No lease is needed** — every case is a render,
a dry-run or a read-only `docker ps`. **Nothing in this plan starts, stops,
recreates or restarts a container.** If a case seems to want you to, stop and
say so: this item deploys nothing.

---

## Group A — render combinations (the anchor's core criterion)

Run each from `<worktree>\OB1\docker`:

```bash
docker compose -f docker-compose.yml --env-file .env [PROFILES] config --services | sort
```

A render error — any non-zero exit, any `service ... depends on undefined
service`, any unresolved variable — is a FAIL for that case, not a warning.

| # | Profiles passed | Expect | Also assert |
|---|---|---|---|
| A1 | *(none)* | **20** services | The 15 core (`openbrain-db`, `-mcp`, `-ext`, `-gateway`, `-ops-gateway`, `-mcpo`, `-mcpo-ext`, `-postgrest`, `-rest`, `-entity-worker`, `-suggestion-worker`, `-extract`, `-chunk-worker`, `-grounding-backfiller`, `-db-backup`) **plus** the 5 always-on scheduled (`-cron`, `-gmail-pull`, `-gmail-prune`, `-digest`, `-podcast`). **ZERO** of: `openbrain-curator`, `openbrain-research`, `openbrain-wiki`, `openbrain-wiki-viewer`, `openbrain-workbench`, `openbrain-wiki-backup`, `surrealdb`, `open_notebook`, `open-notebook-backup`, `openbrain-idea-refinery`. Any one of those present = FAIL. |
| A2 | `--profile research` | **22** | A1's set + `openbrain-curator`, `openbrain-research`, and nothing else |
| A3 | `--profile wiki` | **24** | A1 + `openbrain-wiki`, `-wiki-viewer`, `-workbench`, `-wiki-backup` |
| A4 | `--profile notebook` | **23** | A1 + `surrealdb`, `open_notebook`, `open-notebook-backup` |
| A5 | `--profile idea-refinery` | **21** | A1 + `openbrain-idea-refinery` (this profile pre-dates the item) |
| A6 | `research` + `wiki` | **26** | = A2 ∪ A3 |
| A7 | `research` + `notebook` | **25** | = A2 ∪ A4 |
| A8 | `wiki` + `notebook` | **27** | = A3 ∪ A4 |
| A9 | `research` + `wiki` + `notebook` | **29** | the anchor's "all 24 main-file services" criterion, plus the 5 scheduled |
| A10 | all four | **30** | the complete fleet |

**A11 — definitions are otherwise untouched.** Prove the change is *only*
`profiles:` keys, by diffing the full render against the pinned commit:

```bash
git -C <worktree>/OB1 stash          # back to 17cb157's parent content
cd <worktree>/OB1/docker && docker compose -f docker-compose.yml --env-file .env config > /tmp/pinned.yml
git -C <worktree>/OB1 stash pop
docker compose -f docker-compose.yml --env-file .env --profile research --profile wiki --profile notebook config > /tmp/new.yml
diff /tmp/pinned.yml /tmp/new.yml
```

PASS = the diff is **exactly nine** additions, each a two-line
`profiles:` / `- <name>` block, and nothing else. (Use `git -C OB1 stash` /
`stash pop` rather than checking out 5005197 — per the OB1-gitlink memory,
`git -C OB1 checkout` IS a deploy.) Developer's observed result: nine blocks,
1407 → 1425 lines, no other hunk.

**A12 — the scheduled file still renders.** It is out of scope but is pulled in
by `include:`, so A1–A10 already exercise it. Explicitly confirm
`docker compose -f docker-compose.scheduled.yml --env-file .env config -q`
exits 0.

---

## Group B — the depends_on cross-group audit (do this by reading, not trusting)

**B1 — no CORE service `depends_on` a profiled one.** This is the invariant the
whole change rests on. Do not take the developer's word for it; enumerate.

```bash
grep -n -A6 "depends_on:" <worktree>/OB1/docker/docker-compose.yml
grep -n "depends_on" <worktree>/OB1/docker/docker-compose.scheduled.yml   # expect exit 1, no matches
```

For every `depends_on` block, classify the **owner** and each **target** as
core / research / wiki / notebook using the `profiles:` keys in the file. PASS
requires that no block owned by a core service names a profiled target. The
developer's enumeration (verify it, do not copy it):

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

Services with **no** `depends_on` at all: `openbrain-db`, `openbrain-extract`,
`openbrain-db-backup`, `openbrain-wiki-backup`, `surrealdb`,
`open-notebook-backup`, and every service in the scheduled file.

**Nothing was removed and nothing was set `required: false`** — because there
was nothing to fix. If your enumeration finds an edge the table missed, that is
a FAIL and the table is the thing that was wrong.

**B2 — the RUNTIME cross-references are declared, not hidden.** Core→profiled
references survive as environment URLs. Confirm each is (a) present, (b)
commented at its line or in `OB1/docker/README.md`, (c) *not* a `depends_on`:

- `openbrain-ext` → `WIKI_RECOMPILE_URL: http://openbrain-wiki:8000/recompile`
- `openbrain-podcast` → `RESEARCH_URL: http://openbrain-research:8000` and
  `ON_BASE: http://open_notebook:5055` (scheduled file)

An undocumented third one = FAIL.

**B3 — every profiled service has a stated reason.** `OB1/docker/README.md`
§"Compose profiles" must name **all nine** profiled services with a group and a
one-line reason. A service assigned with no reason FAILS per the anchor. Read
the reasons adversarially: does the reason actually distinguish it from core, or
does it restate the assignment? Disagree in writing if so.

---

## Group C — the ai-stack side

**C1 — unit tests.** `python -m pytest scripts/stack -q` → **41 passed**.
Four tests changed or were added; read the diff of `scripts/stack/test_stack.py`
and judge whether each edit is a correction or an accommodation:
- `test_enable_research_pulls_its_planes_and_ob1_profiles`: `assert "PENDING" in out`
  became `not in`. Correct only because the profiles now exist — confirm with C2.
- `test_the_ob1_profiles_are_no_longer_pending` (new)
- `test_digest_needs_the_notebook_profile_not_just_research` (new)
- `test_a_bare_ob1_plane_passes_only_the_default_profile` (renamed from
  `test_ob1_keeps_the_idea_refinery_profile_stack_ps1_always_passes`; the old
  name became false when stack.ps1 started passing four — the assertion is
  unchanged)
- `test_enabling_research_drives_ob1_with_every_profile_the_live_set_needs` (new)

**C2 — the manifest no longer lies about pending.**
`grep -n "pending" stack.manifest.toml` must return **no** `[planes.ob1.*]`
line. `inference.local`, `frontend.gpu`, `frontend.tailscale` stay pending —
those are other items' and must NOT have been flipped.

**C3 — dry-run vs the live set (the anchor's criterion).**

```bash
python scripts/stack/stack.py --state <scratch>/s.json enable research
python scripts/stack/stack.py --state <scratch>/s.json up --dry-run
```

Use a `--state` path outside the repo so no `.stack/` is created. The OB1 line
must be **exactly**:

```
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d
```

Then prove those flags reproduce what is running:

```bash
docker ps --filter "label=com.docker.compose.project=open-brain" --format "{{.Names}}" | sort > live.txt
cd <worktree>/OB1/docker && docker compose -f docker-compose.yml --env-file .env \
  --profile research --profile wiki --profile notebook --profile idea-refinery config --services | sort > render.txt
diff live.txt render.txt
```

PASS = empty diff, 30 lines each.

**C4 — the other product paths.** Same scratch-state method:

| Command | OB1 profiles expected |
|---|---|
| `enable research --headless` | `idea-refinery, research` (wiki + notebook dropped) |
| `enable digest` | `idea-refinery, research, notebook` |
| `enable open-brain` | `idea-refinery, wiki` |
| `enable open-brain --headless` | `idea-refinery` only |

`enable digest --headless` must still keep `notebook` — it is a `profiles`
entry, not a surface. If it drops it, that is a FAIL and findings §6 explains
why it matters.

**C5 — stack.ps1 does not regress the deployment.**
`scripts/stack/stack.ps1`'s `ob1` row must pass **all four** profiles. Confirm
the file still parses:
`[System.Management.Automation.PSParser]::Tokenize((Get-Content scripts\stack\stack.ps1 -Raw), [ref]$e)`
and that it is ASCII, no BOM, CRLF. **The deliberate divergence from stack.py
is findings §7** — decide for yourself whether the reasoning holds, and say so
either way. The wrong "fix" here is to mark the three `default = true` so the
drivers match; that would make `--headless` a no-op for this plane.

**C6 — `check-project-configs.ps1` from a STAGED state.** The check reads the
git index, so stage first, then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-project-configs.ps1
```

Expect `all 7 compose projects render clean`, `stack-services.json inventory
matches the compose configs`, and clean PS1 parse. **Then break it on purpose**
to prove the check is real: temporarily remove `--profile wiki` from the OB1
render target and confirm it reports nothing new (that is the silent-narrowing
failure mode findings §3 describes), then delete the `openbrain-idea-refinery`
row from `scripts/lib/stack-services.json` and confirm it goes RED with
`MISSING from stack-services.json`. Revert both. A check that cannot be made to
fail is not evidence.

**C7 — inventory rows.** `scripts/lib/stack-services.json` must parse, must
carry `"profile"` on exactly the nine profiled containers plus the new
`openbrain-idea-refinery` row, and the `projects["open-brain"]` entry must list
all four profiles. Cross-check the nine against the compose file's `profiles:`
keys — mismatch = FAIL.

**C8 — `ruff check .`** reports **one** error,
`E501 llm-queue/src/llm_queue/__init__.py:9`, which is **pre-existing** on the
base (`git log --oneline -1 -- llm-queue/src/llm_queue/__init__.py` → `cfa7d4e`,
the plan-store move). Confirm that attribution yourself; if the error is in a
file this branch touched, that is a FAIL.

**C9 — docs.** `.claude/skills/stack-map/references/workspace-stacks.md` OB1
section carries the profile block and the nine `**[profile \`x\`]**` row markers
(same style the inference plane uses for `local`).
`documentation/runbooks/SERVICE-LIFECYCLE.md` gains row 8a. Run `/stack-map` if
you want the drift view.

---

## Group R — the reachability criterion: **WAITING, NOT MET**

The anchor says:

> The OB1 commit's SHA is reachable on the OB1 remote
> (`git -C OB1 branch -r --contains <sha>` lists
> `origin/feature/integrated-knowledge-system`) BEFORE the ai-stack gitlink
> moves; a gitlink pointing at an unreachable commit FAILS the item outright.

**Per DECISIONS D4 the push is a human action, so this item does not push and
does not bump the gitlink.** The tester's job is to verify the item stopped in
the right place, which is the opposite assertion:

**R1 — the OB1 commit exists locally and is NOT on any remote.**

```bash
git -C <worktree>/OB1 log -1 --format=%H work/sl-ob1-profiles
#   -> 17cb1572a7f9350df11283ba9172b9ee7e47a6a0
git -C <worktree>/OB1 branch -r --contains 17cb1572a7f9350df11283ba9172b9ee7e47a6a0
#   -> EMPTY. Any remote branch listed here means something was pushed: FAIL.
git -C <worktree>/OB1 branch -r --contains 5005197
#   -> origin/feature/integrated-knowledge-system  (the parent, still the pin)
```

**R2 — the ai-stack gitlink is still 5005197.**

```bash
git -C <worktree> ls-tree HEAD OB1
#   -> 160000 commit 5005197dd3ae85a2a4aba8bf142084cac2824759  OB1
git -C <worktree> log --format=%H -1 -- OB1
#   -> must NOT be a commit from this branch
```

`git -C <worktree> status --short` will show ` M OB1` because the submodule
worktree HEAD moved. That is expected and correct: the gitlink in the **index
and tree** is unchanged. Confirm with
`git -C <worktree> show --stat HEAD` that no commit on `work/sl-ob1-profiles`
touches the `OB1` path.

**R3 — what the operator has to do.** The OB1 branch `work/sl-ob1-profiles`
(commit `17cb1572a7f9350df11283ba9172b9ee7e47a6a0`) must be pushed to the OB1
remote onto `feature/integrated-knowledge-system`, by a human. Only after
`git -C OB1 branch -r --contains 17cb157` lists
`origin/feature/integrated-knowledge-system` may a follow-up commit
`git add OB1` in ai-stack. **Do not do this as part of testing.**

---

## Group D — nothing was deployed

**D1 — the live set is unchanged.** `docker ps --filter
"label=com.docker.compose.project=open-brain" --format "{{.Names}}" | sort`
must return the same 30 names as the developer recorded before the work (they
are the C3 `render.txt` list). Compare `docker ps` uptimes too: a container
whose uptime resets during testing means something in this plan restarted it,
which is itself a FAIL of the plan.

**D2** — confirm no `.stack/state.json` was created inside the worktree
(`ls <worktree>/.stack` → absent). It is gitignored, but its presence would
mean a case ran without `--state`.
