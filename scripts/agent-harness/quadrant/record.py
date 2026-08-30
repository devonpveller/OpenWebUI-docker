"""The run record and the gate it must pass to enter a comparison.

THE IDEA IN ONE LINE: a status field is a CLAIM; artifacts on disk are what make it a
MEASUREMENT. `admit` is where the difference is enforced.

This is A6 turned on the comparison itself. A6's verdict on prose verification is
FALSIFIED - a tester signed off the same false claim three times - and a quadrant record
saying "completed" is precisely a self-report. So a record is admitted only when:

  * its timestamps and wall clock exist and are non-zero (something actually happened);
  * its evidence paths EXIST on the filesystem at admission time (not merely are strings);
  * every acceptance entry carries the command that ran and the exit code it returned, and
    no entry's `passed` contradicts its own exit code;
  * its item digest matches the item the comparison is about;
  * it names the VENUE it ran in, and that venue is the one this results set is a
    comparison over. Same move as the digest, one axis out: "the same anchored item" was
    mechanized while "in the gym" was not, and a run in the wrong place is not a data
    point about the right one however well it was measured. See quadrant/venue.py.

And symmetrically, a `not_run` record is admitted only when it carries a REASON and
carries no acceptance results - because a quadrant that did not run cannot have checked
anything, and a half-populated record is how a non-result acquires a score.

`admit` returns a LIST OF PROBLEMS rather than a bool, matching `anchor_schema.problems`:
two readers agreeing on "invalid" while disagreeing on WHY have already drifted, and the
report prints the reasons to the operator verbatim.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List

from . import matrix as _matrix


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new(q: "_matrix.Quadrant", item: Dict[str, Any], *, venue: Any = None,
        **over: Any) -> Dict[str, Any]:
    """A blank record for a quadrant + item + VENUE. Callers fill it in as the run proceeds.

    `venue` is a `venue.Venue` (or an already-serialised dict). It is a parameter rather
    than something the caller remembers to `.update()` in, because the whole class of defect
    this field addresses is a dimension nobody wrote down.
    """
    rec: Dict[str, Any] = {
        "venue": (venue.as_record() if hasattr(venue, "as_record")
                  else (dict(venue) if isinstance(venue, dict) else {})),
        "quadrant": q.key,
        "runner": q.runner,
        "target": q.target,
        "runner_status": q.runner_status,
        "item": item.get("id"),
        "item_digest": item.get("digest"),
        "status": "error",
        "not_run_reason": "",
        "started_utc": _now(),
        "ended_utc": "",
        "wall_seconds": 0.0,
        "evidence": {},
        "acceptance": [],
        "rounds": {"dispatch_attempts": 0, "test_cycles": 0, "operator_taps": 0},
        "scope": {"files_changed": 0, "out_of_scope_hits": [], "frozen_touched": []},
        "containment": {"class": "", "guard_events": []},
        "cost": {"wall_seconds": 0.0, "tokens": None, "usd": None, "gpu_seconds": None},
        "notes": [],
    }
    rec.update(over)
    return rec


def not_run(q: "_matrix.Quadrant", item: Dict[str, Any],
            preflight: "_matrix.PreflightResult", *, venue: Any = None) -> Dict[str, Any]:
    """The record a blocked quadrant produces.

    A blocked quadrant must produce a RECORD, not an exception. An exception is caught
    somewhere, logged somewhere, and the quadrant is then absent from the report - which is
    the exact shape this package exists to prevent.
    """
    if preflight.ready:
        raise ValueError("not_run() called for a quadrant whose preflight was ready")
    rec = new(q, item, venue=venue)
    rec["status"] = "not_run"
    rec["not_run_reason"] = preflight.reason
    rec["ended_utc"] = _now()
    rec["preflight_detail"] = preflight.detail
    return rec


def admit(rec: Any, *, item_digest: str = "", venue: str = "",
          schema: Dict[str, Any] | None = None) -> List[str]:
    """[] means admitted. Otherwise, every reason it is not, in the operator's words."""
    s = schema or _matrix.schema()
    problems: List[str] = []
    if not isinstance(rec, dict):
        return ["record is not an object"]

    status = str(rec.get("status") or "")
    statuses = s["statuses"]
    if status not in statuses:
        return [f"unknown status '{status}' - known: {', '.join(sorted(statuses))}"]

    for field in ("quadrant", "runner", "target", "item"):
        if not str(rec.get(field) or "").strip():
            problems.append(f"missing '{field}'")

    if item_digest:
        got = str(rec.get("item_digest") or "")
        if got != item_digest:
            problems.append(
                f"item digest mismatch: record ran '{got[:12] or '(none)'}...', the "
                f"comparison is about '{item_digest[:12]}...'. A record from a different "
                f"item is not a data point about this one.")

    if s.get("record_venue_required"):
        rv = rec.get("venue")
        rv = rv if isinstance(rv, dict) else {}
        got = str(rv.get("name") or "").strip()
        if not got:
            problems.append(
                "record names no VENUE. PLAN section 2's preamble binds every 'Gym:' column to "
                "a place - 'measured runs in ai-orchestration-gym, never live planes or a "
                "real target' - so a record that cannot say where it ran cannot be evidence "
                "for one. Records produced before 2026-08-30 carry no venue by construction.")
        elif venue and got != venue:
            problems.append(
                f"venue mismatch: record ran in venue '{got}' (repo {rv.get('repo') or '?'}), "
                f"this comparison is over venue '{venue}'. A run in another place is not a "
                f"data point about this one.")

    if statuses[status].get("requires_evidence"):
        problems += _evidence_problems(rec, s)
    else:
        if rec.get("acceptance"):
            problems.append(
                f"status '{status}' carries acceptance results. A quadrant that did not "
                f"run cannot have checked anything; this is how a non-result acquires a "
                f"score.")
        if status == "not_run":
            for field in s["required_when_not_run"]:
                if not str(rec.get(field) or "").strip():
                    problems.append(
                        f"status 'not_run' with no '{field}' - indistinguishable from a "
                        f"quadrant nobody considered")
        if status == "error" and not str(rec.get("error") or rec.get("not_run_reason") or "").strip():
            problems.append("status 'error' with no 'error' text")

    return problems


