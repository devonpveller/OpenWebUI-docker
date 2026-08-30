"""Frontier-oracle-on-stall (dark-factory-unification U4).

ORCHESTRATION-DESIGN §7, in its own words: the cost-effective split is "95% small-model
search, 5% frontier unstick", and the frontier is "an **oracle invoked on a stall signal**
- not a better worker. It injects the one constraint that only knowledge provides, then
hands back to the small model."

Two things have to exist for that sentence to be machinery instead of prose: a STALL SIGNAL
that a machine can compute, and a record that the escalation HAPPENED. This module is both.

WHAT A STALL IS, AND WHY THIS DEFINITION AND NOT ANOTHER
--------------------------------------------------------
The definition is agent-org's, not a new one. `orchestrator.py`'s burn-down loop
(the `seen_sigs` / `stall` block, around line 8414) already decides "is this campaign
converging?" every round, and it decides it like this:

  * every round's failure gets a SIGNATURE - the normalized log tail, digits and hashes
    masked (`Orchestrator._failure_sig`, orchestrator.py:8845). `failure_signature` here is
    that function, byte for byte, so a signature computed on either side of the
    unification means the same thing.
  * novelty is measured against EVERY signature seen so far, not just the previous round.
    The comment there says why, and it was paid for: "A->B->A is not progress - it's a
    cycle, and the old `last_sig` test scored both flips as progress and looped forever."
  * a round that produces no new information increments a stall counter; a round that does
    resets it; `stall >= 2` stops the loop and escalates.

Two deliberate differences, each stated rather than absorbed:

  1. agent-org has a COUNTABLE metric (compiler errors), so its progress test is
     `improved OR novel_signature`. A harness round is a tester's pass/fail verdict with
     prose - there is no count to improve. So the signature axis carries the whole test
     here, and `improved` has no analogue rather than a faked one.

  2. A round must also have MOVED THE CODE. little-coder's FLAIL GUARD kills a turn that
     reads without editing (`littlecoder/agent.py:170`, `flail_tripped`); the round-level
     analogue is a round whose branch head is exactly the previous round's. If the code did
     not change and the failure signature did, the failure is nondeterministic - and §6's
     hygiene rule is explicit that noise "must never be recorded as a constraint". Without
     this, a flaky test would reset the stall counter forever and the detector would never
     fire on the one item that most needs it.

     With one boundary the first version got wrong: a head that could not be READ is not
     the same as a head that did not MOVE. That round is recorded and left UNSCORED - see
     `evaluate`. The rule used to score it as "did not move", which turned `git rev-parse`
     failing into a frontier escalation.

  A NOTE ON THE THRESHOLD, because it is the difference that matters when comparing the
  two implementations: agent-org seeds `seen_sigs` with the pre-existing failing log's
  signature BEFORE its loop, so its first round can already be non-novel. A harness item
  has no prior log, so round 1 here is unconditionally progress and `stall >= 2` therefore
  needs strictly MORE than two rounds. `record()` enforces that as an invariant.

WHAT FIRING DOES
----------------
It does NOT swap the worker for a better one. It records an escalation: the stall evidence,
the runner that stalled, the oracle runner resolved through the SAME profile mechanism every
other role goes through (`config.resolve_role`), and `hand_back_to` - the runner the item
returns to after the oracle's round. §7's "then hands back" is a field, not an intention.

When the worker is ALREADY the frontier runner there is no oracle above it. That is recorded
as `no-oracle-above` and NOT as an escalation, because an all-cloud line escalating
claude-code to claude-code would satisfy the audit trail while changing nothing. The stall is
still real and still recorded - it is an andon either way.

THE LEDGER IS THE OBSERVATION
-----------------------------
Append-only JSONL beside the queue in the SHARED state dir, so every worktree sees one
ledger. A firing nobody can point at afterwards has not been observed - `report` is the
surface that makes "observed firing at least once" a command rather than a claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent

LEDGER_NAME = "oracle-escalations.jsonl"

# agent-org's `stall >= 2`. Kept as a name so the two systems can be pinned together, and so
# a drill can lower it without editing the rule it is testing.
STALL_THRESHOLD = 2


# --------------------------------------------------------------------------------------
# the signal
# --------------------------------------------------------------------------------------

def failure_signature(log: str) -> str:
    """A stable signature of WHAT failed - the normalized log tail (digits/hashes masked).

    Ported verbatim from `Orchestrator._failure_sig` (agent-org/agent-bridge/app/
    orchestrator.py:8845). Verbatim on purpose: U3 already writes agent-org's signatures
    through to the memory plane, so a harness signature that normalized differently would
    produce a second, silently incompatible dialect of the same key.
    """
    tail = [ln.strip() for ln in (log or "").splitlines() if ln.strip()][-25:]
    norm = re.sub(r"[0-9a-f]{7,}|\d+", "#", " ".join(tail).lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


_OBJECT_NAME = re.compile(r"^[0-9a-f]{7,40}$")


def object_name(value: str) -> str:
    """`value` if it looks like a git object name, otherwise "" (meaning: not recorded).

    Applied where REAL queue items are read, not inside `evaluate` - the detector takes
    whatever shas it is handed, and the adapter is the layer that knows the shas came from
    git. It exists because `git rev-parse <missing-ref>` prints THE REF NAME on stdout and
    exits 128, so a queue item written before that exit code was checked carries a round
    with `sha: "drill/oracle-stall"`. Two such rounds compare EQUAL, which the detector
    would otherwise read as "the code did not move" and escalate on. A branch name is not a
    commit; the honest reading of that field is "not recorded".
    """
    v = (value or "").strip().lower()
    return v if _OBJECT_NAME.match(v) else ""


def evaluate(rounds: List[Dict[str, str]], threshold: int = STALL_THRESHOLD) -> Dict[str, Any]:
    """The stall test. `rounds` are the item's FAILING rounds, oldest first.

    Each round: ``{"text": <the failure as the tester reported it>, "sha": <branch head>}``.
    Returns the verdict AND the per-round trail that produced it - "what the detector saw"
    is not reconstructible from a boolean.
    """
    seen = set()
    stall = 0
    prev_sha = ""
    trail: List[Dict[str, Any]] = []
    for i, r in enumerate(rounds, start=1):
        sig = failure_signature(r.get("text", ""))
        sha = (r.get("sha") or "").strip()
        novel = sig not in seen
        first = i == 1
        # UNKNOWN IS NOT "DID NOT MOVE". If a round has no branch head recorded, the
        # movement test could not be RUN - the harness failed to measure, which is not
        # evidence about the code. Scoring it as "did not move" is what turns a tooling
        # failure into a frontier escalation, and §6's hygiene rule ("noise must never be
        # recorded as a constraint") applies with more force to a failed MEASUREMENT than
        # to a flaky test. Such a round is recorded, its signature still counts toward
        # novelty, and the stall counter is left exactly as it was: neither advanced nor
        # reset. Reversed 2026-08-30 from "a missing sha counts as not moved" after the
        # source of missing shas turned out to be `git rev-parse` failing silently
        # (queue.ps1 now refuses that verdict outright; this is the second line of defence).
        scored = first or bool(sha and prev_sha)
        # Round 1 has nothing before it: the code cannot have "not moved" yet.
        moved = True if first else (sha != prev_sha)
        progress = novel and moved
        seen.add(sig)
        if not scored:
            why = ("the branch head could not be read for this round - not scored either "
                   "way (a failed measurement is not evidence that the code stood still)")
        elif not novel and not moved:
            why = "the same failure, on the same commit"
        elif not novel:
            why = "a failure already seen on this item (a cycle, not a step)"
        elif not moved:
            why = "the failure changed but the code did not - noise, not a learned clause"
        else:
            why = "new failure on new code"
        if scored:
            stall = 0 if progress else stall + 1
        trail.append({
            "round": i, "sig": sig, "sha": sha[:12], "novel": novel,
            "moved": moved if scored else None, "scored": scored,
            "progress": progress if scored else None, "stall_after": stall, "why": why,
        })
        prev_sha = sha or prev_sha
    # THE STRUCTURAL INVARIANT, stated where it is produced: round 1 is ALWAYS progress
    # (nothing precedes it, so it is novel and cannot have failed to move), and no round
    # raises the counter by more than one. Therefore `stall <= rounds - 1`, and a stall of
    # `threshold` needs STRICTLY MORE than `threshold` rounds. `len(rounds) > threshold` is
    # not belt-and-braces: it makes the impossible state impossible rather than merely
    # unobserved, and `record()` refuses to write a firing that violates it.
    return {
        "rounds": len(rounds),
        "stall": stall,
        "threshold": threshold,
        "stalled": stall >= threshold and len(rounds) > threshold,
        "signatures_seen": len(seen),
        "trail": trail,
    }


# --------------------------------------------------------------------------------------
# the escalation target
# --------------------------------------------------------------------------------------

def _config():
    import config  # the harness's own reader; same file, same precedence
    return config


def oracle_runner_name(cfg=None) -> str:
    """Which configured runner is the oracle: the one whose KIND is a frontier agent.

    Resolved from `runners`, never hardcoded to a name, so renaming a runner in
    harness.config.json does not silently disable the escalation.
    """
    cfg = cfg or _config()
    runners = cfg.get("runners") or {}
    for name, spec in runners.items():
        if isinstance(spec, dict) and spec.get("kind") == "claude-code":
            return name
    return ""


def resolve_escalation(profile: str = "", surface: str = "", cfg=None) -> Dict[str, Any]:
    """Worker runner -> oracle runner, both through `config.resolve_role`.

    §7's oracle is invoked ON TOP of the worker, so the worker's identity is part of the
    record: `hand_back_to` is what the item returns to when the oracle's round is done.
    """
    cfg = cfg or _config()
    worker = cfg.resolve_role("worker", profile=profile, surface=surface)
    oracle_name = oracle_runner_name(cfg)
    runners = cfg.get("runners") or {}
    oracle_spec = runners.get(oracle_name) or {}
    if not oracle_name:
        return {"worker": worker, "oracle": None, "outcome": "no-oracle-configured",
                "why": "no runner in harness.config.json has kind 'claude-code'"}
    if worker.get("runner") == oracle_name:
        return {"worker": worker, "oracle": None, "outcome": "no-oracle-above",
                "why": ("the worker already runs on '" + oracle_name + "' - there is no "
                        "frontier above it, so escalating would change nothing while "
                        "looking like it did (§7: the oracle is not a better worker)")}
    return {
        "worker": worker,
        "oracle": {"runner": oracle_name,
                   "kind": oracle_spec.get("kind", oracle_name),
                   "model": oracle_spec.get("default_model", "")},
        "hand_back_to": worker.get("runner", ""),
        "outcome": "escalate",
        "why": "§7 - inject the constraint only knowledge provides, then hand back",
    }


# --------------------------------------------------------------------------------------
# the ledger
# --------------------------------------------------------------------------------------

def _git_common_dir(repo) -> Path:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("not a git repository: " + str(repo))
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else (Path(repo) / p).resolve()


def state_dir(repo=".") -> Path:
    """The ONE shared coordination namespace - the same one `Get-SharedStateDir` resolves.

    Honours `AI_STACK_WORKTREE_STATE` (resolve.ps1:25) so a drill can run against a scratch
    namespace instead of the live queue's. `durable_checks.registry_path` does NOT honour it
    - see the findings note; that is a real inconsistency, not a pattern to copy.
    """
    override = os.environ.get("AI_STACK_WORKTREE_STATE")
    if override:
        return Path(override)
    return _git_common_dir(repo) / "agent-worktrees"


def ledger_path(repo=".") -> Path:
    return state_dir(repo) / LEDGER_NAME


def read_ledger(repo=".", item: str = "") -> List[Dict[str, Any]]:
    p = ledger_path(repo)
    if not p.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            # A truncated append must not hide the firings that DID land.
            continue
        if item and row.get("item_id") != item:
            continue
        rows.append(row)
    return rows


def fold(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per firing: a later line for the same id supersedes an earlier one.

    The ledger is APPEND-ONLY, so consuming an escalation writes a second line rather than
    editing the first - a record that was already read as evidence must not change
    afterwards. Every reader that asks "what is the current state of this firing?" therefore
    has to fold, and the first version of `pending` did not: it walked the raw lines, skipped
    the consumed copy, found the original still saying `consumed_by: ""`, and handed back an
    escalation that had already been served. Caught by
    `test_pending_then_consume_hands_back`, not by re-reading the code.
    """
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        latest[str(row.get("id", ""))] = row
    return list(latest.values())


