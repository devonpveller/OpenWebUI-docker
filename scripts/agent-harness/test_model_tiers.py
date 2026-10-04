"""Tests for the model-tier policy (item mt-policy, tracker H2) - and that both readers agree.

The policy lives in harness.config.json ``model_tiers``; config.ps1 ``Resolve-ModelTier`` and
config.py ``resolve_model_tier`` read it. queue.ps1's printed advice is proven end to end by
verify-model-tiers.ps1 (hermetic fixtures); this file pins the CONFIG and the two readers.

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
import config  # noqa: E402

PS = shutil.which("powershell") or shutil.which("pwsh")
ROLES = ("developer", "tester", "reviewer")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_STACK_HARNESS_CONFIG", "AI_STACK_HARNESS_ENABLED", "AI_STACK_HARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    config.load(fresh=True)
    yield
    config.load(fresh=True)


def _shipped() -> dict:
    return json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))


def _use(monkeypatch, tmp_path, cfg: dict) -> Path:
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    return p


# ── the shape of the shipped config ─────────────────────────────────────────
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


def test_the_documented_default_is_large():
    assert config.default_model_tier() == "large"
    assert config.resolve_model_tier("developer")["tier"] == "large"
    assert config.resolve_model_tier("developer", item_tier="")["item_tier"] == "large"


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


# ── the rules, as the operator stated them ──────────────────────────────────
# (role, item_tier, attempt, doc_only, delta_lines) -> (tier, rule)
RULE_CASES = [
    # whole-change review = large, whatever the item or its diff
    (("reviewer", "small", 1, True, None), ("large", "review-whole-change")),
    (("reviewer", "small", 3, False, 2), ("large", "review-whole-change")),
    # doc-only items = small (developer and tester)
    (("developer", "large", 1, True, None), ("small", "doc-only")),
    (("tester", "large", 1, True, None), ("small", "doc-only")),
    # attempt >= 2 confirmation re-test of a small diff = small
    (("tester", "large", 2, False, 3), ("small", "retest-small-diff")),
    (("tester", "large", 4, False, 80), ("small", "retest-small-diff")),
    # ...but not a big diff, and not when the delta is unknown
    (("tester", "large", 2, False, 81), ("large", "item-tier")),
    (("tester", "large", 2, False, None), ("large", "item-tier")),
    (("tester", "small", 2, False, 500), ("small", "item-tier")),
    # first adversarial test round of new logic = large, even on a small item
    (("tester", "small", 1, False, None), ("large", "first-adversarial-round")),
    (("tester", "small", 1, None, None), ("large", "first-adversarial-round")),
    # otherwise the item's tier (developer)
    (("developer", "small", 1, False, None), ("small", "item-tier")),
    (("developer", "", 1, None, None), ("large", "item-tier")),
    (("developer", "small", 2, None, None), ("small", "item-tier")),
]


@pytest.mark.parametrize("args,want", RULE_CASES, ids=[f"{a[0]}-{a[1] or 'none'}-a{a[2]}-doc{a[3]}-d{a[4]}" for a, _ in RULE_CASES])
def test_rules(args, want):
    role, tier, attempt, doc, delta = args
    got = config.resolve_model_tier(role, item_tier=tier, attempt=attempt, doc_only=doc,
                                    delta_lines=delta)
    assert (got["tier"], got["rule"]) == want
    mt = _shipped()["model_tiers"]
    assert got["cloud"] == mt["cloud"]["roles"][got["tier"]][role]
    assert got["local"] == mt["local"]["roles"][got["tier"]][role]


def test_an_unknown_item_tier_is_loud():
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_model_tier("developer", item_tier="medium")
    assert "unknown tier 'medium' - known tiers: large, small" in str(e.value)
    assert config.model_tier_problem("medium") == "unknown tier 'medium' - known tiers: large, small"
    assert config.model_tier_problem("small") == ""


def test_an_unknown_role_is_loud():
    with pytest.raises(config.HarnessConfigError):
        config.resolve_model_tier("worker")


def test_a_misspelt_rule_condition_is_refused_not_ignored(monkeypatch, tmp_path):
    """An ignored `max_attemp` would silently widen the rule to every attempt."""
    cfg = _shipped()
    cfg["model_tiers"]["rules"][3]["max_attemp"] = 1
    _use(monkeypatch, tmp_path, cfg)
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_model_tier("tester", attempt=1)
    assert "unknown key 'max_attemp'" in str(e.value)


def test_a_rule_list_without_a_catch_all_is_loud(monkeypatch, tmp_path):
    cfg = _shipped()
    cfg["model_tiers"]["rules"] = [r for r in cfg["model_tiers"]["rules"] if r["id"] != "item-tier"]
    _use(monkeypatch, tmp_path, cfg)
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_model_tier("developer")
    assert "no model_tiers rule matched" in str(e.value)


def test_a_missing_map_cell_is_loud(monkeypatch, tmp_path):
    cfg = _shipped()
    del cfg["model_tiers"]["local"]["roles"]["small"]["tester"]
    _use(monkeypatch, tmp_path, cfg)
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_model_tier("tester", item_tier="small", attempt=2, doc_only=False, delta_lines=1)
    assert "model_tiers.local.roles.small.tester is not set" in str(e.value)


def test_changing_one_map_never_moves_the_other(monkeypatch, tmp_path):
    cfg = _shipped()
    cfg["model_tiers"]["cloud"]["roles"]["small"]["developer"] = "opus"
    _use(monkeypatch, tmp_path, cfg)
    got = config.resolve_model_tier("developer", item_tier="small")
    assert (got["cloud"], got["local"]) == ("opus", "local-small")


# ── the anchor field ────────────────────────────────────────────────────────
VALID_B = {
    "goal": "Rework the coder plane README so it reads as an operator document.",
    "artifact": "coder/README.md - an operator-facing compose-plane README.",
    "audience": "Someone who has to OPERATE this plane, arriving with a task.",
    "acceptance": ["No section whose subject is disagreement between documents."],
    "out_of_scope": ["Rewriting the little-coder design doc."],
}


@pytest.mark.parametrize("tier", ["large", "small", "", "   "])
def test_a_valid_or_blank_tier_is_accepted(tier):
    assert anchor_schema.problems(dict(VALID_B, tier=tier)) == []


@pytest.mark.parametrize("tier", ["medium", "Large", "SMALL", "opus"])
def test_an_invalid_tier_is_refused_naming_the_allowed_values(tier):
    found = anchor_schema.problems(dict(VALID_B, tier=tier))
    assert len(found) == 1
    assert found[0].startswith(f"'tier' must be one of: large, small (got '{tier}')")


# ── THE ANTI-DRIFT TEST: both resolvers, same questions, same answers ───────
def _ps_resolve(cases) -> list:
    dot = (HERE / "config.ps1").as_posix()
    rows = []
    for role, tier, attempt, doc, delta in cases:
        d = "$null" if doc is None else ("$true" if doc else "$false")
        n = "$null" if delta is None else str(int(delta))
        rows.append(
            f"try {{ $a = Resolve-ModelTier -Role '{role}' -ItemTier '{tier}' -Attempt {attempt} "
            f"-DocOnly {d} -DeltaLines {n}; $out += ,@($a.tier, $a.rule, $a.cloud, $a.local) }} "
            f"catch {{ $out += ,@('ERROR', $_.Exception.Message) }}")
    script = f". '{dot}'; $out = @(); " + "; ".join(rows) + "; ConvertTo-Json -Depth 4 -Compress @(,$out)"
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    parsed = json.loads(out.stdout.strip())
    # ConvertTo-Json -Compress @(,$out) wraps once more on some hosts; unwrap to the row list.
    if len(parsed) == 1 and isinstance(parsed[0], list) and parsed and isinstance(parsed[0][0], list):
        parsed = parsed[0]
    return parsed


def _py_resolve(cases) -> list:
    out = []
    for role, tier, attempt, doc, delta in cases:
        try:
            a = config.resolve_model_tier(role, item_tier=tier, attempt=attempt, doc_only=doc,
                                          delta_lines=delta)
            out.append([a["tier"], a["rule"], a["cloud"], a["local"]])
        except config.HarnessConfigError as exc:
            out.append(["ERROR", str(exc)])
    return out


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_powershell_and_python_resolvers_agree():
    cases = [a for a, _ in RULE_CASES] + [("developer", "medium", 1, None, None)]
    assert _ps_resolve(cases) == _py_resolve(cases)
