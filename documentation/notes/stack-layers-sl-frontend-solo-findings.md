# Findings — stack-layers `sl-frontend-solo` (frontend compose profiles)

**Written** 2026-09-19, from worktree `wt-sl-frontend-solo` (branch
`work/sl-frontend-solo`, base `development` b28cbc5).
**Anchor** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-frontend-solo.json`.

Each entry says **how it is known**: *read from source* (file:line), *observed
live* (what was run, when — and it has not been re-run since), or *not
verifiable from this tree*.

---

## 1. Compose can gate a SERVICE on a profile. It cannot gate a FIELD.

*Observed live, 2026-09-19, Docker Compose v5.3.0, throwaway projects in a
scratch directory (all containers/networks labelled
`ai-stack.harness.owner=wt-sl-frontend-solo`, all removed).*

Five behaviours were measured, because the whole design of the artifact turns
on them:

| # | Question | Answer |
|---|---|---|
| a | Does compose read `COMPOSE_PROFILES` out of `--env-file`? | **Yes** — a file containing `COMPOSE_PROFILES=gpu` activated the `gpu` service with no CLI flag. |
| b | Does a CLI `--profile` ADD to that value or REPLACE it? | **Replaces.** env file `stock` + `--profile gpu --profile tailscale` rendered gpu and tailscale and **not** stock. |
| c | Can a `deploy.resources.reservations.devices` list be blanked by an unset variable? | **No.** A literal `devices: []` does vanish from the render (it renders as `reservations: {}`), but interpolation reaches scalars only — no variable can produce an empty list, and `count: 0` leaves the block present. |
| d | Can two active services share a `container_name`? | **No** — `docker compose config` exits 1 with `container name "x" is already in use by service ...`. This is the mechanism that makes `stock`+`gpu` a loud mistake rather than a quiet one. |
| e | Can an always-on service `depends_on` a profile-disabled one? | **Only with `required: false`.** Without it, `config` exits 1: `service "backup" depends on undefined service "gated": invalid compose project`. With it, the project renders, **and the `service_healthy` wait is still enforced when the named service IS active** (measured: dependant started 28 s after the dependency, immediately after `Healthy`). |

Consequence, and it is the whole design: the acceptance criteria run the **same
env file** through both renders and differ only in the `--profile` flags, so
service activation is the only lever compose offers. `build:` and the device
reservation therefore cannot live on one service that is sometimes GPU and
sometimes not. Two service definitions sharing a `container_name` is not a
workaround here — it is the only shape compose supports.

## 2. Acceptance criteria 1 and 2 cannot both be met literally. Declared, not routed around.

*Read from the anchor + observed live.*

- Criterion 1 wants the default render to be **`openwebui` and
  `openwebui-backup`**.
- Criterion 2 wants the `gpu,tailscale` render to differ from `development`'s
  **only** by profiles keys and the image indirection — and `development`'s
  render has a service keyed **`openwebui`**.

By §1 those must be two different service keys, so one of the two criteria
takes a name it did not ask for. **The GPU definition keeps `openwebui`**, and
the fresh-clone one is `openwebui-stock`. The reason is blast radius, not
preference: the service key `openwebui` is addressed by
`scripts/recovery/emergency-recovery.ps1:626` (`restart openwebui`) and `:1074`
(`build --no-cache openwebui`), by
`scripts/backup/restore-from-snapshot.ps1`'s `openwebui` entry (its
`Stop`/`Start` lists, spent one per `docker compose ... stop/start <service>`), by
`backup/openwebui-restore.sh:9,12` and by
`documentation/runbooks/restore-from-snapshot.md:206`. (`emergency-recovery.ps1:1015`
passes `"openwebui"` too, but as `Start-PlaneStack`'s `$GateContainer` — a
`Wait-ForHealthy` CONTAINER name at :303, not a service key. It is unaffected
either way, and is listed here only because a grep makes it look otherwise.)
Renaming the service key would have
made **every one of those profile-dependent** — each would need to know which
variant is active before it could name a service — whereas naming the new
variant costs nothing outside this plane. The anchor's own goal sentence
("renders and runs exactly as it does today") is the tie-breaker.

The test plan states this conflict as a case rather than testing intent
quietly; the gate decides between amending the anchor and accepting it.

## 3. A missing `COMPOSE_PROFILES` line in `.env` makes `down` lie. This is the migration hazard.

*Observed live, 2026-09-19, throwaway project.* With a profiled service running
and no profile active:

```
docker compose -f t11.yml down
  Container wtslfs-t11-base-1 Removed      <- the unprofiled one
  (wtslfs-t11-gated-1 still running)       <- the profiled one, untouched
