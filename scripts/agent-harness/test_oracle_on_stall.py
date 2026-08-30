"""Tests for frontier-oracle-on-stall (U4).

Two things these tests are FOR, beyond the usual:

  1. **A detector that always fires is as useless as one that never does.** Every stall case
     below has a control: the same shape with the stall condition removed, asserted NOT to
     fire. A test suite that only proves firing would pass on `return True`.

  2. **The stall definition is agent-org's, and must stay agent-org's.** `test_signature_
     matches_agent_org_verbatim` extracts `_failure_sig`'s body from `orchestrator.py` and
     runs it, so the two implementations are compared by BEHAVIOUR on live source rather
     than by a comment claiming they match. If agent-org changes its normalization, this
     fails - which is the whole reason to port it verbatim.

    python -m pytest scripts/agent-harness/test_oracle_on_stall.py -q
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import oracle_on_stall as oos  # noqa: E402

REPO = HERE.parents[1]
ORCHESTRATOR = REPO / "agent-org" / "agent-bridge" / "app" / "orchestrator.py"


# --------------------------------------------------------------------------------------
# the signature - pinned to agent-org's, on live source
# --------------------------------------------------------------------------------------

def _agent_org_failure_sig():
    """Build a callable from `_failure_sig`'s ACTUAL body in orchestrator.py.

    Not a copy of it - a copy would drift in exactly the way this test exists to catch.
    """
    src = ORCHESTRATOR.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"def _failure_sig\(log: str\) -> str:\n(.*?)\n\n", src, re.S)
    assert m, "could not locate _failure_sig in orchestrator.py - the pin has broken"
    body = m.group(1)
    # Drop the docstring; keep the statements.
    body = re.sub(r'^\s*""".*?"""\n', "", body, flags=re.S)
    lines = [ln[8:] if ln.startswith(" " * 8) else ln for ln in body.splitlines()]
    ns = {"re": re, "hashlib": hashlib}
    exec("def _sig(log):\n" + "\n".join("    " + ln for ln in lines), ns)  # noqa: S102
    return ns["_sig"]


@pytest.mark.parametrize("log", [
    "",
    "error: cannot find symbol Foo\nat line 42",
    "AssertionError: expected 3 got 4\n  File \"x.py\", line 118\n  deadbeefcafe1234",
    "\n".join(f"line {i} of a long log" for i in range(60)),
    "MiXeD CaSe Error With 0xdeadbeef And 12345",
])
def test_signature_matches_agent_org_verbatim(log):
    assert oracle_sig(log) == _agent_org_failure_sig()(log)


def oracle_sig(log):
    return oos.failure_signature(log)


def test_signature_masks_digits_and_hashes():
    a = oos.failure_signature("failed after 3 attempts at 0123456789ab")
    b = oos.failure_signature("failed after 91 attempts at fedcba9876543")
    assert a == b, "run-to-run digits and hashes must not make one failure look like two"


def test_signature_distinguishes_different_failures():
    assert oos.failure_signature("cannot find symbol") != oos.failure_signature("null pointer")


def test_signature_uses_only_the_last_25_lines():
    tail = "\n".join(f"tail line {i}" for i in range(25))
    assert oos.failure_signature("preamble\n" * 200 + tail) == oos.failure_signature(tail)


# --------------------------------------------------------------------------------------
# the stall test - and its controls
# --------------------------------------------------------------------------------------

def R(text, sha):
    return {"text": text, "sha": sha}


def test_progressing_rounds_never_stall():
    """THE CONTROL. New failure, new commit, every round - the detector must stay silent."""
    v = oos.evaluate([R("error A", "aaa1"), R("error B", "bbb2"),
                      R("error C", "ccc3"), R("error D", "ddd4")])
    assert v["stalled"] is False
    assert v["stall"] == 0
    assert all(t["progress"] for t in v["trail"])


def test_same_failure_repeated_stalls():
    v = oos.evaluate([R("error A", "aaa1"), R("error A", "bbb2"), R("error A", "ccc3")])
    assert v["stalled"] is True
    assert v["stall"] == 2
    assert [t["progress"] for t in v["trail"]] == [True, False, False]


def test_one_repeat_is_not_yet_a_stall():
    """The threshold is 2, like agent-org's. One bad round is a bad round."""
    v = oos.evaluate([R("error A", "aaa1"), R("error A", "bbb2")])
    assert v["stalled"] is False
    assert v["stall"] == 1


