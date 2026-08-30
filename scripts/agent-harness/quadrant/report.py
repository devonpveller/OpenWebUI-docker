"""The side-by-side comparison - readable by a human, re-derivable by a script.

THE ONE STRUCTURAL RULE: THE REPORT IS BUILT FROM THE MATRIX, NEVER FROM THE RECORDS.

`render` walks the quadrants the configuration defines and emits a row for each. A
quadrant that produced no record gets "NOT RUN - no record produced". A quadrant whose
record failed admission gets "REFUSED" plus every reason. Nothing can leave the table by
being absent, because nothing enters it by being present.

That inversion is the whole point. The obvious implementation - iterate the records, print
a row each - produces a beautiful two-row table for a four-quadrant comparison and no
reader can tell. The count in the headline (COMPARED n/4) is derived from the same walk,
so the headline and the table cannot disagree.

WHAT THE TABLES CARRY, AND WHY THAT SET. "Did it complete" separates working from broken;
it does not help anyone CHOOSE. The decision table adds:

  * acceptance rate - quality against the SAME executable criteria in every cell. The only
    reason to run one item four times.
  * wall seconds and cost - what the operator pays. Reported as null when unmeasured,
    never as 0: a zero cost is a claim, an absent one is a fact.
  * rounds - dispatch attempts, test cycles, and OPERATOR TAPS. The gym's rule is that
    every tap is an orchestration bug, so a quadrant that finishes only because a human
    nudged it twice has not finished the way the other one did.
  * scope - files touched outside the anchor's allowance, and frozen files edited. A
    quadrant that "passes" by rewriting the test has told you something important.
  * containment - mechanical (git-proxy, egress allowlist, a container) vs normative
    (rules in a document). A7 is FALSIFIED on normative containment, so this belongs in
    the decision, not in a footnote.
  * confidence - n and, when n > 1, whether the repeats agreed. At n=1 the report says
    "n=1 - not a basis for a decision" in those words, because silence about sample size
    reads as adequacy.

And a closing section names what the comparison CANNOT tell you: every un-run quadrant
with its reason. An operator who reads only the last paragraph should still not be able to
mistake a partial comparison for a complete one.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import matrix as _matrix
from . import record as _record


class QuadrantReportError(RuntimeError):
    """The inputs cannot make an honest report - e.g. a record for a quadrant off the matrix."""


def _rows(quadrants: List["_matrix.Quadrant"], records: List[Dict[str, Any]],
          item: Dict[str, Any], s: Dict[str, Any]) -> List[Dict[str, Any]]:
    keys = {q.key for q in quadrants}
    for r in records:
        k = str(r.get("quadrant") or "")
        if k not in keys:
            raise QuadrantReportError(
                f"record names quadrant '{k}', which is not in this matrix "
                f"({', '.join(sorted(keys))}). Either the configuration changed under the "
                f"records or the records are from a different comparison; both make the "
                f"table a lie, so neither is rendered.")

    by_key: Dict[str, List[Dict[str, Any]]] = {q.key: [] for q in quadrants}
    for r in records:
        by_key[str(r["quadrant"])].append(r)

    digest = str(item.get("digest") or "")
    rows: List[Dict[str, Any]] = []
    for q in quadrants:
        recs = by_key[q.key]
        if not recs:
            rows.append({
                "key": q.key, "label": q.label, "runner": q.runner, "target": q.target,
                "status": "not_run", "compared": False, "n": 0,
                "why_not": "no record produced - this quadrant was never attempted",
                "problems": [], "acceptance": "-", "wall_seconds": None, "usd": None,
                "tokens": None, "rounds": None, "scope": None,
                "containment": _containment_class(q),
            })
            continue

        admitted, refused = [], []
        for r in recs:
            probs = _record.admit(r, item_digest=digest, schema=s)
            (admitted if not probs else refused).append((r, probs))

        if not admitted:
            rows.append({
                "key": q.key, "label": q.label, "runner": q.runner, "target": q.target,
                "status": "refused", "compared": False, "n": 0,
                "why_not": "record REFUSED at admission",
                "problems": [p for _, ps in refused for p in ps],
                "acceptance": "-", "wall_seconds": None, "usd": None, "tokens": None,
                "rounds": None, "scope": None, "containment": _containment_class(q),
            })
            continue

        first = admitted[0][0]
        status = str(first.get("status"))
        comparable = q.comparable and _record.is_comparable(first, q, s)
        outcome_recs = [r for r, _ in admitted
                        if s["statuses"].get(str(r.get("status")), {}).get("comparable")]
        why_not = ""
        if not q.comparable:
            why_not = q.incomparable_reason
        elif not outcome_recs:
            why_not = str(first.get("not_run_reason") or "did not produce an outcome")
        rows.append({
            "key": q.key, "label": q.label, "runner": q.runner, "target": q.target,
            "status": status,
            "compared": bool(comparable and outcome_recs),
            "n": len(outcome_recs),
            "why_not": why_not,
            "problems": [p for _, ps in refused for p in ps],
            "acceptance": _record.acceptance_rate(first) if outcome_recs else "-",
            "wall_seconds": _num(first.get("wall_seconds")) if outcome_recs else None,
            "usd": (first.get("cost") or {}).get("usd") if outcome_recs else None,
            "tokens": (first.get("cost") or {}).get("tokens") if outcome_recs else None,
            "rounds": first.get("rounds") if outcome_recs else None,
            "scope": first.get("scope") if outcome_recs else None,
            "containment": (first.get("containment") or {}).get("class")
                           or _containment_class(q),
            "agreement": _agreement(outcome_recs),
        })
    return rows


def _containment_class(q: "_matrix.Quadrant") -> str:
    """What ACTUALLY holds this quadrant, absent a measured guard event.

    Stated from the substrate rather than from a rule document, because A7's verdict is
    that normative containment is FALSIFIED - so "the protocol says not to" is the value
    'normative', which is information, not reassurance.
    """
    if q.runner_kind == "little-coder":
        return "mechanical (container, git-proxy, egress allowlist)"
    if q.runner_kind == "claude-code":
        return "normative (protocol rules) - A7: FALSIFIED as enforcement"
    return "n/a (scaffolding)"


def _num(v: Any) -> float | None:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _agreement(recs: List[Dict[str, Any]]) -> str:
    if len(recs) < 2:
        return ""
    rates = {_record.acceptance_rate(r) for r in recs}
    return "repeats agree" if len(rates) == 1 else f"repeats DISAGREE: {sorted(rates)}"


def summarize(quadrants: List["_matrix.Quadrant"], records: List[Dict[str, Any]], *,
              item: Dict[str, Any], schema: Dict[str, Any] | None = None) -> Dict[str, Any]:
    s = schema or _matrix.schema()
    rows = _rows(quadrants, records, item, s)
    compared = [r for r in rows if r["compared"]]
    return {
        "item": item.get("id"),
        "item_digest": item.get("digest"),
        "quadrants_total": len(quadrants),
        "compared": len(compared),
        "complete": len(compared) == len(quadrants),
        "rows": rows,
        "not_compared": [{"quadrant": r["label"], "status": r["status"],
                          "reason": r["why_not"], "problems": r["problems"]}
                         for r in rows if not r["compared"]],
        "min_repeats_for_variance": s["min_repeats_for_variance"],
    }


def render(quadrants: List["_matrix.Quadrant"], records: List[Dict[str, Any]], *,
           item: Dict[str, Any], schema: Dict[str, Any] | None = None) -> str:
    s = schema or _matrix.schema()
    summary = summarize(quadrants, records, item=item, schema=s)
    rows = summary["rows"]
    n_total, n_cmp = summary["quadrants_total"], summary["compared"]

    out: List[str] = []
    out.append(f"# Quadrant comparison - item `{item.get('id')}`")
    out.append("")
    out.append(f"**COMPARED {n_cmp}/{n_total}**"
               + ("" if summary["complete"] else
                  "  - this comparison is INCOMPLETE; the quadrants that did not run are"
                  " listed below with the reason each did not."))
    out.append("")
    out.append(f"Item digest `{str(item.get('digest') or '')[:16]}` - every record below "
               f"was checked against it, so a result from a different item cannot appear "
               f"in this table.")
    out.append("")

    out.append("## Outcome")
    out.append("")
    out.append("| quadrant | status | acceptance | wall s | rounds (dispatch/cycles/taps) | scope | containment |")
    out.append("|---|---|---|---|---|---|---|")
    for r in rows:
        rounds = r["rounds"] or {}
        rtxt = ("{}/{}/{}".format(rounds.get("dispatch_attempts", "-"),
                                  rounds.get("test_cycles", "-"),
                                  rounds.get("operator_taps", "-"))
                if r["rounds"] else "-")
        scope = r["scope"] or {}
        stxt = "-"
        if r["scope"]:
            hits = len(scope.get("out_of_scope_hits") or [])
            frozen = len(scope.get("frozen_touched") or [])
            stxt = f"{scope.get('files_changed', '-')} changed"
            if hits:
                stxt += f", {hits} OUT OF SCOPE"
            if frozen:
                stxt += f", {frozen} FROZEN EDITED"
        status = r["status"].upper() if not r["compared"] else r["status"]
        if r["status"] == "not_run":
            status = "NOT RUN"
        out.append(f"| {r['label']} | {status} | {r['acceptance']} | "
                   f"{r['wall_seconds'] if r['wall_seconds'] is not None else '-'} | {rtxt} | "
                   f"{stxt} | {r['containment']} |")
    out.append("")

    out.append("## Decision view")
    out.append("")
    if n_cmp == 0:
        out.append("_Nothing is comparable yet. No quadrant produced an admitted outcome, "
                   "so there is no decision to support - only the blockers below._")
    else:
        out.append("| quadrant | acceptance | cost (wall s / USD / tokens) | taps | confidence |")
        out.append("|---|---|---|---|---|")
        for r in rows:
            if not r["compared"]:
                continue
            taps = (r["rounds"] or {}).get("operator_taps", "-")
            conf = (f"n={r['n']}" + (" - not a basis for a decision"
                                     if r["n"] < s["min_repeats_for_variance"] else ""))
            if r.get("agreement"):
                conf += f"; {r['agreement']}"
            usd = r["usd"]
            cost = (f"{r['wall_seconds'] if r['wall_seconds'] is not None else '-'} / "
                    f"{round(usd, 4) if isinstance(usd, (int, float)) else 'null'} / "
                    f"{r['tokens'] if r['tokens'] is not None else 'null'}")
            out.append(f"| {r['label']} | {r['acceptance']} | {cost} | {taps} | {conf} |")
        out.append("")
        out.append("`null` cost means UNMEASURED, not free - a runner that does not report "
                   "a figure gets no figure invented for it.")
    out.append("")

    out.append("## What this comparison cannot tell you")
    out.append("")
    if summary["complete"]:
        out.append("Every configured quadrant produced an admitted outcome.")
    else:
        for nc in summary["not_compared"]:
            line = f"- **{nc['quadrant']}** - {nc['status'].upper()}: {nc['reason']}"
            out.append(line)
            for p in nc["problems"]:
                out.append(f"    - REFUSED: {p}")
    out.append("")
    out.append(f"Sample size: every cell above is a single run unless its confidence column "
               f"says otherwise. Below n={s['min_repeats_for_variance']} the harness cannot "
               f"separate a quadrant's behaviour from one run's luck, and it does not "
               f"pretend to.")
    out.append("")
    return "\n".join(out)
