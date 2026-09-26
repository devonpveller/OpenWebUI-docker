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
import re
import shutil
import sys
import tomllib
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


def _make_host_path(path: Path, spec: dict) -> None:
    """A usable host path: the directory plus everything its `contains` names."""
    path.mkdir(parents=True, exist_ok=True)
    for name in spec.get("contains", []):
        if name == ".git":
            (path / name).mkdir(exist_ok=True)
        else:
            (path / name).write_text("# placeholder\n", encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A throwaway repo root: the real manifest, placeholder compose + env files.

    The root is a CHILD of tmp_path so the manifest's `host_paths` (memory's
    sibling ../mnemory) land inside this test's own directory; each one is
    created, and a test that wants it absent removes it.
    """
    repo = tmp_path / "ai-stack"
    repo.mkdir()
    for plane in stack.Manifest.load(REAL_MANIFEST).planes.values():
        for spec in plane.get("host_paths", []):
            _make_host_path(repo / Path(spec["path"]), spec)
    tmp_path = repo
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


@pytest.fixture(autouse=True)
def _powershell_present(monkeypatch):
    """Hermetic: whether this machine has PowerShell is not what a test is about.

    `health` runs the owui-drift probe through `powershell` on Windows and `pwsh`
    elsewhere, and SKIPS it when neither exists. Pin "present" for every test so
    the suite answers the same on Windows and on a Linux runner; the skip has its
    own test, which overrides this.
    """
    # raising=False: the same file must RUN against a driver that predates the
    # function, so the base-red reproduction fails on the behaviour, not on setup.
    monkeypatch.setattr(stack, "powershell_command", lambda: ("powershell",), raising=False)
    # Hermetic means no daemon, ever: a verb that falls back to the real capture
    # (doctor's network check, the shipped-placeholder render) gets "no docker"
    # instead of reaching this machine's daemon. A test that wants docker passes
    # a FakeDaemon/FakeHost capture.
    monkeypatch.setattr(stack, "subprocess_capture",
                        lambda cmd, cwd: stack.CommandResult(127, "", "hermetic test: no docker"))


def run(root: Path, *args, runner=None, capture=None):
    """(exit code, stdout, recorder) for one stack.py invocation."""
    out = io.StringIO()
    recorder = runner if runner is not None else Recorder()
    code = stack.main(["--root", str(root), *args], runner=recorder, stdout=out, capture=capture)
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
    code, out, recorder = run(root, "up", "--dry-run", capture=_no_capture)
    assert code == 0
    assert docker_lines(out) == [
        # the anchor is ENSURED from its render, never `up`-ed (ac-front-door)
        "docker compose -f docker-compose.yml config --no-interpolate --format json",
        "docker compose -f frontend/docker-compose.yml up -d",
    ]
    assert recorder.commands == []  # a dry-run that starts a container FAILS


def test_up_without_dry_run_goes_through_the_runner(root):
    daemon = FakeDaemon()
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    started = [" ".join(c) for c in daemon.streamed]
    assert started == [line for line in docker_lines(out) if " config " not in line]
    assert started[-1] == "docker compose -f frontend/docker-compose.yml up -d"


def test_up_stops_at_the_first_failing_plane(root):
    run(root, "init", "--planes", "inference,frontend", "--force")
    failing = ("docker", "compose", "-f", "inference/docker-compose.yml", "up", "-d")
    daemon = FakeDaemon(exit_codes={failing: 17})
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "exited 17" in out
    assert not any("frontend/docker-compose.yml" in " ".join(c) for c in daemon.commands)


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


def test_enable_the_memory_plane_refuses_and_names_inference_and_the_remedy(root):
    """The PLANE refuses on an unmet requires; the remedy enables the inference PLANE.

    `--plane` since ac-driver-products: a bare `memory` is the product now. The
    remedy says `--plane inference` because a bare `inference` would be the
    product, whose `local` profile is more than this refusal asked for.
    """
    code, out, _ = run(root, "enable", "--plane", "memory")
    assert code == stack.EXIT_REFUSED
    assert "inference" in out
    assert "stack.py enable --plane inference" in out
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
    assert "Set them in search/.env" in out
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
        "docker compose -f frontend/docker-compose.yml restart"
    ]


def test_disable_refuses_while_something_still_requires_the_plane(root):
    # `--plane`: a bare `inference` is the product now, and the product was never enabled here
    run(root, "init", "--planes", "inference,frontend,memory")
    code, out, _ = run(root, "disable", "--plane", "inference")
    assert code == stack.EXIT_REFUSED
    assert "memory" in out
    run(root, "disable", "--plane", "memory")
    code, _, _ = run(root, "disable", "--plane", "inference")
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
    assert docker_lines(out)[0] == "docker compose -f docker-compose.yml config --no-interpolate --format json"


def test_disable_says_so_when_a_name_is_ambiguous_too(root):
    """The note is worth most on the destructive half of the pair."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "memory")
    code, out, _ = run(root, "disable", "memory")
    assert code == 0
    assert "names both a plane and a product; acting on the PRODUCT" in out
    assert "--plane memory" in out
    assert set(state_of(root)["planes"]) == {"frontend"}


def test_a_product_name_wins_over_a_plane_of_the_same_name(root):
    """`enable memory` is the PRODUCT (orchestrator decision, ac-driver-products): it brings inference."""
    code, out, _ = run(root, "enable", "memory")
    assert code == 0, out
    assert "names both a plane and a product; acting on the PRODUCT" in out
    # frontend is there because the absent state file defaults to it, not because
    # the product asked for it.
    assert set(state_of(root)["planes"]) == {"memory", "inference", "frontend"}
    code, out, _ = run(root, "enable", "--plane", "memory")
    assert code == 0
    assert "acting on the PLANE alone" in out


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


def test_no_plane_passes_an_env_file_and_each_reads_its_own(root):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    assert manifest.env_file("ob1") is None
    assert manifest.env_path(REPO_ROOT, "ob1") == REPO_ROOT / "OB1" / "docker" / ".env"
    assert manifest.env_file("agent-org") is None
    assert manifest.env_path(REPO_ROOT, "agent-org") == REPO_ROOT / "agent-org" / "docker" / ".env"
    assert manifest.env_file("frontend") is None
    assert manifest.env_path(REPO_ROOT, "frontend") == REPO_ROOT / "frontend" / ".env"
    # sl-env-split: the anchor is the ONLY plane whose env file is the repo root
    # one, and it gets there the same way - its project directory IS the root.
    assert all(manifest.env_file(p) is None for p in manifest.order)
    assert manifest.env_path(REPO_ROOT, "anchor") == REPO_ROOT / ".env"


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
        "docker compose -f inference/docker-compose.yml ps",
        "docker compose -f frontend/docker-compose.yml ps",
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
# health - the fifteen probes stack.ps1 ran, one for one, plus the sixteenth
# (inference serving depth) that has no .ps1 ancestor
# --------------------------------------------------------------------------
#
# The point of pinning the NAMES is parity. A probe dropped, merged into a
# neighbour or renamed shows up here as a list mismatch, which is exactly the
# anchor criterion ("a probe dropped, merged or weakened FAILS").

PS1_PROBES = [
    "0 unhealthy containers (found: )",
    "anchor: ai-stack_llm-net exists and is internal",
    "inference: llm-gateway liveliness",
    # sl-recovery-backups (2026-09-21) added the sixteenth: liveliness answers
    # 200 with an EMPTY model store, and did for thirty hours.
    "inference: serving depth: qwen36-27b resident on llama-cpp-upstream (/running)",
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
        self.network = broken.get("network", "ai-stack_llm-net true")
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
        # sl-recovery-backups: the inference serving-depth probe. Defaults are a
        # stocked model store with one model already resident - the state this
        # host is in when it is working.
        self.gguf_count = broken.get("gguf_count", "3")
        self.gguf_code = broken.get("gguf_code", 0)
        self.models_bind = broken.get("models_bind", "/srv/models/gguf")
        self.llama_running = broken.get(
            "llama_running",
            '{"running":[{"model":"qwen36-27b","state":"ready"}]}',
        )
        self.landing = broken.get("landing", "")
        # ac-ci: the owui-drift probe first counts deployed tool/function/skill rows.
        # Default = this host: plugins ARE deployed, so the drift check is real.
        self.plugin_count = broken.get("plugin_count", "16")
        self.plugin_census_code = broken.get("plugin_census_code", 0)
        self.calls: list[list[str]] = []

    def capture(self, cmd, cwd):
        self.calls.append(list(cmd))
        if cmd[0] in ("powershell", "pwsh"):
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
        if cmd[:2] == ["docker", "inspect"]:
            return stack.CommandResult(0, self.models_bind, "")
        if cmd[:2] == ["docker", "exec"]:
            container = cmd[2]
            if container == "tailscale":
                return stack.CommandResult(0, self.serve_routes, "")
            if container == "llama-cpp-upstream":
                # Two different reads of the same container: the .gguf census
                # (sh -c find) and llama-swap's /running (curl).
                if "curl" in cmd:
                    return stack.CommandResult(0, self.llama_running, "")
                return stack.CommandResult(
                    self.gguf_code, self.gguf_count,
                    "" if self.gguf_code == 0 else "Error: No such container: llama-cpp-upstream",
                )
            # Matched on the SCRIPT, not on the container: llm-gateway also
            # carries the liveliness probe's `python -c`, and swallowing that one
            # here would let exec_codes stop breaking it.
            if cmd[-1] == stack._LANDING_COMPLETION:
                return stack.CommandResult(0, self.landing, "")
            if cmd[-1] == stack._OWUI_PLUGIN_CENSUS:
                return stack.CommandResult(self.plugin_census_code, self.plugin_count, "")
            return stack.CommandResult(self.exec_codes.get(container, 0), "", "")
        raise AssertionError(f"unscripted docker call: {cmd}")

    def http(self, url, timeout=8):
        status = self.http_status.get(url, 200)
        body = self.search_body if url.endswith("8085/health") else "{}"
        return stack.HttpResult(status, body)


def sweep(host: FakeHost, root: Path, planes: str | None = ALL_PLANES_BUT_ANCHOR):
    """One `health` run. `planes` is written as the state first (None = leave the state alone).

    `health` probes only the planes the machine ENABLES (ac-front-door), so the
    tests about individual probes run against a state that enables all of them -
    which is what this host's .stack/state.json does, agent-org aside.
    """
    if planes is not None:
        run(root, "init", "--planes", planes, "--force")
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


# --- the inference serving-depth probe (sl-recovery-backups, 2026-09-21) ----
# Fifteen probes stayed green for thirty hours while chat was dead: the upstream
# was recreated with an EMPTY /models bind, llama-swap answered /health without
# loading anything, and the first real completion returned 500. Each of these
# pins one branch of the probe that exists so that cannot repeat.


def depth_line(out: str) -> tuple[str, str]:
    """(state, label) of the serving-depth probe line."""
    for state, name in probe_lines(out):
        if name.startswith("inference: serving depth"):
            return state, name
    raise AssertionError(f"no serving-depth probe in:\n{out}")


def test_serving_depth_passes_and_names_the_resident_model(root):
    _code, out = sweep(FakeHost(), root)
    state, label = depth_line(out)
    assert state == "OK"
    assert "qwen36-27b" in label


def test_serving_depth_fails_on_an_empty_models_mount_and_names_the_bind(root):
    """THE REGRESSION. A probe that passes here has not done its job."""
    host = FakeHost(gguf_count="0", models_bind=r"D:\My Stack\data\models\gguf")
    code, out = sweep(host, root)
    state, label = depth_line(out)
    assert state == "FAIL"
    assert "NO .gguf files" in label
    assert r"D:\My Stack\data\models\gguf" in label, "the label must name the bind to fix"
    assert "LM_MODELS_DIR" in label
    assert code == 1, "one failed probe, one exit code"
    # And it must NOT have gone on to spend a cold-load timeout on a store that
    # provably has nothing to load.
    assert not any(cmd[-1] == stack._LANDING_COMPLETION for cmd in host.calls)


def test_serving_depth_with_nothing_resident_makes_one_completion(root):
    host = FakeHost(
        llama_running='{"running":[]}',
        landing="OK a 3-token completion through the gateway returned 200 in 257s (qwen36-27b loaded)",
    )
    _code, out = sweep(host, root)
    state, label = depth_line(out)
    assert state == "OK"
    assert "nothing was resident" in label
    assert "257s" in label
    landings = [cmd for cmd in host.calls if cmd[-1] == stack._LANDING_COMPLETION]
    assert len(landings) == 1, "ONE completion, not one per retry"
    assert landings[0][:3] == ["docker", "exec", "llm-gateway"], "through the gateway, never the upstream"


def test_serving_depth_fails_when_the_landing_completion_does(root):
    host = FakeHost(
        llama_running='{"running":[]}',
        landing="FAIL the gateway answered HTTP 500: upstream command exited prematurely",
    )
    code, out = sweep(host, root)
    state, label = depth_line(out)
    assert state == "FAIL"
    assert "upstream command exited prematurely" in label
    assert code == 1


def test_serving_depth_fails_when_the_upstream_is_not_running(root):
    host = FakeHost(gguf_code=1, gguf_count="")
    code, out = sweep(host, root)
    state, label = depth_line(out)
    assert state == "FAIL"
    assert "No such container" in label or "upstream running" in label
    assert code == 1


def test_serving_depth_refuses_without_a_caller_key_rather_than_guessing(root):
    """An unmigrated host gets a sentence naming the file, not a 401 to decode."""
    # The state first: `init` itself refuses once the key is gone.
    run(root, "init", "--planes", ALL_PLANES_BUT_ANCHOR, "--force")
    env = root / "inference" / ".env"
    env.write_text(
        "\n".join(line for line in env.read_text(encoding="utf-8").splitlines()
                  if not line.startswith("LITELLM_MASTER_KEY=")) + "\n",
        encoding="utf-8",
    )
    host = FakeHost(llama_running='{"running":[]}')
    code, out = sweep(host, root, planes=None)
    state, label = depth_line(out)
    assert state == "FAIL"
    assert "LITELLM_MASTER_KEY" in label and "inference/.env" in label
    assert code == 1
    assert not any(cmd[-1] == stack._LANDING_COMPLETION for cmd in host.calls)


def test_the_landing_completion_never_prints_the_key(root):
    """The script reads the container's own env; no value crosses this process."""
    assert "LITELLM_MASTER_KEY" in stack._LANDING_COMPLETION
    assert 'os.environ.get("LITELLM_MASTER_KEY"' in stack._LANDING_COMPLETION
    host = FakeHost(llama_running='{"running":[]}', landing="OK done in 1s (m loaded)")
    sweep(host, root)
    for cmd in host.calls:
        assert "value-for-LITELLM_MASTER_KEY" not in " ".join(cmd), \
            "the key value must never reach an argv"


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
    assert len(rows) == 16          # sixteen since the serving-depth probe
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


def _drift_script_ran(host: FakeHost) -> bool:
    return any(call[0] in ("powershell", "pwsh") for call in host.calls)


ZERO_PLUGINS_WARNING = (
    "  [warn] frontend: owui/ manifest drift NOT CHECKED - 0 plugins deployed in this Open WebUI: "
    "a fresh install, or this host's plugins were wiped; paste them per frontend/owui/README.md "
    "(\"Redeploy mechanism\")"
)


def test_zero_deployed_plugins_is_a_visible_warning_not_a_fail_and_not_a_silent_skip(root):
    """ac-ci F6/X3: on a fresh Open WebUI the drift check REFUSED on empty tables and
    `health` failed on a clean quickstart wherever PowerShell exists. Zero rows cannot be
    told apart from a wiped host, so it is a [warn] naming both readings - never [skip],
    never [OK] - health does not fail on it, and the drift script is not asked."""
    host = FakeHost(plugin_count="0", drift_stdout="REFUSED",
                    drift_stderr="REFUSED: the query ... returned no readable rows.")
    code, out = sweep(host, root, planes="frontend")
    assert code == 0
    assert ZERO_PLUGINS_WARNING in out.splitlines()
    assert "[skip] frontend: owui/ manifest drift" not in out
    assert not any("owui/ manifest" in name for _s, name in probe_lines(out))
    assert not _drift_script_ran(host)


def test_a_deployed_host_whose_plugins_were_wiped_gets_the_same_warning(root):
    """The residual ambiguity, pinned: a host that HAD plugins and now counts zero reads
    exactly like a fresh install. The warning says so in words; it does not fail."""
    host = FakeHost(plugin_count="0")        # every other default is this (deployed) host
    code, out = sweep(host, root, planes="frontend")
    assert "or this host's plugins were wiped" in out
    assert code == 0


def test_with_plugins_deployed_a_drifted_row_still_fails(root):
    """The skip must not weaken the check where plugins ARE deployed (this host)."""
    host = FakeHost(plugin_count="5", drift_stdout="2")
    code, out = sweep(host, root, planes="frontend")
    assert code == 1
    assert ("FAIL", "frontend: owui/ manifest rows drifted from live webui.db: 2") in probe_lines(out)
    assert _drift_script_ran(host)


def test_a_partial_deployment_that_refuses_still_fails(root):
    """One row deployed is not a fresh install: a refusal stays a FAIL."""
    host = FakeHost(plugin_count="1", drift_stdout="REFUSED",
                    drift_stderr="REFUSED: the manifest names an owui_id with no row.")
    code, out = sweep(host, root, planes="frontend")
    assert code == 1
    assert ("FAIL", "frontend: owui/ manifest rows drifted from live webui.db: "
            "REFUSED - the manifest names an owui_id with no row.") in probe_lines(out)


@pytest.mark.parametrize("census_code, census_out", [(1, ""), (0, ""), (0, "no such table: tool")])
def test_a_census_that_cannot_be_read_is_not_zero_and_the_real_check_runs(root, census_code, census_out):
    """An unreadable count must never turn into a skip: the drift check runs and decides."""
    host = FakeHost(plugin_count=census_out, plugin_census_code=census_code, drift_stdout="REFUSED",
                    drift_stderr="REFUSED: container 'openwebui' was not found by docker inspect.")
    code, out = sweep(host, root, planes="frontend")
    assert code == 1
    assert "  [skip] frontend: owui/ manifest drift" not in out
    assert _drift_script_ran(host)


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


def test_stats_on_windows_still_hands_off_to_the_powershell_report(root, monkeypatch):
    """The operator's host keeps the report it has always run (ac-ops-portable: "on Windows it
    may keep delegating"). Pinned to Windows, so a Linux runner answers the same."""
    monkeypatch.setattr(stack, "WINDOWS", True)
    script = root / "scripts" / "stack" / "stack-stats.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# placeholder\n", encoding="utf-8")
    code, out, recorder = run(root, "stats")
    assert code == 0
    assert len(recorder.commands) == 1
    assert recorder.commands[0][:1] == ["powershell"]
    assert recorder.commands[0][-1].endswith("stack-stats.ps1")


# (test_stats_refuses_off_windows_rather_than_printing_nothing was retired by
# ac-ops-portable: `stats` no longer refuses off Windows, it reports. Its point -
# never print nothing and exit 0 - is kept by the stats tests at the end of this
# file, which pin real numbers and an explicit "NOT ENABLED" line.)


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
    # ac-followups N9: the anchor's networks are ENSURED first (its render line is
    # printed; under --dry-run nothing is rendered), and no other plane is started.
    assert docker_lines(out) == ["docker compose -f docker-compose.yml config --no-interpolate --format json",
                                 "docker compose -f coder/docker-compose.yml up -d"]
    assert "# note: coder requires inference; this starts only coder" in out


def test_a_plane_and_all_together_is_refused(root):
    code, out, _r = run(root, "up", "coder", "--all", "--dry-run")
    assert code == stack.EXIT_REFUSED
    assert "not both" in out


def test_status_reports_the_enabled_planes_and_does_not_pull_in_the_anchor(root):
    run(root, "init", "--planes", "memory")
    _code, _out, recorder = run(root, "status")
    assert recorder.lines == ["docker compose -f memory/docker-compose.yml ps"]


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
implicit = true
requires = []
[planes.anchor.ports]

[planes.inference]
compose  = "inference/docker-compose.yml"
requires = ["anchor"]
[planes.inference.ports]
"8081" = "llama-cpp-upstream"
[planes.inference.profiles.local]
description = "the llama.cpp upstreams; COMPOSE_PROFILES in inference/.env turns it on"
opt_in      = true

[planes.frontend]
compose  = "frontend/docker-compose.yml"
requires = ["anchor"]
[planes.frontend.ports]
"3000" = "openwebui"
[planes.frontend.profiles.gpu]
description = "the CUDA image and the device reservation"
pending     = true

[planes.memory]
compose  = "memory/docker-compose.yml"
requires = ["anchor"]
[planes.memory.ports]
"8060" = "mnemory-cloud-gateway"

[planes.search]
compose  = "search/docker-compose.yml"
requires = ["anchor"]
[planes.search.ports]
"8085" = "gateway"

