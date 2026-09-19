# Findings — `sl-driver-parity` (2026-09-19)

Things that are true, were found while building the driver's `health`, `stats`
and `inventory` verbs, and are **not** this item's artifact. Recorded here per
CLAUDE.md ("findings go to `documentation/notes/`, not into the deliverable").

**Provenance labels**, per MERGE-PROTOCOL §2:

- **[source]** — read from the file, cited by path and line.
- **[measured]** — observed live on this host on 2026-09-19, command given. Not
  re-run since.
- **[not verifiable here]** — outside this tree; check before acting.

---

## F1 — `docker compose --profile X` REPLACES `COMPOSE_PROFILES`; it does not union with it

**[measured]** 2026-09-19, compose **v5.3.0**, from the worktree root, with
`.env:334` carrying `COMPOSE_PROFILES=local,gpu,tailscale`:

```text
docker compose -f inference/docker-compose.yml --env-file .env config --services
  -> llama-cpp-embed-upstream llama-cpp-upstream llm-gateway llm-gateway-backup
     llm-gateway-db llm-gateway-ui llm-queue lm-models-backup      (8)

docker compose -f inference/docker-compose.yml --env-file .env \
    --profile idea-refinery config --services
  -> llm-gateway llm-gateway-backup llm-gateway-db llm-gateway-ui  (4)
```

One unrelated `--profile` flag removed the four `local` services. Nothing warned.

**Why it matters beyond this item.** Any script that adds a `--profile` flag to a
plane whose env sets `COMPOSE_PROFILES` silently starts a smaller stack. It does
**not** bite on the base this was written against, because the only plane the
driver passes a flag to is `ob1`, whose env (`OB1/docker/.env`) has no
`COMPOSE_PROFILES` — but it is live ammunition for the next item that adds one.

**Handled inside this item** by `effective_profiles()` in `scripts/stack/stack.py`:
whenever the driver would pass any flag, it unions in the plane env's
`COMPOSE_PROFILES` first, so the flag list can never be a reduction. **Not
handled anywhere else** — `scripts/recovery/emergency-recovery.ps1` and
`scripts/checks/stack-watchdog.ps1` build their own compose command lines
(`Invoke-PlaneCompose`, `scripts/checks/stack-watchdog.ps1:162-193`, whose args are built at `:136-152` **[source]**)
and neither passes a profile flag today, so neither is wrong today. If either
ever gains one, it inherits this trap.

## F2 — `openbrain-idea-refinery` was missing from `scripts/lib/stack-services.json`, and the check that was supposed to catch that could not see it

**[source]** `scripts/checks/check-project-configs.ps1:67-96` (pre-change) rendered
five projects with `docker compose ... config --format json` and **no** `--profile`
flags, then regex-extracted `container_name`. Compose omits profile-gated services
from a bare `config`, so no profile-gated container was ever in the set being
compared. **[measured]** the OB1 render is 29 services bare, 30 with
`--profile idea-refinery`.

Consequence **[source]**: `scripts/checks/stack-watchdog.ps1:170` logs
`Cannot repair '<name>': no compose project owns it in scripts\lib\stack-services.json`
and returns `$false` for any container absent from the inventory. **[measured]**
`openbrain-idea-refinery` has been `Up 6 days` on this host. So the watchdog could
detect it and refuse to repair it, and the drift verifier reported "inventory
matches the compose configs" the whole time.

Fixed here (the row is generated now, and the generator renders with every declared
profile). The wider point is the one to keep: **a verifier that derives its own
input set can be blind and green at the same time.**

## F3 — the anchor project's name is its DIRECTORY name, so it is not `ai-stack` in a worktree

**[source]** `grep -n '^name:'` over the nine compose files: seven declare one
(`inference`, `frontend`, `memory`, `search`, `coder`, `open-brain` at
`OB1/docker/docker-compose.yml:11`, `agent-org` at
`agent-org/docker/docker-compose.yml:23`). The root `docker-compose.yml` and
`portal/docker-compose.yml` declare none.

**[measured]** `docker compose -f docker-compose.yml --env-file .env.example config
--format json` run in this worktree reports `"name": "wt-sl-driver-parity"`.

The networks themselves are safe — the root file names them explicitly — but it
means the *project* an anchor `up` creates from a worktree is not the one the main
checkout owns. That is why the inventory's project names are declared in
`scripts/lib/stack-services.curated.json` rather than read out of the render.
**Not investigated:** whether a `docker compose -f docker-compose.yml up -d` from a
worktree would create a second, near-empty project alongside `ai-stack`. Nobody
should try it to find out; the driver is not what would do it.

## F4 — `ruff check .` is already red on `development`

