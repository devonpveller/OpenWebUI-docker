"""Agent-orientation doc sync — keep `little-coder/AGENTS.md` fact-checked.

Maintains the auto-generated **module index** section of `AGENTS.md` —
the file at the project root that an agent (or human collaborator)
reads first to orient themselves in the little-coder codebase. Sits
alongside the design / plan / tasks docs (which are the authoritative
narrative) and provides a quick reference: which modules exist, what
each one does, which design section governs it.

Two-section discipline inside `AGENTS.md`:

  - HAND-AUTHORED content (architecture summary, principles, common
    workflows) outside the auto markers — never touched by this tool.
  - The auto-generated MODULE INDEX between `<!-- BEGIN AUTO MODULE
    INDEX -->` and `<!-- END AUTO MODULE INDEX -->` markers. This
    tool regenerates it from the source tree on demand.

What gets extracted per module:
  - One-line purpose: the first sentence of the module docstring.
  - Design references: every `§N(.N)?` substring in the docstring.
  - Chapter references: every `Chapter N` substring in the docstring.

The sync is idempotent — re-running with no source changes produces
the same file (sort by filename, fixed sentence-extraction rule). A
test pin guards the format so a future contributor doesn't silently
break the agent-orient round trip.

CLI: `lc admin docs sync` (operator runs this on the host against the
local checkout; not a daemon endpoint — the daemon's source mount is
read-only). `--check` exits non-zero when the file would change,
suitable for a pre-commit hook.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Iterable

# Markers delimiting the auto-regenerated section. Anything between
# them gets replaced; anything outside is preserved verbatim.
BEGIN_MARKER = "<!-- BEGIN AUTO MODULE INDEX -->"
END_MARKER = "<!-- END AUTO MODULE INDEX -->"

# Default paths — relative to the little-coder package root.
DEFAULT_SOURCE_DIR = "src/littlecoder"
DEFAULT_AGENTS_MD = "AGENTS.md"

# Module filenames we never index — they have no public surface worth
# orienting against.
_SKIP_MODULES = frozenset({"__init__.py", "__main__.py"})

# Design and chapter references — pulled from module docstrings.
_DESIGN_REF = re.compile(r"§\d+(?:\.\d+)?(?:[a-z])?")
_CHAPTER_REF = re.compile(r"\bChapter\s+\d+\b")


@dataclasses.dataclass(frozen=True)
class ModuleInfo:
    """One row of the auto-generated index. Filename relative to the
    src dir; purpose is the docstring's first sentence; refs are the
    deduped design / chapter mentions."""

    filename: str
    purpose: str
    design_refs: tuple[str, ...]
    chapter_refs: tuple[str, ...]


def _first_sentence(text: str) -> str:
    """Take the docstring's first sentence — up to the first '.' that
    is followed by whitespace OR is at end of paragraph. Strips
    leading/trailing whitespace, collapses internal newlines to single
    spaces so the result fits one table cell."""
    s = text.strip()
    if not s:
        return ""
    # Walk char-by-char so we can stop at a real sentence terminator.
    for i, ch in enumerate(s):
        if ch != ".":
            continue
        # End of sentence: followed by whitespace, or end of text.
        if i + 1 >= len(s) or s[i + 1].isspace():
            return " ".join(s[: i + 1].split())
    # No terminating period found — take first line or the whole
    # docstring up to ~200 chars, whichever is shorter.
    first_line = s.split("\n", 1)[0]
    return " ".join(first_line.split())[:200]


def _extract_module_docstring(source: str) -> str:
    """Pull the module-level docstring out of a Python source file.
    Returns "" if none. Uses a lightweight parse — finds the first
    triple-quoted string at the very top (after `from __future__` /
    blank lines / comments are tolerated)."""
    lines = source.splitlines()
    i = 0
    # Skip leading shebang / future imports / blank lines / comments.
    while i < len(lines):
        stripped = lines[i].strip()
        if (
            stripped == ""
            or stripped.startswith("#")
            or stripped.startswith("from __future__")
            or stripped.startswith("#!")
        ):
            i += 1
            continue
        break
    if i >= len(lines):
        return ""
    first = lines[i].lstrip()
    for opener in ('"""', "'''"):
        if not first.startswith(opener):
            continue
        # Single-line docstring? Check if the closer is on the same line.
        rest = first[len(opener):]
        if opener in rest:
            return rest[: rest.index(opener)]
        # Multi-line — accumulate until we find the closer.
        body_lines: list[str] = [rest]
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if opener in line:
                body_lines.append(line[: line.index(opener)])
                return "\n".join(body_lines)
            body_lines.append(line)
            j += 1
        # Unterminated docstring — bail, no useful purpose to extract.
        return ""
    return ""


