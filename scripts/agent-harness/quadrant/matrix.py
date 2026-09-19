"""The quadrant matrix: configuration -> the four cells, and whether each can run today.

TWO KINDS OF "NO", KEPT APART ON PURPOSE.

  * A CONFIG ERROR is a fact about the operator's file - a runner named in `quadrant.runners`
    that no `runners` entry defines, a docker-exec runner with no container. `build` RAISES.
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
import urllib.error
import urllib.request
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Any, Dict, List

from . import proc as _proc
from . import venue as _venue

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


def _require_transport(r: Dict[str, Any], name: str, s: Dict[str, Any]) -> None:
    """A runner that declares a TRANSPORT must carry that transport's fields.

    Added 2026-08-30 when the quadrant harness and the dispatch layer were merged. The
    schema used to demand `endpoint` of every little-coder runner - an HTTP door on the host
    that little-coder does not publish - while the dispatch layer had already moved to
    `docker exec`. Making the demand per-transport keeps the property that mattered (a
    misconfigured runner is a LOUD config error, never a quiet NOT RUN) while letting the
    two answers to "how do I reach this runner" coexist.

    An UNKNOWN transport raises for the same reason `preflight` refuses a kind with no probe:
    a transport the harness cannot check is a transport it must not silently attempt.
    """
    reqs = s.get("runner_transport_requirements") or {}
    if not reqs:
        return
    transport = str(r.get("transport") or "").strip()
    if not transport:
        return  # kinds that declare no transport (claude-code, fixture) are unaffected
    if transport not in reqs:
        raise QuadrantConfigError(
            f"runner '{name}' declares transport '{transport}', which this harness has no "
            f"requirements for (known: {', '.join(sorted(reqs)) or 'none'}). A transport the "
            f"harness cannot check is one it must not silently attempt.")
    _require(r, name, reqs[transport], f"runner (transport '{transport}')")


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

    # The VENUE is validated here for the same reason a runner name is: a comparison that
    # cannot say WHERE it ran cannot be judged against a column that begins "Gym:". Shape
    # only - whether the arena is reachable today is a preflight question, not a typo.
    try:
        _venue.validate_shape(cfg, s)
    except _venue.VenueConfigError as exc:
        raise QuadrantConfigError(str(exc)) from exc

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
        _require_transport(r, rname, s)
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


def probe_little_coder(runner_cfg: Dict[str, Any], *, timeout: float = 8.0) -> PreflightResult:
    """Can THIS process dispatch a task to little-coder, over the transport it declares?

    Not "is little-coder healthy" - that question is answered by the container's own
    healthcheck and it is not the one that decides whether a quadrant can run. The question
    is whether the harness has a route to the API the config names, AND whether the daemon
    is in a state that accepts work.

    FOCUS IS PART OF READINESS. `POST /tasks` returns HTTP 409 when the daemon has no focused
    project (daemon.py), so a dispatch into an unfocused daemon is not a result about the
    quadrant - it is a fact about the plane, which is exactly what a BLOCKED preflight is
    for. Checking it here turns that into a reason a reader can act on instead of an adapter
    exception two minutes later.
    """
    transport = str(runner_cfg.get("transport") or "").strip() or "http"
    health_path = runner_cfg.get("health_path") or "/health"

    if transport == "http":
        endpoint = str(runner_cfg.get("endpoint") or runner_cfg.get("base_url") or "").rstrip("/")
        url = f"{endpoint}{health_path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
                if not 200 <= resp.status < 300:
                    return PreflightResult(False, reason=(
                        f"little-coder endpoint {url} answered HTTP {resp.status}"))
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return PreflightResult(False, reason=(
                f"little-coder is not dispatchable from this process: {url} -> {exc}. "
                f"The daemon may well be healthy inside its container; what is missing is a "
                f"route from the host. Measured 2026-08-30: the running container publishes "
                f"no ports at all, so the API port is reachable only via 'docker exec' - "
                f"which is what transport 'docker-exec' does."))
        return _lc_health_verdict(body, {"transport": "http", "endpoint": endpoint})

    if transport != "docker-exec":
        # build() already refuses an unknown transport; this is the second line of defence
        # for a caller that assembled a config by hand.
        return PreflightResult(False, reason=(
            f"little-coder declares transport '{transport}', which this harness cannot probe "
            f"(known: docker-exec, http)"))

    container = str(runner_cfg.get("container") or "").strip()
    base_url = str(runner_cfg.get("base_url") or "").rstrip("/")
    if not shutil.which("docker"):
        return PreflightResult(False, reason=(
            "little-coder is reached with 'docker exec' and this process has no 'docker' on "
            "its PATH. An agent's PATH is not the operator's - that is a fact about THIS "
            "process, not about the container."))
    url = f"{base_url}{health_path}"
    out = _proc.run(["docker", "exec", container, "curl", "-sS", "--max-time",
                          str(int(timeout)), url], capture_output=True, text=True)
    if out.returncode != 0:
        return PreflightResult(False, reason=(
            f"docker exec {container} curl {url} exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:400]}"))
    try:
        body = json.loads(out.stdout or "{}")
    except ValueError:
        return PreflightResult(False, reason=(
            f"little-coder {url} did not answer JSON via docker exec {container}: "
            f"{out.stdout.strip()[:200]!r}"))
    return _lc_health_verdict(body, {"transport": "docker-exec", "container": container,
                                     "base_url": base_url})


def _lc_health_verdict(body: Dict[str, Any], detail: Dict[str, Any]) -> PreflightResult:
    """One reading of /health, whichever transport carried it."""
    status = str(body.get("status") or "")
    focus = str(body.get("focus") or "")
    if status == "draining":
        return PreflightResult(False, reason=(
            "little-coder is draining (shutting down) and is not accepting triggers"))
    if status != "ok":
        return PreflightResult(False, reason=(
            f"little-coder /health says status={status!r}, not 'ok'"))
    if not focus:
        return PreflightResult(False, reason=(
            "little-coder has no focused project, and POST /tasks returns HTTP 409 without "
            "one. Focus it first (POST /project {repo: <git-host url>}) - note that doing so "
            "WIPES its workspace, which is why this runner declares the 'coder' lease."))
    d = dict(detail)
    d.update({"focus": focus, "version": str(body.get("version") or "")})
    return PreflightResult(True, detail=d)


def probe_fixture(runner_cfg: Dict[str, Any]) -> PreflightResult:
    return PreflightResult(True, detail={"note": "in-process; no external dependency"})


def probe_target_self(target_cfg: Dict[str, Any], *, repo: Path | None = None) -> PreflightResult:
    """`repo` is the VENUE's repository, passed down by the caller - not this process's CWD.

    Until 2026-08-30 the fallback chain ended at `Path.cwd()` and the caller passed the
    harness's own repo root, which is precisely how target 'self' came to mean 'ai-stack'
    in a run that had to be in the arena. The venue now decides; a config-level `repo` on
    the target still wins, because a target that names its own repository is being explicit.
    """
    repo = Path(target_cfg.get("repo") or repo or Path.cwd())
    out = _proc.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
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
    # THE VENUE FIRST. A cell whose runner and target are both ready but whose subject is
    # the wrong repository has not been blocked by anything today - it will run, complete,
    # and produce evidence for a column it does not satisfy. That is the failure this
    # ordering exists to make impossible; the reason lands in the cell's own not_run record.
    v = kw.get("venue")
    if v is not None:
        vres = _venue.probe(v, harness_repo=Path(kw.get("harness_repo") or Path.cwd()))
        if not vres.ready:
            return PreflightResult(False, reason=f"venue '{v.name}': {vres.reason}",
                                   detail={"venue": vres.detail})

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
