"""runners - ONE runner registry, read by this bridge AND by the session harness (DFU U4).

## Why this module exists

`documentation/implementation-guide/dark-factory-unification/PLAN.md` §2, phase U4, asserts
"agent-org workers as harness runners and vice versa - one profile mechanism governs both".
The audit behind `documentation/notes/u4bidir-findings.md` found the second half of that
sentence FALSE and the first half blocked by something small:

* **The profile tables are not one thing.** agent-org's `ProfileRegistry` binds
  `{lane, model, charter, temperature, tool_access, caller_key}` to a role and governs the
  bridge's OWN inference calls (pm / po / planner / the reviewer lenses). It has never
  governed a worker: `worker-default` reaches `Scheduler.acquire` and the git identity, and
  never `ModelRouter`. The session harness's profile binds `{runner, model}` to a role and
  governs which AGENT PROGRAM performs the whole role. Collapsing them would force the
  harness's roles to carry a `caller_key`/`tool_access` they cannot use and agent-org's
  judgment roles to carry a `runner` they do not have (the bridge makes the call itself -
  there is no executor to pick).
* **The unifiable object is one layer down: the RUNNER REGISTRY** - what execution
  substrates exist, of what kind, at what address, with what proven status. agent-org held
  it as a bare CSV of URLs (`AO_WORKER_INSTANCE_URLS`, kind implicit and always
  little-coder); the harness held it as `runners{}` in `harness.config.json`. Those two ARE
  the same object, so they became one file with (now) three readers - `config.ps1`,
  `config.py`, and this module - pinned together by cross-reader tests on both sides.

## Why a dispatcher and not just a lookup

`WorkerHarness` has been a pluggable Protocol since it was written (`LittleCoderHarness` +
`FakeHarness`). What blocked a heterogeneous pool was never the protocol: the orchestrator
selected ONE implementation for the whole pool, once, at construction, from
`settings.chat_adapter`. Every call site already passes `inst.base_url` as the first
argument, so `RunnerDispatch` implements the same Protocol and routes each call by that
address to the implementation the registry names. No call site changed; the selection did.

## What is deliberately NOT here

There is no working `claude-code` worker implementation, and this module does not pretend
otherwise. A claude-code agent is a host process, not an HTTP task daemon, and nothing in
this stack exposes one; routing to that kind raises `RunnerNotProvisionedError` naming the
missing door. That is PLAN §C.7's "park with a written reason" made mechanical - a wrapper
that quietly degraded to little-coder instead would pass every test and change nothing.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..worker.harness import LittleCoderHarness, WorkerHarness, WorkResult

log = logging.getLogger("agent_bridge.runners")

# The kind assumed for an address the registry does not name. It is little-coder because
# that is what every pooled worker in this stack has ever been - a NEW kind must be
# declared, never inferred, or a typo in a URL silently changes a worker's substrate.
DEFAULT_KIND = "little-coder"

# WHERE THE POOL COMES FROM - and why the default is the environment.
#
# The first cut of this module let a present registry file supply the pool whenever
# `AO_WORKER_INSTANCE_URLS` was empty. That was a real behaviour change hiding in a
# fallback: compose sets `AO_WORKER_INSTANCE_URLS: ${AO_WORKER_INSTANCE_URLS:-}` and its
# own comment documents that state as "Empty in P0-P4", so in the DOCUMENTED DEFAULT the
# org went from no capacity to two ao-workers - and clearing the variable, the documented
# way to disable the pool, silently ENABLED it instead.
#
# So the source is explicit. `env` is the default and is byte-for-byte the pre-U4
# behaviour: the CSV alone decides which addresses are work capacity, and an empty CSV
# means an empty pool. `registry` is an operator opting in to the shared file deciding it.
# The file is NOT inert under `env` - it still answers what KIND each address is, which is
# what selects the WorkerHarness implementation - but it cannot create capacity nobody
# asked for.
POOL_SOURCE_ENV = "env"
POOL_SOURCE_REGISTRY = "registry"
POOL_SOURCES = (POOL_SOURCE_ENV, POOL_SOURCE_REGISTRY)


class RunnerNotProvisionedError(RuntimeError):
    """A dispatch was routed to a runner kind that has no implementation here.

    Raised, never swallowed: the whole point of the registry is that the substrate a worker
    runs on is a stated fact. Degrading to some other kind would make the statement a lie
    at exactly the moment it mattered.
    """


@dataclass
class RunnerSpec:
    """One row of the shared registry - a substrate, not a role."""

    name: str
    kind: str = DEFAULT_KIND
    status: str = "unknown"            # proven | unproven | unknown  (see MODULE.md)
    endpoint: str = ""                 # a single address, for a non-pooled runner
    instances: "OrderedDict[str, str]" = field(default_factory=OrderedDict)
    # Which network vantage points can actually reach `endpoint`/`instances`. Declared, and
    # checked against reality by scripts/agent-harness/check-runner-endpoints.ps1 - the
    # shipped value was wrong for a year of nobody dispatching (see the findings note).
    reachable_from: list[str] = field(default_factory=list)
    # Whether THIS orchestrator may acquire the row's addresses as work capacity. Not a
    # synonym for addressable: the coder plane's `little-coder` is addressable from inside
    # llm-net and is deliberately NOT pooled - it is the operator's interactive daemon on a
    # single shared /workspace, and an org that acquired it would collide with a human
    # mid-task - a collision little-coder's own design already rejected as deterministic
    # (documentation/implementation-guide/little-coder/Self-improving-little-coder-design.md:656).
    pooled: bool = False


class RunnerRegistry:
    """The declared substrates, and the pool that follows from them.

    Two questions, two owners, and the split is the whole design:

    * **WHICH addresses are work capacity** - owned by `AO_WORKER_INSTANCE_URLS` unless an
      operator explicitly hands that question to the file with
      `AO_WORKER_POOL_SOURCE=registry`. Under the default the file cannot add a worker,
      which is why an empty CSV means an empty pool exactly as it did before U4.
    * **WHAT each address is** - the runner kind, which selects the `WorkerHarness`
      implementation. Owned by the shared file, because a bare URL (every operator's
      current shape) states an address and nothing else. A `kind=url` entry in the CSV
      states its own substrate and overrides the file.

    An earlier version of this docstring claimed the environment "keeps winning" outright.
    It did not: with the file present and the CSV empty, the file supplied the pool. See
    `POOL_SOURCE_ENV` above for what that cost and how it is now prevented.
    """

    def __init__(
        self, specs: dict[str, RunnerSpec], pool: list[tuple[str, str, str | None]]
    ) -> None:
        self.specs = specs
        # Addresses the FILE declares, and what substrate it says each one is.
        self._kind_by_url: dict[str, str] = {}
        for spec in specs.values():
            for url in spec.instances.values():
                self._kind_by_url.setdefault(url, spec.kind)
            if spec.endpoint:
                self._kind_by_url.setdefault(spec.endpoint, spec.kind)
        # Resolve each pool entry's kind. A `kind=url` entry in the environment states its own
        # substrate and wins; a BARE url (the shape every operator has today) states only an
        # address, so its substrate comes from the shared file, and only then from the
        # documented default. Getting this order wrong is how a shared registry becomes
        # decorative: the environment names WHICH daemons are in the pool, the file says WHAT
        # each one is, and neither answer is the other's to give.
        resolved: list[tuple[str, str, str]] = []
        for instance_id, url, kind in pool:
            resolved.append(
                (instance_id, url, kind or self._kind_by_url.get(url) or DEFAULT_KIND)
            )
        self._pool = resolved
        # Assign, not setdefault: a pool entry's resolved kind is already the file's answer
        # when the entry was bare, so the only case this overwrites is an entry that STATED
        # its kind - which is exactly the case that must win. An earlier draft used
        # setdefault here and `kind_for` kept returning the file's answer for an address the
        # operator had explicitly overridden; the test above is that bug's repro.
        for _id, url, kind in resolved:
            self._kind_by_url[url] = kind

    # ── loading ─────────────────────────────────────────────────────────────
    @classmethod
    def load(
        cls,
        registry_file: str = "",
        fallback_urls: str = "",
        pool_source: str = POOL_SOURCE_ENV,
    ) -> "RunnerRegistry":
        """Read the shared registry file; take the POOL from the source the operator named.

        `pool_source` defaults to `env` - the CSV, and only the CSV, decides which addresses
        are work capacity. An empty CSV therefore yields an empty pool with the file fully
        present, which is the pre-U4 behaviour and is deliberately NOT a fallback into the
        file (see POOL_SOURCE_ENV for the defect that cost).

        `pool_source=registry` is the opt-in: the file's `pooled` rows become the pool. Only
        there does the CSV act as a fallback, and only when the file could not be read at
        all - a bind-mount that did not land must not take the org down. That failure mode
        is not hypothetical here: a bind-mount of a MISSING file yields a silent empty
        DIRECTORY on this host, so `_read` also has to survive being handed one.

        An unrecognised value falls back to `env` with a warning rather than raising: a
        typo in a compose variable must not stop the bridge from starting, and `env` is the
        conservative answer (no capacity invented).
        """
        specs = cls._read(registry_file)
        src = (pool_source or POOL_SOURCE_ENV).strip().lower()
        if src not in POOL_SOURCES:
            log.warning(
                "AO_WORKER_POOL_SOURCE=%r is not one of %s - using %r",
                pool_source, ", ".join(POOL_SOURCES), POOL_SOURCE_ENV,
            )
            src = POOL_SOURCE_ENV
        if src == POOL_SOURCE_REGISTRY:
            pool = cls._pool_from_specs(specs)
            if not pool:
                log.warning(
                    "AO_WORKER_POOL_SOURCE=registry but %s declares no pooled runner "
                    "(unreadable, or every row is pooled:false) - falling back to the "
                    "environment pool", registry_file or "(no registry file)",
                )
                pool = cls._pool_from_urls(fallback_urls)
        else:
            pool = cls._pool_from_urls(fallback_urls)
        return cls(specs, pool)

    @staticmethod
    def _read(registry_file: str) -> dict[str, RunnerSpec]:
        if not registry_file:
            return {}
        p = Path(registry_file)
        if not p.is_file():
            log.warning("runner registry %s is not a readable file - falling back to the "
                        "environment pool", registry_file)
            return {}
        try:
            data: Any = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("runner registry %s unreadable (%s) - falling back to the "
                        "environment pool", registry_file, exc)
            return {}
        raw = (data or {}).get("runners") or {}
        specs: dict[str, RunnerSpec] = {}
        for name, row in raw.items():
            if name.startswith("_") or not isinstance(row, dict):
                continue          # `_comment` keys are documentation, not rows
            inst = OrderedDict()
            for label, url in (row.get("instances") or {}).items():
                if not label.startswith("_") and isinstance(url, str) and url:
                    inst[label] = url
            specs[name] = RunnerSpec(
                name=name,
                kind=str(row.get("kind") or name),
                status=str(row.get("status") or "unknown"),
                endpoint=str(row.get("endpoint") or ""),
                instances=inst,
                reachable_from=[str(x) for x in (row.get("reachable_from") or [])],
                pooled=bool(row.get("pooled", False)),
            )
        return specs

    @staticmethod
    def _pool_from_urls(csv: str) -> list[tuple[str, str, str | None]]:
        """Parse `AO_WORKER_INSTANCE_URLS`. An entry may now carry its kind as `kind=url`;
        a bare URL leaves the kind UNSTATED (None) for `__init__` to resolve against the file.

        Instance ids stay positional `worker-<n>`, exactly as `Scheduler.register_from_urls`
        has always produced them: the id is the `worker_instances` PRIMARY KEY, and renaming
        it would orphan every live row's affinity/quarantine state rather than migrate it.
        """
        out: list[tuple[str, str, str | None]] = []
        for i, entry in enumerate(e.strip() for e in (csv or "").split(",") if e.strip()):
            kind, sep, url = entry.partition("=")
            if not sep or "://" in kind:      # a bare URL (the shape every operator has today)
                kind, url = "", entry         # "" = "unstated", resolved against the file
            out.append((f"worker-{i + 1}", url.strip(), kind.strip() or None))
        return out

    @staticmethod
    def _pool_from_specs(specs: dict[str, RunnerSpec]) -> list[tuple[str, str, str | None]]:
        """Only `pooled` rows become work capacity - see RunnerSpec.pooled for why a row can
        be addressable and still not be the org's to acquire."""
        out: list[tuple[str, str, str]] = []
        for spec in specs.values():
            if not spec.pooled:
                continue
            for url in spec.instances.values():
                out.append((f"worker-{len(out) + 1}", url, spec.kind))
        return out

    # ── questions ───────────────────────────────────────────────────────────
    def pool(self) -> list[tuple[str, str, str]]:
        """(instance_id, base_url, runner_kind) for every pooled worker, in order."""
        return list(self._pool)

    def kind_for(self, base_url: str) -> str:
        """The runner kind serving an address. Unregistered addresses get DEFAULT_KIND -
        stated here rather than guessed at each call site."""
        return self._kind_by_url.get(base_url, DEFAULT_KIND)

    def kinds(self) -> set[str]:
        return {k for _i, _u, k in self._pool} | {s.kind for s in self.specs.values()}


