# U4 bidirectional runner unification — findings

Sink for `work/u4bidir` (DFU PLAN §2, U4's third clause: *"then agent-org workers as
harness runners and vice versa — one profile mechanism governs both"*). Written
2026-08-30 against the tips of `refactor/ai-stack-cleanup` (`5f4817d`) and the live
stack. Everything below was checked by reading the named file or running the named
command; where a claim is inferred rather than executed it says so.

Siblings `work/u4disp` (harness→little-coder dispatch), `work/u4oracle`
(frontier-oracle-on-stall) and `work/u4quad` (runner × target quadrant) were empty at
`5f4817d` when this was written; nothing here depends on their files. The seams they
need from this branch are listed at the end.

---

## The verdict, in one line

**The clause is HALF TRUE and the half that is false is the half the sentence leads
with.** "One *profile* mechanism governs both" is **FALSE** — the two profile tables
answer different questions and forcing them together makes both worse. What *is* one
mechanism, and is now genuinely shared, is the layer beneath: the **runner registry**.
Of the two directions, "agent-org worker as a harness runner" is **TRUE with a real
loss to name**; "harness runner as an agent-org worker" is **TRUE in mechanism and
PARKED on provisioning**, and the park is now mechanical rather than prose.

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
author). It never reaches `ModelRouter`. The worker's model is decided *inside
little-coder*, from that container's own `models.json` / `LC_CONFIG`; agent-org's
`worker_model: "qwen36-27b"` (`config.py:273`) is read only by
`app/evals/capability_floor.py`.

**The harness's profile** (`scripts/agent-harness/harness.config.json`) binds
`{runner, model}` to a role and decides which **agent program** performs the whole
role. It has no gateway, no charter, no caller key.

So a merged table would have to carry `caller_key` / `tool_access` / `temperature`
fields that mean nothing to a Claude Code agent, and a `runner` field that means
nothing to a bridge making its own inference call — there is no executor to pick when
the caller *is* the executor. Two tables, two questions. Left separate, deliberately.

**What IS one object:** *which execution substrates exist, of what kind, at what
address, with what proven status.* agent-org held that as `AO_WORKER_INSTANCE_URLS`, a
bare CSV with the kind implicit and always little-coder; the harness held it as
`runners{}`. Those are the same facts written twice, so they are now one file with
three readers (§4).

## 2. "agent-org worker as a harness runner" — TRUE, with two real losses

An ao-worker **is** a little-coder daemon addressed by `base_url`
(`agent-org/docker/docker-compose.yml:293`, `image: little-coder:local`,
healthcheck on `:8090`), and the harness already declares a `little-coder` runner
kind. At the level of "can the harness name one", yes.

Two things do not cross, and calling them cosmetic would be wrong:

**a. The unit of isolation is different.** A harness runner works in a git **worktree
on the host**. little-coder works in `config.workspace.path` (`/workspace`), a named
docker volume, populated *only* by `WorkspaceManager.clone` — and `set_project` WIPES
it on a project switch (`little-coder/src/littlecoder/daemon.py:488–556`). There is no
"operate in this existing directory" mode, and the host worktree is not mounted into
any of these containers (`coder/docker-compose.yml`: `little-coder-workspace:/workspace`;
ao-workers: `ao-worker-N-workspace:/workspace`). So handing a harness item to an
ao-worker means handing over a **branch**, never a path — and therefore a pushed
branch, an uncommitted working tree does not survive the trip. The `.env` files
`new-worktree.ps1` copies do not cross either.

**b. Reachability. The shipped address was dead.** `harness.config.json` declared the
little-coder runner at `http://127.0.0.1:8090`. The coder plane publishes
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
have read before wiring one up. Fixed (§4) and now guarded by a script that fails in
both directions.

## 3. "harness runner as an agent-org worker" — TRUE in mechanism, PARKED on provisioning

The protocol was never the blocker. `WorkerHarness`
(`agent-org/agent-bridge/app/worker/harness.py:97`) is an 8-method `Protocol` with two
implementations already (`LittleCoderHarness`, `FakeHarness`), and every one of the 15
call sites in `orchestrator.py` + `modules/router.py` already passes `inst.base_url`
as the first argument. The address was always the routing key.

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

That is fixed (§4). What remains genuinely missing is a claude-code **worker**, and it
is not a config flip:

- a Claude Code agent is a host process, not an HTTP task daemon. Nothing in this stack
  exposes `POST /tasks`, `GET /tasks/{id}`, `/project`, `/check`, `/cancel` for one.
- agent-org's containment posture — git-proxy, egress allowlist, default-deny, floor
  hook — is what makes a worker safe to dispatch to, and a host process has none of it.
  That is PLAN U5 (*containment parity*), a separate phase.

So the direction is **parked with a written reason**, and the park is executable:
`RunnerDispatch` routes a `claude-code`-kind address to `UnprovisionedHarness`, which
raises `RunnerNotProvisionedError` naming exactly what is missing. A wrapper that
degraded silently to little-coder would have passed every test and changed nothing.

