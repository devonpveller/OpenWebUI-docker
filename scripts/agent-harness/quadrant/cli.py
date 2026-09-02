"""The operator's surface: preflight, run, report.

    python -m quadrant.cli preflight
    python -m quadrant.cli run --runner claude-code --target project --item u4-baseline
    python -m quadrant.cli run-all --item u4-baseline
    python -m quadrant.cli report

THE VENUE IS PART OF EVERY COMMAND. `quadrant.venue` names the repository the experiment is
performed ON (`--repo` overrides it, an env var overrides the config). PLAN section 2's
preamble - "'gym' means measured runs in `ai-orchestration-gym`, never live planes or a real
target" - is a constraint on the PLACE, and until 2026-08-30 this package had no place: a
complete four-cell comparison ran against ai-stack itself and nothing could say so. A gym
venue that resolves to the harness's own repository is now a BLOCKED preflight, every record
carries its venue, and admission refuses a record from another one. See quadrant/venue.py.

EXIT CODES ARE PART OF THE CONTRACT, because a script consuming this has to be able to tell
a finished comparison from a partial one without parsing prose:

    run / run-all
      0  every quadrant THIS invocation attempted completed
      1  at least one attempted quadrant did not complete (not run, failed or errored)
      2  misconfigured, or the command was wrong
    report
      0  every configured quadrant produced an admitted, comparable outcome
      1  the comparison is INCOMPLETE - at least one quadrant did not run or was refused
      2  misconfigured, or the command was wrong

The two are separate on purpose. `run --runner X --target Y` exercises ONE cell and must be
able to report that cell green; whether the whole 2x2 is finished is a question about the
accumulated records, and that is what `report` answers. `run` still WRITES the full-matrix
report, so the artifact on disk never shows a comparison of one.

`report` exiting 1 on an incomplete comparison is the single most load-bearing line in this
file. The failure mode U4 has to survive is a two-of-four comparison that reads as done;
an exit code that says "incomplete" is what makes that visible to cron, to CI, and to the
next agent, none of which read the paragraph at the bottom.

WHAT "COMPLETE" IS MEASURED AGAINST - a defect this file shipped once. Until 2026-08-30
`_emit_report` filtered the records down to the CURRENTLY configured matrix before the
report saw them. Narrowing `quadrant.runners` to one entry - one line of config - then
made the two never-run cells vanish from the table and the comparison read COMPARED 2/2,
complete, exit 0, over the very same evidence. The module's own stated failure mode,
reachable by configuration.

The comparison is therefore over the DECLARED matrix of a results SET, not over today's
configuration. `matrix.json` in the results directory pins it on first write and is
APPEND-ONLY: `_declared_matrix` unions the lock with the configured cells and with every
cell a record on disk names, writes the union back, and hands it to the report. Adding a
runner grows the comparison; removing one cannot shrink it. Nothing is filtered out of
`_load_records` any more - a record the matrix does not know about is rendered, not
dropped.

What that does NOT defend against, stated plainly: a comparison started from a
FRESH results directory with narrow axes is a genuinely narrow comparison, and it says so
(`COMPARED 2/2` over a two-cell declared matrix, with the axes visible in matrix.json).
Laundering requires reusing an existing evidence set, and that is what the lock stops. The
remaining move - delete matrix.json AND the records of the cells being hidden - is a
deletion of evidence rather than a report that lies about it.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import config as harness_config          # noqa: E402
from quadrant import adapters            # noqa: E402
from quadrant import proc as _proc       # noqa: E402
from quadrant import item as item_mod    # noqa: E402
from quadrant import matrix as matrix_mod  # noqa: E402
from quadrant import record as record_mod  # noqa: E402
from quadrant import report as report_mod  # noqa: E402
from quadrant import venue as venue_mod    # noqa: E402

GUARDS = f'"{sys.executable}" "{HERE / "guards.py"}"'
DEFAULT_TIMEOUT = 1800.0


def _repo_root() -> Path:
    out = _proc.run(["git", "rev-parse", "--show-toplevel"],
                         cwd=str(HERE), capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.returncode == 0 else HERE.parents[2]


def _cfg(overrides: Dict[str, Any]) -> Dict[str, Any]:
    cfg = harness_config.load(fresh=True)
    q = dict(cfg.get("quadrant") or {})
    for k, v in overrides.items():
        if v is not None:
            q[k] = v
    cfg = dict(cfg)
    cfg["quadrant"] = q
    return cfg


def _venue(cfg: Dict[str, Any], argv: List[str], repo: Path) -> "venue_mod.Venue":
    """The venue for this invocation. Raises QuadrantConfigError, never guesses a place."""
    try:
        return venue_mod.resolve(cfg, matrix_mod.schema(), harness_repo=repo,
                                 override_repo=str(_opt(argv, "--repo", "") or ""))
    except venue_mod.VenueConfigError as exc:
        raise matrix_mod.QuadrantConfigError(str(exc)) from exc


def _opt(argv: List[str], name: str, default: Any = None) -> Any:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------------ commands --

def cmd_preflight(argv: List[str]) -> int:
    cfg = _cfg({})
    scratch = _opt(argv, "--scratch-root")
    try:
        quadrants = matrix_mod.build(cfg)
    except matrix_mod.QuadrantConfigError as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    repo = _repo_root()
    try:
        v = _venue(cfg, argv, repo)
    except matrix_mod.QuadrantConfigError as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    blocked = 0
    # BOTH repositories, named and distinguished. "item repo" alone was the line that let a
    # reader believe the arena had been used: it printed the harness's own path and nothing
    # said that was wrong.
    print(f"harness repo : {repo}")
    # THE VERDICT NAMES ITS SOURCE. "satisfies a Gym: column" reads as a measurement and is
    # not one: a verifier pointed $AI_STACK_GYM_REPO at a throwaway repo called
    # `not-the-arena` and this line said it satisfied the column, READY 4/4, exit 0. What is
    # checked is below it, and what is not checked is said out loud.
    print(f"venue        : {v.name} (kind {v.kind}) - DECLARED"
          f"{'' if v.satisfies_gym_column else ' NOT'} to satisfy a \"Gym:\" column "
          f"by quadrant/schema.json; that is config, not a measurement")
    print(f"item repo    : {v.repo} @ {v.ref}   (via {v.source})")
    print(f"identity     : {v.identity or 'UNRESOLVED - no root commit for that ref'}")
    for line in (venue_mod.what_was_checked(v.kind, v.rules)["not_checked"] or [])[:1]:
        print(f"NOT CHECKED  : {line}")
    for q in quadrants:
        pf = matrix_mod.preflight(q, cfg, repo=v.repo, venue=v, harness_repo=repo,
                                  scratch_root=scratch or str(repo / ".quadrant" / "scratch"))
        if pf.ready:
            print(f"  READY    {q.label}")
        else:
            blocked += 1
            print(f"  BLOCKED  {q.label}\n           {pf.reason}")
    print(f"{len(quadrants) - blocked}/{len(quadrants)} quadrants can run right now")
    return 0 if blocked == 0 else 1


def _run_one(q: "matrix_mod.Quadrant", cfg: Dict[str, Any], it: Dict[str, Any], *,
             results_dir: Path, repo: Path, scratch_root: Path, timeout: float,
             venue: "venue_mod.Venue", harness_repo: Path) -> Dict[str, Any]:
    """One quadrant, one run. ALWAYS returns a record - blocked, errored or completed.

    The try/except is the honesty mechanism, not defensive coding: an adapter that raises
    must still leave a record behind, or the quadrant disappears from the comparison via
    the one path the report cannot see.
    """
    run_dir = results_dir / f"{_now()}-{q.runner}-{q.target}"
    run_dir.mkdir(parents=True, exist_ok=True)

    pf = matrix_mod.preflight(q, cfg, repo=repo, venue=venue, harness_repo=harness_repo,
                              scratch_root=str(scratch_root))
    if not pf.ready:
        # A blocked quadrant is PERSISTED like any other. The first version returned this
        # record without writing it, and the report then rendered "no record produced" -
        # true, but a weaker sentence than the reason the preflight had already established.
        # Losing a known reason is the same failure as never having one.
        rec = record_mod.not_run(q, it, pf, venue=venue)
        (run_dir / "record.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        return rec
    rec = record_mod.new(q, it, venue=venue)
    t0 = _dt.datetime.now(_dt.timezone.utc)
    transcript_path = run_dir / "transcript.txt"

    try:
        ws = adapters.prepare_target(q, cfg, run_dir=run_dir, repo=repo,
                                     scratch_root=scratch_root, ref=venue.ref)
        manifest = item_mod.plant(it, ws)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        adapters.baseline_commit(ws)

        outcome = adapters.dispatch(q, cfg, item=it, workspace=ws, run_dir=run_dir,
                                    timeout=timeout)
        transcript_path.write_text(outcome.transcript or "(runner produced no transcript)\n",
                                   encoding="utf-8")

        changed = adapters.workspace_changes(ws)
        allowed = it["allowed_paths"]
        out_of_scope = [c for c in changed
                        if not any(fnmatch(c, pat) for pat in allowed)]
        frozen_touched = [c for c in changed if c in set(it["frozen_paths"])]

        acceptance = []
        for crit in it["criteria"]:
            cmd = item_mod.expand(crit["check"], guards=GUARDS, item_id=it["id"])
            proc = _proc.run(cmd, shell=True, cwd=str(ws), capture_output=True, text=True)
            acceptance.append({
                "criterion": crit.get("why") or crit["check"],
                "check": cmd,
                # The UNEXPANDED criterion, beside the expanded one. `check` is the exact
                # command that ran and must stay that; it also embeds this machine's
                # interpreter and this worktree's `guards.py`, so re-running it from a clone
                # fails on a path rather than on the evidence. An auditor re-expands this
                # against their own checkout - see
                # scripts/checks/check_quadrant_evidence_reproduces.py `_runnable`.
                "check_template": crit["check"],
                "exit_code": proc.returncode,
                "passed": proc.returncode == 0,
                "output": (proc.stdout + proc.stderr)[-4000:],
            })

        adapters.finalize_target(q, run_dir=run_dir, repo=repo)

        t1 = _dt.datetime.now(_dt.timezone.utc)
        rec.update({
            "status": "completed" if all(a["passed"] for a in acceptance) else "failed",
            "ended_utc": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wall_seconds": round((t1 - t0).total_seconds(), 1),
            "evidence": {"workspace": str(ws), "transcript": str(transcript_path),
                         "manifest": str(run_dir / "manifest.json")},
            "acceptance": acceptance,
            "rounds": {"dispatch_attempts": outcome.dispatch_attempts,
                       "test_cycles": len(acceptance),
                       "operator_taps": 0},
            "scope": {"files_changed": len(changed), "out_of_scope_hits": out_of_scope,
                      "frozen_touched": frozen_touched},
            "containment": {"class": report_mod._containment_class(q), "guard_events": []},
            "cost": {"wall_seconds": round((t1 - t0).total_seconds(), 1),
                     "tokens": outcome.tokens, "usd": outcome.usd,
                     "gpu_seconds": outcome.gpu_seconds},
        })
        if not outcome.ok and rec["status"] == "completed":
            # The acceptance checks are the verdict, but a runner that reported failure
            # while the checks pass is worth saying out loud rather than smoothing over.
            rec["notes"].append(f"runner reported failure but acceptance passed: {outcome.error}")
        # An adapter may have something to say about HOW it ran the cell - the little-coder
        # transport mirrors the workspace into a container and says so, and says louder still
        # when it could not put the plane back afterwards. Those belong in the record a
        # reader of the comparison sees, not only in a transcript nobody opens.
        for note in (outcome.detail.get("notes") or []):
            rec["notes"].append(str(note))
    except Exception as exc:  # noqa: BLE001
        t1 = _dt.datetime.now(_dt.timezone.utc)
        transcript_path.write_text(f"adapter error: {exc}\n", encoding="utf-8")
        rec.update({"status": "error", "error": f"{type(exc).__name__}: {exc}",
                    "ended_utc": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "wall_seconds": round((t1 - t0).total_seconds(), 1)})
        try:
            adapters.finalize_target(q, run_dir=run_dir, repo=repo)
        except Exception:  # noqa: BLE001
            pass

    (run_dir / "record.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def cmd_run(argv: List[str], *, all_quadrants: bool = False) -> int:
    item_id = _opt(argv, "--item", "u4-baseline")
    repo = _repo_root()
    results_dir = Path(_opt(argv, "--results-dir", str(repo / ".quadrant" / "runs")))
    scratch_root = Path(_opt(argv, "--scratch-root", str(repo / ".quadrant" / "scratch")))
    timeout = float(_opt(argv, "--timeout", DEFAULT_TIMEOUT))
    runner, target = _opt(argv, "--runner"), _opt(argv, "--target")

    overrides: Dict[str, Any] = {}
    if runner:
        overrides["runners"] = [runner]
    if target:
        overrides["targets"] = [target]
    try:
        cfg = _cfg(overrides)
        quadrants = matrix_mod.build(cfg)
        it = item_mod.load(item_id)
        v = _venue(cfg, argv, repo)
    except (matrix_mod.QuadrantConfigError, item_mod.QuadrantItemError) as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    print(f"venue: {v.name} ({v.kind}) - {v.repo} @ {v.ref} (via {v.source}) "
          f"identity {v.identity or 'UNRESOLVED'}")

    results_dir.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    reps = matrix_mod.repeats(cfg)
    attempted = []
    for q in quadrants:
        for i in range(reps):
            rec = _run_one(q, cfg, it, results_dir=results_dir, repo=v.repo,
                           scratch_root=scratch_root, timeout=timeout, venue=v,
                           harness_repo=repo)
            attempted.append(rec)
            suffix = f" (repeat {i + 1}/{reps})" if reps > 1 else ""
            print(f"  {rec['status'].upper():9s} {q.label}{suffix}"
                  + (f"\n            {rec.get('not_run_reason') or rec.get('error') or ''}"
                     if rec["status"] in ("not_run", "error") else ""))

    # Rendered against the FULL configured matrix even when this invocation ran one cell:
    # the artifact on disk must still show the other three as NOT RUN.
    _emit_report(results_dir, item_id, argv)
    # This command's exit code answers the other question - did what I just ran work? -
    # because a caller running one cell at a time would otherwise get exit 1 forever.
    return 0 if all(r["status"] == "completed" for r in attempted) else 1


def _load_records(results_dir: Path) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(results_dir.glob("*/record.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  (skipping unreadable record {p})")
            continue
        # WHERE THIS RECORD WAS READ FROM, carried with it. `record.admit` resolves
        # `evidence.*` against it, so admission asks about the tree in hand instead of the
        # absolute path the producing worktree wrote - which is how every committed record
        # under documentation/evidence/ came to be REFUSED from a clone once the worktree
        # that made them was removed. Stamped here rather than threaded through render(),
        # summarize() and _rows() because the loader is the only place that knows it, and
        # it is a private key that is never written back to disk.
        if isinstance(rec, dict):
            rec[record_mod.RECORD_DIR_KEY] = str(p.parent)
        out.append(rec)
    return out


def _same_path(a: Any, b: Any) -> bool:
    return (str(a or "").replace("\\", "/").rstrip("/").lower()
            == str(b or "").replace("\\", "/").rstrip("/").lower())


def _declared_matrix(results_dir: Path, quadrants: List["matrix_mod.Quadrant"],
                     records: List[Dict[str, Any]],
                     venue: "venue_mod.Venue | None" = None) -> tuple:
    """The declared matrix of this results SET - pinned on first write, append-only.

    Read the lock, union it with the configured cells and with every cell the records
    name, write the union back. The union is the point: a cell that has ever been part of
    this comparison stays part of it, so narrowing `quadrant.runners` can no longer turn a
    2-of-4 comparison into a complete 2-of-2 one (see the module docstring).

    A missing lock is normal (the first run, or a results dir from before this existed) and
    is written silently. An UNREADABLE lock is not: it is reported, and the declared set
    falls back to config + records, which still holds every cell any record names.

    THE LOCK ALSO PINS THE VENUE (2026-08-30). A results set is a comparison over one place
    as well as over one item and one set of cells: pointing `--repo` somewhere else and
    re-running into the same directory would otherwise mix two experiments into one table.
    The venue is pinned on FIRST write and never rewritten, so the pin cannot be moved by a
    later run - it is returned to the caller, which hands it to admission AND to the report.
    Returns (declared cells, pinned venue name, pinned venue BLOCK). The block is what the
    report renders: rendering the venue object built from today's configuration instead is
    how `report --results-dir <a gym set> --repo <ai-stack>` printed ai-stack's path under
    "SATISFIES a Gym: column" over the arena's own records, at exit 0.
    """
    lock_path = results_dir / (matrix_mod.schema().get("declared_matrix_lock")
                               or "matrix.json")
    prior: List[str] = []
    prior_venue: Dict[str, Any] = {}
    if lock_path.is_file():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            prior = [str(k) for k in (lock.get("declared") or [])]
            pv = lock.get("venue")
            prior_venue = dict(pv) if isinstance(pv, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  (matrix lock {lock_path} is unreadable: {exc}; the declared matrix "
                  f"falls back to the configuration plus every cell the records name)")
    declared = report_mod.declared_keys(quadrants, records, prior)

    venue_block = dict(prior_venue)
    pinned_venue = str(prior_venue.get("name") or "")
    if pinned_venue:
        # THE DIVERGENCE IS PRINTED, not only enforced. `report --results-dir <a gym set>
        # --repo <ai-stack>` used to render TODAY's venue over the pinned set's records -
        # "Venue: gym - SATISFIES a Gym: column", ai-stack's path underneath, exit 0. The
        # report now renders the pin, so the operator who passed --repo must be told their
        # flag did not move it, and told by WHICH axis the two differ.
        diff = []
        if venue is not None:
            if venue.name != pinned_venue:
                diff.append(f"name '{venue.name}' vs pinned '{pinned_venue}'")
            if venue.identity and prior_venue.get("identity")                     and venue.identity != prior_venue.get("identity"):
                diff.append(f"repository `{venue.identity}` vs pinned "
                            f"`{prior_venue.get('identity')}`")
            if not _same_path(venue.repo, prior_venue.get("repo")):
                diff.append(f"path '{venue.repo}' vs pinned '{prior_venue.get('repo')}'")
        if diff:
            print(f"  (this results set is PINNED to venue '{pinned_venue}' at "
                  f"{prior_venue.get('repo')}; the venue resolved for this invocation "
                  f"differs: {'; '.join(diff)}. The pin STANDS - the report below renders "
                  f"the pin, not the venue you passed, and records from anywhere else are "
                  f"refused. Use a fresh --results-dir for a new venue.)")
    elif venue is not None:
        # PINNING IS NOT LABELLING. A results set with no pin is either brand new or
        # pre-dates the venue mechanism, and stamping today's venue onto the second kind
        # would put "Venue: gym - SATISFIES a 'Gym:' column" at the top of a report whose
        # every record ran somewhere else. So the pin is taken only when this set has
        # nothing to contradict it: no records at all, or at least one record that already
        # names this venue. Otherwise the set stays UNSTATED, which is what it is.
        named = {str((r.get("venue") or {}).get("name") or "") for r in records}
        named.discard("")
        if not records or venue.name in named:
            venue_block = venue.as_record()
            pinned_venue = venue.name
        else:
            print(f"  (this results set predates the venue mechanism - {len(records)} record(s), "
                  f"none naming a venue. It is NOT being stamped with '{venue.name}': the runs "
                  f"in it happened somewhere else, and the report says UNSTATED.)")
    body: Dict[str, Any] = {
        "version": 2,
        "declared": declared,
        "venue": venue_block,
        "_why": [
                "The cells THIS results set is a comparison over. Append-only: a cell that",
                "has ever been declared stays declared, so narrowing quadrant.runners or",
                "quadrant.targets cannot shrink the comparison into looking complete.",
                "Delete this file and the declared matrix falls back to the configuration",
                "plus every cell the records name - which is weaker, and is why it is",
                "written rather than derived.",
                "",
                "`venue` is pinned on FIRST write and never rewritten: a results set is a",
                "comparison over one PLACE as well as one item, and the report renders THIS",
                "block rather than whatever the configuration resolves to today.",
                "",
                "WHAT record.admit ACTUALLY ENFORCES, corrected 2026-08-30. This text used",
                "to say it 'refuses a record from any other venue'. It refused a record",
                "from any other NAME - and a verifier re-pointed four records' venue.repo",
                "at D:/SomeOther/arena-clone, left the name as `gym`, and all four were",
                "admitted: COMPARED 4/4, exit 0. Admission now compares the REPOSITORY",
                "IDENTITY (`identity` below - the root commit reachable from `ref`) where",
                "both this pin and the record carry one; a pin with an identity refuses a",
                "record without one; and a set predating identity is compared on every",
                "label it carries, with the refusal saying that is the weaker check.",
        ],
    }
    # REWRITE WHEN THE FILE WOULD DIFFER AT ALL, not only when the cells or the venue moved.
    # `_why` is a CLAIM ABOUT WHAT THE HARNESS ENFORCES, sitting in the artifact an operator
    # reads, and this one was false for a day: it said admission "refuses a record from any
    # other venue" while admission compared a name. A results set whose lock never changes
    # would have carried that sentence forever. `updated_utc` is excluded from the
    # comparison, or every report would rewrite the file and the timestamp would stop
    # meaning "when this comparison last changed".
    existing = None
    if lock_path.is_file():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            existing.pop("updated_utc", None)
        except (json.JSONDecodeError, OSError):
            existing = None
    if existing != body:
        results_dir.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(dict(body, updated_utc=_now()), indent=2),
                             encoding="utf-8")
    return declared, pinned_venue, venue_block


def _emit_report(results_dir: Path, item_id: str, argv: List[str]) -> int:
    cfg = _cfg({})
    try:
        quadrants = matrix_mod.build(cfg)
        it = item_mod.load(item_id)
        v = _venue(cfg, argv, _repo_root())
    except (matrix_mod.QuadrantConfigError, item_mod.QuadrantItemError) as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    # NOT filtered to the configured matrix. Dropping a record here is what let a narrowed
    # configuration launder an incomplete comparison into a complete one.
    records = _load_records(results_dir)
    declared, pinned, pin_block = _declared_matrix(results_dir, quadrants, records, v)
    # The venue the REPORT is rendered against is the results set's PIN, always - never
    # today's configuration. The first version compared only the NAMES and rendered today's
    # Venue object when they agreed, so `report --results-dir <a gym set> --repo <ai-stack>`
    # printed "Venue: `gym` (kind `gym`) - SATISFIES a "Gym:" column" with ai-stack's path
    # under it, over the arena's own records, COMPARED 4/4, exit 0 - and wrote that to
    # COMPARISON.md. A set that declined the pin (records that name no venue) renders
    # UNSTATED rather than borrowing the configured venue's name.
    rv = pin_block or None
    try:
        md = report_mod.render(quadrants, records, item=it, declared=declared, venue=rv)
        summary = report_mod.summarize(quadrants, records, item=it, declared=declared,
                                       venue=rv)
    except report_mod.QuadrantReportError as exc:
        print(f"UNRENDERABLE: {exc}")
        return 2
    out_md = results_dir / "COMPARISON.md"
    out_md.write_text(md, encoding="utf-8")
    (results_dir / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print()
    print(md)
    print(f"(written: {out_md} and comparison.json)")
    return 0 if summary["complete"] else 1


def cmd_report(argv: List[str]) -> int:
    repo = _repo_root()
    results_dir = Path(_opt(argv, "--results-dir", str(repo / ".quadrant" / "runs")))
    return _emit_report(results_dir, _opt(argv, "--item", "u4-baseline"), argv)


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "preflight":
        return cmd_preflight(rest)
    if cmd in ("run", "run-all"):
        return cmd_run(rest, all_quadrants=(cmd == "run-all"))
    if cmd == "report":
        return cmd_report(rest)
    if cmd == "items":
        for name in item_mod.known_items():
            print(name)
        return 0
    print(f"unknown command '{cmd}'\n{__doc__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
