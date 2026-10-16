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

ao-preflight2 (the round-2 testers' findings):
  * Order: the git dir's own rules (`info/exclude`, `info/attributes`, recorded in the baseline)
    and then tracked `.gitignore`/`.gitattributes` are restored FIRST and status is read again -
    a turn that rewrote the rules would otherwise expose the earlier turn's ignored files as new
    (and remove them) or hide its own. A file staged by the turn that the restored rules ignore is
    unstaged, never removed. The earlier turn's files are restored before any HEAD restore, so a
    check cut off later has already put them back.
  * A path that was UNTRACKED before the turn and is in the index after it (the turn's `git add`,
    or its commit undone by the soft reset) is unstaged (`rm --cached`, batched): the next
    delivery-branch sync's `checkout -f -B` deletes a staged file that is not in its tree.
  * The snapshot dir is made a directory again if the turn replaced it with a file or a link.
  * The journal is named by a nonce the bridge makes after the turn, so a journal the turn
    planted is never read back as reverts.

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
import hmac
import json
import os
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

def git_dir():
    rc, out = git("rev-parse", "--absolute-git-dir")
    return out.strip() if rc == 0 and out.strip() else None

def state_dir():
    gd = git_dir()
    return os.path.join(gd, "ao-plan-snapshot") if gd else None

def drop_any(f):
    """Remove whatever is at `f` (a directory tree, a file, a symlink); never follows a link."""
    if os.path.isdir(f) and not os.path.islink(f):
        shutil.rmtree(f, ignore_errors=True)
    elif os.path.lexists(f):
        os.remove(f)

def ensure_dir(d):
    """`d` as a real directory: a file or symlink the turn left in its place is removed first."""
    if os.path.lexists(d) and (os.path.islink(d) or not os.path.isdir(d)):
        os.remove(d)
    os.makedirs(d, exist_ok=True)

# Ignore/attribute rules that live in the git dir (never tracked, so never in `status`).
INFO_FILES = ("info/exclude", "info/attributes")
INFO_CAP = 1 << 20

def read_info(gd):
    """{name: base64 content | None (absent)}; a name too large or not a regular file is left
    out (unknown: not restored)."""
    out = {}
    for n in INFO_FILES:
        f = os.path.join(gd, n)
        if not os.path.lexists(f):
            out[n] = None
        elif os.path.isfile(f) and not os.path.islink(f) and os.path.getsize(f) <= INFO_CAP:
            with open(f, "rb") as fh:
                out[n] = base64.b64encode(fh.read()).decode("ascii")
    return out

# Tracked files whose content changes what `status` reports (ignore rules) or how checkout
# writes files (attributes: filters, eol). Restored FIRST, then status is computed again.
CONTROL_NAMES = (".gitignore", ".gitattributes")

def is_control(p):
    return p.rsplit("/", 1)[-1] in CONTROL_NAMES

# `.git/config` keys that change what `status` sees or what checkout/status RUN: ignore and
# attribute files, filter/diff/merge drivers, fsmonitor, includes. The baseline records them; the
# check puts them back first (only them - the rest of the config, e.g. the origin credential a
# focus re-bakes, is left alone). Written to the file directly, as the turn itself can.
CFG_SECTIONS = ("filter", "diff", "merge", "include", "includeif")
CFG_CORE_KEYS = ("excludesfile", "attributesfile", "fsmonitor")

def cfg_split(text):
    """(the rest of the config, the relevant part as canonical text)."""
    kept, blocks, core = [], [], []
    sec, block = None, None
    for ln in text.splitlines(True):
        s = ln.strip()
        if s.startswith("["):
            name = s[1:].split("]")[0].strip().split()[0] if s[1:].split("]")[0].strip() else ""
            sec = name.split(".")[0].lower()
            if sec in CFG_SECTIONS:
                block = [ln if ln.endswith("\n") else ln + "\n"]
                blocks.append(block)
                continue
            block = None
            kept.append(ln)
            continue
        if block is not None:
            block.append(ln if ln.endswith("\n") else ln + "\n")
            continue
        if sec == "core" and s.split("=")[0].strip().lower() in CFG_CORE_KEYS:
            core.append(s)
            continue
        kept.append(ln)
    rel = "".join("".join(b) for b in blocks)
    if core:
        rel = "[core]\n" + "".join("\t" + c + "\n" for c in core) + rel
    return "".join(kept), rel

def read_cfg(gd):
    try:
        with open(os.path.join(gd, "config"), encoding="utf-8", errors="surrogateescape") as fh:
            return fh.read()
    except Exception:
        return None

# Ignored files of the earlier turn (build output, local config) are snapshotted too, within caps
# (recorded when skipped), so a plan turn that edits or deletes one directly can be undone.
IGN_MAX_FILES = 2000
IGN_FILE_CAP = 4 * 1024 * 1024
IGN_TOTAL_CAP = 64 * 1024 * 1024

def unstage(paths):
    """`rm --cached -f` (never touches the file on disk) in argv chunks; a chunk git refuses is
    retried one path at a time. Returns (unstaged, failed)."""
    ok, bad = [], []
    for ch in chunks(paths):
        rc, _o = git("--literal-pathspecs", "rm", "-q", "--cached", "-f", "--ignore-unmatch",
                     "--", *ch)
        if rc == 0:
            ok.extend(ch)
            continue
        for p in ch:
            rc1, _o = git("--literal-pathspecs", "rm", "-q", "--cached", "-f", "--ignore-unmatch",
                          "--", p)
            (ok if rc1 == 0 else bad).append(p)
    return ok, bad

def copies_pending(S):
    """Copies in the snapshot whose content is NOT what is on disk now: earlier-turn work that
    exists only in the snapshot. Such a snapshot is never deleted."""
    n = 0
    for sub in ("files", "ign"):
        root = os.path.join(S, sub)
        if not os.path.isdir(root) or os.path.islink(root):
            continue
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                src = os.path.join(dp, fn)
                p = os.path.relpath(src, root).replace(os.sep, "/")
                try:
                    if os.path.islink(src) or digest(p) != fdigest(src):
                        n += 1
                except Exception:
                    n += 1
    return n

def finish_snapshot(S, tag):
    """Delete the snapshot - unless it holds earlier-turn work that is not back on disk: then it
    is kept under a name the next snapshot does not reuse, and the reply says where."""
    if not S or not os.path.lexists(S):
        return "", 0
    try:
        pend = copies_pending(S) if os.path.isdir(S) and not os.path.islink(S) else 0
    except Exception:
        pend = 1
    if pend:
        kept_as = S + ".kept-" + tag
        try:
            drop_any(kept_as)
            os.rename(S, kept_as)
            return kept_as, pend
        except Exception:
            return S, pend
    drop_any(S)
    return "", 0

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
drop_any(S)
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
# ignored files (not in `status`): hashed and copied within the caps; the rest only counted
ign, ign_skipped, ign_bytes = {}, 0, 0
rc, out = git("ls-files", "-z", "-o", "-i", "--exclude-standard")
for p in (sorted(x for x in out.split("\0") if x) if rc == 0 else []):
    f = full(p)
    try:
        if os.path.islink(f):
            ign[p] = "L:" + os.readlink(f)
            continue
        size = os.path.getsize(f)
        if len(ign) >= IGN_MAX_FILES or size > IGN_FILE_CAP or ign_bytes + size > IGN_TOTAL_CAP:
            ign_skipped += 1
            continue
        dst = os.path.join(S, "ign", p)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(f, dst)
        ign[p] = fdigest(dst)
        ign_bytes += size
    except Exception:
        ign_skipped += 1
# `tracked`: dirty paths that were in the index or HEAD before the turn (every other manifest
# path was untracked - the check unstages one the turn `git add`ed); `info`/`cfg`: the git dir's
# own ignore/attribute rules and the config keys that steer status/checkout, restored first.
gd = os.path.dirname(S)
cfg = read_cfg(gd)
state = {"token": TOKEN, "head": head(), "branch": branch(), "manifest": man, "nocopy": nocopy,
         "refs": rf, "tracked": sorted(p for p, xy in st_xy.items() if xy != "??"),
         "info": read_info(gd), "cfg": None if cfg is None else cfg_split(cfg)[1],
         "ignored": ign, "ignored_skipped": ign_skipped}
blob = pack(state)
with open(os.path.join(S, "state.b64"), "w") as fh:
    fh.write(blob)
out = {"status": "ok", "token": TOKEN, "head": state["head"], "branch": state["branch"],
       "count": len(man), "ignored_count": len(ign), "ignored_skipped": ign_skipped,
       "blob_len": len(blob), "blob_sha": hashlib.sha256(blob.encode("ascii")).hexdigest()}
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
ensure_dir(S)
with open(os.path.join(S, "bridge-" + TOKEN + ".b64"), "w" if FIRST else "a") as fh:
    fh.write(DATA)
emit(%(tag)r, {"status": "ok", "staged": len(DATA)})
'''

_CHECK = r'''
TOKEN = %(token)r
NONCE = %(nonce)r
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
# paths that were UNTRACKED before the turn (known only from a baseline that lists `tracked`)
pre_untracked = (set(man) - set(base.get("tracked") or [])) if "tracked" in base else set()
pre_info = base.get("info") if isinstance(base.get("info"), dict) else {}
pre_cfg = base.get("cfg") if isinstance(base.get("cfg"), str) else None
pre_ign = base.get("ignored") if isinstance(base.get("ignored"), dict) else {}
if S is None:
    emit(%(tag)r, {"status": "error", "why": "not a git workspace after the turn"})
    sys.exit(0)
# the turn may have replaced the snapshot dir with a file or a link: make it a directory again
# (the copies it held are then gone; an earlier edit the turn changed is reported unrestored)
ensure_dir(S)
# the journal is named by a nonce the bridge made AFTER the turn, so nothing the turn wrote can
# be read back as a revert (a resumed run of this same command finds it)
JP = os.path.join(S, "journal-" + NONCE + ".jsonl")
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

gd = os.path.dirname(S)

# ---- 1. git-free first: nothing below can hang before this is done -------------------------
# (a) the config keys that steer status/checkout (a filter the turn configured would hang the
# very checkouts that restore files) and (b) the git dir's own ignore/attribute rules
if pre_cfg is not None:
    cur_cfg = read_cfg(gd)
    if cur_cfg is not None:
        rest, rel = cfg_split(cur_cfg)
        if rel != pre_cfg:
            try:
                tmp = os.path.join(gd, "config.ao-plancheck")
                with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as fh:
                    fh.write(rest + ("" if not rest or rest.endswith("\n") else "\n") + pre_cfg)
                os.replace(tmp, os.path.join(gd, "config"))
                rec("r", "(.git/config)", "filter/diff/merge/include/excludes/attributes/fsmonitor "
                                          "settings changed by the plan turn - restored")
            except Exception:
                rec("u", "(.git/config)", "settings changed by the plan turn - restore failed")
now_info = read_info(gd)
for n, pre in sorted(pre_info.items()):
    if n not in INFO_FILES or now_info.get(n, "?") == pre:
        continue
    f = os.path.join(gd, n)
    try:
        drop(f)
        if pre is not None:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "wb") as fh:
                fh.write(base64.b64decode(pre))
        rec("r", "(.git/" + n + ")", "changed by the plan turn - restored")
    except Exception:
        rec("u", "(.git/" + n + ")", "changed by the plan turn - restore failed")
