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
        # Five planes: the tailnet serve routes entrypoint.sh raises at boot, each
        # behind an ..._ENABLED that defaults TRUE. The first cut of the manifest
        # listed only inference and search and was WRONG.
        "frontend": ["inference", "search", "ob1", "portal", "agent-org"],
        "memory": [],
        "search": [],
        "coder": [],
        "ob1": ["frontend", "agent-org"],
        # agent-org -> ob1 is deliberately absent: AO_OPENBRAIN_MIRROR_ENABLED and
        # AO_GROUNDING_ENABLED both default false, so a default boot never reaches.
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
    # and, like the requires-refusal, it names a remedy: the file to edit and a
    # command that lists every such key.
    assert "Set them in .env" in out
    assert "stack.py doctor" in out


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
    # Was `assert "PENDING" in out`. sl-ob1-profiles (2026-09-19) put all three
    # into OB1/docker/docker-compose.yml, so the driver must no longer warn that
    # enabling them changes nothing - a stale "pending" notice is a lie about
    # what `up` will start.
    assert "PENDING" not in out


def test_the_ob1_profiles_are_no_longer_pending(root):
    """The three OB1 profiles exist in the compose file, so nothing may mark them pending."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert set(manifest.profiles("ob1")) == {"idea-refinery", "research", "wiki", "notebook"}
    assert manifest.pending_profiles("ob1") == []
    # Only idea-refinery is `default`: `default` means "passed on every
    # invocation", and --headless has to be able to drop wiki and notebook.
    assert manifest.default_profiles("ob1") == ["idea-refinery"]


def test_digest_needs_the_notebook_profile_not_just_research(root):
    """openbrain-podcast renders its audio through open_notebook, so --headless must not drop it."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.product("digest")["profiles"]["ob1"] == ["research", "notebook"]
    assert "surfaces" not in manifest.product("digest")
    code, _, _ = run(root, "enable", "digest", "--headless")
    assert code == 0
    assert set(state_of(root)["planes"]["ob1"]["profiles"]) == {
        "idea-refinery", "research", "notebook"
    }


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


def test_disable_says_so_when_a_name_is_ambiguous_too(root):
    """The note is worth most on the destructive half of the pair."""
    run(root, "init", "--planes", "inference,memory")
    code, out, _ = run(root, "disable", "memory")
    assert code == 0
    assert "names both a plane and a product" in out
    assert set(state_of(root)["planes"]) == {"inference"}


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


def test_a_bare_ob1_plane_passes_its_default_profile_and_what_that_needs(root):
    """Enabling the PLANE gets `default` profiles, closed over `requires`.

    `idea-refinery` is ob1's only default; it `requires` research because
    openbrain-idea-refinery's only engine is openbrain-research. So a bare plane
    enable passes BOTH - it used to pass idea-refinery alone, which started a drain
    that could never drain. `wiki` and `notebook` are surfaces and stay out; they
    are reached by enabling a PRODUCT.

    NOTE the deliberate divergence from scripts/stack/stack.ps1, which passes all
    four on every OB1 invocation because it is the pre-manifest driver and must keep
    starting the 30 containers running on this host; the comment on its `ob1` row
    says so. sl-driver-parity reconciles the two.
    """
    run(root, "init", "--planes", "inference,search,ob1")
    _, out, _ = run(root, "up", "--dry-run")
    ob1 = [line for line in docker_lines(out) if "OB1/docker" in line]
    assert ob1 == [
        "docker compose -f OB1/docker/docker-compose.yml "
        "--profile idea-refinery --profile research up -d"
    ]


