# Test plan — `sl-ob1-gitlink`

Item: bump the OB1 gitlink `5005197` -> `fe3e045` (the commit carrying the
research / wiki / notebook compose profiles) and make the manifest, the inventory
and the docs true for that pin.

Branch `work/sl-ob1-gitlink`, cut from `development` at `f9b18f2`.
Anchor: `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-gitlink.json`.
Findings: `documentation/notes/stack-layers-sl-ob1-gitlink-findings.md`.

**Attempt 2.** Attempt 1 (`bdec3ab`) FAILED T12 on two class-2 findings, and all twelve
cases passed as written — the plan was the defect. Both gaps are now cases:
**T13 renders the driver's default profile set** instead of letting the plan assert a
count derived by arithmetic (it was wrong: 23, not 22), and **T14 measures whether a
profile-less `stop`/`down` reaches profiled containers** in a throwaway project, which
attempt 1's plan declared unmeasurable. Findings §11 is the post-mortem.

## Rules for the tester

- **Touch no container of any EXISTING project.** T1–T13 and T15 are renders
  (`docker compose … config`), label queries (`docker ps`, `docker compose … ps`), git
  commands or dry runs. If one of those seems to need `up`/`down`/`restart`/`stop`, it
  is the wrong case — say so and fail it rather than running it.
  **T14 is the one exception and it is deliberate:** it creates and destroys a
  two-service `busybox` project of its own (`tgl2-profile-probe`) to measure compose
  behaviour. That is not a shared resource. "Touch no container" is a constraint on
  WHICH containers, not a reason to call behaviour unmeasurable — attempt 1 made that
  mistake and it cost the item a cycle (findings §11).
- **Do not edit the live host's `OB1/docker/.env`.** T7 needs a modified copy; make
  a COPY and pass it with `--env-file`.
- **Work in a scratch clone for T1–T13 and T15**, not in the developer's worktree, so
  the reachability property is proved rather than inherited. `C:\gl\t` (short path —
  OB1 has deep paths; `core.longpaths=true` is set on the clone command below). T14
  needs no clone — it runs in its own throwaway directory.
- Numbers in this plan are the developer's measurements. Your job is to reproduce
  them, not to accept them. Any disagreement is a FAIL, including a disagreement
  that makes the deliverable look better.

## Scratch clone (setup for T1–T13 and T15; T14 needs none)

```bash
git -c core.longpaths=true clone --recurse-submodules "D:\Open WebUI\ai-stack" C:\gl\t
cd C:\gl\t
git checkout work/sl-ob1-gitlink
git submodule update --init
```

If the clone or `submodule update` cannot fetch, STOP — that is T1 failing, and
nothing below is meaningful.

**Then give the clone a host shape.** Every file below is GITIGNORED, so a clone has
none of them; the developer hit each one, and each refusal is PRE-EXISTING (verified
to behave identically at `5005197`). Seeding them is setup, not a finding:

```bash
cp "D:\Open WebUI\ai-stack\OB1\docker\.env" OB1/docker/.env   # or seed from OB1/docker/.env.example
# compose needs these two to EXIST; empty is enough for a render
touch OB1/recipes/daily-digest/.env OB1/recipes/email-history-import/.env
cp .env.example .env                                          # root: the anchor plane
for p in inference frontend search memory coder portal; do cp $p/.env.example $p/.env; done
cp agent-org/docker/.env.example agent-org/docker/.env
```

Symptoms if you skip them, so they are not misread as failures of this item:

| Missing | Symptom |
|---|---|
| `OB1/recipes/*/.env` | `inventory --check` REFUSES: "`config --profiles` exited 1 ... env file ...\.env not found". Note `config --services` exits 0 on the same tree - only `--profiles` fails, which is why T2/T3 can pass while T5 refuses |
| root `.env` | `inventory --check` FAILS `projects.ai-stack` (`file: null` vs `docker-compose.yml`) - the anchor plane could not be rendered |
| `<plane>/.env` | T8's `init --product research` REFUSES, naming the blank keys. Seeding the examples still leaves `LITELLM_MASTER_KEY` and `MULLVAD_WG_ADDRESSES` blank; append any non-blank placeholder to `inference/.env` and `search/.env`. **Placeholders only - never a real key in a scratch clone** |
| `agent-org/docker/.env` | `inventory --check` prints `[ -- ] NOT VERIFIED - agent-org` (non-fatal) |

**Delete `C:\gl\t` when you are done** - it holds a copy of the host's OB1 env.

---

## T1 — [FIRST] the pinned commit is reachable from a fresh recursive clone

Why first: this is the one property the whole item rests on. CLAUDE.md's rule is
"never bump the gitlink to a commit that isn't on the OB1 remote"; a clone that
cannot resolve the pin is a broken repo for everyone who clones it.

```bash
git -c core.longpaths=true clone --recurse-submodules "D:\Open WebUI\ai-stack" C:\gl\t
cd C:\gl\t && git checkout work/sl-ob1-gitlink && git submodule update --init
git rev-parse HEAD:OB1
git -C OB1 rev-parse HEAD
git -C OB1 fetch origin
git -C OB1 merge-base --is-ancestor fe3e045 origin/feature/integrated-knowledge-system; echo $?
```

**Expect:** `HEAD:OB1` = `fe3e04512f1b2a0a59cb2e11ccef9fe7a991fa19`; the submodule
checkout is at the same sha; `--is-ancestor` exits **0**.

**Disproves it:** a clone that errors fetching the submodule; a `HEAD:OB1` that is
still `5005197…`; `--is-ancestor` exiting 1 (the pin is not on the remote branch —
that is a hard FAIL and the item must not merge).

**Note:** the developer measured the remote TIP to BE `fe3e045`, so `--is-ancestor`
is trivially satisfied today. Run the ancestor form anyway: the tip can move.

## T2 — the bare OB1 render is 20 services

```bash
cd C:\gl\t
docker compose -f OB1/docker/docker-compose.yml config --services 2>bare.err | sort > bare.txt
wc -l < bare.txt ; cat bare.err
```

**Expect:** `20`.

**stderr:** empty ONLY if `OB1/docker/.env` is the host's real file. Seeded from
`OB1/docker/.env.example` you get ~16 `level=warning … variable is not set` lines
(`OB_APP_MEMORY_PASSWORD`, `SURREAL_USER`, `SURREAL_PASSWORD`,
`OPEN_NOTEBOOK_ENCRYPTION_KEY`). Interpolation warnings cannot change WHICH services
render, so the count stands either way. Judge the count; note the warnings.

