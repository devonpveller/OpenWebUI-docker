"""ao-replancarry - OFFLINE end-to-end replay against the REAL worker image (opt-in).

Skipped unless `AO_E2E_OT_IMAGE` names an open-terminal image (the image the ao workers run, e.g.
`little-coder-open-terminal:bakeoff-c7`) and a real Docker engine is reachable. The unit suite runs
with `DOCKER_HOST=tcp://127.0.0.1:1` and therefore always skips this file.

What is real here (the channel both ao-planrevert rounds died on):
  * two containers of the worker image = worker-1 / worker-2, `--network none`, labelled
    `ai-stack.test=ao-replancarry`, removed at the end; `origin` is a bare repo on a shared,
    labelled named volume (file path remote - no network);
  * every bridge /check (the carry, the F33 delivery-branch sync, ao-plancheck's snapshot and
    check) is POSTed to the container's open-terminal `/execute` by little-coder's real
    `OpenTerminalClient.execute`, its `output` entries split by the real `parse_exec_output`,
    joined the way `daemon.run_check` joins them, cut to the 64 KB tail and passed through the real
    `redact_secrets` - and the commands run in the image's shell as its `user`, so every `git` is
    the image's /usr/local/bin/git = the git-proxy;
  * the "turns" (coding turn, plan turn) are shell commands sent the same way the agent's bash
    reaches open-terminal (ot-exec), so their writes - and the plan turn's commit - also pass the
    proxy.
What is not real: the agent-bridge/little-coder daemon processes (the orchestrator runs in-process
with FakeHarness hooks, as in test_ao_replancarry), the model, and the network. The operator-side
clone (`/project`) is done with the image's real git (`git.real`), as the daemon does.

Run (from agent-org/agent-bridge, Docker reachable, no DOCKER_HOST override):
  AO_E2E_OT_IMAGE=little-coder-open-terminal:bakeoff-c7 python -m pytest -q -s \
      tests/test_ao_replancarry_ot_e2e.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.chat import FakeChatAdapter
from app.config import Settings
from app.db import Database
from app.models import SCHED_COMPUTING, WorkerInstance
from app.modules.model_router import FakeModelClient
from app.orchestrator import Orchestrator
from app.worker.harness import FakeHarness

IMAGE = os.environ.get("AO_E2E_OT_IMAGE", "").strip()
pytestmark = pytest.mark.skipif(not IMAGE, reason="opt-in: set AO_E2E_OT_IMAGE to the worker's "
                                                   "open-terminal image (real Docker needed)")

ROOT = Path(__file__).resolve().parents[1]
LC_SRC = ROOT.parents[1] / "little-coder" / "src" / "littlecoder"
REPO = "https://github.com/c7-sandbox/ai-stack"
W1, W2 = "http://w1:8090", "http://w2:8090"
KEY = "e2e-" + uuid.uuid4().hex[:12]
CODING_FILE = "inference/llm-queue/tests/test_leak_repro.py"
CODING_TEXT = "def test_leak_repro():\n    assert False, 'repro: connection leak'\n"
TRACKED, TRACKED_TEXT, PLAN_SCRATCH = "README.md", "# sandbox\n", "PLAN_NOTES.md"
_FLAIL_OUT = ("FLAIL-GUARD: stopped the turn — rule=stale: 10 calls in a row brought back "
              "nothing new (window 10, novel 40/51, edits 1, since edit 18). The approach wasn't "
              "converging; a fresh plan is needed.")
_PLAN = ("UNDERSTANDING: fix the llm-queue connection leak.\nALREADY DONE: the repro test\n"
         "PLAN:\n1. close the upstream client\nWON'T DO: no unrelated refactors\nRISKS: none")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"lc_{name}", LC_SRC / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _docker(*args: str, input: bytes | None = None, check: bool = True,
            timeout: float = 600) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DOCKER_HOST"} \
        if os.environ.get("DOCKER_HOST", "").endswith("127.0.0.1:1") else None
    p = subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout,
                       env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"docker {args[:3]} failed: {p.stderr.decode(errors='replace')[-400:]}")
    return p


class Worker:
    """One container of the worker image = one pool worker's open-terminal + /workspace."""

    def __init__(self, name: str) -> None:
        self.name = name
        ot = _load("openterminal")
        self._redact = _load("sanitize").redact_secrets
        self.client = ot.OpenTerminalClient("http://ot", KEY,
                                            transport=httpx.MockTransport(self._forward))

    def _forward(self, request: httpx.Request) -> httpx.Response:
        """The HTTP hop to the container's open-terminal, made from inside it (no network)."""
        url = "http://127.0.0.1:8000" + request.url.raw_path.decode()
        args = ["exec", "-i", self.name, "curl", "-sS", "-X", request.method,
                "-H", f"Authorization: Bearer {KEY}", "-w", "\n%{http_code}", "--max-time", "400"]
        body = request.content or None
        if body:
            args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        p = _docker(*args, url, input=body)
        raw, _, code = p.stdout.rpartition(b"\n")
        return httpx.Response(int(code or 500), content=raw, request=request)

    def check(self, command: str, timeout: int = 600) -> tuple[int | None, str, bool]:
        """Exactly what the daemon's /check returns (little-coder daemon.run_check)."""
        res = self.client.execute(command, "/workspace", None, timeout)
        out = res.stdout or ""
        if res.stderr:
            out += ("\n" if out else "") + res.stderr
        return res.exit_code, self._redact(out[-65536:]), res.timed_out

    def turn(self, script: str) -> None:
        """A turn's shell command, the way the agent's bash reaches open-terminal (ot-exec)."""
        code, out, _t = self.check(script)
        assert code == 0, out

    def operator(self, script: str, user: str = "user") -> str:
        """Operator/daemon side (the /project clone): the image's real git, no proxy."""
        p = _docker("exec", "-u", user, "-e", "HOME=/home/user", self.name, "sh", "-c", script)
        return p.stdout.decode()

    def focus(self, fresh: bool) -> None:
        if fresh or not self.operator("test -d /workspace/.git && echo y").strip():
            self.operator("find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf {} + && "
                          "/usr/bin/git.real clone -q /origin/repo.git /workspace")

    def files(self) -> dict[str, str]:
        names = self.operator("cd /workspace && /usr/bin/git.real ls-files -z -co "
                              "--exclude-standard").split("\0")
        return {n: self.operator(f"cat /workspace/{shlex.quote(n)}") for n in names if n}

    def head(self) -> str:
        return self.operator("cd /workspace && /usr/bin/git.real rev-parse HEAD").strip()

    def denials(self) -> str:
        return self.operator("cat /var/log/git-proxy.log 2>/dev/null | grep DENIED || true",
                             user="root")