[planes.coder]
compose  = "coder/docker-compose.yml"
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
        "compose": "docker compose -f inference/docker-compose.yml",
        "file": "inference/docker-compose.yml", "env_file": None,
    }
    # NO plane carries an --env-file since sl-env-split: every compose project
    # loads the .env in its own project directory.
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

    It changed nothing about what started while the gitlink pinned 5005197, whose
    compose declared only `idea-refinery`: compose ignored `--profile research` and
    `up --all` brought up the same thirty containers. sl-ob1-gitlink bumped the
    gitlink to fe3e045 on 2026-09-20 and it DOES matter now - measured there, this
    two-profile set renders 23 of the 30, so the operator declares the other two
    once (`stack.py init --product research --force`, or COMPOSE_PROFILES in
    OB1/docker/.env). This test still asserts the driver's DEFAULT closure, not the
    operator's deployment set.

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

    Measured on compose v5.3.0: the inference plane renders 8 services with its
    own env file alone (COMPOSE_PROFILES=local) and 4 with that same env plus
    one unrelated --profile flag. So whenever the driver passes any flag, it has
    to pass the env's profiles too, or it silently starts a subset.

    The env file is the PLANE's own since sl-env-split (D17), which is what
    stops the frontend's `gpu` from ever reaching the inference render.
    """
    env = root / "inference" / ".env"
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
# manifest while the gitlink still pinned 5005197, whose compose declared only
# `idea-refinery`. Calling that drift would have been the check lying, and dropping
# the declaration would have lost something the watchdog and the coverage guard
# read. sl-ob1-gitlink closed that gap on 2026-09-20 (gitlink -> fe3e045), so no
# plane is in this state today - the tests below use a synthetic mini tree, not
# ob1, precisely so the mechanism stays covered once no real plane exercises it.


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
            'description = "the llama.cpp upstreams; COMPOSE_PROFILES in inference/.env turns it on"',
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
    assert len(names) == 15          # the other fifteen all still ran
    assert "ALL HEALTH PROBES PASSED" in out


def test_the_skip_decision_reads_the_RENDER_not_the_env_file(root):
    host = FakeHost(frontend_services=["openwebui-stock", "openwebui-backup"])
    sweep(host, root)
    renders = [c for c in host.calls if c[:3] == ["docker", "compose", "-f"]]
    assert renders and renders[0][3] == "frontend/docker-compose.yml"
    assert renders[0][-2:] == ["config", "--services"]
    assert "--env-file" not in renders[0]   # frontend/.env loads natively; compose
                                           # applies COMPOSE_PROFILES from it itself


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


# --------------------------------------------------------------------------
# a gitignored env file that is absent is a GAP, not drift
# --------------------------------------------------------------------------


def test_a_missing_gitignored_env_is_a_printed_gap_not_a_refusal(mini_root):
    """agent-org's compose carries service-level `env_file:` entries, which
    `docker compose config` STATS whatever --env-file the CLI was given. On any
    machine without agent-org/docker/.env the render exits 1; the generator used
    to refuse, and the pre-commit hook then printed
    "INVENTORY DRIFT - regenerate with --write" - wrong twice, because nothing
    had drifted and `--write` refuses the same way, so the remedy it named could
    not work. Degrade like the coverage guard: name the project and the file,
    skip its rows, exit 0.
    """
    (mini_root / "agent-org" / "docker" / ".env").unlink()
    curated_file(mini_root)
    code, out, compose = inventory(mini_root, "--write")
    assert code == 0, out
    assert "NOT VERIFIED - agent-org: agent-org/docker/.env is absent" in out
    assert "gitignored, so this is expected off the deploy host" in out
    # and it never reached for docker at all on that project
    assert not any("agent-org" in " ".join(c) for c in compose.calls)


def test_the_rows_of_a_skipped_project_are_still_written_from_the_sidecar(mini_root):
    """Skipping the RENDER must not drop the rows - the watchdog reads them."""
    (mini_root / "agent-org" / "docker" / ".env").unlink()
    curated_file(mini_root)
    code, out, _c = inventory(mini_root, "--write")
    assert code == 0, out
    written = {r["container"]: r for g in generated(mini_root)["planes"].values() for r in g}
    assert written["mattermost"]["project"] == "agent-org"
    assert written["ao-worker-1"]["profile"] == "workers"     # carried, unverified


def test_a_compose_file_that_exists_and_will_not_render_is_still_a_refusal(mini_root):
    """The exemption is ONE named condition, not a blanket retry."""
    curated_file(mini_root)

    class Broken(FakeCompose):
        def __call__(self, cmd, cwd):
            if cmd[cmd.index("-f") + 1] == "memory/docker-compose.yml":
                return stack.CommandResult(
                    1, "", "yaml: while parsing a flow node at line 146: did not find expected node content")
            return super().__call__(cmd, cwd)

    code, out, _c = inventory(mini_root, "--check", compose=Broken())
    assert code != 0
    assert "did not find expected node content" in out
    assert "NOT VERIFIED" not in out



# --------------------------------------------------------------------------
# ac-front-door: a REAL up gets past the anchor
# --------------------------------------------------------------------------
#
# Every test above that drives `up` used --dry-run or a Recorder that answers 0
# to everything, so none of them could see what a real `stack.py up` did at
# 3c3ff75: the first command it ran was `docker compose -f docker-compose.yml
# up -d` against a project with ZERO services, compose exited 1 ("no service
# selected"), and `_drive` stopped there. FakeDaemon answers the way the real CLI
# does on exactly the points that bug lived on - measured on compose v2.33.0 in
# a DinD and v5.3.0 on the Windows host, 2026-09-25.


def _no_capture(cmd, cwd):
    raise AssertionError(f"a dry run must not read from docker either: {cmd}")


ANCHOR_NETWORKS = {
    "app-net": {"driver": "bridge", "name": "ai-stack_app-net"},
    "default": {"driver": "bridge", "name": "ai-stack_default"},
    "llm-net": {"internal": True, "name": "ai-stack_llm-net"},
}


class FakeDaemon:
    """A docker CLI + daemon that behaves like the real one where the anchor bug lived.

    Both seams land here: `runner` (what the driver streams) and `capture` (what
    it reads back). Copied behaviours, each measured:
      * `docker compose -f docker-compose.yml up ...` on the anchor exits 1 with
        "no service selected" - it declares networks and no service;
      * its `config --format json` PRUNES the unused networks unless
        `--no-interpolate` is passed (so dropping the flag creates nothing);
      * `docker network inspect <missing>` exits 1; `network create` of a name
        that exists exits 1;
      * a plane's `up -d` fails while an ai-stack_* network it attaches to
        externally is missing (true of every plane under its operator profiles;
        the frontend's `stock` profile needs none, a simplification that makes
        this fake STRICTER, not looser).
    Any docker call it has not been taught is an AssertionError, never a silent 0.
    """

    def __init__(self, networks=None, exit_codes=None, render=None, plane_renders=None, gpu=True):
        self.networks = {k: {"driver": "bridge", "attachable": False, "options": {}, "labels": {}, **v}
                         for k, v in (networks or {}).items()}
        self.exit_codes = exit_codes or {}
        self.render = render if render is not None else {"name": "ai-stack", "networks": ANCHOR_NETWORKS}
        # compose file -> its `config --no-interpolate` render (the shipped-placeholder check)
        self.plane_renders = plane_renders or {}
        # `docker info`: an `nvidia` runtime (gpu=True, a GPU host) or runc only (a GPU-less DinD)
        self.gpu = gpu
        self.commands: list[list[str]] = []   # everything, in order
        self.streamed: list[list[str]] = []   # what went through the runner
        self.created: list[str] = []
        self.mutations: list[list[str]] = []  # rm / connect / disconnect / prune

    def runner(self, cmd, cwd):
        self.streamed.append(list(cmd))
        return self._do(cmd).code

    def capture(self, cmd, cwd):
        return self._do(cmd)

    def _do(self, cmd):
        self.commands.append(list(cmd))
        if tuple(cmd) in self.exit_codes:
            return stack.CommandResult(self.exit_codes[tuple(cmd)], "", "scripted failure")
        args = list(cmd[1:])
        if args[:1] == ["--context"]:
            args = args[2:]
        if args == ["compose", "version"]:
            return stack.CommandResult(0, "Docker Compose version v5.3.0", "")
        if args[:1] == ["info"]:
            runtimes = {"runc": {}, "io.containerd.runc.v2": {}}
            if self.gpu:
                runtimes["nvidia"] = {"path": "nvidia-container-runtime"}
            return stack.CommandResult(0, json.dumps({"Runtimes": runtimes, "DiscoveredDevices": None}), "")
        if args[:2] == ["compose", "-f"]:
            compose_file, rest = args[2], args[3:]
            while rest[:1] == ["--profile"]:
                rest = rest[2:]
            if rest[:1] == ["config"] and compose_file != "docker-compose.yml":
                if compose_file not in self.plane_renders:
                    raise AssertionError(f"FakeDaemon has no render for {compose_file}")
                return stack.CommandResult(0, json.dumps(self.plane_renders[compose_file]), "")
            if rest[:1] == ["config"]:
                data = dict(self.render)
                if "--no-interpolate" not in rest:
                    data = {"name": data.get("name"), "services": {}}
                return stack.CommandResult(0, json.dumps(data), "")
            if compose_file == "docker-compose.yml":
                if rest[:1] == ["up"]:
                    return stack.CommandResult(1, "", "no service selected")
                return stack.CommandResult(0, "", "")
            if rest[:1] == ["up"]:
                missing = [n["name"] for n in ANCHOR_NETWORKS.values() if n["name"] not in self.networks]
                if missing:
                    return stack.CommandResult(
                        1, "", f"network {missing[0]} declared as external, but could not be found")
            return stack.CommandResult(0, "", "")
        if args[:2] == ["network", "inspect"]:
            name = args[2]
            if name not in self.networks:
                return stack.CommandResult(1, "", f"Error response from daemon: network {name} not found")
            net = self.networks[name]
            return stack.CommandResult(0, json.dumps({
                "Name": name, "Driver": net["driver"], "Internal": net["internal"],
                "Attachable": net["attachable"], "Options": net["options"], "Labels": net["labels"],
            }), "")
        if args[:2] == ["network", "create"]:
            name = args[-1]
            if name in self.networks:
                return stack.CommandResult(1, "", f"network with name {name} already exists")
            labels = dict(a.split("=", 1) for a in args[args.index("create"):] if "=" in a and "." in a)
            self.networks[name] = {"internal": "--internal" in args, "labels": labels,
                                   "driver": args[args.index("--driver") + 1],
                                   "attachable": "--attachable" in args, "options": {}}
            self.created.append(name)
            return stack.CommandResult(0, "id-of-" + name, "")
        if args[:1] == ["network"]:
            self.mutations.append(list(cmd))
            return stack.CommandResult(0, "", "")
        raise AssertionError(f"FakeDaemon was not taught: {cmd}")


def up_for_real(root, daemon, *args):
    return run(root, "up", *args, runner=daemon.runner, capture=daemon.capture)


def test_a_real_up_on_an_empty_daemon_gets_past_the_anchor_and_starts_the_frontend(root):
    """THE regression. RED at 3c3ff75: exit 1, '# up stopped: anchor exited 1'."""
    daemon = FakeDaemon()
    code, out, _ = up_for_real(root, daemon)
    assert "up stopped" not in out, out
    assert code == 0, out
    assert ["docker", "compose", "-f", "frontend/docker-compose.yml", "up", "-d"] in daemon.streamed
    # and the anchor was never `up`-ed
    assert not any(c[:5] == ["docker", "compose", "-f", "docker-compose.yml", "up"] for c in daemon.commands)


def test_the_anchor_networks_are_created_with_the_declared_flags_and_compose_labels(root):
    daemon = FakeDaemon()
    up_for_real(root, daemon)
    assert sorted(daemon.created) == ["ai-stack_app-net", "ai-stack_default", "ai-stack_llm-net"]
    assert daemon.networks["ai-stack_llm-net"]["internal"] is True
    assert daemon.networks["ai-stack_app-net"]["internal"] is False
    assert daemon.networks["ai-stack_default"]["internal"] is False
    for key, spec in ANCHOR_NETWORKS.items():
        labels = daemon.networks[spec["name"]]["labels"]
        assert labels["com.docker.compose.project"] == "ai-stack"
        assert labels["com.docker.compose.network"] == key
        # no config-hash: compose reads a hash that disagrees with its own as
        # "diverged" and offers to recreate the network
        assert "com.docker.compose.config-hash" not in labels


def test_the_render_is_asked_with_no_interpolate_or_the_networks_are_pruned(root):
    daemon = FakeDaemon()
    up_for_real(root, daemon)
    renders = [c for c in daemon.commands if "config" in c and "docker-compose.yml" in c]
    assert renders == [["docker", "compose", "-f", "docker-compose.yml",
                        "config", "--no-interpolate", "--format", "json"]]


def test_a_second_up_creates_nothing_and_alters_nothing(root):
    daemon = FakeDaemon()
    up_for_real(root, daemon)
    before = json.dumps(daemon.networks, sort_keys=True)
    daemon.created.clear()
    code, out, _ = up_for_real(root, daemon)
    assert code == 0, out
    assert daemon.created == []
    assert daemon.mutations == []
    assert json.dumps(daemon.networks, sort_keys=True) == before
    assert out.count("[exists]") == 3


def test_an_existing_matching_network_is_left_alone_with_its_own_labels(root):
    """On a running host every plane is attached to these; never recreate, never 'fix'."""
    seeded = {"ai-stack_llm-net": {"internal": True, "labels": {"mine": "1"}}}
    daemon = FakeDaemon(networks=seeded)
    code, out, _ = up_for_real(root, daemon)
    assert code == 0, out
    assert daemon.networks["ai-stack_llm-net"]["labels"] == {"mine": "1"}
    assert "ai-stack_llm-net" not in daemon.created
    assert daemon.mutations == []
    assert "[exists] ai-stack_llm-net (matches docker-compose.yml; left as is)" in out


def test_a_less_isolated_llm_net_is_a_refusal_not_a_note(root):
    """attempt-1 defect D2: a NON-internal llm-net printed a NOTE and the bring-up went on."""
    seeded = {"ai-stack_llm-net": {"internal": False}}
    daemon = FakeDaemon(networks=seeded)
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "[DIFFERS] ai-stack_llm-net: internal is false, declared true" in out
    assert "# up stopped: anchor" in out
    assert daemon.networks["ai-stack_llm-net"]["internal"] is False   # never altered
    assert daemon.created == []                                         # refused BEFORE creating the others
    assert daemon.mutations == []
    assert not any("frontend/docker-compose.yml" in " ".join(c) for c in daemon.commands)


@pytest.mark.parametrize("seed, phrase", [
    ({"driver": "macvlan"}, "driver is 'macvlan', declared 'bridge'"),
    ({"attachable": True}, "attachable is true, declared false"),
])
def test_every_declared_network_flag_is_compared(root, seed, phrase):
    daemon = FakeDaemon(networks={"ai-stack_app-net": {"internal": False, **seed}})
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert phrase in out


def test_a_declared_driver_opt_and_label_are_compared_too():
    spec = {"driver": "bridge", "driver_opts": {"com.docker.network.bridge.name": "br-x"},
            "labels": {"tier": "seam"}}
    have = {"Driver": "bridge", "Internal": False, "Attachable": False,
            "Options": {"com.docker.network.bridge.name": "br-y", "com.docker.network.enable_ipv4": "true"},
            "Labels": {"tier": "seam", "com.docker.compose.project": "ai-stack"}}
    assert stack.network_drift(spec, have) == [
        "driver_opt com.docker.network.bridge.name is 'br-y', declared 'br-x'"]


def test_doctor_and_health_flag_a_non_internal_llm_net(root, monkeypatch):
    monkeypatch.setattr(stack.shutil, "which", lambda name: "/usr/bin/docker")
    daemon = FakeDaemon(networks={"ai-stack_llm-net": {"internal": False}})
    code, out, _ = run(root, "doctor", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "[FAIL] network ai-stack_llm-net differs from docker-compose.yml: internal is false, declared true" in out
    code, out = sweep(FakeHost(network="ai-stack_llm-net false"), root, planes="frontend")
    assert ("FAIL", "anchor: ai-stack_llm-net exists and is internal") in probe_lines(out)
    assert code == 1


def test_a_partial_create_failure_keeps_what_was_made_and_a_rerun_finishes(root):
    failing = ("docker", "network", "create", "--driver", "bridge",
               "--label", "com.docker.compose.network=default",
               "--label", "com.docker.compose.project=ai-stack", "ai-stack_default")
    daemon = FakeDaemon(exit_codes={failing: 7})
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert daemon.created == ["ai-stack_app-net"]          # render order: app-net, default, llm-net
    assert "# up stopped: anchor exited 7" in out
    assert not any("frontend/docker-compose.yml" in " ".join(c) for c in daemon.commands)
    daemon.exit_codes = {}
    code, out, _ = up_for_real(root, daemon)
    assert code == 0, out
    assert daemon.created == ["ai-stack_app-net", "ai-stack_default", "ai-stack_llm-net"]


def test_a_network_key_the_driver_cannot_translate_is_refused_before_anything_starts(root):
    render = {"name": "ai-stack", "networks": {
        "llm-net": {"internal": True, "name": "ai-stack_llm-net",
                    "ipam": {"config": [{"subnet": "10.9.0.0/16"}]}}}}
    daemon = FakeDaemon(render=render)
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "ipam" in out
    assert daemon.created == []
    assert not any("frontend" in " ".join(c) for c in daemon.commands)


def test_a_failing_network_create_stops_the_bring_up(root):
    failing = ("docker", "network", "create", "--driver", "bridge",
               "--label", "com.docker.compose.network=app-net",
               "--label", "com.docker.compose.project=ai-stack", "ai-stack_app-net")
    daemon = FakeDaemon(exit_codes={failing: 5})
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "# up stopped: anchor exited 5" in out
    assert not any("frontend/docker-compose.yml" in " ".join(c) for c in daemon.commands)


def test_down_still_runs_compose_down_on_the_anchor(root):
    """Only `up` changed. `down` on a zero-service project exits 0 (measured)."""
    code, out, _ = run(root, "down", "--dry-run")
    assert docker_lines(out)[-1] == "docker compose -f docker-compose.yml down"


def test_the_root_compose_file_pins_the_project_name():
    """Without `name:` compose names the project after the clone directory."""
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert [ln.strip() for ln in text.splitlines() if ln.startswith("name:")] == ["name: ai-stack"]


# --- placeholders: the value a newcomer copied and never replaced -------------


def _ship_example(root, plane, text):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env = manifest.env_path(root, plane)
    env.with_name(".env.example").write_text(text, encoding="utf-8")
    return env


def _copy_real_frontend_example(root):
    """frontend/.env exactly as a newcomer makes it: a copy of the shipped example."""
    shipped = (REPO_ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")
    (root / "frontend" / ".env.example").write_text(shipped, encoding="utf-8")
    (root / "frontend" / ".env").write_text(shipped, encoding="utf-8")


def test_up_refuses_the_shipped_secret_placeholder_and_starts_nothing(root):
    _copy_real_frontend_example(root)
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    line = [ln for ln in out.splitlines() if "WEBUI_SECRET_KEY" in ln]
    assert line and "frontend/.env" in line[0] and "frontend/.env.example" in line[0], out
    assert daemon.streamed == []           # nothing run, not even the anchor's networks
    assert all("config" in c for c in daemon.commands)   # only read-only renders
    assert "Nothing was started" in out


def test_up_dry_run_refuses_the_placeholder_too(root):
    _copy_real_frontend_example(root)
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, recorder = run(root, "up", "--dry-run", capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "WEBUI_SECRET_KEY" in out
    assert recorder.commands == []


def test_doctor_names_the_file_and_the_key_of_a_placeholder(root):
    _copy_real_frontend_example(root)
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, _ = run(root, "doctor", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert ("[FAIL] WEBUI_SECRET_KEY is still the placeholder shipped in frontend/.env.example "
            "in frontend/.env") in out


def test_enable_refuses_a_placeholder_on_any_plane_it_would_enable(root):
    env = _ship_example(root, "search", "MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key\n"
                                        "MULLVAD_WG_ADDRESSES=\n")
    env.write_text("MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key\n"
                   "MULLVAD_WG_ADDRESSES=10.0.0.2/32\n", encoding="utf-8")
    code, out, _ = run(root, "enable", "search", "--plane")
    assert code == stack.EXIT_REFUSED
    assert "MULLVAD_WG_PRIVATE_KEY is still the placeholder shipped in search/.env.example in search/.env" in out
    assert "MULLVAD_WG_ADDRESSES" not in out   # shipped blank, now set: fine


def test_a_replaced_placeholder_passes(root):
    _copy_real_frontend_example(root)
    env = root / "frontend" / ".env"
    env.write_text(env.read_text(encoding="utf-8").replace(
        "WEBUI_SECRET_KEY=change-me-to-a-long-random-string", "WEBUI_SECRET_KEY=3f9a0c"), encoding="utf-8")
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, _ = up_for_real(root, daemon)
    assert code == 0, out


def test_every_shipped_example_value_of_a_required_key_is_refused_verbatim(root):
    """The rule, against the files that SHIP: copy an example, change nothing, get refused.

    For each plane whose .env.example is in this checkout, a verbatim copy must
    fail every key the example ships non-blank. (OB1's example lives in the
    submodule, which a checkout may not have - it is checked when present.)
    """
    manifest = stack.Manifest.load(REAL_MANIFEST)
    checked = 0
    for plane in manifest.order:
        real_env = manifest.env_path(REPO_ROOT, plane)
        example = real_env.with_name(".env.example")
        if not manifest.keys(plane) or not example.is_file():
            continue
        shipped = stack.read_env_file(example)
        text = example.read_text(encoding="utf-8")
        env = manifest.env_path(root, plane)
        env.with_name(".env.example").write_text(text, encoding="utf-8")
        env.write_text(text, encoding="utf-8")
        flagged = {k for k, why, _p in stack.blank_keys(manifest, root, plane) if "placeholder" in why}
        expected = {k for k in manifest.keys(plane) if shipped.get(k, "").strip()}
        assert flagged == expected, plane
        checked += len(expected)
    assert checked >= 8   # frontend, inference, memory, search, coder, agent-org, portal carry some


# --- health scoped to the planes this machine enables --------------------------


FRONTEND_ONLY_PROBES = [
    "0 unhealthy containers (found: )",
    "anchor: ai-stack_llm-net exists and is internal",
    "frontend: OWUI http://127.0.0.1:3000/health",
    "frontend: 8 tailnet serve routes",
    "frontend: owui/ manifest rows drifted from live webui.db: 0",
]


def test_health_on_a_fresh_clone_probes_only_the_frontend_and_the_anchor(root):
    """No state file = the default, frontend alone. RED at 3c3ff75: 16 probes, 11 of them FAIL here."""
    host = FakeHost(http_status={"http://127.0.0.1:8060/health": 0, "http://127.0.0.1:8062/health": 0})
    code, out = sweep(host, root, planes=None)
    assert [name for _s, name in probe_lines(out)] == FRONTEND_ONLY_PROBES
    assert ("  [skip] not enabled on this machine, probes not run: "
            "inference, memory, search, coder, ob1, agent-org") in out
    assert code == 0
    # a skipped plane is never even asked
    assert not any("llm-gateway" in c or "openbrain-db" in c for call in host.calls for c in call)


def test_health_counts_only_the_probes_it_ran(root):
    run(root, "init", "--planes", "inference,frontend", "--force")
    host = FakeHost(exec_codes={"llm-gateway": 1, "agent-bridge": 1})
    code, out = sweep(host, root, planes=None)
    assert code == 1                           # the gateway; agent-org was not probed
    assert ("FAIL", "inference: llm-gateway liveliness") in probe_lines(out)
    assert "agent-org: mattermost ping" not in out


def test_the_drift_probe_skips_out_loud_where_there_is_no_powershell(root, monkeypatch):
    monkeypatch.setattr(stack, "powershell_command", lambda: None)
    host = FakeHost()
    code, out = sweep(host, root, planes="frontend")
    assert code == 0
    assert "  [skip] frontend: owui/ manifest drift" in out
    assert not any(call[0] in ("powershell", "pwsh") for call in host.calls)


def test_off_windows_the_drift_probe_uses_pwsh_when_it_exists(monkeypatch):
    monkeypatch.undo()   # drop the autouse pin, ask the real function
    monkeypatch.setattr(stack, "WINDOWS", False)
    monkeypatch.setattr(stack.shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None)
    assert stack.powershell_command() == ("pwsh",)
    monkeypatch.setattr(stack.shutil, "which", lambda name: None)
    assert stack.powershell_command() is None
    monkeypatch.setattr(stack, "WINDOWS", True)
    assert stack.powershell_command() == ("powershell",)


# --- an uninitialised submodule is a sentence, not a trace ----------------------


def _without_ob1(root):
    (root / ".gitmodules").write_text('[submodule "OB1"]\n\tpath = OB1\n\turl = https://example.invalid/OB1.git\n',
                                      encoding="utf-8")
    shutil.rmtree(root / "OB1")
    (root / "OB1").mkdir()        # what a clone without --recurse-submodules has: an empty dir


def test_up_with_ob1_enabled_but_not_initialised_names_the_command(root):
    run(root, "init", "--planes", "inference,search,frontend,ob1", "--force")
    _without_ob1(root)
    daemon = FakeDaemon()
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "`git submodule update --init OB1`" in out
    assert "Traceback" not in out
    assert daemon.commands == []


def test_doctor_names_the_submodule_command(root):
    run(root, "init", "--planes", "inference,search,frontend,ob1", "--force")
    _without_ob1(root)
    code, out, _ = run(root, "doctor")
    assert code == stack.EXIT_REFUSED
    assert ("[FAIL] compose file missing: OB1/docker/docker-compose.yml - the OB1 submodule is not "
            "initialised; run `git submodule update --init OB1`") in out
    assert "OB1/docker/.env" not in out   # its env lives in the submodule; one remedy, not five


def test_enable_ob1_without_the_submodule_names_the_command_not_five_missing_keys(root):
    run(root, "init", "--planes", "inference,search,frontend", "--force")
    _without_ob1(root)
    code, out, _ = run(root, "enable", "ob1")
    assert code == stack.EXIT_REFUSED
    assert "`git submodule update --init OB1`" in out
    assert "missing in OB1/docker/.env" not in out


def test_doctor_on_a_fresh_clone_does_not_fail_the_anchor_for_a_root_env_it_never_reads(root, monkeypatch):
    monkeypatch.setattr(stack.shutil, "which", lambda name: "/usr/bin/docker")
    (root / ".env").unlink()
    daemon = FakeDaemon()
    code, out, _ = run(root, "doctor", runner=daemon.runner, capture=daemon.capture)
    assert "[ -- ] env .env absent - not needed" in out
    assert "[ -- ] network ai-stack_llm-net absent (`up` creates it)" in out
    assert code == 0, out



# --- attempt 2 / D1: every shipped placeholder, derived from the .env.example files ----

FRONTEND_RENDER = {"services": {
    "openwebui-stock": {"profiles": ["stock"], "environment": {"WEBUI_SECRET_KEY": "${WEBUI_SECRET_KEY:?x}"}},
    "openwebui": {"profiles": ["gpu"], "environment": {
        "WEBUI_SECRET_KEY": "${WEBUI_SECRET_KEY:?x}", "OWUI_CHAT_LLM_API_KEY": "${OWUI_CHAT_LLM_API_KEY}"}},
    "tailscale": {"profiles": ["tailscale"], "environment": {"TS_AUTHKEY": "${TAILSCALE_AUTH_KEY}"}},
    "openwebui-backup": {"command": ["sh", "-c", "echo $$HOME"]},
}}

SEARCH_RENDER = {"services": {
    "gateway": {"environment": {"GATEWAY_API_KEY": "${GATEWAY_API_KEY}"}},
    "searxng": {"environment": {"SEARXNG_SECRET": "${SEARXNG_SECRET_KEY}"}},
    "vpn": {"environment": {"WIREGUARD_PRIVATE_KEY": "${MULLVAD_WG_PRIVATE_KEY:?x}",
                            "WIREGUARD_ADDRESSES": "${MULLVAD_WG_ADDRESSES}"}},
}}

AGENT_ORG_RENDER = {"services": {
    # a bulk env_file is NOT a read: agent-bridge loads the whole .env that way
    "agent-bridge": {"env_file": [{"path": ".env"}], "environment": {"AO_DB_PASSWORD": "${AO_DB_PASSWORD}"}},
    "llm-gateway-cloud": {"profiles": ["cloud"], "environment": {
        "LITELLM_MASTER_KEY": "${AO_CLOUD_MASTER_KEY}", "DB": "${AO_CLOUD_DB_PASSWORD}"}},
    "ao-ot-1": {"profiles": ["workers"], "environment": {"KEY": "${AO_OPEN_TERMINAL_KEY}"}},
}}


def _real_example_into(root, rel_dir):
    text = (REPO_ROOT / rel_dir / ".env.example").read_text(encoding="utf-8")
    (root / rel_dir / ".env.example").write_text(text, encoding="utf-8")
    return root / rel_dir / ".env", text


def test_the_placeholder_pattern_matches_the_shapes_and_no_real_default():
    for value in ("change-me-to-a-long-random-string", "sk-change-me-owui-virtual-key", "CHANGE_ME",
                  "REPLACE_WITH_64_HEX_CHARS", "your-mnemory-api-key-here", "putyourtskeyhere",
                  "<your token>", "ai.example.com", "you@example.com", "placeholder-value"):
        assert stack.is_placeholder(value), value
    for value in ("llama", "8080", "http://gateway:8080/search?q=<query>", "stock", "true",
                  "/models/lmstudio-community/x.gguf", "q4_0", "", "gateway", "86400"):
        assert not stack.is_placeholder(value), value


def test_every_placeholder_in_every_shipped_example_is_derived_not_listed():
    """The rule is data-driven: each .env.example's placeholders come from its own values."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    found = {}
    for plane in manifest.order:
        example = stack.example_path(manifest.env_path(REPO_ROOT, plane))
        if example.is_file():
            found[plane] = {k for k, v in stack.read_env_file(example).items() if stack.is_placeholder(v)}
    # the ones attempt 1 missed are all derived
    assert {"GATEWAY_API_KEY", "SEARXNG_SECRET_KEY"} <= found["search"]
    assert {"OWUI_CHAT_LLM_API_KEY", "TAILSCALE_AUTH_KEY", "WEBUI_SECRET_KEY"} <= found["frontend"]
    assert {"AO_OPEN_TERMINAL_KEY", "AO_CLOUD_DB_PASSWORD", "AO_CLOUD_MASTER_KEY"} <= found["agent-org"]
    if "ob1" in found:
        assert "MCPO_API_KEY" in found["ob1"]


