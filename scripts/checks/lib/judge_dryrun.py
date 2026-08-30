"""Judge dry-run probe -- what the little-coder judge WOULD mint, minting nothing.

Companion to `scripts/checks/check-judge-dryrun.ps1` (that wrapper is the
supported entry point; this file is the engine). Written for
`documentation/implementation-guide/little-coder/JUDGE-CALIBRATION.md`, the
U5 calibration plan for `observer.judge_enabled`.

SCOPE, STATED FIRST SO NOTHING READS WIDER THAN IT IS
-----------------------------------------------------
This closes ONE of U5's three sub-items -- the `judge_enabled` calibration
plan. It is NOT the hook-bypass guard and NOT the personal-plane exclusion
drill; those are separate deliverables on separate branches. U5's "Validated
by" column is NOT satisfied by this file. See JUDGE-CALIBRATION.md section
"What this does NOT close".

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
library. That last sentence used to be an ASSERTION: the report carried a
hardcoded `"wrote_nothing": true`, which would have kept saying true after
any edit that made the probe write -- proved by injecting a write into a
copy of this file, which still reported true. It is now MEASURED: every
observed root is snapshotted before and after and the report carries the
diff. A violation is exit 8, not a footnote.

THREE ANSWERS THIS TOOL MUST NOT CONFLATE
-----------------------------------------
  1. "Nothing, because there are no journals to read"  -> exit 3, and
     `would_have_minted` says exactly that in one sentence.
  2. "Nothing, because the pools are noise"            -> verdict NOT-READY
     with the measured blocker; exit 0, or 1 under --require-ready.
  3. "N pools would be handed to the judge"            -> READY-FOR-RATING.
EVERY report carries `would_have_minted` as a plain sentence, the cannot-tell
reports included, so a caller that reads one field is never misled.

EMPTY IS NOT POISONED
---------------------
`skill_library_files` used to be a raw `*.md` count. That number cannot tell
a healthy two-skill library from two files the loader silently drops:
`skills.iter_skills` swallows `SkillFormatError` per file (skills.py:298-301),
so a corrupt artifact is invisible rather than loud. The report now carries a
`skill_library` block with a STATE (absent / empty / populated / poisoned),
loadable-vs-on-disk counts, the specific findings, and the remedy -- which
differs per state: an EMPTY library is the expected pre-enablement condition
and blocks nothing, while a POISONED one must be quarantined BEFORE the flip,
because the augmenter reads that directory into the agent's context.

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
  6  (wrapper only) CANNOT TELL -- container or probe unreachable
  7  MISCONFIGURED -- `observer.judge_enabled` is ALREADY true in the config
     under test and no valid rating record was supplied. The gate this tool
     exists to be was skipped. Always non-zero, --require-ready or not.
  8  INTEGRITY -- this probe's own read-only contract was violated: an
     observed root changed between the before and after snapshot.

"Cannot tell" is never reported as a pass. A dry run that does not know is
required to say so with a non-zero exit.

FIDELITY, precisely
-------------------
`clusters.assign` returns UNASSIGNED without ever calling the similarity
function when no cluster shares the occurrence's (lang, task_shape) scope
(`little-coder/src/littlecoder/clusters.py:144-150`). So while the store
holds ZERO clusters, this stub-similarity projection is bit-identical to
what the embedding-similarity projection of a judge-enabled daemon would
produce. The moment the store holds a cluster, that stops being true --
which is exit code 4, not a silent approximation.
"""

from __future__ import annotations

import argparse
import hashlib
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
EXIT_UNREACHABLE = 6  # wrapper-only; named here so the table is complete
EXIT_ALREADY_ENABLED = 7
EXIT_INTEGRITY = 8

# The three subdirectories `skills.write_skill` can produce (skills.py:57-61).
# Anything else under the skill root was not written by the skill library.
_SKILL_SUBDIRS = ("knowledge", "tools", "plan-slots")

# The rating-record rule is NOT defined here. Its one definition lives in
# littlecoder.judge_gate, which the daemon calls at boot and the pre-commit
# guard calls through lib/judge_flag_decide.py -- see read_rating_record below.


