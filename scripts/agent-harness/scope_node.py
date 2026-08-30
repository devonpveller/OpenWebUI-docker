"""A harness queue item, expressed as a depth-1 ScopeNode (dark-factory-unification U2).

U2 is intent unification: the two systems stop describing a unit of work in two vocabularies
that happen to line up. agent-org already has the shape — `ScopeNode`, the tiered-scope model
from ORCHESTRATION-DESIGN §4, where a project is the root and the work decomposes downward.
A harness queue item is exactly one tier below the project: **depth 1**.

WHY DEPTH 1 AND NOT 0. Depth 0 is the project itself. An item is a bounded piece of work
inside a project, handed to one developer who is "deliberately unaware of the rest" — which
is precisely what a worktree is. The harness has been building depth-1 scope nodes since it
existed; it just called them queue items.

WHAT THIS IS NOT. It does not write to agent-org's database and does not import from it. The
projection is local to the harness and produces a plain dict; whether anything persists it is
a separate decision that U2 does not need made. What matters here is that the SHAPE is the
same one, and that it cannot drift from the model without a test failing —
`test_scope_node.py` reads agent-org's `models.py` and asserts every column of the real
`ScopeNode` is either produced here or explicitly declined with a reason.

That last part is the whole value. A mapping written once and never checked is two
vocabularies again within a month, and this time it would LOOK unified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

#: Columns of agent-org's ScopeNode this projection deliberately does not produce, with why.
#: The drift test requires an entry here for every column it does not find in the output, so
#: a column added upstream fails loudly instead of being silently unmapped.
DECLINED: Dict[str, str] = {
    "created_at": "the queue item's own history carries its timestamps; a second one here "
                  "would be the time the projection ran, which is not a fact about the work",
}

#: Queue states that mean the scope is settled, mapped to ScopeNode's status vocabulary
#: (open | done | blocked). Anything unlisted is 'open' - the conservative direction, since
#: calling unfinished work 'done' is the error that matters.
_STATUS = {
    "merged": "done",
    "rejected": "blocked",
    "failed": "blocked",
}


def _first_sentence(text: str, limit: int = 200) -> str:
    """A title from prose. ScopeNode.title is String(200)."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    for stop in (". ", " - ", " — "):
        if stop in t[:limit + 40]:
            t = t.split(stop)[0]
            break
    return t[:limit].rstrip()


def from_queue_item(item: Dict[str, Any], *, project_slug: str = "") -> Dict[str, Any]:
    """Project one queue item into a depth-1 ScopeNode dict. PURE.

    The interesting fields are `scope` and `contract`, and they come from the ANCHOR rather
    than from the item's own metadata:

      scope     — "what IS and ISN'T this tier's business". That is the anchor's `artifact`
                  plus its `out_of_scope`, which is the same question asked twice in the
                  vocabulary the harness already had.
      contract  — "the executable check that this scope is satisfied". That is `acceptance`.
                  §11's point is that a boundary defined in prose is not encapsulation, so
                  an item with no acceptance yields NO contract rather than a prose stand-in.

    An item without an anchor projects with empty scope/contract rather than raising: the
    queue holds pre-anchor rows, and a projection that refused them would be unusable for
    exactly the items a reviewer most wants to see.
    """
    anchor = item.get("anchor") or {}
    if not isinstance(anchor, dict):
        anchor = {}

    artifact = str(anchor.get("artifact") or "").strip()
    out_of_scope = anchor.get("out_of_scope")
    if isinstance(out_of_scope, str):
        out_of_scope = [out_of_scope]
    out_items = [str(x).strip() for x in (out_of_scope or []) if str(x).strip()]

    scope_parts: List[str] = []
    if artifact:
        scope_parts.append("IS: " + artifact)
    if out_items:
        scope_parts.append("IS NOT: " + "; ".join(out_items))

    acceptance = anchor.get("acceptance")
    if isinstance(acceptance, str):
        acceptance = [acceptance]
    acc_items = [str(x).strip() for x in (acceptance or []) if str(x).strip()]

    title = _first_sentence(str(anchor.get("goal") or "")) or str(item.get("id") or "")

    return {
        "id": str(item.get("id") or ""),
        "project_slug": project_slug or str(item.get("line") or ""),
        # None = the project root. A queue item hangs directly off its project; the harness
        # has no intermediate tier, and inventing one would claim a decomposition nobody made.
        "parent_id": None,
        "depth": 1,
        "title": title,
        "scope": "\n".join(scope_parts),
        "contract": "\n".join(acc_items) or None,
        "status": _STATUS.get(str(item.get("state") or ""), "open"),
        # The worktree IS the isolated context a node is worked in - the same role effort_id
        # plays for agent-org.
        "effort_id": str(item.get("developer") or "") or None,
    }


def load_queue(queue_dir: str | Path) -> List[Dict[str, Any]]:
    """Every work item in a queue directory, oldest name first.

    `<id>.anchor.json` and `<id>.plan.md` sit beside `<id>.json`. Ids are `[a-z0-9-]`, so a
    dot in the base name means a sidecar rather than a work item - the same rule queue.ps1's
    -List uses, and it has to stay the same rule or the two disagree about what an item is.
    """
    d = Path(queue_dir)
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(d.glob("*.json")):
        if "." in f.stem:
            continue
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            # A corrupt row must not hide the rest of the queue from a reviewer.
            continue
    return out


def project_queue(queue_dir: str | Path, *, project_slug: str = "") -> List[Dict[str, Any]]:
    return [from_queue_item(i, project_slug=project_slug) for i in load_queue(queue_dir)]


def _main(argv: List[str]) -> int:
    """`python scope_node.py <queue-dir> [project-slug]` -> the projection as JSON.

    A real entry point rather than a `python -c` snippet in queue.ps1. The snippet version
    broke twice for reasons that had nothing to do with the projection: Windows argument
    handling stripped the quotes out of a string literal, and then the repo path (which
    contains a space) split across argv. Running a FILE makes sys.path[0] the module's own
    directory and passes one path instead of three.
    """
    if not argv:
        print("usage: scope_node.py <queue-dir> [project-slug]")
        return 2
    slug = argv[1] if len(argv) > 1 else str()
    print(json.dumps(project_queue(argv[0], project_slug=slug), indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
