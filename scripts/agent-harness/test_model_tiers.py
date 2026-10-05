"""Tests for the model-tier policy (item mt-policy, tracker H2; one resolver, item ef-one-resolver).

The policy lives in harness.config.json ``model_tiers`` and has ONE reader: config.ps1
``Resolve-ModelTier`` (the function queue.ps1 calls to print the advisory MODEL line). Every
rule below is asked of that function directly, in one batched PowerShell call per question set;
there is no Python twin to keep in agreement. queue.ps1's printed advice, and the -Propose
anchor-tier refusals (M3, M11, M12, M13, M18), are proven end to end by verify-model-tiers.ps1
(hermetic fixtures); this file pins the CONFIG and the resolver.

    python -m pytest scripts/agent-harness/test_model_tiers.py -q
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
ROLES = ("developer", "tester", "reviewer")

pytestmark = pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_STACK_HARNESS_CONFIG", "AI_STACK_HARNESS_ENABLED", "AI_STACK_HARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)


def _shipped() -> dict:
    return json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))


def _write_cfg(tmp_path, cfg: dict) -> Path:
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def _resolve_batch(cases, tmp_path, cfg: Path | None = None, want_problems: bool = False):
    """Ask config.ps1 Resolve-ModelTier every case in ONE powershell call.

    ``cases`` are (role, item_tier, attempt, doc_only, delta_lines); doc_only / delta_lines of
    None mean UNKNOWN. Returns one dict per case - {tier, rule, cloud, local, item_tier} or
    {err: <message>} - and, with ``want_problems``, (results, Get-ModelTiersProblems output).
    Inputs travel in a JSON file so no case is ever interpolated into a command line.
    """
    inp = tmp_path / "cases.json"
    outp = tmp_path / "ps_out.json"
    inp.write_text(json.dumps([{"r": r, "t": t, "a": a, "d": d, "dl": dl}
                               for r, t, a, d, dl in cases], ensure_ascii=True), encoding="ascii")
    env_line = (f"$env:AI_STACK_HARNESS_CONFIG='{cfg.as_posix()}';" if cfg else
                "Remove-Item Env:AI_STACK_HARNESS_CONFIG -ErrorAction SilentlyContinue;")
    script = (
        env_line + f". '{(HERE / 'config.ps1').as_posix()}';"
        + f"$cs = [IO.File]::ReadAllText('{inp.as_posix()}') | ConvertFrom-Json;"
        + "$res = New-Object System.Collections.ArrayList;"
        + "foreach ($c in $cs) { try {"
        + " $o = Resolve-ModelTier -Role $c.r -ItemTier $c.t -Attempt $c.a -DocOnly $c.d -DeltaLines $c.dl;"
        + " [void]$res.Add([ordered]@{tier=$o.tier; rule=$o.rule; cloud=$o.cloud; local=$o.local; item_tier=$o.item_tier})"
        + " } catch { [void]$res.Add([ordered]@{err=$_.Exception.Message}) } };"
        + "$doc = [ordered]@{results=@($res); problems=@(Get-ModelTiersProblems)};"
        + f"[IO.File]::WriteAllText('{outp.as_posix()}', ($doc | ConvertTo-Json -Depth 5 -Compress), (New-Object System.Text.UTF8Encoding($false)))"
    )
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr
    doc = json.loads(outp.read_text(encoding="utf-8-sig"))
    results = doc["results"]
    if isinstance(results, dict):   # a single case serialises as an object, not a list
        results = [results]
    assert len(results) == len(cases), (len(results), len(cases))
    problems = doc["problems"]
    if isinstance(problems, str):
        problems = [problems]
    return (results, problems) if want_problems else results


# -- the shape of the shipped config -------------------------------------------------------
def test_cloud_and_local_are_SEPARATE_complete_maps():
    """Operator 2026-10-04: cloud vs local selection stays two configurations."""
    mt = _shipped()["model_tiers"]
    assert set(mt["tiers"]) == {"large", "small"}
    for sub in ("cloud", "local"):
        roles = mt[sub]["roles"]
        assert set(roles) == set(mt["tiers"]), sub
        for t in mt["tiers"]:
            assert set(roles[t]) == set(ROLES), (sub, t)
            assert all(isinstance(v, str) and v for v in roles[t].values()), (sub, t)
    cloud_vals = {v for t in mt["tiers"] for v in mt["cloud"]["roles"][t].values()}
    local_vals = {v for t in mt["tiers"] for v in mt["local"]["roles"][t].values()}
    # Neither map is the other renamed: no model name serves both substrates.
    assert cloud_vals.isdisjoint(local_vals)
    # Cloud names are Claude Code CLI aliases the runner actually declares; local names are
    # agent-org's model roles.
    assert cloud_vals <= set(_shipped()["runners"]["claude-code"]["models"])
    assert local_vals == {"local-large", "local-small"}


def test_large_and_small_map_to_the_expected_models():
    mt = _shipped()["model_tiers"]
    assert set(mt["cloud"]["roles"]["large"].values()) == {"opus"}
    assert set(mt["cloud"]["roles"]["small"].values()) == {"sonnet"}
    assert set(mt["local"]["roles"]["large"].values()) == {"local-large"}
    assert set(mt["local"]["roles"]["small"].values()) == {"local-small"}


def test_haiku_is_trivial_only_never_a_pipeline_role():
    mt = _shipped()["model_tiers"]
    assert mt["cloud"]["trivial"] == "haiku"
    for t in mt["tiers"]:
        assert "haiku" not in mt["cloud"]["roles"][t].values()


def test_the_research_workbench_profiles_are_unchanged():
    """The contract: those profiles are preserved byte-for-value."""
    p = _shipped()["profiles"]
    assert {r: p["research-workbench"][r] for r in ("worker", "tester", "reviewer")} == {
        "worker": {"runner": "claude-code", "model": "sonnet"},
        "tester": {"runner": "claude-code", "model": "sonnet"},
        "reviewer": {"runner": "claude-code", "model": "opus"},
    }
    assert {r: p["research-workbench-elevated"][r] for r in ("worker", "tester", "reviewer")} == {
        "worker": {"runner": "claude-code", "model": "opus"},
        "tester": {"runner": "claude-code", "model": "opus"},
        "reviewer": {"runner": "claude-code", "model": "opus"},
    }


def test_the_anchor_schema_allows_exactly_the_configured_tiers():
    schema = json.loads((HERE / "anchor.schema.json").read_text(encoding="utf-8"))
    for mode in ("A", "B"):
        f = schema["modes"][mode]["fields"]["tier"]
        assert f["required"] is False
        assert f["allowed"] == _shipped()["model_tiers"]["tiers"], mode


# -- the rules, as the operator stated them - asked of Resolve-ModelTier --------------------
# (role, item_tier, attempt, doc_only, delta_lines) -> (tier, rule)
RULE_CASES = [
    # whole-change review = large, whatever the item or its diff
    (("reviewer", "small", 1, True, None), ("large", "review-whole-change")),
    (("reviewer", "small", 3, False, 2), ("large", "review-whole-change")),
    # doc-only items = small (developer and tester)
    (("developer", "large", 1, True, None), ("small", "doc-only")),
    (("tester", "large", 1, True, None), ("small", "doc-only")),
    # doc-only wins over the first-adversarial-round rule, and over a big retest diff
    (("tester", "large", 2, True, 500), ("small", "doc-only")),
    # a developer item that is NOT doc-only (False) never takes the doc-only rule
    (("developer", "large", 1, False, None), ("large", "item-tier")),
    # attempt >= 2 confirmation re-test of a small diff = small
    (("tester", "large", 2, False, 3), ("small", "retest-small-diff")),
    (("tester", "large", 2, False, 0), ("small", "retest-small-diff")),
    (("tester", "large", 4, False, 80), ("small", "retest-small-diff")),
    # ...but not a big diff (81), and not when the delta is unknown
    (("tester", "large", 2, False, 81), ("large", "item-tier")),
    (("tester", "large", 2, False, None), ("large", "item-tier")),
    (("tester", "small", 2, False, 500), ("small", "item-tier")),
    # the retest rule is the TESTER's: a developer with a small delta is not matched by it
    (("developer", "large", 2, False, 3), ("large", "item-tier")),
    # first adversarial test round of new logic = large, even on a small item
    (("tester", "small", 1, False, None), ("large", "first-adversarial-round")),
    (("tester", "small", 1, None, None), ("large", "first-adversarial-round")),
    # attempt 1 with a small delta: retest needs attempt >= 2, so the first round still wins
    (("tester", "small", 1, False, 3), ("large", "first-adversarial-round")),
    # unknown doc_only never matches the doc-only rule
    (("developer", "small", 1, None, None), ("small", "item-tier")),
    # otherwise the item's tier (developer)
    (("developer", "small", 1, False, None), ("small", "item-tier")),
    (("developer", "", 1, None, None), ("large", "item-tier")),
    (("developer", "small", 2, None, None), ("small", "item-tier")),
    (("developer", "large", 1, None, None), ("large", "item-tier")),
]
RULE_IDS = [f"{a[0]}-{a[1] or 'none'}-a{a[2]}-doc{a[3]}-d{a[4]}" for a, _ in RULE_CASES]


@pytest.fixture(scope="module")
def shipped_answers(tmp_path_factory):
    """One powershell call for every rule case; the tests below read their row."""
    return _resolve_batch([a for a, _ in RULE_CASES], tmp_path_factory.mktemp("rules"))


@pytest.mark.parametrize("n", range(len(RULE_CASES)), ids=RULE_IDS)
def test_rules(n, shipped_answers):
    (role, *_), want = RULE_CASES[n]
    got = shipped_answers[n]
    assert "err" not in got, got
    assert (got["tier"], got["rule"]) == want
    mt = _shipped()["model_tiers"]
    assert got["cloud"] == mt["cloud"]["roles"][got["tier"]][role]
    assert got["local"] == mt["local"]["roles"][got["tier"]][role]


def test_cloud_and_local_are_looked_up_in_their_own_maps(shipped_answers):
    """Cloud and local never read each other's map: opus|local-large vs sonnet|local-small."""
    by = dict(zip(RULE_IDS, shipped_answers))
    big = by["reviewer-small-a1-docTrue-dNone"]
    small = by["developer-large-a1-docTrue-dNone"]
    assert (big["cloud"], big["local"]) == ("opus", "local-large")
    assert (small["cloud"], small["local"]) == ("sonnet", "local-small")


