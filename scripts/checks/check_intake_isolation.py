#!/usr/bin/env python3
"""The intake screen's two networks keep exactly the members they are meant to have.

Work item intake-gate-relay (security-hardening-2026-10; closes DESIGN-intake section 12 item 10,
R2-F4, for the first caller). The search gate (search-gateway, search plane) hands pages to the
intake service (openbrain-intake, ob1 plane, profile `intake`) through ONE fixed-target byte
relay, openbrain-intake-callrelay. Two internal networks carry that:

    intake-net      (ob1, native)    openbrain-intake + its relays: -dbrelay, -llmrelay, -callrelay
    intake-callers  (search, native; ob1 joins it as external)   openbrain-intake-callrelay +
                                     search-gateway

The check renders both planes the shipped way (`docker compose -f <compose> --env-file
<plane>/.env.example [--profile ...] config --format json`, COMPOSE_PROFILES scrubbed from the
inherited environment, as check_lan_bindings.py does) and refuses when:

  1. intake-net's members are not exactly openbrain-intake and its three relays (in ANY plane:
     a service of another plane that names intake-net's resolved name is a member too);
  2. openbrain-intake is on more than one network, or on any network other than intake-net;
  3. intake-callers' members are not exactly openbrain-intake-callrelay and search-gateway;
  4. either network is not `internal: true`, has not exactly one subnet, the subnet is not a
     /24, or it overlaps 192.168.0.0/16 (the home LAN, incident 2026-10-17), the daemon's
     default address pools (172.16.0.0/12, 10.240.0.0/16) or the other network;
  plus: intake-callers is declared natively by the search plane (so the search plane starts on a
  host without intake) and joined as external by ob1; the call relay publishes no port and carries
  no network alias (an alias `openbrain-intake` answered its own lookup of its target, measured);
  the gateway's INTAKE_URL dials the call relay on :8000; and no OTHER compose file in the
  repository names either network.

Pure judging lives in judge(); main() renders and judges. It prints verdicts only.
Exit: 0 = all good; 1 = a refusal; 2 = a render could not be produced.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.parse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

OB1 = ("OB1/docker/docker-compose.yml", "OB1/docker/.env.example")
SEARCH = ("search/docker-compose.yml", "search/.env.example")
# ob1 renders judged: the bare plane and every profile that renders from .env.example alone, each
# with and without `intake` (the notebook profile needs the plane's real .env; check_lan_bindings).
OB1_PROFILE_SETS = [(), ("intake",), ("research", "wiki", "idea-refinery", "pantry"),
                    ("research", "wiki", "idea-refinery", "pantry", "intake")]

INTAKE = "openbrain-intake"
CALL_RELAY = "openbrain-intake-callrelay"
GATEWAY = "gateway"                      # the search plane's service key (container search-gateway)
INTAKE_NET_MEMBERS = {INTAKE, "openbrain-intake-dbrelay", "openbrain-intake-llmrelay", CALL_RELAY}
CALLERS_MEMBERS = {("ob1", CALL_RELAY), ("search", GATEWAY)}
FORBIDDEN = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "172.16.0.0/12", "10.240.0.0/16")]


def _members(render: dict, net_name: str) -> set[str]:
    """Service keys in `render` attached to the network whose RESOLVED name is net_name."""
    keys = {k for k, n in (render.get("networks") or {}).items() if n.get("name") == net_name}
    return {s for s, svc in (render.get("services") or {}).items()
            if keys & set(svc.get("networks") or {})}


def _net(render: dict, key: str) -> dict | None:
    return (render.get("networks") or {}).get(key)


def _judge_net(label: str, net: dict, problems: list[str]) -> ipaddress.IPv4Network | None:
    if net.get("internal") is not True:
        problems.append(f"{label} is not internal: true")
    subs = [c.get("subnet") for c in ((net.get("ipam") or {}).get("config") or [])]
    if len(subs) != 1 or not subs[0]:
        problems.append(f"{label} must declare exactly one subnet (found {subs})")
        return None
    try:
        sub = ipaddress.ip_network(subs[0])
    except ValueError:
        problems.append(f"{label} subnet {subs[0]!r} is not a network")
        return None
    if sub.version != 4 or sub.prefixlen != 24:
        problems.append(f"{label} subnet {sub} is not an IPv4 /24")
    for f in FORBIDDEN:
        if sub.overlaps(f):
            problems.append(f"{label} subnet {sub} overlaps {f}")
    return sub


def judge(ob1_renders: dict[tuple, dict], search: dict, other_compose_names: dict[str, list[str]]
          | None = None) -> list[str]:
    """Problems found (empty = all good). ob1_renders maps a profile tuple to its render;
    other_compose_names maps a compose file outside the two planes to the network names it uses."""
    p: list[str] = []
    s_callers = _net(search, "intake-callers")
    if s_callers is None:
        p.append("the search plane does not declare the intake-callers network")
        return p
    if s_callers.get("external"):
        p.append("intake-callers must be declared NATIVELY by the search plane (external there means "
                 "the search plane cannot start on a host without intake)")
    callers_name = s_callers.get("name")
    callers_sub = _judge_net(f"intake-callers ({callers_name})", s_callers, p)
    s_gw = (search.get("services") or {}).get(GATEWAY) or {}
    if "intake-callers" not in (s_gw.get("networks") or {}):
        p.append("search-gateway is not on intake-callers")
    url = urllib.parse.urlsplit(str((s_gw.get("environment") or {}).get("INTAKE_URL") or ""))
    if (url.hostname, url.port) != (CALL_RELAY, 8000):
        p.append(f"search-gateway's INTAKE_URL must dial {CALL_RELAY}:8000 (it dials {url.hostname}:{url.port})")

    intake_net_name = None
    for profiles, r in ob1_renders.items():
        tag = "ob1 [" + (",".join(profiles) or "bare") + "]"
        svcs = r.get("services") or {}
        on = "intake" in profiles
        if not on:
            leaked = sorted(s for s in svcs if s.startswith(INTAKE))
            if leaked:
                p.append(f"{tag}: intake services render without the intake profile: {leaked}")
            continue
        inet = _net(r, "intake-net")
        if inet is None:
            p.append(f"{tag}: no intake-net network")
            continue
        intake_net_name = inet.get("name")
        if inet.get("external"):
            p.append(f"{tag}: intake-net must be ob1's own network, not external")
        inet_sub = _judge_net(f"{tag} intake-net ({intake_net_name})", inet, p)
        if inet_sub and callers_sub and inet_sub.overlaps(callers_sub):
            p.append(f"intake-net {inet_sub} overlaps intake-callers {callers_sub}")
        members = _members(r, intake_net_name)
        if members != INTAKE_NET_MEMBERS:
            p.append(f"{tag}: intake-net members are {sorted(members)}, must be exactly "
                     f"{sorted(INTAKE_NET_MEMBERS)}")
        nets = list((svcs.get(INTAKE) or {}).get("networks") or {})
        if nets != ["intake-net"]:
            p.append(f"{tag}: openbrain-intake must have exactly one network, intake-net (has {nets})")
        o_callers = [k for k, n in (r.get("networks") or {}).items() if n.get("name") == callers_name]
        if len(o_callers) != 1:
            p.append(f"{tag}: ob1 must declare {callers_name} exactly once (found {o_callers})")
        else:
            if not r["networks"][o_callers[0]].get("external"):
                p.append(f"{tag}: ob1 must join {callers_name} as external (the search plane owns it)")
        ob1_callers = _members(r, callers_name)
        s_members = _members(search, callers_name)
        got = {("ob1", s) for s in ob1_callers} | {("search", s) for s in s_members}
        if got != CALLERS_MEMBERS:
            p.append(f"{tag}: intake-callers members are {sorted(got)}, must be exactly "
                     f"{sorted(CALLERS_MEMBERS)}")
        if _members(search, intake_net_name):
            p.append(f"{tag}: a search-plane service is on intake-net: {sorted(_members(search, intake_net_name))}")
        relay = svcs.get(CALL_RELAY) or {}
        if relay.get("ports"):
            p.append(f"{tag}: {CALL_RELAY} publishes a port: {relay['ports']}")
        aliased = sorted(n for n, v in (relay.get("networks") or {}).items() if (v or {}).get("aliases"))
        if aliased:
            p.append(f"{tag}: {CALL_RELAY} carries a network alias on {aliased} (one named after its "
                     "target answers the relay's own lookup)")
    if intake_net_name is None:
        p.append("no ob1 render with the intake profile was judged")
    for f, names in sorted((other_compose_names or {}).items()):
        for n in names:
            if n in {intake_net_name, callers_name}:
                p.append(f"{f} names {n}; only the ob1 and search planes may")
    return p


def _render(compose: str, env_example: str, profiles: tuple = ()) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
    argv = ["docker", "compose", "-f", str(REPO / compose), "--env-file", str(REPO / env_example)]
    for prof in profiles:
        argv += ["--profile", prof]
    r = subprocess.run([*argv, "config", "--format", "json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"{compose} {list(profiles)}: compose exit {r.returncode}")
    return json.loads(r.stdout)


def _other_compose_names() -> dict[str, list[str]]:
    """Every compose file outside the two planes, with the intake network names it mentions."""
    skip = {REPO / OB1[0], REPO / SEARCH[0]}
    out: dict[str, list[str]] = {}
    for f in REPO.rglob("*.yml"):
        parts = set(f.relative_to(REPO).parts)
        if f in skip or "compose" not in f.name or parts & {"node_modules", ".git", "worktrees", "archive"}:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        hits = [n for n in ("open-brain_intake-net", "search_intake-callers") if n in text]
        if hits:
            out[str(f.relative_to(REPO))] = hits
    return out


def main() -> int:
    try:
        search = _render(*SEARCH)
        ob1 = {ps: _render(*OB1, ps) for ps in OB1_PROFILE_SETS}
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        print(f"check_intake_isolation: RENDER FAILED: {e}")
        return 2
    problems = judge(ob1, search, _other_compose_names())
    for x in problems:
        print(f"check_intake_isolation: REFUSED: {x}")
    if problems:
        return 1
    print(f"check_intake_isolation: ok ({len(ob1)} ob1 renders + the search render: intake-net = "
          f"{len(INTAKE_NET_MEMBERS)} members, intake-callers = call relay + gateway, both internal /24)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
