#!/usr/bin/env python3
"""cf-bridge (closeout-followups G15 + G7): #claude-sessions thread replies reach the session
that owns the thread, and headless sessions are told background work dies at turn end.

    python scripts/claude-sessions-bridge/test_interactive_threads.py

Stdlib only. NOTHING LIVE IS TOUCHED:
- Mattermost is a stub HTTP server on 127.0.0.1 (this file); the bridge is pointed at it with
  BRIDGE_MM_URL and a fake MM_TOKEN, and every notifier run uses a COPY of
  scripts/notify-mattermost.sh whose API line is rewritten to the stub (the copy is refused if
  the rewrite did not happen).
- `claude` is a fake: bridge.run_turn runs for real, but Popen of the fake binary path is
  redirected to a python script that records its argv/env, fires the notifier copy as its
  Stop hook (as the real CLI does through .claude/settings.local.json), and prints a
  stream-json result.
- State lives in a temp dir: BRIDGE_STATE_DIR, the notifier copy's own map, its own .env.
- telegram_alert is replaced before anything can call it.

The end-to-end scenario runs in a CHILD process (the bridge reads its config at import time,
and another test module in the same process may already have imported it with defaults).
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFIER = os.path.normpath(os.path.join(HERE, "..", "notify-mattermost.sh"))
ME = "botclaude0000000000000000me"
OP = "alice00000000000000000000op"
BOT_TOKEN = "fake-bot-token-cf-bridge"
LIVE_API_LINE = 'API="http://localhost:8065/api/v4/posts"'

S1 = "1a1a1a1a-1111-4111-8111-111111111111"   # interactive, Stop hook -> full id known
S3 = "3c3c3c3c-3333-4333-8333-333333333333"   # interactive, Notification first, then Stop
S4 = "4d4d4d4d-4444-4444-8444-444444444444"   # interactive, Notification only


# ── stub Mattermost ─────────────────────────────────────────────────────────
class StubMM:
    def __init__(self) -> None:
        self.posts: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._t = int(time.time() * 1000) + 1000
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _who(self):
                auth = self.headers.get("Authorization", "")
                return ME if auth == f"Bearer {BOT_TOKEN}" else None

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_GET(self):
                if not self._who():
                    return self._send(401, {"id": "api.context.session_expired.app_error"})
                path = self.path.split("?")[0]
                if path == "/api/v4/users/me":
                    return self._send(200, {"id": ME, "username": "bot-claude"})
                m = re.match(r"^/api/v4/users/([^/]+)$", path)
                if m:
                    names = {ME: "bot-claude", OP: "alice"}
                    return self._send(200, {"id": m.group(1),
                                            "username": names.get(m.group(1), m.group(1)[:8])})
                m = re.match(r"^/api/v4/channels/([^/]+)/posts$", path)
                if m:
                    since = int((re.search(r"since=(\d+)", self.path) or [0, 0])[1])
                    with stub.lock:
                        ps = {k: v for k, v in stub.posts.items() if v["create_at"] > since}
                    return self._send(200, {"posts": ps, "order": list(ps)})
                if re.match(r"^/api/v4/posts/[^/]+/reactions$", path):
                    return self._send(200, [])
                return self._send(404, {"id": "stub.not_found"})

            def do_POST(self):
                who = self._who()
                if not who:
                    return self._send(401, {"id": "api.context.session_expired.app_error"})
                path = self.path.split("?")[0]
                body = self._body()
                if path == "/api/v4/posts":
                    root = body.get("root_id") or ""
                    with stub.lock:
                        if root and root not in stub.posts:
                            return self._send(400, {"id": "api.post.create_post.root_id.app_error",
                                                    "status_code": 400})
                    p = stub.add(who, body.get("message", ""), root, body.get("props") or {})
                    return self._send(201, p)
                return self._send(201, {})

            def do_PUT(self):
                m = re.match(r"^/api/v4/posts/([^/]+)/patch$", self.path)
                body = self._body()
                if m:
                    with stub.lock:
                        if m.group(1) in stub.posts:
                            stub.posts[m.group(1)]["message"] = body.get("message", "")
                return self._send(200, {})

            def do_DELETE(self):
                return self._send(200, {})

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def add(self, user: str, message: str, root: str = "", props: dict | None = None) -> dict:
        with self.lock:
            self._t += 5
            pid = uuid.uuid4().hex[:26]
            p = {"id": pid, "root_id": root, "channel_id": "6z9khgkdd7df9q454be6fimw1h",
                 "user_id": user, "message": message, "create_at": self._t,
                 "props": props or {}, "type": ""}
            self.posts[pid] = p
            return dict(p)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return sorted((dict(p) for p in self.posts.values()), key=lambda p: p["create_at"])


# ── fake claude (runs as its own process, like the real CLI) ─────────────────
FAKE_CLAUDE = r'''
import json, os, subprocess, sys, uuid
argv = sys.argv[1:]
prompt = sys.stdin.read()
if "SLOWJOB" in prompt:          # keeps the turn in flight long enough to reply during it
    import time; time.sleep(5)
sid = None
if "--resume" in argv and "--fork-session" not in argv:
    sid = argv[argv.index("--resume") + 1]
sid = sid or str(uuid.uuid4())
with open(os.environ["CF_FAKE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv, "prompt": prompt, "session_id": sid,
                         "bridge_thread_env": os.environ.get("CLAUDE_BRIDGE_THREAD", "")}) + "\n")
# The Stop hook, as .claude/settings.local.json wires it: `bash notify-mattermost.sh` with the
# hook JSON on stdin, inheriting THIS process's environment.
hook = os.environ.get("CF_HOOK_SCRIPT")
if hook:
    subprocess.run([os.environ["CF_BASH"], hook],
                   input=json.dumps({"session_id": sid, "hook_event_name": "Stop"}).encode(),
                   capture_output=True, timeout=60)
print(json.dumps({"type": "system", "subtype": "init", "session_id": sid}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "fake reply", "session_id": sid, "total_cost_usd": 0}), flush=True)
'''


def find_git_bash() -> str:
    """Git Bash, never WSL's System32 bash (that one cannot see this Windows python/curl)."""
    if os.environ.get("CF_BASH"):
        return os.environ["CF_BASH"]
    if os.name != "nt":
        return shutil.which("bash") or "/bin/bash"
    cands = []
    git = shutil.which("git")
    if git:
        g = os.path.dirname(os.path.dirname(git))
        cands += [os.path.join(g, "bin", "bash.exe"), os.path.join(g, "usr", "bin", "bash.exe")]
    cands += [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    for c in cands:
        if os.path.isfile(c):
            return c
    raise unittest.SkipTest("Git Bash not found (set CF_BASH)")


def make_notifier_copy(root: str) -> str:
    """<root>/scripts/notify-mattermost.sh pointed at the stub. ROOT_DIR is derived from the
    script's own location, so its .env and its map are <root>/.env and <root>/scripts/."""
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    with open(NOTIFIER, "r", encoding="utf-8", newline="") as fh:
        src = fh.read()
    if src.count(LIVE_API_LINE) != 1:
        raise RuntimeError("notifier API line not found - refusing to run a copy that might "
                           "post to the live Mattermost")
    dst = os.path.join(root, "scripts", "notify-mattermost.sh")
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        fh.write(src.replace(LIVE_API_LINE, 'API="${CF_STUB_URL}/api/v4/posts"'))
    with open(os.path.join(root, ".env"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"CLAUDE_MM_BOT_TOKEN={BOT_TOKEN}\nMM_OPERATOR_MENTION=\n")
    return dst


def hook_env(stub_url: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("MM_SESSION_ID", "CLAUDE_BRIDGE_THREAD", "MM_DEADLINE_SECS", "MM_WALL_SECS")}
    # GENEROUS, FIXED budgets. The notifier's defaults (10 s / 11 s wall) are sized for a 15 s
    # hook, and on a loaded machine a python fork inside it can eat enough of that to post a
    # ping flat - the attempt-1 flake in test_06. The tests are about threading decisions,
    # not about the budget, so the budget is taken out of play.
    env.update({"CF_STUB_URL": stub_url, "MM_OPERATOR_MENTION": "",
                "MM_DEADLINE_SECS": "30", "MM_WALL_SECS": "60"})
    return env


def run_stop_hook(bash: str, script: str, sid: str, env: dict) -> None:
    subprocess.run([bash, script], input=json.dumps({"session_id": sid,
                                                     "hook_event_name": "Stop"}).encode(),
                   env=env, capture_output=True, timeout=60)


def run_notification_hook(bash: str, script: str, sid: str, env: dict) -> None:
    # Exactly the live Notification hook's shape: the text names `session <8 hex>`, stdin closed.
    subprocess.run([bash, script, f"🔔 Claude Code (ai-stack) session {sid[:8]} - needs your permission"],
                   stdin=subprocess.DEVNULL, env=env, capture_output=True, timeout=60)


def read_map(path: str) -> list[list[str]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return [ln.split() for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []


def roots_for(map_lines: list[list[str]], sid: str) -> list[str]:
    return [ln[1] for ln in map_lines if ln[0] == sid[:8] and len(ln) > 1 and ln[1] != "-"]


# ── the scenario (child process) ─────────────────────────────────────────────
def scenario(out_path: str) -> None:
    tmp = tempfile.mkdtemp(prefix="cf-bridge-")
    stub = StubMM()
    bash = find_git_bash()
    notifier = make_notifier_copy(os.path.join(tmp, "repo"))
    tmap = os.path.join(tmp, "repo", "scripts", ".mm-session-threads")
    fake_bin = os.path.join(tmp, "claude-fake.exe")
    open(fake_bin, "w").close()                      # exists, never executed
    fake_py = os.path.join(tmp, "fake_claude.py")
    with open(fake_py, "w", encoding="utf-8") as fh:
        fh.write(FAKE_CLAUDE)
    fake_log = os.path.join(tmp, "fake-claude.jsonl")
    henv = hook_env(stub.url)

    os.environ.update({
        "BRIDGE_MM_URL": stub.url, "MM_URL": stub.url, "MM_TOKEN": BOT_TOKEN,
        "BRIDGE_STATE_DIR": os.path.join(tmp, "state"), "BRIDGE_OPERATORS": "alice",
        "BRIDGE_CLAUDE_BIN": fake_bin, "BRIDGE_NOTIFY_THREADS": tmap,
        "BRIDGE_WORKTREE_DEFAULT": "off", "BRIDGE_ALLOW_SELF": "0",
        "CF_FAKE_LOG": fake_log, "CF_HOOK_SCRIPT": notifier, "CF_BASH": bash,
        "CF_STUB_URL": stub.url, "MM_OPERATOR_MENTION": "",
        "MM_DEADLINE_SECS": "30", "MM_WALL_SECS": "60",   # see hook_env
    })
    for k in ("MM_SESSION_ID", "CLAUDE_BRIDGE_THREAD", "BRIDGE_CHARTER_FILE", "BRIDGE_APPEND_PROMPT"):
        os.environ.pop(k, None)
    sys.path.insert(0, HERE)
    import bridge  # noqa: E402  (after the environment above, on purpose)

    assert bridge.mmapi.DEFAULT_URL == stub.url, "bridge would talk to a real Mattermost"
    assert bridge.STATE_DIR == os.path.join(tmp, "state"), "bridge would write live state"
    bridge.telegram_alert = lambda text: False

    real_popen = subprocess.Popen

    class FakePopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args, *a, **kw):
            if isinstance(args, list) and args and args[0] == fake_bin:
                args = [sys.executable, fake_py] + list(args[1:])
            super().__init__(args, *a, **kw)

    bridge.subprocess.Popen = FakePopen

    b = bridge.Bridge()
    me = bridge.mmapi._me()
    res: dict = {"stub": stub.url}

    def calls() -> list[dict]:
        try:
            with open(fake_log, "r", encoding="utf-8") as fh:
                return [json.loads(x) for x in fh if x.strip()]
        except OSError:
            return []

    def idle() -> bool:
        with b.running_lock:
            if b.running:
                return False
        return all(q.empty() for q in b.queues.values())

    def settle(expect_calls: int | None = None, timeout: float = 90) -> None:
        """Idle for FIVE consecutive checks 0.2 s apart (a worker holds an item for a moment
        between taking it and marking the thread running), and the expected number of fake
        claude calls has been logged."""
        end = time.time() + timeout
        quiet = 0
        while time.time() < end:
            ok = idle() and (expect_calls is None or len(calls()) >= expect_calls)
            quiet = quiet + 1 if ok else 0
            if quiet >= 5:
                return
            time.sleep(0.2)
        # Not an exception: on the BASE code an expected call never comes, and one step timing
        # out must not throw away every other step's result. Recorded instead; test_40 fails on it.
        res.setdefault("settle_timeouts", []).append(
            f"{timeout}s: calls={len(calls())}, expected {expect_calls}")

    def wait_calls(n: int, timeout: float = 60) -> None:
        end = time.time() + timeout
        while time.time() < end and len(calls()) < n:
            time.sleep(0.1)

    def bridge_posts_in(root: str, after: int) -> list[str]:
        return [p["message"] for p in stub.snapshot()
                if p["user_id"] == ME and p["root_id"] == root and p["create_at"] > after]

    # 1. interactive session S1 finishes a turn: the notifier opens its thread R1
    run_stop_hook(bash, notifier, S1, henv)
    r1 = (roots_for(read_map(tmap), S1) or [""])[0]
    res["r1"] = r1
    res["map_after_s1"] = read_map(tmap)
    # 1b. S1's NEXT turn end: must post under R1 and leave the map alone
    run_stop_hook(bash, notifier, S1, henv)
    res["map_after_s1_again"] = read_map(tmap)
    res["s1_second_ping_roots"] = [p["root_id"] for p in stub.snapshot() if p["user_id"] == ME]

    # 2. the operator replies in R1
    mark = stub.add(OP, "are you done with the gateway change?", r1)["create_at"]
    b.poll_once(me)
    settle(expect_calls=None, timeout=30)
    time.sleep(1.0)
    settle(timeout=60)
    res["calls_after_reply"] = calls()
    res["bridge_posts_after_reply"] = bridge_posts_in(r1, mark - 1)

    # 3. the operator takes the offer: fork S1 in the same thread (a SLOW turn), and while that
    #    attach turn is still running replies again and answers `approve` (attempt 1, A4)
    n_before = len(calls())
    mark = stub.add(OP, f"model: sonnet fork {S1} SLOWJOB carry on from here", r1)["create_at"]
    b.poll_once(me)
    wait_calls(n_before + 1)
    time.sleep(0.5)
    with b.running_lock:
        res["fork_running_when_followup_sent"] = r1 in b.running
    stub.add(OP, "and also check the logs", r1)
    stub.add(OP, "approve", r1)
    b.poll_once(me)
    settle(expect_calls=n_before + 2, timeout=45)
    res["calls_after_fork"] = calls()[n_before:]
    res["bridge_posts_after_fork"] = bridge_posts_in(r1, mark - 1)
    res["fork_session"] = b.state["threads"].get(r1, {}).get("session_id", "")

    # 3b. a reply in the now-attached thread resumes the FORK (attempt 1, X1)
    n_before = len(calls())
    mark = stub.add(OP, "one more thing", r1)["create_at"]
    b.poll_once(me)
    settle(expect_calls=n_before + 1, timeout=60)
    res["calls_after_attached_reply"] = calls()[n_before:]
    res["bridge_posts_after_attached_reply"] = bridge_posts_in(r1, mark - 1)

    # 4. S3: a permission ping (8-char id only) opens the thread, then its Stop hook runs
    run_notification_hook(bash, notifier, S3, henv)
    run_stop_hook(bash, notifier, S3, henv)
    r3 = (roots_for(read_map(tmap), S3) or [""])[0]
    res["r3"] = r3
    res["map_after_s3"] = read_map(tmap)
    # 4b. S3's next turn end: under R3, and the full id is not appended a second time
    run_stop_hook(bash, notifier, S3, henv)
    res["map_after_s3_again"] = read_map(tmap)
    res["s3_pings"] = [p["root_id"] for p in stub.snapshot()
                       if p["user_id"] == ME and (p["id"] == r3 or p["root_id"] == r3)]
    n_before = len(calls())
    mark = stub.add(OP, "status?", r3)["create_at"]
    b.poll_once(me)
    time.sleep(1.0)
    settle(timeout=60)
    res["calls_after_s3_reply"] = calls()[n_before:]
    res["bridge_posts_after_s3_reply"] = bridge_posts_in(r3, mark - 1)

    # 5. S4: only a permission ping ever named it
    run_notification_hook(bash, notifier, S4, henv)
    r4 = (roots_for(read_map(tmap), S4) or [""])[0]
    res["r4"] = r4
    n_before = len(calls())
    mark = stub.add(OP, "what do you need?", r4)["create_at"]
    b.poll_once(me)
    time.sleep(1.0)
    settle(timeout=60)
    res["calls_after_s4_reply"] = calls()[n_before:]
    res["bridge_posts_after_s4_reply"] = bridge_posts_in(r4, mark - 1)

    # 6. a headless session: the operator starts one with a ROOT post
    n_before = len(calls())
    map_before = len(read_map(tmap))
    r2 = stub.add(OP, "headless job: summarise the queue")
    b.poll_once(me)
    settle(expect_calls=n_before + 1, timeout=45)
    new_calls = calls()[n_before:]
    res["headless_calls"] = new_calls
    res["headless_root"] = r2["id"]
    res["map_new_lines_headless"] = read_map(tmap)[map_before:]
    res["bot_roots_after_headless"] = [p["id"] for p in stub.snapshot()
                                       if p["user_id"] == ME and not p["root_id"]
                                       and p["create_at"] > r2["create_at"]]
    res["threads_state"] = {k: v.get("session_id") for k, v in b.state["threads"].items()}

    # 7. the guard is for REPLIES only: an operator ROOT post whose id somehow appears in the
    #    map (contrived: appended by hand here) still starts a session (attempt 1, X4)
    n_before = len(calls())
    r7 = stub.add(OP, "a new task in a new thread")
    with open(tmap, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"7a7a7a7a {r7['id']} 7a7a7a7a-7777-4777-8777-777777777777\n")
    b.poll_once(me)
    settle(expect_calls=n_before + 1, timeout=60)
    res["calls_after_mapped_root"] = calls()[n_before:]
    res["bridge_posts_after_mapped_root"] = bridge_posts_in(r7["id"], r7["create_at"] - 1)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    stub.srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)


_RESULT: dict | None = None
_FAILED: str = ""   # a failed scenario is cached too, so N tests do not rerun it N times


def scenario_result() -> dict:
    """Run the child scenario once per test process and cache its JSON."""
    global _RESULT, _FAILED
    if _FAILED:
        raise AssertionError(_FAILED)
    if _RESULT is None:
        find_git_bash()  # skip cleanly when there is no Git Bash
        fd, out = tempfile.mkstemp(suffix=".json", prefix="cf-bridge-result-")
        os.close(fd)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_BRIDGE_THREAD"}
        env["DOCKER_HOST"] = "tcp://127.0.0.1:1"   # nothing here may reach a daemon
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "--scenario", out],
                           env=env, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            _FAILED = (f"scenario process failed ({p.returncode}):\n"
                       f"{p.stdout[-3000:]}\n{p.stderr[-3000:]}")
            raise AssertionError(_FAILED)
        with open(out, "r", encoding="utf-8") as fh:
            _RESULT = json.load(fh)
        os.remove(out)
    return _RESULT


# ── tests ────────────────────────────────────────────────────────────────────
class InteractiveThreadReplyTests(unittest.TestCase):
    """G15, end to end: real bridge + real notifier copy + stub Mattermost + fake claude."""

    def test_00_notifier_records_the_full_session_id(self):
        r = scenario_result()
        self.assertTrue(r["r1"], "the notifier opened no thread for S1")
        self.assertIn([S1[:8], r["r1"], S1], r["map_after_s1"],
                      "the map must carry the FULL session id: " + repr(r["map_after_s1"]))

    def test_06_later_pings_stay_in_the_thread_with_a_three_field_map(self):
        r = scenario_result()
        self.assertEqual(r["s1_second_ping_roots"], ["", r["r1"]],
                         "S1's second ping must reply under R1 (the three-field line must still "
                         "be read as <key> <root>): " + repr(r["s1_second_ping_roots"]))
        self.assertEqual(r["map_after_s1_again"], r["map_after_s1"],
                         "a ping under a root whose line already has the full id must not append")
        self.assertEqual(r["s3_pings"], ["", r["r3"], r["r3"]], repr(r["s3_pings"]))
        self.assertEqual(r["map_after_s3_again"], r["map_after_s3"],
                         "S3's full id must be appended once, not on every ping")

    def test_01_reply_in_interactive_thread_starts_no_session(self):
        r = scenario_result()
        self.assertEqual(r["calls_after_reply"], [],
                         "a reply in an interactive session's thread started a NEW headless "
                         "session (fake claude was run): " + repr(r["calls_after_reply"])[:400])

    def test_02_reply_is_acknowledged_with_fork_handoff_offer(self):
        r = scenario_result()
        posts = r["bridge_posts_after_reply"]
        offer = [m for m in posts if f"fork {S1}" in m and f"handoff {S1}" in m]
        self.assertEqual(len(offer), 1, "expected exactly one in-thread offer naming "
                         f"`fork {S1}` and `handoff {S1}`, got: {posts!r}"[:800])
        self.assertFalse([m for m in posts if "new Claude session" in m],
                         "the bridge announced a new session in the interactive thread")

    def test_03_fork_reply_attaches_the_thread_to_that_session(self):
        r = scenario_result()
        fc = r["calls_after_fork"]
        self.assertGreaterEqual(len(fc), 1, "the fork reply started no turn")
        argv = fc[0]["argv"]
        self.assertIn("--resume", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], S1, "fork must resume S1")
        self.assertIn("--fork-session", argv, "fork must not write into the desk session")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet",
                         "a leading `model:` directive must still apply to the fork")

    def test_07_reply_during_the_attach_turn_is_queued_not_offered(self):
        """Attempt 1, A4: the fork turn is still running when the operator replies again and
        answers `approve`. The reply must be queued for the attached session; the verdict is
        consumed; neither gets the 'nothing was started' offer."""
        r = scenario_result()
        self.assertTrue(r["fork_running_when_followup_sent"],
                        "setup: the fork turn must still be running when the follow-up is sent")
        fc = r["calls_after_fork"]
        self.assertEqual(len(fc), 2, "expected the fork turn + ONE queued follow-up turn, got "
                         + repr([c["prompt"][:40] for c in fc]))
        self.assertIn("and also check the logs", fc[1]["prompt"])
        argv = fc[1]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], fc[0]["session_id"],
                         "the follow-up must resume the FORK's session")
        self.assertNotIn("--fork-session", argv)
        self.assertFalse([c for c in fc if c["prompt"].strip() == "approve"],
                         "`approve` during a turn is a verdict, never a prompt")
        offers = [m for m in r["bridge_posts_after_fork"] if "nothing was started" in m]
        self.assertEqual(offers, [], "a reply during the attach turn got the offer: " + repr(offers)[:300])

    def test_08_reply_in_an_attached_thread_resumes_it(self):
        """Attempt 1, X1: once attached, the thread is an ordinary bridge thread."""
        r = scenario_result()
        fc = r["calls_after_attached_reply"]
        self.assertEqual(len(fc), 1, repr(fc)[:300])
        argv = fc[0]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], r["fork_session"])
        self.assertNotIn("--fork-session", argv)
        self.assertFalse([m for m in r["bridge_posts_after_attached_reply"] if "nothing was started" in m])

    def test_04_notification_then_stop_recovers_the_full_id(self):
        r = scenario_result()
        self.assertTrue(r["r3"], "no thread opened for S3")
        self.assertIn([S3[:8], r["r3"], S3], r["map_after_s3"],
                      "the Stop run must record S3's full id against the root the "
                      "Notification run opened: " + repr(r["map_after_s3"]))
        self.assertEqual(r["calls_after_s3_reply"], [])
        self.assertTrue([m for m in r["bridge_posts_after_s3_reply"] if f"fork {S3}" in m],
                        repr(r["bridge_posts_after_s3_reply"]))

    def test_05_short_id_only_offer_points_at_sessions_lookup(self):
        r = scenario_result()
        self.assertTrue(r["r4"], "no thread opened for S4")
        self.assertEqual(r["calls_after_s4_reply"], [])
        posts = r["bridge_posts_after_s4_reply"]
        self.assertTrue([m for m in posts if f"sessions {S4[:8]}" in m], repr(posts))


