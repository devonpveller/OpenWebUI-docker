"""The vendored connector kit (src/connector_kit) is OB1's, byte for byte, AT THE GITLINK.

  1. every vendored file matches the sha256 manifest in VENDORED.md (always: no OB1 needed)
  2. the vendored files equal the gitlink commit's tree (`git -C OB1 show <gitlink>:<path>`), not
     whatever the OB1 working checkout holds
  3. VENDORED.md's sha is the gitlink
2 and 3 need this repo's git metadata and OB1's objects; where those are absent (a bare copy of
search/gateway, the image) they are skipped WITH the reason, and 1 still runs.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VENDORED = HERE.parent / "src" / "connector_kit"
REPO = HERE.parents[2]
KIT_PATH = "integrations/connectors/connector_kit"
_MANIFEST = re.compile(r"^\s+([0-9a-f]{64})\s{2}(\S+)$", re.M)


def _norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def _files() -> dict[str, bytes]:
    return {p.relative_to(VENDORED).as_posix(): _norm(p.read_bytes())
            for p in VENDORED.rglob("*.py") if "__pycache__" not in p.parts}


def _vendored_md() -> str:
    return (VENDORED / "VENDORED.md").read_text(encoding="utf-8")


def _recorded_sha() -> str:
    m = re.search(r"\*\*([0-9a-f]{40})\*\*", _vendored_md())
    assert m, "VENDORED.md names no 40-hex sha"
    return m.group(1)


def test_every_vendored_file_matches_the_manifest() -> None:
    manifest = {path: digest for digest, path in _MANIFEST.findall(_vendored_md())}
    files = _files()
    assert manifest, "VENDORED.md has no manifest"
    assert sorted(files) == sorted(manifest)
    bad = [n for n, b in files.items() if hashlib.sha256(b).hexdigest() != manifest[n]]
    assert bad == []


def _git() -> str:
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("no git metadata here (a bare copy / the image): the manifest check covers it")
    return git


def _gitlink(git: str) -> str:
    line = subprocess.run([git, "-C", str(REPO), "ls-tree", "HEAD", "OB1"], capture_output=True,
                          text=True, timeout=30).stdout.split()
    assert len(line) >= 3 and line[1] == "commit", "no OB1 gitlink in HEAD"
    return line[2]


def test_the_recorded_sha_is_the_gitlink() -> None:
    git = _git()
    link = _gitlink(git)
    assert _recorded_sha() == link, f"VENDORED.md names {_recorded_sha()[:12]}, gitlink {link[:12]}"


def test_the_vendored_kit_is_the_gitlinks_tree() -> None:
    git = _git()
    link = _gitlink(git)
    ob1 = REPO / "OB1"
    listing = subprocess.run([git, "-C", str(ob1), "ls-tree", "-r", "--name-only", link, KIT_PATH],
                             capture_output=True, text=True, timeout=30)
    if listing.returncode != 0:
        pytest.fail(f"the OB1 objects for the gitlink {link[:12]} are not here (fetch them): "
                    f"{listing.stderr.strip()[:200]}")
    names = {n[len(KIT_PATH) + 1:] for n in listing.stdout.split() if n.endswith(".py")}
    files = _files()
    assert sorted(names) == sorted(files)
    differ = []
    for n in names:
        blob = subprocess.run([git, "-C", str(ob1), "show", f"{link}:{KIT_PATH}/{n}"],
                              capture_output=True, timeout=30).stdout
        if _norm(blob) != files[n]:
            differ.append(n)
    assert differ == []
