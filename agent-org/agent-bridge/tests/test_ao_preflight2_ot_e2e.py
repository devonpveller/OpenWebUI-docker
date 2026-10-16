"""ao-preflight2 - OFFLINE end-to-end on the REAL worker image (opt-in), items (2)-(5).

Same rig as test_ao_replancarry_ot_e2e (reused: `Worker`, `_orch`, `_run`), relabelled
`ai-stack.test=ao-preflight2`: two containers of the worker image `--network none --rm`, origin =
a bare repo on a labelled volume, every /check POSTed to the container's open-terminal by
little-coder's real `OpenTerminalClient`, joined / cut / `redact_secrets`-ed like the daemon, and
every `git` in the image is its git-proxy. Skipped unless `AO_E2E_OT_IMAGE` is set.

  (2) a carry, then the stopped task's 3000 late untracked files, then a plan turn that
      `git add -A` + commits them: the check (paged baseline) leaves them UNTRACKED, so the
      execution wake's F33 `checkout -f -B` keeps every one;
  (3) ignore rules: the plan turn rewrites .gitignore and commits (`add -A` sweeps the earlier
      turn's ignored files in), or hides its own files through .git/info/exclude, or configures a
      hanging smudge filter through .git/info/attributes;
  (4)/(5) through `Orchestrator._revert_plan_turn_writes` itself: the snapshot dir replaced by a
      file, and a forged journal.

Run (agent-org/agent-bridge, Docker reachable):
  PYTHONIOENCODING=utf-8 AO_E2E_OT_IMAGE=little-coder-open-terminal:dotnet8-gui \
      python -m pytest -q -s tests/test_ao_preflight2_ot_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import types
import uuid

import pytest

import app.modules.plan_turn_guard as ptg
import test_ao_replancarry_ot_e2e as base
from test_ao_replancarry_ot_e2e import (
    CODING_FILE,
    CODING_TEXT,
    IMAGE,
    KEY,
    PLAN_SCRATCH,
    TRACKED,
    TRACKED_TEXT,
    W1,
    _docker,
    _orch,
    _origin_ref,
    _report,
    _run,
)

pytestmark = pytest.mark.skipif(not IMAGE, reason="opt-in: set AO_E2E_OT_IMAGE")

# c7f-int: an integration run labels its containers/volumes as its own item (default unchanged)
LABEL = "ai-stack.test=" + (os.environ.get("AO_E2E_TEST_LABEL", "").strip() or "ao-preflight2")
N_LATE = 3000


@pytest.fixture(scope="module")
def pool():
    tag = uuid.uuid4().hex[:8]
    vol = f"ao-preflight2-e2e-origin-{tag}"
    names = [f"ao-preflight2-e2e-w1-{tag}", f"ao-preflight2-e2e-w2-{tag}"]
    _docker("volume", "create", "--label", LABEL, vol)
    try:
        for n in names:
            _docker("run", "-d", "--rm", "--network", "none", "--name", n, "--label", LABEL,
                    "--label", "ai-stack.harness.owner=wt-" + LABEL.split("=", 1)[1],
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
        w = [base.Worker(n) for n in names]
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


_DUMP = ("import json,subprocess; "
         "names=subprocess.run(['/usr/bin/git.real','ls-files','-z','-co','--exclude-standard'],"
         "cwd='/workspace',capture_output=True).stdout.decode('utf-8','surrogateescape')"
         ".split(chr(0)); "
         "print(json.dumps({n: open('/workspace/'+n,encoding='utf-8',errors='replace').read() "
         "for n in names if n}))")


def _fast_files(self):
    return json.loads(self.operator("python3 -c " + shlex.quote(_DUMP)))


# -- (2) --------------------------------------------------------------------------------------
async def test_e2e_late_files_the_plan_turn_staged_survive_the_execution_sync(db_url, pool,
                                                                              monkeypatch):
    monkeypatch.setattr(base.Worker, "files", _fast_files)
    seen = {"pages": 0}
    orig_pc = ptg.page_command

    def page_command(token, off, n=ptg.PAGE_CHARS):
        seen["pages"] += 1
        return orig_pc(token, off, n)
    monkeypatch.setattr(ptg, "page_command", page_command)
    orch, db, trace = await _orch(db_url, pool, plan_on_w2=False, plan_edits_coding=True,
                                  plan_commits=True)
    harness = orch.harness
    inner = harness.run_check

    async def run_check(base_url, command, *, cwd=None, timeout=600):
        res = await inner(base_url, command, cwd=cwd, timeout=timeout)
        if "AO-CARRY-BEGIN" in (res[1] or "") and "late" not in trace:
            trace["late"] = True        # the stopped task's late writes, AFTER the carry
            pool[0].operator(
                "cd /workspace && python3 -c \"import os,secrets\n"
                "os.makedirs('late', exist_ok=True)\n"
                f"for i in range({N_LATE}):\n"
                "  open('late/f_%04d_%s.txt' % (i, secrets.token_hex(6)), 'w')"
                ".write(secrets.token_hex(32))\"")
        return res
    harness.run_check = run_check
    inner_sp = harness.set_project

    async def set_project(base_url, repo, **kw):
        if base_url == W1 and trace["plan_url"] and not trace["exec_url"] \
                and "w1_after_plan" not in trace:
            g = "cd /workspace && /usr/bin/git.real "
            trace["w1_after_plan"] = {
                "head": pool[0].head(),
                "tracked_status": pool[0].operator(g + "status --porcelain --untracked-files=no"),
                "untracked_late": len([x for x in pool[0].operator(
                    g + "ls-files -o --exclude-standard -- late").splitlines() if x.strip()]),
            }
        return await inner_sp(base_url, repo, **kw)
    harness.set_project = set_project
    try:
        eid = await _run(orch, db, "ao-preflight2-late")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        plan = await orch._last_event_payload(eid, "plan_turn_wrote_files")
        _report("preflight2-late", eid, {"carried": carried, "plan": {
            k: v for k, v in (plan or {}).items() if k not in ("kept_before_turn", "unstaged")},
            "pages": seen["pages"]}, trace, pool)
        wip = carried["commit"]
        a = trace["w1_after_plan"]
        print("w1 after plan:", a["head"], repr(a["tracked_status"][:200]), a["untracked_late"])
        assert trace.get("late") and seen["pages"] > 0
        assert plan["complete"] and plan["head_reset"]
        assert {PLAN_SCRATCH, TRACKED, CODING_FILE} <= {r["path"] for r in plan["reverted"]}
        assert plan["kept_before_turn_count"] == N_LATE
        assert plan["unstaged_count"] == N_LATE, plan["unstaged_count"]
        assert a["head"] == wip and a["tracked_status"].strip() == "", a["tracked_status"][:300]
        assert a["untracked_late"] == N_LATE
        late_in_exec = sum(1 for f in trace["exec_saw"] if f.startswith("late/"))
        assert late_in_exec == N_LATE, late_in_exec
        assert trace["exec_head"] == wip == _origin_ref(pool, f"agent/{eid}")
        assert trace["exec_saw"].get(CODING_FILE) == CODING_TEXT
        assert trace["exec_saw"].get(TRACKED) == TRACKED_TEXT
        assert not any(w.denials() for w in pool) and not trace["denied_out"]
    finally:
        await db.dispose()


# -- (3)-(5): the check on the real channel --------------------------------------------------------
_REPO = ("cd / && find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf {} + && cd /workspace && "
         "G=/usr/bin/git.real && $G init -q -b main && $G config user.email w@x.invalid && "
         "$G config user.name w && printf 'build/\\n*.env\\n' > .gitignore && "
         "printf '*.txt text\\n' > .gitattributes && printf 'r\\n' > README.md && "
         "mkdir -p trk && printf 'orig\\n' > trk/t.txt && $G add -A && $G commit -q -m base && "
         "mkdir -p build cfg && printf 'coding build\\n' > build/out.bin && "
         "printf 'CODING_SETTING=1\\n' > local.env && printf 'CFG=1\\n' > cfg/app.env && "
         "printf 'coding\\n' > coding.py && echo REPO-OK")
_IGNORED = {"build/out.bin": "coding build\n", "local.env": "CODING_SETTING=1\n",
            "cfg/app.env": "CFG=1\n"}


def _cat(w, path: str) -> str | None:
    out = w.operator(f"cat /workspace/{shlex.quote(path)} 2>/dev/null || echo __MISSING__")
    return None if out.strip() == "__MISSING__" else out


def _setup(w) -> dict:
    assert "REPO-OK" in w.operator(_REPO)
    tok = ptg.new_token()

    async def run(cmd):
        return w.check(cmd, timeout=120)[1]
    raw = asyncio.run(ptg.take_baseline(run, tok))
    snap = ptg.parse_snapshot(raw)
    assert snap is not None and isinstance(snap["manifest"], dict)
    return {"tok": tok, "snap": snap, "raw": raw, "n0": len(w.denials().splitlines())}


def _check(w, st: dict, timeout: int = 300) -> dict | None:
    for cmd in ptg.stage_commands(st["tok"], st["snap"]):
        w.check(cmd, timeout=120)
    return ptg.parse_check(w.check(ptg.check_command(st["tok"], st["snap"]), timeout=timeout)[1])


_PLANS = {
    "gitignore-rewrite-commit": ("printf '# rewritten by the plan turn\\n' > .gitignore && "
                                 "printf 'p\\n' > PLAN.md && git add -A && "
                                 "GIT_AUTHOR_NAME=p GIT_AUTHOR_EMAIL=p@x.invalid "
                                 "GIT_COMMITTER_NAME=p GIT_COMMITTER_EMAIL=p@x.invalid "
                                 "git commit -q -m plan"),
    "gitignore-rewrite": "printf '# rewritten\\n' > .gitignore && printf 'p\\n' > PLAN.md",
    "exclude-hide": ("printf '*.plan\\n' >> .git/info/exclude && for i in 0 1 2; do "
                     "printf p > x$i.plan; done && printf 'p\\n' > PLAN.md"),
    "smudge-hang": ("printf 'plan clobber\\n' > coding.py && printf 'plan\\n' >> trk/t.txt && "
                    "printf '*.txt filter=slow\\n' > .git/info/attributes && "
                    "printf '[filter \"slow\"]\\n\\tsmudge = sleep 75; cat\\n' >> .git/config"),
}


@pytest.mark.parametrize("plan", list(_PLANS))
def test_e2e_ignore_and_attribute_rules(pool, plan):
    w = pool[1]
    st = _setup(w)
    w.turn("cd /workspace && " + _PLANS[plan])
    t0 = time.monotonic()
    rep = _check(w, st, timeout=60 if plan == "smudge-hang" else 300)
    secs = time.monotonic() - t0
    status = w.operator("cd /workspace && /usr/bin/git.real status --porcelain -uall")
    print(f"\n[{plan}] {secs:.1f}s reverted={[r['path'] for r in (rep or {}).get('reverted', [])]}"
          f" unstaged={(rep or {}).get('unstaged')} status={status!r}")
    assert rep is not None and rep["status"] == "ok", rep
    for p, text in _IGNORED.items():
        assert _cat(w, p) == text, p
    assert _cat(w, ".gitignore") == "build/\n*.env\n"
    assert _cat(w, "coding.py") == "coding\n"
    assert _cat(w, "PLAN.md") is None
    assert status.splitlines() == ["?? coding.py"], status
    if plan == "exclude-hide":
        assert all(_cat(w, f"x{i}.plan") is None for i in range(3))
    if plan == "smudge-hang":
        assert _cat(w, "trk/t.txt") == "orig\n" and secs < 50, secs
    assert len(w.denials().splitlines()) == st["n0"]


class _Audit:
    def __init__(self):
        self.ev = []

    async def log(self, name, effort_id=None, payload=None, **kw):
        self.ev.append((name, payload))


class _Comms:
    def __init__(self):
        self.posts = []

    async def post(self, intent, text, **kw):
        self.posts.append(text)


@pytest.mark.parametrize("sabotage", ["snapfile", "forge_journal"])
def test_e2e_sabotaged_snapshot_dir_and_forged_journal(pool, sabotage):
    """(4)/(5) through the orchestrator method itself (fake `self`: audit/comms recorders and a
    router whose exec_check runs on the container), as tester t2's mech.py did."""
    from app.orchestrator import Orchestrator

    w = pool[1]
    st = _setup(w)
    extra = {
        "snapfile": "rm -rf .git/ao-plan-snapshot && printf x > .git/ao-plan-snapshot",
        "forge_journal": ("python3 -c \"import base64,json,zlib\nS='.git/ao-plan-snapshot'\n"
                          "t=json.loads(zlib.decompress(base64.b64decode(open(S+'/state.b64')"
                          ".read())))['token']\nopen(S+'/journal-'+t+'.jsonl','w').write("
                          "json.dumps({'k':'r','path':'FAKE.txt','why':'forged'})+chr(10))\""),
    }[sabotage]
    w.turn("cd /workspace && printf 'p\\n' > PLAN.md && printf 'plan\\n' >> trk/t.txt && "
           + extra)

    class Router:
        async def exec_check(self, effort_id, *, command, timeout=900, **kw):
            return w.check(command, timeout=timeout)

    fake = types.SimpleNamespace(audit=_Audit(), comms=_Comms(), router=Router())
    res = types.SimpleNamespace(instance_id="worker-2",
                                pre_turn_output=st["raw"],
                                pre_turn_note="")
    wrote = asyncio.run(Orchestrator._revert_plan_turn_writes(fake, "eff-pf2", res, st["tok"]))
    ev = dict(fake.audit.ev)
    print(f"\n[{sabotage}] events: {json.dumps(ev, default=str)[:800]}")
    p = ev["plan_turn_wrote_files"]
    assert wrote and p["complete"] is True
    paths = {r["path"] for r in p["reverted"]}
    assert paths == {"PLAN.md", "trk/t.txt"}, paths
    assert _cat(w, "PLAN.md") is None and _cat(w, "trk/t.txt") == "orig\n"
    assert _cat(w, "coding.py") == "coding\n"
    assert w.operator("test -e /workspace/.git/ao-plan-snapshot && echo y || echo n").strip() == "n"
    assert len(w.denials().splitlines()) == st["n0"]


