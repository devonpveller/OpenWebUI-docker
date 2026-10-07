"""The learning-record gate (MERGE-PROTOCOL Step 4; validated-work-memory phase 1, vwm-p1).

WHAT A LEARNING RECORD IS FOR (operator, 2026-10-06). Learning records are not per-item
paperwork. They are a shared, cross-project account of how systems behave, built from
practice, and their job is to cut iterations by reusing past experience: "cache the answers
to repeated problems so the model doesn't regenerate the same solution". A cache that holds
a wrong answer is worse than no cache. This gate keeps it clean: a record must be genuine
before it can teach anything.

WHAT THIS CHECKS. An item that came back at least once (a tester fail, a reviewer
-Reject/-Requeue, or a -Requeue after a pass, i.e. an improvement send-back; operator D3)
needs an `iterative` learning record beside its anchor's findings_sink. The record must:
  - validate against the schema FILE in the plan store (read from disk, never a copy);
  - name this item (source_ref.queue_item_id);
  - carry iterations == the number of ALL returns (operator D2: history, not a limit);
  - count the returns of the item's PREDECESSORS too: an item that continues a rejected one
    (X -> X2) names it in its anchor (`continues`; queue.ps1 -Propose writes the queue's
    `predecessor` link), and a rejected `X` beside an undeclared `X2` is inferred (vwm-p1b);
  - carry every return's REASON from the queue in `red`, and name no return kind the queue
    does not have (vwm-p1b);
  - claim green only when green is GENUINE (operator D4): the queue's last verdict is a
    tester PASS at the exact tested_at_sha, recorded by someone other than the developer,
    whose evidence file is THAT pass's file in the queue (not an earlier FAIL's, never a
    path resolved against the current directory), is not empty, and reads PASS on every
    case heading, matching the per-case verdicts -Pass recorded (vwm-p1b);
  - be countersigned by the item's reviewer (reviewer_check.checked and .by);
  - give a merge_range whose ends are commits, base an ancestor of head, and head the
    tested commit or the merge;
  - cite only SHAs and paths that resolve, paths inside the code repo or the plan store
    (backslash paths and UPPERCASE hex are tokens too). Anything else in the evidence is
    prose, which this check cannot verify; it says so on every acceptance (the declared
    blind spot).

READ-ONLY. Nothing here writes, edits or fixes a record, a queue item or the plan store,
and nothing touches the plan store's git state. The only git calls are object and ancestry
reads against the CODE repository. An indeterminate read (store missing, record unreadable,
git error, or any unexpected exception) is a refusal with its own reason, never a pass,
and is reported apart from a missing record.

    python scripts/agent-harness/learning_records.py check --item <id> [--merge-sha <sha>] [--reviewer <id>]
    python scripts/agent-harness/learning_records.py audit [--store <path>]
    python scripts/agent-harness/learning_records.py draft --item <id> [--merge-sha <sha>]

Exit codes: 0 accepted (or not required) | 1 refused | 2 usage | 3 refused, INDETERMINATE.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

RECORD_SUFFIX = ".learning-record.json"
SCHEMA_REL = Path("implementation-guide") / "research-workbench" / "learning-record.schema.json"
DEFAULT_STORE_REL = Path("..") / "documentation-plans-ai-stack"
# The date the Step 4 learning-record rule was adopted (research-workbench 06-EXPANSION 3.3).
# The audit lists required-but-missing records only for items merged on or after it.
RULE_SINCE = "2026-09-29"

#: Every named refusal. An indeterminate one is a refusal too, never a pass.
REASONS = (
    "record-missing", "schema", "sink-missing", "item-mismatch", "iterations-mismatch",
    "range-unresolved", "range-head-mismatch", "evidence-sha-unresolved",
    "evidence-path-missing", "green-not-genuine", "countersign-missing", "placeholder-unfilled",
    # vwm-p1b
    "evidence-empty", "evidence-not-pass", "evidence-verdict-mismatch", "evidence-path-outside",
    "predecessor-returns", "return-reason-mismatch",
)
INDETERMINATE = (
    "store-unreadable", "record-unreadable", "queue-unreadable", "schema-unreadable", "git-error",
    "internal-error",
)

#: The marker `draft` puts where a person must write. A record still carrying one is not done.
PLACEHOLDER = "<FILL:"

# All-lowercase or all-UPPERCASE hex (vwm-p1b: uppercase used to pass as prose). Mixed case is
# a word, not a hash. A token is looked up lowercased.
SHA_RE = re.compile(r"(?<![0-9A-Za-z])(?:[0-9a-f]{7,40}|[0-9A-F]{7,40})(?![0-9A-Za-z])")
PATH_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")
LINE_SUFFIX_RE = re.compile(r":\d+(?:[-:]\d+)?$")
STRIP_LEAD = "([{<\"'`"
STRIP_TRAIL = ")]}>\"'`,;:."


class Indeterminate(Exception):
    """A read that could not complete. Always a refusal; never a pass."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def normalize_id(who: Any) -> str:
    """`wt-x` and `x` are the same agent (queue.ps1 Normalize-Id)."""
    if not who:
        return ""
    return re.sub(r"^wt-", "", str(who).strip().lower())


