"""The operator's surface: preflight, run, report.

    python -m quadrant.cli preflight
    python -m quadrant.cli run --runner claude-code --target project --item u4-baseline
    python -m quadrant.cli run-all --item u4-baseline
    python -m quadrant.cli report

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
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import config as harness_config          # noqa: E402
from quadrant import adapters            # noqa: E402
from quadrant import item as item_mod    # noqa: E402
from quadrant import matrix as matrix_mod  # noqa: E402
from quadrant import record as record_mod  # noqa: E402
from quadrant import report as report_mod  # noqa: E402

GUARDS = f'"{sys.executable}" "{HERE / "guards.py"}"'
DEFAULT_TIMEOUT = 1800.0


def _repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
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
    blocked = 0
    print(f"item repo: {repo}")
    for q in quadrants:
        pf = matrix_mod.preflight(q, cfg, repo=repo,
                                  scratch_root=scratch or str(repo / ".quadrant" / "scratch"))
        if pf.ready:
            print(f"  READY    {q.label}")
        else:
            blocked += 1
            print(f"  BLOCKED  {q.label}\n           {pf.reason}")
    print(f"{len(quadrants) - blocked}/{len(quadrants)} quadrants can run right now")
    return 0 if blocked == 0 else 1


def _run_one(q: "matrix_mod.Quadrant", cfg: Dict[str, Any], it: Dict[str, Any], *,
             results_dir: Path, repo: Path, scratch_root: Path,
             timeout: float) -> Dict[str, Any]:
    """One quadrant, one run. ALWAYS returns a record - blocked, errored or completed.

    The try/except is the honesty mechanism, not defensive coding: an adapter that raises
    must still leave a record behind, or the quadrant disappears from the comparison via
    the one path the report cannot see.
    """
    run_dir = results_dir / f"{_now()}-{q.runner}-{q.target}"
    run_dir.mkdir(parents=True, exist_ok=True)

    pf = matrix_mod.preflight(q, cfg, repo=repo, scratch_root=str(scratch_root))
    if not pf.ready:
        # A blocked quadrant is PERSISTED like any other. The first version returned this
        # record without writing it, and the report then rendered "no record produced" -
        # true, but a weaker sentence than the reason the preflight had already established.
        # Losing a known reason is the same failure as never having one.
        rec = record_mod.not_run(q, it, pf)
        (run_dir / "record.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        return rec
    rec = record_mod.new(q, it)
    t0 = _dt.datetime.now(_dt.timezone.utc)
    transcript_path = run_dir / "transcript.txt"

    try:
        ws = adapters.prepare_target(q, cfg, run_dir=run_dir, repo=repo,
                                     scratch_root=scratch_root)
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
            proc = subprocess.run(cmd, shell=True, cwd=str(ws), capture_output=True, text=True)
            acceptance.append({
                "criterion": crit.get("why") or crit["check"],
                "check": cmd,
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
    except (matrix_mod.QuadrantConfigError, item_mod.QuadrantItemError) as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2

    results_dir.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    reps = matrix_mod.repeats(cfg)
    attempted = []
    for q in quadrants:
        for i in range(reps):
            rec = _run_one(q, cfg, it, results_dir=results_dir, repo=repo,
                           scratch_root=scratch_root, timeout=timeout)
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
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"  (skipping unreadable record {p})")
    return out


def _declared_matrix(results_dir: Path, quadrants: List["matrix_mod.Quadrant"],
                     records: List[Dict[str, Any]]) -> List[str]:
    """The declared matrix of this results SET - pinned on first write, append-only.

    Read the lock, union it with the configured cells and with every cell the records
    name, write the union back. The union is the point: a cell that has ever been part of
    this comparison stays part of it, so narrowing `quadrant.runners` can no longer turn a
    2-of-4 comparison into a complete 2-of-2 one (see the module docstring).

    A missing lock is normal (the first run, or a results dir from before this existed) and
    is written silently. An UNREADABLE lock is not: it is reported, and the declared set
    falls back to config + records, which still holds every cell any record names.
    """
    lock_path = results_dir / (matrix_mod.schema().get("declared_matrix_lock")
                               or "matrix.json")
    prior: List[str] = []
    if lock_path.is_file():
        try:
            prior = [str(k) for k in (json.loads(lock_path.read_text(encoding="utf-8"))
                                      .get("declared") or [])]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  (matrix lock {lock_path} is unreadable: {exc}; the declared matrix "
                  f"falls back to the configuration plus every cell the records name)")
    declared = report_mod.declared_keys(quadrants, records, prior)
    if declared != prior:
        results_dir.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({
            "version": 1,
            "declared": declared,
            "updated_utc": _now(),
            "_why": [
                "The cells THIS results set is a comparison over. Append-only: a cell that",
                "has ever been declared stays declared, so narrowing quadrant.runners or",
                "quadrant.targets cannot shrink the comparison into looking complete.",
                "Delete this file and the declared matrix falls back to the configuration",
                "plus every cell the records name - which is weaker, and is why it is",
                "written rather than derived.",
            ],
        }, indent=2), encoding="utf-8")
    return declared


def _emit_report(results_dir: Path, item_id: str, argv: List[str]) -> int:
    cfg = _cfg({})
    try:
        quadrants = matrix_mod.build(cfg)
        it = item_mod.load(item_id)
    except (matrix_mod.QuadrantConfigError, item_mod.QuadrantItemError) as exc:
        print(f"MISCONFIGURED: {exc}")
        return 2
    # NOT filtered to the configured matrix. Dropping a record here is what let a narrowed
    # configuration launder an incomplete comparison into a complete one.
    records = _load_records(results_dir)
    declared = _declared_matrix(results_dir, quadrants, records)
    try:
        md = report_mod.render(quadrants, records, item=it, declared=declared)
        summary = report_mod.summarize(quadrants, records, item=it, declared=declared)
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
