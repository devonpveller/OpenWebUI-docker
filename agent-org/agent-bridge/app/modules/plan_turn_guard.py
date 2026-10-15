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
  * `take_baseline` (via the router's pre-turn hook) runs right before the turn, after the focus
    and after any earlier task on that worker is confirmed dead. `snapshot_command` records HEAD,
    the branch, every ref, and every path that already differs from HEAD (tracked edits and
    untracked files) with a content hash, and copies those files under
    `<git-dir>/ao-plan-snapshot/`. The whole baseline comes back to the bridge, paged if large,
    before the turn starts: work that was already on disk belongs to an earlier turn.
  * `check_command` runs right after the turn with the BRIDGE'S copy of the baseline (inline, or
    staged in chunks by `stage_commands` and verified by hash). It reverts only what CHANGED
    during the turn: a new path is removed, a path that was clean is restored from HEAD, a path
    that was already dirty is put back to its pre-turn content from a hash-verified copy, a commit
    is undone with `reset --soft`. Refs the turn created or moved are recorded (the proxy forbids
    deleting refs). Every revert is journaled as it happens, so a check cut off by its timeout
    resumes on a second run, and `report_command` can still say exactly what was reverted.

The channel is hostile to a naive design (ao-planrevert failed twice on it), so:
  * Only proxy-whitelisted git: rev-parse, status, for-each-ref, ls-tree, checkout, reset
    (soft), rm --cached, plus the benign global `--literal-pathspecs`. No plumbing, no `-c`.
  * Git calls are BATCHED (each proxied call costs ~0.3 s on a worker: python start-up). An
    untracked path needs no git call at all; HEAD membership is one `ls-tree` per argv chunk.
  * Scripts ship base64-encoded inside `python3 -c` (no shell quoting, no heredoc).
  * Replies are ONE base64 token of zlib-compressed JSON between `<TAG>-BEGIN:` and `:<TAG>-END`;
    the parser deletes all whitespace first (per-write chunking, newline joins, CRLF, the 64 KB
    tail), and `redact_secrets` cannot match inside base64. Lists in replies are capped, with
    counts.
  * The baseline the check trusts is the bridge's (sha256-pinned), never the on-disk state the
    turn could have forged; no bridge baseline = no attribution, recorded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
import zlib
from collections.abc import Awaitable, Callable

SNAP_TAG = "AO-PLANSNAP"
CHECK_TAG = "AO-PLANCHECK"
PAGE_TAG = "AO-PLANPAGE"
# Largest encoded baseline carried INLINE in the snapshot reply / the check command (bytes of
# base64). Larger baselines are paged back before the turn and staged in chunks before the check.
MANIFEST_B64_CAP = 40_000
# Per page / per staging chunk (keeps every reply under the daemon's 64 KB tail and every command
# far under Linux's 128 KB single-argument limit).
PAGE_CHARS = 36_000
# Above this the baseline is not fetched at all: fail closed, recorded (~ hundreds of thousands
# of pre-existing dirty paths).
MAX_BASELINE_CHARS = 8_000_000
# Pre-turn files larger than this are hashed but not copied (restoring them is then impossible;
# the check says so instead of pretending).
COPY_CAP_BYTES = 64 * 1024 * 1024
# Reply list caps (counts are always exact).
REPORT_CAP = 200

