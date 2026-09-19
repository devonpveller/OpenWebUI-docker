"""The anchored item under comparison - loaded, digested, and planted into a workspace.

WHY AN ITEM IS A THING AND NOT A PROMPT. U4 compares four quadrants by running the SAME
anchored item in each. "Same" is the experiment's only control, and a control nobody can
check is a control nobody has: two quadrants given subtly different task text produce a
comparison of the text, not of the quadrants. So an item is a directory with a spec, an
anchor validated by the shared schema, and a set of planted files - and it carries a
content digest over all three. `record.admit` refuses a record whose digest disagrees.

WHY THE ITEM IS PORTABLE RATHER THAN TARGET-SPECIFIC. The target axis is self (a worktree
of this repo, plane leases, this repo's branch policy) vs project (a clone the org is
pointed at). Those differ in ENVIRONMENT, not in task - so the item is planted under a
single prefix into whichever workspace the target adapter produced, and the same
acceptance commands run in both. Holding the task fixed is what makes the environment the
variable.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

import anchor_schema

from . import matrix as _matrix

HERE = Path(__file__).resolve().parent
ITEMS_DIR = HERE / "items"


class QuadrantItemError(ValueError):
    """The item is missing or malformed - a problem with the fixture, not with a run."""


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def digest(obj: Any) -> str:
    """Content address of any JSON-able object.

    Canonical form, so re-indenting item.json does not invent a "different item" and
    invalidate every record ever produced against it. Changing a WORD does.
    """
    return hashlib.sha256(_canonical(obj)).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(item_id: str, items_dir: Path | None = None) -> Dict[str, Any]:
    """Read + validate an item in one place, so no caller can hold an unvalidated one.

    Mirrors `anchor_schema.read_anchor_file`, deliberately: the harness already has one
    parse-and-validate-together pattern and a second dialect of it would be a second thing
    to keep true.
    """
    root = Path(items_dir or ITEMS_DIR) / item_id
    spec_path = root / "item.json"
    if not spec_path.is_file():
        raise QuadrantItemError(
            f"no such quadrant item: '{item_id}' (looked for {spec_path}). "
            f"Known items: {', '.join(sorted(p.name for p in Path(items_dir or ITEMS_DIR).glob('*') if p.is_dir())) or '(none)'}")
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise QuadrantItemError(f"item spec '{spec_path}' is not valid JSON: {exc}") from exc

    for field in ("id", "task", "plant_prefix", "anchor", "allowed_paths", "frozen_paths"):
        if field not in spec:
            raise QuadrantItemError(f"item '{item_id}' is missing required field '{field}'")
    if spec["id"] != item_id:
        raise QuadrantItemError(
            f"item '{item_id}' declares id '{spec['id']}' - the directory name is the id")

    anchor = anchor_schema.read_anchor_file(root / spec["anchor"])
    criteria = anchor_schema.executable_criteria(anchor)
    if not criteria:
        raise QuadrantItemError(
            f"item '{item_id}' has no EXECUTABLE acceptance criteria. PLAN C.7: only an "
            f"executable check counts, and a quadrant comparison scored by prose compares "
            f"the judges rather than the quadrants (A6, FALSIFIED).")

    files_dir = root / "files"
    if not files_dir.is_dir():
        raise QuadrantItemError(f"item '{item_id}' has no files/ directory to plant")
    ignore = _matrix.schema().get("item_plant_ignore") or []
    planted = {}
    for p in sorted(files_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(files_dir).as_posix()
        if any(fnmatch(part, pat) for part in rel.split("/") for pat in ignore):
            continue
        planted[rel] = file_digest(p)
    if not planted:
        raise QuadrantItemError(f"item '{item_id}' plants no files")

    for rel in spec["frozen_paths"]:
        bare = _strip_prefix(rel, spec["plant_prefix"])
        if bare not in planted:
            raise QuadrantItemError(
                f"item '{item_id}' freezes '{rel}', which it does not plant")

    return {
        "id": item_id,
        "dir": str(root),
        "spec": spec,
        "anchor": anchor,
        "task": spec["task"],
        "plant_prefix": spec["plant_prefix"],
        "allowed_paths": list(spec["allowed_paths"]),
        "frozen_paths": list(spec["frozen_paths"]),
        "criteria": criteria,
        "planted": planted,
        # The digest covers the spec, the anchor AND the bytes of every planted file. A
        # fixture edited without touching item.json is a different experiment, and this is
        # what makes that visible instead of silent.
        "digest": digest({"spec": spec, "anchor": anchor, "files": planted}),
    }


def _strip_prefix(rel: str, prefix: str) -> str:
    rel = rel.replace("\\", "/").lstrip("./")
    prefix = prefix.replace("\\", "/").strip("/")
    return rel[len(prefix) + 1:] if prefix and rel.startswith(prefix + "/") else rel


def plant(item: Dict[str, Any], workspace: Path) -> Dict[str, str]:
    """Copy the item's files into the workspace under its prefix.

    Returns the MANIFEST: workspace-relative path -> sha256 at plant time. The manifest is
    written outside the workspace by the caller, because a scope check whose baseline lives
    where the runner can edit it is not a check.
    """
    prefix = item["plant_prefix"].strip("/")
    dest_root = Path(workspace) / prefix if prefix else Path(workspace)
    manifest: Dict[str, str] = {}
    for rel, sha in item["planted"].items():
        src = Path(item["dir"]) / "files" / rel
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        manifest[(f"{prefix}/{rel}" if prefix else rel)] = sha
    return manifest


def expand(command: str, *, guards: str, item_id: str) -> str:
    """Expand the placeholders an acceptance command may use.

    `{guards}` becomes an absolute invocation of this package's guard runner. It is a
    placeholder rather than a literal because the item is COMMITTED and an absolute path in
    a committed file is wrong on every other machine - while the command RECORDED in a run
    record must be the exact expanded one, or "re-run it yourself" is not an offer anyone
    can take up.
    """
    return command.replace("{guards}", guards).replace("{item}", item_id)


def known_items(items_dir: Path | None = None) -> List[str]:
    root = Path(items_dir or ITEMS_DIR)
    return sorted(p.name for p in root.glob("*") if (p / "item.json").is_file())