def _append(repo, row: Dict[str, Any]) -> Dict[str, Any]:
    p = ledger_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def escalation_id(item: str, rounds: int) -> str:
    """One firing per (item, round-count). Re-running the detector on unchanged rounds is a
    no-op: a ledger that grows every time someone looks at it cannot be read as evidence."""
    return hashlib.sha256((item + "@" + str(rounds)).encode("utf-8")).hexdigest()[:16]


def record(repo, item: str, verdict: Dict[str, Any],
           escalation: Dict[str, Any], detail: str = "") -> Optional[Dict[str, Any]]:
    """Write the firing. Returns the row, or None when this firing is already on record.

    REFUSES a structurally impossible verdict, loudly. A verifier reported a ledger row with
    `rounds=2, stall=2` and a two-entry trail on one run and could not reproduce it in
    fifteen more. That state cannot come out of `evaluate` - round 1 is always progress and
    the counter rises by at most one per round, so `stall >= threshold` requires strictly
    more than `threshold` rounds - and the ledger is the audit trail this phase is validated
    against (§C.7: "the audit trail is the deliverable's twin"). So the impossible row is
    refused at the point of writing rather than left for someone to find and disbelieve
    later: if it ever happens, it fails where it happened, with the verdict in the message.
    """
    rounds_n = verdict.get("rounds", 0)
    stall_n = verdict.get("stall", 0)
    thr = verdict.get("threshold", STALL_THRESHOLD)
    trail = verdict.get("trail", [])
    if stall_n > max(0, rounds_n - 1) or (stall_n >= thr and rounds_n <= thr):
        raise ValueError(
            "refusing to record a structurally impossible firing for '" + str(item) +
            "': stall=" + str(stall_n) + " over " + str(rounds_n) + " round(s) at threshold "
            + str(thr) + ". Round 1 is always progress, so stall <= rounds-1 always holds. "
            "This verdict did not come from evaluate() on these rounds.")
    if len(trail) != rounds_n:
        raise ValueError(
            "refusing to record a firing whose trail does not match its rounds for '" +
            str(item) + "': " + str(len(trail)) + " trail entries, rounds=" + str(rounds_n) +
            ". The trail IS the evidence; a truncated one is not a record of what was seen.")
    eid = escalation_id(item, verdict.get("rounds", 0))
    for row in read_ledger(repo, item=item):
        if row.get("id") == eid:
            return None
    worker = escalation.get("worker") or {}
    oracle = escalation.get("oracle") or {}
    row = {
        "id": eid,
        "at": int(time.time()),
        # NOT "item". `.item` on a PowerShell collection silently resolves to the .NET
        # IList indexer, so `Where-Object { $_.item -eq $x }` compares a PSMethod to a
        # string and is ALWAYS false - a filter that never matches and never errors. It
        # made this drill's own control checks pass vacuously before they were fixed.
        "item_id": item,
        "outcome": escalation.get("outcome", ""),
        "why": escalation.get("why", ""),
        "stalled_runner": worker.get("runner", ""),
        "stalled_model": worker.get("model", ""),
        "profile": worker.get("profile", ""),
        "oracle_runner": oracle.get("runner", ""),
        "oracle_model": oracle.get("model", ""),
        "hand_back_to": escalation.get("hand_back_to", ""),
        "rounds": verdict.get("rounds", 0),
        "stall": verdict.get("stall", 0),
        "threshold": verdict.get("threshold", STALL_THRESHOLD),
        "signatures_seen": verdict.get("signatures_seen", 0),
        "trail": verdict.get("trail", []),
        "detail": detail,
        "consumed_by": "",
        "consumed_at": 0,
    }
    return _append(repo, row)


