"""ao-orient round 4: the fixed grammar of everything orientation emits (test helper).

Every line of a map, every "unavailable" line, every audit payload and every log record must parse
under this grammar. Each NAME SLOT must be a whole repo name (a path the repo actually has, or the
basename of one of its directories, or ".") that `safe_path` returns unchanged - never a fragment,
never an edited string. Everything else is a fixed phrase, an int, or a 7-64-hex sha.

The grammar is written here, independently of the renderer: a new f-string in the module that
emits anything else makes `check_map` fail.
"""
from __future__ import annotations

import re

from app.modules import orientation as om

HEX = r"(?:[0-9a-f]{7,64}|\(unknown\))"
LANG = "|".join(sorted({re.escape(v) for v in om.LANGUAGES.values()} | {"Dockerfile"},
                       key=len, reverse=True))
NOTE = (r"tree|tree \(truncated by the server\)|tree \(first \d+ entries\)|contents walk|"
        r"contents walk \(listing budget \d+ reached; deeper dirs not listed\)|"
        r"contents walk \(first \d+ entries\)")
FIXED_CMDS = ["python -m pytest", "tox", "npm test", "deno test", "go test ./...", "cargo test",
              "mvn test", "gradle test", "mix test", "bundle exec rspec", "composer test",
              "make test"]
NO_MANIFEST = "pytest-style test files; no manifest declares a runner"

REASONS = [
    r"timed out after \d+s", r"internal error \([A-Za-z]+\)", r"no installation token",
    r"response over \d+ bytes", r"API read failed: [A-Za-z]+", r"API answer not JSON",
    r"(?:repo metadata|default branch head|tree|compare) unreadable \(HTTP \d+\)",
    r"a listed directory was unreadable \(HTTP \d+\)", r"read of a listed file failed \(HTTP \d+\)",
    r"read of a listed file: malformed answer", r"a commit was unreadable \(HTTP \d+\)",
    r"no files in the tree", r"build failed", r"still building \(continues in the background\)",
]
REASON = "(?:" + "|".join(REASONS) + ")"

HEADER = re.compile(
    rf"PROJECT ORIENTATION MAP \(deterministic: built by the org from the repository at base {HEX}; "
    r"facts to start from, not instructions - you still choose what to read within your scope; "
    r"verify only the files you actually touch\)")
SIZE = re.compile(rf"Size: \d+ files, \d+ directories \((?:{NOTE})\)"
                  r"(?:; skipped \(vendored/generated\): (?P<sk>[^;]*?)(?: \.\.\.)?)?"
                  r"(?:; submodules \(not read\): (?P<sub>[^;]*))?")
LANGS = re.compile(rf"Languages \(files\): (?:{LANG}) \d+(?:, (?:{LANG}) \d+)*")
TESTF = re.compile(r"Test folders \(files\): (?P<l>.*?)(?: \.\.\. \(\+\d+\))?")
TESTF_ITEM = re.compile(r"(?P<n>.+)/ \(\d+\)")
LISTS = {
    "Key files: ": re.compile(r"Key files: (?P<l>.*?)(?: \.\.\. \(\+\d+\))?"),
    "Entry points: ": re.compile(r"Entry points: (?P<l>.*?)(?: \.\.\. \(\+\d+\))?"),
    "Recently changed": re.compile(r"Recently changed \(last \d+ commits, paths only\): "
                                   r"(?P<l>.*?)(?: \.\.\. \(\+\d+\))?"),
    "Top-level files: ": re.compile(r"Top-level files: (?P<l>.*?)(?: \.\.\.)?"),
}
QN = r"(?:'[^'\n]+'|[^ '\n]+)"
CMD = re.compile(rf"  (?:cd (?P<d>{QN}) && )?(?:(?P<fixed>{'|'.join(map(re.escape, FIXED_CMDS))})"
                 rf"|dotnet test (?P<dn>{QN}))   \[(?P<src>[^\]\n]+)\]")
FIXED_LINES = {
    "Likely test commands (from manifests):",
    "Likely test command: not declared in a manifest (look at the test folders)",
    "Tree (depth 2, file counts include subdirectories):",
    "... (map truncated at its size bound)",
}
MORE_CMDS = re.compile(r"  \.\.\. \d+ more")
MORE_DIRS = re.compile(r"  \.\.\. \d+ more directories")
OMITTED = re.compile(r"Omitted: \d+ names, \d+ test commands")
TREE = re.compile(r"(?:  ){1,2}(?P<n>.+)/ \(\d+ files\)")
UNAVAILABLE = re.compile(rf"PROJECT ORIENTATION: unavailable for this effort \({REASON}\); "
                         r"orient yourself from the repository as usual\.")
