"""Tester findings become durable executable checks (dark-factory-unification U3).

§0 A5's verdict, in its own words: agent-org "BUILT and PROVED finding→durable-check
(gym-007)"; the harness "banked every lesson from 9 cycles as **prose in MERGE-PROTOCOL** —
the exact evaporation §2.3 names", and is "currently the violator". This is the harness's
side of that pipeline.

WHAT A DURABLE CHECK IS, and why it is not an acceptance criterion. An anchor's acceptance
criteria die with the item: they say what THIS work must satisfy. A durable check outlives
it — it is owned by the LINE, runs against every future item, and cannot regress. That is
the property the whole idea rests on: a lesson that can be un-learned by the next commit was
never banked, it was noted.

WHY IT MIRRORS agent-org RATHER THAN INVENTING A SHAPE. `projects.add_acceptance_check`
(ORCHESTRATION-DESIGN §10) is content-addressed, carries an origin note, and runs a command
against every future delivery. That design is proven; U3 is unification, so the harness gets
the same semantics and not a second dialect of them.

CONTENT-ADDRESSED, so re-recording the same finding is a no-op rather than a duplicate. A
tester who hits the same wall twice should not grow the registry twice, and a registry that
grows on every repeat becomes one nobody runs.

The registry lives in the SHARED git dir beside the queue, never in the worktree: every
worktree of this repo must see one registry, or a check banked in one is invisible to the
next and the durability is imaginary.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List

REGISTRY_NAME = "durable-checks.json"


def _git_common_dir(repo: str | Path) -> Path:
    """The SHARED git dir. In a worktree `.git` is a FILE pointing here."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"not a git repository: {repo}")
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else (Path(repo) / p).resolve()


def registry_path(repo: str | Path) -> Path:
    return _git_common_dir(repo) / "agent-worktrees" / REGISTRY_NAME


def check_id(command: str) -> str:
    """Content address. The COMMAND is the identity — same check, same id, one row.

    Deliberately not the finding text: two testers describing one wall in different words
    have found the same thing, and the check they would write is what says so.
    """
    return hashlib.sha256(" ".join((command or "").split()).encode("utf-8")).hexdigest()[:16]


def load(repo: str | Path) -> List[Dict[str, Any]]:
    p = registry_path(repo)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt registry must not silently read as an EMPTY one - that would report
        # "0 checks, all green" for a line that has banked dozens.
        raise RuntimeError(f"durable-check registry is unreadable: {p}")
    return data.get("checks", []) if isinstance(data, dict) else []


def _save(repo: str | Path, checks: List[Dict[str, Any]]) -> None:
    p = registry_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"checks": checks}, indent=2) + "\n", encoding="utf-8")


def add(repo: str | Path, *, command: str, why: str, source_item: str = "",
        source: str = "tester-finding") -> Dict[str, Any]:
    """Bank a finding as a durable check. Idempotent by command.

    `why` is required by the caller contract, not merely encouraged: a check whose purpose is
    unrecorded cannot be judged when it goes red years later, and the reader cannot tell
    whether the check or the code is wrong. That is the same rule executable acceptance
    criteria enforce, for the same reason.
    """
    cmd = " ".join((command or "").split())
    if not cmd:
        raise ValueError("a durable check needs a command - that is what makes it durable")
    if not (why or "").strip():
        raise ValueError("a durable check needs a 'why' - one that goes red years from now "
                         "is unjudgeable without it")
    checks = load(repo)
    cid = check_id(cmd)
    for c in checks:
        if c.get("id") == cid:
            return c            # already banked: a repeat finding is not a second check
    row = {"id": cid, "check": cmd, "why": why.strip(),
           "source": source, "source_item": source_item}
    checks.append(row)
    _save(repo, checks)
    return row


