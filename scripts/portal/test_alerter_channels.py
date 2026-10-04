#!/usr/bin/env python3
"""pa-channels: portal-alerter delivers to Telegram / Mattermost / email, against FAKES.

Stdlib only. Runs portal/config/alerter/alerter.ts with the host's `deno` from a
temp copy, pointed at ONE local fake HTTP server that plays the Telegram Bot API,
Mattermost's /api/v4/posts, Google's OAuth token endpoint and Gmail's send
endpoint. No real third-party call can happen:
  * every channel URL is the local fake (PORTAL_ALERT_TELEGRAM_API,
    PORTAL_ALERT_MM_URL, PORTAL_ALERT_GOOGLE_TOKEN_URL, PORTAL_ALERT_GMAIL_SEND_URL);
  * HTTP_PROXY / HTTPS_PROXY point at a dead endpoint (127.0.0.1:1) with only
    127.0.0.1 exempt, so anything that is NOT the fake fails closed;
  * DOCKER_HOST=tcp://127.0.0.1:1 (nothing here uses Docker; set anyway).
Every token / secret is a FAKE value made up here.

Also drives integrity-tripwire.sh's REAL post_alert function (extracted from the
script, run under bash + curl) against the alerter, for contract compatibility.

Usage (repo root):
  python scripts/portal/test_alerter_channels.py
  python scripts/portal/test_alerter_channels.py --alerter <path to another alerter.ts>   (e.g. the base, for RED)
  ... --export-undelivered-state <path>   writes the all-channels-failed state file the
      alerter produced, for scripts/checks/test-watchdog-portal-alerts.ps1 -AlerterStateFile
Exit code = number of failed cases.
"""

from __future__ import annotations

import argparse
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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_ALERTER = os.path.join(REPO, "portal", "config", "alerter", "alerter.ts")
TRIPWIRE = os.path.join(REPO, "portal", "config", "tripwire", "integrity-tripwire.sh")

# FAKE secrets. The checks below assert none of these ever appears in a log line,
# the state file or an HTTP response.
FAKE_TG_TOKEN = "123456789:FAKE-tg-token-pa-channels-test"
FAKE_TG_CHAT = "42424242"
FAKE_MM_TOKEN = "fakemmtokenpachannels000000"
FAKE_MM_CHANNEL = "fakechannelid0000000000000"
FAKE_CLIENT_SECRET = "FAKE-client-secret-pa-channels"
FAKE_REFRESH = "FAKE-refresh-token-pa-channels"
FAKE_ACCESS = "FAKE-access-token-pa-channels"
SECRETS = [FAKE_TG_TOKEN, FAKE_MM_TOKEN, FAKE_CLIENT_SECRET, FAKE_REFRESH, FAKE_ACCESS]

# Detail a caller sends that must NOT reach Telegram / Mattermost.
PRIVATE_LOG_LINE = "Your one-time code is 918273 https://auth.example.invalid/reset?token=abc"
PRIVATE_IP = "203.0.113.77"
PRIVATE_USER = "operator-user"

FAILURES = 0
RESULTS: list[str] = []


