"""Longitudinal structural metrics (design §9.1, Chapter 4 §4h).

The **safety net for silent clusters**: tasks where tests pass but the
code degrades. The acute track (errors → clusters) can't see SOLID decay
because the inner loop reports success. This track samples structural
metrics — cyclomatic complexity, file size, fan-out, churn — over time
and flags anomalies to the operator surface.

This module is the INSTRUMENTATION half of the SOLID instruction-vs-
measurement pair (locked decision #19). `engineering-principles.md`
(Chapter 2 founding knowledge) tells the agent to write SOLID code;
the metrics here measure whether it did. A longitudinal anomaly that
maps to a stated baseline principle is a **compliance gap** signal —
escalates as tier-1 enforcement per §5.6 (NOT tier-0 restatement).

Stage-7 ships:
  - Lightweight metric SAMPLERS (no external dep — we count lines,
    parens, fan-out off of plain text). The samples are cheap to
    re-run; the full corpus walk is the operator's call via
    `lc admin longitudinal`.
  - Trend snapshot persistence (atomic-rename, same discipline as
    `cohorts.checkpoint`).
  - Anomaly detection: a sample's metric drifting beyond an
    operator-tunable threshold from the recent rolling mean.

What this module does NOT do:
  - Per-language semantic parsing (a Python `ast.parse` + tree-walker
    would be more accurate; we keep the metrics provider-agnostic so
    polyglot repos work uniformly).
  - Auto-act on anomalies. Per design §9.3 the longitudinal track
    surfaces anomalies; it never auto-acts.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Iterable

from . import SCHEMA_VERSION
from .journals import utc_now


# Files we consider "code". Lean default; the operator can extend.
_CODE_EXTENSIONS = frozenset(
    {".py", ".rs", ".go", ".js", ".ts", ".tsx", ".jsx", ".java", ".cpp", ".c", ".h", ".hpp", ".rb", ".php"}
)
# Files we skip (vendored / generated / config). The walker prunes
# these directories entirely so a `node_modules` tree doesn't dominate.
_SKIP_DIRS = frozenset(
    {".git", "node_modules", "target", "dist", "build", "__pycache__", ".venv", "venv"}
)


@dataclasses.dataclass(frozen=True)
class FileMetric:
    """One file's structural reading. Lightweight — counted off text,
    not parsed. Cheap enough to run across a whole repo on demand."""

    path: str  # repo-relative
    lang: str  # inferred from extension
    lines: int
    cyclomatic: int  # branch-keyword count (proxy)
    fan_out: int  # import/include statement count
    longest_function_lines: int


@dataclasses.dataclass(frozen=True)
class RepoSnapshot:
    """One sampling of a whole repo. Snapshots accumulate over time
    (one per `lc admin longitudinal` run); the anomaly detector
    compares the latest against a rolling window of priors."""

    ts: str
    repo: str
    commit: str | None  # `git rev-parse HEAD` at sample time
    files: tuple[FileMetric, ...]
    schema_version: int = SCHEMA_VERSION

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def mean_cyclomatic(self) -> float:
        if not self.files:
            return 0.0
        return sum(f.cyclomatic for f in self.files) / len(self.files)

    @property
    def mean_fan_out(self) -> float:
        if not self.files:
            return 0.0
        return sum(f.fan_out for f in self.files) / len(self.files)

    @property
    def max_function_lines(self) -> int:
        return max((f.longest_function_lines for f in self.files), default=0)


# --- per-file samplers --------------------------------------------------


# Branch-shaped keywords — a deliberate over-approximation. The metric is
# a PROXY for cyclomatic complexity; counting these correlates with the
# real measure well enough to detect regressions, without needing a
# language-specific parser.
_BRANCH_KEYWORDS = re.compile(
    r"\b(if|elif|else if|else|while|for|case|when|catch|except|match|"
    r"\?|&&|\|\|)\b"
)
# Import-shape — same idea. Different langs use different keywords.
_IMPORT_KEYWORDS = re.compile(
    r"^\s*(import|from|use|require|include|using|#include)\b", re.MULTILINE
)
# A rough "function start" — heuristic across langs. Detects `def`/`fn`/
# `function`/`func`. Misses some shapes (anonymous functions, JS arrows)
# but the absolute count matters less than the trend.
_FUNCTION_START = re.compile(
    r"^\s*(def\s+\w+|fn\s+\w+|function\s+\w+|func\s+\w+|public\s+\w+\s+\w+\s*\()",
    re.MULTILINE,
)


def _lang_from_extension(suffix: str) -> str:
    return {
        ".py": "python",
        ".rs": "rust",
        ".go": "go",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
    }.get(suffix.lower(), "unknown")


def _longest_function_block(text: str) -> int:
    """Rough estimate of the longest function body. Walks function-start
    matches and computes the line distance to the next start; the last
    function runs to EOF. Captures the trend, not the exact size."""
    starts = [m.start() for m in _FUNCTION_START.finditer(text)]
    if not starts:
        return 0
    starts.append(len(text))  # sentinel
    longest = 0
    for i in range(len(starts) - 1):
        block = text[starts[i] : starts[i + 1]]
        block_lines = block.count("\n")
        if block_lines > longest:
            longest = block_lines
    return longest


def sample_file(path: Path) -> FileMetric | None:
    """Sample one file's metrics. Returns None when the file isn't
    code (by extension), can't be read, or is empty."""
    if path.suffix not in _CODE_EXTENSIONS:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    if lines == 0:
        return None
    return FileMetric(
        path=str(path),
        lang=_lang_from_extension(path.suffix),
        lines=lines,
        cyclomatic=len(_BRANCH_KEYWORDS.findall(text)),
        fan_out=len(_IMPORT_KEYWORDS.findall(text)),
        longest_function_lines=_longest_function_block(text),
    )


