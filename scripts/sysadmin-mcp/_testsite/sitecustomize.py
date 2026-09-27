"""Test-only: installs the sysadmin-mcp process guard in Python children of a test run.

_testguard.install() puts this directory first on PYTHONPATH and sets ACSR_TESTGUARD, so a Python
process started from a test the ordinary way (server.py for the STDIO section, a meta-test's child
suite) runs the same guard from its first line. NOT covered: a child started with -I / -E / -S
(which skip PYTHONPATH or site) or given a scrubbed env= without ACSR_TESTGUARD and PYTHONPATH -
that is why the tests run only in a disposable container, which is the barrier; this hook is
defence in depth. Outside a test run ACSR_TESTGUARD is unset and this file does nothing. See
_testguard.py.
"""
import os
import sys

if os.environ.get("ACSR_TESTGUARD") and os.environ.get("ACSR_TESTGUARD_DIR"):
    sys.path.insert(0, os.environ["ACSR_TESTGUARD_DIR"])
    import _testguard

    _testguard.install_process_guard(os.environ["ACSR_TESTGUARD"], "child:" + os.path.basename(sys.argv[0] if sys.argv and sys.argv[0] else "python"))
