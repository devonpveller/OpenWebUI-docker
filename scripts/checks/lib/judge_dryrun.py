"""Judge dry-run probe -- what the little-coder judge WOULD mint, minting nothing.

Companion to `scripts/checks/check-judge-dryrun.ps1` (that wrapper is the
supported entry point; this file is the engine). Written for
`documentation/implementation-guide/little-coder/JUDGE-CALIBRATION.md`, the
U5 calibration plan for `observer.judge_enabled`.

WHY IT EXISTS
-------------
`observer.judge_enabled: false` is the flag that keeps the expertise loop
from minting clusters and drafting skills. Design section 13 says the flip
is a HUMAN decision made against a dry run on REAL journals -- and never
wrote the dry run. This is it.

WHAT IT DOES
------------
It runs the REAL projection the daemon runs (`littlecoder.cohorts.rebuild`
with `littlecoder.meta.default_similarity`) over the journals on disk, then
reports the unassigned pools -- the exact input `Judge.mint_clusters` would
receive -- plus, per pool, the signal-quality measures that decide whether
minting from it would produce craft knowledge or noise. It also assembles
the REAL prompt via `littlecoder.judge.build_messages`, so the artifact a
human rates is the true prompt, not a paraphrase of it.

It makes ZERO LLM calls and writes NOTHING to the cohort store or the skill
library. Every path it touches is opened read-only.

WHAT IT CANNOT DO
-----------------
It cannot tell you what the judge would ANSWER -- that is an LLM call, and
making it would be the thing this script exists to avoid. It reports which
pools the judge would be INVOKED on and how mintable they look; a human
rates the emitted prompts (design section 13, exit criterion 3).

FIDELITY, precisely
-------------------
`clusters.assign` returns UNASSIGNED without ever calling the similarity
function when no cluster shares the occurrence's (lang, task_shape) scope
(`little-coder/src/littlecoder/clusters.py:144-150`). So while the store
holds ZERO clusters, this stub-similarity projection is bit-identical to
what the embedding-similarity projection of a judge-enabled daemon would
produce. The moment the store holds a cluster, that stops being true --
which is exit code 4 below, not a silent approximation.

EXIT CODES
----------
  0  a verdict was produced (READY-FOR-RATING or NOT-READY)
  1  verdict is NOT-READY and --require-ready was passed
  2  usage error
  3  CANNOT TELL -- no readable journal evidence at the given path
  4  CANNOT TELL -- the prior cohort store already holds clusters, so the
     stub-similarity projection is no longer faithful (see FIDELITY)
  5  CANNOT TELL -- littlecoder could not be imported, or the config could
     not be read

"Cannot tell" is never reported as a pass. A dry run that does not know is
required to say so with a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# A signal that carries no payload: the exit-code prefix with an empty
# stderr tail (`agent.py:491` builds "exit {rc}: {stderr_tail}"), or the
# synthesized placeholder cohorts.py uses for a fail with no error record.
_DEGENERATE = re.compile(r"^\s*(exit\s+-?\d+\s*:\s*)?$")
_NO_SIGNAL = "task ended fail (no signal)"

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_USAGE = 2
EXIT_NO_EVIDENCE = 3
EXIT_STORE_HAS_CLUSTERS = 4
EXIT_IMPORT = 5


def _fail(code: int, reason: str, extra: dict | None = None) -> None:
    """Emit a machine-readable CANNOT TELL and exit non-zero."""
    payload = {"status": "cannot_tell", "exit_code": code, "reason": reason}
    if extra:
        payload.update(extra)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    raise SystemExit(code)


def _is_degenerate(text: str) -> bool:
    if text.strip() == _NO_SIGNAL:
        return True
    return bool(_DEGENERATE.match(text or ""))


def _count_records(journals: Path) -> dict:
    """Raw line counts per journal family, live + rotated segments."""
    out: dict[str, int] = {}
    for name in ("tool_calls", "errors", "outcomes"):
        n = 0
        for path in sorted(journals.glob(f"{name}*.jsonl")):
            try:
                with open(path, "rb") as fh:
                    for _ in fh:
                        n += 1
            except OSError:
                continue
        out[name] = n
    out["total"] = sum(out.values())
    return out


def _agent_knowledge_from_config(cfg) -> list[str]:
    """The founding-knowledge files the AGENT actually reads, taken from
    `agent.extra_args` (`--append-system-prompt <path>` pairs). The judge's
    `baseline_covers` verdict is only sound if it sees the same floor."""
    args = list(getattr(cfg.agent, "extra_args", []) or [])
    paths: list[str] = []
    for i, tok in enumerate(args):
        if tok == "--append-system-prompt" and i + 1 < len(args):
            paths.append(args[i + 1])
    return paths


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="judge_dryrun.py",
        description="Report what the little-coder judge WOULD mint. Mints nothing.",
    )
    ap.add_argument("--journals", required=True, help="journals directory (read-only)")
    ap.add_argument("--cohorts", default=None, help="cohorts directory (read-only)")
    ap.add_argument("--skill", default=None, help="skill library directory")
    ap.add_argument("--polyglot", default=None, help="polyglot corpus directory")
    ap.add_argument("--config", default=None, help="little-coder.config.yaml")
    ap.add_argument("--src", default=None, help="prepend to sys.path (little-coder/src)")
    ap.add_argument(
        "--min-pool",
        type=int,
        default=3,
        help="judge min_pool_size; below this the judge is not called at all",
    )
    ap.add_argument(
        "--min-distinct",
        type=int,
        default=3,
        help="distinct signal texts a pool needs before it can carry a craft gap",
    )
    ap.add_argument(
        "--max-degenerate",
        type=float,
        default=0.34,
        help="max fraction of payload-free signals a mintable pool may contain",
    )
    ap.add_argument(
        "--require-ready",
        action="store_true",
        help="exit 1 when the verdict is NOT-READY (use as an enablement gate)",
    )
    ap.add_argument("--emit-prompts", action="store_true", help="include the real judge prompts")
    args = ap.parse_args(argv)

    if args.src:
        sys.path.insert(0, args.src)

    try:
        from littlecoder.cohorts import rebuild, load_checkpoint
        from littlecoder.meta import default_similarity
        from littlecoder.judge import build_messages
    except Exception as exc:  # noqa: BLE001 - any import failure is "cannot tell"
        _fail(EXIT_IMPORT, f"littlecoder is not importable: {exc!r}")

    journals = Path(args.journals)
    if not journals.is_dir():
        _fail(EXIT_NO_EVIDENCE, f"journals directory does not exist: {journals}")

    counts = _count_records(journals)
    if counts["total"] == 0:
        _fail(
            EXIT_NO_EVIDENCE,
            "no journal records on disk -- there is no evidence to judge",
            {"records": counts, "journals_dir": str(journals)},
        )

    # --- config (optional; defaults mirror the shipped config) -----------
    floor = 0.7
    store_filename = "cohort-store.json"
    fk_judge: list[str] = [
        "/app/agent-knowledge/environment.md",
        "/app/agent-knowledge/engineering-principles.md",
    ]
    fk_agent: list[str] = []
    judge_enabled = None
    config_note = "defaults (no --config given)"
    if args.config:
        try:
            from littlecoder.config import load_config

            cfg = load_config(args.config)
            floor = cfg.observer.similarity_floor
            store_filename = cfg.observer.store_filename
            fk_judge = list(cfg.observer.founding_knowledge_paths)
            fk_agent = _agent_knowledge_from_config(cfg)
            judge_enabled = bool(cfg.observer.judge_enabled)
            config_note = str(args.config)
        except Exception as exc:  # noqa: BLE001
            _fail(EXIT_IMPORT, f"config could not be loaded: {exc!r}", {"config": args.config})

    # --- prior store: the fidelity precondition --------------------------
    cohorts = Path(args.cohorts) if args.cohorts else None
    prior_clusters = 0
    store_path = None
    if cohorts and cohorts.is_dir():
        store_path = cohorts / store_filename
        if store_path.exists():
            try:
                prior = load_checkpoint(store_path)
                prior_clusters = len(prior.clusters)
            except Exception as exc:  # noqa: BLE001
                _fail(
                    EXIT_IMPORT,
                    f"cohort store exists but could not be read: {exc!r}",
                    {"store": str(store_path)},
                )
    if prior_clusters > 0:
        _fail(
            EXIT_STORE_HAS_CLUSTERS,
            (
                "the cohort store already holds %d cluster(s); with clusters in "
                "scope the stub-similarity projection is NOT what a judge-enabled "
                "daemon would see (clusters.py:144-150), so this dry run cannot "
                "tell you what the judge would be shown"
            )
            % prior_clusters,
            {"store": str(store_path), "prior_clusters": prior_clusters},
        )

    # --- the real projection --------------------------------------------
    store = rebuild(journals, default_similarity, floor)

    buckets = []
    for (lang, shape), bucket in sorted(store.unassigned.items()):
        texts = [o.signal_text for o in bucket.occurrences]
        degenerate = [t for t in texts if _is_degenerate(t)]
        distinct = sorted(set(texts))
        entry = {
            "lang": lang or "<empty>",
            "task_shape": shape or "<empty>",
            "pool_size": len(texts),
            "distinct_signals": len(distinct),
            "degenerate_signals": len(degenerate),
            "degenerate_ratio": round(len(degenerate) / len(texts), 3) if texts else 0.0,
            "judge_would_be_invoked": len(texts) >= args.min_pool,
            "sample": distinct[:8],
            "repos": sorted({o.repo for o in bucket.occurrences if o.repo}),
        }
        entry["mintable"] = bool(
            entry["judge_would_be_invoked"]
            and entry["distinct_signals"] >= args.min_distinct
            and entry["degenerate_ratio"] <= args.max_degenerate
        )
        if args.emit_prompts and entry["judge_would_be_invoked"]:
            msgs = build_messages(
                list(bucket.occurrences),
                lang=lang,
                task_shape=shape,
                founding_knowledge_paths=[Path(p) for p in fk_judge],
            )
            entry["prompt"] = [{"role": m.role, "content": m.content} for m in msgs]
        buckets.append(entry)

    occurrences_total = sum(b["pool_size"] for b in buckets)
    invoked = [b for b in buckets if b["judge_would_be_invoked"]]
    mintable = [b for b in buckets if b["mintable"]]
    unknown_shape = sum(
        b["pool_size"] for b in buckets if b["task_shape"] in ("unknown", "<empty>")
    )

    # --- blockers: every one is a measured fact, not an opinion ----------
    blockers = []
    if occurrences_total == 0:
        blockers.append(
            "the projection produced ZERO occurrences: journals exist but no task "
            "emitted a craft signal, so the judge would have nothing to mint from"
        )
    if not mintable:
        blockers.append(
            "no pool clears the mintable bar (pool>=%d, distinct>=%d, degenerate<=%.2f): "
            "every judge invocation would be asked to find a craft gap in noise"
            % (args.min_pool, args.min_distinct, args.max_degenerate)
        )
    if occurrences_total and unknown_shape == occurrences_total:
        blockers.append(
            "task_shape is 'unknown' for 100%% of occurrences (%d): scope collapses to "
            "(lang, unknown) so clusters cannot be shape-separated as design 5.5 requires"
            % occurrences_total
        )
    missing_fk = [p for p in fk_judge if not Path(p).is_file()]
    if missing_fk:
        blockers.append(
            "founding-knowledge file(s) the judge is configured to read are MISSING: %s "
            "-- build_messages skips them silently, so baseline_covers would be decided "
            "against a smaller floor than the agent actually has" % ", ".join(missing_fk)
        )
    if fk_agent and sorted(fk_agent) != sorted(fk_judge):
        blockers.append(
            "founding-knowledge MISMATCH: the agent reads %s but the judge is given %s "
            "-- the judge would call a gap 'not covered by the baseline' when the "
            "baseline covers it, minting tier-0 knowledge that restates instructions "
            "the agent already has" % (sorted(fk_agent), sorted(fk_judge))
        )
    polyglot = Path(args.polyglot) if args.polyglot else None
    polyglot_files = None
    if polyglot is not None:
        polyglot_files = len(list(polyglot.glob("*"))) if polyglot.is_dir() else 0
        if not polyglot_files:
            blockers.append(
                "the polyglot corpus is empty, so design 8.3 baseline variance is "
                "unmeasured and design 13 exit criterion 2 cannot be satisfied"
            )
    skill = Path(args.skill) if args.skill else None
    skill_files = None
    if skill is not None:
        skill_files = len([p for p in skill.rglob("*.md")]) if skill.is_dir() else 0

    verdict = "READY-FOR-RATING" if not blockers else "NOT-READY"

    report = {
        "status": "ok",
        "verdict": verdict,
        "judge_enabled_in_config": judge_enabled,
        "config": config_note,
        "journals_dir": str(journals),
        "records": counts,
        "prior_cohort_store": {
            "path": str(store_path) if store_path else None,
            "clusters": prior_clusters,
        },
        "projection": {
            "similarity": "littlecoder.meta.default_similarity (stub, always 0.0)",
            "similarity_floor": floor,
            "faithful_to_judge_enabled_run": True,
            "why": (
                "clusters.assign returns UNASSIGNED without calling similarity when no "
                "cluster shares the occurrence scope; the store holds 0 clusters"
            ),
        },
        "totals": {
            "occurrences": occurrences_total,
            "pools": len(buckets),
            "pools_judge_would_be_invoked_on": len(invoked),
            "pools_mintable": len(mintable),
            "occurrences_with_unknown_task_shape": unknown_shape,
        },
        "thresholds": {
            "min_pool": args.min_pool,
            "min_distinct": args.min_distinct,
            "max_degenerate": args.max_degenerate,
        },
        "founding_knowledge": {
            "judge_reads": fk_judge,
            "agent_reads": fk_agent,
            "missing_on_disk": missing_fk,
        },
        "skill_library_files": skill_files,
        "polyglot_corpus_files": polyglot_files,
        "pools": buckets,
        "blockers": blockers,
        "would_mint": None,
        "would_mint_note": (
            "UNKNOWABLE without an LLM call, by design. This dry run reports the pools "
            "the judge would be INVOKED on and how mintable they look. Design 13 exit "
            "criterion 3 is a HUMAN rating of the emitted prompts."
        ),
        "wrote_nothing": True,
    }
    json.dump(report, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    if verdict == "NOT-READY" and args.require_ready:
        return EXIT_NOT_READY
    return EXIT_OK


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main(sys.argv[1:]))
