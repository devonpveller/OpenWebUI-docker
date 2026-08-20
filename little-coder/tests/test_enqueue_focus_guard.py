"""enqueue must never spawn an agent onto a VOID workspace.

The in-memory focus record can outlive the actual clone on disk (they diverge). Live 2026-07-13: a
corrupt leftover workspace from a prior incident (source tree present but no `.git`) left
`current_focus` SET while the disk held no real repo; a busy dry-run made `switch_project` NOOP
instead of re-cloning, the coding task then ran on the void, "finished" having changed nothing (it
could not branch/commit), and the PM monitor froze the effort as a hard-gate deviation. `enqueue`
now mirrors run_check/add_submodule: a VALID focus = an in-memory record AND a real clone on disk
(`is_focused()`), so a lying focus record is rejected (409) and the bridge re-focuses fresh."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from littlecoder.daemon import LittleCoderDaemon, TriggerRequest


def _enqueue_daemon(*, focus_record: bool, on_disk: bool) -> LittleCoderDaemon:
    d = object.__new__(LittleCoderDaemon)          # unit-scope the enqueue seam — no full boot
    d.draining = False
    d.current_focus = (SimpleNamespace(canonical_url="https://github.com/x/y")
                       if focus_record else None)
    d.workspace = SimpleNamespace(is_focused=lambda: on_disk)
    return d


def test_enqueue_rejects_when_focus_record_lies_about_a_void_workspace():
    # The exact 2026-07-13 incident: the record SAYS focused, the disk is a void (no clone).
    d = _enqueue_daemon(focus_record=True, on_disk=False)
    with pytest.raises(HTTPException) as ei:
        d.enqueue(TriggerRequest(prompt="port the backend", channel="cli"))
    assert ei.value.status_code == 409


def test_enqueue_rejects_when_there_is_no_focus_record_at_all():
    d = _enqueue_daemon(focus_record=False, on_disk=False)
    with pytest.raises(HTTPException) as ei:
        d.enqueue(TriggerRequest(prompt="x", channel="cli"))
    assert ei.value.status_code == 409


def test_enqueue_passes_the_focus_guard_when_record_and_clone_agree():
    # A genuinely-focused workspace clears the guard — proven by getting PAST it to the next
    # validation (empty-prompt 422), not the 409 focus rejection. Keeps the seam tiny (no TaskState
    # machinery) while proving the happy path is not broken by the new is_focused() condition.
    d = _enqueue_daemon(focus_record=True, on_disk=True)
    with pytest.raises(HTTPException) as ei:
        d.enqueue(TriggerRequest(prompt="   ", channel="cli"))
    assert ei.value.status_code == 422        # empty prompt — i.e. it got PAST the focus guard