def test_the_documented_default_is_large(tmp_path):
    got = _resolve_batch([("developer", "", 1, None, None)], tmp_path)[0]
    assert (got["tier"], got["item_tier"]) == ("large", "large")
    assert _shipped()["model_tiers"]["default_tier"] == "large"


def test_a_configured_default_tier_is_used_for_an_item_with_no_tier(tmp_path):
    cfg = _shipped()
    cfg["model_tiers"]["default_tier"] = "small"
    got = _resolve_batch([("developer", "", 1, None, None), ("developer", "large", 1, None, None)],
                         tmp_path, _write_cfg(tmp_path, cfg))
    assert (got[0]["item_tier"], got[0]["tier"]) == ("small", "small")
    assert (got[1]["item_tier"], got[1]["tier"]) == ("large", "large")   # an explicit tier wins


# Roles and tiers that are not exactly a canonical name must be REFUSED - no strip, no case
# folding, no Unicode normalisation (mt-policy attempts 1-3: -contains was case-insensitive,
# -ceq ignored U+00AD / U+200D / U+0000). One refusal per input, never a quiet answer.
SH, ZWJ, ZWSP, NBSP = "­", "‍", "​", " "
BAD_TIERS = ["medium", "Large", "SMALL", "LARGE", " small", "small ", "\tsmall", "small\n", "small\x1f",
             NBSP + "small", "small ", " ", "s" + SH + "mall", "sma" + ZWJ + "ll",
             "sm" + ZWSP + "all", "small\u0000", "ѕmall", "lаrge", "largé",
             "large" + SH, "1", "1.0", "item", "small" * 2000, "x" * 100000]
