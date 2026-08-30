# U4 bidirectional runner unification — findings

Sink for `work/u4bidir` (DFU PLAN §2, U4's third clause: *"then agent-org workers as
harness runners and vice versa — one profile mechanism governs both"*). Written
2026-08-30 against the tips of `refactor/ai-stack-cleanup` (`5f4817d`) and the live
stack; **revised 2026-08-30 after the item was refuted 2 of 2** — §8 records what the
reviewers found, what was wrong with it, and the executable proof of each fix.
Everything below was checked by reading the named file or running the named command;
where a claim is inferred rather than executed it says so.

Siblings `work/u4disp` (harness→little-coder dispatch), `work/u4oracle`
(frontier-oracle-on-stall) and `work/u4quad` (runner × target quadrant) were empty at
`5f4817d` when this was written; nothing here depends on their files. The seams they
need from this branch are listed in §7.

---

## The verdict, in three lines

1. **"One *profile* mechanism governs both" is FALSE.** The two profile tables answer
   different questions and forcing them together makes both worse (§1).
2. **What IS one mechanism is the layer beneath — the runner registry — and agent-org
   now DISPATCHES on it.** Changing one word in the shared file changes which
   `WorkerHarness` implementation executes a wake, on the live path (§3, §5).
3. **The other direction is DECLARED, NOT DISPATCHING, and is parked.** The harness has
   no dispatcher at all: nothing in `scripts/agent-harness/` submits a task to a runner,
   no profile names `agent-org-worker`, and `Get-HarnessRunnerPool` has zero executable
   callers. Saying "both directions are TRUE" was an over-claim; the park is now
   asserted by a test rather than described in a paragraph (§8.3).

Those three are deliberately not one sentence. The repo's own bar for this phase is
`documentation/notes/u4-profile-mechanism-deadcode.md` — *"importing the resolver is
not consuming it"* — and on the harness side nothing even imports it.

---

## 1. "One profile mechanism governs both" — FALSE

The two things called "profile" are not the same object, and neither one governs what
the sentence assumes it governs.

**agent-org's profile** (`agent-org/agent-bridge/app/modules/profiles.py`,
`profiles/*.json`) binds `{lane, model, system_prompt_ref, temperature, tool_access,
caller_key}` to a role name. `lane` is `local|cloud` and selects a **gateway**
(`ModelRouter._endpoint`, `model_router.py`), not an executor.

**It has never governed a worker.** Grepping every `models.structured(` /
`models.complete(` call in the bridge yields exactly three profile names reaching
`ModelRouter`: `po`, `pm`, `pm-voice` (plus `planner` and the stop-gate lenses through
`self.models`). `worker-default` appears 25 times in `orchestrator.py` and once in
`modules/router.py:636` — every one of them as the `role` argument to
`Scheduler.acquire(effort_id, role, session_id)` or to `_agent_identity()` (the git
author). It never reaches `ModelRouter`. The worker's model is decided *inside*
little-coder, from that container's own `models.json` / `LC_CONFIG`; agent-org's
`worker_model: "qwen36-27b"` (`config.py:273`) is read only by
`app/evals/capability_floor.py`.

**The harness's profile** (`scripts/agent-harness/harness.config.json`) binds
`{runner, model}` to a role and *names* which agent program should perform the whole
role. (Names, not causes — see §8.3: nothing in the harness dispatches on that answer.)
It has no gateway, no charter, no caller key.

So a merged table would have to carry `caller_key` / `tool_access` / `temperature`
fields that mean nothing to a Claude Code agent, and a `runner` field that means
nothing to a bridge making its own inference call — there is no executor to pick when
the caller *is* the executor. Two tables, two questions. Left separate, deliberately.

**What IS one object:** *which execution substrates exist, of what kind, at what
address, with what proven status.* agent-org held that as `AO_WORKER_INSTANCE_URLS`, a
bare CSV with the kind implicit and always little-coder; the harness held it as
`runners{}`. Those are the same facts written twice, so they are now one file with
three readers (§4).

## 2. "agent-org worker as a harness runner" — DECLARED here, not dispatched by here

An ao-worker **is** a little-coder daemon addressed by `base_url`
(`agent-org/docker/docker-compose.yml:293`, `image: little-coder:local`,
healthcheck on `:8090`), and the harness already declares a `little-coder` runner
kind. At the level of "can the harness name one", yes — and that is all this branch
delivers on this side. Three things do not cross, and calling any of them cosmetic
would be wrong:

**a. There is no harness dispatcher to hand an item to.** Not a limitation of the
runner: the harness has no code path that submits work to anything. `Resolve-RoleTarget`
validates and returns; `Get-HarnessRunnerPool` has no caller. See §8.3.