def test_a_b_a_cycle_stalls_because_novelty_is_against_the_whole_set():
    """agent-org paid for this one: a one-step comparison scores A->B->A as progress
    forever. Novelty is measured against EVERY signature seen."""
    v = oos.evaluate([R("A", "s1"), R("B", "s2"), R("A", "s3"), R("B", "s4")])
    assert v["stalled"] is True
    assert v["signatures_seen"] == 2


def test_a_b_c_does_not_stall_control_for_the_cycle_case():
    v = oos.evaluate([R("A", "s1"), R("B", "s2"), R("C", "s3"), R("D", "s4")])
    assert v["stalled"] is False


def test_changing_failure_on_an_unchanged_commit_stalls():
    """The flail axis. A failure that changes while the code does not is NOISE - §6's
    hygiene rule. Without this a flaky test resets the stall counter forever."""
    v = oos.evaluate([R("A", "s1"), R("B", "s1"), R("C", "s1")])
    assert v["stalled"] is True
    assert [t["moved"] for t in v["trail"]] == [True, False, False]
    assert "the code did not" in v["trail"][1]["why"]


def test_changing_failure_on_a_changed_commit_does_not_stall_control():
    v = oos.evaluate([R("A", "s1"), R("B", "s2"), R("C", "s3")])
    assert v["stalled"] is False


def test_missing_sha_counts_as_not_moved():
    """An unrecorded head is not evidence that the code moved - fail toward detecting."""
    v = oos.evaluate([R("A", "s1"), R("B", ""), R("C", "")])
    assert v["stalled"] is True


def test_no_rounds_and_one_round_never_stall():
    assert oos.evaluate([])["stalled"] is False
    assert oos.evaluate([R("A", "s1")])["stalled"] is False


def test_recovery_resets_the_counter():
    """A real step forward un-stalls the item: the counter resets, it does not decay."""
    v = oos.evaluate([R("A", "s1"), R("A", "s2"), R("B", "s3")])
    assert v["stall"] == 0
    assert v["stalled"] is False


def test_trail_records_what_the_detector_saw():
    v = oos.evaluate([R("A", "s1"), R("A", "s2"), R("A", "s3")])
    assert [t["round"] for t in v["trail"]] == [1, 2, 3]
    assert [t["stall_after"] for t in v["trail"]] == [0, 1, 2]
    assert all(t["sig"] and t["why"] for t in v["trail"])


# --------------------------------------------------------------------------------------
# the escalation target
# --------------------------------------------------------------------------------------

class FakeCfg:
    """A config stand-in. The real `config` module is exercised by the live-config test
    below; these cases need profiles the shipped file may not have."""

    def __init__(self, runners, worker):
        self._runners = runners
        self._worker = worker

    def get(self, path, default=None):
        return self._runners if path == "runners" else default

    def resolve_role(self, role, profile="", surface=""):
        assert role == "worker"
        return dict(self._worker, role=role, profile=profile or "test-profile")


RUNNERS = {
    "claude-code": {"kind": "claude-code", "status": "proven", "default_model": "opus"},
    "little-coder": {"kind": "little-coder", "status": "unproven",
                     "default_model": "local-default"},
}


def test_local_worker_escalates_to_the_frontier_and_hands_back():
    cfg = FakeCfg(RUNNERS, {"runner": "little-coder", "model": "local-default"})
    e = oos.resolve_escalation(profile="all-local", cfg=cfg)
    assert e["outcome"] == "escalate"
    assert e["oracle"]["runner"] == "claude-code"
    assert e["hand_back_to"] == "little-coder", "§7: the oracle hands back, it does not take over"


def test_frontier_worker_has_no_oracle_above_it():
    """The honest case, and the one today's all-cloud default is in. Escalating
    claude-code -> claude-code would satisfy an audit trail while changing nothing."""
    cfg = FakeCfg(RUNNERS, {"runner": "claude-code", "model": "opus"})
    e = oos.resolve_escalation(profile="all-cloud", cfg=cfg)
    assert e["outcome"] == "no-oracle-above"
    assert e["oracle"] is None


