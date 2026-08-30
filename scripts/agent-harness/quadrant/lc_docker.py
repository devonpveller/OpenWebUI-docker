"""The little-coder runner's `docker exec` transport for the quadrant comparison.

WHY THIS EXISTS AS A TRANSPORT AND NOT AS AN HTTP CALL. Three constraints were measured on
2026-08-30 (`documentation/notes/dfu-u4-findings.md` F2/F3/F5/F6), and together they decide
the whole shape of this file:

  1. **The task API is not published.** `docker inspect little-coder --format
     '{{json .NetworkSettings.Ports}}'` prints `{"9090/tcp":[]}` and `docker port
     little-coder` prints nothing, so `curl http://127.0.0.1:8090/health` from the host is
     connection-refused while `docker exec little-coder curl http://localhost:8090/health`
     answers 200. (The compose file DECLARES `127.0.0.1:9091:9090`; the declared and running
     states disagree and that cause is not established. It is not this file's business:
     nothing here depends on 9091.)
  2. **The workspace is a docker volume, not a bind mount.** `little-coder-workspace` is
     mounted at `/workspace` in both `little-coder` and `open-terminal`. Nothing the
     quadrant harness prepares on the host is visible to the agent unless it is copied in.
  3. **The daemon's own focus path cannot reach a harness worktree.** `normalize_repo_url`
     (`little-coder/src/littlecoder/urlnorm.py`) rejects local paths and `file://` - it
     requires host + owner/repo - and the local runner has no push credential for this repo
     (403). Reaching a run workspace through a git host would make every quadrant run a
     remote round-trip, which the comparison must not require.

So the transport MIRRORS: copy the workspace the target adapter produced into the
container's workspace root, run the task there, and copy back exactly the files that
changed - the changed set being computed by digesting the tree inside the container before
and after.

WHY THE WHOLE CHANGED SET AND NOT THE ITEM'S PREFIX. Copying back only `quadrant-item/`
would make `scope.out_of_scope_hits` read empty for every little-coder run whatever the
runner actually did, because the host `git status` would never see anything else. A column
that cannot register a violation is worse than no column: it reads as a measurement.

WHAT THE MIRROR COSTS, recorded in every record rather than left in a comment: `.git` is
not mirrored (for target `self` the workspace's `.git` is a gitfile pointing into this
repo's worktree store and means nothing inside the container), so the runner has no git in
its workspace. The item does not ask for a commit and both acceptance guards run host-side,
so this does not change the task - but it IS a difference between the quadrants, and the
adapter attaches it to the record as a note.

LIVE-PLANE MUTATION. Mirroring clears the daemon's workspace, so a run destroys whatever
the runner was working on. That is why `harness.config.json` gives this runner
`"lease": "coder"` and why `_restore` re-points the daemon at its prior focus afterwards -
and why a failure to restore is written into the record's notes instead of being swallowed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

TERMINAL = ("done", "abandoned", "rejected")

# Digest the mirrored tree INSIDE the container. Content, not timestamps: `docker cp` does
# not carry mtimes usefully across a Windows host, and a runner can touch a file without
# changing it.
_SNAPSHOT_PY = r"""
import hashlib, json, os
root = __ROOT__
out = {}
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if d not in ('.git', '__pycache__')]
    for fn in fns:
        p = os.path.join(dp, fn)
        rel = os.path.relpath(p, root).replace(os.sep, '/')
        try:
            with open(p, 'rb') as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[rel] = '<unreadable>'
