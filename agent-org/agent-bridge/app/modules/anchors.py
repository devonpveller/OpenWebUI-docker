"""agent-org's side of the SHARED ANCHOR (dark-factory-unification U2).

One intent object, two systems. The harness gates an item on an anchor before work starts;
agent-org has carried the same idea under a different name for months — the North Star, "the
ORIGINAL prompt, never a re-derived scope goal". U2's point is that those stop being two
vocabularies that happen to agree.

MODE A, not mode B. The schema has two: B is a specified deliverable (goal / artifact /
audience / acceptance / out_of_scope), A is "generative discovery: a theme walked toward,
not a spec exhausted" (north_star / audience). An agent-org effort is the second thing —
that is why §6.6 keeps the original prompt verbatim and re-aligns every round against it
rather than against whatever the scope has drifted into.

THE SCHEMA IS READ, NEVER RESTATED. `anchor_schema.py` and `anchor.schema.json` are
bind-mounted read-only at `/app/anchor/` — the SAME files the harness reads, not copies.
A field table written out here would be a third definition of what an anchor is, and the
cross-reader test exists precisely because two definitions drift while both look right.

If the mount is absent — a dev checkout, a test run, an image someone built before the
compose change — this module degrades to inert rather than guessing the shape. `available()`
says which, so a caller can tell "no anchor" from "no reader".
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Where docker-compose mounts the canonical schema and its reader.
ANCHOR_DIR = "/app/anchor"

_reader: Any | None = None
_tried = False


def _load_reader() -> Any | None:
    """Import the mounted reader once. Never raises."""
    global _reader, _tried
    if _tried:
        return _reader
    _tried = True
    try:
        # LOADED BY EXPLICIT PATH, not by `import anchor_schema` off sys.path.
        #
        # A plain import finds A reader; this has to find THE MOUNTED one. Anything else on
        # sys.path with that name would be picked up instead, and the whole point of the
        # mount is that there is exactly one definition of an anchor. It also makes the
        # "no mount" state real rather than incidental: without the file, this fails,
        # instead of quietly succeeding because a copy happened to be importable.
        import importlib.util

        mod_path = Path(ANCHOR_DIR) / "anchor_schema.py"
        spec = importlib.util.spec_from_file_location("_mounted_anchor_schema", mod_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loadable anchor_schema at {mod_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.load()  # fail here, not at first use, if the JSON is unreadable
        _reader = module
    except Exception as exc:  # noqa: BLE001
        # Not a warning: on a dev checkout this is the expected state, and a warning that
        # fires every boot on a normal machine is one people learn to scroll past.
        log.debug("anchor schema unavailable at %s (%s) - anchors are inert", ANCHOR_DIR, exc)
        _reader = None
    return _reader


def available() -> bool:
    """True when the shared schema can be read. Lets a caller tell 'no anchor' from 'no reader'."""
    return _load_reader() is not None


def build_effort_anchor(*, north_star: str, audience: str, constraints: list | None = None) -> dict:
    """The mode-A anchor for one effort. PURE — no schema needed to construct it.

    `north_star` is the operator's ORIGINAL prompt, verbatim. Not a summary, not the scope
    goal: §6.6 keeps the original because a re-derived one drifts, and an anchor built from
    a drifted goal would certify the drift instead of catching it.
    """
    anchor: dict = {
        "mode": "A",
        "north_star": (north_star or "").strip(),
        "audience": (audience or "").strip(),
    }
    items = [c for c in (constraints or []) if isinstance(c, str) and c.strip()]
    if items:
        anchor["constraints"] = items
    return anchor


def problems(anchor: dict) -> list:
    """What is wrong with this anchor, per the SHARED schema. [] when it is usable.

    Returns [] when the reader is unavailable — deliberately, and it is why `available()`
    exists. Reporting invented problems from a module that cannot read the schema would be
    worse than reporting none: a caller would act on a verdict nothing produced.
    """
    reader = _load_reader()
    if reader is None:
        return []
    try:
        return list(reader.problems(anchor))
    except Exception as exc:  # noqa: BLE001
        log.debug("anchor validation failed: %s", exc)
        return []


def is_usable(anchor: dict) -> bool:
    """True when the shared schema accepts this anchor. False when the reader is missing.

    The asymmetry with `problems()` is deliberate. "Are there problems?" answers [] when
    nothing can judge; "is this usable?" must answer NO, because a caller asking that is
    about to rely on the anchor and an unreadable schema is not permission.
    """
    reader = _load_reader()
    if reader is None:
        return False
    try:
        return bool(reader.is_usable(anchor))
    except Exception as exc:  # noqa: BLE001
        log.debug("anchor usability check failed: %s", exc)
        return False


def render(anchor: dict) -> str:
    """The anchor as a goal preamble, for a worker to read. '' when there is nothing to say.

    Guard substring "EFFORT ANCHOR" so an injection site can be idempotent, matching every
    other context block in the orchestrator.
    """
    if not anchor:
        return ""
    ns = (anchor.get("north_star") or "").strip()
    aud = (anchor.get("audience") or "").strip()
    if not ns:
        return ""
    out = [
        "\n\nEFFORT ANCHOR — what this work is FOR. Every round realigns against THIS, "
        "not against a re-derived scope goal.",
        f"  NORTH STAR : {ns}",
    ]
    if aud:
        out.append(f"  AUDIENCE   : {aud}")
    for c in anchor.get("constraints") or []:
        out.append(f"  CONSTRAINT : {c}")
    return "\n".join(out)
