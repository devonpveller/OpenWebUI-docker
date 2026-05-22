"""Minimal ULID generator (design §4.2 — `task_id` minted at trigger).

A ULID is a 48-bit millisecond timestamp followed by 80 bits of randomness,
Crockford-base32 encoded to 26 characters. It is lexicographically sortable by
creation time, which keeps journal scans cheap. No third-party dependency —
the whole thing is ~20 lines.
"""

from __future__ import annotations

import os
import time

# Crockford base32: excludes I, L, O, U to avoid transcription ambiguity.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(ts_ms: int | None = None) -> str:
    """Return a fresh 26-character ULID string."""
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits of randomness
    return _encode(ts_ms, 10) + _encode(rand, 16)


def is_ulid(value: str) -> bool:
    """True if `value` is a syntactically valid ULID."""
    return (
        isinstance(value, str)
        and len(value) == 26
        and all(c in _CROCKFORD for c in value.upper())
    )
