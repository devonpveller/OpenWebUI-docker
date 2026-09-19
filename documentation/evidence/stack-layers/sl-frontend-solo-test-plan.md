# Test plan — `sl-frontend-solo` (frontend compose profiles)

**Branch** `work/sl-frontend-solo` · **base** `development` b28cbc5 ·
**developer worktree** `wt-sl-frontend-solo`
**Anchor** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-frontend-solo.json`
**Findings sink** `documentation/notes/stack-layers-sl-frontend-solo-findings.md`

**What kind of evidence this is.** Mostly *render-and-compare*, plus one live
bring-up (T3). There is no RED→GREEN repro: the change is a compose
restructure, and the thing to prove is that one render gained a capability
while the other lost nothing.

**Read the branch, not the tree.** `core.autocrlf` rewrites every text file
locally and the operator's checkout may hold edits that are not on this branch.
Where a case reads a file, take it from the blob:
`git show work/sl-frontend-solo:<path>`.

**Lease:** none required. Every case below is a render (client-side), a file
read, or a labelled throwaway container on its own private network. **Nothing
here may touch the running `openwebui`/`tailscale` containers, the `ai-stack_*`
networks, the `:local` image tags, or the prod `frontend_openwebui-data`
volume.** If a case seems to need any of those, it is a plan inadequacy —
report it, do not improvise.

---

## Setup — capture the `development` baseline render (used by T2, T4)

The baseline must be rendered from a file **in `frontend/`**, not a temp
directory: the compose file's bind mounts are relative (`../config`), so
rendering from elsewhere changes every absolute source path and fills the diff
with noise.

```bash
cd <your worktree>
git show development:frontend/docker-compose.yml > frontend/_baseline-development.yml
docker compose -f frontend/_baseline-development.yml --env-file .env.example \
  config --format json > /tmp/base-render.json
