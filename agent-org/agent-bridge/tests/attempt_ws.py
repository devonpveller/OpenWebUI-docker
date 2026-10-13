"""ao-attempt test workspaces: the worker's /workspace, reached the way the bridge reaches it.

Two implementations of one small interface, so the SAME scenarios run against both:

* `LocalWS` - a temp git repo driven by local bash. Every `git` the bridge sends goes through a
  shim that applies the REAL `git_proxy.classify()` (little-coder/git-proxy) and refuses what the
  worker's proxy refuses; every argv is logged. The output is then FRAMED exactly as the real
  channel frames it: the PTY turns LF into CRLF, open-terminal returns one output entry per write
  (here: random chunk sizes, so lines split anywhere), the daemon joins entries with the REAL
  `littlecoder.openterminal.parse_exec_output` (extra "\\n" between entries), appends stderr, keeps
  the last 64 KB and runs the REAL `redact_secrets`. ao-planrevert round 2 failed because its fake
  returned one clean string; this one cannot.
* `ContainerWS` - the REAL little-coder-open-terminal image, offline (`--network none`), driven
  over its own /execute API from inside the container (`docker exec ... curl`). Real proxy, real
  PTY framing. Opt-in: set AO_ATTEMPT_OT_CONTAINER to a running container's name.

Setup and observation use the REAL git (the operator's bypass: `git.real` in the image), never
the proxy, and never outside the workspace (GIT_CEILING_DIRECTORIES).
"""

from __future__ import annotations

import base64
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROXY = REPO_ROOT / "little-coder" / "git-proxy" / "git_proxy.py"
LC_SRC = REPO_ROOT / "little-coder" / "src"
if str(LC_SRC) not in sys.path:
    sys.path.insert(0, str(LC_SRC))
if str(PROXY.parent) not in sys.path:
    sys.path.insert(0, str(PROXY.parent))

from littlecoder.openterminal import parse_exec_output  # noqa: E402
from littlecoder.sanitize import redact_secrets  # noqa: E402

BASH = shutil.which("bash")
GIT = shutil.which("git")

_SHIM_PY = """import json, sys
sys.path.insert(0, {proxy_dir!r})
import git_proxy
d = git_proxy.classify(sys.argv[1:])
with open({log!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "action": d.action, "rule": d.rule}}) + "\\n")
if d.action == "deny":
    sys.stderr.write("git-proxy: DENIED (%s) - %s\\n" % (d.rule, d.reason))
    raise SystemExit(git_proxy.EXIT_DENIED)
"""


def daemon_frame(stdout: str, stderr: str, rng: random.Random) -> str:
    """What /check hands the bridge for this stdout/stderr (see the module docstring)."""
    pty = stdout.replace("\r\n", "\n").replace("\n", "\r\n")
    entries = []
    i = 0
    while i < len(pty):
        n = rng.randint(1, 400)
        entries.append({"type": "output", "data": pty[i:i + n]})
        i += n
    out, err = parse_exec_output(entries)
    if stderr:
        out += ("\n" if out else "") + stderr
    return redact_secrets(out[-65536:])


