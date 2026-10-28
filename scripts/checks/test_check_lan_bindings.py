#!/usr/bin/env python3
"""Tests for check_lan_bindings.py - stdlib only; run with pytest OR `python test_check_lan_bindings.py`.

The real check renders the real compose files; these cases feed its judging functions
synthetic renders instead, so the refusals the live renders never produce today (a
duplicate binding, a wildcard, a stray LAN port, a changed llm-gateway) are proven to
fire. No docker.
"""
from __future__ import annotations

import os
import shutil
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


class LiveVerdictFromRender(unittest.TestCase):
    def verdict(self, *ports):
        return c.judge_live_ports("inference", ui_render(*ports))

    def test_loopback_only_is_inert(self):
        self.assertEqual(self.verdict(), ([], "inert (loopback only)"))

    def test_specific_address_is_ok(self):
        probs, v = self.verdict(port(IP, 8445, 8080))
        self.assertEqual(probs, [])
        self.assertIn("ok", v)

    def test_wildcards_are_refused(self):
        for host in ("", "0.0.0.0", "::"):
            probs, v = self.verdict(port(host, 8445, 8080))
            self.assertTrue(probs and v == "REFUSED", host)

    def test_unlisted_non_loopback_port_is_refused(self):
        self.assertTrue(self.verdict(port(IP, 4000, 4000))[0])

    def test_loopback_address_on_lan_port_is_inert(self):
        self.assertEqual(self.verdict(port("127.0.0.1", 8445, 8080))[1], "inert (loopback only)")

    def test_multicast_address_is_refused(self):
        self.assertTrue(self.verdict(port("224.0.0.1", 8445, 8080))[0])


@unittest.skipUnless(shutil.which("docker"), "needs docker compose (render only)")
class LiveVerdictIntegration(unittest.TestCase):
    """The tester's two failing cases, through compose's real native render of a scratch project."""

    COMPOSE = ("services:\n  llm-gateway-ui:\n    image: x\n    ports:\n"
               "      - '${LITELLM_UI_LAN_IP:-127.0.0.1}:8445:8080'\n")

    def run_env(self, text):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docker-compose.yml").write_text(self.COMPOSE, encoding="utf-8")
            (Path(d) / ".env").write_bytes(text.encode("utf-8"))
            return c.native_render_verdict("inference", "docker-compose.yml", Path(d))

    def test_multiline_quoted_value_hides_nothing(self):
        # compose treats the last two lines as ONE value of X, so the first assignment wins
        probs, v = self.run_env('LITELLM_UI_LAN_IP=0.0.0.0\nX="\nLITELLM_UI_LAN_IP=192.0.2.10 #"\n')
        self.assertEqual(v, "REFUSED", probs)

    def test_lowercase_key_is_seen(self):
        probs, v = self.run_env("litellm_ui_lan_ip=0.0.0.0\n")
        self.assertEqual(v, "REFUSED", probs)

    def test_good_value_ok_and_unset_inert(self):
        self.assertEqual(self.run_env("LITELLM_UI_LAN_IP=192.0.2.10 # c\n")[0], [])
        self.assertEqual(self.run_env("OTHER=1\n")[1], "inert (loopback only)")

    def test_value_compose_rejects_is_fail_closed(self):
        self.assertEqual(self.run_env('LITELLM_UI_LAN_IP=" 192.0.2.10"\n')[1], "REFUSED")
        self.assertEqual(self.run_env('LITELLM_UI_LAN_IP="192.0.2.10\n')[1], "REFUSED")


