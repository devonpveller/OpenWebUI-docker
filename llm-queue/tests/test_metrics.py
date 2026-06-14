from llm_queue.metrics import RollingT


def test_initial_value_before_samples():
    t = RollingT(window=5, initial=30.0)
    assert t.value == 30.0
    assert t.count == 0


def test_mean_of_window():
    t = RollingT(window=5, initial=30.0, trim_outlier=False)
    for v in (10, 20, 30):
        t.record(v)
    assert t.value == 20.0


def test_window_evicts_oldest():
    t = RollingT(window=3, initial=30.0, trim_outlier=False)
    for v in (10, 10, 10, 40, 40, 40):
        t.record(v)
    assert t.value == 40.0  # only last 3 retained


def test_outlier_trimmed_when_enough_samples():
    t = RollingT(window=5, initial=30.0, trim_outlier=True)
    # 9-minute deep-research outlier shouldn't dominate the next estimate (§7.2).
    for v in (10, 10, 10, 540):
        t.record(v)
    assert t.value == 10.0  # 540 dropped, mean of the three 10s


def test_nonpositive_samples_ignored():
    t = RollingT(window=5, initial=30.0)
    t.record(0)
    t.record(-5)
    assert t.count == 0
