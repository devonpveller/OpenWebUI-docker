#!/usr/bin/env python3
"""mm-mirror: a VS Code session's #claude-sessions thread is a readable copy of the conversation.

    python scripts/test_notify_mattermost_mirror.py            (-v for names)

Stdlib only. NOTHING LIVE IS TOUCHED:
- Mattermost is a fake HTTP server in THIS process on 127.0.0.1:<random port>; it is shut down in
  tearDownModule and the port is checked closed. The notifier runs from a COPY in a temp root
  whose API line is rewritten to the fake (the copy is refused if that rewrite did not happen),
  with a fake bot token in the copy's own .env. DOCKER_HOST points at a dead endpoint.
- Transcripts are SYNTHETIC fixtures built here in the shape of Claude Code's JSONL (one entry per
  line; one content block per assistant entry; user prompts carry origin.kind == "human"; tool
  results are user entries with tool_result blocks). No real transcript content is used.
- The mirror's detached workers are waited for, and tearDownModule fails the run if any worker
  process whose command line names a temp root of this run is still alive (and kills it).

MM_MIRROR_TEST_SRC=<dir> runs the suite against another copy of scripts/ (used for the base-RED
run: the bf063c2 notify-mattermost.sh, which has no mirror helper beside it).
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("MM_MIRROR_TEST_SRC") or HERE
REPO = os.path.dirname(HERE)
SANITIZE = os.path.join(REPO, "little-coder", "src", "littlecoder", "sanitize.py")
LIVE_API_LINE = 'API="http://localhost:8065/api/v4/posts"'
TOKEN = "fake-token-mm-mirror-test"
MENTION = "@opertest"
MAX_POST = 16383                     # Mattermost's default post size limit, in characters
TMP_TAG = "mmmirror-test-"


# ── fake Mattermost ──────────────────────────────────────────────────────────────────────────
class FakeMM:
    def __init__(self) -> None:
        self.posts: dict[str, dict] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.mode = "ok"                 # ok | 500 | hole | hole-accept | trickle
        self.delay = 0.0                 # seconds each created post takes to answer
        self.get_mode = "ok"             # ok | 500 - the marker lookup (GET) alone
        self.get_delay = 0.0             # seconds the lookup takes to answer
        self.get_free = False            # True: the lookup answers even while POSTs hang
        self.release = threading.Event()
        self.requests = 0
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, {"id": "api.context.session_expired.app_error"})
                if fake.mode in ("hole", "hole-accept") and not fake.get_free:
                    fake.release.wait(30)
                    return
                if fake.get_delay:
                    time.sleep(fake.get_delay)
                if fake.mode == "500" or fake.get_mode == "500":
                    return self._send(500, {"id": "fake.500", "status_code": 500})
                m = re.match(r"^/api/v4/channels/([^/?]+)/posts\?since=(\d+)", self.path)
                if m:
                    since = int(m.group(2))
                    with fake.lock:
                        ps = {k: v for k, v in fake.posts.items() if v["create_at"] > since}
                    return self._send(200, {"posts": ps, "order": list(ps)})
                return self._send(404, {"id": "fake.not_found"})

            def do_POST(self):
                with fake.lock:
                    fake.requests += 1
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, {"id": "api.context.session_expired.app_error"})
                if fake.mode == "500":
                    return self._send(500, {"id": "fake.500", "status_code": 500})
                if fake.mode == "hole":
                    fake.release.wait(30)           # never answers, never creates
                    return
                msg = body.get("message", "")
                if len(msg) > MAX_POST:
                    return self._send(400, {"id": "api.post.message_length.app_error", "status_code": 400})
                root = body.get("root_id") or ""
                with fake.lock:
                    if root and root not in fake.posts:
                        return self._send(400, {"id": "api.post.create_post.root_id.app_error",
                                                "status_code": 400})
                p = fake.add(msg, root, body.get("props") or {})
                if fake.mode == "hole-accept":
                    fake.release.wait(30)           # created, but the answer never arrives
                    return
                if fake.mode == "trickle":          # created; the answer dribbles at 1 byte/s
                    raw = json.dumps(p).encode()
                    self.send_response(201)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    for i in range(len(raw)):
                        if fake.release.wait(1):
                            return
                        try:
                            self.wfile.write(raw[i:i + 1])
                            self.wfile.flush()
                        except OSError:
                            return
                    return
                if fake.delay:
                    time.sleep(fake.delay)
                return self._send(201, p)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def add(self, message: str, root: str, props: dict) -> dict:
        with self.lock:
            pid = uuid.uuid4().hex[:26]
            p = {"id": pid, "root_id": root, "message": message, "props": props,
                 "create_at": int(time.time() * 1000) + len(self.order)}
            self.posts[pid] = p
            self.order.append(pid)
            return dict(p)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(self.posts[i]) for i in self.order]

    def reset(self) -> None:
        with self.lock:
            self.posts.clear()
            self.order.clear()
            self.requests = 0
        self.mode = "ok"
        self.delay = 0.0
        self.get_mode = "ok"
        self.get_delay = 0.0
        self.get_free = False
        self.release.clear()

    def close(self) -> None:
        self.release.set()
        self.srv.shutdown()
        self.srv.server_close()


FAKE: FakeMM | None = None
ROOTS: list[str] = []


def find_git_bash() -> str:
    if os.environ.get("CF_BASH"):
        return os.environ["CF_BASH"]
    if os.name != "nt":
        return shutil.which("bash") or "/bin/bash"
    git = shutil.which("git")
    cands = []
    if git:
        g = os.path.dirname(os.path.dirname(git))
        cands += [os.path.join(g, "bin", "bash.exe"), os.path.join(g, "usr", "bin", "bash.exe")]
    cands += [r"C:\Program Files\Git\bin\bash.exe"]
    for c in cands:
        if os.path.isfile(c):
            return c
    raise unittest.SkipTest("Git Bash not found (set CF_BASH)")


BASH = ""


def setUpModule():
    global FAKE, BASH
    BASH = find_git_bash()
    FAKE = FakeMM()


def _worker_pids(roots: list[str] | None = None) -> list[int]:
    """Live processes whose command line names one of this run's temp roots (or of `roots`)."""
    roots = ROOTS if roots is None else roots
    if not roots:
        return []
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=60).stdout
    else:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
        out = "\n".join(re.sub(r"^\s*(\d+)\s+", r"\1\t", ln) for ln in out.splitlines())
    pids = []
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        if TMP_TAG in cmd and any(os.path.basename(r) in cmd for r in roots) and pid.strip().isdigit():
            pids.append(int(pid))
    return pids


def tearDownModule():
    FAKE.close()
    end = time.time() + 30
    left = _worker_pids()
    while left and time.time() < end:
        time.sleep(1)
        left = _worker_pids()
    for pid in left:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    s = socket.socket()
    s.settimeout(1)
    port_open = s.connect_ex(("127.0.0.1", FAKE.port)) == 0
    s.close()
    for r in ROOTS:
        shutil.rmtree(r, ignore_errors=True)
    print(f"\n[cleanup] fake MM port {FAKE.port} closed={not port_open}; stray workers={left}",
          file=sys.stderr)
    if left or port_open:
        raise RuntimeError(f"cleanup failed: stray workers {left}, port open {port_open}")


# ── transcript fixtures (synthetic, Claude Code's shape) ─────────────────────────────────────
class Transcript:
    def __init__(self, path: str, sid: str):
        self.path, self.sid = path, sid
        self.parent = None
        open(path, "w").close()

    def _base(self, typ: str) -> dict:
        u = str(uuid.uuid4())
        d = {"parentUuid": self.parent, "isSidechain": False, "userType": "external",
             "cwd": "C:\\fixture", "sessionId": self.sid, "version": "9.9.9",
             "gitBranch": "fixture", "type": typ, "uuid": u, "timestamp": "2026-10-01T00:00:00.000Z"}
        self.parent = u
        return d

    def write(self, *entries, raw: str | None = None) -> None:
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
            if raw is not None:
                fh.write(raw)

    def prompt(self, text: str, ide: bool = False, origin=True) -> dict:
        d = self._base("user")
        content = [{"type": "text", "text": "<ide_opened_file>The user opened c:\\x.py</ide_opened_file>"},
                   {"type": "text", "text": text}] if ide else text
        d.update({"message": {"role": "user", "content": content}, "promptSource": "sdk"})
        if origin:
            d["origin"] = {"kind": "human"}
        return d

    def task_note(self, text: str) -> dict:
        d = self._base("user")
        d.update({"message": {"role": "user", "content": f"<task-notification> {text} </task-notification>"},
                  "origin": {"kind": "task-notification"}})
        return d

    def meta(self, text: str) -> dict:
        d = self._base("user")
        d.update({"message": {"role": "user", "content": text}, "isMeta": True})
        return d

    def _assistant(self, block: dict, stop: str, mid: str) -> dict:
        d = self._base("assistant")
        d.update({"requestId": "req_fixture", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": "claude-fixture",
            "content": [block], "stop_reason": stop, "stop_sequence": None, "usage": {}}})
        return d

    def thinking(self, text: str, mid: str = "msg_1") -> dict:
        return self._assistant({"type": "thinking", "thinking": text, "signature": "x"}, "tool_use", mid)

    def text(self, text: str, stop: str = "end_turn", mid: str = "msg_1") -> dict:
        return self._assistant({"type": "text", "text": text}, stop, mid)

    def tool_use(self, name: str, inp: dict, mid: str = "msg_1") -> tuple[dict, str]:
        tid = "toolu_" + uuid.uuid4().hex[:20]
        return self._assistant({"type": "tool_use", "id": tid, "name": name, "input": inp},
                               "tool_use", mid), tid

    def tool_result(self, tid: str, text: str) -> dict:
        d = self._base("user")
        d.update({"message": {"role": "user", "content": [
            {"tool_use_id": tid, "type": "tool_result", "content": text}]},
            "toolUseResult": {"stdout": text}})
        return d

    def other(self, typ: str, **kw) -> dict:
        d = self._base(typ)
        d.update(kw)
        return d