_COMMON = r'''
import base64, hashlib, json, os, shutil, subprocess, sys, zlib
WS = '/workspace'
GIT = ["git"]
ARGV_BUDGET = 24000

def git(*a):
    p = subprocess.run(GIT + list(a), cwd=WS, capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "surrogateescape")

def state_dir():
    rc, out = git("rev-parse", "--absolute-git-dir")
    return os.path.join(out.strip(), "ao-plan-snapshot") if rc == 0 and out.strip() else None

def status_xy():
    rc, out = git("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    if rc != 0:
        return None
    st = {}
    for e in out.split("\0"):
        if len(e) >= 4 and e[2] == " ":
            # one path can appear twice (`D ` in the index and `??` in the tree): keep both codes
            st[e[3:]] = (st[e[3:]] + "," + e[:2]) if e[3:] in st else e[:2]
    return st

def dirty():
    st = status_xy()
    return None if st is None else sorted(st)

def chunks(paths):
    cur, size = [], 0
    for p in paths:
        n = len(p.encode("utf-8", "surrogateescape")) + 3
        if cur and (size + n > ARGV_BUDGET or len(cur) >= 800):
            yield cur
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        yield cur

def refs():
    rc, out = git("for-each-ref", "--format=%(objectname) %(refname)")
    if rc != 0:
        return None
    r = {}
    for ln in out.splitlines():
        sha, _s, name = ln.strip().partition(" ")
        if name:
            r[name] = sha
    return r

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
st_xy = status_xy()
rf = refs()
if S is None or st_xy is None or rf is None:
    emit(%(tag)r, {"status": "error", "why": "not a git workspace"})
    sys.exit(0)
shutil.rmtree(S, ignore_errors=True)
os.makedirs(os.path.join(S, "files"))
man, nocopy = {}, []
for p in sorted(st_xy):
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
state = {"token": TOKEN, "head": head(), "branch": branch(), "manifest": man, "nocopy": nocopy,
         "refs": rf}
blob = pack(state)
with open(os.path.join(S, "state.b64"), "w") as fh:
    fh.write(blob)
out = {"status": "ok", "token": TOKEN, "head": state["head"], "branch": state["branch"],
       "count": len(man), "blob_len": len(blob),
       "blob_sha": hashlib.sha256(blob.encode("ascii")).hexdigest()}
if len(blob) <= CAP:
    out["blob"] = blob
emit(%(tag)r, out)
'''

_PAGE = r'''
TOKEN = %(token)r
OFF = %(off)d
N = %(n)d
S = state_dir()
try:
    with open(os.path.join(S, "state.b64")) as fh:
        blob = fh.read()
    ok = json.loads(zlib.decompress(base64.b64decode(blob))).get("token") == TOKEN
except Exception:
    ok = False
if not ok:
    emit(%(tag)r, {"status": "error", "why": "no baseline for this token"})
    sys.exit(0)
emit(%(tag)r, {"status": "ok", "off": OFF, "total": len(blob), "data": blob[OFF:OFF + N]})
'''

_STAGE = r'''
TOKEN = %(token)r
FIRST = %(first)r
DATA = %(data)r
S = state_dir()
os.makedirs(S, exist_ok=True)
with open(os.path.join(S, "bridge-" + TOKEN + ".b64"), "w" if FIRST else "a") as fh:
    fh.write(DATA)
emit(%(tag)r, {"status": "ok", "staged": len(DATA)})
'''

