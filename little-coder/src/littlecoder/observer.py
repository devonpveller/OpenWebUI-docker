"""Observer report rendering for the operator surface (design §3f, Chapter 3).

The data is in the cohort store (`cohorts.py`); this module shapes it into
the JSON dict the daemon's `/admin/observe` endpoint returns and the
strings `lc admin observe` / `/observe` render.

The single contract this layer pins down: reports distinguish KNOWLEDGE
GAPS from COMPLIANCE GAPS (locked decision #17). A cluster's
`baseline_covers` flag is the dividing line — operators reading the
report need to see which clusters would tier-1-enforce (baseline covers
them; the agent isn't following) vs which would tier-0-add (baseline
silent; the agent needs new knowledge).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .cohorts import CohortStore
from .meta import IterationResult


@dataclasses.dataclass(frozen=True)
class ClusterSummary:
    """One row of the operator's cluster report."""

    cluster_id: str
    label: str
    lang: str
    task_shape: str
    observed: int
    inherited: int
    baseline_covers: bool
    # Top three repos by occurrence count, for at-a-glance drill-down.
    top_repos: list[tuple[str, int]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class UnassignedSummary:
    """One row of the unassigned-pool report."""

    lang: str
    task_shape: str
    size: int


def summarize_store(
    store: CohortStore,
) -> tuple[list[ClusterSummary], list[ClusterSummary], list[UnassignedSummary]]:
    """Roll a `CohortStore` into three lists — knowledge-gap clusters
    (baseline silent), compliance-gap clusters (baseline covers), and
    unassigned-bucket sizes. The split is the §17 distinction made
    visible at the surface."""
    knowledge: list[ClusterSummary] = []
    compliance: list[ClusterSummary] = []
    for cluster_id, cluster in store.clusters.items():
        counter = store.counters.get(cluster_id)
        observed = counter.observed if counter else 0
        inherited = counter.inherited if counter else 0
        repos = (
            sorted(counter.per_repo_observed.items(), key=lambda kv: kv[1], reverse=True)[:3]
            if counter
            else []
        )
        row = ClusterSummary(
            cluster_id=cluster_id,
            label=cluster.label,
            lang=cluster.lang,
            task_shape=cluster.task_shape,
            observed=observed,
            inherited=inherited,
            baseline_covers=cluster.baseline_covers,
            top_repos=[(r, n) for r, n in repos],
        )
        (compliance if cluster.baseline_covers else knowledge).append(row)

    unassigned = [
        UnassignedSummary(lang=b.lang, task_shape=b.task_shape, size=b.size)
        for b in store.unassigned.values()
    ]
    # Sort each list by occurrence count (most prominent first).
    knowledge.sort(key=lambda r: r.observed, reverse=True)
    compliance.sort(key=lambda r: r.observed, reverse=True)
    unassigned.sort(key=lambda u: u.size, reverse=True)
    return knowledge, compliance, unassigned


def report_dict(
    store: CohortStore,
    last_result: IterationResult | None,
) -> dict[str, Any]:
    """Build the JSON-safe report dict the daemon returns from
    `/admin/observe`. The Pipe and the CLI both render this same shape."""
    knowledge, compliance, unassigned = summarize_store(store)
    return {
        "last_iteration": (
            {
                "ts": last_result.ts,
                "records_consumed": last_result.records_consumed,
                "clusters_total": last_result.clusters_total,
                "occurrences_total": last_result.occurrences_total,
                "unassigned_total": last_result.unassigned_total,
                "minted_cluster_ids": list(last_result.minted_cluster_ids),
            }
            if last_result
            else None
        ),
        "knowledge_gaps": [dataclasses.asdict(r) for r in knowledge],
        "compliance_gaps": [dataclasses.asdict(r) for r in compliance],
        "unassigned": [dataclasses.asdict(u) for u in unassigned],
    }


def render_text(report: dict[str, Any]) -> str:
    """Render the report dict for terminal output. Used by `lc admin
    observe` and the `/observe` Pipe slash-command."""
    lines: list[str] = []
    li = report.get("last_iteration")
    if li:
        lines.append(f"last iteration @ {li['ts']}")
        lines.append(
            f"  records={li['records_consumed']} "
            f"clusters={li['clusters_total']} "
            f"observed={li['occurrences_total']} "
            f"unassigned={li['unassigned_total']}"
        )
        if li.get("minted_cluster_ids"):
            lines.append(f"  minted this run: {len(li['minted_cluster_ids'])}")
    else:
        lines.append("(no iteration has completed yet)")

    def _cluster_block(title: str, rows: list[dict[str, Any]]) -> None:
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        if not rows:
            lines.append("  (none)")
            return
        for r in rows:
            lines.append(
                f"  [{r['cluster_id'][:8]}] {r['lang']} | {r['task_shape']} "
                f"— observed={r['observed']} (inherited={r['inherited']})"
            )
            lines.append(f"      {r['label']}")
            if r.get("top_repos"):
                joined = ", ".join(f"{repo}={n}" for repo, n in r["top_repos"])
                lines.append(f"      top repos: {joined}")

    _cluster_block(
        "KNOWLEDGE GAPS (baseline silent — tier-0 candidates)",
        report.get("knowledge_gaps") or [],
    )
    _cluster_block(
        "COMPLIANCE GAPS (baseline covers — tier-1 enforcement)",
        report.get("compliance_gaps") or [],
    )

    lines.append("")
    lines.append(f"unassigned scopes ({len(report.get('unassigned') or [])})")
    for u in report.get("unassigned") or []:
        lines.append(f"  {u['lang']} | {u['task_shape']} — pool size {u['size']}")
    return "\n".join(lines)
