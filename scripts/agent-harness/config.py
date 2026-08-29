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
from typing import Any, Dict, List

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
    "pipeline": {
        "claim_ttl_minutes": 60,
        "anchor_required": True,
        "human_gates": {"anchor": True, "pre_review": True},
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
