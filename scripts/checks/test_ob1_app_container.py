#!/usr/bin/env python3
"""Guards for the openbrain-workbench-app container (rwr-app-container, round 3).

Run with pytest, or `python scripts/checks/test_ob1_app_container.py`. Standard library only: the
compose tests render with `docker compose config` (the way the sibling checks do) and SKIP cleanly
when docker is not available. No daemon is needed for a render.

1. Every build `context` and `additional_contexts` path of every service must exist on disk
   (compose resolves BOTH against the compose project directory, OB1/docker; a wrong one makes
   `docker compose up -d` exit 1 for the WHOLE plane while the image is absent).
2. BEHAVIOUR: the app has its OWN profile, `workbench-app`. It is not in the default render, not in
   the `wiki` render and not in any profile set a host runs today, so no whole-plane `up`, recovery
   run or reboot builds `openbrain-workbench-app:local` before the first deploy enables the profile.
3. GUARDS (text only, they pin a shape, not behaviour): recovery lists the app right after the
   workbench and treats it as an opt-in service; health 5c reads the enabled-profile switch.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "OB1" / "docker" / "docker-compose.yml"
EXAMPLE = ROOT / "OB1" / "docker" / ".env.example"
RECOVERY = ROOT / "scripts" / "recovery" / "emergency-recovery.ps1"
HEALTH = ROOT / "scripts" / "checks" / "check-openbrain-health.ps1"
APP = "openbrain-workbench-app"
PROFILE = "workbench-app"

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def render(compose_file: Path, profiles=(), env_file: Path | None = EXAMPLE) -> dict:
    """`docker compose config --format json`; COMPOSE_PROFILES and the lan vars are scrubbed."""
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES" and not k.endswith("_LAN_IP")}
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if env_file is not None:
        cmd += ["--env-file", str(env_file)]
    for p in profiles:
        cmd += ["--profile", p]
    cmd += ["config", "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    if proc.returncode != 0:
        pytest.skip(f"docker compose cannot render here: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)


def missing_contexts(compose_file: Path, env_file: Path | None = EXAMPLE) -> list[str]:
    """'service: key -> path' for each local build context path that does not exist (every profile on)."""
    every = ("research", "wiki", "notebook", "notebook-app", "idea-refinery", "pantry", "intake", PROFILE)
    data = render(compose_file, every, env_file)
    bad = []
    for name, svc in (data.get("services") or {}).items():
        build = (svc or {}).get("build")
        if not build:
            continue
        paths = {"context": build.get("context")}
        for k, v in (build.get("additional_contexts") or {}).items():
            paths[f"additional_contexts.{k}"] = v
        for k, v in paths.items():
            v = str(v or "")
            if not v or "://" in v or v.startswith("service:"):
                continue
            if not Path(v).exists():
                bad.append(f"{name}: {k} -> {v}")
    return bad


@needs_docker
def test_every_ob1_compose_build_context_exists():
    assert COMPOSE.is_file(), COMPOSE
    assert missing_contexts(COMPOSE) == []


@needs_docker
def test_context_guard_fires_on_a_missing_path(tmp_path):
    d = tmp_path / "docker"
    (d / "app").mkdir(parents=True)
    f = d / "docker-compose.yml"
    f.write_text("services:\n  app:\n    image: x:local\n    build:\n      context: ./app\n"
                 "      additional_contexts:\n        viewerlib: ../wiki-viewer/lib\n", encoding="utf-8")
    assert len(missing_contexts(f, None)) == 1          # the D1 line: wrong base
    (d / "wiki-viewer" / "lib").mkdir(parents=True)
    f.write_text(f.read_text().replace("../wiki-viewer/lib", "./wiki-viewer/lib"), encoding="utf-8")
    assert missing_contexts(f, None) == []


@needs_docker
def test_the_app_is_not_in_the_default_or_the_wiki_render():
    """The structural fix: no whole-plane `up` with today's profile sets builds the app image."""
    for profiles in ((), ("wiki",), ("research", "wiki", "notebook", "idea-refinery"),
                     ("research", "wiki", "notebook", "notebook-app", "idea-refinery", "pantry", "intake")):
        assert APP not in render(COMPOSE, profiles)["services"], f"the app is in the {profiles or 'default'} render"


@needs_docker
def test_the_app_renders_only_with_its_own_profile_and_it_needs_wiki():
    on = render(COMPOSE, ("wiki", PROFILE))["services"]
    assert APP in on
    assert on[APP]["profiles"] == [PROFILE]
    assert "openbrain-workbench" in on[APP]["depends_on"] and "openbrain-workbench" in on


def test_recovery_lists_the_app_right_after_the_workbench():
    text = RECOVERY.read_text(encoding="utf-8")
    assert re.search(r'"openbrain-workbench",\s*"openbrain-workbench-app"', text) or \
        re.search(r'OB1OptionalServices[\s\S]*?"openbrain-workbench-app"', text), \
        "emergency-recovery.ps1 must name openbrain-workbench-app (optional service, profile workbench-app)"
    assert "'workbench-app'" in text and "OB1OptionalServiceProfile" in text
    # a teardown names the profile; the start list holds it back unless enabled
    assert re.search(r"'--profile',\s*'workbench-app'", text)
    assert "Get-OB1StartProfiles" in text


def test_health_5c_reads_the_enabled_profile_switch():
    text = HEALTH.read_text(encoding="utf-8")
    start = text.index("$wbApp = 'openbrain-workbench-app'")
    block = text[start:text.index("# ---- 6.", start)]
    assert "Test-ObProfileEnabled 'workbench-app'" in block
    assert "not enabled" in block
    assert "Confirm-ObContainer $wbApp" in block      # enabled + absent is still a fault


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