_CHECK = r'''
TOKEN = %(token)r
INLINE = %(inline)r
BLOB_SHA = %(blob_sha)r
CAPN = %(cap)d
S = state_dir()
blob = INLINE
if not blob and BLOB_SHA and S and os.path.isfile(os.path.join(S, "bridge-" + TOKEN + ".b64")):
    with open(os.path.join(S, "bridge-" + TOKEN + ".b64")) as fh:
        blob = fh.read()
base = None
if blob and hashlib.sha256(blob.encode("ascii")).hexdigest() == BLOB_SHA:
    try:
        base = json.loads(zlib.decompress(base64.b64decode(blob)))
    except Exception:
        base = None
if not isinstance(base, dict) or base.get("token") != TOKEN or \
        not isinstance(base.get("manifest"), dict):
    emit(%(tag)r, {"status": "no-snapshot",
                   "why": "the bridge's baseline for this turn is not available here - "
                          "nothing attributed"})
    sys.exit(0)
pre_head, pre_branch, man = base.get("head", ""), base.get("branch", ""), base["manifest"]
pre_refs = base.get("refs") or {}
os.makedirs(S, exist_ok=True)
JP = os.path.join(S, "journal-" + TOKEN + ".jsonl")
prior = []
if os.path.isfile(JP):
    with open(JP, encoding="utf-8", errors="surrogateescape") as fh:
        for ln in fh:
            try:
                prior.append(json.loads(ln))
            except Exception:
                pass
JF = open(JP, "a", encoding="utf-8", errors="surrogateescape")
entries = []

def rec(kind, path, why, **extra):
    e = dict(extra, k=kind, path=path, why=why)
    entries.append(e)
    JF.write(json.dumps(e) + "\n")
    if len(entries) %% 100 == 0:
        JF.flush()

def drop(f):
    if os.path.isdir(f) and not os.path.islink(f):
        shutil.rmtree(f, ignore_errors=True)
    elif os.path.lexists(f):
        os.remove(f)

cur_branch = branch()
if pre_branch and pre_branch != "HEAD" and cur_branch and cur_branch != pre_branch:
    rc, _o = git("checkout", "-q", pre_branch)
    rec("r" if rc == 0 else "u", "(branch)",
        ("the plan turn switched to %%s; switched back to %%s" %% (cur_branch, pre_branch))
        if rc == 0 else "the plan turn switched to %%s; switching back failed" %% cur_branch)
head_reset = ""
cur = head()
if pre_head and cur and cur != pre_head:
    rc, _o = git("reset", "-q", "--soft", pre_head)
    if rc == 0:
        head_reset = cur
        rec("r", "(HEAD)", "the plan turn committed %%s; commit undone (reset --soft %%s)"
                            %% (cur[:12], pre_head[:12]), sha=cur)
    else:
        rec("u", "(HEAD)", "the plan turn committed %%s; reset failed" %% cur[:12])
JF.flush()

now = status_xy()
if now is None:
    JF.close()
    emit(%(tag)r, {"status": "error", "why": "git status failed after the turn"})
    sys.exit(0)
kept_n, kept = 0, []
fresh, from_copy = [], []
for p in sorted(set(now) | set(man)):
    pre = man.get(p)
    nowd = digest(p) if p in now else None
    if pre == nowd:
        if pre is not None:
            kept_n += 1
            if len(kept) < 50:
                kept.append(p)
        continue
    (fresh if pre is None else from_copy).append(p)

# paths clean before the turn: in HEAD -> restore from HEAD; else -> remove. A path that is
# ONLY untracked (`??`) is in neither the index nor HEAD, so it costs no git call at all; HEAD
# membership of the rest is one `ls-tree` per argv chunk; `rm --cached` only for staged ones.
in_head = set()
asked = [p for p in fresh if now.get(p, "??") != "??"]
if pre_head and asked:
    for ch in chunks(asked):
        rc, out = git("--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", pre_head,
                      "--", *ch)
        if rc == 0:
            in_head.update(x for x in out.split("\0") if x)
to_restore = [p for p in fresh if p in in_head]
to_remove = [p for p in fresh if p not in in_head]
unstage = [p for p in to_remove if any(x[0] not in ("?", " ")
                                       for x in now.get(p, "??").split(","))]
removed = []

def prune(paths):
    dirs = set()
    for p in paths:
        d = os.path.dirname(full(p))
        while d and os.path.abspath(d) != os.path.abspath(WS) and d not in dirs:
            dirs.add(d)
            d = os.path.dirname(d)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass

for p in to_remove:
    drop(full(p))
    removed.append(p)
    rec("r", p, "created by the plan turn - removed")
JF.flush()
prune(removed)
for ch in chunks(unstage):
    git("--literal-pathspecs", "rm", "-q", "--cached", "--ignore-unmatch", "--", *ch)
for ch in chunks(to_restore):
    rc, _o = git("--literal-pathspecs", "checkout", pre_head, "--", *ch)
    if rc == 0:
        for p in ch:
            rec("r", p, "changed by the plan turn - restored from HEAD")
    else:
        for p in ch:
            rc1, _o = git("--literal-pathspecs", "checkout", pre_head, "--", p)
            rec("r" if rc1 == 0 else "u", p, "changed by the plan turn - restored from HEAD"
                if rc1 == 0 else "changed by the plan turn - restore failed")
    JF.flush()

for p in from_copy:
    pre = man[p]
    f = full(p)
    if pre == "-":
        drop(f)
        removed.append(p)
        rec("r", p, "deleted before the plan turn, re-created by it - removed again")
    elif pre == "DIR":
        rec("u", p, "a directory entry changed during the plan turn - left as is")
    elif pre.startswith("L:"):
        drop(f)
        os.symlink(pre[2:], f)
        rec("r", p, "a symlink from before the plan turn was changed by it - restored to its "
                    "state before the plan turn")
    else:
        src = os.path.join(S, "files", p)
        if os.path.isfile(src) and not os.path.islink(src) and fdigest(src) == pre:
            drop(f)
            os.makedirs(os.path.dirname(f) or WS, exist_ok=True)
            shutil.copy2(src, f)
            rec("r", p, "edited before the plan turn and changed by it - restored to its state "
                        "before the plan turn")
        else:
            rec("u", p, "edited before the plan turn and changed by it - no intact copy to "
                        "restore from; left as is")
JF.flush()

prune(removed)

# refs: the proxy forbids deleting refs (`branch -D`, `tag -d`, `update-ref`), so a ref the
# plan turn created or moved is RECORDED, not removed. Remote-tracking refs are a fetch.
refs_left = []
now_refs = refs() or {}
for r in sorted(set(now_refs) | set(pre_refs)):
    if r.startswith("refs/remotes/") or r == "refs/heads/" + pre_branch:
        continue
    a, b = pre_refs.get(r), now_refs.get(r)
    if a == b:
        continue
    refs_left.append({"ref": r, "change": "created" if a is None else
                      ("deleted" if b is None else "moved")})
JF.close()
allr = prior + entries
rev = [{"path": e["path"], "why": e["why"]} for e in allr if e.get("k") == "r"]
unr = [{"path": e["path"], "why": e["why"]} for e in allr if e.get("k") == "u"]
heads = [e.get("sha", "") for e in allr if e.get("path") == "(HEAD)" and e.get("k") == "r"]
shutil.rmtree(S, ignore_errors=True)
emit(%(tag)r, {"status": "ok", "reverted": rev[:CAPN], "reverted_count": len(rev),
               "unrestored": unr[:CAPN], "unrestored_count": len(unr),
               "head_reset": head_reset or (heads[0] if heads else ""),
               "kept": kept, "kept_count": kept_n, "refs_left": refs_left[:CAPN],
               "resumed": bool(prior)})
'''