class LocalWS:
    kind = "local"

    def __init__(self, tmp: Path, seed: int = 7) -> None:
        self.root = tmp / "workspace"
        self.root.mkdir(parents=True)
        self.rng = random.Random(seed)
        shim = tmp / "bin"
        shim.mkdir()
        self.log = tmp / "git-calls.jsonl"
        self.log.write_text("")
        (shim / "classify.py").write_text(
            _SHIM_PY.format(proxy_dir=str(PROXY.parent), log=str(self.log)))
        real = Path(GIT).as_posix()
        (shim / "git").write_text(
            '#!/bin/bash\n"%s" "%s" "$@" || exit $?\nexec "%s" "$@"\n'
            % (Path(sys.executable).as_posix(), (shim / "classify.py").as_posix(), real))
        self.env = dict(os.environ, PATH=str(shim) + os.pathsep + os.environ.get("PATH", ""),
                        GIT_CEILING_DIRECTORIES=str(tmp), GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.invalid")
        self.real_env = dict(self.env, PATH=os.environ.get("PATH", ""))
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")

    # -- the bridge's channel ---------------------------------------------------------------
    def bridge(self, command: str) -> tuple[int, str, bool]:
        sh = command.replace("/workspace", self.root.as_posix())
        p = subprocess.run([BASH, "-c", sh], capture_output=True, env=self.env)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return p.returncode, daemon_frame(out, err, self.rng), False

    def calls(self) -> list[dict]:
        return [json.loads(ln) for ln in self.log.read_text().splitlines() if ln.strip()]

    # -- setup / observation (real git, inside the workspace only) --------------------------
    def git(self, *args: str) -> str:
        p = subprocess.run([GIT, *args], cwd=self.root, capture_output=True, env=self.real_env)
        if p.returncode != 0:
            raise RuntimeError(f"git {args}: {p.stderr.decode('utf-8', 'replace')}")
        return p.stdout.decode("utf-8", "replace")

    def write(self, rel: str, text: str) -> None:
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(text.encode())

    def read(self, rel: str) -> str | None:
        f = self.root / rel
        return f.read_bytes().decode() if f.exists() else None

    def remove(self, rel: str) -> None:
        (self.root / rel).unlink()


class ContainerWS:
    """The real open-terminal image. The container must already run (offline, labelled) with
    OPEN_TERMINAL_API_KEY=`key`; the test never starts, stops or touches any other container."""
    kind = "container"

    def __init__(self, name: str, key: str) -> None:
        self.name = name
        self.key = key
        self.ws = "/workspace"
        self._sh("rm -rf /workspace/* /workspace/.[!.]* 2>/dev/null; mkdir -p /workspace")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self._sh(": > /var/log/git-proxy.log 2>/dev/null || true")

    def _post(self, path: str, body: dict | None, params: str) -> dict:
        args = ["docker", "exec", "-i", self.name, "curl", "-s", "-H",
                f"Authorization: Bearer {self.key}"]
        if body is not None:
            args += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", "@-"]
        args.append(f"http://127.0.0.1:8000{path}?{params}")
        p = subprocess.run(args, input=json.dumps(body).encode() if body is not None else None,
                           capture_output=True, timeout=900)
        return json.loads(p.stdout.decode("utf-8", "replace"))

    def _execute(self, command: str, timeout: int = 290) -> dict:
        data = self._post("/execute", {"command": command, "cwd": self.ws}, f"wait={timeout}")
        while data.get("status") == "running":
            data = self._post(f"/execute/{data['id']}/status", None, "wait=60")
        return data

    def bridge(self, command: str) -> tuple[int, str, bool]:
        """Exactly `daemon.run_check`: open-terminal's real entries, joined by the real
        parse_exec_output, stderr appended, last 64 KB, real redact_secrets."""
        data = self._execute(command)
        out, err = parse_exec_output(data.get("output"))
        if err:
            out += ("\n" if out else "") + err
        return data.get("exit_code"), redact_secrets(out[-65536:]), False

    def _sh(self, command: str) -> str:
        """Setup/observation: output carried base64 (one token), so the framing cannot alter it."""
        data = self._execute(f"( {command} ) 2>&1 | base64 -w0")
        raw, _ = parse_exec_output(data.get("output"))
        return base64.b64decode("".join(raw.split()) or b"").decode("utf-8", "replace")

    def calls(self) -> list[dict]:
        """The proxy's own journal: one JSON line per decision, when the image writes one."""
        txt = self._sh("cat /var/log/git-proxy.log 2>/dev/null")
        out = []
        for ln in txt.splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                out.append({"raw": ln})
        return out

    def git(self, *args: str) -> str:
        import shlex
        return self._sh("cd /workspace && /usr/bin/git.real " + " ".join(shlex.quote(a) for a in args))

    def write(self, rel: str, text: str) -> None:
        import shlex
        b64 = base64.b64encode(text.encode()).decode()
        path = shlex.quote(f"/workspace/{rel}")
        self._sh(f"mkdir -p $(dirname {path}) && printf %s {b64} | base64 -d > {path}")

    def read(self, rel: str) -> str | None:
        import shlex
        path = shlex.quote(f"/workspace/{rel}")
        txt = self._sh(f"if [ -e {path} ]; then printf Y; cat {path}; else printf N; fi")
        return txt[1:] if txt.startswith("Y") else None

    def remove(self, rel: str) -> None:
        import shlex
        self._sh(f"rm -f {shlex.quote('/workspace/' + rel)}")
