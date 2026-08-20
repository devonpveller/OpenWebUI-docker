#!/usr/bin/env python3
"""Generate per-instance little-coder configs for the agent-org worker pool (P5).

WHY: little-coder's config loader is plain `yaml.safe_load` — no env substitution, no
layering (little-coder/src/littlecoder/config.py) — and `workspace.open_terminal_url` is the
ONLY source the daemon + agent use to reach open-terminal (agent.py overwrites the
LC_OPEN_TERMINAL_URL env from the config value). So each pooled worker needs its OWN config
pointing at its OWN open-terminal, or it would route exec to the MAIN stack's open-terminal
(breaking per-instance isolation).

This copies the canonical config (single source of truth) and rewrites just that one line
per instance — a targeted line replace so all comments/tuning are preserved verbatim.

Usage (from the workspace root):  python agent-org/scripts/gen-worker-configs.py [--count N]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2, help="number of pool instances")
    args = ap.parse_args()

    src_yaml = SRC_DIR / "little-coder.config.yaml"
    if not src_yaml.exists():
        raise SystemExit(f"canonical config not found: {src_yaml}")
    content = src_yaml.read_text(encoding="utf-8")
    if _ORIG not in content:
        raise SystemExit(
            f"expected line {_ORIG!r} not found in {src_yaml} — the canonical config changed; "
            f"update _ORIG in this generator."
        )

    for n in range(1, args.count + 1):
        dst = OUT_DIR / f"worker-{n}"
        dst.mkdir(parents=True, exist_ok=True)
        patched = content.replace(_ORIG, f"open_terminal_url: http://ao-ot-{n}:8000")
        header = (
            f"# GENERATED for agent-org pool instance ao-worker-{n} by "
            f"agent-org/scripts/gen-worker-configs.py.\n"
            f"# Source of truth: little-coder/config/little-coder.config.yaml — DO NOT edit here;\n"
            f"# edit the source + regenerate. Only workspace.open_terminal_url differs.\n"
        )
        (dst / "little-coder.config.yaml").write_text(header + patched, encoding="utf-8")
        # models.json + schema.json are needed alongside the yaml (LC_CONFIG dir).
        for extra in ("models.json", "little-coder.schema.json"):
            if (SRC_DIR / extra).exists():
                shutil.copy2(SRC_DIR / extra, dst / extra)
        print(f"generated {dst} (open_terminal_url -> http://ao-ot-{n}:8000)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