```

So on a host whose `.env` value omits the frontend's profiles (this host's FULL
value is `COMPOSE_PROFILES=local,gpu,tailscale` - see 12):

- `docker compose -f frontend/docker-compose.yml --env-file .env up -d` starts
  `openwebui-backup` and nothing else;
- the matching `down` removes `openwebui-backup` and **leaves `openwebui` and
  `tailscale` running** — a teardown that reports success and tore nothing
  down.

The whole-project verbs in the recovery surface are therefore exposed
(line numbers of THIS branch as committed in attempt 2):
`scripts/recovery/emergency-recovery.ps1:1070` (`down`), `:828` and `:1077`
(`up -d`), `:1015` via `Start-PlaneStack` → `:302` (`up -d`), and `:810` via
`Stop-PlaneStack` → `:316` (`stop --timeout`). Each would operate on
`openwebui-backup` alone. `Confirm-FrontendProfiles` (added at `:165`, called
from all four recovery entry points - `:609`, `:767`, `:953`, `:1063`) now logs
an ERROR naming the fix before any of them runs - see the end of this entry.

**The per-service verbs do NOT survive either — except for `openwebui`.**
*Observed live, 2026-09-19, against this file with this host's `.env` (which
has no `COMPOSE_PROFILES`; `grep -c '^COMPOSE_PROFILES=' .env` → 0):*

```
docker compose -f frontend/docker-compose.yml --env-file .env config tailscale
  no such service: openwebui          exit=1
docker compose -f frontend/docker-compose.yml --env-file .env ps tailscale
  no such service: openwebui          exit=1
docker compose -f frontend/docker-compose.yml --env-file .env config openwebui
  name: frontend …                    exit=0
