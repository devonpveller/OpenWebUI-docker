#!/usr/bin/env python3
"""Generate per-instance little-coder configs for the agent-org worker pool (P5).

WHY: little-coder's config loader is plain `yaml.safe_load` — no env substitution, no
layering (little-coder/src/littlecoder/config.py) — and `workspace.open_terminal_url` is the
ONLY source the daemon + agent use to reach open-terminal (agent.py overwrites the
LC_OPEN_TERMINAL_URL env from the config value). So each pooled worker needs its OWN config
pointing at its OWN open-terminal, or it would route exec to the MAIN stack's open-terminal
(breaking per-instance isolation).

This copies the canonical config (single source of truth) and rewrites just three lines per
instance — targeted line replaces so all comments/tuning are preserved verbatim:
  - `workspace.open_terminal_url` -> the instance's own open-terminal (above);
  - `workspace.exec_timeout_seconds` 1800 -> 900 (ao-exechang, 2026-10-14). A worker's command
    that hangs blocked its coding turn for the whole 1,800 s (4 of 10 bake-off runs lost to it).
    Every bake-off command that legitimately ran past 600 s was an agent-bridge full suite; with
    `-n 4` those took 604-690 s (and 645-661 s on the host), so 900 lets the one full suite the
    worker knowledge prescribes (after editing, `-n 4`) finish, and still halves a hang. The
    interactive little-coder keeps the canonical 1,800 s. Evidence: the ao-exechang findings.
  - `agent.temperature` null -> 0.6 (ao-temp06, operator D22 2026-10-15). Without it every worker
    model call carried temperature 0.3: pi's benchmark-profiles extension (little-coder's
    .pi/settings.json default_model_profile) stamps it on each request. The C7e sweep (bake-off
    PAPER Part 7) showed 0.6 no worse. The open-terminal-exec extension applies the value
    (config -> agent.py LC_TEMPERATURE -> request body). The interactive coder config keeps null.

Usage (from the workspace root):  python agent-org/scripts/gen-worker-configs.py [--count N]
                                   [--out-dir DIR]   (default: agent-org/agent-bridge/worker-configs)
Re-run whenever little-coder/config/little-coder.config.yaml changes.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "little-coder" / "config"
OUT_DIR = ROOT / "agent-org" / "agent-bridge" / "worker-configs"

_ORIG = "open_terminal_url: http://open-terminal:8000"
_EXEC_TIMEOUT_ORIG = "exec_timeout_seconds: 1800"
WORKER_EXEC_TIMEOUT_S = 900
_TEMPERATURE_ORIG = "  temperature: null"
WORKER_TEMPERATURE = 0.6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2, help="number of pool instances")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR, help="where worker-N/ dirs go")
    args = ap.parse_args()

    src_yaml = SRC_DIR / "little-coder.config.yaml"
    if not src_yaml.exists():
        raise SystemExit(f"canonical config not found: {src_yaml}")
    content = src_yaml.read_text(encoding="utf-8")
    for expected in (_ORIG, _EXEC_TIMEOUT_ORIG, _TEMPERATURE_ORIG):
        if content.count(expected) != 1:
            raise SystemExit(
                f"expected exactly one line {expected!r} in {src_yaml} — the canonical config "
                f"changed; update this generator."
            )

    for n in range(1, args.count + 1):
        dst = args.out_dir / f"worker-{n}"
        dst.mkdir(parents=True, exist_ok=True)
        patched = content.replace(_ORIG, f"open_terminal_url: http://ao-ot-{n}:8000").replace(
            _EXEC_TIMEOUT_ORIG,
            f"exec_timeout_seconds: {WORKER_EXEC_TIMEOUT_S}  # agent-org worker bound (generator)",
        ).replace(
            _TEMPERATURE_ORIG,
            f"  temperature: {WORKER_TEMPERATURE}  # agent-org worker temperature (generator)",
        )
        header = (
            f"# GENERATED for agent-org pool instance ao-worker-{n} by "
            f"agent-org/scripts/gen-worker-configs.py.\n"
            f"# Source of truth: little-coder/config/little-coder.config.yaml — DO NOT edit here;\n"
            f"# edit the source + regenerate. Only workspace.open_terminal_url and\n"
            f"# workspace.exec_timeout_seconds ({WORKER_EXEC_TIMEOUT_S}) and agent.temperature "
            f"({WORKER_TEMPERATURE}) differ.\n"
        )
        (dst / "little-coder.config.yaml").write_text(header + patched, encoding="utf-8")
        # models.json + schema.json are needed alongside the yaml (LC_CONFIG dir).
        for extra in ("models.json", "little-coder.schema.json"):
            if (SRC_DIR / extra).exists():
                shutil.copy2(SRC_DIR / extra, dst / extra)
        print(f"generated {dst} (open_terminal_url -> http://ao-ot-{n}:8000, "
              f"exec_timeout_seconds -> {WORKER_EXEC_TIMEOUT_S}, temperature -> {WORKER_TEMPERATURE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