@pytest.fixture(scope="module")
def pool():
    tag = uuid.uuid4().hex[:8]
    vol = f"ao-replancarry-e2e-origin-{tag}"
    names = [f"ao-replancarry-e2e-w1-{tag}", f"ao-replancarry-e2e-w2-{tag}"]
    _docker("volume", "create", "--label", "ai-stack.test=ao-replancarry", vol)
    try:
        for n in names:
            _docker("run", "-d", "--rm", "--network", "none", "--name", n,
                    "--label", "ai-stack.test=ao-replancarry",
                    "--label", "ai-stack.harness.owner=wt-ao-replancarry",
                    "-e", f"OPEN_TERMINAL_API_KEY={KEY}", "-e", f"API_KEY={KEY}",
                    "-e", "GIT_DISCOVERY_ACROSS_FILESYSTEM=1", "-v", f"{vol}:/origin", IMAGE)
        for n in names:
            for _ in range(60):
                if _docker("exec", n, "curl", "-fsS", "http://127.0.0.1:8000/health",
                           check=False).returncode == 0:
                    break
                time.sleep(1)
            _docker("exec", "-u", "root", n, "sh", "-c",
                    "mkdir -p /workspace && chown user:user /workspace /origin")
        w = [Worker(n) for n in names]
        w[0].operator(
            "set -e; G=/usr/bin/git.real; $G init -q --bare -b main /origin/repo.git; "
            "rm -rf /tmp/seed; $G init -q -b main /tmp/seed; cd /tmp/seed; "
            "printf '# sandbox\\n' > README.md; mkdir -p inference/llm-queue/src; "
            "printf 'def run():\\n    return 1\\n' > inference/llm-queue/src/queue.py; "
            "$G add -A; $G -c user.name=op -c user.email=op@x.invalid commit -q -m base; "
            "$G push -q /origin/repo.git main")
        yield w
    finally:
        for n in names:
            _docker("rm", "-f", n, check=False)
        _docker("volume", "rm", "-f", vol, check=False)