BAD_ROLES = ["worker", "Tester", "REVIEWER", "Developer", "test" + SH + "er", "revie" + ZWJ + "wer",
             " tester", "tester ", "developer\u0000", "1", "d" * 50000, ""]


def test_a_non_canonical_item_tier_is_refused_naming_the_known_tiers(tmp_path):
    cases = [("developer", t, 1, None, None) for t in BAD_TIERS]
    for got, t in zip(_resolve_batch(cases, tmp_path), BAD_TIERS):
        assert got.get("err", "").startswith(f"unknown tier '{t}' - known tiers: large, small"), (t[:40], got)


def test_a_non_canonical_role_is_refused_naming_the_known_roles(tmp_path):
    cases = [(r, "large", 1, None, None) for r in BAD_ROLES]
    for got, r in zip(_resolve_batch(cases, tmp_path), BAD_ROLES):
        assert got.get("err", "").startswith(
            f"unknown role '{r}' for a model tier - known roles: developer, tester, reviewer"), (r[:40], got)


def test_the_unknown_tier_message_is_the_one_queue_prints(tmp_path):
    got = _resolve_batch([("developer", "medium", 1, None, None)], tmp_path)[0]
    assert got == {"err": "unknown tier 'medium' - known tiers: large, small"}


def test_attempt_and_delta_boundaries_are_as_stated(tmp_path):
    """max_delta_lines 80 is inclusive; min_attempt 2 is inclusive; attempts below 1 are
    still 'at most attempt 1' for the first-round rule."""
    cases = [("tester", "large", a, False, d) for a, d in
             [(1, 80), (2, 80), (2, 81), (1, 0), (0, None), (-1, None), (2**31 - 1, 80), (2, 2**40)]]
    got = [(g["tier"], g["rule"]) for g in _resolve_batch(cases, tmp_path)]
    assert got == [("large", "first-adversarial-round"), ("small", "retest-small-diff"),
                   ("large", "item-tier"), ("large", "first-adversarial-round"),
                   ("large", "first-adversarial-round"), ("large", "first-adversarial-round"),
                   ("small", "retest-small-diff"), ("large", "item-tier")]


