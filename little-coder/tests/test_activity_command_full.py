"""ao-checks (agent-org gym-002, 2026-10-07) - an activity record keeps its 240-char DISPLAY copy of the
command, but also exposes the full command and says when the display copy was cut.

agent-org read `REPRO:` lines out of these records and banked commands cut mid-token at 240 chars as
permanent acceptance checks (`python3 -c "import os,todo; os.e`), which then failed with a shell syntax
error on every delivery. A consumer must be able to get the whole command, or at least tell it was cut.
"""

import json

from littlecoder.agent import _event_to_activity, read_activity_file

LONG = ("echo 'FINDING: list --due-before keeps tasks due after the cutoff' >> /tmp/lens-findings.txt\n"
        "echo 'REPRO: python3 -c \"import os,todo; os.environ[\\'TODO_DB\\']=\\'/tmp/t.json\\'; "
        "todo.add(\\'x\\', due=\\'2026-10-09\\'); assert todo.list_tasks(due_before=\\'2026-10-08\\') == []\"'"
        " >> /tmp/lens-findings.txt")


def test_long_command_keeps_display_cap_and_exposes_full_text():
    assert len(LONG) > 240
    a = _event_to_activity({"command": LONG, "exit_code": 0})
    assert a["command"] == LONG[:240]                 # display copy unchanged
    assert a["command_truncated"] is True             # ...and marked as cut
    assert a["command_full"] == LONG                  # the whole command is available
    assert a["command_full_truncated"] is False


def test_short_command_is_not_marked_truncated():
    a = _event_to_activity({"command": "git status", "exit_code": 0})
    assert a["command"] == a["command_full"] == "git status"
    assert a["command_truncated"] is False and a["command_full_truncated"] is False


def test_full_copy_is_redacted_too():
    """The full copy goes the same places the display copy does, so it is redacted the same way - a
    token past char 240 must not survive in `command_full`."""
    pat = "ghp_" + "A" * 36
    cmd = "x" * 300 + f" git push https://x-access-token:{pat}@github.com/o/r.git"
    a = _event_to_activity({"command": cmd, "exit_code": 0})
    assert pat not in a["command_full"] and pat not in a["command"]


def test_full_copy_is_bounded_and_says_so():
    cmd = "echo " + "y" * 20000
    a = _event_to_activity({"command": cmd, "exit_code": 0})
    assert len(a["command_full"]) == 8000 and a["command_full_truncated"] is True


def test_read_activity_file_carries_the_full_command(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text(json.dumps({"command": LONG, "exit_code": 1}) + "\n", encoding="utf-8")
    [item] = read_activity_file(str(p))
    assert item["command_full"] == LONG and item["command_truncated"] is True