```

Naming a service activates **only that service's own profile**. `tailscale`
carries `network_mode: service:openwebui` and `depends_on: openwebui`, both
naming a service behind the *gpu* profile, so the project will not LOAD at all;
`--no-deps` does not help, because the reference resolves at project load. The
same run against a scratch copy of `.env` with the line added exits 0 for all
three. `openwebui` is the single exception, because the gpu definition
references nothing outside its own profile — so `restart openwebui` and
`build --no-cache openwebui` do survive.

That makes the exposed set much wider than the whole-project verbs. Line
numbers are of THIS branch's files as committed in attempt 2 (they shifted from
the tester's attempt-1 report, which read `18b4901`):
`scripts/checks/stack-watchdog.ps1`'s seven tailscale repair calls -
`:576` and `:585` (`stop`/`start` in `Repair-TailscaleService`), `:604`, `:605`
and `:607` (`stop`/`rm -f`/`up -d` in the same function's harder path), `:1776`
(`up -d --force-recreate --no-deps tailscale`) and `:1783` (`up -d tailscale`),
plus two operator-advice strings at `:481` and `:1848` that print the same
commands; `scripts/recovery/emergency-recovery.ps1:633` (`restart tailscale`);
`scripts/recovery/quick-fixes.bat`'s four - `:87` (`restart tailscale`), `:121`
(`build --no-cache tailscale`), `:122` and `:514` (`up -d tailscale`);
and the `stop tailscale openwebui` recipe documented in
`backup/openwebui-restore.sh:9`. The watchdog pipes every one of its repairs to
`Out-Null`, so they fail **silently**. (quick-fixes.bat and the runbook were
both raised by the attempt-1 tester. The runbook's recipe is NOT in the list
above: it was given `--profile gpu --profile tailscale` by this item, so it is
exempt alongside `scripts/backup/restore-from-snapshot.ps1` - measured, exit 0
against a profile-less env. The .bat is left to its owner - see 12.)

**Attempt 3 failed on exactly that distinction**, and the lesson is worth more
than the sentence: the runbook's flags and the sentence calling it
"profile-less" landed in the SAME commit. **When a fix and a claim about that
fix land together, the row describing the claim has to move with it** - a
caller-list is a snapshot of a moment, and the commit that changes the moment
owns every copy of the list. There were three copies (compose header, stack-map,
here); the fix updated one.

**Attempt 4 then failed on the same rule applied to LINE NUMBERS**, which is the
stricter case: the fix above replaced 16 header lines with 18, and 25 of the 26
`frontend/docker-compose.yml:<line>` citations in `stack.manifest.toml` went
stale by exactly +2 while the manifest's own header (`:38-39`) kept claiming
every one was verified. Nothing in the diff of that commit touched the manifest,
which is exactly why it was missed: **a citation is invalidated by an edit to
the file it points INTO, not by an edit to the file it lives in.**

### The checklist this item earned

Anything editing `frontend/docker-compose.yml`, before the commit:

1. Make every other edit first - prose, code, the note, the plan.
2. **Then, as the LAST step, re-derive every citation of that file** by
   `git grep -n "frontend/docker-compose.yml:"` across the whole tree (not just
   the manifest, and not just the files you touched), and for each hit locate the
   cited CONSTRUCT with a grep and compare the real line to the cited number.
   **Never re-derive by adding the delta** - two of the three defects this item
   hit were off-by-a-constant in a way that arithmetic reproduces perfectly.
3. Re-run the renders, `ruff`, `pytest scripts/stack` and
   `check-project-configs.ps1` after that, because step 2 can itself edit files.

Citations OUTSIDE this item's own scope, found by the tree-wide sweep and
**deliberately not edited** - they are other items' notes and rewriting another
item's evidence is not this item's business. **For sl-readmes' closeout sweep:**

| File:line | Cites | Where the CONSTRUCT is |
|---|---|---|
| `documentation/notes/stack-layers-sl-closeout-findings.md:70` | `- RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT:-2}` | **`frontend/docker-compose.yml:373`** |
| `documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md:104` | the NVIDIA device reservation (`driver: nvidia` at `:261`) | **`frontend/docker-compose.yml:257-263`** |

Both originally cited the pre-profile file (`:210` and `:108-114`), which is
true of the commit they were written against and stale relative to this branch.

**CLOSED by `sl-readmes`, 2026-09-19.** Both notes were re-derived BY CONSTRUCT
rather than by line, and **both already carry the correct numbers above** - they
were fixed in the same pass that landed them, so nothing needed editing there.
What was stale was this table itself: it said `:368` and `:252-258`, the numbers
that were right when this note was written, and the frontend compose file has
grown by five lines since. That is the failure mode this note exists to warn
about, reproduced inside the warning. Re-derive with, and not by counting:

```bash
grep -n 'RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT' frontend/docker-compose.yml
grep -n 'driver: nvidia' frontend/docker-compose.yml
```

**CORRECTION, and how it was got wrong** (attempt 1, caught by the tester).
This entry previously claimed the opposite — that explicit naming survives, so
the per-service repairs were fine. The measurement behind that claim was real
but was taken on a MODEL service with no cross-profile reference (`up -d gated`
on a two-service throwaway), and generalised to `tailscale`, which has two.
*A behaviour measured on a model is a claim about the model.* It also
contradicted §4 in this same note, which was right; a note that disagrees with
itself should have been the tell.

**What was done about it:** the three sentences that carried the false claim
(compose header, stack-map frontend section, this entry) now state the measured
truth; `scripts/backup/restore-from-snapshot.ps1`'s `openwebui` and `tailscale`
entries now pass `--profile gpu --profile tailscale` in their `ComposeArgs`, as
the portal (`internet`) and agent-org (`workers`) entries there already did, so
a restore no longer depends on the `.env` line at all; and
`emergency-recovery.ps1` gained `Confirm-FrontendProfiles`, a non-fatal
preflight that logs an ERROR naming the fix when the plane renders no Open
WebUI service. **It deliberately does not pass the profiles itself** — a fixed
`--profile gpu --profile tailscale` there would be wrong on a `stock` host,
where it would start the CUDA build and reserve an NVIDIA device, the exact
failure this item exists to remove. The profile set is a property of the host
and belongs in that host's `.env`; the restore catalog is the one place that
already describes a single host's deployment by design, which is why the same
edit is right there and wrong in recovery.

**So the line is REQUIRED, and nothing in code can substitute for it.** It is
documented in the compose header, `.env.example`, the stack-map frontend
section and SERVICE-LIFECYCLE. `scripts/checks/check-watchdog-repair-targets.ps1`
is the check that says whether a given host's `.env` is right — see §4.

## 4. `check-watchdog-repair-targets.ps1` becomes profile-sensitive — and that is the right answer.

*Read from source,* `scripts/checks/check-watchdog-repair-targets.ps1:159-208`:
it renders each plane with the project's own `env_file` (here `.env`) and fails
when a managed container's service key is not declared. *Observed live,
2026-09-19,* run on this branch on this host (whose `.env` has no
`COMPOSE_PROFILES`):

```
4. every managed name resolves to a project that can start it
  [FAIL] 'tailscale' resolves to service 'tailscale' in frontend/docker-compose.yml, which does NOT declare it
  [FAIL] 'tailscale-backup' resolves to service 'tailscale-backup' in frontend/docker-compose.yml, which does NOT declare it
