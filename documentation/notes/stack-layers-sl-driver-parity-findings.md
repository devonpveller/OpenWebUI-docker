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

**[source]** `9f64b84:scripts/checks/check-project-configs.ps1:67-96` — pinned to that
blob, per F16's own rule: the file has been rewritten twice since (this item, then
sl-ob1-profiles' coverage guard), so a HEAD-relative citation would be false. It rendered
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

## F4 — `ruff check .` was red on `development`; **CLOSED by `sl-closeout`**

**[measured]** at base `9f64b84`, with every change in this worktree stashed
(`git stash -u`), `ruff check .` from the repo root reported:

```text
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9:101
```

That citation is into the `9f64b84` blob, and only there
(`git show 9f64b84:llm-queue/src/llm_queue/__init__.py | awk 'NR==9{print length($0)}'`
-> 103; the same line at `be00d53` is 68 characters). **The PATH is also only
that blob's**: `sl-colo-inference` moved the package to
`inference/llm-queue/src/llm_queue/__init__.py` on `3a373e2`, so resolving this
citation against the working tree finds sixteen files named `__init__.py` and
none of them the right one. A blob-pinned citation pins the path as well as the
line.

A docstring line carrying the `../documentation-plans-ai-stack/...` path the
2026-09-18 plan-store move rewrote — the rewrite pushed it past `llm-queue`'s own
100-column limit (the root `ruff.toml` allows 120, the subproject's does not).

**[measured]** after rebasing onto `be00d53`, `ruff check .` from the repo root is
`All checks passed!` — `sl-closeout` fixed that line. The entry is kept rather than
deleted because the first attempt's test plan told the tester to expect the red,
and a sink that silently drops a resolved claim is how a reader ends up trusting a
stale one.

## F5 — one redundant field removed from the inventory, and one row added

Both surfaced as drift the moment the file was generated rather than hand-kept,
and both are content changes to `scripts/lib/stack-services.json`:

- `searxng` carried `"service": "searxng"` **[source]** — identical to its
  container name. Harmless to both consumers (`stack-watchdog.ps1:145` and
  `check-watchdog-repair-targets.ps1:214` both do "service if present, else
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

**[source]** `9f64b84:scripts/checks/check-project-configs.ps1:67-77` (pinned to the
blob for the same reason as F2) listed five render targets plus a conditional
`open-brain`. `agent-org` appeared in neither, so its
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

---

## Added after test attempt 1 (tester `wt-tester-driver`, T9 FAIL)

## F11 — `SERVICE-LIFECYCLE.md` step 8 could not be followed as written, and the generator was the thing that was wrong

**[measured]** the tester executed step 8 for an imaginary `fake-probe-svc` added
to `memory/docker-compose.yml`: a sidecar row "in the right plane group, with
`critical` and any `host_health`", exactly as the row says, then
`inventory --write`:

```text
[FAIL] WRONG project for fake-probe-svc: the sidecar says 'None', the render says 'memory'
```

Two ways to close it: add `project` to the instructions, or derive it. **Derived**
— the render already knows which project owns a container, `_row()` was comparing
the render's answer against `None`, and the instruction was the correct one. The
field is optional in the sidecar now and filled from the render; where it IS
recorded it is still audited (a wrong one is still drift); and it stays REQUIRED
for a container whose project may not be renderable at all — `open-brain`, because
CI has no submodule — where the refusal names that case explicitly. Reproduced the
tester's walkthrough after the fix **[measured]**: `wrote
scripts/lib/stack-services.json`, row generated as
`{"container": "fake-probe-svc", "project": "memory", "critical": false,
"host_health": "..."}`.

The general lesson is the one worth keeping: **an instruction is verified by
executing it, not by reading it.** Three readers (developer, then the doc pass)
read row 8 and saw nothing wrong with it.

## F12 — `check-watchdog-repair-targets.ps1` rendered without profiles — **FIXED here**

**[source]** `Get-DeclaredServices` ran `config --services` with no `--profile`,
so compose omitted every profile-gated service and any such watchdog target would
have been reported `NOT DECLARED`. **[measured]** the difference that function
consumes, on the inference plane:

```text
bare     : llm-gateway llm-gateway-backup llm-gateway-db llm-gateway-ui
--profile local : + llama-cpp-embed-upstream llama-cpp-upstream llm-queue lm-models-backup
```

Green today only because no profile-gated container is in the watchdog's managed
set. Fixed the same way `check-project-configs.ps1` was: read `config --profiles`,
switch them all on, then render. **[measured]** the script still reports
`REPAIR TARGETS OK: 24 container(s)`, exit 0. It is in the artifact's spirit — this
item made the inventory able to carry profiled rows, and this was the last consumer
that could not see them.

## F13 — `check-project-configs.ps1` section 1b degrades quietly in its exit code

**[source]** when python or docker is missing, 1b prints
`service inventory NOT VERIFIED (this is a gap, not a pass)` and does **not**
increment `$failed`, so the hook exits 0. Deliberate, and it matches how section 3
treats a missing python: a pre-commit hook that hard-failed on a machine without
docker would be worse than one that says what it could not do. Recorded so nobody
reads a green hook as a verified inventory. The CI `stack-driver` job has both
tools and no such escape hatch, which is where the guarantee actually lives.

## F14 — the probe threshold had no test, only the parse did

**[measured]** the tester weakened probe 5 from `>= 8` to `>= 0` in the driver and
the whole suite stayed green: the two cases around it covered `""` and
`"not a number"`, both of which fail under either threshold. Closed here with
`test_a_serve_route_count_below_the_threshold_fails`, which pins the boundary in
both directions (3 and 7 FAIL, 8 and 9 PASS). Re-ran the tester's mutation after
adding it **[measured]**: `>= 0` now turns that test RED, 79 passed / 1 failed.

Worth stating plainly because this item's own headline is probe parity: a pinned
list of probe NAMES proves none was dropped, and proves nothing at all about
whether one was weakened. Those are two different guarantees and they need two
different tests.

**Challenged in attempt 2 and re-checked:** the reading was that the test asserts
only `3`, `7` and `8`, so "8 and 9 PASS" claimed more than the test contained.
It does contain the fourth case **[measured]**:

```text
$ git show 5133de9:scripts/stack/test_stack.py | grep -n 'serve_routes="9"'
716:    assert sweep(FakeHost(serve_routes="9"), root)[0] == 0
```

The claim stands as written. What was true is that the body put that case on its
fourth line where a reader could stop early - the same "read to the end" failure
MERGE-PROTOCOL 2 names - so the four cases are now one table, one line each, and
each asserts the probe's VERDICT as well as the exit code. The wording did not
need fixing; the shape that invited the misreading did.

---

## F15 — the branch edits one file outside the anchor's artifact list, deliberately

`scripts/checks/check-watchdog-repair-targets.ps1` is **not** in the anchor's
`artifact` line. It is changed here anyway (F12), and this is the declaration
rather than something a reviewer should have to discover:

* the anchor's fourth acceptance criterion is *about that file* - "the tester runs
  check-watchdog-repair-targets.ps1 and a shape error FAILS";
* this item made the inventory able to carry profile-gated rows, and that script
  was the last consumer that could not see one. Leaving it would have shipped a
  criterion that passes today and breaks the first time a profiled container joins
  the watchdog's managed set;
* the change is eighteen lines inside one function, adds no new behaviour to the
  driver, and the script's exit code and output are unchanged today
  (`REPAIR TARGETS OK: 24 container(s)`).

If the reviewer judges it out of scope, the revert is that one function and the
finding stays as a record for whoever picks it up.

## F16 — a `[source]` citation this branch's OWN fix invalidated

F5 cited `check-watchdog-repair-targets.ps1:196` for the "service if present, else
container" fallback. True when written; the F12 fix added eighteen lines to that
same file and moved it to **`:214`**. Corrected.

The mechanical lesson, which is the reason this is in the sink and not just in a
commit message: **a line citation is invalidated by editing the file it points
into, including by your own change in the same branch.** Re-derive every citation
in the note and the plan after any edit, rather than re-reading the ones that look
suspicious.

**And re-run the sweep over the REPAIRS.** A fix is an edit like any other, so it
invalidates citations exactly the way the original edit did - including the ones
it just wrote. Attempt 6 swept before the repairs and after the new prose, never
over the repaired lines themselves, and shipped a path that does not exist
(F-T18). Bulk replacements across a path must also be ANCHORED: every citation
ends with the same few characters, so an unanchored suffix match will prefix a
line that was already correct. Doing that here also turned up a second defect the eye had passed over
twice - the plan excused a `$Projects` hit in `coder-plane-findings.md:164` that
does not exist (that file contains the string nowhere; line 164 says "the project
registry's `Note` string" in prose), while two real hits went unlisted. Right
verdict, wrong reason.

## F17 — `memory/README.md` carried three claims this item made false — FIXED

The `$Projects` sweep the plan asks for returns exactly one live plane README, and
all three of its hits were instructions a reader would act on **[source]**.

**The line numbers below are in `be00d53:memory/README.md`, before this change**
(`git show be00d53:memory/README.md | sed -n '126p;183p;186p'`) - this branch edits
that file, so citing its current lines would go stale the moment anyone edits it
again, which is the mistake F16 is about:

| line (at `be00d53`) | said | now says |
|---|---|---|
| `:126` | the stack order comes from "`scripts/stack/stack.ps1` `$Projects`" | the order of the `[planes.*]` tables in `stack.manifest.toml`, topologically sorted by the driver |
| `:183` | "`scripts/stack/stack.ps1` — the `$Projects` row and the `health` probe" | the `[planes.memory]` table, plus the probe in `HealthSweep.run()` and its label in `PS1_PROBES` |
| `:186` | "`scripts/lib/stack-services.json` — the `memory` section and the backup row" | the CURATED sidecar, then `inventory --write` — the json is generated and a hand edit is refused |

Checked and NOT changed: `coder/README.md` and `search/README.md` mention
`stack.ps1 health` as a command, which still works through the shim;
`documentation/evidence/stack-layers/sl-manifest-test-plan.md:496` cites the old
`$Projects` array but is a dated evidence artifact of a completed run, and those
record what was true at the time rather than being kept current.

This is a file outside the artifact list, like F15. It is fixed rather than only
recorded because the three lines are instructions THIS branch falsified, and a
plane README is exactly where the next person changing that plane starts.

---

## Added at the rebase onto `f2bb38f` (sl-ob1-profiles merged)

## F18 — after the OB1 gitlink bumps, a bare `up` starts seven fewer containers unless the operator runs `init` once

The seam between the two items, stated where the next person will look for it.

`sl-ob1-profiles` declared `research` / `wiki` / `notebook` on the ob1 plane and
gave `stack.ps1`'s registry all four profiles so the pre-manifest driver kept
starting today's thirty containers. That registry no longer exists — this item's
shim has no plane list — so the set is expressed through the driver instead:
`idea-refinery` is `default = true` and `requires = ["research"]`, so `up` passes
both.

**Today that is exactly equivalent, and it is measured, not assumed [measured]:**

```text
$ git ls-tree development OB1
160000 commit 5005197dd3ae85a2a4aba8bf142084cac2824759  OB1
$ docker compose -f OB1/docker/docker-compose.yml config --profiles
idea-refinery
$ docker compose -f OB1/docker/docker-compose.yml \
    --profile idea-refinery --profile research config --services | wc -l
30
$ docker compose -f OB1/docker/docker-compose.yml \
    --profile idea-refinery --profile research --profile wiki --profile notebook \
    config --services | wc -l
30
$ docker ps --format '{{.Names}}' | grep -cE 'openbrain|open_notebook|surrealdb|open-notebook'
30
```

The pinned OB1 commit declares only `idea-refinery`; compose ignores a profile it
does not know, so two flags and four flags render the same thirty, and thirty are
running. `stack.ps1 up` and `stack.py up --all` both emit
`--profile idea-refinery --profile research`.

**After the gitlink bumps this stops being true.** `wiki` and `notebook` will then
gate seven containers that ARE running today — `openbrain-wiki`,
`openbrain-wiki-viewer`, `openbrain-workbench`, `openbrain-wiki-backup`,
`open_notebook`, `surrealdb`, `open-notebook-backup` — and a bare `up` will not
start them, because neither profile is `default` and neither should be (`default`
survives `--headless`, which would make the research product's surfaces split
meaningless).

**The one-time step:** `python scripts/stack/stack.py init --product research
--force` (or `enable research`), which writes them into `.stack/state.json` — a
file this host does not have at all today. `inventory --check` prints that
instruction on every run until the bump, next to the `[declared, not rendered]`
lines, so it cannot be met cold. This is the wave-4 step; it is a deployment
change and neither item makes it.

## F19 — a pinned submodule can legitimately disagree with the manifest, and only it can

The rule this item added rather than suppressing the noise or failing on it.

A plane whose compose file lives in THIS repo must agree with `stack.manifest.toml`
— both land in the same commit — so a declared profile the render lacks is drift.
`ob1`'s compose comes from a gitlink-pinned submodule, so the manifest may describe
the branch the gitlink will move to. `inventory --check` therefore has three
buckets, not two: drift (fails), `NOT VERIFIED` (could not render at all), and
`[declared, not rendered]` (a pinned submodule that has not caught up — passes,
and starts being verified for real the moment the gitlink moves).

Bounded deliberately, and tested in both directions: the submodule set is read from
`.gitmodules` (`is_pinned_submodule`), so it is not an `ob1` special case; the same
mismatch on any other plane is still drift; and a curated `profile` the MANIFEST
never declared is still `STALE`, so the exemption cannot launder a typo.

## F20 — the coverage guard and the generator check answer different questions, and both are kept

`check-project-configs.ps1` now runs both, which looks like duplication and is not:

* **`sl-ob1-profiles`' coverage guard** asks *did this render reach every inventory
  row for the project?* It derives the expectation from the inventory, which the
  render target cannot move, and it is what caught open-brain silently verifying
  26 of 30 rows. It also prints `NOT VERIFIED` for a project with rows and no
  render target — `agent-org`, which has never had one **[measured]**: the guard's
  own output is `rows verified/expected: inference:8/8 frontend:4/4 memory:3/3
  search:4/4 coder:4/4 open-brain:30/30` plus
  `NOT VERIFIED: project 'agent-org' has 16 inventory row(s) and no render target`.
* **This item's `inventory --check`** asks *is the whole file reproducible from the
  manifest and the curated sidecar?* It renders all eight projects, `agent-org`
  included.

Neither subsumes the other: a file can be perfectly reproducible from inputs that
were themselves derived from a render that quietly narrowed. Deleting either was
available and would have been wrong.

## F21 — a verification artifact, not a defect: `git show | python(text=True)` mojibakes UTF-8 on Windows

While diffing the two inventories I read a committed blob with
`subprocess.run(..., text=True)` and saw `PLAN Â§1.4` where the file has `§`.
`text=True` decodes with the LOCALE codec (cp1252 here), not UTF-8. The file is
fine — `git show ... | python -c "sys.stdin.buffer.read().decode('utf-8')"`
round-trips it exactly. Recorded because for several minutes it looked like the
generator was double-encoding notes, and the next person to compare blobs this way
will see the same ghost.

---

## Added at the rebase onto `b9fff95` (sl-frontend-solo merged)

## F22 — two profiles on one plane can be mutually exclusive, and "render with every profile on" then stops working

`sl-frontend-solo` gave the frontend plane `stock` and `gpu`: two definitions of
the SAME `container_name: openwebui`, one per deployment. This item's generator
renders each project with every declared profile switched on — which is how it
sees profile-gated containers at all — and that render now fails **[measured]**:

```text
$ docker compose -f frontend/docker-compose.yml --env-file .env.example \
    --profile stock --profile gpu --profile tailscale config -q
services.openwebui: container name "openwebui" is already in use by service {}"
```

Rendering each profile *alone* is not the fix either, because a profile can
depend on another **[measured]**:

```text
$ docker compose -f frontend/docker-compose.yml --env-file .env.example \
    --profile tailscale config -q
service "tailscale" depends on undefined service "openwebui": invalid compose project
```

So the generator falls back to **one render per profile CLOSURE**, plus the
bare render, and unions the results — `stock` alone, `gpu` alone,
`tailscale`+`gpu` together. The fallback fires only when the all-profiles render
fails, and it re-raises if any individual render fails, so a genuinely broken
compose file is still a refusal (tested both ways).

## F23 — `requires = ["gpu"]` added to the frontend's `tailscale` profile

The closure above needs a machine-readable answer to *which profiles can be
rendered together*, and the manifest already had the key: `requires`, introduced
by `sl-ob1-profiles`. `sl-frontend-solo`'s own description states the constraint
twice in prose — "usable ONLY with `gpu`, whose service its network_mode names
(frontend/docker-compose.yml:285)" — and compose enforces it by refusing the
render. This item writes it down.

**It changes nothing operationally:** no frontend profile is `default`, so the
driver passes no frontend flag at all and `.env`'s `COMPOSE_PROFILES` decides,
exactly as `sl-frontend-solo` intended. The edge only affects what the generator
renders and what `enable` would close over.

All three frontend profiles are also marked `opt_in` here, which is the flag this
item's accounting gate demands. That is not a change of intent: the block comment
`sl-frontend-solo` wrote above those tables already says none may be `default`,
and gives this item's own F1 (a CLI `--profile` REPLACES `COMPOSE_PROFILES`) as
the reason. `opt_in` is the name for what that comment describes.

## F24 — one container, two definitions: `service` and `profile` cannot be derived

The `openwebui` row is produced by service `openwebui` (profile `gpu`) and by
`openwebui-stock` (profile `stock`). Which key is correct depends on the
deployment, so the generator emits **neither** field and prints
`[ ~~ ] mutually exclusive - openwebui: produced by 2 mutually exclusive
services (openwebui, openwebui-stock)`.

That is the same conclusion `sl-frontend-solo` reached by hand and wrote into the
row's note — "No 'service' field, because the right key depends on which profile
the deployment runs" — now derived rather than remembered. The generator has
three non-drift buckets as a result, printed separately so the advice attached to
one is not attached to another: `NOT VERIFIED` (could not render), `declared, not
rendered` (a pinned submodule behind the manifest), and `mutually exclusive`.

## F25 — `tailscale` and `tailscale-backup` gain a `profile` field

`sl-frontend-solo`'s hand-kept rows for both carry no `profile`, though both
services sit behind the `tailscale` profile in the render **[measured]**. The
generator derives it, so the rows gain `"profile": "tailscale"`.

It is an improvement rather than a cosmetic diff: that item's own coverage guard
reads `$rowProfile[$_]` to tell an operator which `--profile` a narrowed render is
missing, and a row with no `profile` produces no hint. Declared as one of the four
differences between this branch's inventory and `b9fff95`'s.

## F26 — `stack.ps1`'s tailnet guard was a probe-set change, and a shim would have dropped it

`sl-frontend-solo` did not only edit the plane; it changed `stack.ps1 health`,
making the tailnet probe skip itself where the `tailscale` profile is not
deployed, with two fail-open paths. Replacing that script with a shim would have
silently discarded all of it — the exact failure this item's pinned `PS1_PROBES`
list exists to prevent, arriving through a file the list does not cover.

Ported into `HealthSweep.tailscale_deployed()` with its reasoning intact: the
decision reads the RENDER rather than parsing `.env` (compose's own answer after
it has applied `COMPOSE_PROFILES` from every source), an unreadable render probes
anyway, and a container named exactly `tailscale` running while absent from the
render probes anyway and says why. Five tests cover it, including the
exact-versus-substring name match that `stt-tts-tailscale` would otherwise defeat.

**The general point for the next shim:** a pinned list of probe NAMES catches a
probe that is dropped from the file it pins. It does not catch a probe changed in
the file being REPLACED. When a driver absorbs another, diff the absorbed file
across the range, do not just count what you kept.

---

## Added at the rebase onto `ae915c3` (sl-colo-gateways merged)

## F27 - seven inherited citations in `stack.manifest.toml` were broken, five of them by an item that had already merged

The sweep - re-derive every `path:line` in every file this branch touches, by
CONSTRUCT and in both spellings - found 86 citations and 7 defects, none written
by this item:

**The left-hand column below quotes the BROKEN citations verbatim, so they do not
resolve and are not meant to** - the same device F16 uses when it quotes
`check-watchdog-repair-targets.ps1:196`. A sweep run over this note will report
them; that is the quotation, not a live claim. Everything in the right-hand
column resolves.

| citation (quoted, BROKEN) | why it was false | now |
|---|---|---|
| `inference/docker-compose.yml:448-450, 453-455` | `sl-inference-split` moved the services into `inference/compose/*.yml`; that file is **91 lines** | `:78-80, 83-85` |
| `inference/docker-compose.yml:83-86, 129-132` (GPU) | same | `inference/compose/upstreams.yml:100-106, :152-158` |
| `inference/docker-compose.yml:34` (chat GGUF path) | same - and the line itself said that path was being replaced by `LM_MODELS_DIR` | `inference/compose/upstreams.yml:24, :54` |
| `inference/docker-compose.yml:111,119` (embedding GGUF) | same | `inference/compose/upstreams.yml:145` |
| `inference/docker-compose.yml:320-327` (llm-gateway-ui on app-net) | same | `inference/compose/gateway.yml:152, :161-163` |
| `scheduled.yml:186` / `:258` / `:190` / `:254` - **four refs on three lines** (`274`, `278`, `290` at `ae915c3`) | **names no file** - it is `docker-compose.scheduled.yml`. A bare basename that does not exist resolves for a human reading the paragraph above it, and for nothing else | full paths |
| `integrations/openbrain-idea-refinery/index.ts:268` | a path relative to the OB1 submodule root with no `OB1/` prefix; 26 files in this tree are named `index.ts` | `OB1/integrations/...` |

Each replacement was verified by READING the line, not by counting: `:78-80` is
`llm-net` / `external: true` / `name: ai-stack_llm-net`; `upstreams.yml:145` is
`LLAMA_ARG_MODEL=/models/bge-m3-f16.gguf`; `gateway.yml:152` is
`llm-gateway-ui:`; `index.ts:268` is the `fetch(${RESEARCH_URL}/research)` call.

**This table said FIVE bare `scheduled.yml` refs in its first version and there
were four** - `:253` (line 283 at `ae915c3`) was already fully qualified. The
invented fifth is not a harmless miscount: it is the entry whose repair then
damaged a correct line. See F-T18.

**The rule this makes concrete, and the second half is the one that keeps being
missed:** a citation is invalidated by any edit to the file it points into -
including another item's, landing after yours. And a citation must be
*resolvable by a machine*, which a bare basename is not unless a file of exactly
that name exists. F16 stated the first half; this is the second.

## F28 - `health` renders with `.env` and the inventory with `.env.example`, on purpose

Two calls that look identical and answer opposite questions. The tailnet guard
asks *what does THIS HOST deploy*, so it must see this host's
`COMPOSE_PROFILES` and renders with the real `.env`. The inventory generator asks
*what does the compose file DECLARE*, which has to be identical on a laptop, this
host and a CI runner, so `render_env_path` uses the `.example`. Stated now at
both call sites and in `scripts/stack/README.md`, because a reader who noticed
the asymmetry had no way to tell which one was the mistake.

## F29 - a clean three-way merge is not a checked one

`git rebase` onto `ae915c3` reported no conflict in any of the four files the two
items share, because the edits sat in different regions of each. Every one was
still opened and confirmed to carry both intents - their CI job and mine, their
CLAUDE.md sentence and my Driver row, their repo-map row and my shim pointer,
their `memory/README.md` repoints at `:33`/`:151` and my three at
`:126`/`:183`/`:186` (now `:127`/`:184`/`:192`). Recorded because "no conflict" is
the easiest thing there is to mistake for "nothing to check", and this four-file
overlap was flagged in advance precisely because it was real.

## F-T18 - the fix for F27 broke a line F27 had no business touching

`stack.manifest.toml:330` shipped in attempt 6 as

```text
# optional agent-org: OB1/docker/docker-compose.OB1/docker/docker-compose.scheduled.yml:253
```

a path that does not exist. The repair for F27 was a bulk suffix replacement -
`s.replace("scheduled.yml:253", "OB1/docker/docker-compose.scheduled.yml:253")` -
and `scheduled.yml:253` is a SUFFIX of the already-qualified
`OB1/docker/docker-compose.scheduled.yml:253`, so the rewrite prefixed a line
that was already correct. The enumeration that drove it claimed five bare refs
where there were four, and the phantom fifth was exactly that line.

Two rules, and the second is the one that would have caught it:

1. **A bulk replacement across a path must be ANCHORED.** Match the whole
   citation - a word boundary or a preceding non-path character - not a tail of
   it. Every path citation ends with the same few characters by construction, so
   an unanchored suffix match is a loaded gun in any file that mixes qualified
   and unqualified forms. Which this one did, deliberately, which is why it was
   being repaired.
2. **A repair is an edit like any other, so re-run the check on its own output.**
   The sweep ran before the fixes and reported 7 problems; it ran after and
   reported the 8 labelled quotations. It never ran over the REPAIRED lines
   asking whether they resolved - and it would have caught this instantly,
   because that path does not exist. F16's rule now says so explicitly.

The other six repairs were re-derived independently by the tester and stand.

---

## Added at the rebase onto `3a373e2` (sl-colo-inference merged)

## F30 — `inventory --check` refused where it should have reported a gap, and the hook then named a remedy that could not work

`agent-org/docker/docker-compose.yml` carries **service-level `env_file:`**
entries, and `docker compose config` STATS those whatever `--env-file` the CLI
was given. So on any machine without `agent-org/docker/.env` — which is
gitignored, so every machine but the deploy host — the render exits 1, the
generator raised a Refusal, and `check-project-configs.ps1` printed:

```text
[configs] INVENTORY DRIFT - regenerate with: python scripts\stack\stack.py inventory --write
```

**Wrong twice.** Nothing had drifted: the inventory was correct and unreadable,
which is a different thing. And the remedy it named could not work — `--write`
refuses by the same path, so the operator following that line gets the same
error and no way forward.

Fixed by degrading the way the two neighbouring checks already do: the coverage
guard's `NOT VERIFIED: project '<p>' ... (<file> absent - gitignored, so this is
expected off the deploy host)` and F13's missing-python line. The generator now
names the project and the file, carries that project's rows through from the
sidecar unverified, prints the gap and exits 0.

**The exemption is ONE named, checkable condition** — the plane's real env file
is absent — and nothing else. A compose file that exists and fails to render for
any other reason is still a hard refusal, tested both ways. The guard reads
`manifest.env_path()`, the REAL path, not `render_env_path()`'s `.example`: the
two differ on purpose (F28) and it is the literal `.env` that a service-level
`env_file:` names.

The general shape is worth keeping, because this is the third time it has come
up in this item: **"I cannot check this here" and "this is wrong" must not print
the same way.** F13 said it for a missing tool, F19 for a pinned submodule, and
this is the same distinction for a gitignored file. Only one of the three ever
exits non-zero.

## F31 — the inference citations moved again, and `git show` pins a PATH as well as a line

`sl-colo-inference` added five lines to `inference/docker-compose.yml` (91 -> 96)
and shifted `inference/compose/gateway.yml`, so the citations F27 repaired needed
re-deriving a second time **[measured]**:

| citation | was | now | construct |
|---|---|---|---|
| anchor networks | `:78-80, 83-85` | `:83-85, 88-90` | `name: ai-stack_llm-net` / `name: ai-stack_app-net` |
| llm-gateway-ui | `gateway.yml:152` | `:153` | `llm-gateway-ui:` |
| its networks | `:161-163` | `:162-164` | `networks:` / `- llm-net` / `- app-net` |

`upstreams.yml:24, :54, :100-106, :145, :152-158` were untouched, and were
re-derived anyway rather than assumed.

That item also moved `llm-queue/` into the plane, which breaks F4's citation in a
way worth naming: `llm-queue/src/llm_queue/__init__.py:9` is pinned to `9f64b84`
and is correct **there**, but resolving it against today's tree finds sixteen
files named `__init__.py` and not that one. **A blob-pinned citation pins the
path as well as the line** — read it with `git show <blob>:<path>`, never by
opening the working tree. Said now in F4 and in the plan's citation table.
