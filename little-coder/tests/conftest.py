"""Put `src/` and `git-proxy/` on the import path so tests run without an
editable install."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "src"))
sys.path.insert(0, str(_root / "git-proxy"))
