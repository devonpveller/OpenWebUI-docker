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
    comparison over - compared by the REPOSITORY's identity (its root commit), not by the
    venue's name. Same move as the digest, one axis out: "the same anchored item" was
    mechanized while "in the gym" was not, and a run in the wrong place is not a data
    point about the right one however well it was measured. Comparing the NAME alone was
    this file's own version of the defect: four records re-pointed at another repository,
    still labelled `gym`, were admitted in silence. See `_venue_problems`.

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

# THE KEY THE LOADER STAMPS A RECORD'S OWN DIRECTORY UNDER.
#
# It is NOT part of the record schema and is never written to disk - `cli._load_records`
# adds it after reading, so `admit` can answer "is this evidence here?" about the tree it
# was handed rather than about the machine the record was produced on. See
# `_evidence_present`.
RECORD_DIR_KEY = "__record_dir__"


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


def admit(rec: Any, *, item_digest: str = "", venue: Any = "",
          schema: Dict[str, Any] | None = None, record_dir: Any = None) -> List[str]:
    """[] means admitted. Otherwise, every reason it is not, in the operator's words.

    `record_dir` is the directory the record was READ FROM. It is optional and defaults to
    the record's own `RECORD_DIR_KEY` stamp, so callers that already load records through
    `cli._load_records` get it for free. Without it this gate resolves `evidence.*` as the
    ABSOLUTE path the record was written with - which is a fact about the machine that
    produced the record and not about the tree the auditor is holding. See
    `_evidence_present` for what that cost, measured.
    """
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
        problems += _venue_problems(rec.get("venue"), venue)

    if statuses[status].get("requires_evidence"):
        if record_dir is None:
            record_dir = rec.get(RECORD_DIR_KEY)
        problems += _evidence_problems(rec, s, record_dir)
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


def _same_repo_path(a: Any, b: Any) -> bool:
    """Path equality that survives Windows: separators and case both vary per writer."""
    return (str(a or "").replace("\\", "/").rstrip("/").lower()
            == str(b or "").replace("\\", "/").rstrip("/").lower())


def _venue_problems(rv: Any, pin: Any) -> List[str]:
    """Did this record run in the place this comparison is over?

    THE DEFECT THIS REPLACES (2026-08-30, round 7). The first version compared the venue
    NAME and nothing else. A verifier edited four records' `venue.repo` to
    `D:/SomeOther/arena-clone`, left `venue.name` as `gym`, and the arena's own results set
    admitted all four in silence: COMPARED 4/4, exit 0. `matrix.json` asserted in its `_why`
    that "record.admit refuses a record from any other venue" - it refused a record from
    any other NAME, which two different repositories can trivially share. A label agreeing
    with a label, read as two runs having happened in one place: the package's own failure
    class, one axis out.

    THE ORDER, most authoritative first:

      * IDENTITY, where both sides carry one. The repository's root commit (see
        `venue.identity_of`) is content-addressed, so it cannot be RENAMED into agreement.
        Identity deciding also means a checkout that was MOVED or re-cloned still matches,
        which is correct: it is the same repository, and the path was never the point.

        LIMIT OF THIS CHECK, and it is the package's own failure class one turn further out
        (orchestrator, 2026-08-30, at merge): the identity compared here is the one the
        RECORD REPORTS - `rec_id` is read from `rv["identity"]`, not re-derived from the
        repository on disk. Content-addressing makes it impossible to rename a venue into
        agreement; it does not make it impossible to EDIT a record into agreement, because
        the same hand that edits `repo` can edit `identity`. And on this branch a matching
        identity RETURNS EARLY, so `repo` is not compared at all - which is what makes a
        moved checkout match, and equally what makes an edited one match.
        A verifier reported this path as holding and another reported it as admitting a
        repo-repointed record; both were right, on records that did and did not carry an
        identity. Closing it means re-deriving `identity_of(rv["repo"], rv["ref"])` at
        admission wherever that repo is present, and refusing when it is not derivable.
        Recorded as known-open at U4's closure rather than fixed, because it is a sibling of
        the class this very function replaced and the phase reached its convergence bound.
      * A pin that carries an identity and a record that does not is a REFUSAL, not a
        downgrade. Otherwise stripping one field from a forged record buys the weaker check.
      * LABELS, only when neither side has an identity - a results set written before this
        existed. Then every field is compared, not just the name, and the refusal says the
        comparison was by label so nobody reads it as stronger than it is.
    """
    rv = rv if isinstance(rv, dict) else {}
    got = str(rv.get("name") or "").strip()
    if not got:
        return ["record names no VENUE. PLAN section 2's preamble binds every 'Gym:' column "
                "to a place - 'measured runs in ai-orchestration-gym, never live planes or a "
                "real target' - so a record that cannot say where it ran cannot be evidence "
                "for one. Records produced before 2026-08-30 carry no venue by construction."]
    if not pin:
        return []
    pin = {"name": pin} if isinstance(pin, str) else (dict(pin) if isinstance(pin, dict) else {})

    pin_id, rec_id = str(pin.get("identity") or ""), str(rv.get("identity") or "")
    if pin_id and rec_id:
        if pin_id != rec_id:
            return [f"venue mismatch: the record ran in repository `{rec_id}` "
                    f"(labelled '{got}', {rv.get('repo') or '?'}); this comparison is over "
                    f"repository `{pin_id}` (labelled '{pin.get('name')}', "
                    f"{pin.get('repo') or '?'}). Identity is the repository's root commit, "
                    f"which is why it decides and the name does not: two different "
                    f"repositories can carry one name, and one repository keeps its identity "
                    f"through any rename or move."]
        return []
    if pin_id and not rec_id:
        return [f"venue mismatch: this comparison is pinned to repository `{pin_id}` "
                f"(labelled '{pin.get('name')}'), and the record carries no repository "
                f"identity to compare - only the label '{got}'. A label is what the pin "
                f"exists to stop being sufficient, so this is a refusal rather than a "
                f"fallback to the weaker check."]

    mism = []
    for field, label in (("name", "name"), ("kind", "kind"), ("ref", "ref")):
        want = str(pin.get(field) or "").strip()
        if want and str(rv.get(field) or "").strip() != want:
            mism.append(f"{label} '{rv.get(field) or '(none)'}' vs pinned '{want}'")
    if pin.get("repo") and not _same_repo_path(rv.get("repo"), pin.get("repo")):
        mism.append(f"repository path '{rv.get('repo') or '(none)'}' vs pinned "
                    f"'{pin.get('repo')}'")
    if mism:
        return ["venue mismatch: " + "; ".join(mism) + ". A run in another place is not a "
                "data point about this one. NOTE: neither this record nor this results "
                "set's pin carries a repository IDENTITY (both predate it), so the "
                "comparison above is by LABEL - which is weaker, because a label can be "
                "edited into agreement. A set re-run today would be compared by root commit."]
    return []