# --------------------------------------------------------------------------------- git
class Git:
    """Read-only object and ancestry questions against one repository."""

    def __init__(self, repo: Path):
        self.repo = Path(repo)
        out = self._run(["rev-parse", "--git-dir"])
        if out.returncode != 0:
            raise Indeterminate("git-error", f"'{self.repo}' is not a git repository: {out.stderr.strip()}")
        self.extra = [r for r in self._submodule_repos() if r != self.repo]

    def _run(self, args: List[str], stdin: Optional[str] = None, repo: Optional[Path] = None):
        try:
            return subprocess.run(["git", "-C", str(repo or self.repo)] + args, input=stdin,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Indeterminate("git-error", f"cannot run git: {exc}") from exc

    def _submodule_repos(self) -> List[Path]:
        # A SHA cited from a pinned submodule (OB1) is "in this repo" too: its objects live in
        # the submodule's own store, which the superproject's cat-file cannot see.
        gm = self.repo / ".gitmodules"
        out: List[Path] = []
        if not gm.is_file():
            return out
        for line in gm.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*path\s*=\s*(.+?)\s*$", line)
            if m:
                p = self.repo / m.group(1)
                if (p / ".git").exists():
                    out.append(p)
        return out

    def object_types(self, names: Iterable[str], repo: Optional[Path] = None) -> Dict[str, Optional[str]]:
        names = [n for n in dict.fromkeys(names) if n]
        if not names:
            return {}
        out = self._run(["cat-file", "--batch-check"], stdin="\n".join(names) + "\n", repo=repo)
        if out.returncode != 0:
            raise Indeterminate("git-error", f"git cat-file --batch-check failed: {out.stderr.strip()}")
        lines = out.stdout.splitlines()
        if len(lines) != len(names):
            raise Indeterminate("git-error", "git cat-file --batch-check answered a different number of lines")
        res: Dict[str, Optional[str]] = {}
        for name, line in zip(names, lines):
            parts = line.split()
            if line.endswith(" missing") or line.endswith(" ambiguous") or len(parts) < 3:
                res[name] = None
            else:
                res[name] = parts[-2]
        return res

    def sha_resolves(self, tokens: Iterable[str]) -> Dict[str, bool]:
        tokens = list(dict.fromkeys(tokens))
        found = {t: (v is not None) for t, v in self.object_types(tokens).items()}
        for sub in self.extra:
            missing = [t for t in tokens if not found.get(t)]
            if not missing:
                break
            for t, v in self.object_types(missing, repo=sub).items():
                if v is not None:
                    found[t] = True
        return {t: found.get(t, False) for t in tokens}

    def full_commit(self, rev: str) -> Optional[str]:
        if not rev:
            return None
        out = self._run(["rev-parse", "--verify", "--quiet", rev + "^{commit}"])
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    def is_ancestor(self, a: str, b: str) -> bool:
        out = self._run(["merge-base", "--is-ancestor", a, b])
        if out.returncode in (0, 1):
            return out.returncode == 0
        raise Indeterminate("git-error", f"git merge-base --is-ancestor {a} {b}: {out.stderr.strip()}")

    def first_parent(self, rev: str) -> Optional[str]:
        return self.full_commit(rev + "^1")

    def merge_base(self, a: str, b: str) -> Optional[str]:
        out = self._run(["merge-base", a, b])
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def main_checkout(start: Path) -> Path:
    """The main working tree (parent of the shared git dir), so `..` in a sink means the
    same thing from any worktree."""
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0 or not out.stdout.strip():
        raise Indeterminate("git-error", f"cannot resolve the git common dir from '{start}': {out.stderr.strip()}")
    return Path(out.stdout.strip()).parent


def _within(p: Path, base: Path) -> bool:
    try:
        Path(os.path.normcase(os.path.realpath(str(p)))).relative_to(os.path.normcase(os.path.realpath(str(base))))
        return True
    except ValueError:
        return False


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def resolve_repo(repo_arg: str) -> Path:
    """The main checkout of the code repository.

    An explicit --repo must BE the top of a git working tree. `git -C <dir>` walks up from a
    directory that is not a repository and finds whatever .git is above it - a home
    directory's, say - so a --repo that is not a repo used to be checked against an
    unrelated repository (vwm-p1b). That is a git error (indeterminate), never a verdict.
    With no --repo, the repository is the one this file lives in, not the current directory.
    """
    if not repo_arg:
        return main_checkout(Path(__file__).resolve().parent)
    p = Path(repo_arg)
    if not p.is_dir():
        raise Indeterminate("git-error", f"--repo '{p}' is not a directory")
    out = subprocess.run(["git", "-C", str(p), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    top = out.stdout.strip()
    if out.returncode != 0 or not top:
        raise Indeterminate("git-error", f"--repo '{p}' is not a git repository: {out.stderr.strip()}")
    if not _same_path(Path(top), p):
        raise Indeterminate("git-error", f"--repo '{p}' is not the top of a git working tree "
                                         f"(git walked up to '{top}', which is a different repository)")
    return main_checkout(p)


def default_queue_dir(repo: Path) -> Path:
    state = os.environ.get("AI_STACK_WORKTREE_STATE")
    if state:
        return Path(state) / "queue"
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0 or not out.stdout.strip():
        raise Indeterminate("git-error", f"cannot resolve the git common dir from '{repo}'")
    return Path(out.stdout.strip()) / "agent-worktrees" / "queue"


# ------------------------------------------------------------------------------- queue
def load_item(queue_dir: Path, item_id: str) -> Dict[str, Any]:
    p = Path(queue_dir) / f"{item_id}.json"
    if not p.is_file():
        raise Indeterminate("queue-unreadable", f"no queue item file {p}")
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise Indeterminate("queue-unreadable", f"{p}: {exc}") from exc
    if not isinstance(d, dict):
        raise Indeterminate("queue-unreadable", f"{p} is not a JSON object")
    return d


def _after_colon(what: str) -> str:
    i = what.find(": ")
    return what[i + 2:].strip() if i >= 0 else ""


def returns_of(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every time the item went BACK (operator D2/D3), oldest first, each with its reason.

    - tester-fail        a results[] row with verdict fail
    - reviewer-requeue   `returned to test` (the reviewer's -Requeue; also a coordinator's,
                         which needs the reviewer claim)
    - developer-requeue  `developer WITHDREW it from test-passed` - a send-back AFTER a pass,
                         i.e. an improvement; it is learning too (D3)
    - reviewer-reject    `rejected`
    """
    out: List[Dict[str, Any]] = []
    for r in item.get("results") or []:
        if isinstance(r, dict) and str(r.get("verdict", "")).lower() == "fail":
            out.append({"kind": "tester-fail", "by": r.get("by", ""), "at": r.get("at", 0),
                        "attempt": r.get("attempt"), "sha": r.get("sha", ""),
                        "reason": str(r.get("reason") or "").strip()})
    for h in item.get("history") or []:
        if not isinstance(h, dict):
            continue
        what = str(h.get("what", ""))
        kind = None
        if what.startswith("returned to test"):
            kind = "reviewer-requeue"
        elif what.startswith("developer WITHDREW it from test-passed"):
            kind = "developer-requeue"
        elif what.startswith("rejected"):
            kind = "reviewer-reject"
        if kind:
            m = re.search(r"now attempt (\d+)", what)
            out.append({"kind": kind, "by": h.get("who", ""), "at": h.get("at", 0),
                        "attempt": (int(m.group(1)) - 1) if m else None, "sha": "",
                        "reason": _after_colon(what)})
    out.sort(key=lambda r: (int(r.get("at") or 0)))
    return out


#: The anchor value that DECLARES "this item continues nothing" (it turns off the inference).
NO_PREDECESSOR = "none"
RETURN_KINDS = ("tester-fail", "reviewer-requeue", "developer-requeue", "reviewer-reject")


def predecessor_of(item: Dict[str, Any], item_id: str, queue_dir: Optional[Path]) -> Tuple[str, str]:
    """(predecessor id, how) - how is 'declared', 'inferred' or '' (none).

    REOPENED ITEMS (vwm-p1b). A rejected item cannot be reopened; its work continues under a
    new id (amp-owui-deny -> amp-owui-deny2, mm-retire -> mm-retire2), whose own queue shows
    no return. Without a link the gate read "first-try green, no record needed" and the
    predecessor's returns vanished from the cache. The link is DECLARED in the anchor
    (`"continues": "<id>"`, which queue.ps1 -Propose checks and copies to the item's
    `predecessor`). When nothing is declared, a REJECTED item named like this one minus its
    trailing number is taken as the predecessor (inferred: fail closed); an anchor that says
    `"continues": "none"` declares there is none.
    """
    anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
    declared = [str(v).strip() for v in (item.get("predecessor"), anchor.get("continues"))
                if isinstance(v, str) and v.strip()]
    named = [v for v in declared if v.lower() != NO_PREDECESSOR]
    if len(set(named)) > 1:
        raise Indeterminate("queue-unreadable", f"'{item_id}' names two different predecessors: {', '.join(sorted(set(named)))}")
    if named:
        return named[0], "declared"
    if declared or queue_dir is None:
        return "", ""
    m = re.match(r"^(.*?[A-Za-z])[-_]?(\d+)$", item_id)
    if not m:
        return "", ""
    stem, n = m.group(1), int(m.group(2))
    cands = [f"{stem}{k}" for k in range(n - 1, 1, -1)] + [f"{stem}-{k}" for k in range(n - 1, 1, -1)] + [stem]
    for c in cands:
        if c == item_id:
            continue
        p = Path(queue_dir) / f"{c}.json"
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get("state", "")) == "rejected":
            return c, "inferred"
    return "", ""


def chain_returns(item: Dict[str, Any], item_id: str, queue_dir: Optional[Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every return of this item AND of the items it continues, oldest item first.

    Returns (returns, chain notes). Each return carries `item`. A declared predecessor the
    queue does not hold is indeterminate (the history cannot be read), never "no returns".
    """
    out: List[Dict[str, Any]] = []
    notes: List[str] = []
    seen = {item_id}
    links: List[Tuple[str, Dict[str, Any]]] = []
    cur, cur_id = item, item_id
    while True:
        pid, how = predecessor_of(cur, cur_id, queue_dir)
        if not pid:
            break
        if pid in seen or len(seen) > 50:
            raise Indeterminate("queue-unreadable", f"the predecessor chain of '{item_id}' loops at '{pid}'")
        seen.add(pid)
        pred = load_item(Path(queue_dir), pid) if queue_dir is not None else {}
        notes.append(f"'{cur_id}' continues '{pid}' ({how})")
        links.append((pid, pred))
        cur, cur_id = pred, pid
    for pid, pred in reversed(links):
        for r in returns_of(pred):
            out.append({**r, "item": pid})
    for r in returns_of(item):
        out.append({**r, "item": item_id})
    return out, notes


def derived_iterations(item: Dict[str, Any], item_id: str = "", queue_dir: Optional[Path] = None) -> int:
    if queue_dir is None:
        return len(returns_of(item))
    return len(chain_returns(item, item_id or str(item.get("id") or ""), queue_dir)[0])


def record_required(item: Dict[str, Any], item_id: str = "", queue_dir: Optional[Path] = None) -> bool:
    return derived_iterations(item, item_id, queue_dir) > 0


def reviewer_of(item: Dict[str, Any], explicit: str = "") -> str:
    if explicit:
        return explicit
    hist = [h for h in (item.get("history") or []) if isinstance(h, dict)]
    for h in reversed(hist):
        if str(h.get("what", "")).startswith("merged as"):
            return str(h.get("who", ""))
    for h in reversed(hist):
        if str(h.get("what", "")) == "claimed as reviewer":
            return str(h.get("who", ""))
    return ""


def merged_at(item: Dict[str, Any]) -> int:
    for h in reversed(item.get("history") or []):
        if isinstance(h, dict) and str(h.get("what", "")).startswith("merged as"):
            return int(h.get("at") or 0)
    return 0


# ------------------------------------------------------------------------------ schema
SUPPORTED_KEYWORDS = {
    "$schema", "$id", "title", "description", "type", "required", "additionalProperties",
    "properties", "items", "const", "enum", "minItems", "minimum", "format",
}


def _schema_keywords(node: Any, where: str = "") -> List[str]:
    bad: List[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in SUPPORTED_KEYWORDS:
                bad.append(f"{where}/{k}")
            if k == "properties" and isinstance(v, dict):
                for pk, pv in v.items():
                    bad += _schema_keywords(pv, f"{where}/properties/{pk}")
            elif k in ("items", "additionalProperties") and isinstance(v, dict):
                bad += _schema_keywords(v, f"{where}/{k}")
    return bad


def load_schema(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise Indeterminate("schema-unreadable", f"schema file not found: {path}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise Indeterminate("schema-unreadable", f"{path}: {exc}") from exc
    bad = _schema_keywords(schema)
    if bad:
        # A keyword this validator does not implement would be silently ignored, which is a
        # pass by omission. Refuse instead: the schema moved, so the validator must too.
        raise Indeterminate("schema-unreadable",
                            f"{path} uses keyword(s) this stdlib validator does not implement: {', '.join(bad)}")
    return schema


def _is_type(v: Any, t: str) -> bool:
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    if t == "integer":
        if isinstance(v, bool):
            return False
        return isinstance(v, int) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return False


def _json_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def validate(schema: Dict[str, Any], inst: Any, path: str = "") -> List[Tuple[str, str]]:
    """The draft 2020-12 subset the learning-record schema uses. Errors are (json-pointer, what)."""
    errs: List[Tuple[str, str]] = []
    p = path or "/"
    if "const" in schema and not _json_equal(inst, schema["const"]):
        errs.append((p, f"must be {json.dumps(schema['const'])}"))
    if "enum" in schema and not any(_json_equal(inst, e) for e in schema["enum"]):
        errs.append((p, f"must be one of {json.dumps(schema['enum'])}"))
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(inst, x) for x in types):
            errs.append((p, f"must be of type {'/'.join(types)}"))
            return errs
    if isinstance(inst, dict):
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in inst:
                errs.append((f"{path}/{req}", "is required"))
        for k, v in inst.items():
            if k in props:
                errs += validate(props[k], v, f"{path}/{k}")
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errs.append((f"{path}/{k}", "is not allowed (additionalProperties: false)"))
                elif isinstance(ap, dict):
                    errs += validate(ap, v, f"{path}/{k}")
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < int(schema["minItems"]):
            errs.append((p, f"needs at least {schema['minItems']} item(s)"))
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(inst):
                errs += validate(schema["items"], v, f"{path}/{i}")
    if "minimum" in schema and _is_type(inst, "number") and inst < schema["minimum"]:
        errs.append((p, f"must be >= {schema['minimum']}"))
    return errs


# ------------------------------------------------------------------------- locations
class Location:
    """Where an item's record lives, derived from its anchor's findings_sink."""

    def __init__(self, repo_root: Path, sink: str):
        self.sink_text = sink
        sp = Path(sink)
        self.sink = (sp if sp.is_absolute() else (repo_root / sp)).resolve()
        self.findings_dir = self.sink.parent
        self.feature_dir = self.findings_dir.parent if self.findings_dir.name == "findings" else self.findings_dir
        # The store is the nearest directory at or above the sink that carries a .git - but
        # never one that CONTAINS the code repo: a .git in a home directory is not a plan
        # store, and treating it as one would turn "the store is not there" (indeterminate)
        # into "the record is missing".
        self.store_root = None
        repo_root = Path(repo_root).resolve()
        for anc in [self.findings_dir] + list(self.findings_dir.parents):
            if (anc / ".git").exists():
                if anc != repo_root and anc in repo_root.parents:
                    break
                self.store_root = anc
                break

    def record_path(self, item_id: str) -> Path:
        return self.findings_dir / f"{item_id}{RECORD_SUFFIX}"


def read_record(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise Indeterminate("record-unreadable", f"{path}: {exc}") from exc
    try:
        rec = json.loads(raw)
    except ValueError as exc:
        raise Indeterminate("record-unreadable", f"{path} is not valid JSON: {exc}") from exc
    return rec


# ---------------------------------------------------------------------------- tokens
ABS_START_RE = re.compile(r"(?<![^\s(\[{<])[A-Za-z]:[\\/]")
QUOTED_ABS_RE = re.compile(r"""([`"'])([A-Za-z]:[\\/][^`"'\r\n]*?)\1""")
PATH_SEPS = "\\/"


def _clean_path(cand: str) -> str:
    c = cand.lstrip(STRIP_LEAD).rstrip(STRIP_TRAIL)
    return LINE_SUFFIX_RE.sub("", c.split("#", 1)[0]).replace("\\", "/")


def _isdir(text: str) -> bool:
    if not text or text[-1].isspace():
        return False
    try:
        return Path(text).is_dir()
    except (OSError, ValueError):
        return False


def _classify_abs(span: str) -> Optional[str]:
    """An absolute span is a PATH token when it exists or its last segment has a file
    extension (the rule every other path token follows); otherwise it is prose."""
    c = _clean_path(span)
    if not c:
        return None
    try:
        if Path(c).exists():
            return c
    except (OSError, ValueError):
        pass
    return c if PATH_EXT_RE.search(c.rsplit("/", 1)[-1]) else None


def _absolute_spans(text: str) -> Tuple[str, List[str], bool, List[Tuple[str, str]]]:
    r"""(the text with absolute drive paths blanked out, those paths, whether a blanked span was prose).

    vwm-p1b round 3 (R2-A), a DETERMINISTIC rule. The repo and the store may live under a
    directory whose name has a space, so a whitespace split cut absolute paths in two.
      - A drive path in backticks or quotes (`...`, "...", '...') is taken whole.
      - Otherwise the path starts at `X:\` / `X:/` and runs to the next whitespace. It crosses
        that whitespace ONLY when the text up to the next separator after it is an existing
        DIRECTORY (`C:\Some` is not, `C:\Some Dir` is), and then keeps walking. The last
        component ends at the next whitespace. Nothing is guessed from file extensions or a
        word window. (A FILE name containing a space is therefore cut at the space.)
    The span is then a path token if it exists or ends in a file extension, else prose. A prose
    span whose PARENT directory exists is returned with that parent (4th value), so the caller
    can still refuse it when the parent lies outside the repo and the store.
    """
    buf = list(text)
    found: List[str] = []
    parents: List[Tuple[str, str]] = []
    prose = False

    def take(i: int, j: int, span: str) -> None:
        nonlocal prose
        c = _classify_abs(span)
        if c is None:
            prose = True
            cp = _clean_path(span)
            if "/" in cp.rstrip("/"):
                parent = cp.rstrip("/").rsplit("/", 1)[0]
                if _isdir(parent + "/" if parent.endswith(":") else parent):
                    parents.append((cp, parent))
        else:
            found.append(c)
        for k in range(i, j):
            buf[k] = " "

    for m in QUOTED_ABS_RE.finditer(text):
        take(m.start(), m.end(), m.group(2))
    work = "".join(buf)
    n = len(work)
    pos = 0
    while True:
        m = ABS_START_RE.search(work, pos)
        if not m:
            break
        i, j = m.start(), m.end()
        while j < n:
            ch = work[j]
            if ch in PATH_SEPS:
                j += 1
                continue
            if ch.isspace():
                # (a) the text up to the next separator is an existing directory: cross
                k = j
                while k < n and work[k] not in PATH_SEPS and work[k] not in "\r\n":
                    k += 1
                if k < n and work[k] in PATH_SEPS and _isdir(work[i:k]):
                    j = k
                    continue
                # (b) the next word ENDS an existing directory (the path's last component)
                k = j
                while k < n and work[k] in " \t":
                    k += 1
                while k < n and not work[k].isspace() and work[k] not in PATH_SEPS:
                    k += 1
                if k > j and _isdir(work[i:k].rstrip(STRIP_TRAIL)):
                    j = k
                    continue
                break
            j += 1
        take(i, j, work[i:j])
        work = "".join(buf)
        pos = j
    return "".join(buf), found, prose, parents


def tokens_of(text: str) -> Tuple[List[str], List[str], bool]:
    """(sha tokens, path tokens, has_prose). Prose = anything left once both are taken out."""
    shas: List[str] = []
    paths: List[str] = []
    prose = False
    rest_text, abs_paths, abs_prose, _parents = _absolute_spans(str(text))
    paths += abs_paths
    prose = prose or abs_prose
    for raw in rest_text.split():
        tok = raw.lstrip(STRIP_LEAD).rstrip(STRIP_TRAIL)
        if not tok:
            continue
        if "://" in tok:
            prose = True
            continue
        # A backslash path is a path (vwm-p1b: `scripts\x.py` and `C:\...` used to be prose).
        cand = LINE_SUFFIX_RE.sub("", tok.split("#", 1)[0]).replace("\\", "/")
        if "/" in cand and PATH_EXT_RE.search(cand):
            paths.append(cand)
            continue
        found = [m.lower() for m in SHA_RE.findall(tok) if re.search(r"\d", m)]
        if found:
            shas += found
            rest = SHA_RE.sub("", tok).strip(".")
            if rest:
                prose = True
        else:
            prose = True
    return shas, paths, prose


def evidence_strings(rec: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    oc = rec.get("outcome") if isinstance(rec.get("outcome"), dict) else {}
    for i, e in enumerate(oc.get("evidence") or [] if isinstance(oc.get("evidence"), list) else []):
        if isinstance(e, str):
            out.append((f"outcome.evidence[{i}]", e))
    hr = rec.get("hypotheses_refuted")
    if isinstance(hr, list):
        for i, h in enumerate(hr):
            if isinstance(h, dict) and isinstance(h.get("evidence"), str):
                out.append((f"hypotheses_refuted[{i}].evidence", h["evidence"]))
    return out


def _strings(node: Any, where: str = "") -> Iterable[Tuple[str, str]]:
    if isinstance(node, str):
        yield where or "/", node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{where}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{where}/{i}")


# ------------------------------------------------------------------- pass evidence
# The SAME reading queue.ps1 -Pass applies (Get-FenceWalk / Get-EvidenceVerdicts): a case is a
# Markdown H2 that starts with the case id; it reads PASS only when its heading line ENDS in
# the bare, case-sensitive token PASS; fenced blocks count for nothing.
CASE_HEADING_RE = re.compile(r"^##\s+(T\d+|Case\s+\d+)\b", re.IGNORECASE)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
VERDICT_WORD_RE = re.compile(r"(^|\s)(PASS|FAIL|FAILED|SKIP|SKIPPED|SCOPED|PARTIAL|BLOCKED|DEFERRED|NOT RUN|N/A)\b.*$")


def _normalize_case_id(raw: str) -> str:
    r = re.sub(r"\s+", " ", raw).strip()
    m = re.match(r"^[Tt](\d+)$", r)
    if m:
        return "T" + m.group(1)
    m = re.match(r"^[Cc][Aa][Ss][Ee] (\d+)$", r)
    if m:
        return "Case " + m.group(1)
    return r


def evidence_verdicts(text: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    fence = ""
    for line in re.split(r"\r?\n", text):
        fm = FENCE_RE.match(line)
        if fm:
            marker = fm.group(1)[0]
            if not fence:
                fence = marker
                continue
            if fence == marker:
                fence = ""
                continue
        if fence:
            continue
        m = CASE_HEADING_RE.match(line)
        if not m:
            continue
        trimmed = line.rstrip()
        verdict = "(no verdict on the heading line)"
        if re.search(r"(^|\s)PASS$", trimmed):
            verdict = "PASS"
        else:
            vm = VERDICT_WORD_RE.search(trimmed)
            if vm:
                verdict = vm.group(0).strip()
        rows.append({"case": _normalize_case_id(m.group(1)), "verdict": verdict})
    return rows


def decode_like_pass(raw: bytes) -> str:
    """Decode evidence bytes the way queue.ps1 -Pass reads them ([IO.File]::ReadAllText(path,
    UTF8)): a byte-order mark decides (UTF-32 LE/BE, UTF-16 LE/BE, UTF-8), else UTF-8.

    vwm-p1b round 2 (F2): PS 5.1's Out-File and `>` write UTF-16LE with a BOM. -Pass read such
    a file fine and recorded the pass; reading it as UTF-8 found no case heading and refused a
    genuine pass.
    """
    boms = ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
            (b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))
    for bom, enc in boms:
        if raw.startswith(bom):
            return raw[len(bom):].decode(enc, errors="replace")
    return raw.decode("utf-8", errors="replace")


def read_evidence_text(path: Path) -> str:
    return decode_like_pass(Path(path).read_bytes())


def _attempt_no(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def check_pass_evidence(item: Dict[str, Any], item_id: str, p: Dict[str, Any], queue_dir: Path,
                        label: str) -> List[Finding]:
    """Is the pass's evidence THAT pass's, non-empty, and PASS on every case? (vwm-p1b)

    Where it is: `results[].evidence` when that is an ABSOLUTE path to a file, else the
    queue's `<id>.attempt<N>.evidence.md` for the pass's attempt. A relative or inline string
    is never resolved against the current directory: -Pass stores an absolute path into the
    queue for every file it was given, so a relative string was prose.
    """
    qd = Path(queue_dir).resolve()
    att = _attempt_no(p.get("attempt"))
    ev = str(p.get("evidence") or "")
    cand: Optional[Path] = None
    if ev and "\n" not in ev and len(ev) < 1024:
        try:
            pe = Path(ev)
            if pe.is_absolute() and pe.is_file():
                cand = pe.resolve()
        except (OSError, ValueError):
            cand = None
    if cand is None and att is not None:
        q = qd / f"{item_id}.attempt{att}.evidence.md"
        if q.is_file():
            cand = q
    if cand is None:
        return [Finding("green-not-genuine", f"the pass at {label} has no evidence file "
                                             f"(neither '{ev[:80]}' nor {item_id}.attempt{att}.evidence.md)")]
    out: List[Finding] = []
    # BOUND TO THIS PASS. -Pass copies the evidence into the queue as <id>.attempt<N>.evidence.md.
    if not _same_path(cand.parent, qd):
        out.append(Finding("evidence-not-pass", f"the pass's evidence {cand} is not in the queue directory; "
                                                "-Pass copies a pass's evidence beside the item"))
    m = re.match(r"^" + re.escape(item_id) + r"\.attempt(\d+)\.evidence\.md$", cand.name, re.IGNORECASE)
    if not m:
        out.append(Finding("evidence-not-pass", f"the pass's evidence {cand.name} is not one of this item's "
                                                f"attempt evidence files ({item_id}.attempt<N>.evidence.md)"))
    else:
        k = int(m.group(1))
        if att is not None and k != att:
            out.append(Finding("evidence-not-pass", f"the pass is attempt {att} but its evidence is attempt {k}'s file ({cand.name})"))
        for r in (item.get("results") or []):
            if not isinstance(r, dict) or r is p or str(r.get("verdict", "")).lower() == "pass":
                continue
            rev = str(r.get("evidence") or "")
            same = False
            try:
                same = bool(rev) and "\n" not in rev and len(rev) < 1024 and Path(rev).is_absolute() and _same_path(Path(rev), cand)
            except (OSError, ValueError):
                same = False
            if same or _attempt_no(r.get("attempt")) == k:
                out.append(Finding("evidence-not-pass", f"{cand.name} is the evidence of a '{r.get('verdict')}' verdict "
                                                        f"(attempt {r.get('attempt')}, by {r.get('by')}), not of the pass"))
                break
    try:
        text = read_evidence_text(cand)
    except OSError as exc:
        raise Indeterminate("queue-unreadable", f"cannot read the pass's evidence {cand}: {exc}") from exc
    if not text.strip():
        out.append(Finding("evidence-empty", f"the pass's evidence {cand.name} is empty"))
        return out
    rows = evidence_verdicts(text)
    if not rows:
        out.append(Finding("evidence-verdict-mismatch", f"the pass's evidence {cand.name} carries no case heading "
                                                        "('## T1 - ...   PASS'), so nothing in it says a case passed"))
        return out
    bad = [f"{r['case']}: {r['verdict']}" for r in rows if r["verdict"] != "PASS"]
    if bad:
        out.append(Finding("evidence-verdict-mismatch", f"the pass's evidence {cand.name} does not read PASS on every case: "
                                                        + "; ".join(bad[:6]) + (" ..." if len(bad) > 6 else "")))
    recorded = p.get("cases")
    if isinstance(recorded, list) and recorded:
        rec_rows = [(_normalize_case_id(str(c.get("case", ""))), str(c.get("verdict", "")))
                    for c in recorded if isinstance(c, dict)]
        file_rows = [(r["case"], r["verdict"]) for r in rows]
        if rec_rows != file_rows:
            out.append(Finding("evidence-verdict-mismatch",
                               f"the case verdicts in {cand.name} ({', '.join(f'{c} {v}' for c, v in file_rows[:6])}) "
                               f"are not the ones -Pass recorded ({', '.join(f'{c} {v}' for c, v in rec_rows[:6])})"))
    return out


# ------------------------------------------------------------------- return reasons
def _norm_text(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", str(s).lower()).strip()


REASON_PREFIX = 160
ENUM_RETURN_RE = re.compile(r"(?:^|\s)(\d+)\)\s+(" + "|".join(RETURN_KINDS) + r")\b")


KIND_TOKEN_RE = re.compile(r"(?<![A-Za-z-])(" + "|".join(RETURN_KINDS) + r")(?![A-Za-z-])", re.IGNORECASE)
ENTRY_RE = re.compile(r"(?<!\S)(\d+)\)\s+(" + "|".join(RETURN_KINDS) + r")(?![A-Za-z-])", re.IGNORECASE)
REASON_PLACEHOLDER = " <reason> "


def _norm_with_map(s: str) -> Tuple[str, List[int]]:
    """_norm_text(s) plus, for every character of it, the index in `s` it came from (-1 for a
    separator space). Same rule as _norm_text: lowercase, any run of non [0-9a-z] is one space."""
    out: List[str] = []
    idx: List[int] = []
    for i, ch in enumerate(s):
        for c in ch.lower():
            if ("0" <= c <= "9") or ("a" <= c <= "z"):
                out.append(c)
                idx.append(i)
            elif out and out[-1] != " ":
                out.append(" ")
                idx.append(-1)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _reason_variants(reason: str) -> List[str]:
    full = _norm_text(reason)
    if not full:
        return []
    out = [full]
    if len(full) > REASON_PREFIX:
        cut = full[:REASON_PREFIX]
        if full[REASON_PREFIX] != " " and " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        cut = cut.strip()
        if cut:
            out.append(cut)
    return out


def _blank_reasons(red: str, rets: List[Dict[str, Any]]) -> str:
    """`red` with each queue return's reason replaced by REASON_PLACEHOLDER (vwm-p1b round 3,
    R2-B), deterministically:
      - one EXACT contiguous span per return (normalised as _norm_text: case, whitespace,
        quotes and punctuation), aligned on word boundaries, at most once per return (a
        reason the queue holds twice is blanked twice), first free occurrence wins;
      - an occurrence that would cut a return-kind token or a numbered entry (`2) tester-fail`)
        in part is not used: a reason may CONTAIN those whole, never share them with text
        outside it;
      - the placeholder keeps a space on both sides, so nothing fuses across it.
    """
    norm, idx = _norm_with_map(red)
    protected = [(m.start(), m.end()) for m in KIND_TOKEN_RE.finditer(red)]
    protected += [(m.start(), m.end()) for m in ENTRY_RE.finditer(red)]
    chosen: List[Tuple[int, int]] = []
    for r in rets:
        for v in _reason_variants(str(r.get("reason") or "")):
            placed = False
            start = 0
            while True:
                p = norm.find(v, start)
                if p < 0:
                    break
                start = p + 1
                end = p + len(v)
                if (p > 0 and norm[p - 1] != " ") or (end < len(norm) and norm[end] != " "):
                    continue
                rs, re_ = idx[p], idx[end - 1] + 1
                if any(rs < ce and cs < re_ for cs, ce in chosen):
                    continue
                if any(rs < pe and ps < re_ and not (rs <= ps and pe <= re_) for ps, pe in protected):
                    continue
                chosen.append((rs, re_))
                placed = True
                break
            if placed:
                break
    out = red
    for rs, re_ in sorted(chosen, reverse=True):
        out = out[:rs] + REASON_PLACEHOLDER + out[re_:]
    return out


def check_return_reasons(rec: Dict[str, Any], rets: List[Dict[str, Any]]) -> List[Finding]:
    """`red` must carry each return's reason from the queue, and list exactly the queue's
    returns (vwm-p1b). `draft` writes them as `N) <kind> ... by <who>: <reason>`.

    Presence: each reason's first REASON_PREFIX normalised characters appear in `red`.
    Invariant (round 3): with the queue's reasons blanked out (_blank_reasons), the return-kind
    tokens left in `red`, and the numbered entries `N) <kind>`, match the queue's returns kind
    for kind (the entries in queue order) - `draft` writes exactly one of each per return,
    outside the reason. Free prose that invents a return WITHOUT a kind name or a numbered
    entry is the declared blind spot.
    """
    red = str(rec.get("red") or "")
    nred = " " + _norm_text(red) + " "
    out: List[Finding] = []
    for i, r in enumerate(rets, 1):
        reason = _norm_text(r.get("reason") or "")[:REASON_PREFIX].strip()
        if reason and (" " + reason) not in nred:
            out.append(Finding("return-reason-mismatch",
                               f"return {i} ({r['kind']} by {r.get('by')} on '{r.get('item')}') has the queue reason "
                               f"'{str(r.get('reason'))[:100]}', which `red` does not carry"))
    scan = _blank_reasons(red, rets)
    have = {k: sum(1 for r in rets if r["kind"] == k) for k in RETURN_KINDS}
    named = {k: 0 for k in RETURN_KINDS}
    for m in KIND_TOKEN_RE.finditer(scan):
        named[m.group(1).lower()] += 1
    for k in RETURN_KINDS:
        if named[k] != have[k]:
            out.append(Finding("return-reason-mismatch",
                               f"outside the queue's quoted reasons, `red` names {k} {named[k]} time(s); "
                               f"the queue shows {have[k]} {k} return(s) (draft writes one per return)"))
    listed = [m.group(2).lower() for m in ENTRY_RE.finditer(scan)]
    if listed != [r["kind"] for r in rets]:
        out.append(Finding("return-reason-mismatch",
                           f"`red` lists the returns as ({', '.join(listed) or 'none'}); the queue shows "
                           f"({', '.join(r['kind'] for r in rets) or 'none'}) - copy the numbered list `draft` prints"))
    return out


# ------------------------------------------------------------------------------ check
class Finding:
    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}"


def _parse_range(text: str) -> Optional[Tuple[str, str]]:
    t = (text or "").strip()
    if "..." in t:
        return None
    parts = t.split("..")
    if len(parts) != 2 or not parts[0] or not parts[1] or any(c.isspace() for c in t):
        return None
    return parts[0], parts[1]


def check_record(rec: Dict[str, Any], rec_path: Path, item: Dict[str, Any], item_id: str, *,
                 git: Git, schema: Dict[str, Any], queue_dir: Path, repo_root: Path,
                 store_root: Optional[Path], merge_sha: str = "", reviewer: str = "",
                 enforce_merge_fields: bool = True) -> Tuple[List[Finding], Dict[str, Any]]:
    """Every named check on one record. Returns (findings, info for the acceptance line)."""
    f: List[Finding] = []
    info: Dict[str, Any] = {"prose": [], "resolved_shas": 0, "resolved_paths": 0}

    # (c) the schema, from the file
    if not isinstance(rec, dict):
        return [Finding("schema:/", "the record is not a JSON object")], info
    for ptr, what in validate(schema, rec):
        f.append(Finding(f"schema:{ptr}", what))

    # placeholders left by `draft`
    for ptr, s in _strings(rec):
        if PLACEHOLDER in s:
            f.append(Finding("placeholder-unfilled", f"{ptr} still carries a {PLACEHOLDER} marker from `draft`"))

    # (d) derived fields
    sr = rec.get("source_ref") if isinstance(rec.get("source_ref"), dict) else {}
    qid = sr.get("queue_item_id")
    if qid != item_id:
        f.append(Finding("item-mismatch", f"source_ref.queue_item_id is {json.dumps(qid)}, the item is '{item_id}'"))
    if rec.get("fidelity") != "iterative" or rec.get("producer") != "harness":
        f.append(Finding("item-mismatch", "a harness merge record is fidelity 'iterative', producer 'harness' "
                                          f"(got {json.dumps(rec.get('fidelity'))}, {json.dumps(rec.get('producer'))})"))

    rets, chain_notes = chain_returns(item, item_id, queue_dir)
    info["chain"] = chain_notes
    own = [r for r in rets if r.get("item") == item_id]
    want = len(rets)
    got = rec.get("iterations")
    # 1.0 is the integer 1 in JSON Schema (and the schema validated it), so it counts as 1.
    if isinstance(got, float) and not isinstance(got, bool) and got.is_integer():
        got = int(got)
    if not (isinstance(got, int) and not isinstance(got, bool) and got == want):
        kinds = ", ".join(r["kind"] for r in rets) or "none"
        if len(own) != want and got == len(own):
            f.append(Finding("predecessor-returns",
                             f"record says {json.dumps(got)}, which counts only '{item_id}'s own returns; "
                             f"{'; '.join(chain_notes)}, so the queue shows {want} return(s) across the chain "
                             f"({kinds}); a continued item carries its predecessor's returns"))
        else:
            f.append(Finding("iterations-mismatch",
                             f"record says {json.dumps(got)}, the queue shows {want} return(s) ({kinds})"
                             + (f" across the chain ({'; '.join(chain_notes)})" if chain_notes else "")
                             + "; iterations counts ALL returns, improvement send-backs included (D2/D3)"))
    f += check_return_reasons(rec, rets)

    tested = str(item.get("tested_at_sha") or "")
    merge = merge_sha or str(item.get("merged_sha") or "")
    developer = item.get("developer", "")
    oc = rec.get("outcome") if isinstance(rec.get("outcome"), dict) else {}

    if enforce_merge_fields:
        # green must be GENUINE (D4)
        gproblems: List[str] = []
        if oc.get("kind") != "green":
            gproblems.append(f"outcome.kind is {json.dumps(oc.get('kind'))}; a merge records green")
        results = [r for r in (item.get("results") or []) if isinstance(r, dict)]
        if not tested:
            gproblems.append("the item records no tested_at_sha")
        if not results:
            gproblems.append("the queue holds no tester verdict at all")
        else:
            last = results[-1]
            if str(last.get("verdict", "")).lower() != "pass":
                gproblems.append(f"the queue's LAST verdict is '{last.get('verdict')}' "
                                 f"(attempt {last.get('attempt')}, by {last.get('by')}), not a pass")
            passes = [r for r in results if str(r.get("verdict", "")).lower() == "pass"
                      and tested and str(r.get("sha", "")) == tested]
            if tested and not passes:
                gproblems.append(f"no tester PASS is recorded at tested_at_sha {tested[:12]}")
            if passes:
                p = passes[-1]
                if normalize_id(p.get("by")) == normalize_id(developer):
                    gproblems.append(f"the pass at {tested[:12]} was recorded by '{p.get('by')}', "
                                     f"the developer identity ('{developer}')")
                f += check_pass_evidence(item, item_id, p, queue_dir, tested[:12])
        if tested and merge and git.full_commit(merge) and git.full_commit(tested):
            if not git.is_ancestor(tested, merge):
                gproblems.append(f"tested_at_sha {tested[:12]} is not an ancestor of the merge {merge[:12]}")
        for g in gproblems:
            f.append(Finding("green-not-genuine", g))

        # countersign (D4)
        rc = rec.get("reviewer_check") if isinstance(rec.get("reviewer_check"), dict) else None
        rev = reviewer_of(item, reviewer)
        if rc is None:
            f.append(Finding("countersign-missing", "no reviewer_check: the reviewer has not countersigned"))
        else:
            if rc.get("checked") is not True:
                f.append(Finding("countersign-missing", "reviewer_check.checked is not true"))
            by = rc.get("by")
            if developer and normalize_id(by) == normalize_id(developer):
                f.append(Finding("countersign-missing", f"reviewer_check.by '{by}' is the developer"))
            elif not rev:
                f.append(Finding("countersign-missing", "the queue names no reviewer for this item"))
            elif normalize_id(by) != normalize_id(rev):
                f.append(Finding("countersign-missing", f"reviewer_check.by is '{by}', the item's reviewer is '{rev}'"))

        # merge_range
        mr = str(sr.get("merge_range") or "").strip()
        if not mr:
            f.append(Finding("range-unresolved", "source_ref.merge_range is empty (allowed only when parked)"))
        else:
            ends = _parse_range(mr)
            if not ends:
                f.append(Finding("range-unresolved", f"merge_range '{mr}' is not base..head"))
            else:
                types = git.object_types(list(ends))
                bad = [e for e in ends if types.get(e) != "commit"]
                if bad:
                    f.append(Finding("range-unresolved", f"merge_range end(s) not a commit here: {', '.join(bad)}"))
                else:
                    base_full, head_full = git.full_commit(ends[0]), git.full_commit(ends[1])
                    if not git.is_ancestor(base_full, head_full):
                        f.append(Finding("range-unresolved", f"base {ends[0]} is not an ancestor of head {ends[1]}"))
                    allowed = {x for x in (git.full_commit(tested) if tested else None,
                                           git.full_commit(merge) if merge else None) if x}
                    if head_full not in allowed:
                        f.append(Finding("range-head-mismatch",
                                         f"head {ends[1]} is neither tested_at_sha {tested[:12] or '(none)'} "
                                         f"nor the merge {merge[:12] or '(none)'}"))

    # (e) evidence tokens
    feature_dir = rec_path.parent.parent if rec_path.parent.name == "findings" else rec_path.parent
    roots = [feature_dir, rec_path.parent]
    if store_root:
        roots.append(store_root)
    roots.append(repo_root)
    revs = [x for x in (merge, tested, (_parse_range(str(sr.get("merge_range") or "")) or ("", ""))[1]) if x]
    all_shas: Dict[str, List[str]] = {}
    all_paths: Dict[str, List[str]] = {}
    abs_outside: List[Tuple[str, str]] = []
    for where, text in evidence_strings(rec):
        for span, parent in _absolute_spans(text)[3]:
            if not any(_within(Path(parent), b) for b in [x for x in (repo_root, store_root or feature_dir) if x]):
                abs_outside.append((span, where))
        shas, paths, prose = tokens_of(text)
        for s in shas:
            all_shas.setdefault(s, []).append(where)
        for p in paths:
            all_paths.setdefault(p, []).append(where)
        if prose:
            info["prose"].append(where)
    if all_shas:
        res = git.sha_resolves(list(all_shas))
        for s, ok in res.items():
            if ok:
                info["resolved_shas"] += 1
            else:
                f.append(Finding("evidence-sha-unresolved", f"{s} (in {', '.join(all_shas[s])}) is not an object in this repo"))
    # A path resolves only INSIDE the code repo or the plan store (vwm-p1b: an absolute path
    # anywhere on disk - C:/Windows/notepad.exe - used to count as resolved evidence).
    bases = [b for b in (repo_root, store_root or feature_dir) if b]
    missing_paths = []
    for p in all_paths:
        pp = Path(p)
        absolute = pp.is_absolute() or p.startswith("/") or bool(re.match(r"^[A-Za-z]:", p))
        cands = [pp] if absolute else [r / pp for r in roots]
        ok = False
        outside = False
        for c in cands:
            try:
                rc = Path(os.path.realpath(str(c)))
                exists = rc.exists()
            except (OSError, ValueError):
                continue
            if not any(_within(rc, b) for b in bases):
                outside = outside or exists or absolute
                continue
            if exists:
                ok = True
                break
        if ok:
            info["resolved_paths"] += 1
        elif outside:
            f.append(Finding("evidence-path-outside",
                             f"{p} (in {', '.join(all_paths[p])}) is outside the code repo and the plan store; "
                             "evidence must point at something the next reader of this record can open"))
        else:
            missing_paths.append(p)
    if missing_paths and revs:
        tree = git.object_types([f"{r}:{p}" for r in revs for p in missing_paths])
        still = []
        for p in missing_paths:
            if any(tree.get(f"{r}:{p}") for r in revs):
                info["resolved_paths"] += 1
            else:
                still.append(p)
        missing_paths = still
    for span, where in abs_outside:
        f.append(Finding("evidence-path-outside",
                         f"{span} (in {where}) lies in a directory outside the code repo and the plan store"))
    for p in missing_paths:
        f.append(Finding("evidence-path-missing",
                         f"{p} (in {', '.join(all_paths[p])}) exists under none of: feature dir, store root, "
                         "repo root, or the merged/tested tree"))
    return f, info


def _blind_spot_line(info: Dict[str, Any]) -> str:
    n = len(info["prose"])
    return (f"  BLIND SPOT (declared): {n} evidence string(s) carry prose this check cannot verify"
            + (f" ({', '.join(info['prose'][:6])}{', ...' if n > 6 else ''})" if n else "")
            + f"; resolved {info['resolved_shas']} sha(s) and {info['resolved_paths']} path(s).")


def run_check(args) -> int:
    repo = resolve_repo(args.repo)
    queue_dir = Path(args.queue_dir) if args.queue_dir else default_queue_dir(repo)
    print(f"learning-record check: {args.item}")
    try:
        item = load_item(queue_dir, args.item)
        rets, chain_notes = chain_returns(item, args.item, queue_dir)
        for n in chain_notes:
            print(f"  CHAIN: {n}; its returns count toward this item's record")
        if not rets:
            print("  NOT REQUIRED: the queue shows no return (no tester fail, no reviewer reject/requeue, "
                  "no send-back after a pass" + (", none on the predecessor chain" if chain_notes else "") + "). Accepted.")
            return 0
        print(f"  REQUIRED: {len(rets)} return(s): " + "; ".join(
            f"{r['kind']} by {r['by']}" + (f" (on '{r['item']}')" if r.get("item") != args.item else "") for r in rets))
        anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
        sink = str(anchor.get("findings_sink") or "").strip()
        if not sink:
            print("  REFUSED (1):\n    - sink-missing: the item's anchor has no findings_sink, so there is "
                  "nowhere the record can be")
            return 1
        loc = Location(repo, sink)
        if loc.store_root is None:
            raise Indeterminate("store-unreadable",
                                f"no plan store (no .git) at or above {loc.findings_dir} - cannot tell whether a record exists")
        rp = loc.record_path(args.item)
        print(f"  record: {rp}")
        if not rp.exists():
            print(f"  REFUSED (1):\n    - record-missing: no {rp.name} beside the findings sink ({loc.findings_dir})")
            print(f"    Generate the factual fields with: python scripts/agent-harness/learning_records.py draft --item {args.item}")
            return 1
        if not rp.is_file():
            raise Indeterminate("record-unreadable", f"{rp} exists but is not a file")
        rec = read_record(rp)
        schema = load_schema(Path(args.schema) if args.schema else loc.store_root / SCHEMA_REL)
        git = Git(repo)
        findings, info = check_record(rec, rp, item, args.item, git=git, schema=schema, queue_dir=queue_dir,
                                      repo_root=repo, store_root=loc.store_root, merge_sha=args.merge_sha or "",
                                      reviewer=args.reviewer or "")
    except Indeterminate as exc:
        print("  REFUSED, INDETERMINATE (this is NOT a missing record - the check could not read what it needs):")
        print(f"    - {exc.reason}: {exc.detail}")
        return 3
    if findings:
        print(f"  REFUSED ({len(findings)}):")
        for x in findings:
            print(f"    - {x}")
        return 1
    print(f"  ACCEPTED: schema-valid, derived fields agree with the queue and git (iterations {len(rets)}).")
    print(_blind_spot_line(info))
    return 0


# ------------------------------------------------------------------------------ audit
def run_audit(args) -> int:
    repo = resolve_repo(args.repo)
    queue_dir = Path(args.queue_dir) if args.queue_dir else default_queue_dir(repo)
    store = Path(args.store).resolve() if args.store else (repo / DEFAULT_STORE_REL).resolve()
    print(f"learning-record AUDIT (read-only)\n  store: {store}\n  queue: {queue_dir}\n  repo : {repo}")
    try:
        if not store.is_dir():
            raise Indeterminate("store-unreadable", f"store '{store}' is not a directory")
        schema = load_schema(Path(args.schema) if args.schema else store / SCHEMA_REL)
        git = Git(repo)
    except Indeterminate as exc:
        print(f"AUDIT INDETERMINATE: {exc.reason}: {exc.detail}")
        return 3
    records = sorted(store.rglob(f"*{RECORD_SUFFIX}"))
    totals: Dict[str, int] = {}
    clean = 0
    bad_records = 0
    seen_ids = set()

    def emit(rel: str, reason: str, detail: str) -> None:
        key = "schema" if reason.startswith("schema:") else reason
        totals[key] = totals.get(key, 0) + 1
        print(f"VIOLATION {rel} {reason}: {detail}")

    for rp in records:
        rel = rp.relative_to(store).as_posix()
        try:
            rec = read_record(rp)
        except Indeterminate as exc:
            bad_records += 1
            emit(rel, exc.reason, exc.detail)
            continue
        sr = rec.get("source_ref") if isinstance(rec, dict) and isinstance(rec.get("source_ref"), dict) else {}
        item_id = str(sr.get("queue_item_id") or rp.name[: -len(RECORD_SUFFIX)])
        seen_ids.add(item_id)
        found: List[Finding] = []
        try:
            item = load_item(queue_dir, item_id)
        except Indeterminate as exc:
            bad_records += 1
            emit(rel, "item-mismatch", f"no readable queue item '{item_id}' ({exc.detail})")
            continue
        anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
        sink = str(anchor.get("findings_sink") or "").strip()
        if not sink:
            found.append(Finding("sink-missing", "the item's anchor has no findings_sink"))
        else:
            want = Location(repo, sink).record_path(item_id)
            if want.resolve() != rp.resolve():
                found.append(Finding("item-mismatch", f"record sits at {rel}; the anchor's sink puts it at {want}"))
        state = str(item.get("state", ""))
        merged = state in ("merged", "deployed")
        try:
            required = record_required(item, item_id, queue_dir)
        except Indeterminate as exc:
            bad_records += 1
            emit(rel, exc.reason, exc.detail)
            continue
        if not required:
            print(f"NOTE {rel}: the queue shows no return for '{item_id}' (record not required); checked anyway")
        try:
            more, info = check_record(rec, rp, item, item_id, git=git, schema=schema, queue_dir=queue_dir,
                                      repo_root=repo, store_root=store,
                                      merge_sha=str(item.get("merged_sha") or ""), reviewer="",
                                      enforce_merge_fields=merged)
        except Indeterminate as exc:
            bad_records += 1
            emit(rel, exc.reason, exc.detail)
            continue
        if not merged:
            print(f"NOTE {rel}: item state '{state}' - green/countersign/range checks need a merge and were skipped")
        found += more
        if found:
            bad_records += 1
            for x in found:
                emit(rel, x.reason, x.detail)
        else:
            clean += 1
            print(f"CLEAN {rel} (iterations {derived_iterations(item, item_id, queue_dir)}; {len(info['prose'])} prose evidence string(s) unverified)")

    # Items that needed a record and have none, merged since the rule existed.
    since = int(_dt.datetime.fromisoformat(args.since).replace(tzinfo=_dt.timezone.utc).timestamp())
    missing = 0
    for qp in sorted(queue_dir.glob("*.json")):
        if qp.name.endswith(".anchor.json"):
            continue
        try:
            item = json.loads(qp.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(item, dict) or "history" not in item:
            continue
        iid = str(item.get("id") or qp.stem)
        if iid in seen_ids or str(item.get("state")) not in ("merged", "deployed"):
            continue
        if merged_at(item) < since:
            continue
        try:
            n_ret = derived_iterations(item, iid, queue_dir)
        except Indeterminate as exc:
            emit(f"(queue item {iid})", exc.reason, exc.detail)
            continue
        if not n_ret:
            continue
        missing += 1
        emit(f"(queue item {iid})", "record-missing",
             f"merged with {n_ret} return(s) and no learning record in the store")

    nviol = sum(totals.values())
    print(f"TOTALS: {len(records)} record(s); {clean} clean; {bad_records} with violations; "
          f"{missing} required-but-missing item(s) merged since {args.since}; {nviol} violation line(s)")
    for k in sorted(totals):
        print(f"  {k}: {totals[k]}")
    return 1 if nviol else 0


# ------------------------------------------------------------------------------ draft
def run_draft(args) -> int:
    """Print the record's derived skeleton. Writes NOTHING."""
    repo = resolve_repo(args.repo)
    queue_dir = Path(args.queue_dir) if args.queue_dir else default_queue_dir(repo)
    try:
        item = load_item(queue_dir, args.item)
        git = Git(repo)
    except Indeterminate as exc:
        print(f"draft INDETERMINATE: {exc.reason}: {exc.detail}", file=sys.stderr)
        return 3
    try:
        rets, chain_notes = chain_returns(item, args.item, queue_dir)
    except Indeterminate as exc:
        print(f"draft INDETERMINATE: {exc.reason}: {exc.detail}", file=sys.stderr)
        return 3
    tested = str(item.get("tested_at_sha") or "")
    merge = args.merge_sha or str(item.get("merged_sha") or "")
    if merge:
        head = git.full_commit(merge) or merge
        base = git.first_parent(head) or ""
    else:
        head = tested
        line = str(item.get("line") or "")
        base = git.merge_base(line, tested) if (line and tested) else ""
    anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
    goal = str(anchor.get("goal") or "").strip()
    results = [r for r in (item.get("results") or []) if isinstance(r, dict)]
    passes = [r for r in results if str(r.get("verdict", "")).lower() == "pass" and tested and r.get("sha") == tested]
    last_pass = passes[-1] if passes else None
    lines = []
    for n, r in enumerate(rets, 1):
        at = f" at attempt {r['attempt']}" if r.get("attempt") else ""
        on = f" on {r['item']}" if r.get("item") != args.item else ""
        lines.append(f"{n}) {r['kind']}{on}{at} by {r['by']}: {r['reason'] or '(no reason recorded)'}")
    green = bool(last_pass and results and str(results[-1].get("verdict", "")).lower() == "pass")
    evidence = []
    for r in results:
        evidence.append(f"{r.get('verdict', '').upper()} attempt {r.get('attempt')} at {str(r.get('sha', ''))[:12]} by {r.get('by')}")
    if merge:
        evidence.append(f"merge {head}")
    rev = reviewer_of(item, args.reviewer or "")
    skeleton = {
        "schema_version": 1,
        "fidelity": "iterative",
        "producer": "harness",
        "source_ref": {"queue_item_id": args.item, "anchor_id": args.item,
                       "merge_range": f"{base}..{head}" if (base and head) else ""},
        "domains": [f"{PLACEHOLDER} topic tags>"],
        "steer": f"Anchor {args.item}: {goal}" if goal else f"{PLACEHOLDER} the anchor's goal + acceptance>",
        "red": ("DERIVED RETURNS (queue): " + " ".join(lines) + f" {PLACEHOLDER} what the failing state looked like>")
               if lines else f"{PLACEHOLDER} what the failing state looked like>",
        "iterations": len(rets),
        "hypotheses_refuted": [],
        "outcome": {"kind": "green" if green else "gap-remains",
                    "summary": f"{PLACEHOLDER} what landed, checked by the reviewer against the merge diff>",
                    "evidence": evidence},
        "mental_model": {"claim": f"WORKER'S CLAIM (not a verified fact): {PLACEHOLDER} the abstraction that would have skipped the iterations>",
                         "author_role": "worker"},
        "skill_candidate": f"{PLACEHOLDER} optional; delete the field if none>",
        "reviewer_check": {"checked": False, "by": rev or f"{PLACEHOLDER} reviewer id>",
                           "note": f"{PLACEHOLDER} reviewer: compare outcome.summary with the diff, then set checked true>"},
        "created_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    print(json.dumps(skeleton, indent=2, ensure_ascii=False))
    for n in chain_notes:
        print(f"# chain: {n}; its returns are counted and listed in `red`", file=sys.stderr)
    print(f"# draft for '{args.item}': {len(rets)} return(s); green genuine: {green}; nothing was written. "
          f"Fill every {PLACEHOLDER} marker; the check refuses a record that still carries one.", file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Learning-record gate (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "draft"):
        s = sub.add_parser(name)
        s.add_argument("--item", required=True)
        s.add_argument("--merge-sha", default="")
        s.add_argument("--reviewer", default="")
        s.add_argument("--queue-dir", default="")
        s.add_argument("--repo", default="")
        s.add_argument("--schema", default="")
    a = sub.add_parser("audit")
    a.add_argument("--store", default="")
    a.add_argument("--queue-dir", default="")
    a.add_argument("--repo", default="")
    a.add_argument("--schema", default="")
    a.add_argument("--since", default=RULE_SINCE)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            return run_check(args)
        if args.cmd == "audit":
            return run_audit(args)
        return run_draft(args)
    except Indeterminate as exc:
        print(f"REFUSED, INDETERMINATE: {exc.reason}: {exc.detail}")
        return 3
    except Exception as exc:  # noqa: BLE001 - the point: an unexpected crash is never a verdict
        # vwm-p1b: an uncaught exception exited 1, which queue.ps1 labels "refused" (a
        # verdict on the record). It is not one: the check did not finish. Indeterminate.
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"REFUSED, INDETERMINATE: internal-error: {type(exc).__name__}: {exc} "
              "(the check crashed; this is NOT a verdict on the record)")
        return 3


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
