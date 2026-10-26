"""Tester t-llmq own scenarios (llmq-research-share attempt 1). In-process; stub upstream = an
asyncio.Semaphore of P lanes (llama.cpp --parallel P), never a live service."""
import asyncio
import itertools
import time
import uuid

import pytest

from llm_queue.models import Rejected
from llm_queue.policy import build_policy
from llm_queue.scheduler import Waiter

POL = build_policy("", 120.0)
_s = itertools.count(10_000)


def mk(key):
    return Waiter(id=uuid.uuid4().hex[:8], key=key, model="m", cls=POL.classify(key), seq=next(_s))


class Stub:
    """P real lanes; records how long each request waited before it actually computed."""

    def __init__(self, mq, lanes):
        self.mq, self.lanes = mq, asyncio.Semaphore(lanes)
        self.started, self.maxrun, self.run = {}, 0, 0

    async def call(self, key, dur, tag=None):
        w = mk(key)
        t0 = time.monotonic()
        await self.mq.enqueue(w)
        await self.mq.await_dispatch(w)
        try:
            async with self.lanes:
                self.started[tag or w.id] = time.monotonic() - t0
                self.run += 1
                self.maxrun = max(self.maxrun, self.run)
                try:
                    await asyncio.sleep(dur)
                finally:
                    self.run -= 1
        finally:
            await self.mq.release(w)
        return w


# ---- T1 slots 1..4 ----
@pytest.mark.parametrize("slots", [1, 2, 3, 4])
async def test_t1_slots_idle_fill(make_queue, slots):
    mq = make_queue(slots=slots, max_in_flight=slots)
    rs = [mk(k) for k in ("ob-research", "ob-digest", "ob-research", "ob-digest", "ob-research")]
    for r in rs:
        await mq.enqueue(r)
    assert sum(r.dispatched for r in rs) == slots  # idle: research/batch fills every slot


@pytest.mark.parametrize("slots", [2, 3, 4])
async def test_t1_slots_chat_present_n_eq_p(make_queue, slots):
    mq = make_queue(slots=slots, max_in_flight=slots)
    c = mk("owui-chat")
    await mq.enqueue(c)
    rs = [mk(k) for k in ("ob-research", "ob-digest", "ob-research", "ob-digest")]
    for r in rs:
        await mq.enqueue(r)
    assert mq._permits == 1  # exactly one permit left for a chat
    c2 = mk("owui-chat")
    await mq.enqueue(c2)
    assert c2.dispatched and mq._permits == 0


async def test_t1_one_slot_order_and_progress(make_queue):
    mq = make_queue(slots=1, max_in_flight=1)
    r1 = mk("ob-research")
    await mq.enqueue(r1)
    r2 = mk("ob-research")
    c = mk("owui-chat")
    await mq.enqueue(r2)
    await mq.enqueue(c)
    await mq.release(r1)
    assert c.dispatched and not r2.dispatched  # priority order
    await mq.release(c)
    assert r2.dispatched  # no reservation, no deadlock


# ---- T2 the LIVE shape N = P+1 (inference/.env SLOTS=2, MAX_IN_FLIGHT=3; defaults 3/4) ----
@pytest.mark.parametrize("slots,n", [(2, 2), (2, 3), (3, 4)])
async def test_t2_chat_in_hold_window_lane_free(make_queue, slots, n):
    """A chat finished within H (reservation active); research arrives; a chat then arriving
    must not wait for a research call at the upstream's P lanes (clause 1)."""
    mq = make_queue(slots=slots, max_in_flight=n)
    st = Stub(mq, slots)
    await st.call("owui-chat", 0.01, "warm")  # sets the hold window
    tasks = [asyncio.create_task(st.call("ob-research" if i % 2 else "ob-digest", 1.0, f"r{i}"))
             for i in range(4)]
    await asyncio.sleep(0.1)
    research_running = sum(1 for w in mq._running.values() if w.rank >= 3)
    await st.call("owui-chat", 0.01, "chat")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    print(f"\nT2 slots={slots} N={n}: research in flight {research_running}, "
          f"chat waited {st.started['chat']:.2f}s for a lane")
    assert st.started["chat"] < 0.5, (
        f"chat waited {st.started['chat']:.2f}s behind research at the upstream lanes")


# ---- T3 burst of 5 chats while idle research holds every permit ----
async def test_t3_burst_five_chats(make_queue):
    mq = make_queue(slots=2, max_in_flight=2, enforce_budget=True, t_initial_s=100.0)
    r1, r2 = mk("ob-research"), mk("ob-podcast")
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    r3 = mk("research-branch")
    await mq.enqueue(r3)
    chats, refused = [], []
    for i in range(5):
        c = mk("owui-chat")
        try:
            await mq.enqueue(c)
            chats.append(c)
        except Rejected as e:
            refused.append((i + 1, round(e.projected_wait_s)))
    print(f"\nT3 admitted {len(chats)}, refused (n, est_s) {refused}")
    await mq.release(r1)
    assert chats[0].dispatched and not r3.dispatched
    await mq.release(r2)
    assert chats[1].dispatched and not r3.dispatched
    for c in chats[:2]:
        await mq.release(c)
    assert all(c.dispatched for c in chats[2:])