```

**Normalisation recipe.** `docker compose config --format json` is a supported
output and is already normalised by compose (keys expanded, durations and sizes
canonicalised); the only thing left is key ORDER, so re-dump it sorted with the
Python standard library — no third-party YAML is needed or assumed:

```bash
python -c "import json,sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2],'w'), sort_keys=True, indent=1)" /tmp/base-render.json /tmp/base-norm.json
```

**Delete `frontend/_baseline-development.yml` when you are done** — it must not
be committed, and it must not be left where a later `docker compose -f frontend/*`
glob could pick it up.

---

## T1 - The default render is Open WebUI alone: no GPU, no build, no tailscale

*Anchor criterion 1.*

```bash
cd <your worktree>
docker compose -f frontend/docker-compose.yml --env-file .env.example config --services | sort
docker compose -f frontend/docker-compose.yml --env-file .env.example config \
  | grep -n -i -E "devices|USE_CUDA|NVIDIA|build:|tailscale" ; echo "grep exit=$?"
```

**PASS** when the service list is exactly two lines — `openwebui-backup` and
`openwebui-stock` — and the grep prints nothing (exit 1). Also confirm by eye
in the full `config` output that `openwebui-stock` has `image:
ghcr.io/open-webui/open-webui:v0.11.0`, no `build`, no `deploy`, and is on
`owui-net` only.

**FAIL** if any of `tailscale`, `tailscale-backup`, a `deploy.resources...devices`
block, a `USE_CUDA*` key, an `NVIDIA_*` key or a `build:` block appears; or if
the render exits non-zero; or if `openwebui-stock` attaches to any `ai-stack_*`
network.

**Declared deviation from the criterion's wording — judge this, do not skip
it.** The criterion says the two services should be **`openwebui`** and
`openwebui-backup`; the render says **`openwebui-stock`** and
`openwebui-backup`. The *container* is still named `openwebui`
(`container_name` in the render). This is deliberate and is the one place the
anchor could not be met literally — see T7, which is the case that decides it.

## T2 - The `gpu,tailscale` render loses nothing from `development`

*Anchor criterion 2. This is the case that protects the live deployment.*

```bash
docker compose -f frontend/docker-compose.yml --env-file .env.example \
  --profile gpu --profile tailscale config --format json > /tmp/new-render.json
python -c "import json,sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2],'w'), sort_keys=True, indent=1)" /tmp/new-render.json /tmp/new-norm.json
diff -u /tmp/base-norm.json /tmp/new-norm.json
```

**PASS** when the service KEYS are identical to the baseline's four
(`openwebui`, `openwebui-backup`, `tailscale`, `tailscale-backup`) and the diff
contains **exactly these seven additive/necessary items and nothing else**:

1. top-level network `owui-net` (`name: frontend_owui-net`) added;
2. `openwebui.networks` gains `owui-net` (keeps `default`, `llm-net`, `app-net`);
3. `openwebui` gains `profiles: [gpu]`;
4. `openwebui-backup.depends_on.openwebui.required` `true` → `false`;
5. `openwebui-backup.depends_on` gains an `openwebui-stock` entry (`required: false`);
6. `openwebui-backup.networks` `default` → `owui-net`;
7. `tailscale` and `tailscale-backup` gain `profiles: [tailscale]`.

**FAIL** on ANY other difference — and specifically on any **removal**: a
dropped env key, mount, port, healthcheck field, label, capability, `tmpfs`,
`security_opt`, `deploy` block or network. `diff -u` shows removals as `-`
lines; there must be none except items 4 and 6 above. Also FAIL if
`openwebui.image` is no longer `openwebui:local`, or if the `build` block or
the nvidia device reservation is missing from `openwebui`.

**Also diff what was REMOVED at the source level**, not only in the render:

```bash
git diff development...work/sl-frontend-solo -- frontend/docker-compose.yml | grep '^-' | grep -v '^---'
```

**PASS** when every `-` line is either a comment being rewritten or one of the
two changes above. **FAIL** if a `-` line removes a setting that no `+` line
restores.

## T3 - A fresh clone with only Docker gets Open WebUI, with no GPU

*Anchor criterion 3. The only case that starts a container.*

Take the definition from T1's render. Rules, all mandatory: a **labelled**
container and network, a **non-prod** name, a **private** network (never an
`ai-stack_*` one), a throwaway volume, and a port that is **not 3000**.

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

# poll until 200 (record the elapsed time)
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:3900/health || echo 000)
  echo "$(date -u +%FT%TZ) attempt=$i http=$code"; [ "$code" = "200" ] && break; sleep 5
done
curl -s http://127.0.0.1:3900/health; echo
docker inspect <your-wt-id>-owui --format 'State={{.State.Status}} Health={{.State.Health.Status}} FailingStreak={{.State.Health.FailingStreak}}'
docker inspect <your-wt-id>-owui --format 'DeviceRequests={{.HostConfig.DeviceRequests}} Runtime={{.HostConfig.Runtime}}'
```

**CLEANUP — run it even if the case fails, and paste the output:**

```bash
docker rm -f <your-wt-id>-owui
docker network rm <your-wt-id>-net
docker volume rm <your-wt-id>-data
docker ps -a     --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Names}}'
docker network ls --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
docker volume ls  --filter label=ai-stack.harness.owner=<your-wt-id> --format '{{.Name}}'
```

**PASS** when `/health` answers `200` with `{"status":true}`, the container
reaches `Health=healthy` with `FailingStreak=0`, `DeviceRequests=[]` and
`Runtime=runc` (it was never given a GPU), and all three cleanup listings come
back empty.

**FAIL** if the container exits or restarts, if `/health` never answers 200, if
it ever reports `Health=unhealthy`, if it needed a GPU/nvidia runtime to start,
or if anything labelled is still present after cleanup.

**Do not score this against the 60 s `start_period` alone.** On a cold, empty
data volume the developer measured the first 200 at ~92 s, with the container
reaching `healthy` and never `unhealthy` — correct behaviour, because
`start_period` only suppresses failure counting and the effective grace is
`start_period + retries x interval` (~105 s). The assertion is *reaches
healthy, never unhealthy*; record your timing either way. (Findings §7.)

## T4 - The compose header tells the truth, and `entrypoint.sh` is untouched

*Anchor criterion 4.*

```bash
git show work/sl-frontend-solo:frontend/docker-compose.yml | sed -n '1,70p'
git diff development...work/sl-frontend-solo --stat
git diff development...work/sl-frontend-solo -- entrypoint.sh dockerfile.tailscale Dockerfile.openwebui-gpu
```

**PASS** when the header states: the three profiles and which deployment each
belongs to; that the operator's deployment is `COMPOSE_PROFILES=gpu,tailscale`
set in `.env` and is NOT the default; that a CLI `--profile` REPLACES that
value; and why there are two `openwebui` definitions. And when the third
command prints nothing — `entrypoint.sh` and both Dockerfiles are unmodified,
so the tailscale profile's behaviour is unchanged, and no Dockerfile was moved
(that is `sl-colo-frontend`'s work).

**FAIL** if any header claim is not true of the file below it, if the
`.env`/profile relationship is not stated, or if `entrypoint.sh`,
`dockerfile.tailscale` or `Dockerfile.openwebui-gpu` appear in the diff.

**Check the stated REASON, not only the claim.** The header asserts three
compose behaviours. Verify each yourself rather than believing the comment
(throwaway projects, labelled, in a scratch directory):

```bash
# a) --profile REPLACES the env file's COMPOSE_PROFILES
docker compose -f frontend/docker-compose.yml --env-file .env.example --profile gpu config --services | sort
#    expect: openwebui, openwebui-backup   (NOT openwebui-stock)

# b) stock + gpu together is refused, loudly
docker compose -f frontend/docker-compose.yml --env-file .env.example --profile stock --profile gpu config -q ; echo "exit=$?"
#    expect: exit 1, 'container name "openwebui" is already in use'

# c) tailscale alone is refused, because it needs openwebui's netns
docker compose -f frontend/docker-compose.yml --env-file .env.example --profile tailscale config -q ; echo "exit=$?"
#    expect: exit 1, 'depends on undefined service "openwebui"'
```

**FAIL** if any of the three behaves differently from what the header says.

## T5 - Observers SKIP the tailnet checks when the profile is absent - they do not FAIL, and do not repair

*Anchor criterion 5.* Three sub-cases; all three must pass.

**T5a — `stack.ps1 health` prints a skip, not a FAIL.** The skip branch needs
a host with no `tailscale` container, which this one has. Reach it without
touching anything by pointing the client at a dead daemon: the compose render
is client-side and still works, while `docker ps` returns nothing.

```powershell
$env:DOCKER_HOST='tcp://127.0.0.1:1'
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\stack\stack.ps1" health
Remove-Item Env:\DOCKER_HOST
```

**PASS** when the output contains
`[skip] frontend: 8 tailnet serve routes (no tailscale profile in this deployment)`
and **no** `[FAIL]` line mentioning tailnet serve routes. (Other probes will
FAIL — the daemon is unreachable. That is the point of the setup, not a
result.) **FAIL** if a tailnet-route FAIL line appears, or if the sweep dies
before reaching that probe.

**T5b — the same guard, normal daemon, says why it is still checking.**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\stack\stack.ps1" health
```

**PASS** when — on a host where a `tailscale` container is running but the
worktree `.env` has no `COMPOSE_PROFILES` — the output contains the `[warn]
tailscale is RUNNING but absent from the frontend render ...` line **and still
runs** the `frontend: 8 tailnet serve routes` probe. A fail-open guard that
went quiet here would be the defect. **FAIL** if the probe is skipped while a
tailscale container is running. (If your host's `.env` DOES carry
`COMPOSE_PROFILES=gpu,tailscale`, expect no warn line and the probe running
normally — also a pass.)

**T5c — the watchdog's guard, both branches, run against the artifact's own
bytes.** `stack-watchdog.ps1` cannot be executed here: any mode runs a full
repair cycle against the live stack. Extract and call just the decision
function instead — this runs the committed text, not a copy:

```powershell
Set-Location "<worktree>"
function Write-LogEntry { param([string]$Message,[string]$Level='INFO') Write-Host "LOG[$Level] $Message" }
$src = Get-Content 'scripts\checks\stack-watchdog.ps1' -Raw
Invoke-Expression ([regex]::Match($src,'(?ms)^function Test-TailscaleDeployed \{.*?^\}')).Value
"A: $(Test-TailscaleDeployed)"                       # real daemon
$script:TailscaleDeployedCache = $null
$env:DOCKER_HOST='tcp://127.0.0.1:1'
"B: $(Test-TailscaleDeployed)"                       # no daemon -> no container
Remove-Item Env:\DOCKER_HOST
```

**PASS** when A is `True` (with the "RUNNING but absent" WARN on a host whose
`.env` lacks the profiles, or silently on one that has them) and B is `False`.

Then read the call site to confirm the guard actually wraps the repairs:

```bash
git diff -w development...work/sl-frontend-solo -- scripts/checks/stack-watchdog.ps1
```

**PASS** when `if (Test-TailscaleDeployed) {` opens immediately before the
tailscale-container block and the matching `} else {` logs a skip AFTER the
`Repair-TailscaleServes` call — i.e. the guarded span covers container
health/recreate, `Test-NetworkConnectivity` + `Repair-TailscaleService`,
`Test-TailscaleConnection` + `Repair-TailscaleService`, `Test-TailscaleNodeState`
and `Repair-TailscaleServes`; and when the whitespace-ignored diff shows
**nothing removed** from that span. **FAIL** if any repair call sits outside the
guard, or if the diff drops a check. `Test-HostTailscaleBackend` is
deliberately OUTSIDE (it is the Windows Tailscale app, not a container this
project owns) — that is correct, not a miss.

## T6 - The gates pass, and `stack-services.json` still matches the render

*Anchor criterion 6.*

`check-project-configs.ps1` only runs its compose gate when something is
STAGED, and on a clean checkout of this branch nothing is — `git add` of an
unchanged file stages nothing, so the check would print "nothing staged - skip"
and prove nothing. Re-stage this branch's own commit instead. `--soft` moves
HEAD and leaves the index and working tree alone, so nothing on disk changes
and the second command puts HEAD back exactly:

```powershell
cd <worktree>
$sha = git rev-parse HEAD            # write this down
git reset --soft HEAD~1              # the commit's files are now staged
powershell -NoProfile -ExecutionPolicy Bypass -File "<worktree>\scripts\checks\check-project-configs.ps1"
git reset --soft $sha                # HEAD restored; `git status` must be clean again
git status --porcelain               # expect: no output
```

(Never `git add -A` in a worktree — that is how one session's sweep picked up
another's dirty gitlink.)

```bash
ruff check .
```

**PASS** when `check-project-configs.ps1` exits 0 and prints "all 8 compose
projects render clean" (it now renders `frontend` **twice** — default and
`gpu,tailscale`) and "stack-services.json inventory matches the compose
configs".

**`ruff check .` is expected to report exactly ONE error, and it is NOT from
this branch:** `E501 Line too long (103 > 100)` in
`llm-queue/src/llm_queue/__init__.py:9`. Verify that for yourself rather than
taking this plan's word:

```bash
git show development:llm-queue/src/llm_queue/__init__.py | sed -n '9p' | awk '{print length}'   # expect 103
git diff development...work/sl-frontend-solo --name-only | grep '\.py$'                          # expect no output
```

**PASS** when the length is 103 on `development` and this branch touches no
`.py` file — the red is pre-existing (introduced by the plan-store path
rewrite) and was deliberately not fixed here, because it belongs to another
plane's file. **FAIL** if `ruff` reports anything else, or if this branch does
touch a `.py` file.

## T7 - The anchor conflict: criterion 1's service name vs the goal's "runs exactly as it does today"

*This case exists because the work showed an acceptance criterion cannot be met
as written. It is here to be DECIDED, not to be quietly passed.*

Criterion 1 requires the default render's services to be `openwebui` and
`openwebui-backup`. Criterion 2 and the goal require the `gpu,tailscale` render
to be `development`'s, whose openwebui service key is `openwebui`. Those must
be two different service keys (compose cannot give one service two shapes, and
refuses two active services sharing a `container_name`), so one of them takes a
different name. The developer gave the **GPU** definition the name `openwebui`
and called the new one `openwebui-stock`.

Verify the reasoning, do not take it on trust:

```bash
# the mechanism: a devices block cannot be blanked by an unset variable
git show work/sl-frontend-solo:frontend/docker-compose.yml | sed -n '/WHY TWO openwebui/,/fresh-clone variant/p'
# the blast radius the choice avoids - these name the SERVICE key `openwebui`
grep -n "restart openwebui\|build --no-cache openwebui" scripts/recovery/emergency-recovery.ps1
sed -n '96,103p' scripts/backup/restore-from-snapshot.ps1
grep -n "openwebui" backup/openwebui-restore.sh | head
```

**PASS** when: (a) the three call-site groups above really do address the
compose SERVICE key `openwebui` and would each have had to become
profile-dependent under the other choice; (b) the container names in the
`gpu,tailscale` render are still exactly `openwebui`, `tailscale`,
`openwebui-backup`, `tailscale-backup`; and (c) the conflict is stated in the
findings note (§2) rather than papered over.

**FAIL** if the deviation is undocumented, if any of those call sites does NOT
in fact use the service key (making the justification false), or if the
`gpu,tailscale` render's container names differ from today's in any way.

**Escalate** the naming itself to the gate: this plan does not get to decide
between amending the anchor's criterion 1 and accepting the developer's
reading.

## T8 - The migration hazard is documented where an operator will hit it

*Not an anchor criterion; it is the one way this change can break the live
deployment, so it gets a case.*

Without `COMPOSE_PROFILES=gpu,tailscale` in `.env`, the frontend project's
whole-project verbs operate on `openwebui-backup` alone. Reproduce it on a
throwaway project (labelled; nothing of the real stack involved):

```bash
cd <a scratch dir>
cat > t.yml <<'EOF'
name: <your-wt-id>-t
services:
  base:
    image: alpine:3.21
    labels: { ai-stack.harness.owner: <your-wt-id> }
    command: ["sh","-c","sleep 120"]
  gated:
    profiles: [gpu]
    image: alpine:3.21
    labels: { ai-stack.harness.owner: <your-wt-id> }
    command: ["sh","-c","sleep 120"]
EOF
COMPOSE_PROFILES=gpu docker compose -f t.yml up -d
docker compose -f t.yml down            # no profile active
docker ps -a --filter name=<your-wt-id>-t --format '{{.Names}}'
docker rm -f <your-wt-id>-t-gated-1; docker network rm <your-wt-id>-t_default
```

**PASS** when `down` leaves the gated container running (proving the hazard is
real) AND all four of these say so in terms an operator meets:

```bash
git show work/sl-frontend-solo:frontend/docker-compose.yml | grep -n -A6 "WITHOUT THAT LINE"
git show work/sl-frontend-solo:.env.example | sed -n '1,32p'
git show work/sl-frontend-solo:.claude/skills/stack-map/references/workspace-stacks.md | sed -n '/PROFILE-GATED since/,/adding to it/p'
git show work/sl-frontend-solo:documentation/runbooks/SERVICE-LIFECYCLE.md | sed -n '/When you PROFILE-GATE a service/,$p'
```

**FAIL** if any of the four omits the consequence, or if one of them describes
it wrongly (e.g. claims `down` still tears the plane down). **FAIL** also if
the `stop`/`down`/`up` call sites named in findings §3 do not match the script:

```bash
sed -n '253,280p' scripts/recovery/emergency-recovery.ps1     # Start-PlaneStack / Stop-PlaneStack bodies
sed -n '1008,1020p' scripts/recovery/emergency-recovery.ps1   # the nuclear down/build/up trio
```

## T9 - The findings note is held to the artifact's standard

Every claim in `documentation/notes/stack-layers-sl-frontend-solo-findings.md`
is labelled *read from source* / *observed live* / *not verifiable from this
tree*. Spot-check at least these four, because each one is acted on elsewhere
in this plan:

1. §1e — `required: false` still enforces `service_healthy` when the dependency
   IS active. Rebuild the developer's measurement with a labelled throwaway
   project (a slow-to-become-healthy service plus a dependant) and confirm the
   dependant starts only after `Healthy`.
2. §3 — `down` leaves profiled containers. Covered by T8.
3. §5 — `tailscale-backup` DOES get `ai-stack_default`, and did so before this
   change: `python -c "import json;print(json.load(open('/tmp/base-norm.json'))['services']['tailscale-backup']['networks'])"`.
4. §8 — `cmd /c "docker ps --filter name=^tailscale$ ..."` is not anchored:
   run it and count the names returned; then run the substring+`Where-Object`
   form used in the code and confirm it returns exactly one.

**PASS** when each spot-checked claim holds as stated and at the strength
stated. **FAIL** on any claim that is stated more confidently than its
provenance supports — a false entry in the sink is worse than one in the
deliverable, because the next item reads it.