@unittest.skipUnless(shutil.which("docker"), "needs docker compose (render only)")
class LiveProfilesAndScrub(unittest.TestCase):
    """Profile-gated services and the process-env scrub, through a real compose render."""

    COMPOSE = ("services:\n  llm-gateway-ui:\n    image: x\n    profiles: [gpu]\n    ports:\n"
               "      - '${LITELLM_UI_LAN_IP:-127.0.0.1}:8445:8080'\n  other:\n    image: y\n")

    def project(self, env_text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "docker-compose.yml").write_text(self.COMPOSE, encoding="utf-8")
        (Path(d.name) / ".env").write_bytes(env_text.encode("utf-8"))
        return Path(d.name)

    def test_profile_gated_wildcard_is_invisible_without_the_profile_and_seen_with_it(self):
        d = self.project("LITELLM_UI_LAN_IP=0.0.0.0\n")
        self.assertEqual(c.native_render_verdict("inference", "docker-compose.yml", d)[1],
                         "inert (loopback only)")  # why the profile rows exist
        self.assertEqual(c.native_render_verdict("inference", "docker-compose.yml", d, ("gpu",))[1], "REFUSED")

    def test_live_rows_cover_every_renders_profile_set(self):
        for plane in c.LAN:
            want = {tuple(pr) for (pl, pr, _b) in c.RENDERS if pl == plane and pr}
            self.assertTrue(want <= set(c.live_rows(plane)), plane)
        self.assertIn(("gpu",), c.live_rows("frontend"))
        self.assertIn(("stock",), c.live_rows("frontend"))

    def test_partial_env_profile_set_is_a_note_not_a_refusal(self):
        d = self.project("COMPOSE_PROFILES=nope\nLITELLM_UI_LAN_IP=192.0.2.10\n")
        with mock.patch.object(c, "live_rows", return_value=[None, ("gpu",)]):
            probs, label, notes = c.live_plane_verdicts("inference", "docker-compose.yml", d)
        # an unknown profile in the .env still renders (nothing selected) - and the gpu row sees the port
        self.assertEqual(probs, [], probs)
        self.assertIn("ok", label)

    def test_process_env_cannot_change_the_verdict(self):
        d = self.project("OTHER=1\n")
        hostile = {"LITELLM_UI_LAN_IP": "0.0.0.0", "litellm_ui_lan_ip": "0.0.0.0",
                   "COMPOSE_PROJECT_NAME": "zzz", "COMPOSE_FILE": "does-not-exist.yml",
                   "COMPOSE_ENV_FILES": str(d / "nope.env"), "COMPOSE_PROFILES": "gpu"}
        with mock.patch.dict(os.environ, hostile):
            probs, v = c.native_render_verdict("inference", "docker-compose.yml", d)
        self.assertEqual((probs, v), ([], "inert (loopback only)"))

    def test_compose_env_files_pointing_at_a_bad_file_is_ignored(self):
        d = self.project("LITELLM_UI_LAN_IP=192.0.2.10\n")
        bad = d / "bad.env"
        bad.write_text("LITELLM_UI_LAN_IP=0.0.0.0\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"COMPOSE_ENV_FILES": str(bad)}):
            probs, v = c.native_render_verdict("inference", "docker-compose.yml", d, ("gpu",))
        self.assertEqual(probs, [])
        self.assertIn("ok", v)


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


def viewer_render(*ports, workbench_ports=None):
    """research-workbench P2b (rw-p2b-workspace): the ob1 plane's front end - the wiki viewer, which
    serves /workspace and forwards /workbench/* with the key server-side - and its backend."""
    wb = {"image": "workbench", "ports": list(workbench_ports or [port("127.0.0.1", 8814, 8000)])}
    viewer = {"image": "viewer", "ports": [port("127.0.0.1", 8812, 8080), *ports]}
    return {"name": "open-brain", "services": {"openbrain-wiki-viewer": viewer, "openbrain-workbench": wb}}


OB1_BASE = {("openbrain-wiki-viewer", "127.0.0.1", "8812", "8080"), ("openbrain-workbench", "127.0.0.1", "8814", "8000")}


class Ob1Viewer(unittest.TestCase):
    """WIKI_VIEWER_LAN_IP: inert while unset; exactly the viewer's :8812 on one address when set;
    any other binding - the workbench (a backend), another port, a wildcard - refused."""

    def test_unset_is_inert(self):
        self.assertEqual(c.check_render("ob1", "t", viewer_render(), OB1_BASE, None), [])

    def test_set_binds_exactly_the_viewer_port(self):
        self.assertEqual(c.check_render("ob1", "t", viewer_render(port(IP, 8812, 8080)), OB1_BASE, IP), [])

    def test_viewer_lan_port_while_unset_is_refused(self):
        out = c.check_render("ob1", "t", viewer_render(port(IP, 8812, 8080)), OB1_BASE, None)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_backend_on_the_lan_is_refused(self):
        out = c.check_render("ob1", "t", viewer_render(port(IP, 8812, 8080), workbench_ports=[
            port("127.0.0.1", 8814, 8000), port(IP, 8814, 8000)]), OB1_BASE, IP)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_another_viewer_port_is_refused(self):
        out = c.check_render("ob1", "t", viewer_render(port(IP, 8080, 8080)), OB1_BASE, IP)
        self.assertTrue(any("non-loopback" in p for p in out), out)

    def test_wildcard_is_refused(self):
        out = c.check_render("ob1", "t", viewer_render(port("0.0.0.0", 8812, 8080)), OB1_BASE, IP)
        self.assertTrue(any("WILDCARD" in p for p in out), out)

    def test_live_verdicts(self):
        self.assertEqual(c.judge_live_ports("ob1", viewer_render())[1], "inert (loopback only)")
        self.assertEqual(c.judge_live_ports("ob1", viewer_render(port(IP, 8812, 8080)))[0], [])
        probs, verdict = c.judge_live_ports("ob1", viewer_render(workbench_ports=[port(IP, 8814, 8000)]))
        self.assertEqual(verdict, "REFUSED")
        self.assertFalse(any(IP in p for p in probs), "a verdict never prints the value")

    def test_ob1_is_rendered_with_the_viewer_profile(self):
        rows = [(pl, prof) for (pl, prof, _b) in c.RENDERS if pl == "ob1"]
        self.assertIn(("ob1", ("wiki",)), rows)
        self.assertEqual(c.COMPOSE["ob1"], ("OB1/docker/docker-compose.yml", "OB1/docker/.env.example"))


if __name__ == "__main__":
    unittest.main()
