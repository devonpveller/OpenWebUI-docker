#!/usr/bin/env python3
"""The opt-in LAN bindings stay inert until set, and bind exactly what is listed when set.

Work item edge-b2-pub (cluster-transition PLAN.md 5.1, Track B item B2). Three host
ports may be published on the host's LAN address so the Proxmox Traefik can reach them,
each turned on by ONE variable in the plane's OWN .env:

    frontend   OWUI_LAN_IP         openwebui / openwebui-stock   <ip>:3000 -> 8080
    agent-org  MATTERMOST_LAN_IP   mattermost                    <ip>:8065 -> 8065
    inference  LITELLM_UI_LAN_IP   llm-gateway-ui                <ip>:8445 -> 8080

The check renders each plane with `docker compose config --format json` under every
profile combination that matters, three times - variable unset, set to "" and set to a
LAN address - and refuses when:

  * any binding has a wildcard host (no host_ip, 0.0.0.0, ::) - nothing may listen on
    every interface, set or not;
  * a service's rendered port list holds the same binding twice. The frontend and
    agent-org entries default to the loopback binding they sit beside, so with the
    variable unset they render the SAME entry twice in the file; compose v5.3 collapses
    that to one (measured 2026-10-16, see the findings file), and a compose that stopped
    doing so would hand the engine a duplicate and fail `up` with "port is already
    allocated". This is the line that catches that;
  * the loopback bindings differ from today's (BASELINE below) - in any state;
  * the non-loopback bindings are not EXACTLY the listed ones on exactly the given
    address when set, or are not empty when unset / empty;
  * setting the variable changes anything else in the render: every service other than
    the listed one is compared whole, and the listed one with its LAN entry removed.
    That is what proves llm-gateway (the inference gateway itself) is untouched - it is
    also asserted to publish nothing.

`--live-env` additionally reads each plane's real `.env` (when present) and refuses a
LAN variable whose value is not one specific IPv4 unicast address - 0.0.0.0 above all.
It prints the verdict only, never the value.

WHY --env-file. Each render passes `--env-file <plane>/.env.example` (the committed
example, the same file check-project-configs.ps1 renders against). Passing --env-file
SUPPRESSES compose's native load of `<plane>/.env`, which on the deploy host - and in a
worktree copied from it - is the live file: without the flag a live LAN value (or any
other live value) would colour the answer. The variable under test, and
COMPOSE_PROFILES, are passed through the process environment, which compose ranks above
any env file; both are scrubbed from the inherited environment first.

THE PORT LIST BELOW MUST MATCH B1's single source of truth for LAN ingress (work item
edge-b1-fw, the firewall guard). The two are reconciled when both have merged.

Exit: 0 = all good; 1 = a refusal; 2 = a render could not be produced.
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LAN_TEST_IP = "192.0.2.10"
WILDCARDS = {"", "0.0.0.0", "::", "[::]"}

# plane -> its variable and the (service, published, target) it binds on the LAN address.
# MUST MATCH B1's LAN-ingress table (edge-b1-fw); reconciled at merge.
LAN = {
    "frontend": {
        "var": "OWUI_LAN_IP",
        "bindings": {("openwebui", "3000", "8080"), ("openwebui-stock", "3000", "8080")},
    },
    "agent-org": {
        "var": "MATTERMOST_LAN_IP",
        "bindings": {("mattermost", "8065", "8065")},
    },
    "inference": {
        "var": "LITELLM_UI_LAN_IP",
        "bindings": {("llm-gateway-ui", "8445", "8080")},
    },
}
ALL_VARS = sorted(spec["var"] for spec in LAN.values())

COMPOSE = {
    "frontend": ("frontend/docker-compose.yml", "frontend/.env.example"),
    "agent-org": ("agent-org/docker/docker-compose.yml", "agent-org/docker/.env.example"),
    "inference": ("inference/docker-compose.yml", "inference/.env.example"),
}

_FRONT_STOCK = {("openwebui-stock", "127.0.0.1", "3000", "8080")}
_FRONT_GPU = {("openwebui", "127.0.0.1", "3000", "8080")}
_AO = {("agent-bridge", "127.0.0.1", "8830", "8000"), ("mattermost", "127.0.0.1", "8065", "8065")}
_INF_LOCAL = {("llama-cpp-upstream", "127.0.0.1", "8081", "8080"),
              ("llama-cpp-embed-upstream", "127.0.0.1", "8082", "8080")}

# (plane, profiles, today's loopback bindings). Measured on development e5b501cf with
# compose v5.3.0, 2026-10-16. The frontend's stock and gpu definitions share a
# container_name and cannot render together, so each is its own row.
RENDERS = [
    ("frontend", (), set()),
    ("frontend", ("stock",), _FRONT_STOCK),
    ("frontend", ("gpu",), _FRONT_GPU),
    ("frontend", ("gpu", "tailscale"), _FRONT_GPU),
    ("agent-org", (), _AO),
    ("agent-org", ("workers",), _AO),
    ("agent-org", ("cloud",), _AO),
    ("agent-org", ("workers", "cloud"), _AO),
    ("inference", (), set()),
    ("inference", ("local",), _INF_LOCAL),
]

LOOPBACK = "127.0.0.1"


def _binding(service: str, port: dict) -> tuple:
    return (service, str(port.get("host_ip") or ""), str(port.get("published") or ""),
            str(port.get("target") or ""))


def render(plane: str, profiles, lan_value) -> dict:
    """`docker compose config --format json` for one plane; lan_value None = unset."""
    compose, example = COMPOSE[plane]
    env = {k: v for k, v in os.environ.items() if k not in ALL_VARS and k != "COMPOSE_PROFILES"}
    env["COMPOSE_PROFILES"] = ",".join(profiles)
    if lan_value is not None:
        env[LAN[plane]["var"]] = lan_value
    cmd = ["docker", "compose", "-f", compose, "--env-file", example]
    for profile in profiles:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exited {proc.returncode}: "
                           + (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1])
    return json.loads(proc.stdout)


def check_render(plane: str, label: str, data: dict, baseline: set, lan_ip) -> list:
    """Refusals for ONE render. lan_ip None/'' = the variable is off."""
    problems = []
    seen_lan = set()
    loopback = set()
    for name, spec in (data.get("services") or {}).items():
        raw = [p for p in (spec or {}).get("ports") or [] if p.get("published")]
        binds = [_binding(name, p) for p in raw]
        dupes = sorted({b for b in binds if binds.count(b) > 1})
        for b in dupes:
            problems.append(f"{label}: DUPLICATE binding {b[1]}:{b[2]}->{b[3]} on {name} "
                            "(compose no longer collapses it; `up` would fail 'port is already allocated')")
        for b in binds:
            if b[1] in WILDCARDS:
                problems.append(f"{label}: WILDCARD binding {b[1] or '<all>'}:{b[2]}->{b[3]} on {name}")
            elif b[1] == LOOPBACK:
                loopback.add(b)
            else:
                seen_lan.add(b)
    if loopback != baseline:
        problems.append(f"{label}: loopback bindings changed: added {sorted(loopback - baseline)} "
                        f"removed {sorted(baseline - loopback)}")
    present = set((data.get("services") or {}).keys())
    want = set()
    if lan_ip:
        want = {(svc, lan_ip, pub, tgt) for (svc, pub, tgt) in LAN[plane]["bindings"] if svc in present}
    if seen_lan != want:
        problems.append(f"{label}: non-loopback bindings are {sorted(seen_lan)}, expected {sorted(want)}")
    if plane == "inference" and "llm-gateway" in present:
        if (data["services"]["llm-gateway"] or {}).get("ports"):
            problems.append(f"{label}: llm-gateway publishes a port - it must never have one")
    return problems


def strip_lan(data: dict, plane: str, lan_ip: str) -> dict:
    """The render with the listed LAN entries removed (to compare against the unset render)."""
    out = copy.deepcopy(data)
    listed = {svc for (svc, _p, _t) in LAN[plane]["bindings"]}
    for name, spec in (out.get("services") or {}).items():
        if name not in listed or not spec:
            continue
        kept = [p for p in spec.get("ports") or [] if str(p.get("host_ip") or "") != lan_ip]
        if kept:
            spec["ports"] = kept
        else:
            spec.pop("ports", None)
    return out


def compare_unset_set(label: str, unset: dict, setd: dict, plane: str, lan_ip: str) -> list:
    problems = []
    a = (unset.get("services") or {})
    b = (strip_lan(setd, plane, lan_ip).get("services") or {})
    for name in sorted(set(a) | set(b)):
        if json.dumps(a.get(name), sort_keys=True) != json.dumps(b.get(name), sort_keys=True):
            problems.append(f"{label}: setting {LAN[plane]['var']} changed service {name} beyond its LAN port")
    for key in sorted(set(unset) | set(setd)):
        if key == "services":
            continue
        if json.dumps(unset.get(key), sort_keys=True) != json.dumps(setd.get(key), sort_keys=True):
            problems.append(f"{label}: setting {LAN[plane]['var']} changed top-level `{key}`")
    return problems


def validate_lan_ip(value: str):
    """None when acceptable, else the reason. Only one specific IPv4 unicast address."""
    try:
        ip = ipaddress.IPv4Address(value.strip())
    except ValueError:
        return "not an IPv4 address (a hostname, IPv6, or a typo)"
    if ip.is_unspecified:
        return "0.0.0.0 binds EVERY interface - name the one LAN address"
    if ip.is_multicast or ip.is_reserved or str(ip) == "255.255.255.255":
        return "not a unicast address"
    if ip.is_loopback:
        return "a loopback address - leave the variable unset instead"
    return None


def read_env_value(path: Path, key: str):
    """The last assignment of key in an env file, or None. The value is never printed."""
    value = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        if k == key:
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            value = v
    return value


def live_env_problems() -> list:
    problems = []
    for plane, (compose, _example) in COMPOSE.items():
        var = LAN[plane]["var"]
        env_path = REPO / Path(compose).parent / ".env"
        if not env_path.is_file():
            print(f"  live-env {plane}: NOT VERIFIED ({env_path.relative_to(REPO).as_posix()} absent)")
            continue
        value = read_env_value(env_path, var)
        if not value:
            print(f"  live-env {plane}: {var} unset - inert")
            continue
        why = validate_lan_ip(value)
        if why:
            problems.append(f"live-env {plane}: {var} is set to a value that is refused: {why}")
        else:
            print(f"  live-env {plane}: {var} set to a specific IPv4 unicast address - ok")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live-env", action="store_true",
                    help="also validate the LAN variables in each plane's real .env (verdict only)")
    ap.add_argument("--ip", default=LAN_TEST_IP, help="the LAN address the `set` renders use")
    args = ap.parse_args(argv)

    problems = []
    rendered = 0
    lan_seen = {plane: set() for plane in LAN}
    for plane, profiles, baseline in RENDERS:
        label = f"{plane} [{','.join(profiles) or 'no profile'}]"
        try:
            unset = render(plane, profiles, None)
            empty = render(plane, profiles, "")
            setd = render(plane, profiles, args.ip)
        except (RuntimeError, ValueError) as exc:
            print(f"RENDER FAILED {label}: {exc}")
            return 2
        rendered += 3
        problems += check_render(plane, label + " unset", unset, baseline, None)
        problems += check_render(plane, label + " empty", empty, baseline, "")
        problems += check_render(plane, label + " set", setd, baseline, args.ip)
        problems += compare_unset_set(label, unset, setd, plane, args.ip)
        if json.dumps(unset, sort_keys=True) != json.dumps(empty, sort_keys=True):
            problems.append(f"{label}: {LAN[plane]['var']}='' renders differently from unset")
        lan_seen[plane] |= {svc for (svc, _p, _t) in LAN[plane]["bindings"]
                            if svc in (setd.get("services") or {})}
        lan = sorted(f"{b[1]}:{b[2]}->{b[3]} ({b[0]})" for name, spec in (setd.get("services") or {}).items()
                     for b in (_binding(name, p) for p in (spec or {}).get("ports") or [] if p.get("published"))
                     if b[1] != LOOPBACK)
        print(f"  {label}: loopback {len(baseline)} unchanged; LAN when set: {', '.join(lan) or 'none'}")
    for plane, spec in LAN.items():
        missing = {svc for (svc, _p, _t) in spec["bindings"]} - lan_seen[plane]
        if missing:
            problems.append(f"{plane}: listed service(s) {sorted(missing)} appear in no render - renamed?")
    if args.live_env:
        problems += live_env_problems()
    for p in problems:
        print("LAN BINDING REFUSED: " + p)
    if problems:
        print(f"FAIL: {len(problems)} refusal(s) across {rendered} renders")
        return 1
    print(f"OK: {rendered} renders - unset/empty inert, set = exactly the listed LAN ports on {args.ip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