JF.flush()

def put_back(p, pre, sub, what):
    """Put an earlier-turn path back to its pre-turn state from the snapshot (no git).
    Returns "r", "u" or "" (already as it was)."""
    f = full(p)
    try:
        if digest(p) == pre:
            return ""
        if pre == "-":
            drop(f)
            rec("r", p, "deleted before the plan turn, re-created by it - removed again")
            return "r"
        if pre == "DIR":
            rec("u", p, "a directory entry changed during the plan turn - left as is")
            return "u"
        if pre.startswith("L:"):
            drop(f)
            os.makedirs(os.path.dirname(f) or WS, exist_ok=True)
            os.symlink(pre[2:], f)
            rec("r", p, what + " - restored to its state before the plan turn")
            return "r"
        src = os.path.join(S, sub, p)
        if os.path.isfile(src) and not os.path.islink(src) and fdigest(src) == pre:
            drop(f)
            os.makedirs(os.path.dirname(f) or WS, exist_ok=True)
            shutil.copy2(src, f)
            rec("r", p, what + " - restored to its state before the plan turn")
            return "r"
        rec("u", p, what + " - no intact copy to restore from; left as is")
        return "u"
    except Exception:
        rec("u", p, what + " - restore failed")
        return "u"

# (c) the earlier turn's files - its dirty paths and its ignored files - back from the snapshot,
# BEFORE any branch switch or HEAD restore: a check cut off in either has put them back already
early = set()
for p in sorted(man):
    if put_back(p, man[p], "files", "edited before the plan turn and changed by it"):
        early.add(p)
