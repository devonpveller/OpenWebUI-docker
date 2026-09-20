# Test plan — `sl-ob1-gitlink`

Item: bump the OB1 gitlink `5005197` -> `fe3e045` (the commit carrying the
research / wiki / notebook compose profiles) and make the manifest, the inventory
and the docs true for that pin.

Branch `work/sl-ob1-gitlink`, cut from `development` at `f9b18f2`.
Anchor: `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-gitlink.json`.
Findings: `documentation/notes/stack-layers-sl-ob1-gitlink-findings.md`.

## Rules for the tester

- **Touch no container.** Every case below is a render (`docker compose … config`),
  a label query (`docker ps`, `docker compose … ps`), a git command or a dry run.
  If a case seems to need `up`/`down`/`restart`/`stop`, it is the wrong case — say
  so and fail it rather than running it.
- **Do not edit the live host's `OB1/docker/.env`.** T7 needs a modified copy; make
  a COPY and pass it with `--env-file`.
- **Work in a scratch clone for T1–T6**, not in the developer's worktree, so the
  reachability property is proved rather than inherited. `C:\gl\t` (short path —
  OB1 has deep paths; `core.longpaths=true` is set on the clone command below).
- Numbers in this plan are the developer's measurements. Your job is to reproduce
  them, not to accept them. Any disagreement is a FAIL, including a disagreement
  that makes the deliverable look better.

## Scratch clone (setup for T1–T6, T8–T13)

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

**Expect:** `20`, stderr empty.
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

**Expect:** `30`, stderr empty; `live.txt` also 30; `diff` clean; the ten profiled
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
that would start fewer than 30 FAILS. Cross-check by counting: four flags -> 30 per
T3; two -> 22.

## T9 — the driver's DEFAULT (no ob1 state) is two profiles, not four

The honest half of T8: the plan must not claim the driver does this on its own.

```bash
rm -f C:/…/t9.json
python scripts/stack/stack.py --state C:/…/t9.json up ob1 --dry-run
```

**Expect:** `--profile idea-refinery --profile research` only (the `default` plus its
`requires` closure) = 22 services.
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
git diff development...work/sl-ob1-gitlink | grep -c $'\r'    # expect 0 stray CR in the diff body
```

**Expect:** ruff clean; tests green; every commit attested; no stray CR.
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
| 5 | `CLAUDE.md` OB1 row | "Neither is set on this host yet, so `stack.py up ob1` … starts 22" | T9 |
| 6 | `CLAUDE.md` OB1 row | still "30 containers" | T3 (`live.txt` = 30) |
| 7 | `stack.manifest.toml`, above the three profile tables | the per-profile deltas and the all-four total | T2, T3, T4 |
| 8 | same | "This host's .stack/state.json … does not list `ob1` at all" | T9 |
| 9 | same | "wiki and notebook gate SEVEN running containers that `up` would no longer start" | T4 (4 + 3) and T9 |
| 10 | same | both declaration sites work and compose (`effective_profiles` unions) | T7, T8 |
| 11 | `stack.manifest.toml` `idea-refinery` description | `default = true`, so both drivers pass it; stack.ps1 is a shim | `grep default stack.manifest.toml` at that table; `head -5 scripts/stack/stack.ps1` |
| 12 | `stack.manifest.toml` `pending` doc | "No plane is in that state today" | T5 (no `declared, not rendered` line for ANY plane) |
| 13 | `stack.manifest.toml` research/wiki/notebook descriptions | each names exactly the services its profile gates | T4 |
| 14 | `scripts/stack/stack.ps1` header | bare 20 / idea-refinery+research 22 / all four 30 | T2, T3, T9 |
| 15 | `scripts/stack/stack.ps1` header | "a CLI --profile REPLACES COMPOSE_PROFILES … neither is done by this script" | T7 second render; `grep -n profile scripts/stack/stack.ps1` shows no `--profile` in the body |
| 16 | `scripts/stack/stack.py` `unpinned_profiles` docstring | "returns the empty set for every plane today" | T5 |
| 17 | `scripts/stack/README.md` | "The bump has happened"; bare 20 / four 30 / no-state 22 | T2, T3, T9 |
| 18 | `scripts/stack/README.md` | frontend's `stock`/`gpu`/`tailscale` are live in `frontend/docker-compose.yml` | `grep -n 'profiles:' frontend/docker-compose.yml` |
| 19 | `scripts/stack/test_stack.py` comments | the two-profile default renders 22 of the 30 | T9 + T3 |
| 20 | `scripts/lib/stack-services.curated.json` `_comment` | all TEN profiled open-brain rows are derived from the render; no row is a bare declaration | T5 |
| 21 | `SERVICE-LIFECYCLE.md` row 8a | all four in the pinned gitlink; bare render 20 vs 30 | T2, T3 |
| 22 | `SERVICE-LIFECYCLE.md` row 8a | the D17 declaration sites, and that `up ob1` starts 22 "seven short of the 30 that are running" | T7, T8, T9, T3 |
| 23 | `SERVICE-LIFECYCLE.md` profile-gating section | OB1's four are "all four carried by the pinned gitlink since `fe3e045`" | T1, T4 |
| 24 | stack-map §2 profiles block | the six-row render table | T2, T3, T4 |
| 25 | stack-map §2 profiles block | "a bare `up` starts 20, not 30: ten containers are now gated, seven of them ones no driver default passes" | T2, T3, T9 |
| 26 | stack-map §2 profiles block | "Neither is set on this host yet (… `.stack/state.json` does not list the `ob1` plane at all)" | T9 |
| 27 | stack-map §2 profiles block | "with all four in the env file, `--profile research` alone renders 22" | T7 |
| 28 | stack-map §2, "The full set is all four" para | "at `fe3e045` … two render 22 and four render 30" | T3, T9 |
| 29 | stack-map §2 Volumes | four volumes, "unchanged by the 2026-09-20 bump" | `docker compose -f OB1/docker/docker-compose.yml --profile research --profile wiki --profile notebook --profile idea-refinery config --format json` -> top-level `volumes` keys |
| 30 | `emergency-recovery.ps1` `Start-OB1Stack` comment | "or this starts 21 of the 30 it just said it would start"; bare 20, all four 30 | T4 (`idea-refinery` alone = 21), T2, T3; `$Script:OB1Services` has 30 entries |
| 31 | `emergency-recovery.ps1` comment | "A CLI --profile REPLACES COMPOSE_PROFILES … it has to carry them itself" | T7 |
| 32 | `check-project-configs.ps1` header | a gitlink bump stages only `OB1` and matches no `*.yml`, so the compose half never ran | T10's revert-and-rerun |
| 33 | findings §5 | the watchdog's `up -d surrealdb` / `up -d open_notebook` still resolve | `docker compose -f OB1/docker/docker-compose.yml config surrealdb` etc., exit 0 |
| 34 | findings §5 | bare `docker compose … ps` lists all 30 at `fe3e045` | `docker compose -f OB1/docker/docker-compose.yml ps --format '{{.Service}}' \| wc -l` -> 30 |
| 35 | the commit message | says what moved, what the commit carries, and where it is reachable | `git log -1 --format=%B <tip>` + T1 |

**Anything NOT claimed, and must not be read into the deliverable:** that a bare
`docker compose down` reaches profiled containers (findings §5, not measured — it
would require stopping containers); that `agent-org`'s 16 inventory rows are
verified (they are not, and the check says so on every run); that the live plane was
reconfigured (it was not — the landing is the orchestrator's).