def _evidence_problems(rec: Dict[str, Any], s: Dict[str, Any]) -> List[str]:
    problems: List[str] = []

    for field in ("started_utc", "ended_utc"):
        if not str(rec.get(field) or "").strip():
            problems.append(f"missing '{field}' - nothing attests that this run happened")
    try:
        wall = float(rec.get("wall_seconds") or 0)
    except (TypeError, ValueError):
        wall = 0.0
    if wall <= 0:
        problems.append(
            "wall_seconds is zero - a run that took no time did not take place")

    ev = rec.get("evidence")
    ev = ev if isinstance(ev, dict) else {}
    for key in s["required_evidence_keys"]:
        val = str(ev.get(key) or "").strip()
        if not val:
            problems.append(f"evidence.{key} is missing")
            continue
        if not Path(val).exists():
            problems.append(f"evidence.{key} does not exist on disk: {val}")

    acc = rec.get("acceptance")
    if not isinstance(acc, list) or not acc:
        problems.append(
            "no acceptance results - the run was never checked, so 'completed' is a "
            "self-report (PLAN C.7: only an executable check counts)")
        return problems

    for i, a in enumerate(acc):
        if not isinstance(a, dict):
            problems.append(f"acceptance[{i}] is not an object")
            continue
        for field in s["required_acceptance_fields"]:
            if field == "exit_code":
                if not isinstance(a.get("exit_code"), int):
                    problems.append(
                        f"acceptance[{i}] has no integer 'exit_code' - a verdict with no "
                        f"way to re-derive it")
                continue
            if not str(a.get(field) or "").strip():
                problems.append(f"acceptance[{i}] is missing '{field}'")
        if isinstance(a.get("exit_code"), int) and "passed" in a:
            if bool(a["passed"]) != (a["exit_code"] == 0):
                problems.append(
                    f"acceptance[{i}] claims passed={a['passed']} beside exit_code="
                    f"{a['exit_code']} - a self-report overriding a measurement")
    return problems


def is_comparable(rec: Dict[str, Any], q: "_matrix.Quadrant | None" = None,
                  schema: Dict[str, Any] | None = None) -> bool:
    """Comparable = the STATUS is an outcome AND the runner is not scaffolding."""
    s = schema or _matrix.schema()
    status = str(rec.get("status") or "")
    if not s["statuses"].get(status, {}).get("comparable"):
        return False
    if q is not None:
        return bool(q.comparable)
    rs = str(rec.get("runner_status") or "")
    return rs in set(s["comparable_runner_statuses"]) if rs else True


def acceptance_rate(rec: Dict[str, Any]) -> str:
    acc = [a for a in (rec.get("acceptance") or []) if isinstance(a, dict)]
    if not acc:
        return "-"
    passed = sum(1 for a in acc if a.get("exit_code") == 0)
    return f"{passed}/{len(acc)}"