def pending(repo, item: str) -> Optional[Dict[str, Any]]:
    """The open escalation on an item: fired, `escalate`, and not yet consumed.

    This is the handle a dispatcher reads. It exists NOW, before the dispatcher does, so
    that "wired" means a caller can obtain the oracle's target without re-deriving it.
    """
    for row in reversed(fold(read_ledger(repo, item=item))):
        if row.get("outcome") == "escalate" and not row.get("consumed_by"):
            return row
    return None


def consume(repo, item: str, by: str) -> Optional[Dict[str, Any]]:
    """Mark the open escalation consumed - the oracle's round ran, hand back to the worker.

    Append-only: the consumption is a NEW line, and `read_ledger` folds it. Rewriting the
    original row would let a record that was already read as evidence change afterwards.
    """
    row = pending(repo, item)
    if row is None:
        return None
    done = dict(row)
    done["consumed_by"] = by
    done["consumed_at"] = int(time.time())
    return _append(repo, done)


# --------------------------------------------------------------------------------------
# the queue adapter - rounds from an item the harness already records
# --------------------------------------------------------------------------------------

def failing_rounds(item: Dict[str, Any], queue_dir="") -> List[Dict[str, str]]:
    """The item's failing test rounds, oldest first.

    No new store: `queue.ps1 -Fail` has always written `results[]` with the verdict, the
    branch head at the time, the tester's reason and their evidence. That IS the round
    history; the detector reads it rather than asking anyone to maintain a second one.

    `evidence` may be a PATH (queue.ps1 copies long evidence beside the item). Read it when
    it is - the file is the failure text, and signing the path string instead would make
    every round of one item look identical.
    """
    out: List[Dict[str, str]] = []
    for r in (item.get("results") or []):
        if (r.get("verdict") or "") != "fail":
            continue
        ev = str(r.get("evidence") or "")
        if ev:
            p = Path(ev)
            if not p.is_absolute() and queue_dir:
                p = Path(queue_dir) / ev
            try:
                if p.is_file():
                    ev = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        out.append({"text": (str(r.get("reason") or "") + "\n" + ev).strip(),
                    "sha": object_name(str(r.get("sha") or ""))})
    return out