# ── the harness around one temp root ─────────────────────────────────────────────────────────
class Root:
    def __init__(self, with_helper: bool = True, with_sanitizer: bool = True):
        self.dir = tempfile.mkdtemp(prefix=TMP_TAG)
        ROOTS.append(self.dir)
        os.makedirs(os.path.join(self.dir, "scripts"))
        with open(os.path.join(SRC, "notify-mattermost.sh"), "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        if src.count(LIVE_API_LINE) != 1:
            raise RuntimeError("notifier API line not found - refusing a copy that might post live")
        self.script = os.path.join(self.dir, "scripts", "notify-mattermost.sh")
        with open(self.script, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.replace(LIVE_API_LINE, f'API="{FAKE.url}/api/v4/posts"'))
        helper = os.path.join(SRC, "notify_mattermost_mirror.py")
        if with_helper and os.path.isfile(helper):
            shutil.copy(helper, os.path.join(self.dir, "scripts", "notify_mattermost_mirror.py"))
        if with_sanitizer:
            d = os.path.join(self.dir, "little-coder", "src", "littlecoder")
            os.makedirs(d)
            shutil.copy(SANITIZE, d)
        with open(os.path.join(self.dir, ".env"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"CLAUDE_MM_BOT_TOKEN={TOKEN}\nMM_OPERATOR_MENTION={MENTION}\n")
        self.log = os.path.join(self.dir, "scripts", ".mm-mirror", "mirror.log")
        self.map = os.path.join(self.dir, "scripts", ".mm-session-threads")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("MM_SESSION_ID", "CLAUDE_BRIDGE_THREAD", "MM_DEADLINE_SECS",
                                 "MM_WALL_SECS", "MM_OPERATOR_MENTION") and not k.startswith("MM_MIRROR_")}
        self.env.update({"DOCKER_HOST": "tcp://127.0.0.1:1", "MM_MIRROR_SETTLE_SECS": "1"})

    def transcript(self, sid: str) -> Transcript:
        return Transcript(os.path.join(self.dir, f"{sid}.jsonl"), sid)

    def hook(self, payload: dict, args: list[str] | None = None, env: dict | None = None,
             stdin_closed: bool = False):
        e = dict(self.env)
        e.update(env or {})
        t0 = time.time()
        r = subprocess.run([BASH, self.script] + (args or []),
                           input=None if stdin_closed else json.dumps(payload).encode(),
                           stdin=subprocess.DEVNULL if stdin_closed else None,
                           env=e, capture_output=True, timeout=60)
        return r, time.time() - t0

    def stop(self, tr: Transcript, last: str | None = None, **kw):
        p = {"session_id": tr.sid, "transcript_path": tr.path, "cwd": self.dir,
             "hook_event_name": "Stop", "stop_hook_active": False}
        if last is not None:
            p["last_assistant_message"] = last
        return self.hook(p, **kw)

    def dones(self) -> int:
        try:
            with open(self.log, encoding="utf-8") as fh:
                return sum(1 for ln in fh if " done event=" in ln or " worker:" in ln
                           or "killed at its hard wall" in ln)
        except OSError:
            return 0

    def wait_done(self, n: int, timeout: float = 120, locks: bool = True) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.dones() >= n and not locks:
                return
            if self.dones() >= n and not [x for x in os.listdir(os.path.dirname(self.log))
                                          if x.endswith(".lock")]:
                return
            time.sleep(0.2)
        raise AssertionError(f"mirror worker did not finish: {self.dones()} of {n} done lines; "
                             f"posts={len(FAKE.snapshot())} live={_worker_pids([self.dir])}\n"
                             f"log:\n{self.logtext()}")

    def logtext(self) -> str:
        try:
            with open(self.log, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""


def sid_new() -> str:
    return str(uuid.uuid4())


def texts(posts: list[dict]) -> list[str]:
    return [p["message"].split("\n", 1)[-1] for p in posts]


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        os.kill(pid, 9)


def load_helper(src_dir: str):
    """The helper module under test, loaded by path with the repo root as its ROOT."""
    import importlib.util
    os.environ["MM_ROOT_DIR"] = REPO
    try:
        spec = importlib.util.spec_from_file_location("mm_mirror_under_test",
                                                      os.path.join(src_dir, "notify_mattermost_mirror.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.pop("MM_ROOT_DIR", None)


def bodies(posts: list[dict]) -> list[str]:
    return [p["message"] for p in posts]


class MirrorTests(unittest.TestCase):
    def setUp(self):
        FAKE.reset()
        self._first_root = len(ROOTS)
        self.root = Root()

    def tearDown(self):
        """ISOLATION: no worker of this test may outlive it and post into the next test's fake.
        Wait (bounded) for this test's workers and watchdogs to end; kill any that do not."""
        FAKE.release.set()
        mine = ROOTS[self._first_root:]
        end = time.time() + 40
        left = _worker_pids(mine)
        while left and time.time() < end:
            time.sleep(0.5)
            left = _worker_pids(mine)
        for pid in left:
            kill(pid)
        FAKE.release.clear()

    def assertOneThread(self, posts):
        self.assertTrue(posts)
        root = posts[0]["id"]
        self.assertEqual(posts[0]["root_id"], "")
        for p in posts[1:]:
            self.assertEqual(p["root_id"], root, "a post left the session's thread")

    def turn1(self, tr: Transcript):
        tr.write(tr.other("queue-operation", operation="enqueue"),
                 tr.prompt("PROMPT-ONE please look", ide=True),
                 tr.other("attachment", attachment={"type": "hook_success", "content": "ATTACH-SECRET"}),
                 tr.thinking("THINKING-SECRET musing"),
                 tr.text("REPLY-1A let me look", stop="tool_use"))
        tu, tid = tr.tool_use("Bash", {"command": "cat TOOLCALL-SECRET"})
        tr.write(tu, tr.tool_result(tid, "TOOLRESULT-SECRET output"),
                 tr.other("system", subtype="info", content="SYSTEM-SECRET"),
                 tr.meta("META-SECRET caveat"),
                 tr.text("REPLY-1B here is the answer", mid="msg_2"))

    # T1 - prompts and replies, in order, once, one thread, no mention, nothing else
    def test_t1_multi_turn_mirror(self):
        tr = self.root.transcript(sid_new())
        self.turn1(tr)
        r, dt = self.root.stop(tr, last="REPLY-1B here is the answer")
        self.assertEqual(r.returncode, 0)
        self.root.wait_done(1)
        tr.write(tr.task_note("TASKNOTE-SECRET finished"), tr.text("REPLY-TASK noted", mid="msg_3"),
                 tr.prompt("PROMPT-TWO and then"), tr.text("REPLY-2 done", mid="msg_4"))
        self.root.stop(tr, last="REPLY-2 done")
        self.root.wait_done(2)
        posts = FAKE.snapshot()
        got = bodies(posts)
        want = ["PROMPT-ONE", "REPLY-1A", "REPLY-1B", "REPLY-TASK", "PROMPT-TWO", "REPLY-2"]
        self.assertEqual(len(got), len(want), got)
        for b, w in zip(got, want):
            self.assertIn(w, b)
        self.assertTrue(got[0].startswith("**\U0001f9d1 Operator**"))
        self.assertTrue(got[1].startswith("**\U0001f916 Claude**"))
        self.assertOneThread(posts)
        joined = "\n".join(got)
        for bad in ("SECRET", MENTION, "finished a turn", "ide_opened_file"):
            self.assertNotIn(bad, joined)
        with open(self.root.map, encoding="utf-8") as fh:
            lines = [ln.split() for ln in fh.read().splitlines() if ln.strip()]
        self.assertEqual(lines, [[tr.sid[:8], posts[0]["id"], tr.sid]])

    # T2 - a Stop with nothing new posts nothing
    def test_t2_no_new_entries_posts_nothing(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-A"), tr.text("REPLY-A"))
        self.root.stop(tr, last="REPLY-A")
        self.root.wait_done(1)
        n = len(FAKE.snapshot())
        self.assertEqual(n, 2)
        self.root.stop(tr, last="REPLY-A")
        self.root.wait_done(2)
        self.assertEqual(len(FAKE.snapshot()), n)

    # T3 - a reply over the post limit is split, in order, every piece under the limit
    def test_t3_long_reply_split_in_order(self):
        tr = self.root.transcript(sid_new())
        lines = [f"line {i:05d} " + "x" * 60 for i in range(600)]
        text = "start\n```python\n" + "\n".join(lines) + "\n```\nend"
        tr.write(tr.prompt("PROMPT-LONG"), tr.text(text))
        self.root.stop(tr, last=text)
        self.root.wait_done(1)
        posts = FAKE.snapshot()
        self.assertGreaterEqual(len(posts), 4, [len(p["message"]) for p in posts])
        self.assertOneThread(posts)
        parts = posts[1:]
        n = len(parts)
        for i, p in enumerate(parts, 1):
            self.assertLessEqual(len(p["message"]), MAX_POST)
            self.assertTrue(p["message"].startswith(f"**\U0001f916 Claude** ({i}/{n})"), p["message"][:40])
        seen = re.findall(r"line (\d{5}) ", "\n".join(p["message"] for p in parts))
        self.assertEqual(seen, [f"{i:05d}" for i in range(600)])
        for p in parts:                          # every piece's code fences balance
            self.assertEqual(p["message"].count("```") % 2, 0)

    # T4 - an interrupted turn (no Stop) and a half-written line are picked up next time, once
    def test_t4_interrupted_turn_and_partial_line(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-INTERRUPTED"), tr.text("REPLY-PARTIAL so far", stop="tool_use"),
                 tr.prompt("[Request interrupted by user]", origin=False))
        # no Stop fires for an interrupted turn
        tr.write(tr.prompt("PROMPT-NEXT"))
        half = json.dumps(tr.text("REPLY-NEXT done"))
        tr.write(raw=half[:40])                  # still being written
        self.root.stop(tr)
        self.root.wait_done(1)
        got = bodies(FAKE.snapshot())
        self.assertEqual([g.split("\n", 1)[1] for g in got],
                         ["PROMPT-INTERRUPTED", "REPLY-PARTIAL so far", "PROMPT-NEXT"])
        tr.write(raw=half[40:] + "\n")
        self.root.stop(tr, last="REPLY-NEXT done")
        self.root.wait_done(2)
        got = bodies(FAKE.snapshot())
        self.assertEqual(len(got), 4)
        self.assertIn("REPLY-NEXT done", got[3])

    # T5 - a resumed session (same id, same file appended, new processes) continues from the mark;
    #      a failed run loses nothing and duplicates nothing
    def test_t5_resume_and_failure_continue_from_mark(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-R1"), tr.text("REPLY-R1"))
        self.root.stop(tr, last="REPLY-R1")
        self.root.wait_done(1)
        FAKE.mode = "500"
        tr.write(tr.prompt("PROMPT-R2"), tr.text("REPLY-R2"))
        r, dt = self.root.stop(tr, last="REPLY-R2")
        self.assertEqual(r.returncode, 0)
        self.root.wait_done(2)
        self.assertEqual(len(FAKE.snapshot()), 2)
        FAKE.mode = "ok"
        # "resumed": a fresh hook process, same session id and transcript, more appended
        tr.write(tr.prompt("PROMPT-R3"), tr.text("REPLY-R3"))
        self.root.stop(tr, last="REPLY-R3")
        self.root.wait_done(3)
        got = [b.split("\n", 1)[1] for b in bodies(FAKE.snapshot())]
        self.assertEqual(got, ["PROMPT-R1", "REPLY-R1", "PROMPT-R2", "REPLY-R2", "PROMPT-R3", "REPLY-R3"])
        self.assertOneThread(FAKE.snapshot())

    # T6 - AskUserQuestion: question + every option label and description, WITH the mention,
    #      after the turn so far; the tool call and its answer are never mirrored
    def test_t6_ask_user_question(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-Q"), tr.text("REPLY-BEFORE-Q", stop="tool_use"))
        qin = {"questions": [
            {"question": "Which colour?", "header": "Colour", "multiSelect": False,
             "options": [{"label": "Red", "description": "warm DESC-R"},
                         {"label": "Blue", "description": "cool DESC-B"}]},
            {"question": "Which sizes?", "header": "Size", "multiSelect": True,
             "options": [{"label": "Small", "description": "DESC-S"}, {"label": "Large", "description": "DESC-L"}]}]}
        tu, tid = tr.tool_use("AskUserQuestion", qin, mid="msg_q")
        tr.write(tu)                              # in the transcript when PreToolUse fires (probed)
        r, dt = self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "cwd": self.root.dir,
                                "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                                "tool_input": qin, "tool_use_id": tid})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"")           # no decision: the question is shown as usual
        self.root.wait_done(1)
        got = bodies(FAKE.snapshot())
        self.assertEqual(len(got), 3, got)
        self.assertIn("PROMPT-Q", got[0])
        self.assertIn("REPLY-BEFORE-Q", got[1])
        q = got[2]
        self.assertTrue(q.startswith(MENTION + " "), q[:40])
        for s in ("Which colour?", "Colour", "Red", "warm DESC-R", "Blue", "cool DESC-B",
                  "Which sizes?", "Small", "DESC-S", "Large", "DESC-L"):
            self.assertIn(s, q)
        tr.write(tr.tool_result(tid, "User has answered ANSWER-SECRET"), tr.text("REPLY-AFTER-Q", mid="msg_5"))
        self.root.stop(tr, last="REPLY-AFTER-Q")
        self.root.wait_done(2)
        got = bodies(FAKE.snapshot())
        self.assertEqual(len(got), 4)
        self.assertIn("REPLY-AFTER-Q", got[3])
        self.assertNotIn(MENTION, got[3])
        self.assertNotIn("ANSWER-SECRET", "\n".join(got))
        self.assertOneThread(FAKE.snapshot())

    # T7 - a permission Notification mentions and says what is asked; an idle one posts nothing
    def test_t7_notification(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-N"))
        base = {"session_id": tr.sid, "transcript_path": tr.path, "cwd": self.root.dir,
                "hook_event_name": "Notification"}
        self.root.hook(dict(base, message="Claude needs your permission to use Bash",
                            notification_type="permission_prompt"))
        self.root.wait_done(1)
        self.root.hook(dict(base, message="Claude is waiting for your input", notification_type="idle_prompt"))
        self.root.wait_done(2)
        got = bodies(FAKE.snapshot())
        self.assertEqual(len(got), 2, got)
        self.assertIn("PROMPT-N", got[0])
        self.assertTrue(got[1].startswith(MENTION + " "))
        self.assertIn("Claude needs your permission to use Bash", got[1])
        self.assertNotIn("waiting for your input", "\n".join(got))
        self.assertOneThread(FAKE.snapshot())

    # T8 - secret-shaped strings are masked; mentions inside a reply are defanged
    def test_t8_redaction(self):
        tr = self.root.transcript(sid_new())
        secrets = ["sk-" + "a1B2" * 10, "ghp_" + "Z9" * 20, "AKIA" + "ABCDEFGHIJKLMNOP",
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlc2ln", "QWERTYsecretvalue1234"]
        kind = "PRIVATE" + " KEY"     # assembled, so the repo's own secret scanner is not tripped
        pem = f"-----BEGIN {kind}-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END {kind}-----"
        text = (f"key {secrets[0]} and {secrets[1]} and {secrets[2]}\n"
                f"Authorization: Bearer {secrets[3]}\nCLAUDE_MM_BOT_TOKEN={secrets[4]}\n{pem}\n"
                "tell @channel and @someone")
        tr.write(tr.prompt("PROMPT-S"), tr.text(text))
        self.root.stop(tr, last=text)
        self.root.wait_done(1)
        got = "\n".join(bodies(FAKE.snapshot()))
        for s in secrets + ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"]:
            self.assertNotIn(s, got)
        self.assertIn("«REDACTED", got)
        self.assertNotIn("@channel", got)
        self.assertNotIn("@someone", got)
        self.assertIn("@\u200bchannel", got)

    # T9 - a black-holed Mattermost: the hook returns fast and clean; nothing is lost or doubled
    def test_t9_blackhole_never_blocks_and_never_duplicates(self):
        env = {"MM_MIRROR_POST_TIMEOUT": "2"}
        for mode in ("hole", "hole-accept"):
            FAKE.reset()
            root = Root()
            root.env.update(env)
            tr = root.transcript(sid_new())
            tr.write(tr.prompt("PROMPT-H"), tr.text("REPLY-H"))
            FAKE.mode = mode
            r, dt = root.stop(tr, last="REPLY-H")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, b"")
            self.assertLess(dt, 8, f"hook took {dt:.1f}s against a black hole ({mode})")
            root.wait_done(1)
            FAKE.release.set()
            time.sleep(0.5)
            FAKE.mode = "ok"
            FAKE.release.clear()
            root.stop(tr, last="REPLY-H")
            root.wait_done(2)
            got = [b.split("\n", 1)[1] for b in bodies(FAKE.snapshot())]
            self.assertEqual(got, ["PROMPT-H", "REPLY-H"], f"{mode}: {got}")
            # X3: the session stays in ONE thread after the unknown-outcome root
            tr.write(tr.prompt("PROMPT-H2"), tr.text("REPLY-H2", mid="m2"))
            root.stop(tr, last="REPLY-H2")
            root.wait_done(3)
            got = texts(FAKE.snapshot())
            self.assertEqual(got, ["PROMPT-H", "REPLY-H", "PROMPT-H2", "REPLY-H2"], f"{mode}: {got}")
            self.assertOneThread(FAKE.snapshot())

    # T10 - a 500ing Mattermost: the hook exits 0 at once; the stop is logged
    def test_t10_500_never_fails_the_hook(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-5"), tr.text("REPLY-5"))
        FAKE.mode = "500"
        r, dt = self.root.stop(tr, last="REPLY-5")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        self.assertLess(dt, 8)
        self.root.wait_done(1)
        self.assertEqual(FAKE.snapshot(), [])
        self.assertIn("stopped=post fail 500", self.root.logtext())

    # T11 - malformed and unknown transcript lines are skipped and logged, never fatal
    def test_t11_malformed_lines(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-M"), raw="{this is not json\n")
        tr.write({"type": "user", "message": "not-a-dict"}, {"type": "assistant", "message": {"content": 7}},
                 {"type": "future-entry-kind", "payload": {"x": 1}}, [1, 2, 3], tr.text("REPLY-M"))
        self.root.stop(tr, last="REPLY-M")
        self.root.wait_done(1)
        got = [b.split("\n", 1)[1] for b in bodies(FAKE.snapshot())]
        self.assertEqual(got, ["PROMPT-M", "REPLY-M"])
        self.assertIn("skip transcript line", self.root.logtext())
        self.assertNotIn("Traceback", self.root.logtext())

    # T12 - concurrent hooks: two sessions never cross threads; one session's overlapping
    #       Stops never duplicate
    def test_t12_concurrency(self):
        trs = [self.root.transcript(sid_new()) for _ in range(2)]
        for i, tr in enumerate(trs):
            for t in range(3):
                tr.write(tr.prompt(f"S{i}-P{t}"), tr.text(f"S{i}-R{t}", mid=f"m{t}"))
        threads = [threading.Thread(target=self.root.stop, args=(tr,), kwargs={"last": f"S{i}-R2"})
                   for i, tr in enumerate(trs) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.root.wait_done(4)
        posts = FAKE.snapshot()
        self.assertEqual(len(posts), 12, bodies(posts))
        for i in range(2):
            mine = [p for p in posts if f"S{i}-" in p["message"]]
            self.assertEqual([p["message"].split("\n", 1)[1] for p in mine],
                             [f"S{i}-{k}{t}" for t in range(3) for k in ("P", "R")])
            self.assertOneThread(mine)
        roots = {[p for p in posts if f"S{i}-" in p["message"]][0]["id"] for i in range(2)}
        self.assertEqual(len(roots), 2, "two sessions shared a thread")

    # T13 - a bridge-launched session still stands down: nothing posted, no worker
    def test_t13_bridge_stand_down(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-B"), tr.text("REPLY-B"))
        r, _ = self.root.stop(tr, env={"CLAUDE_BRIDGE_THREAD": "root123"})
        self.assertEqual(r.returncode, 0)
        time.sleep(2)
        self.assertEqual(FAKE.snapshot(), [])
        self.assertFalse(os.path.exists(self.root.log))

    # T14 - manual mode is unchanged: one post, with the mention
    def test_t14_manual_message(self):
        r, _ = self.root.hook({}, args=["manual hello"], stdin_closed=True)
        self.assertEqual(r.returncode, 0)
        got = bodies(FAKE.snapshot())
        self.assertEqual(got, [f"{MENTION} manual hello"])

    # T15 - the final reply missing from the transcript at Stop is posted from the payload, and
    #       not again when it lands
    def test_t15_final_reply_late_in_transcript(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-L"))
        self.root.stop(tr, last="REPLY-LATE final words")
        self.root.wait_done(1)
        tr.write(tr.text("REPLY-LATE final words"), tr.prompt("PROMPT-L2"), tr.text("REPLY-L2"))
        self.root.stop(tr, last="REPLY-L2")
        self.root.wait_done(2)
        got = [b.split("\n", 1)[1] for b in bodies(FAKE.snapshot())]
        self.assertEqual(got, ["PROMPT-L", "REPLY-LATE final words", "PROMPT-L2", "REPLY-L2"])

    # T16 - no redactor, no post (fail closed)
    def test_t16_no_redactor_posts_nothing(self):
        root = Root(with_sanitizer=False)
        tr = root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-NR"), tr.text("REPLY-NR"))
        root.stop(tr, last="REPLY-NR")
        deadline = time.time() + 30
        while "redactor unavailable" not in root.logtext() and time.time() < deadline:
            time.sleep(0.2)
        self.assertIn("redactor unavailable", root.logtext())
        self.assertEqual(FAKE.snapshot(), [])

    # T17 - the Stop hook stays far inside its 15 s budget at the default settings
    def test_t17_hook_budget(self):
        tr = self.root.transcript(sid_new())
        for t in range(20):
            tr.write(tr.prompt(f"P{t}"), tr.text(f"R{t}", mid=f"m{t}"))
        times = []
        for _ in range(5):
            r, dt = self.root.stop(tr, last="R19")
            self.assertEqual(r.returncode, 0)
            times.append(dt)
        self.root.wait_done(5)
        self.assertEqual(len(FAKE.snapshot()), 40)
        self.assertLess(max(times), 6, times)
        print(f"\n[T17] Stop hook wall times (s): {[round(t, 2) for t in times]}", file=sys.stderr)

    # T18 (X1) - Stops queued behind a busy (back-filling) worker post nothing twice, in order
    def test_t18_queued_stops_behind_busy_worker(self):
        tr = self.root.transcript(sid_new())
        for i in range(20):
            tr.write(tr.prompt(f"OLD-P{i}"), tr.text(f"OLD-R{i}", mid=f"o{i}"))
        FAKE.delay = 0.3
        self.root.stop(tr, last="OLD-R19")
        time.sleep(1.0)
        for i in range(3):
            tr.write(tr.prompt(f"NEW-P{i}"), tr.text(f"NEW-R{i}", mid=f"n{i}"))
            self.root.stop(tr, last=f"NEW-R{i}")
            time.sleep(0.5)
        self.root.wait_done(4, timeout=120)
        want = [x for i in range(20) for x in (f"OLD-P{i}", f"OLD-R{i}")] + \
               [x for i in range(3) for x in (f"NEW-P{i}", f"NEW-R{i}")]
        self.assertEqual(texts(FAKE.snapshot()), want)
        self.assertOneThread(FAKE.snapshot())

    # T19 (X1) - six overlapping Stops per session, two sessions: exactly 12 posts
    def test_t19_many_overlapping_stops(self):
        FAKE.delay = 0.1
        trs = [self.root.transcript(sid_new()) for _ in range(2)]
        for i, tr in enumerate(trs):
            for t in range(3):
                tr.write(tr.prompt(f"S{i}-P{t}"), tr.text(f"S{i}-R{t}", mid=f"m{t}"))
        ths = [threading.Thread(target=self.root.stop, args=(tr,), kwargs={"last": f"S{i}-R{k % 3}"})
               for i, tr in enumerate(trs) for k in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.root.wait_done(12, timeout=120)
        posts = FAKE.snapshot()
        for i in range(2):
            mine = [p for p in posts if f"S{i}-" in p["message"]]
            self.assertEqual(texts(mine), [f"S{i}-{k}{t}" for t in range(3) for k in ("P", "R")])
            self.assertOneThread(mine)
        self.assertEqual(len(posts), 12)

    # T20 (X2) - a worker killed after the server created a post but before it heard back:
    #            no duplicate, one thread, and the dead worker's lock is taken over at once
    def test_t20_kill_between_post_and_mark(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-K"), tr.text("REPLY-K0", mid="m0"))
        self.root.stop(tr, last="REPLY-K0")
        self.root.wait_done(1)
        tr.write(tr.prompt("PROMPT-K1"), tr.text("REPLY-K1", mid="m1"))
        FAKE.mode = "hole-accept"
        self.root.stop(tr, last="REPLY-K1")
        lock = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.lock")
        end = time.time() + 30
        while time.time() < end and not any("PROMPT-K1" in b for b in bodies(FAKE.snapshot())):
            time.sleep(0.1)
        with open(os.path.join(lock, "pid")) as fh:
            pid = int(fh.read())
        kill(pid)
        while pid_alive(pid):
            time.sleep(0.1)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        tr.write(tr.prompt("PROMPT-K2"), tr.text("REPLY-K2", mid="m2"))
        t0 = time.time()
        self.root.stop(tr, last="REPLY-K2")
        self.root.wait_done(2)
        self.assertLess(time.time() - t0, 30, "the dead worker's lock blocked the session")
        self.assertEqual(texts(FAKE.snapshot()),
                         ["PROMPT-K", "REPLY-K0", "PROMPT-K1", "REPLY-K1", "PROMPT-K2", "REPLY-K2"])
        self.assertOneThread(FAKE.snapshot())

    # T21 (X4) - a trickling server cannot keep a worker past its hard wall, and the next run
    #            neither races it nor duplicates
    def test_t21_hard_wall_against_trickling_server(self):
        self.root.env.update({"MM_MIRROR_MAX_SECS": "10", "MM_MIRROR_POST_TIMEOUT": "30"})
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-T"), tr.text("REPLY-T"))
        FAKE.mode = "trickle"
        t0 = time.time()
        self.root.stop(tr, last="REPLY-T")
        lock = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.lock")
        pid = None
        while time.time() - t0 < 10 and pid is None:
            try:
                with open(os.path.join(lock, "pid")) as fh:
                    pid = int(fh.read())
            except (OSError, ValueError):
                time.sleep(0.1)
        self.assertIsNotNone(pid)
        # a Stop while it is alive must not take its lock over
        self.root.stop(tr, last="REPLY-T")
        with open(os.path.join(lock, "deadline")) as fh:
            deadline = int(fh.read())
        while pid_alive(pid) and time.time() < deadline + 30:
            time.sleep(0.2)
        over = time.time() - deadline
        # measured against the worker's OWN recorded wall, not the hook's start (start-up lag on a
        # loaded machine is not the wall's business): dead by the wall + 2 s (+2 s polling)
        self.assertLess(over, 2 + 2, f"worker lived {over:.1f}s past its wall")
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        self.root.wait_done(2, timeout=40, locks=False)    # the walled worker's lock is left behind
        self.assertIn("killed at its hard wall", self.root.logtext())
        self.root.stop(tr, last="REPLY-T")
        self.root.wait_done(3, timeout=40)
        self.assertEqual(texts(FAKE.snapshot()), ["PROMPT-T", "REPLY-T"])
        self.assertOneThread(FAKE.snapshot())

    # T22 (B4) - a session's FIRST Stop, final reply not yet in the transcript: posted from the
    #            payload, and not again when it lands
    def test_t22_first_stop_waits_for_transcript(self):
        self.root.env["MM_MIRROR_SETTLE_SECS"] = "20"
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-F"))
        self.root.stop(tr, last="REPLY-F final")
        end = time.time() + 15
        while time.time() < end and not FAKE.snapshot():           # the worker posts the prompt...
            time.sleep(0.1)
        time.sleep(1.5)                                            # ...and then waits, posting nothing
        self.assertEqual(texts(FAKE.snapshot()), ["PROMPT-F"])      # the payload is never posted
        tr.write(tr.text("REPLY-F final"))                          # lands while the worker waits
        self.root.wait_done(1)
        self.assertEqual(texts(FAKE.snapshot()), ["PROMPT-F", "REPLY-F final"])
        tr.write(tr.prompt("PROMPT-F2"), tr.text("REPLY-F2", mid="m2"))
        self.root.stop(tr, last="REPLY-F2")
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PROMPT-F", "REPLY-F final", "PROMPT-F2", "REPLY-F2"])

    # T23 (B3) - a final message of two text blocks: each block posted once, whichever way it lands
    def test_t23_multiblock_final(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-0"), tr.text("REPLY-0", mid="m0"))
        self.root.stop(tr, last="REPLY-0")
        self.root.wait_done(1)
        # (a) neither block in the transcript at Stop
        tr.write(tr.prompt("PROMPT-MB"))
        self.root.stop(tr, last="PART-A first block\n\nPART-B second block")
        self.root.wait_done(2)
        tr.write(tr.text("PART-A first block", mid="mz"), tr.text("PART-B second block", mid="mz"))
        # (b) the first block in, the second not
        tr.write(tr.prompt("PROMPT-MC"), tr.text("PART-C first block", mid="mc"))
        self.root.stop(tr, last="PART-C first block\n\nPART-D second block")
        self.root.wait_done(3)
        tr.write(tr.text("PART-D second block", mid="mc"), tr.prompt("PROMPT-END"), tr.text("REPLY-END", mid="me"))
        self.root.stop(tr, last="REPLY-END")
        self.root.wait_done(4)
        joined = "\n".join(texts(FAKE.snapshot()))
        for part in ("PART-A", "PART-B", "PART-C", "PART-D", "PROMPT-END", "REPLY-END"):
            self.assertEqual(joined.count(part), 1, f"{part}: {texts(FAKE.snapshot())}")
        self.assertLess(joined.index("PART-D"), joined.index("PROMPT-END"))

    # T24 (B1) - MM_MIRROR_SETTLE_SECS=0: every final reply still exactly once
    def test_t24_settle_zero(self):
        self.root.env["MM_MIRROR_SETTLE_SECS"] = "0"
        tr = self.root.transcript(sid_new())
        for t in range(3):
            tr.write(tr.prompt(f"P{t}"))
            self.root.stop(tr, last=f"R{t} final")
            self.root.wait_done(t + 1)
            tr.write(tr.text(f"R{t} final", mid=f"m{t}"))
        tr.write(tr.prompt("P-END"), tr.text("R-END", mid="me"))
        self.root.stop(tr, last="R-END")
        self.root.wait_done(4)
        self.assertEqual(texts(FAKE.snapshot()),
                         ["P0", "R0 final", "P1", "R1 final", "P2", "R2 final", "P-END", "R-END"])

    # T25 (X6) - the same transcript spelt differently (separators, case) is the same mark
    def test_t25_path_spelling(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PROMPT-P"), tr.text("REPLY-P"))
        self.root.stop(tr, last="REPLY-P")
        self.root.wait_done(1)
        alt = tr.path.replace("\\", "/")
        if os.name == "nt":
            alt = alt[0].swapcase() + alt[1:]
        self.root.hook({"session_id": tr.sid, "transcript_path": alt, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash",
                        "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        got = texts(FAKE.snapshot())
        self.assertEqual(got[:2], ["PROMPT-P", "REPLY-P"])
        self.assertEqual(len(got), 3, got)

    # T26 (R1) - every secret shape in the table is masked (fake values); prose stays readable
    def test_t26_redaction_table(self):
        M = load_helper(SRC)
        red = M.load_redactor()
        self.assertIsNotNone(red)
        F = "FAKEq9Zx7Lm2Np4Rt6Vw8Yb1Cd3Ef5Gh"
        pem = "PRIVATE" + " KEY"          # assembled, so the repo's own secret scanner is not tripped
        table = {
            "env bot token": (f"CLAUDE_MM_BOT_TOKEN={F}", F),
            "env password": (f"POSTGRES_PASSWORD={F}", F),
            "env short value": ("DB_PASSWORD=Hx7#k2q", "Hx7#k2q"),
            "env *_KEY": (f"LITELLM_MASTER_KEY={F}", F),
            "env quoted": (f'OPENAI_API_KEY="{F}"', F),
            "env SECRET_KEY_BASE": (f"SECRET_KEY_BASE={F}", F),
            "env *_SECRET": (f"AUTHENTIK_CLIENT_SECRET={F}", F),
            "env *_PASS": (f"SMTP_PASS={F}", F),
            "env *_PWD": (f"REDIS_PWD={F}", F),
            "env *_CREDENTIALS": (f"GOOGLE_CREDENTIALS={F}", F),
            "env DATABASE_URL": (f"DATABASE_URL=postgres://app:{F}@db:5432/x", F),
            "url postgres": (f"postgresql://app:{F}@db:5432/x", F),
            "url redis": (f"redis://:{F}@redis:6379/0", F),
            "url mysql": (f"mysql://root:{F}@db/x", F),
            "url mongodb": (f"mongodb+srv://u:{F}@cluster/x", F),
            "url amqp": (f"amqp://guest:{F}@mq:5672/", F),
            "url https": (f"https://admin:{F}@example.com/x", F),
            "json api_key": (f'{{"api_key": "{F}"}}', F),
            "json token": (f'{{"token": "{F}"}}', F),
            "json password": (f'{{"password": "{F}"}}', F),
            "json secret": (f'{{"client_secret": "{F}"}}', F),
            "yaml password": (f"password: {F}", F),
            "basic auth": (f"Authorization: Basic {F}==", F),
            "bearer": (f"Authorization: Bearer {F}", F),
            "aws key id": ("AKIA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"),
            "aws secret bare": ("the aws secret is wJalrXUtnFEMI/K7MDENG/bPxRfiCYFAKEKEY12a ok", "wJalrXUtnFEMI"),
            "pem complete": (f"-----BEGIN RSA {pem}-----\nMIIFAKEFAKEFAKE\n-----END RSA {pem}-----", "MIIFAKE"),
            "pem truncated": (f"```\n-----BEGIN OPENSSH {pem}-----\nb3BlbnNzaFAKEFAKE\n```\nafter", "b3BlbnNzaFAKE"),
            "sk- key": (f"sk-{F}", F),
            "sk-ant": (f"sk-ant-{F}", F),
            "stripe live": (f"sk_live_{F}", F),
            "stripe restricted": (f"rk_live_{F}", F),
            "stripe publishable": (f"pk_live_{F}", F),
            "github pat": ("ghp_" + "Z9" * 20, "Z9Z9Z9Z9Z9"),
            "github fine": (f"github_pat_{F}{F}", F),
            "slack": (f"xoxb-1234-{F}", F),
            "hf": (f"hf_{F}", F),
            "tailscale auth": (f"tskey-auth-{F}", F),
            "tailscale": (f"tskey-{F}", F),
            "google": ("AIza" + F + "abc", F),
            "gitlab": (f"glpat-{F}", F),
            "pgp block": ("-----BEGIN PGP PRIVATE " + "KEY BLOCK-----\n\nlQOYBFAKEFAKEFAKEpgpbody\n-----END PGP PRIVATE "
                          + "KEY BLOCK-----", "lQOYBFAKEFAKE"),
            "pem truncated in prose": (f"-----BEGIN {pem}-----\nMIIEvQIBADANBgkqhkiG9w0B\nMIIEvQIBADANBgkqhkiG9w0C",
                                       "MIIEvQIBADANBgkqhkiG9w0C"),
            # assembled, so the repo's own secret scanner is not tripped by a fake
            "telegram": ("https://api.telegram.org/bot" + "123456789" + ":" + "AAHfakeFAKEq9Zx7Lm2Np4Rt6Vw8Yb1Cd3E"
                         + "/sendMessage", "AAHfakeFAKEq9Zx7"),
            ".NET Password=": ("Server=db;User Id=sa;Password=Hx7#k2q!x9;", "Hx7#k2q!x9"),
            ".NET Pwd=": ("Driver={x};Uid=sa;Pwd=Qz8!m3Lp#t;", "Qz8!m3Lp#t"),
            "Password: capital": ("Password: Hx7#k2q!x9", "Hx7#k2q!x9"),
            "ApiKey= short": ("ApiKey=Hx7#k2q!x9", "Hx7#k2q!x9"),
            "X-Api-Key short": ("X-Api-Key: Hx7#k2q!x9", "Hx7#k2q!x9"),
            "curl -u": (f"curl -u admin:{F} https://x/api", F),
            "docker login -p": (f"docker login -u bot -p {F} registry", F),
            "--password": ("mysql -u root --password Hx7k2qZm9Lp4 db", "Hx7k2qZm9Lp4"),
            "mysql -pPASS": ("mysql -u root -pHx7k2qZm9Lp4 db", "Hx7k2qZm9Lp4"),
            "azure AccountKey": (f"DefaultEndpointsProtocol=https;AccountName=x;AccountKey={F}==;", F),
            "discord": ("MTk4NjIyNDgzNDcxOTI1MjQ4.Cl2F" + "MQ.ZnCjm1XVW7vRze4b7Cq4se7kKWs", "ZnCjm1XVW7vRz" + "e4b7Cq4se7kKWs"),
            "service-account JSON (one line, escaped)": (
                '{"type": "service_account", "private_key": "-----BEGIN ' + pem + '-----\\nMIIEvQIBADANBgkqhkiG9w0BFAKE'
                '\\nAAAAB3NzaC1yc2EFAKEBODY\\n-----END ' + pem + '-----\\n", "client_email": "svc@example.com"}',
                "AAAAB3NzaC1yc2EFAKEBODY"),
            "PEM in a blockquote": ("> -----BEGIN " + pem + "-----\n> MIIEvQIBADANBgkqhkiG9w0BFAKE\n"
                                    "> QUOTEDBODYFAKEFAKE12\n> -----END " + pem + "-----", "QUOTEDBODYFAKEFAKE12"),
            "PEM as python concatenation": ('KEY = ("-----BEGIN ' + pem + '-----\\n"\n       "MIIEvQIBADANBgkqhkiG9w0B\\n"\n'
                                            '       "CONCATBODYFAKEFAKE34\\n"\n       "-----END ' + pem + '-----")',
                                            "CONCATBODYFAKEFAKE34"),
            "PEM with cat -n numbers": ("     1\t-----BEGIN " + pem + "-----\n     2\tMIIEvQIBADANBgkqhkiG9w0B\n"
                                        "     3\tNUMBEREDBODYFAKE56\n     4\t-----END " + pem + "-----", "NUMBEREDBODYFAKE56"),
            "PEM after armor headers": ("-----BEGIN RSA " + pem + "-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,00"
                                        "\n\n\n\nARMOREDBODYFAKE78ab\n-----END RSA " + pem + "-----", "ARMOREDBODYFAKE78ab"),
            "800-char env token (tail)": ("AWS_SESSION_TOKEN=" + "Ab1" * 266 + "TAILFAKE9", "TAILFAKE9"),
            "800-char JSON token": ('{"aws_session_token": "' + "Ab1" * 266 + 'TAILFAKE8"}', "TAILFAKE8"),
            "600-char quoted env token": ('GITHUB_TOKEN="' + "Cd2" * 197 + 'TAILFAKE7"', "TAILFAKE7"),
            "env changeme": ("DB_PASSWORD=changeme", "changeme"),
            "env postgres": ("POSTGRES_PASSWORD=postgres", "=postgres"),
            "env passphrase": ("ADMIN_PASSWORD=correct-horse-battery-staple", "correct-horse"),
            "yaml hunter2": ("password: hunter2", "hunter2"),
            "k8s tls.key": ("tls.key: " + "LS0tLS1CRUdJTi" * 100, "LS0tLS1CRUdJTiLS0t"),
            ".netrc": ("machine api.example.com login bot password Zq8xFakeP4ssw0rd", "Zq8xFakeP4ssw0rd"),
            "url password with @": ("postgres://app:p@ssFAKE99@db:5432/x", "ssFAKE99"),
            # attempt-4 tester's 22 shapes (redact_probe4), fake values
            "a4 password: special then prose": ("Set the password: Tr0ub4dor&3x and restart the service.", "Tr0ub4dor"),
            "a4 password: short mixed then prose": ("Use password: Zq7xW2 for the admin login.", "Zq7xW2"),
            "a4 DB_PASSWORD= spaced then prose": ("Set DB_PASSWORD= Hx8kQ2m9 then restart.", "Hx8kQ2m9"),
            "a4 token: short then prose": ("The token: Ab3Cd9Ef is for staging only.", "Ab3Cd9Ef"),
            "a4 PGPASSWORD=": ("export PGPASSWORD=Kj4mN8pQ2r", "Kj4mN8pQ2r"),
            "a4 DBPASS=": ("DBPASS=Wv5tY7uI3o", "Wv5tY7uI3o"),
            "a4 GHTOKEN=": ("GHTOKEN=Qa1Ws2Ed3Rf4Tg5Yh6", "Qa1Ws2Ed3Rf4Tg5Yh6"),
            "a4 MYSQLPASSWORD=": ("MYSQLPASSWORD=Pl9Ok8Ij7Uh6", "Pl9Ok8Ij7Uh6"),
            "a4 tail after ;": ("DB_PASSWORD=Fk3j;Zr8Lm2Np", "Zr8Lm2Np"),
            "a4 tail after ,": ("ADMIN_PASSWORD=Gh5k,Wq9Xc4Vb", "Wq9Xc4Vb"),
            "a4 JSON escaped quote": ('{"password": "Ab\\"Zt7Yu6Io5"}', "Zt7Yu6Io5"),
            "a4 yaml block scalar": ("password: |\n  Mn4Bv3Cx2Za1\n", "Mn4Bv3Cx2Za1"),
            "a4 yaml next line": ("password:\n  Rt5Gy6Hu7Ji8\n", "Rt5Gy6Hu7Ji8"),
            "a4 .pgpass": ("db.internal:5432:app:appuser:Sd4Fg5Hj6Kl7", "Sd4Fg5Hj6Kl7"),
            "a4 PuTTY PPK line 1": ("PuTTY-User-" + "Key-File-2: ssh-rsa\nEncryption: none\nPrivate-Lines: 2\n"
                                    "QUFBQUIzTnphQzF5YzJFQUFBQURBUUFCQUFBQkFRQ3pGYWtlUHJpdmF0ZUJvZHk=\n"
                                    "RmFrZVByaXZhdGVCb2R5VHdvRmFrZQ==\n", "QUFBQUIzTnphQzF5YzJFQUFBQURBUUFC"),
            "a4 PuTTY PPK line 2": ("PuTTY-User-" + "Key-File-2: ssh-rsa\nEncryption: none\nPrivate-Lines: 2\n"
                                    "QUFBQUIzTnphQzF5YzJFQUFBQURBUUFCQUFBQkFRQ3pGYWtlUHJpdmF0ZUJvZHk=\n"
                                    "RmFrZVByaXZhdGVCb2R5VHdvRmFrZQ==\n", "RmFrZVByaXZhdGVCb2R5"),
            "a4 Azure SAS sig": ("https://acct.blob.core.windows.net/c/f?sv=2022-11-02&se=2030&sp=r&sig=Fa1keSigNat2ure3XyZ%2Bab%3D",
                                 "Fa1keSigNat2ure3XyZ"),
            "a4 markdown table cell": ("| user | password |\n|---|---|\n| admin | Qw8Er7Ty6 |", "Qw8Er7Ty6"),
            "a4 set -x echo": ("+ export ADMIN_PASSWORD=Lk2Jh3Gf4", "Lk2Jh3Gf4"),
            "a4 python kwarg": ("connect(host='db', password='Zx9Cv8Bn7')", "Zx9Cv8Bn7"),
            "a4 PEM after 9 armor lines": ("-----BEGIN PGP PRIVATE " + "KEY BLOCK-----\n" + "".join(f"H{i}: v\n" for i in range(9))
                                           + "\nlQOYBF9ha2VQcml2YXRlS2V5Qm9keUZha2U=\n-----END PGP PRIVATE " + "KEY BLOCK-----",
                                           "lQOYBF9ha2VQcml2YXRlS2V5Qm9keUZha2U"),
            "a4 PEM after a long comment line": ("-----BEGIN RSA " + pem + "-----\nProc-Type: 4,ENCRYPTED\n"
                                                 "DEK-Info: AES-128-CBC,FAKE00112233445566778899AABBCCDD\n\n"
                                                 "MIIEFakeRsaPrivateKeyBodyLineOneAAAA\n-----END RSA " + pem + "-----",
                                                 "MIIEFakeRsaPrivateKeyBodyLineOne"),
            "a4 user:pass@host without scheme": ("proxy: user:Op9Iu8Yt7@proxy.local:3128", "Op9Iu8Yt7"),
            # attempt-5 F3: nested yaml (a non-secret parent must not hide its children)
            "a5 nested next-line": ("db:\n  password:\n    Nx7Yz8Ab9\n  host: h\n", "Nx7Yz8Ab9"),
            "a5 nested block scalar": ("app:\n  token: |\n    Tk1Lm2Np3Qr4\n", "Tk1Lm2Np3Qr4"),
            "a5 nested folded": ("svc:\n  auth:\n    token: >-\n      Fo5Ld6Ed7\n", "Fo5Ld6Ed7"),
            "a5 k8s stringData": ("kind: Secret\nstringData:\n  password: Sd9Kk8Jj7\n  username: app\n", "Sd9Kk8Jj7"),
            "a5 compose environment": ("services:\n  db:\n    environment:\n      POSTGRES_PASSWORD: Cp4Ee5Rr6\n",
                                       "Cp4Ee5Rr6"),
            "mm personal token": ("use token 9xk3fakefakefakefakefake1a in the header", "9xk3fakefakefakefakefake1a"),
        }
        leaks = [n for n, (text, core) in table.items() if core in red(text)]
        self.assertEqual(leaks, [], "leaked shapes")
        self.assertIn("after", red(table["pem truncated"][0]))       # masked only to the block's end
        clean = "the commit 0123456789abcdef0123456789abcdef01234567 is fine; secret santa list"
        self.assertEqual(red(clean), clean)

    # T27 - "start from now": seeding a session's mark posts nothing of its existing transcript
    def test_t27_seed_start_from_now(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("OLD-PROMPT"), tr.text("OLD-REPLY"))
        r = subprocess.run([sys.executable, os.path.join(self.root.dir, "scripts", "notify_mattermost_mirror.py"),
                            "seed", tr.path], capture_output=True, text=True, timeout=60,
                           env=dict(self.root.env, MM_ROOT_DIR=self.root.dir))
        self.assertIn("seeded 1", r.stdout)
        tr.write(tr.prompt("NEW-PROMPT"), tr.text("NEW-REPLY", mid="m2"))
        self.root.stop(tr, last="NEW-REPLY")
        self.root.wait_done(1)
        self.assertEqual(texts(FAKE.snapshot()), ["NEW-PROMPT", "NEW-REPLY"])

    # T28 (F1) - an interim reply whose text also occurs inside the final reply: both posted whole
    def test_t28_interim_inside_final(self):
        self.root.env["MM_MIRROR_SETTLE_SECS"] = "20"
        tr = self.root.transcript(sid_new())
        final = "Summary: the tests are Done. Docs updated too."
        tr.write(tr.prompt("PROMPT-FR"), tr.text("Done.", stop="tool_use", mid="m0"))
        self.root.stop(tr, last=final)
        time.sleep(1.5)
        tr.write(tr.text(final, mid="m1"))
        self.root.wait_done(1)
        tr.write(tr.prompt("PROMPT-FR2"), tr.text("REPLY-FR2", mid="m2"))
        self.root.stop(tr, last="REPLY-FR2")
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PROMPT-FR", "Done.", final, "PROMPT-FR2", "REPLY-FR2"])

    # T29 (F2) - a late final reply, then a task-notification-driven reply that is a substring of it
    def test_t29_task_reply_after_late_final(self):
        tr = self.root.transcript(sid_new())                     # settle 1 s: the final misses it
        final = "Build started in the background. Done."
        tr.write(tr.prompt("P-BG"))
        self.root.stop(tr, last=final)
        self.root.wait_done(1)
        tr.write(tr.text(final, mid="m0"), tr.task_note("bg task finished"), tr.text("Done.", mid="m1"))
        self.root.stop(tr, last="Done.")
        self.root.wait_done(2)
        tr.write(tr.prompt("P-NEXT"), tr.text("R-NEXT", mid="m2"))
        self.root.stop(tr, last="R-NEXT")
        self.root.wait_done(3)
        self.assertEqual(texts(FAKE.snapshot()), ["P-BG", final, "Done.", "P-NEXT", "R-NEXT"])

    # T30 (F3) - a multi-line final reply keeps every line, indent and fence, landing in or after the wait
    def test_t30_multiline_final_formatting(self):
        final = "Here is the fix:\n\n```python\ndef f(x):\n    return x + 1\n```\n\n- step one\n- step two"
        for settle, delay in (("20", 1.5), ("1", None)):
            FAKE.reset()
            root = Root()
            root.env["MM_MIRROR_SETTLE_SECS"] = settle
            tr = root.transcript(sid_new())
            tr.write(tr.prompt("P-ML"))
            root.stop(tr, last=final)
            if delay:
                time.sleep(delay)
                tr.write(tr.text(final, mid="m0"))
                root.wait_done(1)
            else:
                root.wait_done(1)
                tr.write(tr.text(final, mid="m0"), tr.prompt("P-ML2"), tr.text("R-ML2", mid="m1"))
                root.stop(tr, last="R-ML2")
                root.wait_done(2)
            posts = bodies(FAKE.snapshot())
            self.assertEqual(posts[1], "**\U0001f916 Claude**\n" + final, f"settle={settle}")
            self.assertEqual(sum(final in b for b in posts), 1)

    # T31 (F4) - root outcome unknown AND the lookup down: a permission Notification is queued, not
    #            posted as a second root; when the lookup recovers it is posted into the ONE thread
    def test_t31_mention_while_root_unresolved(self):
        self.root.env["MM_MIRROR_POST_TIMEOUT"] = "2"
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PU"))
        FAKE.mode = "hole-accept"
        self.root.stop(tr)
        self.root.wait_done(1)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_mode = "500"
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash",
                        "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PU"])                 # nothing opened a second root
        FAKE.get_mode = "ok"
        tr.write(tr.text("RU", mid="m1"))
        self.root.stop(tr, last="RU")
        self.root.wait_done(3)
        posts = FAKE.snapshot()
        got = texts(posts)
        self.assertEqual(got[0], "PU")
        self.assertIn("needs your permission", got[1])
        self.assertTrue(bodies(posts)[1].startswith(MENTION))
        self.assertEqual(got[2], "RU")
        self.assertEqual(len(posts), 3)
        self.assertOneThread(posts)

    # T32 - a pending post whose lookup never succeeds expires after N tries / T seconds, treated as
    #       POSTED (logged): the session moves on, nothing is duplicated
    def test_t32_pending_expiry(self):
        self.root.env.update({"MM_MIRROR_POST_TIMEOUT": "2", "MM_MIRROR_PENDING_TRIES": "2",
                              "MM_MIRROR_PENDING_SECS": "0"})
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        tr.write(tr.prompt("P1"), tr.text("R1", mid="m1"))
        FAKE.mode = "hole-accept"                    # P1 is created; its answer never arrives
        self.root.stop(tr, last="R1")
        self.root.wait_done(2)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_mode = "500"                        # and the lookup stays down
        self.root.stop(tr, last="R1")
        self.root.wait_done(3)
        self.assertEqual(texts(FAKE.snapshot()), ["P0", "R0", "P1"])     # try 1: nothing posted
        self.root.stop(tr, last="R1")
        self.root.wait_done(4)
        self.assertIn("EXPIRED", self.root.logtext())
        self.assertEqual(texts(FAKE.snapshot()), ["P0", "R0", "P1", "R1"])
        self.assertOneThread(FAKE.snapshot())

    # T33 (F6) - ordinary code and prose come through redaction and defang byte-for-byte unchanged
    def test_t33_false_positive_table(self):
        M = load_helper(SRC)
        red = M.load_redactor()
        samples = [
            "merged at b3f74e620d3c91057d22d4bd51a718fad5fee9b4 on development",
            "commit b3f74e6 (on 1a2dc1e)",
            "session 3cf42692-267e-4d13-a1c2-d605d9771876 started",
            "image sha256:fe3f7ce4e1a9063d83f575e93126c6b11c6fd52660f0e57a40b5d895ccdf448f",
            "The secret to a good test is that it can fail.",
            "That costs about 2000 tokens per turn.",
            "Reset the password in the admin UI, then log in again.",
            "see https://code.claude.com/docs/en/hooks#stop and http://localhost:8065/api/v4/posts",
            "data = b64decode('SGVsbG8gV29ybGQhIFRoaXMgaXMgYmFzZTY0')",
            "call it with max_tokens=4096 and temperature=0.2",
            "max_tokens: 4096",
            "result: tests_passed=27 tests_failed=0",
            "tokenizer = AutoTokenizer.from_pretrained(name)",
            "PRIMARY_KEY=id, SORT_KEY=created_at",
            "secret_name: db-credentials",
            "password: rotated yesterday, see the runbook",
            "token: the next step is to rotate it",
            "The flaws were in KubernetesSecretProviderClassVolumeMount.",
            "This draws on src/main/java/org/apache/commons/LangUtils for parsing",
            "the secret lives in documentation/runbooks/BackupAndRestoreProcedure",
            "AWS docs: CreateMultipartUploadRequestBuilderFactory explains it",
            "A file that starts with -----BEGIN RSA PRIVATE" + " KEY----- is a key; never paste it.\n\nNext paragraph stays.",
            "```python\n@dataclass\nclass X: ...\n```",
            "git clone git@github.com:org/repo.git",
            "npm i @anthropic-ai/sdk",
            "write to ops@example.com",
            "run `mkdir -p build` then `ssh -p 22 host`",
            "the secretary's laws",
            "set DB_PASSWORD= to an empty value, or DB_PASSWORD=${DB_PASSWORD} from the env",
            "API_TOKEN=<your token here>",
            "password: see the vault entry",
            # attempt-4 tester's 16 ordinary samples (redact_probe4)
            "First pass: cleanup. Second pass: renamed.",
            "Verdict pass: correct, plan adequate.",
            "The review pass: thorough, nothing found.",
            "if password == expected:\n    return True",
            "self.token = token",
            "user.password = hash_password(raw)",
            "token = os.environ['MM_TOKEN']",
            "pwd: /srv/projects/app",
            "PWD=/c/Users/someone/project",
            "Run `pwd` and `cd ..`; pass: none.",
            "secret = None",
            "password: required, 12 chars minimum.",
            "The token: expired.",
            "bypass: enabled; compass: north.",
            "--pass-through=yes",
            "password_hash = bcrypt(pw)",
            # attempt-5 F3 structure that must stay: a mapping or list under a secret-named key
            "volumes:\n  - name: certs\n    secret:\n      secretName: certs\n",
            "password:\n  - at least 12 chars\n  - one digit\n",
        ]
        changed = [x for x in samples if M.defang(red(x)) != x]
        self.assertEqual(changed, [])
        self.assertEqual(M.defang("ping @alice and @channel, not `@bob`"),
                         "ping @\u200balice and @\u200bchannel, not `@bob`")

    # T34 (F7) - redaction is linear on pathological input, and the wall holds against a worker
    #            that cannot run its own code (suspended: as stuck as a GIL-holding regex)
    def test_t34_linear_redaction_and_external_wall(self):
        M = load_helper(SRC)
        red = M.load_redactor()
        cases = {
            "200k dotted run": "a." * 100000, "200k dashed run": "a-" * 100000,
            "dotted runs with spaces": ("a." * 1000 + " ") * 100, "dashed runs with spaces": ("a-" * 1000 + " ") * 100,
            "scheme-ish runs": ("a+b.c-d" * 290 + "://x ") * 60, "kv runs": ("a_b.c-d" * 9 + "=x ") * 20000,
            "1 MB prose": "The quick brown fox jumps over the lazy dog. " * 23000,
            "many BEGIN lines": ("-----BEGIN RSA PRIVATE" + " KEY-----\nnot base64 here\n") * 5000,
            "aws repeated": "aws " * 12500, "quotes": '"a":"b",' * 7500, "x: runs": "a:" * 20000 + "@",
            # attempt 5's F1: a table line followed by a long whitespace line (was 33 s at 40k)
            "table + 100k spaces": "| a |\n" + " " * 100000 + "x",
            "table + 100k tabs": "| a |\n" + "\t" * 100000 + "x",
            "table + sep + 100k spaces": "| a | b |\n|---|---|\n" + " " * 100000 + "x",
            "1000 x (table + 1000 spaces)": ("| a |\n" + " " * 1000 + "\n") * 1000,
        }
        slow = {}
        for name, text in cases.items():
            t0 = time.time()
            red(text)
            dt = time.time() - t0
            if dt > 3:
                slow[name] = round(dt, 1)
        self.assertEqual(slow, {})
        if os.name != "nt":
            self.skipTest("suspend half is Windows-only")
        import ctypes
        self.root.env.update({"MM_MIRROR_MAX_SECS": "10"})
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P-W"), tr.text("R-W"))
        FAKE.mode = "hole"
        t0 = time.time()
        self.root.stop(tr, last="R-W")
        lock = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.lock")
        pid = None
        while time.time() - t0 < 10 and pid is None:
            try:
                with open(os.path.join(lock, "pid")) as fh:
                    pid = int(fh.read())
            except (OSError, ValueError):
                time.sleep(0.1)
        self.assertIsNotNone(pid)
        h = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)          # PROCESS_SUSPEND_RESUME
        self.assertTrue(h)
        ctypes.windll.ntdll.NtSuspendProcess(h)
        ctypes.windll.kernel32.CloseHandle(h)
        with open(os.path.join(lock, "deadline")) as fh:
            deadline = int(fh.read())
        while pid_alive(pid) and time.time() < deadline + 30:
            time.sleep(0.2)
        over = time.time() - deadline
        # measured against the worker's OWN recorded wall, not the hook's start (start-up lag on a
        # loaded machine is not the wall's business): dead by the wall + 2 s (+2 s polling)
        self.assertLess(over, 2 + 2, f"suspended worker lived {over:.1f}s past its wall")
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        self.root.stop(tr, last="R-W")
        self.root.wait_done(2, timeout=40)    # the kill line + this run
        self.assertEqual(texts(FAKE.snapshot()), ["P-W", "R-W"], self.root.logtext())
        self.assertIn("killed at its hard wall", self.root.logtext())


    # T35 (G2) - a send with an unknown outcome is never clobbered by a Notification: the
    #            Notification is spooled, nothing is sent until the pending send is resolved
    def test_t35_notification_never_clobbers_pending(self):
        self.root.env["MM_MIRROR_POST_TIMEOUT"] = "2"
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        tr.write(tr.prompt("P1"), tr.text("R1", mid="m1"))
        FAKE.mode = "hole-accept"                 # P1 created, its answer never arrives
        self.root.stop(tr, last="R1")
        self.root.wait_done(2)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_mode = "500"                     # the lookup is struggling
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash",
                        "notification_type": "permission_prompt"})
        self.root.wait_done(3)
        self.assertEqual(texts(FAKE.snapshot()), ["P0", "R0", "P1"])
        FAKE.get_mode = "ok"
        tr.write(tr.prompt("P2"), tr.text("R2", mid="m2"))
        self.root.stop(tr, last="R2")
        self.root.wait_done(4)
        got = texts(FAKE.snapshot())
        self.assertEqual(got[:4], ["P0", "R0", "P1", "R1"])
        self.assertIn("needs your permission", got[4])
        self.assertEqual(got[5:], ["P2", "R2"])
        self.assertOneThread(FAKE.snapshot())

    # T36 (G3) - a spooled mention whose post was created but unanswered is resolved by marker,
    #            not posted twice
    def test_t36_spooled_mention_unknown_outcome(self):
        """The attempt-3 G3 path: a mention that had to wait (root unknown, lookup down) is posted
        later, and THAT post is created but unanswered. It is resolved by its marker, not re-sent."""
        self.root.env["MM_MIRROR_POST_TIMEOUT"] = "2"
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PZ"))
        FAKE.mode = "hole-accept"                    # the root: created, unanswered
        self.root.stop(tr)
        self.root.wait_done(1)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_mode = "500"                        # lookup down: the mention has to wait
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash",
                        "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PZ"])
        FAKE.get_mode = "ok"
        FAKE.get_free = True                         # the root's lookup answers...
        FAKE.mode = "hole-accept"                    # ...and the mention's own post hangs: created, unanswered
        self.root.stop(tr)
        self.root.wait_done(3)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        tr.write(tr.text("RZ", mid="m1"))
        self.root.stop(tr, last="RZ")
        self.root.wait_done(4)
        got = texts(FAKE.snapshot())
        self.assertEqual(sum("needs your permission" in g for g in got), 1, got)
        self.assertEqual(got[0], "PZ")
        self.assertIn("needs your permission", got[1])
        self.assertEqual(got[2:], ["RZ"])
        self.assertOneThread(FAKE.snapshot())

    # T37 (G4) - questions come from the transcript, in transcript order; one already answered when
    #            it is posted carries no mention (a stale question must not ping)
    def test_t37_question_order_and_staleness(self):
        qin = {"questions": [{"question": "Pick one?", "header": "Pick", "multiSelect": False,
                              "options": [{"label": "A", "description": "first"}, {"label": "B", "description": "second"}]}]}
        tr = self.root.transcript(sid_new())
        # (a) root unknown and the lookup down while the question is asked; answered before recovery
        self.root.env["MM_MIRROR_POST_TIMEOUT"] = "2"
        tr.write(tr.prompt("PQ"))
        FAKE.mode = "hole-accept"
        self.root.stop(tr)
        self.root.wait_done(1)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_mode = "500"
        tr.write(tr.text("R-BEFORE-QUESTION", stop="tool_use", mid="m1"))
        tu, tid = tr.tool_use("AskUserQuestion", qin, mid="m2")
        tr.write(tu)
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "PreToolUse",
                        "tool_name": "AskUserQuestion", "tool_input": qin, "tool_use_id": tid})
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PQ"])
        tr.write(tr.tool_result(tid, "answered A"), tr.text("R-AFTER-ANSWER", mid="m3"))
        FAKE.get_mode = "ok"
        self.root.stop(tr, last="R-AFTER-ANSWER")
        self.root.wait_done(3)
        posts = bodies(FAKE.snapshot())
        self.assertEqual(len(posts), 4, posts)
        self.assertIn("R-BEFORE-QUESTION", posts[1])
        self.assertIn("Pick one?", posts[2])
        self.assertIn("answered in the session", posts[2])
        self.assertNotIn(MENTION, posts[2])                          # stale: no ping
        self.assertIn("R-AFTER-ANSWER", posts[3])
        # (b) a live question: posted with the mention, after the reply that precedes it
        tr.write(tr.prompt("PQ2"), tr.text("R-BEFORE-Q2", stop="tool_use", mid="m4"))
        tu, tid = tr.tool_use("AskUserQuestion", qin, mid="m5")
        tr.write(tu)
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "PreToolUse",
                        "tool_name": "AskUserQuestion", "tool_input": qin, "tool_use_id": tid})
        self.root.wait_done(4)
        posts = bodies(FAKE.snapshot())
        self.assertEqual(len(posts), 7, posts)
        self.assertIn("R-BEFORE-Q2", posts[5])
        self.assertTrue(posts[6].startswith(MENTION + " "))
        self.assertIn("Pick one?", posts[6])
        self.assertOneThread(FAKE.snapshot())

    # T38 (Q4) - a mention that was neither created nor answered is re-sent from the spool, once
    def test_t38_uncreated_mention_resent(self):
        self.root.env["MM_MIRROR_POST_TIMEOUT"] = "2"
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("PN"), tr.text("RN", mid="m0"))
        self.root.stop(tr, last="RN")
        self.root.wait_done(1)
        FAKE.mode = "hole"                         # nothing created, no answer
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash",
                        "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        self.root.stop(tr, last="RN")
        self.root.wait_done(3)
        got = texts(FAKE.snapshot())
        self.assertEqual(got[:2], ["PN", "RN"])
        self.assertEqual(sum("needs your permission" in g for g in got), 1, got)
        self.assertIn("was not created", self.root.logtext())

    # T39 (Q6) - expiry counts only DEFINITIVE lookup errors: a merely slow lookup never expires a
    #            pending send; a 500 does (TRIES=1, SECS=0 here)
    def test_t39_expiry_ignores_slow_lookups(self):
        self.root.env.update({"MM_MIRROR_POST_TIMEOUT": "2", "MM_MIRROR_PENDING_TRIES": "1",
                              "MM_MIRROR_PENDING_SECS": "0"})
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        tr.write(tr.prompt("P1"), tr.text("R1", mid="m1"))
        FAKE.mode = "hole-accept"
        self.root.stop(tr, last="R1")
        self.root.wait_done(2)
        FAKE.release.set()
        time.sleep(0.3)
        FAKE.mode = "ok"
        FAKE.release.clear()
        FAKE.get_delay = 3                         # slower than POST_TIMEOUT: no answer
        for n in (3, 4):
            self.root.stop(tr, last="R1")
            self.root.wait_done(n)
        self.assertNotIn("EXPIRED", self.root.logtext())
        self.assertIn("(noanswer)", self.root.logtext())
        self.assertEqual(texts(FAKE.snapshot()), ["P0", "R0", "P1"])
        FAKE.get_delay = 0
        FAKE.get_mode = "500"                      # a definitive error answer
        self.root.stop(tr, last="R1")
        self.root.wait_done(5)
        self.assertIn("EXPIRED", self.root.logtext())
        self.assertEqual(texts(FAKE.snapshot()), ["P0", "R0", "P1", "R1"])

    # T40 (Q8) - the watchdog acts only on the process it was started for
    def test_t40_watchdog_identity(self):
        M = load_helper(SRC)
        helper = os.path.join(self.root.dir, "scripts", "notify_mattermost_mirror.py")
        env = dict(self.root.env, MM_ROOT_DIR=self.root.dir, MM_KEY="wdtest")
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            sid = M.process_start_id(victim.pid)
            self.assertTrue(sid)
            past = str(time.time() - 100)
            # (a) the right pid with the WRONG identity (= a reused pid): left alone
            subprocess.run([sys.executable, helper, "watchdog", str(victim.pid), past, sid + "1"],
                           env=env, timeout=30)
            time.sleep(0.5)
            self.assertIsNone(victim.poll(), "the watchdog killed a process that was not its worker")
            # (b) the right identity, deadline passed: killed
            subprocess.run([sys.executable, helper, "watchdog", str(victim.pid), past, sid], env=env, timeout=30)
            victim.wait(10)
            self.assertIsNotNone(victim.poll())
        finally:
            if victim.poll() is None:
                victim.kill()

    # T41 (Q5) - SessionEnd drains: a last reply that missed its Stop, or a last Stop during an
    #            outage, is posted at session end
    def test_t41_session_end_drains(self):
        tr = self.root.transcript(sid_new())                   # settle 1 s in the suite
        tr.write(tr.prompt("PL"))
        self.root.stop(tr, last="RL final")
        self.root.wait_done(1)
        tr.write(tr.text("RL final", mid="m0"))                # lands after the wait; no later turn
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "SessionEnd",
                        "reason": "other"})
        self.root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PL", "RL final"])
        FAKE.reset()
        root = Root()
        tr = root.transcript(sid_new())
        tr.write(tr.prompt("PO"), tr.text("RO", mid="m0"))
        FAKE.mode = "500"
        root.stop(tr, last="RO")
        root.wait_done(1)
        FAKE.mode = "ok"
        root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "SessionEnd",
                   "reason": "prompt_input_exit"})
        root.wait_done(2)
        self.assertEqual(texts(FAKE.snapshot()), ["PO", "RO"])


    # T42 (D1) - `seed --force` keeps the spool position: nothing old is posted again, pings included
    def test_t42_seed_force_keeps_spool_position(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        for i in range(3):
            self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                            "message": f"Claude needs your permission to use Bash {i}",
                            "notification_type": "permission_prompt"})
            self.root.wait_done(2 + i)
        self.assertEqual(sum("needs your permission" in t for t in texts(FAKE.snapshot())), 3)
        r = subprocess.run([sys.executable, os.path.join(self.root.dir, "scripts", "notify_mattermost_mirror.py"),
                            "seed", "--force", tr.path], capture_output=True, text=True, timeout=60,
                           env=dict(self.root.env, MM_ROOT_DIR=self.root.dir))
        self.assertIn("seeded 1", r.stdout)
        tr.write(tr.prompt("P1"), tr.text("R1", mid="m1"))
        self.root.stop(tr, last="R1")
        self.root.wait_done(5)
        got = texts(FAKE.snapshot())
        self.assertEqual(sum("needs your permission" in t for t in got), 3, got)
        self.assertEqual(got[-2:], ["P1", "R1"])

    # T43 - a spool line left unterminated (writer killed mid-line) does not swallow the next one
    def test_t43_partial_spool_line(self):
        """A writer killed mid-write leaves only a temporary file (never read); a corrupt item is
        skipped and logged; neither swallows the next Notification."""
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        spool = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.outbox")
        os.makedirs(spool, exist_ok=True)
        with open(os.path.join(spool, ".00000000000000000001-00000001-dead.tmp"), "w") as fh:
            fh.write('{"at": 0, "mention": "", "text": "HALF-WRITTEN')          # a killed writer's temp file
        with open(os.path.join(spool, "00000000000000000002-00000001-bad0.json"), "w") as fh:
            fh.write('{"at": 0, "mention": "", "text": "CORRUPT')               # a damaged item
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "AFTER-PARTIAL permission", "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        got = texts(FAKE.snapshot())
        self.assertTrue(any("AFTER-PARTIAL" in t for t in got), got)
        self.assertFalse(any("HALF-WRITTEN" in t or "CORRUPT" in t for t in got))
        self.assertIn("unreadable or empty; skipped", self.root.logtext())

    # T44 - without the helper, only Stop (and manual runs) take the legacy path: SessionEnd,
    #       Notification and PreToolUse hook runs post nothing
    def test_t44_no_helper_no_legacy_ping_for_other_events(self):
        root = Root(with_helper=False)
        tr = root.transcript(sid_new())
        tr.write(tr.prompt("P0"))
        base = {"session_id": tr.sid, "transcript_path": tr.path}
        for ev in ({"hook_event_name": "SessionEnd", "reason": "other"},
                   {"hook_event_name": "Notification", "message": "x", "notification_type": "permission_prompt"},
                   {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "t"}):
            r, _ = root.hook(dict(base, **ev))
            self.assertEqual(r.returncode, 0)
        self.assertEqual(FAKE.snapshot(), [])
        root.hook(dict(base, hook_event_name="Stop"))
        got = bodies(FAKE.snapshot())
        self.assertEqual(len(got), 1)
        self.assertIn("finished a turn", got[0])

    # T45 - a permission ping posted after the session has moved on (a later operator prompt in the
    #       transcript) carries no @-mention, like a stale question
    def test_t45_stale_permission_ping_no_mention(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        FAKE.mode = "500"
        self.root.hook({"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                        "message": "Claude needs your permission to use Bash", "notification_type": "permission_prompt"})
        self.root.wait_done(2)
        FAKE.mode = "ok"
        tr.write(tr.text("R0b", mid="m1"), tr.prompt("P1"), tr.text("R1", mid="m2"))
        self.root.stop(tr, last="R1")
        self.root.wait_done(3)
        got = bodies(FAKE.snapshot())
        ping = [b for b in got if "needs your permission" in b]
        self.assertEqual(len(ping), 1, got)
        self.assertFalse(ping[0].startswith(MENTION), ping[0])
        self.assertEqual(texts(FAKE.snapshot())[-2:], ["P1", "R1"])

    # T46 (H1) - detached children run on the BASE interpreter, not a venv launcher
    def test_t46_base_interpreter_for_detached_children(self):
        M = load_helper(SRC)
        exe = M.python_exe()
        self.assertTrue(os.path.isfile(exe))
        base = getattr(sys, "_base_executable", "") or sys.executable
        self.assertEqual(os.path.normcase(exe), os.path.normcase(base))
        if sys.prefix != sys.base_prefix:
            self.assertNotEqual(os.path.normcase(exe), os.path.normcase(sys.executable),
                                "under a venv the detached worker must not go through the venv launcher")
        if os.name != "nt":
            return
        # and the LIVE processes started through the real hook (the attempt-5 tester's W3c)
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P-X"))
        self.root.env["MM_MIRROR_SETTLE_SECS"] = "12"
        self.root.stop(tr, last="never-in-transcript")
        ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)`t$($_.CommandLine)\" }")
        seen = {}
        end = time.time() + 15
        while time.time() < end and len(seen) < 2:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
            for ln in out.splitlines():
                f = ln.split("\t")
                if len(f) == 3 and os.path.basename(self.root.dir) in f[2]:
                    for mode in ("worker", "watchdog"):
                        if f" {mode}" in f[2]:
                            seen[mode] = f[1]
            time.sleep(0.5)
        self.root.wait_done(1, timeout=60)
        self.assertEqual(sorted(seen), ["watchdog", "worker"], seen)
        self.assertEqual({os.path.normcase(v) for v in seen.values()}, {os.path.normcase(base)}, seen)


    # T47 (F1 guard) - EVERY regex in the helper and in the sanitizer it loads, enumerated
    #                  programmatically, against long runs of each character class: each search/sub
    #                  and fullmatch finishes quickly. A quadratic pattern cannot be added unnoticed.
    def test_t47_every_pattern_is_linear(self):
        import re as _re
        M = load_helper(SRC)
        self.assertIsNotNone(M.load_redactor())
        san = sys.modules.get("_mm_mirror_sanitize")
        pats = {f"helper.{n}": v for n, v in vars(M).items() if isinstance(v, _re.Pattern)}
        if san is not None:
            pats.update({f"sanitize.{n}": v for n, v in vars(san).items() if isinstance(v, _re.Pattern)})
            pats.update({f"sanitize.{k}": v for k, v in getattr(san, "_SECRET_PATTERNS", [])})
        self.assertGreater(len(pats), 25, sorted(pats))
        N = 100000
        units = [" ", "\t", "\n", "|", "-", ".", "=", ":", '"', "'", "`", "@", "/", "\\", "a", "A", "0", "+",
                 "a.", "a-", "a:", "a=", "| ", ": ", "= ", "\\n", "aB3+", "-----BEGIN ", "password: ", "token=",
                 "secret aws ", "u:p@", "\u00a0"]
        inputs = {f"{u!r}*": (u * (N // len(u) + 1))[:N] for u in units}
        inputs["table+spaces"] = "| a |\n" + " " * N + "x"
        slow = {}
        for pn, pat in pats.items():
            for inn, text in inputs.items():
                t0 = time.time()
                pat.sub("", text)
                pat.fullmatch(text)
                dt = time.time() - t0
                if dt > 1.5:
                    slow[f"{pn} on {inn}"] = round(dt, 2)
        self.assertEqual(slow, {})
        red = M.load_redactor()
        slow = {}
        for inn, text in inputs.items():
            t0 = time.time()
            M.defang(red(text))
            dt = time.time() - t0
            if dt > 3:
                slow[inn] = round(dt, 2)
        self.assertEqual(slow, {})

    # T48 (F1 never wedge) - a message whose redaction killed the previous run (its wall) is replaced
    #                        by a placeholder and the session moves on
    def test_t48_redaction_wedge_placeholder(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"))
        self.root.stop(tr)
        self.root.wait_done(1)
        r1 = tr.text("R-WEDGED", mid="m0")
        tr.write(r1, tr.prompt("P1"), tr.text("R1", mid="m1"))
        # what a run killed inside that message's redaction leaves behind
        sp = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.json")
        with open(sp, encoding="utf-8") as fh:
            st = json.load(fh)
        st["redacting"] = {"uid": r1["uuid"]}
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        self.root.stop(tr, last="R1")
        self.root.wait_done(2)
        got = texts(FAKE.snapshot())
        self.assertEqual(got[0], "P0")
        self.assertIn("message omitted: redaction timed out", got[1])
        self.assertEqual(got[2:], ["P1", "R1"])
        self.assertNotIn("R-WEDGED", "\n".join(got))
        self.assertIn("posting a placeholder", self.root.logtext())

    # T49 (F2) - simultaneous permission Notifications through the real hook: every one posted once
    def test_t49_simultaneous_notifications(self):
        tr = self.root.transcript(sid_new())
        tr.write(tr.prompt("P0"), tr.text("R0", mid="m0"))
        self.root.stop(tr, last="R0")
        self.root.wait_done(1)
        total, done = 0, 1
        for rnd, k in enumerate((3, 8, 8)):
            msgs = [f"PERM-{rnd}-{i} needs your permission" for i in range(k)]
            ths = [threading.Thread(target=self.root.hook, args=(
                {"session_id": tr.sid, "transcript_path": tr.path, "hook_event_name": "Notification",
                 "message": m, "notification_type": "permission_prompt"},)) for m in msgs]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            total += k
            done += k
            self.root.wait_done(done)
            tr.write(tr.prompt(f"PN{rnd}"), tr.text(f"RN{rnd}", mid=f"n{rnd}"))
            self.root.stop(tr, last=f"RN{rnd}")
            done += 1
            self.root.wait_done(done)
        got = texts(FAKE.snapshot())
        perms = [t for t in got if t.startswith("\U0001f514") or "PERM-" in t]
        ids = sorted(t.split("PERM-")[1].split(" ")[0] for t in perms)
        want = sorted(f"{rnd}-{i}" for rnd, k in enumerate((3, 8, 8)) for i in range(k))
        self.assertEqual(ids, want)                    # each exactly once: none lost, none merged
        self.assertOneThread(FAKE.snapshot())
        spool = os.path.join(self.root.dir, "scripts", ".mm-mirror", f"{tr.sid[:8]}.outbox")
        self.assertEqual(len([n for n in os.listdir(spool) if n.endswith(".json")]), total)



if __name__ == "__main__":
    unittest.main()
