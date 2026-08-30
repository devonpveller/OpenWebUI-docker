"""Tests for the anchor schema — including that both readers agree.

The cross-language test is the point of this file, as it is in test_harness_config.py. Two
readers of one schema is a standing invitation to drift: someone tightens a rule in
anchor.ps1, anchor_schema.py keeps accepting the old shape, and nothing complains until an
anchor means two different things to the tester and the bridge.

So the corpus below is fed to BOTH readers and their PROBLEM LISTS are compared, not just
their verdicts. Two readers that agree an anchor is invalid while disagreeing about why
have already drifted — the boolean would stay green through it.

    python -m pytest scripts/agent-harness/test_anchor_schema.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import anchor_schema  # noqa: E402

PS = shutil.which("powershell") or shutil.which("pwsh")

# A valid mode B anchor — the shape every anchor in the queue today has.
VALID_B = {
    "goal": "Rework the coder plane README so it reads as an operator document.",
    "artifact": "coder/README.md - an operator-facing compose-plane README.",
    "audience": "Someone who has to OPERATE this plane, arriving with a task.",
    "acceptance": ["No section whose subject is disagreement between documents."],
    "out_of_scope": ["Rewriting the little-coder design doc."],
    "findings_sink": "documentation/notes/coder-plane-findings.md",
}

VALID_A = {
    "mode": "A",
    "north_star": "A command-line todo tool a real person would actually enjoy using.",
    "audience": "A real person managing their own tasks, not a developer reading code.",
    "constraints": ["The path does not run through packaging metadata or linting config."],
}


def _ps_problems(anchor: dict, schema_path: Path | None = None,
                 tmp_path: Path | None = None) -> list[str]:
    """Ask the PowerShell reader the same question, get its problem list back.

    The anchor goes via a FILE, not inline in -Command. Embedding JSON in a PowerShell
    double-quoted string does not work: JSON's \\" escapes are not PowerShell escapes, so
    the string terminates early and ConvertFrom-Json silently yields $null — which showed
    up as every anchor being "empty". A temp file has no quoting layer to get wrong.
    """
    import tempfile
    d = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    payload = d / "anchor.json"
    payload.write_text(json.dumps(anchor), encoding="utf-8")

    dot = (HERE / "anchor.ps1").as_posix()
    override = ""
    if schema_path is not None:
        override = "$script:AnchorSchemaPath='{0}';".format(Path(schema_path).as_posix())
    script = (
        f". '{dot}';"
        + override
        + f"$a = Get-Content -Raw -LiteralPath '{payload.as_posix()}' | ConvertFrom-Json;"
        + "$p = @(Test-Anchor $a);"
        + "ConvertTo-Json -Depth 4 -Compress @{ problems = $p }"
    )
    out = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    parsed = json.loads(out.stdout.strip())["problems"]
    # ConvertTo-Json renders a 0- or 1-element array as null / a bare string.
    if parsed is None:
        return []
    return [parsed] if isinstance(parsed, str) else list(parsed)


# ── the schema is the single source of the shape ─────────────────────────────
def test_the_field_spec_is_not_hardcoded_in_either_reader():
    """PLAN A.2: no literal that encodes policy survives in source."""
    ps_src = (HERE / "anchor.ps1").read_text(encoding="utf-8")
    py_src = (HERE / "anchor_schema.py").read_text(encoding="utf-8")
    # The old hardcoded table is gone, and neither reader restates the rationale text.
    assert "$script:AnchorFields" not in ps_src
    for phrase in ("what stops goal drift across test cycles",
                   "the field that would have prevented the coder README"):
        assert phrase not in ps_src, "field rationale still hardcoded in anchor.ps1"
        assert phrase not in py_src, "field rationale still hardcoded in anchor_schema.py"
    # And it IS in the schema.
    schema = json.loads((HERE / "anchor.schema.json").read_text(encoding="utf-8"))
    assert "what stops goal drift across test cycles" in schema["modes"]["B"]["fields"]["goal"]["why"]


def test_a_missing_schema_is_loud_not_a_silent_fallback(tmp_path):
    """No built-in default shape: a fallback is how two readers drift while staying green."""
    with pytest.raises(anchor_schema.AnchorSchemaError):
        anchor_schema.load(fresh=True, path=tmp_path / "nope.json")


# ── mode B is exactly what it was ────────────────────────────────────────────
def test_valid_mode_b_anchor_passes():
    assert anchor_schema.problems(VALID_B) == []


def test_absent_mode_means_b_so_every_existing_anchor_stays_valid():
    assert "mode" not in VALID_B
    assert anchor_schema.mode_of(VALID_B) == "B"


@pytest.mark.parametrize("field", ["goal", "artifact", "audience", "acceptance", "out_of_scope"])
def test_mode_b_required_fields(field):
    a = dict(VALID_B)
    a.pop(field)
    found = anchor_schema.problems(a)
    assert any(f"'{field}'" in p for p in found), found


def test_findings_sink_stays_optional():
    a = dict(VALID_B)
    a.pop("findings_sink")
    assert anchor_schema.problems(a) == []


def test_a_vague_acceptance_criterion_is_refused():
    a = dict(VALID_B, acceptance=["it works"])
    assert any("too short to check" in p for p in anchor_schema.problems(a))


# ── mode A: the North Star, and the list it must not have ────────────────────
def test_valid_mode_a_anchor_passes():
    assert anchor_schema.problems(VALID_A) == []


def test_mode_a_requires_a_north_star():
    a = dict(VALID_A)
    a.pop("north_star")
    assert any("'north_star'" in p for p in anchor_schema.problems(a))


def test_mode_a_refuses_an_acceptance_list():
    """ORCHESTRATION-DESIGN 6.6 — the goal comes into focus by walking the path, so a fixed
    list written up front is a target that does not exist yet. gym-024 is the recorded cost
    of accepting one."""
    a = dict(VALID_A, acceptance=["the tool has a --version flag"])
    found = anchor_schema.problems(a)
    assert any("must not carry 'acceptance'" in p for p in found), found


def test_mode_a_tolerates_an_empty_acceptance_key():
    # An empty list is not someone asserting a fixed target; only a populated one is.
    assert anchor_schema.problems(dict(VALID_A, acceptance=[])) == []


def test_mode_b_still_demands_the_acceptance_mode_a_refuses():
    # The two modes genuinely differ; the schema is not just decorating one contract.
    b_missing = anchor_schema.problems({k: v for k, v in VALID_B.items() if k != "acceptance"})
    assert any("'acceptance'" in p for p in b_missing)


def test_an_unknown_mode_is_loud():
    """Defaulting an unknown mode to B would validate a generative anchor against a bounded
    contract and pass it for the wrong reasons."""
    found = anchor_schema.problems(dict(VALID_A, mode="Q"))
    assert len(found) == 1 and "unknown anchor mode 'Q'" in found[0]


def test_mode_is_case_insensitive():
    assert anchor_schema.problems(dict(VALID_A, mode="a")) == []


# ── the real anchors already in the queue must all still validate ────────────
def test_every_queued_anchor_still_validates():
    """These were written before the schema existed and none carries a `mode`. If any of
    them stops validating, this was a migration, not the extension it claims to be."""
    # --git-common-dir, NOT repo_root/.git: in a worktree .git is a FILE, so a naive path
    # makes this test silently SKIP in exactly the environment agents run it in.
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=HERE, capture_output=True, text=True, timeout=60,
        )
        assert common.returncode == 0, common.stderr
        git_dir = Path(common.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (HERE / git_dir).resolve()
    except (OSError, AssertionError) as exc:  # no git on PATH — say so, do not pass quietly
        pytest.skip(f"git unavailable: {exc}")
    state = git_dir / "agent-worktrees" / "queue"
    files = sorted(state.glob("*.anchor.json")) if state.is_dir() else []
    if not files:
        pytest.skip(f"no queued anchors under {state}")
    for f in files:
        anchor = json.loads(f.read_text(encoding="utf-8-sig"))
        assert anchor_schema.problems(anchor) == [], f.name


# ── THE ANTI-DRIFT TEST: both readers, same corpus, same problems ────────────
CORPUS = [
    ("valid-B", VALID_B),
    ("valid-A", VALID_A),
    ("empty", {}),
    ("B-missing-goal", {k: v for k, v in VALID_B.items() if k != "goal"}),
    ("B-missing-artifact", {k: v for k, v in VALID_B.items() if k != "artifact"}),
    ("B-missing-audience", {k: v for k, v in VALID_B.items() if k != "audience"}),
    ("B-empty-acceptance", dict(VALID_B, acceptance=[])),
    ("B-blank-acceptance-entry", dict(VALID_B, acceptance=["   "])),
    ("B-short-acceptance", dict(VALID_B, acceptance=["it works"])),
    ("B-missing-out-of-scope", {k: v for k, v in VALID_B.items() if k != "out_of_scope"}),
    ("A-missing-north-star", {k: v for k, v in VALID_A.items() if k != "north_star"}),
    ("A-with-acceptance", dict(VALID_A, acceptance=["the tool has a --version flag"])),
    ("A-empty-acceptance", dict(VALID_A, acceptance=[])),
    ("A-lowercase-mode", dict(VALID_A, mode="a")),
    ("unknown-mode", dict(VALID_A, mode="Q")),
]


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
@pytest.mark.parametrize("name,anchor", CORPUS, ids=[c[0] for c in CORPUS])
def test_powershell_and_python_readers_agree(name, anchor):
    """Same schema, same anchors, same answers — problem lists, not just verdicts."""
    assert sorted(_ps_problems(anchor)) == sorted(anchor_schema.problems(anchor)), name


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_the_agreement_test_would_catch_real_drift(tmp_path):
    """RED-first, kept in the suite: prove the comparison above can FAIL.

    A test that only ever passes would not detect drift, so here the schema is edited so
    that mode B no longer requires `artifact`. Both readers must follow the FILE — if
    either had kept a private copy of the field table, they would now disagree, and this
    test asserts they still match while both accepting the edited contract.
    """
    schema = json.loads((HERE / "anchor.schema.json").read_text(encoding="utf-8"))
    schema["modes"]["B"]["fields"]["artifact"]["required"] = False
    edited = tmp_path / "anchor.schema.json"
    edited.write_text(json.dumps(schema), encoding="utf-8")

    no_artifact = {k: v for k, v in VALID_B.items() if k != "artifact"}
    # Against the SHIPPED schema this is invalid...
    assert any("'artifact'" in p for p in anchor_schema.problems(no_artifact))
    # ...and against the edited one both readers accept it, together.
    edited_schema = anchor_schema.load(fresh=True, path=edited)
    assert anchor_schema.problems(no_artifact, edited_schema) == []
    assert _ps_problems(no_artifact, schema_path=edited) == []
    anchor_schema.load(fresh=True)  # restore the cache for later tests


# ── THE THIRD READER: agent-bridge, in its container ─────────────────────────
# The finding this closes (documentation/notes/anchor-schema-findings.md F1) is explicit
# that whichever delivery mechanism won, "the cross-reader test is what keeps it honest — it
# must be extended to ask the CONTAINERISED reader the same questions, or the copy will
# drift silently".
#
# There is no copy to drift: the bridge bind-mounts the same file. These tests exist anyway,
# because "there is no copy" is a claim about a compose file that somebody can edit.

import shutil
import subprocess

BRIDGE_IMAGE = "agent-bridge:local"


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", BRIDGE_IMAGE],
                           capture_output=True, text=True)
    return probe.returncode == 0


def _bridge_problems(anchor: dict) -> list:
    """Ask the reader INSIDE the bridge image, over the same mounts compose declares."""
    schema = str((HERE / "anchor.schema.json").resolve())
    reader = str((HERE / "anchor_schema.py").resolve())
    code = (
        "import sys,json;sys.path.insert(0,'/app/anchor');"
        "import anchor_schema as a;"
        "print(json.dumps(a.problems(json.load(sys.stdin))))"
    )
    out = subprocess.run(
        ["docker", "run", "--rm", "-i",
         "-v", f"{schema}:/app/anchor/anchor.schema.json:ro",
         "-v", f"{reader}:/app/anchor/anchor_schema.py:ro",
         BRIDGE_IMAGE, "python", "-c", code],
        input=json.dumps(anchor), capture_output=True, text=True,
    )
    assert out.returncode == 0, f"containerised reader failed: {out.stderr[-400:]}"
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not _docker_available(), reason="docker or agent-bridge:local unavailable")
def test_the_containerised_reader_sees_the_schema_at_all():
    """The delivery half. Before the mount, the bridge could not read the file at all.

    The finding said it was unreachable "at build time and at run time". The build-time half
    was right; a bind mount is a HOST path resolved at container start and has nothing to do
    with the build context, which is why no copy was needed.
    """
    assert _bridge_problems(VALID_B) == []


@pytest.mark.skipif(not _docker_available(), reason="docker or agent-bridge:local unavailable")
def test_all_THREE_readers_report_the_same_problems():
    """Not just the same verdict - the same PROBLEMS.

    Two readers that agree on "invalid" while disagreeing on WHY have already drifted; the
    existing two-reader test says so, and a third reader does not get a weaker bar.
    """
    for anchor in (VALID_B, {}, {"goal": "only a goal"}):
        py = anchor_schema.problems(anchor)
        ps = _ps_problems(anchor)
        bridge = _bridge_problems(anchor)
        assert py == ps == bridge, f"readers disagree on {anchor}: {py} / {ps} / {bridge}"


# ── EXECUTABLE ACCEPTANCE CRITERIA (U3) ──────────────────────────────────────
# §10's finding->check pipeline is the elevation path: a tester should RUN what can be
# run and judge only what cannot. These pin that BOTH readers agree about which is which,
# because a criterion one reader calls executable and the other calls prose is worse than
# either answer - the tester and the reviewer would be checking different things.

EXEC_B = {
    **VALID_B,
    "acceptance": [
        "a prose criterion long enough to be checkable by a human",
        {"check": "python -m pytest -q", "why": "the suite is green"},
    ],
}


def test_an_executable_criterion_is_valid_and_so_is_prose_beside_it():
    # Backward compatibility is the requirement, not a nicety: every anchor in the queue
    # today uses strings, and a schema change that invalidated them would strand them.
    assert anchor_schema.problems(EXEC_B) == []


def test_both_readers_agree_an_executable_criterion_is_valid():
    assert anchor_schema.problems(EXEC_B) == _ps_problems(EXEC_B) == []


def test_an_empty_check_is_refused_by_BOTH_readers():
    """A criterion that claims to be runnable and is not is worse than prose.

    A tester reading `check` will believe it ran.
    """
    bad = {**VALID_B, "acceptance": [{"check": "", "why": "something"}]}
    py, ps = anchor_schema.problems(bad), _ps_problems(bad)
    assert py and py == ps, f"readers disagree: {py} vs {ps}"


def test_a_check_without_a_why_is_refused_by_BOTH_readers():
    # A command whose purpose is unstated cannot be judged when it fails - the reader sees
    # a red command and no way to tell whether the criterion or the code is wrong.
    bad = {**VALID_B, "acceptance": [{"check": "pytest -q"}]}
    py, ps = anchor_schema.problems(bad), _ps_problems(bad)
    assert py and py == ps, f"readers disagree: {py} vs {ps}"


def test_the_length_rule_applies_to_prose_and_NOT_to_a_terse_why():
    # A `why` may be terse and still honest; prose has to carry the whole criterion.
    short_why = {**VALID_B, "acceptance": [{"check": "pytest -q", "why": "green"}]}
    assert anchor_schema.problems(short_why) == []
    short_prose = {**VALID_B, "acceptance": ["too short"]}
    assert anchor_schema.problems(short_prose) != []


def test_executable_and_prose_criteria_partition_the_list():
    ex = anchor_schema.executable_criteria(EXEC_B)
    pr = anchor_schema.prose_criteria(EXEC_B)
    assert [e["check"] for e in ex] == ["python -m pytest -q"]
    assert pr == ["a prose criterion long enough to be checkable by a human"]
    # Every entry is in exactly one bucket - a criterion in neither would be silently
    # untested, which is the failure this whole seam exists to prevent.
    assert len(ex) + len(pr) == len(EXEC_B["acceptance"])


def test_an_anchor_with_no_executable_criteria_yields_an_empty_list():
    assert anchor_schema.executable_criteria(VALID_B) == []
    assert anchor_schema.prose_criteria(VALID_B) == list(VALID_B["acceptance"])


def test_malformed_anchors_do_not_raise_the_extractors():
    for bad in (None, "a string", 7, {}, {"acceptance": None}):
        assert anchor_schema.executable_criteria(bad) == []
        assert anchor_schema.prose_criteria(bad) == []