**b. The unit of isolation is different.** A harness runner works in a git **worktree
on the host**. little-coder works in `config.workspace.path` (`/workspace`), a named
docker volume, populated *only* by `WorkspaceManager.clone` — and `set_project` WIPES
it on a project switch (`little-coder/src/littlecoder/daemon.py:488–556`). There is no
"operate in this existing directory" mode, and the host worktree is not mounted into
any of these containers (`coder/docker-compose.yml`: `little-coder-workspace:/workspace`;
ao-workers: `ao-worker-N-workspace:/workspace`). So handing a harness item to an
ao-worker means handing over a **branch**, never a path — and therefore a pushed
branch; an uncommitted working tree does not survive the trip. The `.env` files
`new-worktree.ps1` copies do not cross either.

**c. Reachability. The shipped address was dead.** `harness.config.json` declared the
little-coder runner at `http://127.0.0.1:8090`. The coder plane declares
`127.0.0.1:9091:9090` (Prometheus metrics) **and nothing else**; ao-workers publish
nothing. Verified live 2026-08-30:

```
$ docker ps --format '{{.Names}}\t{{.Ports}}'
little-coder    9090/tcp
ao-worker-1     8090/tcp, 9090/tcp
ao-worker-2     8090/tcp, 9090/tcp
$ curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/health
000   (curl rc=7, connection refused)
```

Nothing had ever dispatched, so nothing had ever falsified it. **A runner nobody calls
is a runner nobody corrects** — and that config file is the only thing anyone would
have read before wiring one up. Fixed (§4), and the guard that keeps it fixed is itself
now falsifiable (§8.1).

## 3. "harness runner as an agent-org worker" — the SELECTION is fixed; the runner is parked

The protocol was never the blocker. `WorkerHarness`
(`agent-org/agent-bridge/app/worker/harness.py:97`) is an 8-method `Protocol` with two
implementations already (`LittleCoderHarness`, `FakeHarness`), and every one of the 16
call sites in `orchestrator.py` (5) + `modules/router.py` (11) already passes a worker
ADDRESS as the first argument — `inst.base_url`, or a `url`/`worker_url` local holding
one. The address was always the routing key. (Counted 2026-08-30 by grepping the eight
protocol method names against `self.harness.` / `self.router.harness.`; the first
version of this note said 15.)

**The blocker was the SELECTION**, one line: `orchestrator.py` bound a single
implementation for the entire pool at construction, chosen from `settings.chat_adapter`.
A heterogeneous pool was therefore inexpressible. RED evidence, run against the
unmodified tree — the address changed and the implementation did not:

```
registry module present            : False
harness impl bound by construction : FakeHarness == FakeHarness
run A  address / result            : http://w1:8090 -> [lc] ok
run B  address / result            : http://cc1:9099 -> [lc] ok
distinct impls reached             : 1
```

That is fixed, and the fix is now guarded at the line that installs it (§8.4). What
remains genuinely missing is a claude-code **worker**, and it is not a config flip:

- a Claude Code agent is a host process, not an HTTP task daemon. Nothing in this stack
  exposes `POST /tasks`, `GET /tasks/{id}`, `/project`, `/check`, `/cancel` for one.
- agent-org's containment posture — git-proxy, egress allowlist, default-deny, floor
  hook — is what makes a worker safe to dispatch to, and a host process has none of it.
  That is PLAN U5 (*containment parity*), a separate phase.

So the runner is **parked with a written reason**, and the park is executable:
`RunnerDispatch` routes a `claude-code`-kind address to `UnprovisionedHarness`, which
raises `RunnerNotProvisionedError` naming exactly what is missing. A wrapper that
degraded silently to little-coder would have passed every test and changed nothing.

## 4. What was built