_REPORT = r'''
TOKEN = %(token)r
CAPN = %(cap)d
S = state_dir()
JP = os.path.join(S, "journal-" + TOKEN + ".jsonl") if S else ""
allr = []
if JP and os.path.isfile(JP):
    with open(JP, encoding="utf-8", errors="surrogateescape") as fh:
        for ln in fh:
            try:
                allr.append(json.loads(ln))
            except Exception:
                pass
rev = [{"path": e["path"], "why": e["why"]} for e in allr if e.get("k") == "r"]
unr = [{"path": e["path"], "why": e["why"]} for e in allr if e.get("k") == "u"]
left = status_xy()
if S:
    shutil.rmtree(S, ignore_errors=True)
emit(%(tag)r, {"status": "partial", "reverted": rev[:CAPN], "reverted_count": len(rev),
               "unrestored": unr[:CAPN], "unrestored_count": len(unr), "head_reset": "",
               "kept": [], "kept_count": 0, "refs_left": [],
               "dirty_after": None if left is None else len(left)})
'''


def new_token() -> str:
    return uuid.uuid4().hex


def _wrap(src: str) -> str:
    """`python3 -c` with the script base64-encoded: no quoting, no heredoc, any POSIX shell."""
    b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
    return ("python3 -c \"import base64;exec(compile(base64.b64decode('" + b64
            + "').decode('utf-8'),'ao-plancheck','exec'))\"")


def _pack(obj: dict) -> str:
    return base64.b64encode(zlib.compress(json.dumps(obj).encode("utf-8"), 9)).decode("ascii")


def _sha(blob: str) -> str:
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def snapshot_command(token: str) -> str:
    """The pre-turn baseline (runs on the plan turn's worker, after the focus)."""
    return _wrap(_COMMON + _SNAPSHOT % {"token": token, "cap": MANIFEST_B64_CAP,
                                        "copy_cap": COPY_CAP_BYTES, "tag": SNAP_TAG})


def page_command(token: str, off: int, n: int = PAGE_CHARS) -> str:
    """One page of a baseline too large to come back inline (run BEFORE the turn)."""
    return _wrap(_COMMON + _PAGE % {"token": token, "off": off, "n": n, "tag": PAGE_TAG})


def bridge_blob(baseline: dict | None) -> str:
    """The bridge's encoded baseline (what the check trusts)."""
    if not baseline:
        return ""
    if baseline.get("blob"):
        return baseline["blob"]
    if isinstance(baseline.get("manifest"), dict):
        return _pack({"token": baseline.get("token", ""), "head": baseline.get("head", ""),
                      "branch": baseline.get("branch", ""), "manifest": baseline["manifest"],
                      "nocopy": baseline.get("nocopy") or [], "refs": baseline.get("refs") or {}})
    return ""


def stage_commands(token: str, baseline: dict | None) -> list[str]:
    """Commands that write the bridge's baseline into the workspace in chunks, for a baseline too
    large to embed in the check command. Empty when it fits inline. Run AFTER the turn; the check
    accepts the staged copy only if its sha256 matches the bridge's."""
    blob = bridge_blob(baseline)
    if len(blob) <= MANIFEST_B64_CAP:
        return []
    return [_wrap(_COMMON + _STAGE % {"token": token, "first": i == 0,
                                      "data": blob[o:o + PAGE_CHARS], "tag": PAGE_TAG})
            for i, o in enumerate(range(0, len(blob), PAGE_CHARS))]