for p in sorted(pre_ign):
    put_back(p, pre_ign[p], "ign", "ignored file of an earlier turn changed by the plan turn")
JF.flush()

# ---- 2. branch and commits --------------------------------------------------------------------
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

def classify(now, only=None):
    kept, fresh, from_copy = [], [], []
    for p in sorted(set(now) | set(man)):
        if only is not None and not only(p):
            continue
        pre = man.get(p)
        nowd = digest(p) if p in now else None
        if pre == nowd:
            if pre is not None:
                kept.append(p)
            continue
        (fresh if pre is None else from_copy).append(p)
    return kept, fresh, from_copy

def revert(now, fresh, from_copy):
    removed = []
    # the earlier turn's work first (again: a branch switch can have changed it after step 1)
    for p in from_copy:
        if put_back(p, man[p], "files", "edited before the plan turn and changed by it") == "r" \
                and man[p] == "-":
            removed.append(p)
    JF.flush()
    # paths clean before the turn: in HEAD -> restore from HEAD; else -> remove. A path that is
    # ONLY untracked (`??`) is in neither the index nor HEAD, so it costs no git call at all;
    # HEAD membership of the rest is one `ls-tree` per argv chunk; `rm --cached` only for staged
    # ones.
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
    staged = [p for p in to_remove if any(x[0] not in ("?", " ")
                                          for x in now.get(p, "??").split(","))]
    # staged by the turn but IGNORED (by the rules as they were before it): never removed - it is
    # the earlier turn's (build output, local config) that the turn's `add -A` swept in after it
    # rewrote the rules; unstaged, and the file kept
    ign = set()
    for ch in chunks(staged):
        rc, out = git("--literal-pathspecs", "ls-files", "-z", "-c", "-i", "--exclude-standard",
                      "--", *ch)
        if rc == 0:
            ign.update(x for x in out.split("\0") if x)
    to_remove = [p for p in to_remove if p not in ign]
    ok, bad = unstage(sorted(ign))
    for p in ok:
        rec("s", p, "ignored, staged by the plan turn - unstaged, file kept")
    for p in bad:
        rec("u", p, "ignored, staged by the plan turn - unstaging failed")
    for p in to_remove:
        drop(full(p))
        removed.append(p)
        rec("r", p, "created by the plan turn - removed")
    JF.flush()
    prune(removed)
    _ok, bad = unstage([p for p in staged if p not in ign])
    for p in bad:
        rec("u", p, "created and staged by the plan turn - removed, but unstaging failed")
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
    prune(removed)

