#!/usr/bin/env python3
"""Unit + integration tests for worktree-per-thread (Phase B) — stdlib only:

    python scripts/claude-sessions-bridge/test_worktree.py

Unit tests cover the directive grammar, id derivation, and the fail-closed contract.
The integration tests actually provision and retire a real git worktree through
scripts/worktree/*.ps1 (they self-skip when git or PowerShell is unavailable, and always
clean up after themselves) — the point of this feature is filesystem isolation, and a
mock cannot show that two threads stop sharing an index.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402


def _tooling_available() -> bool:
    if os.name != "nt":
        return False
    if not os.path.isdir(bridge.WORKTREE_SCRIPTS):
        return False
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=bridge._REPO_ROOT, capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


class DirectiveTests(unittest.TestCase):
    """`worktree: on|off` must behave exactly like the sibling `model:`/`mode:` directives."""

    def _parse(self, text):
        m = bridge.WORKTREE_DIRECTIVE_RE.match(text)
        return (m.group(1).lower(), m.group(2).strip()) if m else None

    def test_on_off_with_trailing_prompt(self):
        self.assertEqual(self._parse("worktree: on\ndo the thing"), ("on", "do the thing"))
        self.assertEqual(self._parse("worktree: off"), ("off", ""))

    def test_equals_and_case_and_spacing(self):
        self.assertEqual(self._parse("WorkTree = ON"), ("on", ""))
        self.assertEqual(self._parse("  worktree:off  "), ("off", ""))

    def test_colon_required_so_prose_cannot_trigger_it(self):
        # The same guard `model:` has: a sentence STARTING with the word must not switch modes.
        self.assertIsNone(self._parse("worktree isolation would be nice here"))
        self.assertIsNone(self._parse("the worktree: is fine"))

    def test_invalid_value_is_reported_not_guessed(self):
        # Parsing succeeds; execute() rejects the value and keeps the current setting.
        self.assertEqual(self._parse("worktree: maybe"), ("maybe", ""))


class IdTests(unittest.TestCase):
    def test_id_is_stable_and_short(self):
        tid = "p4iic4trq7fnun7ke8arenhyuw"
        self.assertEqual(bridge.worktree_id(tid), "mm-p4iic4tr")
        self.assertEqual(bridge.worktree_id(tid), bridge.worktree_id(tid))
        # new-worktree.ps1 caps ids at 24 chars (Windows MAX_PATH + node_modules depth).
        self.assertLessEqual(len(bridge.worktree_id(tid)), 24)

    def test_id_is_lowercase_and_path_safe(self):
        wid = bridge.worktree_id("ABCDEF12345")
        self.assertEqual(wid, "mm-abcdef12")
        self.assertRegex(wid, r"^[a-z0-9][a-z0-9-]{0,23}$")


class DefaultTests(unittest.TestCase):
    def test_default_is_off(self):
        """Deploying Phase B must change nothing until a thread opts in."""
        self.assertFalse(bridge.WORKTREE_DEFAULT,
                         "BRIDGE_WORKTREE_DEFAULT must default off (set in this environment?)")

    def test_run_turn_defaults_to_the_shared_repo(self):
        import inspect
        sig = inspect.signature(bridge.run_turn)
        self.assertEqual(sig.parameters["cwd"].default, "",
                         "cwd must default empty so existing callers keep using REPO")


class ScriptFailureTests(unittest.TestCase):
    def test_missing_script_returns_error_not_exception(self):
        """A broken/absent script must surface as an in-thread message, never a traceback."""
        rc, out = bridge._run_worktree_script("does-not-exist.ps1", [], timeout=60)
        self.assertNotEqual(rc, 0)
        self.assertTrue(out)


@unittest.skipUnless(_tooling_available(), "needs Windows + git + scripts/worktree")
class ProvisioningIntegrationTests(unittest.TestCase):
    """Real worktrees. These prove the isolation claim rather than asserting it."""

    ID = "mm-selftest"

    @classmethod
    def _cleanup(cls):
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(bridge.WORKTREE_SCRIPTS, "remove-worktree.ps1"),
                        "-Id", cls.ID, "-Force"],
                       cwd=bridge._REPO_ROOT, capture_output=True, text=True, timeout=300)

    @classmethod
    def setUpClass(cls):
        cls._cleanup()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    def test_provision_then_isolate_then_retire(self):
        rc, out = bridge._run_worktree_script("new-worktree.ps1", [
            "-Id", self.ID, "-OwnerKind", "bridge", "-OwnerRef", "selftest", "-Reuse", "-Json"])
        self.assertEqual(rc, 0, f"provisioning failed:\n{out}")

        path = ""
        for line in reversed([ln for ln in out.splitlines() if ln.strip()]):
            try:
                import json as _json
                path = (_json.loads(line) or {}).get("path", "")
            except Exception:  # noqa: BLE001
                continue
            if path:
                break
        self.assertTrue(path and os.path.isdir(path), f"no worktree path in output:\n{out}")

        # The three things a BARE `git worktree add` gets wrong here.
        self.assertTrue(os.path.isfile(os.path.join(path, ".env")), "runtime .env not copied")
        self.assertTrue(os.listdir(os.path.join(path, "OB1")), "OB1 submodule left empty")
        branch = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(branch, f"work/{self.ID}")

        # Isolation: a write here must not appear in the main checkout.
        probe = os.path.join(path, "SELFTEST-PROBE.txt")
        with open(probe, "w", encoding="ascii") as fh:
            fh.write("isolation probe")
        self.assertFalse(os.path.exists(os.path.join(bridge._REPO_ROOT, "SELFTEST-PROBE.txt")))

        # Copied env files must never show as untracked (one `git add .` from a leak).
        status = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=60).stdout
        self.assertNotIn(".env", status, f"an env copy is exposed to the index:\n{status}")

        # Retirement REFUSES while the probe is uncommitted (exit 2), rather than deleting it.
        rc, out = bridge._run_worktree_script("remove-worktree.ps1", ["-Id", self.ID])
        self.assertEqual(rc, 2, f"expected refusal while dirty, got {rc}:\n{out}")
        self.assertTrue(os.path.isdir(path), "refused, but the worktree was removed anyway")

        # ...and succeeds when told to discard deliberately.
        rc, out = bridge._run_worktree_script("remove-worktree.ps1", ["-Id", self.ID, "-Force"])
        self.assertEqual(rc, 0, f"forced removal failed:\n{out}")
        self.assertFalse(os.path.isdir(path))


@unittest.skipUnless(_tooling_available(), "needs Windows + git + scripts/worktree")
class MergeLockTests(unittest.TestCase):
    """The merge queue's mutual exclusion, exercised through the real script."""

    A, B = "wt-selftest-a", "wt-selftest-b"

    def _lock(self, *args):
        return bridge._run_worktree_script("merge-lock.ps1", list(args), timeout=120)

    def tearDown(self):
        for owner in (self.A, self.B):
            self._lock("-Release", "-Owner", owner)

    def test_second_agent_is_told_to_wait_not_allowed_through(self):
        rc, _ = self._lock("-Acquire", "-Owner", self.A, "-Thread", "t-a")
        self.assertEqual(rc, 0)
        rc, out = self._lock("-Acquire", "-Owner", self.B)
        self.assertEqual(rc, 3, "a second agent must be told to WAIT")
        self.assertIn("WAIT", out)

    def test_foreign_release_is_refused(self):
        self.assertEqual(self._lock("-Acquire", "-Owner", self.A)[0], 0)
        rc, out = self._lock("-Release", "-Owner", self.B)
        self.assertEqual(rc, 3)
        self.assertIn("refusing", out.lower())
        # ...and the rightful owner still holds it.
        self.assertEqual(self._lock("-Acquire", "-Owner", self.A)[0], 0)

    def test_expired_lock_needs_explicit_takeover(self):
        self.assertEqual(self._lock("-Acquire", "-Owner", self.A, "-TtlMin", "0")[0], 0)
        rc, out = self._lock("-Acquire", "-Owner", self.B)
        self.assertEqual(rc, 3, "an expired lock must not be silently stolen")
        self.assertIn("EXPIRED", out)
        rc, _ = self._lock("-Takeover", "-Owner", self.B)
        self.assertEqual(rc, 0, "-Takeover must claim an expired lock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