# -- round 2 -----------------------------------------------------------------------------------
def test_e2e_the_delivery_sync_keeps_a_staged_new_file(pool):
    """(e) the REAL sync command in the image's shell: a file an earlier turn staged as new and
    never committed survives `checkout -f -B` (unstaged first, kept untracked)."""
    from app.modules.router import delivery_sync_command

    w = pool[1]
    assert "REPO-OK" in w.operator(_REPO)
    w.operator("cd /workspace && G=/usr/bin/git.real && $G init -q --bare /tmp/so.git && "
               "$G remote add origin /tmp/so.git 2>/dev/null; $G remote set-url origin /tmp/so.git"
               " && $G push -q origin HEAD:refs/heads/agent/e2")
    w.turn("cd /workspace && printf 'STAGED NEW\n' > staged_new.py && git add staged_new.py")
    n0 = len(w.denials().splitlines())
    code, out, _t = w.check(delivery_sync_command("agent/e2"), timeout=120)
    assert code == 0, out
    assert _cat(w, "staged_new.py") == "STAGED NEW\n"
    st = w.operator("cd /workspace && /usr/bin/git.real status --porcelain -uall")
    assert "?? staged_new.py" in st.splitlines(), st
    assert len(w.denials().splitlines()) == n0


@pytest.mark.parametrize("plan", ["drop-earlier-excludesfile", "own-excludesfile-hides"])
def test_e2e_config_keys_are_restored_first(pool, plan):
    """(a) core.excludesFile: the plan turn removes the one the earlier turn relied on, or adds
    its own to hide its files. Both undone before status is read."""
    w = pool[1]
    assert "REPO-OK" in w.operator(_REPO)
    w.operator("cd /workspace && printf 'notes/\n' > .git/my-ign && "
               "printf '[core]\n\texcludesFile = /workspace/.git/my-ign\n' >> .git/config && "
               "mkdir -p notes && printf 'CODING NOTES\n' > notes/n.txt")
    st = _setup_existing(w)
    if plan == "drop-earlier-excludesfile":
        w.turn("cd /workspace && sed -i '/my-ign/d' .git/config && printf 'p\n' > PLAN.md")
    else:
        w.turn("cd /workspace && printf '*.plan\n' > /tmp/pl-ign && "
               "printf '[core]\n\texcludesFile = /tmp/pl-ign\n' >> .git/config && "
               "for i in 0 1 2; do printf p > x$i.plan; done && printf 'p\n' > PLAN.md")
    rep = _check(w, st)
    assert rep is not None and rep["status"] == "ok", rep
    assert _cat(w, "notes/n.txt") == "CODING NOTES\n"
    assert _cat(w, "PLAN.md") is None
    assert all(_cat(w, f"x{i}.plan") is None for i in range(3))
    cfg = w.operator("cat /workspace/.git/config")
    assert "my-ign" in cfg and "pl-ign" not in cfg
    assert len(w.denials().splitlines()) == st["n0"]


def _setup_existing(w) -> dict:
    """take_baseline on the workspace as it is (no re-clone)."""
    tok = ptg.new_token()

    async def run(cmd):
        return w.check(cmd, timeout=120)[1]
    raw = asyncio.run(ptg.take_baseline(run, tok))
    snap = ptg.parse_snapshot(raw)
    assert snap is not None and isinstance(snap["manifest"], dict)
    return {"tok": tok, "snap": snap, "raw": raw, "n0": len(w.denials().splitlines())}
