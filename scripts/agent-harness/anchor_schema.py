"""Python reader for the anchor schema — the twin of ``anchor.ps1``.

Same file (``anchor.schema.json``), same modes, same validation semantics, so an anchor
means one thing no matter who reads it. ``test_anchor_schema.py`` pins the two together by
feeding both readers the same anchors and comparing the PROBLEMS, not just pass/fail —
two readers that agree on "invalid" while disagreeing on *why* have already drifted.

Why a second reader exists at all: the anchor is the org's intent object, and PowerShell
cannot be the only thing that knows its shape. The sessions bridge needs it to draft and
lint anchors, and agent-org needs it to consume them at the ``set_goal`` seam. (agent-org
cannot read this file yet — its Docker build context is ``agent-org/agent-bridge/`` and
cannot reach ``scripts/`` — which is a delivery problem recorded in the findings, not one
this module pretends to solve.)

    python -m pytest scripts/agent-harness/test_anchor_schema.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "anchor.schema.json"

_CACHE: Dict[str, Any] | None = None


class AnchorSchemaError(ValueError):
    """The schema itself is missing or unusable — not a problem with someone's anchor."""


def load(fresh: bool = False, path: Path | None = None) -> Dict[str, Any]:
    """Read the schema. Raises rather than falling back to a built-in copy.

    A silent fallback is exactly how the two readers would drift apart without anything
    failing, so there deliberately is no default shape in this file.
    """
    global _CACHE
    if _CACHE is not None and not fresh and path is None:
        return _CACHE
    p = path or SCHEMA_PATH
    if not p.is_file():
        raise AnchorSchemaError(
            f"anchor schema not found: '{p}'. It defines what an anchor is; "
            "without it nothing can validate one.")
    try:
        schema = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnchorSchemaError(f"anchor schema '{p}' is not valid JSON: {exc}") from exc
    if path is None:
        _CACHE = schema
    return schema


def default_mode(schema: Dict[str, Any] | None = None) -> str:
    return str((schema or load())["default_mode"])


def mode_of(anchor: Dict[str, Any] | None, schema: Dict[str, Any] | None = None) -> str:
    """The anchor's mode. Absent means the default (B).

    Every anchor written before the schema existed has no ``mode`` field and all of them
    stay valid — this was an extension, not a migration.
    """
    s = schema or load()
    if not isinstance(anchor, dict):
        return default_mode(s)
    raw = anchor.get("mode")
    if raw is None or not str(raw).strip():
        return default_mode(s)
    return str(raw).strip().upper()


def field_help(mode: str = "", schema: Dict[str, Any] | None = None) -> str:
    s = schema or load()
    mode = mode or default_mode(s)
    spec = s["modes"].get(mode)
    if spec is None:
        return f"  (unknown mode '{mode}' - known modes: {', '.join(s['modes'])})"
    lines = [f"  mode {mode} - {spec['desc']}"]
    for name, f in spec["fields"].items():
        req = "required" if f.get("required") else "optional"
        lines.append(f"  {name:<14} {'(' + req + ')':<8} {f['why']}")
    for name, why in spec.get("forbidden", {}).items():
        lines.append(f"  {name:<14} {'(REFUSED)':<8} {why}")
    return "\n".join(lines)


def _nonempty_items(value: Any) -> List[Any]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [i for i in items if i is not None and str(i).strip()]


def problems(anchor: Any, schema: Dict[str, Any] | None = None) -> List[str]:
    """Everything wrong with this anchor; empty when it is usable.

    Returns rather than raises, mirroring ``Test-Anchor``: the caller decides whether a bad
    anchor is fatal (the queue) or merely reported (an agent linting its own draft).
    """
    s = schema or load()
    out: List[str] = []
    # `{}` is NOT "empty" — it is a dict with every field missing, and saying which fields
    # are missing helps the author more than saying the anchor is empty. Only a null/
    # non-object anchor gets the blunt message. (This exact line disagreed with anchor.ps1
    # until the cross-reader test caught it.)
    if not isinstance(anchor, dict):
        return ["the anchor is empty"]

    mode = mode_of(anchor, s)
    spec = s["modes"].get(mode)
    if spec is None:
        # A typo in `mode` must be loud. Defaulting an unknown mode to B would validate a
        # generative anchor against a bounded contract and pass it for the wrong reasons.
        return [f"unknown anchor mode '{mode}' - known modes: {', '.join(s['modes'])}"]

    for name, f in spec["fields"].items():
        value = anchor.get(name)
        if f["kind"] == "list":
            if f.get("required") and len(_nonempty_items(value)) < 1:
                out.append(f"'{name}' must list at least one entry - {f['why']}")
        else:
            if f.get("required") and (value is None or not str(value).strip()):
                out.append(f"'{name}' is required - {f['why']}")

    # Fields this mode REFUSES. Mode A rejecting `acceptance` is the load-bearing case: a
    # category error, not a style preference (see the schema's note on gym-024).
    for name, why in spec.get("forbidden", {}).items():
        if name in anchor and _nonempty_items(anchor.get(name)):
            out.append(f"mode {mode} anchors must not carry '{name}' - {why}")

    # An acceptance criterion nobody can check is a wish. This catches the common shape -
    # a single vague line - without pretending to judge English.
    min_len = int(s["rules"]["min_acceptance_criterion_chars"])
    if "acceptance" in anchor and "acceptance" in spec["fields"]:
        for c in _nonempty_items(anchor.get("acceptance")):
            if len(str(c).strip()) < min_len:
                out.append(
                    f"acceptance criterion '{c}' is too short to check - say what "
                    "would count as failing it")
    return out


def is_usable(anchor: Any, schema: Dict[str, Any] | None = None) -> bool:
    return not problems(anchor, schema)


def read_anchor_file(path: str | Path, schema: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Parse + validate in one place, so no caller can hold an unvalidated anchor."""
    p = Path(path)
    if not p.is_file():
        raise AnchorSchemaError(
            f"anchor file not found: '{p}'. Start from anchor.template.json in "
            f"{HERE.name} - the fields are:\n{field_help()}")
    try:
        anchor = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AnchorSchemaError(f"anchor file '{p}' is not valid JSON: {exc}") from exc
    found = problems(anchor, schema)
    if found:
        # Show the help for the mode the author ACTUALLY declared, not the default.
        raise AnchorSchemaError(
            f"the anchor in '{p}' is not usable:\n  - " + "\n  - ".join(found)
            + "\n\nFields:\n" + field_help(mode_of(anchor, schema or load())))
    return anchor
