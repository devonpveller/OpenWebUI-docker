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
    ["checkout", "-q", "-B", "REF", "SHA"],
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
ID_MAX = 160            # test ids are SHOWN up to this length (compared in full)
ID_SHOW = 140           # a longer id is shown as its first ID_SHOW chars + "~" + its cksum
CHECKS_BUDGET_S = 1500  # ALL worker checks of one plan wake (both runs) share this wall-clock budget
HANG_RCS = (124, 137)   # GNU timeout: 124 = timed out, 137 = killed after -k
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


_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/\[\]=,+~-]")


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


_LIST_TAGS = ("J", "X")


def new_nonce() -> str:
    """A per-run tag for the records of a command that runs WORKER code (a check): uppercase hex,
    unguessable. The check's own output goes to a file, never into the /check stream."""
    import secrets
    return secrets.token_hex(8).upper()


def records(out: str | None, nonce: str | None = None) -> list[tuple[str, str]] | None:
    """All records in one /check output, in order, or None when the output is untrusted (no
    BEGIN, or no END after it - truncated, or the command died). CR and LF are deleted first:
    open-terminal's per-write entries are joined with an extra newline that can land anywhere.

    With `nonce` (a command that ran worker code): only records whose tag carries `X<nonce>`
    count; BEGIN and END must appear EXACTLY once, and every single-valued tag at most once -
    a duplicate (something that learned the nonce and wrote into the stream) makes the whole
    output untrusted, never accepted. Untagged record-shaped text is ignored."""
    if not out:
        return None
    flat = out.replace("\r", "").replace("\n", "")
    if nonce:
        bt, et = f"@@B{nonce} ao B{nonce}@@", f"@@E{nonce} ao E{nonce}@@"
        if flat.count(bt) != 1 or flat.count(et) != 1:
            return None
        b, e = flat.find(bt), flat.find(et)
        if e < b:
            return None
        suf = "X" + nonce
        got = [(m.group(1)[:-len(suf)], m.group(2))
               for m in _RECORD_RE.finditer(flat[b + len(bt):e]) if m.group(1).endswith(suf)]
        singles = [t for t, _ in got if t not in _LIST_TAGS]
        if len(singles) != len(set(singles)):
            return None
        return got
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


def refs_cmd(refs: list[str]) -> str:
    """Where each salvage ref points now (R records: `<ref> <sha|missing>`)."""
    body = [f'printf \'@@R %s %s R@@\' {shlex.quote(r)} "$(git rev-parse --verify -q '
            f'refs/heads/{r} 2>/dev/null || echo missing)"' for r in refs if is_branch(r)]
    return _wrap(body or [":"])


def parse_refs(out: str | None) -> dict | None:
    recs = records(out)
    if recs is None:
        return None
    res = {}
    for t, v in recs:
        if t == "R" and " " in v:
            r, sha = v.rsplit(" ", 1)
            res[r] = sha.strip()
    return res


def repair_ref_cmd(ref: str, sha: str, branch: str, head: str) -> str:
    """Point a moved salvage ref back at its parked commit, through the proxy: `checkout -B` is
    the only proxy-legal way to move a branch (`branch -f` and `update-ref` are denied), so the
    workspace briefly visits the ref and is put back on `branch` at `head` (clean tree only)."""
    back = restore_snippet(branch, head)
    return _wrap([
        f'if [ "$(git status --porcelain --untracked-files=all 2>/dev/null | wc -l)" -eq 0 ]; then '
        f'git checkout -q -B {shlex.quote(ref)} {sha} >/dev/null 2>&1; fi',
        back,
        _rec("R", f'"{ref} $(git rev-parse --verify -q refs/heads/{ref} 2>/dev/null || echo missing)"'),
        *_state(),
    ])


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
# dotnet test (console logger, normal verbosity): "  Passed Ns.Class.Method [12 ms]"
_DOTNET_SED = r"sed -nE 's/^[[:space:]]*(Passed|Failed) ([^[:space:]]+).*/\1 \2/p'"
# status + FULL id per test (no truncation before comparison: two long ids never merge),
# failing wins: F <id> / P <id>
_SPLIT_AWK = ("awk '{ if ($1 ~ /::/) { id=$1; st=$2 } else { id=$2; st=$1 }; "
              "if (st==\"FAILED\"||st==\"ERROR\"||st==\"XPASS\"||st==\"Failed\") f[id]=1; "
              "else p[id]=1 } "
              "END { for (i in f) print \"F \" i; for (i in p) if (!(i in f)) print \"P \" i }'")