def load_item(queue_dir, item_id: str) -> Dict[str, Any]:
    p = Path(queue_dir) / (item_id + ".json")
    if not p.is_file():
        raise FileNotFoundError("no queue item '" + item_id + "' in " + str(queue_dir))
    return json.loads(p.read_text(encoding="utf-8"))


def check(queue_dir, item_id: str, repo=".", profile: str = "", surface: str = "",
          threshold: int = STALL_THRESHOLD, cfg=None) -> Dict[str, Any]:
    """The whole thing, once: read the rounds, evaluate, and record a firing if it stalled.

    Returns `{verdict, escalation, recorded}`. `recorded` is None when nothing stalled OR
    when this exact firing is already on the ledger.
    """
    item = load_item(queue_dir, item_id)
    rounds = failing_rounds(item, queue_dir)
    verdict = evaluate(rounds, threshold=threshold)
    if not verdict["stalled"]:
        return {"verdict": verdict, "escalation": None, "recorded": None}
    prof = profile or str(item.get("profile") or "")
    escalation = resolve_escalation(profile=prof, surface=surface, cfg=cfg)
    detail = ("developer=" + str(item.get("developer", "")) +
              " branch=" + str(item.get("branch", "")))
    recorded = record(repo, item=item_id, verdict=verdict, escalation=escalation, detail=detail)
    return {"verdict": verdict, "escalation": escalation, "recorded": recorded}


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _fmt_check(item_id: str, res: Dict[str, Any]) -> str:
    v = res["verdict"]
    lines = []
    if not v["stalled"]:
        lines.append("'" + item_id + "': no stall - " + str(v["rounds"]) +
                     " failing round(s), stall " + str(v["stall"]) + "/" + str(v["threshold"]) + ".")
    else:
        e = res["escalation"] or {}
        lines.append("'" + item_id + "': STALLED - " + str(v["stall"]) +
                     " consecutive round(s) with no new information (" + str(v["rounds"]) +
                     " failing rounds, " + str(v["signatures_seen"]) +
                     " distinct failure signature(s)).")
        if e.get("outcome") == "escalate":
            o = e.get("oracle") or {}
            w = e.get("worker") or {}
            lines.append("  ORACLE-ON-STALL: " + str(w.get("runner")) + "/" + str(w.get("model")) +
                         " -> " + str(o.get("runner")) + "/" + str(o.get("model")) +
                         ", then hand back to " + str(e.get("hand_back_to")) + ".")
        else:
            lines.append("  NO ESCALATION (" + str(e.get("outcome")) + "): " + str(e.get("why")))
        lines.append("  recorded in the ledger."
                     if res.get("recorded") else "  already on the ledger - not re-recorded.")
    for t in v["trail"]:
        lines.append("    round " + str(t["round"]) + "  sig=" + t["sig"] +
                     "  sha=" + (t["sha"] or "-") + "  " +
                     ("PROGRESS" if t["progress"] else "no progress") + ": " + t["why"])
    return "\n".join(lines)