**[measured]** with every change in this worktree stashed (`git stash -u`),
`ruff check .` from the repo root still reports:

```text
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9:101
```

It is a docstring line carrying the `../documentation-plans-ai-stack/...` path the
2026-09-18 plan-store move rewrote — the rewrite lengthened the line past
`llm-queue`'s own 100-column limit (the root `ruff.toml` allows 120, the
subproject's config does not). The CI `ruff` job runs `ruff check .` from the root,
so that job is red on the work line for a reason that predates this item and has
nothing to do with it. One line; left alone deliberately.

## F5 — one redundant field removed from the inventory, and one row added

Both surfaced as drift the moment the file was generated rather than hand-kept,
and both are content changes to `scripts/lib/stack-services.json`:

- `searxng` carried `"service": "searxng"` **[source]** — identical to its
  container name. Harmless to both consumers (`stack-watchdog.ps1:145` and
  `check-watchdog-repair-targets.ps1:196` both do "service if present, else
  container"), but redundant data is what drifts. Dropped; `service` is now emitted
  only where the compose SERVICE key actually differs (`redis`/`gateway`/`vpn` in
  the search plane).
- `openbrain-idea-refinery` added (F2).

Every other row — 67 of 68 — and all eight `projects` entries came out of the
generator byte-identical to the hand-maintained file.

## F6 — the manifest's `[planes.inference.profiles.local]` said `pending = true` after the profile had shipped

**[source]** `sl-inference-split` merged at `9f64b84` and put the two upstreams,
`llm-queue` and `lm-models-backup` behind a `local` profile; the manifest table
still said `pending = true   # sl-inference-split adds it`. Corrected in this item,
and `inventory --check` now compares every plane's declared profiles against
`docker compose config --profiles`, so the flag cannot go stale silently again.

**This is the shape to expect from `sl-ob1-profiles`** (in test, not merged as of
2026-09-19): it adds `research`/`wiki`/`notebook` to OB1's compose around services
that run today. When it lands it must mark those three `default = true` (the driver
passes them, and today's 30 containers keep starting) or `opt_in = true` with the
description naming what turns them on. `inventory --check` refuses a profile that
declares neither — see the `opt_in` row in the manifest header and
`Manifest.unaccounted_profiles()`.

## F7 — `agent-org` was never in the pre-commit inventory verifier's render set

**[source]** `check-project-configs.ps1:67-77` (pre-change) listed five render
targets plus a conditional `open-brain`. `agent-org` appeared in neither, so its
twelve inventory rows were never verified against anything. This matches the
standing note `documentation/notes/memory-plane-phase0-findings.md` §F2, written
when agent-org had **no** rows at all. Fixed here: the generator renders all eight
projects.

## F8 — the two scheduled tasks that touch this stack do not invoke `stack.ps1`

**[measured]** `Get-ScheduledTask` on this host, filtered to the stack's tasks:

```text
StackWatchdog                 powershell.exe -File "...\scripts\checks\stack-watchdog.ps1"
AI-Stack Weekly Maintenance   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\scripts\maintenance\weekly-maintenance.ps1"
```

**[source]** neither `scripts/checks/stack-watchdog.ps1` nor
`scripts/maintenance/weekly-maintenance.ps1` contains the string `stack.ps1`
(`grep -n` over both: no hits). So turning `stack.ps1` into a shim cannot affect
either task — recorded because the anchor asks the tester to confirm it, and this
is what was checked.

## F9 — `stack.ps1 health` covers the daemon and not the executor (still true)

**[source]** the existing note `documentation/notes/coder-plane-findings.md:158-165`
says the coder probe curls `little-coder:8090/health` and never touches
`open-terminal`, so a dead executor passes green. The port moved to the driver
unchanged — **this item deliberately reproduced the probe set one for one**, gaps
included, because parity is the anchor's first acceptance criterion. Adding an
`open-terminal` probe is a behaviour change and belongs to whoever owns that gap
(`HARNESS-V2-PLAN.md` row 3 calls it "requires a design choice, not just an edit").
Note the pinned `PS1_PROBES` list in `scripts/stack/test_stack.py` is where such an
addition now has to be declared.

## F10 — `[planes.*.ports]` now has a consumer, and it agreed everywhere

**[measured]** the manifest's declared host ports match the published ports of every
renderable plane exactly — all 16 OB1 ports, both agent-org ports, both inference
`local` ports, and the single ports of frontend / memory / search / coder; anchor
and portal declare none and publish none. The `sl-manifest` reviewer flagged those
tables as having no consumer; `inventory --check` is now one, in both directions
(a declared port nothing publishes, and a published port nothing declares).
