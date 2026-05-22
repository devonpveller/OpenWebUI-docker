"""ULID minting (design §4.2)."""

from littlecoder.ulid import is_ulid, new_ulid


def test_ulid_shape():
    u = new_ulid()
    assert len(u) == 26
    assert is_ulid(u)


def test_ulid_is_time_sortable():
    early = new_ulid(ts_ms=1_000_000_000_000)
    late = new_ulid(ts_ms=2_000_000_000_000)
    assert early < late


def test_ulid_unique():
    assert len({new_ulid() for _ in range(2000)}) == 2000


def test_is_ulid_rejects_junk():
    assert not is_ulid("")
    assert not is_ulid("not-a-ulid")
    assert not is_ulid("I" * 26)  # I is not in the Crockford alphabet