def _sibling_of(val: str, record_dir: Any) -> "Path | None":
    r"""The evidence path RESOLVED AGAINST THE RECORD'S OWN DIRECTORY, or None.

    Every record this harness writes names its evidence INSIDE its own run directory, so
    the last two components of the recorded path are `<run dir>/<name>`. When the record
    was read from a directory of that name, `<record dir>/<name>` is the same artifact in
    the tree the auditor is holding. Returns None for a record shaped some other way -
    there is nothing to resolve against and inventing one would be a guess.

    The split is on BOTH separators deliberately: these paths are written by whichever OS
    produced the record (`D:\...\workspace` here), and a POSIX reader that splits on "/"
    alone sees one component and silently resolves nothing.
    """
    if not record_dir:
        return None
    norm = str(val).replace("\\", "/").rstrip("/")
    parts = [x for x in norm.split("/") if x]
    if len(parts) < 2:
        return None
    rd = Path(str(record_dir))
    if parts[-2] != rd.name:
        return None
    return rd / parts[-1]


def _evidence_present(val: str, record_dir: Any = None) -> bool:
    r"""Is the artifact this record names actually HERE?

    THE DEFECT THIS CLOSES, measured 2026-09-01 from a clean clone at `fba111d`. Every
    committed record under `documentation/evidence/dfu-u4/` was REFUSED at admission -
    "evidence.workspace does not exist on disk: D:\...\wt-u4close\...\workspace" - and
    `cli.py report` answered COMPARED 0/4, exit 1, while the retained workspaces and
    transcripts sat in the checkout, tracked by git, beside their records. `Path(val)` is
    the absolute path the PRODUCING WORKTREE wrote; that worktree was removed after the
    branch merged, so the gate was answering a question about a machine rather than about
    the evidence set in hand. It had passed until then only because that directory still
    existed on the author's disk - the exact "runs in the author's tree only" shape
    `documentation/evidence/README.md` was created to end.

    THE SIBLING IS AUTHORITATIVE, and the recorded absolute path is not consulted when one
    can be resolved. That is not a preference; it is
    `scripts/checks/check_quadrant_evidence_reproduces.py`'s rule, already earned there by
    the U3 gym drill: a results set that was COPIED (or archived, or cloned) still has the
    ORIGINAL absolute path in every record, so following it walks out of the tree under
    audit and back into an untouched original - a deleted workspace then reads as present.
    An auditor reads the tree they were handed.

    A record shaped some other way, or read from nowhere in particular, still resolves the
    way it always did: the absolute path, or nothing.
    """
    sib = _sibling_of(val, record_dir)
    if sib is not None:
        return sib.exists()
    return Path(str(val)).exists()


def _where_it_was_looked_for(val: str, record_dir: Any) -> str:
    """Name the path that was actually tested, when it is not the one printed above."""
    sib = _sibling_of(val, record_dir)
    if sib is None:
        return ""
    return (f" (resolved against this record's own directory, which is what an auditor "
            f"holds: {sib} - the recorded absolute path is a fact about the machine that "
            f"produced it and is deliberately not consulted)")


def missing_evidence(rec: Dict[str, Any], record_dir: Any = None,
                     schema: Dict[str, Any] | None = None) -> List[str]:
    """Which required evidence keys have NO artifact in the tree in hand.

    Asked as a STRUCTURED question so a caller never has to grep `admit`'s prose to learn
    why a record was refused. "the evidence is gone" and "the record names no venue" are
    both refusals and they are not the same finding: the first is what an evidence audit
    exists to report, the second keeps a record out of a comparison and says nothing about
    the tree. See check_quadrant_evidence_reproduces.py, which routes them differently.
    """
    s = schema or _matrix.schema()
    if record_dir is None:
        record_dir = rec.get(RECORD_DIR_KEY) if isinstance(rec, dict) else None
    ev = rec.get("evidence") if isinstance(rec, dict) else None
    ev = ev if isinstance(ev, dict) else {}
    out: List[str] = []
    for key in s["required_evidence_keys"]:
        val = str(ev.get(key) or "").strip()
        if not val or not _evidence_present(val, record_dir):
            out.append(key)
    return out


def _evidence_problems(rec: Dict[str, Any], s: Dict[str, Any],
                       record_dir: Any = None) -> List[str]:
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
        if not _evidence_present(val, record_dir):
            problems.append(f"evidence.{key} does not exist on disk: {val}"
                            + _where_it_was_looked_for(val, record_dir))

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