print(json.dumps(out))
"""


class LcDockerError(RuntimeError):
    """The transport could not do its job. The adapter turns this into an `error` record."""


def _docker(args: List[str], *, input_text: str | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          input=input_text, timeout=timeout)


def _docker_ok(args: List[str], what: str, *, input_text: str | None = None,
               timeout: float | None = None) -> str:
    out = _docker(args, input_text=input_text, timeout=timeout)
    if out.returncode != 0:
        raise LcDockerError(f"{what} failed (exit {out.returncode}): "
                            f"{(out.stderr or out.stdout).strip()[:600]}")
    return out.stdout


def api(container: str, base_url: str, method: str, path: str,
        body: Dict[str, Any] | None = None, *, timeout: int = 60) -> Dict[str, Any]:
    """One call to the daemon across `docker exec`. A non-2xx raises, with the body.

    The request body goes in on curl's stdin (`--data-binary @-`) rather than through a
    temp file inside the container: nothing then has to be cleaned up in a filesystem this
    harness does not own, and no JSON has to survive an argv round-trip.
    """
    url = base_url.rstrip("/") + path
    args = ["exec", "-i", container, "curl", "-sS", "--max-time", str(timeout),
            "-w", "\n%{http_code}", "-X", method]
    payload = None
    if body is not None:
        args += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        payload = json.dumps(body)
    args.append(url)
    raw = _docker_ok(args, f"docker exec {container} curl {method} {url}",
                     input_text=payload, timeout=timeout + 60)
    text, _, code = raw.rpartition("\n")
    if not code.strip().startswith("2"):
        raise LcDockerError(f"little-coder {method} {path} returned HTTP {code.strip()}: "
                            f"{text.strip()[:600]}")
    text = text.strip()
    return json.loads(text) if text else {}


def snapshot(container: str, root: str) -> Dict[str, str]:
    # The script is fed on stdin (`python3 -`) with the root substituted as a Python
    # literal, so no path has to survive an argv round-trip through two shells.
    out = _docker_ok(["exec", "-i", container, "python3", "-"],
                     f"digesting {root} inside {container}",
                     input_text=_SNAPSHOT_PY.replace("__ROOT__", repr(root)))
    return json.loads(out)


def clear(container: str, root: str) -> None:
    _docker_ok(["exec", container, "sh", "-c",
                "find '%s' -mindepth 1 -maxdepth 1 -exec rm -rf {} +" % root],
               f"clearing {root} in {container}")


def diff(before: Dict[str, str], after: Dict[str, str]) -> Tuple[List[str], List[str]]:
    changed = sorted(rel for rel, d in after.items() if before.get(rel) != d)
    deleted = sorted(rel for rel in before if rel not in after)
    return changed, deleted


def mirror_in(container: str, cws: str, workspace: Path, stage: Path) -> int:
    """Stage the workspace without `.git`, clear the container's workspace, copy it in."""
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(workspace, stage, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    clear(container, cws)
    # `<dir>/.` copies the directory's CONTENTS into cws, rather than nesting a directory
    # named after the staging dir underneath it.
    _docker_ok(["cp", f"{stage}/.", f"{container}:{cws}"],
               f"docker cp {stage} -> {container}:{cws}")
    # The daemon's own clone is world-writable (0777 dirs, 0666 files, verified with
    # `stat -c %a /workspace`); files arriving by `docker cp` carry the host's modes and
    # root ownership, and the agent's exec does not run as root. Without this the runner
    # cannot write its own answer and the cell records a failure that is about the harness.
    _docker_ok(["exec", container, "sh", "-c", f"chmod -R a+rwX '{cws}'"],
               f"chmod -R a+rwX {cws} in {container}")
    return sum(1 for p in stage.rglob("*") if p.is_file())


def follow(container: str, base_url: str, task_path: str, timeout: float,
           log: List[str], poll_seconds: float = 10.0) -> Tuple[Dict[str, Any], bool]:
    """Poll one task to a terminal status. Returns (task, timed_out)."""
    deadline = time.time() + timeout
    task: Dict[str, Any] = {}
    while True:
        task = api(container, base_url, "GET", task_path)
        status = str(task.get("status") or "")
        if status in TERMINAL:
            log.append(f"status    : {status} (outcome={task.get('outcome')} "
                       f"signal={task.get('signal')})")
            return task, False
        if time.time() >= deadline:
            log.append(f"status    : TIMEOUT after {timeout}s (last status {status!r})")
            return task, True
        time.sleep(poll_seconds)


def copy_back(container: str, cws: str, workspace: Path, changed: List[str],
              deleted: List[str], log: List[str], notes: List[str],
              cap: int = 2000) -> int:
    """Bring the runner's edits back - ALL of them, not only the item's own prefix."""
    if len(changed) > cap:
        notes.append(f"the runner changed {len(changed)} files; only the first {cap} were "
                     f"copied back, so the scope figure is a LOWER BOUND")
        changed = changed[:cap]
    copied = 0
    for rel in changed:
        dst = workspace / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        out = _docker(["cp", f"{container}:{cws}/{rel}", str(dst)])
        if out.returncode != 0:
            log.append(f"  ! could not copy back {rel}: {(out.stderr or '').strip()[:200]}")
            notes.append(f"a file the runner changed could not be copied back: {rel}")
            continue
        copied += 1
    for rel in deleted:
        p = workspace / rel
        if p.is_file():
            p.unlink()
            copied += 1
    return copied


def restore(container: str, base_url: str, cws: str, prior_focus: str,
            log: List[str], notes: List[str]) -> None:
    """Put the plane back: clear the mirror, re-clone whatever the daemon was focused on.

    Best-effort, but NEVER silent. A daemon claiming a focus over a workspace that holds a
    quadrant fixture is a lying state that the next user of the runner inherits, so a
    failure to restore is written into the transcript AND into the record's notes, where a
    reader of the comparison sees it.
    """
    try:
        clear(container, cws)
    except LcDockerError as exc:
        log.append(f"restore   : could not clear {cws}: {exc}")
        notes.append(f"THE RUNNER'S WORKSPACE WAS NOT CLEARED after this run: {exc}")
    if not prior_focus:
        log.append("restore   : the daemon had no prior focus; nothing to re-point")
        return
    try:
        api(container, base_url, "POST", "/project",
            {"repo": prior_focus, "actor": "quadrant-harness", "fresh": True}, timeout=900)
        log.append(f"restore   : re-focused on {prior_focus}")
    except LcDockerError as exc:
        log.append(f"restore   : FAILED to re-focus on {prior_focus}: {exc}")
        notes.append(f"THE RUNNER WAS LEFT UNFOCUSED - re-point it at {prior_focus} "
                     f"(POST /project) before the next use: {exc}")