def test_a_copied_search_env_refuses_the_gateway_and_searxng_secrets(root):
    """attempt-1 defect D1, the tester's repro: both went up at the public placeholder."""
    env, text = _real_example_into(root, "search")
    env.write_text(text.replace("MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key",
                                "MULLVAD_WG_PRIVATE_KEY=abc").replace("MULLVAD_WG_ADDRESSES=",
                                                                      "MULLVAD_WG_ADDRESSES=10.0.0.2/32"),
                   encoding="utf-8")
    daemon = FakeDaemon(plane_renders={"search/docker-compose.yml": SEARCH_RENDER})
    code, out, _ = run(root, "enable", "search", "--plane", capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "GATEWAY_API_KEY is still the placeholder shipped in search/.env.example in search/.env" in out
    assert "SEARXNG_SECRET_KEY is still the placeholder" in out
    assert "refused: search cannot be enabled yet:" in out
    run(root, "init", "--planes", "frontend", "--force")
    state = state_of(root)
    state["planes"]["search"] = {"profiles": [], "context": None}
    (root / stack.STATE_REL).write_text(json.dumps(state), encoding="utf-8")
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "search: GATEWAY_API_KEY in search/.env" in out
    assert daemon.streamed == []
    code, out, _ = run(root, "doctor", runner=daemon.runner, capture=daemon.capture)
    assert "[FAIL] SEARXNG_SECRET_KEY is still the placeholder" in out


def test_a_profile_gated_placeholder_counts_only_under_its_profile(root):
    """TAILSCALE_AUTH_KEY / OWUI_CHAT_LLM_API_KEY: refused under gpu,tailscale, not under stock."""
    env, text = _real_example_into(root, "frontend")
    env.write_text(text.replace("WEBUI_SECRET_KEY=change-me-to-a-long-random-string", "WEBUI_SECRET_KEY=3f9a"),
                   encoding="utf-8")          # COMPOSE_PROFILES=stock, as shipped
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, _ = up_for_real(root, daemon)
    assert code == 0, out                      # a stock newcomer is not blocked by the tailnet key
    env.write_text(env.read_text(encoding="utf-8").replace("COMPOSE_PROFILES=stock",
                                                           "COMPOSE_PROFILES=gpu,tailscale"), encoding="utf-8")
    daemon = FakeDaemon(plane_renders={"frontend/docker-compose.yml": FRONTEND_RENDER})
    code, out, _ = up_for_real(root, daemon)
    assert code == stack.EXIT_REFUSED
    assert "TAILSCALE_AUTH_KEY in frontend/.env" in out
    assert "OWUI_CHAT_LLM_API_KEY in frontend/.env" in out


def test_a_bulk_env_file_is_not_a_read_and_cloud_keys_wait_for_their_profile(root):
    env, text = _real_example_into(root, "agent-org/docker")
    daemon = FakeDaemon(plane_renders={"agent-org/docker/docker-compose.yml": AGENT_ORG_RENDER})
    state = stack.State({"agent-org": {"profiles": [], "context": None}}, None, True)
    manifest = stack.Manifest.load(REAL_MANIFEST)
    env.write_text(text, encoding="utf-8")
    keys = {k for k, _w, _p in stack.shipped_placeholders(manifest, state, root, "agent-org", daemon.capture)}
    assert "AO_CLOUD_MASTER_KEY" not in keys and "AO_OPEN_TERMINAL_KEY" not in keys
    state = stack.State({"agent-org": {"profiles": ["cloud", "workers"], "context": None}}, None, True)
    keys = {k for k, _w, _p in stack.shipped_placeholders(manifest, state, root, "agent-org", daemon.capture)}
    assert {"AO_CLOUD_MASTER_KEY", "AO_CLOUD_DB_PASSWORD", "AO_OPEN_TERMINAL_KEY"} <= keys


def test_an_unrenderable_plane_fails_closed_and_says_why(root):
    env, text = _real_example_into(root, "search")
    env.write_text(text, encoding="utf-8")
    broken = ("docker", "compose", "-f", "search/docker-compose.yml", "config", "--no-interpolate", "--format", "json")
    daemon = FakeDaemon(exit_codes={broken: 1})
    manifest = stack.Manifest.load(REAL_MANIFEST)
    state = stack.State({"search": {"profiles": [], "context": None}}, None, True)
    found = stack.shipped_placeholders(manifest, state, root, "search", daemon.capture)
    assert {k for k, _w, _p in found} == {"GATEWAY_API_KEY", "SEARXNG_SECRET_KEY"}
    assert all("could not render search/docker-compose.yml" in why for _k, why, _p in found)


def test_a_replaced_or_blank_non_required_value_is_not_a_placeholder(root):
    env, text = _real_example_into(root, "search")
    env.write_text(text.replace("GATEWAY_API_KEY=change-me-to-a-long-random-string", "GATEWAY_API_KEY=abc")
                   .replace("SEARXNG_SECRET_KEY=change-me-to-another-long-random-string", "SEARXNG_SECRET_KEY="),
                   encoding="utf-8")
    daemon = FakeDaemon(plane_renders={"search/docker-compose.yml": SEARCH_RENDER})
    manifest = stack.Manifest.load(REAL_MANIFEST)
    state = stack.State({"search": {"profiles": [], "context": None}}, None, True)
    assert stack.shipped_placeholders(manifest, state, root, "search", daemon.capture) == []


# --- attempt 2 / D3: the refusal names the cause it actually has ----------------


def test_a_submodule_only_refusal_does_not_talk_about_placeholders(root):
    run(root, "init", "--planes", "inference,search,frontend,ob1", "--force")
    _without_ob1(root)
    code, out, _ = up_for_real(root, FakeDaemon())
    assert code == stack.EXIT_REFUSED
    assert "placeholder" not in out.lower()
    assert "Nothing was started. Initialise the submodule with the command named above, and re-run." in out
    code, out, _ = run(root, "enable", "ob1")
    assert "refused: ob1 cannot be enabled yet:" in out
    assert "needs these keys" not in out


# --- attempt 2 / D4: health probes exactly the enabled planes + the anchor -------


def test_health_does_not_probe_a_required_plane_nobody_enabled(root):
    """A hand-written state {coder, memory}: inference is required, but not enabled -> not probed."""
    (root / stack.STATE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / stack.STATE_REL).write_text(json.dumps({"version": 1, "planes": {"coder": {}, "memory": {}}}),
                                        encoding="utf-8")
    host = FakeHost()
    code, out = sweep(host, root, planes=None)
    names = [name for _s, name in probe_lines(out)]
    assert not any(n.startswith("inference:") for n in names)
    assert any(n.startswith("coder:") for n in names) and any(n.startswith("memory:") for n in names)
    assert "probes not run: inference, frontend, search, ob1, agent-org" in out


# --- ac-planes-contained: memory's sibling checkout, and the shared backup module ----


MNEMORY_CLONE = "git clone -b dev https://github.com/devonpveller/mnemory.git ../mnemory"


def _without_mnemory(root):
    sibling = root.parent / "mnemory"
    if sibling.is_dir():
        shutil.rmtree(sibling)
    elif sibling.exists():
        sibling.unlink()


def test_doctor_fails_memory_without_the_sibling_mnemory_and_names_the_clone(root):
    run(root, "init", "--planes", "inference,frontend,memory", "--force")
    _without_mnemory(root)
    code, out, _ = run(root, "doctor")
    assert code == stack.EXIT_REFUSED
    assert "[FAIL] ../mnemory is missing (plane memory)" in out
    assert f"run `{MNEMORY_CLONE}` from the repo root" in out


def test_doctor_passes_the_sibling_mnemory_when_it_is_there(root):
    run(root, "init", "--planes", "inference,frontend,memory", "--force")
    code, out, _ = run(root, "doctor")
    assert "[OK]   host path ../mnemory" in out
    assert "../mnemory is missing" not in out


def test_enable_memory_without_the_sibling_mnemory_refuses_with_the_clone(root):
    run(root, "init", "--planes", "inference,frontend", "--force")
    _without_mnemory(root)
    code, out, _ = run(root, "enable", "memory")
    assert code == stack.EXIT_REFUSED
    assert "../mnemory is missing (plane memory)" in out
    assert f"Run `{MNEMORY_CLONE}`." in out
    assert "memory" not in state_of(root)["planes"]


@pytest.mark.parametrize("shape", ["empty-dir", "plain-file", "no-dockerfile", "no-git"])
def test_an_unusable_sibling_mnemory_is_refused_by_doctor_and_enable(root, shape):
    """Existing is not enough: only a directory holding .git and the Dockerfile passes."""
    sibling = root.parent / "mnemory"
    _without_mnemory(root)
    if shape == "plain-file":
        sibling.write_text("not a checkout\n", encoding="utf-8")
    else:
        sibling.mkdir()
        if shape == "no-dockerfile":
            (sibling / ".git").mkdir()
        if shape == "no-git":
            (sibling / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    run(root, "init", "--planes", "inference,frontend", "--force")
    code, out, _ = run(root, "enable", "memory")
    assert code == stack.EXIT_REFUSED, out
    assert f"Run `{MNEMORY_CLONE}`." in out
    # init refuses memory here too, so write the state by hand: doctor must still catch it
    (root / stack.STATE_REL).write_text(
        json.dumps({"version": 1, "planes": {"inference": {}, "frontend": {}, "memory": {}}}), encoding="utf-8")
    code, out, _ = run(root, "doctor")
    assert code == stack.EXIT_REFUSED
    assert "[FAIL] ../mnemory " in out and f"run `{MNEMORY_CLONE}`" in out
    assert "[OK]   host path ../mnemory" not in out


def test_the_mnemory_host_path_requires_the_dockerfile_the_compose_file_names():
    manifest = stack.Manifest.load(REAL_MANIFEST)
    (spec,) = manifest.plane("memory")["host_paths"]
    text = (REPO_ROOT / manifest.plane("memory")["compose"]).read_text(encoding="utf-8")
    block = text[text.index("context: ../../mnemory"):]
    dockerfile = re.search(r"dockerfile:\s*(\S+)", block).group(1)
    assert dockerfile in spec["contains"] and ".git" in spec["contains"]


def test_enable_memory_with_the_sibling_mnemory_succeeds(root):
    run(root, "init", "--planes", "inference,frontend", "--force")
    code, out, _ = run(root, "enable", "memory")
    assert code == 0, out
    assert "memory" in state_of(root)["planes"]


def test_the_memory_product_refuses_too(root):
    run(root, "init", "--planes", "inference,frontend", "--force")
    _without_mnemory(root)
    code, out, _ = run(root, "enable", "--product", "memory")
    assert code == stack.EXIT_REFUSED
    assert f"Run `{MNEMORY_CLONE}`." in out


def test_the_mnemory_host_path_is_the_memory_build_context():
    """The manifest's ../mnemory must be where memory/docker-compose.yml builds from."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    declared = [(REPO_ROOT / Path(spec["path"])).resolve() for spec in manifest.plane("memory")["host_paths"]]
    compose = REPO_ROOT / manifest.plane("memory")["compose"]
    contexts = re.findall(r"^\s*context:\s*(\S+)\s*$", compose.read_text(encoding="utf-8"), re.MULTILINE)
    outside = [(compose.parent / c).resolve() for c in contexts
               if not (compose.parent / c).resolve().is_relative_to(REPO_ROOT.resolve())]
    assert outside, "memory/docker-compose.yml no longer builds from outside the repo - drop host_paths"
    assert declared == outside


def test_every_backup_consumer_is_declared_and_every_declared_consumer_is_real():
    """[modules.backup].consumers == the planes whose compose files reference ../backup."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    with (REPO_ROOT / "stack.manifest.toml").open("rb") as fh:
        module = tomllib.load(fh)["modules"]["backup"]
    backup_dir = (REPO_ROOT / module["path"]).resolve()
    assert backup_dir.is_dir()
    found = set()
    scanned = 0
    for name, plane in manifest.planes.items():
        compose = REPO_ROOT / plane["compose"]
        files = [compose] + sorted((compose.parent / "compose").glob("*.yml"))
        for path in files:
            if not path.is_file():
                continue    # OB1 in a checkout without the submodule
            scanned += 1
            for ref in re.findall(r"(\.\.(?:/\.\.)*/backup)(?=[/\s])", path.read_text(encoding="utf-8")):
                if (path.parent / ref).resolve() == backup_dir:
                    found.add(name)
    assert scanned >= 8, f"only {scanned} compose files read - the scan is not looking at the planes"
    assert found, "no plane references backup/ - the scan matched nothing"
    assert set(module["consumers"]) == found


# --------------------------------------------------------------------------
# ac-ops-portable: recover, backup, restore, stats - operating off Windows
# --------------------------------------------------------------------------
#
# The renders below are written in the shape `docker compose config --format
# json` emits (measured on compose v5.3.0, this repo's frontend and inference
# planes, 2026-09-25): depends_on as {service: {condition, required}}, durations
# as Go strings ("1m0s"), network_mode "service:X", volumes as long-form dicts.
# They are trimmed copies of the real planes, so the orders pinned here are the
# orders the real compose files produce - the DinD rehearsal
# (scripts/stack/rehearse-ops.sh) checks the same against a real daemon.


def _svc(image, container=None, depends=None, netns=None, health=False, volumes=(), restart="unless-stopped"):
    # Every service in this repo's real renders carries `restart: unless-stopped`.
    spec = {"image": image}
    if restart:
        spec["restart"] = restart
    if container:
        spec["container_name"] = container
    if depends:
        spec["depends_on"] = {k: {"condition": c, "required": True} for k, c in depends.items()}
    if netns:
        spec["network_mode"] = f"service:{netns}"
    if health:
        spec["healthcheck"] = {"test": ["CMD", "true"], "interval": "15s", "timeout": "15s",
                               "retries": 3, "start_period": "1m0s"}
    if volumes:
        spec["volumes"] = [{"type": "volume", "source": s, "target": t, "read_only": ro} for s, t, ro in volumes]
    return spec


FRONTEND_GPU = {
    "name": "frontend",
    "services": {
        "openwebui": _svc("openwebui:local", "openwebui", health=True,
                          volumes=[("openwebui-data", "/app/backend/data", False)]),
        "openwebui-backup": _svc("alpine:3.21", "openwebui-backup", depends={"openwebui": "service_healthy"},
                                 volumes=[("openwebui-data", "/data", True)]),
        "tailscale": _svc("tailscale:local", "tailscale", depends={"openwebui": "service_healthy"},
                          netns="openwebui", health=True),
        "tailscale-backup": _svc("alpine:3.21", "tailscale-backup"),
    },
    "volumes": {"openwebui-data": {"name": "frontend_openwebui-data"}},
}

INFERENCE_LOCAL = {
    "name": "inference",
    "services": {
        "llama-cpp-upstream": _svc("ghcr.io/mostlygeek/llama-swap:cuda", "llama-cpp-upstream", health=True),
        "llama-cpp-embed-upstream": _svc("ghcr.io/ggml-org/llama.cpp:server-cuda", "llama-cpp-embed-upstream",
                                         health=True),
        "llm-queue": _svc("llm-queue:local", "llm-queue", depends={"llama-cpp-upstream": "service_healthy"},
                          health=True, volumes=[("llm-queue-data", "/data", False)]),
        "llm-gateway-db": _svc("postgres:16-alpine", "llm-gateway-db", health=True,
                               volumes=[("llm-gateway-db-data", "/var/lib/postgresql/data", False)]),
        "llm-gateway": _svc("ghcr.io/berriai/litellm@sha256:abc", "llm-gateway", health=True,
                            depends={"llama-cpp-upstream": "service_healthy",
                                     "llama-cpp-embed-upstream": "service_healthy",
                                     "llm-queue": "service_healthy", "llm-gateway-db": "service_healthy"}),
        "llm-gateway-backup": _svc("postgres:16-alpine", "llm-gateway-backup",
                                   depends={"llm-gateway-db": "service_healthy"}),
    },
    "volumes": {"llm-gateway-db-data": {"name": "inference_llm-gateway-db-data"},
                "llm-queue-data": {"name": "inference_llm-queue-data"}},
}

EXISTING_NETWORKS = {spec["name"]: {"internal": bool(spec.get("internal"))} for spec in ANCHOR_NETWORKS.values()}
HEALTHY = {"Status": "running", "Health": {"Status": "healthy"}, "RestartCount": 0}


class OpsDaemon(FakeDaemon):
    """FakeDaemon plus containers, volumes and the helper container.

    `states` scripts `docker inspect` per container: a list consumed one poll at a
    time, the last entry repeating. A container never scripted answers HEALTHY
    once something has `up`-ed it and "no such container" before.
    """

    def __init__(self, renders, states=None, volumes=None, users=None, networks=None, **kw):
        super().__init__(networks=EXISTING_NETWORKS if networks is None else networks,
                         plane_renders=renders, **kw)
        self.states = {k: list(v) for k, v in (states or {}).items()}
        self.upped: set[str] = set()
        self.volumes = dict(volumes or {})     # volume name -> bytes
        self.volume_labels: dict = {}
        self.users = users or {}               # volume name -> running container names
        self.piped: list[list[str]] = []
        self.ps_ids: dict = {}                 # compose file -> ids
        self.stats = []                        # docker stats rows
        self.execs: dict = {}                  # container -> CommandResult
        self.removed: list[str] = []           # helper containers `rm -f`-ed
        self.images: dict = {}                 # image -> its .Config.Healthcheck (None = none)
        self.pipe_codes: list[int] = []        # scripted exit codes for pipe(), consumed in order
        self.stats_gone: set = set()           # ids `docker stats` says no longer exist

    def _containers(self, compose_file, keys):
        services = self.plane_renders[compose_file]["services"]
        return [services[k].get("container_name") or k for k in keys]

    def _do(self, cmd):
        args = list(cmd[1:])
        if args[:1] == ["--context"]:
            args = args[2:]
        if args[:1] == ["inspect"]:
            self.commands.append(list(cmd))
            name = args[-1]
            if name in self.states:
                seq = self.states[name]
                state = seq.pop(0) if len(seq) > 1 else seq[0]
            elif name in self.upped:
                state = HEALTHY
            else:
                return stack.CommandResult(1, "", f"Error: No such object: {name}")
            # docker keeps RestartCount OUTSIDE .State; answer the format the driver asks for
            assert args[1:3] == ["--format", "{{json .State}}|{{.RestartCount}}"], args
            state = dict(state)
            restarts = state.pop("RestartCount", 0)
            return stack.CommandResult(0, json.dumps(state) + "|" + str(restarts), "")
        if args[:2] == ["volume", "inspect"]:
            self.commands.append(list(cmd))
            return stack.CommandResult(0 if args[2] in self.volumes else 1, "", "")
        if args[:2] == ["volume", "create"]:
            self.commands.append(list(cmd))
            self.mutations.append(list(cmd))
            name = args[-1]
            self.volumes[name] = b""
            self.volume_labels[name] = dict(a.split("=", 1) for a in args if a.startswith("com.docker"))
            return stack.CommandResult(0, name, "")
        if args[:2] == ["image", "inspect"]:
            self.commands.append(list(cmd))
            image = args[-1]
            if image not in self.images:
                return stack.CommandResult(1, "", f"Error: No such image: {image}")
            return stack.CommandResult(0, json.dumps(self.images[image]), "")
        if args[:2] == ["rm", "-f"]:
            self.commands.append(list(cmd))
            self.removed.append(args[2])
            return stack.CommandResult(0, args[2], "")
        if args[:3] == ["ps", "-a", "-q"]:
            self.commands.append(list(cmd))
            name = args[-1].split("=", 1)[1].strip("^$")
            return stack.CommandResult(0, "" if name in self.removed else "abc\n", "")
        if args[:2] == ["ps", "--filter"]:
            self.commands.append(list(cmd))
            volume = args[2].split("=", 1)[1]
            return stack.CommandResult(0, "".join(n + "\n" for n in self.users.get(volume, [])), "")
        if args[:1] == ["stats"]:
            self.commands.append(list(cmd))
            ids = args[4:]
            if any(i in self.stats_gone for i in ids):
                return stack.CommandResult(1, "", f"Error response from daemon: No such container: {ids[0]}")
            rows = [r for r in self.stats if r.get("ID") in ids] if len(ids) == 1 else self.stats
            return stack.CommandResult(0, "".join(json.dumps(r) + "\n" for r in rows), "")
        if args[:1] == ["exec"]:
            self.commands.append(list(cmd))
            return self.execs.get(args[1], stack.CommandResult(1, "", "no such container"))
        if args[:2] == ["compose", "-f"]:
            compose_file, rest = args[2], args[3:]
            while rest[:1] == ["--profile"]:
                rest = rest[2:]
            if rest[:2] == ["ps", "-q"]:
                self.commands.append(list(cmd))
                return stack.CommandResult(0, "".join(i + "\n" for i in self.ps_ids.get(compose_file, [])), "")
            if rest[:1] == ["stop"]:
                self.commands.append(list(cmd))
                self.upped.difference_update(self._containers(compose_file, rest[3:]))
                return stack.CommandResult(0, "", "")
            if rest[:1] == ["up"] and compose_file != "docker-compose.yml":
                result = super()._do(cmd)
                if result.code == 0:
                    self.upped.update(self._containers(compose_file, rest[3:]))
                return result
        return super()._do(cmd)

    def pipe(self, cmd, cwd, stdin_path=None, stdout_path=None):
        self.commands.append(list(cmd))
        self.piped.append(list(cmd))
        self.mutations.append(list(cmd)) if stdin_path else None
        volume = cmd[cmd.index("-v") + 1].split(":")[0]
        if self.pipe_codes:
            code = self.pipe_codes.pop(0)
            if code:
                if stdout_path:
                    Path(stdout_path).write_bytes(b"partial")
                return stack.CommandResult(code, "", "write /dev/stdout: no space left on device")
        if stdout_path:
            Path(stdout_path).write_bytes(b"TAR:" + self.volumes[volume])
            return stack.CommandResult(0, "", "")
        data = Path(stdin_path).read_bytes()
        self.volumes[volume] = data[4:] if data.startswith(b"TAR:") else data
        return stack.CommandResult(0, "", "")


@pytest.fixture
def fast_clock(monkeypatch):
    """The gate loop's clock and sleep: a four-minute timeout runs in no time."""
    now = [0.0]
    monkeypatch.setattr(stack, "monotonic", lambda: now[0])

    def _sleep(seconds):
        now[0] += seconds
    monkeypatch.setattr(stack, "sleep", _sleep)
    return now


def ops(root, daemon, *args):
    out = io.StringIO()
    code = stack.main(["--root", str(root), *args], runner=daemon.runner, stdout=out,
                      capture=daemon.capture, pipe=daemon.pipe)
    return code, out.getvalue()


RENDERS = {"frontend/docker-compose.yml": FRONTEND_GPU, "inference/docker-compose.yml": INFERENCE_LOCAL}


def _enable(root, planes="inference,frontend"):
    code, out, _ = run(root, "init", "--planes", planes, "--force")
    assert code == 0, out


def _vol(cmd):
    return cmd[cmd.index("-v") + 1]


def _index(lines, needle):
    return next(i for i, line in enumerate(lines) if needle in line)


# --- the orders ----------------------------------------------------------------


def test_recover_dry_run_is_up_order_for_planes_and_depends_on_order_for_containers(root):
    _enable(root)
    daemon = OpsDaemon(RENDERS)
    code, out = ops(root, daemon, "recover", "--dry-run")
    assert code == 0, out
    lines = docker_lines(out)
    # planes: stop in reverse of `up`, start in `up`'s order (anchor, inference, frontend)
    assert "# recover: anchor, inference, frontend" in out
    stop_fe = _index(lines, "frontend/docker-compose.yml stop")
    stop_inf = _index(lines, "inference/docker-compose.yml stop")
    up_inf = _index(lines, "inference/docker-compose.yml up")
    up_fe = _index(lines, "frontend/docker-compose.yml up")
    assert stop_fe < stop_inf < up_inf < up_fe
    # the netns rule: tailscale stops BEFORE openwebui and starts AFTER it
    assert lines[stop_fe].endswith("stop --timeout 30 openwebui-backup tailscale")
    assert lines[stop_fe + 1].endswith("stop --timeout 30 openwebui tailscale-backup")
    starts = [ln for ln in lines if "frontend/docker-compose.yml up" in ln]
    assert starts == ["docker compose -f frontend/docker-compose.yml up -d --no-deps openwebui tailscale-backup",
                      "docker compose -f frontend/docker-compose.yml up -d --no-deps openwebui-backup tailscale"]
    # the inference rule: both upstreams (and the db) before llm-queue, llm-queue before the gateway
    ups = [ln.split("--no-deps ")[1] for ln in lines if "inference/docker-compose.yml up" in ln]
    assert ups == ["llama-cpp-embed-upstream llama-cpp-upstream llm-gateway-db",
                   "llm-gateway-backup llm-queue", "llm-gateway"]


def test_recover_dry_run_stops_starts_and_creates_nothing(root):
    _enable(root)
    daemon = OpsDaemon(RENDERS)
    code, out = ops(root, daemon, "recover", "--dry-run")
    assert code == 0, out
    assert daemon.streamed == [] and daemon.created == [] and daemon.mutations == []
    # the only docker calls were the two read-only plane renders
    assert all(c[-3:] == ["config", "--format", "json"] for c in daemon.commands), daemon.commands
    assert "nothing was stopped, started or created" in out


def test_every_start_line_names_the_gate_and_its_budget(root):
    _enable(root)
    code, out = ops(root, OpsDaemon(RENDERS), "recover", "frontend", "--dry-run")
    assert code == 0, out
    # 60 s start_period + 3 x (15 s interval + 15 s timeout) + one 15 s interval + 30 s margin
    assert "#   gate [healthy]: openwebui healthy (compose healthcheck), up to 195s" in out
    assert ("#   gate [settle]: openwebui-backup running and not restarted for 15s (restart: unless-stopped; "
            "no compose healthcheck") in out


def test_gate_timeout_is_the_healthchecks_own_worst_case_and_can_be_overridden():
    svc = stack.Service("x", "x", {}, None, {"interval": "30s", "timeout": "10s", "retries": 3,
                                            "start_period": "1m0s"}, "img", ())
    assert stack.gate_timeout(svc) == 60 + 3 * 40 + 30 + stack.GATE_MARGIN_SECONDS
    assert stack.gate_timeout(svc, 12) == 12
    bare = svc._replace(healthcheck=None)
    assert stack.gate_timeout(bare) == stack.DEFAULT_GATE_TIMEOUT


@pytest.mark.parametrize("text,seconds", [("15s", 15), ("1m0s", 60), ("1m30s", 90), ("500ms", 0.5),
                                          ("1h", 3600), (None, 7), ("", 7), (15_000_000_000, 15)])
def test_parse_duration_reads_what_compose_renders(text, seconds):
    assert stack.parse_duration(text, 7) == pytest.approx(seconds)


def test_the_netns_rule_is_a_check_on_the_plan_not_only_a_consequence_of_depends_on():
    """Even with NO depends_on, network_mode alone orders the tenant after its provider."""
    render = stack.PlaneRender("frontend", "frontend", {
        "openwebui": stack.Service("openwebui", "openwebui", {}, None, None, "i", ()),
        "tailscale": stack.Service("tailscale", "tailscale", {}, "openwebui", None, "i", ()),
    }, {})
    assert stack.start_levels(render) == [["openwebui"], ["tailscale"]]
    with pytest.raises(stack.Refusal, match="must start after it"):
        stack.check_netns(render, [["openwebui", "tailscale"]])
    with pytest.raises(stack.Refusal, match="never restart one alone"):
        stack.check_netns(render, [["openwebui"]])


def test_a_depends_on_cycle_is_refused_not_looped_on():
    render = stack.PlaneRender("p", "p", {
        "a": stack.Service("a", "a", {"b": "service_started"}, None, None, "i", ()),
        "b": stack.Service("b", "b", {"a": "service_started"}, None, None, "i", ()),
    }, {})
    with pytest.raises(stack.Refusal, match="cycle"):
        stack.start_levels(render)


# --- a real recover --------------------------------------------------------------


def test_recover_stops_everything_first_then_starts_and_gates_every_container(root, fast_clock):
    _enable(root)
    daemon = OpsDaemon(RENDERS)
    code, out = ops(root, daemon, "recover")
    assert code == 0, out
    verbs = [c[c.index("--no-deps") - 2] if "--no-deps" in c else c[c.index("--timeout") - 1]
             for c in daemon.streamed]
    first_up = verbs.index("up")
    assert set(verbs[:first_up]) == {"stop"} and set(verbs[first_up:]) == {"up"}
    for name in ("openwebui", "tailscale", "llm-gateway", "llama-cpp-upstream", "tailscale-backup"):
        assert re.search(rf"\[ok\] \w+/{re.escape(name)} \({re.escape(name)}\): (healthy|running)", out), name
    assert "recovered: inference, frontend - every container passed its gate" in out


def test_recover_stops_with_a_named_refusal_at_the_first_failed_gate(root, fast_clock):
    """The acceptance's planted failure: an unhealthy container stops the run and is named."""
    _enable(root)
    unhealthy = {"Status": "running", "Health": {"Status": "unhealthy", "Log": [{"Output": "curl: (7) refused"}]}}
    daemon = OpsDaemon(RENDERS, states={"llm-queue": [{"Status": "running", "Health": {"Status": "starting"}},
                                                       unhealthy]})
    code, out = ops(root, daemon, "recover")
    assert code == stack.EXIT_REFUSED
    assert ("refused: recover stopped at inference: llm-queue (llm-queue) unhealthy - last healthcheck "
            "output: curl: (7) refused.") in out
    assert "# stopped and not started: frontend" in out
    # nothing after the failed level was started
    assert not any("--no-deps" in c and "llm-gateway" in c for c in daemon.streamed)
    assert not any("frontend/docker-compose.yml" in c and "up" in c for c in daemon.streamed)


def test_a_container_that_never_reaches_a_verdict_times_out_with_its_last_state(root, fast_clock):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, states={"openwebui": [{"Status": "running", "Health": {"Status": "starting"}}]})
    code, out = ops(root, daemon, "recover", "frontend", "--timeout", "40")
    assert code == stack.EXIT_REFUSED
    assert "openwebui (openwebui) no verdict within 40s (last seen: running/starting)" in out
    assert fast_clock[0] >= 40


def test_a_restart_loop_without_a_healthcheck_never_passes_as_running(root, fast_clock):
    """Seen 'running' between two crashes must not pass: RestartCount has to hold still."""
    _enable(root, "frontend")
    loop = [{"Status": "running", "RestartCount": n} for n in range(1, 200)]
    daemon = OpsDaemon(RENDERS, states={"tailscale-backup": loop})
    code, out = ops(root, daemon, "recover", "frontend", "--timeout", "30")
    assert code == stack.EXIT_REFUSED
    assert "tailscale-backup (tailscale-backup) restart loop: it restarted 3s into the 15s settle window" in out


def test_a_crash_8s_after_start_fails_the_settle_window_with_a_named_reason(root, fast_clock):
    """Attempt 1, attack H: a no-healthcheck sidecar that runs 8 s then exits 1 PASSED the gate
    ('running after 3s') and recover said every container passed. RED at 19ae98f."""
    _enable(root, "frontend")
    started = "2026-09-25T12:00:00Z"
    run8 = [{"Status": "running", "RestartCount": 0, "StartedAt": started}] * 3
    crash = [{"Status": "exited", "ExitCode": 1, "RestartCount": 0, "StartedAt": started}]
    daemon = OpsDaemon(RENDERS, states={"openwebui-backup": run8 + crash})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED, out
    assert ("refused: recover stopped at frontend: openwebui-backup (openwebui-backup) exited with exit code 1 "
            "9s after it was first seen running (a crash inside the settle window).") in out
    assert "every container passed its gate" not in out


def test_a_restart_that_docker_already_performed_is_caught_by_startedat(root, fast_clock):
    """unless-stopped restarts it before the next poll: Status is running again, but StartedAt moved."""
    _enable(root, "frontend")
    a = {"Status": "running", "RestartCount": 0, "StartedAt": "t0"}
    b = {"Status": "running", "RestartCount": 0, "StartedAt": "t1"}
    daemon = OpsDaemon(RENDERS, states={"openwebui-backup": [a, a, b]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "openwebui-backup (openwebui-backup) restart loop: it restarted 6s into the 15s settle window" in out


def test_restarting_status_fails_the_gate_at_once(root, fast_clock):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, states={"openwebui-backup": [{"Status": "restarting", "RestartCount": 3}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "openwebui-backup (openwebui-backup) restart loop: docker reports it restarting (RestartCount 3)" in out


def test_a_declared_restart_delay_widens_the_settle_window():
    svc = stack.Service("x", "x", {}, None, None, "img", (), {}, 40.0)
    assert stack.settle_seconds(svc) == 40.0 + stack.GATE_POLL_SECONDS
    assert stack.settle_seconds(svc._replace(restart_delay=0.0)) == stack.SETTLE_SECONDS


def test_an_exited_container_fails_its_gate_unless_it_is_a_one_shot_that_exited_0(root, fast_clock):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, states={"tailscale-backup": [{"Status": "exited", "ExitCode": 3}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "tailscale-backup (tailscale-backup) exited with exit code 3" in out
    render = json.loads(json.dumps(FRONTEND_GPU))
    render["services"]["openwebui"]["depends_on"] = {
        "tailscale-backup": {"condition": "service_completed_successfully", "required": True}}
    render["services"]["tailscale-backup"]["restart"] = "no"   # a job that can complete
    daemon = OpsDaemon({"frontend/docker-compose.yml": render},
                       states={"tailscale-backup": [{"Status": "exited", "ExitCode": 0}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert "frontend/tailscale-backup (tailscale-backup): completed (exit 0)" in out


def test_recover_one_plane_ensures_the_anchor_networks_before_starting_it(root, fast_clock):
    """F6 of ac-front-door: emergency-recovery.ps1's anchor step creates nothing; this one does."""
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, networks={})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert sorted(daemon.created) == ["ai-stack_app-net", "ai-stack_default", "ai-stack_llm-net"]
    first_up = next(i for i, c in enumerate(daemon.commands) if "--no-deps" in c)
    first_create = next(i for i, c in enumerate(daemon.commands) if c[1:3] == ["network", "create"])
    assert first_create < first_up


def test_recover_renders_every_plane_before_it_stops_anything(root):
    _enable(root)
    daemon = OpsDaemon({"inference/docker-compose.yml": INFERENCE_LOCAL},
                       exit_codes={("docker", "compose", "-f", "frontend/docker-compose.yml",
                                    "config", "--format", "json"): 1})
    code, out = ops(root, daemon, "recover")
    assert code == stack.EXIT_REFUSED
    assert "refused: could not render frontend/docker-compose.yml" in out
    assert daemon.streamed == []


def test_recover_one_plane_names_the_enabled_planes_that_depend_on_it(root):
    code, out, _ = run(root, "init", "--planes", "inference,memory,frontend", "--force")
    assert code == 0, out
    code, out = ops(root, OpsDaemon(RENDERS), "recover", "inference", "--dry-run")
    assert code == 0, out
    assert "# note: memory require inference and are not restarted by this" in out
    assert "frontend/docker-compose.yml" not in out


def test_recover_refuses_the_manual_plane(root):
    code, out = ops(root, OpsDaemon(RENDERS), "recover", "portal", "--dry-run")
    assert code == stack.EXIT_REFUSED
    assert "refused: portal is not driven by stack.py" in out


def test_recover_warns_about_a_placeholder_where_up_refuses_it(root):
    """A crashed host must be recoverable before key rotation (operator decision D1)."""
    _enable(root, "frontend")
    (root / "frontend" / ".env.example").write_text("WEBUI_SECRET_KEY=value-for-WEBUI_SECRET_KEY\n",
                                                   encoding="utf-8")
    code, out, _ = run(root, "up", "--dry-run")
    assert code == stack.EXIT_REFUSED
    code, out = ops(root, OpsDaemon(RENDERS), "recover", "--dry-run")
    assert code == 0, out
    assert "# WARNING (not a refusal for recover; `up` refuses it): frontend: WEBUI_SECRET_KEY" in out


# --- backup --------------------------------------------------------------------


@pytest.fixture
def stamped(monkeypatch):
    monkeypatch.setattr(stack, "utc_stamp", lambda: "20260925T120000Z")


def test_backup_archives_each_named_volume_the_render_mounts_and_records_its_sha256(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"webui.db bytes"})
    code, out = ops(root, daemon, "backup", "frontend")
    assert code == 0, out
    out_dir = root / "backups" / "frontend" / "manual-20260925T120000Z"
    archive = out_dir / "frontend_openwebui-data.tar.gz"
    record = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    digest = stack.hashlib.sha256(archive.read_bytes()).hexdigest()
    assert record["plane"] == "frontend" and record["format"] == 1
    assert record["volumes"] == [{"volume": "frontend_openwebui-data", "key": "openwebui-data",
                                  "archive": "frontend_openwebui-data.tar.gz",
                                  "bytes": archive.stat().st_size, "sha256": digest}]
    assert (out_dir / "SHA256SUMS").read_text(encoding="utf-8") == f"{digest}  frontend_openwebui-data.tar.gz\n"
    # a throwaway helper, read-only mount, no network
    [helper] = daemon.piped
    assert helper[:3] == ["docker", "run", "--rm"]
    assert helper[helper.index("--network") + 1] == "none" and "--name" in helper
    assert _vol(helper) == "frontend_openwebui-data:/volume:ro" and stack.HELPER_IMAGE in helper
    assert not list(out_dir.glob("*.partial"))


def test_backup_dest_writes_under_a_scratch_dir_and_leaves_backups_alone(root, stamped, tmp_path_factory):
    _enable(root, "frontend")
    scratch = tmp_path_factory.mktemp("scratch")
    code, out = ops(root, OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"x"}),
                    "backup", "frontend", "--dest", str(scratch))
    assert code == 0, out
    assert (scratch / "frontend" / "manual-20260925T120000Z" / "manifest.json").is_file()
    assert not (root / "backups").exists()


def test_backup_names_a_running_database_and_its_dump_sidecar_instead_of_tarring_it(root, stamped):
    _enable(root, "inference")
    daemon = OpsDaemon(RENDERS, volumes={"inference_llm-gateway-db-data": b"PGDATA",
                                         "inference_llm-queue-data": b"queue"},
                       states={"llm-gateway-db": [HEALTHY]})
    code, out = ops(root, daemon, "backup", "inference")
    assert code == 0, out
    assert ("[skip] inference_llm-gateway-db-data: a live database data directory (llm-gateway-db is running) "
            "- use the plane's dump for a consistent copy (llm-gateway-backup writes it)") in out
    assert [_vol(c) for c in daemon.piped] == ["inference_llm-queue-data:/volume:ro"]
    record = json.loads(next((root / "backups" / "inference").glob("*/manifest.json")).read_text("utf-8"))
    assert [v["volume"] for v in record["volumes"]] == ["inference_llm-queue-data"]
    assert record["skipped"][0]["volume"] == "inference_llm-gateway-db-data"


def test_backup_archives_a_stopped_database_as_a_cold_copy(root, stamped):
    _enable(root, "inference")
    daemon = OpsDaemon(RENDERS, volumes={"inference_llm-gateway-db-data": b"PGDATA",
                                         "inference_llm-queue-data": b"queue"},
                       states={"llm-gateway-db": [{"Status": "exited", "ExitCode": 0}]})
    code, out = ops(root, daemon, "backup", "inference")
    assert code == 0, out
    assert "inference_llm-gateway-db-data.tar.gz" in out and "(cold copy: llm-gateway-db stopped)" in out


def test_backup_skips_an_absent_volume_and_refuses_when_nothing_was_archived(root, stamped):
    _enable(root, "frontend")
    code, out = ops(root, OpsDaemon(RENDERS), "backup", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "[skip] frontend_openwebui-data: absent on this daemon" in out
    assert "refused: nothing was archived for frontend" in out
    assert not (root / "backups" / "frontend" / "manual-20260925T120000Z").exists()


def test_backup_follows_the_render_so_a_new_volume_needs_no_code_change(root, stamped):
    render = json.loads(json.dumps(FRONTEND_GPU))
    render["services"]["tailscale-backup"]["volumes"] = [
        {"type": "volume", "source": "extra", "target": "/x", "read_only": False}]
    render["volumes"]["extra"] = {"name": "frontend_extra"}
    render["volumes"]["unused"] = {"name": "frontend_unused"}
    _enable(root, "frontend")
    daemon = OpsDaemon({"frontend/docker-compose.yml": render},
                       volumes={"frontend_openwebui-data": b"a", "frontend_extra": b"b", "frontend_unused": b"c"})
    code, out = ops(root, daemon, "backup", "frontend")
    assert code == 0, out
    assert sorted(_vol(c).split(":")[0] for c in daemon.piped) == ["frontend_extra", "frontend_openwebui-data"]


def test_backup_of_the_anchor_is_refused(root):
    code, out = ops(root, OpsDaemon(RENDERS), "backup", "anchor")
    assert code == stack.EXIT_REFUSED
    assert "has no volume to back up" in out


# --- restore -------------------------------------------------------------------


def _backed_up(root, daemon, plane="frontend"):
    code, out = ops(root, daemon, "backup", plane)
    assert code == 0, out
    return next((root / "backups" / plane).glob("manual-*"))


def test_restore_round_trip_puts_the_archived_contents_back(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"marker-1234"})
    backup = _backed_up(root, daemon)
    del daemon.volumes["frontend_openwebui-data"]          # the volume is destroyed
    code, out = ops(root, daemon, "restore", "frontend", "--from", str(backup))
    assert code == 0, out
    assert daemon.volumes["frontend_openwebui-data"] == b"marker-1234"
    # recreated with the labels compose gives its own volumes, so a later `up` adopts it
    assert daemon.volume_labels["frontend_openwebui-data"] == {
        "com.docker.compose.project": "frontend", "com.docker.compose.volume": "openwebui-data"}
    assert "restored. Start the plane: python3 scripts/stack/stack.py up frontend" in out


def test_restore_verifies_the_sha256_first_and_a_tampered_archive_changes_nothing(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"good"})
    backup = _backed_up(root, daemon)
    archive = backup / "frontend_openwebui-data.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"tampered")
    daemon.mutations.clear()
    before = list(daemon.commands)
    code, out = ops(root, daemon, "restore", "frontend", "--from", str(backup))
    assert code == stack.EXIT_REFUSED
    assert "refused: archive verification failed - nothing was changed:" in out
    assert "frontend_openwebui-data.tar.gz: sha256 " in out
    assert daemon.mutations == [] and daemon.volumes["frontend_openwebui-data"] == b"good"
    assert daemon.commands == before       # it did not even ask docker anything


def test_restore_refuses_while_a_running_container_holds_the_volume(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"old"},
                       users={"frontend_openwebui-data": ["openwebui", "openwebui-backup"]})
    backup = _backed_up(root, daemon)
    daemon.volumes["frontend_openwebui-data"] = b"changed since"
    daemon.mutations.clear()
    code, out = ops(root, daemon, "restore", "frontend", "--from", str(backup))
    assert code == stack.EXIT_REFUSED
    assert "frontend_openwebui-data: in use by openwebui, openwebui-backup" in out
    assert "python3 scripts/stack/stack.py down frontend" in out
    assert daemon.mutations == [] and daemon.volumes["frontend_openwebui-data"] == b"changed since"


def test_restore_touches_only_the_volume_it_was_asked_to(root, stamped):
    _enable(root, "inference")
    daemon = OpsDaemon(RENDERS, volumes={"inference_llm-gateway-db-data": b"pg", "inference_llm-queue-data": b"q"},
                       states={"llm-gateway-db": [{"Status": "exited", "ExitCode": 0}]})
    backup = _backed_up(root, daemon, "inference")
    daemon.volumes = {"inference_llm-gateway-db-data": b"pg-now", "inference_llm-queue-data": b"q-now"}
    daemon.piped.clear()
    code, out = ops(root, daemon, "restore", "inference", "--from", str(backup), "--volume", "llm-queue-data")
    assert code == 0, out
    assert [c[c.index("-v") + 1] for c in daemon.piped] == ["inference_llm-queue-data:/volume"]
    assert daemon.volumes == {"inference_llm-gateway-db-data": b"pg-now", "inference_llm-queue-data": b"q"}


def test_restore_accepts_the_manifest_or_one_archive_as_from(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"v1"})
    backup = _backed_up(root, daemon)
    for source in (backup / "manifest.json", backup / "frontend_openwebui-data.tar.gz"):
        daemon.volumes["frontend_openwebui-data"] = b"other"
        code, out = ops(root, daemon, "restore", "frontend", "--from", str(source))
        assert code == 0, out
        assert daemon.volumes["frontend_openwebui-data"] == b"v1"


def test_restore_refuses_a_backup_of_another_plane_and_a_volume_the_plane_does_not_declare(root, stamped):
    _enable(root, "inference,frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"v"})
    backup = _backed_up(root, daemon)
    code, out = ops(root, daemon, "restore", "inference", "--from", str(backup))
    assert code == stack.EXIT_REFUSED and "is a backup of plane 'frontend', not 'inference'" in out
    code, out = ops(root, daemon, "restore", "frontend", "--from", str(backup), "--volume", "nope")
    assert code == stack.EXIT_REFUSED and "nope is not in" in out
    # a manifest edited to aim at a volume the render does not own (the sha still matches)
    record = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    record["volumes"][0]["volume"] = "someone-elses-volume"
    (backup / "manifest.json").write_text(json.dumps(record), encoding="utf-8")
    daemon.mutations.clear()
    code, out = ops(root, daemon, "restore", "frontend", "--from", str(backup))
    assert code == stack.EXIT_REFUSED
    assert "someone-elses-volume is not a named volume frontend mounts" in out
    assert daemon.mutations == []


def test_restore_without_a_manifest_is_refused(root, tmp_path_factory):
    empty = tmp_path_factory.mktemp("empty")
    code, out = ops(root, OpsDaemon(RENDERS), "restore", "frontend", "--from", str(empty))
    assert code == stack.EXIT_REFUSED
    assert "will not restore an archive it cannot verify" in out


def test_the_restore_script_extracts_into_staging_before_it_removes_anything():
    script = stack._RESTORE_SCRIPT
    assert script.index("tar -xzf - -C \"$S\"") < script.index("-exec rm -rf {}")
    assert "! -name .stack-restore-staging" in script


def test_the_real_pipe_seam_moves_bytes_through_a_command_both_ways(tmp_path):
    src, dst = tmp_path / "in.bin", tmp_path / "out.bin"
    src.write_bytes(bytes(range(256)) * 10)
    result = stack.subprocess_pipe(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"],
        tmp_path, stdin_path=src, stdout_path=dst)
    assert result.code == 0
    assert dst.read_bytes() == src.read_bytes()[::-1]


# --- stats off Windows -----------------------------------------------------------


def test_stats_off_windows_prints_container_numbers_and_says_inference_is_not_enabled(root, monkeypatch):
    monkeypatch.setattr(stack, "WINDOWS", False)
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS)
    daemon.ps_ids["frontend/docker-compose.yml"] = ["abc123", "def456"]
    daemon.stats = [{"Name": "openwebui", "CPUPerc": "1.25%", "MemUsage": "512MiB / 7.6GiB", "MemPerc": "6.58%",
                     "NetIO": "1.2kB / 3.4kB"},
                    {"Name": "openwebui-backup", "CPUPerc": "0.00%", "MemUsage": "1MiB / 7.6GiB", "MemPerc": "0.01%",
                     "NetIO": "0B / 0B"}]
    code, out = ops(root, daemon, "stats")
    assert code == 0, out
    assert re.search(r"openwebui\s+1\.25%\s+512MiB / 7\.6GiB\s+6\.58%\s+1\.2kB / 3\.4kB", out)
    assert ["docker", "stats", "--no-stream", "--format", "{{json .}}", "abc123", "def456"] in daemon.commands
    assert "== inference: NOT ENABLED on this machine" in out
    assert not any(c[:2] == ["docker", "exec"] for c in daemon.commands)


def test_stats_with_inference_enabled_reads_the_queue_board_and_the_ledger(root, monkeypatch):
    monkeypatch.setattr(stack, "WINDOWS", False)
    _enable(root)
    daemon = OpsDaemon(RENDERS)
    board = {"models": {"qwen": {"running": [{"id": "r1", "key": "owui", "model": "qwen", "elapsed_s": 4.2}],
                                 "waiting": [{"id": "w1", "key": "ob1"}], "permits_free": 0, "avg_T_s": 9.5}},
             "held_total": 2, "max_total_connections": 64}
    daemon.execs["llm-queue"] = stack.CommandResult(0, json.dumps(board), "")
    daemon.execs["llm-gateway-db"] = stack.CommandResult(0, "12:10|7|3500|1\n", "")
    code, out = ops(root, daemon, "stats")
    assert code == 0, out
    assert "qwen: running=1 waiting=1 permits_free=0 avg_T=9.5s" in out
    assert "RUNNING  r1  key=owui  model=qwen  4s elapsed" in out
    assert "12:10      7 req       3,500 tok    1 fail" in out
    assert "connections held: 2/64" in out
    sql = [c for c in daemon.commands if c[:3] == ["docker", "exec", "llm-gateway-db"]]
    assert len(sql) == 3 and all(c[-2] == "-c" for c in sql)


def test_stats_says_so_when_the_ledger_cannot_be_read_and_fails(root, monkeypatch):
    monkeypatch.setattr(stack, "WINDOWS", False)
    _enable(root)
    code, out = ops(root, OpsDaemon(RENDERS), "stats")
    assert code == stack.EXIT_REFUSED
    assert "(llm-queue unreachable" in out
    assert "(llm-gateway-db unreachable - the spend ledger could not be read)" in out


def test_stats_with_nothing_running_says_so_rather_than_printing_an_empty_table(root, monkeypatch):
    monkeypatch.setattr(stack, "WINDOWS", False)
    _enable(root, "frontend")
    code, out = ops(root, OpsDaemon(RENDERS), "stats")
    assert code == 0, out
    assert "no running container in the enabled planes" in out


def test_a_container_without_a_healthcheck_passes_once_it_is_steady(root, fast_clock):
    """RED on the first cut: RestartCount lives OUTSIDE docker's .State, the gate read it from
    .State, got None every poll, and a steady no-healthcheck container timed out (found by the
    DinD rehearsal, 2026-09-25: openwebui-backup, 'no verdict within 300s (last seen: running)')."""
    _enable(root, "frontend")
    steady = [{"Status": "running", "RestartCount": 2}]
    daemon = OpsDaemon(RENDERS, states={"tailscale-backup": steady, "openwebui-backup": steady})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert ("frontend/openwebui-backup (openwebui-backup): running and steady for 15s (no healthcheck; "
            "RestartCount 2, not restarted)") in out
    assert fast_clock[0] < 60


# --- attempt 2: what attempt 1's tester broke ---------------------------------


OB1_RENDER = {"name": "open-brain", "services": {
    "openbrain-db": _svc("pgvector/pgvector:pg16", "openbrain-db", health=True,
                         volumes=[("openbrain-db-data", "/var/lib/postgresql/data", False)]),
    "openbrain-db-backup": {**_svc("openbrain-db-backup:local", "openbrain-db-backup"),
                            "environment": {"PGHOST": "openbrain-db", "PGPORT": "5432"}},
    "openbrain-postgrest": {**_svc("postgrest/postgrest:v12", "openbrain-postgrest"),
                            "environment": {"PGRST_DB_URI": "postgres://u:p@openbrain-db:5432/openbrain"}},
}, "volumes": {"openbrain-db-data": {"name": "open-brain_openbrain-db-data"}}}


def test_a_dump_sidecar_is_found_without_depends_on_through_its_host_variable():
    """Attempt 1, attack K: openbrain-db-backup has no depends_on, only PGHOST=openbrain-db.
    RED at 19ae98f (the sidecar list was empty and backup said 'no dump sidecar')."""
    render = stack.PlaneRender("ob1", "open-brain", {}, {})
    services = {}
    for key, spec in OB1_RENDER["services"].items():
        services[key] = stack.Service(
            key, spec.get("container_name"), {k: v["condition"] for k, v in (spec.get("depends_on") or {}).items()},
            None, spec.get("healthcheck"), spec["image"],
            tuple(("volume", m["source"], m["target"], m["read_only"]) for m in spec.get("volumes", [])),
            dict(spec.get("environment") or {}))
    render = render._replace(services=services, volumes={"openbrain-db-data": {"name": "x", "external": False}})
    [vol] = stack.plane_volumes(render)
    assert vol["engines"] == ["openbrain-db"]
    # postgrest names the db host too, but it is not a backup/dump service
    assert vol["sidecars"] == ["openbrain-db-backup"]


@pytest.mark.parametrize("value,host,hit", [
    ("openbrain-db", "openbrain-db", True), ("openbrain-db:5432", "openbrain-db", True),
    ("http://surrealdb:8000", "surrealdb", True), ("postgres://u:p@mattermost-db/mm", "mattermost-db", True),
    ("openbrain-db-backup", "openbrain-db", False), ("my-openbrain-db", "openbrain-db", False)])
def test_names_host_matches_a_host_not_a_substring(value, host, hit):
    assert stack._names_host(value, host) is hit


def test_a_live_copy_is_recorded_in_the_manifest_not_only_printed(root, stamped):
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"db"},
                       users={"frontend_openwebui-data": ["openwebui"]})
    code, out = ops(root, daemon, "backup", "frontend")
    assert code == 0, out
    record = json.loads(next((root / "backups" / "frontend").glob("*/manifest.json")).read_text("utf-8"))
    live = record["volumes"][0]["live_copy"]
    assert live["running"] == ["openwebui"] and "SQLite" in live["warning"]


def test_a_failed_helper_is_removed_by_name_before_backup_returns(root, stamped):
    """Attempt 1, attack F: a backup onto a full disk left its helper alive, holding the volume."""
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS, volumes={"frontend_openwebui-data": b"db"})
    daemon.pipe_codes = [1]
    code, out = ops(root, daemon, "backup", "frontend")
    assert code == stack.EXIT_REFUSED
    [helper] = daemon.piped
    name = helper[helper.index("--name") + 1]
    assert daemon.removed == [name]
    assert f"[ok]   helper {name} removed" in out
    assert not list((root / "backups").rglob("*.partial"))


