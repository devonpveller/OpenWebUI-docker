"""ao-attempt - the turn boundary between a coding attempt and the next plan turn.

Operator model (2026-10-11): when planning begins, the previous attempt's work is either KEPT as
completed plan steps or DISCARDED as a failed attempt - never left as clutter, never silently
wiped. Tests are evidence about the anchor, not the goal: each plan step declares the anchor
acceptance item it serves (top-down, at planning), the worker chooses the checks that evidence
its step, and the planner judges whether that evidence demonstrates the item.

This module is the PURE half: the shell commands the bridge sends to the worker, the parsers for
what comes back, the plan/verdict/checks grammars, the test-fact classifier and the bounded note.
The orchestrator owns the I/O.

WHY THE COMMANDS LOOK LIKE THIS (ao-planrevert's two failed rounds, findings/ao-planrevert.md):

* Every git argv goes through the worker's git proxy (little-coder/git-proxy/git_proxy.py), which
  denies plumbing (write-tree, read-tree, hash-object, update-ref ...), every `-c`/`-C` global and
  `branch -D`/`-f`. Only add / commit / branch / reset (never --hard) / checkout / clean / read-only
  commands are used here; `GIT_ARGVS` lists them and a test runs each through the real classify().
* /check output is NOT a byte stream: open-terminal returns one output entry per PTY write and
  `parse_exec_output` joins them with an extra "\\n" - a line can be split anywhere, and blank
  lines appear. So every value travels inside an explicit record, `@@TAG value TAG@@`, emitted
  with printf and no newline of its own, and the parser deletes EVERY CR and LF before reading
  records. A split anywhere then rejoins losslessly. A record value never contains CR/LF (git
  C-quotes such paths in non -z output).
* /check keeps only the last 64 KB and runs `redact_secrets`: every command opens with a BEGIN
  record and closes with an END record, and output without both is untrusted (None) - never read
  as "nothing there". Lists are capped in the shell (head) so the END record survives.
* Nothing the plan turn can reach is trusted as evidence about the plan turn: the attempt is a
  COMMIT plus a branch ref (the proxy cannot delete or force-move a branch), not a scratch copy.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

WS = "/workspace"
BEGIN = "@@B ao B@@"
END = "@@E ao E@@"

#: Every git argv shape the bridge sends for ao-attempt (placeholders: SHA, REF, PATH, MSG).
#: Pinned by tests/test_attempts.py, which runs each through the real git_proxy.classify().
GIT_ARGVS: list[list[str]] = [
    ["rev-parse", "HEAD"],
    ["rev-parse", "--abbrev-ref", "HEAD"],
    ["rev-parse", "--verify", "-q", "refs/heads/REF"],
    ["status", "--porcelain", "--untracked-files=all"],
    ["merge-base", "--is-ancestor", "SHA", "HEAD"],
    ["config", "--get", "user.email"],
    ["add", "-A"],
    ["commit", "-q", "-m", "MSG"],
    ["commit", "-q", "--allow-empty", "-m", "MSG"],
    ["branch", "REF", "HEAD"],
    ["diff", "--no-renames", "--numstat", "SHA", "SHA"],
    ["diff", "--no-renames", "--name-only", "--diff-filter=A", "SHA", "SHA"],
    ["diff", "--quiet", "SHA", "HEAD"],
    ["checkout", "-q", "--detach", "SHA"],
    ["checkout", "-q", "BRANCH"],
    ["checkout", "-q", "-b", "BRANCH", "SHA"],
    ["checkout", "-q", "--", "."],
    ["clean", "-fdq"],
    ["reset", "-q", "SHA"],
    ["--literal-pathspecs", "add", "-A", "--", "PATH"],
]

_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_REF_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

MAX_CHECKS = 6
MAX_CHECK_CHARS = 300
CHECK_TIMEOUT_S = 300
CHECK_KILL_S = 10
ID_MAX = 160            # test ids are cut to this length (in the shell, before any comparison)
MAX_LISTED = 200        # test facts LISTED per check; every count is exact whatever is listed
MAX_FIXED_LISTED = 30
MAX_NUMSTAT = 200
NOTE_MAX = 600
NOTES_CAP = 6           # active discard notes per effort; older ones are deactivated
DISCARDS_ESCALATE = 3


_BRANCH_RE = re.compile(r"^(?!-)[A-Za-z0-9._/-]{1,200}$")


def is_sha(s: str | None) -> bool:
    return bool(s and _SHA_RE.match(s))


def is_branch(b: str | None) -> bool:
    """A branch name the bridge will put in a command (or "HEAD" = detached)."""
    return bool(b and _BRANCH_RE.match(b) and ".." not in b)


_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/\[\]=,+-]")


def safe_id(tid: str) -> str:
    """A test id as it may appear in a fact, a prompt or a note: id characters only (anything
    else - `$`, `(`, `|`, backticks, quotes - becomes `?`), bounded."""
    return _ID_UNSAFE.sub("?", tid or "")[:ID_MAX]


def ref_name(effort_id: str, n: int) -> str:
    """The per-effort attempt ref. A branch (the proxy allows creating one but not deleting or
    force-moving it), outside the delivery branch, never pushed (delivery pushes its branch by
    name)."""
    safe = _REF_SAFE.sub("-", effort_id).strip("-.") or "effort"
    return f"ao-attempts/{safe[:60]}/a{int(n)}"


def _rec(tag: str, expr: str) -> str:
    """A printf that emits one record. `expr` is a shell word (already quoted/substituted)."""
    return f"printf '@@{tag} %s {tag}@@' {expr}"


def _wrap(body: list[str]) -> str:
    return " ; ".join([f"cd {WS} 2>/dev/null || exit 90", f"printf '{BEGIN}'", *body,
                       f"printf '{END}'"])


# ── parsing /check output with open-terminal's real framing ──────────────────────────────────
_RECORD_RE = re.compile(r"@@([A-Z][A-Z0-9]*) (.*?) \1@@")


def records(out: str | None) -> list[tuple[str, str]] | None:
    """All records in one /check output, in order, or None when the output is untrusted (no
    BEGIN, or no END after it - truncated, or the command died). CR and LF are deleted first:
    open-terminal's per-write entries are joined with an extra newline that can land anywhere."""
    if not out:
        return None
    flat = out.replace("\r", "").replace("\n", "")
    b = flat.find(BEGIN)
    e = flat.rfind(END)
    if b < 0 or e < 0 or e < b:
        return None
    body = flat[b + len(BEGIN):e]
    return [(m.group(1), m.group(2)) for m in _RECORD_RE.finditer(body)]


