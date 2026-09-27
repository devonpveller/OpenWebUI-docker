#!/usr/bin/env python3
"""Fail-closed guard every sysadmin-mcp test module installs AT IMPORT (2026-09-27).

WHY: a test that fed forbidden docker commands to the reclaim code relied on the code under test to
refuse them. A tester's mutation made that code permissive, and the test then ran
`system prune -af --volumes`, `image prune -a` and `volume prune` against the HOST daemon. The
tests must not depend on the thing they test for the host's safety. Two layers here, plus the
deny-list inside sysadmin._run:

  1. DOCKER TARGET. If DOCKER_HOST is unset, it is set to a DEAD endpoint (tcp://127.0.0.1:1), so a
     command that escapes every guard fails to connect instead of reaching Docker Desktop. A test
     that deliberately needs a daemon (the LIVE sections) says so and requires DOCKER_HOST to name
     one - normally a disposable DinD. The resolved target is printed first, every run.
  2. CALL STUBS. mode="fake": sysadmin._run is replaced by a stub that RAISES on every call; a test
     serves calls only by swapping in its own fake inside a context. mode="readonly": sysadmin._run
     passes only read-only docker/wsl shapes and raises on anything else.
     In both modes subprocess.run/Popen refuse docker and wsl outright (hermetic modules) or refuse
     every mutating docker shape (live modules), so a code path that bypasses sysadmin._run is
     caught too. Every attempt is appended to $ACSR_CALL_LOG when set (the meta-test reads it).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DEAD = "tcp://127.0.0.1:1"
_REAL_RUN = subprocess.run
_REAL_POPEN = subprocess.Popen
UNGUARDED_RUN = None  # sysadmin._run as it was before install(); t05 drives its deny-list with a recorder


class RealDockerCall(RuntimeError):
    """A test reached for a real docker/wsl call it was not allowed to make."""


def _log(kind: str, cmd) -> None:
    p = os.environ.get("ACSR_CALL_LOG")
    if p:
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "cmd": [str(c) for c in cmd][:8]}) + "\n")


def _tool(cmd) -> str:
    if isinstance(cmd, (list, tuple)) and cmd:
        return os.path.basename(str(cmd[0])).lower().replace(".exe", "")
    return os.path.basename(str(cmd).split(" ")[0]).lower().replace(".exe", "")


_RO_DOCKER = {("ps",), ("inspect",), ("images",), ("image", "inspect"), ("image", "ls"), ("volume", "ls"),
              ("volume", "inspect"), ("system", "df"), ("logs",), ("info",), ("version",)}
_BAD_WORDS = ("rm ", "rm\t", "truncate", " mv ", "> /", "dd ", "-delete", "prune", "kill", "stop")


def readonly(cmd) -> bool:
    t = _tool(cmd)
    a = [str(x) for x in cmd[1:]] if isinstance(cmd, (list, tuple)) else str(cmd).split()[1:]
    if t == "docker":
        if a[:1] == ["compose"]:
            return "config" in a and not any(x in ("up", "down", "rm", "stop", "kill") for x in a)
        if a[:1] == ["exec"]:
            return not any(w in " ".join(a) for w in _BAD_WORDS)
        return tuple(a[:2]) in _RO_DOCKER or tuple(a[:1]) in _RO_DOCKER
    if t == "schtasks":
        return "/run" not in [x.lower() for x in a]
    if t == "wsl":
        body = " ".join(a)
        return not any(w in body for w in ("truncate", " rm ", "-delete", "fstrim", "shutdown", "--terminate"))
    return True  # not a docker/wsl command


def target(label: str) -> str:
    if not os.environ.get("DOCKER_HOST"):
        os.environ["DOCKER_HOST"] = DEAD
    t = os.environ["DOCKER_HOST"]
    ctx = os.environ.get("DOCKER_CONTEXT", "")
    note = "DEAD endpoint - no daemon reachable" if t == DEAD else "a real daemon - must be a disposable DinD"
    print(f"[{label}] docker target: DOCKER_HOST={t} DOCKER_CONTEXT={ctx or '(unset)'} ({note})")
    return t


def install(sa, mode: str, label: str) -> None:
    """mode: 'fake' (hermetic: every sa._run call raises unless a test swapped in a fake) or
    'readonly' (live: only read-only docker/wsl shapes pass)."""
    target(label)

    def guard_subprocess(real):
        def wrapped(cmd, *a, **kw):
            t = _tool(cmd)
            if (t in ("docker", "wsl") and mode == "fake") or not readonly(cmd):
                _log("subprocess-refused", cmd if isinstance(cmd, (list, tuple)) else [cmd])
                raise RealDockerCall(f"test guard ({mode}): refused subprocess {cmd!r}")
            return real(cmd, *a, **kw)
        return wrapped

    subprocess.run = guard_subprocess(_REAL_RUN)
    subprocess.Popen = guard_subprocess(_REAL_POPEN)

    global UNGUARDED_RUN
    real_run = sa._run
    if getattr(sa, "_GUARD_STUB", None) is not real_run:
        UNGUARDED_RUN = real_run

    def fake_mode_stub(cmd, timeout=30):
        _log("sa._run-refused", cmd)
        raise RealDockerCall(f"test guard: no fake installed for {cmd[:4]!r}")

    def readonly_stub(cmd, timeout=30):
        if not readonly(cmd):
            _log("sa._run-refused", cmd)
            raise RealDockerCall(f"test guard (readonly): refused {cmd[:5]!r}")
        return real_run(cmd, timeout)

    sa._run = fake_mode_stub if mode == "fake" else readonly_stub
    sa._GUARD_STUB = sa._run
    sys.modules.setdefault("_testguard_installed", sys.modules[__name__])


def require_daemon(label: str, allowed: bool) -> bool:
    """For LIVE sections: True when DOCKER_HOST names a real daemon on purpose."""
    if os.environ.get("DOCKER_HOST") in (None, "", DEAD):
        if not allowed:
            print(f"[{label}] LIVE section skipped: DOCKER_HOST is the dead endpoint. Point it at a "
                  f"disposable DinD (tcp://127.0.0.1:<port>) to run it.")
        return False
    return True
