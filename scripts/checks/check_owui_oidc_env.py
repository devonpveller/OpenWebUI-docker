#!/usr/bin/env python3
"""The Open WebUI OIDC provider settings reach both openwebui definitions, inert when unset.

Work item edge-b3-oidc (cluster-transition PLAN.md 5.1). Open WebUI 0.11.0 registers its
OIDC login provider once at startup, only from the environment variables below; values
saved in the admin UI are not used for that. frontend/docker-compose.yml passes them
through from frontend/.env under the same names.

Renders `docker compose config --format json` for frontend under the stock and the gpu
profile, with the variables unset and set to dummy values, and refuses when:

  * a variable is absent from the openwebui service's environment;
  * unset does not render OWUI's own default, or set does not render the given value;
  * setting the variables changes anything else in the render (services and top-level
    keys compared whole, with these environment entries removed).

Like check_oauth_persistent.py, each render passes `--env-file frontend/.env.example`,
which suppresses compose's native load of the live frontend/.env (it holds a real client
secret). Render-only; never prints the live .env.

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
# name -> (rendered when unset, dummy value used for the "set" render)
KEYS = {
    "OAUTH_CLIENT_ID": ("", "dummy-client-id"),
    "OAUTH_CLIENT_SECRET": ("", "dummy-client-secret"),
    "OPENID_PROVIDER_URL": ("", "https://idp.example.invalid/.well-known/openid-configuration"),
    "OAUTH_PROVIDER_NAME": ("SSO", "Authentik"),
    "OAUTH_SCOPES": ("openid email profile", "openid email profile roles"),
}
PROFILES = [("stock", "openwebui-stock"), ("gpu", "openwebui")]


def render(profile: str, set_values: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in KEYS and k != "COMPOSE_PROFILES"}
    env["COMPOSE_PROFILES"] = profile
    if set_values:
        env.update({k: dummy for k, (_unset, dummy) in KEYS.items()})
    cmd = ["docker", "compose", "-f", COMPOSE, "--env-file", EXAMPLE, "--profile", profile,
           "config", "--format", "json"]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exited {proc.returncode}: "
                           + (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1])
    return json.loads(proc.stdout)


def without_keys(data: dict, service: str) -> dict:
    out = copy.deepcopy(data)
    env = ((out.get("services") or {}).get(service) or {}).get("environment")
    if isinstance(env, dict):
        for k in KEYS:
            env.pop(k, None)
    return out


def main() -> int:
    problems = []
    renders = 0
    for profile, service in PROFILES:
        try:
            unset, setv = render(profile, False), render(profile, True)
        except (RuntimeError, ValueError) as exc:
            print(f"RENDER FAILED frontend [{profile}]: {exc}")
            return 2
        renders += 2
        for label, data, idx in (("unset", unset, 0), ("set", setv, 1)):
            env = (((data.get("services") or {}).get(service)) or {}).get("environment") or {}
            for k, pair in KEYS.items():
                if k not in env:
                    problems.append(f"[{profile}] {label}: {k} missing from {service}")
                elif env[k] != pair[idx]:
                    problems.append(f"[{profile}] {label}: {k} renders {env[k]!r}, expected {pair[idx]!r}")
        if json.dumps(without_keys(unset, service), sort_keys=True) != \
                json.dumps(without_keys(setv, service), sort_keys=True):
            problems.append(f"[{profile}] setting the OIDC variables changed something else")
        print(f"  frontend [{profile}] {service}: checked unset + set")
    for p in problems:
        print("OIDC ENV REFUSED: " + p)
    if problems:
        print(f"FAIL: {len(problems)} refusal(s) across {renders} renders")
        return 1
    print(f"OK: {renders} renders - the five OIDC settings pass through, OWUI defaults when unset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