**Disproves it:** 29 or 30 (the submodule is not actually at `fe3e045`); any other
number (the developer's count is wrong and every doc sentence built on it is too).

## T3 — all four profiles render 30, and they are the 30 that are running

```bash
docker compose -f OB1/docker/docker-compose.yml \
  --profile research --profile wiki --profile notebook --profile idea-refinery \
  config --services 2>all.err | sort > all.txt
wc -l < all.txt ; cat all.err
docker ps -a --filter "label=com.docker.compose.project=open-brain" --format "{{.Names}}" | sort > live.txt
wc -l < live.txt
diff all.txt live.txt && echo IDENTICAL
comm -13 bare.txt all.txt     # the ten profiled names
```

**Expect:** `30`; stderr as T2 (empty with the host's real env file, interpolation
warnings with the example); `live.txt` also 30; `diff` clean; the ten profiled
names are `open-notebook-backup`, `open_notebook`, `openbrain-curator`,
`openbrain-idea-refinery`, `openbrain-research`, `openbrain-wiki`,
`openbrain-wiki-backup`, `openbrain-wiki-viewer`, `openbrain-workbench`, `surrealdb`.

**Disproves it:** a `diff` that is not clean — the render and the running set have
come apart, which means landing this WOULD change the deployment.

## T4 — per-profile membership matches every description the manifest gives

```bash
for p in idea-refinery research notebook wiki; do
  echo "== $p"
  docker compose -f OB1/docker/docker-compose.yml --profile $p config --services 2>/dev/null \
    | sort | comm -13 bare.txt -
done
```

**Expect:** `idea-refinery` -> `openbrain-idea-refinery` (21 total);
`research` -> `openbrain-curator`, `openbrain-research` (22);
`notebook` -> `open-notebook-backup`, `open_notebook`, `surrealdb` (23);
`wiki` -> `openbrain-wiki`, `-wiki-backup`, `-wiki-viewer`, `-workbench` (24).

Then read the four `description =` strings under `[planes.ob1.profiles.*]` in
`stack.manifest.toml` against those lists.

**Disproves it:** a description naming a service its profile does not gate, or
omitting one it does — e.g. if `research` still claimed the grounding backfiller
(it is core and renders bare), or if `wiki` omitted `openbrain-wiki-backup`.

## T5 — `inventory --check` is clean, with NO `[ ~~ ]` row for ob1

```bash
cd C:\gl\t
python scripts/stack/stack.py inventory --check ; echo "EXIT=$?"
```

**Expect:** `EXIT=0`. The only `[ ~~ ]` line is the `mutually exclusive - openwebui`
one (pre-existing, frontend). **No** `declared, not rendered` line, and none of the
follow-up paragraph about the gitlink bump.

```bash
python - <<'PY'
import json
d = json.load(open('scripts/lib/stack-services.json'))
rows = [r for p in d['planes'].values() for r in p if r.get('project') == 'open-brain']
print(len(rows), sum(1 for r in rows if r.get('profile')))
for r in sorted(rows, key=lambda r: r['container']):
    if r.get('profile'): print(r['container'], '->', r['profile'])
PY
```

**Expect:** 30 open-brain rows, 10 with a `profile`, matching T4's mapping exactly.

**Disproves it:** any `declared, not rendered` line for ob1 (the acceptance
criterion's literal subject); fewer than 10 profiled rows; a row whose `profile`
disagrees with T4.

**Read this before judging:** there is no `rendered` field in the JSON. "rendered
rather than declared, not rendered" is about how the value was DERIVED — see
findings §7. The observable is the absence of the `[ ~~ ]` lines.

## T6 — `inventory --write` is a no-op on the committed file

```bash
python scripts/stack/stack.py inventory --write
git status --short scripts/lib/stack-services.json
```

**Expect:** `already up to date` (or a write producing NO diff); `git status` clean.
**Disproves it:** any diff — the committed generated file does not match what the
generator produces at this pin, i.e. it was hand-edited or generated elsewhere.

## T7 — `COMPOSE_PROFILES` in `OB1/docker/.env` is honoured, and a `--profile` flag REPLACES it

This settles the doc claim that `OB1/docker/.env` is a valid declaration site (D17).

```bash
cd C:\gl\t
cp OB1/docker/.env /tmp/ob1-t7.env
printf '\nCOMPOSE_PROFILES=research,wiki,notebook,idea-refinery\n' >> /tmp/ob1-t7.env
docker compose -f OB1/docker/docker-compose.yml --env-file /tmp/ob1-t7.env config --services 2>/dev/null | sort > env.txt
wc -l < env.txt ; diff env.txt all.txt && echo IDENTICAL
docker compose -f OB1/docker/docker-compose.yml --env-file /tmp/ob1-t7.env --profile research config --services 2>/dev/null | wc -l
```

**Expect:** 30 and `IDENTICAL` for the env-only render; **22** for the second — the
CLI flag replaces the env list rather than adding to it.

**Disproves it:** 20 for the first (compose does NOT read it, and every doc sentence
offering `OB1/docker/.env` as a declaration site is wrong); 30 for the second (the
flag unions, and the warnings written into `stack.ps1` and `emergency-recovery.ps1`
about one flag not being rescuable are wrong).

**Do not** leave `/tmp/ob1-t7.env` in place of the real file, and do not modify the
real file.

## T8 — `init --product research --force` writes the four, and `up --dry-run` passes them

```bash
cd C:\gl\t
rm -f /tmp/t8.json
python scripts/stack/stack.py --state C:/Users/<you>/AppData/Local/Temp/t8.json init --product research --force
python scripts/stack/stack.py --state C:/…/t8.json list
python scripts/stack/stack.py --state C:/…/t8.json up --all --dry-run
cat C:/…/t8.json
```

(`--state` takes a path Python resolves; a Git-Bash `/tmp/...` string is NOT the
same directory Python sees. Use a Windows-style absolute path.)

**Expect:** `init` prints `ob1  profiles: idea-refinery, research, wiki, notebook`;
`list` shows the same four; the state JSON's `planes.ob1.profiles` is exactly those
four; and the OB1 line of `up --all --dry-run` is

```
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d
```

**Disproves it:** fewer than four flags on that line — the anchor says a dry run
that would start fewer than 30 FAILS. Cross-check by RENDERING, not counting: four
flags -> 30 per T3; the two-flag default -> 23 per **T13**.

## T9 — the driver's DEFAULT (no ob1 state) is two profiles, not four

The honest half of T8: the plan must not claim the driver does this on its own.

```bash
rm -f C:/…/t9.json
python scripts/stack/stack.py --state C:/…/t9.json up ob1 --dry-run
```

**Expect:** `--profile idea-refinery --profile research` only (the `default` plus its
`requires` closure). **T13 renders that set and settles its count — do not take one
from here.**
**Disproves it:** four flags (then the "declare it once" instruction in CLAUDE.md,
the stack-map, SERVICE-LIFECYCLE and `stack.ps1` is unnecessary and misleading).

Also confirm the host premise the docs assert:

```bash
cat "D:\Open WebUI\ai-stack\.stack\state.json" | grep -c '"ob1"'      # expect 0
grep -c COMPOSE_PROFILES "D:\Open WebUI\ai-stack\OB1\docker\.env"     # expect 0
```

**Expect:** both `0` — which is what "neither is set on this host yet" claims.

## T10 — `check-project-configs.ps1` on the whole staged delta, with the coverage assertion RUNNING

```powershell
cd C:\gl\t
git reset --soft HEAD~2        # restage this branch's two commits, or: git add -A after a fresh checkout
git add -A
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\check-project-configs.ps1
```

**Expect:** exit 0 AND these lines present:

```
  [configs] all 9 compose projects render clean
  [configs] stack-services.json inventory matches the compose configs [rows verified/expected: … open-brain:30/30]
```

**Disproves it:** the run ending after `parse clean` with no render lines — that is
the hole findings §6 describes, and it means the gate fix did not take.

To prove the gate is load-bearing, swap in the BASE version of that one script and
re-run against the SAME staged delta:

```bash
cp scripts/checks/check-project-configs.ps1 /tmp/cfg-fixed.ps1
git show f9b18f2:scripts/checks/check-project-configs.ps1 > scripts/checks/check-project-configs.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-project-configs.ps1
cp /tmp/cfg-fixed.ps1 scripts/checks/check-project-configs.ps1
```

**Expect:** the base version exits **0** and prints ONLY the two inventory lines and
the two parse/json lines - no `all 9 compose projects render clean`, no
`rows verified/expected`. Green while verifying nothing is the whole point.
**Disproves it:** the base version printing the render lines anyway (then the gate
change is decoration, not a fix).

The `NOT VERIFIED: project 'agent-org'` line is expected and pre-existing.

## T11 — the remaining gates

```bash
cd C:\gl\t
ruff check .                                         # expect: All checks passed!
python -m pytest scripts/stack/test_stack.py -q      # expect: 107 passed
git log --format='%H %s' -2                          # both messages end with the Co-Authored-By trailer
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\check-hook-attestation.ps1 -Branch work/sl-ob1-gitlink -Base development
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\checks\validate-lineendings.ps1
# stray CR, EXCLUDING documentation/evidence/** (see below)
git diff development...work/sl-ob1-gitlink -- . ':(exclude)documentation/evidence/**' | grep -c $'\r'
```

**Expect:** ruff clean; tests green; every commit attested; **0** stray CR outside
`documentation/evidence/`.

**`documentation/evidence/**` is excluded BY DESIGN, not waived.**
`documentation/evidence/.gitattributes` sets `* -text` ("EVIDENCE IS BYTES … store and
check out verbatim"), so the CRs in this plan file are the intended bytes; merged
siblings do the same (`sl-frontend-solo/test-plan.md`, `regempty/test-plan.md`).
Attempt 1's plan said "expect 0" over the whole diff and was wrong about the repo, not
about the branch. If you see CRs in any file OUTSIDE that directory, that IS a failure.

**Disproves it:** any non-zero exit. Note `ruff check .` was GREEN on the base
`f9b18f2` (findings §9) — a failure here belongs to this branch.

## T12 — every sentence this item introduces, and what settles it

Read each cell's claim against the command in the last column, at `fe3e045`. A
sentence that no longer matches is a FAIL even if everything else passes — that is
the acceptance criterion the anchor writes out in full.

| # | File | The claim | Settled by |
|---|---|---|---|
| 1 | `CLAUDE.md` OB1 row | gitlink is `fe3e045`, from the commit that added the profiles, reachable on `origin/feature/integrated-knowledge-system` | T1 |
| 2 | `CLAUDE.md` OB1 row | bare render 20; `research` +2, `wiki` +4, `notebook` +3, `idea-refinery` +1; all four 30 | T2, T3, T4 |
| 3 | `CLAUDE.md` OB1 row | the operator declares the four "either in the driver state … or as COMPOSE_PROFILES in OB1/docker/.env — both measured" | T7, T8 |
| 4 | `CLAUDE.md` OB1 row | "the driver unions the env's list into its own flags" | T7 + `effective_profiles` in `scripts/stack/stack.py` |
| 5 | `CLAUDE.md` OB1 row | "Neither is set on this host yet, so `stack.py up ob1` … starts **23** (20 + 1 + 2), seven short" | T9 **and T13** |
| 6 | `CLAUDE.md` OB1 row | still "30 containers" | T3 (`live.txt` = 30) |
| 7 | `stack.manifest.toml`, above the three profile tables | the per-profile deltas and the all-four total | T2, T3, T4 |
| 8 | same | "This host's .stack/state.json … does not list `ob1` at all" | T9 |
| 9 | same | "wiki and notebook gate SEVEN running containers that `up` would no longer start" | T4 (4 + 3) and T9 |
| 10 | same | both declaration sites work and compose (`effective_profiles` unions) | T7, T8 |
| 11 | `stack.manifest.toml` `idea-refinery` description | `default = true`, so both drivers pass it; stack.ps1 is a shim | `grep default stack.manifest.toml` at that table; `head -5 scripts/stack/stack.ps1` |
| 12 | `stack.manifest.toml` `pending` doc | "No plane is in that state today" | T5 (no `declared, not rendered` line for ANY plane) |
| 13 | `stack.manifest.toml` research/wiki/notebook descriptions | each names exactly the services its profile gates | T4 |
| 14 | `scripts/stack/stack.ps1` header | bare 20 / idea-refinery+research **23** / all four 30 | T2, T3, **T13** |
| 15 | `scripts/stack/stack.ps1` header | "a CLI --profile REPLACES COMPOSE_PROFILES … neither is done by this script" | T7 second render; `grep -n profile scripts/stack/stack.ps1` shows no `--profile` in the body |
| 16 | `scripts/stack/stack.py` `unpinned_profiles` docstring | "returns the empty set for every plane today" | T5 |
| 17 | `scripts/stack/README.md` | "The bump has happened"; bare 20 / four 30 / no-state **renders 23** | T2, T3, **T13** |
| 18 | `scripts/stack/README.md` | frontend's `stock`/`gpu`/`tailscale` are live in `frontend/docker-compose.yml` | `grep -n 'profiles:' frontend/docker-compose.yml` |
| 19 | `scripts/stack/test_stack.py` comments | the two-profile default renders **23** of the 30 | **T13** + T3 |
| 20 | `scripts/lib/stack-services.curated.json` `_comment` | all TEN profiled open-brain rows are derived from the render; no row is a bare declaration | T5 |
| 21 | `SERVICE-LIFECYCLE.md` row 8a | all four in the pinned gitlink; bare render 20 vs 30 | T2, T3 |
| 22 | `SERVICE-LIFECYCLE.md` row 8a | the D17 declaration sites; that `up ob1` RENDERS **23**, "seven short of the 30"; and "render the set, never add up the deltas" | T7, T8, **T13**, T3 |
| 23 | `SERVICE-LIFECYCLE.md` profile-gating section | OB1's four are "all four carried by the pinned gitlink since `fe3e045`" | T1, T4 |
| 24 | stack-map §2 profiles block | the six-row render table | T2, T3, T4 |
| 25 | stack-map §2 profiles block | "a bare `up` starts 20, not 30: ten containers are now gated, seven of them ones no driver default passes" | T2, T3, T9 |
| 26 | stack-map §2 profiles block | "Neither is set on this host yet (… `.stack/state.json` does not list the `ob1` plane at all)" | T9 |
| 27 | stack-map §2 profiles block | "with all four in the env file, `--profile research` alone renders 22" | T7 |
| 28 | stack-map §2, "The full set is all four" para | "at `fe3e045` … that pair renders **23** and four render 30" | T3, **T13** |
| 29 | stack-map §2 Volumes | four volumes, "unchanged by the 2026-09-20 bump" | `docker compose -f OB1/docker/docker-compose.yml --profile research --profile wiki --profile notebook --profile idea-refinery config --format json` -> top-level `volumes` keys |
| 30 | `emergency-recovery.ps1` `Start-OB1Stack` comment | "or this starts 21 of the 30 it just said it would start"; bare 20, all four 30 | T4 (`idea-refinery` alone = 21), T2, T3; `$Script:OB1Services` has 30 entries |
| 31 | `emergency-recovery.ps1` comment | "A CLI --profile REPLACES COMPOSE_PROFILES … it has to carry them itself" | T7 |
| 32 | `check-project-configs.ps1` header | a gitlink bump stages only `OB1` and matches no `*.yml`, so the compose half never ran | T10's revert-and-rerun |
| 33 | findings §5 | the watchdog's `up -d surrealdb` / `up -d open_notebook` still resolve | `docker compose -f OB1/docker/docker-compose.yml config surrealdb` etc., exit 0 |
| 34 | findings §5 | bare `docker compose … ps` lists all 30 at `fe3e045` | `docker compose -f OB1/docker/docker-compose.yml ps --format '{{.Service}}' \| wc -l` -> 30 |
| 35 | the commit message | says what moved, what the commit carries, and where it is reachable | `git log -1 --format=%B 679a963` + T1 |
| 36 | `check-project-configs.ps1` header | "the gitlink bump that took OB1's bare render from **29** services to 20" | check out OB1 at `5005197` in the SCRATCH CLONE only, `config --services \| wc -l` -> 29; restore to `fe3e045`. (Attempt 1 shipped this sentence with no claim row; the tester measured it and it is TRUE) |
| 37 | `emergency-recovery.ps1` `Reset-OB1Stack` | "on the `down` half it is the difference between tearing the project down and leaving part of it behind for the `up` to collide with" | **T14** |
| 38 | `emergency-recovery.ps1` `$Script:OB1Profiles` header | bare `stop` reaches 20 of 30; bare `down` removes 20 and then fails the network drop with `Resource is still in use` | **T14** |
| 39 | same | "`ps` is a LABEL query … bare and profiled both report all 30" | `ps --format json \| wc -l` bare and with all four -> 30 both |
| 40 | same | "eight of them" — every OB1 compose invocation in the file uses the one list | `grep -c 'docker compose -f $Script:OB1Compose @prof' scripts/recovery/emergency-recovery.ps1` -> **8**; and `grep -n 'docker compose -f $Script:OB1Compose' …` shows no invocation without `@prof` |
| 41 | same | splatting passes the flags as separate arguments under PS 5.1 | `$p=@('--profile','research','--profile','wiki'); docker compose -f OB1\docker\docker-compose.yml @p config --services` -> 26 (20+2+4) |
| 42 | `SERVICE-LIFECYCLE.md` row 8a + stack-map §2 | the LANDING STEP is BOTH `COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` in `OB1/docker/.env` AND the driver state | T7 (env honoured), T8 (state), **T14** (env repairs a bare verb) |
| 43 | same | "`frontend/.env` carries `gpu,tailscale`, `inference/.env` carries `local`", OB1 has no such line | `grep -h '^COMPOSE_PROFILES=' frontend/.env inference/.env OB1/docker/.env` on the HOST (read-only) |
| 44 | same | with all four in `OB1/docker/.env`, **`--headless` becomes a no-op for this plane** | **T15** |
| 45 | findings §5a | seven of the ten gated containers hold endpoints on `ai-stack_llm-net`/`app-net`/`default` | `docker inspect` (read-only) on the ten names from T3 |
| 46 | findings §11 | attempt 1's 22 was `--profile research` alone reused for a different set | **T13** + the `--profile research` row of T4 |

**Anything NOT claimed, and must not be read into the deliverable:** that
`agent-org`'s 16 inventory rows are verified (they are not, and the check says so on
every run); that the live plane was reconfigured (it was not — the landing is the
orchestrator's, and `COMPOSE_PROFILES` is still absent from the host's
`OB1/docker/.env`); that OB1's own `.env.example` was updated (it is inside the
submodule and deliberately untouched).

Attempt 1's block also fenced off "whether a bare `docker compose down` reaches
profiled containers", calling it unmeasurable. **That fence is gone and the question
is T14.** A scope fence may say "out of scope"; it must never say "unmeasurable"
unless the impossibility has itself been tested. See findings §11.

---

## T13 — RENDER the driver's default profile set (do not compute it)

This case exists because attempt 1 did not have it. The plan asserted "= 22" for
`idea-refinery + research` from arithmetic, the author's arithmetic was wrong, and six
of this table's rows told the tester to confirm the wrong number.

```bash
cd C:\gl\t
docker compose -f OB1/docker/docker-compose.yml \
  --profile idea-refinery --profile research config --services 2>/dev/null | sort > two.txt
wc -l < two.txt
comm -13 bare.txt two.txt      # what those two profiles add
comm -13 two.txt all.txt       # what is still missing vs all four
docker compose -f OB1/docker/docker-compose.yml --profile research config --services 2>/dev/null | wc -l
```

**Expect:** `two.txt` is **23**; it adds `openbrain-curator`, `openbrain-idea-refinery`,
`openbrain-research`; the set still missing is exactly **seven** —
`open-notebook-backup`, `open_notebook`, `openbrain-wiki`, `openbrain-wiki-backup`,
`openbrain-wiki-viewer`, `openbrain-workbench`, `surrealdb`. The last command is **22**
— that is `--profile research` ALONE, the number attempt 1 misfiled.

**Disproves it:** 22 for the two-flag render (then the correction is itself wrong);
a missing set that is not exactly the seven (then "seven short" is wrong everywhere);
23 for `--profile research` alone (then T4's table is wrong).

**Rule this case encodes, and the reason it is worth a case of its own:** a delta table
summarises renders; it is not a calculator. Render the set you are about to describe.

## T14 — does a profile-less `stop` / `down` reach a profiled container?

Attempt 1 said this "cannot be measured without stopping them". It can, in a throwaway
project, in about two minutes, touching nothing real. **Do not run any compose verb
against the `open-brain` project.**

```bash
mkdir -p C:/tgl2/probe && cd C:/tgl2/probe
cat > docker-compose.yml <<'YML'
name: tgl2-profile-probe
services:
  core:
    image: busybox
    container_name: tgl2-probe-core
    command: ["sh", "-c", "sleep 3600"]
  gated:
    image: busybox
    container_name: tgl2-probe-gated
    profiles: ["extra"]
    command: ["sh", "-c", "sleep 3600"]
YML
docker compose --profile extra up -d
docker compose stop                      ; docker ps -a --filter name=tgl2-probe
docker compose --profile extra start
docker compose --profile extra stop      ; docker ps -a --filter name=tgl2-probe
docker compose --profile extra up -d
docker compose down                      ; docker ps -a --filter name=tgl2-probe
docker network ls --filter name=tgl2-profile-probe
docker compose --profile extra down
# and the env-file route:
docker compose --profile extra up -d ; printf 'COMPOSE_PROFILES=extra\n' > .env
docker compose down                      ; docker ps -a --filter name=tgl2-probe
# cleanup - REQUIRED
docker compose --profile extra down -v ; cd / ; rm -rf C:/tgl2
```

**Expect:**
- bare `stop` -> only `tgl2-probe-core` stops; `tgl2-probe-gated` stays **running**
- `--profile extra stop` -> **both** stop
- bare `down` -> only core removed, gated **still running**, and
  `Network tgl2-profile-probe_default Removing` followed by
  **`Resource is still in use`** — the network does not drop
- `--profile extra down` -> both removed, `Network … Removed`
- with `COMPOSE_PROFILES=extra` in `.env`, the **bare** `down` removes both and drops
  the network

**Disproves it:** a bare `stop`/`down` reaching the gated container (then rows 37/38
are wrong and the `emergency-recovery.ps1` change is unnecessary); the network dropping
after a bare `down` with the gated container still up (then the
`Invoke-NuclearRecovery` argument is wrong).

**Why it matters here, concretely:** `Stop-OB1Stack` and `Invoke-NuclearRecovery` both
carry comments saying they run FIRST so the root `docker compose down` can drop
`ai-stack_llm-net`; row 45 shows seven of OB1's ten gated containers hold endpoints on
those anchor networks. Confirm the fix landed:
`grep -c 'docker compose -f $Script:OB1Compose @prof' scripts/recovery/emergency-recovery.ps1`
-> **8**, with no OB1 invocation lacking `@prof` (row 40).

## T15 — the `--headless` no-op the landing step buys

The consequence the docs now state out loud. Run it in the SCRATCH CLONE, against the
clone's own `OB1/docker/.env`; never the host's.

```bash
cd C:\gl\t
printf '\nCOMPOSE_PROFILES=research,wiki,notebook,idea-refinery\n' >> OB1/docker/.env
rm -f C:/…/t15.json
python scripts/stack/stack.py --state C:/…/t15.json init --product research --headless --force
python scripts/stack/stack.py --state C:/…/t15.json up ob1 --dry-run
# restore the clone's env file afterwards
```

**Expect:** `init` prints `# --headless: dropped surface profiles ob1:wiki,
ob1:notebook` and writes `idea-refinery, research` — and `up ob1 --dry-run` then emits
**all four** `--profile` flags anyway, because `effective_profiles()` unions the plane
env's list into the driver's. The driver says it dropped them and passes them.

**Disproves it:** two flags on the dry-run line (then the `--headless` warning in
SERVICE-LIFECYCLE row 8a and the stack-map section is false and must come out).