def test_the_idea_refinery_profile_pulls_the_research_engine_it_calls(root):
    """A profile whose only engine is another profile must pull it in."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.profile_requires("ob1", "idea-refinery") == ["research"]
    # ...and it is the plane's only default, so this closure runs on every invocation.
    assert manifest.default_profiles("ob1") == ["idea-refinery"]
    # Even the most headless path a person can ask for carries the engine.
    code, _, _ = run(root, "enable", "open-brain", "--headless")
    assert code == 0
    assert set(state_of(root)["planes"]["ob1"]["profiles"]) == {"idea-refinery", "research"}


def test_enabling_the_PLANE_writes_the_closure_not_just_the_defaults(root):
    """`enable <plane>` must WRITE what it PRINTS.

    Attempt 2 shipped the closure on cmd_enable's product branch only, so
    `stack.py enable ob1` printed "profiles: idea-refinery, research" and wrote
    ["idea-refinery"]. Drive time was right either way - `run_profiles` closes -
    which is exactly why 44 tests passed with the defect present: no CLI surface
    misled, the artifact on disk just lied. Assert the FILE, not the line.
    """
    run(root, "init", "--planes", "inference,search")
    code, out, _ = run(root, "enable", "ob1")
    assert code == 0
    assert "profiles: idea-refinery, research" in out
    assert state_of(root)["planes"]["ob1"]["profiles"] == ["idea-refinery", "research"]


def test_init_with_planes_writes_the_closure_too(root):
    """The third state-file writer. Same defect, same fix, its own test."""
    run(root, "init", "--planes", "inference,search,ob1")
    assert state_of(root)["planes"]["ob1"]["profiles"] == ["idea-refinery", "research"]
    # ...and the --context writer, which is a separate call site.
    run(root, "init", "--force", "--planes", "inference,search,ob1", "--context", "ob1=remote")
    entry = state_of(root)["planes"]["ob1"]
    assert entry["profiles"] == ["idea-refinery", "research"] and entry["context"] == "remote"


def test_every_state_writer_goes_through_the_one_helper(root):
    """No call site may write profiles into state except `enable_plane_profiles`.

    The defect above existed because three writers each resolved profiles their own
    way. This pins the shape of the fix: `State.enable` is called exactly once in the
    module, from the helper. A new command that writes state and skips it reintroduces
    the same class of bug, silently.
    """
    tree = ast.parse(Path(stack.__file__).read_text(encoding="utf-8"))
    callers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            # `<something>.enable(...)` where the receiver is a plain name - i.e.
            # `state.enable(...)` / `fresh.enable(...)`, the State method. Method
            # DEFINITIONS are not Calls, so State.enable's own def is not matched.
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "enable"
                    and isinstance(inner.func.value, ast.Name)):
                callers.append(node.name)
    assert sorted(set(callers)) == ["enable_plane_profiles"], (
        f"State.enable is called from {sorted(set(callers))}; it must only be called by "
        "enable_plane_profiles, which closes the profile set over `requires`."
    )


def test_profile_requires_is_transitive_and_order_is_the_manifests(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    manifest.planes["ob1"]["profiles"]["wiki"] = {"description": "x", "requires": ["notebook"]}
    manifest.planes["ob1"]["profiles"]["research"] = {"description": "y", "requires": ["wiki"]}
    assert manifest.profile_closure("ob1", {"idea-refinery"}) == {
        "idea-refinery", "research", "wiki", "notebook"
    }
    # The ORDER handed to compose stays the manifest's declaration order, not
    # discovery order - `--profile` flags are order-insensitive, but a command line
    # that reshuffles between runs makes diffing two dry-runs pointlessly hard.
    assert manifest.profile_order("ob1", manifest.profile_closure("ob1", {"idea-refinery"})) == [
        "idea-refinery", "research", "wiki", "notebook"
    ]


def test_a_profile_requiring_an_unknown_profile_is_refused(root):
    """The typo has to fail loudly here, not silently pass an unknown flag to compose."""
    text = (REAL_MANIFEST).read_text(encoding="utf-8").replace(
        'requires    = ["research"]', 'requires    = ["reserch"]'
    )
    path = root / "typo.manifest.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(stack.Refusal) as exc:
        stack.Manifest.load(path)
    assert "ob1.idea-refinery" in str(exc.value) and "reserch" in str(exc.value)


def test_enabling_research_drives_ob1_with_every_profile_the_live_set_needs(root):
    """The `research` product's dry-run must name the profiles that render the live 30."""
    run(root, "init", "--product", "research")
    _, out, _ = run(root, "up", "--dry-run")
    ob1 = [line for line in docker_lines(out) if "OB1/docker" in line]
    assert ob1 == [
        "docker compose -f OB1/docker/docker-compose.yml "
        "--profile idea-refinery --profile research --profile wiki --profile notebook up -d"
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


# --------------------------------------------------------------------------
# health - the fifteen probes stack.ps1 ran, one for one
# --------------------------------------------------------------------------
#
# The point of pinning the NAMES is parity. A probe dropped, merged into a
# neighbour or renamed shows up here as a list mismatch, which is exactly the
# anchor criterion ("a probe dropped, merged or weakened FAILS").

PS1_PROBES = [
    "0 unhealthy containers (found: )",
    "anchor: ai-stack_llm-net exists",
    "inference: llm-gateway liveliness",
    "frontend: OWUI http://127.0.0.1:3000/health",
    "frontend: 8 tailnet serve routes",
    "frontend: owui/ manifest rows drifted from live webui.db: 0",
    "memory: cloud door http://127.0.0.1:8060/health",
    "search: gateway http://127.0.0.1:8085/healthz",
    "search: ok - 4 engine(s) answering",
    "coder: little-coder daemon :8090/health",
    "OB1: open_notebook API :5055/api/config",
    "OB1: ops door :8062/health",
    "OB1: research-curator http://127.0.0.1:8816/health",
    "OB1: openbrain-db accepting connections",
    "agent-org: mattermost ping",
]

HEALTHY_SEARCH = json.dumps(
    {"status": "ok", "search": "ok",
     "providers": {"searxng": {"engines_answering_now": 4, "verdict": "ok"}}}
)


class FakeHost:
    """A stand-in stack: every docker call and every HTTP GET is scripted.

    Defaults are the all-green case; a test breaks exactly the one thing it is
    about. Nothing here touches a daemon or a socket.
    """

    def __init__(self, **broken):
        self.unhealthy = broken.get("unhealthy", [])
        self.network = broken.get("network", "ai-stack_llm-net")
        self.exec_codes = broken.get("exec_codes", {})
        self.serve_routes = broken.get("serve_routes", "8")
        self.drift_stdout = broken.get("drift_stdout", "0")
        self.drift_stderr = broken.get("drift_stderr", "")
        self.http_status = broken.get("http_status", {})
        self.search_body = broken.get("search_body", HEALTHY_SEARCH)
        # sl-frontend-solo made the tailnet probe deployment-aware: it renders the
        # frontend project and skips itself when that render has no `tailscale`
        # service. The default here is THIS host - the profile is deployed.
        self.frontend_services = broken.get(
            "frontend_services",
            ["openwebui", "openwebui-backup", "tailscale", "tailscale-backup"],
        )
        self.running = broken.get("running", [])
        self.calls: list[list[str]] = []

    def capture(self, cmd, cwd):
        self.calls.append(list(cmd))
        if cmd[0] == "powershell":
            return stack.CommandResult(0, self.drift_stdout, self.drift_stderr)
        if cmd[:3] == ["docker", "compose", "-f"]:
            return stack.CommandResult(0 if self.frontend_services else 1,
                                       "\n".join(self.frontend_services), "")
        if cmd[:2] == ["docker", "ps"]:
            if "name=tailscale" in cmd:
                return stack.CommandResult(0, "\n".join(self.running), "")
            return stack.CommandResult(0, "\n".join(self.unhealthy), "")
        if cmd[:2] == ["docker", "network"]:
            return stack.CommandResult(0, self.network, "")
        if cmd[:2] == ["docker", "exec"]:
            container = cmd[2]
            if container == "tailscale":
                return stack.CommandResult(0, self.serve_routes, "")
            return stack.CommandResult(self.exec_codes.get(container, 0), "", "")
        raise AssertionError(f"unscripted docker call: {cmd}")

    def http(self, url, timeout=8):
        status = self.http_status.get(url, 200)
        body = self.search_body if url.endswith("8085/health") else "{}"
        return stack.HttpResult(status, body)


def sweep(host: FakeHost, root: Path):
    out = io.StringIO()
    code = stack.main(["--root", str(root), "health"], stdout=out, capture=host.capture, http=host.http)
    return code, out.getvalue()


def probe_lines(text: str) -> list[tuple[str, str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("  [OK]   "):
            rows.append(("OK", line[len("  [OK]   "):]))
        elif line.startswith("  [FAIL] "):
            rows.append(("FAIL", line[len("  [FAIL] "):]))
    return rows


def test_health_runs_exactly_the_probes_stack_ps1_ran_in_the_same_order(root):
    code, out = sweep(FakeHost(), root)
    assert [name for _state, name in probe_lines(out)] == PS1_PROBES
    assert code == 0
    assert "ALL HEALTH PROBES PASSED" in out


def test_health_exit_code_is_the_number_of_failed_probes(root):
    host = FakeHost(
        unhealthy=["openbrain-curator"],
        exec_codes={"openbrain-db": 1},
        http_status={"http://127.0.0.1:8062/health": 503},
    )
    code, out = sweep(host, root)
    assert code == 3
    assert "3 probe(s) FAILED" in out
    assert [name for state, name in probe_lines(out) if state == "FAIL"] == [
        "0 unhealthy containers (found: openbrain-curator)",
        "OB1: ops door :8062/health",
        "OB1: openbrain-db accepting connections",
    ]


def test_one_dead_plane_costs_its_own_probes_and_not_the_rest_of_the_sweep(root):
    """The regression this sweep was rebuilt around.

    stack.ps1's first owui-drift probe assigned OUTSIDE a Probe block, so a
    stopped openwebui turned the check's stderr into a terminating
    NativeCommandError: five probe lines, no summary, and the eight later probes
    (memory, search, coder, OB1 x4, agent-org) never ran at all.
    """
    host = FakeHost(
        drift_stdout="REFUSED",
        drift_stderr="REFUSED: the container 'openwebui' is not running.",
        http_status={"http://127.0.0.1:3000/health": 0},
        serve_routes="",
    )
    code, out = sweep(host, root)
    rows = probe_lines(out)
    assert len(rows) == 15
    assert [name for state, name in rows if state == "FAIL"] == [
        "frontend: OWUI http://127.0.0.1:3000/health",
        "frontend: 8 tailnet serve routes",
        "frontend: owui/ manifest rows drifted from live webui.db: "
        "REFUSED - the container 'openwebui' is not running.",
    ]
    assert code == 3
    # The eight probes AFTER the frontend block still ran and still passed.
    assert [state for state, _name in rows][-9:] == ["OK"] * 9


def test_a_drift_count_above_zero_fails_and_the_number_is_in_the_label(root):
    code, out = sweep(FakeHost(drift_stdout="7"), root)
    assert code == 1
    assert ("FAIL", "frontend: owui/ manifest rows drifted from live webui.db: 7") in probe_lines(out)


def test_healthz_alone_cannot_pass_search(root):
    """/healthz said 200 through the whole 2026-09-11 outage. Hence two probes."""
    degraded = json.dumps(
        {"search": "DEGRADED", "providers": {"searxng": {"engines_answering_now": 0}}}
    )
    code, out = sweep(FakeHost(search_body=degraded), root)
    rows = probe_lines(out)
    assert ("OK", "search: gateway http://127.0.0.1:8085/healthz") in rows
    assert ("FAIL", "search: DEGRADED - 0 engine(s) answering") in rows
    assert code == 1


def test_search_unknown_is_not_a_failure_only_a_measured_degraded_is(root):
    body = json.dumps({"search": "unknown", "providers": {"searxng": {"engines_answering_now": 0}}})
    code, out = sweep(FakeHost(search_body=body), root)
    assert ("OK", "search: unknown - 0 engine(s) answering") in probe_lines(out)
    assert code == 0


def test_a_probe_that_throws_is_one_failed_probe_not_a_crash(root):
    """`[int]$r` on an empty string threw in PowerShell; int('') raises here."""
    code, out = sweep(FakeHost(serve_routes="not a number"), root)
    assert ("FAIL", "frontend: 8 tailnet serve routes") in probe_lines(out)
    assert code == 1


def test_a_serve_route_count_below_the_threshold_fails(root):
    """The THRESHOLD, not just the parse.

    A tester weakened this probe from `>= 8` to `>= 0` in the driver and the
    whole suite stayed green: the two cases around it only covered an empty and
    an unparseable answer, both of which fail either way. Eight routes is the
    tailnet's full serve table; seven means one backend stopped being published,
    which is precisely the silent failure the probe exists for.
    """
    # One table rather than four asserts: the cases below the threshold and the
    # cases above it have to be visible in a single glance, or a reader stops
    # early and reports the last one missing (a tester did, on this exact body).
    for routes, expect_fail in (("3", True), ("7", True), ("8", False), ("9", False)):
        code, out = sweep(FakeHost(serve_routes=routes), root)
        verdict = "FAIL" if expect_fail else "OK"
        assert (verdict, "frontend: 8 tailnet serve routes") in probe_lines(out), \
            f"{routes} routes should be {verdict}"
        assert code == (1 if expect_fail else 0), f"{routes} routes -> exit {code}"


def test_the_liveliness_probe_never_gets_litellms_bare_health(root):
    """A GET of LiteLLM /health through the alias makes it load every model."""
    host = FakeHost()
    sweep(host, root)
    gateway = [c for c in host.calls if c[:3] == ["docker", "exec", "llm-gateway"]]
    assert gateway and all("/health/liveliness" in " ".join(c) for c in gateway)
    assert not any("localhost:8080/health'" in " ".join(c) for c in gateway)


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------


def test_stats_hands_off_to_the_powershell_report(root):
    script = root / "scripts" / "stack" / "stack-stats.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# placeholder\n", encoding="utf-8")
    code, out, recorder = run(root, "stats")
    assert code == 0
    assert len(recorder.commands) == 1
    assert recorder.commands[0][:1] == ["powershell"]
    assert recorder.commands[0][-1].endswith("stack-stats.ps1")


def test_stats_refuses_off_windows_rather_than_printing_nothing(root, monkeypatch):
    """A verb that silently does nothing is the failure class this repo hunts."""
    script = root / "scripts" / "stack" / "stack-stats.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(stack, "WINDOWS", False)
    code, out, recorder = run(root, "stats")
    assert code == stack.EXIT_REFUSED
    assert out.startswith("refused:")
    assert "stack-stats.ps1" in out
    assert recorder.commands == []


# --------------------------------------------------------------------------
# plane selection: one plane, --all, or whatever this machine enables
# --------------------------------------------------------------------------


def test_up_all_is_the_order_stack_ps1_used_whatever_the_state_says(root):
    code, out, _recorder = run(root, "up", "--all", "--dry-run")
    assert code == 0
    assert [ln.split(" -f ")[1].split()[0] for ln in docker_lines(out)] == [
        "docker-compose.yml", "inference/docker-compose.yml", "frontend/docker-compose.yml",
        "memory/docker-compose.yml", "search/docker-compose.yml", "coder/docker-compose.yml",
        "OB1/docker/docker-compose.yml", "agent-org/docker/docker-compose.yml",
    ]


def test_down_all_is_the_exact_reverse(root):
    _code, up_out, _r = run(root, "up", "--all", "--dry-run")
    _code, down_out, _r = run(root, "down", "--all", "--dry-run")
    ups = [ln.split(" -f ")[1].split()[0] for ln in docker_lines(up_out)]
    downs = [ln.split(" -f ")[1].split()[0] for ln in docker_lines(down_out)]
    assert downs == list(reversed(ups))


def test_up_all_never_starts_the_manual_plane(root):
    _code, out, _r = run(root, "up", "--all", "--dry-run")
    assert "portal/docker-compose.yml" not in out


def test_up_one_plane_starts_only_that_plane_and_names_what_it_assumes(root):
    code, out, _r = run(root, "up", "coder", "--dry-run")
    assert code == 0
    assert docker_lines(out) == ["docker compose -f coder/docker-compose.yml --env-file .env up -d"]
    assert "# note: coder requires anchor, inference; this starts only coder" in out


def test_a_plane_and_all_together_is_refused(root):
    code, out, _r = run(root, "up", "coder", "--all", "--dry-run")
    assert code == stack.EXIT_REFUSED
    assert "not both" in out


def test_status_reports_the_enabled_planes_and_does_not_pull_in_the_anchor(root):
    run(root, "init", "--planes", "memory")
    _code, _out, recorder = run(root, "status")
    assert recorder.lines == ["docker compose -f memory/docker-compose.yml --env-file .env ps"]


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------
#
# Hermetic here means the compose RENDER is scripted too: `docker compose config`
# is answered from a fixture, so these tests pin the generator's behaviour
# without a daemon. The real tree is covered by `inventory --check` itself,
# which CI and the pre-commit hook both run.

# The inventory generator is unit-tested against a SMALL manifest of its own,
# not the shipping one: these tests are about the generator, and pinning them to
# every port and profile the real stack happens to publish would make an
# unrelated plane change fail them. The real tree is covered three ways - the
# `shipped` tests at the end of this file, the pre-commit check, and the
# `stack-driver` CI job, all of which run `inventory --check` for real.
MINI_MANIFEST = """
[planes.anchor]
compose  = "docker-compose.yml"
env_file = ".env"
implicit = true
requires = []
[planes.anchor.ports]

[planes.inference]
compose  = "inference/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
[planes.inference.ports]
"8081" = "llama-cpp-upstream"
[planes.inference.profiles.local]
description = "the llama.cpp upstreams; COMPOSE_PROFILES in the root .env turns it on"
opt_in      = true

[planes.frontend]
compose  = "frontend/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
[planes.frontend.ports]
"3000" = "openwebui"
[planes.frontend.profiles.gpu]
description = "the CUDA image and the device reservation"
pending     = true

[planes.memory]
compose  = "memory/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
[planes.memory.ports]
"8060" = "mnemory-cloud-gateway"

[planes.search]
compose  = "search/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
[planes.search.ports]
"8085" = "gateway"

[planes.coder]
compose  = "coder/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
[planes.coder.ports]
"9091" = "little-coder metrics"

[planes.ob1]
compose  = "OB1/docker/docker-compose.yml"
requires = ["anchor"]
[planes.ob1.ports]
[planes.ob1.profiles.idea-refinery]
description = "the idea-refinery services"
default     = true

[planes.agent-org]
compose  = "agent-org/docker/docker-compose.yml"
requires = ["anchor"]
[planes.agent-org.ports]
"8065" = "mattermost"
"8830" = "agent-bridge"
[planes.agent-org.profiles.workers]
description = "the worker pool; the operator starts it"
opt_in      = true
[planes.agent-org.profiles.cloud]
description = "the cloud lane; the operator starts it"
opt_in      = true

[planes.portal]
compose  = "portal/docker-compose.yml"
env_file = ".env"
requires = ["anchor"]
manual   = "scripts/portal/portal-on.ps1"
[planes.portal.ports]
[planes.portal.profiles.internet]
description = "cloudflared; portal-on.ps1 passes it"
opt_in      = true
"""


@pytest.fixture
def mini_root(tmp_path: Path) -> Path:
    """A throwaway root built on MINI_MANIFEST, with placeholder compose files."""
    (tmp_path / stack.MANIFEST_NAME).write_text(MINI_MANIFEST, encoding="utf-8")
    manifest = stack.Manifest.load(tmp_path / stack.MANIFEST_NAME)
    for name in manifest.order:
        compose = tmp_path / Path(manifest.plane(name)["compose"])
        compose.parent.mkdir(parents=True, exist_ok=True)
        compose.write_text("# placeholder - the render is scripted\n", encoding="utf-8")
        env = manifest.env_path(tmp_path, name)
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text("", encoding="utf-8")
    return tmp_path


FIXTURE_RENDER = {
    "docker-compose.yml": {"profiles": [], "services": {}},
    "inference/docker-compose.yml": {
        "profiles": ["local"],
        "services": {
            "llm-gateway": {"container_name": "llm-gateway"},
            "llama-cpp-upstream": {
                "container_name": "llama-cpp-upstream",
                "profiles": ["local"],
                "ports": [{"published": "8081"}],
            },
        },
    },
    "frontend/docker-compose.yml": {
        "profiles": [],
        "services": {"openwebui": {"container_name": "openwebui", "ports": [{"published": "3000"}]}},
    },
    "memory/docker-compose.yml": {
        "profiles": [],
        "services": {"mnemory-cloud-gateway": {"container_name": "mnemory-cloud-gateway",
                                               "ports": [{"published": "8060"}]}},
    },
    "search/docker-compose.yml": {
        "profiles": [],
        # The case the `service` field exists for: the key is not the name.
        "services": {"gateway": {"container_name": "search-gateway", "ports": [{"published": "8085"}]}},
    },
    "coder/docker-compose.yml": {
        "profiles": [],
        "services": {"little-coder": {"container_name": "little-coder", "ports": [{"published": "9091"}]}},
    },
    "OB1/docker/docker-compose.yml": {
        "profiles": ["idea-refinery"],
        "services": {
            "openbrain-db": {"container_name": "openbrain-db"},
            "openbrain-idea-refinery": {"container_name": "openbrain-idea-refinery",
                                        "profiles": ["idea-refinery"]},
        },
    },
    "agent-org/docker/docker-compose.yml": {
        "profiles": ["cloud", "workers"],
        "services": {
            "mattermost": {"container_name": "mattermost", "ports": [{"published": "8065"}]},
            "agent-bridge": {"container_name": "agent-bridge", "ports": [{"published": "8830"}]},
            "ao-worker-1": {"container_name": "ao-worker-1", "profiles": ["workers"]},
        },
    },
}

CURATED_PROJECTS = {
    "ai-stack": {"plane": "anchor", "note": "pure network anchor"},
    "inference": {"plane": "inference"},
    "frontend": {"plane": "frontend"},
    "memory": {"plane": "memory"},
    "search": {"plane": "search"},
    "coder": {"plane": "coder"},
    "open-brain": {"plane": "ob1"},
    "agent-org": {"plane": "agent-org", "profiles": ["workers", "cloud"]},
}

CURATED_ROWS = {
    "core": [
        {"container": "openwebui", "project": "frontend", "critical": True,
         "host_health": "http://127.0.0.1:3000/health"},
        {"container": "llm-gateway", "project": "inference", "critical": True},
        {"container": "llama-cpp-upstream", "profile": "local", "project": "inference", "critical": True},
    ],
    "search": [{"container": "search-gateway", "service": "gateway", "project": "search", "critical": False}],
    "memory": [{"container": "mnemory-cloud-gateway", "project": "memory", "critical": False}],
    "coder": [{"container": "little-coder", "project": "coder", "critical": False}],
    "openbrain": [
        {"container": "openbrain-db", "project": "open-brain", "critical": True},
        {"container": "openbrain-idea-refinery", "profile": "idea-refinery",
         "project": "open-brain", "critical": False},
    ],
    "agent-org": [
        {"container": "mattermost", "project": "agent-org", "critical": True},
        {"container": "agent-bridge", "project": "agent-org", "critical": True},
        {"container": "ao-worker-1", "profile": "workers", "project": "agent-org", "critical": False},
    ],
}


class FakeCompose:
    """Answers `config --profiles` and `config --format json` from a fixture."""

    def __init__(self, render=None, missing=()):
        self.render = json.loads(json.dumps(render if render is not None else FIXTURE_RENDER))
        self.missing = set(missing)
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd):
        self.calls.append(list(cmd))
        compose = cmd[cmd.index("-f") + 1]
        spec = self.render[compose]
        if "--profiles" in cmd:
            return stack.CommandResult(0, "\n".join(spec["profiles"]), "")
        services = {k: dict(v) for k, v in spec["services"].items()}
        return stack.CommandResult(0, json.dumps({"name": "x", "services": services}), "")


def curated_file(root: Path, projects=None, rows=None, comment=("generated",)) -> Path:
    path = root / stack.CURATED_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_comment": list(comment),
        "projects": json.loads(json.dumps(projects if projects is not None else CURATED_PROJECTS)),
        "planes": json.loads(json.dumps(rows if rows is not None else CURATED_ROWS)),
    }, indent=2), encoding="utf-8")
    return path


