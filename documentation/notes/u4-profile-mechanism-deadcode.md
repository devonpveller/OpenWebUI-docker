# U4 groundwork — the runner/profile mechanism has no executable consumer

Recorded 2026-08-30 by the orchestrator, BEFORE the U4 build agents reported, so this is
independent evidence rather than a restatement of theirs.

## What was checked, and how

| Claim | Command | Result |
|---|---|---|
| little-coder is up | `docker ps`, `docker exec little-coder curl -fsS localhost:8090/health` | healthy, `{"status":"ok","version":"0.1.0"}` |
| its API is reachable from the host | `curl http://127.0.0.1:8090/health` | **connection refused** |
| what it actually publishes | `docker compose -f coder/docker-compose.yml config` | only `127.0.0.1:9091 -> 9090`, the **metrics** port |
| a working path exists | `docker exec little-coder curl -fsS localhost:8090/tasks` | `{"tasks":[]}` |
| harness code dispatches to it | `grep -rn 8090 scripts/agent-harness/` | **nothing** |
| anything resolves a runner | `grep -rn Resolve-RoleTarget` (repo-wide, worktrees excluded) | **3 hits, 0 executable callers** |

The three hits are: the function definition (`config.ps1:187`), a test that exercises it
(`test_harness_config.py:125`), and `.claude/skills/merge-queue/SKILL.md:133` — a **skill doc
instructing a human to run it by hand**.

## The finding

`Resolve-RoleTarget` correctly validates the profile, the role, and that the named runner is
defined; it even returns the runner's `status` (`proven` / `unproven`). Nothing consumes that
return value. `status` appears exactly once in the harness — on the line that produces it.

So the runner axis is a complete, tested, **unconsumed island**. `MODULE.md`'s claim that one
config file holds the role→model profiles is true and is not the same claim as the profiles
governing execution.

Two consequences that matter beyond U4:

1. **Three of the four shipped profiles are unusable.** `all-local`,
   `local-work-cloud-review` and `cloud-work-local-test` all assign at least one role to
   `little-coder`. Selecting one resolves cleanly — and then no dispatch exists, so the run
   proceeds on whatever the surface actually invokes. The config offers a choice it cannot
   honour, and it fails **silently** rather than loudly: `Resolve-RoleTarget` throws on an
   unknown profile or an undefined runner, which are the two errors that cannot happen here.
   `all-cloud` being both the default and `profile_locked: true` for extension sessions is
   what has kept this invisible.

2. **A11 is understated, not merely unproven.** The audit records little-coder as "wired and
   callable, but no work item has completed through it yet". The resolution is wired; the
   dispatch was never built. "Unproven" reads as *tried and not yet demonstrated*; the truth
   is *never attempted, because the code path stops one layer above*.

This is the vacuity pattern this effort keeps finding, in a new place: not a check that passes
while checking nothing, but a **configuration surface that validates while governing nothing**.
Its tests are honest — they test resolution, and resolution works.

## DECISIONS entries to append

- **U4 groundwork (2026-08-30): the profile mechanism resolves but does not govern.**
  `Resolve-RoleTarget` has zero executable callers repo-wide; the runner `status` field is
  read nowhere; `docker exec` reaches little-coder's API but the declared endpoint
  `http://127.0.0.1:8090` is unpublished and refuses. Therefore U4's "one profile mechanism
  governs both" is FALSE at the start of the phase in the strongest sense — it governs
  neither side. Any U4 deliverable claiming unification must show a dispatch changing when
  the resolver's answer changes; importing the resolver is not consuming it.
- **Corollary, carried as a known defect until U4 lands:** three of four shipped profiles
  route roles to a runner that cannot execute, and select silently. Whatever U4 concludes
  about dispatch, the config must stop advertising choices it cannot honour — either the
  dispatch exists or the profile is marked unavailable and selecting it fails loudly.

---

## Correction, 2026-08-30 — the running container is worse than the declared one

A U4 verifier reported that `docker port little-coder` returns nothing, which contradicted
the row above. I re-checked rather than take either side on trust:

```
$ docker port little-coder                                          # prints nothing, exit 0
$ docker inspect little-coder --format '{{json .NetworkSettings.Ports}}'
{"9090/tcp":[]}
```

The verifier is right and my row was imprecise. What I wrote — "only `127.0.0.1:9091 -> 9090`,
the metrics port" — was read off `docker compose config`, which is the **declared** state.
The **running** container publishes nothing at all: `9090/tcp` is exposed with an empty host
binding list. So the metrics port is not reachable either; the container predates that port
declaration and was never recreated.

The conclusion the note draws is unchanged and slightly strengthened — the harness's declared
`http://127.0.0.1:8090` is unreachable, and so is `9091`. But the sentence was stated with
more precision than the command behind it supported, which is the thing this effort keeps
catching in everyone else.

**The reusable finding is the gap itself**: on this host, compose text and the running stack
disagree. That is why a check that greps a compose file cannot establish that a door exists —
it establishes that a door is *declared*. A U4 branch shipped exactly that conflation
(`_door_problems` is a substring match over compose text, described in MODULE.md as proving a
door "actually exists"), and this host is a live counterexample to it.

Rule for anything downstream: **`docker inspect` / `docker port` for what IS; compose text for
what was INTENDED.** Never the second when you mean the first.

---

## Second correction, 2026-08-30 — my *explanation* was invented, and a verifier caught it

The correction above ends: "the container predates that port declaration and was never
recreated." **That is false.** A U4 verifier disputed it and I re-checked:

```
$ docker inspect little-coder --format '{{.Created}}'
2026-08-23T17:00:48Z
$ git log --format='%h %ad %s' --date=short -1 -S "9091:9090" -- coder/docker-compose.yml
56af93a 2026-08-21 K.4: coder plane becomes its own compose project (open-terminal moves in)
```

The container **postdates** the declaration by two days. My sentence had the causality
backwards.

What survives, and what does not:

- **SURVIVES (measured):** the running container publishes nothing —
  `NetworkSettings.Ports` is `{"9090/tcp":[]}` and `docker port` prints nothing — while
  `coder/docker-compose.yml` declares `127.0.0.1:9091:9090`. Compose text and the running
  stack disagree. Every conclusion drawn from that stands, including that a substring check
  over compose text proves a door is *declared*, never that it exists.
- **DOES NOT SURVIVE (invented):** *why* they disagree. I do not know. "Never recreated" was a
  plausible story I attached to a real measurement without checking it, and it is wrong.

The honest statement is: **the declared and running states disagree, and I have not
established the cause.**

That this happened inside the note written to teach "`docker inspect` for what IS, compose for
what was INTENDED" is the useful part. The measurement was sound both times; the failure was
appending an unverified *mechanism* to a verified *observation* and writing them in the same
voice. A reader cannot tell which half was checked — and under §C.7 the operator reads these
notes instead of the diffs, so the two halves have to be visibly separated.

This is the second time in this effort that an explanation of mine outran its evidence, and
both were caught by someone else. Recorded rather than quietly edited, because a note that
silently changes its story is worse than one that shows where it was wrong.