def check_command(token: str, baseline: dict | None) -> str:
    """The post-turn check + revert, against the bridge's baseline (inline when small, else the
    copy `stage_commands` wrote, pinned by sha256). Resumable: re-running it after a timeout
    continues and reports everything reverted across both runs."""
    blob = bridge_blob(baseline)
    inline = blob if len(blob) <= MANIFEST_B64_CAP else ""
    return _wrap(_COMMON + _CHECK % {"token": token, "inline": inline,
                                     "blob_sha": _sha(blob) if blob else "",
                                     "cap": REPORT_CAP, "tag": CHECK_TAG})


def report_command(token: str) -> str:
    """After a check that never reported (timed out twice): what its journal says it reverted.
    Status `partial`. Removes the snapshot directory."""
    return _wrap(_COMMON + _REPORT % {"token": token, "cap": REPORT_CAP, "tag": CHECK_TAG})


def frame(tag: str, obj: dict) -> str:
    """The reply framing the scripts' `emit` writes (also used by tests to fake a reply)."""
    return f"{tag}-BEGIN:{_pack(obj)}:{tag}-END"


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


def _decode_blob(blob: str) -> dict | None:
    try:
        st = json.loads(zlib.decompress(base64.b64decode(blob, validate=True)))
    except (ValueError, zlib.error):
        return None
    return st if isinstance(st, dict) else None


def parse_snapshot(out: str | None) -> dict | None:
    """The baseline, or None when the snapshot did not run cleanly (no attribution possible).
    A complete baseline carries `manifest`, `refs` and `blob` (the encoded state the check will
    trust); one still to be paged has `manifest` None."""
    obj = _parse(SNAP_TAG, out)
    if not obj or obj.get("status") != "ok" or not obj.get("token"):
        return None
    blob = obj.get("blob")
    if blob is None:
        if "blob_sha" in obj:
            obj["manifest"] = None
        return obj
    st = _decode_blob(blob)
    if st is None or st.get("token") != obj["token"] or \
            (obj.get("blob_sha") and _sha(blob) != obj["blob_sha"]):
        return None
    for k in ("manifest", "nocopy", "refs", "head", "branch"):
        obj[k] = st.get(k)
    return obj


Runner = Callable[[str], Awaitable[str | None]]


async def take_baseline(run: Runner, token: str) -> str | None:
    """Run the snapshot on the plan turn's worker and, if it is too large to come back in one
    reply, page it back - all BEFORE the turn starts, so the bridge holds the complete baseline the
    turn cannot touch. `run(command)` executes on that worker and returns its output. Returns a
    snapshot frame for `parse_snapshot` (with the blob inline), or a frame with an `error` status
    saying why there is no baseline."""
    out = await run(snapshot_command(token))
    obj = _parse(SNAP_TAG, out)
    if not obj or obj.get("status") != "ok" or obj.get("token") != token:
        why = (obj or {}).get("why") or "the snapshot did not report"
        return frame(SNAP_TAG, {"status": "error", "why": why})
    if obj.get("blob") is None:
        total = int(obj.get("blob_len") or 0)
        if total > MAX_BASELINE_CHARS:
            return frame(SNAP_TAG, {"status": "error",
                                    "why": f"baseline too large to carry ({total} chars)"})
        parts: list[str] = []
        off = 0
        while off < total:
            page = _parse(PAGE_TAG, await run(page_command(token, off)))
            if not page or page.get("status") != "ok" or page.get("off") != off \
                    or not page.get("data"):
                return frame(SNAP_TAG, {"status": "error", "why": "baseline paging failed"})
            parts.append(page["data"])
            off += len(page["data"])
        blob = "".join(parts)
        if _sha(blob) != obj.get("blob_sha"):
            return frame(SNAP_TAG, {"status": "error", "why": "paged baseline hash mismatch"})
        obj["blob"] = blob
    return frame(SNAP_TAG, obj)


def parse_check(out: str | None) -> dict | None:
    return _parse(CHECK_TAG, out)


def parse_page(out: str | None) -> dict | None:
    """A page / staging reply."""
    return _parse(PAGE_TAG, out)
