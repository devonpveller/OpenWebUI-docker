import itertools
import uuid

import pytest

from llm_queue.config import Settings
from llm_queue.policy import PriorityClass
from llm_queue.scheduler import ModelQueue, Waiter

_seq = itertools.count()


@pytest.fixture
def make_settings():
    def _make(**over):
        s = Settings()  # all fields have defaults; no env needed
        for k, v in over.items():
            setattr(s, k, v)
        return s

    return _make


@pytest.fixture
def make_queue(make_settings):
    def _make(slots=2, max_in_flight=2, **over):
        s = make_settings(slots=slots, max_in_flight=max_in_flight, **over)
        return ModelQueue("qwen36-27b", slots=slots, max_in_flight=max_in_flight, settings=s)

    return _make


@pytest.fixture
def make_waiter():
    def _make(key="anon", rank=2, acceptable=120.0, max_concurrency=None):
        cls = PriorityClass("c", rank, acceptable, max_concurrency)
        return Waiter(
            id=uuid.uuid4().hex[:8], key=key, model="qwen36-27b", cls=cls, seq=next(_seq)
        )

    return _make
