"""Python reader for the agent-harness configuration.

The twin of ``config.ps1``. Same file, same layers, same precedence — so a value is
defined once and both the PowerShell scripts and the Mattermost bridge agree about it.
If you change the semantics here, change them there; ``test_harness_config.py`` pins the
two together on the parts that matter.

Layers, lowest to highest::

    built-in DEFAULTS < harness.config.json < harness.local.json (gitignored) < environment

Environment overrides are a short explicit list (see ``_env_overrides``), not a generic
"any key by env var" scheme — a scheme nobody can enumerate is a scheme where a typo
silently does nothing.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent

# Mirrors $script:Defaults in config.ps1. Present so a missing or deleted config file
# degrades to "the documented default", not to a crash. The FILE wins where both speak.
DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "default_profile": "all-cloud",
    "surfaces": {
        "extension": {"enabled": True, "profile": "all-cloud", "profile_locked": True},
        "mattermost": {"enabled": True, "profile": "all-cloud", "profile_locked": False},
    },
    "runners": {
        "claude-code": {"kind": "claude-code", "status": "proven", "default_model": "opus"},
    },
    "profiles": {
        "all-cloud": {
            "worker": {"runner": "claude-code", "model": "opus"},
            "tester": {"runner": "claude-code", "model": "opus"},
            "reviewer": {"runner": "claude-code", "model": "opus"},
        },
    },
    "gate_profiles": {
        "attended": {"anchor": "human", "pre_review": "human"},
        "dark": {"anchor": "auto", "pre_review": "auto"},
    },
    "pipeline": {
        "claim_ttl_minutes": 60,
        "anchor_required": True,
        "gate_profile": "attended",
    },
    "worktree": {
        "root": ".claude/worktrees",
        "dir_prefix": "wt-",
        "branch_prefix": "work/",
        "work_line_env": "AI_STACK_WORK_LINE",
        "work_line_fallback": "development",
        "state_dir_env": "AI_STACK_WORKTREE_STATE",
        "state_dir_name": "agent-worktrees",
        "env_files": [".env", ".env.test", "OB1/docker/.env"],
        "test_image_tag_prefix": "wt-",
    },
    "leases": {"names_file": "lease-names.conf", "default_ttl_minutes": 30},
    # The docker label reap.ps1 reads to know who created a test container or network.
    # Which KINDS are reapable is not a setting - see the note in harness.config.json.
    "reap": {"owner_label": "ai-stack.harness.owner"},
}

ROLES = ("worker", "tester", "reviewer")

#: The pipeline gates, in the order a work item crosses them. Declared once, here, so the
#: two readers and the audit verifier cannot disagree about how many there are.
GATES = ("anchor", "pre_review")

#: Reserved principal namespace for a gate NOBODY looked at. A human ``-By`` value may
#: never start with this, and an auto record may never omit it - that is what makes an
#: auto-pass distinguishable from a human approval when the operator reads the ledger
#: afterwards. A record saying only "passed" reads as approval and is worse than none.
AUTO_PRINCIPAL_PREFIX = "auto:"

#: The andon conditions the system REQUIRES, declared here in code and deliberately not in
#: ``harness.config.json``. The config says which conditions are configured and with what
#: parameters; this says which ones must EXIST.
#:
#: The defect that produced it (2026-08-30): the board could be switched off two ways and
#: both were closed. They report DIFFERENT states, and this comment claimed otherwise until
#: 2026-08-30 — the same false sentence as ``andon.ps1`` and ``config.ps1`` carried, all three
#: written by the commit that made it false. The mapping is stated once, in README.md's
#: ways-off table, and cited here by route id:
#:
#:   andon-disabled      -> not-evaluated
#:   andon-block-deleted -> incomplete
#:
#: Both halt. There was a THIRD, the one actually reached for: deleting condition ENTRIES
#: from ``andon.conditions`` (route ``conditions-deleted``). Thinned to one of five on a
#: genuinely detached checkout, the dark gate AUTO-PASSED — exit 0, ledger ``clear``,
#: coverage ``1 declared / 1 evaluated / 0 switched off``, ``-VerifyAudit COMPLETE``.
#:
#: A required-set living in the same file as the conditions would be no guard: whoever
#: deletes the entry deletes the name beside it and the file agrees with itself. Here,
#: retiring a condition is a CODE edit that shows in a diff. Mirrors
#: ``$script:RequiredAndonConditions`` in ``config.ps1``; ``test_gate_profiles.py`` asks
#: both readers and the shipped config the same question. No environment override exists —
#: a variable that thins the board is the same hole with a longer name.
#:
#: THE VALUE beside each id is the predicate that id is SUPPOSED to run, and it pins the
#: COMMITTED config only. ``test_gate_profiles.py`` compares this map against
#: ``harness.config.json``, so an entry that keeps a required id while naming a different
#: predicate — id squatting, invisible to the id-set check, which compares ids — fails the
#: suite. ``andon.ps1`` reads only the keys and does NOT re-check the predicate at run time:
#: a swap in an uncommitted config, or in one named by ``AI_STACK_HARNESS_CONFIG``, still
#: runs whatever the entry says. That route is open, and is named as open in README.md and
#: MODULE.md rather than papered over here.
REQUIRED_ANDON_CONDITIONS = {
    "operator-checkout-off-branch": "git-checkout-state",
    "policy-declared-unread": "config-key-unread",
    "git-error-swallowed": "git-error-unchecked",
    "work-branch-on-remote": "branch-on-remote",
    "protected-ref-moved": "protected-ref-moved",
}

#: The only words an andon condition may use for ``on_fire`` / ``on_indeterminate``. An
#: action the board does not understand cannot be honoured, and guessing at one is how a
#: config ends up deciding something nobody wrote down, so an unknown literal is refused.
#:
#: ``warn`` does not mean "carry on": a fired condition is never a clear board whatever its
#: action says, so no unattended gate passes over one either way. ``warn`` buys the WORD
#: (``warned`` rather than ``raised``) and the ledger's separate ``fired``/``halted`` lists
#: — severity for a human reading afterwards, not permission for a machine at the time.
ALLOWED_ANDON_ACTIONS = ("halt", "warn")

#: THE OUTCOME TABLE — mirror of ``$script:AndonBuckets`` in ``config.ps1``. Every
#: ``(status, action)`` pair the board knows how to think about, and the bucket it counts as.
#:
#: WHY IT EXISTS (2026-08-30): the verdict used to be computed BY EXCEPTION — a halt flag
#: set only for ``action == "halt"``, a fired list only for ``status == "fire"``, every other
#: outcome setting NOTHING, and ``clear`` as whatever was left when nothing objected. So an
#: outcome nobody had enumerated silently meant "fine", which cost two rounds: ``on_fire:
#: warn`` was closed and ``on_indeterminate: warn`` reopened the identical hole on the
#: sibling key, auto-passing a dark gate on a condition that could not be evaluated. ``clear``
#: is now PROVEN: every result lands in exactly one bucket, the buckets must sum to the
#: conditions in scope, and every bucket but ``evaluated_ok`` must be empty. A pair that is
#: not a key here — a new status, a new action word — falls to ``unrecognised``, which
#: refuses, with no branch naming the new word.
ANDON_BUCKETS = {
    ("ok", "none"): "evaluated_ok",
    ("fire", "halt"): "fired",
    ("fire", "warn"): "fired",
    ("indeterminate", "halt"): "indeterminate",
    ("indeterminate", "warn"): "indeterminate",
    ("disabled", "none"): "disabled",
}

#: The one bucket compatible with an unattended pass, and the bucket everything unenumerated
#: falls into. Mirrors ``$script:AndonClearBucket`` / ``$script:AndonUnrecognisedBucket``.
ANDON_CLEAR_BUCKET = "evaluated_ok"
ANDON_UNRECOGNISED_BUCKET = "unrecognised"

#: Bucket → the board's headline word, in SEVERITY ORDER. Also the declared bucket set: a
#: bucket absent from here is not a bucket, and a result classified into one is
#: ``unaccounted``. Mirrors ``$script:AndonBucketBoard``.
ANDON_BUCKET_BOARD = {
    "unrecognised": "unaccounted",
    "fired": "warned",
    "indeterminate": "indeterminate",
    "disabled": "partial",
    "evaluated_ok": "clear",
}


def andon_bucket(status: str, action: str) -> str:
    """Which census bucket a ``(status, action)`` outcome counts as.

    Mirrors ``Get-AndonBucket`` in ``config.ps1``. Anything unenumerated is
    ``unrecognised`` — a REFUSING bucket — rather than falling through to a pass.
    """
    return ANDON_BUCKETS.get((status, action), ANDON_UNRECOGNISED_BUCKET)


def missing_andon_conditions() -> List[str]:
    """Required condition ids that the loaded config does not declare, in required order.

    The board's own :func:`Invoke-AndonEvaluation` computes the same set; this is here so
    the bridge and the tests can ask without shelling out to PowerShell.
    """
    declared = {
        str(c.get("id", ""))
        for c in (get("andon.conditions") or [])
        if isinstance(c, dict)
    }
    return [c for c in REQUIRED_ANDON_CONDITIONS if c not in declared]


def andon_predicate_mismatches() -> List[Tuple[str, str, str]]:
    """``(id, expected, declared)`` for each required condition wired to the wrong predicate.

    The id-set check above compares ids, so an entry that KEEPS a required id while naming a
    different predicate satisfies it completely — the board still declares five ids and
    still evaluates five conditions, one of which is now a different check. This asks the
    other half of the question, of the config as loaded.

    Scope, stated because it is narrow: this is what ``test_gate_profiles.py`` runs against
    the committed ``harness.config.json``. Nothing calls it at a gate, so it does not make a
    run-time swap detectable.
    """
    out: List[Tuple[str, str, str]] = []
    for cond in (get("andon.conditions") or []):
        if not isinstance(cond, dict):
            continue
        cid = str(cond.get("id", ""))
        expected = REQUIRED_ANDON_CONDITIONS.get(cid)
        if expected is None:
            continue
        declared = str(cond.get("predicate", ""))
        if declared != expected:
            out.append((cid, expected, declared))
    return out

_CACHE: Dict[str, Any] | None = None


class HarnessConfigError(ValueError):
    """A configuration problem the operator has to see, not one to paper over."""


def _merge(base: Any, overlay: Any) -> Any:
    """Deep-merge maps; anything else replaces.

    Lists replace rather than extend — an operator who narrows ``worktree.env_files`` to
    one file must get one file, not the defaults plus theirs.
    """
    if overlay is None:
        return base
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    out = dict(base)
    for k, v in overlay.items():
        out[k] = _merge(out[k], v) if k in out else v
    return out


def _read_json(path: Path) -> Dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HarnessConfigError(f"harness config '{path}' is not valid JSON: {exc}") from exc


def _env_overrides() -> Dict[str, Any]:
    """The complete list of environment overrides.

    AI_STACK_HARNESS_CONFIG   path to an alternate harness.config.json
    AI_STACK_HARNESS_ENABLED  0/1 — kill switch, beats both files
    AI_STACK_HARNESS_PROFILE  profile name applied to every surface
    """
    out: Dict[str, Any] = {}
    enabled = os.environ.get("AI_STACK_HARNESS_ENABLED")
    if enabled:
        out["enabled"] = enabled.lower() not in ("0", "false", "no", "off")
    profile = os.environ.get("AI_STACK_HARNESS_PROFILE")
    if profile:
        out["default_profile"] = profile
    return out


def load(fresh: bool = False) -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not fresh:
        return _CACHE
    cfg_path = Path(os.environ.get("AI_STACK_HARNESS_CONFIG") or (HERE / "harness.config.json"))
    merged: Dict[str, Any] = copy.deepcopy(DEFAULTS)
    merged = _merge(merged, _read_json(cfg_path))
    merged = _merge(merged, _read_json(HERE / "harness.local.json"))
    merged = _merge(merged, _env_overrides())
    _CACHE = merged
    return merged


def get(path: str, default: Any = None) -> Any:
    """One accessor, dotted path: ``get("worktree.root")``."""
    node: Any = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def disabled_reason(surface: str = "") -> str:
    """"" when the harness may run, else a sentence saying why it may not.

    Callers decide what to do with it. "Off" has to be a stated reason, not an obscure
    failure three calls deeper.
    """
    if not get("enabled", True):
        return ("the agent harness is disabled (enabled=false in harness.config.json, "
                "or AI_STACK_HARNESS_ENABLED=0)")
    if surface:
        s = get(f"surfaces.{surface}")
        if isinstance(s, dict) and s.get("enabled") is False:
            return (f"the agent harness is disabled for the '{surface}' surface "
                    f"(surfaces.{surface}.enabled=false)")
    return ""


def is_enabled(surface: str = "") -> bool:
    return disabled_reason(surface) == ""


def is_profile_locked(surface: str) -> bool:
    s = get(f"surfaces.{surface}")
    return bool(isinstance(s, dict) and s.get("profile_locked"))


def profile_names() -> List[str]:
    profiles = get("profiles") or {}
    return [k for k in profiles if not k.startswith("_")]


def runner_names() -> List[str]:
    runners = get("runners") or {}
    return [k for k in runners if not k.startswith("_")]


def runner(name: str) -> Dict[str, Any]:
    """The full runner record — kind, status, and (for a runner something has to CALL) its
    transport topology.

    `resolve_role` deliberately returns only the policy answer; a dispatcher needs the
    topology too, and reading it here keeps that knowledge in the config file instead of
    hardcoded in the dispatcher. Mirrors `Get-HarnessRunner` in config.ps1.
    """
    runners = get("runners") or {}
    if name not in runners:
        raise HarnessConfigError(
            f"unknown runner '{name}' - known runners: {', '.join(runner_names())}")
    return runners[name]


def profile_name(surface: str = "", requested: str = "") -> str:
    """Which profile applies on a surface.

    A LOCKED surface ignores every request, including the environment override — that is
    what locked means (extension sessions, operator decision 2026-08-28).
    """
    s = get(f"surfaces.{surface}") if surface else None
    if isinstance(s, dict) and s.get("profile_locked") and s.get("profile"):
        return str(s["profile"])
    if requested:
        return requested
    if isinstance(s, dict) and s.get("profile"):
        return str(s["profile"])
    return str(get("default_profile", "all-cloud"))


def resolve_role(role: str, profile: str = "", surface: str = "") -> Dict[str, str]:
    """role + profile -> the runner and model that role executes on.

    Raises on an unknown profile or role rather than quietly falling back: a typo in a
    ``profile:`` directive must be visible, not silently served by the default.
    """
    if role not in ROLES:
        raise HarnessConfigError(f"unknown role '{role}' - known roles: {', '.join(ROLES)}")
    name = profile_name(surface, profile)
    profiles = get("profiles") or {}
    if name not in profiles:
        raise HarnessConfigError(
            f"unknown harness profile '{name}' - known profiles: {', '.join(profile_names())}")
    assigned = profiles[name]
    if role not in assigned:
        raise HarnessConfigError(f"profile '{name}' does not assign the '{role}' role")
    target = assigned[role]
    runner_name = target.get("runner", "")
    runners = get("runners") or {}
    if runner_name not in runners:
        raise HarnessConfigError(
            f"profile '{name}' assigns '{role}' to runner '{runner_name}', "
            f"which is not defined under runners")
    runner = runners[runner_name]
    return {
        "role": role,
        "profile": name,
        "runner": runner_name,
        "kind": runner.get("kind", runner_name),
        "model": target.get("model") or runner.get("default_model", ""),
        "status": runner.get("status", "unknown"),
    }


def gate_profile_name(requested: str = "") -> str:
    """Which gate profile is in force. Explicit request beats the configured default."""
    return requested or str(get("pipeline.gate_profile", "attended"))


def resolve_gate(gate: str, profile: str = "") -> Dict[str, str]:
    """gate + gate profile -> who passes it.

    Raises rather than defaulting, for the same reason ``resolve_role`` does: a typo in a
    gate profile name must be visible. Silently serving ``attended`` would be safe here and
    silently serving ``dark`` would not, and a rule that depends on which way the typo fell
    is not a rule.
    """
    if gate not in GATES:
        raise HarnessConfigError(f"unknown gate '{gate}' - known gates: {', '.join(GATES)}")
    name = gate_profile_name(profile)
    profiles = get("gate_profiles") or {}
    if name not in profiles:
        known = ", ".join(k for k in profiles if not k.startswith("_"))
        raise HarnessConfigError(f"unknown gate profile '{name}' - known gate profiles: {known}")
    assigned = profiles[name]
    if gate not in assigned:
        raise HarnessConfigError(f"gate profile '{name}' does not assign the '{gate}' gate")
    passer = str(assigned[gate])
    if passer not in ("human", "auto"):
        raise HarnessConfigError(
            f"gate profile '{name}' assigns '{gate}' to '{passer}' - only 'human' or 'auto'")
    return {"gate": gate, "profile": name, "passer": passer}


def gate_profile_names() -> List[str]:
    return [k for k in (get("gate_profiles") or {}) if not k.startswith("_")]


def describe_profile(name: str) -> str:
    """One line per profile for an operator listing in chat."""
    profiles = get("profiles") or {}
    p = profiles.get(name)
    if not p:
        return f"{name}: (unknown)"
    parts = []
    for role in ROLES:
        t = p.get(role)
        if isinstance(t, dict):
            parts.append(f"{role}={t.get('runner')}/{t.get('model')}")
    desc = p.get("_desc", "")
    return f"{name}: " + ", ".join(parts) + (f" - {desc}" if desc else "")


def describe_runner(name: str) -> str:
    """One line per runner for an operator listing in chat.

    The runner half of describe_profile: names the substrate (default model), how the
    harness reaches it (transport, when it has one), and its status — so an operator
    switching a thread to a local profile sees the caveat at the point of choice.
    """
    runners = get("runners") or {}
    r = runners.get(name)
    if not isinstance(r, dict):
        return f"{name}: (unknown)"
    parts = []
    if r.get("default_model"):
        parts.append(f"model={r['default_model']}")
    if r.get("transport"):
        parts.append(f"transport={r['transport']}")
    if r.get("status"):
        parts.append(f"status={r['status']}")
    return f"{name}: " + ", ".join(parts)


# --- MODEL TIERS (tracker H2, mt-policy) ----------------------------------------------
# Mirror of the model-tier block in config.ps1 (Get-ModelTiersProblems / Resolve-ModelTier).
# The item's tier plus the ordered rules in ``model_tiers`` decide a tier; the model comes from
# one of TWO SEPARATE maps, ``cloud`` (Claude Code subagents) and ``local`` (agent-org model
# roles). Advisory: the queue prints it and never blocks on it. Case-sensitive, the whole block
# validated before any rule is evaluated, fractional numbers refused - see config.ps1 for why.
# test_model_tiers.py pins the two readers on canonical AND non-canonical inputs.

#: The closed set of rule keys. A rule with any other (non ``_``) key is refused: an ignored
#: condition is a misspelt ``max_attemp`` that silently widens the rule to every attempt.
MODEL_TIER_RULE_KEYS = ("id", "why", "tier", "role", "min_attempt", "max_attempt",
                        "doc_only", "max_delta_lines")
MODEL_TIER_INT_KEYS = ("min_attempt", "max_attempt", "max_delta_lines")
MODEL_TIER_ROLES = ("developer", "tester", "reviewer")
MODEL_TIER_SUBSTRATES = ("cloud", "local")
MODEL_TIER_FALLBACK_DEFAULT = "large"


def _json_kind(v: Any) -> str:
    """The JSON type name of a json.loads value - config.ps1 Get-JsonKindName's twin."""
    if v is None:
        return "null"
    if isinstance(v, str):
        return "string"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, (int, float)):
        return "number"
    return "object"


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def model_tier_names() -> List[str]:
    mt = get("model_tiers")
    if not isinstance(mt, dict) or not isinstance(mt.get("tiers"), list):
        return []
    return [t for t in mt["tiers"] if isinstance(t, str)]


