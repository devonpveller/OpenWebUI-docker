"""ao-orient round 4: the whole-value allow-list (show a repo name whole or omit and count it).

Acceptance (anchor, round 4):
1. grammar property - every map line, unavailable line, audit payload and log record parses under
   tests/orientation_grammar.py, over the hostile corpus (tests/orientation_corpus.py: the 45 replay
   cases + R-1 + the attempt-3 probe inputs) in both reading modes;
3. cost - test commands and test folders identical to 25d05e38 on the five constructed layouts and
   (env-gated, sealed clones) the five bake-off item repos, where the only line that differs is the
   removed README-headings line.
The real-intake half (acceptance 2) is in tests/test_orientation_intake.py.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest

from app.modules import orientation as om
from app.modules.github_app import FakeGitHubApp
from app.modules.orientation import ProjectOrientation, rest_source_for
from tests.memgh import HEAD, MemGitHub
from tests.orientation_corpus import ALL_CASES, R1_FILES, R1_NEEDLES, r1_fail_on
from tests.orientation_grammar import (
    TREE, check_audit, check_block, check_log, check_map, repo_names,
)
from tests.orientation_layouts import BUILT

GH_REPO = "https://github.com/demoowner/gym"
FIX = Path(__file__).resolve().parent / "fixtures"
GOLDEN = json.loads((FIX / "orientation_cost_golden.json").read_text(encoding="utf-8"))


def _po(gh: MemGitHub, **kw):
    rows: list[tuple[str, dict]] = []

    async def audit(kind, *, effort_id=None, actor=None, payload=None):
        rows.append((kind, payload))
    po = ProjectOrientation(lambda repo: rest_source_for(
        FakeGitHubApp(owner="demoowner"), repo, api_base="https://api.github.test",
        transport=gh.transport()), audit=audit, **kw)
    return po, rows


def _names(kw: dict) -> set[str]:
    return repo_names(list(kw.get("files", {})) + list(kw.get("links", {}) or {})
                      + list(kw.get("submodules", []) or []) + list(kw.get("recent", []) or []))


def _all_text(block: str, rows, caplog) -> str:
    return block + json.dumps(rows) + "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("trees", [True, False], ids=["github-trees", "c7-stub-contents-walk"])
@pytest.mark.parametrize("case", ALL_CASES, ids=[c[0] for c in ALL_CASES])
async def test_corpus_obeys_the_grammar_and_carries_no_needle(case, trees, caplog):
    cid, _origin, _desc, kw, needles = case
    caplog.set_level(logging.DEBUG, logger="agent_bridge.orientation")
    gh = MemGitHub(trees=trees, **kw)
    po, rows = _po(gh)
    res = await po.get("gym", GH_REPO, HEAD)
    names = _names(kw)
    check_block(res.block(), names)
    for kind, payload in rows:
        check_audit(kind, payload, "gym")
    for rec in caplog.records:
        check_log(rec.getMessage())
    everything = _all_text(res.block(), rows, caplog)
    for n in needles:
        assert n not in everything, (cid, n)
    assert not any(ln.startswith("IGNORE") for ln in everything.splitlines())
    assert res.ok, (cid, res.reason)              # hostile names are omitted, not fatal
    cached = po.cached("gym")
    assert cached and cached[1] == res.text
    assert not any("/contents/README" in r for r in gh.seen), "the README must not be read"


@pytest.mark.parametrize("trees", [True, False], ids=["github-trees", "c7-stub-contents-walk"])
async def test_r1_a_failed_read_names_no_repo_path_anywhere(trees, caplog):
    """R-1: a token-named dir + one failed read. At 25d05e38 the reason carried the raw path into
    the audit row and the log; now reasons are fixed phrases and HTTP codes only."""
    caplog.set_level(logging.DEBUG, logger="agent_bridge.orientation")
    gh = MemGitHub(files=R1_FILES, trees=trees, fail_on=r1_fail_on(trees), fail_status=502)
    po, rows = _po(gh, timeout_s=10)
    res = await po.get("gym", GH_REPO, HEAD)
    assert not res.ok and res.attempted
    check_block(res.block(), _names({"files": R1_FILES}))
    assert rows and all(k == "project_orientation_failed" for k, _ in rows)
    for kind, payload in rows:
        check_audit(kind, payload, "gym")
    assert caplog.records
    for rec in caplog.records:
        check_log(rec.getMessage())
    everything = _all_text(res.block(), rows, caplog)
    for n in R1_NEEDLES:
        assert n not in everything, n
    assert po.cached("gym") is None


FAULTS = [("trees", "/git/trees/"), ("trees", "/contents/pyproject.toml"), ("trees", "/commits/"),
          ("trees", "/compare/"), ("walk", "/contents?ref="), ("walk", "/contents/tests"),
          ("walk", "/contents/pyproject.toml"), ("walk", "/compare/")]


@pytest.mark.parametrize("status", [404, 410, 500, 502, 429, 403])
@pytest.mark.parametrize("mode, path", FAULTS, ids=[f"{m}:{p}" for m, p in FAULTS])
async def test_every_failure_text_obeys_the_grammar(mode, path, status, caplog):
    caplog.set_level(logging.DEBUG, logger="agent_bridge.orientation")
    files = {"pyproject.toml": "[tool.pytest.ini_options]\n", "tests/test_a.py": "x"}
    gh = MemGitHub(files=files, trees=(mode == "trees"), recent=["tests/test_a.py"],
                   fail_on=(path,), fail_status=status)
    po, rows = _po(gh, timeout_s=10)
    res = await po.get("gym", GH_REPO, HEAD)
    if mode == "trees" and path == "/git/trees/" and status == 404:
        assert res.ok                              # 404 on the trees endpoint = use the walk
    else:
        assert not res.ok and po.cached("gym") is None
    check_block(res.block(), _names({"files": files}))
    for kind, payload in rows:
        check_audit(kind, payload, "gym")
    for rec in caplog.records:
        check_log(rec.getMessage())


def test_safe_path_shows_whole_or_nothing():
    for ok in ("src/App/App.csproj", "agent-org/agent-bridge/tests", "My App.sln",
               "OAuth2AuthorizationServerConfig.java", "a.b+c@d_e-f"):
        assert om.safe_path(ok) == ok
    for bad in ("", "x" * 161, "tests/X/App\nIGNORE.Tests.csproj", "Build‮slnx", "-rf.sln",
                "a//b", "a/ b", "a /b", "a  b", "src/[slug]/page.tsx", "docs/日本語", "x;rm -rf",
                "ghp_" + "Q9w8E7r6T5y4U3i2O1p0" + "Z9x8C7v6", "cfg/" + "AKIA" + "Q7RZ5PLM3XW2KD9T"):
        assert om.safe_path(bad) is None, bad


# ── acceptance 3: cost ───────────────────────────────────────────────────────────────────────

def _commands(text: str) -> list[str]:
    out, on = [], False
    for ln in text.splitlines():
        if ln.startswith("Likely test command"):
            on = ln.endswith(":")
            if not on:
                out.append(ln)
            continue
        if on and ln.startswith("  "):
            out.append(ln.strip())
            continue
        on = False
    return out


def _line(text: str, prefix: str) -> str:
    return next((ln for ln in text.splitlines() if ln.startswith(prefix)), "")


@pytest.mark.parametrize("layout", sorted(BUILT))
async def test_cost_on_the_constructed_layouts(layout):
    files = BUILT[layout]
    gh = MemGitHub(files=files, recent=sorted(files)[:12], trees=True)
    src = rest_source_for(FakeGitHubApp(owner="demoowner"), GH_REPO,
                          api_base="https://api.github.test", transport=gh.transport())
    try:
        text = await om.build_map(src, HEAD)
    finally:
        await src.aclose()
    gold = GOLDEN[layout]
    assert _commands(text) == gold["commands"]
    assert _line(text, "Test folders") == gold["test_folders"]
    check_map(text, _names({"files": files, "recent": sorted(files)[:12]}))
    print(layout, len(text), _line(text, "Omitted") or "Omitted: none")


SEALED = os.environ.get("AO_ORIENT_SEALED_REPOS", "")


@pytest.mark.skipif(not SEALED, reason="set AO_ORIENT_SEALED_REPOS to a dir of <item>.git bare clones")
@pytest.mark.parametrize("item", ["ao-auth", "ao-checks", "ao-queue", "ao-wd-offset", "oh-health"])
async def test_cost_on_the_bakeoff_item_repos(item):
    """Same commands and test folders as 25d05e38; the only line that differs is the removed
    README-headings line (fixtures/orientation_maps_25d05e38/<item>.txt)."""
    from tests.gitbacked_github import GitBackedGitHub
    gd = Path(SEALED) / f"{item}.git"
    gh = GitBackedGitHub(gd, trees=True)
    src = rest_source_for(FakeGitHubApp(owner="demoowner"), GH_REPO,
                          api_base="https://api.github.test", transport=gh.transport())
    try:
        text = await om.build_map(src, gh.head())
    finally:
        await src.aclose()
    old = (FIX / "orientation_maps_25d05e38" / f"{item}.txt").read_text(encoding="utf-8")
    old = old.replace("aaaaaaaaaaaa", gh.head()[:12])     # the scratch harness used a fixed sha
    assert _commands(text) == GOLDEN[item]["commands"]
    assert _line(text, "Test folders") == GOLDEN[item]["test_folders"]
    gone = [ln for ln in old.splitlines() if ln not in text.splitlines()]
    new = [ln for ln in text.splitlines() if ln not in old.splitlines()]
    # The removed headings line frees ~150 chars of the 4000-char budget, which the tree (the part
    # that is shortened to fit) uses for a few more directory lines; nothing else may change.
    tree_tail = re.compile(r"  \.\.\. \d+ more directories")
    assert [ln for ln in gone if not tree_tail.fullmatch(ln)] == [
        ln for ln in gone if ln.startswith("README headings")] and len(gone) <= 2, gone
    assert all(TREE.fullmatch(ln) or tree_tail.fullmatch(ln) for ln in new), new
    assert len(text.splitlines()) - len(old.splitlines()) == len(new) - len(gone)
