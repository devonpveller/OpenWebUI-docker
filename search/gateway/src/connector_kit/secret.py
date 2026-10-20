"""The connector's own intake key: read from a FILE, held redacted.

Intake keys are one per connector (intake's `intake_caller_keys`: `connector=key`); the key a
request presents decides which connector it may submit for. A connector gets ONLY its own key, as
a file (a compose secret mounted read-only), never an environment variable: every `docker exec`
process inherits the environment (intake-i5b R2-F1). The value is never logged, printed, put in
an envelope or in a report; `repr()`/`str()` show `Secret(***)`.
"""
from __future__ import annotations

import os
import re

# intake_service/settings.py _KEY: 32..256 printable ASCII, no blank; ',' separates entries there
_KEY = re.compile(r"[!-~]{32,256}")
MAX_KEY_FILE_BYTES = 4096


class KeyFileError(ValueError):
    """The key file is missing, unreadable or malformed (the message never holds the value)."""


class Secret:
    __slots__ = ("_v",)

    def __init__(self, value: str):
        if not isinstance(value, str) or not _KEY.fullmatch(value) or "," in value:
            raise KeyFileError("an intake key is 32..256 printable ASCII characters, no blank and no ','")
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a Secret is not serialisable")


def load_key_file(path: str | os.PathLike) -> Secret:
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_KEY_FILE_BYTES + 1)
    except OSError as e:
        raise KeyFileError(f"the key file cannot be read ({type(e).__name__})") from None
    if len(data) > MAX_KEY_FILE_BYTES:
        raise KeyFileError("the key file is too large")
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        raise KeyFileError("the key file is not ASCII") from None
    return Secret(text)