def model_tiers_problems() -> List[str]:
    """Everything wrong with the model_tiers block, in config.ps1's order and words."""
    mt = get("model_tiers")
    if mt is None:
        return ["harness.config.json has no model_tiers block"]
    if not isinstance(mt, dict):
        return ["model_tiers must be an object"]
    p: List[str] = []
    tiers: List[str] = []
    raw = mt.get("tiers")
    if not isinstance(raw, list) or not raw:
        p.append("model_tiers.tiers must be a non-empty list of tier names")
    else:
        for t in raw:
            if not isinstance(t, str) or not t:
                p.append(f"model_tiers.tiers holds a {_json_kind(t)}, not a tier name")
                continue
            tiers.append(t)
    if "default_tier" in mt:
        d = mt["default_tier"]
        if not isinstance(d, str):
            p.append(f"model_tiers.default_tier must be a tier name (got a {_json_kind(d)})")
        elif d not in tiers:
            p.append(f"model_tiers.default_tier '{d}' is not one of the tiers: {', '.join(tiers)}")
    rules = mt.get("rules")
    if not isinstance(rules, list):
        p.append("model_tiers.rules must be a list")
    else:
        for n, r in enumerate(rules, 1):
            if not isinstance(r, dict):
                p.append(f"model_tiers rule #{n} is a {_json_kind(r)}, not an object")
                continue
            rid = r["id"] if isinstance(r.get("id"), str) and r.get("id") else f"#{n}"
            for k in r:
                if k.startswith("_"):
                    continue
                if k not in MODEL_TIER_RULE_KEYS:
                    p.append(f"model_tiers rule '{rid}' has an unknown key '{k}' - rule keys: "
                             f"{', '.join(MODEL_TIER_RULE_KEYS)}")
            tv = r.get("tier")
            if not isinstance(tv, str):
                p.append(f"model_tiers rule '{rid}' tier must be a tier name (got a {_json_kind(tv)})")
            elif not (tv == "item" or tv in tiers):
                p.append(f"model_tiers rule '{rid}' names unknown tier '{tv}' - known tiers: "
                         f"{', '.join(tiers)} (or item)")
            if "role" in r:
                rv = r["role"]
                if not isinstance(rv, str):
                    p.append(f"model_tiers rule '{rid}' role must be a role name (got a {_json_kind(rv)})")
                elif rv not in MODEL_TIER_ROLES:
                    p.append(f"model_tiers rule '{rid}' names unknown role '{rv}' - known roles: "
                             f"{', '.join(MODEL_TIER_ROLES)}")
            for k in MODEL_TIER_INT_KEYS:
                if k in r and not _whole(r[k]):
                    p.append(f"model_tiers rule '{rid}' key '{k}' must be a whole number "
                             f"(got a {_json_kind(r[k])})")
            if "doc_only" in r and not isinstance(r["doc_only"], bool):
                p.append(f"model_tiers rule '{rid}' key 'doc_only' must be true or false "
                         f"(got a {_json_kind(r['doc_only'])})")
    for sub in MODEL_TIER_SUBSTRATES:
        for t in tiers:
            for role in MODEL_TIER_ROLES:
                node: Any = mt.get(sub)
                for part in ("roles", t, role):
                    node = node.get(part) if isinstance(node, dict) else None
                if not isinstance(node, str) or not node:
                    p.append(f"model_tiers.{sub}.roles.{t}.{role} is not set")
    return p


