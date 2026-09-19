# Test plan — `sl-frontend-solo` (frontend compose profiles), attempt 3

**Branch** `work/sl-frontend-solo` · **base** `development` b28cbc5 ·
**developer worktree** `wt-sl-frontend-solo`
**Anchor** the AMENDED anchor (`queue.ps1 -Show -Id sl-frontend-solo`) —
criterion 1 now names `openwebui-stock`, and criterion 5 now covers the repair
paths BEHIND the guard, not only the guard.
**Findings sink** `documentation/notes/stack-layers-sl-frontend-solo-findings.md`
**Attempt 2** passed every case at `1ceb070` and was then REQUEUED by the
reviewer: the rebase onto `development` be00d53 brought this branch into contact
with two things that did not exist at its base - `sl-inference-split`'s own
`COMPOSE_PROFILES` block in `.env.example`, and `stack.manifest.toml`. **T11,
T12 and T13 at the end of this plan cover exactly that contact and were written
by the reviewer, not by me**; T1-T10 are unchanged and still apply.

**Attempt 1** FAILED (`-PlanInadequate`) at `18b4901`. Read
`.git/agent-worktrees/queue/sl-frontend-solo.attempt1.evidence.md` first: it is
a better map of this change than this plan is, and the reason attempt 1 failed
was that its plan **enumerated which of the author's own claims to check, and
the enumeration omitted the false one.** This plan is written against that:
where it lists claims, the list is itself under test — **if you find a claim
the list omits, that omission is a plan inadequacy and you should check the
claim anyway and say so.**

**What kind of evidence this is.** Render-and-compare, one live bring-up (T3),
and — new in attempt 2 — a *command-shape matrix* run against scratch copies of
`.env` with and without the profile line (T5B). The compose restructure has no
RED→GREEN repro; it is a render comparison. The attempt-1 DEFECT does have one,
and T5B is it: the same command shapes exit 1 without the line and 0 with it,
which is what three documents previously denied and now assert.

**Read the branch, not the tree.** `core.autocrlf` rewrites every text file
locally. Where a case reads a file, take it from the blob:
`git show work/sl-frontend-solo:<path>`.

**Lease:** none required. Every case is a render (client-side), a file read, a
scratch env file, or a labelled throwaway container on its own private network.
**Nothing here may touch the running `openwebui`/`tailscale` containers, the
`ai-stack_*` networks, the `:local` tags, or `frontend_openwebui-data`.** No
case may run a real `up`/`down`/`stop`/`start`/`restart` against the `frontend`
project — T5B uses `config` and `ps`, which resolve the project exactly the same
way and change nothing (see its note). If a case seems to need more than that,
it is a plan inadequacy — report it, do not improvise.

---

## Setup

**S1 — the `development` baseline render** (used by T2). Render from a file
**inside `frontend/`**: the compose file's bind mounts are relative (`../config`),
so rendering from a temp directory changes every absolute source path.

```bash
cd <your worktree>
git show development:frontend/docker-compose.yml > frontend/_baseline-development.yml
docker compose -f frontend/_baseline-development.yml --env-file .env.example \
  config --format json > /tmp/base-render.json
python -c "import json,sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2],'w'), sort_keys=True, indent=1)" /tmp/base-render.json /tmp/base-norm.json
rm frontend/_baseline-development.yml     # must not be committed or left behind
```

`docker compose config --format json` is a supported output and compose has
already normalised it (keys expanded, durations and sizes canonicalised); only
key ORDER is left, which the `sort_keys` re-dump fixes. No third-party YAML.

**S2 — two scratch env files** (used by T5B, T6). Never edit the worktree
`.env` in place; make copies.

```bash
cd <your worktree>
grep -c '^COMPOSE_PROFILES=' .env          # record what this host has TODAY
grep -v '^COMPOSE_PROFILES=' .env > /tmp/env.without
cp /tmp/env.without /tmp/env.with && printf '\nCOMPOSE_PROFILES=gpu,tailscale\n' >> /tmp/env.with
```

---

## T1 - The default render is Open WebUI alone: no GPU, no build, no tailscale

*Amended criterion 1.*

```bash
docker compose -f frontend/docker-compose.yml --env-file .env.example config --services | sort
docker compose -f frontend/docker-compose.yml --env-file .env.example config \
  | grep -n -i -E "devices|USE_CUDA|NVIDIA|build:|tailscale" ; echo "grep exit=$?"
docker compose -f frontend/docker-compose.yml --env-file .env.example config --format json \
  | python -c "import json,sys; s=json.load(sys.stdin)['services']['openwebui-stock']; print(s['container_name'], s['image'], s.get('ports'), sorted(s['environment']), list(s['networks']))"
```

**PASS** when the service list is exactly `openwebui-backup` and
`openwebui-stock`; the grep prints nothing (exit 1); and `openwebui-stock`
renders `container_name: openwebui`, `image:
ghcr.io/open-webui/open-webui:v0.11.0`, published port `3000`, an `environment`
of exactly `TZ` and `WEBUI_SECRET_KEY`, and `owui-net` as its only network.
Confirm too that the data volume is still `openwebui-data`.

**FAIL** if `tailscale`, `tailscale-backup`, a `deploy.resources...devices`
block, a `build:` block, or any `USE_CUDA*`/`NVIDIA_*` key appears; if the
container name, port or volume changed; or if `openwebui-stock` attaches to any
`ai-stack_*` network.