def status_or_stop():
    st = status_xy()
    if st is None:
        JF.close()
        kept_as, _pend = finish_snapshot(S, NONCE[:12])
        emit(%(tag)r, {"status": "error", "why": "git status failed after the turn",
                       "snapshot_kept": kept_as})
        sys.exit(0)
    return st

# ---- 3. tracked ignore/attribute files, then status AGAIN, then everything else --------------
now = status_or_stop()
_k, cfresh, ccopy = classify(now, is_control)
if cfresh or ccopy:
    revert(now, cfresh, ccopy)
    now = status_or_stop()
ctl_done = set(cfresh) | set(ccopy) | early
kept_all, fresh, from_copy = classify(now)
kept_all = [p for p in kept_all if p not in ctl_done]
kept_n, kept = len(kept_all), kept_all[:50]
revert(now, fresh, from_copy)
for p in sorted(pre_ign):           # a checkout above cannot have touched them; cheap to confirm
    put_back(p, pre_ign[p], "ign", "ignored file of an earlier turn changed by the plan turn")

# ---- 4. untracked before the turn, in the index now (the turn's `git add`, or its commit undone
# by the soft reset): unstaged, so it is untracked again - a later `checkout -f` of the effort
# branch deletes a staged file that is not in the branch's tree. The file on disk is not touched.
# `rm --cached -f`: without -f git refuses a WHOLE chunk when one path's staged content differs
# from both the file and HEAD (a file the turn edited and added, put back from the snapshot).
restage = sorted(p for p in pre_untracked
                 if any(x[0] not in ("?", " ") for x in now.get(p, "??").split(",")))
ok, bad = unstage(restage)
if ok:
    st3 = status_xy() or {}
    still = set(p for p in ok if any(x[0] not in ("?", " ")
                                     for x in st3.get(p, "??").split(",")))
    bad += sorted(still)
    ok = [p for p in ok if p not in still]
for p in ok:
    rec("s", p, "untracked before the plan turn, staged by it - unstaged, file kept")
for p in bad:
    rec("u", p, "untracked before the plan turn, staged by it - unstaging failed")
JF.flush()

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

def last_by_path(allr, kind):
    """One entry per path: its LAST verdict (a path put back, then changed by the branch switch
    and put back again, is one revert; one that failed and then succeeded is not unrestored)."""
    last = {}
    for e in allr:
        if e.get("k") in ("r", "u", "s") and "path" in e:
            last[e["path"]] = e
    return [{"path": e["path"], "why": e["why"]} for e in last.values() if e.get("k") == kind]