## 4. What was built

| change | file | what it makes true |
|---|---|---|
| shared runner registry | `scripts/agent-harness/harness.config.json` | `runners{}` gains `reachable_from`, `pooled`, and agent-org's ao-worker pool; the dead `127.0.0.1:8090` becomes `http://little-coder:8090` |
| PS reader | `scripts/agent-harness/config.ps1` | `Get-HarnessRunner`, `Get-HarnessRunnerAddresses`, `Get-HarnessRunnerPool` |
| Python reader | `scripts/agent-harness/config.py` | `runner()`, `runner_addresses()`, `runner_pool()` |
| third reader + dispatcher | `agent-org/agent-bridge/app/modules/runners.py` | `RunnerRegistry` (reads the same file), `RunnerDispatch` (a `WorkerHarness` that routes by address), `UnprovisionedHarness` |
| per-instance selection | `agent-org/agent-bridge/app/orchestrator.py` | the global harness binding becomes `RunnerDispatch.default(self.runners)`; pool registration reads the registry |
| pool registration | `agent-org/agent-bridge/app/modules/scheduler.py` | `register_pool((id, url, kind))` beside the legacy `register_from_urls` |
| deployment | `agent-org/docker/docker-compose.yml` | the file is bind-mounted read-only at `/etc/agent-bridge/runner-registry.json` + `AO_RUNNER_REGISTRY_FILE` |
| the guard | `scripts/agent-harness/check-runner-endpoints.ps1` | `reachable_from` becomes a falsifiable claim |

**Precedence, and why it matters.** `AO_WORKER_INSTANCE_URLS` still says **which**
daemons are in the pool and still wins — a live pool cannot move by accident. The
shared file says **what** each address is (the runner kind that selects the
implementation). Getting that split backwards is exactly how a shared registry becomes
decorative, and an earlier draft of `RunnerRegistry.__init__` had the bug: it used
`setdefault`, so an operator entry that explicitly stated `little-coder=<url>` was
overridden by the file. `test_a_bare_env_url_takes_its_substrate_from_the_shared_file`
is that bug's repro.

## 5. Evidence

Executable, all re-runnable:

- `python -m pytest -q scripts/agent-harness/test_harness_config.py` — **14 passed**,
  including the PowerShell↔Python cross-reader test extended to the runner registry.
  Falsified deliberately: breaking `Get-HarnessRunnerAddresses` so it drops
  endpoint-only rows makes it FAIL with a concrete diff, and reverting makes it pass.
- `python -m pytest -q agent-org/agent-bridge/tests/test_runner_registry.py` —
  **11 passed** (needs `pip install -e .[test]` plus `pyjwt`, `cryptography`,
  `websockets`; see §6). One of them walks the `WorkerHarness` Protocol itself and
  asserts `RunnerDispatch` forwards every method — proved RED by deleting the
  `cancel_task` forwarding.
- **The whole agent-org suite: `pytest -q -n 8` → 861 passed** in 6m03s (the serial
  run does not finish inside a 15-minute window, hence `pytest-xdist`). The one
  warning is a pre-existing `Event loop is closed` ResourceWarning at teardown, not
  a failure. This matters because the change replaces the pool-registration call
  (`register_from_urls` → `register_pool`) and the harness binding that every one of
  those tests constructs.
- `powershell -File scripts/agent-harness/check-runner-endpoints.ps1` — exit **0** on
  the shipped registry; exit **1** on the pre-fix declaration
  (`declared reachable_from 'host' but the host cannot open it (…actively refused it
  127.0.0.1:8090)`); exit **1** on a stale declaration (an address that answers on the
  host without declaring it).
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

1. **`agent-org/README.md:121` says "55 tests".** The suite is far larger (it did not
   finish inside a 10-minute window on this host). Stale by a wide margin.
2. **The documented test install is incomplete.** `pip install -e .[test]` does not
   yield a collectable suite: `tests/test_github_app.py` needs `pyjwt` +
   `cryptography` and `tests/test_http_api.py` needs `websockets`, neither of which is
   in `pyproject.toml`'s `[test]` extra. Collection is *interrupted*, so the whole run
   fails, not just those files. One-line fix to the extra; not taken here because it is
   not this branch's subject.
3. **`worker_instances.role` is documented as "profile bound while assigned"**
   (`models.py:218`) but nothing ever resolves it against `ProfileRegistry`. Either
   the comment is aspirational or the binding is missing; worth one look when U4's
   quadrant work needs per-role model selection on a worker.
4. **U4's first clause is still open.** `little-coder`'s `status` remains `unproven`
   and this branch does not change that — no work item has completed through it. The
   registry now at least states a reachable address for whoever proves it.

## 7. Seams for the sibling branches

