"""Hermetic tests for scripts/stack/stack.py.

Hermetic means: no Docker daemon, ever. Every test drives the real
stack.manifest.toml (so the ordering and the product expansions being pinned are
the ones that ship) inside a throwaway root whose compose files are empty
placeholders and whose env files are generated from the manifest's own `keys`
lists. The docker call is an injected recorder, so a test that would have
started a container instead leaves a command in a list.

Run:  python -m pytest scripts/stack -q
"""

from __future__ import annotations

import ast
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "stack.manifest.toml"

# The order scripts/stack/stack.ps1 uses. Portal is deliberately absent: that
# script says so in its header, and the manifest marks the plane `manual`.
PS1_ORDER = ["anchor", "inference", "frontend", "memory", "search", "coder", "ob1", "agent-org"]

ALL_PLANES_BUT_ANCHOR = "inference,frontend,memory,search,coder,ob1,agent-org,portal"


class Recorder:
    """Stand-in for the docker call: records, never executes."""

    def __init__(self, exit_codes=None):
        self.commands: list[list[str]] = []
        self.exit_codes = exit_codes or {}

    def __call__(self, cmd, cwd):
        self.commands.append(list(cmd))
        return self.exit_codes.get(tuple(cmd), 0)

    @property
    def lines(self) -> list[str]:
        return [" ".join(c) for c in self.commands]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A throwaway repo root: the real manifest, placeholder compose + env files."""
    shutil.copy(REAL_MANIFEST, tmp_path / stack.MANIFEST_NAME)
    manifest = stack.Manifest.load(tmp_path / stack.MANIFEST_NAME)

    for name in manifest.order:
        compose = tmp_path / Path(manifest.plane(name)["compose"])
        compose.parent.mkdir(parents=True, exist_ok=True)
        compose.write_text("# placeholder - these tests never invoke docker\n", encoding="utf-8")

    env_files: dict[Path, list[str]] = {}
    for name in manifest.order:
        path = manifest.env_path(tmp_path, name)
        env_files.setdefault(path, [])
        env_files[path].extend(f"{key}=value-for-{key}" for key in manifest.keys(name))
    for path, lines in env_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return tmp_path


def run(root: Path, *args, runner=None):
    """(exit code, stdout, recorder) for one stack.py invocation."""
    out = io.StringIO()
    recorder = runner if runner is not None else Recorder()
    code = stack.main(["--root", str(root), *args], runner=recorder, stdout=out)
    return code, out.getvalue(), recorder


def docker_lines(text: str) -> list[str]:
    """Just the command lines: comments and headers are not commands."""
    return [ln for ln in text.splitlines() if ln.startswith("docker ")]


def state_of(root: Path) -> dict:
    return json.loads((root / stack.STATE_REL).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the anchor's own rules
# --------------------------------------------------------------------------


def test_driver_imports_only_the_standard_library():
    """The OptiPlex gets Python and Docker and nothing else (anchor criterion)."""
    tree = ast.parse((Path(stack.__file__)).read_text(encoding="utf-8"))
    modules = {
        (node.names[0].name if isinstance(node, ast.Import) else node.module).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    assert not [m for m in modules if m not in sys.stdlib_module_names]


def test_real_manifest_parses_and_every_edge_names_a_real_plane():
    manifest = stack.Manifest.load(REAL_MANIFEST)  # _validate raises on an unknown name
    assert manifest.order == PS1_ORDER + ["portal"]
    assert list(manifest.products) == [
        "chat", "inference", "memory", "search", "open-brain",
        "research", "coding-agent", "agent-org", "digest", "portal",
    ]


def test_manifest_requires_edges_are_the_verified_set():
    """Pinned so that dropping or inventing an edge fails here, not in production."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert {name: manifest.requires(name) for name in manifest.order} == {
        "anchor": [],
        "inference": ["anchor"],
        "frontend": ["anchor"],
        "memory": ["anchor", "inference"],
        "search": ["anchor"],
        "coder": ["anchor", "inference"],
        "ob1": ["anchor", "inference", "search"],
        "agent-org": ["anchor", "inference"],
        "portal": ["anchor", "frontend"],
    }
    assert {name: manifest.optional(name) for name in manifest.order} == {
        "anchor": [],
        "inference": [],
        "frontend": ["inference", "search"],
        "memory": [],
        "search": [],
        "coder": [],
        "ob1": ["frontend"],
        "agent-org": [],
        "portal": ["ob1", "inference"],
    }


# --------------------------------------------------------------------------
# default state
# --------------------------------------------------------------------------


