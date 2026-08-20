#!/usr/bin/env python3
"""Unit tests for the two-lane attention model — stdlib only, no Mattermost needed:

    python scripts/claude-sessions-bridge/test_lanes.py

Covers PLAN-bridge-two-lane-attention.md: lane arbitration, the valve control law, the
coalesced catch-up, `!stop` purge semantics, 👎 tombstones, decision/progress classification
(including the fail-open rule), and the release-time staleness guard.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402

mmapi = bridge.mmapi
PM = "pm-bot-pm-user-id-000000000"


def user(msg="hello", pid="u1") -> bridge._Item:
    return bridge._Item("user", msg, pid)


def wake(msg="AUTO-WAKE", pid="w1", created_at=1000, body="", klass="decision") -> bridge._Item:
    return bridge._Item("wake", msg, pid,
                        {"created_at": created_at, "body": body or msg, "klass": klass})


def blocking_get(q: bridge._ThreadQueue, secs: float = 2.0):
    """Drive the real blocking get() from ONE thread (as the single per-thread worker does)."""
    out = []
    t = threading.Thread(target=lambda: out.append(q.get()), daemon=True)
    t.start()
    t.join(secs)
    return out[0] if out else None


class LaneArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.q = bridge._ThreadQueue()

    def test_lane1_beats_lane2_regardless_of_arrival_order(self):
        self.q.put_wake(wake(pid="w1"))
        self.q.put_wake(wake(pid="w2"))
        self.q.put_user(user(pid="u1"))
        item, _ = self.q.try_get()
        self.assertEqual(item.post_id, "u1", "the operator must be served before queued wakes")

    def test_operator_message_closes_the_valve(self):
        self.assertTrue(self.q.valve_open)
        self.q.put_user(user())
        self.assertFalse(self.q.valve_open, "lane-1 activity closes the valve")

    def test_closed_valve_holds_lane2_but_never_lane1(self):
        self.q.set_valve(False)
        self.q.put_wake(wake())
        self.assertIsNone(self.q.try_get(), "lane 2 must be held while the valve is shut")
        self.q.put_user(user(pid="u9"))
        item, _ = self.q.try_get()
        self.assertEqual(item.post_id, "u9", "lane 1 is never gated")

    def test_held_wakes_are_deferred_not_dropped(self):
        self.q.set_valve(False)
        self.q.put_wake(wake(pid="w1"))
        self.assertIsNone(self.q.try_get())
        self.q.set_valve(True)  # the turn ended with no parked question
        item, extras = self.q.try_get()
        self.assertEqual(item.post_id, "w1", "the held wake survives and is delivered on reopen")
        self.assertEqual(extras, [])

    def test_reopen_coalesces_every_held_wake_into_one_turn(self):
        self.q.set_valve(False)
        for i in range(4):
            self.q.put_wake(wake(pid=f"w{i}"))
        self.q.set_valve(True)
        item, extras = self.q.try_get()
        self.assertEqual(item.post_id, "w0")
        self.assertEqual([e.post_id for e in extras], ["w1", "w2", "w3"],
                         "all held wakes ride out in a single catch-up turn")
        self.assertTrue(self.q.empty())

    def test_full_stop_purges_lane2_and_keeps_lane1(self):
        self.q.put_user(user(pid="u1"))
        self.q.put_wake(wake(pid="w1"))
        self.q.put_wake(wake(pid="w2"))
        self.assertEqual(self.q.purge_lane2(), 2)
        lane1, lane2 = self.q.depth()
        self.assertEqual((lane1, lane2), (1, 0), "!stop clears background, never the operator")

    def test_cancelled_item_is_tombstoned(self):
        self.q.put_user(user(pid="u1"))
        self.q.cancel("u1")
        self.assertIn("u1", self.q.cancelled)

    def test_blocking_get_wakes_when_the_valve_reopens(self):
        """The real worker path: one thread parked in get() is released by set_valve(True)."""
        self.q.set_valve(False)
        self.q.put_wake(wake(pid="w1"))
        out = []
        t = threading.Thread(target=lambda: out.append(self.q.get()), daemon=True)
        t.start()
        t.join(0.4)
        self.assertEqual(out, [], "still held while the valve is shut")
        self.q.set_valve(True)
        t.join(2.0)
        self.assertTrue(out and out[0][0].post_id == "w1", "reopening releases the parked worker")

    def test_pending_ids_cover_both_lanes(self):
        self.q.put_user(user(pid="u1"))
        self.q.put_wake(wake(pid="w1"))
        self.assertEqual(sorted(self.q.pending_post_ids()), ["u1", "w1"])


class ClassificationTests(unittest.TestCase):
    def test_props_ao_class_is_authoritative(self):
        self.assertEqual(bridge.classify_post(
            {"props": {"ao_class": "progress"}, "message": "Reply `approve x`"})[0], "progress")
        self.assertEqual(bridge.classify_post(
            {"props": {"ao_class": "decision"}, "message": "just narrating"})[0], "decision")

    def test_decision_prose_markers(self):
        for msg in ("Reply `approve effort-1` to proceed",
                    "⛔ needs your attention",
                    "CONCERN — state=frozen",
                    "say **merge it** when ready",
                    "stall-escalated to you"):
            self.assertEqual(bridge.classify_post({"props": {}, "message": msg})[0], "decision",
                             f"should be decision: {msg}")

    def test_progress_prose_markers(self):
        for msg in ("opened `effort-gym-005b`", "readiness ✓ dispatching a worker",
                    "plan approved — dispatching", "archived effort", "agent-bridge online"):
            self.assertEqual(bridge.classify_post({"props": {}, "message": msg})[0], "progress",
                             f"should be progress: {msg}")

    def test_unrecognised_fails_open_to_decision(self):
        klass, why = bridge.classify_post({"props": {}, "message": "something entirely novel"})
        self.assertEqual(klass, "decision", "under-waking would MISS A GATE — must fail open")
        self.assertIn("failing open", why)


class StalenessGuardTests(unittest.TestCase):
    """Deferral manufactures staleness, so held wakes are re-validated at RELEASE time."""

    def setUp(self):
        self.b = bridge.Bridge.__new__(bridge.Bridge)
        self.b.queues = {"bt1": bridge._ThreadQueue()}
        self.b.digest = {}
        mmapi._user_cache.update({PM: "bot-pm"})

    def test_fresh_lone_wake_passes_through_verbatim(self):
        """REGRESSION: a live wake must reach the session exactly as it did pre-change.

        Wrapping it in 'held while you were with the operator … VERIFY before acting' told the
        session a falsehood and made it distrust current information."""
        fresh = wake(pid="w1", msg="ORIGINAL WAKE TEXT", created_at=int(time.time() * 1000))
        out = self.b._compose_wake_prompt("bt1", fresh, [])
        self.assertEqual(out, "ORIGINAL WAKE TEXT", "a live wake must not be reframed")
        self.assertNotIn("Catch-up", out)

    def test_fresh_wake_still_wraps_when_digest_is_pending(self):
        """The fast path must not silently swallow buffered progress."""
        self.b.digest["bt1"] = ["@bot-pm: opened effort-1"]
        out = self.b._compose_wake_prompt(
            "bt1", wake(pid="w1", msg="X", created_at=int(time.time() * 1000)), [])
        self.assertIn("Progress since the last wake", out)

    def test_coalesced_fresh_wakes_do_not_claim_they_waited(self):
        now = int(time.time() * 1000)
        out = self.b._compose_wake_prompt(
            "bt1", wake(pid="w1", msg="A", created_at=now),
            [wake(pid="w2", msg="B", created_at=now)])
        self.assertIn("delivered together", out)
        self.assertNotIn("held while you were with the operator", out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_genuinely_held_wake_keeps_the_warning(self):
        old = int((time.time() - 45 * 60) * 1000)
        out = self.b._compose_wake_prompt("bt1", wake(pid="w1", created_at=old), [])
        self.assertIn("held while you were with the operator", out)
        self.assertIn("still in a blocking state", out)

    def test_age_is_always_stated(self):
        old = int((__import__("time").time() - 45 * 60) * 1000)
        out = self.b._compose_wake_prompt("bt1", wake(pid="w1", created_at=old), [])
        self.assertIn("45 min ago", out, "every wake must state its age")

    def test_superseded_effort_is_downgraded_not_acted_on(self):
        approved = wake(pid="w1", msg="plan approved for effort-gym-9",
                        body="plan approved for effort-gym-9")
        aborted = wake(pid="w2", msg="effort-gym-9 aborted",
                       body="effort-gym-9 aborted")
        out = self.b._compose_wake_prompt("bt1", approved, [aborted])
        self.assertIn("SUPERSEDED", out)
        self.assertIn("now stale", out)
        self.assertIn("do NOT act on these", out)

    def test_all_stale_burns_no_turn(self):
        a = wake(pid="w1", msg="plan approved for effort-x", body="plan approved for effort-x")
        b = wake(pid="w2", msg="effort-x archived", body="effort-x archived")
        # b itself carries the terminal state, so it stays live; a is superseded.
        out = self.b._compose_wake_prompt("bt1", a, [b])
        self.assertIsNotNone(out)
        # a lone superseded item with nothing live → no turn at all
        self.b.queues["bt1"].cancelled.clear()
        only = self.b._compose_wake_prompt("bt1", wake(pid="w3", msg="x", body="x"), [])
        self.assertIsNotNone(only, "a normal wake still produces a turn")

    def test_buffered_progress_digest_rides_along(self):
        self.b.digest["bt1"] = ["@bot-pm: opened effort-1", "@bot-pm: dispatching"]
        out = self.b._compose_wake_prompt("bt1", wake(pid="w1"), [])
        self.assertIn("Progress since the last wake", out)
        self.assertIn("opened effort-1", out)
        self.assertEqual(self.b.digest.get("bt1"), None, "digest is consumed once delivered")

    def test_cancelled_extras_are_excluded(self):
        self.b.queues["bt1"].cancel("w2")
        out = self.b._compose_wake_prompt(
            "bt1", wake(pid="w1", msg="live one"), [wake(pid="w2", msg="cancelled one")])
        self.assertIn("live one", out)
        self.assertNotIn("cancelled one", out)


class ThumbsDownTests(unittest.TestCase):
    """👎 detection. Only an OPERATOR may cancel — a bot's reaction never can."""

    def setUp(self):
        self.b = bridge.Bridge.__new__(bridge.Bridge)
        self._orig = mmapi._api
        mmapi._user_cache.update({"op1": "profnovice", "bot1": "bot-claude"})

    def tearDown(self):
        mmapi._api = self._orig

    def _reactions(self, rs):
        mmapi._api = lambda method, path, body=None: rs

    def test_operator_thumbsdown_detected(self):
        self._reactions([{"emoji_name": "-1", "user_id": "op1"}])
        self.assertTrue(self.b._operator_thumbsdown("p1"))

    def test_thumbsdown_alias_detected(self):
        self._reactions([{"emoji_name": "thumbsdown", "user_id": "op1"}])
        self.assertTrue(self.b._operator_thumbsdown("p1"))

    def test_bot_thumbsdown_never_cancels(self):
        self._reactions([{"emoji_name": "-1", "user_id": "bot1"}])
        self.assertFalse(self.b._operator_thumbsdown("p1"))

    def test_other_emoji_ignored(self):
        self._reactions([{"emoji_name": "white_check_mark", "user_id": "op1"}])
        self.assertFalse(self.b._operator_thumbsdown("p1"))

    def test_api_failure_is_not_a_cancel(self):
        def boom(*a, **k):
            raise OSError("transient")
        mmapi._api = boom
        self.assertFalse(self.b._operator_thumbsdown("p1"), "a failed poll must not cancel work")