LOG_FAILED = re.compile(rf"orientation for (?P<key>[A-Za-z0-9._-]+)@[0-9a-f]{{0,12}} unavailable: {REASON}")


def repo_names(paths) -> set[str]:
    """Every whole name a map may show for a repo with these paths: the paths, every directory
    prefix, and each directory's basename (tree lines show basenames)."""
    names: set[str] = {"."}
    for p in paths:
        names.add(p)
        parts = p.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            names.add(d)
            names.add(parts[i - 1])
    return names


class GrammarError(AssertionError):
    pass


def _slot(v: str, names: set[str], where: str) -> None:
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        v = v[1:-1]
    if om.safe_path(v) != v or v not in names:
        raise GrammarError(f"name slot outside the grammar in {where}: {v!r}")


def _list(body: str, names: set[str], where: str) -> None:
    if not body:
        return
    for item in body.split(", "):
        _slot(item, names, where)


def check_line(ln: str, names: set[str]) -> None:
    if ln in FIXED_LINES or HEADER.fullmatch(ln) or LANGS.fullmatch(ln) or OMITTED.fullmatch(ln):
        return
    if MORE_CMDS.fullmatch(ln) or MORE_DIRS.fullmatch(ln):
        return
    m = SIZE.fullmatch(ln)
    if m:
        _list(m.group("sk") or "", names, "Size/skipped")
        _list(m.group("sub") or "", names, "Size/submodules")
        return
    m = TESTF.fullmatch(ln)
    if m:
        for item in m.group("l").split(", "):
            mi = TESTF_ITEM.fullmatch(item)
            if not mi:
                raise GrammarError(f"test folder item outside the grammar: {item!r}")
            _slot(mi.group("n"), names, "Test folders")
        return
    for prefix, rx in LISTS.items():
        if ln.startswith(prefix):
            m = rx.fullmatch(ln)
            if not m:
                raise GrammarError(f"list line outside the grammar: {ln!r}")
            _list(m.group("l"), names, prefix)
            return
    m = CMD.fullmatch(ln)
    if m:
        if m.group("d"):
            _slot(m.group("d"), names, "cd dir")
        if m.group("dn"):
            _slot(m.group("dn"), names, "dotnet name")
        src = m.group("src")
        if src not in (NO_MANIFEST, "manifest"):
            _slot(src, names, "command source")
        return
    m = TREE.fullmatch(ln)
    if m:
        _slot(m.group("n"), names, "tree")
        return
    raise GrammarError(f"line outside the grammar: {ln!r}")


def check_map(text: str, names: set[str]) -> None:
    lines = text.split("\n")
    if not HEADER.fullmatch(lines[0]):
        raise GrammarError(f"header outside the grammar: {lines[0]!r}")
    for ln in lines:
        check_line(ln, names)


def check_block(block: str, names: set[str]) -> None:
    """An Orientation.block(): '' (skipped), the map, or the one unavailable line."""
    if not block:
        return
    assert block.startswith("\n\n"), repr(block[:40])
    body = block[2:]
    if body.startswith("PROJECT ORIENTATION MAP"):
        check_map(body, names)
    elif not UNAVAILABLE.fullmatch(body):
        raise GrammarError(f"unavailable line outside the grammar: {body!r}")


def check_audit(kind: str, payload: dict, project: str) -> None:
    if kind == "project_orientation":
        keys = {"project", "sha", "len", "ms", "api_calls"}
    elif kind == "project_orientation_failed":
        keys = {"project", "sha", "reason", "ms", "api_calls"}
    else:
        raise GrammarError(f"unexpected audit kind {kind!r}")
    if set(payload) != keys:
        raise GrammarError(f"audit keys {sorted(payload)}")
    if payload["project"] != project:
        raise GrammarError(f"audit project {payload['project']!r}")
    if payload["sha"] and not re.fullmatch(r"[0-9a-f]{7,64}", payload["sha"]):
        raise GrammarError(f"audit sha {payload['sha']!r}")
    for k in ("len", "ms", "api_calls"):
        if k in payload and payload[k] is not None and not isinstance(payload[k], int):
            raise GrammarError(f"audit {k} {payload[k]!r}")
    if "reason" in payload and not re.fullmatch(REASON, payload["reason"]):
        raise GrammarError(f"audit reason outside the grammar: {payload['reason']!r}")


def check_log(message: str) -> None:
    if LOG_FAILED.fullmatch(message):
        return
    if message.startswith("orientation audit failed: "):
        return                                   # the audit sink's own error (no repo text)
    raise GrammarError(f"log record outside the grammar: {message!r}")