REPAIR TARGETS BROKEN: 2 problem(s).   EXIT=1
```

**CORRECTION:** this entry previously predicted `openwebui` and `tailscale`.
The real pair is `tailscale` and `tailscale-backup` — `openwebui` is not in the
watchdog's managed set at all, so nothing resolves it here. That was a
read-from-source prediction stated without running the script; it has now been
run.

The RED is a true finding, not a false alarm: the script exists to answer "can
the watchdog actually start every container it claims to repair", and on a host
missing the `.env` line the answer is no (§3). It is green again once the line
is added, and it is green on `development` (no profiles, so everything is
declared). It is not wired into pre-commit (the script says so at line 18), so
nothing breaks at commit time — **it is the post-merge check for the `.env`
edit**, and both the compose header and the stack-map section now name it as
such.

## 5. `tailscale-backup`'s comment about its network was wrong — and was wrong before this change. FIXED.

*Read from source + observed live.* `frontend/docker-compose.yml` says of
`tailscale-backup`: "has no network attachment (the bind mount is the only
source)". The rendered config, from **`development`'s own file** as well as the
new one, gives it `networks: {default: null}` — i.e. it is attached to
`ai-stack_default`, because a service that declares no `networks:` key gets the
project default. Nothing depends on this (its `HEALTH_TCP` is deliberately
empty), so the behaviour is harmless; the comment is what was false.

**Fixed 2026-09-19 (attempt 2).** The comment now says the service needs no
network for its own work but still GETS the project default, and the stack-map
table records the same. Attempt 1 left it alone as "not this item's work",
which was the wrong call: a false comment in a file already being edited is
cheaper to correct than to carry.

## 6. The upstream image ships CUDA-named env of its own.

*Observed live, 2026-09-19:* inside a container from
`ghcr.io/open-webui/open-webui:v0.11.0` with no CUDA env passed,
`env | grep -i "NVIDIA\|CUDA"` returns `USE_CUDA_DOCKER=false` and
`USE_CUDA_DOCKER_VER=cu128`. Criterion 1's "any ... `USE_CUDA` env present
FAILS" is about the **compose render**, which has none. A tester who greps
inside the running container instead will see two CUDA-named variables that the
compose file did not put there, and `USE_CUDA_DOCKER=false` is the upstream
default rather than a leak.

## 7. Criterion 3's "within its start_period" understates how the healthcheck works.

*Observed live, 2026-09-19.* The stock container (cold, empty data volume)
answered `/health` 200 at **~92 s** after start — past the 60 s `start_period`
— and reached `healthy` with `FailingStreak=0`, never passing through
`unhealthy`. That is correct behaviour, not a near miss: `start_period`
suppresses failure COUNTING, and only `retries × interval` (3 × 15 s) of
failures **after** it produce an unhealthy verdict, so the effective grace is
~105 s. The healthcheck was left at today's values.

If the tester uses a warm `openwebui-data` volume the first 200 comes sooner;
if they score strictly against 60 s on a cold volume, the case will look failed
when nothing is wrong. The test plan therefore asserts *reaches `healthy`,
never `unhealthy`*, and records the timing.

## 8. `cmd /c` eats `^`, so a `docker ps --filter name=^x$` through it is not anchored.

*Observed live, 2026-09-19.*
`cmd /c "docker ps --filter name=^tailscale$ --format {{.Names}}"` returned
`tailscale` **and** `stt-tts-tailscale` — the anchor was consumed by cmd's
escape character before docker saw it. The substring filter plus an exact
`Where-Object { $_ -eq 'tailscale' }` pass returns 1. Both new guards use the
latter form.

*Checked and clean:* `grep -rn 'cmd /c' --include=*.ps1 scripts/ | grep '\^'`
finds no other occurrence of the idiom in this tree (2026-09-19) — the only
hits are the two comments warning about it.

## 9. Not verifiable from this tree

- **That the `gpu,tailscale` deployment still RUNS identically.** What was
  verified is that its rendered config differs from `development`'s by exactly
  seven additive items (the new project-local network; `owui-net` added to
  `openwebui`; `profiles` keys on `openwebui`/`tailscale`/`tailscale-backup`;
  `openwebui-backup`'s `depends_on` gaining `required: false` and the second
  entry; `openwebui-backup` moving from `ai-stack_default` to `owui-net`) with
  **nothing dropped** — no env, mount, healthcheck, label, image or port
  changed. Proving the running behaviour would mean recreating the prod
  frontend, which the anchor puts out of scope and which is a gated deploy.
- **That `entrypoint.sh` behaves identically under the tailscale profile.** It
  was read, not re-run: the `tailscale` service's image, env, mounts, caps,
  healthcheck and `network_mode` are byte-identical to `development`'s in the
  render, and `entrypoint.sh` was not edited. Re-running it means restarting
  the tailnet node.

## 10. The naming choice has a cost in the other direction, and it is written down here.

*Read from source, 2026-09-19.* §2 gave the GPU definition the service key
`openwebui` so that the recovery, restore and backup call sites keep working on
this host. The cost is symmetric and worth naming: on a **`stock`** host the
service key is `openwebui-stock`, so `emergency-recovery.ps1:626`
(`restart openwebui`) and `:1074` (`build --no-cache openwebui`) would exit 1
there, and `scripts/backup/restore-from-snapshot.ps1`'s `openwebui` entry names
the same key.

Nothing is broken today — those scripts describe and drive THIS host, which is
the `gpu,tailscale` deployment, and `Confirm-FrontendProfiles` passes on a
stock host (it accepts either OWUI service). But a future item that makes
recovery run on a stock clone has to resolve the service key per host rather
than hard-code it. The restore catalog says so in a comment at its `openwebui`
entry; recovery does not, because the fix there is a per-host lookup rather than
a line of text. Raised by the attempt-1 tester as C3-2; recorded, not acted on.

## 11. A render error in the profile guards is now LOUD, not merely safe.

*Observed live, 2026-09-19 (attempt 2).* `Test-TailscaleDeployed`
(`scripts/checks/stack-watchdog.ps1:259`) and `stack.ps1 health`'s inline guard
both treated an empty render as "cannot tell → keep checking", which is the
safe direction but said nothing. The trigger is real rather than theoretical:
the compose file carries a fail-loud `WEBUI_SECRET_KEY` guard, so a `.env` that
is missing or lacks that one key renders **nothing at all** —

```
(blank WEBUI_SECRET_KEY in .env)
docker compose -f frontend/docker-compose.yml --env-file .env config --services
  -> ''                                     (empty)
