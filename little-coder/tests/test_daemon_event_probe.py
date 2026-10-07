"""ao-wd-offset: the /events probe reads O(new bytes), not the whole journal, and keeps the
line-offset semantics (events = lines[offset:], next_offset = offset + len(events))."""

from __future__ import annotations

import builtins

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
    assert read_event_lines(p, 0, cache)[-1] == '{"i":2'
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
