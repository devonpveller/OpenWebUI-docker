"""Tests for check_intake_isolation.py (work item intake-gate-relay).

The pure judge() is exercised on a synthetic render pair that mirrors the shipped shape, then on
one mutation per refusal the anchor names: a second network on intake, the gateway on intake-net,
a non-internal net (plus the subnet rules and the member lists). The last test renders the real
compose files (skipped where there is no docker CLI) and must exit 0.
"""
from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_intake_isolation as cii  # noqa: E402

CALLERS = "search_intake-callers"
INTAKE_NET = "open-brain_intake-net"


def _ob1(intake: bool = True) -> dict:
    nets = {"obnet": {"name": "open-brain_obnet"},
            "llm-net": {"name": "ai-stack_llm-net", "external": True}}
    svcs = {"openbrain-db": {"networks": {"obnet": None}}}
    if intake:
        nets["intake-net"] = {"name": INTAKE_NET, "internal": True,
                              "ipam": {"config": [{"subnet": "10.243.50.0/24"}]}}
        nets["intake-callers"] = {"name": CALLERS, "external": True}
        svcs.update({
            "openbrain-intake": {"networks": {"intake-net": None}},
            "openbrain-intake-dbrelay": {"networks": {"intake-net": None, "obnet": None}},
            "openbrain-intake-llmrelay": {"networks": {"intake-net": {"aliases": ["llama-cpp"]},
                                                       "llm-net": {}}},
            "openbrain-intake-callrelay": {"networks": {"intake-net": None, "intake-callers": None}},
        })
    return {"name": "open-brain", "networks": nets, "services": svcs}


def _search() -> dict:
    return {"name": "search",
            "networks": {"default": {"name": "ai-stack_default", "external": True},
                         "search-net": {"name": "search_search-net", "internal": True},
                         "intake-callers": {"name": CALLERS, "internal": True,
                                            "ipam": {"config": [{"subnet": "10.243.51.0/24"}]}}},
            "services": {"gateway": {"networks": {"default": None, "search-net": None,
                                                  "intake-callers": None},
                                     "environment": {"INTAKE_URL": "http://openbrain-intake-callrelay:8000/v1/intake"},
                                     "sysctls": {"net.ipv4.ip_forward": "0"},
                                     "ports": [{"host_ip": "127.0.0.1", "published": "8085"}]},
                         "redis": {"networks": {"search-net": None}}}}


def _renders() -> dict:
    return {(): _ob1(False), ("intake",): _ob1(True)}


def test_the_shipped_shape_passes():
    assert cii.judge(_renders(), _search(), {}) == []


def _fails(ob1=None, search=None, others=None, needle=""):
    problems = cii.judge(ob1 or _renders(), search or _search(), others or {})
    assert problems, "expected a refusal"
    if needle:
        assert any(needle in x for x in problems), problems
    return problems


def test_a_second_network_on_intake_fails():
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake"]["networks"]["obnet"] = None
    _fails(ob1=r, needle="exactly one network")


def test_intake_on_callers_fails():
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake"]["networks"]["intake-callers"] = None
    _fails(ob1=r, needle="intake-callers members")


def test_the_gateway_on_intake_net_fails():
    s = _search()
    s["networks"]["inet"] = {"name": INTAKE_NET, "external": True}
    s["services"]["gateway"]["networks"]["inet"] = None
    _fails(search=s, needle="search-plane service is on intake-net")


def test_a_non_internal_intake_net_fails():
    r = _renders()
    r[("intake",)]["networks"]["intake-net"]["internal"] = False
    _fails(ob1=r, needle="not internal")


def test_a_non_internal_callers_net_fails():
    s = _search()
    del s["networks"]["intake-callers"]["internal"]
    _fails(search=s, needle="not internal")


@pytest.mark.parametrize("subnet,needle", [("192.168.51.0/24", "overlaps 192.168.0.0/16"),
                                           ("172.20.0.0/24", "overlaps 172.16.0.0/12"),
                                           ("10.240.3.0/24", "overlaps 10.240.0.0/16"),
                                           ("10.243.0.0/16", "not an IPv4 /24"),
                                           ("10.243.50.0/24", "overlaps intake-callers")])
def test_subnet_rules(subnet, needle):
    s = _search()
    s["networks"]["intake-callers"]["ipam"]["config"][0]["subnet"] = subnet
    _fails(search=s, needle=needle)


