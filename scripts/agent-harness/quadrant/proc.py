"""The ONE way this package runs a subprocess, and why there is only one.

WHAT WENT WRONG. Every call site used `subprocess.run(..., text=True)`. On Windows that
decodes the child's output with the LOCALE codec - cp1252 here - and the things this package
reads are UTF-8: a local model's answer, a container's JSON, a guard's output, a git status
line. Most of the time cp1252 maps the bytes to something wrong but harmless (the first
little-coder transcript recorded a model writing `UnicodeDecodeError` examples as
`ÃœnicÃ¶dÃ©`). Then one byte - 0x9d, which cp1252 does not map at all - made the reader
thread raise inside `subprocess`, leaving `stdout` as `None`. The cell recorded

    ERROR  little-coder x project
           AttributeError: 'NoneType' object has no attribute 'rpartition'

which says nothing about the runner, nothing about the quadrant, and nothing about the
actual fault. A comparison that turns a model's choice of characters into an error record
is measuring the harness.

WHY A CHOKEPOINT AND NOT SIX PATCHES. DECISIONS.md, 2026-08-30, "ENUMERATE-AND-PATCH LOSES":
a guard whose completeness rests on a list of closed routes states a property over ALL
routes while proving it for some. So there is one function, every call site in the package
goes through it, and `test_quadrant.py` proves the completeness by SCANNING the package for
`subprocess.run(` outside this file rather than by keeping a list of files someone has to
remember to extend.

`errors="replace"` rather than `errors="strict"`: the output of a runner under test is data,
not a contract. A byte sequence nobody anticipated must degrade to a visible replacement
character in a transcript, never to an exception that destroys the record around it.
"""

from __future__ import annotations

import subprocess
from typing import Any, List


def run(args: List[str], **kw: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` with capture + UTF-8 decoding forced. Never raises on exit code."""
    kw.setdefault("capture_output", True)
    kw.pop("text", None)
    kw.pop("universal_newlines", None)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(args, **kw)  # noqa: S603 - argv is built by this package