def test_stats_keeps_the_table_when_one_container_vanishes_mid_run(root, monkeypatch):
    """Attempt 1, attack G: one removed container cost the whole table."""
    monkeypatch.setattr(stack, "WINDOWS", False)
    _enable(root, "frontend")
    daemon = OpsDaemon(RENDERS)
    daemon.ps_ids["frontend/docker-compose.yml"] = ["aaa", "bbb"]
    daemon.stats = [{"ID": "aaa", "Name": "openwebui", "CPUPerc": "1.00%", "MemUsage": "1MiB / 2GiB",
                     "MemPerc": "0.05%", "NetIO": "0B / 0B"}]
    daemon.stats_gone = {"bbb"}
    code, out = ops(root, daemon, "stats")
    assert code == 0, out
    assert re.search(r"openwebui\s+1\.00%", out)
    assert "(gone: the container disappeared between `compose ps` and `docker stats`)" in out


# --- attempt 3: one-shot and init services (tester, attempt 2: R1, R2) -----------


def _with_init(runs_for=None, dependant=False):
    """FRONTEND_GPU plus `init-once` (restart "no"); optionally tailscale-backup waits on it."""
    render = json.loads(json.dumps(FRONTEND_GPU))
    render["services"]["init-once"] = _svc("alpine:3.21", restart="no")
    if dependant:
        render["services"]["tailscale-backup"]["depends_on"] = {
            "init-once": {"condition": "service_completed_successfully", "required": True}}
    return {"frontend/docker-compose.yml": render}


