#!/usr/bin/env python3
"""Unit tests for the follow/auto-wake matcher — stdlib only, no Mattermost needed:

    python scripts/claude-sessions-bridge/test_follows.py
"""
from __future__ import annotations

import os
import queue
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402

mmapi = bridge.mmapi

ME = "me-bot-claude-user-id-00000"
PM = "pm-bot-pm-user-id-000000000"
OP = "op-profnovice-user-id-00000"


def follow(**kw) -> dict:
    f = {"id": "fw-test01", "bridge_thread": "bt1", "channel_id": "ch1",
         "channel_label": "proj-x", "thread_id": "root1", "wake_on": [], "note": "",
         "created": 1000, "expires": 10 ** 15, "last_seen": 1000, "wakes": 0,
         "max_wakes": 20, "one_shot": False}
    f.update(kw)
    return f


def post(**kw) -> dict:
    p = {"id": "p2", "root_id": "root1", "channel_id": "ch1", "user_id": PM,
         "message": "reply from the PM", "create_at": 2000, "props": {}, "type": ""}
    p.update(kw)
    return p


class FollowMatchTests(unittest.TestCase):
    def setUp(self):
        # prefill the username cache so no API call is attempted
        mmapi._user_cache.update({PM: "bot-pm", OP: "profnovice", ME: "bot-claude"})

    def test_reply_from_other_user_wakes(self):
        self.assertTrue(bridge.follow_matches(follow(), post(), ME))

    def test_own_posts_never_wake(self):
        self.assertFalse(bridge.follow_matches(follow(), post(user_id=ME), ME))

    def test_tagged_posts_never_wake(self):
        for prop in ("from_bridge", "from_claude", "from_webhook"):
            self.assertFalse(bridge.follow_matches(follow(), post(props={prop: True}), ME),
                             f"props.{prop} must not wake")

    def test_posts_at_or_before_last_seen_do_not_wake(self):
        self.assertFalse(bridge.follow_matches(follow(last_seen=2000), post(create_at=2000), ME))
        self.assertFalse(bridge.follow_matches(follow(last_seen=2500), post(create_at=2000), ME))

    def test_other_thread_does_not_wake_a_thread_follow(self):
        self.assertFalse(bridge.follow_matches(follow(), post(root_id="otherroot"), ME))

    def test_new_root_post_of_followed_thread_wakes(self):
        # the followed root itself edited/reposted late — id matches thread_id, empty root_id
        self.assertTrue(bridge.follow_matches(follow(), post(id="root1", root_id=""), ME))

    def test_channel_follow_matches_any_thread(self):
        self.assertTrue(bridge.follow_matches(follow(thread_id=""), post(root_id="whatever"), ME))
        self.assertTrue(bridge.follow_matches(follow(thread_id=""), post(root_id=""), ME))

    def test_wake_on_filters_by_username(self):
        self.assertFalse(bridge.follow_matches(follow(wake_on=["profnovice"]), post(), ME))
        self.assertTrue(bridge.follow_matches(follow(wake_on=["bot-pm"]), post(), ME))
        self.assertTrue(bridge.follow_matches(follow(wake_on=["profnovice", "bot-pm"]), post(), ME))

    def test_wake_on_tolerates_at_prefix_and_case(self):
        self.assertTrue(bridge.follow_matches(follow(wake_on=["@Bot-PM"]), post(), ME))

    def test_system_posts_do_not_wake(self):
        self.assertFalse(bridge.follow_matches(follow(), post(type="system_join_channel"), ME))