def test_a_misspelt_rule_condition_is_refused_not_ignored(tmp_path):
    """An ignored `max_attemp` would silently widen the rule to every attempt."""
    cfg = _shipped()
    cfg["model_tiers"]["rules"][3]["max_attemp"] = 1
    got = _resolve_batch([("tester", "large", 1, None, None)], tmp_path, _write_cfg(tmp_path, cfg))[0]
    assert "unknown key 'max_attemp'" in got["err"]


def test_a_rule_list_without_a_catch_all_is_loud(tmp_path):
    cfg = _shipped()
    cfg["model_tiers"]["rules"] = [r for r in cfg["model_tiers"]["rules"] if r["id"] != "item-tier"]
    got = _resolve_batch([("developer", "large", 1, None, None)], tmp_path, _write_cfg(tmp_path, cfg))[0]
    assert "no model_tiers rule matched" in got["err"]


def test_a_missing_map_cell_is_loud(tmp_path):
    cfg = _shipped()
    del cfg["model_tiers"]["local"]["roles"]["small"]["tester"]
    got = _resolve_batch([("tester", "small", 2, False, 1)], tmp_path, _write_cfg(tmp_path, cfg))[0]
    assert "model_tiers.local.roles.small.tester is not set" in got["err"]


def test_changing_one_map_never_moves_the_other(tmp_path):
    cfg = _shipped()
    cfg["model_tiers"]["cloud"]["roles"]["small"]["developer"] = "opus"
    got = _resolve_batch([("developer", "small", 1, None, None)], tmp_path, _write_cfg(tmp_path, cfg))[0]
    assert (got["cloud"], got["local"]) == ("opus", "local-small")


# -- the anchor field ----------------------------------------------------------------------
VALID_B = {
    "goal": "Rework the coder plane README so it reads as an operator document.",
    "artifact": "coder/README.md - an operator-facing compose-plane README.",
    "audience": "Someone who has to OPERATE this plane, arriving with a task.",
    "acceptance": ["No section whose subject is disagreement between documents."],
    "out_of_scope": ["Rewriting the little-coder design doc."],
}