def test_no_frontier_runner_configured_is_reported_not_guessed():
    cfg = FakeCfg({"little-coder": RUNNERS["little-coder"]},
                  {"runner": "little-coder", "model": "local-default"})
    e = oos.resolve_escalation(cfg=cfg)
    assert e["outcome"] == "no-oracle-configured"


def test_oracle_runner_is_found_by_kind_not_by_name():
    cfg = FakeCfg({"frontier-thing": {"kind": "claude-code", "default_model": "opus"}},
                  {"runner": "little-coder", "model": "x"})
    assert oos.oracle_runner_name(cfg) == "frontier-thing"


def test_live_config_resolves_an_oracle_for_the_shipped_local_profile():
    """Against the REAL harness.config.json: the 95/5 split has somewhere to escalate to."""
    import config
    e = oos.resolve_escalation(profile="local-work-cloud-review", cfg=config)
    assert e["outcome"] == "escalate"
    assert e["worker"]["runner"] == "little-coder"
    assert e["oracle"]["runner"] == "claude-code"


# --------------------------------------------------------------------------------------
# the ledger - the observation
# --------------------------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_STACK_WORKTREE_STATE", str(tmp_path / "state"))
    return tmp_path


def _fire(repo, item="itm", rounds=None):
    rounds = rounds or [R("A", "s1"), R("A", "s2"), R("A", "s3")]
    v = oos.evaluate(rounds)
    cfg = FakeCfg(RUNNERS, {"runner": "little-coder", "model": "local-default"})
    return v, oos.record(repo, item=item, verdict=v,
                         escalation=oos.resolve_escalation(profile="all-local", cfg=cfg))


def test_a_firing_is_recorded_with_the_evidence_that_produced_it(ledger):
    v, row = _fire(ledger)
    assert row is not None
    rows = oos.read_ledger(ledger)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "escalate"
    assert rows[0]["stalled_runner"] == "little-coder"
    assert rows[0]["oracle_runner"] == "claude-code"
    assert len(rows[0]["trail"]) == 3, "the record must carry what the detector saw"


def test_the_same_firing_is_not_recorded_twice(ledger):
    _fire(ledger)
    _, second = _fire(ledger)
    assert second is None
    assert len(oos.read_ledger(ledger)) == 1


def test_a_later_round_is_a_new_firing(ledger):
    _fire(ledger)
    _, second = _fire(ledger, rounds=[R("A", "s1"), R("A", "s2"), R("A", "s3"), R("A", "s4")])
    assert second is not None
    assert len(oos.read_ledger(ledger)) == 2


def test_pending_then_consume_hands_back(ledger):
    _fire(ledger)
    p = oos.pending(ledger, "itm")
    assert p and p["hand_back_to"] == "little-coder"
    oos.consume(ledger, "itm", by="oracle-round-1")
    assert oos.pending(ledger, "itm") is None
    assert oos.read_ledger(ledger)[-1]["consumed_by"] == "oracle-round-1"


def test_no_oracle_above_is_recorded_but_is_not_a_pending_escalation(ledger):
    v = oos.evaluate([R("A", "s1"), R("A", "s2"), R("A", "s3")])
    cfg = FakeCfg(RUNNERS, {"runner": "claude-code", "model": "opus"})
    oos.record(ledger, item="cloudy", verdict=v,
               escalation=oos.resolve_escalation(cfg=cfg))
    assert oos.read_ledger(ledger, item="cloudy")[0]["outcome"] == "no-oracle-above"
    assert oos.pending(ledger, "cloudy") is None


def test_a_corrupt_line_does_not_hide_the_firings_that_landed(ledger):
    _fire(ledger)
    with oos.ledger_path(ledger).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert len(oos.read_ledger(ledger)) == 1


# --------------------------------------------------------------------------------------
# the queue adapter, and the whole thing end to end
# --------------------------------------------------------------------------------------

def _write_item(queue_dir, item_id, results, developer="dev", profile=""):
    queue_dir.mkdir(parents=True, exist_ok=True)
    item = {"id": item_id, "branch": "work/" + item_id, "developer": developer,
            "state": "test-failed", "attempt": len(results), "results": results,
            "history": []}
    if profile:
        item["profile"] = profile
    (queue_dir / (item_id + ".json")).write_text(json.dumps(item), encoding="utf-8")
    return item