class FollowEngineTests(unittest.TestCase):
    """Registration → wake → drop, with Mattermost and the worker pool mocked out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (bridge.STATE_FILE, bridge.AUDIT_FILE, bridge.LOG_FILE,
                      bridge.post, mmapi._api)
        bridge.STATE_FILE = os.path.join(self.tmp, "state.json")
        bridge.AUDIT_FILE = os.path.join(self.tmp, "audit.jsonl")
        bridge.LOG_FILE = os.path.join(self.tmp, "bridge.log")
        self.posts_sent: list[tuple[str, str]] = []
        bridge.post = lambda msg, thread_id=None: (
            self.posts_sent.append((thread_id or "", msg)) or {"id": "note1"})
        mmapi._user_cache.update({PM: "bot-pm", ME: "bot-claude"})
        b = object.__new__(bridge.Bridge)
        b.state = {"last_seen": 0, "threads": {"bt1": {"title": "test thread"}},
                   "processed": [], "follows": {}}
        b.state_lock = threading.Lock()
        b.queues = {}
        b._team = ""  # skip the team-name API lookup
        b.ensure_worker = lambda t: b.queues.setdefault(t, queue.Queue())  # no real workers
        self.b = b

    def tearDown(self):
        (bridge.STATE_FILE, bridge.AUDIT_FILE, bridge.LOG_FILE,
         bridge.post, mmapi._api) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _channel_with(posts: dict):
        return lambda method, path, body=None: {"posts": posts}

    def test_register_wake_and_cursor_advance(self):
        self.b._apply_follow_request({
            "action": "follow", "id": "fw-aaa111", "bridge_thread": "bt1",
            "channel_id": "ch1", "channel_label": "proj-x", "thread_id": "root1",
            "wake_on": [], "note": "waiting on PM", "created": 1000,
            "expires": 10 ** 15, "last_seen": 1000, "wakes": 0, "max_wakes": 20,
            "one_shot": False})
        self.assertIn("fw-aaa111", self.b.state["follows"])
        self.assertTrue(any("Following" in m for _, m in self.posts_sent))

        mmapi._api = self._channel_with({"p9": post(id="p9", message="PM says done",
                                                    create_at=5000)})
        self.b.poll_follows(ME)
        q = self.b.queues.get("bt1")
        self.assertIsNotNone(q, "wake must be enqueued for the owning bridge thread")
        prompt, trigger = q.get_nowait()
        self.assertIn("AUTO-WAKE", prompt)
        self.assertIn("PM says done", prompt)
        self.assertIn("waiting on PM", prompt)
        f = self.b.state["follows"]["fw-aaa111"]
        self.assertEqual(f["wakes"], 1)
        self.assertEqual(f["last_seen"], 5000)
        self.assertTrue(any("Auto-wake" in m for _, m in self.posts_sent))
        # same posts again → cursor holds, no second wake
        self.b.poll_follows(ME)
        self.assertTrue(q.empty())

    def test_one_shot_removed_after_first_wake(self):
        self.b.state["follows"]["fw-ccc333"] = follow(id="fw-ccc333", one_shot=True)
        mmapi._api = self._channel_with({"p9": post(create_at=5000)})
        self.b.poll_follows(ME)
        self.assertNotIn("fw-ccc333", self.b.state["follows"])
        self.assertFalse(self.b.queues["bt1"].empty(), "the single wake still fires")

    def test_expired_follow_dropped_without_wake(self):
        self.b.state["follows"]["fw-ddd444"] = follow(id="fw-ddd444", expires=1)
        mmapi._api = self._channel_with({"p9": post(create_at=5000)})
        self.b.poll_follows(ME)
        self.assertNotIn("fw-ddd444", self.b.state["follows"])
        self.assertNotIn("bt1", self.b.queues)
        self.assertTrue(any("expired" in m for _, m in self.posts_sent))

    def test_unfollow_request_drops(self):
        self.b.state["follows"]["fw-bbb222"] = follow(id="fw-bbb222")
        self.b._apply_follow_request({"action": "unfollow", "bridge_thread": "bt1",
                                      "follow_id": "fw-bbb222"})
        self.assertNotIn("fw-bbb222", self.b.state["follows"])
        self.assertTrue(any("unfollowed by the session" in m for _, m in self.posts_sent))

    def test_refollow_same_target_replaces(self):
        self.b.state["follows"]["fw-old111"] = follow(id="fw-old111")
        self.b._apply_follow_request({
            "action": "follow", "id": "fw-new222", "bridge_thread": "bt1",
            "channel_id": "ch1", "channel_label": "proj-x", "thread_id": "root1",
            "wake_on": [], "note": "", "created": 2000, "expires": 10 ** 15,
            "last_seen": 2000, "wakes": 0, "max_wakes": 20, "one_shot": False})
        self.assertNotIn("fw-old111", self.b.state["follows"])
        self.assertIn("fw-new222", self.b.state["follows"])


    # ── sliding "work-day" idle window: renew on human engagement (2026-07-16) ──────────
    def test_renew_slides_expiry_forward(self):
        window = 10 * 3600 * 1000
        self.b.state["follows"]["fw-renew1"] = follow(id="fw-renew1", idle_ms=window, expires=100)
        now = 9_000_000_000
        renewed = self.b._renew_follows(now, bridge_thread="bt1")     # operator engaged the session
        self.assertEqual(renewed, ["fw-renew1"])
        self.assertEqual(self.b.state["follows"]["fw-renew1"]["expires"], now + window)

    def test_renew_only_pushes_forward_never_shortens(self):
        far = 10 ** 15
        self.b.state["follows"]["fw-renew2"] = follow(id="fw-renew2", idle_ms=5000, expires=far)
        renewed = self.b._renew_follows(2000, bridge_thread="bt1")    # 2000+5000 ≪ far → no change
        self.assertEqual(renewed, [])
        self.assertEqual(self.b.state["follows"]["fw-renew2"]["expires"], far)

    def test_renew_scoped_to_the_matching_session_or_channel(self):
        window = 1000
        self.b.state["follows"]["fw-mine"] = follow(id="fw-mine", bridge_thread="bt1",
                                                    idle_ms=window, expires=1)
        self.b.state["follows"]["fw-other"] = follow(id="fw-other", bridge_thread="bt2",
                                                     idle_ms=window, expires=1)
        self.b._renew_follows(5_000_000, bridge_thread="bt1")
        self.assertEqual(self.b.state["follows"]["fw-mine"]["expires"], 5_000_000 + window)
        self.assertEqual(self.b.state["follows"]["fw-other"]["expires"], 1)   # untouched

    def test_operator_post_in_followed_channel_renews_without_waking(self):
        import time as _t
        mmapi._user_cache.update({OP: "profnovice", PM: "bot-pm", ME: "bot-claude"})
        now = int(_t.time() * 1000)
        window = 10 * 3600 * 1000
        self.b.state["follows"]["fw-ch1"] = follow(
            id="fw-ch1", wake_on=["bot-pm"], created=now - window, idle_ms=window,
            expires=now + 60_000)                        # ~1 min from lapsing → survives the drop
        mmapi._api = self._channel_with({"pop": post(id="pop", user_id=OP,
                                          message="steering the org directly", create_at=now)})
        self.b.poll_follows(ME)
        # the operator's own post slid the window forward ~a full work day…
        self.assertGreaterEqual(self.b.state["follows"]["fw-ch1"]["expires"], now + window - 5000)
        # …but did NOT wake the session (wake_on is bot-pm, not the operator)
        self.assertTrue("bt1" not in self.b.queues or self.b.queues["bt1"].empty())

    def test_apply_request_derives_idle_ms_when_absent(self):
        self.b._apply_follow_request({
            "action": "follow", "id": "fw-idle1", "bridge_thread": "bt1", "channel_id": "ch1",
            "channel_label": "proj-x", "thread_id": "root1", "wake_on": [], "note": "",
            "created": 1000, "expires": 1000 + 7000, "last_seen": 1000, "wakes": 0,
            "max_wakes": 20, "one_shot": False})                       # no idle_ms in the request
        self.assertEqual(self.b.state["follows"]["fw-idle1"]["idle_ms"], 7000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
