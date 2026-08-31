"""Runner and target adapters - the two axes, behind two small interfaces.

PLAN 1's L3: "Runner x target compose freely ... Nothing in the pipeline layer knows which
quadrant it is in." That sentence is a design constraint, and this file is where it is
either honoured or quietly broken. A target adapter produces a WORKSPACE; a runner adapter
is handed a workspace and a task and produces a DISPATCH OUTCOME. Neither knows the other
exists, which is what makes the four cells a cross product rather than four scripts.

HOW THE LITTLE-CODER RUNNER REACHES A WORKSPACE (2026-08-30, when the quadrant harness and
the dispatch layer were merged). It cannot simply be handed one: its workspace is a docker
volume, its task API is not published on the host, and its own focus path clones from a
git-host URL only. So the runner adapter MIRRORS the workspace into the container, runs the
task there, and copies back every file that changed. The mechanics, the measurements behind
them and what the mirror costs live in `lc_docker.py`, which this file delegates to rather
than re-implementing; `matrix.probe_little_coder` decides reachability BEFORE dispatch, so
an unreachable or unfocused daemon is a `not_run` record with a reason and never an
exception here. See `documentation/notes/u4quad-findings.md` F7 for the park this closed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from . import proc as _proc
from . import lc_docker as _lc
from . import matrix as _matrix


@dataclass
class DispatchOutcome:
    ok: bool
    transcript: str = ""
    error: str = ""
    dispatch_attempts: int = 1
    turns: int | None = None
    usd: float | None = None
    tokens: int | None = None
    gpu_seconds: float | None = None
    detail: Dict[str, Any] = field(default_factory=dict)


class AdapterError(RuntimeError):
    """The adapter could not do its job. Becomes an `error` record, never a silent pass."""


# ------------------------------------------------------------------ targets --

def prepare_target(q: "_matrix.Quadrant", cfg: Dict[str, Any], *, run_dir: Path,
                   repo: Path, scratch_root: Path | None = None,
                   ref: str = "HEAD") -> Path:
    """Produce the workspace this quadrant's run happens in.

    The workspace always lives at ``<run_dir>/workspace`` regardless of target kind. That is
    not tidiness: ``evidence.workspace`` must still EXIST when the record is admitted, and a
    workspace that lives somewhere the harness later cleans up produces records that fail
    admission days after a perfectly good run.
    """
    ws = run_dir / "workspace"
    if q.target_kind == "project":
        ws.mkdir(parents=True, exist_ok=True)
        _git(ws, "init", "-q")
        _git(ws, "config", "user.email", "quadrant@local")
        _git(ws, "config", "user.name", "quadrant harness")
        return ws
    if q.target_kind == "self":
        # A DETACHED worktree: the comparison must not create a branch on the operator's
        # repo. Nothing here is landed, so a ref would be litter with a name.
        #
        # `repo` and `ref` come from the VENUE, not from this process's location. In a gym
        # run they are the arena checkout and its arena branch, so "self" means "the
        # repository the org is working in" - which in the arena is the arena's repo. The
        # hardcoded "HEAD" this line used to carry is why a gym run silently became an
        # ai-stack run: the only repo a hardcoded HEAD can name is the caller's.
        out = _proc.run(["git", "-C", str(repo), "worktree", "add", "--detach",
                              str(ws), ref], capture_output=True, text=True)
        if out.returncode != 0:
            raise AdapterError(
                f"target 'self': could not create a detached worktree at {ws} from "
                f"'{ref}' in {repo}: {out.stderr.strip()}")
        return ws
    raise AdapterError(f"no target adapter for kind '{q.target_kind}'")


def _rmtree_force(path: Path) -> None:
    """Delete a tree that git wrote, and RAISE if anything survives.

    Two facts make the obvious one-liner wrong on this platform, and both were measured
    rather than anticipated (2026-08-31):

      * git marks loose objects and packs READ-ONLY, and `shutil.rmtree` on Windows fails
        on a read-only file. So the mode bit is cleared in an `onerror` hook and the delete
        retried.
      * `ignore_errors=True` would have swallowed exactly that failure, leaving the nested
        repository in place while the caller believed it was gone. The first version of this
        used it and the test below caught it - which is the whole reason the check is an
        assertion about the FILESYSTEM afterwards and not about this call returning.
    """
    if not path.exists():
        return

    def _chmod_retry(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    # `onexc` replaced `onerror` in 3.12; this module supports both interpreters.
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda f, t, e: _chmod_retry(f, t, e))
    else:  # pragma: no cover - exercised only on <3.12
        shutil.rmtree(path, onerror=lambda f, t, e: _chmod_retry(f, t, e))
    if path.exists():
        raise AdapterError(f"could not remove the scratch repository at {path}; the "
                           f"evidence directory would not be committable")


def finalize_target(q: "_matrix.Quadrant", *, run_dir: Path, repo: Path) -> None:
    """Leave the operator's repo exactly as it was found, without losing the evidence.

    For target `self` the workspace is a registered worktree; leaving it registered would
    accumulate one per run in the operator's `git worktree list`. So the files are copied
    out first, then the worktree is deregistered, then the directory is re-created from the
    copy. `evidence.workspace` keeps pointing at a real directory holding exactly what the
    run produced.

    WHAT IS KEPT, and a defect fixed 2026-08-30. The first version kept only the CHANGED
    files. That is enough to see what the runner did and NOT enough to re-run the acceptance
    checks: `guards.py unmodified` needs the frozen files, and on this item the frozen file
    (`test_slugify.py`) is by definition unchanged - so it was dropped, and a verifier found
    that the two `target: self` cells' acceptance was no longer reproducible from the
    retained evidence even though it had genuinely passed when run. Evidence that cannot be
    re-checked is a record of a check, not a check. The kept set is therefore the union of
    the changed files and every path the PLANT MANIFEST names.

    TARGET `project`: THE SCRATCH `.git` IS REMOVED, and that is an evidence-durability
    requirement rather than tidiness (2026-08-31). A `project` workspace is a fresh
    `git init` scaffold whose entire history is the one baseline commit this module writes;
    everything unique about it is already in `manifest.json` (the planted paths) and in the
    item digest (their bytes). Leaving `.git` there makes the run directory UNCOMMITTABLE -
    `git add` on a tree containing a nested repository records a GITLINK to a commit that
    exists in no remote, so a fresh clone gets an empty directory where the workspace was.
    PLAN §C.6 makes the audit trail the deliverable's twin, and the U4 evidence set is the
    proof of that: the four-quadrant comparison of 2026-08-30 lived in `.quadrant/` under a
    per-session worktree, was ignored by `.gitignore`, and was destroyed with the worktree
    when the branch merged - `python -m quadrant.cli report` then said COMPARED 0/4 over the
    same claim of 4/4. Evidence a clone cannot see is not evidence, and evidence a
    repository cannot accept is evidence on its way to being deleted.

    The measurement is unaffected: `workspace_changes` and the acceptance commands both run
    BEFORE this function (see `cli._run_one`), and no acceptance check in any shipped item
    reads git - `guards.py` invokes no subprocess at all.
    """
    if q.target_kind == "project":
        _rmtree_force(run_dir / "workspace" / ".git")
        return
    if q.target_kind != "self":
        return
    ws = run_dir / "workspace"
    if not ws.exists():
        return
    keep = run_dir / "_workspace_changed"
    keep.mkdir(parents=True, exist_ok=True)
    changed = [ln[3:].strip().strip('"') for ln in _git_out(ws, "status", "--porcelain").splitlines() if ln.strip()]
    planted: List[str] = []
    mf = run_dir / "manifest.json"
    if mf.is_file():
        try:
            planted = [str(k) for k in json.loads(mf.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, OSError):
            planted = []
    for rel in dict.fromkeys(changed + planted):
        src = ws / rel
        if src.is_file():
            dst = keep / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    _proc.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(ws)],
                   capture_output=True, text=True)
    ws.mkdir(parents=True, exist_ok=True)
    for p in keep.rglob("*"):
        if p.is_file():
            dst = ws / p.relative_to(keep)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
    shutil.rmtree(keep, ignore_errors=True)


def _git(ws: Path, *args: str) -> None:
    out = _proc.run(["git", "-C", str(ws), *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise AdapterError(f"git {' '.join(args)} failed in {ws}: {out.stderr.strip()}")


def _git_out(ws: Path, *args: str) -> str:
    out = _proc.run(["git", "-C", str(ws), *args], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def baseline_commit(ws: Path) -> None:
    """Commit the planted item so `git status` afterwards is exactly what the RUNNER did.

    On target `self` this lands on a detached HEAD and dies with the worktree; on target
    `project` it is the scratch repo's first commit. Either way it is the measurement
    baseline for the scope column, and it is why scope does not need a filesystem walk of a
    repository this size.
    """
    _git(ws, "add", "-A")
    _proc.run(["git", "-C", str(ws), "-c", "user.email=quadrant@local",
                    "-c", "user.name=quadrant harness", "commit", "-q", "-m",
                    "quadrant: planted item baseline"], capture_output=True, text=True)


def workspace_changes(ws: Path) -> List[str]:
    lines = [ln for ln in _git_out(ws, "status", "--porcelain").splitlines() if ln.strip()]
    return [ln[3:].strip().strip('"') for ln in lines]


# ------------------------------------------------------------------ runners --

def dispatch(q: "_matrix.Quadrant", cfg: Dict[str, Any], *, item: Dict[str, Any],
             workspace: Path, run_dir: Path, timeout: float) -> DispatchOutcome:
    rcfg = (cfg.get("runners") or {}).get(q.runner, {})
    if q.runner_kind == "fixture":
        return _dispatch_fixture(item, workspace)
    if q.runner_kind == "claude-code":
        return _dispatch_claude_code(rcfg, item, workspace, run_dir, timeout)
    if q.runner_kind == "little-coder":
        return _dispatch_little_coder(rcfg, item, workspace, timeout)
    raise AdapterError(f"no runner adapter for kind '{q.runner_kind}'")


def _dispatch_fixture(item: Dict[str, Any], workspace: Path) -> DispatchOutcome:
    """Scaffolding. Performs the item deterministically, with no model of any kind.

    Its ONLY purpose is to prove this harness end to end - plant, dispatch, check, record,
    admit - without spending anything and without an LLM's variance in the loop. Its runner
    status is `self-test`, so `matrix.build` marks every quadrant it appears in
    non-comparable and `report.render` keeps it out of the decision table. A green here is a
    statement about the harness and never about a quadrant.
    """
    sol = item["spec"].get("fixture_solution")
    if not sol:
        raise AdapterError(f"item '{item['id']}' has no fixture_solution to apply")
    src = Path(item["dir"]) / sol["from"]
    if not src.is_file():
        raise AdapterError(f"fixture solution missing: {src}")
    dst = workspace / sol["to"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return DispatchOutcome(ok=True, transcript=(
        f"fixture runner: applied {sol['from']} -> {sol['to']}\n"
        f"(deterministic; no model was consulted and no cost was incurred)\n"))


def _dispatch_claude_code(rcfg: Dict[str, Any], item: Dict[str, Any], workspace: Path,
                          run_dir: Path, timeout: float) -> DispatchOutcome:
    pf = _matrix.probe_claude_code(rcfg)
    if not pf.ready:
        raise AdapterError(pf.reason)
    binary = pf.detail["bin"]
    model = rcfg.get("default_model") or "opus"
    cmd = [binary, "-p", item["task"], "--output-format", "json",
           "--permission-mode", "acceptEdits", "--model", model]
    budget = str(rcfg.get("max_budget_usd") or "").strip()
    if budget:
        cmd += ["--max-budget-usd", budget]
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(workspace)
    proc = _proc.run(cmd, cwd=str(workspace), capture_output=True, text=True,
                          timeout=timeout, env=env)
    transcript = (f"$ {' '.join(cmd[:2])} <task> {' '.join(cmd[3:])}\n"
                  f"--- exit {proc.returncode} ---\n{proc.stdout}\n{proc.stderr}\n")
    usd = tokens = turns = None
    try:
        payload = json.loads(proc.stdout)
        usd = payload.get("total_cost_usd")
        turns = payload.get("num_turns")
        usage = payload.get("usage") or {}
        tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or None
    except (json.JSONDecodeError, AttributeError):
        pass  # a runner that did not report is recorded as null, never as zero
    return DispatchOutcome(ok=proc.returncode == 0, transcript=transcript,
                           error="" if proc.returncode == 0 else f"claude exited {proc.returncode}",
                           turns=turns, usd=usd, tokens=tokens)


def _dispatch_little_coder(rcfg: Dict[str, Any], item: Dict[str, Any], workspace: Path,
                           timeout: float) -> DispatchOutcome:
    """Run the item on the local runner, over the transport its config declares."""
    transport = str(rcfg.get("transport") or "").strip() or "http"
    if transport == "docker-exec":
        return _dispatch_little_coder_docker(rcfg, item, workspace, timeout)
    if transport == "http":
        return _dispatch_little_coder_http(rcfg, item, workspace, timeout)
    raise AdapterError(
        f"little-coder declares transport '{transport}', which this adapter cannot speak "
        f"(known: docker-exec, http)")


def _dispatch_little_coder_docker(rcfg: Dict[str, Any], item: Dict[str, Any],
                                  workspace: Path, timeout: float) -> DispatchOutcome:
    """Mirror the workspace in, run the task, mirror the changed files back out.

    NO ACCEPTANCE COMMAND IS SENT, deliberately. The daemon will grade a task against one
    (`acceptance_command`, which dispatch.ps1 uses) and the claude-code adapter has no
    equivalent - it is handed the task text and nothing else. Giving one runner a
    machine-checkable success signal its counterpart does not have would turn the cells into
    a comparison of the briefs.
    """
    container = str(rcfg.get("container") or "")
    base_url = str(rcfg.get("base_url") or "").rstrip("/")
    cws = str(rcfg.get("container_workspace") or "/workspace").rstrip("/") or "/workspace"

    try:
        health = _lc.api(container, base_url, "GET",
                         str(rcfg.get("health_path") or "/health"))
    except _lc.LcDockerError as exc:
        raise AdapterError(str(exc)) from exc
    prior_focus = str(health.get("focus") or "")

    log: List[str] = [f"transport : docker-exec ({container}), workspace {cws}",
                      f"focus     : {prior_focus or '(none)'}"]
    notes: List[str] = [
        f"little-coder ran on a MIRROR of this workspace inside {container}:{cws}. The host "
        f"workspace's own '.git' was not carried across; the mirror was given a fresh "
        f"'git init' and one baseline commit, so the runner saw no history. Scope and "
        f"acceptance were measured host-side, after the changed files were copied back."]

    stage = workspace.parent / "_mirror"
    task: Dict[str, Any] = {}
    task_id = ""
    timed_out = False
    try:
        try:
            planted = _lc.mirror_in(container, cws, workspace, stage, log)
            log.append(f"mirrored  : {planted} file(s) into {container}:{cws}")
            before = _lc.snapshot(container, cws)

            created = _lc.api(container, base_url, "POST",
                              str(rcfg.get("submit_path") or "/tasks"),
                              # `channel` is a CLOSED SET in the daemon (TriggerRequest ->
                              # 422 "channel must be one of ['batch','cli','owui',
                              # 'validation']"). Measured, not guessed: the first run of
                              # this adapter sent "quadrant" and the cell recorded an
                              # error. 'batch' is the non-interactive lane dispatch.ps1
                              # already uses.
                              {"prompt": item["task"], "channel": "batch",
                               "user_id": "quadrant-harness"})
            task_id = str(created.get("task_id") or created.get("id") or "")
            if not task_id:
                raise AdapterError(
                    f"little-coder accepted the trigger but returned no task_id: {created}")
            log.append(f"task      : {task_id}")

            task_path = str(rcfg.get("task_path") or "/tasks/{id}").replace("{id}", task_id)
            task, timed_out = _lc.follow(container, base_url, task_path, timeout, log)

            after = _lc.snapshot(container, cws)
            changed, deleted = _lc.diff(before, after)
            log.append(f"changed   : {len(changed)} file(s) changed, {len(deleted)} deleted")
            copied = _lc.copy_back(container, cws, workspace, changed, deleted, log, notes)
            log.append(f"copied    : {copied} path(s) back to {workspace}")
        except _lc.LcDockerError as exc:
            raise AdapterError(str(exc)) from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        _lc.restore(container, base_url, cws, prior_focus, log, notes)

    status = str(task.get("status") or "")
    ok = (not timed_out) and status == "done"
    if timed_out:
        error = f"task {task_id} did not reach a terminal state within {timeout}s"
    elif not ok:
        error = f"task ended '{status}'"
    else:
        error = ""
    answer = str(task.get("answer") or "")
    if answer:
        log += ["", "--- the runner's answer ---", answer]
    commands = task.get("commands")
    return DispatchOutcome(
        ok=ok, transcript="\n".join(log) + "\n", error=error,
        # `commands` is how many shell commands the agent ran - the closest thing this
        # runner reports to a turn count. The cost fields stay None: the local runner reports
        # no tokens and no dollars, and null is honest where zero would be a claim.
        turns=(int(commands) if isinstance(commands, int) else None),
        detail={"task_id": task_id, "status": status, "notes": notes,
                "outcome": str(task.get("outcome") or ""),
                "signal": str(task.get("signal") or "")})


def _dispatch_little_coder_http(rcfg: Dict[str, Any], item: Dict[str, Any], workspace: Path,
                                timeout: float) -> DispatchOutcome:
    """POST the task, poll until terminal. THE REVERT PATH, kept executable.

    Only reachable when the runner declares transport 'http' - i.e. after someone publishes
    little-coder's task port and flips the config back (the steps are spelled out in
    harness.config.json's `_why_docker_exec`). Kept live rather than deleted so reverting is
    a config edit and not a re-implementation. Note what it CANNOT do: over HTTP the daemon
    is reachable but its filesystem is not, so this path drives a daemon whose workspace is
    already the workspace under test - there is no mirror.
    """
    endpoint = str(rcfg.get("endpoint") or rcfg.get("base_url") or "").rstrip("/")
    submit = f"{endpoint}{rcfg.get('submit_path') or '/tasks'}"
    body = json.dumps({"prompt": item["task"], "cwd": str(workspace),
                       "session": f"quadrant-{item['id']}"}).encode("utf-8")
    req = urllib.request.Request(submit, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            created = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"little-coder submit to {submit} failed: {exc}") from exc

    task_id = str(created.get("task_id") or created.get("id") or "")
    if not task_id:
        raise AdapterError(f"little-coder accepted the task but returned no id: {created}")
    poll = f"{endpoint}/tasks/{task_id}"
    deadline = time.time() + timeout
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(poll, timeout=15) as resp:  # noqa: S310
                last = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise AdapterError(f"little-coder poll {poll} failed: {exc}") from exc
        state = str(last.get("status") or last.get("state") or "")
        if state in _lc.TERMINAL:
            return DispatchOutcome(
                ok=state == "done",
                transcript=json.dumps(last, indent=2),
                error="" if state == "done" else f"task ended '{state}'",
                detail={"task_id": task_id})
        time.sleep(3)
    raise AdapterError(
        f"little-coder task {task_id} did not reach a terminal state within {timeout}s")