class HeadlessOneThreadTests(unittest.TestCase):
    def test_10_headless_session_opens_exactly_one_thread(self):
        r = scenario_result()
        self.assertEqual(len(r["headless_calls"]), 1, "the root post did not start one turn")
        self.assertEqual(r["bot_roots_after_headless"], [],
                         "the notifier opened a SECOND thread for a headless bridge session: "
                         + repr(r["bot_roots_after_headless"]))
        self.assertEqual(r["map_new_lines_headless"], [],
                         "the notifier recorded a thread for a headless session: "
                         + repr(r["map_new_lines_headless"]))

    def test_12_guard_ignores_root_posts(self):
        """Attempt 1, X4: a ROOT post starts a session even if its id is in the map."""
        r = scenario_result()
        self.assertEqual(len(r["calls_after_mapped_root"]), 1, repr(r["calls_after_mapped_root"])[:300])
        self.assertNotIn("--resume", r["calls_after_mapped_root"][0]["argv"],
                         "a root post starts a NEW session")
        self.assertFalse([m for m in r["bridge_posts_after_mapped_root"] if "nothing was started" in m])

    def test_11_bridge_marks_its_sessions(self):
        r = scenario_result()
        self.assertEqual(r["headless_calls"][0]["bridge_thread_env"], r["headless_root"],
                         "run_turn must export CLAUDE_BRIDGE_THREAD=<thread root>")