def case(cid: str, title: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    verdict = "PASS" if ok else "FAIL"
    if not ok:
        FAILURES += 1
    RESULTS.append(f"{cid} {verdict}")
    print(f"## {cid} - {title}    {verdict}")
    for line in (detail or "").splitlines():
        print(f"    {line}")


# --- the fake --------------------------------------------------------------------

class Fake:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.mode = {"telegram": "ok", "mattermost": "ok", "token": "ok", "gmail": "ok"}

    def reset(self, **modes: str) -> None:
        with self.lock:
            self.calls = []
            self.mode = {"telegram": "ok", "mattermost": "ok", "token": "ok", "gmail": "ok"}
            self.mode.update(modes)

    def of(self, kind: str) -> list[dict]:
        with self.lock:
            return [c for c in self.calls if c["kind"] == kind]


def make_handler(fake: Fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            p = self.path
            if p.startswith("/tg/bot") and p.endswith("/sendMessage"):
                kind = "telegram"
            elif p == "/mm/api/v4/posts":
                kind = "mattermost"
            elif p == "/google/token":
                kind = "token"
            elif p == "/gmail/send":
                kind = "gmail"
            else:
                kind = "unknown"
            with fake.lock:
                fake.calls.append({"kind": kind, "path": p, "body": raw,
                                   "auth": self.headers.get("Authorization", "")})
                mode = fake.mode.get(kind, "ok")
            if kind == "telegram":
                if mode == "ok":
                    return self._send(200, {"ok": True, "result": {"message_id": 1}})
                return self._send(500, {"ok": False, "description": "fake telegram outage"})
            if kind == "mattermost":
                if mode == "ok":
                    return self._send(201, {"id": "post1"})
                return self._send(503, {"message": "fake mattermost outage"})
            if kind == "token":
                if mode == "ok":
                    return self._send(200, {"access_token": FAKE_ACCESS, "token_type": "Bearer",
                                            "expires_in": 3600})
                return self._send(400, {"error": "invalid_grant", "error_description": "Bad Request"})
            if kind == "gmail":
                if mode == "ok":
                    return self._send(200, {"id": "m1"})
                return self._send(500, {"error": "fake gmail outage"})
            return self._send(404, {"error": "unknown fake path"})

    return H


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- the alerter under test ----------------------------------------------------

class Alerter:
    def __init__(self, src: str, fake_base: str, *, telegram=True, mattermost=True,
                 oauth: str = "empty") -> None:
        self.dir = tempfile.mkdtemp(prefix="pa-alerter-")
        shutil.copy(src, os.path.join(self.dir, "alerter.ts"))
        self.state = os.path.join(self.dir, "state.json")
        self.log_path = os.path.join(self.dir, "alerter.log")
        self.port = free_port()
        self.write_oauth(oauth)
        env = dict(os.environ)
        for k in list(env):
            if k.startswith("PORTAL_ALERT_") or k.startswith("DIGEST_"):
                del env[k]
        env.update({
            "DIGEST_TO": "operator@example.invalid",
            "DIGEST_PORT": str(self.port),
            "PORTAL_ALERT_HOST_LABEL": "testhost",
            "PORTAL_ALERT_STATE_FILE": self.state,
            "PORTAL_ALERT_TELEGRAM_API": f"{fake_base}/tg",
            "PORTAL_ALERT_GOOGLE_TOKEN_URL": f"{fake_base}/google/token",
            "PORTAL_ALERT_GMAIL_SEND_URL": f"{fake_base}/gmail/send",
            "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
            "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
            "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
            "DOCKER_HOST": "tcp://127.0.0.1:1",
            "NO_COLOR": "1",
        })
        if telegram:
            env["PORTAL_ALERT_TELEGRAM_BOT_TOKEN"] = FAKE_TG_TOKEN
            env["PORTAL_ALERT_TELEGRAM_CHAT_ID"] = FAKE_TG_CHAT
        if mattermost:
            env["PORTAL_ALERT_MM_URL"] = f"{fake_base}/mm"
            env["PORTAL_ALERT_MM_TOKEN"] = FAKE_MM_TOKEN
            env["PORTAL_ALERT_MM_CHANNEL_ID"] = FAKE_MM_CHANNEL
            env["PORTAL_ALERT_MM_MENTION"] = "@operator"
        self.env = env
        self.log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["deno", "run", "--allow-net", "--allow-read", "--allow-write", "--allow-env", "alerter.ts"],
            cwd=self.dir, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError(f"alerter did not come up; log:\n{self.output()}")

    def write_oauth(self, kind: str) -> None:
        cred = os.path.join(self.dir, "credentials.json")
        tok = os.path.join(self.dir, "token.json")
        if kind == "empty":  # the incident: 0-byte placeholders
            open(cred, "w").close()
            open(tok, "w").close()
        elif kind == "valid":
            with open(cred, "w") as f:
                json.dump({"installed": {"client_id": "fake-client.apps.example.invalid",
                                         "client_secret": FAKE_CLIENT_SECRET,
                                         "redirect_uris": ["http://localhost"]}}, f)
            with open(tok, "w") as f:  # expired, so the alerter must refresh via the fake
                json.dump({"access_token": "", "refresh_token": FAKE_REFRESH,
                           "token_type": "Bearer", "expiry_date": 0}, f)

    def post(self, body: dict | str) -> tuple[int, str]:
        data = body if isinstance(body, str) else json.dumps(body)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/alert", data=data.encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def read_state(self) -> dict | None:
        try:
            with open(self.state, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def output(self) -> str:
        self.log.flush()
        with open(self.log_path, encoding="utf-8", errors="replace") as f:
            return f.read()

    def selftest(self) -> tuple[int, str]:
        p = subprocess.run(
            ["deno", "run", "--allow-net", "--allow-read", "--allow-write", "--allow-env",
             "alerter.ts", "--selftest"],
            cwd=self.dir, env=self.env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def stop(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        self.log.close()


def find_bash() -> str | None:
    for c in (r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash") or ""):
        if c and os.path.exists(c) and "System32" not in c:
            return c
    return None


def tripwire_post(bash: str, port: int, detail: str) -> tuple[int, str]:
    """Run integrity-tripwire.sh's own post_alert function (extracted verbatim)."""
    with open(TRIPWIRE, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^now_iso\(\).*?$", src, re.M)
    fn = re.search(r"^post_alert\(\) \{.*?^\}", src, re.M | re.S)
    assert m and fn, "tripwire functions not found"
    script = "\n".join([
        "set -eu",
        f"ALERTER_URL='http://127.0.0.1:{port}/alert'",
        m.group(0),
        fn.group(0),
        'post_alert config.drift "$1"',
    ])
    env = dict(os.environ, NO_PROXY="127.0.0.1", no_proxy="127.0.0.1",
               HTTP_PROXY="http://127.0.0.1:1", http_proxy="http://127.0.0.1:1")
    # Through a FILE, not `bash -c`: Windows argv quoting mangles the
    # function's backslash-heavy sed expressions on the way to Git bash.
    fd, path = tempfile.mkstemp(prefix="pa-tripwire-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(script + "\n")
    try:
        p = subprocess.run([bash, path, detail], capture_output=True,
                           text=True, timeout=60, env=env)
    finally:
        os.unlink(path)
    return p.returncode, p.stdout + p.stderr


def no_secrets(*texts: str) -> list[str]:
    leaked = []
    for s in SECRETS:
        for t in texts:
            if t and s in t:
                leaked.append(s[:8] + "...")
                break
    return leaked


MINIMAL_RE = re.compile(
    r"^(@operator )?Portal alert \[CRITICAL\] config\.drift on testhost at "
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerter", default=DEFAULT_ALERTER)
    ap.add_argument("--export-undelivered-state", default="")
    args = ap.parse_args()
    src = os.path.abspath(args.alerter)
    print(f"alerter under test: {src}")
    if not shutil.which("deno"):
        print("deno not on PATH - cannot run")
        return 99
    bash = find_bash()

    fake = Fake()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    all_output: list[str] = []
    all_responses: list[str] = []
    all_states: list[str] = []

    # ---- C1..C6: the incident config (0-byte OAuth) + Telegram + Mattermost fakes ----
    fake.reset()
    a = Alerter(src, base, oauth="empty")
    try:
        if bash:
            rc, out = tripwire_post(bash, a.port, "< 0f1e2d  ./users_database.yml\n> 9a8b7c  ./users_database.yml")
            tg = fake.of("telegram")
            case("C1", "tripwire's own post_alert succeeds with email unconfigured (Telegram/MM deliver)",
                 "POST failed" not in out and "sed:" not in out and len(tg) == 1,
                 f"tripwire output: {out.strip() or '(none)'}\ntelegram calls: {len(tg)}")
        else:
            case("C1", "tripwire's own post_alert (needs Git bash)", False, "no Git bash found")
        st, body = a.post({"severity": "critical", "event": "config.drift", "source_ip": PRIVATE_IP,
                           "username": PRIVATE_USER, "timestamp_utc": "2026-10-04T04:00:00Z",
                           "log_line": PRIVATE_LOG_LINE})
        all_responses.append(body)
        tg = fake.of("telegram")
        mm = fake.of("mattermost")
        tg_texts = [json.loads(c["body"]).get("text", "") for c in tg]
        mm_texts = [json.loads(c["body"]).get("message", "") for c in mm]
        case("C2", "POST /alert -> 200 ok, delivered to Telegram AND Mattermost",
             st == 200 and '"ok": true' in body and len(tg) >= 1 and len(mm) >= 1,
             f"status={st} telegram={len(tg)} mattermost={len(mm)}\nbody={body[:300]}")
        texts = tg_texts + mm_texts
        bad = [t for t in texts if not MINIMAL_RE.match(t)]
        leaks = [t for t in texts if any(x in t for x in (PRIVATE_IP, PRIVATE_USER, "918273", "reset?token"))]
        case("C3", "Telegram/MM text is minimal (severity, event, host, time) - no IP/user/log_line",
             bool(texts) and not bad and not leaks,
             "texts:\n" + "\n".join(texts) + (f"\nnon-minimal: {bad}" if bad else "") +
             (f"\nleaked detail: {leaks}" if leaks else ""))
        chat_ok = all(json.loads(c["body"]).get("chat_id") == FAKE_TG_CHAT for c in tg) and \
            all(json.loads(c["body"]).get("channel_id") == FAKE_MM_CHANNEL for c in mm) and \
            all(c["auth"] == f"Bearer {FAKE_MM_TOKEN}" for c in mm)
        case("C4", "Telegram goes to the configured chat; MM to the configured channel as the bot",
             chat_ok and bool(tg) and bool(mm))
        a.post({"severity": "critical", "event": "config.drift", "timestamp_utc": "2026-10-04T04:01:00Z"})
        out = a.output()
        n_disabled = out.count("email disabled: OAuth not configured")
        case("C5", "email unconfigured: 'email disabled: OAuth not configured' logged ONCE over 3 alerts, no Gmail call",
             n_disabled == 1 and not fake.of("token") and not fake.of("gmail"),
             f"'email disabled' lines: {n_disabled}; token calls: {len(fake.of('token'))}; gmail calls: {len(fake.of('gmail'))}")
        s = a.read_state()
        all_states.append(json.dumps(s or {}))
        ok6 = bool(s) and s.get("undelivered_since") is None and \
            s["channels"]["telegram"]["consecutive_failures"] == 0 and \
            s["channels"]["email"]["configured"] is False and \
            s["channels"]["email"]["consecutive_failures"] == 0 and \
            sorted(s["last_attempt"]["delivered"]) == ["mattermost", "telegram"]
        case("C6", "delivery-state file written: delivered, email off (not a failure)", ok6,
             json.dumps(s, indent=1)[:900] if s else "no state file")
        log_lines = [ln for ln in out.splitlines() if "telegram=" in ln]
        case("C7", "per-channel outcome logged for each alert",
             len(log_lines) >= 3 and all("mattermost=" in ln and "email=" in ln for ln in log_lines),
             "\n".join(log_lines[:4]))
        stt, health = a.get("/health")
        all_responses.append(health)
        h = json.loads(health) if stt == 200 else {}
        case("C8", "/health reports per-channel status",
             stt == 200 and h.get("channels", {}).get("telegram", {}).get("configured") is True
             and h.get("channels", {}).get("email", {}).get("configured") is False,
             health[:600])
        rc, so = a.selftest()
        all_output.append(so)
        case("C9", "--selftest goes through every enabled channel, exit 0",
             rc == 0 and "telegram: delivered" in so and "mattermost: delivered" in so,
             so.strip()[:600])
        all_output.append(a.output())
    finally:
        a.stop()

    # ---- C10: one channel down -> logged per channel, still success via the other ----
    fake.reset(telegram="fail")
    a = Alerter(src, base, oauth="empty")
    try:
        sts = [a.post({"severity": "critical", "event": "config.drift"}) for _ in range(3)]
        all_responses += [b for _, b in sts]
        out = a.output()
        s = a.read_state() or {}
        all_states.append(json.dumps(s))
        tgs = (s.get("channels") or {}).get("telegram") or {}
        case("C10", "Telegram outage: each alert still 200 via Mattermost; telegram=FAILED logged; 3 consecutive failures recorded",
             all(st == 200 for st, _ in sts) and out.count("telegram=FAILED") == 3
             and tgs.get("consecutive_failures") == 3 and s.get("undelivered_since") is None,
             f"statuses={[st for st, _ in sts]} failed-lines={out.count('telegram=FAILED')} state.telegram={tgs}")
        all_output.append(out)
    finally:
        a.stop()

    # ---- C11..C12: ALL channels fail -> failure to the caller + marker ----
    fake.reset(telegram="fail", mattermost="fail")
    a = Alerter(src, base, oauth="empty")
    try:
        st, body = a.post({"severity": "critical", "event": "config.drift"})
        all_responses.append(body)
        tw_out = ""
        if bash:
            _, tw_out = tripwire_post(bash, a.port, "drift")
        s = a.read_state() or {}
        all_states.append(json.dumps(s))
        case("C11", "all channels failing: POST /alert -> non-2xx, and the tripwire logs 'POST failed'",
             st >= 500 and '"ok": false' in body and (not bash or "POST failed" in tw_out),
             f"status={st} body={body[:200]}\ntripwire: {tw_out.strip()}")
        case("C12", "all channels failing: delivery-failure marker set (undelivered_since, count, event)",
             bool(s.get("undelivered_since")) and s.get("undelivered_count") == 2
             and s.get("last_undelivered_event") == "config.drift",
             json.dumps({k: s.get(k) for k in ("undelivered_since", "undelivered_count", "last_undelivered_event")}))
        if args.export_undelivered_state and s:
            with open(args.export_undelivered_state, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2)
            print(f"    exported the undelivered state to {args.export_undelivered_state}")
        all_output.append(a.output())
    finally:
        a.stop()

    # ---- C13: email turns itself on when valid OAuth appears (no restart) ----
    fake.reset()
    a = Alerter(src, base, telegram=False, mattermost=False, oauth="empty")
    try:
        st1, b1 = a.post({"severity": "high", "event": "auth.notification", "log_line": PRIVATE_LOG_LINE})
        a.write_oauth("valid")
        st2, b2 = a.post({"severity": "high", "event": "auth.notification", "log_line": PRIVATE_LOG_LINE})
        all_responses += [b1, b2]
        out = a.output()
        gm = fake.of("gmail")
        case("C13", "email: disabled with 0-byte OAuth, re-enabled by valid files with no restart, delivers (full detail)",
             st1 >= 500 and st2 == 200 and len(gm) == 1 and len(fake.of("token")) == 1
             and "email enabled" in out and gm[0]["auth"] == f"Bearer {FAKE_ACCESS}",
             f"before={st1} after={st2} gmail calls={len(gm)} token calls={len(fake.of('token'))}")
        all_output.append(out)
        s = a.read_state() or {}
        all_states.append(json.dumps(s))
    finally:
        a.stop()

    # ---- C14: Google REFUSES the stored refresh token (the live 10-04 cause) ----
    fake.reset(token="fail")
    a = Alerter(src, base, telegram=True, mattermost=False, oauth="valid")
    try:
        sts = [a.post({"severity": "critical", "event": "config.drift"}) for _ in range(3)]
        all_responses += [b for _, b in sts]
        s = a.read_state() or {}
        all_states.append(json.dumps(s))
        em = (s.get("channels") or {}).get("email") or {}
        out = a.output()
        n_tok = len(fake.of("token"))
        case("C14", "refused refresh token: email -> 'email disabled: OAuth not configured' once, tried once, not a failure; Telegram delivers",
             all(st == 200 for st, _ in sts) and n_tok == 1
             and out.count("email disabled: OAuth not configured") == 1
             and em.get("configured") is False and em.get("consecutive_failures") == 0
             and "refused" in (em.get("disabled_reason") or "") and s.get("undelivered_since") is None,
             f"statuses={[st for st, _ in sts]} token calls={n_tok} state.email={em}")
        # a NEW token.json (what setup-token.ts writes) turns it back on, no restart
        fake.mode["token"] = "ok"
        with open(os.path.join(a.dir, "token.json"), "w") as f:
            json.dump({"access_token": "", "refresh_token": FAKE_REFRESH + "-new",
                       "token_type": "Bearer", "expiry_date": 0}, f)
        SECRETS.append(FAKE_REFRESH + "-new")
        st, body = a.post({"severity": "critical", "event": "config.drift"})
        all_responses.append(body)
        out = a.output()
        case("C15", "a new token.json re-enables email without a restart",
             st == 200 and len(fake.of("gmail")) == 1 and "email enabled" in out.split("email disabled")[-1],
             f"status={st} gmail calls={len(fake.of('gmail'))}")
        all_output.append(out)
    finally:
        a.stop()

    # ---- C16: a configured email whose SENDS fail is a failing channel ----
    fake.reset(gmail="fail")
    a = Alerter(src, base, telegram=True, mattermost=False, oauth="valid")
    try:
        for _ in range(3):
            all_responses.append(a.post({"severity": "critical", "event": "config.drift"})[1])
        s = a.read_state() or {}
        all_states.append(json.dumps(s))
        em = (s.get("channels") or {}).get("email") or {}
        case("C16", "Gmail send outage with valid OAuth: email counted failing (3 in a row), Telegram still delivers",
             em.get("configured") is True and em.get("consecutive_failures") == 3
             and "Gmail send failed" in (em.get("last_error") or "") and s.get("undelivered_since") is None,
             f"state.email={em}")
        all_output.append(a.output())
    finally:
        a.stop()

    # ---- C17: no secret value anywhere ----
    leaked = no_secrets("\n".join(all_output), "\n".join(all_responses), "\n".join(all_states))
    case("C17", "no fake secret value in any log, HTTP response or state file", not leaked,
         f"leaked: {leaked}" if leaked else "")

    # ---- C18: nothing but the fake was contacted ----
    case("C18", "no call left for an unknown endpoint", not fake.of("unknown"))

    server.shutdown()
    print("\nRESULTS: " + " ".join(RESULTS))
    print(f"FAILED: {FAILURES}")
    return FAILURES


if __name__ == "__main__":
    sys.exit(main())