## T2 - The `gpu,tailscale` render loses nothing from `development`

*Criterion 2. This is the case that protects the live deployment.*

```bash
docker compose -f frontend/docker-compose.yml --env-file .env.example \
  --profile gpu --profile tailscale config --format json > /tmp/new-render.json
python -c "import json,sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2],'w'), sort_keys=True, indent=1)" /tmp/new-render.json /tmp/new-norm.json
diff -u /tmp/base-norm.json /tmp/new-norm.json
```

**PASS** when the four service KEYS match the baseline (`openwebui`,
`openwebui-backup`, `tailscale`, `tailscale-backup`) and the diff is exactly
these seven items:

1. top-level network `owui-net` (`name: frontend_owui-net`) added;
2. `openwebui.networks` gains `owui-net` (keeps `default`, `llm-net`, `app-net`);
3. `openwebui` gains `profiles: [gpu]`;
4. `openwebui-backup.depends_on.openwebui.required` `true` → `false`;
5. `openwebui-backup.depends_on` gains `openwebui-stock` (`required: false`);
6. `openwebui-backup.networks` `default` → `owui-net`;
7. `tailscale` and `tailscale-backup` gain `profiles: [tailscale]`.

**FAIL** on any other difference, and specifically on any REMOVAL beyond items
4 and 6 — a dropped env key, mount, port, healthcheck field, label, capability,
`tmpfs`, `security_opt`, `deploy` block or network. Also FAIL if
`openwebui.image` is no longer `openwebui:local`, or if `openwebui`'s `build`
block or nvidia device reservation is missing.

Then the source-level removals (attempt 1's tester decomposed the render leaf by
leaf; do at least this):

```bash
git diff development...work/sl-frontend-solo -- frontend/docker-compose.yml | grep '^-' | grep -v '^---'
```

**PASS** when every `-` line is a comment rewrite or one of the two changes
above. **FAIL** if a `-` line removes a setting no `+` line restores.

## T3 - A fresh clone with only Docker gets Open WebUI, with no GPU

*Criterion 3. The only case that starts a container.* Labelled container and
network, non-prod name, private network, throwaway volume, port ≠ 3000.

```bash
docker network create --label ai-stack.harness.owner=<your-wt-id> <your-wt-id>-net
docker volume  create --label ai-stack.harness.owner=<your-wt-id> <your-wt-id>-data
docker run -d \
  --name <your-wt-id>-owui \
  --label ai-stack.harness.owner=<your-wt-id> \
  --network <your-wt-id>-net \
  -p 127.0.0.1:3900:8080 \
  -v <your-wt-id>-data:/app/backend/data \
  -v "<abs path to your worktree>/config:/app/config:ro" \
  -e TZ=America/New_York \
  -e WEBUI_SECRET_KEY=<any scratch value> \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:noexec,nosuid,size=100m \
  --health-cmd 'curl -f --max-time 10 http://localhost:8080/health' \
  --health-interval 15s --health-timeout 15s --health-retries 3 --health-start-period 60s \
  ghcr.io/open-webui/open-webui:v0.11.0

for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:3900/health || echo 000)
  echo "$(date -u +%FT%TZ) attempt=$i http=$code"; [ "$code" = "200" ] && break; sleep 5
done
curl -s http://127.0.0.1:3900/health; echo
docker inspect <your-wt-id>-owui --format 'State={{.State.Status}} Health={{.State.Health.Status}} FailingStreak={{.State.Health.FailingStreak}} Restarts={{.RestartCount}}'
docker inspect <your-wt-id>-owui --format 'DeviceRequests={{.HostConfig.DeviceRequests}} Runtime={{.HostConfig.Runtime}} Privileged={{.HostConfig.Privileged}}'
```

**CLEANUP — run even if the case fails, and paste the output:**

```bash
docker rm -f <your-wt-id>-owui ; docker network rm <your-wt-id>-net ; docker volume rm <your-wt-id>-data
docker ps -a      --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Names}}'
docker network ls --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
docker volume ls  --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
```

**PASS** when `/health` answers `200 {"status":true}`, the container reaches
`Health=healthy` with `FailingStreak=0` and 0 restarts, `DeviceRequests=[]`,
`Runtime=runc`, `Privileged=false`, and all three cleanup listings are empty.

**FAIL** if it exits or restarts, never answers 200, ever reports `unhealthy`,
needed a GPU to start, or leaves anything labelled behind.

**Do not score against the 60 s `start_period` alone.** Cold, on an empty
volume, both the developer and attempt-1's tester measured the first 200 at
~92 s, healthy and never unhealthy — correct, because `start_period` only
suppresses failure counting and the effective grace is `start_period +
retries × interval` (~105 s). Assert *reaches healthy, never unhealthy*, and
record your timing.

## T4 - EVERY claim the compose header makes

*Criterion 4. Attempt 1 failed here because the plan named three of the
header's claims and the fourth was false.* Read the whole header from the blob
and check each claim below. **The list is under test too**: if the header
asserts something not listed, that is a plan inadequacy — check it anyway and
report the omission.

```bash
git show work/sl-frontend-solo:frontend/docker-compose.yml | sed -n '1,95p'
```

