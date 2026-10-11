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
    ["checkout", "-q", "REF"],
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
MAX_TESTS = 300
MAX_NUMSTAT = 200
NOTE_MAX = 600
NOTES_CAP = 6           # active discard notes per effort; older ones are deactivated
DISCARDS_ESCALATE = 3


def is_sha(s: str | None) -> bool:
    return bool(s and _SHA_RE.match(s))


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


def checks_cmd(checks: list[dict]) -> str:
    """Run each worker-chosen check at the CURRENT HEAD, then put the tree back exactly (the
    attempt is committed, so checkout + clean restores it; ignored files are left alone). Per
    check: one RC record and up to MAX_TESTS test-result records (pytest -v / -rA lines)."""
    body = []
    for k, c in enumerate(checks[:MAX_CHECKS]):
        q = shlex.quote(c["cmd"])
        body.append(
            f"o=$(mktemp); ( cd {WS} && NO_COLOR=1 PY_COLORS=0 TERM=dumb timeout {CHECK_TIMEOUT_S} "
            f"bash -c {q} ) >\"$o\" 2>&1 </dev/null; rc=$?; "
            f"{_TEST_GREP} \"$o\" | head -{MAX_TESTS} | while IFS= read -r l; do "
            f"{_rec(f'T{k}', chr(34) + '$l' + chr(34))}; done; "
            f"{_rec(f'RC{k}', chr(34) + '$rc' + chr(34))}; rm -f \"$o\"")
    body.append(f"cd {WS} && git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1")
    return _wrap(body)


def parse_checks_run(out: str | None, n: int) -> dict | None:
    """{check index: {"rc": int, "tests": {test id: PASSED|FAILED|...}}} or None if untrusted or
    any check's RC record is missing."""
    recs = records(out)
    if recs is None:
        return None
    res: dict[int, dict] = {}
    for k in range(min(n, MAX_CHECKS)):
        rc = _int(first(recs, f"RC{k}"))
        if rc is None:
            return None
        tests: dict[str, str] = {}
        for tag, v in recs:
            if tag != f"T{k}":
                continue
            parts = v.split()
            if len(parts) != 2:
                continue
            a, b = parts
            if "::" in a:
                tid, st = a, b
            else:
                st, tid = a, b
            st = "PASSED" if st in ("PASSED", "XFAIL") else (
                "FAILED" if st in ("FAILED", "ERROR", "XPASS") else st)
            # FAILED wins over PASSED for the same id (a -v line and a summary line).
            if tests.get(tid) != "FAILED":
                tests[tid] = st
        res[k] = {"rc": rc, "tests": tests}
    return res


def checkout_cmd(target: str) -> str:
    """Move HEAD to `target` (a sha: detached; a branch name: that branch) with a clean tree."""
    if is_sha(target):
        mv = f"git checkout -q --detach {target} >/dev/null 2>&1"
    else:
        mv = f"git checkout -q {shlex.quote(target)} >/dev/null 2>&1"
    return _wrap([
        mv + "; " + _rec("RC", '"$?"'),
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
    ])


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
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
    ]
    return _wrap(body)