class _Cannot(Exception):
    """Raised to unwind to main's single exit point with a code + reason."""

    def __init__(self, code: int, reason: str, extra: dict | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.extra = extra or {}


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


# --- the read-only proof -------------------------------------------------
#
# Two classes, because they have different truth conditions:
#
#   HASHED roots (cohorts, skill, polyglot) are the surfaces a judge-enabled
#   daemon writes. Nothing should touch them during a dry run, so any add,
#   remove or content change is a violation. Full sha256 per file.
#
#   APPEND-ONLY roots (journals) are written continuously by the LIVE daemon
#   while we read. Growth there is the daemon, not us, and calling it a
#   violation would make the check fire on correct behaviour -- the failure
#   mode that gets guards switched off. So journals are recorded by
#   (name, size) and only SHRINK or DISAPPEARANCE counts, which is what a
#   rewrite or a truncation looks like.


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(root: Path, p: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


def _snapshot_hashed(root: Path | None) -> dict:
    if root is None or not root.is_dir():
        return {}
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            out[_rel(root, p)] = _hash_file(p)
        except OSError as exc:
            out[_rel(root, p)] = "<unreadable:%s>" % exc.errno
    return out


def _snapshot_sizes(root: Path | None) -> dict:
    if root is None or not root.is_dir():
        return {}
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            out[_rel(root, p)] = p.stat().st_size
        except OSError:
            out[_rel(root, p)] = -1
    return out


def _diff_hashed(label: str, before: dict, after: dict) -> list[str]:
    changes = []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b is None:
            changes.append(f"{label}: CREATED {rel}")
        elif a is None:
            changes.append(f"{label}: REMOVED {rel}")
        elif a != b:
            changes.append(f"{label}: MODIFIED {rel}")
    return changes


def _diff_append_only(label: str, before: dict, after: dict) -> list[str]:
    """Only shrink or disappearance is a violation -- see the note above."""
    changes = []
    for rel in sorted(before):
        if rel not in after:
            changes.append(f"{label}: REMOVED {rel}")
        elif after[rel] < before[rel]:
            changes.append(
                "%s: TRUNCATED %s (%d -> %d bytes)" % (label, rel, before[rel], after[rel])
            )
    return changes


# --- skill library classification ----------------------------------------


def _classify_skill_library(skill_dir: Path | None, store_cluster_ids: set | None) -> dict:
    """EMPTY and POISONED are different failures with different remedies, and a
    raw file count cannot tell them apart. This does.

    POISONED means at least one artifact in the library is one the loader will
    silently drop, or one whose provenance cannot be established:
      - unparseable frontmatter/body (`skills.parse_skill` raises; iter_skills
        swallows it per file, so the augmenter simply never sees it);
      - a leftover `*.tmp` from an interrupted atomic write (skills.py:264);
      - a `*.md` outside the three subdirectories `write_skill` can produce;
      - two artifacts claiming the same id;
      - an ACTIVE or PENDING artifact whose `cluster_id` is not in the cohort
        store, i.e. a skill nothing in this deployment's evidence minted.

    That last rule is deliberately narrow. This dry run only ever runs against
    a ZERO-cluster store (a non-empty one is exit 4), so "cluster_id not in the
    store" is true of EVERY artifact here -- a rule that always fires measures
    nothing. What is actually informative pre-enablement is a skill that will be
    SERVED (status active) or is AWAITING APPROVAL (pending) while no evidence
    on disk accounts for it. A retired or superseded one is inert -- list_skills
    defaults to status='active' (skills.py:310-319) -- so it is reported as a
    note, not as poisoning.
    """
    if skill_dir is None:
        return {
            "state": "not_observed",
            "path": None,
            "files_on_disk": None,
            "loadable": None,
            "unloadable": None,
            "findings": [],
            "notes": [],
            "remedy": "pass --skill (wrapper: -SkillPath) to classify the library",
        }
    if not skill_dir.is_dir():
        return {
            "state": "absent",
            "path": str(skill_dir),
            "files_on_disk": 0,
            "loadable": 0,
            "unloadable": 0,
            "findings": [],
            "notes": [],
            "remedy": (
                "the skill directory does not exist. write_skill() creates it on first "
                "mint, so this is not a blocker -- but confirm the little-coder-skill "
                "volume is mounted where the config says, or minted skills land in the "
                "container's writable layer and vanish on the next recreate."
            ),
        }

    try:
        from littlecoder.skills import parse_skill
    except Exception as exc:  # noqa: BLE001
        raise _Cannot(EXIT_IMPORT, f"littlecoder.skills is not importable: {exc!r}")

    findings: list[str] = []
    notes: list[str] = []
    on_disk: list[Path] = []
    loadable = 0
    ids: dict[str, str] = {}
    unaccounted: list[str] = []
    unaccounted_inert: list[str] = []

    # Strays first: anything the library's own writer could not have produced.
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = _rel(skill_dir, p)
        if p.name.endswith(".tmp"):
            findings.append(
                "STRAY TMP: %s -- an atomic write (skills.py:264) was interrupted; readers "
                "skip it, so it is invisible dead weight" % rel
            )
            continue
        if not p.name.endswith(".md"):
            continue
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in _SKILL_SUBDIRS:
            findings.append(
                "STRAY MD: %s -- outside the %s subdirs write_skill can produce "
                "(skills.py:57-61); something other than the skill library put it there"
                % (rel, "/".join(_SKILL_SUBDIRS))
            )
            continue
        on_disk.append(p)

    for p in on_disk:
        rel = _rel(skill_dir, p)
        try:
            skill = parse_skill(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - any parse failure is poison
            findings.append(
                "UNPARSEABLE: %s -- %s: %s. iter_skills swallows this per file "
                "(skills.py:298-301), so the augmenter silently never sees it"
                % (rel, type(exc).__name__, exc)
            )
            continue
        loadable += 1
        sid = skill.frontmatter.id
        if sid in ids:
            findings.append("DUPLICATE ID: %s and %s both claim id=%s" % (rel, ids[sid], sid))
        else:
            ids[sid] = rel
        if store_cluster_ids is not None and skill.frontmatter.cluster_id not in store_cluster_ids:
            entry = "%s (cluster_id=%s, status=%s)" % (
                rel,
                skill.frontmatter.cluster_id,
                skill.frontmatter.status,
            )
            if skill.frontmatter.status in ("active", "pending"):
                unaccounted.append(entry)
            else:
                unaccounted_inert.append(entry)

    if unaccounted:
        findings.append(
            "UNACCOUNTED ARTIFACT: %s -- status is active or pending, so the augmenter "
            "serves it or an operator is being asked to approve it, yet its cluster_id is "
            "not in the cohort store: no evidence in this deployment minted it"
            % ", ".join(unaccounted)
        )
    if unaccounted_inert:
        notes.append(
            "INERT ARTIFACT (not poisoning): %s -- retired or superseded, so list_skills "
            "does not select it (skills.py:310-319). Its cluster_id is not in the store, "
            "which pre-enablement just means it predates this store."
            % ", ".join(unaccounted_inert)
        )

    if findings:
        state = "poisoned"
        remedy = (
            "QUARANTINE BEFORE ENABLING. Move the named artifacts out of the skill root -- "
            "the augmenter reads that directory into the agent's context, and the loader "
            "drops the unparseable ones without a word -- then re-run this dry run. Do NOT "
            "flip judge_enabled first: a poisoned library makes design section 8 efficacy "
            "unattributable, because a new bad skill cannot be told from an old one."
        )
    elif not on_disk:
        state = "empty"
        remedy = (
            "NOTHING TO DO. An empty library is the EXPECTED pre-enablement state and "
            "blocks nothing. It does mean design section 8 efficacy has no baseline yet, "
            "so the first minted skills are the baseline."
        )
    else:
        state = "populated"
        remedy = (
            "no action -- every artifact parses, is uniquely identified, and nothing "
            "active or pending is unaccounted for."
        )

    return {
        "state": state,
        "path": str(skill_dir),
        "files_on_disk": len(on_disk),
        "loadable": loadable,
        "unloadable": len(on_disk) - loadable,
        "findings": findings,
        "notes": notes,
        "remedy": remedy,
    }


# --- rating record -------------------------------------------------------


def read_rating_record(path: Path) -> tuple[dict | None, str]:
    """Delegate to littlecoder.judge_gate.read_rating_record -- the ONE
    definition of a valid rating record, shared with the daemon's boot-time
    gate (meta_wiring -> judge_gate.require) and with the pre-commit guard
    (check-judge-flag.ps1 -> lib/judge_flag_decide.py).

    This function used to CARRY the rule. It no longer does, because a second
    copy of a rule is how a guard ends up disagreeing with the thing it
    guards -- the same drift that let a regex here say OFF while the daemon
    read ON. Requires --src, like every other littlecoder import in this
    module; without it the caller gets a cannot-tell rather than a guess.
    """
    try:
        from littlecoder.judge_gate import read_rating_record as _read
    except Exception as exc:  # noqa: BLE001
        raise _Cannot(
            EXIT_IMPORT,
            f"littlecoder.judge_gate is not importable, so the rating-record "
            f"rule cannot be evaluated: {exc!r}",
        ) from exc
    return _read(path)


# --- main ----------------------------------------------------------------


def build_report(args) -> tuple[dict, int]:
    """Everything except argument parsing and printing. Returns (report, exit)."""
    if args.src:
        sys.path.insert(0, args.src)

    try:
        from littlecoder.cohorts import load_checkpoint, rebuild
        from littlecoder.judge import build_messages
        from littlecoder.meta import default_similarity
    except Exception as exc:  # noqa: BLE001 - any import failure is "cannot tell"
        raise _Cannot(EXIT_IMPORT, f"littlecoder is not importable: {exc!r}")

    journals = Path(args.journals)
    if not journals.is_dir():
        raise _Cannot(
            EXIT_NO_EVIDENCE,
            "journals directory does not exist: %s -- there is nothing to read, so the "
            "answer to 'what would the judge have minted' is NOTHING, and that is a "
            "cannot-tell rather than a clean bill of health" % journals,
            {"journals_dir": str(journals)},
        )

    counts = _count_records(journals)
    if counts["total"] == 0:
        raise _Cannot(
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
            raise _Cannot(
                EXIT_IMPORT, f"config could not be loaded: {exc!r}", {"config": args.config}
            )

    rating = None
    rating_problem = ""
    if args.rating_record:
        rating, rating_problem = read_rating_record(Path(args.rating_record))

    # --- prior store: the fidelity precondition --------------------------
    cohorts = Path(args.cohorts) if args.cohorts else None
    prior_clusters = 0
    store_cluster_ids: set | None = None
    store_path = None
    if cohorts and cohorts.is_dir():
        store_path = cohorts / store_filename
        if store_path.exists():
            try:
                prior = load_checkpoint(store_path)
                prior_clusters = len(prior.clusters)
                store_cluster_ids = set(prior.clusters.keys())
            except Exception as exc:  # noqa: BLE001
                raise _Cannot(
                    EXIT_IMPORT,
                    f"cohort store exists but could not be read: {exc!r}",
                    {"store": str(store_path)},
                )
        else:
            store_cluster_ids = set()
    if prior_clusters > 0:
        raise _Cannot(
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

    skill = Path(args.skill) if args.skill else None
    polyglot = Path(args.polyglot) if args.polyglot else None

    # --- READ-ONLY PROOF: snapshot before ---------------------------------
    before_hashed = {
        "cohorts": _snapshot_hashed(cohorts),
        "skill": _snapshot_hashed(skill),
        "polyglot": _snapshot_hashed(polyglot),
    }
    before_sizes = _snapshot_sizes(journals)

    # --- the real projection --------------------------------------------
    store = rebuild(journals, default_similarity, floor)

    # DRILL ONLY. Deliberately writes into an observed root so verify-judge-dryrun.ps1
    # can prove the write DETECTOR fires -- a detector nobody has seen fire is not
    # known to detect anything. Not reachable from check-judge-dryrun.ps1: the wrapper
    # has no switch that emits this flag.
    if args.prove_write_detector:
        target = skill if skill is not None else cohorts
        if target is None or not target.is_dir():
            raise _Cannot(
                EXIT_USAGE,
                "--prove-write-detector needs an existing --skill or --cohorts directory",
            )
        (target / "PROVE-WRITE-DETECTOR.marker").write_text("drill", encoding="utf-8")

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
    degenerate_total = sum(b["degenerate_signals"] for b in buckets)
    invoked = [b for b in buckets if b["judge_would_be_invoked"]]
    mintable = [b for b in buckets if b["mintable"]]
    unknown_shape = sum(
        b["pool_size"] for b in buckets if b["task_shape"] in ("unknown", "<empty>")
    )

    skill_library = _classify_skill_library(skill, store_cluster_ids)

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
    polyglot_files = None
    if polyglot is not None:
        polyglot_files = len(list(polyglot.glob("*"))) if polyglot.is_dir() else 0
        if not polyglot_files:
            blockers.append(
                "the polyglot corpus is empty, so design 8.3 baseline variance is "
                "unmeasured and design 13 exit criterion 2 cannot be satisfied"
            )
    if skill_library["state"] == "poisoned":
        blockers.append(
            "the skill library is POISONED (%d artifact-level finding(s), first: %s). An "
            "EMPTY library would block nothing; a poisoned one must be quarantined first."
            % (len(skill_library["findings"]), skill_library["findings"][0])
        )

    verdict = "READY-FOR-RATING" if not blockers else "NOT-READY"

    # --- the plain answer, in every report shape --------------------------
    if not invoked:
        would = (
            "NOTHING. %d occurrence(s) across %d pool(s), and no pool reaches min_pool=%d, "
            "so Judge.mint_clusters would not be called at all."
            % (occurrences_total, len(buckets), args.min_pool)
        )
    elif not mintable:
        would = (
            "NOTHING WORTH MINTING. The judge would be invoked on %d pool(s), but none "
            "clears the signal bar (%d of %d occurrences carry a payload-free signal), so "
            "every invocation would ask an LLM to find a craft gap in noise."
            % (len(invoked), degenerate_total, occurrences_total)
        )
    else:
        would = (
            "%d pool(s) would be handed to the judge, %d of them clearing the signal bar. "
            "WHAT it would answer is unknowable without an LLM call, by design -- design 13 "
            "exit criterion 3 is a HUMAN rating of the prompts (--emit-prompts)."
            % (len(invoked), len(mintable))
        )

    # --- READ-ONLY PROOF: snapshot after and diff -------------------------
    after_hashed = {
        "cohorts": _snapshot_hashed(cohorts),
        "skill": _snapshot_hashed(skill),
        "polyglot": _snapshot_hashed(polyglot),
    }
    after_sizes = _snapshot_sizes(journals)
    changes: list[str] = []
    for label in ("cohorts", "skill", "polyglot"):
        changes += _diff_hashed(label, before_hashed[label], after_hashed[label])
    changes += _diff_append_only("journals", before_sizes, after_sizes)

    report = {
        "status": "ok",
        "verdict": verdict,
        "would_have_minted": would,
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
            "degenerate_signals": degenerate_total,
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
        "skill_library": skill_library,
        "polyglot_corpus_files": polyglot_files,
        "pools": buckets,
        "blockers": blockers,
        "read_only_proof": {
            "method": (
                "sha256 per file over cohorts/skill/polyglot before and after the "
                "projection; (name,size) over journals, where only shrink or "
                "disappearance counts because the live daemon appends while we read"
            ),
            "roots_observed": {
                "cohorts": str(cohorts) if cohorts else None,
                "skill": str(skill) if skill else None,
                "polyglot": str(polyglot) if polyglot else None,
                "journals": str(journals),
            },
            "files_hashed": sum(len(v) for v in before_hashed.values()),
            "journal_files_sized": len(before_sizes),
            "changes": changes,
        },
        # MEASURED, not asserted. This was a hardcoded literal until a verifier
        # pointed out it would keep saying true after an edit that made the probe
        # write -- which it did, when tested.
        "wrote_nothing": not changes,
        "rating_record": {
            "path": args.rating_record,
            "valid": bool(rating),
            "problem": rating_problem,
        },
    }

    if changes:
        report["status"] = "integrity_violation"
        report["verdict"] = "INTEGRITY-VIOLATION"
        report["would_have_minted"] = (
            "UNANSWERABLE. This probe's own read-only contract was violated -- an observed "
            "root changed during the run -- so nothing it reports can be trusted."
        )
        return report, EXIT_INTEGRITY

    if judge_enabled and not rating:
        report["verdict"] = "MISCONFIGURED"
        report["would_have_minted"] = (
            "MOOT -- observer.judge_enabled is already true, so the judge is not a "
            "hypothetical in this configuration. " + would
        )
        report["blockers"] = [
            (
                "observer.judge_enabled is ALREADY true in %s and no valid rating record "
                "was supplied%s. The gate this dry run exists to be was skipped: design 13 "
                "exit criterion 3 requires a HUMAN rating of the emitted prompts before the "
                "flip. Re-run with --rating-record <path> if that rating exists."
                % (config_note, (" (" + rating_problem + ")") if rating_problem else "")
            )
        ] + blockers
        return report, EXIT_ALREADY_ENABLED

    if verdict == "NOT-READY" and args.require_ready:
        return report, EXIT_NOT_READY
    return report, EXIT_OK


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
        "--rating-record",
        default=None,
        help="a human rating record (design 13 exit criterion 3); the only thing that "
        "makes judge_enabled=true legitimate to this tool",
    )
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
    ap.add_argument(
        "--prove-write-detector",
        action="store_true",
        help="DRILL ONLY: deliberately write into an observed root, to prove the "
        "read-only detector fires. Never use outside verify-judge-dryrun.ps1.",
    )
    args = ap.parse_args(argv)

    try:
        report, code = build_report(args)
    except _Cannot as exc:
        payload = {
            "status": "cannot_tell",
            "exit_code": exc.code,
            "reason": exc.reason,
            "would_have_minted": "NOTHING KNOWABLE -- " + exc.reason,
            # NOT True. The run aborted before the projection, so there is no
            # before/after snapshot pair and nothing was measured. Asserting
            # `true` here would reintroduce exactly the unmeasured claim this
            # field was fixed to stop making.
            "wrote_nothing": None,
            "read_only_proof": {
                "measured": False,
                "note": "aborted before the projection; no snapshot pair exists",
            },
        }
        payload.update(exc.extra)
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=True)
        sys.stdout.write("\n")
        return exc.code

    json.dump(report, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main(sys.argv[1:]))