def _show_loop(tag: str) -> str:
    """Emit each `<status> <id>` (or bare `<id>`) line as a record; an id longer than ID_SHOW is
    shown cut + `~<cksum>` so two shown ids never look the same."""
    return (f'while IFS= read -r l; do id="$l"; pre=""; case "$l" in "P "*|"A "*|"F "*|"U "*) '
            f'pre="${{l%% *}} "; id="${{l#* }}";; esac; if [ ${{#id}} -gt {ID_SHOW} ]; then '
            f'h=$(printf %s "$id" | cksum | cut -d" " -f1); id="$(printf %s "$id" | cut -c1-{ID_SHOW})~$h"; '
            f'fi; {_rec(tag, chr(34) + "$pre$id" + chr(34))}; done')


_SHELL_META = re.compile(r"[$`|&;<>(){}*?\[\]!~'\"\\]")


def operands(cmd: str) -> list[str]:
    """Relative file operands of a check command (`sh tests/t.sh`, `pytest tests/test_x.py`), and
    the same under a leading `cd DIR &&`: what the shell tests for EXISTENCE at the end and at the
    start. A check whose operand did not exist at the start could not run there (G1: measured,
    not a substring match against the diff)."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return []
    out: list[str] = []
    cds: list[str] = []
    for i, t in enumerate(toks):
        if i > 0 and toks[i - 1] == "cd" and not _SHELL_META.search(t):
            cds.append(t)
            continue
        if (t.startswith("-") or t.startswith("/") or _SHELL_META.search(t) or ".." in t
                or ("/" not in t and "." not in t[1:]) or len(t) > 200):
            continue
        out.append(t)
        out.extend(f"{d}/{t}" for d in cds)
    seen: list[str] = []
    for o in out:
        if o not in seen:
            seen.append(o)
    return seen[:12]


def check_cmd(cmd: str, *, start: str | None, branch: str, park: str, ref: str,
              timeout: int = CHECK_TIMEOUT_S, nonce: str = "") -> str:
    """ONE worker check, end AND start, in ONE /check:

    1. run it at the parked commit - stdout AND stderr into a FILE (G3: nothing the check prints
       reaches the /check stream, so no output volume can push the bridge's records out and no
       record-shaped text can be read as one); exit status and elapsed seconds;
    2. `checkout --detach <start>` (verified, CO record), which operands are MISSING there, run it;
    3. RESTORE the branch (name and tip) and the tree, whatever the check did;
    4. OPTIONAL per-test detail (pytest `::` ids, dotnet Passed/Failed lines), full-id comparison.
    Every bridge record carries the per-run `nonce` (records(out, nonce)); the check runs in its
    own session (`setsid`, where present) so it has no controlling terminal to write to."""
    nonce = nonce or new_nonce()
    sfx = "X" + nonce

    def rec(tag: str, expr: str) -> str:
        return _rec(tag + sfx, expr)

    q = shlex.quote(cmd)
    run = (f"( cd {WS} && NO_COLOR=1 PY_COLORS=0 TERM=dumb DOTNET_CLI_TELEMETRY_OPTOUT=1 "
           f"$SS timeout -k {CHECK_KILL_S} {int(timeout)} bash -c {q} ) </dev/null")
    tests = f'{{ {_TEST_GREP} "$OUT"; {_DOTNET_SED} "$OUT"; }} | {_SPLIT_AWK} | LC_ALL=C sort'
    ops = " ".join(shlex.quote(o) for o in operands(cmd)) or "''"
    body = [
        'SS=""; command -v setsid >/dev/null 2>&1 && SS="setsid -w"',
        'd=$(mktemp -d); : > "$d/a.t"; : > "$d/b.t"; : > "$d/j"; : > "$d/x"; : > "$d/ops"',
        f'for o in {ops}; do [ -n "$o" ] && [ -e "$o" ] && printf "%s\\n" "$o" >> "$d/ops"; done',
        f't0=$(date +%s); {run} > "$d/a.out" 2>&1; rca=$?; t1=$(date +%s)',
        tests.replace("$OUT", "$d/a.out") + ' > "$d/a.t"',
        rec("RCA", '"$rca"'), rec("TA", '"$((t1-t0))"'), rec("TO", str(int(timeout))),
    ]
    if is_sha(start):
        body += [
            f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1; "
            f"git checkout -q --detach {start} >/dev/null 2>&1; "
            f"git checkout -q -- . >/dev/null 2>&1; git clean -fdq >/dev/null 2>&1",
            f'if [ "$(git rev-parse HEAD 2>/dev/null)" = "{start}" ]; then '
            + rec("CO", "1") + '; nm=0; while IFS= read -r o; do [ -e "$o" ] || nm=$((nm+1)); '
            'done < "$d/ops"; ' + rec("NMISS", '"$nm"')
            + f'; t0=$(date +%s); {run} > "$d/b.out" 2>&1; rcb=$?; t1=$(date +%s); '
            + tests.replace("$OUT", "$d/b.out") + ' > "$d/b.t"; '
            + rec("RCB", '"$rcb"') + "; " + rec("TB", '"$((t1-t0))"') + "; else "
            + rec("CO", "0") + "; fi",
        ]
    body += [
        restore_snippet(branch, park),
        'if [ -s "$d/b.t" ]; then awk \'NR==FNR { s[substr($0,3)]=substr($0,1,1); next } '
        '$1=="F" { id=substr($0,3); print ((id in s) ? s[id] : "A") " " id }\' "$d/b.t" "$d/a.t" '
        '> "$d/j"; awk \'NR==FNR { if ($1=="F") f[substr($0,3)]=1; next } '
        '$1=="P" && (substr($0,3) in f) { print substr($0,3) }\' "$d/b.t" "$d/a.t" > "$d/x"; '
        'else awk \'$1=="F" { print "U " substr($0,3) }\' "$d/a.t" > "$d/j"; fi',
        *[rec(tag, f'"$(grep -c {pat} "$d/{f}" 2>/dev/null)"') for tag, pat, f in (
            ("NFA", "'^F '", "a.t"), ("NPA", "'^P '", "a.t"), ("NFB", "'^F '", "b.t"),
            ("NPB", "'^P '", "b.t"), ("NREG", "'^P '", "j"), ("NNEW", "'^A '", "j"),
            ("NSTILL", "'^F '", "j"), ("NUNK", "'^U '", "j"))],
        rec("NFIX", '"$(wc -l < "$d/x")"'),
        f'{{ grep \'^P \' "$d/j"; grep \'^A \' "$d/j"; grep \'^F \' "$d/j"; grep \'^U \' "$d/j"; }} '
        f'| head -{MAX_LISTED} | {_show_loop("J" + sfx)}',
        f'head -{MAX_FIXED_LISTED} "$d/x" | {_show_loop("X" + sfx)}',
        'rm -rf "$d"',
        *[x.replace("@@HEAD ", f"@@HEAD{sfx} ").replace(" HEAD@@", f" HEAD{sfx}@@")
          .replace("@@BR ", f"@@BR{sfx} ").replace(" BR@@", f" BR{sfx}@@")
          .replace("@@DIRTY ", f"@@DIRTY{sfx} ").replace(" DIRTY@@", f" DIRTY{sfx}@@")
          .replace("@@REF ", f"@@REF{sfx} ").replace(" REF@@", f" REF{sfx}@@") for x in _state(ref)],
    ]
    return " ; ".join([f"cd {WS} 2>/dev/null || exit 90", f"printf '@@B{nonce} ao B{nonce}@@'",
                       *body, f"printf '@@E{nonce} ao E{nonce}@@'"])


@dataclass
class CheckRun:
    rca: int
    rcb: int | None                 # None: the start could not be measured
    counts: dict
    listed: list                    # [(status, id)]: P regression, A new, F still, U unknown
    fixed: list
    state: State
    start_checked_out: bool | None = None
    elapsed_a: int | None = None
    elapsed_b: int | None = None
    limit: int | None = None
    missing_at_start: int = 0

    def hung(self, rc: int | None, elapsed: int | None) -> bool:
        """A HANG is a 124/137 that came from the timeout: the run lasted (about) its limit. An
        instant `exit 124` or an OOM SIGKILL (137) well inside the limit is an ordinary failure."""
        return (rc in HANG_RCS and elapsed is not None and self.limit is not None
                and elapsed >= self.limit - 1)


def parse_check(out: str | None, nonce: str | None = None) -> CheckRun | None:
    """None when the output is untrusted (no BEGIN/END: cut, or the command died; a duplicated
    nonce record) or incomplete."""
    recs = records(out, nonce)
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
    co = first(recs, "CO")
    return CheckRun(rca=rca, rcb=_int(first(recs, "RCB")), counts=counts, listed=listed,
                    fixed=fixed, state=st, start_checked_out=None if co is None else co.strip() == "1",
                    elapsed_a=_int(first(recs, "TA")), elapsed_b=_int(first(recs, "TB")),
                    limit=_int(first(recs, "TO")), missing_at_start=_int(first(recs, "NMISS")) or 0)


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


def parse_plan(text: str, known_items: dict | None = None, *, fixed: bool = False) -> PlanDecl:
    """Read the PLAN's numbered steps. Every step must end with `[anchor: Ax]`. With `fixed`
    (round 3: the effort's items are fixed at acceptance) only `known_items` count and any
    ANCHOR ITEMS section the planner wrote is IGNORED - it cannot define, renumber or redefine
    an item. A plan whose PLAN says 'nothing remains' needs no steps."""
    d = PlanDecl()
    if not fixed:
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
_NUMSPEC = r"\d{1,3}(?:\s*-\s*\d{1,3})?(?:\s*(?:,|\band\b|\s)\s*\d{1,3}(?:\s*-\s*\d{1,3})?)*"
_KEEP = re.compile(r"^KEEP\b\s*(?:steps?\s*)?(" + _NUMSPEC + r")\s*(?:[.:;].*|-\s*\D.*)?$", re.I)
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
        spec = re.sub(r"\s*-\s*", "-", m.group(1).strip())
        for part in re.split(r"\s*(?:,|\band\b|\s)\s*", spec):
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
OUTCOMES = ("regression", "hang", "new_not_passing", "still_failing", "could_not_run_at_start",
            "untrusted", "not_run", "fixed", "new_passing", "passing")


def _status(rc: int | None, hung: bool = False) -> str:
    if rc is None:
        return "not run"
    if hung:
        return "HANG"
    return "PASS" if rc == 0 else f"FAIL (exit {rc})"


def classify(checks: list[dict], runs: list, steps: list[dict],
             new_paths: list[str] | None = None) -> dict:
    """CHECK-LEVEL facts, framework-agnostic (round 3): per worker check, its exit status at the
    attempt's start vs now -

    hang                   timed out now (killed by its timeout: 124/137 AND the run lasted its
                           limit), whatever it did at the start - a new check that hangs too
    regression             MEASURED: passed at the start, fails now (decided before anything else)
    new_not_passing        the check COULD NOT RUN at the start - exit 126/127 there, or a file
    new_passing            operand of its command did not exist there (tested in the shell) - and
                           fails / passes now (round 4: no substring rule against the diff)
    still_failing          failed (or hung) at the start and fails now
    could_not_run_at_start no start, or the start could not be checked out
    untrusted              its /check output was cut or incomplete: UNKNOWN, never "no regression"
    not_run                the plan wake's check-time budget ran out before it
    fixed / new_passing / passing - the rest
    Per-test detail (pytest `::` ids, dotnet Passed/Failed lines) is an optional EXTRA under
    `tests`; no fact above depends on it. Each fact carries its step's anchor item."""
    new_paths = list(new_paths or [])
    anchor_of = {int(s["n"]): (s.get("anchor") or "") for s in steps}
    facts: dict = {"available": bool(checks), "checks": [],
                   "tests": {k: [] for k in ("regressed", "new", "still_failing", "unknown", "fixed")},
                   "tests_counts": {"regressed": 0, "new": 0, "still_failing": 0, "unknown": 0,
                                    "fixed": 0}, "tests_truncated": False}
    facts.update({f"n_{o}": 0 for o in OUTCOMES})
    label = {"P": "regressed", "A": "new", "F": "still_failing", "U": "unknown"}
    for k, c in enumerate(checks[:MAX_CHECKS]):
        step = int(c["step"])
        base = {"check": f"c{k + 1}", "step": step, "anchor": anchor_of.get(step, "")}
        r = runs[k] if k < len(runs) else None
        if r == "budget":
            facts["checks"].append({**base, "start": "not run", "now": "not run", "outcome": "not_run"})
            facts["n_not_run"] += 1
            continue
        if r is None:
            facts["checks"].append({**base, "start": "?", "now": "?", "outcome": "untrusted"})
            facts["n_untrusted"] += 1
            continue
        now_pass = r.rca == 0
        now_hang = r.hung(r.rca, r.elapsed_a)
        start_hang = r.hung(r.rcb, r.elapsed_b)
        could_not = r.rcb is not None and r.rcb != 0 and not start_hang and (
            r.rcb in (126, 127) or r.missing_at_start > 0)
        if now_hang:
            outcome = "hang"
        elif r.rcb is None:
            outcome = "could_not_run_at_start"
        elif r.rcb == 0:
            outcome = "passing" if now_pass else "regression"
        elif could_not:
            outcome = "new_passing" if now_pass else "new_not_passing"
        else:
            outcome = "fixed" if now_pass else "still_failing"
        facts["checks"].append({**base, "start": _status(r.rcb, start_hang)
                                if r.start_checked_out is not False else "start not checked out",
                                "now": _status(r.rca, now_hang), "outcome": outcome})
        facts[f"n_{outcome}"] += 1
        # optional per-test detail
        cn = r.counts
        for s_, tid in r.listed:
            facts["tests"][label[s_]].append({**base, "test": tid})
        for tid in r.fixed:
            facts["tests"]["fixed"].append({**base, "test": tid})
        for key, n in (("regressed", cn["NREG"]), ("new", cn["NNEW"]), ("still_failing", cn["NSTILL"]),
                       ("unknown", cn["NUNK"]), ("fixed", cn["NFIX"])):
            facts["tests_counts"][key] += n
        if len(r.listed) < cn["NREG"] + cn["NNEW"] + cn["NSTILL"] + cn["NUNK"]:
            facts["tests_truncated"] = True
    return facts


def facts_text(facts: dict, diff: dict | None, steps: list[dict], checks: list[dict]) -> str:
    """The FACTS block for the planner (bounded): EVERY check's pass/fail at the start and now,
    with its outcome, then the optional per-test detail. Includes the worker's check commands (the
    planner judges whether they evidence the anchor item) - a prompt, never audit or a note."""
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
    words = {"regression": "REGRESSION (passed at the start, fails now)",
             "hang": "HANG (killed by its timeout now)",
             "new_not_passing": "new check (it could not run at the start), NOT passing",
             "new_passing": "new check (it could not run at the start), passing",
             "still_failing": "still failing (failed at the start too)",
             "could_not_run_at_start": "start not measured (no start / not checked out)",
             "untrusted": "NO FACTS - output cut or incomplete: UNKNOWN, not absent",
             "not_run": "not run (the check-time budget of this plan wake ran out)",
             "fixed": "FIXED (failed at the start, passes now)", "passing": "passing (before and now)"}
    by_k = {f["check"]: f for f in facts.get("checks") or []}
    for k, c in enumerate(checks[:MAX_CHECKS]):
        f = by_k.get(f"c{k + 1}") or {"start": "?", "now": "?", "outcome": "untrusted",
                                      "anchor": ""}
        lines.append(f"- check c{k + 1} [step {c['step']}, anchor {f.get('anchor') or '?'}] "
                     f"`{c['cmd'][:160]}`: start {f['start']} -> now {f['now']}: "
                     f"{words.get(f['outcome'], f['outcome'])}")
    tc = facts.get("tests_counts") or {}
    if any(tc.values()):
        parts = []
        for key, label in (("regressed", "tests passing at the start, failing now"),
                           ("new", "new failing tests"), ("still_failing", "tests failing both"),
                           ("fixed", "tests fixed")):
            items = (facts.get("tests") or {}).get(key) or []
            if tc.get(key):
                shown = ", ".join(i["test"] for i in items[:6])
                parts.append(f"{label}: {tc[key]}" + (f" ({shown}{', ...' if tc[key] > 6 else ''})"
                                                       if shown else ""))
        lines.append("- per-test detail (only where the output names tests; informational): "
                     + "; ".join(parts))
    return "\n".join(lines)


def fact_ids(facts: dict) -> dict:
    """The audit-safe projection: check ids, step, anchor, start/now status, outcome, counts and
    (optional) test ids - never a command."""
    out = {f"n_{o}": int(facts.get(f"n_{o}", 0)) for o in OUTCOMES}
    out["checks"] = [{k: f[k] for k in ("check", "step", "anchor", "start", "now", "outcome")}
                     for f in (facts.get("checks") or [])][:MAX_CHECKS]
    tests = facts.get("tests") or {}
    out["tests"] = {k: [safe_id(t["test"]) for t in (tests.get(k) or [])[:20]] for k in tests}
    out["tests_counts"] = dict(facts.get("tests_counts") or {})
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
    for f in (facts.get("checks") or [])[:MAX_CHECKS]:
        if f.get("outcome") not in ("passing", "new_passing", "fixed"):
            ev.append(f"check {f['check']} {f['outcome'].replace('_', ' ')}")
    regressed = ((facts.get("tests") or {}).get("regressed") or [])
    if regressed:
        ev.append("tests regressed: " + ", ".join(safe_id(f["test"])[:80] for f in regressed[:3])
                  + (" ..." if len(regressed) > 3 else ""))
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
    red = (f"anchor item {anchor}: checks - {ids['n_regression']} regression(s), "
           f"{ids['n_hang']} hang(s), {ids['n_new_not_passing']} new not passing, "
           f"{ids['n_still_failing']} still failing, {ids['n_untrusted']} unknown"
           + ("; tests regressed: " + ", ".join(t[:80] for t in ids["tests"].get("regressed", [])[:3])
              if ids["tests"].get("regressed") else ""))
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


# the bridge's own clauses appended to a request at intake (and later): never anchor items
_INJECTED = re.compile(r"\n\n(?:ITERATION |EFFORT ANCHOR|RELEVANT MEMORIES|LEARNED CONSTRAINTS|"
                       r"ACCEPTANCE CORPUS|HANDOFF|PROJECT ORIENTATION|WORKSPACE PROVENANCE|"
                       r"WHAT NOT TO DO|STANDING INTENT|COMPOSITION CONTEXT|How this gets checked|"
                       r"REQUIRED VERIFICATION|THIS IS A RUNTIME|PRIOR ATTEMPTS|YOUR SCOPE)")
_LIST_ITEM = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+(.+?)\s*$")
MAX_ITEMS = 8


def derive_items(goal: str) -> dict:
    """The effort's ANCHOR ITEMS, derived deterministically from its ACCEPTED goal (the first goal
    version, set at intake), ids A1..An by position. Pure: the same goal always gives the same
    items, so an id can never name a different goal. List lines (- / * / 1.) are the items;
    without a list, the goal's sentences; at most MAX_ITEMS (the rest folded into the last)."""
    text = _INJECTED.split(goal or "", maxsplit=1)[0].strip()
    lines = [m.group(1) for m in (_LIST_ITEM.match(ln) for ln in text.splitlines()) if m]
    if not lines:
        flat = " ".join(text.split())
        lines = [x.strip() for x in re.split(r"(?<=[.!?;])\s+", flat) if len(x.split()) >= 2] or (
            [flat] if flat else [])
    if len(lines) > MAX_ITEMS:
        lines = lines[:MAX_ITEMS - 1] + ["; ".join(lines[MAX_ITEMS - 1:])]
    return {f"A{i}": " ".join(t.split())[:240] for i, t in enumerate(lines, 1)}


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