| # | Claim (header lines, as committed) | How to check |
|---|---|---|
| H1 | Three profiles `stock` / `gpu` / `tailscale`, and what each owns | compare with the `profiles:` keys in the file's four services |
| H2 | `stock` = fresh clone; `cp .env.example .env` + `up -d` gives OWUI on 127.0.0.1:3000 with only Docker; needs none of the `ai-stack_*` networks | T1 + T3 |
| H3 | `gpu` = this host: CUDA build, device reservation, `USE_CUDA`/`NVIDIA_*`, `llm-net` + `app-net`, status-pipe mount | T2's render of `openwebui` |
| H4 | `tailscale` requires `gpu`, because `network_mode: service:openwebui` names that service | `--profile tailscale` alone → exit 1, `depends on undefined service "openwebui"` |
| H5 | This plane's profiles are `gpu,tailscale`, are NOT the default, and go in a GLOBAL `COMPOSE_PROFILES` owned by ONE section of `.env.example`; **this host's full value is `local,gpu,tailscale`** and `gpu,tailscale` alone would drop the inference backends | T11; S2 files; `--env-file /tmp/env.with config --services` → four services |
| H6 | A CLI `--profile` REPLACES `COMPOSE_PROFILES` rather than adding | `--env-file .env.example --profile gpu config --services` → `openwebui` + backup, NOT `openwebui-stock` |
| H7 | Without the line, `up -d` starts `openwebui-backup` and nothing else | `--env-file /tmp/env.without config --services` → one service |
| H8 | Without the line, `down` leaves `openwebui` and `tailscale` running | T5C (throwaway project) |
| H9 | Without the line, ANY verb naming `tailscale` — `up -d`/`stop`/`start`/`restart`/`rm`/`ps`/`config`, and `--force-recreate --no-deps` — exits 1 with `no such service: openwebui` | **T5B** |
| H10 | Why: naming a service activates only ITS profile; `network_mode`/`depends_on` then point outside the project; `--no-deps` does not help because the reference resolves at project load | T5B + a model project |
| H11 | `... openwebui` survives — `restart openwebui`, `build --no-cache openwebui` | T5B |
| H12 | The affected callers are the watchdog's seven tailscale repair calls (+2 advice strings), `emergency-recovery.ps1`'s whole-project verbs and its `restart tailscale`, and `backup/openwebui-restore.sh:9`; silent because piped to `Out-Null` | **T5D** (grep each, count them, read the pipes) |
| H13 | `check-watchdog-repair-targets.ps1` reports `tailscale` / `tailscale-backup` NOT DECLARED, and is the check to run after editing `.env` | **T5E** |
| H14 | `restore-from-snapshot.ps1` is the one caller that does NOT depend on the line — its frontend and tailscale entries pass the profiles, as the portal and agent-org entries already did | **T5F** |
| H15 | Compose gates a SERVICE, not a FIELD; `build:`/`devices` cannot be blanked by an unset var; a fresh clone would get an unwanted CUDA build then `could not select device driver "nvidia"` | attempt 1's tester verified this (X-b: an unset var degrades `devices` to `count: -1`, i.e. ALL GPUs). Re-verify or cite |
| H16 | Both render criteria run the same env file and differ only in `--profile` flags | read the amended anchor |
| H17 | The two definitions share `container_name`, the volume and :3000, so `stock`+`gpu` together is refused by `docker compose config` with "container name ... is already in use" | run it → exit 1 |
| H18 | The GPU definition keeps the key `openwebui` because recovery/backup/restore address it by that key | T7 |
| H19 | The `WEBUI_SECRET_KEY` guard makes a bare `up` fail loud | blank it in a scratch env copy → the render fails (this is also T5G's trigger) |
| H20 | NETNS rule unchanged; `depends_on` encodes openwebui → tailscale inside the project | read `tailscale.depends_on` in T2's render |
| H21 | Seams are `ai-stack_default` / `llm-net` / `app-net`, all external | T2's render |
| H22 | `owui-net` is the only network shared by both openwebui definitions and the always-on backup sidecar; the sidecar's sole network use is `nc -z openwebui 8080` | read `backup/openwebui-backup.sh` END TO END and count its network calls |
| H23 | `required: false` does not weaken the wait when the named service IS active | T9 §1e |
| H24 | Compose does not require an UNUSED external network to exist | model project: declare a nonexistent external network, use none of it, `up` |
| H25 | `tailscale-backup` needs no network but still GETS the project default | T2's render: `tailscale-backup.networks == {default: null}` in BOTH renders |
| H26 | The stock image is the same base the CUDA image is built FROM | `git show work/sl-frontend-solo:Dockerfile.openwebui-gpu | head -1` |
| H27 | `entrypoint.sh` is unchanged by the profile split | `git diff development...work/sl-frontend-solo -- entrypoint.sh dockerfile.tailscale Dockerfile.openwebui-gpu` → empty |
| H28 | `stock` is a PROFILE and not the un-profiled default because `--profile` only ADDS, never subtracts: an un-profiled stock service stays active alongside `openwebui`, and their shared `container_name` then fails the whole render | build it: two services, one un-profiled, one `profiles: [gpu]`, same `container_name`; `--profile gpu config -q` → exit 1, `container name ... is already in use` |
| H29 | A duplicate `COMPOSE_PROFILES` key in an env file is last-wins and silent | T11 |
| H30 | The AFFECTED callers include `quick-fixes.bat`'s four tailscale calls; the EXEMPT ones are `restore-from-snapshot.ps1` AND the frontend recipe in `restore-from-snapshot.md`, both of which pass the profiles themselves | `grep -n tailscale scripts/recovery/quick-fixes.bat` (expect `:87`, `:121`, `:122`, `:514` naming the frontend file); then read `documentation/runbooks/restore-from-snapshot.md:206` and RUN its exact shape against a profile-less env - `docker compose -f frontend/docker-compose.yml --env-file /tmp/env.without --profile gpu --profile tailscale config tailscale` → exit 0. **A caller on the wrong side of that split is a FAIL** - attempt 3 failed here, because the commit that gave the runbook its flags left three copies of the caller-list still calling it profile-less |

**PASS** only when every row holds AND you found no unlisted header claim that
is false. **FAIL** on any false claim, and say which row.

## T5 - Amended criterion 5: the repair paths, the guard, and the guarded thing

Seven sub-cases. **All must pass.** T5A/T5G are the guard; T5B–T5F are the
paths behind it — the half attempt 1's plan never asked about.

### T5B — the command-shape matrix, with and without the profile line

*This is the core of the amended criterion.* Run **every shape** below against
both scratch env files from S2 and record the exit code of each.

`config <svc>` and `ps <svc>` resolve the project exactly as `up`/`stop`/
`restart` do — the failure under test happens at PROJECT LOAD, before any verb
runs — so they are the safe stand-ins for the destructive verbs against the
live plane. Prove that equivalence once on a labelled throwaway project that
copies the shape (a profiled `app`, a profiled `ts` with `network_mode:
service:app` + `depends_on: app`), where you MAY run the destructive verbs:

```bash
for ENV in /tmp/env.without /tmp/env.with; do
  echo "=== $ENV ==="
  for ARGS in "config tailscale" "ps tailscale" "config openwebui" "ps openwebui" \
              "config tailscale-backup" "config openwebui-backup" "config --services"; do
    docker compose -f frontend/docker-compose.yml --env-file "$ENV" $ARGS >/dev/null 2>&1
    echo "  $ARGS -> exit=$?"
  done
done
```

```bash
# the destructive verbs, on a labelled MODEL of the same shape
cat > /tmp/model.yml <<'EOF'
name: <your-wt-id>-m
services:
  app:
    profiles: [gpu]
    image: alpine:3.21
    labels: { ai-stack.harness.owner: <your-wt-id> }
    command: ["sh","-c","sleep 300"]
  ts:
    profiles: [tailscale]
    image: alpine:3.21
    labels: { ai-stack.harness.owner: <your-wt-id> }
    network_mode: "service:app"
    depends_on: { app: { condition: service_started } }
    command: ["sh","-c","sleep 300"]
EOF
for V in "up -d ts" "up -d --force-recreate --no-deps ts" "stop ts" "start ts" "restart ts" "rm -f ts" "ps ts" "config ts"; do
  COMPOSE_PROFILES=tailscale docker compose -f /tmp/model.yml $V >/dev/null 2>&1; echo "  (tailscale only) $V -> exit=$?"
done
COMPOSE_PROFILES=gpu,tailscale docker compose -f /tmp/model.yml up -d >/dev/null 2>&1; echo "  (both) up -d -> exit=$?"   # control
COMPOSE_PROFILES=gpu,tailscale docker compose -f /tmp/model.yml down -v >/dev/null 2>&1
docker ps -a --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Names}}'   # expect empty
```

**PASS** when, against the real file: with `/tmp/env.without`, every shape that
names `tailscale` exits **1** and the `openwebui` shapes exit **0**; with
`/tmp/env.with`, **every** shape exits **0**. And when, on the model, the
destructive verbs behave the same as `config`/`ps` — so the stand-in is
justified. Record the full matrix.

**FAIL** if any shape exits non-zero **with** the line (that is the amended
criterion's explicit FAIL), or if `config`/`ps` disagree with the destructive
verbs on the model (which would make this case's method invalid — report it as
a plan inadequacy rather than guessing).

### T5C — `down` without the line leaves the profiled containers

On a labelled throwaway project only (never the `frontend` project):

```bash
cat > /tmp/t8.yml <<'EOF'
name: <your-wt-id>-t
services:
  base:  { image: alpine:3.21, command: ["sh","-c","sleep 120"], labels: { ai-stack.harness.owner: <your-wt-id> } }
  gated: { profiles: [gpu], image: alpine:3.21, command: ["sh","-c","sleep 120"], labels: { ai-stack.harness.owner: <your-wt-id> } }
EOF
COMPOSE_PROFILES=gpu docker compose -f /tmp/t8.yml up -d
docker compose -f /tmp/t8.yml down
docker ps -a --filter name=<your-wt-id>-t --format '{{.Names}}'
docker rm -f <your-wt-id>-t-gated-1 ; docker network rm <your-wt-id>-t_default
```

**PASS** when `down` removes `base` and leaves `gated` running. **FAIL** if it
removes both (the documented hazard would then be false).

### T5D — the header's caller list is complete and the failures are silent

```bash
git show work/sl-frontend-solo:scripts/checks/stack-watchdog.ps1 | grep -n 'docker compose -f frontend'
git show work/sl-frontend-solo:scripts/recovery/emergency-recovery.ps1 | grep -n 'FrontendCompose --env-file'
git show work/sl-frontend-solo:backup/openwebui-restore.sh | grep -n 'docker compose'
```

**PASS** when the watchdog's calls that NAME `tailscale` number exactly seven
repair calls plus two operator-advice strings (the header's count), each repair
one is piped to `Out-Null` (so a non-zero exit is invisible), and
`emergency-recovery.ps1` + `backup/openwebui-restore.sh` carry the calls the
header names. **FAIL** if the count is wrong, if a call the header omits also
names `tailscale`, or if the findings note's line numbers (§3) do not match the
committed file.

### T5E — the repo's own regression guard agrees

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\checks\check-watchdog-repair-targets.ps1"
```

**PASS** when, on a host whose `.env` lacks the line, it reports exactly
`tailscale` and `tailscale-backup` NOT DECLARED and exits 1 — matching findings
§4 (which attempt 1 got wrong, predicting `openwebui`+`tailscale`). Then repeat
with the line present, by whatever means does not mutate the operator's `.env`
— the script reads `.env` via `stack-services.json`'s `env_file`, so this may
not be runnable without editing it. **If you cannot run the second half without
mutating `.env`, say so and score only the first half — that is a plan
inadequacy on my side, not a scoped pass.**

### T5F — `restore-from-snapshot.ps1` no longer depends on the line

```bash
git show work/sl-frontend-solo:scripts/backup/restore-from-snapshot.ps1 | grep -n "ComposeArgs"
git diff development...work/sl-frontend-solo -- scripts/backup/restore-from-snapshot.ps1
git show work/sl-frontend-solo:scripts/backup/restore-from-snapshot.ps1 | sed -n '310,330p;430,450p'
```

**PASS** when the `openwebui` and `tailscale` catalog entries carry
`ComposeArgs = @('--env-file','.env','--profile','gpu','--profile','tailscale')`;
when the portal (`internet`) and agent-org (`workers`) entries are unchanged and
show the same convention; and when the `$cargs` splat at the stop and start
phases really does place those flags before the verb (read both call sites).
Then prove it works, against the real compose file, with the profile-less env:

```bash
docker compose -f frontend/docker-compose.yml --env-file /tmp/env.without --profile gpu --profile tailscale config tailscale >/dev/null 2>&1; echo "exit=$?"
```

**PASS** when that exits 0 — i.e. the catalog's own flags are sufficient without
the `.env` line. **FAIL** if it exits 1, or if the flags land after the verb.

### T5G — `emergency-recovery.ps1` says so loudly instead of doing nothing

The coordinator asked whether recovery should pass the profiles too. It does
NOT, deliberately; it CHECKS. Verify both the behaviour and the reason.

```powershell
Set-Location "<worktree>"
function Write-Log { param([string]$Level,[string]$Message) Write-Host "[$Level] $Message" }
$Script:FrontendCompose = "frontend\docker-compose.yml"
$src = Get-Content 'scripts\recovery\emergency-recovery.ps1' -Raw
Invoke-Expression ([regex]::Match($src,'(?ms)^function Confirm-FrontendProfiles \{.*?^\}')).Value
Confirm-FrontendProfiles      # with this host's .env (no COMPOSE_PROFILES)
```

Then re-run it with `COMPOSE_PROFILES=gpu,tailscale` and with
`COMPOSE_PROFILES=stock` — **on a scratch copy**; if the function's hardcoded
`--env-file .env` forces you to edit the worktree `.env`, back it up first and
restore it, and say in your evidence that you did.

**PASS** when: it returns `$false` and logs an `[ERROR]` naming
`COMPOSE_PROFILES=gpu,tailscale` and `check-watchdog-repair-targets.ps1` when
the plane renders no OWUI service; returns `$true` for BOTH `gpu,tailscale` and
`stock`; returns `$true` with a WARN when the render fails; is non-fatal (the
recovery paths continue); and is called from all four recovery entry points:

```bash
git show work/sl-frontend-solo:scripts/recovery/emergency-recovery.ps1 | grep -n 'Confirm-FrontendProfiles'
```

**FAIL** if it is fatal, if it returns `$false` on a `stock` host (that would
break the fresh-clone deployment this item exists for), if any recovery entry
point does not call it, or if the function passes `--profile` flags itself —
**and judge the reasoning in its comment**: passing fixed `gpu,tailscale` in a
generic driver would start a CUDA build and reserve a GPU on a stock host. If
you think that reasoning is wrong, say so; it is a judgement call the gate
should see.

### T5A — the observers SKIP rather than FAIL when the profile is absent

```powershell
$env:DOCKER_HOST='tcp://127.0.0.1:1'
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\stack\stack.ps1" health
Remove-Item Env:\DOCKER_HOST
```

**PASS** when the output contains `[skip] frontend: 8 tailnet serve routes (no
tailscale profile in this deployment)` and no `[FAIL]` about tailnet routes.
(Other probes FAIL — the daemon is unreachable; that is the setup.)

Normal daemon, this host (`tailscale` running, `.env` without the line):

**PASS** when it prints `[warn] tailscale is RUNNING but absent from the
frontend render ...` and STILL runs the probe.

The watchdog's half, against the artifact's own bytes (it cannot be executed —
any mode runs a full repair cycle against the live stack):

```powershell
Set-Location "<worktree>"
function Write-LogEntry { param([string]$Message,[string]$Level='INFO') Write-Host "LOG[$Level] $Message" }
$src = Get-Content 'scripts\checks\stack-watchdog.ps1' -Raw
Invoke-Expression ([regex]::Match($src,'(?ms)^function Test-TailscaleDeployed \{.*?^\}')).Value
"A: $(Test-TailscaleDeployed)"                    # real daemon, container running
$script:TailscaleDeployedCache = $null
$env:DOCKER_HOST='tcp://127.0.0.1:1'
"B: $(Test-TailscaleDeployed)"                    # no daemon, no container
Remove-Item Env:\DOCKER_HOST
```

**PASS** when A is `True` (with the WARN on a host lacking the line) and B is
`False`. Then read the call site:

```bash
git diff -w development...work/sl-frontend-solo -- scripts/checks/stack-watchdog.ps1
```

**PASS** when `if (Test-TailscaleDeployed) {` opens immediately before the
tailscale-container block and its `} else {` logs a skip AFTER
`Repair-TailscaleServes` — so the guarded span covers container
health/recreate, `Test-NetworkConnectivity` + `Repair-TailscaleService`,
`Test-TailscaleConnection` + `Repair-TailscaleService`,
`Test-TailscaleNodeState` and `Repair-TailscaleServes` — and when the
whitespace-ignored diff shows nothing removed from that span.
`Test-HostTailscaleBackend` sitting OUTSIDE is correct (Windows app, not a
container this project owns), not a miss.

### T5H — a render error must never become a silent skip

*New in the amended criterion (attempt-1 finding C2-2).* The compose file's
fail-loud `WEBUI_SECRET_KEY` guard makes an incomplete `.env` render nothing.

```bash
sed 's/^WEBUI_SECRET_KEY=.*/WEBUI_SECRET_KEY=/' /tmp/env.with > /tmp/env.nokey
docker compose -f frontend/docker-compose.yml --env-file /tmp/env.nokey config --services ; echo "exit=$?"
```

Then drive both guards into that state. The functions hardcode `--env-file
.env`, so this needs a backed-up-and-restored worktree `.env`; say in your
evidence that you did it, and prove the restore (`grep -c` the key afterwards).

**PASS** when the render is empty, and BOTH guards then (a) emit a WARN naming
the render failure and (b) still treat tailscale as deployed and run the
checks. **FAIL** on a silent fall-through either way, and FAIL on a skip.

## T6 - The gates pass, and `stack-services.json` still matches the render

```powershell
cd <your own worktree>
$sha = git rev-parse HEAD
git reset --soft HEAD~1          # index + working tree untouched; stages this commit
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\checks\check-project-configs.ps1"
git reset --soft $sha
git status --porcelain           # expect empty
```

(Attempt 1's tester used `git switch --detach b28cbc5` + `git cherry-pick -n`
in their own worktree instead — equally good; use whichever you can restore
from. Never `git add -A`, and never stage in the developer's worktree.)

```bash
ruff check .
python -m pytest scripts/stack -q
git diff development...work/sl-frontend-solo --name-only | grep '\.py$'      # expect none
```

**PASS** when `check-project-configs.ps1` exits 0 with "all 8 compose projects
render clean" (the frontend counted twice — default and `gpu,tailscale`) and
"stack-services.json inventory matches the compose configs"; when `ruff check .`
is CLEAN; when `pytest scripts/stack` is green (38 tests at the time of writing
— the branch edits `stack.manifest.toml`, which that suite loads); and when the
branch touches no `.py` file.

Attempts 1 and 2 carried one pre-existing `E501` in
`llm-queue/src/llm_queue/__init__.py:9` and this plan used to tell you to expect
it. The rebase onto `be00d53` picked up the fix (that line is 68 chars on
`development` now), so there is no longer an expected error to excuse.
**FAIL on any ruff finding**, any failing driver test, or a `.py` file in the
diff.

## T7 - The naming choice, and its cost in both directions

The amended anchor accepts `openwebui-stock`. What still needs checking is the
JUSTIFICATION, and the cost the choice imposes the other way.

```bash
grep -n "restart openwebui\|build --no-cache openwebui" scripts/recovery/emergency-recovery.ps1
git show work/sl-frontend-solo:scripts/backup/restore-from-snapshot.ps1 | sed -n '96,115p'
grep -n "openwebui" backup/openwebui-restore.sh | head
```

**PASS** when those really do address the compose SERVICE key `openwebui` (so
renaming it would have made each profile-dependent); when the `gpu,tailscale`
render's container names are still exactly `openwebui`, `tailscale`,
`openwebui-backup`, `tailscale-backup`; and when findings §10 records the
symmetric cost — that on a `stock` host those same call sites name a key that
does not exist there. **FAIL** if any justification is false, if a container
name changed, or if §10 is missing or overstates what was checked.

## T8 - The migration hazard is documented, consistently, in all four places

Attempt 1 failed here: three of the four documents described the survivable
half wrongly. Read all four and check them **against T5B's matrix**, not
against each other.

```bash
git show work/sl-frontend-solo:frontend/docker-compose.yml | sed -n '22,60p'
git show work/sl-frontend-solo:.env.example | sed -n '1,40p'
git show work/sl-frontend-solo:.claude/skills/stack-map/references/workspace-stacks.md | sed -n '/PROFILE-GATED since/,/adding to it/p'
git show work/sl-frontend-solo:documentation/runbooks/SERVICE-LIFECYCLE.md | sed -n '/When you PROFILE-GATE a service/,$p'
```

**PASS** when all four say: the line is REQUIRED; `up -d` starts only the
backup; `down` leaves openwebui and tailscale running; **every verb naming
`tailscale` exits 1**, with the reason (naming activates only that service's
profile, and `--no-deps` does not help); `openwebui` is the exception; and each
statement matches T5B's measured exit codes. **FAIL** on any sentence that
contradicts the matrix — including any survivor of attempt 1's "naming a
service explicitly still works past an inactive profile".

**Check the caller lists as LISTS, not as prose.** Each of the four documents
names who is affected and who is exempt. Build the union of both columns across
all four and test every entry against T5B's matrix:

- exempt, because they pass `--profile gpu --profile tailscale` themselves:
  `scripts/backup/restore-from-snapshot.ps1` (T5F) and the frontend recipe in
  `documentation/runbooks/restore-from-snapshot.md:206`;
- affected: the watchdog's seven repairs + two advice strings,
  `emergency-recovery.ps1`'s whole-project verbs and its `restart tailscale`,
  `quick-fixes.bat`'s four, `backup/openwebui-restore.sh:9`;
- neither: `emergency-recovery.ps1` CHECKS rather than passes (T5G).

**FAIL if any entry is on the wrong side in any of the four documents**, even
one. This is the attempt-3 failure: a commit added the flags to the runbook
recipe AND left three copies of the caller-list describing it as profile-less.
A caller-list is a snapshot of a moment, and the commit that changes the moment
owns every copy.

## T9 - The findings note, in full - no enumeration to hide behind

`documentation/notes/stack-layers-sl-frontend-solo-findings.md` has twelve
entries. **Check every one**, not a sample: attempt 1's plan listed four and the
false claim was in the fifth. For each, ask the two questions this repo's
protocol asks — is it TRUE, and is it stated at the strength its own
provenance supports (*read from source* / *observed live* / *not verifiable*)?

- §1 a–e — the five compose behaviours the whole design rests on
- §2 — the criterion conflict and the blast-radius argument
- §3 — the migration hazard, the per-service verbs, **the CORRECTION of
  attempt 1's false claim**, and the line numbers it cites
- §4 — `check-watchdog-repair-targets.ps1`, **and its corrected name pair**
- §5 — `tailscale-backup`'s network comment, now fixed in the file
- §6 — the upstream image's own `USE_CUDA_DOCKER=false`
- §7 — the healthcheck's effective grace vs `start_period`
- §8 — `cmd /c` eats `^`
- §9 — what is NOT verifiable from this tree (judge whether the two items
  really are unverifiable, or whether they are dodges)
- §10 — the naming choice's cost on a `stock` host
- §11 — the render-error WARN
- §12 — `COMPOSE_PROFILES` is global, the rebase gave it two owners, and what
  was deliberately LEFT alone (quick-fixes.bat), including the two bare
  `docker compose` calls there that have hit the empty anchor project since
  K.5b - verify that sub-claim against the file, it is unrelated to profiles

**PASS** when every entry holds at its stated strength, the corrections in §3
and §4 are accurate, and nothing is claimed as *observed live* that was only
read. **FAIL** on any entry that overstates — a false claim in the sink is
worse than one in the deliverable, because the next item acts on it. In
particular: §3 now generalises from `tailscale`'s own measured behaviour rather
than from a model; check that it does not repeat attempt 1's mistake in the
other direction.

## T10 - Nothing was left behind, and the live stack is untouched

```bash
docker ps -a      --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Names}}'
docker network ls --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
docker volume ls  --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E '^(openwebui|tailscale)'
git -C <your worktree> status --porcelain
grep -c '^WEBUI_SECRET_KEY=.\+' <your worktree>/.env      # expect 1, if T5H touched it
```

**PASS** when the three label listings are empty; `openwebui`, `tailscale` and
both backups are still up with the same uptime class as before you started; your
worktree is clean; `frontend/_baseline-development.yml` is gone; and any `.env`
you backed up is restored. **FAIL** on any leftover, any prod container
restarted/recreated, or an unrestored `.env`.

---

# Cases the rebase added (written by the reviewer `wt-reviewer-frontend`)

These three came from the review, not from the developer. They are reproduced
here so `queue.ps1` counts them; the reviewer's original wording is in
`sl-frontend-solo-attempt3-added-cases.md`. Where they name a measured fact,
**verify it yourself** rather than taking it from the page.

## T11 - `.env.example` has exactly ONE authority for `COMPOSE_PROFILES`

`COMPOSE_PROFILES` is a single GLOBAL compose variable, not a per-plane one.

```bash
grep -n "COMPOSE_PROFILES" .env.example
grep -c "^COMPOSE_PROFILES=" .env.example          # expect exactly 1
grep -n "^COMPOSE_PROFILES" "D:/Open WebUI/ai-stack/.env"   # the LIVE value
```

```bash
# duplicate-key semantics, on a throwaway project (one service per profile)
printf 'COMPOSE_PROFILES=stock
COMPOSE_PROFILES=local
' > /tmp/dup.env
docker compose -f /tmp/dup.yml --env-file /tmp/dup.env config --services
```

**PASS** when: `.env.example` assigns the variable in exactly ONE place; that
section enumerates every plane's profiles together (inference `local`; frontend
`stock` | `gpu` | `tailscale`), says which planes are deliberately NOT part of
the value (portal's `internet` is passed by `portal-on.ps1`; agent-org and OB1
load their own env files, so `sl-ob1-profiles` adds nothing here), and gives
the two canonical values; the value it names for THIS host
(`local,gpu,tailscale`) is what the live `.env` actually carries; each plane's
own section describes its profiles but points at the single authority instead of
assigning again; and no document anywhere on the branch tells the operator to
set a value that omits another plane's required profile:

```bash
grep -rn "COMPOSE_PROFILES=gpu,tailscale" --include=*.md --include=*.yml --include=*.ps1 --include=*.bat . | grep -v documentation/evidence
```

**Sweep it unbounded, and classify every hit.** A one-directional grep finds
only the mistake you already know about; attempt 3's tester found the mirror
image (five sentences instructing `COMPOSE_PROFILES=local` ALONE) by looking at
all of them:

```bash
git grep -n "COMPOSE_PROFILES"
```

For each hit say which it is: (a) the single assignment in `.env.example`;
(b) prose naming one of the two canonical values, or pointing at the section;
(c) code reading the variable (`config/litellm/assemble-config.py`,
`inference/compose/gateway.yml`); (d) a measurement or a quotation in a findings
note; or (e) **prose instructing a value that is neither canonical** — which is
the failure.

**FAIL** when: more than one assignment exists (commented or not); or any hit
falls in class (e) — a document telling the operator to set a value that is not
`stock` or `local,gpu,tailscale`, in either direction (dropping `local`, or
dropping `gpu,tailscale`). And **FAIL** if the duplicate-key measurement does
not reproduce as last-wins-silently-exit-0 — that is the fact the whole decision
rests on.

## T12 - `stack.manifest.toml` no longer calls these profiles `pending`

```bash
sed -n '/\[planes.frontend.profiles/,+4p' stack.manifest.toml
python -m pytest scripts/stack -q
```

```bash
# the driver's equivalent of "enable this profile" - `enable` takes a PLANE or
# PRODUCT name, and applies that plane's `default = true` profiles. Use a
# SCRATCH state file: the real one is this host's per-machine enablement.
python scripts/stack/stack.py --state /tmp/st.json enable frontend
python scripts/stack/stack.py --state /tmp/st.json list
rm -f /tmp/st.json
```

**PASS** when: `planes.frontend.profiles.gpu` and `.tailscale` no longer carry
`pending = true`; a `stock` entry exists beside them; every description cites
lines that resolve (T13); and nothing prints the "PENDING profiles enabled … the
compose files do not carry them yet" note from `scripts/stack/stack.py:648-657`.

**Also judge the `default` decision.** None of the three is `default = true`,
deliberately: a `default` profile makes the driver pass `--profile …` on every
invocation, and a CLI `--profile` REPLACES `COMPOSE_PROFILES` rather than adding
to it — so the host's own value would be silently overridden and a `stock` host
handed `gpu`. That is parity with `stack.ps1`, which passes no frontend profile
either. **FAIL** if you think that reasoning is wrong — say so; it is a
judgement the gate should see. A green `pytest scripts/stack` is NOT sufficient
evidence here: 38 tests passed against the un-updated manifest, so the suite
does not cover this.

## T13 - the manifest's `frontend/docker-compose.yml:<line>` citations resolve

The manifest header asserts "EVERY edge below is evidenced in a compose file;
the evidence is cited in the comment above it as `<file>:<line>`". This branch
inserts ~130 lines near the top of that file, so every citation was re-derived.

```bash
grep -n "frontend/docker-compose.yml:" stack.manifest.toml
for n in 113 151 156 163 239 250 256 283 284 295 304 306 307 310 311 312 313 314 316 325 326 328 329 466 474 479; do
  printf "%4s: %s
" "$n" "$(sed -n "${n}p" frontend/docker-compose.yml)"
done
```

**PASS** when every citation in `[planes.frontend]` — the `requires anchor`
networks, the entrypoint override span, the inference / search / ob1 / portal /
agent-org edges, the two `host` entries and all three profile descriptions —
points at the construct the comment beside it names. Check the RANGES too, not
only their first line.

**FAIL** on any that does not. For reference, the pre-fix state: `:290-298`
(cited as the three `external: true` networks) was `LLAMA_CPP_EMBED_ENABLED` and
a caddy comment; `:111-114` (the CUDA image + device reservation) was `ports:` /
`- owui-net`; `:135` (`network_mode`) was a blank line.

**While you are there:** the `host = [...]` entries used to read as a forward
reference ("sl-frontend-solo makes the stock image the default"). They now
describe the present tense and split the requirement by profile — the CUDA image
and NVIDIA runtime are needed under `gpu` only, and the auth key under
`tailscale` only. **FAIL** if a `host` entry still states a requirement the
DEFAULT deployment does not have: that is the line a newcomer reads to decide
whether their machine can run this.
