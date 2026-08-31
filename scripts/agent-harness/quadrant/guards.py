"""The acceptance checks, run by the harness FROM OUTSIDE the workspace.

WHY THE CHECKS DO NOT LIVE IN THE WORKSPACE. Everything inside the workspace is writable by
the runner under test. A test file the runner can edit is a test file the runner can pass
by editing, and the cheapest way to "solve" any item is to weaken its checks. So the
pristine copies stay in the item directory and these guards run them from there:

  tests       runs the PRISTINE test module against the workspace's implementation. Immune
              to any edit the runner made to the workspace copy of the tests.
  unmodified  asserts the workspace's frozen files still match the pristine bytes.

Both are needed and they are not redundant. `tests` alone would report a green for a run
that solved the item AND rewrote the tests; `unmodified` is what separates "solved it"
from "solved it and also tried to cheat" - and for a quadrant COMPARISON that distinction
is a first-class result, not an aside.

Invoked as an absolute path (item.expand's `{guards}`), with the workspace as CWD:

    python <harness>/quadrant/guards.py tests --item u4-baseline
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

# Belt to item.py's braces: running a pristine test module would otherwise leave a
# __pycache__ inside the ITEM directory, which is the control of the experiment.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quadrant import item as item_mod  # noqa: E402


def _pristine(it: dict, workspace_rel: str) -> Path:
    """The item's own copy of a planted file, by its workspace-relative path."""
    bare = item_mod._strip_prefix(workspace_rel, it["plant_prefix"])
    return Path(it["dir"]) / "files" / bare


def cmd_unmodified(it: dict, workspace: Path) -> int:
    bad = []
    for rel in it["frozen_paths"]:
        here = workspace / rel
        if not here.is_file():
            bad.append(f"{rel}: MISSING from the workspace")
            continue
        if item_mod.file_digest(here) != item_mod.file_digest(_pristine(it, rel)):
            bad.append(f"{rel}: MODIFIED - the item forbids editing it")
    for line in bad:
        print(f"frozen file violation: {line}")
    if bad:
        return 1
    print(f"{len(it['frozen_paths'])} frozen file(s) unmodified")
    return 0


def cmd_tests(it: dict, workspace: Path) -> int:
    """Run every ``test_*`` function in the item's pristine test modules.

    Deliberately not pytest. The workspace may be a checkout of this repo (target `self`),
    where a pytest run would pick up the repo's own configuration, its conftest files and
    its plugins - and a comparison whose acceptance check behaves differently in one target
    than in the other is measuring the harness, not the quadrants.
    """
    prefix = it["plant_prefix"].strip("/")
    impl_dir = str((workspace / prefix) if prefix else workspace)
    sys.path.insert(0, impl_dir)

    failures, total = [], 0
    for rel in it["frozen_paths"]:
        src = _pristine(it, rel)
        if not src.name.startswith("test_"):
            continue
        spec = importlib.util.spec_from_file_location(f"_pristine_{src.stem}", src)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 - a broken import is a failed item, not a crash
            failures.append(f"{rel}: could not import the workspace implementation\n"
                            + traceback.format_exc())
            continue
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            total += 1
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{rel}::{name}: {type(exc).__name__}: {exc}")

    for f in failures:
        print(f"FAIL {f}")
    print(f"{total - len(failures)}/{total} pristine test(s) passed")
    return 1 if (failures or total == 0) else 0


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--item":
        print("usage: guards.py <tests|unmodified> --item <item-id> [--items-dir DIR]")
        return 2
    cmd, item_id = argv[0], argv[2]
    items_dir = None
    if "--items-dir" in argv:
        items_dir = Path(argv[argv.index("--items-dir") + 1])
    it = item_mod.load(item_id, items_dir)
    workspace = Path.cwd()
    if cmd == "unmodified":
        return cmd_unmodified(it, workspace)
    if cmd == "tests":
        return cmd_tests(it, workspace)
    print(f"unknown guard '{cmd}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
