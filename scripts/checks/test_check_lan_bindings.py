#!/usr/bin/env python3
"""Tests for check_lan_bindings.py - stdlib only; run with pytest OR `python test_check_lan_bindings.py`.

The real check renders the real compose files; these cases feed its judging functions
synthetic renders instead, so the refusals the live renders never produce today (a
duplicate binding, a wildcard, a stray LAN port, a changed llm-gateway) are proven to
fire. No docker.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_lan_bindings as c  # noqa: E402

IP = "192.0.2.10"


def port(host, published, target):
    return {"mode": "ingress", "host_ip": host, "target": int(target), "published": str(published),
            "protocol": "tcp"}


def ui_render(*ports, gateway_ports=None):
    gw = {"image": "litellm", "networks": {"llm-net": None}}
    if gateway_ports:
        gw["ports"] = list(gateway_ports)
    ui = {"image": "litellm", "networks": {"llm-net": None, "app-net": None}}
    if ports:
        ui["ports"] = list(ports)
    return {"name": "inference", "services": {"llm-gateway": gw, "llm-gateway-ui": ui}}


def mm_render(*ports):
    return {"name": "agent-org", "services": {
        "mattermost": {"image": "mm", "ports": list(ports)},
        "agent-bridge": {"image": "ab", "ports": [port("127.0.0.1", 8830, 8000)]},
    }}


class Judging(unittest.TestCase):
    def test_inert_inference_render_passes(self):
        self.assertEqual(c.check_render("inference", "t", ui_render(), set(), None), [])

    def test_set_inference_render_passes(self):
        self.assertEqual(c.check_render("inference", "t", ui_render(port(IP, 8445, 8080)), set(), IP), [])

    def test_lan_port_while_unset_is_refused(self):
        out = c.check_render("inference", "t", ui_render(port(IP, 8445, 8080)), set(), None)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_wrong_ip_is_refused(self):
        out = c.check_render("inference", "t", ui_render(port("192.0.2.11", 8445, 8080)), set(), IP)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_extra_lan_port_is_refused(self):
        out = c.check_render("inference", "t", ui_render(port(IP, 8445, 8080), port(IP, 4000, 4000)),
                             set(), IP)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_wildcard_is_refused_set_or_not(self):
        for host in ("", "0.0.0.0", "::"):
            out = c.check_render("inference", "t", ui_render(port(host, 8445, 8080)), set(), None)
            self.assertTrue(any("WILDCARD" in p for p in out), (host, out))

    def test_gateway_port_is_refused(self):
        out = c.check_render("inference", "t", ui_render(gateway_ports=[port("127.0.0.1", 4000, 8080)]),
                             {("llm-gateway", "127.0.0.1", "4000", "8080")}, None)
        self.assertTrue(any("llm-gateway publishes" in p for p in out), out)

    def test_duplicate_binding_is_refused(self):
        # what an `up` would see if compose stopped collapsing the unset LAN line
        lb = port("127.0.0.1", 8065, 8065)
        out = c.check_render("agent-org", "t", mm_render(lb, dict(lb)), c._AO, None)
        self.assertTrue(any("DUPLICATE" in p for p in out), out)

    def test_loopback_change_is_refused(self):
        out = c.check_render("agent-org", "t", mm_render(port("127.0.0.1", 8066, 8065)), c._AO, None)
        self.assertTrue(any("loopback bindings changed" in p for p in out), out)

    def test_mattermost_set_passes(self):
        out = c.check_render("agent-org", "t", mm_render(port("127.0.0.1", 8065, 8065), port(IP, 8065, 8065)),
                             c._AO, IP)
        self.assertEqual(out, [])


class UnsetVersusSet(unittest.TestCase):
    def test_only_the_lan_port_differs_passes(self):
        self.assertEqual(c.compare_unset_set("t", ui_render(), ui_render(port(IP, 8445, 8080)), "inference", IP), [])

    def test_gateway_change_is_refused(self):
        setd = ui_render(port(IP, 8445, 8080))
        setd["services"]["llm-gateway"]["networks"]["app-net"] = None
        out = c.compare_unset_set("t", ui_render(), setd, "inference", IP)
        self.assertTrue(any("changed service llm-gateway" in p for p in out), out)

    def test_ui_change_beyond_port_is_refused(self):
        setd = ui_render(port(IP, 8445, 8080))
        setd["services"]["llm-gateway-ui"]["networks"]["default"] = None
        out = c.compare_unset_set("t", ui_render(), setd, "inference", IP)
        self.assertTrue(any("changed service llm-gateway-ui" in p for p in out), out)

    def test_top_level_change_is_refused(self):
        setd = ui_render(port(IP, 8445, 8080))
        setd["networks"] = {"new": {}}
        out = c.compare_unset_set("t", ui_render(), setd, "inference", IP)
        self.assertTrue(any("top-level `networks`" in p for p in out), out)


class LiveValue(unittest.TestCase):
    def test_specific_address_is_accepted(self):
        self.assertIsNone(c.validate_lan_ip("192.0.2.10"))

    def test_refusals(self):
        for bad in ("0.0.0.0", "::", "127.0.0.1", "127.0.0.2", "host.lan", "192.0.2.10:3000",
                    "224.0.0.1", "255.255.255.255", "", " "):
            self.assertIsNotNone(c.validate_lan_ip(bad), bad)


class EnvValueLikeCompose(unittest.TestCase):
    """Expectations measured against `docker compose config` v5.3.0 on 2026-10-16."""

    def test_values_compose_accepts(self):
        for raw, want in (("1.2.3.4", "1.2.3.4"), ('"1.2.3.4"', "1.2.3.4"), ("'1.2.3.4'", "1.2.3.4"),
                          (" 1.2.3.4", "1.2.3.4"), ("1.2.3.4   ", "1.2.3.4"),
                          ("1.2.3.4 # c", "1.2.3.4"), ('"1.2.3.4" # c', "1.2.3.4"), ("", "")):
            self.assertEqual(c.parse_env_value(raw), want, raw)

    def test_quoted_whitespace_is_kept_and_refused(self):
        for raw in ('" 192.0.2.10"', '"192.0.2.10 "', "' 192.0.2.10'"):
            self.assertIsNotNone(c.validate_lan_ip(c.parse_env_value(raw)), raw)

    def test_hash_without_space_is_part_of_the_value(self):
        self.assertIsNotNone(c.validate_lan_ip(c.parse_env_value("192.0.2.10#c")))

    def test_unterminated_quote_is_an_error(self):
        with self.assertRaises(ValueError):
            c.parse_env_value('"192.0.2.10')

    def test_validate_does_not_strip(self):
        self.assertIsNotNone(c.validate_lan_ip(" 192.0.2.10"))
        self.assertIsNotNone(c.validate_lan_ip("192.0.2.10 "))

    def test_file_read_last_wins_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("OWUI_LAN_IP=10.0.0.1\n# x\nexport OWUI_LAN_IP = \"192.0.2.10\" # c\n", encoding="utf-8")
            self.assertEqual(c.read_env_value(f, "OWUI_LAN_IP"), "192.0.2.10")
            self.assertIsNone(c.read_env_value(f, "OTHER"))
            f.write_text('OWUI_LAN_IP="1.2.3.4\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                c.read_env_value(f, "OWUI_LAN_IP")


class RenderWithoutLiveEnv(unittest.TestCase):
    def _cmd(self, env_present):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = list(cmd)
            if "--project-directory" in cmd:
                scratch = Path(cmd[cmd.index("--project-directory") + 1])
                seen["scratch_env"] = (scratch / ".env").is_file()
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        with mock.patch.object(c.Path, "is_file", return_value=env_present),              mock.patch.object(c.shutil, "copyfile"), mock.patch.object(c.subprocess, "run", fake_run):
            c.render("agent-org", (), None)
        return seen

    def test_absent_env_uses_scratch_project_directory(self):
        seen = self._cmd(False)
        self.assertIn("--project-directory", seen["cmd"])

    def test_present_env_renders_in_place(self):
        seen = self._cmd(True)
        self.assertNotIn("--project-directory", seen["cmd"])


if __name__ == "__main__":
    unittest.main()
