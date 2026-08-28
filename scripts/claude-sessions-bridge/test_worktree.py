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


class ReleaseDirectiveTests(unittest.TestCase):
    """`release: <id>` is the pipeline's human gate reached from a thread."""

    def _parse(self, text):
        m = bridge.RELEASE_DIRECTIVE_RE.match(text)
        return (m.group(1), m.group(2).strip()) if m else None

    def test_parses_id_and_trailing_prompt(self):
        self.assertEqual(self._parse("release: search-readme"), ("search-readme", ""))
        self.assertEqual(self._parse("release:coder-readme carry on"), ("coder-readme", "carry on"))

    def test_colon_required_so_prose_cannot_release_work(self):
        self.assertIsNone(self._parse("release the item when you can"))
        self.assertIsNone(self._parse("released the hounds"))

    def test_does_not_collide_with_the_tool_approval_relay(self):
        """`approve`/`ok`/`yes` already mean "run that gated command" mid-turn. One word
        meaning both that and "send this to review" would be ambiguous exactly when it
        matters, which is why the gate token is `release`."""
        self.assertFalse(bridge.VERDICT_RE.match("release: x"))
        for verdict in ("approve", "ok", "yes", "lgtm"):
            self.assertIsNone(self._parse(verdict))
            self.assertTrue(bridge.VERDICT_RE.match(verdict))

    def test_every_reported_state_has_a_note(self):
        """A transition the operator cares about must not pass silently."""
        for state in ("test-failed", "test-passed", "ready-review", "merged", "rejected"):
            self.assertIn(state, bridge.QUEUE_NOTES)
        # the gate note must name the exact reply that opens it - a pass that just waits
        # looks stalled
        self.assertIn("release: {id}", bridge.QUEUE_NOTES["test-passed"])

    def test_queue_dir_is_the_shared_repo_namespace(self):
        d = bridge.queue_dir()
        self.assertTrue(d, "queue dir did not resolve")
        self.assertIn("agent-worktrees", d.replace("/", os.sep))
        self.assertNotIn("scripts", d, "queue must not resolve under the script directory")


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

        # THE REGRESSION THAT MATTERS (soak run 1): coordination state must be ONE
        # namespace per repository. When the toolkit is checked out inside a worktree,
        # that copy must resolve the SAME lease/registry dir as the main checkout - or
        # two agents each get "ACQUIRED" for `merge` and exclude nobody.
        wt_common = os.path.join(path, "scripts", "worktree", "common.ps1")
        if not os.path.isfile(wt_common):
            # Explicit skip, never a silent pass: when this file is uncommitted or the
            # work line predates the toolkit, the worktree has no copy and there is
            # nothing to compare - but a guard that quietly evaporates is worse than none.
            self.skipTest("toolkit not present in the worktree (uncommitted?) - "
                          "shared-state regression NOT exercised")
        if True:
            main_common = os.path.join(bridge._REPO_ROOT, "scripts", "worktree", "common.ps1")
            dirs = []
            for script, cwd in ((wt_common, path), (main_common, bridge._REPO_ROOT)):
                r = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                     f". '{script}'; Get-SharedStateDir"],
                    cwd=cwd, capture_output=True, text=True, timeout=120)
                dirs.append((r.stdout or "").strip().lower())
            self.assertEqual(dirs[0], dirs[1],
                             "a toolkit copy inside a worktree resolved a DIFFERENT state dir "
                             f"({dirs[0]!r} vs {dirs[1]!r}) - leases would exclude nobody")
            self.assertTrue(dirs[0], "state dir resolved empty")

        # Retirement REFUSES while the probe is uncommitted (exit 2), rather than deleting it.
        rc, out = bridge._run_worktree_script("remove-worktree.ps1", ["-Id", self.ID])
        self.assertEqual(rc, 2, f"expected refusal while dirty, got {rc}:\n{out}")
        self.assertTrue(os.path.isdir(path), "refused, but the worktree was removed anyway")

        # ...and succeeds when told to discard deliberately.
        rc, out = bridge._run_worktree_script("remove-worktree.ps1", ["-Id", self.ID, "-Force"])
        self.assertEqual(rc, 0, f"forced removal failed:\n{out}")
        self.assertFalse(os.path.isdir(path))