| change | file | what it makes true |
|---|---|---|
| shared runner registry | `scripts/agent-harness/harness.config.json` | `runners{}` gains `reachable_from`, `pooled`, and agent-org's ao-worker pool; the dead `127.0.0.1:8090` becomes `http://little-coder:8090` |
| PS reader | `scripts/agent-harness/config.ps1` | `Get-HarnessRunner`, `Get-HarnessRunnerAddresses`, `Get-HarnessRunnerPool` |
| Python reader | `scripts/agent-harness/config.py` | `runner()`, `runner_addresses()`, `runner_pool()` |
| third reader + dispatcher | `agent-org/agent-bridge/app/modules/runners.py` | `RunnerRegistry` (reads the same file), `RunnerDispatch` (a `WorkerHarness` that routes by address), `UnprovisionedHarness`, and `POOL_SOURCE_ENV`/`POOL_SOURCE_REGISTRY` |
| per-instance selection | `agent-org/agent-bridge/app/orchestrator.py` | the global harness binding becomes `RunnerDispatch.default(self.runners)`; pool registration carries each instance's kind |
| pool registration | `agent-org/agent-bridge/app/modules/scheduler.py` | `register_pool((id, url, kind))` beside the legacy `register_from_urls` |
| deployment | `agent-org/docker/docker-compose.yml` | the file is bind-mounted read-only at `/etc/agent-bridge/runner-registry.json`; `AO_RUNNER_REGISTRY_FILE` + `AO_WORKER_POOL_SOURCE` (default `env`) |
| the guard | `scripts/agent-harness/check-runner-endpoints.ps1` | `reachable_from` is a falsifiable claim — probed from inside a container on each declared network |
| the guard's guard | `scripts/agent-harness/verify-runner-endpoint-check.ps1` | six mutations, six expected exit codes; proves the check above can FAIL |

**Precedence, stated exactly** (the earlier version of this section was wrong — see
§8.2). Two questions, two owners:

- **WHICH addresses are work capacity** — `AO_WORKER_INSTANCE_URLS`, and only that,
  unless an operator sets `AO_WORKER_POOL_SOURCE=registry`. An empty CSV is an empty
  pool even with the file fully present. This is byte-for-byte the pre-U4 behaviour.
