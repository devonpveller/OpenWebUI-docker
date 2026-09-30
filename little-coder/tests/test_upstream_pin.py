"""The upstream little-coder CLI pin and the wrapper's adaptations to it (cf-lc-upgrade, 1.20.0).

Static checks over the committed image/config files - no Docker, no network:
  * one version everywhere the pin is named (Dockerfile ARG default, compose build-arg default,
    coder/.env.example), so a bump cannot land half-done;
  * every upstream tool that executes or egresses from the CONTROL-plane container is denied twice
    (the entrypoint `rm` of its extension dir AND the config `--exclude-tools` list) - 1.16.0 added
    `bg-shell` (ShellStart/ShellLog/ShellList/ShellSend/ShellStop), a second in-container shell;
  * the pi runtime patches are applied at BUILD time, as root, after the npm install - since 1.12.0
    upstream has no postinstall and the launcher's launch-time patch cannot write the root-owned
    global node_modules as the `lc` user (it fails silently; the 1.20.0 multi-line `edit` repair
    would never land).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

LC = Path(__file__).resolve().parent.parent
REPO = LC.parent
DOCKERFILE = LC / "docker" / "Dockerfile.agent"
ENTRYPOINT = LC / "docker" / "entrypoint-agent.sh"
PATCHER = LC / "docker" / "apply-pi-patches.mjs"
CONFIG = LC / "config" / "little-coder.config.yaml"
COMPOSE = REPO / "coder" / "docker-compose.yml"
ENV_EXAMPLE = REPO / "coder" / ".env.example"

# Upstream extension dir -> the tool names it registers (read off the 1.20.0 package).
CONTROL_PLANE_ESCAPES = {
    "shell-session": ["ShellSession", "ShellSessionCwd", "ShellSessionReset"],
    "bg-shell": ["ShellStart", "ShellLog", "ShellList", "ShellSend", "ShellStop"],
    "browser": ["BrowserNavigate", "BrowserClick", "BrowserExtract", "BrowserBack",
                "BrowserHistory", "BrowserScroll", "BrowserType"],
}
EGRESS_TOOLS = ["webfetch", "websearch"]


def _dockerfile_pin() -> str:
    m = re.search(r"^ARG LITTLE_CODER_VERSION=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert m, "Dockerfile.agent lost its LITTLE_CODER_VERSION ARG"
    return m.group(1)


def test_pin_is_a_concrete_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _dockerfile_pin()), "pin a concrete version, never `latest`"


def test_compose_build_arg_default_matches_pin():
    if not COMPOSE.exists():
        pytest.skip("coder/ not in this checkout (little-coder copied alone)")
    m = re.search(r"LITTLE_CODER_VERSION: \$\{LITTLE_CODER_VERSION:-([^}]+)\}", COMPOSE.read_text(encoding="utf-8"))
    assert m, "coder/docker-compose.yml lost the LITTLE_CODER_VERSION build arg"
    assert m.group(1) == _dockerfile_pin()


def test_env_example_matches_pin():
    if not ENV_EXAMPLE.exists():
        pytest.skip("coder/ not in this checkout (little-coder copied alone)")
    m = re.search(r"^LITTLE_CODER_VERSION=(\S+)$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    assert m and m.group(1) == _dockerfile_pin()


def _exclude_tools() -> set[str]:
    args = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["agent"]["extra_args"]
    i = args.index("--exclude-tools")
    return {t.strip() for t in args[i + 1].split(",") if t.strip()}


@pytest.mark.parametrize("ext", sorted(CONTROL_PLANE_ESCAPES))
def test_every_control_plane_escape_tool_is_excluded(ext):
    missing = [t for t in CONTROL_PLANE_ESCAPES[ext] if t not in _exclude_tools()]
    assert not missing, f"{ext}: {missing} not in --exclude-tools"


def test_egress_tools_are_excluded():
    assert set(EGRESS_TOOLS) <= _exclude_tools()


def test_bash_and_edit_tools_stay_available():
    # bash is OUR open-terminal-routed override; edit/write are the daemon's plan-only lever.
    assert not ({"bash", "edit", "write", "read"} & _exclude_tools())


def test_entrypoint_removes_every_escape_extension():
    m = re.search(r"^\s*for ext in ([^;]+); do", ENTRYPOINT.read_text(encoding="utf-8"), re.M)
    assert m, "entrypoint lost its extension-removal loop"
    removed = set(m.group(1).split())
    assert set(CONTROL_PLANE_ESCAPES) | {"browser-extract-retention"} <= removed


def test_pi_patches_applied_at_build_after_install():
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    install = next(i for i, ln in enumerate(lines) if "npm install -g" in ln and "little-coder@" in ln)
    apply = next((i for i, ln in enumerate(lines) if ln.startswith("RUN node /tmp/apply-pi-patches.mjs")), None)
    assert apply is not None and apply > install, "pi patches must be applied after the npm install"
    user_switch = [i for i, ln in enumerate(lines) if ln.startswith("USER ")]
    assert not user_switch or min(user_switch) > apply, "the patch step must run as root"


def test_build_patcher_fails_closed():
    src = PATCHER.read_text(encoding="utf-8")
    assert "process.exit(1)" in src and "NOT APPLIED" in src
