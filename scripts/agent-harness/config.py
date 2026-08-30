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
#: both were closed — ``andon.enabled: false`` and deleting the whole ``andon`` block each
#: report ``not-evaluated`` and halt. There was a THIRD, the one actually reached for:
#: deleting condition ENTRIES from ``andon.conditions``. Thinned to one of five on a
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