@pytest.mark.parametrize("tier", ["large", "small", ""])
def test_a_valid_or_blank_tier_is_accepted(tier):
    assert anchor_schema.problems(dict(VALID_B, tier=tier)) == []


# attempt 3: no strip, no normalisation - padding, control and ignorable characters are refused.
@pytest.mark.parametrize("tier", ["medium", "Large", "SMALL", "opus", "   ", " small", "small\x1f",
                                  "\x1csmall", "sm­all", "sma‍ll", "small\u0000"])
def test_an_invalid_tier_is_refused_naming_the_allowed_values(tier):
    found = anchor_schema.problems(dict(VALID_B, tier=tier))
    assert len(found) == 1
    assert found[0].startswith(f"'tier' must be one of: large, small (got '{tier}')")


@pytest.mark.parametrize("tier,kind", [(["small"], "array"), ([], "array"), (1, "number"),
                                       (1.5, "number"), (True, "boolean"),
                                       ({"tier": "small"}, "object")])
def test_a_non_string_tier_is_refused_by_its_json_type(tier, kind):
    """Attempt 2 (tester F2). The cross-reader corpus in test_anchor_schema.py asks PowerShell the
    same; this pins the words."""
    found = anchor_schema.problems(dict(VALID_B, tier=tier))
    assert len(found) == 1
    assert found[0].startswith(
        f"'tier' must be a string, one of: large, small (got JSON type {kind})")


# The -Propose anchor-tier check itself lives in queue.ps1 Assert-AnchorTier (it calls the same
# Test-ModelTierName the resolver does, plus the anchor schema above). It is proven end to end,
# on a scratch queue, by verify-model-tiers.ps1 M3 / M11 / M12 / M13 / M18; this runs that script
# so a pytest run covers it too.
@pytest.mark.skipif(shutil.which("git") is None, reason="no git on PATH")
def test_the_propose_anchor_tier_check_passes_its_hermetic_drill(tmp_path):
    out = subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-File", str(HERE / "verify-model-tiers.ps1"),
         "-Root", str(tmp_path / "mt-drill")],
        capture_output=True, text=True, timeout=1500)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-1000:]
    for case in ("M3", "M11", "M12", "M13", "M18"):
        assert f"[PASS] {case} " in out.stdout, case
    assert "[FAIL]" not in out.stdout