def sample_repo(
    repo_root: Path | str,
    *,
    commit: str | None = None,
) -> RepoSnapshot:
    """Walk a repo and sample every code file. Skips `_SKIP_DIRS`
    (vendored / generated content). `commit` is what the daemon can
    grab from `git rev-parse HEAD` before calling — recorded in the
    snapshot for drill-down."""
    root = Path(repo_root)
    files: list[FileMetric] = []
    for entry in _walk_code_files(root):
        metric = sample_file(entry)
        if metric is None:
            continue
        # Store path relative to the repo root for portable snapshots.
        try:
            rel = entry.relative_to(root)
        except ValueError:
            rel = entry
        files.append(dataclasses.replace(metric, path=str(rel).replace("\\", "/")))
    return RepoSnapshot(
        ts=utc_now(),
        repo=str(root),
        commit=commit,
        files=tuple(files),
    )


def _walk_code_files(root: Path) -> Iterable[Path]:
    """Yield every code file beneath `root`, pruning skip-dirs. We do
    this with `os.walk` semantics so we can prune branches early."""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip-dirs in place — os.walk mutates traversal.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix in _CODE_EXTENSIONS:
                yield p


# --- snapshot store + anomaly detection --------------------------------


# Storage shape: one file per repo at `<longitudinal_dir>/<repo_slug>.jsonl`
# (append-only, one snapshot per line). Cheap to grow, atomic write
# (append O_APPEND).


def _repo_slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", repo).strip("_") or "repo"


def append_snapshot(snapshot_dir: Path | str, snapshot: RepoSnapshot) -> Path:
    """Append a snapshot to its repo's history file. Returns the path."""
    target = Path(snapshot_dir) / f"{_repo_slug(snapshot.repo)}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "ts": snapshot.ts,
            "repo": snapshot.repo,
            "commit": snapshot.commit,
            "schema_version": snapshot.schema_version,
            "totals": {
                "total_lines": snapshot.total_lines,
                "mean_cyclomatic": snapshot.mean_cyclomatic,
                "mean_fan_out": snapshot.mean_fan_out,
                "max_function_lines": snapshot.max_function_lines,
            },
            "files": [dataclasses.asdict(f) for f in snapshot.files],
        }
    )
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return target


def load_history(snapshot_dir: Path | str, repo: str) -> list[dict]:
    """Return every snapshot for `repo`, oldest first. Each entry has
    `ts`, `commit`, and the `totals` rollup; per-file detail is
    available for drill-down but rarely needed at the anomaly layer."""
    target = Path(snapshot_dir) / f"{_repo_slug(repo)}.jsonl"
    if not target.exists():
        return []
    out: list[dict] = []
    with open(target, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@dataclasses.dataclass(frozen=True)
class Anomaly:
    """One metric whose recent value strayed beyond the rolling mean
    by more than the operator's tolerance. The principle field maps
    the anomaly to the founding-knowledge principle it most likely
    violates — that's the §4h compliance-gap-signal link."""

    metric: str
    repo: str
    current: float
    rolling_mean: float
    deviation: float
    principle: str | None
    reason: str


# Map metric name → which `engineering-principles.md` principle is
# most likely being violated. The §4h "wire the explicit link" item.
_PRINCIPLE_LINK: dict[str, str] = {
    "mean_cyclomatic": (
        "Naming & readability — Functions short (~20 lines); early returns over deep nesting"
    ),
    "mean_fan_out": (
        "SOLID — Dependency Inversion (high fan-out suggests too many concrete deps)"
    ),
    "max_function_lines": (
        "Naming & readability — Functions short (~20 lines)"
    ),
    "total_lines": (
        "DRY, YAGNI — build only what the task needs now; delete dead code"
    ),
}


def detect_anomalies(
    history: list[dict],
    *,
    window: int = 5,
    tolerance: float = 0.25,
) -> list[Anomaly]:
    """Compare the latest snapshot's totals against the rolling mean of
    the prior `window` snapshots. Any metric whose latest value
    deviates by more than `tolerance` (fraction) is flagged.

    Returns [] when history is too short — at minimum we want
    `window + 1` snapshots so the mean has something to compare
    against."""
    if len(history) < window + 1:
        return []
    latest = history[-1]
    priors = history[-(window + 1) : -1]
    repo = latest.get("repo", "?")
    out: list[Anomaly] = []
    for metric in ("total_lines", "mean_cyclomatic", "mean_fan_out", "max_function_lines"):
        current = latest.get("totals", {}).get(metric)
        if current is None:
            continue
        prior_values = [
            p.get("totals", {}).get(metric) for p in priors
        ]
        prior_values = [v for v in prior_values if v is not None]
        if not prior_values:
            continue
        mean = sum(prior_values) / len(prior_values)
        if mean <= 0:
            continue
        deviation = (current - mean) / mean
        if abs(deviation) <= tolerance:
            continue
        direction = "increased" if deviation > 0 else "decreased"
        out.append(
            Anomaly(
                metric=metric,
                repo=repo,
                current=float(current),
                rolling_mean=float(mean),
                deviation=float(deviation),
                principle=_PRINCIPLE_LINK.get(metric),
                reason=(
                    f"{metric} {direction} by {deviation:+.0%} vs "
                    f"{window}-sample mean ({current:.1f} vs {mean:.1f})"
                ),
            )
        )
    return out
