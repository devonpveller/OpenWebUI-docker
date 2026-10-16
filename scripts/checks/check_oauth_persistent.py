#!/usr/bin/env python3
"""ENABLE_OAUTH_PERSISTENT_CONFIG is exposed on both openwebui definitions, off by default.

Work item edge-b3-oauth (cluster-transition PLAN.md 5.1, Track B item B3). Open WebUI
0.11.0 keeps UI-entered `oauth.*` settings only when ENABLE_OAUTH_PERSISTENT_CONFIG=true.
frontend/docker-compose.yml passes it through from OWUI_OAUTH_PERSISTENT_CONFIG.

Renders `docker compose config --format json` for frontend under the stock and the gpu
profile, with the variable unset, empty, "true" and "false", and refuses when:

  * ENABLE_OAUTH_PERSISTENT_CONFIG is absent from the openwebui service's environment;
  * unset / empty does not render "false", "true" does not render "true";
  * setting the variable changes anything else in the render (the rendered services and
    top-level keys are compared whole, with that one environment entry removed).

Like check_lan_bindings.py, each render passes `--env-file frontend/.env.example`, which
suppresses compose's native load of the live frontend/.env. Render-only.

Exit: 0 = all good; 1 = a refusal; 2 = a render could not be produced.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = "frontend/docker-compose.yml"
EXAMPLE = "frontend/.env.example"
VAR = "OWUI_OAUTH_PERSISTENT_CONFIG"
KEY = "ENABLE_OAUTH_PERSISTENT_CONFIG"
# (profile, openwebui service name). The two definitions share a container_name and
# cannot render together, so each is its own render.
PROFILES = [("stock", "openwebui-stock"), ("gpu", "openwebui")]


def render(profile: str, value) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in (VAR, "COMPOSE_PROFILES")}
    env["COMPOSE_PROFILES"] = profile
    if value is not None:
        env[VAR] = value
    cmd = ["docker", "compose", "-f", COMPOSE, "--env-file", EXAMPLE, "--profile", profile,
           "config", "--format", "json"]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exited {proc.returncode}: "
                           + (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1])
    return json.loads(proc.stdout)


def without_key(data: dict, service: str) -> dict:
    out = copy.deepcopy(data)
    env = ((out.get("services") or {}).get(service) or {}).get("environment")
    if isinstance(env, dict):
        env.pop(KEY, None)
    return out


def main() -> int:
    problems = []
    renders = 0
    for profile, service in PROFILES:
        try:
            renders_by = {v: render(profile, v) for v in (None, "", "true", "false")}
        except (RuntimeError, ValueError) as exc:
            print(f"RENDER FAILED frontend [{profile}]: {exc}")
            return 2
        renders += len(renders_by)
        got = {}
        for v, data in renders_by.items():
            svc = (data.get("services") or {}).get(service)
            if svc is None:
                problems.append(f"[{profile}] service {service} missing from the render")
                continue
            env = svc.get("environment") or {}
            got[v] = env.get(KEY)
        want = {None: "false", "": "false", "false": "false", "true": "true"}
        for v, w in want.items():
            if v in got and got[v] != w:
                problems.append(f"[{profile}] {VAR}={v!r}: {service} renders {KEY}={got[v]!r}, expected {w!r}")
        base = json.dumps(without_key(renders_by[None], service), sort_keys=True)
        for v in ("", "true", "false"):
            if json.dumps(without_key(renders_by[v], service), sort_keys=True) != base:
                problems.append(f"[{profile}] {VAR}={v!r} changed something besides {KEY}")
        print(f"  frontend [{profile}] {service}: unset={got.get(None)} empty={got.get('')} "
              f"true={got.get('true')} false={got.get('false')}")
    for p in problems:
        print("OAUTH PERSISTENT REFUSED: " + p)
    if problems:
        print(f"FAIL: {len(problems)} refusal(s) across {renders} renders")
        return 1
    print(f"OK: {renders} renders - unset/empty/false = false, true = true, nothing else moves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