def _unique_preserving_order(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def extract_module_info(path: Path, src_root: Path) -> ModuleInfo | None:
    """Read one Python file and produce its `ModuleInfo`. Returns None
    when the file has no module docstring (orient-value too low to
    include in the index)."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    docstring = _extract_module_docstring(source)
    if not docstring.strip():
        return None
    relative = path.relative_to(src_root)
    return ModuleInfo(
        filename=str(relative).replace("\\", "/"),
        purpose=_first_sentence(docstring),
        design_refs=_unique_preserving_order(_DESIGN_REF.findall(docstring)),
        chapter_refs=_unique_preserving_order(
            m.group(0) for m in _CHAPTER_REF.finditer(docstring)
        ),
    )


def walk_modules(src_dir: Path | str) -> list[ModuleInfo]:
    """Walk the package source and produce one `ModuleInfo` per .py file
    (sorted by filename — stable output)."""
    root = Path(src_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"source dir not found: {root}")
    out: list[ModuleInfo] = []
    for path in sorted(root.glob("*.py")):
        if path.name in _SKIP_MODULES:
            continue
        info = extract_module_info(path, root)
        if info is None:
            continue
        out.append(info)
    return out


# --- markdown generation ---------------------------------------------


def generate_module_index_markdown(modules: list[ModuleInfo]) -> str:
    """Produce the Markdown table the auto-section will contain. Stable
    output — same input always renders the same string."""
    if not modules:
        return "_(no modules found)_\n"
    lines = [
        "| Module | Purpose | Design refs | Chapters |",
        "| --- | --- | --- | --- |",
    ]
    for m in modules:
        refs = ", ".join(m.design_refs) if m.design_refs else "—"
        chapters = ", ".join(m.chapter_refs) if m.chapter_refs else "—"
        # Pipe characters in the purpose would break the table — escape.
        purpose = m.purpose.replace("|", "\\|")
        lines.append(
            f"| `{m.filename}` | {purpose} | {refs} | {chapters} |"
        )
    return "\n".join(lines) + "\n"


def _replace_between_markers(
    existing: str, new_section: str
) -> tuple[str, bool]:
    """Replace the content between `BEGIN_MARKER` and `END_MARKER`.
    Returns (updated_text, changed?). Raises if the markers are
    missing or out of order."""
    begin_idx = existing.find(BEGIN_MARKER)
    end_idx = existing.find(END_MARKER)
    if begin_idx < 0 or end_idx < 0:
        raise ValueError(
            f"AGENTS.md missing auto markers: need both "
            f"{BEGIN_MARKER!r} and {END_MARKER!r}"
        )
    if end_idx < begin_idx:
        raise ValueError(
            "AGENTS.md marker order is inverted — END appears before BEGIN"
        )
    head = existing[: begin_idx + len(BEGIN_MARKER)]
    tail = existing[end_idx:]
    composed = f"{head}\n{new_section.rstrip()}\n{tail}"
    return composed, composed != existing


# --- public API + CLI -------------------------------------------------


def sync_agents_md(
    agents_md: Path | str,
    src_dir: Path | str,
    *,
    write: bool = True,
) -> tuple[bool, str]:
    """Regenerate the auto-section of `AGENTS.md` from the source tree.

    Returns `(changed, new_text)`:
      - `changed=True` when the file's bytes would differ from what's
        on disk.
      - `new_text` is the full new contents (whether or not write
        actually persists it — `write=False` is the dry-run / --check
        mode).

    Raises `FileNotFoundError` if either path doesn't exist, or
    `ValueError` if the markers are missing/inverted."""
    agents_path = Path(agents_md)
    if not agents_path.exists():
        raise FileNotFoundError(f"AGENTS.md not found: {agents_path}")
    existing = agents_path.read_text(encoding="utf-8")
    modules = walk_modules(src_dir)
    section = generate_module_index_markdown(modules)
    new_text, changed = _replace_between_markers(existing, section)
    if changed and write:
        # Atomic write — `.tmp` + rename, same discipline as the rest
        # of the codebase's writers.
        tmp = agents_path.with_suffix(agents_path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(agents_path)
    return changed, new_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lc-docs-sync",
        description="Regenerate the auto-generated module index in AGENTS.md.",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE_DIR,
        help=f"path to the src/littlecoder dir (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--agents-md",
        default=DEFAULT_AGENTS_MD,
        help=f"path to AGENTS.md (default: {DEFAULT_AGENTS_MD})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "exit 1 when AGENTS.md is out of date but don't write. "
            "Suitable for pre-commit hooks / CI."
        ),
    )
    args = parser.parse_args(argv)

    try:
        changed, _new = sync_agents_md(
            args.agents_md, args.source, write=not args.check
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"lc-docs-sync: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if changed:
            print(
                "AGENTS.md is out of date — run `lc admin docs sync` to update.",
                file=sys.stderr,
            )
            return 1
        print("AGENTS.md is up to date.")
        return 0
    if changed:
        print(f"AGENTS.md updated: {args.agents_md}")
    else:
        print("AGENTS.md already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