def inventory(root: Path, *args, compose=None):
    out = io.StringIO()
    compose = compose or FakeCompose()
    code = stack.main(["--root", str(root), "inventory", *args], stdout=out, capture=compose)
    return code, out.getvalue(), compose


def generated(root: Path) -> dict:
    return json.loads((root / stack.INVENTORY_REL).read_text(encoding="utf-8"))


def test_inventory_write_builds_the_file_from_the_manifest_the_sidecar_and_the_render(mini_root):
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write")
    assert code == 0, out
    data = generated(mini_root)

    # projects: the compose invocation comes from the manifest, and the anchor's
    # file is null because its render declares no services.
    assert data["projects"]["ai-stack"] == {
        "compose": "docker compose", "file": None, "env_file": None, "note": "pure network anchor",
    }
    assert data["projects"]["inference"] == {
        "compose": "docker compose -f inference/docker-compose.yml --env-file .env",
        "file": "inference/docker-compose.yml", "env_file": ".env",
    }
    # ob1 and agent-org carry no --env-file: compose loads their own.
    assert data["projects"]["open-brain"]["env_file"] is None
    assert data["projects"]["agent-org"]["compose"] == \
        "docker compose -f agent-org/docker/docker-compose.yml"
    assert data["projects"]["agent-org"]["profiles"] == ["workers", "cloud"]
    # the MANUAL plane is deliberately not an inventory project
    assert "portal" not in data["projects"]

    # rows: service only where it differs, profile from the render, curated kept
    rows = {r["container"]: r for group in data["planes"].values() for r in group}
    assert rows["search-gateway"]["service"] == "gateway"
    assert "service" not in rows["openwebui"]
    assert rows["llama-cpp-upstream"]["profile"] == "local"
    assert rows["openwebui"]["host_health"] == "http://127.0.0.1:3000/health"
    assert list(rows["openwebui"]) == ["container", "project", "critical", "host_health"]


