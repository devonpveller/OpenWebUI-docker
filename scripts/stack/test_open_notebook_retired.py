"""The open_notebook app stays stopped when it is retired (intake-gate-relay round 2, D-1).

Operator decision 2026-10-31: the app is stopped before intake writes bronze (DESIGN-intake section
12 item 1). THE SWITCH is the ob1 compose profile `notebook-app` - open_notebook alone; surrealdb and
open-notebook-backup stay in `notebook`. ON = in .stack/state.json (planes.ob1.profiles) or in
COMPOSE_PROFILES of OB1/docker/.env, the two sources the driver unions. Four starters were measured
to bring a stopped app back (tester t-gate-relay); this file covers compose (the restart policy and
the profile) and `stack.py up`; scripts/checks/test-open-notebook-retired.ps1 covers the watchdog and
emergency recovery (run here too when PowerShell is on PATH); the health probe is in test_stack.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import stack  # noqa: E402

THIS_HOST = ["idea-refinery", "research", "wiki", "notebook"]   # the live OB1 set, without the app
needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI to render with")


def render(profiles, tmp_path: Path) -> dict:
    files = ["-f", str(REPO / "OB1/docker/docker-compose.yml")]
    if not (REPO / "OB1/docker/.env").exists():   # open_notebook's env_file ./.env is required
        reset = tmp_path / "reset.yml"
        reset.write_text("services:\n  open_notebook:\n    env_file: !reset []\n", encoding="utf-8")
        files += ["-f", str(reset)]
    argv = ["docker", "compose", *files, "--env-file", str(REPO / "OB1/docker/.env.example")]
    for p in profiles:
        argv += ["--profile", p]
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
    r = subprocess.run([*argv, "config", "--format", "json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=180)
    assert r.returncode == 0, r.stderr[-400:]
    return json.loads(r.stdout)


@needs_docker
def test_compose_this_hosts_profile_set_does_not_render_the_app(tmp_path):
    svcs = render(THIS_HOST, tmp_path)["services"]
    assert "open_notebook" not in svcs, "the `notebook` profile still starts the app"
    assert {"surrealdb", "open-notebook-backup"} <= set(svcs), "the data side must keep running"


@needs_docker
def test_compose_the_app_renders_only_under_notebook_app(tmp_path):
    svcs = render(THIS_HOST + ["notebook-app"], tmp_path)["services"]
    assert "open_notebook" in svcs
    assert svcs["open_notebook"].get("profiles") == ["notebook-app"]


@needs_docker
def test_compose_restart_policy_holds_a_deliberate_stop(tmp_path):
    # `always` restarts a stopped container when the daemon restarts (Docker Desktop: every Saturday
    # 03:15); `unless-stopped` keeps a `compose stop` stopped.
    svc = render(THIS_HOST + ["notebook-app"], tmp_path)["services"]["open_notebook"]
    assert svc.get("restart") == "unless-stopped", svc.get("restart")


@needs_docker
def test_stack_py_up_does_not_start_the_app_when_retired(tmp_path):
    manifest = stack.Manifest.load(REPO / stack.MANIFEST_NAME)
    state = stack.State.load(tmp_path / "state.json")
    stack.enable_plane_profiles(manifest, state, "ob1", THIS_HOST)
    # root = tmp_path: no OB1/docker/.env there, so only the state file speaks (as on a host whose
    # env file carries no COMPOSE_PROFILES)
    flags = stack.effective_profiles(manifest, state, tmp_path, "ob1")
    assert "notebook" in flags
    assert "open_notebook" not in render(flags, tmp_path)["services"]


@needs_docker
def test_stack_py_up_starts_it_once_the_switch_is_on(tmp_path):
    manifest = stack.Manifest.load(REPO / stack.MANIFEST_NAME)
    state = stack.State.load(tmp_path / "state.json")
    stack.enable_plane_profiles(manifest, state, "ob1", THIS_HOST + ["notebook-app"])
    flags = stack.effective_profiles(manifest, state, tmp_path, "ob1")
    assert "open_notebook" in render(flags, tmp_path)["services"]


@pytest.mark.skipif(shutil.which("powershell") is None or shutil.which("docker") is None,
                    reason="needs powershell and the docker CLI")
def test_the_watchdog_and_recovery_do_not_start_a_retired_app():
    r = subprocess.run(["powershell", "-NoProfile", "-File", str(REPO / "scripts/checks/test-open-notebook-retired.ps1")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-500:]