allr = prior + entries
rev, unr = last_by_path(allr, "r"), last_by_path(allr, "u")
uns = [e["path"] for e in last_by_path(allr, "s")]
heads = [e.get("sha", "") for e in allr if e.get("path") == "(HEAD)" and e.get("k") == "r"]
kept_as, pend = finish_snapshot(S, NONCE[:12])
emit(%(tag)r, {"status": "ok", "reverted": rev[:CAPN], "reverted_count": len(rev),
               "unrestored": unr[:CAPN], "unrestored_count": len(unr),
               "unstaged": uns[:CAPN], "unstaged_count": len(uns),
               "head_reset": head_reset or (heads[0] if heads else ""),
               "kept": kept, "kept_count": kept_n, "refs_left": refs_left[:CAPN],
               "resumed": bool(prior), "snapshot_kept": kept_as, "snapshot_pending": pend,
               "ignored_skipped": int(base.get("ignored_skipped") or 0)})
'''

_REPORT = r'''
TOKEN = %(token)r
NONCE = %(nonce)r
CAPN = %(cap)d
S = state_dir()
JP = os.path.join(S, "journal-" + NONCE + ".jsonl") if S and NONCE else ""
allr = []
if JP and os.path.isfile(JP):
    with open(JP, encoding="utf-8", errors="surrogateescape") as fh:
        for ln in fh:
            try:
                allr.append(json.loads(ln))
            except Exception:
                pass
last = {}
for e in allr:
    if e.get("k") in ("r", "u", "s") and "path" in e:
        last[e["path"]] = e
rev = [{"path": e["path"], "why": e["why"]} for e in last.values() if e.get("k") == "r"]
unr = [{"path": e["path"], "why": e["why"]} for e in last.values() if e.get("k") == "u"]
uns = [e["path"] for e in last.values() if e.get("k") == "s"]
left = status_xy()
# never delete the only copy of earlier-turn work that is not back on disk
kept_as, pend = finish_snapshot(S, NONCE[:12]) if S else ("", 0)
emit(%(tag)r, {"status": "partial", "reverted": rev[:CAPN], "reverted_count": len(rev),
               "unrestored": unr[:CAPN], "unrestored_count": len(unr),
               "unstaged": uns[:CAPN], "unstaged_count": len(uns), "head_reset": "",
               "kept": [], "kept_count": 0, "refs_left": [],
               "dirty_after": None if left is None else len(left),
               "snapshot_kept": kept_as, "snapshot_pending": pend})
'''


def new_token() -> str:
    return uuid.uuid4().hex


# The journal nonce, when the caller passes none: keyed by a secret that exists only in this
# bridge process, so the same token gives the same nonce here (a re-built check command, or the
# report, finds the journal) and nothing on the worker can compute it. A bridge restart makes a
# new key: the check then starts a fresh journal instead of resuming (it reverts what is left).
_NONCE_KEY = os.urandom(32)


def journal_nonce(token: str) -> str:
    return hmac.new(_NONCE_KEY, token.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


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
                      "nocopy": baseline.get("nocopy") or [], "refs": baseline.get("refs") or {},
                      **{k: baseline[k] for k in ("tracked", "info") if k in baseline}})
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


def check_command(token: str, baseline: dict | None, nonce: str | None = None) -> str:
    """The post-turn check + revert, against the bridge's baseline (inline when small, else the
    copy `stage_commands` wrote, pinned by sha256). Resumable: re-running THIS command after a
    timeout continues and reports everything reverted across both runs. `nonce` names the
    journal: one the turn cannot know (default `journal_nonce(token)`, a bridge-secret HMAC), the
    same one `report_command` reads - so no journal the turn planted is ever read."""
    blob = bridge_blob(baseline)
    inline = blob if len(blob) <= MANIFEST_B64_CAP else ""
    return _wrap(_COMMON + _CHECK % {"token": token, "nonce": nonce or journal_nonce(token),
                                     "inline": inline,
                                     "blob_sha": _sha(blob) if blob else "",
                                     "cap": REPORT_CAP, "tag": CHECK_TAG})


def report_command(token: str, nonce: str | None = None) -> str:
    """After a check that never reported (timed out twice): what its journal (the one named by
    the check's `nonce`) says it reverted. Status `partial`. Removes the snapshot directory."""
    return _wrap(_COMMON + _REPORT % {"token": token, "nonce": nonce or journal_nonce(token),
                                      "cap": REPORT_CAP,
                                      "tag": CHECK_TAG})


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
    for k in ("tracked", "info"):
        if k in st:
            obj[k] = st[k]
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
