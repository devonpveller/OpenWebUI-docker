"""Who is calling (PLAN-connectors principle 7: per-caller keys replace the one shared key).

Three kinds of caller:
  screened   a per-caller key from GATE_CALLER_KEYS_FILE. Everything it receives passed intake:
             search results and fetched pages are screened, only what intake labelled clean is
             returned, nonce-fenced and labelled. The only kind /v1/fetch and /v1/web-loader serve.
  legacy     GATEWAY_API_KEY: today's host tools and Tavily clients, unscreened, exactly as before
             this item. Step 5 moves them to a per-caller key and retires this lane.
  anonymous  no key. Refused everywhere, except GET /search while SEARCH_ANONYMOUS_LEGACY is on
             (today's OWUI and research engine, until step 5).

Keys are compared in constant time against EVERY known key (no early exit that would time which
one matched). A key is never logged; the caller's NAME is what the gate records.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MIN_KEY_CHARS = 32


class CallerConfigError(ValueError):
    """The caller-keys file is malformed (the gateway refuses to start on it)."""


@dataclass(frozen=True)
class Caller:
    name: str
    screened: bool

    @property
    def legacy(self) -> bool:
        return not self.screened


LEGACY = "legacy"


def parse_caller_keys(text: str) -> dict[str, str]:
    """'name=key' entries separated by newlines or commas -> {name: key}. Blank lines and lines
    starting with '#' are ignored. A malformed entry, a short key, or a duplicate name or key is
    refused (never silently dropped)."""
    out: dict[str, str] = {}
    seen: set[str] = set()
    for raw in re.split(r"[\n,]", text):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, key = entry.partition("=")
        name, key = name.strip(), key.strip()
        if not sep or not _NAME.fullmatch(name):
            raise CallerConfigError("a caller entry is not 'name=key' with a [a-z0-9._-] name")
        if name == LEGACY:
            raise CallerConfigError("'legacy' is reserved for GATEWAY_API_KEY")
        if len(key) < MIN_KEY_CHARS or not key.isprintable() or " " in key:
            raise CallerConfigError(
                f"caller {name!r}: a key is {MIN_KEY_CHARS}+ printable characters")
        if name in out or key in seen:
            raise CallerConfigError(f"caller {name!r}: duplicate name or key")
        out[name] = key
        seen.add(key)
    return out


class CallerRegistry:
    def __init__(self, screened: dict[str, str], legacy_key: str) -> None:
        if legacy_key in screened.values():
            raise CallerConfigError("a per-caller key equals GATEWAY_API_KEY")
        self._entries: list[tuple[bytes, Caller]] = [
            (k.encode("utf-8"), Caller(n, True)) for n, k in screened.items()
        ]
        if legacy_key:
            self._entries.append((legacy_key.encode("utf-8"), Caller(LEGACY, False)))

    @property
    def screened_names(self) -> list[str]:
        return [c.name for _, c in self._entries if c.screened]

    def identify(self, token: str | None) -> Caller | None:
        """The caller a token belongs to, or None. Constant time over every key."""
        if not token:
            return None
        supplied = token.encode("utf-8", "surrogatepass")
        found: Caller | None = None
        for key, caller in self._entries:
            if hmac.compare_digest(supplied, key) and found is None:
                found = caller
        return found
