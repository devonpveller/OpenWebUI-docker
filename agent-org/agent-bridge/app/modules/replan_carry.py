"""ao-replancarry - carry a stopped coding turn's uncommitted work across the re-plan.

When the flail guard or the loop guard stops a coding turn, the orchestrator forks a fresh session
and forces a plan turn (`_flail_replan`), and the execution turn after the plan gate can land on
ANY free worker. The stopped turn's edits, though, exist only as uncommitted files in the
workspace of the worker that ran it. C7e 0.3 ao-queue r1 (2026-10-15): the coding turn wrote its
repro test on worker-1, the plan turn and then the execution turn ran on worker-2 in fresh clones
at base, and the work was simply not there.

The carry closes that the way normal turn commits travel. Right after the stop, ON THE STOPPED
TURN'S WORKER (pinned, and only while its workspace is still focused for the effort), the bridge
runs `carry_command`:
  * nothing changed and nothing unpushed -> no commit, nothing pushed (`status: clean`);
  * otherwise switch to the effort branch `agent/<effort>` at the current commit (the working tree
    is kept), `git add -A` + `git commit` a WIP commit, and `git push origin
    HEAD:refs/heads/agent/<effort>` - plain, non-force, through the worker's git = its git-proxy.
Every later turn's focus syncs to that branch (F33, `router.wake(checkout_branch=)`), so the plan
turn and the execution turn start from the carried work on whichever worker they get.

What it does not do, on purpose:
  * never targets anything but `agent/...` (the bridge refuses to build the command otherwise, and
    the worker's git-proxy denies a push to main/master on its own);
  * never moves a local effort branch that is NOT contained in the current commit (`checkout -B`
    would drop its commits): it stops and reports `diverged` with the tree untouched;
  * `git add -A` honours .gitignore exactly as the publish step's `git add -A` does - nothing a
    normal turn commit would leave out is added; no `--no-verify`, no `-c`, no force.

Channel (ao-plancheck's lessons): the script ships base64-encoded inside `python3 -c`, only
proxy-whitelisted git runs (rev-parse, status, rev-list, merge-base, diff-tree, checkout, add,
commit, push), and the reply is ONE base64(zlib(json)) token between `AO-CARRY-BEGIN:` and
`:AO-CARRY-END`, so open-terminal's chunking/CRLF, the daemon's newline join, `redact_secrets` and
the 64 KB tail cannot corrupt it.
"""

from __future__ import annotations

import re

from .plan_turn_guard import _COMMON, _parse, _wrap

CARRY_TAG = "AO-CARRY"
PROTECTED = {"main", "master"}
# agent/<segment>[/<segment>...]; each segment starts alphanumeric (no "..", no leading "-" or ".")
_BRANCH_RE = re.compile(r"agent(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+")
_FILES_CAP = 200

_CARRY = r'''
import re as _re
BRANCH = %(branch)r
SUBJ = %(subj)r
BODY = %(body)r
os.environ.update({"GIT_AUTHOR_NAME": %(name)r, "GIT_AUTHOR_EMAIL": %(email)r,
                   "GIT_COMMITTER_NAME": %(name)r, "GIT_COMMITTER_EMAIL": %(email)r})

def gitx(*a):
    p = subprocess.run(GIT + list(a), cwd=WS, capture_output=True)
    return (p.returncode, p.stdout.decode("utf-8", "surrogateescape"),
            p.stderr.decode("utf-8", "surrogateescape"))

def scrub(t):
    t = _re.sub(r"://[^/@\s]+@", "://***@", t or "")
    t = _re.sub(r"(gh[pousr]_|github_pat_)[A-Za-z0-9_]+", r"\1***", t)
    return t.strip()[-300:]

def out(**kw):
    emit(%(tag)r, dict(kw, branch=BRANCH))
    sys.exit(0)

if git("rev-parse", "--absolute-git-dir")[0] != 0:
    out(status="error", why="not a git workspace")
pre_head, pre_branch = head(), branch()
paths = dirty()
if paths is None:
    out(status="error", why="git status failed")
unpushed = None
if pre_head:
    rc, o = git("rev-list", "--count", "HEAD", "--not", "--remotes=origin")
    if rc == 0 and o.strip().isdigit():
        unpushed = int(o.strip())
if not paths and (unpushed == 0 or not pre_head):
    out(status="clean", from_branch=pre_branch, head_before=pre_head, unpushed_before=unpushed)
if pre_branch != BRANCH:
    if git("rev-parse", "--verify", "-q", "refs/heads/" + BRANCH)[0] == 0 \
            and git("merge-base", "--is-ancestor", "refs/heads/" + BRANCH, "HEAD")[0] != 0:
        out(status="error", from_branch=pre_branch, head_before=pre_head,
            why="a local %%s exists that the checked-out %%s does not contain (diverged) - "
                "nothing was committed or moved" %% (BRANCH, pre_branch))
    rc, _o, e = gitx("checkout", "-q", "-B", BRANCH)
    if rc != 0:
        out(status="error", from_branch=pre_branch, head_before=pre_head,
            why="could not switch to %%s: %%s" %% (BRANCH, scrub(e)))
commit = ""
if paths:
    rc, _o, e = gitx("add", "-A")
    if rc != 0:
        out(status="error", from_branch=pre_branch, head_before=pre_head,
            why="git add failed: " + scrub(e))
    rc, _o, e = gitx("commit", "-q", "-m", SUBJ, "-m", BODY)
    if rc == 0:
        commit = head()
    elif unpushed == 0 or not pre_head:
        out(status="error", from_branch=pre_branch, head_before=pre_head,
            why="git commit failed: " + scrub(e))
files = []
if commit:
    if pre_head:
        rc, o = git("diff-tree", "-r", "-z", "--no-commit-id", "--name-only", "--no-renames",
                    pre_head, commit)
        files = sorted(f for f in o.split("\0") if f) if rc == 0 else list(paths)
    else:
        files = list(paths)
rc, _o, e = gitx("push", "-q", "origin", "HEAD:refs/heads/" + BRANCH)
pushed = rc == 0
now = head()
rc2, rh = git("rev-parse", "--verify", "-q", "refs/remotes/origin/" + BRANCH)
left = dirty()
out(status="ok", from_branch=pre_branch, head_before=pre_head, commit=commit, head=now,
    files=files[:%(cap)d], n_files=len(files), unpushed_before=unpushed, pushed=pushed,
    push_detail="" if pushed else scrub(e),
    remote_head=rh.strip() if rc2 == 0 else "",
    left_dirty=len(left) if left is not None else None)
'''


def valid_branch(branch: str) -> bool:
    """An effort branch the carry may write to: `agent/...`, never a protected name."""
    return (bool(_BRANCH_RE.fullmatch(branch or "")) and ".." not in branch
            and not branch.endswith((".lock", ".")) and branch.split("/")[-1] not in PROTECTED)


def carry_command(branch: str, *, subject: str, body: str, author: tuple[str, str]) -> str:
    """The carry, to run on the stopped turn's worker via /check. Raises ValueError for a branch
    that is not an `agent/...` effort branch (defence in depth; the proxy guards main too)."""
    if not valid_branch(branch):
        raise ValueError(f"refusing to carry work to {branch!r}: not an agent/* effort branch")
    name, email = author
    return _wrap(_COMMON + _CARRY % {"branch": branch, "subj": subject, "body": body,
                                     "name": name, "email": email, "tag": CARRY_TAG,
                                     "cap": _FILES_CAP})


def parse_carry(out: str | None) -> dict | None:
    """The carry's report, or None when no framed reply came back."""
    return _parse(CARRY_TAG, out)