def test_inventory_check_passes_on_what_write_just_wrote(mini_root):
    curated_file(mini_root)
    inventory(mini_root, "--write")
    code, out, _c = inventory(mini_root, "--check")
    assert code == 0
    assert "[OK]" in out


def test_inventory_check_is_red_when_one_row_is_edited_by_hand(mini_root):
    curated_file(mini_root)
    inventory(mini_root, "--write")
    path = mini_root / stack.INVENTORY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    data["planes"]["core"][1]["project"] = "memory"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    code, out, _c = inventory(mini_root, "--check")
    assert code != 0
    assert "planes.core[llm-gateway]" in out
    assert "memory" in out and "inference" in out


def test_inventory_check_is_red_when_a_row_is_deleted(mini_root):
    curated_file(mini_root)
    inventory(mini_root, "--write")
    path = mini_root / stack.INVENTORY_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    data["planes"]["agent-org"] = [r for r in data["planes"]["agent-org"] if r["container"] != "ao-worker-1"]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    code, out, _c = inventory(mini_root, "--check")
    assert code != 0
    assert "ao-worker-1" in out


def test_a_container_the_sidecar_does_not_list_refuses_rather_than_being_invented(mini_root):
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["openbrain"] = [r for r in rows["openbrain"] if r["container"] != "openbrain-idea-refinery"]
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "MISSING from scripts/lib/stack-services.curated.json: openbrain-idea-refinery" in out
    assert not (mini_root / stack.INVENTORY_REL).exists()


