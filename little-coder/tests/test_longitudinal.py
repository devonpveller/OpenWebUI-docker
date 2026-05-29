"""Longitudinal structural metrics (design §9.1, Chapter 4 §4h).

Drives the per-file samplers, the repo walker (with skip-dirs pruning),
the JSONL history append, and the anomaly detector. The principle-link
table makes the §4h compliance-gap signal explicit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from littlecoder.longitudinal import (
    Anomaly,
    FileMetric,
    RepoSnapshot,
    append_snapshot,
    detect_anomalies,
    load_history,
    sample_file,
    sample_repo,
)


# --- sample_file -------------------------------------------------------


def test_sample_file_picks_up_branches(tmp_path):
    """Cyclomatic proxy counts branch keywords. A function with two
    `if`s and an `else` should report ≥ 3."""
    f = tmp_path / "x.py"
    f.write_text(
        "def foo(x):\n"
        "    if x:\n"
        "        return 1\n"
        "    elif x < 0:\n"
        "        return -1\n"
        "    else:\n"
        "        return 0\n",
        encoding="utf-8",
    )
    metric = sample_file(f)
    assert metric is not None
    assert metric.lang == "python"
    assert metric.cyclomatic >= 3
    assert metric.lines == 7


def test_sample_file_counts_fan_out(tmp_path):
    """Fan-out is import-shape. Three imports → 3."""
    f = tmp_path / "x.py"
    f.write_text(
        "import os\nfrom typing import Any\nimport json\n\ndef f(): pass\n",
        encoding="utf-8",
    )
    metric = sample_file(f)
    assert metric is not None
    assert metric.fan_out == 3


def test_sample_file_finds_longest_function(tmp_path):
    """The longest function-block is reported."""
    f = tmp_path / "x.py"
    f.write_text(
        "def small(): pass\n\n"
        "def big():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    z = 3\n"
        "    return x + y + z\n",
        encoding="utf-8",
    )
    metric = sample_file(f)
    assert metric is not None
    assert metric.longest_function_lines >= 4


def test_sample_file_skips_non_code_extensions(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# doc\n", encoding="utf-8")
    assert sample_file(f) is None


def test_sample_file_handles_unreadable_path(tmp_path):
    assert sample_file(tmp_path / "missing.py") is None


def test_sample_file_skips_empty(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("", encoding="utf-8")
    assert sample_file(f) is None


# --- sample_repo --------------------------------------------------------


def test_sample_repo_walks_and_aggregates(tmp_path):
    """A repo with two Python files yields a snapshot with both."""
    (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def g(): pass\n", encoding="utf-8")
    snapshot = sample_repo(tmp_path, commit="abc123")
    assert len(snapshot.files) == 2
    assert snapshot.commit == "abc123"
    assert snapshot.total_lines == 2


def test_sample_repo_skips_vendor_dirs(tmp_path):
    """node_modules + .git + __pycache__ are pruned. A file inside any
    of them MUST NOT appear in the snapshot."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "evil.py").write_text("CRASH\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("CACHED\n", encoding="utf-8")
    snapshot = sample_repo(tmp_path)
    paths = {f.path for f in snapshot.files}
    assert paths == {"src/a.py"}


def test_sample_repo_uses_repo_relative_paths(tmp_path):
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "x.py").write_text("def f(): pass\n", encoding="utf-8")
    snapshot = sample_repo(tmp_path)
    # Forward-slash repo-relative path — portable across OSes.
    assert snapshot.files[0].path == "deep/x.py"


# --- history persistence ------------------------------------------------


def test_append_and_load_round_trip(tmp_path):
    snap1 = RepoSnapshot(
        ts="2026-05-23T00:00:00Z",
        repo="r",
        commit="aaa",
        files=(FileMetric("a.py", "python", 10, 2, 1, 5),),
    )
    snap2 = RepoSnapshot(
        ts="2026-05-23T01:00:00Z",
        repo="r",
        commit="bbb",
        files=(FileMetric("a.py", "python", 12, 3, 1, 6),),
    )
    append_snapshot(tmp_path, snap1)
    append_snapshot(tmp_path, snap2)
    history = load_history(tmp_path, "r")
    assert len(history) == 2
    assert history[0]["commit"] == "aaa"
    assert history[1]["commit"] == "bbb"
    assert history[1]["totals"]["total_lines"] == 12