stack.ps1 health
  [warn] the frontend plane rendered NOTHING (docker down, or .env missing/incomplete)
         - cannot tell whether tailscale is deployed, so probing it anyway
  [OK]   frontend: 8 tailnet serve routes
```

Both now emit that WARN and still run the checks. Raised by the attempt-1
tester as C2-2 (correctly classed as not class-1, since the direction was
already safe).

## 12. `COMPOSE_PROFILES` is GLOBAL, and the rebase put two blocks in charge of it.

*Observed live, 2026-09-19 (attempt 3), after rebasing onto `development`
be00d53.* `sl-inference-split` landed an `.env.example` block that also told the
reader to set `COMPOSE_PROFILES` (`#COMPOSE_PROFILES=local`, "uncomment the line
below in your .env") while this branch's frontend block set it to `stock`. Two
blocks, one variable.

The variable is a **single global compose setting**: every plane driven with
`--env-file .env` reads the same value, and each project ignores profile names
it does not declare. A duplicate key in an env file is **last-wins, silently,
exit 0** - measured:

```
printf 'COMPOSE_PROFILES=stock
COMPOSE_PROFILES=local
' > dup.env
docker compose -f dup.yml --env-file dup.env config --services
  s_local                     <- only the LAST assignment survives, exit 0
```

