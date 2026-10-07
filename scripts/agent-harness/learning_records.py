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
  - claim green only when green is GENUINE (operator D4): the queue's last verdict is a
    tester PASS at the exact tested_at_sha, recorded by someone other than the developer,
    with its evidence file present;
  - be countersigned by the item's reviewer (reviewer_check.checked and .by);
  - give a merge_range whose ends are commits, base an ancestor of head, and head the
    tested commit or the merge;
  - cite only SHAs and paths that resolve. Anything else in the evidence is prose, which
    this check cannot verify; it says so on every acceptance (the declared blind spot).

READ-ONLY. Nothing here writes, edits or fixes a record, a queue item or the plan store,
and nothing touches the plan store's git state. The only git calls are object and ancestry
reads against the CODE repository. An indeterminate read (store missing, record unreadable,
git error) is a refusal with its own reason, never a pass, and is reported apart from a
missing record.

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
)
INDETERMINATE = (
    "store-unreadable", "record-unreadable", "queue-unreadable", "schema-unreadable", "git-error",
)

#: The marker `draft` puts where a person must write. A record still carrying one is not done.
PLACEHOLDER = "<FILL:"

SHA_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])")
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


def derived_iterations(item: Dict[str, Any]) -> int:
    return len(returns_of(item))


def record_required(item: Dict[str, Any]) -> bool:
    return derived_iterations(item) > 0


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
def tokens_of(text: str) -> Tuple[List[str], List[str], bool]:
    """(sha tokens, path tokens, has_prose). Prose = anything left once both are taken out."""
    shas: List[str] = []
    paths: List[str] = []
    prose = False
    for raw in str(text).split():
        tok = raw.lstrip(STRIP_LEAD).rstrip(STRIP_TRAIL)
        if not tok:
            continue
        if "://" in tok:
            prose = True
            continue
        cand = LINE_SUFFIX_RE.sub("", tok.split("#", 1)[0])
        if "/" in cand and PATH_EXT_RE.search(cand):
            paths.append(cand)
            continue
        found = [m for m in SHA_RE.findall(tok) if re.search(r"\d", m)]
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

    rets = returns_of(item)
    want = derived_iterations(item)
    got = rec.get("iterations")
    if not (isinstance(got, int) and not isinstance(got, bool) and got == want):
        kinds = ", ".join(r["kind"] for r in rets) or "none"
        f.append(Finding("iterations-mismatch",
                         f"record says {json.dumps(got)}, the queue shows {want} return(s) ({kinds}); "
                         "iterations counts ALL returns, improvement send-backs included (D2/D3)"))

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
                ev = str(p.get("evidence") or "")
                ev_ok = False
                if ev and "\n" not in ev and len(ev) < 1024:
                    try:
                        ev_ok = Path(ev).is_file()
                    except OSError:
                        ev_ok = False
                if not ev_ok:
                    ev_ok = (Path(queue_dir) / f"{item_id}.attempt{p.get('attempt')}.evidence.md").is_file()
                if not ev_ok:
                    gproblems.append(f"the pass at {tested[:12]} has no evidence file "
                                     f"(neither '{ev[:80]}' nor {item_id}.attempt{p.get('attempt')}.evidence.md)")
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
    for where, text in evidence_strings(rec):
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
    missing_paths = []
    for p in all_paths:
        pp = Path(p)
        ok = False
        if pp.is_absolute():
            ok = pp.exists()
        else:
            ok = any((r / pp).exists() for r in roots)
        if not ok:
            missing_paths.append(p)
        else:
            info["resolved_paths"] += 1
    if missing_paths and revs:
        tree = git.object_types([f"{r}:{p}" for r in revs for p in missing_paths])
        still = []
        for p in missing_paths:
            if any(tree.get(f"{r}:{p}") for r in revs):
                info["resolved_paths"] += 1
            else:
                still.append(p)
        missing_paths = still
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
    repo = main_checkout(Path(args.repo or os.getcwd()))
    queue_dir = Path(args.queue_dir) if args.queue_dir else default_queue_dir(repo)
    print(f"learning-record check: {args.item}")
    try:
        item = load_item(queue_dir, args.item)
        rets = returns_of(item)
        if not rets:
            print("  NOT REQUIRED: the queue shows no return (no tester fail, no reviewer reject/requeue, "
                  "no send-back after a pass). Accepted.")
            return 0
        print(f"  REQUIRED: {len(rets)} return(s): " + "; ".join(
            f"{r['kind']} by {r['by']}" for r in rets))
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
    repo = main_checkout(Path(args.repo or os.getcwd()))
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
        if not record_required(item):
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
            print(f"CLEAN {rel} (iterations {derived_iterations(item)}; {len(info['prose'])} prose evidence string(s) unverified)")

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
        if merged_at(item) < since or not record_required(item):
            continue
        missing += 1
        emit(f"(queue item {iid})", "record-missing",
             f"merged with {derived_iterations(item)} return(s) and no learning record in the store")

    nviol = sum(totals.values())
    print(f"TOTALS: {len(records)} record(s); {clean} clean; {bad_records} with violations; "
          f"{missing} required-but-missing item(s) merged since {args.since}; {nviol} violation line(s)")
    for k in sorted(totals):
        print(f"  {k}: {totals[k]}")
    return 1 if nviol else 0


# ------------------------------------------------------------------------------ draft
def run_draft(args) -> int:
    """Print the record's derived skeleton. Writes NOTHING."""
    repo = main_checkout(Path(args.repo or os.getcwd()))
    queue_dir = Path(args.queue_dir) if args.queue_dir else default_queue_dir(repo)
    try:
        item = load_item(queue_dir, args.item)
        git = Git(repo)
    except Indeterminate as exc:
        print(f"draft INDETERMINATE: {exc.reason}: {exc.detail}", file=sys.stderr)
        return 3
    rets = returns_of(item)
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
        lines.append(f"{n}) {r['kind']}{at} by {r['by']}: {r['reason'] or '(no reason recorded)'}")
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


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