def plane_statuses(out: str) -> dict[str, str]:
    """{plane: 'enabled'|'disabled'} parsed out of the `planes:` block of `list`."""
    statuses: dict[str, str] = {}
    inside = False
    for line in out.splitlines():
        if line.rstrip() == "planes:":
            inside = True
            continue
        if inside:
            if not line.strip():
                break
            parts = line.split()
            statuses[parts[0]] = parts[1]
    return statuses


def test_no_state_file_lists_frontend_enabled_and_the_rest_disabled(root):
    code, out, _ = run(root, "list")
    assert code == 0
    statuses = plane_statuses(out)
    assert set(statuses) == set(PS1_ORDER + ["portal"])
    assert statuses.pop("frontend") == "enabled"
    assert set(statuses.values()) == {"disabled"}


def test_no_state_file_up_dry_run_is_anchor_then_frontend_and_runs_nothing(root):
    code, out, recorder = run(root, "up", "--dry-run")
    assert code == 0
    assert docker_lines(out) == [
        "docker compose -f docker-compose.yml --env-file .env up -d",
        "docker compose -f frontend/docker-compose.yml --env-file .env up -d",
    ]
    assert recorder.commands == []  # a dry-run that starts a container FAILS


def test_up_without_dry_run_goes_through_the_runner(root):
    code, out, recorder = run(root, "up")
    assert code == 0
    assert recorder.lines == docker_lines(out)
    assert recorder.lines[0].endswith("docker-compose.yml --env-file .env up -d")


def test_up_stops_at_the_first_failing_plane(root):
    failing = ("docker", "compose", "-f", "docker-compose.yml", "--env-file", ".env", "up", "-d")
    recorder = Recorder({failing: 17})
    code, out, recorder = run(root, "up", runner=recorder)
    assert code == stack.EXIT_REFUSED
    assert len(recorder.commands) == 1
    assert "exited 17" in out


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def test_full_stack_up_matches_the_order_stack_ps1_uses(root):
    run(root, "init", "--planes", ALL_PLANES_BUT_ANCHOR)
    code, out, _ = run(root, "up", "--dry-run")
    assert code == 0
    started = [line.split(" -f ")[1].split()[0] for line in docker_lines(out)]
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert started == [manifest.plane(p)["compose"] for p in PS1_ORDER]


def test_full_stack_down_is_the_exact_reverse(root):
    run(root, "init", "--planes", ALL_PLANES_BUT_ANCHOR)
    _, up_out, _ = run(root, "up", "--dry-run")
    _, down_out, _ = run(root, "down", "--dry-run")
    ups = [line.split(" -f ")[1].split()[0] for line in docker_lines(up_out)]
    downs = [line.split(" -f ")[1].split()[0] for line in docker_lines(down_out)]
    assert downs == list(reversed(ups))
    assert all(line.endswith(" down") for line in docker_lines(down_out))


def test_order_planes_breaks_ties_by_declaration_order():
    manifest = stack.Manifest.load(REAL_MANIFEST)
    everything = set(manifest.order)
    assert stack.order_planes(manifest, everything) == PS1_ORDER + ["portal"]
    # A subset keeps the same relative order.
    assert stack.order_planes(manifest, {"ob1", "anchor", "search", "inference"}) == [
        "anchor", "inference", "search", "ob1",
    ]


def test_a_docker_context_prefixes_the_command(root):
    run(root, "init", "--planes", "inference,frontend", "--context", "inference=optiplex-1")
    _, out, _ = run(root, "up", "--dry-run")
    assert "docker --context optiplex-1 compose -f inference/docker-compose.yml" in out
    assert "docker compose -f frontend/docker-compose.yml" in out


def test_context_flag_rejects_a_malformed_pair(root):
    code, out, _ = run(root, "init", "--context", "inference")
    assert code == stack.EXIT_REFUSED
    assert "plane=name" in out


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_enable_memory_refuses_and_names_inference_and_the_remedy(root):
    code, out, _ = run(root, "enable", "memory")
    assert code == stack.EXIT_REFUSED
    assert "inference" in out
    assert "stack.py enable inference" in out
    assert not (root / stack.STATE_REL).exists()