- **`work/u4disp` (harness → little-coder dispatch).** Read the endpoint from
  `Get-HarnessRunner -Name little-coder` / `config.runner("little-coder")`; do not
  hardcode. The address is `http://little-coder:8090` and is **not reachable from the
  host** — a host-side dispatcher needs either a published `127.0.0.1:8090:8090` on
  `coder/docker-compose.yml` or to run inside `ai-stack_llm-net`. If you publish it,
  flip that row's `reachable_from` to include `host`, or
  `check-runner-endpoints.ps1` will fail you (in the *stale declaration* direction).
  Do not add ao-workers to a harness dispatcher without agent-org's scheduler: its
  alloc lock, affinity and quarantine are what stop one daemon being double-booked.
- **`work/u4oracle` (frontier-oracle-on-stall).** The claude-code worker door is the
  missing piece named in §3, and `UnprovisionedHarness` is where it plugs in: give
  `RunnerDispatch.default` a real `"claude-code"` implementation and nothing else in
  agent-org has to change. If the oracle goes through Mattermost rather than HTTP, that
  is still a `WorkerHarness` implementation — the Protocol says nothing about transport.
- **`work/u4quad` (runner × target quadrant).** `RunnerRegistry.kind_for(base_url)` is
  the runner axis, already resolved per worker; the target axis (self vs
  project(<repo>)) is not in the registry and should not be — it belongs to the effort,
  not the substrate.

---

## DECISIONS entries to append

Append verbatim to
`documentation/implementation-guide/dark-factory-unification/DECISIONS.md`. Not
appended by this branch, per its instructions.

```
## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: U4's "one profile mechanism governs both" is recorded as FALSE and the
          phase amended on the record. The two profile tables answer different
          questions - agent-org's binds a role to a model LANE for the bridge's own
          inference calls and has never governed a worker (only `po`/`pm`/`pm-voice`
          + the planner/stop-gate lenses reach ModelRouter; `worker-default` is a
          scheduler role string), while the harness's binds a role to a RUNNER. The
          unifiable object is one layer down: the RUNNER REGISTRY (what substrates
          exist, of what kind, at what address). That is what was unified.
CITED:    §C.1 (amend on the record and continue) + §C.2 class 2 (option consistent
          with the evidence, most reversible, closest to the house pattern - one
          config file, multiple readers, a cross-reader test).
REVERT:   Delete app/modules/runners.py, restore the two lines in orchestrator.py
          that bound one harness globally, and drop the registry mount. agent-org
          returns to AO_WORKER_INSTANCE_URLS alone; the harness's `runners` block is
          additive and inert without a dispatcher.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: A worker's runner KIND is resolved from the shared registry, not stored on
          the `worker_instances` row. Kind is a property of the ADDRESS and the
          registry is its single source of truth; a persisted copy would drift the
          first time an operator re-points a URL - the same defect class the shared
          registry exists to end. Pool instance ids stay positional `worker-<n>`
          because that column is the primary key and renaming it would orphan every
          live row's affinity and quarantine state.
CITED:    §A.2 (configuration over hardcoding; one config, multiple readers).
REVERT:   Add a `("worker_instances", "runner", "VARCHAR(32) DEFAULT 'little-coder'")`
          row to Database._ADDITIVE_COLUMNS and have RunnerDispatch consult it. The
          additive-migration mechanism is already there; nothing else changes.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: `AO_WORKER_INSTANCE_URLS` keeps winning on WHICH addresses are in the pool;
          the shared file decides WHAT each address is. A bare URL (every operator's
          current shape) leaves its kind unstated and takes it from the file; a
          `kind=url` entry overrides the file.
CITED:    §C.2 class 2 (most reversible: a live pool cannot move by accident) + the
          harness's own precedence order, defaults < file < environment.
REVERT:   Have RunnerRegistry.__init__ resolve an unstated kind to DEFAULT_KIND
          instead of consulting the specs. The registry then governs nothing at run
          time, which is why it is not the default.

## 2026-08-30 · U4 (bidirectional clause) · class 2
DECISION: The "harness runner as an agent-org worker" direction is PARKED, and the
          park is mechanical: routing to a `claude-code`-kind address raises
          RunnerNotProvisionedError naming the missing task endpoint and the U5
          containment work, rather than degrading to little-coder.
CITED:    §C.7 ("a phase that cannot satisfy its column parks with a written reason";
          "andon, not silence") + §0 A6 (prose verification is FALSIFIED - so the
          park is a raise, not a paragraph).
REVERT:   Supply a real `"claude-code"` implementation to RunnerDispatch.default.
          Nothing else in agent-org changes.

## 2026-08-30 · U4 (bidirectional clause) · class 1
DECISION: `check-runner-endpoints.ps1` is an operator/drill script under
          scripts/agent-harness/, NOT a pre-commit hook. It needs the stack running,
          and a hook that cannot run offline would be disabled within a week.
CITED:    §C.2 class 1 (house pattern - .githooks are offline structural checks).
REVERT:   Move it to scripts/checks/ and add it to the hook chain.
```