# -- config defects: LOUD refusals from Resolve-ModelTier on EVERY input ------------------
# Each planted defect must (a) appear, in these words, in Get-ModelTiersProblems and (b) make
# Resolve-ModelTier throw for every canonical input - validated at load, not only on the path a
# rule match happens to take (the REVIEWER row would match rule 1 before a late bad rule).
PLANTED = [
    ("rule-tier-wrong-case", lambda c: c["model_tiers"]["rules"][1].update(tier="Small"),
     "model_tiers rule 'doc-only' names unknown tier 'Small' - known tiers: large, small (or item)"),
    ("rule-role-wrong-case", lambda c: c["model_tiers"]["rules"][0].update(role="Reviewer"),
     "model_tiers rule 'review-whole-change' names unknown role 'Reviewer' - known roles: developer, tester, reviewer"),
    ("fractional-min-attempt", lambda c: c["model_tiers"]["rules"][2].update(min_attempt=1.5),
     "model_tiers rule 'retest-small-diff' key 'min_attempt' must be a whole number from 0 to 2147483647 (got a number)"),
    # KEY case at every level, ignorable characters in VALUES, and an out-of-range bound.
    ("key-Cloud", lambda c: c["model_tiers"].__setitem__("Cloud", c["model_tiers"].pop("cloud")),
     "model_tiers has an unknown key 'Cloud' - keys: tiers, default_tier, doc_only_patterns, rules, cloud, local"),
    ("key-Rules", lambda c: c["model_tiers"].__setitem__("Rules", c["model_tiers"].pop("rules")),
     "model_tiers has an unknown key 'Rules'"),
    ("key-local-Roles", lambda c: c["model_tiers"]["local"].__setitem__("Roles", c["model_tiers"]["local"].pop("roles")),
     "model_tiers.local has an unknown key 'Roles' - keys: substrate, roles, trivial"),
    ("key-roles-Large", lambda c: c["model_tiers"]["cloud"]["roles"].__setitem__("Large", c["model_tiers"]["cloud"]["roles"].pop("large")),
     "model_tiers.cloud.roles has an unknown key 'Large' - keys: large, small"),
    ("key-role-map-Tester", lambda c: c["model_tiers"]["local"]["roles"]["small"].__setitem__("Tester", c["model_tiers"]["local"]["roles"]["small"].pop("tester")),
     "model_tiers.local.roles.small has an unknown key 'Tester' - keys: developer, tester, reviewer"),
    ("key-rule-Role", lambda c: c["model_tiers"]["rules"][2].__setitem__("Role", c["model_tiers"]["rules"][2].pop("role")),
     "model_tiers rule 'retest-small-diff' has an unknown key 'Role'"),
    ("rule-tier-softhyphen", lambda c: c["model_tiers"]["rules"][1].update(tier="sm­all"),
     "model_tiers rule 'doc-only' names unknown tier 'sm­all'"),
    ("rule-role-softhyphen", lambda c: c["model_tiers"]["rules"][2].update(role="test­er"),
     "model_tiers rule 'retest-small-diff' names unknown role 'test­er'"),
    ("default-tier-softhyphen", lambda c: c["model_tiers"].update(default_tier="large­"),
     "model_tiers.default_tier 'large­' is not one of the tiers: large, small"),
    ("tiers-entry-nul", lambda c: c["model_tiers"].update(tiers=["large", "small\u0000"]),
     "model_tiers.tiers entry 'small\u0000' is not a name"),
    ("min-attempt-bigint", lambda c: c["model_tiers"]["rules"][2].update(min_attempt=99999999999999999999),
     "model_tiers rule 'retest-small-diff' key 'min_attempt' must be a whole number from 0 to 2147483647"),
    ("max-delta-negative", lambda c: c["model_tiers"]["rules"][2].update(max_delta_lines=-1),
     "model_tiers rule 'retest-small-diff' key 'max_delta_lines' must be a whole number from 0 to 2147483647"),
    ("bad-key-in-a-late-rule", lambda c: c["model_tiers"]["rules"][3].update(max_attemp=1),
     "model_tiers rule 'first-adversarial-round' has an unknown key 'max_attemp'"),
    ("doc-only-not-boolean", lambda c: c["model_tiers"]["rules"][1].update(doc_only="yes"),
     "model_tiers rule 'doc-only' key 'doc_only' must be true or false (got a string)"),
    ("tier-not-a-string", lambda c: c["model_tiers"]["rules"][4].update(tier=["item"]),
     "model_tiers rule 'item-tier' tier must be a tier name (got a array)"),
    ("default-tier-wrong-case", lambda c: c["model_tiers"].update(default_tier="Large"),
     "model_tiers.default_tier 'Large' is not one of the tiers: large, small"),
    ("block-missing", lambda c: c.pop("model_tiers"),
     "harness.config.json has no model_tiers block"),
]

CANONICAL = [(r, t, a, d, dl) for r in ROLES for t in ("", "large", "small") for a in (1, 2)
             for d, dl in ((None, None), (True, None), (False, 5))]


@pytest.mark.parametrize("name,mutate,msg", PLANTED, ids=[p[0] for p in PLANTED])
def test_a_planted_config_defect_is_loud_on_every_input(tmp_path, name, mutate, msg):
    cfg = _shipped()
    mutate(cfg)
    results, problems = _resolve_batch(CANONICAL, tmp_path, _write_cfg(tmp_path, cfg),
                                       want_problems=True)
    assert any(p.startswith(msg) for p in problems), problems
    # No canonical input slips through, the reviewer row (rule 1 would match first) included.
    assert all("err" in r for r in results), [c for c, r in zip(CANONICAL, results) if "err" not in r]
    # the throw is the block's FIRST problem, whatever the input
    assert {r["err"] for r in results} == {problems[0]}
