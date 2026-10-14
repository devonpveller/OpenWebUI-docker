"""ao-exechang e2e, inside the little-coder container (see run.sh). Real ot-exec -> real
open-terminal; then real pi + the open-terminal-exec extension, invoked from the GENERATED agent-org
worker config, against a scripted stub model. Survivors are looked for in /proc (this container
shares open-terminal's pid namespace). Prints one line per check; exit 1 if any check failed.
Writes results.json to $E2E_OUT."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(os.environ["E2E_OUT"])
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []
OT_ENV = {
    "LC_OPEN_TERMINAL_URL": "http://127.0.0.1:8000",
    "LC_OPEN_TERMINAL_KEY": "e2e-test-key",
    "LC_WORKSPACE": "/tmp",
}


def check(name: str, ok: bool, **facts) -> None:
    RESULTS.append({"check": name, "ok": bool(ok), **facts})
    print(("PASS " if ok else "FAIL ") + name + "  " + json.dumps(facts)[:600], flush=True)


def procs() -> list[tuple[int, str]]:
    """(pid, cmdline) of every live, non-zombie process in the shared pid namespace."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            state = (d / "stat").read_text().rsplit(")", 1)[1].split()[0]
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        if state != "Z" and cmd:
            out.append((int(d.name), cmd))
    return out


PROMPT_S = 6.0   # set from the measured baseline in main()


def _state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return ""


def alive(arg: str) -> list[int]:
    """pids whose command line is exactly `sleep <arg>` (a wrapper's `sh -c ...` never matches)."""
    return [p for p, c in procs() if c == f"sleep {arg}"]


def ot_exec(command: str, timeout_s: int, wall_cap: float = 120.0) -> dict:
    env = {**os.environ, **OT_ENV, "LC_EXEC_TIMEOUT": str(timeout_s)}
    t0 = time.monotonic()
    p = subprocess.run(["ot-exec", "-c", command], env=env, capture_output=True, text=True,
                       timeout=wall_cap)
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr, "s": round(time.monotonic() - t0, 2)}