And the live value is one no document stated: `.env:334` is
`COMPOSE_PROFILES=local,gpu,tailscale`. An operator obeying attempt 2's "put
`COMPOSE_PROFILES=gpu,tailscale` in .env" **exactly** would have dropped
`local` and taken `llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue`
and llm-gateway's local model-group registration down with it. That is the
attempt-1 failure class again: an instruction about `.env` stated more
confidently than the environment supports.

**Fixed per orchestrator decision D15:** `.env.example` now carries ONE
authoritative `COMPOSE_PROFILES` section at the top that owns the variable for
all planes - it says the variable is global, lists every profile by plane,
names which planes are NOT part of it (portal passes `internet` on the command
line; agent-org and OB1 load their own env files, so `sl-ob1-profiles` adds
research/wiki/notebook THERE, not here), gives the two canonical values
(`stock` for a fresh clone, `local,gpu,tailscale` for this host), and ships
`stock` active. The inference block keeps its prose and points at that section
instead of assigning the variable again. Every sentence in this branch that
said `gpu,tailscale` alone - compose header, stack-map, the watchdog and
stack.ps1 warnings, the recovery ERROR - now names the full value or points at
the section.

**Left alone, deliberately:** `scripts/recovery/quick-fixes.bat`'s four
tailscale calls (`:87`, `:121`, `:122`, `:514`) have the same profile problem
as the watchdog's. They are not fixed here because fixing them means either
hardcoding this host's profiles into a generic .bat - the thing 10 explains is
wrong for a generic driver - or porting the preflight to batch. The header and
this note name them so the next reader is not surprised. *Also noticed while
reading it (read from source, unrelated to profiles): `quick-fixes.bat:120`
(`docker compose down tailscale`) and `:511` (`docker compose start tailscale`)
carry no `-f`, so they hit the ROOT anchor project, which has had zero services
since Part K.5b. Those two have been broken since 2026-08-21, independently of
anything here.*

## 13. The rebase onto `sl-ob1-profiles` (f2bb38f), and what it did NOT break.

*Observed live, 2026-09-19 (attempt 6).* `development` moved from `be00d53` to
`f2bb38f` while attempt 5 was under test, touching three files this item also
edits. Per D18 the developer rebases and the work is re-tested. What happened:

- **`frontend/docker-compose.yml` is unchanged on `development`**, so every one
  of the 27 citations re-derived in attempt 5 still resolves. Verified by
  re-running the tree-wide sweep and the construct greps AFTER the rebase rather
  than reasoning that it must be fine - the checklist in 3 says the file it
  points INTO is what matters, and "unchanged" is a claim like any other.
- **`scripts/stack/stack.py` (+83: `profile_closure`, `enable_plane_profiles`)
  did not conflict** because this branch never touched it.
- **One conflict, in `scripts/checks/check-project-configs.ps1`**: development
  gave the inference render target `--profile local`, this branch gave the
  frontend target `--profile gpu --profile tailscale`. Both kept - they are the
  same idea applied to two planes, and the resolution says so in one comment
  rather than two.
- **`stack.manifest.toml` auto-merged, and that was the thing to check hardest.**
  `development` still carried the pre-profile frontend rows (`pending = true`
  x2, citing `frontend/docker-compose.yml:111-114`), so a hunk-level merge could
  have left a development-era citation beside a re-derived one - attempt 4's
  failure walking back in. It did not: the frontend block came through wholesale,
  no `pending` row survives outside the key vocabulary, and OB1's four profiles
  (with the `requires` key its item added, and `idea-refinery` correctly
  `default = true`) are intact.

**Checked because a reviewer will ask, and it holds:** the `chat` product
(development's) is `planes = ["frontend"]` with NO `profiles`, described as
"Open WebUI on its own - what a fresh clone gets". With no product profiles and
no `default = true` row, the driver passes no `--profile` for this plane, so
compose falls back to `COMPOSE_PROFILES` from `--env-file .env` - which on a
fresh clone is `.env.example`'s `stock`. The promise holds *because* the value
lives in `.env` rather than in the product row. Had `chat` declared
`frontend = ["stock"]`, enabling it on the operator's GPU host would have passed
`--profile stock`, replaced `local,gpu,tailscale` for that invocation and
collided with the running `openwebui` container - the same reason none of the
frontend's rows is `default = true` (see 12 and the manifest comment).