def test_a_sidecar_row_no_render_produces_is_drift(mini_root):
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["coder"].append({"container": "open-terminal", "project": "coder", "critical": False})
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "NOT IN THE RENDER: open-terminal" in out


def test_a_stale_service_key_in_the_sidecar_is_drift_not_a_silent_win(mini_root):
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["search"][0]["service"] = "searxng"
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "STALE `service` for search-gateway" in out


def test_a_published_port_the_manifest_does_not_declare_is_drift(mini_root):
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["memory/docker-compose.yml"]["services"]["mnemory-cloud-gateway"]["ports"].append(
        {"published": "9999"}
    )
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "PORT 9999 is published by mnemory-cloud-gateway" in out
    assert "[planes.memory.ports]" in out


def test_a_declared_port_nothing_publishes_is_drift(mini_root):
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["frontend/docker-compose.yml"]["services"]["openwebui"]["ports"] = []
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "PORT 3000 is declared under [planes.frontend.ports]" in out


def test_a_profile_the_manifest_still_calls_pending_is_drift(mini_root):
    """The stale flag this item found: inference's `local` shipped, the manifest
    still said `pending = true`, and nothing compared the two."""
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["frontend/docker-compose.yml"]["profiles"] = ["gpu"]
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "PROFILE 'gpu'" in out and "pending = true" in out


def test_an_unrenderable_project_is_named_not_quietly_passed(mini_root):
    """CI has no OB1 submodule. A check that cannot run must say so."""
    (mini_root / "OB1" / "docker" / "docker-compose.yml").unlink()
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write")
    assert code == 0
    assert "NOT VERIFIED - open-brain:" in out
    rows = {r["container"]: r for group in generated(mini_root)["planes"].values() for r in group}
    # the recorded facts are carried through so the file is the same either way
    assert rows["openbrain-idea-refinery"]["profile"] == "idea-refinery"
    assert rows["openbrain-db"]["project"] == "open-brain"


def test_a_plane_with_no_project_refuses_so_a_new_plane_cannot_vanish(mini_root):
    projects = {k: v for k, v in CURATED_PROJECTS.items() if k != "coder"}
    curated_file(mini_root, projects=projects)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "plane 'coder' has no project" in out


def test_the_manual_plane_may_not_be_given_a_project(mini_root):
    projects = dict(CURATED_PROJECTS, portal={"plane": "portal"})
    curated_file(mini_root, projects=projects)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "is `manual`" in out and "AUTOMATED repair" in out


def test_inventory_needs_exactly_one_of_write_and_check(mini_root):
    curated_file(mini_root)
    code, out, _c = inventory(mini_root)
    assert code == stack.EXIT_REFUSED
    assert "--write" in out and "--check" in out


def test_the_header_comment_comes_from_the_sidecar(mini_root):
    curated_file(mini_root, comment=["GENERATED - regenerate with inventory --write"])
    inventory(mini_root, "--write")
    assert generated(mini_root)["_comment"] == ["GENERATED - regenerate with inventory --write"]


# --------------------------------------------------------------------------
# the real tree: the inventory that actually ships
# --------------------------------------------------------------------------


def test_the_shipped_sidecar_covers_every_non_manual_plane_and_only_those():
    manifest = stack.Manifest.load(REAL_MANIFEST)
    curated = json.loads((REPO_ROOT / stack.CURATED_REL).read_text(encoding="utf-8"))
    claimed = {spec["plane"] for spec in curated["projects"].values()}
    assert claimed == {p for p in manifest.order if not manifest.manual(p)}
    assert "portal" not in claimed


def test_every_shipped_row_carries_a_critical_flag_and_a_known_project():
    curated = json.loads((REPO_ROOT / stack.CURATED_REL).read_text(encoding="utf-8"))
    projects = set(curated["projects"])
    for group, rows in curated["planes"].items():
        for row in rows:
            assert "critical" in row, f"{group}/{row['container']} has no critical flag"
            assert row["project"] in projects, f"{group}/{row['container']} names an unknown project"


def test_the_shipped_inventory_is_the_shape_its_consumers_read():
    """status_check.py and stack-watchdog.ps1 read exactly these keys."""
    data = json.loads((REPO_ROOT / stack.INVENTORY_REL).read_text(encoding="utf-8"))
    assert set(data) == {"_comment", "projects", "planes"}
    for name, project in data["projects"].items():
        assert set(project) <= {"compose", "file", "env_file", "note", "profiles"}
        assert "file" in project and "env_file" in project and "compose" in project
        if project["file"] is None:
            assert name == "ai-stack", "only an anchor owns no services"
    for rows in data["planes"].values():
        for row in rows:
            assert set(row) <= set(stack.ROW_KEY_ORDER)
            assert isinstance(row["critical"], bool)