def test_the_gate_kind_is_derived_from_the_render():
    render = stack.PlaneRender("frontend", "frontend", {
        "db": stack.Service("db", "db", {}, None, {"test": ["CMD", "true"]}, "i", (), {}, 0.0, "always"),
        "init": stack.Service("init", "init", {}, None, None, "i", (), {}, 0.0, "no"),
        "bare": stack.Service("bare", "bare", {}, None, None, "i", (), {}, 0.0, ""),
        "side": stack.Service("side", "side", {}, None, None, "i", (), {}, 0.0, "unless-stopped"),
        "app": stack.Service("app", "app", {"init": "service_completed_successfully"}, None, None, "i", (), {},
                             0.0, "unless-stopped"),
    }, {})
    assert {k: stack.gate_kind(render, k) for k in render.services} == {
        "db": "healthy", "init": "completes", "bare": "one-shot", "side": "settle", "app": "settle"}


def test_a_restart_no_one_shot_that_exits_0_passes(root, fast_clock):
    """R1: attempt 2 refused an init job's exit 0 as 'a crash inside the settle window'. RED at 00e80e3."""
    _enable(root, "frontend")
    run2 = [{"Status": "running", "RestartCount": 0, "StartedAt": "t0"}]
    done = [{"Status": "exited", "ExitCode": 0, "RestartCount": 0, "StartedAt": "t0"}]
    daemon = OpsDaemon(_with_init(), states={"frontend-init-once-1": run2 + done})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert 'frontend/init-once (frontend-init-once-1): exited 0 after 3s (restart: "no" - a one-shot' in out
    assert "crash" not in out


