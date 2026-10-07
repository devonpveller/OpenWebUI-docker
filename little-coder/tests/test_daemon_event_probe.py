"""ao-wd-offset: the /events probe reads O(new bytes), not the whole journal, and keeps the
line-offset semantics (events = lines[offset:], next_offset = offset + len(events))."""

from __future__ import annotations

import builtins
import threading

from littlecoder.daemon import read_event_lines


def _write(p, n, start=0):
    with open(p, "ab") as fh:
        for i in range(start, start + n):
            fh.write(f'{{"i":{i}}}\n'.encode())


def test_semantics_match_full_read(tmp_path):
    p = str(tmp_path / "ev.jsonl")
    _write(p, 10)
    cache: dict = {}
    assert len(read_event_lines(p, 0, cache)) == 10
    assert read_event_lines(p, 10, cache) == []
    _write(p, 5, 10)
    got = read_event_lines(p, 10, cache)
    assert got == [f'{{"i":{i}}}' for i in range(10, 15)]
    assert read_event_lines(p, 3, cache)[0] == '{"i":3}'       # behind the cache: full read, correct
    assert read_event_lines(p, 99, cache) == []                 # past EOF: empty


def test_partial_trailing_line_not_counted_twice(tmp_path):
    p = str(tmp_path / "ev.jsonl")
    _write(p, 2)
    with open(p, "ab") as fh:
        fh.write(b'{"i":2')                                     # writer mid-line
    cache: dict = {}
    assert read_event_lines(p, 0, cache) == ['{"i":0}', '{"i":1}']   # unterminated tail not returned
    assert cache[p][0] == 2
    with open(p, "ab") as fh:
        fh.write(b'}\n')
    assert read_event_lines(p, 2, cache) == ['{"i":2}']


def test_probe_reads_only_new_bytes(tmp_path, monkeypatch):
    p = str(tmp_path / "ev.jsonl")
    _write(p, 20000)
    cache: dict = {}
    read_event_lines(p, 0, cache)                               # prime
    _write(p, 7, 20000)
    new_bytes = sum(len(f'{{"i":{i}}}\n') for i in range(20000, 20007))
    total = 0
    real_open = builtins.open

    class Spy:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): self.fh.__enter__(); return self
        def __exit__(self, *a): return self.fh.__exit__(*a)
        def fileno(self): return self.fh.fileno()
        def seek(self, *a): return self.fh.seek(*a)
        def readlines(self):
            nonlocal total
            out = self.fh.readlines(); total += sum(len(x) for x in out); return out

    monkeypatch.setattr("littlecoder.daemon.open", lambda *a, **k: Spy(real_open(*a, **k)), raising=False)
    got = read_event_lines(p, 20000, cache)
    assert len(got) == 7 and total == new_bytes                # O(new bytes), not the 20k-line file


def test_split_readlines_keeps_cache_on_a_line_boundary(tmp_path, monkeypatch):
    """A journal appended during the read can make readlines() return one line split in two.
    Only the leading complete lines may be counted; the cached byte position must stay on a line
    boundary (it used to land mid-line: cache (6,44) vs true (6,48))."""
    p = str(tmp_path / "ev.jsonl")
    _write(p, 6)
    real_open = builtins.open

    class Split:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): self.fh.__enter__(); return self
        def __exit__(self, *a): return self.fh.__exit__(*a)
        def fileno(self): return self.fh.fileno()
        def seek(self, *a): return self.fh.seek(*a)
        def readlines(self):
            out = self.fh.readlines()
            head, tail = out[3][:4], out[3][4:]          # line 3 seen as an unterminated head + its tail
            return out[:3] + [head, tail] + out[4:]

    cache: dict = {}
    monkeypatch.setattr("littlecoder.daemon.open", lambda *a, **k: Split(real_open(*a, **k)), raising=False)
    got = read_event_lines(p, 0, cache)
    assert got == ['{"i":0}', '{"i":1}', '{"i":2}']
    true_pos = sum(len(f'{{"i":{i}}}\n') for i in range(3))
    assert cache[p] == (3, true_pos)
    monkeypatch.undo()
    assert read_event_lines(p, 3, cache) == [f'{{"i":{i}}}' for i in range(3, 6)]


def test_concurrent_appends_never_misalign(tmp_path):
    """Writer appends each line in TWO writes while a poller follows next_offset. Over several
    runs the poller must see every line exactly once, in order (drift 0)."""
    for run in range(4):
        p = str(tmp_path / f"ev{run}.jsonl")
        open(p, "wb").close()
        n = 1500
        done = threading.Event()

        def writer():
            with open(p, "ab", buffering=0) as fh:
                for i in range(n):
                    line = f'{{"i":{i}}}\n'.encode()
                    fh.write(line[:3]); fh.write(line[3:])
            done.set()

        t = threading.Thread(target=writer); t.start()
        cache: dict = {}
        seen: list[str] = []
        while True:
            finished = done.is_set()
            ev = read_event_lines(p, len(seen), cache)
            seen.extend(ev)
            if finished and not ev:
                break
        t.join()
        assert seen == [f'{{"i":{i}}}' for i in range(n)], f"run {run} drifted"


def test_eviction_is_thread_safe(tmp_path):
    paths = []
    for i in range(80):
        q = str(tmp_path / f"f{i}.jsonl"); _write(q, 2); paths.append(q)
    cache: dict = {}
    errs: list[BaseException] = []

    def work(chunk):
        try:
            for _ in range(20):
                for q in chunk:
                    read_event_lines(q, 0, cache)
        except BaseException as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(paths[i::4],)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and len(cache) <= 64