def test_load_history_returns_empty_for_unknown_repo(tmp_path):
    assert load_history(tmp_path, "never-sampled") == []


def test_load_history_skips_corrupt_lines(tmp_path):
    """A single corrupted line in the history file must not blind the
    rest — the loader skips malformed entries."""
    snap = RepoSnapshot(
        ts="t", repo="r", commit=None, files=()
    )
    append_snapshot(tmp_path, snap)
    # Corrupt the file with an invalid JSON line in the middle.
    target = tmp_path / "r.jsonl"
    target.write_text(
        target.read_text(encoding="utf-8") + "not json\n",
        encoding="utf-8",
    )
    history = load_history(tmp_path, "r")
    assert len(history) == 1


# --- detect_anomalies ---------------------------------------------------


def _hist(repo: str, totals_list: list[dict]) -> list[dict]:
    return [
        {"ts": f"t{i}", "repo": repo, "commit": f"c{i}", "totals": t}
        for i, t in enumerate(totals_list)
    ]


def test_anomaly_flags_large_growth_in_complexity():
    """5 prior samples averaging cyclomatic 10; latest = 20 → +100%
    well over a 25% tolerance, anomaly."""
    history = _hist(
        "r",
        [
            {"total_lines": 100, "mean_cyclomatic": 10, "mean_fan_out": 5, "max_function_lines": 20}
            for _ in range(5)
        ]
        + [
            {"total_lines": 100, "mean_cyclomatic": 20, "mean_fan_out": 5, "max_function_lines": 20}
        ],
    )
    anomalies = detect_anomalies(history, window=5, tolerance=0.25)
    assert any(a.metric == "mean_cyclomatic" for a in anomalies)
    # Compliance-gap link: cyclomatic → naming/readability principle.
    cyclomatic = next(a for a in anomalies if a.metric == "mean_cyclomatic")
    assert cyclomatic.principle is not None
    assert "Functions short" in cyclomatic.principle


def test_anomaly_returns_empty_when_history_too_short():
    """Below `window + 1` snapshots, we cannot compute a rolling mean.
    The detector returns [] — never claims to see what it can't."""
    history = _hist("r", [{"mean_cyclomatic": 10} for _ in range(3)])
    assert detect_anomalies(history, window=5) == []


def test_anomaly_returns_empty_when_within_tolerance():
    """A 15% drift with a 25% tolerance is noise. No anomaly."""
    history = _hist(
        "r",
        [{"total_lines": 100, "mean_cyclomatic": 10, "mean_fan_out": 5, "max_function_lines": 20}] * 5
        + [{"total_lines": 115, "mean_cyclomatic": 10, "mean_fan_out": 5, "max_function_lines": 20}],
    )
    assert detect_anomalies(history, window=5, tolerance=0.25) == []


def test_anomaly_carries_principle_link_for_each_metric():
    """Every metric in the table maps to a principle (§4h locked link).
    Anomalies on different metrics surface different principles."""
    history = _hist(
        "r",
        [{"total_lines": 100, "mean_cyclomatic": 10, "mean_fan_out": 5, "max_function_lines": 20}] * 5
        + [{"total_lines": 500, "mean_cyclomatic": 50, "mean_fan_out": 30, "max_function_lines": 200}],
    )
    anomalies = detect_anomalies(history, window=5, tolerance=0.25)
    by_metric = {a.metric: a for a in anomalies}
    assert "total_lines" in by_metric
    assert "mean_cyclomatic" in by_metric
    assert "mean_fan_out" in by_metric
    assert "max_function_lines" in by_metric
    # Different metrics → different principles.
    assert by_metric["mean_fan_out"].principle != by_metric["mean_cyclomatic"].principle
    assert "Dependency Inversion" in by_metric["mean_fan_out"].principle