class UnprovisionedHarness:
    """A `WorkerHarness` for a kind this deployment cannot actually run.

    Every method raises, with the reason. This is the honest shape for the "vice versa"
    half of U4: agent-org can ADDRESS a claude-code worker the moment one exists, and until
    then a dispatch to that kind fails where it is visible instead of silently running
    somewhere else.
    """

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason

    def _raise(self, method: str):
        raise RunnerNotProvisionedError(
            f"runner kind '{self.kind}' has no implementation in this deployment "
            f"({method}): {self.reason}"
        )

    async def wake(self, base_url, session_id, prompt, **kw) -> WorkResult:  # noqa: ANN001
        self._raise("wake")

    async def set_project(self, base_url, repo, **kw):  # noqa: ANN001
        self._raise("set_project")

    async def current_focus(self, base_url):  # noqa: ANN001
        self._raise("current_focus")

    async def add_submodule(self, base_url, url, path, **kw):  # noqa: ANN001
        self._raise("add_submodule")

    async def run_check(self, base_url, command, **kw):  # noqa: ANN001
        self._raise("run_check")

    async def cancel_task(self, base_url, task_id):  # noqa: ANN001
        self._raise("cancel_task")

    async def has_running_task(self, base_url):  # noqa: ANN001
        self._raise("has_running_task")

    async def running_task_progress(self, base_url, *a, **kw):  # noqa: ANN001
        self._raise("running_task_progress")


