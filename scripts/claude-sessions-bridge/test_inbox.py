"""Tests for the durable inbox (inbox.py) and its wiring into the bridge.

The case that matters is `test_kill_the_poller_*`: it is the validation the dark-factory
plan's U0 row names by hand ("a kill-the-poller drill proves no message is lost"), and it
is written to FAIL against the pre-inbox bridge, where an admitted message lived only in
an in-memory deque.
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from inbox import COMPACT_AFTER, Inbox  # noqa: E402


@pytest.fixture()
def box(tmp_path):
    return Inbox(str(tmp_path / "inbox"))


# ── the record itself ────────────────────────────────────────────────────────

def test_recorded_message_is_pending(box):
    assert box.record("thread1", "post1", "do the thing") is True
    pend = box.pending("thread1")
    assert [p["post_id"] for p in pend] == ["post1"]
    assert pend[0]["prompt"] == "do the thing"


def test_record_is_idempotent_by_post_id(box):
    assert box.record("thread1", "post1", "do the thing") is True
    assert box.record("thread1", "post1", "do the thing") is False
    assert len(box.pending("thread1")) == 1


def test_consumed_message_is_no_longer_pending(box):
    box.record("thread1", "post1", "do the thing")
    box.mark_consumed("thread1", "post1")
    assert box.pending("thread1") == []


def test_threads_lists_only_threads_with_records(box):
    box.record("thread1", "post1", "a")
    box.record("thread2", "post2", "b")
    assert sorted(box.threads()) == ["thread1", "thread2"]


def test_wake_items_without_a_post_id_are_not_recorded(box):
    # Lane-2 wakes carry no post id and are regenerated from follow state; keying them all
    # to "" would collapse them into a single entry.
    assert box.record("thread1", "", "a wake") is False
    assert box.pending("thread1") == []


def test_threads_are_isolated_from_one_another(box):
    box.record("thread1", "post1", "a")
    box.record("thread2", "post2", "b")
    box.mark_consumed("thread1", "post1")
    assert box.pending("thread1") == []
    assert [p["post_id"] for p in box.pending("thread2")] == ["post2"]


def test_pending_preserves_arrival_order(box):
    for i in range(5):
        box.record("thread1", f"post{i}", f"msg{i}")
    assert [p["post_id"] for p in box.pending("thread1")] == [f"post{i}" for i in range(5)]


def test_out_of_order_consumption_is_honoured(box):
    # The queue does not consume FIFO (lane 1 pre-empts lane 2, catch-up coalesces), which is
    # why consumption is a SET of ids and not a count-based offset. A count would mark the
    # WRONG message consumed here.
    for i in range(3):
        box.record("thread1", f"post{i}", f"msg{i}")
    box.mark_consumed("thread1", "post1")
    assert [p["post_id"] for p in box.pending("thread1")] == ["post0", "post2"]


# ── crash-shaped inputs ──────────────────────────────────────────────────────

def test_torn_final_line_is_dropped_not_fatal(box):
    box.record("thread1", "post1", "complete")
    path = box._entries_path("thread1")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"post_id": "post2", "prompt": "half-writ')  # what a crash mid-append leaves
    pend = box.pending("thread1")
    assert [p["post_id"] for p in pend] == ["post1"]


def test_unreadable_inbox_reads_as_empty(box):
    assert box.pending("never-seen") == []
    assert box.threads() == []


# ── the bound on an append-only file ─────────────────────────────────────────

def test_compaction_bounds_the_file(box):
    for i in range(COMPACT_AFTER + 10):
        box.record("thread1", f"post{i}", "x")
    for i in range(COMPACT_AFTER + 9):          # consume all but the last
        box.mark_consumed("thread1", f"post{i}")
    with open(box._entries_path("thread1"), encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    assert len(lines) < COMPACT_AFTER, "compaction never ran - the log grows without bound"
    # and compaction must not lose the one message still owed
    assert [p["post_id"] for p in box.pending("thread1")] == [f"post{COMPACT_AFTER + 9}"]


def test_compaction_survives_a_reopen(box, tmp_path):
    for i in range(COMPACT_AFTER + 5):
        box.record("thread1", f"post{i}", "x")
    for i in range(COMPACT_AFTER + 4):
        box.mark_consumed("thread1", f"post{i}")
    reopened = Inbox(str(tmp_path / "inbox"))
    assert [p["post_id"] for p in reopened.pending("thread1")] == [f"post{COMPACT_AFTER + 4}"]


# ── THE KILL-THE-POLLER DRILL (the plan's named U0 validation) ───────────────

def test_kill_the_poller_message_survives_a_restart(tmp_path):
    """The bridge dies after admitting a message and before its turn finishes.

    Against the pre-inbox bridge the message existed only in an in-memory deque at this
    point, so the restart below had nothing to find. That is the bug.
    """
    root = str(tmp_path / "inbox")

    live = Inbox(root)
    live.record("thread1", "post1", "the message that used to vanish")
    del live                                    # <- the process dies here. No consume ran.

    restarted = Inbox(root)
    pend = restarted.pending("thread1")
    assert [p["post_id"] for p in pend] == ["post1"]
    assert pend[0]["prompt"] == "the message that used to vanish"


def test_kill_the_poller_a_finished_turn_is_not_replayed(tmp_path):
    """The other half: a message whose turn DID finish must not come back from the dead."""
    root = str(tmp_path / "inbox")

    live = Inbox(root)
    live.record("thread1", "post1", "already handled")
    live.mark_consumed("thread1", "post1")      # the turn finished
    del live

    assert Inbox(root).pending("thread1") == []


def test_kill_the_poller_replay_is_idempotent_across_many_restarts(tmp_path):
    """N restarts with no intervening turn leave the message pending exactly ONCE.

    A replay that re-recorded what it replayed would grow the backlog on every bounce and
    run the same turn N times.
    """
    root = str(tmp_path / "inbox")
    Inbox(root).record("thread1", "post1", "still owed")

    for _ in range(5):
        box = Inbox(root)
        pend = box.pending("thread1")
        assert len(pend) == 1, "a restart duplicated the pending message"
        del box

    assert len(Inbox(root).pending("thread1")) == 1


def test_kill_the_poller_mixed_finished_and_unfinished(tmp_path):
    """Realistic crash: three admitted, one finished, then the process dies."""
    root = str(tmp_path / "inbox")
    live = Inbox(root)
    for i in range(3):
        live.record("thread1", f"post{i}", f"msg{i}")
    live.mark_consumed("thread1", "post1")
    del live

    assert [p["post_id"] for p in Inbox(root).pending("thread1")] == ["post0", "post2"]


# ── the wiring, not just the module ──────────────────────────────────────────

def _bridge_source() -> str:
    with open(os.path.join(_HERE, "bridge.py"), encoding="utf-8") as fh:
        return fh.read()


def test_bridge_records_before_it_admits():
    """The ordering IS the fix, so it is asserted rather than trusted.

    `record()` must appear BEFORE `put_user()` at the admission site: everything after that
    call is inside the pass that persists `last_seen`, so recording later would put the
    message back in RAM-only territory for the length of a turn.
    """
    src = _bridge_source()
    rec = src.index("self.inbox.record(thread_root, pid, msg)")
    admit = src.index('self.queues[thread_root].put_user(_Item("user", msg, pid))')
    assert rec < admit, "the inbox record must precede the in-memory admission"


def test_bridge_consumes_in_a_finally_not_at_dequeue():
    """Consumption must be tied to the turn ENDING.

    Marking consumed where the item is dequeued would look equivalent and would silently
    reopen the original hole one layer down, so the `finally:` placement is asserted.
    """
    src = _bridge_source()
    idx = src.index("self.inbox.mark_consumed(thread_root, item.post_id)\n                stop_evt.set()")
    before = src[:idx]
    assert before.rstrip().endswith("finally:") or "finally:" in before[-400:], \
        "mark_consumed must sit in the worker's finally block"


def test_bridge_replays_on_start():
    src = _bridge_source()
    assert "def replay_inbox(self)" in src
    assert "self.replay_inbox()" in src, "replay_inbox is defined but never called"


def test_inbox_lives_under_the_bridge_state_dir():
    src = _bridge_source()
    assert 'INBOX_DIR = os.path.join(STATE_DIR, "inbox")' in src, \
        "the inbox must follow BRIDGE_STATE_DIR, or tests and prod disagree about where it is"
