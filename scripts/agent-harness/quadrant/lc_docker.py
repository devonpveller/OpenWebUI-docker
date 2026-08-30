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

WHAT THE MIRROR COSTS, recorded in every record rather than left in a comment: the host
workspace's own `.git` is not carried across (for target `self` it is a gitfile pointing
into this repo's worktree store and means nothing inside the container), so the mirror gets
a FRESH `git init` plus a baseline commit and the runner sees no history. That is not a
nicety - the daemon refuses a task with HTTP 409 unless `<workspace>/.git/HEAD` exists on
disk, which the first run of this transport discovered the hard way. The difference from
the claude-code cells (a real checkout with this repo's history) is attached to every
little-coder record as a note rather than left for a reader to infer.

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


_OWNER_PY = r"""
import os
root = __ROOT__
try:
    names = sorted(os.listdir(root))
except OSError:
    names = []
for n in names:
    try:
        st = os.stat(os.path.join(root, n))
    except OSError:
        continue
    print("%d:%d" % (st.st_uid, st.st_gid))
    break
"""


def workspace_owner(container: str, root: str) -> str:
    """uid:gid of whatever the daemon's own clone left in `root`, or "" if it is empty.

    SAMPLED rather than configured, because the answer is a property of the running images
    and would rot in a config file. Measured 2026-08-30: the clone's files are owned by
    1000:1000 - open-terminal's service user (`docker exec open-terminal id user` ->
    uid=1000), which is the uid every command the agent runs executes as, while the
    `/workspace` mount point itself belongs to little-coder's `lc` (10002). Files arriving
    by `docker cp` are root-owned with the host's modes, so without restoring this
    ownership the agent cannot write its own answer and git reports dubious ownership -
    a cell that fails for a reason about the harness, not about the runner.
    """
    out = _docker(["exec", "-i", container, "python3", "-"],
                  input_text=_OWNER_PY.replace("__ROOT__", repr(root)))
    return out.stdout.strip() if out.returncode == 0 else ""


def mirror_in(container: str, cws: str, workspace: Path, stage: Path,
              log: List[str] | None = None) -> int:
    """Stage the workspace, clear the container's workspace, copy it in, make it usable.

    A GIT REPOSITORY IS NOT OPTIONAL HERE, and that is the daemon's rule rather than a
    preference: `POST /tasks` refuses with HTTP 409 "no project focused" unless
    `WorkspaceManager.is_focused()` finds `<workspace>/.git/HEAD` on disk - an in-memory
    focus record is not enough, deliberately, because a corrupt workspace once let a task
    run "successfully" on a tree it could not branch or commit in. The first run of this
    transport hit exactly that 409. So the mirror gets its own `git init` plus a baseline
    commit, which also puts the little-coder cells closer to the claude-code ones, where
    the target adapter hands over a real checkout.

    The host workspace's own `.git` is NOT copied: for target `self` it is a gitfile
    pointing into this repo's worktree store and means nothing inside the container.
    """
    log = log if log is not None else []
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(workspace, stage, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    owner = workspace_owner(container, cws)
    clear(container, cws)
    # `<dir>/.` copies the directory's CONTENTS into cws, rather than nesting a directory
    # named after the staging dir underneath it.
    _docker_ok(["cp", f"{stage}/.", f"{container}:{cws}"],
               f"docker cp {stage} -> {container}:{cws}")
    _docker_ok(["exec", container, "sh", "-c",
                f"cd '{cws}' && git init -q && git add -A && "
                f"git -c user.email=quadrant@local -c user.name='quadrant harness' "
                f"commit -q -m 'quadrant: mirrored baseline'"],
               f"git init + baseline commit in {container}:{cws}")
    if owner:
        _docker_ok(["exec", container, "sh", "-c",
                    f"chown -R {owner} '{cws}' && chmod -R a+rwX '{cws}'"],
                   f"chown -R {owner} + chmod -R a+rwX {cws} in {container}")
        log.append(f"ownership : restored to {owner} (sampled from the daemon's own clone)")
    else:
        _docker_ok(["exec", container, "sh", "-c", f"chmod -R a+rwX '{cws}'"],
                   f"chmod -R a+rwX {cws} in {container}")
        log.append("ownership : the workspace was EMPTY before the mirror, so no owner "
                   "could be sampled; the mirror is root-owned and world-writable")
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