# ---- T4 chat at the H boundary (default H = 5 s) ----
@pytest.mark.parametrize("after,expect_immediate", [(4.9, True), (5.1, False)])
async def test_t4_h_boundary(make_queue, after, expect_immediate):
    mq = make_queue(slots=2, max_in_flight=2)
    assert mq._settings.reserve_hold_s == 5.0
    c0 = mk("owui-chat")
    await mq.enqueue(c0)
    await mq.release(c0)
    r1, r2, r3 = mk("ob-research"), mk("ob-research"), mk("ob-digest")
    for r in (r1, r2, r3):
        await mq.enqueue(r)
    assert r1.dispatched and not r2.dispatched
    await asyncio.sleep(after)
    c = mk("owui-chat")
    await mq.enqueue(c)
    print(f"\nT4 after {after}s: r2 dispatched={r2.dispatched} chat dispatched={c.dispatched}")
    assert c.dispatched is expect_immediate
    if not expect_immediate:
        assert r2.dispatched  # idle-reclaim after H
        await mq.release(r1)
        assert c.dispatched and not r3.dispatched  # one completion, chat next
    assert len(mq._running) <= 2


# ---- T5 rank-1/2 callers ----
@pytest.mark.parametrize("hi", ["ob-mcp", "llama", "ollama"])
async def test_t5_rank12_reservation(make_queue, hi):
    mq = make_queue(slots=2, max_in_flight=2)
    h1 = mk(hi)
    await mq.enqueue(h1)
    assert h1.rank in (1, 2)
    r1, r2 = mk("ob-research"), mk("ob-digest")
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert not r1.dispatched and not r2.dispatched  # only the last permit is free: held
    h2 = mk(hi)
    await mq.enqueue(h2)
    assert h2.dispatched


# ---- T6 research-branch + ob-research + ob-podcast never exceed the class cap (2) ----
async def test_t6_class_cap_under_churn(make_queue):
    mq = make_queue(slots=6, max_in_flight=7)
    st = Stub(mq, 6)
    peak = 0

    async def watch():
        nonlocal peak
        while True:
            peak = max(peak, sum(1 for w in mq._running.values() if w.cls.name == "ob-research"))
            await asyncio.sleep(0.002)

    wt = asyncio.create_task(watch())
    keys = ["ob-research", "ob-podcast", "research-branch"] * 4
    await asyncio.gather(*(st.call(k, 0.03 + 0.01 * (i % 3)) for i, k in enumerate(keys)))
    wt.cancel()
    print(f"\nT6 class peak in flight {peak}")
    assert peak == 2 and mq._inflight_by_class == {} and mq._permits == 7


# ---- T7 research not starved: chat every 4 s for 60 s, slots=2 (time scaled x0.1) ----
@pytest.mark.parametrize("chat_dur", [0.1, 0.6])  # 1 s chats; 6 s chats (overlapping)
async def test_t7_research_progress(make_queue, chat_dur):
    mq = make_queue(slots=2, max_in_flight=2)
    mq._settings.reserve_hold_s = 0.5
    st = Stub(mq, 2)
    done = 0
    stop = False

    async def research_loop():
        nonlocal done
        while not stop:
            await st.call("ob-research", 0.3)
            done += 1

    rl = [asyncio.create_task(research_loop()) for _ in range(2)]
    await asyncio.sleep(0.01)
    chats = []
    for _ in range(15):
        chats.append(asyncio.create_task(st.call("owui-chat", chat_dur)))
        await asyncio.sleep(0.4)
    stop = True
    await asyncio.gather(*chats)
    await asyncio.wait_for(asyncio.gather(*rl), 10)
    print(f"\nT7 chat_dur={chat_dur}: research completions in 6 s = {done}")
    assert done >= 3, "research starved"


# ---- T8 client disconnect of the reserved-for chat ----
async def test_t8_disconnect_waiting_chat(make_queue):
    mq = make_queue(slots=2, max_in_flight=2)
    r1, r2 = mk("ob-research"), mk("ob-research")
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    c = mk("owui-chat")
    await mq.enqueue(c)
    r3 = mk("ob-digest")
    await mq.enqueue(r3)
    assert await mq.cancel_waiting(c)
    await mq.release(r1)
    assert r3.dispatched  # no phantom reservation for the departed chat


async def test_t8b_disconnect_running_chat_then_hold_expires(make_queue):
    mq = make_queue(slots=2, max_in_flight=2)
    mq._settings.reserve_hold_s = 0.3
    c = mk("owui-chat")
    await mq.enqueue(c)
    r1, r2 = mk("ob-research"), mk("ob-digest")
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert not r1.dispatched
    await mq.release(c, record_duration=False)  # disconnect mid-stream -> release path
    await asyncio.wait_for(asyncio.gather(mq.await_dispatch(r1), mq.await_dispatch(r2)), 2.0)


# ---- T9 hold-timer wake-up never double-dispatches ----
async def test_t9_kick_no_double_dispatch(make_queue):
    mq = make_queue(slots=2, max_in_flight=2)
    mq._settings.reserve_hold_s = 0.2
    c = mk("owui-chat")
    await mq.enqueue(c)
    await mq.release(c)
    rs = [mk("ob-digest") for _ in range(2)] + [mk("ob-wiki") for _ in range(4)]
    for r in rs:
        await mq.enqueue(r)
    orig = mq._try_dispatch_locked
    bad = []

    def spy():
        orig()
        if mq._permits < 0 or len(mq._running) > 2:
            bad.append((mq._permits, len(mq._running)))

    mq._try_dispatch_locked = spy
    await asyncio.sleep(0.19)
    first = next(r for r in rs if r.dispatched)
    await mq.release(first)  # races the timer
    c2 = mk("owui-chat")
    await mq.enqueue(c2)
    await mq.release(c2)  # extends the hold while a kick is pending
    await asyncio.sleep(0.5)
    print(f"\nT9 running {len(mq._running)} permits {mq._permits} bad {bad}")
    assert not bad and len(mq._running) == 2 and mq._permits == 0
    assert sum(1 for r in rs if r.dispatched) == 3  # first (released) + 2 running