def test_a_net_without_a_subnet_fails():
    r = _renders()
    r[("intake",)]["networks"]["intake-net"]["ipam"] = {}
    _fails(ob1=r, needle="exactly one subnet")


def test_another_member_on_intake_net_fails():
    r = _renders()
    r[("intake",)]["services"]["openbrain-db"]["networks"]["intake-net"] = None
    _fails(ob1=r, needle="intake-net members")


def test_another_member_on_callers_fails():
    s = _search()
    s["services"]["redis"]["networks"]["intake-callers"] = None
    _fails(search=s, needle="intake-callers members")
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake-dbrelay"]["networks"]["intake-callers"] = None
    _fails(ob1=r, needle="intake-callers members")


def test_the_gateway_off_callers_fails():
    s = _search()
    del s["services"]["gateway"]["networks"]["intake-callers"]
    _fails(search=s, needle="search-gateway is not on intake-callers")


def test_callers_external_in_search_fails():
    s = _search()
    s["networks"]["intake-callers"]["external"] = True
    _fails(search=s, needle="NATIVELY")


def test_callers_native_in_ob1_fails():
    r = _renders()
    r[("intake",)]["networks"]["intake-callers"] = {"name": CALLERS, "internal": True}
    _fails(ob1=r, needle="as external")


def test_the_relay_publishing_a_port_fails():
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake-callrelay"]["ports"] = [{"published": "8000"}]
    _fails(ob1=r, needle="publishes a port")


def test_intake_services_without_the_profile_fail():
    r = _renders()
    r[()] = copy.deepcopy(r[("intake",)])
    _fails(ob1=r, needle="without the intake profile")


@pytest.mark.parametrize("url", ["http://openbrain-intake:8000/v1/intake", "",
                                 "http://openbrain-intake-callrelay:8080/v1/intake"])
def test_the_gateway_dialling_anything_but_the_call_relay_fails(url):
    s = _search()
    s["services"]["gateway"]["environment"]["INTAKE_URL"] = url
    _fails(search=s, needle="INTAKE_URL must dial")


def test_an_alias_on_the_call_relay_fails():
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake-callrelay"]["networks"]["intake-callers"] = {
        "aliases": ["openbrain-intake"]}
    _fails(ob1=r, needle="network alias")


def test_another_compose_file_naming_a_network_fails():
    _fails(others={"agent-org/docker/docker-compose.yml": [CALLERS]}, needle="only the ob1 and search")


def test_no_callers_net_in_search_fails():
    s = _search()
    del s["networks"]["intake-callers"]
    _fails(search=s, needle="does not declare")


@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI to render with")
def test_the_real_renders_pass(capsys):
    assert cii.main() == 0, capsys.readouterr().out


# --- round 2 (tester t-gate-relay T-F2, T-F4) ------------------------------------------------------
def test_the_call_relay_on_a_third_network_fails():
    # M10: the call relay also on obnet = a route out of intake-callers into obnet
    r = _renders()
    r[("intake",)]["services"]["openbrain-intake-callrelay"]["networks"]["obnet"] = None
    _fails(ob1=r, needle="call relay must be on exactly")


def test_the_gateway_forwarding_fails():
    s = _search()
    s["services"]["gateway"]["sysctls"] = {"net.ipv4.ip_forward": "1"}
    _fails(search=s, needle="ip_forward")
    del s["services"]["gateway"]["sysctls"]
    _fails(search=s, needle="ip_forward")


def test_a_notebook_service_on_either_network_fails():
    # M6/M7: open_notebook (profile notebook / notebook-app) on intake-net or intake-callers
    for net in ("intake-net", "intake-callers"):
        r = _renders()
        r[("intake",)]["services"]["open_notebook"] = {"networks": {net: None}}
        _fails(ob1=r, needle="members")


def test_the_profile_sets_judged_cover_every_ob1_profile_the_manifest_declares():
    import tomllib
    m = tomllib.loads((cii.REPO / "stack.manifest.toml").read_text(encoding="utf-8"))
    declared = set(m["planes"]["ob1"]["profiles"])
    judged = set().union(*map(set, cii.OB1_PROFILE_SETS))
    assert declared <= judged, sorted(declared - judged)
    assert any(set(ps) == declared for ps in cii.OB1_PROFILE_SETS), "one render must carry every profile at once"