def test_failing_rounds_ignores_passes():
    item = {"results": [{"verdict": "fail", "reason": "A", "sha": "s1", "evidence": "e"},
                        {"verdict": "pass", "reason": "", "sha": "s2", "evidence": "e"}]}
    assert len(oos.failing_rounds(item)) == 1


def test_failing_rounds_reads_evidence_stored_as_a_file(tmp_path):
    (tmp_path / "itm.attempt1.evidence.md").write_text("the real failure text",
                                                       encoding="utf-8")
    item = {"results": [{"verdict": "fail", "reason": "case 3", "sha": "s1",
                         "evidence": "itm.attempt1.evidence.md"}]}
    rounds = oos.failing_rounds(item, tmp_path)
    assert "the real failure text" in rounds[0]["text"], (
        "signing the PATH instead of the file would make every round look identical")


def test_check_fires_on_a_stalled_queue_item(ledger):
    q = ledger / "queue"
    _write_item(q, "itm", [
        {"verdict": "fail", "reason": "case 3: same wall", "sha": "s1", "evidence": "x"},
        {"verdict": "fail", "reason": "case 3: same wall", "sha": "s2", "evidence": "x"},
        {"verdict": "fail", "reason": "case 3: same wall", "sha": "s3", "evidence": "x"},
    ], profile="all-local")
    res = oos.check(q, "itm", repo=ledger)
    assert res["verdict"]["stalled"] is True
    assert res["escalation"]["outcome"] == "escalate"
    assert res["recorded"] is not None
    assert oos.pending(ledger, "itm") is not None


def test_check_stays_silent_on_a_progressing_queue_item(ledger):
    """THE CONTROL, end to end: remove the stall condition and nothing is recorded."""
    q = ledger / "queue"
    _write_item(q, "moving", [
        {"verdict": "fail", "reason": "case 1: missing guard", "sha": "s1", "evidence": "x"},
        {"verdict": "fail", "reason": "case 2: wrong exit code", "sha": "s2", "evidence": "y"},
        {"verdict": "fail", "reason": "case 4: path not quoted", "sha": "s3", "evidence": "z"},
    ], profile="all-local")
    res = oos.check(q, "moving", repo=ledger)
    assert res["verdict"]["stalled"] is False
    assert res["recorded"] is None
    assert oos.read_ledger(ledger, item="moving") == []


def test_check_uses_the_profile_recorded_on_the_item(ledger):
    q = ledger / "queue"
    fails = [{"verdict": "fail", "reason": "same", "sha": "s%d" % i, "evidence": "x"}
             for i in (1, 2, 3)]
    _write_item(q, "cloudy", fails, profile="all-cloud")
    res = oos.check(q, "cloudy", repo=ledger)
    assert res["escalation"]["outcome"] == "no-oracle-above"
    _write_item(q, "local", fails, profile="local-work-cloud-review")
    res = oos.check(q, "local", repo=ledger)
    assert res["escalation"]["outcome"] == "escalate"


def test_check_on_a_missing_item_says_so(ledger):
    with pytest.raises(FileNotFoundError):
        oos.check(ledger / "queue", "nope", repo=ledger)


def test_report_folds_a_consumed_firing_into_one_row(ledger):
    """The raw ledger keeps every append; a reader asking "did it fire?" must not see the
    same firing twice because it was later consumed."""
    _fire(ledger)
    oos.consume(ledger, "itm", by="oracle-round-1")
    assert len(oos.read_ledger(ledger)) == 2
    folded = oos.fold(oos.read_ledger(ledger))
    assert len(folded) == 1
    assert folded[0]["consumed_by"] == "oracle-round-1"


def test_the_ledger_names_the_item_field_item_id_not_item(ledger):
    """A LANDMINE, defused deliberately. `.item` on a .NET collection resolves to the
    IList indexer, so a PowerShell reader writing `$_.item -eq $id` compares a PSMethod to
    a string: false, always, and silently. That is exactly how this drill's control checks
    came to pass while checking nothing. The field is `item_id` so no reader can trip it."""
    _fire(ledger, item="itm")
    row = oos.read_ledger(ledger)[0]
    assert "item_id" in row and row["item_id"] == "itm"
    assert "item" not in row
