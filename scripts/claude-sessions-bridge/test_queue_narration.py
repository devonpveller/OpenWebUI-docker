#!/usr/bin/env python3
"""The queue narration must name a PRINCIPAL - stdlib only, no Mattermost needed:

    python scripts/claude-sessions-bridge/test_queue_narration.py

WHY THIS FILE EXISTS (U6, 2026-08-30). `poll_queue()` is the one existing consumer that
narrates a pipeline gate transition into a human channel, and for the state a `dark`
pre-review auto-pass produces it emitted "released for review." - passive, no principal, and
BYTE-IDENTICAL to the attended case where a person typed `release:`. Its audit event
{event, from, to} carried no principal either. By the rule the gate ledger is built on - a
record that says a gate passed without saying who or what passed it reads as human approval -
that surface violated it.

It is DORMANT today (every real queue item carries thread="", so poll_queue returns before
posting), which is exactly why it is pinned by a test rather than left as a comment: a
dormant surface is one `-Thread` argument away from waking up.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402


def _item(gate_kind: str = "", by: str = "", profile: str = "") -> dict:
    it = {"id": "x1", "state": "ready-review", "attempt": 1}
    if gate_kind:
        it["gates"] = {"pre_review": {"kind": gate_kind, "by": by, "profile": profile}}
    return it


def _render(item: dict, state: str = "ready-review") -> str:
    """Render exactly the way poll_queue does, through the shipped template."""
    return bridge.QUEUE_NOTES[state].format(
        id=item["id"], attempt=item.get("attempt", 1), sha="",
        principal=bridge.gate_principal_note(item, state), detail="")


class QueueNarrationPrincipal(unittest.TestCase):
    def test_auto_pass_and_human_release_are_not_the_same_sentence(self):
        # The defect, stated as an assertion: these two must never render identically.
        auto = _render(_item("auto", "auto:dark", "dark"))
        human = _render(_item("human", "profnovice", "attended"))
        self.assertNotEqual(auto, human)

    def test_an_auto_pass_says_no_human_saw_it(self):
        out = _render(_item("auto", "auto:dark", "dark"))
        self.assertIn("No human saw this gate", out)
        self.assertIn("auto:dark", out)
        self.assertIn("dark", out)

    def test_a_human_release_names_the_person(self):
        out = _render(_item("human", "profnovice", "attended"))
        self.assertIn("profnovice", out)
        self.assertNotIn("No human saw this gate", out)

    def test_an_item_with_no_gate_record_says_so_rather_than_going_quiet(self):
        # Silence is the failure. An item that predates the gate ledger must not render as a
        # clean "released for review." that a reader takes for an approval.
        out = _render(_item())
        self.assertIn("names no principal", out)

    def test_states_that_cross_no_gate_add_nothing(self):
        self.assertEqual(bridge.gate_principal_note(_item("auto", "auto:dark", "dark"), "merged"), "")
        self.assertEqual(bridge.gate_principal_note(_item("auto", "auto:dark", "dark"), "test-failed"), "")

    def test_every_narrated_gate_state_has_a_principal_slot(self):
        # Derived from the map, not from a hand-written list: adding a gate-crossing state to
        # QUEUE_STATE_GATE without a {principal} slot in its template must fail here.
        for state in bridge.QUEUE_STATE_GATE:
            self.assertIn(state, bridge.QUEUE_NOTES, f"{state} crosses a gate but is never narrated")
            self.assertIn("{principal}", bridge.QUEUE_NOTES[state],
                          f"{state} crosses a gate and its note has no principal slot")


if __name__ == "__main__":
    unittest.main(verbosity=2)
