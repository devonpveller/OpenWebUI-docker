"""P12 — intake classification: a build request must not be read as a bug report.

gym-010 (2026-07-19) was a greenfield feature build on a single-commit template arena. The org
appended its full error-report protocol to it — "THIS IS A RUNTIME / BEHAVIORAL SYMPTOM …
REPRODUCE the symptom … BEFORE: FAIL — the failing run's evidence on the unfixed code" — plus
three PRIOR ATTEMPTS at an error that never existed. Two independent classifiers misfired on
ordinary English and their outputs compounded.

See docs/log/P12-intake-classification.md. Fakes only.
"""

from __future__ import annotations

import inspect
import re

from app.orchestrator import Orchestrator, _ERROR_REPORT_RE

# The exact prose that misclassified, lifted from the stored gym-010 objective.
GYM_010 = (
    "start a new effort gym-010 todo-product on the gym project. Take the\n"
    "existing python todo CLI and turn it into a POLISHED, FINAL-QUALITY todo\n"
    "application that a real person would enjoy using. Beyond add/list/done,\n"
    "deliver a full feature set: delete a todo, edit a todo's text, priority\n"
    "levels (low/medium/high), due dates, search by text, and filters (by\n"
    "status, by priority, and due-before a date), plus a summary/stats view and\n"
    "a clear-completed action. Give it a real UI/UX: an interactive REPL mode -\n"
    "final-product quality bar: every command exposes clear --help/usage, all\n"
    "inputs are validated with helpful error messages, the data file is written\n"
    "atomically and never corrupts or crashes on malformed or missing-field data,\n"
    "and the code is documented (module and function docstrings, type hints, and\n"
    "an updated README with usage examples). IMPORTANT - you are a HEADLESS worker\n"
    "with NO interactive terminal: NEVER launch a blocking foreground process to\n"
    "test the app (no curses TUI, no foreground web server, no REPL awaiting live\n"
    "stdin) - it will hang your turn. Verify everything non-interactively.\n"
)


def _live_sig():
    """The SHIPPED `_sig`, lifted out of `_attempt_history` so these tests can't drift from it."""
    src = inspect.getsource(Orchestrator._attempt_history)
    body = src[src.index("def _sig"):src.index("lines = {")]
    body = "\n".join(l[8:] if l.startswith(" " * 8) else l for l in body.splitlines())
    ns = {"re": re, "_ERROR_REPORT_RE": _ERROR_REPORT_RE}
    exec(body, ns)  # noqa: S102 - executing our own source, deliberately
    return ns["_sig"]


# ── P12.1 — a symptom must be REPORTED, not FORBIDDEN ────────────────────────
def test_a_build_request_is_not_a_runtime_symptom():
    """The live regression: gym-010 matched exactly two words — `crashes` from "never corrupts or
    crashes" (a REQUIREMENT) and `hang` from "it will hang your turn" (a warning about the WORKER'S
    OWN TURN). One match appended the whole reproduce-the-symptom protocol."""
    o = Orchestrator.__new__(Orchestrator)
    assert o._runtime_symptom_phrase(GYM_010) is None


def test_a_forbidden_symptom_is_not_a_reported_one():
    o = Orchestrator.__new__(Orchestrator)
    for text in (
        "the data file must never crash on malformed input",
        "the store should not hang when two runs collide",
        "validate input so the app cannot crash on a bad date",
        "write atomically and never corrupt or crash on missing fields",
    ):
        assert o._runtime_symptom_phrase(text) is None, text


def test_a_warning_about_the_workers_own_turn_is_not_a_product_defect():
    o = Orchestrator.__new__(Orchestrator)
    for text in (
        "do not launch a blocking foreground process - it will hang your turn",
        "never await live stdin; it will hang your session",
    ):
        assert o._runtime_symptom_phrase(text) is None, text


def test_a_genuine_runtime_report_still_classifies():
    """The true-positive guard. These paths exist because real re-reported runtime symptoms were
    being 'proven fixed' by a green build — do not weaken them."""
    o = Orchestrator.__new__(Orchestrator)
    for text in (
        "the editor crashes when I click the toolbar button",
        "the app hangs on launch and shows a black screen",
        "nothing happens when the user presses the export button",
        "it throws an unhandled exception at runtime",
    ):
        assert o._runtime_symptom_phrase(text) is not None, text


# ── P12.2 — a signature line must look like TOOL OUTPUT ──────────────────────
def test_prose_with_slashes_is_not_an_error_signature():
    """8 of 23 gym-010 lines qualified as 'error signatures' because the old test accepted any long
    line containing an apostrophe or a slash. English prose is full of both."""
    sig = _live_sig()
    hits = [l.strip() for l in GYM_010.splitlines() if sig(l)]
    assert hits == [], hits


def test_real_tool_output_is_still_an_error_signature():
    sig = _live_sig()
    for line in (
        "Program.cs(42,17): error CS0246: The type or namespace could not be found",
        'File "/app/todo.py", line 88, in cmd_add',
        "  at Murder.Core.Grid.GetCell(Int32 x, Int32 y) in C:/src/Grid.cs:line 52",
        # bare MSBuild code, no "error" word, and "was not found" is NOT in _ERROR_REPORT_RE —
        # this is the class of failure this org hits most often
        "MSB3202: The project file /src/vendor/murder/Murder.csproj was not found",
        "ERROR: Cannot find module './lib/parser' in the build output",
    ):
        assert sig(line), line


def test_short_lines_are_never_signatures():
    sig = _live_sig()
    assert not sig("error CS0246")          # under the 30-char distinctiveness floor
