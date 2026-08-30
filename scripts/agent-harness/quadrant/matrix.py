"""The quadrant matrix: configuration -> the four cells, and whether each can run today.

TWO KINDS OF "NO", KEPT APART ON PURPOSE.

  * A CONFIG ERROR is a fact about the operator's file - a runner named in `quadrant.runners`
    that no `runners` entry defines, a little-coder runner with no endpoint. `build` RAISES.
    Reporting a typo as "NOT RUN: unreachable" would file a mistake under a legitimate
    result and it would never be found.
  * A BLOCKED QUADRANT is a fact about the world - the daemon is not listening, the CLI is
    not installed. `preflight` returns not-ready WITH A REASON, and the run becomes a
    `not_run` record. That is a real, reportable outcome.

The matrix is also the report's backbone: report.render walks THESE objects, never the
records, so a quadrant cannot vanish from a comparison by producing nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.json"

_SCHEMA: Dict[str, Any] | None = None


class QuadrantConfigError(ValueError):
    """The comparison is misconfigured. Loud by design - see the module docstring."""


def schema(fresh: bool = False) -> Dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is not None and not fresh:
        return _SCHEMA
    if not SCHEMA_PATH.is_file():
        raise QuadrantConfigError(
            f"quadrant schema not found: '{SCHEMA_PATH}'. It defines what a comparison "
            f"record is; without it nothing can validate one.")
    _SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


@dataclass(frozen=True)
class Quadrant:
    runner: str
    target: str
    runner_kind: str
    target_kind: str
    runner_status: str
    target_status: str
    comparable: bool
    incomparable_reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.runner}::{self.target}"

    @property
    def label(self) -> str:
        return f"{self.runner} x {self.target}"


class PreflightResult:
    """Ready, or not-ready WITH A REASON. There is no third state and no silent one."""

    def __init__(self, ready: bool, reason: str = "", detail: Dict[str, Any] | None = None):
        if not ready and not (reason or "").strip():
            # The one invariant of this class. A blocked quadrant with no reason is exactly
            # the record that reads as "nobody thought about it".
            raise ValueError("a not-ready PreflightResult must carry a reason")
        self.ready = bool(ready)
        self.reason = (reason or "").strip()
        self.detail = detail or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PreflightResult(ready={self.ready}, reason={self.reason!r})"


def _require(mapping: Dict[str, Any], name: str, fields: List[str], what: str) -> None:
    missing = [f for f in fields if not str(mapping.get(f) or "").strip()]
    if missing:
        raise QuadrantConfigError(
            f"{what} '{name}' is missing required field(s): {', '.join(missing)}")


def build(cfg: Dict[str, Any]) -> List[Quadrant]:
    """The configured cross product, validated. Raises QuadrantConfigError, never guesses."""
    s = schema()
    q = (cfg or {}).get("quadrant")
    if not isinstance(q, dict):
        raise QuadrantConfigError(
            "no 'quadrant' section in the harness configuration - the comparison does not "
            "know which runners and targets it is comparing")
    runners = list(q.get("runners") or [])
    targets = list(q.get("targets") or [])
    if not runners or not targets:
        raise QuadrantConfigError(
            f"quadrant.runners ({len(runners)}) and quadrant.targets ({len(targets)}) must "
            f"each name at least one entry")
    repeats = q.get("repeats", 1)
    if not isinstance(repeats, int) or repeats < 1:
        raise QuadrantConfigError(
            f"quadrant.repeats must be an integer >= 1, got {repeats!r}. A comparison that "
            f"runs a quadrant zero times still renders a row for it, which is worse than "
            f"refusing the configuration.")

    all_runners = (cfg or {}).get("runners") or {}
    all_targets = (cfg or {}).get("targets") or {}
    comparable_statuses = set(s["comparable_runner_statuses"])

    out: List[Quadrant] = []
    for rname in runners:
        r = all_runners.get(rname)
        if not isinstance(r, dict):
            raise QuadrantConfigError(
                f"quadrant.runners names '{rname}', which no 'runners' entry defines "
                f"(defined: {', '.join(sorted(all_runners)) or 'none'})")
        _require(r, rname, s["runner_required_fields"], "runner")
        _require(r, rname, s["runner_kind_requirements"].get(r["kind"], []), "runner")
        for tname in targets:
            t = all_targets.get(tname)
            if not isinstance(t, dict):
                raise QuadrantConfigError(
                    f"quadrant.targets names '{tname}', which no 'targets' entry defines "
                    f"(defined: {', '.join(sorted(all_targets)) or 'none'})")
            _require(t, tname, s["target_required_fields"], "target")
            _require(t, tname, s["target_kind_requirements"].get(t["kind"], []), "target")
            comparable = r["status"] in comparable_statuses
            out.append(Quadrant(
                runner=rname, target=tname,
                runner_kind=r["kind"], target_kind=t["kind"],
                runner_status=r["status"], target_status=t["status"],
                comparable=comparable,
                incomparable_reason=(
                    "" if comparable else
                    f"runner status '{r['status']}' is not comparable - it is scaffolding, "
                    f"and scaffolding in a decision table is a lie about where the number "
                    f"came from"),
            ))
    return out


def repeats(cfg: Dict[str, Any]) -> int:
    return int(((cfg or {}).get("quadrant") or {}).get("repeats", 1))


# ------------------------------------------------------------------ probes --
# Each probe answers one question about the WORLD and returns a PreflightResult. They are
# separate functions so a caller can stub one in a test without stubbing the concept.

def probe_claude_code(runner_cfg: Dict[str, Any]) -> PreflightResult:
    """Is a claude CLI resolvable? Same discovery order the sessions bridge proved.

    The PATH half matters: an agent's PATH is not the operator's, so 'it is installed'
    is not the same claim as 'this process can run it'.
    """
    env_bin = os.environ.get("BRIDGE_CLAUDE_BIN") or runner_cfg.get("bin")
    if env_bin and Path(env_bin).is_file():
        return PreflightResult(True, detail={"bin": env_bin, "via": "configured"})
    on_path = shutil.which("claude")
    if on_path:
        return PreflightResult(True, detail={"bin": on_path, "via": "PATH"})
    exts = sorted(glob(os.path.join(os.path.expanduser("~"), ".vscode", "extensions",
                                    "anthropic.claude-code-*", "resources", "native-binary",
                                    "claude.exe")))
    if exts:
        return PreflightResult(True, detail={"bin": exts[-1], "via": "vscode-extension"})
    return PreflightResult(False, reason=(
        "no claude CLI found: not on this process's PATH, no BRIDGE_CLAUDE_BIN, and no "
        "VS Code extension binary"))


def probe_little_coder(runner_cfg: Dict[str, Any], *, timeout: float = 4.0) -> PreflightResult:
    """Can THIS process dispatch a task to little-coder?

    Not "is little-coder healthy" - that question is answered by the container's own
    healthcheck and it is not the one that decides whether a quadrant can run. The question
    is whether the harness has a route to the API the config names.
    """
    endpoint = str(runner_cfg.get("endpoint") or "").rstrip("/")
    url = f"{endpoint}{runner_cfg.get('health_path') or '/health'}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if 200 <= resp.status < 300:
                return PreflightResult(True, detail={"endpoint": endpoint})
            return PreflightResult(False, reason=(
                f"little-coder endpoint {url} answered HTTP {resp.status}"))
    except (urllib.error.URLError, OSError) as exc:
        return PreflightResult(False, reason=(
            f"little-coder is not dispatchable from this process: {url} -> {exc}. "
            f"The daemon may well be healthy inside its container; what is missing is a "
            f"route from the host. Verified 2026-08-30: the running container publishes no "
            f"ports at all, so the API port is reachable only via 'docker exec'."))


def probe_fixture(runner_cfg: Dict[str, Any]) -> PreflightResult:
    return PreflightResult(True, detail={"note": "in-process; no external dependency"})


def probe_target_self(target_cfg: Dict[str, Any], *, repo: Path | None = None) -> PreflightResult:
    repo = Path(target_cfg.get("repo") or repo or Path.cwd())
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return PreflightResult(False, reason=(
            f"target 'self' needs a git repository at {repo}; git rev-parse failed: "
            f"{out.stderr.strip() or out.returncode}"))
    return PreflightResult(True, detail={"repo": str(repo)})


def probe_target_project(target_cfg: Dict[str, Any], *, scratch_root: str = "") -> PreflightResult:
    root = Path(scratch_root or target_cfg.get("scratch_root") or "")
    if not str(root):
        return PreflightResult(False, reason="target 'project' has no scratch_root")
    if not shutil.which("git"):
        return PreflightResult(False, reason="target 'project' needs git on PATH; not found")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return PreflightResult(False, reason=f"scratch root '{root}' is not writable: {exc}")
    return PreflightResult(True, detail={"scratch_root": str(root)})


_RUNNER_PROBES = {
    "claude-code": probe_claude_code,
    "little-coder": probe_little_coder,
    "fixture": probe_fixture,
}
_TARGET_PROBES = {
    "self": probe_target_self,
    "project": probe_target_project,
}


def preflight(q: Quadrant, cfg: Dict[str, Any], **kw: Any) -> PreflightResult:
    """Both halves must be ready. The reason names WHICH half, and why.

    A quadrant is a pair, and "it did not run" is useless to whoever has to fix it if it
    does not say whether the runner or the target was the blocker.
    """
    rprobe = _RUNNER_PROBES.get(q.runner_kind)
    tprobe = _TARGET_PROBES.get(q.target_kind)
    if rprobe is None:
        raise QuadrantConfigError(
            f"runner kind '{q.runner_kind}' has no preflight probe - a kind the harness "
            f"cannot check is a kind it must not silently attempt")
    if tprobe is None:
        raise QuadrantConfigError(
            f"target kind '{q.target_kind}' has no preflight probe")
    rcfg = (cfg.get("runners") or {}).get(q.runner, {})
    tcfg = (cfg.get("targets") or {}).get(q.target, {})
    rres = rprobe(rcfg)
    if not rres.ready:
        return PreflightResult(False, reason=f"runner '{q.runner}': {rres.reason}",
                               detail={"runner": rres.detail})
    # Only the keyword THIS probe declares. A probe signature is part of its contract, and
    # forwarding every caller kwarg to every probe makes adding one to a probe a change that
    # breaks the others.
    import inspect
    accepted = set(inspect.signature(tprobe).parameters) - {"target_cfg"}
    tres = tprobe(tcfg, **{k: v for k, v in kw.items() if k in accepted})
    if not tres.ready:
        return PreflightResult(False, reason=f"target '{q.target}': {tres.reason}",
                               detail={"runner": rres.detail, "target": tres.detail})
    return PreflightResult(True, detail={"runner": rres.detail, "target": tres.detail})