def first(recs: list[tuple[str, str]], tag: str) -> str | None:
    for t, v in recs:
        if t == tag:
            return v
    return None


def _int(v: str | None) -> int | None:
    try:
        return int((v or "").strip())
    except ValueError:
        return None


# ── commands ──────────────────────────────────────────────────────────────────────────────────
def probe_cmd(start_sha: str | None = None) -> str:
    body = [
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("BR", '"$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
    ]
    if is_sha(start_sha):
        body.append(f"if git merge-base --is-ancestor {start_sha} HEAD >/dev/null 2>&1; "
                    f"then {_rec('ANC', '1')}; else {_rec('ANC', '0')}; fi")
    return _wrap(body)


@dataclass
class Probe:
    head: str
    branch: str
    dirty: int
    start_is_ancestor: bool | None = None


def parse_probe(out: str | None) -> Probe | None:
    recs = records(out)
    if recs is None:
        return None
    head = (first(recs, "HEAD") or "").strip()
    dirty = _int(first(recs, "DIRTY"))
    if not is_sha(head) or dirty is None:
        return None
    anc = first(recs, "ANC")
    return Probe(head=head, branch=(first(recs, "BR") or "").strip(), dirty=dirty,
                 start_is_ancestor=None if anc is None else anc.strip() == "1")


_IDENTITY = ("git config --get user.email >/dev/null 2>&1 || export GIT_AUTHOR_NAME=agent-org "
             "GIT_AUTHOR_EMAIL=agent-org@invalid GIT_COMMITTER_NAME=agent-org "
             "GIT_COMMITTER_EMAIL=agent-org@invalid")


def park_cmd(effort_id: str, n: int, *, commit: bool) -> str:
    """Commit the dirty tree (when `commit`) and point the attempt ref at HEAD."""
    ref = ref_name(effort_id, n)
    msg = shlex.quote(f"ao-attempt: parked attempt {int(n)} of {effort_id}")
    body = [_IDENTITY]
    if commit:
        body.append(f"git add -A >/dev/null 2>&1 && git commit -q -m {msg} >/dev/null 2>&1; "
                    + _rec("RC", '"$?"'))
    body += [
        f"git branch {ref} HEAD >/dev/null 2>&1; " + _rec("BRC", '"$?"'),
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("REF", f'"$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
    ]
    return _wrap(body)


@dataclass
class Park:
    head: str
    ref_sha: str
    dirty: int
    commit_rc: int | None


def parse_park(out: str | None) -> Park | None:
    recs = records(out)
    if recs is None:
        return None
    head = (first(recs, "HEAD") or "").strip()
    dirty = _int(first(recs, "DIRTY"))
    if not is_sha(head) or dirty is None:
        return None
    return Park(head=head, ref_sha=(first(recs, "REF") or "").strip(), dirty=dirty,
                commit_rc=_int(first(recs, "RC")))


def restore_snippet(branch: str, tip: str) -> str:
    """Put the workspace back on `branch` at `tip`, clean - whatever a worker's check (or a plan
    turn) did to HEAD, the branch or the tree. "HEAD" = a detached workspace. Proxy-legal:
    checkout (incl. -b to re-create a deleted branch), mixed reset, clean."""
    clean = "git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1"
    if branch == "HEAD":
        move = f"git checkout -q --detach {tip} >/dev/null 2>&1"
    else:
        b = shlex.quote(branch)
        move = (f"{{ git checkout -q {b} >/dev/null 2>&1 || git checkout -q -b {b} {tip} "
                f">/dev/null 2>&1; }}; git reset -q {tip} >/dev/null 2>&1")
    return f"cd {WS} ; {clean}; {move}; {clean}"


def _state(ref: str | None = None) -> list[str]:
    out = [
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("BR", '"$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
    ]
    if ref:
        out.append(_rec("REF", f'"$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)"'))
    return out


@dataclass
class State:
    head: str
    branch: str
    dirty: int
    ref: str | None = None

    def at(self, branch: str, tip: str, ref_sha: str | None = None) -> bool:
        """Exactly where the bridge left it: the branch (name AND tip), clean, the ref intact."""
        return (self.head == tip and self.branch == branch and self.dirty == 0
                and (ref_sha is None or self.ref == ref_sha))


def parse_state(recs: list[tuple[str, str]] | None) -> State | None:
    if recs is None:
        return None
    head = (first(recs, "HEAD") or "").strip()
    dirty = _int(first(recs, "DIRTY"))
    if not is_sha(head) or dirty is None:
        return None
    ref = first(recs, "REF")
    return State(head=head, branch=(first(recs, "BR") or "").strip(), dirty=dirty,
                 ref=None if ref is None else ref.strip())


def restore_cmd(branch: str, tip: str, ref: str | None = None) -> str:
    return _wrap([restore_snippet(branch, tip), *_state(ref)])


def numstat_cmd(a: str, b: str) -> str:
    d = f"git diff --no-renames --numstat {a} {b} 2>/dev/null"
    return _wrap([
        f"{d} | head -{MAX_NUMSTAT} | while IFS= read -r l; do {_rec('N', chr(34) + '$l' + chr(34))}; done",
        _rec("NT", f'"$({d} | wc -l)"'),
        f"git diff --no-renames --name-only --diff-filter=A {a} {b} 2>/dev/null | head -{MAX_NUMSTAT}"
        f" | while IFS= read -r l; do {_rec('A', chr(34) + '$l' + chr(34))}; done",
    ])


_NUMSTAT_RE = re.compile(r"^([0-9]+|-)\s+([0-9]+|-)\s+(.+)$")


def parse_numstat(out: str | None) -> dict | None:
    """{"files": [{"path", "added", "deleted"}], "n_files", "added", "deleted"} - paths and counts
    only, never content. None when untrusted."""
    recs = records(out)
    if recs is None:
        return None
    files = []
    for tag, v in recs:
        if tag != "N":
            continue
        m = _NUMSTAT_RE.match(v)
        if not m:
            return None
        a = 0 if m.group(1) == "-" else int(m.group(1))
        d = 0 if m.group(2) == "-" else int(m.group(2))
        files.append({"path": m.group(3).strip(), "added": a, "deleted": d})
    total = _int(first(recs, "NT"))
    if total is None:
        return None
    return {"files": files, "n_files": total, "added": sum(f["added"] for f in files),
            "deleted": sum(f["deleted"] for f in files), "listed": len(files),
            "new_paths": [v.strip() for t, v in recs if t == "A"]}


_TEST_GREP = (r"grep -aoE '(PASSED|FAILED|ERROR|XPASS|XFAIL) [^ ]+::[^ ]+|"
              r"[^ ]+::[^ ]+ (PASSED|FAILED|ERROR|XPASS|XFAIL)'")
# status + id per test, failing wins, ids made safe and cut IN THE SHELL (both runs alike, so the
# comparison is exact): F <id> / P <id>
_SPLIT_AWK = ("awk '{ if ($1 ~ /::/) { id=$1; st=$2 } else { id=$2; st=$1 }; "
              "gsub(/[^][A-Za-z0-9_.:\\/=,+-]/, \"?\", id); id=substr(id, 1, %d); "
              "if (st==\"FAILED\"||st==\"ERROR\"||st==\"XPASS\") f[id]=1; else p[id]=1 } "
              "END { for (i in f) print \"F \" i; for (i in p) if (!(i in f)) print \"P \" i }'")


def check_cmd(cmd: str, *, start: str | None, branch: str, park: str, ref: str) -> str:
    """ONE worker check, end AND start, in ONE /check (R2: a /check per check, so one big suite
    cannot push another's facts out of the 64 KB tail):

    1. run it at the parked commit; 2. `checkout --detach <start>` (verified) and run it there;
    3. RESTORE the branch (name and tip) and the tree, whatever the check did (R4);
    4. compare the two runs IN THE SHELL - every failing id at the end gets the start's status
       (P passed / F failed / A absent = new / U start not measurable) - and emit exact COUNTS
       plus at most MAX_LISTED ids (regressions first), so a regression is never lost to a cap.
    Output stays far below 64 KB: ids <= ID_MAX chars, <= MAX_LISTED + MAX_FIXED_LISTED of them."""
    q = shlex.quote(cmd)
    run = (f"( cd {WS} && NO_COLOR=1 PY_COLORS=0 TERM=dumb timeout -k {CHECK_KILL_S} "
           f"{CHECK_TIMEOUT_S} bash -c {q} ) </dev/null 2>&1")
    split = _SPLIT_AWK % ID_MAX
    body = [
        'd=$(mktemp -d); : > "$d/a.t"; : > "$d/b.t"; : > "$d/j"; : > "$d/x"',
        f'{run} > "$d/a.out"; rca=$?',
        f'{_TEST_GREP} "$d/a.out" | {split} | LC_ALL=C sort > "$d/a.t"',
        _rec("RCA", '"$rca"'),
    ]
    if is_sha(start):
        body += [
            f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1; "
            f"git checkout -q --detach {start} >/dev/null 2>&1; "
            f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1",
            f'if [ "$(git rev-parse HEAD 2>/dev/null)" = "{start}" ]; then '
            f'{run} > "$d/b.out"; rcb=$?; '
            f'{_TEST_GREP} "$d/b.out" | {split} | LC_ALL=C sort > "$d/b.t"; '
            + _rec("RCB", '"$rcb"') + "; fi",
        ]
    body += [
        restore_snippet(branch, park),
        # join: for every failing id at the end, its status at the start
        'if [ -s "$d/b.t" ]; then awk \'NR==FNR { s[substr($0,3)]=substr($0,1,1); next } '
        '$1=="F" { id=substr($0,3); print ((id in s) ? s[id] : "A") " " id }\' "$d/b.t" "$d/a.t" '
        '> "$d/j"; awk \'NR==FNR { if ($1=="F") f[substr($0,3)]=1; next } '
        '$1=="P" && (substr($0,3) in f) { print substr($0,3) }\' "$d/b.t" "$d/a.t" > "$d/x"; '
        'else awk \'$1=="F" { print "U " substr($0,3) }\' "$d/a.t" > "$d/j"; fi',
        *[_rec(tag, f'"$(grep -c {pat} "$d/{f}" 2>/dev/null)"') for tag, pat, f in (
            ("NFA", "'^F '", "a.t"), ("NPA", "'^P '", "a.t"), ("NFB", "'^F '", "b.t"),
            ("NPB", "'^P '", "b.t"), ("NREG", "'^P '", "j"), ("NNEW", "'^A '", "j"),
            ("NSTILL", "'^F '", "j"), ("NUNK", "'^U '", "j"))],
        _rec("NFIX", '"$(wc -l < "$d/x")"'),
        f'{{ grep \'^P \' "$d/j"; grep \'^A \' "$d/j"; grep \'^F \' "$d/j"; grep \'^U \' "$d/j"; }} '
        f'| head -{MAX_LISTED} | while IFS= read -r l; do {_rec("J", chr(34) + "$l" + chr(34))}; done',
        f'head -{MAX_FIXED_LISTED} "$d/x" | while IFS= read -r l; do '
        f'{_rec("X", chr(34) + "$l" + chr(34))}; done',
        'rm -rf "$d"',
        *_state(ref),
    ]
    return _wrap(body)


@dataclass
class CheckRun:
    rca: int
    rcb: int | None                 # None: the start could not be measured
    counts: dict
    listed: list                    # [(status, id)]: P regression, A new, F still, U unknown
    fixed: list
    state: State


def parse_check(out: str | None) -> CheckRun | None:
    """None when the output is untrusted (no BEGIN/END: cut, or the command died) or incomplete."""
    recs = records(out)
    st = parse_state(recs)
    if recs is None or st is None:
        return None
    rca = _int(first(recs, "RCA"))
    if rca is None:
        return None
    counts = {}
    for tag in ("NFA", "NPA", "NFB", "NPB", "NREG", "NNEW", "NSTILL", "NUNK", "NFIX"):
        v = _int(first(recs, tag))
        if v is None:
            return None
        counts[tag] = v
    listed = []
    for tag, v in recs:
        if tag == "J":
            parts = v.split(" ", 1)
            if len(parts) == 2 and parts[0] in ("P", "A", "F", "U"):
                listed.append((parts[0], safe_id(parts[1])))
    fixed = [safe_id(v) for t, v in recs if t == "X"]
    return CheckRun(rca=rca, rcb=_int(first(recs, "RCB")), counts=counts, listed=listed,
                    fixed=fixed, state=st)


def reset_mixed_cmd(sha: str) -> str:
    """Move the current branch to `sha`, keeping the work tree (a plan-turn COMMIT becomes dirty
    files the unchanged plan-turn write check then reverts). `reset` without --hard: proxy-legal."""
    return _wrap([f"git reset -q {sha} >/dev/null 2>&1; " + _rec("RC", '"$?"'),
                  _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"')])


def keep_cmd(effort_id: str, parked_from: str | None, park_sha: str,
             commits: list[dict]) -> str:
    """Turn the parked attempt into one commit per completed step.

    `parked_from` is the HEAD the park commit was made on (None when the bridge made no park
    commit: the worker had committed everything itself, and its commits are never rewritten).
    `commits` is [{"step", "anchor", "paths"}] in order; the LAST one also takes every change not
    attributed to an earlier step, so nothing is dropped. Verified: the resulting tree must equal
    the parked tree (`diff --quiet park HEAD`) and be clean."""
    body = [_IDENTITY]
    if parked_from:
        body.append(f"git reset -q {parked_from} >/dev/null 2>&1; " + _rec("RRC", '"$?"'))
        for i, c in enumerate(commits):
            msg = shlex.quote(f"ao-attempt {effort_id}: step {int(c['step'])} "
                              f"(anchor {c['anchor']})")
            if i < len(commits) - 1:
                adds = "; ".join(
                    f"git --literal-pathspecs add -A -- {shlex.quote(p)} >/dev/null 2>&1"
                    for p in c.get("paths") or []) or ":"
            else:
                adds = "git add -A >/dev/null 2>&1"
            body.append(f"{adds}; git commit -q --allow-empty -m {msg} >/dev/null 2>&1; "
                        + _rec(f"C{i}", '"$?"'))
    body += [
        f"git diff --quiet {park_sha} HEAD >/dev/null 2>&1; " + _rec("SAME", '"$?"'),
        *_state(),
    ]
    return _wrap(body)


def discard_cmd(start_sha: str, ref: str, park_sha: str, branch: str) -> str:
    """Tree back to the attempt's start; the attempt stays on `ref`. Mixed reset + checkout +
    clean (the proxy allows `reset --hard` only to a tag). Untracked files the attempt created
    are on the ref (the park committed them), so `clean` loses nothing. The reset runs ONLY when
    the ref still points at the parked commit and the tree is clean - a discard never destroys
    the attempt."""
    guard = (f'[ "$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)" = "{park_sha}" ] && '
             f'[ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = {shlex.quote(branch)} ] && '
             f'[ "$(git rev-parse HEAD 2>/dev/null)" = "{park_sha}" ] && '
             f'[ "$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)" -eq 0 ]')
    return _wrap([
        _rec("REF", f'"$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)"'),
        f"if {guard}; then git reset -q {start_sha} >/dev/null 2>&1; "
        f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1; "
        + _rec("RC", '"$?"') + "; else " + _rec("RC", "refused") + "; fi",
        *_state(ref),
    ])


# ── grammars: plan declaration, worker checks, verdict ───────────────────────────────────────
_ITEMS_HDR = re.compile(r"^\s*\**ANCHOR ITEMS\**\s*:\s*(.*)$", re.I)
_ITEM_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**(A\d{1,3})\**\s*[:.)\-]\s*(.+?)\s*$", re.I)
_SECTION = re.compile(r"^\s*\**(UNDERSTANDING|ALREADY DONE|PLAN|WON'?T DO|RISKS|VERDICT|"
                      r"ANCHOR ITEMS|CHECKS)\**\s*:", re.I)
_PLAN_HDR = re.compile(r"^\s*\**PLAN\**\s*:\s*(.*)$", re.I)
_STEP_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:step\s*)?(\d{1,3})\s*[.):]\s*(.+?)\s*$", re.I)
_ANCHOR_TAG = re.compile(r"\[\s*anchor\s*:\s*(A\d{1,3})\s*\]", re.I)
_NOTHING = re.compile(r"\bnothing remains\b|^\s*(?:none|nothing)\.?\s*$", re.I)


def _section_lines(text: str, hdr: re.Pattern, *, last: bool = False) -> list[str] | None:
    lines = (text or "").splitlines()
    idx = [i for i, ln in enumerate(lines) if hdr.match(ln)]
    for i in (idx[-1:] if last else idx[:1]):
        m = hdr.match(lines[i])
        out = [m.group(1)] if m.group(1).strip() else []
        for nxt in lines[i + 1:]:
            if _SECTION.match(nxt):
                break
            out.append(nxt)
        return out
    return None


@dataclass
class PlanDecl:
    items: dict = field(default_factory=dict)       # {"A1": "text"}
    steps: list = field(default_factory=list)       # [{"n", "text", "anchor"}]
    problems: list = field(default_factory=list)    # step numbers / reasons - ids only
    nothing_remains: bool = False


def parse_plan(text: str, known_items: dict | None = None) -> PlanDecl:
    """Read ANCHOR ITEMS and the PLAN's numbered steps. Every step must end with
    `[anchor: Ax]` naming an item declared now or stored from an earlier plan. A plan whose PLAN
    says 'nothing remains' needs no steps."""
    d = PlanDecl()
    for ln in _section_lines(text, _ITEMS_HDR) or []:
        m = _ITEM_LINE.match(ln)
        if m:
            d.items[m.group(1).upper()] = m.group(2)[:240]
    known = {k.upper() for k in (known_items or {})} | set(d.items)
    plan = _section_lines(text, _PLAN_HDR)
    if plan is None:
        d.problems.append("no PLAN section")
        return d
    for ln in plan:
        m = _STEP_LINE.match(ln)
        if not m:
            continue
        n, body = int(m.group(1)), m.group(2)
        tag = _ANCHOR_TAG.search(body)
        anchor = tag.group(1).upper() if tag else ""
        clean = _ANCHOR_TAG.sub("", body).strip()
        d.steps.append({"n": n, "text": clean[:300], "anchor": anchor})
        if not anchor:
            d.problems.append(f"step {n}: no [anchor: Ax]")
        elif anchor not in known:
            d.problems.append(f"step {n}: anchor {anchor} is not a declared item")
    if not d.steps:
        if any(_NOTHING.search(ln) for ln in plan):
            d.nothing_remains = True
        else:
            d.problems.append("PLAN has no numbered steps")
    return d


_CHECKS_HDR = re.compile(r"^\s*\**CHECKS\**\s*:\s*(.*)$", re.I)
_CHECK_LINE = re.compile(r"^\s*(?:[-*]\s*)?step\s*(\d{1,3})\s*[:\-]\s*(.+?)\s*$", re.I)


def parse_checks(text: str) -> list[dict]:
    """The worker's own checks from its reply's CHECKS section: `step <n>: <command>` per line.
    Single-line commands only, capped in count and length. Backticks around a command are
    dropped."""
    out: list[dict] = []
    # the LAST CHECKS section wins (a worker may quote the instruction earlier in its reply)
    lines = _section_lines(text, _CHECKS_HDR, last=True)
    if lines is None:
        return out
    for ln in lines:
        m = _CHECK_LINE.match(ln)
        if not m:
            continue
        cmd = m.group(2).strip().strip("`").strip()
        if not cmd or len(cmd) > MAX_CHECK_CHARS or "\x00" in cmd:
            continue
        out.append({"step": int(m.group(1)), "cmd": cmd})
        if len(out) >= MAX_CHECKS:
            break
    return out


_VERDICT_LINE = re.compile(r"^\s*\**VERDICT\**\s*:\s*(.*?)\s*$", re.I | re.M)
_KEEP = re.compile(r"^KEEP\b\s*(?:steps?\s*)?([0-9][0-9 ,\-and]*?)\s*(?:[.:;].*|\s-\s.*)?$", re.I)
_DISCARD = re.compile(
    r"^DISCARD\b\s*(?:step\s*)?(\d{1,3})\b\s*(?:\(?\s*(deviation|no-solution)\s*\)?)?\s*[:\-]\s*(.+)$",
    re.I)


@dataclass
class Verdict:
    kind: str                       # keep | discard
    steps: list = field(default_factory=list)
    klass: str = ""                 # discard: deviation | no-solution | "" (bridge decides)
    reason: str = ""


def parse_verdict(text: str, step_nos: set[int]) -> Verdict | None:
    """Exactly one VERDICT line, naming steps of the PARKED attempt's plan. Anything else -
    missing, two different verdicts, an unknown step, a DISCARD without a reason - is None:
    unparseable, and an unparseable verdict never discards."""
    found = [m.group(1) for m in _VERDICT_LINE.finditer(text or "")]
    found = [f for f in found if f]
    if len({f.strip().lower() for f in found}) != 1:
        return None
    v = found[0].strip().strip("*").strip()
    m = _KEEP.match(v)
    if m:
        nums: set[int] = set()
        for part in re.split(r"\s*(?:,|\band\b|\s)\s*", m.group(1).strip()):
            if not part:
                continue
            r = re.fullmatch(r"(\d{1,3})\s*-\s*(\d{1,3})|(\d{1,3})", part)
            if not r:
                return None
            if r.group(3):
                nums.add(int(r.group(3)))
            else:
                lo, hi = int(r.group(1)), int(r.group(2))
                if lo > hi or hi - lo > 50:
                    return None
                nums.update(range(lo, hi + 1))
        if not nums or any(x not in step_nos for x in nums):
            return None
        return Verdict("keep", steps=sorted(nums))
    m = _DISCARD.match(v)
    if m:
        n = int(m.group(1))
        reason = m.group(3).strip()
        if n not in step_nos or not reason:
            return None
        return Verdict("discard", steps=[n], klass=(m.group(2) or "").lower(), reason=reason)
    return None


# ── test facts ────────────────────────────────────────────────────────────────────────────────
def classify(checks: list[dict], runs: list, steps: list[dict]) -> dict:
    """Deterministic facts from the per-check runs (`runs[k]` = CheckRun or None = untrusted).

    regression      - a test that PASSED at the attempt's start and fails now
    new_not_passing - a failing test whose id is ABSENT from the start's results (R1: whatever
                      file it lives in - a test added to an existing file is new). A RENAMED test
                      is a new id: it reads as new_not_passing, and its old id is no longer failing
                      or passing at all (visible only as the passing-count change).
    still_failing   - failing at the start and now
    unknown_before  - failing now, the start not measurable (no start, the start's run untrusted,
                      or it listed no test results at all): never guessed into a regression
    fixed           - failing at the start, not failing now
    A whole check whose exit code went 0 -> non-zero with no failing test listed at all is a
    check-level regression (failing tests explain a failing exit code themselves). Counts are exact (computed in the shell over every test); ids are listed up to a
    cap and `truncated` says so. Each fact carries the anchor item of the step the worker
    attributed the check to."""
    anchor_of = {int(s["n"]): (s.get("anchor") or "") for s in steps}
    keys = ("regression", "new_not_passing", "still_failing", "unknown_before", "fixed")
    facts: dict = {k: [] for k in keys}
    facts.update({f"n_{k}": 0 for k in keys})
    facts.update({"available": bool(checks), "truncated": False, "untrusted_checks": [],
                  "passing": []})
    label = {"P": "regression", "A": "new_not_passing", "F": "still_failing", "U": "unknown_before"}
    for k, c in enumerate(checks[:MAX_CHECKS]):
        step = int(c["step"])
        base = {"check": f"c{k + 1}", "step": step, "anchor": anchor_of.get(step, "")}
        r = runs[k] if k < len(runs) else None
        if r is None:
            facts["untrusted_checks"].append(base["check"])
            continue
        cn = r.counts
        before_ok = r.rcb is not None and (cn["NFB"] + cn["NPB"]) > 0
        n = {"regression": cn["NREG"], "new_not_passing": cn["NNEW"],
             "still_failing": cn["NSTILL"], "unknown_before": cn["NUNK"], "fixed": cn["NFIX"]}
        listed = list(r.listed)
        if r.rcb is not None and not before_ok:
            # the start ran but listed no results: a "new" there means "unknown", not new
            listed = [("U", t) if s == "A" else (s, t) for s, t in listed]
            n["unknown_before"] += n["new_not_passing"]
            n["new_not_passing"] = 0
        for s_, tid in listed:
            facts[label[s_]].append({**base, "test": tid})
        for tid in r.fixed:
            facts["fixed"].append({**base, "test": tid})
        if r.rca != 0 and r.rcb == 0 and cn["NFA"] == 0:
            # the check fails now, passed at the start, and no failing TEST explains it
            facts["regression"].append({**base, "test": f"check {base['check']} (exit 0 -> {r.rca})"})
            n["regression"] += 1
        elif r.rca != 0 and r.rcb is None and cn["NFA"] == 0:
            facts["unknown_before"].append({**base, "test": f"check {base['check']} (exit {r.rca})"})
            n["unknown_before"] += 1
        elif r.rca == 0 and r.rcb not in (0, None) and cn["NFB"] == 0:
            facts["fixed"].append({**base, "test": f"check {base['check']} (exit {r.rcb} -> 0)"})
            n["fixed"] += 1
        for key in keys:
            facts[f"n_{key}"] += n[key]
        listed_n = sum(1 for f in facts["regression"] + facts["new_not_passing"]
                       + facts["still_failing"] + facts["unknown_before"] if f["check"] == base["check"])
        if listed_n < n["regression"] + n["new_not_passing"] + n["still_failing"] + n["unknown_before"]:
            facts["truncated"] = True
        facts["passing"].append({**base, "start": cn["NPB"] if r.rcb is not None else None,
                                 "now": cn["NPA"]})
    return facts


def facts_text(facts: dict, diff: dict | None, steps: list[dict], checks: list[dict]) -> str:
    """The FACTS block for the planner (bounded). Includes the worker's check commands - the
    planner must judge whether they evidence the anchor item - but this text is a prompt, never
    an audit payload or a note. Test ids are already safe_id()'d."""
    lines = []
    if diff:
        shown = ", ".join(f"{f['path']} (+{f['added']}/-{f['deleted']})" for f in diff["files"][:15])
        more = f", and {diff['n_files'] - min(15, diff['listed'])} more" if diff["n_files"] > 15 else ""
        lines.append(f"- diff vs the attempt's start: {diff['n_files']} file(s), "
                     f"+{diff['added']}/-{diff['deleted']} lines: {shown}{more}")
    else:
        lines.append("- diff summary: unavailable")
    if not checks:
        lines.append("- the worker reported NO checks for this attempt (its turn may have been "
                     "stopped) - there is no test evidence either way")
        return "\n".join(lines)
    for k, c in enumerate(checks[:MAX_CHECKS]):
        anchor = next((s.get("anchor") for s in steps if int(s["n"]) == int(c["step"])), "")
        lines.append(f"- worker check c{k + 1} for step {c['step']} (anchor {anchor or '?'}): "
                     f"`{c['cmd'][:160]}`")
    for key, label in (("regression", "REGRESSIONS (passed at the start, fail now)"),
                       ("new_not_passing", "new (absent at the start), not yet passing"),
                       ("still_failing", "failing before and after"),
                       ("unknown_before", "failing now, start not measurable"),
                       ("fixed", "failing at the start, not now")):
        items = facts.get(key) or []
        total = facts.get(f"n_{key}", len(items))
        if total:
            shown = "; ".join(f"{f['test']} [step {f['step']}, anchor {f['anchor'] or '?'}]"
                              for f in items[:8])
            more = f"; {total - min(8, len(items))} more not shown" if total > min(8, len(items)) else ""
            lines.append(f"- {label}: {total}: {shown}{more}")
    for p in facts.get("passing") or []:
        if p["start"] is not None and p["now"] < p["start"]:
            lines.append(f"- {p['check']}: passing tests {p['start']} at the start, {p['now']} now "
                         "(tests removed or renamed?)")
    if facts.get("untrusted_checks"):
        lines.append("- NO FACTS for " + ", ".join(facts["untrusted_checks"]) + ": its output "
                     "was cut or incomplete - regressions there are UNKNOWN, not absent")
    return "\n".join(lines)


def fact_ids(facts: dict) -> dict:
    """The audit-safe projection: test ids + anchor ids + exact counts."""
    out = {"truncated": bool(facts.get("truncated")),
           "untrusted_checks": list(facts.get("untrusted_checks") or [])}
    for key in ("regression", "new_not_passing", "still_failing", "unknown_before", "fixed"):
        items = facts.get(key) or []
        out[key] = [{"test": safe_id(f["test"]), "anchor": f["anchor"]} for f in items[:20]]
        out[f"n_{key}"] = int(facts.get(f"n_{key}", len(items)))
    return out


# ── the bounded note + learning record ────────────────────────────────────────────────────────
_CODE_SPAN = re.compile(r"```.*?```|`[^`]*`", re.S)
_SHELLISH = re.compile(r"[$;|&<>{}\\]")


def scrub(text: str, limit: int) -> str:
    """Planner prose for a note: no code spans, no shell metacharacters, one line, bounded. The
    threat model is 'no file content or command text in notes'; prose survives, code does not."""
    t = _CODE_SPAN.sub("", text or "")
    t = _SHELLISH.sub(" ", t)
    t = " ".join(t.split())
    return t[:limit].rstrip()


def discard_note(*, step: int, anchor: str, anchor_text: str, attempt_no: int, klass: str,
                 diff: dict | None, facts: dict, reason: str) -> str:
    """'What not to do', framed against the anchor item - never 'test X failed' alone."""
    tried = "no diff summary"
    if diff:
        paths = ", ".join(f["path"] for f in diff["files"][:5])
        tried = (f"{diff['n_files']} file(s) changed, +{diff['added']}/-{diff['deleted']}"
                 + (f" ({paths}{', ...' if diff['n_files'] > 5 else ''})" if paths else ""))
    ev = []
    for key, label in (("regression", "regressed"), ("new_not_passing", "new not passing")):
        items = facts.get(key) or []
        if items:
            ev.append(f"{label}: " + ", ".join(safe_id(f["test"])[:80] for f in items[:3])
                      + (" ..." if len(items) > 3 else ""))
    evidence = "; ".join(ev) or "no test evidence for the item"
    item = f"{anchor} ({scrub(anchor_text, 120)})" if anchor_text else (anchor or "the goal")
    note = (f"Step {step} toward anchor item {item}, attempt {attempt_no}, DISCARDED "
            f"[{klass}]. Tried: {tried}. Evidence: {evidence}. Why it did not advance "
            f"{anchor or 'the item'}: {scrub(reason, 220) or 'not stated'}. Do not repeat this "
            f"approach; find a different way to satisfy {anchor or 'the item'}.")
    return note[:NOTE_MAX]


def learning_record(*, effort_id: str, attempt_id: str, ref: str, anchor: str, anchor_text: str,
                    note: str, facts: dict, klass: str, created_at: str) -> dict:
    """The discard as a learning record in the harness's v1 shape
    (scripts/agent-harness/fixtures/learning-record/learning-record.schema.json): `red` is a
    string, `source_ref` holds only schema fields (session_id = the effort, branch_id = the
    attempt ref), the attempt id is outcome evidence. `producer` is "agent-org", which the v1
    enum (harness|job-tree) does not list yet - an operator decision, not changed here."""
    ids = fact_ids(facts)
    red = (f"anchor item {anchor}: {ids['n_regression']} regression(s), "
           f"{ids['n_new_not_passing']} new not passing, {ids['n_still_failing']} still failing, "
           f"{ids['n_unknown_before']} unknown"
           + ("; regressed: " + ", ".join(f["test"][:80] for f in ids["regression"][:3])
              if ids["regression"] else ""))
    return {
        "schema_version": 1,
        "fidelity": "iterative",
        "producer": "agent-org",
        "source_ref": {"session_id": effort_id, "branch_id": ref},
        "domains": ["agent-org", "attempt-discard"],
        "steer": f"anchor item {anchor}: {scrub(anchor_text, 200)}" if anchor_text
                 else f"anchor item {anchor or 'goal'}",
        "red": red,
        "outcome": {"kind": "gap-remains", "summary": note,
                    "evidence": [f"attempt {attempt_id}", f"ref {ref}"]},
        "deviation": klass,
        "created_at": created_at,
    }


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).split())