# --------------------------------------------------------------------------
# profiles: the driver must never start FEWER containers than a bare compose
# --------------------------------------------------------------------------


def test_the_profile_flags_each_plane_gets_are_pinned(root):
    """Today's set, from the REAL manifest. Changing it is a deliberate edit.

    ob1 is the one that matters, and this expectation MOVED when sl-ob1-profiles
    merged: from `[idea-refinery]` to `[idea-refinery, research]`. Not a
    regression - that item declared `idea-refinery requires research` (the drain's
    only engine is openbrain-research, so the profile the driver always passes
    starts a queue that can never empty without it) and `run_profiles` closes over
    `requires`.

    It changes NOTHING about what starts today: the pinned OB1 gitlink (5005197)
    declares only `idea-refinery`, so compose ignores `--profile research`, and
    `up --all` still brings up the same thirty containers. After the gitlink bumps
    it does matter - see `inventory --check`'s `[declared, not rendered]` block and
    finding F18 for the one-time `stack.py init --product research` the operator
    runs at that point.

    If a later item puts more OB1 services behind more profiles, this test is
    where "the shim now starts fewer containers" surfaces.
    """
    _code, out, _r = run(root, "up", "--all", "--dry-run")
    flags = {}
    for line in docker_lines(out):
        parts = line.split()
        plane = parts[parts.index("-f") + 1]
        flags[plane] = [parts[i + 1] for i, tok in enumerate(parts) if tok == "--profile"]
    assert flags["OB1/docker/docker-compose.yml"] == ["idea-refinery", "research"]
    assert all(not v for k, v in flags.items() if k != "OB1/docker/docker-compose.yml")


def test_a_planes_compose_profiles_are_unioned_into_any_flags_the_driver_passes(root):
    """`docker compose --profile X` REPLACES COMPOSE_PROFILES, it does not add.

    Measured on compose v5.3.0: the inference plane renders 8 services with the
    root .env alone (COMPOSE_PROFILES=local,...) and 4 with that same env plus
    one unrelated --profile flag. So whenever the driver passes any flag, it has
    to pass the env's profiles too, or it silently starts a subset.
    """
    env = root / ".env"
    env.write_text(env.read_text(encoding="utf-8") + "\nCOMPOSE_PROFILES=local,gpu\n", encoding="utf-8")
    manifest = stack.Manifest.load(root / stack.MANIFEST_NAME)
    state = stack.State.default()

    # ob1 gets its flags (idea-refinery is `default`, research comes in through
    # that profile's `requires`), but reads its OWN env file, which has no
    # COMPOSE_PROFILES - so nothing is unioned in.
    assert stack.effective_profiles(manifest, state, root, "ob1") == ["idea-refinery", "research"]
    # inference gets NO flag today, so compose reads COMPOSE_PROFILES itself.
    assert stack.effective_profiles(manifest, state, root, "inference") == []
    # ...but the moment anything enables a profile there, the env's come too.
    state.enable("inference", ["local"])
    assert set(stack.effective_profiles(manifest, state, root, "inference")) == {"local", "gpu"}


def test_a_profile_that_says_nothing_about_deployment_is_refused(mini_root):
    """The guard for the next item that puts running services behind a profile."""
    curated_file(mini_root)
    manifest_path = mini_root / stack.MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + '\n[planes.ob1.profiles.newslice]\ndescription = "services that run today"\n',
        encoding="utf-8",
    )
    code, out, _c = inventory(mini_root, "--check")
    assert code != 0
    assert "PROFILE 'newslice'" in out
    assert "`default = true`" in out and "`opt_in = true`" in out


def test_every_profile_the_real_manifest_declares_is_accounted_for():
    manifest = stack.Manifest.load(REAL_MANIFEST)
    unaccounted = {p: manifest.unaccounted_profiles(p) for p in manifest.order}
    assert not any(unaccounted.values()), unaccounted


def test_an_unknown_key_in_a_profile_table_is_tolerated(mini_root):
    """A profile table may grow keys the deployment gate does not know about.

    Written when `requires` was the unknown key - `sl-ob1-profiles` was in flight
    and had to be able to add it without this item's accounting gate refusing it.
    `requires` is real and validated now (that item merged), so the case moves to
    a key that IS unknown. The rule it pins is unchanged and is the reason the two
    items merged without touching each other here: the gate reads the three
    deployment flags and ignores every other key.
    """
    curated_file(mini_root)
    manifest_path = mini_root / stack.MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'description = "the idea-refinery services"',
            'description = "the idea-refinery services"\nowner       = "the digest chain"',
        ),
        encoding="utf-8",
    )
    manifest = stack.Manifest.load(manifest_path)
    assert manifest.default_profiles("ob1") == ["idea-refinery"]
    assert manifest.unaccounted_profiles("ob1") == []
    code, out, _c = inventory(mini_root, "--write")
    assert code == 0, out


def test_a_profile_requires_edge_is_closed_over_and_validated(mini_root):
    """`requires`, the key sl-ob1-profiles added, through THIS item's paths.

    Two halves: the closure reaches drive time (so `up` passes the prerequisite),
    and an edge naming a profile that does not exist is refused at load.
    """
    manifest_path = mini_root / stack.MANIFEST_NAME
    base = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        base.replace(
            'description = "the idea-refinery services"\ndefault     = true',
            'description = "the idea-refinery services"\ndefault     = true\n'
            'requires    = ["research"]\n\n[planes.ob1.profiles.research]\n'
            'description = "the research engine"\nopt_in      = true',
        ),
        encoding="utf-8",
    )
    manifest = stack.Manifest.load(manifest_path)
    state = stack.State.default()
    assert stack.effective_profiles(manifest, state, mini_root, "ob1") == ["idea-refinery", "research"]

    manifest_path.write_text(
        base.replace(
            'description = "the idea-refinery services"',
            'description = "the idea-refinery services"\nrequires    = ["nonesuch"]',
        ),
        encoding="utf-8",
    )
    with pytest.raises(stack.Refusal) as caught:
        stack.Manifest.load(manifest_path)
    assert "requires unknown profile 'nonesuch'" in str(caught.value)


def test_a_sidecar_row_may_omit_project_and_the_render_fills_it_in(mini_root):
    """SERVICE-LIFECYCLE.md step 8, executed.

    Step 8 says: add the row in the right plane group, with `critical` and any
    `host_health`. A tester followed that literally for a new service and the
    generator refused - the row had no `project`, and the render's answer was
    compared against `None`. The instruction was right and the generator was
    wrong; the render fills the field in now.
    """
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["memory"].append({"container": "mnemory-cloud-gateway-2", "critical": False})
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["memory/docker-compose.yml"]["services"]["mnemory-cloud-gateway-2"] = {
        "container_name": "mnemory-cloud-gateway-2"
    }
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code == 0, out
    written = {r["container"]: r for g in generated(mini_root)["planes"].values() for r in g}
    assert written["mnemory-cloud-gateway-2"]["project"] == "memory"