def test_a_restart_no_one_shot_that_exits_non_zero_fails_by_name(root, fast_clock):
    _enable(root, "frontend")
    daemon = OpsDaemon(_with_init(), states={"frontend-init-once-1": [{"Status": "exited", "ExitCode": 2}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "refused: recover stopped at frontend: init-once (frontend-init-once-1) exited with exit code 2" in out


def test_a_completion_dependency_must_exit_0_before_its_dependant_starts(root, fast_clock):
    """R2: attempt 2 passed a still-running init job after its settle window and started the
    service that waits on it (service_completed_successfully) 3.4 s early. RED at 00e80e3."""
    _enable(root, "frontend")
    running = [{"Status": "running", "RestartCount": 0, "StartedAt": "t0"}] * 10   # ~30 s, past the window
    done = [{"Status": "exited", "ExitCode": 0, "RestartCount": 0, "StartedAt": "t0"}]
    daemon = OpsDaemon(_with_init(dependant=True), states={"frontend-init-once-1": running + done})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert "frontend/init-once (frontend-init-once-1): completed (exit 0) after 30s" in out
    ups = [c for c in daemon.streamed if "--no-deps" in c]
    init_level = next(i for i, c in enumerate(ups) if "init-once" in c)
    dependant_level = next(i for i, c in enumerate(ups) if "tailscale-backup" in c)
    assert init_level < dependant_level
    # and the dependant's `up` came only after the last inspect of init-once (the exit)
    last_inspect = max(i for i, c in enumerate(daemon.commands) if c[:2] == ["docker", "inspect"]
                       and c[-1] == "frontend-init-once-1")
    dependant_up = next(i for i, c in enumerate(daemon.commands) if "--no-deps" in c and "tailscale-backup" in c)
    assert last_inspect < dependant_up


def test_a_completion_dependency_that_fails_or_never_finishes_is_a_named_refusal(root, fast_clock):
    _enable(root, "frontend")
    daemon = OpsDaemon(_with_init(dependant=True), states={"frontend-init-once-1": [{"Status": "exited",
                                                                                      "ExitCode": 1}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED
    assert ("init-once (frontend-init-once-1) exited with exit code 1 - a service others wait on with "
            "service_completed_successfully must exit 0") in out
    assert not any("--no-deps" in c and "tailscale-backup" in c for c in daemon.streamed)
    daemon = OpsDaemon(_with_init(dependant=True), states={"frontend-init-once-1": [{"Status": "running"}]})
    code, out = ops(root, daemon, "recover", "frontend", "--timeout", "20")
    assert code == stack.EXIT_REFUSED
    assert "init-once (frontend-init-once-1) did not complete within 20s (last seen: running)" in out


def test_the_dry_run_prints_each_gate_kind(root):
    _enable(root, "frontend")
    code, out = ops(root, OpsDaemon(_with_init(dependant=True)), "recover", "frontend", "--dry-run")
    assert code == 0, out
    assert ("#   gate [completes]: frontend-init-once-1 exits 0 (another service waits on it with "
            "service_completed_successfully)") in out
    assert "#   gate [healthy]: openwebui healthy" in out
    assert "#   gate [settle]: openwebui-backup running and not restarted for 15s" in out
    code, out = ops(root, OpsDaemon(_with_init()), "recover", "frontend", "--dry-run")
    assert '#   gate [one-shot]: frontend-init-once-1 exits 0, or runs unrestarted for 15s (restart: no' in out


# --- ac-ops-portable2: depends_on conditions compose would refuse (review, 2026-09-25) ---


def _healthy_on_the_backup():
    """The reviewer's planted case: tailscale-backup waits service_healthy on openwebui-backup,
    which has no healthcheck (alpine:3.21 has none either)."""
    render = json.loads(json.dumps(FRONTEND_GPU))
    render["services"]["tailscale-backup"]["depends_on"] = {
        "openwebui-backup": {"condition": "service_healthy", "required": True}}
    return {"frontend/docker-compose.yml": render}


@pytest.mark.parametrize("dry", [True, False])
def test_service_healthy_on_a_target_without_a_healthcheck_is_refused_before_anything_stops(root, fast_clock, dry):
    """RED at c2e5560: it waited out the settle window and printed 'recovered'."""
    _enable(root, "frontend")
    daemon = OpsDaemon(_healthy_on_the_backup())
    daemon.images["alpine:3.21"] = None
    code, out = ops(root, daemon, "recover", "frontend", *(["--dry-run"] if dry else []))
    assert code == stack.EXIT_REFUSED, out
    assert ("frontend/tailscale-backup depends_on openwebui-backup with condition service_healthy, but "
            "openwebui-backup has no healthcheck (none in the compose file, none in its image alpine:3.21)") in out
    assert "Nothing was stopped." in out
    assert "recovered" not in out
    assert daemon.streamed == [] and daemon.created == []     # no stop, no up, no network create
    assert not any(c[:3] == ["docker", "compose", "-f"] and ("stop" in c or "up" in c) for c in daemon.commands)


def test_an_image_healthcheck_satisfies_service_healthy(root, fast_clock):
    """Compose accepts service_healthy on a target whose IMAGE declares the healthcheck."""
    _enable(root, "frontend")
    daemon = OpsDaemon(_healthy_on_the_backup())
    daemon.images["alpine:3.21"] = {"Test": ["CMD-SHELL", "true"], "Interval": 5000000000}
    code, out = ops(root, daemon, "recover", "frontend", "--dry-run")
    assert code == 0, out


def test_an_image_not_on_the_daemon_is_a_warning_not_a_refusal(root):
    _enable(root, "frontend")
    code, out = ops(root, OpsDaemon(_healthy_on_the_backup()), "recover", "frontend", "--dry-run")
    assert code == 0, out
    assert ("# WARNING: frontend/tailscale-backup depends_on openwebui-backup with condition service_healthy; "
            "openwebui-backup has no compose healthcheck and its image alpine:3.21 is not on this daemon") in out


def test_a_disabled_healthcheck_under_service_healthy_is_refused_without_asking_the_image(root):
    render = _healthy_on_the_backup()
    render["frontend/docker-compose.yml"]["services"]["openwebui-backup"]["healthcheck"] = {"disable": True}
    _enable(root, "frontend")
    daemon = OpsDaemon(render)
    code, out = ops(root, daemon, "recover", "frontend", "--dry-run")
    assert code == stack.EXIT_REFUSED
    assert "but openwebui-backup disables its healthcheck in the compose file" in out
    assert not any(c[1:3] == ["image", "inspect"] for c in daemon.commands)


@pytest.mark.parametrize("policy", ["always", "unless-stopped"])
def test_service_completed_successfully_on_a_restarting_target_is_refused(root, policy):
    """A target docker restarts after it exits can never 'complete'."""
    _enable(root, "frontend")
    daemon = OpsDaemon(_with_init(dependant=True))
    daemon.plane_renders["frontend/docker-compose.yml"]["services"]["init-once"]["restart"] = policy
    code, out = ops(root, daemon, "recover", "frontend", "--dry-run")
    assert code == stack.EXIT_REFUSED
    assert (f"frontend/tailscale-backup depends_on init-once with condition service_completed_successfully, "
            f"but init-once has restart: {policy}") in out
    assert daemon.streamed == []


def test_a_timing_only_healthcheck_asks_the_image_and_is_refused_when_it_has_none(root):
    """ac-ops-portable2 attempt 1, attack i: `healthcheck: {interval: 5s}` with no `test` was
    counted as a healthcheck. RED at 7f886cf."""
    render = _healthy_on_the_backup()
    render["frontend/docker-compose.yml"]["services"]["openwebui-backup"]["healthcheck"] = {"interval": "5s"}
    _enable(root, "frontend")
    daemon = OpsDaemon(render)
    daemon.images["alpine:3.21"] = None
    code, out = ops(root, daemon, "recover", "frontend", "--dry-run")
    assert code == stack.EXIT_REFUSED, out
    assert "but openwebui-backup has no healthcheck (none in the compose file, none in its image alpine:3.21)" in out
    # and on an image WITH a healthcheck the same timing-only block is fine (compose merges it)
    daemon = OpsDaemon(render)
    daemon.images["alpine:3.21"] = {"Test": ["CMD-SHELL", "true"]}
    code, out = ops(root, daemon, "recover", "frontend", "--dry-run")
    assert code == 0, out


def test_an_image_pulled_during_recover_is_checked_before_its_service_healthy_dependant_starts(root, fast_clock):
    """ac-ops-portable2 attempt 1, attack h: the image was not local, recover warned, stopped the
    plane, pulled, and printed 'recovered' - compose pulls and then refuses. RED at 7f886cf."""
    _enable(root, "frontend")
    # alpine:3.21 is not "on this daemon"; after the pull its container has no Health at all
    daemon = OpsDaemon(_healthy_on_the_backup(),
                       states={"openwebui-backup": [{"Status": "running", "RestartCount": 0, "StartedAt": "t0"}]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == stack.EXIT_REFUSED, out
    assert "is decided after the pull" in out
    assert ("refused: recover stopped at frontend: tailscale-backup depends_on openwebui-backup with condition "
            "service_healthy, but openwebui-backup (openwebui-backup) has no healthcheck - decided after its image "
            "was pulled") in out
    assert "recovered" not in out
    assert not any("--no-deps" in c and "tailscale-backup" in c for c in daemon.streamed)


def test_the_runtime_check_passes_a_target_whose_pulled_image_has_a_healthcheck(root, fast_clock):
    _enable(root, "frontend")
    healthy = {"Status": "running", "Health": {"Status": "healthy"}, "RestartCount": 0}
    daemon = OpsDaemon(_healthy_on_the_backup(), states={"openwebui-backup": [healthy]})
    code, out = ops(root, daemon, "recover", "frontend")
    assert code == 0, out
    assert any("--no-deps" in c and "tailscale-backup" in c for c in daemon.streamed)


# --- docs: the generated blocks (ac-doc-generator) ---------------------------
#
# Every block renderer and the marker scanner, against MINI_MANIFEST and a
# scripted render that honours --profile the way compose does: a service with
# no profiles is always in, a profiled one only when one of its profiles is
# passed, and two selected services sharing a container_name make the render
# fail - which is what "mutually exclusive" looks like to the generator.

DOCS_RENDER = {
    "docker-compose.yml": {"name": "ai-stack", "services": {}},
    "inference/docker-compose.yml": {"name": "inference", "services": {
        "llm-gateway": {"container_name": "llm-gateway", "networks": {"llm-net": None}},
        "llama-cpp-upstream": {"container_name": "llama-cpp-upstream", "profiles": ["local"],
                               "ports": [{"host_ip": "127.0.0.1", "published": "8081", "target": 8080}]},
    }, "networks": {"llm-net": {"name": "ai-stack_llm-net", "external": True}}},
    "frontend/docker-compose.yml": {"name": "frontend", "services": {
        "openwebui": {"container_name": "openwebui",
                      "ports": [{"host_ip": "127.0.0.1", "published": "3000", "target": 8080}]},
    }},
    "memory/docker-compose.yml": {"name": "memory", "services": {
        "mnemory-cloud-gateway": {"container_name": "mnemory-cloud-gateway",
                                  "ports": [{"host_ip": "127.0.0.1", "published": "8060", "target": 8060}]},
    }},
    "search/docker-compose.yml": {"name": "search", "services": {
        "gateway": {"container_name": "search-gateway", "networks": {"search-net": None},
                    "ports": [{"host_ip": "127.0.0.1", "published": "8085", "target": 8080}]},
    }, "networks": {"search-net": {"name": "search_search-net", "internal": True}}},
    "coder/docker-compose.yml": {"name": "coder", "services": {
        "little-coder": {"container_name": "little-coder",
                         "ports": [{"host_ip": "127.0.0.1", "published": "9091", "target": 9090}]},
    }},
    "OB1/docker/docker-compose.yml": {"name": "open-brain", "services": {
        "openbrain-db": {"container_name": "openbrain-db"},
        "openbrain-idea-refinery": {"container_name": "openbrain-idea-refinery", "profiles": ["idea-refinery"]},
    }},
    "agent-org/docker/docker-compose.yml": {"name": "agent-org", "services": {
        "mattermost": {"container_name": "mattermost",
                       "ports": [{"host_ip": "127.0.0.1", "published": "8065", "target": 8065}]},
        "agent-bridge": {"container_name": "agent-bridge",
                         "ports": [{"host_ip": "127.0.0.1", "published": "8830", "target": 8000}]},
        "ao-worker-1": {"container_name": "ao-worker-1", "profiles": ["workers"]},
        "ao-egress": {"container_name": "ao-egress", "profiles": ["cloud"]},
    }},
    "portal/docker-compose.yml": {"name": "portal", "services": {
        "caddy": {"container_name": "caddy"},
        "cloudflared": {"container_name": "cloudflared", "profiles": ["internet"]},
    }},
}


class DocsHost:
    """`docker compose config` for the docs generator, honouring --profile."""

    def __init__(self, render=None):
        self.render = json.loads(json.dumps(render if render is not None else DOCS_RENDER))
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd):
        self.calls.append(list(cmd))
        spec = self.render[cmd[cmd.index("-f") + 1]]
        if "--profiles" in cmd:
            found = sorted({p for s in spec["services"].values() for p in s.get("profiles", [])})
            return stack.CommandResult(0, "\n".join(found), "")
        passed = {cmd[i + 1] for i, part in enumerate(cmd) if part == "--profile"}
        chosen, names = {}, set()
        for key, service in spec["services"].items():
            profiles = service.get("profiles") or []
            if profiles and not (set(profiles) & passed):
                continue
            if service["container_name"] in names:
                return stack.CommandResult(1, "", f'container name "{service["container_name"]}" is already in use')
            names.add(service["container_name"])
            chosen[key] = service
        return stack.CommandResult(0, json.dumps({"name": spec["name"], "services": chosen,
                                                  "networks": spec.get("networks", {})}), "")


DOC_TEXT = """# a doc

Prose before. OWUI alone is <!-- stack:count:frontend:bare --><!-- /stack:count:frontend:bare -->.

<!-- stack:plane-table -->
<!-- /stack:plane-table -->

<!-- stack:profile-counts:agent-org -->
<!-- /stack:profile-counts:agent-org -->

<!-- stack:plane-services:search -->
<!-- /stack:plane-services:search -->

Workers make it <!-- stack:count:agent-org:workers --><!-- /stack:count:agent-org:workers -->.
Profiles: <!-- stack:profiled-planes --><!-- /stack:profiled-planes -->.

Prose after.
"""

DOC_BLOCKS = ["count:frontend:bare", "plane-table", "profile-counts:agent-org", "plane-services:search",
              "count:agent-org:workers", "profiled-planes"]


@pytest.fixture
def docs_root(mini_root: Path, monkeypatch) -> Path:
    (mini_root / "DOC.md").write_text(DOC_TEXT, encoding="utf-8")
    manifest = stack.Manifest.load(mini_root / stack.MANIFEST_NAME)
    for plane in manifest.order:   # the docs render ONLY from committed examples
        stack.example_path(manifest.env_path(mini_root, plane)).write_text("", encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": list(DOC_BLOCKS)})
    return mini_root


def docs(root: Path, *args, host=None):
    out = io.StringIO()
    host = host or DocsHost()
    code = stack.main(["--root", str(root), "docs", *args], stdout=out, capture=host)
    return code, out.getvalue(), host


def doc(root: Path) -> str:
    return (root / "DOC.md").read_text(encoding="utf-8")


def test_docs_write_fills_every_block_and_check_then_passes(docs_root):
    code, out, _h = docs(docs_root, "--write")
    assert code == 0, out
    text = doc(docs_root)
    assert "OWUI alone is <!-- stack:count:frontend:bare -->**1** services with no profile<!--" in text
    assert "Workers make it <!-- stack:count:agent-org:workers -->**3** services with `workers`<!--" in text
    # every count in the plane table carries its condition
    assert ("| **agent-org** (`agent-org`) | `agent-org/docker/docker-compose.yml` | 2 with no profile; "
            "3 with `workers`; 3 with `cloud`; 4 with every profile (`workers`, `cloud`) |") in text
    assert "| **anchor** (`ai-stack`) | `docker-compose.yml` | 0 with no profile | none |" in text
    assert "by hand: `scripts/portal/portal-on.ps1`" in text
    assert "| `workers` | 3 | `ao-worker-1` |" in text
    assert ("| `gateway` | `search-gateway` | *(none)* | `127.0.0.1:8085->8080` | `search_search-net` (internal) |"
            in text)
    assert "`.env.example` and `COMPOSE_PROFILES` cleared" in text
    assert text.startswith("# a doc\n\nProse before.") and text.endswith("\nProse after.\n")
    code, out, _h = docs(docs_root, "--check")
    assert code == 0, out
    assert "6 block(s) in 1 file(s) match" in out


def test_docs_check_names_each_stale_block_after_a_service_is_added(docs_root):
    assert docs(docs_root, "--write")[0] == 0
    before = doc(docs_root)
    host = DocsHost()
    host.render["agent-org/docker/docker-compose.yml"]["services"]["ao-worker-2"] = {
        "container_name": "ao-worker-2", "profiles": ["workers"]}
    code, out, _h = docs(docs_root, "--check", host=host)
    assert code == stack.EXIT_REFUSED, out
    for name in ("plane-table", "profile-counts:agent-org", "count:agent-org:workers"):
        assert f"block `{name}` is STALE" in out, out
    assert "block `plane-services:search` is STALE" not in out
    assert doc(docs_root) == before, "--check wrote to the file"
    assert docs(docs_root, "--write", host=host)[0] == 0
    code, out, _h = docs(docs_root, "--check", host=host)
    assert code == 0, out
    assert "**4** services with `workers`" in doc(docs_root)


def test_a_hand_edit_inside_a_block_is_stale(docs_root):
    assert docs(docs_root, "--write")[0] == 0
    text = doc(docs_root).replace("**1** services with no profile", "**2** services with no profile")
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "block `count:frontend:bare` is STALE" in out


@pytest.mark.parametrize("breakage, expected", [
    # both markers deleted: nothing would regenerate it
    (lambda t: t.replace("<!-- stack:plane-table -->\n<!-- /stack:plane-table -->\n", ""),
     "block `plane-table` is MISSING"),
    # the closing marker deleted
    (lambda t: t.replace("<!-- /stack:plane-table -->\n", ""), "block `plane-table` is never closed"),
    # the opening marker deleted: an orphan close
    (lambda t: t.replace("<!-- stack:plane-table -->\n", ""), "closes a block that was never opened"),
    # a mangled marker is not ordinary text
    (lambda t: t.replace("<!-- stack:plane-table -->", "<!-- stack: plane-table -->"), "a malformed `stack:` marker"),
    # a close for a different block
    (lambda t: t.replace("<!-- /stack:plane-table -->", "<!-- /stack:product-menu -->"),
     "block `plane-table` is closed by `/stack:product-menu`"),
    # a multi-line block's marker shares its line with prose
    (lambda t: t.replace("<!-- stack:plane-table -->", "Table: <!-- stack:plane-table -->"),
     "both of its markers must stand alone"),
])
def test_a_broken_or_missing_marker_pair_fails_loudly_in_both_modes(docs_root, breakage, expected):
    (docs_root / "DOC.md").write_text(breakage(DOC_TEXT), encoding="utf-8")
    for mode in ("--check", "--write"):
        code, out, _h = docs(docs_root, mode)
        assert code == stack.EXIT_REFUSED, out
        assert expected in out, out
    assert doc(docs_root) == breakage(DOC_TEXT), "a refused --write changed the file"


def test_an_unregistered_block_is_refused(docs_root):
    text = DOC_TEXT + "\n<!-- stack:product-menu -->\n<!-- /stack:product-menu -->\n"
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "block `product-menu` is not registered for this file in DOCS_BLOCKS" in out


def test_a_block_naming_an_unknown_plane_or_the_wrong_shape_is_refused(docs_root, monkeypatch):
    text = (DOC_TEXT + "\n<!-- stack:plane-services:nope -->\n<!-- /stack:plane-services:nope -->\n"
            "One line: <!-- stack:plane-services:coder --><!-- /stack:plane-services:coder -->\n")
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS + ["plane-services:nope",
                                                                     "plane-services:coder"]})
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "`nope` is not a plane" in out
    assert "block `plane-services:coder` is multi: its markers go on lines of their own" in out


def test_a_registered_file_that_is_gone_fails(docs_root, monkeypatch):
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS, "GONE.md": ["plane-table"]})
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "GONE.md: the file is gone" in out


def test_a_plane_that_cannot_be_rendered_here_is_not_verified_and_left_alone(docs_root, monkeypatch):
    text = DOC_TEXT + "\n<!-- stack:profile-counts:ob1 -->\nkept as committed\n<!-- /stack:profile-counts:ob1 -->\n"
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS + ["profile-counts:ob1"]})
    (docs_root / "OB1" / "docker" / "docker-compose.yml").unlink()
    code, out, _h = docs(docs_root, "--write")
    assert code == stack.EXIT_UNVERIFIED, out
    assert "NOT VERIFIED - DOC.md" in out and "OB1/docker/docker-compose.yml is not on disk" in out
    assert "kept as committed" in doc(docs_root)
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_UNVERIFIED, out
    # never written, so there is no committed ob1 row to keep: the table is not verified at all
    assert ("`plane-table`: the ob1 row (OB1/docker/docker-compose.yml is not on disk) - and the committed "
            "block has no such row to keep") in out
    code, out, _h = docs(docs_root, "--check", "--allow-unverified")
    assert code == 0, out
    assert "not a failure" in out


def test_a_missing_gitignored_env_is_unverified_only_where_the_render_needs_it(docs_root):
    assert docs(docs_root, "--write")[0] == 0
    (docs_root / "agent-org" / "docker" / ".env").unlink()
    host = DocsHost()

    def env_file_stat(cmd, cwd):   # compose stats a service-level `env_file: .env`
        if "agent-org/docker/docker-compose.yml" in cmd:
            return stack.CommandResult(1, "", "env file agent-org/docker/.env not found")
        return host(cmd, cwd)

    code, out, _h = docs(docs_root, "--check", host=env_file_stat)
    assert code == stack.EXIT_UNVERIFIED, out
    assert "agent-org/docker/.env is absent (gitignored)" in out
    unverified = [line for line in out.splitlines() if "NOT VERIFIED" in line]
    assert any("`profile-counts:agent-org`" in line for line in unverified)
    # the search block needs no agent-org render and is still compared
    assert not any("`plane-services:search`" in line for line in unverified)
    # plane-table is compared row by row: every row but agent-org's
    assert "PARTLY VERIFIED - DOC.md:5 `plane-table`: every row compared except the agent-org row" in out
    assert "4 block(s) in 1 file(s) match" in out


def test_a_plane_with_no_env_example_is_refused_not_rendered_from_the_host_env(docs_root):
    stack.example_path(docs_root / "search" / ".env").unlink()
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "search/.env.example does not exist" in out


def test_the_render_uses_the_example_env_and_passes_no_unasked_profile(docs_root):
    _code, _out, host = docs(docs_root, "--write")
    renders = [c for c in host.calls if "--format" in c]
    assert renders, "no render was issued"
    for cmd in renders:
        assert cmd[cmd.index("--env-file") + 1].endswith(".env.example"), cmd
    bare = [c for c in renders if "agent-org/docker/docker-compose.yml" in c and "--profile" not in c]
    assert bare, "the no-profile render was never issued"


def test_mutually_exclusive_profiles_render_as_a_named_refusal_row(docs_root, monkeypatch):
    manifest = MINI_MANIFEST.replace(
        '[planes.frontend.profiles.gpu]\ndescription = "the CUDA image and the device reservation"\npending     = true',
        '[planes.frontend.profiles.gpu]\ndescription = "the CUDA image"\nopt_in = true\n'
        '[planes.frontend.profiles.stock]\ndescription = "the upstream image"\nopt_in = true')
    assert manifest != MINI_MANIFEST
    (docs_root / stack.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    host = DocsHost()
    host.render["frontend/docker-compose.yml"]["services"] = {
        "openwebui": {"container_name": "openwebui", "profiles": ["gpu"]},
        "openwebui-stock": {"container_name": "openwebui", "profiles": ["stock"]},
        "openwebui-backup": {"container_name": "openwebui-backup"},
    }
    text = DOC_TEXT + "\n<!-- stack:profile-counts:frontend -->\n<!-- /stack:profile-counts:frontend -->\n"
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS + ["profile-counts:frontend"]})
    code, out, _h = docs(docs_root, "--write", host=host)
    assert code == 0, out
    written = doc(docs_root)
    assert "| `gpu` | 2 | `openwebui` |" in written
    assert "| `stock` | 2 | `openwebui-stock` |" in written
    assert "| every profile (`gpu`, `stock`) | does not render - compose refuses the combination | - |" in written
    assert "**1** services with no profile" in written


def test_a_count_naming_an_unknown_profile_is_refused(docs_root, monkeypatch):
    text = DOC_TEXT + "\nX <!-- stack:count:agent-org:nope --><!-- /stack:count:agent-org:nope -->\n"
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS + ["count:agent-org:nope"]})
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "names profile(s) nope" in out


def test_a_block_carrying_a_host_secret_or_the_checkout_path_is_refused(docs_root):
    (docs_root / "agent-org" / "docker" / ".env").write_text("AO_DB_PASSWORD=hunter2hunter2\n", encoding="utf-8")
    host = DocsHost()
    host.render["search/docker-compose.yml"]["services"]["gateway"]["container_name"] = "hunter2hunter2"
    code, out, _h = docs(docs_root, "--write", host=host)
    assert code == stack.EXIT_REFUSED
    assert "would contain the value of AO_DB_PASSWORD (from agent-org/docker/.env)" in out
    assert "hunter2hunter2" not in out.split("refused:", 1)[1]
    assert "hunter2hunter2" not in doc(docs_root)
    host = DocsHost()
    host.render["search/docker-compose.yml"]["services"]["gateway"]["container_name"] = str(docs_root)
    code, out, _h = docs(docs_root, "--write", host=host)
    assert code == stack.EXIT_REFUSED
    assert "this checkout's absolute path" in out


def test_a_value_the_example_ships_is_not_a_host_leak(docs_root):
    env = docs_root / "agent-org" / "docker" / ".env"
    env.write_text("AO_DB_PASSWORD=shipped-placeholder\n", encoding="utf-8")
    env.with_name(".env.example").write_text("AO_DB_PASSWORD=shipped-placeholder\n", encoding="utf-8")
    host = DocsHost()
    host.render["search/docker-compose.yml"]["services"]["gateway"]["container_name"] = "shipped-placeholder"
    code, out, _h = docs(docs_root, "--write", host=host)
    assert code == 0, out


def test_docs_needs_exactly_one_of_write_and_check(docs_root):
    for args in ((), ("--write", "--check")):
        code, out, _h = docs(docs_root, *args)
        assert code == stack.EXIT_REFUSED
        assert "exactly one of --write" in out


def test_render_capture_scrubs_the_environment(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kwargs):
        seen.update(kwargs["env"])
        return Done()

    monkeypatch.setattr(stack.subprocess, "run", fake_run)
    monkeypatch.setenv("COMPOSE_PROFILES", "gpu,tailscale")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "a-host-value")
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    stack.render_capture(["docker", "compose", "config"], ".")
    assert seen["COMPOSE_PROFILES"] == ""
    assert "WEBUI_SECRET_KEY" not in seen
    assert seen["DOCKER_CONTEXT"] == "default"
    assert any(k.upper() == "PATH" for k in seen)


def test_the_product_menu_is_what_enable_resolves(tmp_path):
    manifest = stack.Manifest.load(REAL_MANIFEST)
    menu = stack.DocsGenerator(manifest, tmp_path, DocsHost()).product_menu()
    rows = {line.split("|")[1].strip(): line for line in menu.splitlines() if line.startswith("| **")}
    assert set(rows) == {f"**{name}**" for name in manifest.products}
    # open-brain enables its `default = true` idea-refinery AND that profile's `requires`
    assert "ob1: `idea-refinery`, `research`, `wiki`" in rows["**open-brain**"]
    assert "anchor, frontend, portal *(manual)*" in rows["**portal**"]
    # keys are the union over every plane started, not only the product's own
    assert "`LITELLM_MASTER_KEY`" in rows["**memory**"] and "`MCP_API_KEY`" in rows["**memory**"]
    for name in manifest.products:
        full, _profiles, _dp, _dprof = stack.product_plan(manifest, name, headless=False)
        assert rows[f"**{name}**"].split("|")[3].strip().replace(" *(manual)*", "") == ", ".join(full)


def test_enable_still_writes_what_the_menu_says(root):
    """product_plan() was factored out of cmd_enable; the state it writes is the menu's row."""
    code, out, _r = run(root, "enable", "open-brain")
    assert code == 0, out
    state = json.loads((root / ".stack" / "state.json").read_text(encoding="utf-8"))
    assert state["planes"]["ob1"]["profiles"] == ["idea-refinery", "research", "wiki"]


