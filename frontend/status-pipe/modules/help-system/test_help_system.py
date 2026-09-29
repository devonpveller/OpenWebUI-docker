"""The recovery advice offers the lightest namespace fix first (cf-small-fixes G12).

The retired quick-fixes.bat `namespace` label did one thing that worked: restart tailscale
ALONE (it joins openwebui's network namespace, so that is safe - it is openwebui that must
never be restarted alone), then ping. Its replacements only offered the heavier
emergency-recovery pass. These tests read what the module actually RENDERS for a user.

Run: python -m pytest frontend/status-pipe/modules/help-system/test_help_system.py
"""

import importlib.util
from pathlib import Path

_SERVICE = Path(__file__).resolve().parent / "service" / "help_system.py"
_spec = importlib.util.spec_from_file_location("help_system_under_test", _SERVICE)
help_system = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(help_system)

RESTART_TS = "docker compose -f frontend\\docker-compose.yml restart tailscale"
PING = "docker exec tailscale ping -c 1 8.8.8.8"
RECOVER = "emergency-recovery.ps1 -Action recover"


def _render(query: str) -> str:
    out = help_system.main({"request_id": "t", "input": query})
    assert out["status"] == "ok", out
    return out["content"]


def test_common_issues_answer_names_the_tailscale_only_restart_before_recover():
    content = _render("common issues")
    line = next(ln for ln in content.splitlines() if ln.startswith("- **Network Unreachable**"))
    assert RESTART_TS in line and PING in line and RECOVER in line, line
    assert line.index(RESTART_TS) < line.index(PING) < line.index(RECOVER), line


def test_recovery_quick_fixes_lead_with_the_tailscale_only_restart_and_its_ping():
    content = _render("recovery")
    fixes = [ln for ln in content.splitlines() if ln.startswith("- `")]
    actionable = [f for f in fixes if "Run these on the HOST" not in f]
    assert actionable, content
    assert RESTART_TS in actionable[0] and PING in actionable[0], actionable[0]