def test_a_row_in_no_render_at_all_must_name_its_project(mini_root):
    """The honest refusal that remains: nothing to derive it from."""
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["memory"].append({"container": "typo-svc", "critical": False})
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write")
    assert code != 0
    assert "NO `project` for typo-svc" in out
    assert "not declared in any plane's compose file" in out


# --------------------------------------------------------------------------
# a PINNED SUBMODULE may declare profiles its pinned commit does not carry
# --------------------------------------------------------------------------
#
# The sl-ob1-profiles / sl-driver-parity seam. That item made OB1's research,
# wiki and notebook profiles real on the OB1 BRANCH and declared them in the
# manifest; the gitlink still pins 5005197, whose compose declares only
# `idea-refinery`. Calling that drift would be the check lying, and dropping the
# declaration would lose something the watchdog and the coverage guard read.


def submodule_root(mini_root: Path) -> Path:
    """The mini tree, with ob1's compose declared as a submodule path."""
    (mini_root / ".gitmodules").write_text(
        '[submodule "OB1"]\n\tpath = OB1\n\turl = https://example.invalid/OB1.git\n',
        encoding="utf-8",
    )
    return mini_root


def test_only_a_pinned_submodule_gets_the_declared_not_rendered_treatment(mini_root):
    manifest = stack.Manifest.load(mini_root / stack.MANIFEST_NAME)
    assert stack.is_pinned_submodule(submodule_root(mini_root), "OB1/docker/docker-compose.yml")
    # ...and nothing else. A plane whose compose lives in this repo must agree
    # with the manifest, because both land in the same commit.
    for plane in ("inference", "frontend", "memory", "search", "coder", "agent-org"):
        assert not stack.is_pinned_submodule(mini_root, manifest.plane(plane)["compose"])


def test_a_profile_the_pinned_submodule_lacks_is_reported_not_failed(mini_root):
    submodule_root(mini_root)
    manifest_path = mini_root / stack.MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'description = "the idea-refinery services"\ndefault     = true',
            'description = "the idea-refinery services"\ndefault     = true\n\n'
            '[planes.ob1.profiles.wiki]\ndescription = "the wiki surface"\nopt_in      = true',
        ),
        encoding="utf-8",
    )
    rows = json.loads(json.dumps(CURATED_ROWS))
    # the row DECLARES a profile the pinned render does not carry
    rows["openbrain"].append({"container": "openbrain-wiki", "profile": "wiki",
                              "project": "open-brain", "critical": False})
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["OB1/docker/docker-compose.yml"]["services"]["openbrain-wiki"] = {
        "container_name": "openbrain-wiki"          # no `profiles` - the pinned commit
    }
    curated_file(mini_root, rows=rows)

    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code == 0, out
    assert "declared, not rendered" in out
    assert "PROFILE 'wiki'" in out
    assert "openbrain-wiki: `profile: wiki` is declared" in out
    # the operator's one-time step at the gitlink bump is named, not implied
    assert "init --product research" in out
    # and the declaration survives into the generated file
    written = {r["container"]: r for g in generated(mini_root)["planes"].values() for r in g}
    assert written["openbrain-wiki"]["profile"] == "wiki"


def test_the_same_mismatch_in_a_NON_submodule_plane_is_still_drift(mini_root):
    """The rule must not become a blanket excuse."""
    manifest_path = mini_root / stack.MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'description = "the llama.cpp upstreams; COMPOSE_PROFILES in the root .env turns it on"',
            'description = "a profile the compose file does not have"',
        ),
        encoding="utf-8",
    )
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["inference/docker-compose.yml"]["profiles"] = []
    render["inference/docker-compose.yml"]["services"]["llama-cpp-upstream"].pop("profiles")
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "PROFILE 'local' is declared" in out
    assert "declared, not rendered" not in out


def test_a_curated_profile_the_manifest_never_declared_is_still_drift(mini_root):
    """`[declared, not rendered]` covers a real declaration, not a typo."""
    submodule_root(mini_root)
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["openbrain"].append({"container": "openbrain-wiki", "profile": "wikki",
                              "project": "open-brain", "critical": False})
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["OB1/docker/docker-compose.yml"]["services"]["openbrain-wiki"] = {
        "container_name": "openbrain-wiki"
    }
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "STALE `profile` for openbrain-wiki" in out


def test_the_shipped_manifest_accounts_for_the_three_ob1_profiles_as_opt_in():
    """They are surfaces and engines a PRODUCT enables - never `default`.

    `default` survives --headless, so marking wiki or notebook default would make
    `enable open-brain --headless` a no-op for this plane, which is the entire
    point of the research product's surfaces split.
    """
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.default_profiles("ob1") == ["idea-refinery"]
    assert sorted(manifest.opt_in_profiles("ob1")) == ["notebook", "research", "wiki"]
    assert manifest.pending_profiles("ob1") == []
    assert manifest.unaccounted_profiles("ob1") == []
    # and the requires edge sl-ob1-profiles added is still enforced
    assert manifest.profile_requires("ob1", "idea-refinery") == ["research"]
    assert manifest.profile_closure("ob1", {"idea-refinery"}) == {"idea-refinery", "research"}


# --------------------------------------------------------------------------
# the frontend plane is profile-gated (sl-frontend-solo)
# --------------------------------------------------------------------------


def test_the_tailnet_probe_skips_itself_where_the_profile_is_not_deployed(root):
    """A FAIL line about a container that is not meant to exist is noise.

    Carried across from sl-frontend-solo's stack.ps1 when this sweep replaced it.
    The skip is NOT a failure and NOT a silent drop: it prints its own line and
    the sweep still exits 0.
    """
    code, out = sweep(FakeHost(frontend_services=["openwebui-stock", "openwebui-backup"]), root)
    assert code == 0
    assert "  [skip] frontend: 8 tailnet serve routes (no tailscale profile in this deployment)" in out
    names = [name for _s, name in probe_lines(out)]
    assert "frontend: 8 tailnet serve routes" not in names
    assert len(names) == 14          # the other fourteen all still ran
    assert "ALL HEALTH PROBES PASSED" in out


def test_the_skip_decision_reads_the_RENDER_not_the_env_file(root):
    host = FakeHost(frontend_services=["openwebui-stock", "openwebui-backup"])
    sweep(host, root)
    renders = [c for c in host.calls if c[:3] == ["docker", "compose", "-f"]]
    assert renders and renders[0][3] == "frontend/docker-compose.yml"
    assert renders[0][-2:] == ["config", "--services"]
    assert "--env-file" in renders[0]   # compose applies COMPOSE_PROFILES itself


def test_an_unreadable_frontend_render_probes_anyway_and_says_why(root):
    """Fail open. A checker that goes quiet on its own error is the failure mode."""
    code, out = sweep(FakeHost(frontend_services=[]), root)
    assert "[warn] the frontend plane rendered NOTHING" in out
    assert ("OK", "frontend: 8 tailnet serve routes") in probe_lines(out)
    assert code == 0


def test_a_running_tailscale_absent_from_the_render_probes_anyway_and_says_why(root):
    """The operator whose .env lost the frontend profiles from COMPOSE_PROFILES."""
    code, out = sweep(FakeHost(frontend_services=["openwebui-stock"], running=["tailscale"]), root)
    assert "[warn] tailscale is RUNNING but absent from the frontend render" in out
    assert ("OK", "frontend: 8 tailnet serve routes") in probe_lines(out)
    assert code == 0