def run(repo: str | Path, checks: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Run every durable check. Returns a result summary; never raises on a red check.

    THE CANNOT-REGRESS PROPERTY IS THIS FUNCTION. A registry nobody runs is a list of
    lessons, which is the prose evaporation A5 names - the thing this exists to stop.
    """
    rows = load(repo) if checks is None else checks
    results = []
    for c in rows:
        try:
            rc = subprocess.run(c["check"], shell=True, cwd=str(repo)).returncode
        except Exception as exc:  # noqa: BLE001
            results.append({**c, "passed": False, "detail": f"failed to run: {exc}"})
            continue
        results.append({**c, "passed": rc == 0, "detail": f"exit {rc}"})
    failed = [r for r in results if not r["passed"]]
    return {"total": len(results), "failed": len(failed), "results": results}


def _main(argv: List[str]) -> int:
    """durable_checks.py <repo> list|run|add --check <cmd> --why <text> [--item <id>]"""
    if len(argv) < 2:
        print("usage: durable_checks.py <repo> list|run|add [--check CMD --why TEXT --item ID]")
        return 2
    repo, cmd = argv[0], argv[1]
    rest = argv[2:]

    def opt(name: str) -> str:
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else ""

    if cmd == "list":
        rows = load(repo)
        print(f"{len(rows)} durable check(s)")
        for c in rows:
            print(f"  [{c['id']}] {c['check']}\n      why: {c['why']}"
                  f"{'  (from ' + c['source_item'] + ')' if c.get('source_item') else ''}")
        return 0
    if cmd == "add":
        try:
            row = add(repo, command=opt("--check"), why=opt("--why"), source_item=opt("--item"))
        except ValueError as exc:
            print(f"refused: {exc}")
            return 2
        print(f"banked [{row['id']}] {row['check']}")
        return 0
    if cmd == "run":
        rows = load(repo)
        if not rows:
            # Said out loud. "0 checks, all green" reads as coverage, and it is the state a
            # line has before it has banked anything - which is exactly when it is weakest.
            print("no durable checks banked yet - nothing to run")
            return 0
        out = run(repo, rows)
        for r in out["results"]:
            print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['check']}  ({r['detail']})")
            if not r["passed"]:
                print(f"         why it exists: {r['why']}")
        print(f"{out['total'] - out['failed']}/{out['total']} durable checks passed")
        return 1 if out["failed"] else 0
    print(f"unknown command '{cmd}'")
    return 2




# ── the memory-plane half (U3) ───────────────────────────────────────────────
# §2's U3 row: "harness findings write memory_type='check'". Banking a check locally makes
# it durable FOR THIS LINE; writing it to the plane makes it visible to the other system,
# which is what "verification unification" means. The type only became legal on 2026-08-30
# (init-agent-memory-check-type.sql) - before that, this write was rejected by the CHECK.

OPS_DOOR = "http://127.0.0.1:8062"
WORKSPACE = "ai-stack"


def memory_payload(row: Dict[str, Any], *, project: str = "ai-stack") -> Dict[str, Any]:
    """The writeback for a banked check. PURE.

    Carries the COMMAND as the content, because that is the artifact - a memory of a check
    that omitted the command would be a memory ABOUT a check, which is the prose form this
    pipeline exists to replace.
    """
    return {
        "workspace_id": WORKSPACE,
        "project_id": project,
        "summary": (row.get("why") or row["check"])[:300],
        "content": f"{row['check']}\n\nwhy: {row.get('why', '')}".strip(),
        "memory_type": "check",
        # Idempotent on the check's content address, so re-banking never writes twice.
        "idempotency_key": f"check-{row['id']}",
        "metadata": {
            "runtime_name": "agent-harness",
            "source": row.get("source", "tester-finding"),
            "source_item": row.get("source_item", ""),
            "check": row["check"],
        },
    }


def mirror_to_plane(row: Dict[str, Any], *, project: str = "ai-stack",
                    door: str = OPS_DOOR, key: str = "") -> bool:
    """Write a banked check to the agent-memory plane. NEVER raises, returns False on any
    failure including "the door is not there".

    FAIL-SOFT BY DESIGN: the local registry is the durable artifact and the plane is a
    second home for it. A memory write that could block banking a check would make the
    unification cost you the thing being unified.
    """
    if not key:
        return False
    import urllib.request

    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "agent_memory_writeback",
                   "arguments": memory_payload(row, project=project)},
    }
    try:
        req = urllib.request.Request(
            door.rstrip("/") + "/mcp",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return False
            raw = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return False
    return _mirror_succeeded(raw)


def _mirror_succeeded(raw: str) -> bool:
    """Did the WRITE happen? PURE.

    HTTP 200 with result.isError is how MCP reports tool failure, so a status check alone
    marks unwritten memories as written - the trap the audit mirror fell into.
    """
    text = (raw or "").strip()
    if text.startswith("event:") or text.startswith("data:"):
        lines = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        text = lines[-1] if lines else ""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(msg, dict) or msg.get("error"):
        return False
    result = msg.get("result")
    return isinstance(result, dict) and not result.get("isError")


# The entrypoint stays LAST. It was above the memory-plane helpers for one commit, which
# makes anything appended after it dead code when the file is run as a script - the
# functions are not defined yet when SystemExit fires.
if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
