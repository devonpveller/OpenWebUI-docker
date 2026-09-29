#!/usr/bin/env python3
"""Fail-closed, PROCESS-WIDE guard for every sysadmin-mcp test run (2026-09-27, attempt 3).

WHY. Twice in one day a sysadmin test reached the real host while the code under test was broken:
at 13:00 a test drove the reclaim guard outside its fake and pruned the host daemon; in attempt 2 a
test's CHILD process (test_sysadmin's STDIO section spawns server.py) ran without any guard, and with
compaction's gate mutated open it sent `schtasks /run` for the VHDX-compaction task to the host. A
dead DOCKER_HOST does not cover wsl or schtasks, and an in-process wrapper does not cover children.

WHERE TESTS RUN (the barrier): ONLY inside a disposable Linux container with no docker socket, no
Windows tools and the code mounted read-only - see the test plan's T0 for the exact `docker run` line.
install() exits 2 anywhere else (container_only). Everything below is defence in depth.

WHAT install() DOES (every test module calls it at import):
  1. DOCKER TARGET: DOCKER_HOST defaults to the dead endpoint tcp://127.0.0.1:1 and is printed first.
     Live sections run only when DOCKER_HOST names a daemon on purpose (a disposable DinD).
  2. PROCESS GUARD, in this process AND inherited by Python children started the ordinary way
     (NOT by a child started with -I / -E / -S, which skip PYTHONPATH or site, nor by one given a
     scrubbed env= without ACSR_TESTGUARD and PYTHONPATH - that is why the CONTAINER is the barrier
     and this guard only defence in depth): the lowest Python hook,
     subprocess.Popen._execute_child, is wrapped. Every start of docker / wsl / schtasks /
     powershell / cmd / sh / bash is checked and written to the call log with its pid:
       mode "fake"     - all of those are REFUSED (hermetic suites: fakes serve every call);
       mode "readonly" - only the EXACT read-only shapes in allowed_readonly() pass.
     Children inherit it through the environment: ACSR_TESTGUARD=<mode>, and the directory
     `_testsite/` (holding a sitecustomize.py that installs this guard at interpreter start) is
     put first on PYTHONPATH. server.py also installs it at import when ACSR_TESTGUARD is set.
     The mode never weakens: a process that already runs the "fake" guard keeps it.
  3. WINDOWS PROGRAMS: the guard judges every start whose tool is one of the names above OR any
     program found in System32 / SysWOW64 / the Windows directory (%SystemRoot%), by name or by
     path, and via `executable=`; refuses `shell=True`; and replaces os.system / os.spawn* /
     os.exec* / os.startfile with refusals. (2026-09-27 15:19 UTC: an unguarded child ran the real
     `schtasks /run` for the compaction task because Windows searches System32 before PATH.) In
     the Linux test container the Windows directories do not exist; t26 points SystemRoot at a
     scratch tree to exercise that rule there.
  4. CALL LOG: ACSR_CALL_LOG (default: a temp file, path printed) gets one JSON line per guarded
     start - {"kind": "guard", "verdict": "allowed"|"refused", "pid", "label", "argv"}.
     Children append to the same file. At exit the top-level module prints the counts.
  5. mode "fake" also replaces sysadmin._run with a stub that raises (RealDockerCall).

THE READ-ONLY ALLOWLIST (allowed_readonly), and why each entry is there:
  docker  ps / inspect / images / logs / info / version (read-only in every form; no global
          options); image inspect|ls; volume ls|inspect; system df; compose <global options>
          config (--images | --no-consistency --format json), parsed with
          sysadmin.compose_subcommand; exec <ao-worker> with exactly the idleness/size probe
          scripts executor.py and sysadmin.py use (find -mmin, du, ls|wc, the /proc scan).
  wsl     exactly `-d docker-desktop -e` + `df -k <mount>`, the container-log `find ... -exec du
          -k {} ;` scan, or the volume-age `find <volumes> -maxdepth 3 -exec stat -c "%Y %n" {} +`.
  schtasks  `/query /tn <name> [/fo LIST]` only.
  powershell  `-NoProfile -ExecutionPolicy Bypass -File <a .ps1 under the system temp dir>`
          (test_telegram_listener's stub) - nothing else.
In the test container wsl, schtasks and powershell do not exist, so an allowed shape fails with
"not found" and the code's fail-soft path runs; the allowlist matters when docker is a DinD.
(The .NET PATH recorder of attempt 3 was removed: it only ever ran on Windows, where tests no
longer run at all.)
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEAD = "tcp://127.0.0.1:1"
HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(HERE, "_testsite")
UNGUARDED_RUN = None  # sysadmin._run as it was before install(); t05 drives its deny-list with a recorder
_GUARDED = {"docker", "docker-compose", "wsl", "schtasks", "powershell", "pwsh", "cmd", "sh", "bash",
            "taskkill", "sc", "shutdown", "reg", "net", "wmic", "diskpart", "bcdedit", "vssadmin"}
_REAL_EXEC = subprocess.Popen._execute_child
_STATE = {"mode": None, "label": None}


class RealDockerCall(RuntimeError):
    """A test reached for a real docker/wsl/schtasks call it was not allowed to make."""


# ---------------------------------------------------------------- the allowlist
_EXEC_SCRIPTS = [
    re.compile(r'for p in /proc/\[0-9\]\*/cmdline; do tr "\\0" " " < "\$p" 2>/dev/null; echo; done'),
    re.compile(r"find /tmp -maxdepth 1 -name 'lc-\*\.jsonl' -mmin [-+]\d+ 2>/dev/null \| (head -1|wc -l)"),
    re.compile(r"du -ck /tmp/lc-pi-\*\.jsonl /tmp/lc-ot-\*\.jsonl 2>/dev/null \| tail -1"),
    re.compile(r"ls -1 /tmp/lc-pi-\*\.jsonl /tmp/lc-ot-\*\.jsonl 2>/dev/null \| wc -l"),
    re.compile(r"ls -1 /tmp/lc-\*\.jsonl 2>/dev/null \| wc -l"),
]
_MOUNT = r"/[\w./-]+"
_WSL = [
    re.compile(rf"-d docker-desktop -e df -k {_MOUNT}"),
    re.compile(rf"-d docker-desktop -e find {_MOUNT}/data/docker/containers -name \*-json\.log -size \+\d+c -exec du -k \{{\}} ;"),
    re.compile(rf"-d docker-desktop -e find {_MOUNT}/data/docker/volumes -maxdepth 3 -exec stat -c %Y %n \{{\}} \+"),
]


_EXE_SUFFIXES = (".exe", ".com", ".bat", ".cmd")


def tool_of(cmd) -> str:
    first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd).strip().split(" ")[0]
    name = os.path.basename(str(first).strip('"')).lower()
    for suf in _EXE_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _windows_dirs() -> list[str]:
    w = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return [os.path.join(w, "System32"), os.path.join(w, "SysWOW64"), w]


def is_guarded(cmd, executable=None) -> bool:
    """A start the guard must judge: a named tool family, OR any program Windows itself ships
    (found in System32 / SysWOW64 / the Windows directory) - by bare name or by any path. Closes the
    bare-name gap: CreateProcess searches System32 BEFORE PATH, so a PATH shim cannot stand in for
    wsl / schtasks / powershell / cmd; this check does not depend on how the name resolves."""
    names = {tool_of(cmd)}
    if executable:
        names.add(tool_of([executable]))
    if names & _GUARDED:
        return True
    for n in names:
        if not n:
            continue
        for d in _windows_dirs():
            if any(os.path.isfile(os.path.join(d, n + suf)) for suf in _EXE_SUFFIXES):
                return True
        first = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd).strip().split(" ")[0]
        full = os.path.normcase(os.path.abspath(str(first).strip('"')))
        if any(full.startswith(os.path.normcase(d) + os.sep) for d in _windows_dirs()):
            return True
    return False


def allowed_readonly(cmd) -> bool:
    """EXACT read-only shapes (see the module docstring). Anything else is refused."""
    if not isinstance(cmd, (list, tuple)):
        return False  # a shell string cannot be judged
    t, a = tool_of(cmd), [str(x) for x in cmd[1:]]
    if t == "docker":
        if not a or a[0].startswith("-"):
            return False
        v, rest = a[0], a[1:]
        if v in ("ps", "inspect", "images", "logs", "info", "version"):
            return True
        sub = rest[0] if rest else ""
        if v == "image":
            return sub in ("inspect", "ls")
        if v == "volume":
            return sub in ("ls", "inspect")
        if v == "system":
            return sub == "df"
        if v == "compose":
            sys.path.insert(0, HERE)
            import sysadmin as _sa
            subc, cargs = _sa.compose_subcommand(rest)
            return subc == "config" and cargs in (["--images"], ["--no-consistency", "--format", "json"])
        if v == "exec":
            if len(rest) == 4 and rest[1:] == ["du", "-sk", "/tmp"]:
                return True
            return len(rest) == 4 and rest[1:3] == ["sh", "-c"] and any(p.fullmatch(rest[3]) for p in _EXEC_SCRIPTS)
        return False
    if t == "wsl":
        return any(p.fullmatch(" ".join(a)) for p in _WSL)
    if t == "schtasks":
        return (len(a) in (3, 5) and a[0].lower() == "/query" and a[1].lower() == "/tn"
                and (len(a) == 3 or [x.lower() for x in a[3:]] == ["/fo", "list"]))
    if t in ("powershell", "pwsh"):
        tmp = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
        if a[:4] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] and len(a) >= 5:
            f = os.path.normcase(os.path.abspath(a[4]))
            return f.startswith(tmp + os.sep) and f.endswith(".ps1")
        return False
    return False  # cmd / sh / bash / docker-compose / every other Windows program: never


# ---------------------------------------------------------------- the process guard
def _log(rec: dict) -> None:
    p = os.environ.get("ACSR_CALL_LOG")
    if p:
        try:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass


def install_process_guard(mode: str, label: str) -> None:
    """Wrap subprocess.Popen._execute_child in THIS process. Never weakens an installed mode."""
    if _STATE["mode"] == "fake" and mode != "fake":
        return
    _STATE.update(mode=mode, label=label)
    if getattr(subprocess.Popen._execute_child, "_acsr", False):
        return

    def _exec(self, args, *rest, **kw):
        # signature (Windows and POSIX alike, 3.12/3.13): (args, executable, preexec_fn, close_fds,
        # pass_fds, cwd, env, startupinfo, creationflags, shell, p2cread, ...) - so after `args`,
        # executable is rest[0] and shell is rest[8]
        executable = rest[0] if rest else kw.get("executable")
        shell = rest[8] if len(rest) > 8 else kw.get("shell", False)
        if not isinstance(shell, bool):
            shell = True  # the signature moved: judge as the most dangerous case
        t = tool_of(args)
        if shell or is_guarded(args, executable):
            ok = (not shell and _STATE["mode"] == "readonly" and allowed_readonly(args)
                  and (not executable or tool_of([executable]) == t))
            argv = [str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)]
            _log({"kind": "guard", "verdict": "allowed" if ok else "refused", "pid": os.getpid(),
                  "label": _STATE["label"], "mode": _STATE["mode"], "argv": argv[:64],
                  "shell": bool(shell), "executable": str(executable) if executable else None})
            if not ok:
                raise RealDockerCall(f"test guard ({_STATE['mode']}, pid {os.getpid()}): refused {argv[:6]!r}")
            real = os.environ.get(f"ACSR_REAL_{t.upper()}")
            if real and isinstance(args, list):
                args = [real] + args[1:]
            elif real and isinstance(args, tuple):
                args = (real,) + tuple(args[1:])
        return _REAL_EXEC(self, args, *rest, **kw)

    _exec._acsr = True
    subprocess.Popen._execute_child = _exec

    # the other ways a Python process can start a program: refused outright in a test run
    def _refuse(name):
        def f(*a, **k):
            _log({"kind": "guard", "verdict": "refused", "pid": os.getpid(), "label": _STATE["label"],
                  "mode": _STATE["mode"], "argv": [name] + [str(x) for x in a][:8]})
            raise RealDockerCall(f"test guard: os.{name} is not allowed in a test run")
        return f
    for name in ("system", "startfile", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
                 "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
                 "execvpe", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            setattr(os, name, _refuse(name))


# ---------------------------------------------------------------- entry points
def target(label: str) -> str:
    if not os.environ.get("DOCKER_HOST"):
        os.environ["DOCKER_HOST"] = DEAD
    t = os.environ["DOCKER_HOST"]
    ctx = os.environ.get("DOCKER_CONTEXT", "")
    note = "DEAD endpoint - no daemon reachable" if t == DEAD else "a real daemon - must be a disposable DinD"
    print(f"[{label}] docker target: DOCKER_HOST={t} DOCKER_CONTEXT={ctx or '(unset)'} ({note})")
    return t


def _summary(log: str, label: str) -> None:
    try:
        rows = [json.loads(x) for x in open(log, encoding="utf-8")]
    except (OSError, ValueError):
        rows = []
    allowed = sum(1 for r in rows if r.get("verdict") == "allowed")
    refused = sum(1 for r in rows if r.get("verdict") == "refused")
    pids = len({r.get("pid") for r in rows if r.get("pid")})
    print(f"[{label}] call log {log}: {allowed} allowed (read-only), {refused} refused, "
          f"from {pids} process(es)")


def in_container() -> bool:
    """True only inside the disposable Linux test container: POSIX, docker's /.dockerenv marker,
    and ACSR_IN_CONTAINER=1 set by the `docker run` line in the test plan."""
    return os.name == "posix" and os.path.exists("/.dockerenv") and os.environ.get("ACSR_IN_CONTAINER") == "1"


def container_only(label: str) -> None:
    """RULE (2026-09-27, after two incidents): sysadmin tests never run on a host. The container
    (no docker socket, no Windows tools, the code mounted read-only) is the barrier; this guard is
    defence in depth. Exit 2 before anything else runs."""
    if not in_container():
        print(f"[{label}] REFUSED: sysadmin-mcp tests run ONLY inside a disposable Linux container "
              f"(docker run --rm --network none -e ACSR_IN_CONTAINER=1 -v <worktree>:/w:ro ... "
              f"python:3.12-slim). os.name={os.name}, /.dockerenv={os.path.exists('/.dockerenv')}, "
              f"ACSR_IN_CONTAINER={os.environ.get('ACSR_IN_CONTAINER')!r}.")
        sys.stdout.flush()
        os._exit(2)


def install(sa, mode: str, label: str) -> None:
    """mode: 'fake' (hermetic) or 'readonly' (live). See the module docstring."""
    global UNGUARDED_RUN
    container_only(label)
    target(label)
    top = not os.environ.get("ACSR_TESTGUARD")
    if top:
        for t in ("docker", "wsl", "schtasks"):  # an allowed start runs the tool by absolute path
            w = shutil.which(t)
            if w:
                os.environ.setdefault(f"ACSR_REAL_{t.upper()}", w)
        if not os.environ.get("ACSR_CALL_LOG"):
            fd, p = tempfile.mkstemp(prefix=f"acsr-calls-{label}-", suffix=".jsonl")
            os.close(fd)
            os.environ["ACSR_CALL_LOG"] = p
        print(f"[{label}] process guard: mode={mode}; call log: {os.environ['ACSR_CALL_LOG']}")
        atexit.register(_summary, os.environ["ACSR_CALL_LOG"], label)
    inherited = os.environ.get("ACSR_TESTGUARD")
    eff = "fake" if "fake" in (mode, inherited) else "readonly"
    os.environ["ACSR_TESTGUARD"] = eff
    os.environ["ACSR_TESTGUARD_DIR"] = HERE
    pp = os.environ.get("PYTHONPATH", "")
    if SITE_DIR not in pp.split(os.pathsep):
        os.environ["PYTHONPATH"] = SITE_DIR + (os.pathsep + pp if pp else "")
    install_process_guard(eff, label)
    real_run = sa._run
    if getattr(sa, "_GUARD_STUB", None) is not real_run:
        UNGUARDED_RUN = real_run
    if eff == "fake":
        def fake_mode_stub(cmd, timeout=30):
            _log({"kind": "guard", "verdict": "refused", "pid": os.getpid(), "label": label,
                  "mode": "fake", "argv": [str(c) for c in cmd][:64], "via": "sysadmin._run"})
            raise RealDockerCall(f"test guard: no fake installed for {cmd[:4]!r}")
        sa._run = fake_mode_stub
        sa._GUARD_STUB = fake_mode_stub


def canary_ok(env: dict, log: str) -> tuple[bool, str]:
    """Start a throwaway Python child with `env` and prove the startup hook guards it: the child
    tries `schtasks /run` on a task that does not exist (harmless even if unguarded), and the call
    log must show that attempt REFUSED under a `child:` label from the child's pid. Every
    meta-test runs this first and aborts when it fails."""
    code = ("import subprocess\n"
            "try:\n    subprocess.run(['schtasks', '/run', '/tn', 'ACSR-CANARY-DOES-NOT-EXIST'], capture_output=True)\n"
            "except Exception:\n    pass\n")
    p = subprocess.Popen([sys.executable, "-c", code], env=env)
    p.wait(timeout=120)
    try:
        rows = [json.loads(x) for x in open(log, encoding="utf-8")]
    except (OSError, ValueError):
        rows = []
    return canary_verdict(rows, p.pid)


def canary_verdict(rows: list, pid: int) -> tuple[bool, str]:
    """The canary passes only on a row from THAT pid, with verdict `refused`, under a `child:` label
    (a row from another process, another label or an `allowed` verdict proves nothing about it)."""
    hits = [r for r in rows if r.get("pid") == pid and r.get("verdict") == "refused"
            and str(r.get("label", "")).startswith("child:")]
    return bool(hits), f"canary pid {pid}: {len(hits)} refused entr(y/ies) under a child: label"


def require_daemon(label: str, allowed: bool) -> bool:
    """For LIVE sections: True when DOCKER_HOST names a real daemon on purpose."""
    if os.environ.get("DOCKER_HOST") in (None, "", DEAD):
        if not allowed:
            print(f"[{label}] LIVE section skipped: DOCKER_HOST is the dead endpoint. Point it at a "
                  f"disposable DinD (tcp://127.0.0.1:<port>) to run it.")
        return False
    return True
