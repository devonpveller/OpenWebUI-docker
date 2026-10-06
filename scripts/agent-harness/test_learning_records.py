"""Tests for learning_records.py - the learning-record gate (vwm-p1).

Every named refusal fires on a constructed bad fixture and stays silent on its GOOD TWIN
(the same fixture with only that one thing right). Everything is built under tmp_path: a
scratch code repo with a real merge, a scratch plan store, a scratch queue. Nothing here
reads or writes the live queue or the live plan store, except one read-only drift test
that compares the schema fixture with the store's schema file when the store is present.

    python -m pytest scripts/agent-harness/test_learning_records.py -q
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import learning_records as lr  # noqa: E402

SCHEMA_FIXTURE = HERE / "fixtures" / "learning-record" / "learning-record.schema.json"
try:
    LIVE_STORE = (lr.main_checkout(HERE) / lr.DEFAULT_STORE_REL).resolve()
except lr.Indeterminate:  # pragma: no cover - not inside a git checkout
    LIVE_STORE = HERE / "no-live-store"

try:
    import jsonschema  # noqa: F401
    HAVE_JSONSCHEMA = True
except ImportError:  # pragma: no cover - the cross-check says so instead of passing
    HAVE_JSONSCHEMA = False

ID = "lrx"


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture(autouse=True)
def _no_state_env(monkeypatch):
    monkeypatch.delenv("AI_STACK_WORKTREE_STATE", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")


@pytest.fixture
def world(tmp_path):
    """A code repo with base -> w1 (failed) -> w2 (tested) merged --no-ff as M; a store; a queue."""
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "line")
    git(repo, "config", "user.email", "lr@example.invalid")
    git(repo, "config", "user.name", "lr test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "work/lrx")
    (repo / "src").mkdir()
    (repo / "src" / "thing.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "w1")
    w1 = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "thing.py").write_text("x = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "w2")
    w2 = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "line")
    git(repo, "merge", "-q", "--no-ff", "work/lrx", "-m", "merge lrx")
    merge = git(repo, "rev-parse", "HEAD")

    store = tmp_path / "store"
    (store / ".git").mkdir(parents=True)  # a store is a directory with a .git; never run git in it
    schema_dir = store / "implementation-guide" / "research-workbench"
    schema_dir.mkdir(parents=True)
    shutil.copy(SCHEMA_FIXTURE, schema_dir / "learning-record.schema.json")
    findings = store / "implementation-guide" / "feat" / "findings"
    findings.mkdir(parents=True)
    (store / "implementation-guide" / "feat" / "test-evidence").mkdir()
    (store / "implementation-guide" / "feat" / "test-evidence" / "lrx.attempt2.md").write_text("## T1 PASS\n")
    (store / "journal" / "evidence" / "lrx").mkdir(parents=True)
    (store / "journal" / "evidence" / "lrx" / "run.log").write_text("ok\n")

    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / f"{ID}.attempt1.evidence.md").write_text("## T1 FAIL\n")
    (queue / f"{ID}.attempt2.evidence.md").write_text("## T1 PASS\n")
    return {"repo": repo, "store": store, "queue": queue, "findings": findings,
            "base": base, "w1": w1, "w2": w2, "merge": merge, "tmp": tmp_path}


def make_item(w, *, results=None, history=None, developer="wt-lrx", tested=None, sink=None):
    q = w["queue"]
    if results is None:
        results = [
            {"at": 100, "by": "t-lrx", "verdict": "fail", "attempt": 1, "sha": w["w1"],
             "evidence": str(q / f"{ID}.attempt1.evidence.md"), "reason": "T1 fails on a cold cache"},
            {"at": 300, "by": "t2-lrx", "verdict": "pass", "attempt": 2, "sha": w["w2"],
             "evidence": str(q / f"{ID}.attempt2.evidence.md"), "reason": ""},
        ]
    if history is None:
        history = [
            {"at": 10, "who": developer, "what": "anchor proposed"},
            {"at": 20, "who": "op", "what": "anchor confirmed"},
            {"at": 30, "who": developer, "what": "submitted for testing"},
            {"at": 40, "who": "t-lrx", "what": "claimed as tester"},
            {"at": 100, "who": "t-lrx", "what": "tests FAILED (attempt 1): T1 fails on a cold cache"},
            {"at": 200, "who": developer, "what": "re-submitted for testing (attempt 2)"},
            {"at": 300, "who": "t2-lrx", "what": "tests PASSED (attempt 2)"},
            {"at": 400, "who": "op", "what": "released for review"},
            {"at": 500, "who": "r-lrx", "what": "claimed as reviewer"},
        ]
    item = {
        "id": ID, "branch": "work/lrx", "line": "line", "developer": developer, "state": "reviewing",
        "anchor": {"goal": "the thing works", "findings_sink": sink or str(w["findings"] / f"{ID}.md")},
        "attempt": 2, "tested_at_sha": w["w2"] if tested is None else tested,
        "results": results, "history": history,
    }
    (q / f"{ID}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    return item


def good_record(w):
    return {
        "schema_version": 1, "fidelity": "iterative", "producer": "harness",
        "source_ref": {"queue_item_id": ID, "anchor_id": ID, "merge_range": f"{w['base'][:10]}..{w['merge'][:10]}"},
        "domains": ["drill"],
        "steer": "Anchor lrx: the thing works",
        "red": "attempt 1 failed T1 on a cold cache",
        "iterations": 1,
        "hypotheses_refuted": [{"hypothesis": "warm cache is enough", "why_refuted": "cold start differs",
                                "evidence": f"test-evidence/lrx.attempt2.md; commit {w['w2'][:8]}"}],
        "outcome": {"kind": "green", "summary": "src/thing.py sets x = 2",
                    "evidence": [f"merge {w['merge']}", "journal/evidence/lrx/run.log",
                                 "src/thing.py:1 at the merge", "findings/lrx.md (the findings note)"]},
        "mental_model": {"claim": "WORKER'S CLAIM: caches lie", "author_role": "worker"},
        "reviewer_check": {"checked": True, "by": "r-lrx", "note": "summary checked against the diff"},
        "created_at": "2026-10-06T00:00:00Z",
    }


def write_record(w, rec):
    p = w["findings"] / f"{ID}{lr.RECORD_SUFFIX}"
    if isinstance(rec, str):
        p.write_text(rec, encoding="utf-8")
    else:
        p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    (w["findings"] / f"{ID}.md").write_text("findings\n")
    return p


def run(w, *argv, merge=True, reviewer="r-lrx"):
    args = ["check", "--item", ID, "--queue-dir", str(w["queue"]), "--repo", str(w["repo"])]
    if merge:
        args += ["--merge-sha", w["merge"]]
    if reviewer:
        args += ["--reviewer", reviewer]
    args += list(argv)
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(io.StringIO()):
        code = lr.main(args)
    return code, buf.getvalue()


def reasons(out: str):
    rs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("- "):
            rs.append(line[2:].split(":")[0] if not line[2:].startswith("schema:") else
                      "schema:" + line[2:].split(":")[1].split()[0])
    return rs


# --------------------------------------------------------------- required / not required
def test_required_is_decided_from_the_queue_alone(world):
    w = world
    first_try = make_item(w, results=[{"at": 1, "by": "t", "verdict": "pass", "attempt": 1, "sha": w["w2"]}],
                          history=[{"at": 1, "who": "wt-lrx", "what": "re-submitted before testing: typo"},
                                   {"at": 2, "who": "op", "what": "anchor AMENDED (was 'x'): y"},
                                   {"at": 3, "who": "t", "what": "tests PASSED (attempt 1)"}])
    assert not lr.record_required(first_try)
    assert lr.derived_iterations(first_try) == 0
    failed = make_item(w)
    assert lr.record_required(failed) and lr.derived_iterations(failed) == 1


def test_an_improvement_send_back_is_required_and_counted(world):
    """Developer -Requeue after a PASS (ef-lc-upstream / ef-lane-500 shape): REQUIRED (D3)."""
    w = world
    item = make_item(w, results=[
        {"at": 100, "by": "t-lrx", "verdict": "pass", "attempt": 1, "sha": w["w1"], "evidence": "x"},
        {"at": 300, "by": "t2-lrx", "verdict": "pass", "attempt": 2, "sha": w["w2"], "evidence": "y"}],
        history=[{"at": 100, "who": "t-lrx", "what": "tests PASSED (attempt 1)"},
                 {"at": 150, "who": "wt-lrx", "what": "developer WITHDREW it from test-passed (now attempt 2): F1 fold-in"},
                 {"at": 300, "who": "t2-lrx", "what": "tests PASSED (attempt 2)"}])
    rets = lr.returns_of(item)
    assert lr.record_required(item)
    assert [r["kind"] for r in rets] == ["developer-requeue"]
    assert rets[0]["reason"] == "F1 fold-in"
    assert lr.derived_iterations(item) == 1


def test_every_return_kind_counts(world):
    w = world
    item = make_item(w, history=[
        {"at": 150, "who": "r", "what": "returned to test (now attempt 3): rebase changed it"},
        {"at": 160, "who": "dev", "what": "developer WITHDREW it from test-passed (now attempt 4): improve"},
        {"at": 170, "who": "r", "what": "rejected: misfits"}])
    kinds = [r["kind"] for r in lr.returns_of(item)]
    assert kinds == ["tester-fail", "reviewer-requeue", "developer-requeue", "reviewer-reject"]
    assert lr.derived_iterations(item) == 4


def test_not_required_is_accepted_without_a_record(world):
    w = world
    make_item(w, results=[{"at": 1, "by": "t", "verdict": "pass", "attempt": 1, "sha": w["w2"]}],
              history=[{"at": 3, "who": "t", "what": "tests PASSED (attempt 1)"}])
    code, out = run(w)
    assert code == 0 and "NOT REQUIRED" in out


# --------------------------------------------------------------- the good twin
def test_the_good_twin_is_accepted_and_declares_its_blind_spot(world):
    w = world
    make_item(w)
    write_record(w, good_record(w))
    code, out = run(w)
    assert code == 0, out
    assert "ACCEPTED" in out and "BLIND SPOT" in out


def test_head_may_be_the_tested_commit(world):
    w = world
    make_item(w)
    rec = good_record(w)
    rec["source_ref"]["merge_range"] = f"{w['base']}..{w['w2']}"
    write_record(w, rec)
    assert run(w)[0] == 0


def test_a_relative_sink_resolves_from_the_main_checkout(world):
    w = world
    rel = os.path.relpath(w["findings"] / f"{ID}.md", w["repo"]).replace("\\", "/")
    assert rel.startswith("../")
    make_item(w, sink=rel)
    write_record(w, good_record(w))
    assert run(w)[0] == 0


# --------------------------------------------------------------- each named refusal
def _mut(path, value):
    def f(rec):
        node = rec
        for k in path[:-1]:
            node = node[k]
        if value is DELETE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
    return f


DELETE = object()

RECORD_MUTATIONS = [
    ("schema:/steer", _mut(["steer"], DELETE)),
    ("schema:/surprise", _mut(["surprise"], "not in the schema")),
    ("schema:/outcome/kind", _mut(["outcome", "kind"], "greenish")),
    ("schema:/schema_version", _mut(["schema_version"], 2)),
    ("schema:/iterations", _mut(["iterations"], "1")),
    ("schema:/mental_model/author_role", _mut(["mental_model", "author_role"], DELETE)),
    ("item-mismatch", _mut(["source_ref", "queue_item_id"], "someone-else")),
    ("iterations-mismatch", _mut(["iterations"], 2)),
    ("iterations-mismatch", _mut(["iterations"], DELETE)),
    ("range-unresolved", _mut(["source_ref", "merge_range"], "")),
    ("range-unresolved", _mut(["source_ref", "merge_range"], "deadbeef1..cafef00d2")),
    ("range-head-mismatch", None),  # set per world below
    ("range-unresolved", None),     # base not an ancestor of head
    ("evidence-sha-unresolved", _mut(["outcome", "evidence"], ["commit 1234567abc that never existed"])),
    ("evidence-path-missing", _mut(["outcome", "evidence"], ["journal/evidence/lrx/never-written.md"])),
    ("green-not-genuine", _mut(["outcome", "kind"], "parked")),
    ("countersign-missing", _mut(["reviewer_check"], DELETE)),
    ("countersign-missing", _mut(["reviewer_check", "checked"], False)),
    ("countersign-missing", _mut(["reviewer_check", "by"], "wt-lrx")),
    ("countersign-missing", _mut(["reviewer_check", "by"], "r-somebody-else")),
    ("placeholder-unfilled", _mut(["outcome", "summary"], "<FILL: what landed>")),
]


@pytest.mark.parametrize("reason,mutate", RECORD_MUTATIONS, ids=[f"{r}-{i}" for i, (r, _) in enumerate(RECORD_MUTATIONS)])
def test_each_record_refusal_fires_and_its_good_twin_is_silent(world, reason, mutate):
    w = world
    make_item(w)
    good = good_record(w)
    write_record(w, good)
    code, out = run(w)
    assert code == 0, "good twin must be accepted: " + out
    bad = copy.deepcopy(good)
    if mutate is None and reason == "range-head-mismatch":
        bad["source_ref"]["merge_range"] = f"{w['base']}..{w['w1']}"
    elif mutate is None:
        bad["source_ref"]["merge_range"] = f"{w['w2']}..{w['w1']}"
    else:
        mutate(bad)
    write_record(w, bad)
    code, out = run(w)
    assert code == 1, out
    assert reason in reasons(out), out


ITEM_MUTATIONS = [
    # a record claiming green when the queue's LAST verdict was a fail
    ("green-not-genuine", "last-fail"),
    # the pass at the tested commit was recorded by the developer identity
    ("green-not-genuine", "dev-pass"),
    # no pass at the tested commit
    ("green-not-genuine", "no-pass-at-sha"),
    # the pass's evidence file is gone
    ("green-not-genuine", "no-evidence-file"),
]


@pytest.mark.parametrize("reason,how", ITEM_MUTATIONS)
def test_green_must_be_genuine(world, reason, how):
    w = world
    item = make_item(w)
    write_record(w, good_record(w))
    assert run(w)[0] == 0
    if how == "last-fail":
        item["results"].append({"at": 600, "by": "t3-lrx", "verdict": "fail", "attempt": 3, "sha": w["w2"],
                                "evidence": "x", "reason": "regressed"})
        item["history"].append({"at": 600, "who": "t3-lrx", "what": "tests FAILED (attempt 3): regressed"})
        # the record is otherwise right about the returns, so only genuineness can refuse it
        rec = good_record(w)
        rec["iterations"] = 2
        write_record(w, rec)
    elif how == "dev-pass":
        item["results"][-1]["by"] = "lrx"  # == wt-lrx once normalised
    elif how == "no-pass-at-sha":
        item["results"][-1]["sha"] = w["w1"]
    elif how == "no-evidence-file":
        (w["queue"] / f"{ID}.attempt2.evidence.md").unlink()
        item["results"][-1]["evidence"] = "inline prose, not a file"
    (w["queue"] / f"{ID}.json").write_text(json.dumps(item), encoding="utf-8")
    code, out = run(w)
    assert code == 1, out
    assert reasons(out) == [reason], out


def test_countersign_comes_from_the_queue_when_no_reviewer_is_passed(world):
    w = world
    make_item(w)
    write_record(w, good_record(w))
    assert run(w, reviewer="")[0] == 0  # 'claimed as reviewer' by r-lrx
    rec = good_record(w)
    rec["reviewer_check"]["by"] = "r-other"
    write_record(w, rec)
    code, out = run(w, reviewer="")
    assert code == 1 and reasons(out) == ["countersign-missing"]


def test_record_missing_and_sink_missing(world):
    w = world
    make_item(w)
    code, out = run(w)
    assert code == 1 and reasons(out) == ["record-missing"], out
    item = make_item(w)
    item["anchor"].pop("findings_sink")
    (w["queue"] / f"{ID}.json").write_text(json.dumps(item), encoding="utf-8")
    code, out = run(w)
    assert code == 1 and reasons(out) == ["sink-missing"], out


# --------------------------------------------------------------- indeterminate is never a pass
def test_a_store_that_is_not_there_is_indeterminate_not_missing(world):
    w = world
    make_item(w, sink=str(w["tmp"] / "no-store-here" / "implementation-guide" / "feat" / "findings" / "lrx.md"))
    code, out = run(w)
    assert code == 3, out
    assert "store-unreadable" in out and "NOT a missing record" in out


def test_an_unreadable_record_is_indeterminate(world):
    w = world
    make_item(w)
    write_record(w, "{ not json")
    code, out = run(w)
    assert code == 3 and "record-unreadable" in out


def test_a_missing_schema_is_indeterminate(world):
    w = world
    make_item(w)
    write_record(w, good_record(w))
    (w["store"] / lr.SCHEMA_REL).unlink()
    code, out = run(w)
    assert code == 3 and "schema-unreadable" in out


def test_a_schema_keyword_the_validator_does_not_know_is_indeterminate(world):
    w = world
    make_item(w)
    write_record(w, good_record(w))
    sp = w["store"] / lr.SCHEMA_REL
    s = json.loads(sp.read_text(encoding="utf-8"))
    s["properties"]["steer"]["minLength"] = 1
    sp.write_text(json.dumps(s), encoding="utf-8")
    code, out = run(w)
    assert code == 3 and "minLength" in out


def test_a_missing_queue_item_is_indeterminate(world):
    w = world
    code, out = run(w)
    assert code == 3 and "queue-unreadable" in out


# --------------------------------------------------------------- read-only
def _snapshot(*roots):
    h = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                h[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


def test_check_audit_and_draft_write_nothing(world):
    w = world
    make_item(w)
    write_record(w, good_record(w))
    before = _snapshot(w["store"], w["queue"])
    run(w)
    with redirect_stdout(io.StringIO()):
        lr.main(["audit", "--store", str(w["store"]), "--queue-dir", str(w["queue"]), "--repo", str(w["repo"])])
        with redirect_stderr(io.StringIO()):
            lr.main(["draft", "--item", ID, "--queue-dir", str(w["queue"]), "--repo", str(w["repo"])])
    assert _snapshot(w["store"], w["queue"]) == before


# --------------------------------------------------------------- audit
def test_audit_reports_one_line_per_violation_and_exits_1(world):
    w = world
    item = make_item(w)
    item["state"] = "merged"
    item["merged_sha"] = w["merge"]
    item["history"].append({"at": 700, "who": "r-lrx", "what": f"merged as {w['merge']}"})
    (w["queue"] / f"{ID}.json").write_text(json.dumps(item), encoding="utf-8")
    write_record(w, good_record(w))
    args = ["audit", "--store", str(w["store"]), "--queue-dir", str(w["queue"]), "--repo", str(w["repo"]),
            "--since", "1970-01-01"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert lr.main(args) == 0, buf.getvalue()
    assert "CLEAN" in buf.getvalue()
    rec = good_record(w)
    rec["iterations"] = 2
    rec["reviewer_check"]["checked"] = False
    write_record(w, rec)
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert lr.main(args) == 1
    lines = [x for x in buf.getvalue().splitlines() if x.startswith("VIOLATION")]
    assert len(lines) == 2
    assert any("iterations-mismatch" in x for x in lines) and any("countersign-missing" in x for x in lines)
    assert "TOTALS: 1 record(s)" in buf.getvalue()


# --------------------------------------------------------------- draft
def test_draft_derives_the_facts_and_a_filled_draft_passes(world):
    w = world
    make_item(w)
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        assert lr.main(["draft", "--item", ID, "--merge-sha", w["merge"], "--reviewer", "r-lrx",
                        "--queue-dir", str(w["queue"]), "--repo", str(w["repo"])]) == 0
    d = json.loads(buf.getvalue())
    assert d["iterations"] == 1
    assert d["source_ref"]["merge_range"] == f"{w['base']}..{w['merge']}"
    assert "T1 fails on a cold cache" in d["red"]
    assert d["outcome"]["kind"] == "green"
    assert "nothing was written" in err.getvalue()
    # as printed, with its placeholders, the check refuses it
    write_record(w, d)
    code, out = run(w)
    assert code == 1 and "placeholder-unfilled" in reasons(out) and "countersign-missing" in reasons(out)
    # filled in by the people who own those fields, it passes
    d["domains"] = ["drill"]
    d["red"] = d["red"].split(lr.PLACEHOLDER)[0] + "the failing state"
    d["outcome"]["summary"] = "x = 2"
    d["mental_model"]["claim"] = "WORKER'S CLAIM: caches lie"
    d.pop("skill_candidate")
    d["reviewer_check"] = {"checked": True, "by": "r-lrx", "note": "checked"}
    write_record(w, d)
    code, out = run(w)
    assert code == 0, out


# --------------------------------------------------------------- tokens
def test_tokens_split_shas_paths_and_prose():
    shas, paths, prose = lr.tokens_of("commit 6658af52c8 (attempt 1); see test-evidence/x.attempt1.md:12 and F1/A4")
    assert shas == ["6658af52c8"]
    assert paths == ["test-evidence/x.attempt1.md"]
    assert prose
    shas, paths, prose = lr.tokens_of("range 93d9b09f..687185e7")
    assert shas == ["93d9b09f", "687185e7"]
    shas, _, _ = lr.tokens_of("deadbeefcafe has no digit; abc123 is too short")
    assert shas == []
    shas, paths, prose = lr.tokens_of("1234567")
    assert shas == ["1234567"] and not prose


# --------------------------------------------------------------- validator vs jsonschema
def _fixture_records(w):
    good = good_record(w)
    out = [good]
    for _, m in RECORD_MUTATIONS:
        if m is None:
            continue
        b = copy.deepcopy(good)
        m(b)
        out.append(b)
    out += [{}, [], {"schema_version": True}, {**good, "iterations": 1.0}, {**good, "iterations": -1},
            {**good, "domains": []}, {**good, "created_at": 5}]
    return out


@pytest.mark.skipif(not HAVE_JSONSCHEMA, reason="jsonschema is not installed - the stdlib validator is NOT cross-checked in this run")
def test_the_stdlib_validator_agrees_with_jsonschema(world):
    import jsonschema as js
    schema = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    validator = js.Draft202012Validator(schema)
    corpus = _fixture_records(world)
    if LIVE_STORE.is_dir():
        corpus += [json.loads(p.read_text(encoding="utf-8-sig")) for p in LIVE_STORE.rglob(f"*{lr.RECORD_SUFFIX}")]
    for inst in corpus:
        ours = lr.validate(schema, inst)
        theirs = list(validator.iter_errors(inst))
        assert bool(ours) == bool(theirs), (inst, ours, [e.message for e in theirs])


def test_the_schema_fixture_matches_the_store_schema_when_present():
    live = LIVE_STORE / lr.SCHEMA_REL
    if not live.is_file():
        pytest.skip("plan store not beside this checkout - drift not checked")
    assert json.loads(live.read_text(encoding="utf-8")) == json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
