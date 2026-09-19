"""Falsifiability drill for the recall seams - prove every guard can actually go RED.

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH. PLAN §C.7: "a phase whose evidence is a paragraph
is not done", and §0 A6 records the verdict on prose verification as FALSIFIED. The specific
failure this drill exists for was found in this very item: the seam-4 test passed with seam 4
DELETED, because the resumed dispatch re-entered a different seam that re-injected the block.
It named one seam and was satisfied by another, and no amount of reading it would have shown
that - only deleting the code it claimed to guard.

So: for each guard, apply the exact mutation it claims to catch, run the tests it claims to
run, and require RED. A mutation that stays GREEN is a check that is checking nothing.

The files are restored byte-for-byte in a `finally`, whatever happens. Run it on a clean
working tree so an interrupted run cannot be mistaken for an edit.

    python scripts/checks/recall-falsifiability-drill.py

Exit: 0 = every mutation went red | 1 = at least one guard is vacuous.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BRIDGE = os.path.join(ROOT, "agent-org", "agent-bridge")
OB1 = os.path.join(ROOT, "OB1", "integrations", "kubernetes-deployment")

ORCH = os.path.join(BRIDGE, "app", "orchestrator.py")
MEM = os.path.join(BRIDGE, "app", "modules", "openbrain_memory.py")
RANK = os.path.join(OB1, "agent-memory-ranking.ts")

PYTEST_FILES = ["tests/test_recall_seams.py", "tests/test_agent_memory_recall.py"]

# (label, file, old, new, selector) - selector is a pytest -k expression, or "deno" for the
# Deno suite. Each mutation is the smallest edit that removes the property being claimed.
MUTATIONS = [
    ("seam 4 injection deleted", ORCH,
     "            if _fresh:\n                resume_goal = strip_recall_block(resume_goal) + _fresh\n",
     "            if False:\n                resume_goal = strip_recall_block(resume_goal) + _fresh\n",
     "seam_4"),
    ("seam 4 back to guard-and-skip (the shipped behaviour this item refuted)", ORCH,
     "        _pm = await self._effort_project(frm)\n        if _pm:\n"
     "            _fresh = await self._agent_memory_context(\n",
     "        _pm = await self._effort_project(frm)\n"
     "        if _pm and \"RELEVANT MEMORIES\" not in resume_goal:\n"
     "            _fresh = await self._agent_memory_context(\n",
     "seam_4"),
    ("seam 3 stops stripping the block it inherited from intake", ORCH,
     '        base_goal = strip_recall_block(goal or "").split',
     '        base_goal = (goal or "").split',
     "burndown_round_never_embeds"),
    ("the handoff fix effort goes back to the templated query", ORCH,
     '        if "RELEVANT MEMORIES" not in goal:\n'
     '            goal += await self._agent_memory_context(\n'
     '                owner, f"{target}: {summary}\\n\\n{ho[\'log\'][:2400]}")\n',
     "",
     "handoff_fix_effort"),
    ("the recall query stops being stripped at the helper", ORCH,
     '                project=slug, query=strip_recall_block(request or ""))',
     '                project=slug, query=(request or ""))',
     "burndown_round_never_embeds or seam_4_asks_about_the_HANDOFF"),
    ("rows that render to nothing stop being reported (the defect-3 hole)", ORCH,
     '            block, shown = "", set()\n',
     '            return ""\n        if not block:\n            return ""\n',
     "render_to_NOTHING"),
    ("the block budget goes back to bounding item lines only", MEM,
     "        if used + cost > RECALL_BODY_MAX:",
     "        if used + cost > RECALL_BLOCK_MAX:",
     "self_bounded or fits_the_budget"),
    ("summaries stop being clipped (the per-item line bound)", MEM,
     '        summary = _clip(" ".join(str(it.get("summary") or "").split()), RECALL_SUMMARY_MAX)',
     '        summary = " ".join(str(it.get("summary") or "").split())',
     "every_item_line_is_bounded"),
    ("summaries stop being whitespace-collapsed (the one-paragraph premise)", MEM,
     '        summary = _clip(" ".join(str(it.get("summary") or "").split()), RECALL_SUMMARY_MAX)',
     '        summary = _clip(str(it.get("summary") or "").strip(), RECALL_SUMMARY_MAX)',
     "exactly_one_paragraph or forge_STRUCTURE"),
    ("strip becomes a no-op", MEM,
     "    i = text.find(RECALL_BLOCK_MARKER)\n    if i < 0:\n        return text\n",
     "    i = text.find(RECALL_BLOCK_MARKER)\n    if i >= 0:\n        return text\n",
     "strip_removes_the_block"),
    ("recall ignores its own off switch", MEM,
     '        if not bool(getattr(self.s, "memory_recall_enabled", False)):\n            return "", []\n',
     '        if False:\n            return "", []\n',
     "control_recall_off"),
    ("the two phases collapse into one (RECALL_OVERFETCH = 1)", RANK,
     "export const RECALL_OVERFETCH = 4;",
     "export const RECALL_OVERFETCH = 1;",
     "deno"),
]


def _python() -> str:
    return os.environ.get("AI_STACK_PYTEST_PYTHON", sys.executable)


def run_one(label: str, path: str, old: str, new: str, selector: str) -> bool:
    src = io.open(path, encoding="utf-8").read()
    if old not in src:
        print("SKIP  %-70s (anchor not found - the code moved)" % label)
        return False
    bak = path + ".drillbak"
    shutil.copyfile(path, bak)
    try:
        io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
        if selector == "deno":
            p = subprocess.run(["deno", "test", "agent-memory-ranking.test.ts"],
                               cwd=OB1, capture_output=True, text=True, shell=True)
        else:
            p = subprocess.run([_python(), "-m", "pytest", *PYTEST_FILES, "-q", "-k", selector,
                                "--no-header", "-p", "no:cacheprovider"],
                               cwd=BRIDGE, capture_output=True, text=True)
        lines = [ln for ln in (p.stdout + p.stderr).strip().splitlines() if ln.strip()]
        tail = lines[-1] if lines else "(no output)"
        red = p.returncode != 0
        print("%-5s %-70s %s" % ("RED" if red else "GREEN", label, tail[:90]))
        return red
    finally:
        shutil.copyfile(bak, path)
        os.remove(bak)


def main() -> int:
    dirty = subprocess.run(["git", "status", "--porcelain", "--", ORCH, MEM],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("NOTE: the working tree is dirty; the drill restores what it edits, but a "
              "crash mid-run would leave a mutation behind.\n%s\n" % dirty)
    ok = True
    for m in MUTATIONS:
        ok = run_one(*m) and ok
    print("\nALL MUTATIONS RED - every guard can fail" if ok
          else "\nSOME MUTATION SURVIVED - at least one guard is vacuous")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