@unittest.skipUnless(_tooling_available(), "needs Windows + git + scripts/worktree")
class LeaseTests(unittest.TestCase):
    """Named-lease mutual exclusion (lease.ps1), exercised through the real script.

    Leases now cover the SHARED RUNTIME only - `merge` is no longer a lease name, because
    git refuses concurrent merge worktrees and only a reviewer merges (see queue.ps1).

    Hermetic: AI_STACK_LEASE_DIR points every invocation at a temp dir, so these tests
    can grab real plane names ('merge', 'frontend', ...) without ever colliding with an
    actual agent's lease. Name VALIDATION still reads the repo's lease-names.conf, so
    the policy file is covered too."""

    A, B = "wt-selftest-a", "wt-selftest-b"

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.lease_dir = tempfile.mkdtemp(prefix="lease-test-")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.lease_dir, ignore_errors=True)

    def _lease(self, *args):
        env = dict(os.environ, AI_STACK_LEASE_DIR=self.lease_dir)
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               os.path.join(bridge.WORKTREE_SCRIPTS, "lease.ps1")] + list(args)
        p = subprocess.run(cmd, cwd=bridge._REPO_ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=env)
        return p.returncode, ((p.stdout or "") + "\n" + (p.stderr or "")).strip()

    def tearDown(self):
        for owner in (self.A, self.B):
            self._lease("-Release", "-Name", "memory,frontend,coder,open-brain",
                        "-Owner", owner)

    def test_second_agent_is_told_to_wait_not_allowed_through(self):
        rc, _ = self._lease("-Acquire", "-Name", "memory", "-Owner", self.A, "-Thread", "t-a")
        self.assertEqual(rc, 0)
        rc, out = self._lease("-Acquire", "-Name", "memory", "-Owner", self.B)
        self.assertEqual(rc, 3, "a second agent must be told to WAIT")
        self.assertIn("WAIT", out)

    def test_disjoint_planes_do_not_serialize(self):
        """The entire point of naming leases: no interference -> no queueing."""
        self.assertEqual(self._lease("-Acquire", "-Name", "frontend", "-Owner", self.A)[0], 0)
        self.assertEqual(self._lease("-Acquire", "-Name", "coder", "-Owner", self.B)[0], 0)

    def test_foreign_release_is_refused(self):
        self.assertEqual(self._lease("-Acquire", "-Name", "memory", "-Owner", self.A)[0], 0)
        rc, out = self._lease("-Release", "-Name", "memory", "-Owner", self.B)
        self.assertEqual(rc, 3)
        self.assertIn("refusing", out.lower())
        # ...and the rightful owner still holds it (idempotent re-acquire succeeds).
        self.assertEqual(self._lease("-Acquire", "-Name", "memory", "-Owner", self.A)[0], 0)

    def test_expired_lease_needs_explicit_takeover(self):
        # ttl 0 = instantly expired. Regression for the -ge/-gt boundary: with -gt a
        # dead agent's ttl-0 lease never expired and held the queue forever.
        self.assertEqual(self._lease("-Acquire", "-Name", "memory", "-Owner", self.A,
                                     "-TtlMin", "0")[0], 0)
        rc, out = self._lease("-Acquire", "-Name", "memory", "-Owner", self.B)
        self.assertEqual(rc, 3, "an expired lease must not be silently stolen")
        self.assertIn("EXPIRED", out)
        rc, _ = self._lease("-Takeover", "-Name", "memory", "-Owner", self.B)
        self.assertEqual(rc, 0, "-Takeover must claim an expired lease")

    def test_multi_name_is_all_or_nothing(self):
        """A partial set held while waiting is deadlock bait - it must roll back."""
        self.assertEqual(self._lease("-Acquire", "-Name", "frontend", "-Owner", self.A)[0], 0)
        # B wants coder+frontend; coder is free, frontend is A's. Sorted order takes
        # coder FIRST, so the rollback path is genuinely exercised.
        rc, out = self._lease("-Acquire", "-Name", "coder,frontend", "-Owner", self.B)
        self.assertEqual(rc, 3)
        self.assertIn("rolled back", out)
        # The proof: a third party can take coder immediately - B left nothing behind.
        self.assertEqual(self._lease("-Acquire", "-Name", "coder", "-Owner", self.A)[0], 0)

    def test_unknown_name_is_refused_so_typos_cannot_fragment_locking(self):
        rc, out = self._lease("-Acquire", "-Name", "openbrain", "-Owner", self.A)
        self.assertEqual(rc, 1, "a name absent from lease-names.conf must be refused")
        self.assertIn("unknown lease name", out.lower())
        # -AdHoc is the deliberate escape hatch for new coordination points.
        rc, _ = self._lease("-Acquire", "-Name", "zz-adhoc-probe", "-Owner", self.A, "-AdHoc")
        self.assertEqual(rc, 0)
        self._lease("-Release", "-Name", "zz-adhoc-probe", "-Owner", self.A, "-AdHoc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
