"""COMPLETENESS PROOF for the judge-enablement chokepoint.

The fix this file guards is not "the regex was patched". Three U5 branches in a
row closed a reported defect and were then walked through by the NEIGHBOURING
case: a different YAML spelling, a different tool on the same allow-list, a
different channel carrying the same lie. Patching routes loses, because there
is always one more route.

So the decision moved to a chokepoint -- `littlecoder.judge_gate` -- and THIS
file is what makes the chokepoint's completeness mechanical rather than
asserted. It enumerates, by AST, every site in the package that could decide
the question without the gate, and fails when a new one appears:

  * reading `observer.judge_enabled` anywhere but `judge_gate.py`;
  * constructing a `Judge` anywhere but `meta_wiring.py`;
  * `meta_wiring.build_meta_runner` no longer calling the gate;
  * a second implementation of the rating-record rule inside the package.

A future wiring path CANNOT bypass the gate by omission, because the only two
ways to answer "is the judge on?" -- call the gate, or read the field -- are
both watched here. That is the difference between a guard and a patch.

What this file does NOT claim: it cannot stop someone editing `judge_gate.py`
itself, and it does not reason about dynamic access (`getattr(cfg.observer,
name)` built from a runtime string). Both are stated in judge_gate.py's header
and neither is a spelling-of-YAML problem, which is the class this round is
closing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from littlecoder import judge_gate

SRC = Path(judge_gate.__file__).resolve().parent
GATE_MODULE = "judge_gate.py"
WIRING_MODULE = "meta_wiring.py"
FLAG = judge_gate.FLAG_KEY  # "judge_enabled"

# A scan that finds nothing passes for free. This is the floor the package was
# at when the test was written (38 modules); it exists so that a broken path,
# a renamed package directory, or a glob that stops matching turns this file
# RED instead of quietly green -- the "EXPECTED_CASES" discipline the flag
# drill already uses.
MIN_MODULES_SCANNED = 25


def _modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_scan_is_not_vacuous():
    """The enumeration below only means something if it enumerated something."""
    mods = _modules()
    assert len(mods) >= MIN_MODULES_SCANNED, (
        "only %d modules found under %s -- the scan the other tests in this "
        "file depend on is not reaching the package" % (len(mods), SRC)
    )
    assert (SRC / GATE_MODULE) in mods
    assert (SRC / WIRING_MODULE) in mods


def test_only_the_gate_reads_the_flag():
    """Every read of `observer.judge_enabled` in the package must be inside
    the gate. Two forms count: attribute access (`cfg.observer.judge_enabled`)
    and a bare string used as a key or a getattr name (`"judge_enabled"`).
    `config.py` declares the field as an annotated assignment, which is a
    declaration and not a read, so it does not appear here."""
    offenders = []
    for path in _modules():
        if path.name == GATE_MODULE:
            continue
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Attribute) and node.attr == FLAG:
                offenders.append("%s:%d attribute read" % (path.name, node.lineno))
            elif isinstance(node, ast.Constant) and node.value == FLAG:
                offenders.append("%s:%d string key" % (path.name, node.lineno))
    assert not offenders, (
        "these sites decide the judge-enablement question without the gate:\n  "
        + "\n  ".join(offenders)
        + "\nRoute them through littlecoder.judge_gate.admit/require instead. "
        "The gate is the only place the rating-record rule is applied; a "
        "direct read of the flag skips it silently."
    )


def test_only_meta_wiring_constructs_a_judge():
    """The gate is only worth anything if the thing it gates has one door.
    `judge.py` DEFINES the class (a ClassDef, not a call); production code may
    only CONSTRUCT one in `meta_wiring`, which is gated."""
    offenders = []
    for path in _modules():
        if path.name == WIRING_MODULE:
            continue
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name == "Judge":
                offenders.append("%s:%d" % (path.name, node.lineno))
    assert not offenders, (
        "a Judge is constructed outside the gated wiring path at:\n  "
        + "\n  ".join(offenders)
        + "\nEvery production construction must go through meta_wiring."
        "build_meta_runner, which calls judge_gate.require first."
    )


def test_meta_wiring_still_calls_the_gate():
    """The counterpart to the two scans above: they stop a NEW unguarded site
    appearing, this one stops the guarded site quietly losing its guard. A
    `build_meta_runner` that neither calls the gate nor reads the flag would
    otherwise satisfy every other test in this file."""
    tree = _tree(SRC / WIRING_MODULE)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("judge_gate")
        for alias in node.names
    }
    assert imported, "meta_wiring no longer imports anything from judge_gate"

    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "build_meta_runner"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called & imported, (
        "build_meta_runner does not call anything imported from judge_gate; "
        "it calls %s. The judge cannot be wired without the gate deciding it."
        % sorted(called)
    )


def test_one_definition_of_the_rating_rule_in_the_package():
    """The rating-record rule has exactly one implementation. A second copy is
    how a guard ends up disagreeing with the daemon -- the same drift class as
    the regex this round replaced."""
    holders = [
        p.name
        for p in _modules()
        if '"rated_by"' in p.read_text(encoding="utf-8")
        or "'rated_by'" in p.read_text(encoding="utf-8")
    ]
    assert holders == [GATE_MODULE], (
        "the rating-record key set appears in %s; it must appear only in %s "
        "(judge_gate.RATING_REQUIRED)" % (holders, GATE_MODULE)
    )


# --- the gate's own behaviour, so the scans above guard something real -----


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


APPROVED = (
    "rated_by: operator\n"
    "rated_at: 2026-08-30T12:00:00Z\n"
    "rated_report: dryrun/judge-dryrun-little-coder.json\n"
    "verdict: approve\n"
)


@pytest.mark.parametrize(
    "body,valid",
    [
        (APPROVED, True),
        ("---\n" + APPROVED + "---\nnotes\n", True),  # frontmatter form
        (APPROVED.replace("approve", "reject"), False),
        ("rated_by: operator\nverdict: approve\n", False),  # missing keys
        (APPROVED.replace("rated_by: operator", "rated_by: ''"), False),  # empty value
        ("just a sentence\n", False),  # not a mapping
        ("a: [1,\n", False),  # not YAML
    ],
)
def test_rating_record_rule(tmp_path, body, valid):
    record, problem = judge_gate.read_rating_record(_write(tmp_path, "r.yaml", body))
    assert (record is not None) is valid, problem


def test_rating_record_path_resolution_order(tmp_path):
    """explicit argument > env override > the container path compose mounts."""
    assert judge_gate.resolve_rating_record_path("/x.yaml", {}) == "/x.yaml"
    assert (
        judge_gate.resolve_rating_record_path(None, {judge_gate.RATING_RECORD_ENV: "/y.yaml"})
        == "/y.yaml"
    )
    assert (
        judge_gate.resolve_rating_record_path(None, {})
        == judge_gate.DEFAULT_RATING_RECORD_PATH
    )