def default_model_tier() -> str:
    """The documented default for an item that names no tier (never smaller than large)."""
    d = get("model_tiers.default_tier")
    return d if isinstance(d, str) and d else MODEL_TIER_FALLBACK_DEFAULT


def model_tier_problem(tier: str) -> str:
    """"" when ``tier`` is configured (case-sensitive), else the sentence a caller prints."""
    known = model_tier_names()
    if tier in known:
        return ""
    return f"unknown tier '{tier}' - known tiers: {', '.join(known)}"


def resolve_model_tier(role: str, item_tier: str = "", attempt: int = 1,
                       doc_only: bool | None = None,
                       delta_lines: int | None = None) -> Dict[str, Any]:
    """role + item facts -> tier, deciding rule, and the model in EACH map.

    Raises HarnessConfigError on a malformed block or an unknown/wrong-case role or tier;
    callers are advisory and report it. ``doc_only`` / ``delta_lines`` of None mean UNKNOWN,
    and unknown never matches a rule that conditions on it.
    """
    if role not in MODEL_TIER_ROLES:
        raise HarnessConfigError(
            f"unknown role '{role}' for a model tier - known roles: {', '.join(MODEL_TIER_ROLES)}")
    problems = model_tiers_problems()
    if problems:
        raise HarnessConfigError(problems[0])
    mt = get("model_tiers")
    item_t = item_tier or default_model_tier()
    bad = model_tier_problem(item_t)
    if bad:
        raise HarnessConfigError(bad)
    for r in mt["rules"]:
        if "role" in r and r["role"] != role:
            continue
        if "min_attempt" in r and attempt < r["min_attempt"]:
            continue
        if "max_attempt" in r and attempt > r["max_attempt"]:
            continue
        if "doc_only" in r and (doc_only is None or bool(doc_only) != r["doc_only"]):
            continue
        if "max_delta_lines" in r and (delta_lines is None
                                       or int(delta_lines) > r["max_delta_lines"]):
            continue
        t = item_t if r["tier"] == "item" else r["tier"]
        out: Dict[str, Any] = {
            "role": role, "attempt": attempt, "item_tier": item_t, "tier": t,
            "rule": str(r.get("id", "")), "why": str(r.get("why", "")),
        }
        for sub in MODEL_TIER_SUBSTRATES:
            out[sub] = mt[sub]["roles"][t][role]
        return out
    raise HarnessConfigError(
        f"no model_tiers rule matched role '{role}' - the rule list needs a final catch-all "
        "(tier: item)")