class ScenarioHealthTests(unittest.TestCase):
    def test_40_every_scenario_step_settled(self):
        """A step that timed out waiting for the bridge means its assertions read a half-done
        run. On the tip every step settles; this is what makes a green here trustworthy."""
        r = scenario_result()
        self.assertEqual(r.get("settle_timeouts", []), [])


class BackgroundTaskWarningTests(unittest.TestCase):
    """G7: the preamble every headless session receives says background work dies."""

    def test_20_preamble_sent_to_claude_carries_the_warning(self):
        r = scenario_result()
        argv = r["headless_calls"][0]["argv"]
        sp = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("run_in_background", sp)
        self.assertIn("DOES NOT SURVIVE YOUR TURN", sp)
        self.assertRegex(sp, r"run \d+ seconds", "the turn limit must be stated as a number")
        self.assertNotIn(" may be unreliable", sp)


class NotifierManualCallTests(unittest.TestCase):
    """The stand-down is for HOOK runs only - a session id that arrived as a hook payload on
    stdin. A plain `notify-mattermost.sh "msg"` (the watchdog's shape: stdin closed) made from
    inside a bridge turn must still post, FLAT, whatever its text says (attempt 1, A7: any
    `session <8 hex/digits>` in the text used to count as "a hook run" and the message vanished).
    """

    def _run(self, runs: list, marker: bool = True):
        """runs: [(message or None, stdin bytes or None)]. Returns (posts, map lines)."""
        bash = find_git_bash()
        tmp = tempfile.mkdtemp(prefix="cf-bridge-manual-")
        stub = StubMM()
        try:
            script = make_notifier_copy(os.path.join(tmp, "repo"))
            env = hook_env(stub.url)
            if marker:
                env["CLAUDE_BRIDGE_THREAD"] = "somebridgethreadroot000000"
            for msg, stdin in runs:
                argv = [bash, script] + ([msg] if msg is not None else [])
                if stdin is None:
                    subprocess.run(argv, stdin=subprocess.DEVNULL, env=env,
                                   capture_output=True, timeout=60)
                else:
                    subprocess.run(argv, input=stdin, env=env, capture_output=True, timeout=60)
            posts = [(p["message"], p["root_id"]) for p in stub.snapshot()]
            return posts, read_map(os.path.join(tmp, "repo", "scripts", ".mm-session-threads"))
        finally:
            stub.srv.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_30_manual_call_inside_a_bridge_turn_still_posts(self):
        posts, lines = self._run([("watchdog: openwebui restarted", None)])
        self.assertEqual(posts, [("watchdog: openwebui restarted", "")], repr(posts))
        self.assertEqual(lines, [])

    def test_31_manual_and_watchdog_texts_with_hex_or_digit_runs_still_post(self):
        msgs = ["merged: session 3cf42692 finished the gateway change",
                "ALERT backup session 20260928 overdue",
                "Session ABCDEF12 needs a look",
                "🔔 Claude Code (ai-stack) session 1a2b3c4d - needs your permission",
                "disk C: 12345678 bytes free, session 00000000"]
        posts, lines = self._run([(m, None) for m in msgs])
        self.assertEqual(posts, [(m, "") for m in msgs],
                         "every one must post, flat (no thread, no stand-down): " + repr(posts))
        self.assertEqual(lines, [], "a manual call must never open a thread: " + repr(lines))

    def test_32_manual_text_with_a_hex_run_and_an_empty_pipe_still_posts(self):
        # A Bash-tool call: stdin is a pipe with nothing in it, not a tty and not a hook payload.
        posts, _ = self._run([("ALERT backup session 20260928 overdue", b"")])
        self.assertEqual(posts, [("ALERT backup session 20260928 overdue", "")], repr(posts))

    def test_33_stop_hook_payload_under_the_marker_stands_down(self):
        payload = json.dumps({"session_id": S1, "hook_event_name": "Stop"}).encode()
        posts, lines = self._run([(None, payload), ("text naming session 1a1a1a1a", payload)])
        self.assertEqual(posts, [], "a hook run inside a bridge session must post nothing: "
                         + repr(posts))
        self.assertEqual(lines, [])

    def test_35_caller_named_session_inside_a_bridge_turn_still_posts(self):
        # MM_SESSION_ID is the caller naming a session on purpose - not a hook payload.
        bash = find_git_bash()
        tmp = tempfile.mkdtemp(prefix="cf-bridge-manual-")
        stub = StubMM()
        try:
            script = make_notifier_copy(os.path.join(tmp, "repo"))
            env = hook_env(stub.url)
            env.update({"CLAUDE_BRIDGE_THREAD": "somebridgethreadroot000000", "MM_SESSION_ID": S4})
            subprocess.run([bash, script, "named on purpose"], stdin=subprocess.DEVNULL, env=env,
                           capture_output=True, timeout=60)
            self.assertEqual([p["message"] for p in stub.snapshot()], ["named on purpose"])
        finally:
            stub.srv.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_34_outside_a_bridge_turn_the_notification_text_still_threads(self):
        # No marker: the live Notification hook's shape keeps its old behaviour (a thread keyed
        # by the 8 characters), so interactive permission pings are unchanged.
        posts, lines = self._run([("🔔 Claude Code (ai-stack) session 5e5e5e5e - needs you", None)],
                                 marker=False)
        self.assertEqual(len(posts), 1)
        self.assertEqual([ln[0] for ln in lines], ["5e5e5e5e"], repr(lines))