def _main(argv: List[str]) -> int:
    if not argv:
        print("usage: oracle_on_stall.py check <queue-dir> <item-id> [--repo R] "
              "[--profile P] [--json]")
        print("       oracle_on_stall.py pending <item-id> [--repo R] [--json]")
        print("       oracle_on_stall.py report [--repo R] [--item I] [--json]")
        return 2
    cmd, rest = argv[0], argv[1:]
    opts: Dict[str, str] = {}
    positional: List[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--json":
            opts["json"] = "1"
        elif a.startswith("--"):
            opts[a[2:]] = rest[i + 1] if i + 1 < len(rest) else ""
            i += 1
        else:
            positional.append(a)
        i += 1
    repo = opts.get("repo") or "."

    if cmd == "check":
        if len(positional) < 2:
            print("usage: oracle_on_stall.py check <queue-dir> <item-id>")
            return 2
        res = check(positional[0], positional[1], repo=repo, profile=opts.get("profile", ""))
        if opts.get("json"):
            print(json.dumps(res, indent=2))
        else:
            print(_fmt_check(positional[1], res))
        return 0

    if cmd == "pending":
        # The dispatcher's handle, and the drill's - "is there an oracle round owed on this
        # item, and to which runner?". Prints NONE rather than nothing, so a caller can tell
        # "no escalation" from "the command did not run".
        if not positional:
            print("usage: oracle_on_stall.py pending <item-id> [--repo R] [--json]")
            return 2
        row = pending(repo, positional[0])
        if opts.get("json"):
            print(json.dumps(row, indent=2) if row else "null")
        else:
            print(row["oracle_runner"] if row else "NONE")
        return 0

    if cmd == "report":
        # FOLDED: one line per firing, showing its current state. The raw file keeps every
        # append; an operator asking "did the oracle fire?" wants the firings, not the
        # bookkeeping.
        rows = fold(read_ledger(repo, item=opts.get("item", "")))
        if opts.get("json"):
            print(json.dumps(rows, indent=2))
            return 0
        if not rows:
            print("no oracle-on-stall escalations on record.")
            return 0
        for r in rows:
            when = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(r.get("at", 0)))
            head = (when + "  " + str(r.get("item_id")) + "  " + str(r.get("outcome")) +
                    "  rounds=" + str(r.get("rounds")) + " stall=" + str(r.get("stall")) +
                    "/" + str(r.get("threshold")))
            if r.get("outcome") == "escalate":
                head += ("  " + str(r.get("stalled_runner")) + " -> " +
                         str(r.get("oracle_runner")) + " (hand back to " +
                         str(r.get("hand_back_to")) + ")")
                if r.get("consumed_by"):
                    head += "  [consumed by " + str(r.get("consumed_by")) + "]"
            print(head)
            print("    " + str(r.get("why")))
        return 0

    print("unknown command '" + cmd + "'")
    return 2


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