CLAUDE_CODE_NOT_PROVISIONED = (
    "a claude-code agent is a host process, not an HTTP task daemon, and nothing in this "
    "stack exposes one to the bridge. Wiring it means giving it the WorkerHarness surface "
    "(POST /tasks, GET /tasks/{id}, /project, /check, /cancel) plus agent-org's containment "
    "posture (PLAN U5) - not a config flip."
)


class RunnerDispatch:
    """A `WorkerHarness` that routes each call to the implementation the registry names.

    It implements the Protocol itself, so every existing call site - all of which already
    pass `inst.base_url` first - keeps working untouched. That is the whole trick: the
    address was always the routing key; nothing was reading it.
    """

    def __init__(self, impls: dict[str, WorkerHarness], registry: RunnerRegistry) -> None:
        self.impls = impls
        self.registry = registry

    @classmethod
    def default(
        cls, registry: RunnerRegistry, *, poll_interval_s: float = 3.0,
        poll_timeout_s: float = 5400.0,
    ) -> "RunnerDispatch":
        return cls(
            {
                "little-coder": LittleCoderHarness(poll_interval_s, poll_timeout_s),
                "claude-code": UnprovisionedHarness("claude-code", CLAUDE_CODE_NOT_PROVISIONED),
            },
            registry,
        )

    def impl_for(self, base_url: str) -> WorkerHarness:
        kind = self.registry.kind_for(base_url)
        impl = self.impls.get(kind)
        if impl is None:
            raise RunnerNotProvisionedError(
                f"runner kind '{kind}' (for {base_url}) is declared in the registry but this "
                f"bridge has no implementation for it - known kinds: "
                f"{', '.join(sorted(self.impls)) or '(none)'}"
            )
        return impl

    # ── the WorkerHarness surface, delegated ────────────────────────────────
    async def wake(self, base_url, session_id, prompt, **kw):  # noqa: ANN001
        return await self.impl_for(base_url).wake(base_url, session_id, prompt, **kw)

    async def set_project(self, base_url, repo, **kw):  # noqa: ANN001
        return await self.impl_for(base_url).set_project(base_url, repo, **kw)

    async def current_focus(self, base_url):  # noqa: ANN001
        return await self.impl_for(base_url).current_focus(base_url)

    async def add_submodule(self, base_url, url, path, **kw):  # noqa: ANN001
        return await self.impl_for(base_url).add_submodule(base_url, url, path, **kw)

    async def run_check(self, base_url, command, **kw):  # noqa: ANN001
        return await self.impl_for(base_url).run_check(base_url, command, **kw)

    async def cancel_task(self, base_url, task_id):  # noqa: ANN001
        return await self.impl_for(base_url).cancel_task(base_url, task_id)

    async def has_running_task(self, base_url):  # noqa: ANN001
        return await self.impl_for(base_url).has_running_task(base_url)

    async def running_task_progress(self, base_url, *a, **kw):  # noqa: ANN001
        return await self.impl_for(base_url).running_task_progress(base_url, *a, **kw)