class MapParserTests(unittest.TestCase):
    """bridge.interactive_threads, in process (no HTTP)."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import bridge
        self.bridge = bridge
        fd, self.path = tempfile.mkstemp(prefix="cf-map-")
        os.close(fd)

    def tearDown(self):
        os.remove(self.path)

    def parse(self, text: str) -> dict:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return self.bridge.interactive_threads(self.path)

    def test_two_field_legacy_lines_are_read(self):
        got = self.parse("abcdef12 rootA\n")
        self.assertEqual(got, {"rootA": {"key": "abcdef12", "session_id": ""}})

    def test_full_id_from_a_later_line_applies_to_every_root_of_the_key(self):
        full = "abcdef12-0000-4000-8000-000000000000"
        got = self.parse(f"abcdef12 rootA\nabcdef12 -\nabcdef12 rootB {full}\n")
        self.assertEqual(got["rootA"]["session_id"], full)
        self.assertEqual(got["rootB"]["session_id"], full)

    def test_sentinel_does_not_disown_the_thread(self):
        got = self.parse("abcdef12 rootA\nabcdef12 -\n")
        self.assertIn("rootA", got)

    def test_sentinel_is_never_a_root(self):
        """Attempt 1, X3."""
        self.assertEqual(self.parse("abcdef12 -\n"), {})
        self.assertNotIn("-", self.parse("abcdef12 rootA\nabcdef12 -\n"))

    def test_mismatched_or_malformed_full_id_is_ignored(self):
        got = self.parse("abcdef12 rootA 99999999-0000-4000-8000-000000000000\n"
                         "abcdef12 rootB not-a-uuid\n\nlonely\n")
        self.assertEqual(got["rootA"]["session_id"], "")
        self.assertNotIn("lonely", got)

    def test_missing_file_is_empty(self):
        self.assertEqual(self.bridge.interactive_threads(self.path + ".nope"), {})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        scenario(sys.argv[2])
        sys.exit(0)
    unittest.main(verbosity=2)
