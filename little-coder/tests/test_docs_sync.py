"""AGENTS.md auto-sync (`docs_sync` module).

These pin the contract: stable output (same source → same Markdown),
markers-required round-trip, hand-authored content preserved outside
the markers, atomic write, --check non-destructive mode.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from littlecoder.docs_sync import (
    BEGIN_MARKER,
    END_MARKER,
    ModuleInfo,
    extract_module_info,
    generate_module_index_markdown,
    main as docs_sync_main,
    sync_agents_md,
    walk_modules,
)


# --- module info extraction --------------------------------------------


def _write_module(dir: Path, name: str, content: str) -> Path:
    target = dir / name
    target.write_text(content, encoding="utf-8")
    return target


def test_extract_first_sentence_from_docstring(tmp_path):
    src = _write_module(
        tmp_path,
        "example.py",
        '"""First sentence here. Second sentence ignored."""\n\ndef f(): pass\n',
    )
    info = extract_module_info(src, tmp_path)
    assert info is not None
    assert info.purpose == "First sentence here."


def test_extract_design_and_chapter_refs(tmp_path):
    """Both `§N(.N)` and `Chapter N` patterns flow into the index row."""
    src = _write_module(
        tmp_path,
        "x.py",
        '"""Does the thing (design §3.4, Chapter 2). Also §10.2."""\n',
    )
    info = extract_module_info(src, tmp_path)
    assert info is not None
    assert info.design_refs == ("§3.4", "§10.2")
    assert info.chapter_refs == ("Chapter 2",)


def test_extract_handles_module_without_docstring(tmp_path):
    """No docstring → no row in the index (orient-value too low)."""
    src = _write_module(tmp_path, "no_doc.py", "x = 1\n")
    assert extract_module_info(src, tmp_path) is None


def test_extract_tolerates_future_imports_before_docstring(tmp_path):
    """`from __future__ import annotations` is the common shape at the
    top of our modules — the walker has to skip past it."""
    src = _write_module(
        tmp_path,
        "future.py",
        '"""Real docstring."""\n\nfrom __future__ import annotations\n',
    )
    # Above is wrong order — the docstring SHOULD be first. The other
    # direction (future first, docstring second) is the realistic case:
    src.write_text(
        "from __future__ import annotations\n\n"
        '"""Real docstring with §3.1 ref."""\n\n'
        "def f(): pass\n",
        encoding="utf-8",
    )
    info = extract_module_info(src, tmp_path)
    assert info is not None
    assert info.purpose == "Real docstring with §3.1 ref."
    assert "§3.1" in info.design_refs


def test_extract_deduplicates_repeated_refs(tmp_path):
    src = _write_module(
        tmp_path,
        "x.py",
        '"""Mentions §5.6 multiple times: §5.6 and §5.6 again. Chapter 4 too. Chapter 4."""\n',
    )
    info = extract_module_info(src, tmp_path)
    assert info is not None
    assert info.design_refs == ("§5.6",)
    assert info.chapter_refs == ("Chapter 4",)


def test_extract_single_line_triple_quoted_docstring(tmp_path):
    src = _write_module(
        tmp_path,
        "x.py",
        '"""Brief one-liner."""\n\ndef f(): pass\n',
    )
    info = extract_module_info(src, tmp_path)
    assert info is not None
    assert info.purpose == "Brief one-liner."


def test_extract_pipes_in_purpose_get_escaped(tmp_path):
    """A `|` in the purpose would break the Markdown table — render
    escapes it. Tested via the index generator."""
    src = _write_module(
        tmp_path,
        "x.py",
        '"""Pipes | here | break tables."""\n',
    )
    info = extract_module_info(src, tmp_path)
    rendered = generate_module_index_markdown([info])
    assert r"Pipes \| here \| break tables." in rendered


# --- walk_modules ------------------------------------------------------


def test_walk_modules_returns_sorted_stable_order(tmp_path):
    """Stable output is critical — same source must produce the same
    file every run. We sort filenames alphabetically."""
    _write_module(tmp_path, "zebra.py", '"""Z module."""\n')
    _write_module(tmp_path, "alpha.py", '"""A module."""\n')
    _write_module(tmp_path, "mango.py", '"""M module."""\n')
    found = walk_modules(tmp_path)
    names = [m.filename for m in found]
    assert names == sorted(names)


def test_walk_modules_skips_init(tmp_path):
    _write_module(tmp_path, "__init__.py", '"""package."""\nVERSION = "0.1"\n')
    _write_module(tmp_path, "real.py", '"""Real module."""\n')
    found = walk_modules(tmp_path)
    assert [m.filename for m in found] == ["real.py"]


def test_walk_modules_raises_on_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        walk_modules(tmp_path / "does-not-exist")


# --- generate_module_index_markdown -----------------------------------


def test_generate_renders_table_with_headers():
    mods = [
        ModuleInfo("a.py", "Does A.", ("§1.1",), ("Chapter 1",)),
        ModuleInfo("b.py", "Does B.", (), ()),
    ]
    out = generate_module_index_markdown(mods)
    assert "| Module | Purpose |" in out
    assert "| `a.py` | Does A. | §1.1 | Chapter 1 |" in out
    # No refs renders an em-dash so the column isn't blank.
    assert "| `b.py` | Does B. | — | — |" in out


def test_generate_handles_empty_list():
    assert generate_module_index_markdown([]).strip() == "_(no modules found)_"


# --- sync_agents_md (markers + atomic + idempotence) -------------------


def _seed_agents_md(tmp_path: Path, body: str = "") -> Path:
    agents = tmp_path / "AGENTS.md"
    if not body:
        body = (
            "# AGENTS.md\n\n"
            "Hand-authored intro.\n\n"
            "## Module index\n\n"
            f"{BEGIN_MARKER}\n"
            "(placeholder)\n"
            f"{END_MARKER}\n\n"
            "Hand-authored tail.\n"
        )
    agents.write_text(body, encoding="utf-8")
    return agents


def _seed_source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    _write_module(src, "alpha.py", '"""Alpha module — design §1.1."""\n')
    _write_module(src, "beta.py", '"""Beta module — Chapter 2."""\n')
    return src


def test_sync_replaces_only_between_markers(tmp_path):
    """Critical contract: hand-authored intro + tail are PRESERVED;
    only the section between markers gets regenerated."""
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    changed, new = sync_agents_md(agents, src)
    assert changed is True
    assert "Hand-authored intro." in new
    assert "Hand-authored tail." in new
    assert "Alpha module" in new
    assert "Beta module" in new
    # Placeholder is gone — the auto-section was replaced.
    assert "(placeholder)" not in new


def test_sync_is_idempotent(tmp_path):
    """Same source + already-synced AGENTS.md → no change reported."""
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    sync_agents_md(agents, src)  # first run lands the index
    changed, _ = sync_agents_md(agents, src)  # second run is no-op
    assert changed is False


def test_sync_raises_when_markers_missing(tmp_path):
    src = _seed_source(tmp_path)
    bad = tmp_path / "AGENTS.md"
    bad.write_text("# AGENTS.md\n\nNo markers here.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="markers"):
        sync_agents_md(bad, src)


def test_sync_raises_when_markers_inverted(tmp_path):
    src = _seed_source(tmp_path)
    bad = tmp_path / "AGENTS.md"
    bad.write_text(
        f"# AGENTS.md\n\n{END_MARKER}\n(garbage)\n{BEGIN_MARKER}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="order"):
        sync_agents_md(bad, src)


def test_sync_write_false_does_not_persist(tmp_path):
    """`write=False` (dry run / --check) returns the new text but
    leaves the file alone."""
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    original = agents.read_text(encoding="utf-8")
    changed, new = sync_agents_md(agents, src, write=False)
    assert changed is True
    assert new != original
    assert agents.read_text(encoding="utf-8") == original


def test_sync_atomic_write(tmp_path, monkeypatch):
    """File goes through `.tmp` + rename — same discipline as the rest
    of the writers."""
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    seen = {"tmp_existed": False}
    real_replace = os.replace

    def spy_replace(src_, dst):
        if str(src_).endswith(".tmp"):
            seen["tmp_existed"] = True
            assert Path(src_).exists()
        return real_replace(src_, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    sync_agents_md(agents, src)
    assert seen["tmp_existed"] is True


# --- CLI main ----------------------------------------------------------


def test_main_writes_when_changes_pending(tmp_path, capsys):
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    rc = docs_sync_main(["--source", str(src), "--agents-md", str(agents)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "updated" in out


def test_main_check_mode_returns_1_on_drift(tmp_path, capsys):
    """`--check` is the pre-commit / CI mode — exit 1 when drift,
    don't write."""
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    original = agents.read_text(encoding="utf-8")
    rc = docs_sync_main(
        ["--source", str(src), "--agents-md", str(agents), "--check"]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "out of date" in err
    # File untouched.
    assert agents.read_text(encoding="utf-8") == original


def test_main_check_mode_returns_0_when_up_to_date(tmp_path, capsys):
    src = _seed_source(tmp_path)
    agents = _seed_agents_md(tmp_path)
    docs_sync_main(["--source", str(src), "--agents-md", str(agents)])
    rc = docs_sync_main(
        ["--source", str(src), "--agents-md", str(agents), "--check"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "up to date" in out


def test_main_returns_2_on_missing_markers(tmp_path, capsys):
    src = _seed_source(tmp_path)
    bad = tmp_path / "AGENTS.md"
    bad.write_text("# AGENTS.md\n\nNo markers here.\n", encoding="utf-8")
    rc = docs_sync_main(["--source", str(src), "--agents-md", str(bad)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "markers" in err