def test_the_probe_catalogue_is_the_sweep_itself():
    catalogue = stack.probe_catalogue()
    assert len(catalogue) == len(PS1_PROBES)
    order = list(stack.HealthSweep.PROBED_PLANES)
    planes = [plane for plane, _label, _needs in catalogue]
    assert planes == sorted(planes, key=order.index)
    conditional = {label: needs for _p, label, needs in catalogue if needs}
    assert conditional == {
        "frontend: 8 tailnet serve routes": ["tailscale"],
        "frontend: owui/ manifest rows drifted from live webui.db: <count>": ["shell", "plugins"],
    }
    count = stack.DocsGenerator(stack.Manifest.load(REAL_MANIFEST), REPO_ROOT, DocsHost()).health_count()
    assert count.startswith(f"{len(PS1_PROBES)} probes with every plane enabled")
    assert f"({len(PS1_PROBES) - 2} when none of those holds)" in count


def test_the_shipped_docs_carry_every_registered_block_with_clean_markers():
    """No docker needed: every registered file exists, carries exactly the blocks
    the registry names, and no marker is broken."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    generator = stack.DocsGenerator(manifest, REPO_ROOT, DocsHost())
    for rel_path, expected in stack.DOCS_BLOCKS.items():
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        blocks, problems = stack.scan_doc_blocks(text)
        assert problems == [], (rel_path, problems)
        assert sorted({b.name for b in blocks}) == sorted(set(expected)), rel_path
        for block in blocks:
            kind, why = generator.kind(block.name)
            assert kind == ("inline" if block.inline else "multi"), (rel_path, block.name, why)


def test_the_shipped_manifest_only_blocks_match_without_docker():
    """product-menu, profiled-planes and the probe blocks need no render, so the
    suite holds them to the committed text even where OB1 and docker are absent."""
    manifest = stack.Manifest.load(REAL_MANIFEST)
    generator = stack.DocsGenerator(manifest, REPO_ROOT, DocsHost())
    seen = 0
    for rel_path in stack.DOCS_BLOCKS:
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        blocks, _problems = stack.scan_doc_blocks(text)
        for block in blocks:
            if block.name.split(":")[0] not in ("product-menu", "profiled-planes", "health-probes", "health-count"):
                continue
            body = generator.render(block.name)
            wanted = body if block.inline else "\n" + body + "\n\n"
            assert text[block.start:block.end] == wanted, (rel_path, block.name)
            seen += 1
    assert seen >= 4


# --- attempt 2: enforcement that does not leak (X1-X3) ----------------------


def test_a_hand_edit_of_a_derivable_row_is_stale_even_when_another_row_cannot_be_rendered(docs_root):
    """X1: plane-table was all-or-nothing, so without ob1 a hand edit of the MEMORY row passed."""
    assert docs(docs_root, "--write")[0] == 0
    (docs_root / "OB1" / "docker" / "docker-compose.yml").unlink()
    text = doc(docs_root)
    memory_row = next(line for line in text.splitlines() if line.startswith("| **memory**"))
    (docs_root / "DOC.md").write_text(text.replace(memory_row, memory_row.replace("1 with no profile",
                                                                                  "5 with no profile")),
                                      encoding="utf-8")
    code, out, _h = docs(docs_root, "--check", "--allow-unverified")
    assert code == stack.EXIT_REFUSED, out
    assert "block `plane-table` is STALE (row '**memory** (`memory`)'" in out
    assert "cell 3: committed '5 with no profile', generated '1 with no profile'" in out


def test_the_row_that_cannot_be_rendered_is_named_and_kept_not_passed(docs_root):
    assert docs(docs_root, "--write")[0] == 0
    (docs_root / "OB1" / "docker" / "docker-compose.yml").unlink()
    text = doc(docs_root)
    ob1_row = next(line for line in text.splitlines() if line.startswith("| **ob1**"))
    edited = text.replace(ob1_row, ob1_row.replace("1 with no profile", "99 with no profile", 1))
    (docs_root / "DOC.md").write_text(edited, encoding="utf-8")
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_UNVERIFIED, out
    assert "PARTLY VERIFIED - DOC.md:5 `plane-table`: every row compared except the ob1 row" in out
    assert "was NOT compared and may be stale" in out and "CI's stack-driver job" in out
    assert "none was stale" not in out
    assert docs(docs_root, "--write")[0] == stack.EXIT_UNVERIFIED
    assert doc(docs_root) == edited, "--write replaced a row it could not render"


def _pin_ob1(root: Path) -> None:
    (root / ".gitmodules").write_text('[submodule "OB1"]\n\tpath = OB1\n\turl = x\n', encoding="utf-8")


def test_a_submodule_checkout_that_is_not_the_staged_gitlink_is_not_verified(docs_root, monkeypatch):
    """X2: a dirty or mismatched OB1 tree was rendered and the gate recorded RAN."""
    assert docs(docs_root, "--write")[0] == 0
    _pin_ob1(docs_root)
    seen = []
    monkeypatch.setattr(stack, "submodule_mismatch",
                        lambda root, sub: seen.append(sub) or "the `OB1` checkout has uncommitted tracked edits")
    text = doc(docs_root) + "\n<!-- stack:profile-counts:ob1 -->\nkept\n<!-- /stack:profile-counts:ob1 -->\n"
    (docs_root / "DOC.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(stack, "DOCS_BLOCKS", {"DOC.md": DOC_BLOCKS + ["profile-counts:ob1"]})
    code, out, host = docs(docs_root, "--check")
    assert "PARTLY VERIFIED - DOC.md:5 `plane-table`: every row compared except the ob1 row" in out
    # a checked-out but mismatched submodule is exit 4 (the hook refuses a MERGE on it), not 3
    assert code == stack.EXIT_SUBMODULE_MISMATCH, out
    assert "a merge commit is refused on it" in out
    assert "`profile-counts:ob1`: the `OB1` checkout has uncommitted tracked edits" in out
    assert seen == ["OB1"], "asked once, cached"
    assert not any("OB1/docker/docker-compose.yml" in c for c in host.calls), "rendered the mismatched tree"


def _git_run(args, cwd):
    import subprocess
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "protocol.file.allow=always",
                    *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_submodule_mismatch_reads_the_staged_gitlink_head_and_tracked_dirt(tmp_path):
    sub_src = tmp_path / "src"
    sub_src.mkdir()
    _git_run(["init", "-q"], sub_src)
    (sub_src / "a.yml").write_text("a: 1\n", encoding="utf-8")
    _git_run(["add", "a.yml"], sub_src)
    _git_run(["commit", "-q", "-m", "one"], sub_src)
    parent = tmp_path / "parent"
    parent.mkdir()
    _git_run(["init", "-q"], parent)
    _git_run(["submodule", "-q", "add", str(sub_src), "OB1"], parent)
    assert stack.submodule_mismatch(parent, "OB1") is None
    (parent / "OB1" / "untracked.env").write_text("x", encoding="utf-8")
    assert stack.submodule_mismatch(parent, "OB1") is None, "untracked files are not a mismatch"
    (parent / "OB1" / "a.yml").write_text("a: 2\n", encoding="utf-8")
    assert "uncommitted tracked edits" in stack.submodule_mismatch(parent, "OB1")
    _git_run(["commit", "-q", "-am", "two"], parent / "OB1")
    assert "but the staged gitlink pins" in stack.submodule_mismatch(parent, "OB1")
    _git_run(["add", "OB1"], parent)            # stage the moved gitlink
    assert stack.submodule_mismatch(parent, "OB1") is None
    # INSIDE A HOOK git exports the PARENT's repository-local variables; the
    # submodule calls must not inherit them (attempt 2's regression).
    import os
    saved = {k: os.environ.get(k) for k in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")}
    try:
        os.environ["GIT_INDEX_FILE"] = str(parent / ".git" / "index")
        os.environ["GIT_DIR"] = str(parent / ".git")
        os.environ["GIT_WORK_TREE"] = str(parent)
        assert stack.submodule_mismatch(parent, "OB1") is None
        (parent / "OB1" / "a.yml").write_text("a: 3\n", encoding="utf-8")
        assert "uncommitted tracked edits" in stack.submodule_mismatch(parent, "OB1")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert "cannot read the staged `NOPE` gitlink" in stack.submodule_mismatch(parent, "NOPE")


def test_a_marker_in_an_unregistered_file_is_refused(docs_root):
    """X3: CLAUDE.md with a hand-written `**99**` between markers passed."""
    (docs_root / "CLAUDE.md").write_text(
        "rules\nOB1 is <!-- stack:count:ob1:all -->**99** services<!-- /stack:count:ob1:all -->\n",
        encoding="utf-8")
    code, out, _h = docs(docs_root, "--check")
    assert code == stack.EXIT_REFUSED
    assert "CLAUDE.md:2: a `stack:` marker in a file DOCS_BLOCKS does not register" in out


def test_the_host_path_guard_knows_every_spelling():
    spelled = stack.path_spellings(Path("D:\\Open WebUI\\ai-stack") if stack.WINDOWS
                                   else Path("/home/x/ai-stack"))
    if stack.WINDOWS:
        assert {"D:\\Open WebUI\\ai-stack", "D:/Open WebUI/ai-stack", "/d/Open WebUI/ai-stack",
                "/mnt/d/Open WebUI/ai-stack"} <= set(spelled)
    else:
        assert "/home/x/ai-stack" in spelled
    labels = {label for label, _w, _v in stack.host_values(stack.Manifest.load(REAL_MANIFEST), REPO_ROOT)}
    assert "this host's home directory" in labels


def test_a_stale_table_row_names_the_changed_cell_untruncated():
    long = "x" * 200
    diff = stack._first_difference(f"| **a** | {long} | 3 |\n", f"| **a** | {long} | 4 |\n")
    assert "cell 3: committed '3', generated '4'" in diff and "row '**a**'" in diff
    diff = stack._first_difference("prose " + long + " 1", "prose " + long + " 2")
    assert long + " 1" in diff and long + " 2" in diff


def test_a_plane_table_with_no_renderable_row_is_not_verified_not_partly(docs_root):
    assert docs(docs_root, "--write")[0] == 0

    def no_docker(cmd, cwd):
        return stack.CommandResult(127, "", "docker: not found")

    code, out, _h = docs(docs_root, "--check", host=no_docker)
    assert code == stack.EXIT_UNVERIFIED, out
    assert "NOT VERIFIED - DOC.md:5 `plane-table`: no row could be rendered" in out
    assert "PARTLY VERIFIED -" not in out


def test_the_host_path_guard_ignores_case(docs_root):
    host = DocsHost()
    host.render["search/docker-compose.yml"]["services"]["gateway"]["container_name"] = str(docs_root).upper()
    code, out, _h = docs(docs_root, "--write", host=host)
    assert code == stack.EXIT_REFUSED
    assert "this checkout's absolute path" in out


# --------------------------------------------------------------------------
# ac-driver-products: the driver's profiles reach the containers, and a name
# that is both a plane and a product means the product
# --------------------------------------------------------------------------

# Captured at import, before the autouse fixture swaps it for a hermetic stub:
# the tests below exercise the REAL execute seams with subprocess itself faked.
_REAL_SUBPROCESS_CAPTURE = stack.subprocess_capture
_REAL_SUBPROCESS_RUNNER = stack.subprocess_runner

_MANIFEST = stack.Manifest.load(REAL_MANIFEST)
SHARED_NAMES = sorted(set(_MANIFEST.planes) & set(_MANIFEST.products))


def _record_calls(monkeypatch):
    """Fake subprocess.call/run: record (argv, env kwarg), never execute."""
    seen = []

    def fake_call(cmd, cwd=None, env=None, **_kw):
        seen.append((list(cmd), env))
        return 0

    def fake_run(cmd, cwd=None, env=None, **_kw):
        seen.append((list(cmd), env))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(stack.subprocess, "call", fake_call)
    monkeypatch.setattr(stack.subprocess, "run", fake_run)
    return seen


def _anchor_without_networks(cmd, cwd):
    """`up <plane>` ensures the anchor's networks first (ac-followups N9). Answer its
    render with a project that declares none, so nothing is inspected or created;
    every other read goes to the hermetic stub as before."""
    if list(cmd[:4]) == ["docker", "compose", "-f", "docker-compose.yml"] and "config" in cmd:
        return stack.CommandResult(0, '{"name": "ai-stack", "networks": {}}', "")
    return stack.subprocess_capture(cmd, cwd)


def _run_with_the_real_runner(root, *args):
    out = io.StringIO()
    code = stack.main(["--root", str(root), *args], stdout=out,   # runner=None -> subprocess_runner
                      capture=_anchor_without_networks)
    return code, out.getvalue()


def test_up_hands_the_compose_process_the_profiles_it_passes(root, monkeypatch):
    """The bug: `--profile local` alone left llm-gateway's COMPOSE_PROFILES empty -> 0 models."""
    assert run(root, "enable", "--product", "inference")[0] == 0
    seen = _record_calls(monkeypatch)
    code, out = _run_with_the_real_runner(root, "up", "inference")
    assert code == 0, out
    ((cmd, env),) = seen
    assert cmd == ["docker", "compose", "-f", "inference/docker-compose.yml", "--profile", "local", "up", "-d"]
    assert env is not None, "the compose process inherited an environment with no COMPOSE_PROFILES"
    assert env["COMPOSE_PROFILES"] == "local"


def test_the_variable_is_the_union_the_flags_are(root, monkeypatch):
    """Env-file COMPOSE_PROFILES + state + defaults: the variable carries exactly the --profile set."""
    run(root, "init", "--planes", "ob1", "--force")
    state = json.loads((root / stack.STATE_REL).read_text(encoding="utf-8"))
    state["planes"]["ob1"]["profiles"] = ["research"]
    (root / stack.STATE_REL).write_text(json.dumps(state), encoding="utf-8")
    env_path = _MANIFEST.env_path(root, "ob1")
    env_path.write_text(env_path.read_text(encoding="utf-8") + "COMPOSE_PROFILES=wiki\n", encoding="utf-8")
    seen = _record_calls(monkeypatch)
    code, out = _run_with_the_real_runner(root, "up", "ob1")
    assert code == 0, out
    ((cmd, env),) = seen
    flags = [cmd[i + 1] for i, word in enumerate(cmd) if word == "--profile"]
    assert set(flags) == {"idea-refinery", "research", "wiki"}
    assert env["COMPOSE_PROFILES"] == ",".join(flags)


def test_a_shell_compose_profiles_does_not_leak_past_the_flags(root, monkeypatch):
    """Another plane's value exported in the shell must not reach the gateway."""
    monkeypatch.setenv("COMPOSE_PROFILES", "gpu,tailscale")
    run(root, "enable", "--product", "inference")
    seen = _record_calls(monkeypatch)
    code, out = _run_with_the_real_runner(root, "up", "inference")
    assert code == 0, out
    assert seen[0][1]["COMPOSE_PROFILES"] == "local"


def test_no_flag_means_no_override(root, monkeypatch):
    """With no --profile, compose keeps reading COMPOSE_PROFILES itself (shell, then the plane env)."""
    run(root, "enable", "--plane", "inference")
    seen = _record_calls(monkeypatch)
    code, out = _run_with_the_real_runner(root, "up", "inference")
    assert code == 0, out
    ((cmd, env),) = seen
    assert "--profile" not in cmd
    assert env is None


def test_the_other_verbs_carry_the_variable_too(root, monkeypatch):
    """down, restart and status run through the same runner; each must see the same set."""
    run(root, "enable", "--product", "inference")
    seen = _record_calls(monkeypatch)
    for verb in (["down", "inference"], ["restart", "inference"], ["status", "inference"]):
        _run_with_the_real_runner(root, *verb)
    assert [env and env["COMPOSE_PROFILES"] for _cmd, env in seen] == ["local", "local", "local"]


def test_the_capture_seam_carries_the_variable_too(root, monkeypatch):
    """recover and stats render/list through capture; their compose must see the same set."""
    seen = _record_calls(monkeypatch)
    cmd = stack.compose_command(_MANIFEST, "inference", ["config", "--format", "json"], profiles=["local"])
    _REAL_SUBPROCESS_CAPTURE(cmd, root)
    _REAL_SUBPROCESS_RUNNER(cmd, root)
    assert [env and env.get("COMPOSE_PROFILES") for _cmd, env in seen] == ["local", "local"]
    assert seen[0][0] == ["docker", "compose", "-f", "inference/docker-compose.yml", "--profile", "local",
                          "config", "--format", "json"]


def test_the_printed_command_is_unchanged(root):
    """The override is environment, not argv: --dry-run prints what it always printed."""
    run(root, "enable", "--product", "inference")
    code, out, _ = run(root, "up", "inference", "--dry-run")
    assert code == 0
    assert docker_lines(out)[-1:] == ["docker compose -f inference/docker-compose.yml --profile local up -d"]


def test_the_shared_names_are_the_five_the_docs_name():
    assert SHARED_NAMES == ["agent-org", "inference", "memory", "portal", "search"]


@pytest.mark.parametrize("name", SHARED_NAMES)
def test_a_bare_shared_name_enables_the_product(root, name):
    code, out, _ = run(root, "enable", name)
    assert code == 0, out
    assert f"enabled product {name}:" in out
    assert f"acting on the PRODUCT (use `--plane {name}` for the plane alone)" in out
    bare = state_of(root)
    (root / stack.STATE_REL).unlink()
    code, _, _ = run(root, "enable", "--product", name)
    assert code == 0
    assert state_of(root) == bare
    # it printed every plane it wrote (frontend is in the state by default, not by the product)
    listed = [line.split()[0] for line in out.splitlines() if line.startswith("  ")]
    wrote = [p for p in bare["planes"] if p != "frontend" or p in listed]
    assert sorted(listed) == sorted(wrote)


@pytest.mark.parametrize("name", SHARED_NAMES)
def test_dash_dash_plane_enables_the_plane_alone(root, name):
    deps = [p for p in stack.order_planes(_MANIFEST, stack.dependency_closure(_MANIFEST, [name]))
            if p != name and not _MANIFEST.is_implicit(p)]
    for dep in deps:
        assert run(root, "enable", "--plane", dep)[0] == 0
    code, out, _ = run(root, "enable", "--plane", name)
    assert code == 0, out
    assert f"enabled plane {name}:" in out
    assert "acting on the PLANE alone" in out
    assert state_of(root)["planes"][name]["profiles"] == _MANIFEST.default_profiles(name)


def test_enable_inference_turns_on_local_and_prints_it(root):
    code, out, _ = run(root, "enable", "inference")
    assert code == 0, out
    assert state_of(root)["planes"]["inference"]["profiles"] == ["local"]
    assert "enabled product inference:" in out
    assert "  inference  profiles: local" in out


def test_enable_memory_brings_inference_and_lists_both(root):
    code, out, _ = run(root, "enable", "memory")
    assert code == 0, out
    assert {"memory", "inference"} <= set(state_of(root)["planes"])
    assert "enabled product memory:" in out
    assert "\n  inference" in out and "\n  memory" in out


def test_disable_dash_dash_plane_is_the_plane_alone(root):
    run(root, "init", "--planes", "inference,memory")
    code, out, _ = run(root, "disable", "--plane", "inference")
    assert code == stack.EXIT_REFUSED
    assert "memory" in out
    code, out, _ = run(root, "disable", "--plane", "memory")
    assert code == 0
    assert "acting on the PLANE alone" in out
    assert set(state_of(root)["planes"]) == {"inference"}


# --------------------------------------------------------------------------
# ac-driver-products attempt 2: disable removes only what the product added,
# the memory product brings `local`, and a GPU-less daemon is refused up front
# --------------------------------------------------------------------------


def _raw_state(root):
    return (root / stack.STATE_REL).read_text(encoding="utf-8")


def test_disabling_a_product_that_was_never_enabled_changes_nothing(root):
    """attempt 1: `disable portal` with state [frontend] removed Open WebUI."""
    run(root, "init", "--planes", "frontend", "--force")
    before = _raw_state(root)
    code, out, _ = run(root, "disable", "portal")
    assert code == 0, out
    assert "product portal is not enabled on this machine; nothing to do" in out
    assert _raw_state(root) == before


def test_disabling_one_product_keeps_every_plane_another_product_needs(root):
    """portal and coding-agent share frontend; disabling either keeps it."""
    run(root, "init", "--planes", "inference", "--force")
    assert run(root, "enable", "coding-agent")[0] == 0
    assert run(root, "enable", "portal")[0] == 0
    code, out, _ = run(root, "disable", "portal")
    assert code == 0, out
    planes = state_of(root)["planes"]
    assert "portal" not in planes
    assert {"frontend", "coder", "inference"} <= set(planes)
    assert "frontend (product coding-agent)" in out
    assert "removed planes: portal" in out
    # and the other way round
    assert run(root, "enable", "portal")[0] == 0
    code, out, _ = run(root, "disable", "coding-agent")
    assert code == 0, out
    planes = state_of(root)["planes"]
    assert "coder" not in planes and "frontend" in planes and "portal" in planes
    assert "inference" in planes          # enabled directly by init


def test_a_directly_enabled_plane_survives_disabling_a_product_that_lists_it(root):
    run(root, "init", "--planes", "frontend,inference", "--force")
    assert run(root, "enable", "memory")[0] == 0
    assert state_of(root)["planes"]["inference"]["profiles"] == ["local"]
    code, out, _ = run(root, "disable", "memory")
    assert code == 0, out
    planes = state_of(root)["planes"]
    assert "memory" not in planes
    assert set(planes) == {"frontend", "inference"}
    # the product added `local`, so the product takes it back out; the plane stays
    assert planes["inference"]["profiles"] == []
    assert "dropped profiles: inference:local" in out
    assert "inference (enabled directly)" in out


def test_a_product_keeps_its_profile_while_another_product_asks_for_it(root):
    assert run(root, "enable", "memory")[0] == 0
    assert run(root, "enable", "inference")[0] == 0
    code, out, _ = run(root, "disable", "memory")
    assert code == 0, out
    planes = state_of(root)["planes"]
    assert "memory" not in planes
    assert planes["inference"]["profiles"] == ["local"]


def test_disabling_the_only_product_removes_what_it_added(root):
    code, _, _ = run(root, "enable", "memory")
    assert code == 0
    code, out, _ = run(root, "disable", "memory")
    assert code == 0, out
    # frontend was the no-state default, enabled directly; memory's closure goes
    assert set(state_of(root)["planes"]) == {"frontend"}
    assert state_of(root)["products"] == {}


def test_a_plane_still_required_is_kept_when_its_product_goes(root):
    run(root, "init", "--planes", "frontend", "--force")
    assert run(root, "enable", "inference")[0] == 0
    run(root, "enable", "--plane", "memory")
    code, out, _ = run(root, "disable", "inference")
    assert code == 0, out
    planes = state_of(root)["planes"]
    assert "inference" in planes and planes["inference"]["profiles"] == []
    assert "required by memory" in out