def canonical_items(old: dict, new: dict) -> tuple[dict, dict]:
    """Merge a re-plan's ANCHOR ITEMS into the stored ones. A stored id is IMMUTABLE (its text is
    kept); a new id whose text matches a stored item is an ALIAS of it, so renumbering an item
    on a re-plan cannot reset its discard count. Returns (merged items, {new id: canonical id})."""
    merged = dict(old)
    by_text = {_norm(v): k for k, v in old.items()}
    alias = {}
    for k, v in new.items():
        if k in old:
            alias[k] = k
            continue
        same = by_text.get(_norm(v))
        if same:
            alias[k] = same
        else:
            merged[k] = v
            by_text[_norm(v)] = k
            alias[k] = k
    return merged, alias


def kept_conflicts(steps: list[dict], kept: list[int], paths: list[str]) -> list[str]:
    """Changed paths named ONLY by steps the KEEP did not name: committing them under a kept
    step would mis-attribute another step's (unfinished) work."""
    by_n = {int(s["n"]): s for s in steps}
    out = []
    for p in paths:
        named = [n for n, s in by_n.items() if _names(s.get("text", ""), p)]
        if named and not any(n in kept for n in named):
            out.append(p)
    return out


def classify_discards(klasses: list[str], had_regression: list[bool]) -> str:
    """Three discards of one step: deviation-from-anchor vs no-solution-identified. The planner's
    own class per discard when it gave one; otherwise a regression reads as deviation."""
    votes = [k if k in ("deviation", "no-solution") else
             ("deviation" if r else "no-solution") for k, r in zip(klasses, had_regression)]
    dev = votes.count("deviation")
    sol = votes.count("no-solution")
    if dev == sol:
        return "deviation-from-anchor" if votes and votes[-1] == "deviation" \
            else "no-solution-identified"
    return "deviation-from-anchor" if dev > sol else "no-solution-identified"


def _names(text: str, path: str) -> bool:
    """Does a step's text name this path (the full path, or its file name as a whole word)?"""
    if not path:
        return False
    for needle in {path, path.rsplit("/", 1)[-1]}:
        if re.search(r"(?<![\w./-])" + re.escape(needle) + r"(?![\w-])", text or ""):
            return True
    return False


def attribute_paths(steps: list[dict], kept: list[int], paths: list[str]) -> list[dict]:
    """[{"step", "anchor", "paths"}] for the kept steps, in order: a changed path goes to the
    first kept step whose text names it; the last kept step takes the rest (keep_cmd adds -A)."""
    by_n = {int(s["n"]): s for s in steps}
    left = list(paths)
    out = []
    for i, n in enumerate(kept):
        s = by_n.get(n) or {"n": n, "text": "", "anchor": ""}
        mine = [] if i == len(kept) - 1 else [p for p in left if _names(s.get("text", ""), p)]
        left = [p for p in left if p not in mine]
        out.append({"step": n, "anchor": s.get("anchor") or "", "paths": mine})
    return out