- **WHAT each address is** — the runner kind, which selects the `WorkerHarness`
  implementation. The shared file, because a bare URL (every operator's current shape)
  states an address and nothing else. A `kind=url` entry in the CSV overrides the file.

Getting that second split backwards is how a shared registry becomes decorative, and an
earlier draft of `RunnerRegistry.__init__` had the bug: it used `setdefault`, so an
operator entry that explicitly stated `little-coder=<url>` was overridden by the file.
`test_a_bare_env_url_takes_its_substrate_from_the_shared_file` is that bug's repro.

## 5. Evidence

Executable, all re-runnable. Commands are given exactly as they were run — the
interpreter matters, because the repo-root `.venv` does not carry agent-org's
dependencies:

- `python -m pytest -q scripts/agent-harness/test_harness_config.py` — **16 passed**
  (repo-root `.venv`), including the PowerShell↔Python cross-reader test extended to
  the runner registry, and the two park guards from §8.3.
  Falsified deliberately: breaking `Get-HarnessRunnerAddresses` so it drops
  endpoint-only rows makes it FAIL with a concrete diff, and reverting makes it pass.
- `agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest -q tests/test_runner_registry.py`
  (run from `agent-org/agent-bridge/`) — **19 passed in 3.5s**. One walks the
  `WorkerHarness` Protocol itself and asserts `RunnerDispatch` forwards every method —
  proved RED by deleting the `cancel_task` forwarding.
- **The whole agent-org suite**, same interpreter, **serially**:
  `agent-org/agent-bridge/.venv/Scripts/python.exe -m pytest -q` →
  **869 passed in 567.46s (9m27s)**, exit 0, run 2026-08-30 from
  `agent-org/agent-bridge/` in this worktree. 869 = the 861 that existed before this
  branch's second pass plus the 8 guards added in §8. This matters because the change
  replaces the pool-registration call (`register_from_urls` → `register_pool`) and the
  harness binding that every one of those tests constructs.
  *(Two corrections to the first version of this note. It reported `pytest -q -n 8`, and
  `pytest-xdist` is installed in none of the three agent-bridge virtualenvs, so that
  command was not reproducible as written — see §6.2; the count was right, the command
  was not. It also said the serial suite "does not finish inside a 15-minute window on
  this host". It does: 9m27s, measured.)*
- `powershell -File scripts/agent-harness/verify-runner-endpoint-check.ps1` — **6/6**,
  exit 0. Six mutated registries, six expected exit codes, including `wrong-port` → 1
  and `nonexistent-container` → 2. Against the check as originally shipped it reports
  **2 of 6 FAIL** (§8.1).
- `powershell -File scripts/agent-harness/check-runner-endpoints.ps1` — exit **0** on
  the shipped registry, and it now says *why*, per network:
  `ai-stack_llm-net : opened from openwebui (http 404) ; coder_lc-net : opened from
  open-terminal (http 404)`.
- **The dispatch actually changes.** Real production image (`agent-bridge:wt-u4bidir`,
  built from this branch), real bind mount, real `AO_WORKER_INSTANCE_URLS`, `--network
  none`, prod untouched. One word changed in the registry file between the two runs:

  ```
  === AS SHIPPED ===
  resolved pool  : [('worker-1','http://ao-worker-1:8090','little-coder'), …]
    impl for http://ao-worker-1:8090 -> LittleCoderHarness
  === SAME CONTAINER, ONE WORD CHANGED IN THE FILE ===
  resolved pool  : [('worker-1','http://ao-worker-1:8090','claude-code'), …]
    impl for http://ao-worker-1:8090 -> UnprovisionedHarness
  ```

- Degradation proven by accident and kept: an early run passed a path Git-Bash had
  mangled, and the bridge logged `runner registry … is not a readable file - falling
  back to the environment pool` and carried on with the correct pool. A mount that does
  not land does not take the org down.

## 6. Findings for someone else (deferred, not fixed here)

1. **`agent-org/README.md:121` says "55 tests".** The suite is far larger (see §5).
   Stale by a wide margin.
2. **The documented test install is incomplete, and `pytest-xdist` is absent
   everywhere.** `pip install -e .[test]` does not yield a collectable suite:
   `tests/test_github_app.py` needs `pyjwt` + `cryptography` and `tests/test_http_api.py`
   needs `websockets`, neither in `pyproject.toml`'s `[test]` extra. Collection is
   *interrupted*, so the whole run fails rather than those files. Checked 2026-08-30
   across all three virtualenvs that exist on this host — `agent-bridge/.venv`,
   `.venv-test`, `.testvenv`: `import xdist` fails in each, so any `-n <N>` invocation
   in a note or README is not reproducible. `.venv` and `.venv-test` do carry the
   `pyjwt`/`cryptography`/`websockets` extras; `.testvenv` does not. The absence of
   xdist is a convenience gap, not a correctness one - the serial suite finishes in
   9m27s on this host (see 5).
3. **`worker_instances.role` is documented as "profile bound while assigned"**
   (`models.py:218`) but nothing ever resolves it against `ProfileRegistry`. Either
   the comment is aspirational or the binding is missing; worth one look when U4's
   quadrant work needs per-role model selection on a worker.
4. **U4's first clause is still open.** `little-coder`'s `status` remains `unproven`
   and this branch does not change that — no work item has completed through it, and
   §8.3 is why: there is nothing to complete one *with*.
5. **The coder plane's published metrics port does not answer on the host.**
   `coder/docker-compose.yml:121` declares `127.0.0.1:9091:9090` and
   `docker inspect little-coder -f '{{json .HostConfig.PortBindings}}'` confirms
   `{"9090/tcp":[{"HostIp":"127.0.0.1","HostPort":"9091"}]}` — yet `docker ps` prints
   only `9090/tcp` for that container, and a host TCP connect to `127.0.0.1:9091` is
   REFUSED (verified from both Git-Bash `curl` and a PowerShell `TcpClient`, 2026-08-30)
   while `docker exec little-coder curl -s localhost:9090/metrics` returns **200**. So
   the metrics endpoint is up and the host-side publish is not. Whoever owns coder-plane
   observability should look; nothing in this branch depends on it, and
   `verify-runner-endpoint-check.ps1` deliberately opens its own loopback listener
   rather than assuming that port works.

## 7. Seams for the sibling branches

- **`work/u4disp` (harness → little-coder dispatch).** This is the branch that ends the
  park in §8.3, and two harness tests will fail the moment it does — deliberately:
  `test_the_harness_side_of_u4_is_declared_not_dispatching` and
  `test_no_profile_routes_a_role_to_a_pooled_runner`. They name what to re-state.
  Read the endpoint from `Get-HarnessRunner -Name little-coder` /
  `config.runner("little-coder")`; do not hardcode. The address is
  `http://little-coder:8090` and is **not reachable from the host** — a host-side
  dispatcher needs either a published `127.0.0.1:8090:8090` on
  `coder/docker-compose.yml` or to run inside `ai-stack_llm-net`. If you publish it,
  flip that row's `reachable_from` to include `host`, or `check-runner-endpoints.ps1`
  will fail you (in the *stale declaration* direction), and re-run
  `verify-runner-endpoint-check.ps1` because its `stale-host-claim` case assumes the
  little-coder row does not claim `host`.
  Do not add ao-workers to a harness dispatcher without agent-org's scheduler: its
  alloc lock, affinity and quarantine are what stop one daemon being double-booked —
  which is exactly what `test_no_profile_routes_a_role_to_a_pooled_runner` guards.
- **`work/u4oracle` (frontier-oracle-on-stall).** The claude-code worker door is the
  missing piece named in §3, and `UnprovisionedHarness` is where it plugs in: give
  `RunnerDispatch.default` a real `"claude-code"` implementation and nothing else in
  agent-org has to change. If the oracle goes through Mattermost rather than HTTP, that
  is still a `WorkerHarness` implementation — the Protocol says nothing about transport.
- **`work/u4quad` (runner × target quadrant).** `RunnerRegistry.kind_for(base_url)` is
  the runner axis, already resolved per worker; the target axis (self vs
  project(<repo>)) is not in the registry and should not be — it belongs to the effort,
  not the substrate. If the quadrant work needs the ao-workers as declared capacity,
  set `AO_WORKER_POOL_SOURCE=registry` explicitly — it is deliberately not the default
  (§8.2).

## 8. The review, and what it cost

The item was refuted 2 of 2, and the orchestrator re-verified the two most consequential
findings by reading the source. Both were correct. This section is the record.

### 8.1 The check could not fail for the rows it existed to validate — CORRECT

`check-runner-endpoints.ps1` shipped in the exact vacuity class this effort exists to
kill. Every row in the registry is a container-DNS address and none claims `host`, so
`$claimsHost` was false for all of them; a FAILED host probe then landed in
`else { $why = "not reachable from the host, as declared" }` with `$status` left `"ok"`.
**The failing probe was the passing branch.** The other leg called
`Get-ContainerNetworks`, which returns `$null` when the container does not exist, which
set `netStatus = "skip"` — and `skip` never incremented `$failed`. Net result: a row
naming a container that does not exist, on a port nothing listens on, PASSED. The port
was never validated on any container row. The header's *"Exit 0 = every declaration
matched reality"* was false, and the file's own comment — *"a check that quietly passes
when it cannot look is the exact failure class this file exists to catch"* — described
what it then did.

Reproduced independently before fixing, using the script as committed at `fe0adae`
against a registry whose little-coder port was changed to one nothing listens on:

```
$ AI_STACK_HARNESS_CONFIG=<copy with :9999> powershell -File <fe0adae check-runner-endpoints.ps1>
  [ok] little-coder/little-coder  http://little-coder:9999
       host: not reachable from the host, as declared
       nets[ok]: attached to ai-stack_llm-net, coder_lc-net, as declared
OLD exit=0
```

**The fix, and why this one.** Both remedies the review offered were taken, because
each covers a hole the other does not:

- *Probe from somewhere that can actually reach the address.* For every network a row
  declares, the check now finds a container ON that network and opens the URL from
  inside it (`docker exec <probe> curl <url>`). That exercises the container name AND
  the port, which is the only honest test of a container-DNS claim. Any completed HTTP
  exchange counts as reachable — a 404 proves the name resolved and the port answered,
  which is the question; only refused/unresolvable/timed-out is a miss.
- *Make "cannot look" a distinct non-zero outcome.* `skip` is gone. Exit 0 = checked and
  true, 1 = false, **2 = could not be checked**. A caller can now tell "the stack is
  down" from "the config is wrong", and neither is a pass.

Two implementation details cost a cycle each and are commented in place: under
`$ErrorActionPreference = "Stop"`, capturing a native command's stderr with `2>&1` turns
BusyBox `wget`'s "server returned error: HTTP/1.1 404" into a terminating error — so a
reachable endpoint read as a failure; and BusyBox `wget` exits 1 for a 404 and 1 for a
DNS failure alike, so the probe now *prefers* a curl-capable container and only falls
back to wget where a network has none.

**The acceptance test is mechanical and it lives in the repo.**
`scripts/agent-harness/verify-runner-endpoint-check.ps1` mutates a copy of the registry
six ways and asserts the exit code each mutation deserves. RED→GREEN, both directions:

```
$ powershell -File scripts/agent-harness/verify-runner-endpoint-check.ps1 -Quiet
[PASS] as-shipped             expected exit 0, got 0
[PASS] wrong-port             expected exit 1, got 1
[PASS] nonexistent-container  expected exit 2, got 2
[PASS] wrong-network          expected exit 1, got 1
[PASS] dead-host-claim        expected exit 1, got 1
[PASS] stale-host-claim       expected exit 1, got 1
6/6 cases produced the expected exit code - the check fails when it should.   (exit 0)

# the same drill, with check-runner-endpoints.ps1 reverted to fe0adae:
[FAIL] wrong-port             expected exit 1, got 0
[FAIL] nonexistent-container  expected exit 2, got 0
2 of 6 case(s) did not produce the expected exit code.                        (exit 1)
```

The `wrong-port` case is the acceptance test named in the review, and the drill is the
thing that stops this defect from being re-introduced by the next edit.

### 8.2 Clearing `AO_WORKER_INSTANCE_URLS` silently re-enabled the pool — CORRECT

`RunnerRegistry.load` read:

```python
pool = cls._pool_from_urls(fallback_urls)
if not pool:
    pool = cls._pool_from_specs(specs)
```

`agent-org/docker/docker-compose.yml:137` sets `AO_WORKER_INSTANCE_URLS:
${AO_WORKER_INSTANCE_URLS:-}` and its own comment documents that state as *"Empty in
P0-P4"*. So in the **documented default state** the branch turned an empty pool into
ao-worker-1 and ao-worker-2 — work capacity that did not exist before — and the
documented way to disable the pool (clear the variable) enabled it. The docstring's
claim that *"an operator's existing AO_WORKER_INSTANCE_URLS keeps winning and this
change cannot alter a live pool by accident"* was false in exactly the empty case, and
the rollback note's *"falls back to the pre-U4 behaviour"* was true only when the FILE
IS MISSING.

**Chosen: fix the BEHAVIOUR, and then fix every claim to match it.** The alternative —
keep the fallback and document it honestly — was rejected because it leaves a
deployment whose default state is a behaviour change nobody asked for, and §C.2 class 2
ranks reversibility above convenience. The alternative of deleting the file-derived pool
entirely was also rejected: `pooled` would then govern nothing on either side, which is
the decorative-config failure this phase is supposed to end.

So the source of the pool is now **explicit**, `AO_WORKER_POOL_SOURCE`:

| value | WHICH addresses are capacity | WHAT each address is |
|---|---|---|
| `env` **(default)** | `AO_WORKER_INSTANCE_URLS` alone. Empty ⇒ empty pool. Identical to pre-U4. | the shared file |
| `registry` | the file's `pooled: true` rows; the CSV is a fallback only if the file could not be read | the shared file |

An unrecognised value falls back to `env` with a warning rather than raising: a typo in
a compose variable must not stop the bridge, and `env` is the conservative answer
because it can only produce what the CSV already named.

RED→GREEN, at the registry level and end to end through `Orchestrator.setup()`:

```
# the shipped load() verbatim, restored into the current module as a RED probe:
FAILED tests/test_runner_registry.py::test_an_empty_env_pool_stays_empty_with_the_file_present
FAILED tests/test_runner_registry.py::test_an_unrecognised_pool_source_is_conservative
FAILED tests/test_runner_registry.py::test_startup_registers_no_worker_when_the_env_pool_is_empty
3 failed, 16 passed

# with the fix:
19 passed
```

`test_startup_registers_no_worker_when_the_env_pool_is_empty` is the one that answers
the review directly: it builds a real `Orchestrator` with the real shared file mounted
and `worker_instance_urls=""`, runs `setup()`, and asserts the `worker_instances` table
is EMPTY. `test_startup_registers_the_file_pool_only_when_asked` is its twin: same file,
same empty CSV, `AO_WORKER_POOL_SOURCE=registry`, and the two ao-workers appear.

Every claim that stated the old semantics has been corrected: the `RunnerRegistry`
docstring (which now names the defect the old wording caused), `Settings.
worker_pool_source` and `runner_registry_file` in `app/config.py`, the compose comment
at `agent-org/docker/docker-compose.yml`, the `_runners_comment` and `agent-org-worker`
row in `harness.config.json`, `MODULE.md`'s removal recipe, and §4 above.

### 8.3 "Both directions TRUE" was an over-claim — CORRECT, and the park is now asserted

The verifier confirmed the agent-org direction is real (`RunnerDispatch` is on the live
path via `app/main.py:124`; changing only the registry file reaches a different
implementation). It refuted the symmetry, and it was right: on the harness side there is
no dispatcher, no profile names `agent-org-worker`, and `Get-HarnessRunnerPool` has zero
executable callers. Verified again here, repo-wide with worktree noise excluded — the
only non-test consumer of any runner reader is `check-runner-endpoints.ps1`, which
*validates declarations* and dispatches nothing.

Both an over-claim and a too-easy park are failures, so the park is made mechanical
(§C.7 "andon, not silence"; §0 A6 "prose verification is FALSIFIED"). Two tests in
`scripts/agent-harness/test_harness_config.py`:

- `test_the_harness_side_of_u4_is_declared_not_dispatching` — fails the moment any
  harness `.ps1` other than `config.ps1` reads the runner pool, with a message naming
  the three claims to re-state in the same commit. Proved RED by dropping a two-line
  `.ps1` that calls `Get-HarnessRunnerPool` into the directory: 1 failed, 15 passed;
  removing it: 16 passed.
- `test_no_profile_routes_a_role_to_a_pooled_runner` — a harness profile that named a
  pooled daemon would hand out a workspace behind agent-org's scheduler's back, past the
  alloc lock that exists because two efforts were once bound to one worker.

`MODULE.md` now states the asymmetry as a table (dispatching vs declared-not-dispatching)
rather than a sentence, and PLAN §2's U4 row carries the amendment.

### 8.4 The line that installs the dispatcher was unguarded — CORRECT

The verifier reverted `RunnerDispatch.default(...)` back to `LittleCoderHarness(...)`
and 861/861 tests passed, because the end-to-end tests install the dispatcher themselves
(`orch.harness = RunnerDispatch(...)`). They proved `RunnerDispatch` consults the
registry; they did not prove the orchestrator uses `RunnerDispatch`.

`test_the_orchestrator_installs_the_registry_dispatcher` is that revert's repro. It
constructs an `Orchestrator` on the non-fake path and asserts `orch.harness` is a
`RunnerDispatch`, that its registry **is** `orch.runners` (so a throwaway registry does
not satisfy it), that `orch.router.harness` is the same object, and that a little-coder
address still reaches the real `LittleCoderHarness`.
`test_the_fake_adapter_still_bypasses_the_dispatcher` pins the other branch of the same
line. RED, with the installation line reverted exactly as the verifier reverted it:

```
FAILED tests/test_runner_registry.py::test_the_orchestrator_installs_the_registry_dispatcher
E   +  where <app.worker.harness.LittleCoderHarness object ...> = <Orchestrator ...>.harness
1 failed, 18 passed
```

### 8.5 Documentation claims that did not match execution — CORRECT

- `pytest -q -n 8 → 861 passed` was not reproducible as written: `pytest-xdist` is
  installed in none of the three agent-bridge virtualenvs (§6.2). The command is
  corrected in §5 and the number re-established serially.
- `MODULE.md`'s "wired and callable but unproven" was the exact phrasing already
  refuted by `u4-profile-mechanism-deadcode.md`. Replaced with the accurate statement —
  *the resolution is wired; the dispatch was never built* — plus the consequence that
  note draws: three of the four shipped profiles route a role to a runner nothing can
  execute on, and select silently.
- PLAN §2's U4 row asserted the sentence this note calls FALSE. Amended in place, on
  the record, with the reason and a pointer here.

---

## DECISIONS entries to append

Append verbatim to
`documentation/implementation-guide/dark-factory-unification/DECISIONS.md`. **Not
appended by this branch** — agents do not edit that file, so parallel branches cannot
collide on it; the orchestrator appends these at merge.

```
## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: U4's "one profile mechanism governs both" is recorded as FALSE and the
          phase amended on the record (PLAN §2's U4 row now carries the amendment).
          The two profile tables answer different questions - agent-org's binds a role
          to a model LANE for the bridge's own inference calls and has never governed
          a worker (only `po`/`pm`/`pm-voice` + the planner/stop-gate lenses reach
          ModelRouter; `worker-default` is a scheduler role string), while the
          harness's binds a role to a RUNNER. The unifiable object is one layer down:
          the RUNNER REGISTRY (what substrates exist, of what kind, at what address).
          That is what was unified.
CITED:    §C.1 (amend on the record and continue) + §C.2 class 2 (option consistent
          with the evidence, most reversible, closest to the house pattern - one
          config file, multiple readers, a cross-reader test).
REVERT:   Delete agent-org/agent-bridge/app/modules/runners.py and
          tests/test_runner_registry.py; in orchestrator.py restore
          `LittleCoderHarness(settings.worker_poll_interval_s,
          settings.worker_poll_timeout_s)` as the non-fake harness binding, restore
          `await self.scheduler.register_from_urls(self.s.worker_instance_urls)` in
          setup(), and drop `self.runners`; remove `runner_registry_file` and
          `worker_pool_source` from app/config.py; remove the registry bind mount,
          AO_RUNNER_REGISTRY_FILE and AO_WORKER_POOL_SOURCE from
          agent-org/docker/docker-compose.yml. agent-org returns to
          AO_WORKER_INSTANCE_URLS alone. The harness's `runners` block is additive and
          inert without a dispatcher, so it may stay; to remove it too, drop the
          runner reader functions from config.ps1/config.py, the runner tests from
          test_harness_config.py, and check-runner-endpoints.ps1 +
          verify-runner-endpoint-check.ps1.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: WHICH addresses are work capacity stays the environment's question.
          AO_WORKER_POOL_SOURCE=env is the default and means AO_WORKER_INSTANCE_URLS
          alone, so an EMPTY value is an EMPTY pool exactly as before U4;
          AO_WORKER_POOL_SOURCE=registry is an explicit opt-in to the shared file's
          `pooled: true` rows. Corrects a defect this branch shipped and a reviewer
          caught: letting the file fill an empty CSV meant the DOCUMENTED compose
          default silently created two workers, and clearing the variable - the
          documented way to disable the pool - turned it on.
CITED:    §C.2 class 2 (most reversible: the default configuration behaves exactly as
          it did before the change) + §C.7 (the claim must be executable - the
          behaviour is pinned by test_startup_registers_no_worker_when_the_env_pool_is_empty
          and test_startup_registers_the_file_pool_only_when_asked, both driving the
          real Orchestrator.setup()).
REVERT:   Set AO_WORKER_POOL_SOURCE=registry in agent-org/docker/docker-compose.yml to
          restore the file-derived pool deliberately; or delete `worker_pool_source`
          from app/config.py and the POOL_SOURCE_* branch in RunnerRegistry.load,
          leaving `pool = _pool_from_urls(...)` - which is the pre-U4 behaviour with
          the file supplying kinds only.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: The shared file decides WHAT each address is (its runner kind, which selects
          the WorkerHarness implementation); a `kind=url` entry in
          AO_WORKER_INSTANCE_URLS overrides it, and a bare URL - every operator's
          current shape - takes its kind from the file. A worker's kind is NOT stored
          on the `worker_instances` row: kind is a property of the ADDRESS and the
          registry is its single source of truth, so a persisted copy would drift the
          first time an operator re-points a URL. Pool instance ids stay positional
          `worker-<n>` because that column is the primary key and renaming it would
          orphan every live row's affinity and quarantine state.
CITED:    §A.2 (configuration over hardcoding; one config, multiple readers).
REVERT:   Have RunnerRegistry.__init__ resolve an unstated kind to DEFAULT_KIND instead
          of consulting the specs (the registry then governs nothing at run time, which
          is why it is not the default). For per-row persistence instead, add a
          ("worker_instances", "runner", "VARCHAR(32) DEFAULT 'little-coder'") row to
          Database._ADDITIVE_COLUMNS and have RunnerDispatch consult it; the
          additive-migration mechanism is already there.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: The two directions of U4 are recorded as UNEQUAL. "agent-org reads the shared
          registry" is DISPATCHING - RunnerDispatch is on the live wake path and the
          implementation that executes changes when the file changes. "the harness runs
          work on a runner" is DECLARED, NOT DISPATCHING, and is PARKED: the harness has
          no dispatcher at all, no profile names `agent-org-worker`, and
          Get-HarnessRunnerPool has zero executable callers. The park is asserted, not
          described, by test_the_harness_side_of_u4_is_declared_not_dispatching and
          test_no_profile_routes_a_role_to_a_pooled_runner, which fail the moment it
          ends and name the claims to re-state. Owner of the unpark: work/u4disp.
CITED:    §C.7 ("a phase that cannot satisfy its column parks with a written reason";
          "andon, not silence") + §0 A6 + documentation/notes/u4-profile-mechanism-deadcode.md
          ("importing the resolver is not consuming it").
REVERT:   Delete those two tests. Nothing else depends on them; the park then reverts to
          prose, which is the state this decision exists to leave behind.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: The "harness runner as an agent-org worker" runner is PARKED and the park is
          mechanical: routing to a `claude-code`-kind address raises
          RunnerNotProvisionedError naming the missing task endpoint and the U5
          containment work, rather than degrading to little-coder.
CITED:    §C.7 (park with a written reason) + §0 A6 (so the park is a raise, not a
          paragraph).
REVERT:   Supply a real `"claude-code"` implementation to RunnerDispatch.default.
          Nothing else in agent-org changes.

## 2026-08-30 · U4 (bidirectional clause) · class 1
DECISION: check-runner-endpoints.ps1 probes container-DNS rows from INSIDE a container
          on each declared network, and reports "cannot look" as exit 2 rather than a
          silent pass. As first shipped it could not fail for any row in the registry:
          no row claims `host`, so a FAILED host probe was the branch that recorded a
          pass, and a missing container made the network leg `skip`, which never
          counted. Its falsifiability is itself now checked by
          verify-runner-endpoint-check.ps1 (six mutations, six expected exit codes;
          2 of 6 fail against the original script). Both remain operator/drill scripts
          under scripts/agent-harness/, NOT pre-commit hooks: they need the stack
          running, and a hook that cannot run offline would be disabled within a week.
CITED:    §C.7 (verification replaces the operator's reading; an executable check that
          fails red) + §C.2 class 1 (house pattern - .githooks are offline structural
          checks).
REVERT:   `git checkout <this commit>~ -- scripts/agent-harness/check-runner-endpoints.ps1`
          and delete verify-runner-endpoint-check.ps1. Nothing imports either; the
          registry and the dispatch are unaffected. Doing so restores a check that
          cannot fail, which is why it is recorded here rather than left to a diff.
```
