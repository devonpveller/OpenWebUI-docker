"""ao-plancheck - attribute a plan turn's writes to the plan turn.

A plan turn is read-only by contract, but `plan_only` only removes the daemon's edit/write TOOLS;
the shell still writes (gym-015: `sed -i`, `python3 -c`, commits). The bridge therefore checks the
workspace after the turn and reverts what the turn wrote (P17 F1/F15).

The first version of that check had no baseline: it ran `git status` after the plan turn and
reverted EVERYTHING dirty with `git checkout -- . && git clean -fd`, from a `~planclean` session
that could land on any free worker. In the C7e 0.3 ao-queue r1 bake-off run that deleted the
previous CODING turn's uncommitted repro test on worker-1 - and called it the plan turn's write -
while the plan turn itself had run in a fresh clone on worker-2 that nobody looked at.

The check is now two halves that run on the PLAN TURN'S OWN WORKER:
  * `snapshot_command` runs right before the turn (after the focus). It records HEAD, the branch
    and every path that already differs from HEAD (tracked edits and untracked files) with a
    content hash, and copies those files under `<git-dir>/ao-plan-snapshot/`. That is the
    baseline: work that was already on disk belongs to an earlier turn.
  * `check_command` runs right after the turn. It compares the workspace with the baseline and
    reverts only what CHANGED during the turn: a new path is removed, a path that was clean is
    restored from HEAD, a path that was already dirty is put back to its pre-turn content from the
    copy, a commit is undone with `reset --soft`. Everything else is left alone and counted as
    kept. It reports what it reverted and why.

The channel is hostile to a naive design (ao-planrevert failed twice on it), so:
  * Only proxy-whitelisted git: rev-parse, status, cat-file, checkout, reset (soft), rm. No
    plumbing, no `-c` (the worker's git-proxy denies both). Tests route every call through the
    real `git_proxy.classify`.
  * Scripts ship base64-encoded inside `python3 -c` (no shell quoting, no heredoc); python3 is in
    the open-terminal image (the git-proxy itself runs on it).
  * Results come back as ONE base64 token of zlib-compressed JSON between `<TAG>-BEGIN:` and
    `:<TAG>-END`. The parser deletes all whitespace before matching, so open-terminal's per-write
    chunking, a newline join between chunks, CRLF from a PTY and the daemon's 64 KB tail cannot
    break it; `redact_secrets` cannot match inside base64 (no `_`, no `://`). Paths travel inside
    JSON, so a newline in a path cannot forge an entry.
  * The bridge keeps the baseline manifest and embeds it in the check, so the plan turn cannot
    rewrite the record of what was on disk before it. The on-disk copies ARE in the turn's reach:
    a copy is used only if its hash matches the bridge's manifest; otherwise the path is reported
    as not restorable instead of being restored from tampered content.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
import zlib

SNAP_TAG = "AO-PLANSNAP"
CHECK_TAG = "AO-PLANCHECK"
# Largest encoded baseline the bridge carries back into the check command (bytes of base64). Past
# it the snapshot omits the manifest and the check relies on the on-disk state alone: still
# attributed, but no longer tamper-resistant. Keeps the check command far below Linux's 128 KB
# single-argument limit, and the snapshot reply below the daemon's 64 KB output tail.
MANIFEST_B64_CAP = 40_000
# Pre-turn files larger than this are hashed but not copied (restoring them is then impossible;
# the check says so instead of pretending).
COPY_CAP_BYTES = 64 * 1024 * 1024

_COMMON = r'''
import base64, hashlib, json, os, shutil, subprocess, sys, zlib
WS = '/workspace'
GIT = ["git"]

def git(*a):
    p = subprocess.run(GIT + list(a), cwd=WS, capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "surrogateescape")

def state_dir():
    rc, out = git("rev-parse", "--absolute-git-dir")
    return os.path.join(out.strip(), "ao-plan-snapshot") if rc == 0 and out.strip() else None

def dirty():
    rc, out = git("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    if rc != 0:
        return None
    paths = set()
    for e in out.split("\0"):
        if len(e) >= 4 and e[2] == " ":
            paths.add(e[3:])
    return sorted(paths)

def full(p):
    return os.path.join(WS, p)

def fdigest(f):
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:32]

def digest(p):
    f = full(p)
    if os.path.islink(f):
        return "L:" + os.readlink(f)
    if os.path.isdir(f):
        return "DIR"
    if not os.path.exists(f):
        return "-"
    return fdigest(f)

def head():
    rc, out = git("rev-parse", "--verify", "-q", "HEAD")
    return out.strip() if rc == 0 else ""

def branch():
    rc, out = git("rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if rc == 0 else ""

def pack(obj):
    return base64.b64encode(zlib.compress(json.dumps(obj).encode("utf-8"), 9)).decode("ascii")

def emit(tag, obj):
    sys.stdout.write(tag + "-BEGIN:" + pack(obj) + ":" + tag + "-END\n")
    sys.stdout.flush()
'''

_SNAPSHOT = r'''
TOKEN = %(token)r
CAP = %(cap)d
COPY_CAP = %(copy_cap)d
S = state_dir()
paths = dirty()
if S is None or paths is None:
    emit(%(tag)r, {"status": "error", "why": "not a git workspace"})
    sys.exit(0)
shutil.rmtree(S, ignore_errors=True)
os.makedirs(os.path.join(S, "files"))
man, nocopy = {}, []
for p in paths:
    d = digest(p)
    man[p] = d
    if d in ("-", "DIR") or d.startswith("L:"):
        continue
    if os.path.getsize(full(p)) > COPY_CAP:
        nocopy.append(p)
        continue
    dst = os.path.join(S, "files", p)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(full(p), dst)
st = {"token": TOKEN, "head": head(), "branch": branch(), "manifest": man, "nocopy": nocopy}
with open(os.path.join(S, "state.json"), "w") as fh:
    json.dump(st, fh)
out = dict(st, status="ok", count=len(man))
if len(pack(out)) > CAP:
    out["manifest"] = None
    out["nocopy"] = None
emit(%(tag)r, out)
'''

_CHECK = r'''
TOKEN = %(token)r
BRIDGE_B64 = %(bridge)r
BRIDGE = json.loads(zlib.decompress(base64.b64decode(BRIDGE_B64))) if BRIDGE_B64 else None
S = state_dir()
disk = None
if S and os.path.isfile(os.path.join(S, "state.json")):
    try:
        with open(os.path.join(S, "state.json")) as fh:
            disk = json.load(fh)
    except Exception:
        disk = None
if not isinstance(disk, dict) or disk.get("token") != TOKEN:
    disk = None
base = BRIDGE if BRIDGE is not None else disk
if base is None or not isinstance(base.get("manifest"), dict):
    emit(%(tag)r, {"status": "no-snapshot",
                   "why": "no baseline for this turn in this workspace - nothing attributed"})
    sys.exit(0)
pre_head, pre_branch, man = base.get("head", ""), base.get("branch", ""), base["manifest"]
nocopy = set(base.get("nocopy") or [])
reverted, unrestored = [], []

def drop(f):
    if os.path.isdir(f) and not os.path.islink(f):
        shutil.rmtree(f, ignore_errors=True)
    elif os.path.lexists(f):
        os.remove(f)

cur_branch = branch()
if pre_branch and pre_branch != "HEAD" and cur_branch and cur_branch != pre_branch:
    rc, _o = git("checkout", "-q", pre_branch)
    if rc == 0:
        reverted.append({"path": "(branch)",
                         "why": "the plan turn switched to %%s; switched back to %%s"
                                %% (cur_branch, pre_branch)})
    else:
        unrestored.append({"path": "(branch)",
                           "why": "the plan turn switched to %%s; switching back failed"
                                  %% cur_branch})
head_reset = ""
cur = head()
if pre_head and cur and cur != pre_head:
    rc, _o = git("reset", "-q", "--soft", pre_head)
    if rc == 0:
        head_reset = cur
        reverted.append({"path": "(HEAD)",
                         "why": "the plan turn committed %%s; commit undone (reset --soft %%s)"
                                %% (cur[:12], pre_head[:12])})
    else:
        unrestored.append({"path": "(HEAD)", "why": "the plan turn committed %%s; reset failed"
                                                    %% cur[:12]})

now = dirty()
if now is None:
    emit(%(tag)r, {"status": "error", "why": "git status failed after the turn"})
    sys.exit(0)
now_set = set(now)
kept, removed = [], []
for p in sorted(now_set | set(man)):
    pre = man.get(p)
    nowd = digest(p) if p in now_set else None
    if pre == nowd:
        if pre is not None:
            kept.append(p)
        continue
    f = full(p)
    if pre is None:
        in_head = bool(pre_head) and git("cat-file", "-e", pre_head + ":" + p)[0] == 0
        if in_head:
            rc, _o = git("checkout", pre_head, "--", p)
            if rc == 0:
                reverted.append({"path": p, "why": "changed by the plan turn - restored from HEAD"})
            else:
                unrestored.append({"path": p, "why": "changed by the plan turn - restore failed"})
        else:
            git("rm", "-q", "--cached", "--ignore-unmatch", "--", p)
            drop(f)
            removed.append(p)
            reverted.append({"path": p, "why": "created by the plan turn - removed"})
    elif pre == "-":
        drop(f)
        removed.append(p)
        reverted.append({"path": p, "why": "deleted before the plan turn, re-created by it - "
                                           "removed again"})
    elif pre == "DIR":
        unrestored.append({"path": p, "why": "a directory entry changed during the plan turn - "
                                             "left as is"})
    elif pre.startswith("L:"):
        drop(f)
        os.symlink(pre[2:], f)
        reverted.append({"path": p, "why": "a symlink from before the plan turn was changed by it "
                                           "- restored to its state before the plan turn"})
    else:
        src = os.path.join(S, "files", p) if S else ""
        if p not in nocopy and src and os.path.isfile(src) and not os.path.islink(src) \
                and fdigest(src) == pre:
            drop(f)
            os.makedirs(os.path.dirname(f) or WS, exist_ok=True)
            shutil.copy2(src, f)
            reverted.append({"path": p, "why": "edited before the plan turn and changed by it - "
                                               "restored to its state before the plan turn"})
        else:
            unrestored.append({"path": p, "why": "edited before the plan turn and changed by it - "
                                                 "no intact copy to restore from; left as is"})
for p in removed:
    d = os.path.dirname(full(p))
    while d and os.path.abspath(d) != os.path.abspath(WS):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)
if S:
    shutil.rmtree(S, ignore_errors=True)
emit(%(tag)r, {"status": "ok", "reverted": reverted, "unrestored": unrestored,
               "head_reset": head_reset, "kept": kept[:50], "kept_count": len(kept)})
'''


def new_token() -> str:
    return uuid.uuid4().hex


def _wrap(src: str) -> str:
    """`python3 -c` with the script base64-encoded: no quoting, no heredoc, any POSIX shell."""
    b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
    return ("python3 -c \"import base64;exec(compile(base64.b64decode('" + b64
            + "').decode('utf-8'),'ao-plancheck','exec'))\"")


def snapshot_command(token: str) -> str:
    """The pre-turn baseline (runs on the plan turn's worker, after the focus)."""
    return _wrap(_COMMON + _SNAPSHOT % {"token": token, "cap": MANIFEST_B64_CAP,
                                        "copy_cap": COPY_CAP_BYTES, "tag": SNAP_TAG})


def check_command(token: str, baseline: dict | None) -> str:
    """The post-turn check + revert. `baseline` is what `parse_snapshot` returned; its manifest
    makes the check independent of the on-disk state the turn could have touched."""
    bridge = ""
    if baseline and isinstance(baseline.get("manifest"), dict):
        bridge = base64.b64encode(zlib.compress(json.dumps({
            "head": baseline.get("head", ""), "branch": baseline.get("branch", ""),
            "manifest": baseline["manifest"], "nocopy": baseline.get("nocopy") or [],
        }).encode("utf-8"), 9)).decode("ascii")
    return _wrap(_COMMON + _CHECK % {"token": token, "bridge": bridge, "tag": CHECK_TAG})


def frame(tag: str, obj: dict) -> str:
    """The reply framing the scripts' `emit` writes (also used by tests to fake a reply)."""
    payload = base64.b64encode(zlib.compress(json.dumps(obj).encode("utf-8"), 9)).decode("ascii")
    return f"{tag}-BEGIN:{payload}:{tag}-END"


def _parse(tag: str, out: str | None) -> dict | None:
    """The last framed reply in `out`. All whitespace is removed first: the transport may split
    the line anywhere (per-write chunks joined with a newline, CRLF), never inside a base64
    character."""
    compact = re.sub(r"\s+", "", out or "")
    found = re.findall(re.escape(tag) + r"-BEGIN:([A-Za-z0-9+/=]+):" + re.escape(tag) + "-END",
                       compact)
    if not found:
        return None
    try:
        obj = json.loads(zlib.decompress(base64.b64decode(found[-1], validate=True)))
    except (ValueError, zlib.error):
        return None
    return obj if isinstance(obj, dict) else None


def parse_snapshot(out: str | None) -> dict | None:
    """The baseline, or None when the snapshot did not run cleanly (no attribution possible)."""
    obj = _parse(SNAP_TAG, out)
    return obj if obj and obj.get("status") == "ok" and obj.get("token") else None


def parse_check(out: str | None) -> dict | None:
    return _parse(CHECK_TAG, out)
