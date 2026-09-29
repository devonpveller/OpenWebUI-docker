#!/usr/bin/env python3
"""Shared CLI entry for status-pipe modules.

Every module's service file used to carry a byte-identical ~30-line
``__main__`` dispatch (piped-JSON -> main, --describe, --health, argv-as-input,
default describe). That block lives here once (CLEANUP-PLAN v3 E.3); service
files end with a 4-line bootstrap calling :func:`run_module_cli`.
"""
import json
import sys
import time
from typing import Any, Callable, Dict

Handler = Callable[..., Dict[str, Any]]


def run_module_cli(main: Handler, describe: Handler, health: Handler) -> None:
    """Dispatch exactly like the historical per-module ``__main__`` blocks."""
    if not sys.stdin.isatty():
        try:
            input_data = json.loads(sys.stdin.read())
            print(json.dumps(main(input_data), indent=2, ensure_ascii=False))
        except Exception as e:  # contract: error JSON + exit 1, never a traceback
            print(json.dumps({"error": str(e), "type": "CLI execution error"}, indent=2))
            sys.exit(1)
    elif len(sys.argv) > 1:
        if sys.argv[1] == "--describe":
            print(json.dumps(describe(), indent=2))
        elif sys.argv[1] == "--health":
            print(json.dumps(health(), indent=2))
        else:
            input_text = " ".join(sys.argv[1:])
            input_data = {"request_id": str(time.time()), "input": input_text}
            print(json.dumps(main(input_data), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(describe(), indent=2))