def discard_cmd(start_sha: str, ref: str, park_sha: str) -> str:
    """Tree back to the attempt's start; the attempt stays on `ref`. Mixed reset + checkout +
    clean (the proxy allows `reset --hard` only to a tag). Untracked files the attempt created
    are on the ref (the park committed them), so `clean` loses nothing. The reset runs ONLY when
    the ref still points at the parked commit and the tree is clean - a discard never destroys
    the attempt."""
    guard = (f'[ "$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)" = "{park_sha}" ] && '
             f'[ "$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)" -eq 0 ]')
    return _wrap([
        _rec("REF", f'"$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)"'),
        f"if {guard}; then git reset -q {start_sha} >/dev/null 2>&1; "
        f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1; "
        + _rec("RC", '"$?"') + "; else " + _rec("RC", "refused") + "; fi",
        _rec("HEAD", '"$(git rev-parse HEAD 2>/dev/null)"'),
        _rec("DIRTY", '"$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)"'),
        _rec("REF2", f'"$(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null)"'),
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
_KEEP = re.compile(r"^KEEP\b\s*(?:steps?\s*)?([0-9][0-9 ,and]*)\s*(?:[.:;\-].*)?$", re.I)
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
        nums = sorted({int(x) for x in re.findall(r"\d+", m.group(1))})
        if not nums or any(x not in step_nos for x in nums):
            return None
        return Verdict("keep", steps=nums)
    m = _DISCARD.match(v)
    if m:
        n = int(m.group(1))
        reason = m.group(3).strip()
        if n not in step_nos or not reason:
            return None
        return Verdict("discard", steps=[n], klass=(m.group(2) or "").lower(), reason=reason)
    return None


# ── test facts ────────────────────────────────────────────────────────────────────────────────
def classify(checks: list[dict], before: dict | None, after: dict | None,
             steps: list[dict], added_paths: set[str]) -> dict:
    """Deterministic facts, test ids and anchor item ids only.

    regression     - passed at the attempt's start, fails now (per test; or a whole check whose
                     exit code went 0 -> non-zero)
    new_not_passing- not seen at the start (new), fails now - NOT a regression
    fixed / new_passing / still_failing - the rest of the picture
    Each fact carries the anchor item of the step the worker attributed the check to. `before`
    None = the start could not be run: failures are reported as `unknown_before`, never guessed
    into regressions."""
    anchor_of = {int(s["n"]): (s.get("anchor") or "") for s in steps}
    facts: dict[str, list] = {"regression": [], "new_not_passing": [], "fixed": [],
                              "new_passing": [], "still_failing": [], "unknown_before": []}
    if after is None:
        return {"available": False, **facts}
    for k, c in enumerate(checks[:MAX_CHECKS]):
        anchor = anchor_of.get(int(c["step"]), "")
        a = after.get(k) or {"rc": None, "tests": {}}
        b = (before or {}).get(k) if before is not None else None
        base = {"check": f"c{k + 1}", "step": int(c["step"]), "anchor": anchor}
        if a["tests"]:
            for tid, st in sorted(a["tests"].items()):
                fact = {**base, "test": tid}
                prev = (b or {}).get("tests", {}).get(tid) if b else None
                if st == "FAILED":
                    if b is None:
                        facts["unknown_before"].append(fact)
                    elif prev == "PASSED":
                        facts["regression"].append(fact)
                    elif prev == "FAILED":
                        facts["still_failing"].append(fact)
                    elif tid.split("::", 1)[0] in added_paths or b["rc"] != 0:
                        facts["new_not_passing"].append(fact)
                    else:
                        # the start ran clean (rc 0) and listed no result for an unchanged file:
                        # treated as passing at the start
                        facts["regression"].append(fact)
                elif st == "PASSED":
                    if prev == "FAILED":
                        facts["fixed"].append(fact)
                    elif prev is None and b is not None and b["tests"]:
                        facts["new_passing"].append(fact)
        # the check as a whole (covers checks that print no per-test lines)
        fact = {**base, "test": f"check {base['check']}"}
        if a["rc"] not in (0, None):
            if b is None:
                if not a["tests"]:
                    facts["unknown_before"].append(fact)
            elif b["rc"] == 0:
                if not any(f["check"] == base["check"] for f in facts["regression"]):
                    facts["regression"].append(fact)
            elif not a["tests"]:
                facts["new_not_passing" if b["rc"] in (2, 4, 5, 127) else "still_failing"].append(fact)
        elif a["rc"] == 0 and b is not None and b["rc"] not in (0, None) and not a["tests"]:
            facts["fixed"].append(fact)
    return {"available": True, **facts}


def facts_text(facts: dict, diff: dict | None, steps: list[dict], checks: list[dict]) -> str:
    """The FACTS block for the planner (bounded). Includes the worker's check commands - the
    planner must judge whether they evidence the anchor item - but this text is a prompt, never
    an audit payload or a note."""
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
    else:
        for k, c in enumerate(checks[:MAX_CHECKS]):
            anchor = next((s.get("anchor") for s in steps if int(s["n"]) == int(c["step"])), "")
            lines.append(f"- worker check c{k + 1} for step {c['step']} (anchor {anchor or '?'}): "
                         f"`{c['cmd'][:160]}`")
    if facts.get("available"):
        for key, label in (("regression", "REGRESSIONS (passed at the start, fail now)"),
                           ("new_not_passing", "new, not yet passing"),
                           ("still_failing", "failing before and after"),
                           ("unknown_before", "failing now, start not measurable"),
                           ("fixed", "fixed by the attempt"),
                           ("new_passing", "new and passing")):
            items = facts.get(key) or []
            if items:
                shown = "; ".join(f"{f['test']} [step {f['step']}, anchor {f['anchor'] or '?'}]"
                                  for f in items[:8])
                more = f"; and {len(items) - 8} more" if len(items) > 8 else ""
                lines.append(f"- {label}: {shown}{more}")
    elif checks:
        lines.append("- the checks could not be run (no test facts)")
    return "\n".join(lines)


def fact_ids(facts: dict) -> dict:
    """The audit-safe projection: test ids + anchor ids + counts."""
    out = {}
    for key in ("regression", "new_not_passing", "still_failing", "unknown_before", "fixed",
                "new_passing"):
        items = facts.get(key) or []
        out[key] = [{"test": f["test"][:160], "anchor": f["anchor"]} for f in items[:20]]
        out[f"n_{key}"] = len(items)
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
            ev.append(f"{label}: " + ", ".join(f["test"][:80] for f in items[:3])
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
    """The discard as a learning record (shape of the harness's learning-record v1, producer
    `agent-org`): the solution cache entry a later effort with a similar goal can find. Facts
    are ids and counts; the summary IS the bounded note."""
    return {
        "schema_version": 1,
        "fidelity": "iterative",
        "producer": "agent-org",
        "source_ref": {"effort_id": effort_id, "attempt_id": attempt_id, "ref": ref},
        "domains": ["agent-org", "attempt-discard"],
        "steer": f"anchor item {anchor}: {scrub(anchor_text, 200)}" if anchor_text
                 else f"anchor item {anchor or 'goal'}",
        "red": {k: v for k, v in fact_ids(facts).items() if k.startswith("n_") or v},
        "outcome": {"kind": "gap-remains", "summary": note},
        "deviation": klass,
        "created_at": created_at,
    }


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