def main() -> int:
    import littlecoder.otexec as o
    check("ot-exec is this checkout's", hasattr(o, "run_command") and o.__file__.startswith("/e2e-src"),
          file=o.__file__)

    # ---- the bound: killed at N s, the whole tree, the model gets the fixed message -------------
    r = ot_exec("echo hello", 3)
    check("fast command untouched", r["rc"] == 0 and r["out"].replace("\r", "") == "hello\n"
          and r["err"] == "" and r["s"] < 3, **r)
    # T4 (round 2): "promptly" is measured against this host's own latency right now, so a loaded
    # host does not fail a check whose point is "far below the 60-s bound" (1 s slice + 1 s settle)
    global PROMPT_S
    PROMPT_S = max(6.0, 2 * r["s"] + 4)
    check("baseline latency measured", True, base_s=r["s"], prompt_threshold_s=PROMPT_S)
    r = ot_exec("sleep 1; echo under", 3)
    check("command under the bound untouched", r["rc"] == 0 and "under" in r["out"] and r["err"] == ""
          and r["s"] < 3, **r)

    for name, cmd, sleeps in [
        ("sleep 1000 killed at the bound", "sleep 1000", ["1000"]),
        ("children killed", "sleep 1011 & sleep 1012 & wait", ["1011", "1012"]),
        ("SIGTERM-ignoring child SIGKILLed", "trap '' TERM; sleep 1013", ["1013"]),
        ("setsid escapee killed", "setsid sleep 1014 & sleep 1015", ["1014", "1015"]),
        ("nested shells killed", "sh -c 'sh -c \"sleep 1016\"'", ["1016"]),
        ("background job of a timed-out command killed", "sleep 1051 & echo bg; sleep 1052", ["1051", "1052"]),
        ("python test runner that hangs killed",
         "printf 'import time\\ndef test_hang():\\n    time.sleep(1000)\\n' > /tmp/test_hang.py && "
         "python3 -c 'import time; print(\"collected 1 item\", flush=True); time.sleep(1017)'", []),
    ]:
        r = ot_exec(cmd, 3)
        time.sleep(0.5)
        left = {s: alive(s) for s in sleeps}
        py_left = [p for p, c in procs() if "time.sleep(1017)" in c]
        first = f"[ot-exec] timed out after 3 s, killed: `{cmd[:60]}"
        check(name, r["rc"] == 124 and first in r["err"] and "nohup CMD > /tmp/x.log 2>&1 &" in r["err"]
              and "could not" not in r["err"] and "every process it started" not in r["err"]
              and 3 <= r["s"] < 3 + 15 and not any(left.values()) and not py_left,
              survivors=left, py_survivors=py_left, **r)
    r_py = r
    check("partial output is kept on timeout", "collected 1 item" in r_py["out"], out=r_py["out"])

    # ---- round 2 (tester attempt 1, D1-D3) ------------------------------------------------------
    for name, cmd, marks in [
        ("D1: untagged SIGTERM-ignorer in the command's group is killed",
         "env -i sh -c 'trap \"\" TERM; sleep 2009' & sleep 2010", ["sleep 2009", "sleep 2010"]),
        ("D1: env -u LC_EXEC_ID SIGTERM-ignorer is killed",
         "env -u LC_EXEC_ID sh -c 'trap \"\" TERM; sleep 2023'", ["sleep 2023"]),
        ("D2: a STOPPED wrapper does not stretch the call and is killed",
         "kill -STOP $PPID; sleep 2018", ["sleep 2018"]),
        ("D2: a stopped process in the group is killed", "sleep 2019 & kill -STOP $!; sleep 2020",
         ["sleep 2019", "sleep 2020"]),
    ]:
        r = ot_exec(cmd, 3)
        time.sleep(0.5)
        left = [(p, c) for p, c in procs() if any(m in c for m in marks)]
        check(name, r["rc"] == 124 and 3 <= r["s"] < 3 + 15 and not left
              and "none of them is still running" in r["err"] and "every process it started" not in r["err"],
              survivors=left, **r)
    r = ot_exec("setsid env -i sleep 2011 & sleep 2012", 3)
    time.sleep(0.5)
    check("D3/A6: new session + cleared env, parent alive: killed as a descendant",
          r["rc"] == 124 and not alive("2011") and not alive("2012"), **r)
    r = ot_exec("setsid env -i sh -c 'sleep 2024 &'; sleep 2025", 3)
    time.sleep(0.5)
    check("D3: a truly untrackable escapee (new session, cleared env, parent gone) is not claimed killed",
          r["rc"] == 124 and not alive("2025") and "cannot be tracked and may still run" in r["err"]
          and "every process it started" not in r["err"], escapee_alive=alive("2024"), **r)
    ign = next(x for x in RESULTS if x["check"] == "SIGTERM-ignoring child SIGKILLed")
    check("SIGTERM-ignorer needed SIGKILL and the message says so",
          "needed SIGKILL" in ign.get("err", ""), err=ign.get("err", ""))

    # ---- the background trap: returns when the command ends, the job keeps running ---------------
    for name, cmd, sleeps in [
        ("( sleep ) & returns promptly", "( sleep 1021 ) &", ["1021"]),
        ("sleep & returns promptly", "sleep 1022 &", ["1022"]),
        ("C7d pattern: ( suite > log; echo >> log ) & echo returns promptly",
         "cd /tmp && ( sleep 1023 > /tmp/ab_baseline.log 2>&1; echo \"AB_EXIT=$?\" >> /tmp/ab_baseline.log ) "
         "& echo \"started AB full suite in bg: $!\"", ["1023"]),
        ("chatty unredirected background runner returns promptly",
         "python3 -c 'import time\nfor i in range(100000):\n    print(i, flush=True); time.sleep(0.01)' & "
         "echo started", []),
        ("unredirected background unittest run returns promptly",
         "printf 'import time, unittest\\nclass T(unittest.TestCase):\\n    def test_slow(self):\\n"
         "        time.sleep(1024)\\n' > /tmp/t_slow.py && cd /tmp && python3 -m unittest -v t_slow &", []),
    ]:
        r = ot_exec(cmd, 60)
        time.sleep(0.3)
        kept = {s: alive(s) for s in sleeps}
        check(name, r["rc"] == 0 and r["s"] < PROMPT_S and "background process(es) it started are still running"
              in r["err"] and all(kept.values()), kept_running=kept, **r)
    r = ot_exec("nohup sleep 1031 > /tmp/x 2>&1 &", 60)
    time.sleep(0.3)
    check("nohup ... > log 2>&1 & returns promptly, job keeps running, no note",
          r["rc"] == 0 and r["s"] < PROMPT_S and r["err"] == "" and alive("1031"), **r)

    # ---- unchanged semantics + fixed classes ---------------------------------------------------
    r = ot_exec("cat", 3)
    check("stdin is /dev/null: `cat` ends at once instead of hanging", r["rc"] == 0 and r["s"] < 3, **r)
    r = ot_exec("echo out; echo err >&2; exit 3", 3)
    check("exit status passes through", r["rc"] == 3 and "out" in r["out"] and "err" in r["out"], **r)
    r = ot_exec("timeout 1 sleep 5", 10)
    check("a user's own `timeout` exit 124 is not our timeout", r["rc"] == 124 and "[ot-exec]" not in r["err"], **r)
    r = ot_exec("python3 -c \"print('a'*20000)\"", 10)
    check("long output has no invented line breaks", r["out"].replace("\r\n", "\n") == "a" * 20000 + "\n",
          out_len=len(r["out"]), newlines=r["out"].count("\n"))
    r = ot_exec("echo 'café ✓'", 3)
    check("non-ASCII command text and output", r["rc"] == 0 and "café ✓" in r["out"], **r)
    big = "x" * 100_000
    r = ot_exec(f"cat > /tmp/big.txt <<'EOF'\n{big}\nEOF\nwc -c < /tmp/big.txt", 10)
    check("a 100 KB heredoc command still runs", r["rc"] == 0 and r["out"].strip() == "100001",
          rc=r["rc"], out=r["out"][-200:], err=r["err"][-300:])
    for n in range(5):   # the status file is written just before the PTY closes: no false note
        r = ot_exec(f"sleep 1; echo plain{n}", 10)
        check(f"a command ending near a slice edge is a plain finish ({n})",
              r["rc"] == 0 and r["err"] == "" and f"plain{n}" in r["out"], **r)
    r = ot_exec("umask", 3)
    check("umask 000 still applies", r["out"].strip() == "0000", **r)
    r = ot_exec("echo $LC_EXEC_CMD; echo id=${LC_EXEC_ID:+set}", 3)
    check("the command sees LC_EXEC_ID, not LC_EXEC_CMD", r["out"].replace("\r", "") == "\nid=set\n", **r)

    # ---- pi abort: SIGTERM to ot-exec kills the command in open-terminal ------------------------
    env = {**os.environ, **OT_ENV, "LC_EXEC_TIMEOUT": "60"}
    t0 = time.monotonic()
    p = subprocess.Popen(["ot-exec", "-c", "sleep 1041"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    time.sleep(2.0)
    was = alive("1041")
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    time.sleep(0.3)
    check("SIGTERM to ot-exec (pi abort) kills the command", bool(was) and p.returncode == 143
          and not alive("1041") and "[ot-exec] stopped (SIGTERM)" in err and time.monotonic() - t0 < 15,
          rc=p.returncode, err=err, was=was, s=round(time.monotonic() - t0, 2))
    # the same once the wait loop runs (SIGTERM lands in a status poll, not in the start request)
    t0 = time.monotonic()
    p = subprocess.Popen(["ot-exec", "-c", "sleep 1042"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    time.sleep(4.5)
    was = alive("1042")
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    time.sleep(0.3)
    check("SIGTERM mid-wait kills the command", bool(was) and p.returncode == 143
          and not alive("1042") and "[ot-exec] stopped (SIGTERM)" in err and time.monotonic() - t0 < 15,
          rc=p.returncode, err=err, was=was, s=round(time.monotonic() - t0, 2))

    # ---- the real pi turn, built from the generated worker config ---------------------------------
    pi_turn("timeout", "sleep 1000", expect_timeout=True)
    pi_turn("bgtrap", "( sleep 1061 ) & echo started-bg", expect_timeout=False)

    check("other commands' processes survive later kills (nohup job from earlier still alive)",
          bool(alive("1031")), alive=alive("1031"))
    wrappers = [(p, c[:80]) for p, c in procs() if "/tmp/lc-exec-" in c]
    stopped = [p for p, c in procs() if _state(p) == "T"]
    check("no wrapper shell and no stopped process is left anywhere", not wrappers and not stopped,
          wrappers=wrappers, stopped=stopped)

    (OUT / "results.json").write_text(json.dumps(RESULTS, indent=1), encoding="utf-8")
    failed = [r["check"] for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


def pi_turn(tag: str, command: str, expect_timeout: bool) -> None:
    sys.path.insert(0, "/e2e-src")
    from littlecoder.agent import AgentRunner
    from littlecoder.config import load_config
    gen = Path(f"/tmp/worker-configs-{tag}")
    subprocess.run([sys.executable, "/e2e-repo/agent-org/scripts/gen-worker-configs.py", "--out-dir", str(gen)],
                   check=True, capture_output=True, timeout=60)
    cfg = load_config(gen / "worker-1" / "little-coder.config.yaml")
    cfg.agent.session_dir = f"/tmp/sess-{tag}"

    class _State:
        task_id, session_id, channel, user_id, repo, prompt, plan_only = (
            f"e2e-{tag}", f"s-{tag}", "cli", "u", "r", "Run the command.", False)

    class _Ctx:
        state = _State()
    runner = AgentRunner(cfg, journals=None, ot_client=None)
    cmd, _ = runner._build_invocation("Run the command.", _Ctx())
    env = runner._build_env(_Ctx(), f"/tmp/ot-events-{tag}.jsonl")
    bound_from_config = env["LC_EXEC_TIMEOUT"]
    env.update(OT_ENV)
    env["LC_EXEC_TIMEOUT"] = "3"   # the mechanism at a test-sized bound; the value 900 is checked above
    reqs = OUT / f"model-requests-{tag}.jsonl"
    stub = subprocess.Popen([sys.executable, "/e2e/stub_model.py", "8080", str(reqs), command],
                            stderr=open(OUT / f"stub-{tag}.log", "w"))
    time.sleep(1.0)
    os.makedirs("/tmp/ws", exist_ok=True)
    t0 = time.monotonic()
    with open(OUT / f"pi-{tag}.jsonl", "w") as fo, open(OUT / f"pi-{tag}.err", "w") as fe:
        p = subprocess.run(cmd, env=env, cwd="/tmp/ws", stdout=fo, stderr=fe, timeout=180)
    secs = round(time.monotonic() - t0, 2)
    stub.terminate()
    bodies = [json.loads(x)["body"] for x in reqs.read_text().splitlines()]
    tool_msgs = [m for b in bodies for m in b.get("messages", []) if m.get("role") == "tool"]
    tool_text = json.dumps(tool_msgs[-1].get("content")) if tool_msgs else ""
    system = json.dumps([m for m in bodies[0].get("messages", []) if m.get("role") in ("system", "developer")])
    events = [json.loads(x) for x in open(OUT / f"pi-{tag}.jsonl") if x.strip().startswith("{")]
    ends = [e for e in events if e.get("type") == "tool_execution_end"]
    check(f"pi[{tag}]: argv built from the generated worker config; its bound is 900",
          bound_from_config == "900" and "/app/agent-knowledge/environment.md" in cmd, bound=bound_from_config)
    check(f"pi[{tag}]: the worker system prompt carries the guidance",
          "Run the narrowest test that shows the behaviour first" in system
          and "nohup python -m pytest -q tests > /tmp/suite.log 2>&1 &" in system)
    check(f"pi[{tag}]: the turn continued (a 2nd model call) and pi ended on its own",
          len(bodies) == 2 and p.returncode == 0, requests=len(bodies), rc=p.returncode, s=secs)
    if expect_timeout:
        check("pi[timeout]: the MODEL received the timeout message as the tool result",
              "[ot-exec] timed out after 3 s, killed: `sleep 1000`" in tool_text
              and "none of them is still running" in tool_text
              and "nohup CMD > /tmp/x.log 2>&1 &" in tool_text, tool_result=tool_text[:900])
        check("pi[timeout]: pi recorded it as an error (isError true)",
              bool(ends) and ends[-1].get("isError") is True, end=ends[-1] if ends else None)
        check("pi[timeout]: no survivor of the timed-out command", not alive("1000"))
        check("pi[timeout]: the turn waited ~the bound, not more", secs < 30, s=secs)
    else:
        check("pi[bgtrap]: returned promptly with the output and the note",
              "started-bg" in tool_text and "still running" in tool_text and secs < 20,
              tool_result=tool_text[:600], s=secs)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a crash is a failure, with its cause in the log
        import traceback
        traceback.print_exc()
        (OUT / "results.json").write_text(json.dumps(RESULTS + [{"check": "crash", "ok": False,
                                                                 "error": repr(exc)}], indent=1))
        sys.exit(2)