async def _orch(db_url, pool, *, plan_on_w2: bool, plan_edits_coding: bool = False,
                plan_commits: bool = False):
    settings = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(ROOT / "profiles"), charters_dir=str(ROOT / "charters"),
        floor_dir=str(ROOT / "floor"), worker_instance_urls=f"{W1},{W2}",
        max_concurrent_workers=2, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off", worker_plan_gate="off",
        worker_flail_guard=True, sync_delivery_branch=True,
    )
    db = Database(db_url)
    harness = FakeHarness()
    orch = Orchestrator(settings, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=harness)
    await orch.setup()
    by_url = {W1: pool[0], W2: pool[1]}
    seen: set[str] = set()
    trace: dict = {"coding_url": None, "plan_url": None, "plan_saw": None, "exec_url": None,
                   "exec_saw": None, "exec_head": None, "checks": [], "exec_fresh": False,
             "denied_out": []}
    orig_set_project, orig_wake = harness.set_project, harness.wake

    async def set_project(base_url, repo, **kw):
        fresh = bool(kw.get("fresh")) or base_url not in seen
        if plan_on_w2 and base_url == W2 and trace["plan_url"] and not trace["exec_url"]:
            fresh = trace["exec_fresh"] = True       # C7e ev 33: `fresh: true` at base
        seen.add(base_url)
        by_url[base_url].focus(fresh)
        return await orig_set_project(base_url, repo, **kw)

    async def wake(base_url, session_id, prompt, **kw):
        w = by_url[base_url]
        if kw.get("plan_only"):
            trace["plan_url"], trace["plan_saw"] = base_url, w.files()
            s = ("printf 'scratch\\n' > PLAN_NOTES.md && printf 'edited by the plan turn\\n' "
                 ">> README.md")
            if plan_edits_coding:
                s += f" && printf '# clobbered\\n' > {CODING_FILE}"
            if plan_commits:
                s += (" && git add -A && GIT_AUTHOR_NAME=p GIT_AUTHOR_EMAIL=p@x.invalid "
                      "GIT_COMMITTER_NAME=p GIT_COMMITTER_EMAIL=p@x.invalid git commit -q -m plan")
            w.turn(s)
            harness.output_queue.insert(0, _PLAN)
        elif trace["coding_url"] is None:
            trace["coding_url"] = base_url
            w.turn(f"mkdir -p {os.path.dirname(CODING_FILE)} && printf "
                   f"{shlex.quote(CODING_TEXT.replace(chr(10), chr(92) + 'n'))} > {CODING_FILE}")
            harness.output_queue.insert(0, _FLAIL_OUT)
        elif trace["plan_url"] and not trace["exec_url"]:
            trace["exec_url"], trace["exec_saw"], trace["exec_head"] = (
                base_url, w.files(), w.head())
            harness.output_queue.insert(0, "Implemented the fix; tests pass.")
        return await orig_wake(base_url, session_id, prompt, **kw)

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        harness.checks.append({"base_url": base_url, "command": command})
        trace["checks"].append((base_url, command[:60]))
        code, out, timed = by_url[base_url].check(command, timeout=timeout)
        if "git-proxy: DENIED" in out:
            trace["denied_out"].append(out[-300:])
        return code, out, timed

    harness.set_project, harness.wake, harness.run_check = set_project, wake, run_check
    if plan_on_w2:
        orig = orch.scheduler.acquire

        async def acquire(effort_id, role, session_id, *a, **kw):
            if "~r1" not in session_id:
                return await orig(effort_id, role, session_id, *a, **kw)
            async with orch.db.session_factory() as s:
                w1 = (await s.execute(select(WorkerInstance).where(
                    WorkerInstance.base_url == W1))).scalar_one()
                prev, w1.sched_state = w1.sched_state, SCHED_COMPUTING
                await s.commit()
            try:
                return await orig(effort_id, role, session_id, *a, **kw)
            finally:
                async with orch.db.session_factory() as s:
                    w1 = (await s.execute(select(WorkerInstance).where(
                        WorkerInstance.base_url == W1))).scalar_one()
                    w1.sched_state = prev
                    await s.commit()

        orch.scheduler.acquire = acquire
    return orch, db, trace