def test_enable_search_with_a_blank_key_refuses_and_names_the_key(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env = manifest.env_path(root, "search")
    env.write_text(
        env.read_text(encoding="utf-8").replace(
            "MULLVAD_WG_PRIVATE_KEY=value-for-MULLVAD_WG_PRIVATE_KEY", "MULLVAD_WG_PRIVATE_KEY="
        ),
        encoding="utf-8",
    )
    code, out, _ = run(root, "enable", "search")
    assert code == stack.EXIT_REFUSED
    assert "MULLVAD_WG_PRIVATE_KEY" in out
    assert "blank" in out


def test_enable_refuses_a_missing_key_too(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env = manifest.env_path(root, "frontend")
    env.write_text(
        "\n".join(
            line for line in env.read_text(encoding="utf-8").splitlines()
            if not line.startswith("WEBUI_SECRET_KEY=")
        ) + "\n",
        encoding="utf-8",
    )
    code, out, _ = run(root, "enable", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "WEBUI_SECRET_KEY is missing" in out


def test_unknown_name_is_refused_and_lists_what_exists(root):
    code, out, _ = run(root, "enable", "nope")
    assert code == stack.EXIT_REFUSED
    assert "unknown plane or product 'nope'" in out
    assert "research" in out


def test_restart_all_is_refused(root):
    code, out, recorder = run(root, "restart", "all")
    assert code == stack.EXIT_REFUSED
    assert "emergency-recovery.ps1" in out
    assert recorder.commands == []


def test_restart_one_plane_restarts_only_that_plane(root):
    code, out, _ = run(root, "restart", "frontend", "--dry-run")
    assert code == 0
    assert docker_lines(out) == [
        "docker compose -f frontend/docker-compose.yml --env-file .env restart"
    ]


def test_disable_refuses_while_something_still_requires_the_plane(root):
    run(root, "init", "--planes", "inference,frontend,memory")
    code, out, _ = run(root, "disable", "inference")
    assert code == stack.EXIT_REFUSED
    assert "memory" in out
    run(root, "disable", "memory")
    code, _, _ = run(root, "disable", "inference")
    assert code == 0


# --------------------------------------------------------------------------
# products and surfaces
# --------------------------------------------------------------------------


def test_enable_research_pulls_its_planes_and_ob1_profiles(root):
    code, out, _ = run(root, "enable", "research")
    assert code == 0
    planes = state_of(root)["planes"]
    assert set(planes) == {"inference", "search", "ob1", "frontend"}
    assert set(planes["ob1"]["profiles"]) == {"idea-refinery", "research", "wiki", "notebook"}
    assert "PENDING" in out  # the driver says the profiles do not exist yet


def test_enable_research_headless_omits_the_wiki_and_notebook_profiles(root):
    code, out, _ = run(root, "enable", "research", "--headless")
    assert code == 0
    planes = state_of(root)["planes"]
    assert set(planes) == {"inference", "search", "ob1", "frontend"}  # frontend is a PLANE here
    assert set(planes["ob1"]["profiles"]) == {"idea-refinery", "research"}
    assert "--headless" in out


def test_headless_also_drops_a_plane_that_is_only_a_surface(root):
    run(root, "init", "--planes", "inference")     # a state WITHOUT the frontend
    run(root, "enable", "coding-agent", "--headless")
    assert set(state_of(root)["planes"]) == {"inference", "coder"}


def test_a_surface_plane_is_enabled_without_headless(root):
    run(root, "init", "--planes", "inference")
    run(root, "enable", "coding-agent")
    assert set(state_of(root)["planes"]) == {"inference", "coder", "frontend"}


def test_enabling_from_the_default_state_keeps_the_frontend(root):
    """No state file means frontend is already enabled; `enable` never takes it away."""
    run(root, "enable", "--product", "search")
    assert set(state_of(root)["planes"]) == {"frontend", "search"}


def test_the_anchor_is_implicit_never_written_to_state_but_always_started(root):
    run(root, "enable", "research")
    assert "anchor" not in state_of(root)["planes"]
    _, out, _ = run(root, "up", "--dry-run")
    assert docker_lines(out)[0] == "docker compose -f docker-compose.yml --env-file .env up -d"


def test_a_plane_name_wins_over_a_product_of_the_same_name(root):
    """`enable memory` must be the PLANE, which is what makes it refuse."""
    code, out, _ = run(root, "enable", "memory")
    assert code == stack.EXIT_REFUSED
    assert "names both a plane and a product" in out
    code, _, _ = run(root, "enable", "--product", "memory")
    assert code == 0
    # frontend is there because the absent state file defaults to it, not because
    # the product asked for it.
    assert set(state_of(root)["planes"]) == {"memory", "inference", "frontend"}


def test_enable_a_product_refuses_on_any_member_planes_blank_key(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env = manifest.env_path(root, "ob1")
    env.write_text(env.read_text(encoding="utf-8").replace(
        "OPS_GATEWAY_KEY=value-for-OPS_GATEWAY_KEY", "OPS_GATEWAY_KEY="), encoding="utf-8")
    code, out, _ = run(root, "enable", "research")
    assert code == stack.EXIT_REFUSED
    assert "OPS_GATEWAY_KEY" in out
    assert "read by plane ob1" in out
    assert not (root / stack.STATE_REL).exists()


# --------------------------------------------------------------------------
# per-plane compose invocation
# --------------------------------------------------------------------------


def test_ob1_and_agent_org_pass_no_env_file_and_read_their_own(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.env_file("ob1") is None
    assert manifest.env_path(REPO_ROOT, "ob1") == REPO_ROOT / "OB1" / "docker" / ".env"
    assert manifest.env_file("agent-org") is None
    assert manifest.env_path(REPO_ROOT, "agent-org") == REPO_ROOT / "agent-org" / "docker" / ".env"
    assert manifest.env_file("frontend") == ".env"


def test_ob1_keeps_the_idea_refinery_profile_stack_ps1_always_passes(root):
    run(root, "init", "--planes", "inference,search,ob1")
    _, out, _ = run(root, "up", "--dry-run")
    ob1 = [line for line in docker_lines(out) if "OB1/docker" in line]
    assert ob1 == [
        "docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery up -d"
    ]


def test_portal_is_declared_but_never_driven(root):
    run(root, "init", "--planes", ALL_PLANES_BUT_ANCHOR)
    _, out, recorder = run(root, "up", "--dry-run")
    assert not any("portal/docker-compose.yml" in line for line in docker_lines(out))
    assert "portal-on.ps1" in out
    code, out, _ = run(root, "restart", "portal")
    assert code == stack.EXIT_REFUSED
    assert "portal-on.ps1" in out


# --------------------------------------------------------------------------
# state file
# --------------------------------------------------------------------------


def test_init_writes_a_state_file_and_refuses_to_overwrite_it(root):
    code, _, _ = run(root, "init", "--planes", "frontend")
    assert code == 0
    assert (root / stack.STATE_REL).exists()
    before = (root / stack.STATE_REL).read_text(encoding="utf-8")

    code, out, _ = run(root, "init", "--planes", "inference,frontend")
    assert code == stack.EXIT_REFUSED
    assert "--force" in out
    assert (root / stack.STATE_REL).read_text(encoding="utf-8") == before

    code, _, _ = run(root, "init", "--planes", "inference,frontend", "--force")
    assert code == 0
    assert set(state_of(root)["planes"]) == {"inference", "frontend"}


def test_init_with_no_flags_writes_the_default(root):
    run(root, "init")
    assert set(state_of(root)["planes"]) == set(stack.DEFAULT_ENABLED)


def test_init_with_a_product_runs_the_same_checks_enable_does(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env = manifest.env_path(root, "search")
    env.write_text(env.read_text(encoding="utf-8").replace(
        "MULLVAD_WG_PRIVATE_KEY=value-for-MULLVAD_WG_PRIVATE_KEY",
        "MULLVAD_WG_PRIVATE_KEY="), encoding="utf-8")
    code, out, _ = run(root, "init", "--product", "research")
    assert code == stack.EXIT_REFUSED
    assert "MULLVAD_WG_PRIVATE_KEY" in out
    assert not (root / stack.STATE_REL).exists()


def test_status_only_ever_runs_ps(root):
    run(root, "init", "--planes", "inference,frontend")
    code, out, recorder = run(root, "status")
    assert code == 0
    assert recorder.lines == [
        "docker compose -f inference/docker-compose.yml --env-file .env ps",
        "docker compose -f frontend/docker-compose.yml --env-file .env ps",
    ]
    assert all(cmd[-1] == "ps" for cmd in recorder.commands)


def test_doctor_reports_a_blank_key_and_a_missing_compose_file(root):
    run(root, "init", "--planes", "inference,frontend")
    manifest = stack.Manifest.load(REAL_MANIFEST)
    (root / Path(manifest.plane("inference")["compose"])).unlink()
    env = manifest.env_path(root, "frontend")
    env.write_text(env.read_text(encoding="utf-8").replace(
        "WEBUI_SECRET_KEY=value-for-WEBUI_SECRET_KEY", "WEBUI_SECRET_KEY="), encoding="utf-8")
    code, out, _ = run(root, "doctor")
    assert code == stack.EXIT_REFUSED
    assert "compose file missing" in out
    assert "WEBUI_SECRET_KEY is blank" in out


def test_env_file_reader_handles_quotes_exports_and_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n"
        "EMPTY=\n"
        "QUOTED=\"a b\"\n"
        "export EXPORTED=yes\n"
        "SPACED = spaced\n"
        "NOTAKEY\n",
        encoding="utf-8",
    )
    values = stack.read_env_file(path)
    assert values == {"EMPTY": "", "QUOTED": "a b", "EXPORTED": "yes", "SPACED": "spaced"}


def test_a_corrupt_state_file_is_refused_not_ignored(root):
    (root / stack.STATE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / stack.STATE_REL).write_text("{not json", encoding="utf-8")
    code, out, _ = run(root, "list")
    assert code == stack.EXIT_REFUSED
    assert "unreadable" in out
