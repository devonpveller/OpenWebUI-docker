#!/usr/bin/env python3
"""Tests for check_port_healthcheck.py - stdlib only; run with pytest OR `python test_check_port_healthcheck.py`.

Each case writes a synthetic `docker compose config --format json` render (and, where the
case needs one, a Dockerfile and an allow-list) into a temp dir and runs the real CLI in a
subprocess, the way check-project-configs.ps1 runs it. No docker.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check_port_healthcheck.py")
PORT = [{"mode": "ingress", "host_ip": "127.0.0.1", "target": 8000, "published": "8999", "protocol": "tcp"}]
HC = {"test": ["CMD", "true"], "interval": "30s"}


class PortHealthcheckRule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.allow = self._write("allow.json", {"services": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, obj):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)
        return path

    def run_gate(self, services, allow=None):
        render = self._write("render.json", {"name": "fixture", "services": services})
        if allow is not None:
            self.allow = self._write("allow.json", {"services": allow})
        p = subprocess.run([sys.executable, GATE, "--render", f"fixture={render}",
                            "--allowlist", self.allow], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_port_without_healthcheck_is_refused(self):
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT}})
        self.assertEqual(code, 1, out)
        self.assertIn("PUBLISHED PORT WITHOUT HEALTHCHECK: fixture/svc-a", out)

    def test_port_with_compose_healthcheck_passes(self):
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT, "healthcheck": HC}})
        self.assertEqual(code, 0, out)
        self.assertIn("compose healthcheck 1", out)

    def test_disabled_healthcheck_is_refused(self):
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT, "healthcheck": {"disable": True}}})
        self.assertEqual(code, 1, out)
        self.assertIn("DISABLED", out)

    def test_no_published_port_is_not_in_scope(self):
        svc = {"image": "x", "ports": [{"target": 8000, "protocol": "tcp"}], "expose": ["8000"]}
        code, out = self.run_gate({"svc-a": svc})
        self.assertEqual(code, 0, out)

    def test_dockerfile_healthcheck_passes(self):
        ctx = os.path.join(self.dir, "ctx")
        os.makedirs(ctx)
        self._write("ctx/Dockerfile", "FROM x\nHEALTHCHECK --interval=30s \\\n    CMD curl -f http://localhost/ || exit 1\n")
        code, out = self.run_gate({"svc-a": {"build": {"context": ctx, "dockerfile": "Dockerfile"}, "ports": PORT}})
        self.assertEqual(code, 0, out)
        self.assertIn("Dockerfile HEALTHCHECK 1", out)

    def test_dockerfile_healthcheck_none_is_refused(self):
        ctx = os.path.join(self.dir, "ctx")
        os.makedirs(ctx)
        self._write("ctx/Dockerfile", "FROM x\nHEALTHCHECK CMD true\nHEALTHCHECK NONE\n")
        code, out = self.run_gate({"svc-a": {"build": {"context": ctx, "dockerfile": "Dockerfile"}, "ports": PORT}})
        self.assertEqual(code, 1, out)

    def test_allow_listed_service_passes(self):
        allow = {"svc-a": {"kind": "gap", "reason": "known debt"}}
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT}}, allow)
        self.assertEqual(code, 0, out)
        self.assertIn("allow-list 1", out)

    def test_stale_allow_row_is_refused(self):
        allow = {"svc-a": {"kind": "gap", "reason": "known debt"}}
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT, "healthcheck": HC}}, allow)
        self.assertEqual(code, 1, out)
        self.assertIn("STALE ALLOW-LIST ROW: 'svc-a'", out)

    def test_unrendered_allow_row_is_reported_not_passed_silently(self):
        allow = {"svc-elsewhere": {"kind": "gap", "reason": "lives in an unrendered plane"}}
        code, out = self.run_gate({"svc-a": {"image": "x"}}, allow)
        self.assertEqual(code, 0, out)
        self.assertIn("NOT VERIFIED", out)
        self.assertIn("svc-elsewhere", out)

    def test_row_without_reason_is_bad_input(self):
        code, out = self.run_gate({"svc-a": {"image": "x", "ports": PORT}}, {"svc-a": {"kind": "gap", "reason": " "}})
        self.assertEqual(code, 2, out)

    def test_no_render_is_not_a_pass(self):
        p = subprocess.run([sys.executable, GATE, "--allowlist", self.allow], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout)

    def test_scan_reports_what_it_scanned(self):
        code, out = self.run_gate({"a": {"image": "x"}, "b": {"image": "y"}})
        self.assertEqual(code, 0, out)
        self.assertIn("1 render(s), 2 service(s) scanned", out)

    def test_shipped_allowlist_is_valid(self):
        sys.path.insert(0, HERE)
        import check_port_healthcheck as m
        rows = m.load_allowlist(m.DEFAULT_ALLOWLIST)
        self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
