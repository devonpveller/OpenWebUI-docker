"""Test-only: installs the sysadmin-mcp process guard in EVERY Python child of a test run.

_testguard.install() puts this directory first on PYTHONPATH and sets ACSR_TESTGUARD, so any
Python process started from a test (server.py for the STDIO section, a meta-test's child suite,
anything) runs the same guard from its first line. Outside a test run ACSR_TESTGUARD is unset and
this file does nothing. See _testguard.py.
"""
import os
import sys

if os.environ.get("ACSR_TESTGUARD") and os.environ.get("ACSR_TESTGUARD_DIR"):
    sys.path.insert(0, os.environ["ACSR_TESTGUARD_DIR"])
    import _testguard

    _testguard.install_process_guard(os.environ["ACSR_TESTGUARD"], "child:" + os.path.basename(sys.argv[0] if sys.argv and sys.argv[0] else "python"))
