"""Admission scheduler — the core invariants of design §3.1 / §8b / §8c / §8d."""

import pytest

from llm_queue.models import Cancelled, Rejected


async def test_admits_up_to_n_then_holds(make_queue, make_waiter):
    mq = make_queue(slots=3, max_in_flight=4, backstop_depth=24)
    waiters = [make_waiter() for _ in range(6)]
    for w in waiters:
        await mq.enqueue(w)
    # N=4 dispatched immediately; the rest HELD (not dropped).
    dispatched = [w for w in waiters if w.dispatched]
    waiting = [w for w in waiters if not w.dispatched]
    assert len(dispatched) == 4
    assert len(waiting) == 2


async def test_release_dispatches_next(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1)
    w1, w2 = make_waiter(), make_waiter()
    await mq.enqueue(w1)
    await mq.enqueue(w2)
    assert w1.dispatched and not w2.dispatched
    await mq.release(w1)
    # The freed permit dispatches the next waiter.
    assert w2.dispatched
    await mq.await_dispatch(w2)  # returns immediately


async def test_priority_beats_fifo(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1)
    running = make_waiter(rank=2)
    await mq.enqueue(running)  # takes the only permit
    low = make_waiter(rank=2)  # enqueued FIRST
    high = make_waiter(rank=0)  # enqueued SECOND but higher priority
    await mq.enqueue(low)
    await mq.enqueue(high)
    assert not low.dispatched and not high.dispatched
    await mq.release(running)
    # Higher priority jumps ahead despite arriving later (§8c).
    assert high.dispatched and not low.dispatched


async def test_depth_backstop_rejects(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1, backstop_depth=2)
    running = make_waiter()
    await mq.enqueue(running)  # dispatched, not counted in waiting depth
    await mq.enqueue(make_waiter())  # waiting depth 1
    await mq.enqueue(make_waiter())  # waiting depth 2
    with pytest.raises(Rejected) as exc:
        await mq.enqueue(make_waiter())  # depth would be 3 > backstop 2
    assert exc.value.type == "queue_over_depth"
    assert exc.value.status_code == 429
    assert exc.value.retry_after_s >= 1


async def test_budget_gate_rejects_when_enforced(make_queue, make_waiter):
    # enforce_budget ON; tiny budget so a backed-up queue rejects (§8b).
    mq = make_queue(slots=1, max_in_flight=1, enforce_budget=True, t_initial_s=60.0)
    await mq.enqueue(make_waiter(acceptable=600))  # running
    await mq.enqueue(make_waiter(acceptable=600))  # waiting, ok budget
    # A short-budget interactive request projects a long wait → honest reject.
    with pytest.raises(Rejected) as exc:
        await mq.enqueue(make_waiter(acceptable=10))
    assert exc.value.type == "queue_over_budget"
    assert exc.value.projected_wait_s > 10


async def test_budget_not_enforced_by_default(make_queue, make_waiter):
    # P1 default: enforce_budget OFF → only the depth backstop gates.
    mq = make_queue(slots=1, max_in_flight=1, enforce_budget=False, t_initial_s=600.0)
    await mq.enqueue(make_waiter(acceptable=1))  # running
    # Would be way over a 1s budget, but P1 admits it (queues it).
    w = make_waiter(acceptable=1)
    await mq.enqueue(w)
    assert not w.dispatched  # queued, NOT rejected


async def test_per_key_max_concurrency(make_queue, make_waiter):
    mq = make_queue(slots=2, max_in_flight=2)
    a1 = make_waiter(key="ob-entity", rank=3, max_concurrency=1)
    a2 = make_waiter(key="ob-entity", rank=3, max_concurrency=1)
    b1 = make_waiter(key="ob-other", rank=3)
    await mq.enqueue(a1)  # ob-entity inflight=1
    await mq.enqueue(a2)  # ob-entity at cap → must be skipped
    await mq.enqueue(b1)  # different key → dispatched into the 2nd permit
    assert a1.dispatched and b1.dispatched
    assert not a2.dispatched  # capped caller can't own both slots (§8d)
    # When a1 finishes, a2 becomes eligible.
    await mq.release(a1)
    assert a2.dispatched


async def test_cancel_waiting_does_not_burn_permit(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1)
    running = make_waiter()
    waiting = make_waiter()
    await mq.enqueue(running)
    await mq.enqueue(waiting)
    assert await mq.cancel_waiting(waiting) is True
    with pytest.raises(Cancelled):
        await mq.await_dispatch(waiting)
    # Permit was never consumed by the cancelled waiter; releasing the runner
    # leaves a free permit (nothing else queued).
    await mq.release(running)
    snap = mq.snapshot()
    assert snap["permits_free"] == 1
    assert snap["waiting"] == []


async def test_per_model_backstop_and_budget_override(make_settings, make_waiter):
    # Embed-style queue: generous backstop + budget disabled even though the
    # global settings enforce a tiny budget (regression-safe for embeddings, P4).
    from llm_queue.scheduler import ModelQueue

    s = make_settings(enforce_budget=True, t_initial_s=999.0)
    mq = ModelQueue(
        "bge-m3", slots=2, max_in_flight=3, settings=s, backstop_depth=256, enforce_budget=False
    )
    # Would be wildly over any budget, but the embed queue doesn't gate on budget.
    for _ in range(30):  # > the global backstop (24) but < embed backstop (256)
        w = make_waiter(acceptable=1)
        await mq.enqueue(w)  # must NOT raise (budget off, generous backstop)
    snap = mq.snapshot()
    assert len(snap["waiting"]) == 30 - 3  # 3 dispatched, rest queued, none rejected


async def test_estimate_wait_math(make_queue, make_waiter):
    # P=3, T=5 → 24 ahead → ceil(24/3)*5 = 40s (the §8b worked example).
    mq = make_queue(slots=3, max_in_flight=3, t_initial_s=5.0)
    # 3 running + 21 waiting = 24 ahead of a new same-rank arrival.
    for _ in range(24):
        await mq.enqueue(make_waiter(rank=2))
    est = mq.estimate_wait(rank=2)
    assert est == 40.0


async def test_t_updates_from_completions(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1, t_initial_s=30.0, t_window=5, t_trim_outlier=False)
    import time as _t

    w = make_waiter()
    await mq.enqueue(w)
    # Simulate a 2s upstream processing time.
    w.started_monotonic = _t.monotonic() - 2.0
    await mq.release(w, record_duration=True)
    assert 1.5 < mq.avg_t < 2.5  # T now reflects the measured completion
