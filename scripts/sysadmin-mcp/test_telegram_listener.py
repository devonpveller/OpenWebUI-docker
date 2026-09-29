#!/usr/bin/env python3
"""Tests for the Telegram listener's recovery reply. Stdlib only. No Telegram call,
no docker call, no recovery run.

Run:  ONLY inside the test container - scripts/sysadmin-mcp/README.md, 'Run the tests'
      (on a host it exits 2 by design); there: python test_telegram_listener.py

What it proves (ac-legacy-recovery, from the ac-recovery-gates review R6):
  * a recovery run whose only ERROR is EARLY and whose exit code is 0 produces a
    reply that carries that ERROR line - first with a stubbed _run, then through a
    REAL powershell child running a stub recovery script (Write-Host output, exit 0)
    in place of emergency-recovery.ps1, via the listener's own _handle_recover and
    _handle_destructive;
  * the check is not vacuous: the ERROR sits outside the last 15 lines, which is all
    the previous reply showed;
  * the reply stays under Telegram's 4096-char limit when the run is noisy.
_reply is replaced by a list append, so nothing is sent.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import telegram_listener as tl  # noqa: E402
import sysadmin as sa  # noqa: E402
import _testguard  # noqa: E402  - fail-closed: dead DOCKER_HOST unless set, readonly call stub
_testguard.install(sa, "readonly", "test_telegram_listener")

_passed = 0
_failed = 0

ERR = "[2026-09-25 03:00:01] [ERROR]   [DIFFERS] ai-stack_llm-net: internal: declared true, found false"


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def fake_run_output(n_info: int = 60) -> str:
    lines = ["[2026-09-25 03:00:00] [INFO] Phase 3: Service restart", ERR]
    lines += [f"[2026-09-25 03:00:{i % 60:02d}] [INFO] step {i}" for i in range(n_info)]
    lines += ["[2026-09-25 03:05:00] [SUCCESS] Recovery completed"]
    return "\n".join(lines)


def test_pure() -> None:
    out = fake_run_output()
    check("fixture: the ERROR is outside the last 15 lines (old reply could not show it)",
          ERR not in out.splitlines()[-15:])
    text = tl._recovery_report("recover", 0, out)
    check("reply carries the early ERROR line", ERR in text, text[:300])
    check("reply states exit 0", "(exit 0)" in text)
    check("reply says exit 0 is not clean when an ERROR was logged", "does NOT mean clean" in text)
    check("reply still carries the last line", "Recovery completed" in text)

    clean = tl._recovery_report("recover", 0, "\n".join(f"[t] [INFO] s{i}" for i in range(40)))
    check("clean run: no 'not clean' warning", "does NOT mean clean" not in clean)
    check("clean run: counts 0 ERROR", "0 ERROR" in clean)

    noisy = "\n".join([ERR] * 400 + [f"[t] [INFO] {'x' * 200}" for _ in range(50)])
    t2 = tl._recovery_report("nuclear", 1, noisy)
    check("noisy run stays under Telegram's 4096 limit", len(t2) <= 4096, str(len(t2)))
    check("noisy run still leads with the ERROR lines", ERR in t2)
    check("noisy run says how many were not shown", "more ERROR/WARN line(s) not shown" in t2)

    tail_only = tl._recovery_report("recover", 0, "\n".join([f"[t] [INFO] s{i}" for i in range(10)] + [ERR]))
    check("an ERROR already in the tail is not repeated", tail_only.count(ERR) == 1)

    # cf-small-fixes G12: 30 early WARNs, THEN the ERROR. In run order the ERROR is the 31st
    # issue line and fell past the 25-line cap; ERROR lines are now listed first.
    warns = [f"[2026-09-25 03:00:00] [WARN] slow step {i}" for i in range(30)]
    warn_first = "\n".join(warns + [ERR] + [f"[t] [INFO] s{i}" for i in range(40)])
    check("fixture: the ERROR comes after more WARNs than the cap",
          warn_first.splitlines().index(ERR) >= tl._MAX_ISSUE_LINES)
    t3 = tl._recovery_report("recover", 0, warn_first)
    check("an ERROR after 30 WARNs is still SHOWN (ERROR lines lead the list)", ERR in t3, t3[:400])
    check("... and it is listed before the first WARN", ERR in t3 and warns[0] in t3 and t3.index(ERR) < t3.index(warns[0]), t3[:400])
    check("... and the WARNs past the cap are counted, not lost", "(+6 more ERROR/WARN line(s) not shown)" in t3, t3[-600:])
    check("... WARNs keep their run order", t3.index(warns[0]) < t3.index(warns[1]))

    empty = tl._recovery_report("recover", 124, "")
    check("empty output still reports the exit code", "(exit 124)" in empty and "(no output)" in empty)

    sent: list[str] = []
    orig_run, orig_reply = tl._run, tl._reply
    try:
        tl._run = lambda cmd, timeout: (0, out)
        tl._reply = sent.append
        tl._handle_recover()
    finally:
        tl._run, tl._reply = orig_run, orig_reply
    final = sent[-1] if sent else ""
    check("_handle_recover (stubbed _run): reply carries the early ERROR", ERR in final, final[:300])


STUB_PS1 = r"""param([string]$Action = "recover")
function Write-Log { param([string]$Level, [string]$Message)
    Write-Host "[2026-09-25 03:00:00] [$Level] $Message" }
Write-Log "INFO" "Ensuring the root anchor's networks (stub)"
Write-Log "ERROR" "  [DIFFERS] ai-stack_llm-net: internal: declared true, found false"
Write-Log "ERROR" "Anchor networks differ from docker-compose.yml - none created (stub)"
for ($i = 0; $i -lt 40; $i++) { Write-Log "INFO" "stub step $i ($Action)" }
Write-Log "SUCCESS" "stub recovery finished"
exit 0
"""


def test_real_child() -> None:
    if not os.path.exists(tl._PWSH) and not shutil.which("powershell"):
        print("SKIP  real powershell child (no powershell on this host)")
        return
    d = tempfile.mkdtemp(prefix="ac-lr-tl-")
    stub = os.path.join(d, "stub-recovery.ps1")
    with open(stub, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write(STUB_PS1)
    sent: list[str] = []
    orig_rec, orig_reply = tl._RECOVERY, tl._reply
    try:
        tl._RECOVERY = stub
        tl._reply = sent.append
        rc, raw = tl._run([tl._PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", stub, "recover"], 120)
        check("stub child exits 0", rc == 0, f"rc={rc} out={raw[:200]}")
        raw_lines = raw.splitlines()
        check("stub child: the DIFFERS line is outside the last 15 lines",
              any("[DIFFERS]" in ln for ln in raw_lines) and not any("[DIFFERS]" in ln for ln in raw_lines[-15:]),
              f"{len(raw_lines)} lines")
        tl._handle_recover()
        final = sent[-1] if sent else ""
        check("_handle_recover (real child, exit 0): reply shows exit 0", "(exit 0)" in final, final[:300])
        check("_handle_recover (real child, exit 0): reply carries the early [DIFFERS] ERROR",
              "[ERROR]   [DIFFERS] ai-stack_llm-net" in final, final[:400])
        sent.clear()
        tl._handle_destructive("nuclear")
        final = sent[-1] if sent else ""
        check("_handle_destructive (real child, exit 0): reply carries the early ERROR",
              "[DIFFERS]" in final and "(exit 0)" in final, final[:400])
    finally:
        tl._RECOVERY, tl._reply = orig_rec, orig_reply
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_pure()
    test_real_child()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