async def _run(orch, db, name: str):
    import asyncio

    await orch.projects.add("ai-stack", REPO)
    # one origin serves every scenario: a distinct effort (= a distinct agent/<effort> branch) each
    eid, chan, root = await orch.router.open_effort(name, project="ai-stack")
    try:
        await orch.delegate(eid, chan, root, "fix the llm-queue connection leak")
        for _ in range(3):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks))
    finally:
        for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
            if t is not None:
                t.cancel()
    return eid


def _origin_ref(pool, branch: str) -> str:
    return pool[0].operator(f"/usr/bin/git.real --git-dir=/origin/repo.git rev-parse "
                            f"--verify -q refs/heads/{branch} || true").strip()


def _report(name: str, eid: str, orch_payloads: dict, trace: dict, pool) -> None:
    print(f"\n[e2e {name}] effort={eid} image={IMAGE}")
    print(json.dumps({k: v for k, v in orch_payloads.items()}, indent=1, default=str)[:3000])
    print(json.dumps({"coding": trace["coding_url"], "plan": trace["plan_url"],
                      "exec": trace["exec_url"], "exec_fresh": trace["exec_fresh"],
                      "exec_head": trace["exec_head"],
                      "exec_has_coding_file": CODING_FILE in (trace["exec_saw"] or {}),
                      "checks": trace["checks"]}, indent=1))
    print("proxy denials:", [w.denials() for w in pool], trace["denied_out"])


async def test_e2e_c7e_shape_execution_turn_on_worker_2_has_the_coding_file(db_url, pool):
    orch, db, trace = await _orch(db_url, pool, plan_on_w2=True)
    try:
        eid = await _run(orch, db, "ao-queue-c7e")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        plan = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        _report("c7e", eid, {"carried": carried, "plan_turn_wrote_files": plan}, trace, pool)
        assert trace["coding_url"] == W1 and trace["plan_url"] == W2 == trace["exec_url"]
        assert trace["exec_fresh"]
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT
        assert trace["plan_saw"].get(CODING_FILE) == CODING_TEXT
        assert carried and carried["pushed"] and carried["files"] == [CODING_FILE]
        assert carried["worker"] == "worker-1"
        assert _origin_ref(pool, f"agent/{eid}") == carried["commit"] == trace["exec_head"]
        assert {r["path"] for r in plan["reverted"]} == {PLAN_SCRATCH, TRACKED}, plan
        assert not any(w.denials() for w in pool) and not trace["denied_out"]
    finally:
        await db.dispose()


async def test_e2e_plan_turn_write_and_commit_after_the_wip_commit_is_reverted(db_url, pool):
    orch, db, trace = await _orch(db_url, pool, plan_on_w2=False, plan_edits_coding=True,
                                  plan_commits=True)
    try:
        eid = await _run(orch, db, "ao-queue-same")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        plan = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        _report("same-worker+plan-commit", eid,
                {"carried": carried, "plan_turn_wrote_files": plan}, trace, pool)
        assert trace["coding_url"] == trace["plan_url"] == trace["exec_url"] == W1
        assert plan["head_reset"] and {PLAN_SCRATCH, TRACKED, CODING_FILE} <= {
            r["path"] for r in plan["reverted"]}, plan
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT
        assert PLAN_SCRATCH not in trace["exec_saw"]
        assert trace["exec_head"] == carried["commit"] == _origin_ref(pool, f"agent/{eid}")
        assert not any(w.denials() for w in pool) and not trace["denied_out"]
    finally:
        await db.dispose()


def test_e2e_control_the_channel_does_report_a_proxy_denial(pool):
    """Positive control for the 'no denials' assertions above (runs last): a command the proxy
    refuses is visible both in the /check output and in the proxy's journal."""
    code, out, _t = pool[1].check("cd /workspace && git config user.name someone-else")
    assert code != 0 and "git-proxy: DENIED" in out, out
    assert "blocklist:config-write" in pool[1].denials()