def test_disable_plane_is_refused_while_a_product_owns_it(root):
    run(root, "init", "--planes", "inference", "--force")
    run(root, "enable", "coding-agent")
    code, out, _ = run(root, "disable", "--plane", "frontend")
    assert code == stack.EXIT_REFUSED
    assert "coding-agent" in out
    assert "frontend" in state_of(root)["planes"]


def test_enabled_products_are_recorded_in_the_state_file(root):
    run(root, "enable", "memory")
    data = state_of(root)
    assert set(data["products"]) == {"memory"}
    assert "product:memory" in data["planes"]["memory"]["owners"]
    assert "product:memory" in data["planes"]["inference"]["owners"]
    _, out, _ = run(root, "list")
    assert "[enabled]" in [ln for ln in out.splitlines() if ln.strip().startswith("memory ")][-1]


HOST_STYLE_STATE = {
    "planes": {
        "anchor": {"context": None, "profiles": []},
        "coder": {"context": None, "profiles": []},
        "frontend": {"context": None, "profiles": []},
        "inference": {"context": None, "profiles": []},
        "memory": {"context": None, "profiles": []},
        "ob1": {"context": None, "profiles": ["idea-refinery", "research", "wiki", "notebook"]},
        "search": {"context": None, "profiles": []},
    },
    "version": 1,
}


def test_a_state_file_from_before_products_were_tracked_keeps_working(root):
    """The migration: no `products`, no `owners` - every plane counts as enabled directly."""
    path = root / stack.STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(HOST_STYLE_STATE, indent=2), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    code, out, _ = run(root, "up", "--dry-run", capture=_no_capture)
    assert code == 0, out
    assert "--profile idea-refinery --profile research --profile wiki --profile notebook up -d" in out
    assert run(root, "list")[0] == 0
    assert path.read_text(encoding="utf-8") == before          # reading never rewrites it
    code, out, _ = run(root, "disable", "research")
    assert code == 0 and "nothing to do" in out and "disable --plane" in out
    assert path.read_text(encoding="utf-8") == before
    code, out, _ = run(root, "disable", "--plane", "ob1")
    assert code == 0, out
    assert "ob1" not in state_of(root)["planes"]
    assert set(state_of(root)["planes"]) == set(HOST_STYLE_STATE["planes"]) - {"ob1"}


def test_disable_help_says_exactly_what_it_removes():
    parser = stack.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, stack.argparse._SubParsersAction))
    text = sub.choices["disable"].format_help()
    flat = " ".join(text.split())
    assert "removes only the planes and profiles that product added" in flat
    assert "no-op" in flat
    assert "requires closure and its profiles" not in flat


def test_the_memory_product_turns_on_local():
    """mnemory's two models exist only in the gateway's `local` group."""
    assert _MANIFEST.product("memory")["profiles"] == {"inference": ["local"]}
    compose = (REPO_ROOT / "memory" / "docker-compose.yml").read_text(encoding="utf-8")
    local = (REPO_ROOT / "inference" / "config" / "litellm" / "model_list" / "local.yaml").read_text(encoding="utf-8")
    for var in ("LLM_MODEL", "EMBED_MODEL"):
        model = re.search(rf"{var}=(\S+)", compose).group(1)
        assert f"model_name: {model}" in local, (var, model)


def test_enable_memory_writes_local(root):
    code, out, _ = run(root, "enable", "memory")
    assert code == 0, out
    assert state_of(root)["planes"]["inference"]["profiles"] == ["local"]
    assert "inference  profiles: local" in out


INFERENCE_GPU_RENDER = {
    "name": "inference",
    "services": {
        "llm-gateway": {"image": "litellm"},
        "llama-cpp-upstream": {
            "profiles": ["local"],
            "deploy": {"resources": {"reservations": {"devices": [
                {"driver": "nvidia", "device_ids": ["0"], "capabilities": [["gpu"]]}]}}},
        },
        "llama-cpp-embed-upstream": {
            "profiles": ["local"],
            "deploy": {"resources": {"reservations": {"devices": [
                {"driver": "nvidia", "device_ids": ["1"], "capabilities": [["gpu"]]}]}}},
        },
        "llm-queue": {"profiles": ["local"]},
    },
}


GPU_RENDERS = {
    "inference/docker-compose.yml": INFERENCE_GPU_RENDER,
    "frontend/docker-compose.yml": {"name": "frontend", "services": {"openwebui-stock": {"profiles": ["stock"]}}},
    "memory/docker-compose.yml": {"name": "memory", "services": {"mnemory": {"image": "mnemory"}}},
}


def test_a_gpu_less_daemon_refuses_local_before_anything_starts(root):
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert daemon.streamed == [] and daemon.created == []       # not even the anchor's networks
    assert "has no NVIDIA GPU" in out
    assert "inference: profile local starts llama-cpp-embed-upstream, llama-cpp-upstream" in out
    assert "stack.py enable --plane inference" in out
    assert "Nothing was started." in out
    assert "could not select device driver" not in out


def test_a_gpu_less_daemon_runs_the_gateway_alone_under_dash_dash_plane(root):
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "--plane", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert "docker compose -f inference/docker-compose.yml up -d" in [" ".join(c) for c in daemon.streamed]


def test_a_gpu_host_is_not_rendered_for_the_check(root):
    """One `docker info` and nothing else when the daemon has the runtime."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=True, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert not [c for c in daemon.commands if "config" in c and "inference/docker-compose.yml" in c]
    assert "docker compose -f inference/docker-compose.yml --profile local up -d" in [
        " ".join(c) for c in daemon.streamed]


def test_the_env_file_profile_is_checked_too(root):
    """`local` from inference/.env (no flag) reserves the GPU just the same."""
    run(root, "init", "--planes", "frontend,inference", "--force")
    env = _MANIFEST.env_path(root, "inference")
    env.write_text(env.read_text(encoding="utf-8") + "COMPOSE_PROFILES=local\n", encoding="utf-8")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "set `COMPOSE_PROFILES=` in inference/.env (was `local`)" in out
    assert daemon.streamed == []


def test_reserves_nvidia_reads_every_spelling():
    assert stack.reserves_nvidia({"runtime": "nvidia"})
    assert stack.reserves_nvidia({"gpus": "all"})
    assert stack.reserves_nvidia({"deploy": {"resources": {"reservations": {"devices": [
        {"capabilities": [["gpu"]]}]}}}})
    assert not stack.reserves_nvidia({"image": "x"})


def test_the_gpu_check_renders_what_up_will_start_interpolated(root):
    """Not --no-interpolate: inference's `${LM_MODELS_DIR:-...}:/models:ro` does not parse that way."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    run(root, "up", runner=daemon.runner, capture=daemon.capture)
    renders = [" ".join(c) for c in daemon.commands if "config" in c and "inference/docker-compose.yml" in c]
    assert renders == ["docker compose -f inference/docker-compose.yml --profile local config --format json"]


def test_a_plane_the_gpu_check_cannot_render_is_refused_not_skipped(root):
    """A check that skips what it cannot read passes while checking nothing (attempt-2 DinD finding)."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    failing = ("docker", "compose", "-f", "inference/docker-compose.yml", "--profile", "local",
               "config", "--format", "json")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS, exit_codes={failing: 1})
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "refused: compose cannot render inference/docker-compose.yml" in out
    assert "this is not about the GPU" in out
    assert "has no NVIDIA GPU" not in out and "To run without them" not in out
    assert daemon.streamed == []


# --------------------------------------------------------------------------
# ac-driver-products attempt 3: a refusal's remedy is FOLLOWED, not read
# --------------------------------------------------------------------------

_STEP = re.compile(r"^\s+\d+\. `python scripts/stack/stack\.py ([^`]+)`")


def _remedy_commands(out: str) -> list[list[str]]:
    """The numbered `python scripts/stack/stack.py ...` steps a GPU refusal printed, in order."""
    return [m.group(1).split() for m in map(_STEP.match, out.splitlines()) if m]


def _follow_gpu_remedy(root, out):
    """Run every printed step; the last is `up`. Returns (exit code, output) of that `up`."""
    steps = _remedy_commands(out)
    assert steps and steps[-1] == ["up"], out
    numbered = [ln for ln in out.splitlines() if re.match(r"^\s+\d+\. ", ln)]
    assert len(numbered) == len(steps), f"a step is not a command:\n{out}"
    for args in steps[:-1]:
        code, step_out, _ = run(root, *args)
        assert code == 0, f"remedy step `{' '.join(args)}` failed:\n{step_out}"
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, up_out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    return code, up_out, daemon


@pytest.mark.parametrize("setup", [
    ("frontend", ["enable", "memory"]),                 # T12b: the memory product owns inference + local
    ("frontend", ["enable", "inference"]),              # the inference product path
    ("frontend,inference", ["enable", "memory"]),       # inference ALSO enabled directly
])
def test_following_the_gpu_remedy_gets_a_gpu_less_up_through(root, setup):
    planes, enable = setup
    run(root, "init", "--planes", planes, "--force")
    assert run(root, *enable)[0] == 0
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED and "has no NVIDIA GPU" in out, out
    assert "disable --plane inference" not in out          # refused while a product owns the plane
    code, up_out, daemon = _follow_gpu_remedy(root, out)
    assert "has no NVIDIA GPU" not in up_out
    assert code == 0, up_out
    assert "docker compose -f inference/docker-compose.yml up -d" in [" ".join(c) for c in daemon.streamed]


def test_the_memory_remedy_names_the_memory_product(root):
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "memory")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    _, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert _remedy_commands(out) == [["disable", "memory"], ["enable", "--plane", "inference"], ["up"]]


def test_a_direct_local_on_a_pre_owners_file_is_not_offered_a_refused_command(root):
    """Pre-owners state with local on a directly-enabled inference that memory requires."""
    path = root / stack.STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "planes": {
        "frontend": {"context": None, "profiles": []},
        "inference": {"context": None, "profiles": ["local"]},
        "memory": {"context": None, "profiles": []}}}), encoding="utf-8")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "disable --plane inference" not in out
    assert "remove `local` from planes.inference.profiles" in out
    # follow it: the edit, then up
    data = json.loads(path.read_text(encoding="utf-8"))
    data["planes"]["inference"]["profiles"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    code, up_out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert "has no NVIDIA GPU" not in up_out and code == 0, up_out


def test_recover_runs_the_gpu_check_before_anything_starts(root):
    """F12: recover on a GPU-less daemon started containers and printed the raw nvidia error."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "recover", "inference", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "has no NVIDIA GPU" in out and "`recover` would start" in out
    assert daemon.streamed == []
    assert _remedy_commands(out)[-1] == ["recover", "inference"]     # N6: as invoked


def test_a_requirement_kept_plane_survives_a_save_and_load_as_unowned(root):
    """N1: `owners: {}` must not come back as "enabled directly"."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    run(root, "enable", "--plane", "memory")
    code, out, _ = run(root, "disable", "inference")
    assert "required by memory" in out
    assert state_of(root)["planes"]["inference"]["owners"] == {}
    loaded = stack.State.load(root / stack.STATE_REL)
    assert loaded.owners_of("inference") == {}                 # NOT {"plane": []}
    code, out, _ = run(root, "disable", "--plane", "memory")
    assert code == 0, out
    assert set(state_of(root)["planes"]) == {"frontend"}      # its last requirer went, so it went
    assert "disabled: memory, inference" in out


def test_the_pre_owners_hint_is_only_for_a_pre_owners_file(root):
    """N2: a tracked file whose last product was disabled is not a pre-owners file."""
    run(root, "enable", "memory")
    run(root, "disable", "memory")
    code, out, _ = run(root, "disable", "memory")
    assert code == 0 and "nothing to do" in out
    assert "before products were tracked" not in out and "disable --plane" not in out


def test_enable_plane_labels_a_profile_a_product_added(root):
    """N3: `--plane` did not turn `local` on; say who did."""
    run(root, "enable", "memory")
    code, out, _ = run(root, "enable", "--plane", "inference")
    assert code == 0
    assert "inference  profiles: local (already on: product memory)" in out
    code, out, _ = run(root, "enable", "inference")
    assert "inference  profiles: local\n" in out                 # the product's own profile, unlabelled


# --------------------------------------------------------------------------
# ac-driver-products attempt 4: a render failure is not a GPU refusal; the
# frontend step respects `requires` and brings in the stand-in
# --------------------------------------------------------------------------


def _blank(root, plane, key):
    env = _MANIFEST.env_path(root, plane)
    env.write_text(re.sub(rf"(?m)^{key}=.*$", f"{key}=", env.read_text(encoding="utf-8")), encoding="utf-8")


def test_up_refuses_a_blank_required_key_before_any_render(root):
    """T12e's trigger: `enable` refused a blank key, `up` did not."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    _blank(root, "inference", "LITELLM_DB_PASSWORD")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "inference: LITELLM_DB_PASSWORD in inference/.env is blank" in out
    assert "has no NVIDIA GPU" not in out
    assert daemon.streamed == [] and not [c for c in daemon.commands if c[1:2] == ["info"]]


@pytest.mark.parametrize("enable", [["enable", "inference"], ["enable", "--plane", "inference"]])
def test_a_render_failure_on_a_gpu_less_daemon_names_compose_s_error_not_the_gpu(root, enable):
    """T12e: it headlined "no NVIDIA GPU" and its one step (`up` again) looped."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, *enable)
    profiles = ["--profile", "local"] if "--plane" not in enable else []
    failing = ("docker", "compose", "-f", "inference/docker-compose.yml", *profiles, "config", "--format", "json")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS, exit_codes={failing: 1})
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "refused: compose cannot render inference/docker-compose.yml" in out
    assert "scripted failure" in out                        # compose's own words, passed through
    assert "inference/.env" in out
    assert "has no NVIDIA GPU" not in out and _remedy_commands(out) == []
    assert daemon.streamed == []


def test_the_shell_step_is_printed_when_only_the_shell_turns_the_profile_on(root, monkeypatch):
    """The mutation that survived attempt 3 (M10): nothing pinned the `unset` step."""
    run(root, "init", "--planes", "frontend,inference", "--force")
    monkeypatch.setenv("COMPOSE_PROFILES", "local")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "unset COMPOSE_PROFILES in this shell" in out
    monkeypatch.delenv("COMPOSE_PROFILES")                   # follow it
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0 and "has no NVIDIA GPU" not in out, out


FRONTEND_GPU_RENDER = {
    "name": "frontend",
    "services": {
        "openwebui-stock": {"profiles": ["stock"], "container_name": "openwebui"},
        "openwebui": {"profiles": ["gpu"], "container_name": "openwebui", "deploy": {"resources": {
            "reservations": {"devices": [{"driver": "nvidia", "capabilities": [["gpu"]]}]}}}},
        "tailscale": {"profiles": ["tailscale"]},
        "openwebui-backup": {},
    },
}


@pytest.mark.parametrize("env_value,expected", [
    ("gpu,tailscale", "stock"),     # X8a: tailscale requires gpu; left alone it does not render
    ("gpu", "stock"),               # X8b: without the stand-in no Open WebUI starts
    ("tailscale,gpu", "stock"),
])
def test_the_frontend_gpu_step_drops_what_requires_gpu_and_brings_in_stock(root, env_value, expected):
    run(root, "init", "--planes", "frontend", "--force")
    env = _MANIFEST.env_path(root, "frontend")
    env.write_text(env.read_text(encoding="utf-8") + f"COMPOSE_PROFILES={env_value}\n", encoding="utf-8")
    renders = dict(GPU_RENDERS, **{"frontend/docker-compose.yml": FRONTEND_GPU_RENDER})
    daemon = FakeDaemon(gpu=False, plane_renders=renders)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert f"set `COMPOSE_PROFILES={expected}` in frontend/.env" in out
    # follow it
    text = re.sub(r"(?m)^COMPOSE_PROFILES=.*$", f"COMPOSE_PROFILES={expected}", env.read_text(encoding="utf-8"))
    env.write_text(text, encoding="utf-8")
    assert stack.compose_profiles_env(_MANIFEST, root, "frontend") == ["stock"]
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0 and "has no NVIDIA GPU" not in out, out


def test_stands_in_for_names_a_declared_profile():
    assert _MANIFEST.stand_ins("frontend", {"gpu"}) == ["stock"]
    assert stack.profile_dependents(_MANIFEST, "frontend", {"gpu"}) == {"tailscale"}


def test_the_up_rerun_step_repeats_the_plane_argument(root):
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    _, out, _ = run(root, "up", "inference", runner=daemon.runner, capture=daemon.capture)
    assert _remedy_commands(out)[-1] == ["up", "inference"]
    assert "init --context" not in out                      # N5: a hint that wiped the state is gone


def test_headless_after_a_full_enable_does_not_claim_it_dropped_the_surface(root):
    """N4."""
    run(root, "init", "--planes", "inference", "--force")
    run(root, "enable", "coding-agent")
    code, out, _ = run(root, "enable", "coding-agent", "--headless")
    assert code == 0
    assert "dropped surface planes frontend" not in out
    assert "frontend stay - an earlier `enable coding-agent` added them" in out
    assert state_of(root)["products"]["coding-agent"]["headless"] is False
    assert "product:coding-agent" in state_of(root)["planes"]["frontend"]["owners"]


# --------------------------------------------------------------------------
# ac-followups: N9 - `up <plane>` ensures the anchor's networks as a bare `up`
# does; N10 - the shell `unset` step gets the same profiles as the env-file step
# --------------------------------------------------------------------------


def test_up_one_plane_on_an_empty_daemon_creates_the_anchor_networks_first(root):
    """N9. RED at e30fe42: `up inference` never ensured them, so compose said
    'network ai-stack_app-net declared as external, but could not be found'."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "--plane", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", "inference", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert sorted(daemon.created) == ["ai-stack_app-net", "ai-stack_default", "ai-stack_llm-net"]
    streamed = [" ".join(c) for c in daemon.streamed]
    ups = [i for i, c in enumerate(streamed) if c == "docker compose -f inference/docker-compose.yml up -d"]
    creates = [i for i, c in enumerate(streamed) if c.startswith("docker network create")]
    assert ups and creates and max(creates) < ups[0], streamed
    # the anchor is still never `up`-ed, and no OTHER plane is started
    assert not any(c.startswith("docker compose -f docker-compose.yml up") for c in streamed)
    assert not any("frontend/docker-compose.yml" in c for c in streamed)


def test_the_gpu_refusal_s_rerun_step_works_on_a_fresh_daemon(root):
    """N9 as the reader meets it: follow the printed steps of `up inference` literally."""
    run(root, "init", "--planes", "frontend", "--force")
    run(root, "enable", "inference")
    daemon = FakeDaemon(gpu=False, plane_renders=GPU_RENDERS)
    code, out, _ = run(root, "up", "inference", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED and daemon.created == [], out
    steps = _remedy_commands(out)
    assert steps[-1] == ["up", "inference"], out
    for args in steps[:-1]:
        assert run(root, *args)[0] == 0
    code, out, _ = run(root, *steps[-1], runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert "declared as external" not in out


def test_up_one_plane_leaves_existing_anchor_networks_alone(root):
    daemon = FakeDaemon(networks={spec["name"]: {"internal": spec.get("internal", False)}
                                  for spec in ANCHOR_NETWORKS.values()})
    code, out, _ = run(root, "up", "frontend", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert daemon.created == [] and daemon.mutations == []
    assert out.count("[exists]") == 3


def test_up_one_plane_refuses_on_a_drifted_llm_net_before_starting_it(root):
    daemon = FakeDaemon(networks={"ai-stack_llm-net": {"internal": False}})
    code, out, _ = run(root, "up", "frontend", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED
    assert "# up stopped: anchor" in out
    assert not any("frontend/docker-compose.yml" in " ".join(c) for c in daemon.streamed)


def test_up_the_anchor_by_name_still_only_ensures_its_networks(root):
    daemon = FakeDaemon()
    code, out, _ = run(root, "up", "anchor", runner=daemon.runner, capture=daemon.capture)
    assert code == 0, out
    assert sorted(daemon.created) == ["ai-stack_app-net", "ai-stack_default", "ai-stack_llm-net"]
    assert out.count("declares networks and no service") == 1


FRONTEND_NO_PROFILE_LINE = "# no COMPOSE_PROFILES line\n"


def _frontend_env_without_profiles(root):
    env = _MANIFEST.env_path(root, "frontend")
    env.write_text(re.sub(r"(?m)^COMPOSE_PROFILES=.*\n?", "", env.read_text(encoding="utf-8")), encoding="utf-8")
    return env


@pytest.mark.parametrize("shell", ["gpu,tailscale", "gpu"])
def test_the_shell_unset_step_brings_in_the_stand_in_the_env_file_step_does(root, monkeypatch, shell):
    """N10. RED at e30fe42: `unset` alone left a frontend/.env with no COMPOSE_PROFILES
    line to decide, which starts openwebui-backup and no Open WebUI (X11)."""
    run(root, "init", "--planes", "frontend", "--force")
    env = _frontend_env_without_profiles(root)
    assert stack.compose_profiles_env(_MANIFEST, root, "frontend") == []
    monkeypatch.setenv("COMPOSE_PROFILES", shell)
    renders = dict(GPU_RENDERS, **{"frontend/docker-compose.yml": FRONTEND_GPU_RENDER})
    daemon = FakeDaemon(gpu=False, plane_renders=renders)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    numbered = [ln.strip() for ln in out.splitlines() if re.match(r"^\s+\d+\. ", ln)]
    unset = [i for i, ln in enumerate(numbered) if "unset COMPOSE_PROFILES in this shell" in ln]
    setting = [i for i, ln in enumerate(numbered) if "set `COMPOSE_PROFILES=stock` in frontend/.env" in ln]
    assert unset and setting and unset[0] < setting[0], out
    # follow them literally
    monkeypatch.delenv("COMPOSE_PROFILES")
    env.write_text(env.read_text(encoding="utf-8") + "COMPOSE_PROFILES=stock\n", encoding="utf-8")
    assert stack.active_profiles(_MANIFEST, stack.State.load(root / stack.STATE_REL), root, "frontend") == {"stock"}
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == 0 and "has no NVIDIA GPU" not in out, out


def test_the_shell_step_adds_nothing_when_the_env_file_already_runs_the_stand_in(root, monkeypatch):
    """A fresh clone's frontend/.env says `stock`: `unset` alone is enough, no extra step."""
    run(root, "init", "--planes", "frontend", "--force")
    env = _frontend_env_without_profiles(root)
    env.write_text(env.read_text(encoding="utf-8") + "COMPOSE_PROFILES=stock\n", encoding="utf-8")
    monkeypatch.setenv("COMPOSE_PROFILES", "gpu,tailscale")
    renders = dict(GPU_RENDERS, **{"frontend/docker-compose.yml": FRONTEND_GPU_RENDER})
    daemon = FakeDaemon(gpu=False, plane_renders=renders)
    code, out, _ = run(root, "up", runner=daemon.runner, capture=daemon.capture)
    assert code == stack.EXIT_REFUSED, out
    assert "unset COMPOSE_PROFILES in this shell" in out
    assert "in frontend/.env" not in out