def test_the_running_check_is_an_exact_name_match_not_a_substring(root):
    """`--filter name=tailscale` also matches stt-tts-tailscale."""
    code, out = sweep(
        FakeHost(frontend_services=["openwebui-stock"], running=["stt-tts-tailscale"]), root)
    assert "[skip] frontend: 8 tailnet serve routes" in out
    assert code == 0


def test_the_shipped_frontend_profiles_are_opt_in_and_tailscale_requires_gpu():
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.default_profiles("frontend") == []
    assert sorted(manifest.opt_in_profiles("frontend")) == ["gpu", "stock", "tailscale"]
    assert manifest.unaccounted_profiles("frontend") == []
    # prose in two descriptions, and a compose error if you ignore it
    assert manifest.profile_requires("frontend", "tailscale") == ["gpu"]
    assert manifest.profile_closure("frontend", {"tailscale"}) == {"tailscale", "gpu"}


# --------------------------------------------------------------------------
# mutually exclusive profiles: one container, two definitions
# --------------------------------------------------------------------------

EXCLUSIVE_RENDER = {
    # keyed by the frozenset of profiles the render was asked for
    frozenset(): {"openwebui-backup": {"container_name": "openwebui-backup"}},
    frozenset({"stock"}): {
        "openwebui-backup": {"container_name": "openwebui-backup"},
        "openwebui-stock": {"container_name": "openwebui", "profiles": ["stock"]},
    },
    frozenset({"gpu"}): {
        "openwebui-backup": {"container_name": "openwebui-backup"},
        "openwebui": {"container_name": "openwebui", "profiles": ["gpu"],
                      "ports": [{"published": "3000"}]},
    },
    frozenset({"tailscale", "gpu"}): {
        "openwebui-backup": {"container_name": "openwebui-backup"},
        "openwebui": {"container_name": "openwebui", "profiles": ["gpu"],
                      "ports": [{"published": "3000"}]},
        "tailscale": {"container_name": "tailscale", "profiles": ["tailscale"]},
    },
}


class ExclusiveCompose(FakeCompose):
    """frontend renders per profile-set; every other project as usual."""

    def __call__(self, cmd, cwd):
        compose = cmd[cmd.index("-f") + 1]
        if compose != "frontend/docker-compose.yml":
            return super().__call__(cmd, cwd)
        self.calls.append(list(cmd))
        asked = frozenset(cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--profile")
        if "--profiles" in cmd:
            return stack.CommandResult(0, "gpu\nstock\ntailscale", "")
        if asked not in EXCLUSIVE_RENDER:
            # what compose actually says when two definitions share a name
            return stack.CommandResult(
                1, "", 'services.openwebui: container name "openwebui" is already in use')
        return stack.CommandResult(
            0, json.dumps({"name": "frontend", "services": EXCLUSIVE_RENDER[asked]}), "")


def exclusive_mini(mini_root: Path) -> Path:
    path = mini_root / stack.MANIFEST_NAME
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '[planes.frontend.profiles.gpu]\ndescription = "the CUDA image and the device reservation"\n'
            'pending     = true',
            '[planes.frontend.profiles.stock]\ndescription = "the stock image"\nopt_in      = true\n\n'
            '[planes.frontend.profiles.gpu]\ndescription = "the CUDA image"\nopt_in      = true\n\n'
            '[planes.frontend.profiles.tailscale]\ndescription = "the netns companion"\n'
            'requires    = ["gpu"]\nopt_in      = true',
        ),
        encoding="utf-8",
    )
    return mini_root


def test_mutually_exclusive_profiles_fall_back_to_a_render_per_closure(mini_root):
    exclusive_mini(mini_root)
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["core"] = [r for r in rows["core"] if r["container"] != "openwebui"] + [
        {"container": "openwebui", "project": "frontend", "critical": True},
        {"container": "openwebui-backup", "project": "frontend", "critical": False},
        {"container": "tailscale", "profile": "tailscale", "project": "frontend", "critical": True},
    ]
    curated_file(mini_root, rows=rows)
    compose = ExclusiveCompose()
    code, out, _c = inventory(mini_root, "--write", compose=compose)
    assert code == 0, out

    # `tailscale` was never rendered ALONE - its `requires` closure came too,
    # because `--profile tailscale` on its own is not a deployment.
    asked = [frozenset(c[i + 1] for i, t in enumerate(c) if t == "--profile")
             for c in compose.calls if "--format" in c]
    assert frozenset({"tailscale", "gpu"}) in asked
    assert frozenset({"tailscale"}) not in asked

    written = {r["container"]: r for g in generated(mini_root)["planes"].values() for r in g}
    # one container, two definitions -> neither key is derivable, and it says so
    assert "service" not in written["openwebui"] and "profile" not in written["openwebui"]
    assert "mutually exclusive - openwebui: produced by 2" in out
    # ...while an unambiguous profiled row still gets its profile derived
    assert written["tailscale"]["profile"] == "tailscale"


def test_a_render_that_fails_for_any_other_reason_still_refuses(mini_root):
    """The fallback is for exclusivity, not a blanket retry."""
    exclusive_mini(mini_root)
    curated_file(mini_root)

    class Broken(ExclusiveCompose):
        def __call__(self, cmd, cwd):
            if cmd[cmd.index("-f") + 1] == "frontend/docker-compose.yml" and "--format" in cmd:
                return stack.CommandResult(1, "", "yaml: line 3: mapping values are not allowed")
            return super().__call__(cmd, cwd)

    code, out, _c = inventory(mini_root, "--write", compose=Broken())
    assert code != 0
    assert "mapping values are not allowed" in out


def test_a_bogus_service_on_a_declared_not_rendered_row_is_still_stale(mini_root):
    """The F-T14 regression: the gitlink bucket must exempt ONE key, not three.

    A row whose `profile` is accepted as a declaration had its `service` and
    `profiles` skipped as well, so a bogus `service` was green on a host with the
    submodule and red in CI without it. A check whose answer depends on which
    machine runs it is not a check.
    """
    submodule_root(mini_root)
    manifest_path = mini_root / stack.MANIFEST_NAME
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'description = "the idea-refinery services"\ndefault     = true',
            'description = "the idea-refinery services"\ndefault     = true\n\n'
            '[planes.ob1.profiles.wiki]\ndescription = "the wiki surface"\nopt_in      = true',
        ),
        encoding="utf-8",
    )
    rows = json.loads(json.dumps(CURATED_ROWS))
    rows["openbrain"].append({"container": "openbrain-wiki", "profile": "wiki",
                              "service": "not-the-service-key",
                              "project": "open-brain", "critical": False})
    render = json.loads(json.dumps(FIXTURE_RENDER))
    render["OB1/docker/docker-compose.yml"]["services"]["openbrain-wiki"] = {
        "container_name": "openbrain-wiki"
    }
    curated_file(mini_root, rows=rows)
    code, out, _c = inventory(mini_root, "--write", compose=FakeCompose(render))
    assert code != 0
    assert "STALE `service` for openbrain-wiki" in out
    # ...and the profile is still accepted as a declaration, not called stale
    assert "STALE `profile` for openbrain-wiki" not in out
    assert "declared, not rendered" in out
