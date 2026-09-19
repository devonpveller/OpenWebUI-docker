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
`scripts/recovery/emergency-recovery.ps1:580` (`restart openwebui`) and `:1016`
(`build --no-cache openwebui`), by
`scripts/backup/restore-from-snapshot.ps1:99-100` (its `Stop`/`Start` lists,
spent one per `docker compose ... stop/start <service>` at :323 and :438), by
`backup/openwebui-restore.sh:9,12` and by
`documentation/runbooks/restore-from-snapshot.md:206`. (`emergency-recovery.ps1:961`
passes `"openwebui"` too, but as `Start-PlaneStack`'s `$GateContainer` — a
`Wait-ForHealthy` CONTAINER name at :261, not a service key. It is unaffected
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

So on a host whose `.env` has not been given `COMPOSE_PROFILES=gpu,tailscale`:

- `docker compose -f frontend/docker-compose.yml --env-file .env up -d` starts
  `openwebui-backup` and nothing else;
- the matching `down` removes `openwebui-backup` and **leaves `openwebui` and
  `tailscale` running** — a teardown that reports success and tore nothing
  down.

The whole-project verbs in the recovery surface are therefore the exposed ones:
`scripts/recovery/emergency-recovery.ps1:1012` (`down`), `:778` and `:1019`
(`up -d`), `:961` via `Start-PlaneStack` → `:261` (`up -d`), and `:760` via
`Stop-PlaneStack` → `:275` (`stop --timeout`). Each would operate on
`openwebui-backup` alone.

**What survives a missing line** (*observed live*): naming a service
explicitly activates it past an inactive profile — `up -d gated` started the
gated service with no profile set. So the watchdog's per-service paths
(`up -d tailscale`, `stop/start tailscale`, `build --no-cache openwebui`,
`up -d --force-recreate --no-deps tailscale`) still work. It is the
whole-project verbs that go quiet.

This is documented in the compose header, `.env.example`, the stack-map
frontend section and SERVICE-LIFECYCLE. **It is a one-line operator action
before the next frontend `up`/`down`, and nothing in code can substitute for
it** — the whole point of the profile set living in `.env` is that it declares
what this host is.

## 4. `check-watchdog-repair-targets.ps1` becomes profile-sensitive — and that is the right answer.

*Read from source,* `scripts/checks/check-watchdog-repair-targets.ps1:159-208`.
It renders each plane with the project's own `env_file` (here `.env`) and fails
when a managed container's service key is not declared. After this change, on a
host whose `.env` lacks `COMPOSE_PROFILES`, it will report `openwebui` and
`tailscale` as NOT DECLARED.

That is a true finding, not a false alarm: the script exists to answer "can the
watchdog actually start every container it claims to repair", and on such a
host the answer is no. It is not wired into pre-commit (the script says so at
line 18), so nothing breaks at commit time.

## 5. `tailscale-backup`'s comment about its network is wrong — and was wrong before this change.

*Read from source + observed live.* `frontend/docker-compose.yml` says of
`tailscale-backup`: "has no network attachment (the bind mount is the only
source)". The rendered config, from **`development`'s own file** as well as the
new one, gives it `networks: {default: null}` — i.e. it is attached to
`ai-stack_default`, because a service that declares no `networks:` key gets the
project default. Nothing depends on this (its `HEALTH_TCP` is deliberately
empty), so the behaviour is harmless; the comment is what is false. The
stack-map table now records the render; the compose comment was left alone,
since rewriting it is not this item's work.

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
