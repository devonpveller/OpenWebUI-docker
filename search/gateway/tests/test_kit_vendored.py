"""The vendored connector kit (src/connector_kit) is OB1's, byte for byte, at the gitlink.

Skipped without the OB1 checkout (the image, a bare copy of search/gateway)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VENDORED = HERE.parent / "src" / "connector_kit"
REPO = HERE.parents[2]
OB1_KIT = REPO / "OB1" / "integrations" / "connectors" / "connector_kit"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes().replace(b"\r\n", b"\n")
            for p in root.rglob("*.py") if "__pycache__" not in p.parts}


def test_the_vendored_kit_is_ob1s() -> None:
    if not OB1_KIT.is_dir():
        pytest.skip("OB1 checkout not present")
    ours, theirs = _files(VENDORED), _files(OB1_KIT)
    assert sorted(ours) == sorted(theirs)
    assert [n for n in ours if ours[n] != theirs[n]] == []


def test_the_recorded_sha_is_the_gitlink() -> None:
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    recorded = re.search(r"\*\*([0-9a-f]{40})\*\*", (VENDORED / "VENDORED.md")
                         .read_text(encoding="utf-8"))
    assert recorded, "VENDORED.md names no 40-hex sha"
    line = subprocess.run([git, "-C", str(REPO), "ls-tree", "HEAD", "OB1"], capture_output=True,
                          text=True, timeout=30).stdout.split()
    assert len(line) >= 3 and line[1] == "commit"
    if line[2] != recorded.group(1):
        pytest.fail(f"VENDORED.md names {recorded.group(1)[:12]}, the gitlink is {line[2][:12]}")