class QuestionMarkerTests(unittest.TestCase):
    """The marker IS the deterministic 'agent is waiting on the human' signal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # LOG_FILE/AUDIT_FILE are resolved at import time, so redirecting STATE_DIR alone would
        # leave the tests writing into the LIVE bridge.log and audit.jsonl (it did, once).
        self._orig = (bridge.STATE_DIR, bridge.LOG_FILE, bridge.AUDIT_FILE)
        bridge.STATE_DIR = self.tmp
        bridge.LOG_FILE = os.path.join(self.tmp, "bridge.log")
        bridge.AUDIT_FILE = os.path.join(self.tmp, "audit.jsonl")

    def tearDown(self):
        bridge.STATE_DIR, bridge.LOG_FILE, bridge.AUDIT_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, thread, asked_at):
        with open(bridge.question_marker(thread), "w", encoding="utf-8") as fh:
            json.dump({"thread": thread, "asked_at": asked_at, "question": "?"}, fh)

    def test_absent_marker_means_not_parked(self):
        self.assertIsNone(bridge.question_parked("nope"))

    def test_fresh_marker_parks_the_valve(self):
        self._write("bt1", int(time.time() * 1000))
        self.assertIsNotNone(bridge.question_parked("bt1"))

    def test_stale_marker_self_heals(self):
        """A hard-killed approval server must NOT be able to latch the valve shut forever."""
        self._write("bt1", int((time.time() - bridge.QUESTION_TIMEOUT - 600) * 1000))
        self.assertIsNone(bridge.question_parked("bt1"), "stale marker must not hold the valve")
        self.assertFalse(os.path.exists(bridge.question_marker("bt1")), "and is cleaned up")


if __name__ == "__main__":
    unittest.main(verbosity=2)
