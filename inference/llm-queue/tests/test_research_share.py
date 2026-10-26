"""llmq-research-share: priority reservation (last permit) + class-level research cap."""

import asyncio

from llm_queue.policy import build_policy


async def test_reservation_chat_finds_permit_while_research_runs(make_queue, make_waiter):
    # 3 slots, N=4. A chat call runs: research may take one more lane, the last REAL lane is
    # held, so a second chat arrival dispatches at once.
    mq = make_queue(slots=3, max_in_flight=4)
    chat1 = make_waiter(key="owui-chat", rank=0)
    await mq.enqueue(chat1)
    r1, r2 = make_waiter(key="a", rank=3), make_waiter(key="b", rank=3)
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert chat1.dispatched and r1.dispatched and not r2.dispatched  # last lane held
    await mq.release(r1)
    assert r2.dispatched
    chat2 = make_waiter(key="owui-chat", rank=0)
    await mq.enqueue(chat2)
    assert chat2.dispatched and chat2.wait_seconds < 0.5
    assert len(mq.snapshot()["running"]) <= 3  # never above the real lanes here


async def test_reservation_hold_window_after_chat_finishes(make_queue, make_waiter):
    mq = make_queue(slots=2, max_in_flight=2)
    mq._settings.__dict__['reserve_hold_s'] = 0.2  # absent on old code: RED by behaviour
    chat = make_waiter(rank=0)
    await mq.enqueue(chat)
    await mq.release(chat)
    r1, r2 = make_waiter(rank=3), make_waiter(rank=3)
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert r1.dispatched and not r2.dispatched  # inside H
    await asyncio.wait_for(mq.await_dispatch(r2), 2.0)  # H ends, timer wakes it (no deadlock)
    assert r2.dispatched


async def test_idle_research_uses_every_slot(make_queue, make_waiter):
    mq = make_queue(slots=2, max_in_flight=2)
    r1, r2 = make_waiter(rank=3), make_waiter(rank=3)
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert r1.dispatched and r2.dispatched


async def test_one_slot_no_reservation(make_queue, make_waiter):
    mq = make_queue(slots=1, max_in_flight=1)
    chat = make_waiter(rank=0)
    await mq.enqueue(chat)
    r = make_waiter(rank=3)
    await mq.enqueue(r)
    assert not r.dispatched
    await mq.release(chat)
    assert r.dispatched  # no refused-forever
    mq2 = make_queue(slots=1, max_in_flight=1)
    r2 = make_waiter(rank=3)
    await mq2.enqueue(r2)
    assert r2.dispatched


def test_policy_research_branch_shares_class():
    p = build_policy("", 120.0)
    names = {p.classify(k).name for k in ("ob-research", "ob-podcast", "research-branch")}
    assert names == {"ob-research"}


async def test_class_cap_across_keys(make_queue, make_waiter):
    import uuid

    from llm_queue.scheduler import Waiter

    pol = build_policy("", 120.0)
    mq = make_queue(slots=4, max_in_flight=5)
    ws = []
    for i, k in enumerate(["ob-research", "ob-podcast", "research-branch", "ob-research"]):
        cls = pol.classify(k)
        w = Waiter(id=uuid.uuid4().hex[:8], key=k, model="m", cls=cls, seq=100 + i)
        ws.append(w)
        await mq.enqueue(w)
    assert sum(w.dispatched for w in ws) == 2  # class cap 2 across three keys
    await mq.release(next(w for w in ws if w.dispatched))
    assert sum(w.dispatched for w in ws) == 3 and len(mq.snapshot()["running"]) == 2


def test_policy_json_class_shares_cap():
    p = build_policy(
        '{"x": {"class": "g", "rank": 3, "max_concurrency": 1}, '
        '"y": {"class": "g", "rank": 3, "max_concurrency": 1}}',
        60.0,
    )
    assert p.classify("x").name == p.classify("y").name == "g"


async def test_cold_chat_behind_idle_research_dispatched_next_no_429(make_queue):
    import uuid

    from llm_queue.scheduler import Waiter

    pol = build_policy("", 120.0)
    assert pol.classify("owui-chat").acceptable_wait_s == 120.0
    # budget gate ON; T = 100 s simulates a 100 s research call
    mq = make_queue(slots=2, max_in_flight=2, enforce_budget=True, t_initial_s=100.0)

    def mk(k, seq):
        return Waiter(id=uuid.uuid4().hex[:8], key=k, model="m", cls=pol.classify(k), seq=seq)

    r1, r2 = mk("ob-research", 1), mk("ob-podcast", 2)
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert r1.dispatched and r2.dispatched  # idle: research holds both permits
    r3 = mk("ob-research", 3)  # a LATER research request, waiting
    await mq.enqueue(r3)
    chat = mk("owui-chat", 4)
    await mq.enqueue(chat)  # must not raise Rejected (est 100 s <= 120 s)
    assert not chat.dispatched
    await mq.release(r1)  # ONE completion
    assert chat.dispatched and not r3.dispatched  # chat next, not behind r3


async def test_hold_path_unchanged_with_longer_budget(make_queue, make_waiter):
    mq = make_queue(slots=3, max_in_flight=4)
    chat = make_waiter(key="owui-chat", rank=0)
    await mq.enqueue(chat)
    r1, r2 = make_waiter(rank=3), make_waiter(rank=3)
    await mq.enqueue(r1)
    await mq.enqueue(r2)
    assert r1.dispatched and not r2.dispatched  # last lane held while chat is in flight
    chat2 = make_waiter(key="owui-chat", rank=0)
    await mq.enqueue(chat2)
    assert chat2.dispatched and chat2.wait_seconds < 0.5


import time  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize("slots,n", [(2, 3), (3, 4)])
async def test_real_lane_free_for_chat(make_queue, make_waiter, slots, n):
    """Stub upstream of `slots` lanes (a Semaphore, like llama.cpp --parallel): the chat must
    find a REAL lane free, not merely be dispatched by the queue."""
    mq = make_queue(slots=slots, max_in_flight=n)
    lanes = asyncio.Semaphore(slots)
    waits: dict[str, float] = {}

    async def call(w, dur):
        await mq.enqueue(w)
        await mq.await_dispatch(w)
        t = time.monotonic()
        async with lanes:
            waits[w.id] = time.monotonic() - t
            await asyncio.sleep(dur)
        await mq.release(w)

    warm = make_waiter(key="owui-chat", rank=0)
    await call(warm, 0.0)  # chat finished just now: hold window active
    research = [make_waiter(key=f"r{i}", rank=3) for i in range(slots + 1)]
    tasks = [asyncio.create_task(call(w, 1.0)) for w in research]
    await asyncio.sleep(0.1)
    chat = make_waiter(key="owui-chat", rank=0)
    await call(chat, 0.0)
    assert waits[chat.id] < 0.3, f"chat waited {waits[chat.id]:.2f}s for a real lane"
    await asyncio.gather(*tasks)
