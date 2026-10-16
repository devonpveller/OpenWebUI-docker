#!/usr/bin/env python3
"""The LiteLLM Admin UI's SSO settings reach llm-gateway-ui, and are ABSENT when unset.

Work item edge-litellm-sso (cluster-transition PLAN.md 5.1). LiteLLM reads its generic SSO
settings from the environment and tests them with `is not None`
(litellm/proxy/management_endpoints/ui_sso.py), so an EMPTY value turns SSO on and an empty
GENERIC_ROLE_MAPPINGS_ROLES raises. llm-gateway-ui therefore lists them by name only
(`- GENERIC_CLIENT_ID`), which compose fills from inference/.env when set and leaves out of
the container when unset.

Renders `docker compose -f inference/docker-compose.yml config --format json` from a scratch
copy of the inference plane whose .env compose loads natively (the driver's path), under the
profiles none and local, and refuses when:

  * unset: any of the keys renders as a string (an empty string included) on llm-gateway-ui;
  * set: any key is missing or does not render the scratch value;
  * llm-gateway (the inference gateway) gains or loses anything;
  * llm-gateway-ui changes anything besides these keys.

Render-only; never reads or prints the live inference/.env.
Exit: 0 = all good; 1 = a refusal; 2 = a render could not be produced.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEYS = {
    "GENERIC_CLIENT_ID": "dummy-client",
    "GENERIC_CLIENT_SECRET": "dummy-secret",
    "GENERIC_AUTHORIZATION_ENDPOINT": "https://idp.example.invalid/application/o/authorize/",
    "GENERIC_TOKEN_ENDPOINT": "https://idp.example.invalid/application/o/token/",
    "GENERIC_USERINFO_ENDPOINT": "https://idp.example.invalid/application/o/userinfo/",
    "GENERIC_SCOPE": "openid email profile roles",
    "GENERIC_ROLE_MAPPINGS_ROLES": "{'proxy_admin': ['admin'], 'internal_user': ['user']}",
    "GENERIC_ROLE_MAPPINGS_GROUP_CLAIM": "roles",
    "GENERIC_ROLE_MAPPINGS_DEFAULT_ROLE": "internal_user_viewer",
    "PROXY_BASE_URL": "https://lite.example.invalid",
}
PROFILES = ["", "local"]


def make_plane() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="litellm-sso-"))
    shutil.copytree(REPO / "inference", tmp / "inference",
                    ignore=shutil.ignore_patterns(".env", "*.log", "__pycache__"))
    return tmp


def write_env(tmp: Path, extra: dict | None) -> None:
    lines = (REPO / "inference" / ".env.example").read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in lines if ln.split("=", 1)[0].strip() not in KEYS]
    for k, v in (extra or {}).items():
        lines.append(f'{k}="{v}"' if any(c in v for c in " {}'") else f"{k}={v}")
    (tmp / "inference" / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(tmp: Path, profile: str, extra: dict | None) -> dict:
    """Both renders of a pair use the SAME scratch dir, so bind-mount paths compare equal."""
    write_env(tmp, extra)
    env = {k: v for k, v in os.environ.items() if k not in KEYS and k != "COMPOSE_PROFILES"}
    cmd = ["docker", "compose", "-f", "inference/docker-compose.yml"]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    proc = subprocess.run(cmd, cwd=str(tmp), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"render [{profile or 'none'}] exited {proc.returncode}: "
                           + (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1])
    return json.loads(proc.stdout)


def strip(svc: dict) -> dict:
    out = copy.deepcopy(svc)
    env = out.get("environment")
    if isinstance(env, dict):
        for k in KEYS:
            env.pop(k, None)
    return out


def main() -> int:
    problems = []
    for profile in PROFILES:
        label = profile or "none"
        tmp = make_plane()
        try:
            unset, setv = render(tmp, profile, None), render(tmp, profile, KEYS)
        except (RuntimeError, ValueError) as exc:
            print(f"RENDER FAILED: {exc}")
            return 2
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ui_u = unset["services"].get("llm-gateway-ui")
        ui_s = setv["services"].get("llm-gateway-ui")
        if ui_u is None or ui_s is None:
            problems.append(f"[{label}] llm-gateway-ui missing from the render")
            continue
        env_u, env_s = ui_u.get("environment") or {}, ui_s.get("environment") or {}
        for k, want in KEYS.items():
            if isinstance(env_u.get(k), str):
                problems.append(f"[{label}] unset: {k} renders as a string {env_u[k]!r} (must be absent)")
            if env_s.get(k) != want:
                problems.append(f"[{label}] set: {k} renders {env_s.get(k)!r}, expected the scratch value")
        if json.dumps(unset["services"].get("llm-gateway"), sort_keys=True) != \
                json.dumps(setv["services"].get("llm-gateway"), sort_keys=True):
            problems.append(f"[{label}] llm-gateway changed when the SSO variables were set")
        if json.dumps(strip(ui_u), sort_keys=True) != json.dumps(strip(ui_s), sort_keys=True):
            problems.append(f"[{label}] llm-gateway-ui changed beyond the SSO keys")
        print(f"  inference [{label}] llm-gateway-ui: checked unset + set")
    for p in problems:
        print("LITELLM SSO REFUSED: " + p)
    if problems:
        print(f"FAIL: {len(problems)} refusal(s)")
        return 1
    print("OK: 4 renders - SSO keys absent when unset, passed through when set; llm-gateway untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
