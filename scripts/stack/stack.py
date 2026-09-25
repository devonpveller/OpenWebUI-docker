#!/usr/bin/env python3
"""stack.py - read stack.manifest.toml, drive the planes this machine enables.

One manifest (stack.manifest.toml, committed) declares every plane: its compose
file, what it REQUIRES to run, what SURFACES make it usable, its profiles, what
the host must provide and which keys must be non-blank. One gitignored state
file (.stack/state.json) says which planes and profiles THIS machine enables and
which docker context each runs on. This driver is the only thing that reads
both; it never writes the manifest.

Standard library only (Python >= 3.11, for tomllib), so a fresh host needs
nothing but Python and Docker. That rule is enforced by a test in
scripts/stack/test_stack.py and by the item's anchor.

Verbs:  status  up  down  restart <plane>  recover  enable  disable  list
        doctor  init  health  stats  backup <plane>  restore <plane>
        inventory --write|--check
        status/up/down/recover take an optional plane, or --all for every
        declared plane (the `manual` ones excepted); with neither they act on
        the set this machine ENABLES.
        `--dry-run` on up/down/restart/recover prints the exact docker command
        lines and runs nothing (recover still RENDERS each plane's compose file,
        read-only, because its plan is built from the render). `status`,
        `health`, `stats` and `inventory --check` are READ-ONLY: no lease,
        nothing started, stopped or recreated.

Everything - refusals included - is written to STDOUT, never stderr, and the
exit code carries the failure. PowerShell 5.1 turns a native command's stderr
into a terminating NativeCommandError under `$ErrorActionPreference = 'Stop'`
(the trap that once ate nine probes out of stack.ps1's health sweep), and this
driver is meant to be callable from a .ps1 without that dance.

Exit codes: 0 fine, 1 refused / a docker command failed, 2 usage error.
`health` is the exception and says so out loud: its exit code is the NUMBER
OF FAILED PROBES, exactly as scripts/stack/stack.ps1 health has always been.

Design and every verb's refusal cases: scripts/stack/README.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

MANIFEST_NAME = "stack.manifest.toml"
STATE_REL = Path(".stack") / "state.json"
STATE_VERSION = 1

# A fresh clone with no state file runs Open WebUI and nothing else.
DEFAULT_ENABLED = ("frontend",)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

# A module constant, not a call site: `stats` delegates to a .ps1 and a test has
# to be able to ask "what would this do on a Linux node?" without monkeypatching
# the os module out from under pathlib.
WINDOWS = os.name == "nt"


class Refusal(Exception):
    """A deliberate, explained "no". The message names the cause and the remedy."""


class Console:
    """Single output stream (see the module docstring for why there is no stderr)."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdout

    def line(self, text=""):
        print(text, file=self.stream)
        # Flush every line: docker's own output goes straight to the terminal, so a
        # buffered stream would print our headers AFTER the command they introduce.
        try:
            self.stream.flush()
        except (AttributeError, ValueError):
            pass


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------


class Manifest:
    def __init__(self, data: dict, path: Path):
        self.path = path
        self.planes: dict = data.get("planes", {})
        self.products: dict = data.get("products", {})
        # Declaration order IS the tie-break for `up`. See the manifest header.
        self.order: list[str] = list(self.planes)
        self._validate()

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.is_file():
            raise Refusal(f"refused: no manifest at {path} (expected {MANIFEST_NAME} at the repo root)")
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return cls(data, path)

    def _validate(self) -> None:
        for name, plane in self.planes.items():
            if "compose" not in plane:
                raise Refusal(f"refused: plane '{name}' in {self.path.name} has no compose file")
            for rel in ("requires", "optional"):
                for dep in plane.get(rel, []):
                    if dep not in self.planes:
                        raise Refusal(
                            f"refused: plane '{name}' lists unknown plane '{dep}' under {rel} in {self.path.name}"
                        )
            declared_profiles = plane.get("profiles", {})
            for profile, spec in declared_profiles.items():
                for dep in _spec(spec).get("requires", []):
                    if dep not in declared_profiles:
                        raise Refusal(
                            f"refused: profile '{name}.{profile}' requires unknown profile "
                            f"'{dep}' in {self.path.name}"
                        )
        for name, product in self.products.items():
            for plane in list(product.get("planes", [])) + list(product.get("surfaces", {})):
                if plane not in self.planes:
                    raise Refusal(
                        f"refused: product '{name}' names unknown plane '{plane}' in {self.path.name}"
                    )

    # -- plane accessors ---------------------------------------------------

    def plane(self, name: str) -> dict:
        try:
            return self.planes[name]
        except KeyError:
            raise Refusal(f"refused: unknown plane '{name}'. Planes: {', '.join(self.order)}") from None

    def requires(self, name: str) -> list[str]:
        return list(self.plane(name).get("requires", []))

    def optional(self, name: str) -> list[str]:
        return list(self.plane(name).get("optional", []))

    def is_implicit(self, name: str) -> bool:
        return bool(self.plane(name).get("implicit", False))

    def networks_only(self, name: str) -> bool:
        """The plane's compose file declares networks and NO service (the anchor).

        `docker compose up -d` on such a project exits 1 with "no service
        selected" (measured on compose v2.33 and v5.3), so `up` must not issue
        it: ensure_networks() creates what is missing instead.
        """
        return bool(self.plane(name).get("networks_only", False))

    def manual(self, name: str) -> str | None:
        return self.plane(name).get("manual")

    def profiles(self, name: str) -> dict:
        return self.plane(name).get("profiles", {})

    def profile_order(self, name: str, wanted) -> list[str]:
        """`wanted`, ordered as the manifest declares them (unknown ones last)."""
        declared = list(self.profiles(name))
        known = [p for p in declared if p in wanted]
        extra = [p for p in wanted if p not in declared]
        return known + sorted(extra)

    def profile_requires(self, name: str, profile: str) -> list[str]:
        return list(_spec(self.profiles(name).get(profile, {})).get("requires", []))

    def profile_closure(self, name: str, wanted) -> set[str]:
        """`wanted` plus every profile those transitively `requires`.

        A profile can name another as a hard prerequisite. The case this exists for:
        OB1's `idea-refinery` drain calls `openbrain-research` and nothing else, so
        enabling that profile without `research` starts a drain that can never drain -
        it comes up healthy and silently produces nothing, which is the failure this
        whole item is about. `idea-refinery` is also the ONLY `default = true` profile
        on that plane, so without this expansion a bare `enable ob1` reproduces it.

        Compose has no equivalent: `--profile idea-refinery` on the command line still
        needs `--profile research` beside it. This is the driver making the manifest's
        declaration real, not a compose feature.
        """
        out, todo = set(), list(wanted)
        while todo:
            profile = todo.pop()
            if profile in out:
                continue
            out.add(profile)
            todo.extend(self.profile_requires(name, profile))
        return out

    def default_profiles(self, name: str) -> list[str]:
        return [p for p, spec in self.profiles(name).items() if _spec(spec).get("default")]

    def pending_profiles(self, name: str) -> list[str]:
        return [p for p, spec in self.profiles(name).items() if _spec(spec).get("pending")]

    # NOTE for whoever extends a profile table (sl-ob1-profiles adds `requires`):
    # these accessors read the three deployment flags and IGNORE every other key.
    # An unknown key is never a refusal - a profile table is allowed to grow.
    def opt_in_profiles(self, name: str) -> list[str]:
        """Profiles nothing turns on automatically - the operator or an env does."""
        return [p for p, spec in self.profiles(name).items() if _spec(spec).get("opt_in")]

    def unaccounted_profiles(self, name: str) -> list[str]:
        """Declared profiles that say nothing about whether they are deployed.

        Every profile must carry exactly one of `default` (passed on every
        invocation), `opt_in` (something outside the driver turns it on:
        COMPOSE_PROFILES in the plane env, portal-on.ps1, the operator) or
        `pending` (not in the compose file yet). A profile with none of the
        three is a silent reduction waiting to happen: the driver will not pass
        it, and nobody declared that it should not.
        """
        out = []
        for profile, spec in self.profiles(name).items():
            flags = _spec(spec)
            if not (flags.get("default") or flags.get("opt_in") or flags.get("pending")):
                out.append(profile)
        return out

    def keys(self, name: str) -> list[str]:
        return list(self.plane(name).get("keys", []))

    def env_file(self, name: str) -> str | None:
        """The --env-file the compose CLI is given, or None when compose loads its own.

        None for every plane since sl-env-split (2026-09-19): each project directory
        holds its own `.env` and compose loads it natively, so the driver passes no
        flag at all. The key is still honoured so a plane whose env genuinely lives
        somewhere else could declare one.
        """
        value = self.plane(name).get("env_file")
        return value or None

    def env_path(self, root: Path, name: str) -> Path:
        """The env file the plane actually READS - which is what a key check must read.

        With an explicit env_file that is the file; without one, compose loads the
        .env sitting in the compose file's own directory - which is every plane
        since sl-env-split (frontend/.env, inference/.env, ... OB1/docker/.env).
        """
        explicit = self.env_file(name)
        if explicit:
            return root / explicit
        return root / Path(self.plane(name)["compose"]).parent / ".env"

    # -- product accessors -------------------------------------------------

    def product(self, name: str) -> dict:
        try:
            return self.products[name]
        except KeyError:
            raise Refusal(
                f"refused: unknown product '{name}'. Products: {', '.join(self.products)}"
            ) from None


def _spec(spec) -> dict:
    """A profile may be declared as a bare description string or as a table."""
    if isinstance(spec, str):
        return {"description": spec}
    return dict(spec)


def profile_description(manifest: Manifest, plane: str, profile: str) -> str:
    return _spec(manifest.profiles(plane).get(profile, {})).get("description", "")


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------


def dependency_closure(manifest: Manifest, names) -> set[str]:
    """`names` plus everything they transitively require."""
    seen: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        manifest.plane(name)
        seen.add(name)
        pending.extend(manifest.requires(name))
    return seen


def order_planes(manifest: Manifest, names) -> list[str]:
    """Topological sort of `requires`, ties broken by manifest declaration order.

    That tie-break is the whole point: it is what makes the full set come out
    anchor, inference, frontend, memory, search, coder, ob1, agent-org - the
    order scripts/stack/stack.ps1 uses.
    """
    wanted = set(names)
    remaining = [p for p in manifest.order if p in wanted]
    ordered: list[str] = []
    placed: set[str] = set()
    while remaining:
        for candidate in remaining:
            deps = [d for d in manifest.requires(candidate) if d in wanted]
            if all(d in placed for d in deps):
                ordered.append(candidate)
                placed.add(candidate)
                remaining.remove(candidate)
                break
        else:
            raise Refusal(
                "refused: the manifest's requires edges contain a cycle involving "
                + ", ".join(sorted(remaining))
            )
    return ordered


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


class State:
    def __init__(self, planes: dict | None = None, path: Path | None = None, exists: bool = False):
        self.planes: dict = planes if planes is not None else {}
        self.path = path
        self.exists = exists

    @classmethod
    def default(cls, path: Path | None = None) -> "State":
        return cls({name: {"profiles": [], "context": None} for name in DEFAULT_ENABLED}, path, False)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.is_file():
            return cls.default(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refusal(f"refused: state file {path} is unreadable ({exc}); delete it or re-run `init --force`")
        planes = {}
        for name, entry in (data.get("planes") or {}).items():
            entry = entry or {}
            planes[name] = {
                "profiles": list(entry.get("profiles", [])),
                "context": entry.get("context"),
            }
        return cls(planes, path, True)

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": STATE_VERSION, "planes": self.planes}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def is_enabled(self, name: str) -> bool:
        return name in self.planes

    def enable(self, name: str, profiles=(), context=None) -> None:
        entry = self.planes.setdefault(name, {"profiles": [], "context": None})
        for profile in profiles:
            if profile not in entry["profiles"]:
                entry["profiles"].append(profile)
        if context is not None:
            entry["context"] = context

    def profiles_of(self, name: str) -> list[str]:
        return list(self.planes.get(name, {}).get("profiles", []))

    def context_of(self, name: str) -> str | None:
        return self.planes.get(name, {}).get("context")


# --------------------------------------------------------------------------
# env files
# --------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def example_path(env_path: Path) -> Path:
    """`frontend/.env` -> `frontend/.env.example`: the file a newcomer copies."""
    return env_path.with_name(env_path.name + ".example")


def placeholder_reason(root: Path, env_path: Path) -> str:
    return f"still the placeholder shipped in {rel(root, example_path(env_path))}"


def blank_keys(manifest: Manifest, root: Path, plane: str) -> list[tuple[str, str, Path]]:
    """[(key, why, env path)] for every key that would fail the plane.

    `why` is 'missing', 'blank', or 'still the placeholder shipped in <x>.example'.

    THE PLACEHOLDER RULE. A manifest `keys` entry is a value this machine must
    supply - a secret, a domain - so it has no value that is right everywhere.
    Whatever its `.env.example` ships NON-BLANK for it is therefore a
    placeholder by construction (`change-me-...`, `REPLACE_WITH_...`,
    `ai.example.com`), and an env file still holding exactly that value was
    copied and never filled in. Frontend's WEBUI_SECRET_KEY is the case that
    matters: the shipped value passes the compose file's `:?` guard, so Open
    WebUI would start encrypting webui.db with a key printed in a public repo.
    A key the example ships BLANK is caught by the 'blank' rule instead.
    """
    wanted = manifest.keys(plane)
    if not wanted:
        return []
    env_path = manifest.env_path(root, plane)
    values = read_env_file(env_path)
    shipped = read_env_file(example_path(env_path))
    problems = []
    for key in wanted:
        if key not in values:
            problems.append((key, "missing", env_path))
        elif values[key].strip() == "":
            problems.append((key, "blank", env_path))
        elif shipped.get(key, "").strip() and values[key].strip() == shipped[key].strip():
            problems.append((key, placeholder_reason(root, env_path), env_path))
    return problems


def placeholder_keys(manifest: Manifest, root: Path, plane: str) -> list[tuple[str, str, Path]]:
    """The subset of blank_keys() that is a shipped placeholder (what `up` refuses)."""
    return [p for p in blank_keys(manifest, root, plane) if p[1] not in ("missing", "blank")]


def missing_submodule(manifest: Manifest, root: Path, plane: str) -> str | None:
    """The submodule path when the plane's compose file is absent because of it."""
    compose_rel = manifest.plane(plane)["compose"]
    if (root / Path(compose_rel)).is_file():
        return None
    first = compose_rel.replace("\\", "/").split("/")[0]
    return first if first in submodule_paths(root) else None


def submodule_remedy(submodule: str) -> str:
    return f"`git submodule update --init {submodule}`"


def host_path_problem(root: Path, spec: dict) -> str | None:
    """Why one `host_paths` entry is not usable, or None when it is.

    Existing is not enough: an empty directory or a plain file at the path
    would pass an .exists() test and still fail the build. The entry's
    `contains` names what a real checkout holds there (memory: `.git` and the
    Dockerfile its compose file builds with); each must be present.
    """
    path = root / Path(spec["path"])
    if not path.exists():
        return "is missing"
    if not path.is_dir():
        return "is not a directory"
    absent = [name for name in spec.get("contains", []) if not (path / name).exists()]
    if absent:
        return "is not a checkout the plane can build from (no " + ", ".join(absent) + ")"
    return None


def missing_host_paths(manifest: Manifest, root: Path, plane: str) -> list[tuple[dict, str]]:
    """(entry, reason) for each of the plane's `host_paths` that is not usable.

    A path OUTSIDE the checkout that the plane builds from - memory's sibling
    ../mnemory. Each entry carries the `remedy` command that creates it.
    """
    found = []
    for spec in manifest.plane(plane).get("host_paths", []):
        reason = host_path_problem(root, spec)
        if reason:
            found.append((spec, reason))
    return found


def host_path_line(spec: dict, plane: str, reason: str = "is missing") -> str:
    return (f"{spec['path']} {reason} (plane {plane}): {spec.get('why', 'the plane builds from it')} "
            f"- run `{spec['remedy']}` from the repo root")


# --------------------------------------------------------------------------
# shipped placeholders beyond the manifest's `keys`
# --------------------------------------------------------------------------
#
# THE RULE, data-driven from the .env.example files themselves (ac-front-door,
# attempt 2 - the first attempt read only the manifest's `keys`, and a copied
# search/.env went up with GATEWAY_API_KEY and SEARXNG_SECRET_KEY still at the
# public placeholder). A value in a plane's `.env.example` is a PLACEHOLDER when
# it matches PLACEHOLDER_PATTERN:
#     change-me / change_me / changeme anywhere   (change-me-to-a-long-random-string,
#                                                  sk-change-me-owui-virtual-key)
#     starts with replace-with / REPLACE_WITH      (REPLACE_WITH_64_HEX_CHARS)
#     starts with your- / your_ / putyour          (your-mnemory-api-key-here,
#                                                  putyourtskeyhere)
#     the whole value is <...>                     (<your token>)
#     an example.com/.org/.net domain or address   (ai.example.com, you@example.com)
#     contains the word placeholder
# A new placeholder written in any of those shapes is covered without touching
# the manifest. A real default (a port, a URL, `llama`) matches none of them -
# checked against every .env.example in the tree when this was written.
#
# WHICH ONES COUNT: only a key a service THIS DEPLOYMENT RUNS actually reads.
# That is decided from compose's own render (`config --no-interpolate`, which
# keeps `${VAR}` references visible and lists every service with its
# `profiles`), filtered to the profiles this plane runs with. So frontend's
# TAILSCALE_AUTH_KEY counts under `tailscale` and not under `stock`, and
# agent-org's AO_CLOUD_* only under `cloud`. A bulk `env_file:` is NOT a read:
# agent-bridge loads its whole .env that way, and counting it would make every
# profile-gated secret "in use" on every host.
#
# The manifest `keys` rule (blank_keys) is separate and stays profile-blind:
# those keys are required everywhere.

PLACEHOLDER_PATTERN = re.compile(
    r"change[-_ ]?me|^replace[-_ ]?with|^your[-_]|^putyour|^<[^<>]*>$"
    r"|(^|[@.])example\.(com|org|net)$|placeholder",
    re.IGNORECASE,
)
_VAR_REF = re.compile(r"(?<!\$)\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def is_placeholder(value: str) -> bool:
    return bool(value.strip()) and bool(PLACEHOLDER_PATTERN.search(value.strip()))


def active_profiles(manifest, state, root, plane, extra=()) -> set[str]:
    """The profiles compose will run this plane with, as the driver would invoke it."""
    flags = set(effective_profiles(manifest, state, root, plane)) | set(extra)
    if flags:
        return flags | set(compose_profiles_env(manifest, root, plane))
    shell = os.environ.get("COMPOSE_PROFILES")
    if shell is not None:
        return {p.strip() for p in shell.split(",") if p.strip()}
    return set(compose_profiles_env(manifest, root, plane))


def _strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif node is not None:
        yield str(node)


def referenced_vars(manifest, root, plane, capture, profiles, context=None):
    """({VAR, ...} the active services interpolate, None) or (None, why the render failed)."""
    cmd = compose_command(manifest, plane, ["config", "--no-interpolate", "--format", "json"], context=context)
    result = capture(cmd, root)
    if result.code != 0:
        return None, (result.stderr or result.stdout or f"exit {result.code}").strip().splitlines()[0:1]
    try:
        services = (json.loads(result.stdout) or {}).get("services") or {}
    except ValueError:
        return None, ["the render is not JSON"]
    refs = set()
    for service in services.values():
        gated = (service or {}).get("profiles") or []
        if gated and not set(gated) & set(profiles):
            continue
        for text in _strings({k: v for k, v in (service or {}).items() if k != "env_file"}):
            refs.update(_VAR_REF.findall(text))
    return refs, None


def shipped_placeholders(manifest, state, root, plane, capture, extra_profiles=()):
    """[(key, why, env path)] for keys OUTSIDE the manifest's `keys` still at a shipped placeholder.

    Only keys a running service reads (see the block comment above). When the
    render fails the answer cannot be narrowed, so every candidate counts and
    `why` says the render failed - failing closed, since the fix (replace the
    value) is the same either way.
    """
    env_path = manifest.env_path(root, plane)
    values = read_env_file(env_path)
    shipped = read_env_file(example_path(env_path))
    required = set(manifest.keys(plane))
    candidates = [
        key for key, value in shipped.items()
        if key not in required and is_placeholder(value)
        and key in values and values[key].strip() == value.strip()
    ]
    if not candidates:
        return []
    why = placeholder_reason(root, env_path)
    profiles = active_profiles(manifest, state, root, plane, extra_profiles)
    refs, failed = referenced_vars(manifest, root, plane, capture, profiles, state.context_of(plane))
    if refs is None:
        note = f" (could not render {manifest.plane(plane)['compose']} to tell whether it is read: " \
               f"{failed[0] if failed else 'no output'})"
        return [(key, why + note, env_path) for key in candidates]
    return [(key, why, env_path) for key in candidates if key in refs]


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# docker command construction
# --------------------------------------------------------------------------


def compose_command(manifest: Manifest, plane: str, args, context=None, profiles=()) -> list[str]:
    cmd = ["docker"]
    if context:
        cmd += ["--context", context]
    cmd += ["compose", "-f", manifest.plane(plane)["compose"]]
    env_file = manifest.env_file(plane)
    if env_file:
        cmd += ["--env-file", env_file]
    for profile in profiles:
        cmd += ["--profile", profile]
    cmd += list(args)
    return cmd


def enable_plane_profiles(manifest: Manifest, state: State, plane: str, profiles, context=None) -> list[str]:
    """THE ONLY WAY a plane's profiles are written into the state file.

    Resolves `profiles` the same way `run_profiles` does at drive time - closed over
    `requires`, ordered as the manifest declares - so the JSON on disk says what `up`
    will actually pass. Returns the resolved list for display.

    There is one function because there were three writers and only one of them was
    right. `enable <product>` closed over `requires`; `enable <plane>` and `init
    --planes` did not, so `stack.py enable ob1` PRINTED "profiles: idea-refinery,
    research" and WROTE ["idea-refinery"]. Drive time was correct either way, which is
    exactly why it survived a test suite: nothing deployed wrong, the artifact just
    lied. Route every new writer through here rather than calling `state.enable`
    directly with a profile list.
    """
    resolved = manifest.profile_order(plane, manifest.profile_closure(plane, set(profiles)))
    state.enable(plane, resolved, context=context)
    return resolved


def run_profiles(manifest: Manifest, state: State, plane: str) -> list[str]:
    """State profiles, unioned with the plane's `default = true` ones, closed over `requires`.

    `default` means "passed on every invocation". It exists for profiles a plane is
    not really usable without, and it deliberately survives `--headless` - which is
    also why a SURFACE must never be marked default.

    NOT parity with stack.ps1 for the ob1 plane, and the docstring used to say it
    was: since sl-ob1-profiles, stack.ps1's ob1 row passes all four profiles (it is
    the pre-manifest driver and has to keep starting the 30 containers on this host),
    while only `idea-refinery` is `default` here (marking the other three default
    would make `--headless` a no-op for the plane). The divergence is deliberate and
    argued at stack.ps1's ob1 row and above stack.manifest.toml's profile tables;
    sl-driver-parity reconciles the two drivers.
    """
    wanted = set(state.profiles_of(plane)) | set(manifest.default_profiles(plane))
    return manifest.profile_order(plane, manifest.profile_closure(plane, wanted))


def compose_profiles_env(manifest: Manifest, root: Path, plane: str) -> list[str]:
    """COMPOSE_PROFILES as the plane's own env file sets it."""
    value = read_env_file(manifest.env_path(root, plane)).get("COMPOSE_PROFILES", "")
    return [p.strip() for p in value.split(",") if p.strip()]


def effective_profiles(manifest: Manifest, state: State, root: Path, plane: str) -> list[str]:
    """The --profile flags to pass, which must never START FEWER CONTAINERS.

    `docker compose --profile X` REPLACES COMPOSE_PROFILES; it does not union
    with it. Measured 2026-09-19 on compose v5.3.0, inference plane, with its own
    env file carrying COMPOSE_PROFILES=local:

        docker compose -f inference/... config --services
            -> 8 services (the `local` half is on)
        ... --profile idea-refinery config --services
            -> 4 services. `local` was silently dropped.

    So the moment ANY flag is passed to a plane, every profile that plane's env
    already enabled has to be passed too, or the driver quietly starts a subset
    of what a bare invocation would have started - four llama.cpp containers, in
    that example, with no error anywhere. Passing no flag at all is safe:
    compose then reads COMPOSE_PROFILES itself, which is today's behaviour for
    every plane except ob1. Since sl-env-split that value is PER PLANE (D17):
    compose_profiles_env reads the plane's OWN file, so the frontend's
    `gpu,tailscale` can no longer leak into the inference render.
    """
    wanted = set(run_profiles(manifest, state, plane))
    if not wanted:
        return []
    wanted |= set(compose_profiles_env(manifest, root, plane))
    return manifest.profile_order(plane, wanted)


def subprocess_runner(cmd, cwd) -> int:
    return subprocess.call(cmd, cwd=str(cwd))


# --------------------------------------------------------------------------
# the anchor: ensure its networks, never `up` it
# --------------------------------------------------------------------------
#
# WHY NOT `docker compose up -d` (the step this replaces). The root compose file
# declares networks and ZERO services, and compose refuses to act on a project
# with no service: `up -d`, `up --no-start` and `up -d --no-start` all print
# "no service selected" and exit 1 (measured on compose v2.33.0 in a DinD, and
# the same message is what a real `stack.py up` printed at 3c3ff75). `_drive`
# stops at the first non-zero exit, so EVERY real `up` ended at the anchor
# before it reached a single plane. Every earlier test used --dry-run, which
# never calls the runner. `docker compose create` exits 0 but creates nothing.
#
# WHY `docker network create`, and how it stays compose-compatible:
#   * the SPEC comes from compose's own render, never from parsing YAML here:
#     `config --no-interpolate --format json`. The flag is load-bearing. A
#     plain `config` PRUNES networks no service uses, which for this project is
#     all of them - it renders `{"name": ..., "services": {}}` and nothing else
#     (measured, v2.33.0 and v5.3.0). `--no-interpolate` keeps them. The anchor
#     interpolates no variable today; one that ever does is refused, not guessed.
#   * the LABELS are the two compose reads back when it later resolves a network
#     it owns: com.docker.compose.project and com.docker.compose.network. The
#     config-hash label is deliberately NOT written - compose treats a network
#     without one as not diverged (an older compose's network), whereas a hash
#     that disagreed with its own would make it offer to recreate the network.
#   * a network that ALREADY EXISTS is inspected and left exactly as it is -
#     never recreated, never altered, never disconnected - even when its flags
#     differ from the declaration (that is printed, not repaired). On a running
#     host every plane is attached to these three networks.
#   * a key in the render this function does not translate (ipam, enable_ipv6,
#     ...) is a REFUSAL naming it, never a silently weaker network.

_NETWORK_KEYS_TRANSLATED = {"name", "driver", "internal", "attachable", "driver_opts", "labels", "external"}


def _network_spec_refusal(compose_rel: str, key: str, why: str) -> Refusal:
    return Refusal(
        f"refused: network '{key}' in {compose_rel} {why}; stack.py creates the anchor's networks "
        f"itself (compose cannot `up` a project with no service) and will not create a weaker one. "
        f"Create it by hand with `docker network create`, or teach ensure_networks() the key."
    )


def anchor_render_command(manifest: Manifest, plane: str, context=None) -> list[str]:
    return compose_command(manifest, plane, ["config", "--no-interpolate", "--format", "json"],
                           context=context)


def anchor_networks(manifest: Manifest, root: Path, plane: str, capture, context=None):
    """(project name, [(key, spec)]) for every NON-external network the plane declares."""
    compose_rel = manifest.plane(plane)["compose"]
    cmd = anchor_render_command(manifest, plane, context)
    result = capture(cmd, root)
    if result.code != 0:
        raise Refusal(
            f"refused: `{' '.join(cmd)}` exited {result.code}\n"
            + (result.stderr.strip() or result.stdout.strip())
        )
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise Refusal(f"refused: the render of {compose_rel} is not JSON ({exc})") from None
    project = data.get("name") or ""
    out = []
    for key, spec in (data.get("networks") or {}).items():
        spec = spec or {}
        if spec.get("external"):
            continue
        unknown = sorted(set(spec) - _NETWORK_KEYS_TRANSLATED)
        if unknown:
            raise _network_spec_refusal(compose_rel, key, "declares " + ", ".join(unknown))
        if "${" in json.dumps(spec) or "${" in project:
            raise _network_spec_refusal(compose_rel, key, "interpolates a variable")
        out.append((key, spec))
    return project, out


def network_drift(spec: dict, inspected: dict) -> list[str]:
    """Every way an EXISTING network differs from its declaration, as readable phrases.

    Compared: driver (compose's default is bridge), internal, attachable, each
    declared driver_opt, each declared label. Labels and options the network
    carries beyond the declaration (compose's own, docker's enable_ipv4/6) are
    not drift. An empty list means it matches.
    """
    problems = []
    want_driver = spec.get("driver") or "bridge"
    if (inspected.get("Driver") or "") != want_driver:
        problems.append(f"driver is {inspected.get('Driver')!r}, declared {want_driver!r}")
    for flag, field in (("internal", "Internal"), ("attachable", "Attachable")):
        want = bool(spec.get(flag, False))
        have = bool(inspected.get(field, False))
        if want != have:
            problems.append(f"{flag} is {str(have).lower()}, declared {str(want).lower()}")
    options = inspected.get("Options") or {}
    for opt, value in sorted((spec.get("driver_opts") or {}).items()):
        if str(options.get(opt)) != str(value):
            problems.append(f"driver_opt {opt} is {options.get(opt)!r}, declared {str(value)!r}")
    labels = inspected.get("Labels") or {}
    for label, value in sorted((spec.get("labels") or {}).items()):
        if str(labels.get(label)) != str(value):
            problems.append(f"label {label} is {labels.get(label)!r}, declared {str(value)!r}")
    return problems


def inspect_network(capture, root, docker, name):
    """The parsed `docker network inspect` of one network, or None when it does not exist."""
    found = capture(docker + ["network", "inspect", name, "--format", "{{json .}}"], root)
    if found.code != 0:
        return None
    try:
        data = json.loads(found.stdout.strip() or "{}")
    except ValueError:
        raise Refusal(f"refused: `docker network inspect {name}` did not return JSON") from None
    return data if isinstance(data, dict) else {}


def anchor_network_report(manifest, root, capture, plane, context=None):
    """[(name, key, spec, inspected-or-None, drift)] for every network the plane declares."""
    project, networks = anchor_networks(manifest, root, plane, capture, context)
    docker = ["docker"] + (["--context", context] if context else [])
    rows = []
    for key, spec in networks:
        name = spec.get("name") or f"{project}_{key}"
        inspected = inspect_network(capture, root, docker, name)
        rows.append((project, name, key, spec,
                     inspected, network_drift(spec, inspected) if inspected is not None else []))
    return rows


def ensure_networks(manifest, state, root, console, runner, capture, plane, dry_run) -> int:
    """Create the plane's declared networks that do not exist; touch none that do.

    Idempotent: a second run finds every network and runs no create at all.

    An existing network whose flags DIFFER from the declaration is a REFUSAL,
    decided before anything is created: ai-stack_llm-net is the isolation
    boundary (internal: true is what keeps every inference caller off the
    internet), so a bring-up on top of a weaker one would be a silent downgrade.
    stack.py still never alters it - recreating a network means detaching every
    plane on it, which is an operator's decision, not a side effect of `up`.

    Returns 0, EXIT_REFUSED on drift, or the exit code of the first
    `docker network create` that failed.
    """
    context = state.context_of(plane)
    compose_rel = manifest.plane(plane)["compose"]
    console.line(f"# {plane}: {compose_rel} declares networks and no service, so it is not `up`-ed;"
                 " each missing network is created and an existing one is left exactly as it is")
    console.line(" ".join(anchor_render_command(manifest, plane, context)))
    if dry_run:
        console.line("# (dry run: nothing rendered, inspected or created)")
        return EXIT_OK
    rows = anchor_network_report(manifest, root, capture, plane, context)
    drifted = [(name, drift) for _p, name, _k, _s, inspected, drift in rows if inspected is not None and drift]
    for _p, name, _k, _s, inspected, drift in rows:
        if inspected is not None and not drift:
            console.line(f"  [exists] {name} (matches {compose_rel}; left as is)")
    if drifted:
        for name, drift in drifted:
            console.line(f"  [DIFFERS] {name}: " + "; ".join(drift))
        console.line(
            f"# refused: an existing network differs from {compose_rel}, and stack.py never alters one. "
            "Nothing was created or started. To rebuild it: stop every plane attached to it "
            "(`docker network inspect <name>` lists them), `docker network rm <name>`, then re-run `up`."
        )
        return EXIT_REFUSED
    docker = ["docker"] + (["--context", context] if context else [])
    for project, name, key, spec, inspected, _drift in rows:
        if inspected is not None:
            continue
        cmd = docker + ["network", "create", "--driver", spec.get("driver") or "bridge"]
        if spec.get("internal"):
            cmd.append("--internal")
        if spec.get("attachable"):
            cmd.append("--attachable")
        for opt, value in sorted((spec.get("driver_opts") or {}).items()):
            cmd += ["--opt", f"{opt}={value}"]
        labels = dict(spec.get("labels") or {})
        labels["com.docker.compose.project"] = project
        labels["com.docker.compose.network"] = key
        for label, value in sorted(labels.items()):
            cmd += ["--label", f"{label}={value}"]
        cmd.append(name)
        console.line(" ".join(cmd))
        code = runner(cmd, root)
        if code != 0:
            console.line(f"# creating {name} exited {code}; the networks created before it are kept, "
                         "and a re-run creates only what is still missing")
            return code
    return EXIT_OK


# --------------------------------------------------------------------------
# name resolution
# --------------------------------------------------------------------------


def resolve_target(manifest: Manifest, name: str, kind: str = "auto") -> tuple[str, str]:
    """('plane'|'product', name).

    Plane wins a name collision (five names are both, e.g. `memory`), because the
    plane is the smaller, more surprising-if-wrong action: `enable memory` must
    refuse when inference is off rather than quietly enabling inference too.
    Force the other reading with --product / --plane.
    """
    if kind == "plane":
        manifest.plane(name)
        return "plane", name
    if kind == "product":
        manifest.product(name)
        return "product", name
    if name in manifest.planes:
        return "plane", name
    if name in manifest.products:
        return "product", name
    raise Refusal(
        f"refused: unknown plane or product '{name}'.\n"
        f"  planes:   {', '.join(manifest.order)}\n"
        f"  products: {', '.join(manifest.products)}"
    )


# --------------------------------------------------------------------------
# verbs
# --------------------------------------------------------------------------


def cmd_list(manifest: Manifest, state: State, root: Path, console: Console) -> int:
    state_note = "" if state.exists else "  (absent - defaults: " + ", ".join(DEFAULT_ENABLED) + ")"
    console.line(f"manifest: {rel(root, manifest.path)}")
    console.line(f"state:    {rel(root, state.path) if state.path else '(none)'}{state_note}")
    console.line("")
    console.line("planes:")
    width = max(len(p) for p in manifest.order)
    for name in manifest.order:
        enabled_here = state.is_enabled(name)
        status = "enabled" if enabled_here else "disabled"
        bits = []
        profiles = run_profiles(manifest, state, name) if enabled_here else []
        if profiles:
            pending = set(manifest.pending_profiles(name))
            shown = [p + " (pending)" if p in pending else p for p in profiles]
            bits.append("profiles: " + ", ".join(shown))
        context = state.context_of(name)
        if context:
            bits.append(f"context: {context}")
        if manifest.manual(name):
            bits.append(f"manual: {manifest.manual(name)}")
        suffix = ("  " + "; ".join(bits)) if bits else ""
        console.line(f"  {name.ljust(width)}  {status}{suffix}" if not suffix
                     else f"  {name.ljust(width)}  {status.ljust(8)}{suffix}")
    console.line("")

    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if enabled:
        run_set = [p for p in order_planes(manifest, dependency_closure(manifest, enabled))
                   if not manifest.manual(p)]
        console.line("up would start: " + ", ".join(run_set))
    else:
        console.line("up would start: nothing (no plane enabled)")
    console.line("")
    console.line("products:")
    pwidth = max(len(p) for p in manifest.products) if manifest.products else 1
    for name, product in manifest.products.items():
        console.line(f"  {name.ljust(pwidth)}  {product.get('description', '')}")
    return EXIT_OK


def _drive(manifest, state, root, console, runner, verb_args, planes, dry_run, label, capture=None) -> int:
    for plane in planes:
        if verb_args[:1] == ["up"] and manifest.networks_only(plane):
            code = ensure_networks(manifest, state, root, console, runner,
                                   capture or subprocess_capture, plane, dry_run)
            if code != 0:
                console.line(f"# {label} stopped: {plane} exited {code}")
                return EXIT_REFUSED
            continue
        cmd = compose_command(
            manifest,
            plane,
            verb_args,
            context=state.context_of(plane),
            profiles=effective_profiles(manifest, state, root, plane),
        )
        console.line(" ".join(cmd))
        if dry_run:
            continue
        code = runner(cmd, root)
        if code != 0:
            console.line(f"# {label} stopped: {plane} exited {code}")
            return EXIT_REFUSED
    return EXIT_OK


def select_planes(manifest, state, plane, every: bool, verb: str, closure: bool = True):
    """(planes, mode) - which planes a verb acts on, and how they were chosen.

    Three selections, because scripts/stack/stack.ps1 had two and the state file
    adds a third:

      <plane>   exactly that plane. `stack.ps1 up coder` meant this and the
                runbooks still say it, so the shim keeps working.
      --all     every plane the manifest declares except the `manual` ones,
                dependency-ordered. This is what a bare `stack.ps1 up` did.
      neither   the planes THIS MACHINE enables, plus their requires closure -
                the state-driven selection stack.py was built for.

    The distinction matters. A bare `stack.py up` on a fresh clone starts the
    frontend and nothing else; if the shim quietly mapped `stack.ps1 up` onto
    that, a post-reboot bring-up would come back with one plane and no error.
    It maps onto --all instead.
    """
    if plane and every:
        raise Refusal(f"refused: `{verb}` takes a plane name or --all, not both")
    if plane:
        manifest.plane(plane)
        return [plane], "one"
    if every:
        return order_planes(manifest, [p for p in manifest.order if not manifest.manual(p)]), "all"
    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if not enabled:
        return [], "enabled"
    # `closure=False` for status: reporting on a plane nobody enabled - the
    # anchor, pulled in by `requires` - is noise, and the anchor owns no
    # services, so its `ps` is an empty table with a header.
    wanted = dependency_closure(manifest, enabled) if closure else enabled
    return order_planes(manifest, wanted), "enabled"


def _manual_notes(manifest, console, planes, what: str) -> None:
    for plane in planes:
        console.line(f"# {plane} is not driven by stack.py - {what} it with {manifest.manual(plane)}")


def _requires_note(manifest, console, plane: str, driven) -> None:
    """`up coder` starts coder alone - say what it assumes is already running."""
    unmet = [dep for dep in manifest.requires(plane) if dep not in driven]
    if unmet:
        console.line(f"# note: {plane} requires {', '.join(unmet)}; this starts only {plane}")


def _placeholder_lines(manifest, state, root, planes, capture) -> list[str]:
    lines = []
    for plane in planes:
        if missing_submodule(manifest, root, plane):
            continue
        found = placeholder_keys(manifest, root, plane) + shipped_placeholders(
            manifest, state, root, plane, capture)
        for key, why, env_path in found:
            lines.append(f"  {plane}: {key} in {rel(root, env_path)} is {why}")
    return lines


def _preflight(manifest, state, root, planes, verb: str, capture) -> None:
    """Refuse BEFORE anything runs: a missing submodule, or a key still at its placeholder.

    Both are checked for every plane first, so a refusal never lands halfway
    through a bring-up with the anchor's networks made and nothing else.
    Placeholders are the manifest `keys` rule AND the shipped-placeholder rule
    (shipped_placeholders: any key a running service reads whose value is still
    the .env.example placeholder). Blank and missing keys are NOT refused here:
    `enable`/`doctor` name those, and each plane's compose `:?` guards refuse
    them at render time.
    """
    submodule_lines = []
    for plane in planes:
        submodule = missing_submodule(manifest, root, plane)
        if submodule:
            submodule_lines.append(
                f"  {plane}: {manifest.plane(plane)['compose']} is missing because the {submodule} "
                f"submodule is not initialised - run {submodule_remedy(submodule)}"
            )
    placeholder_lines = _placeholder_lines(manifest, state, root, planes, capture)
    if submodule_lines or placeholder_lines:
        steps = []
        if submodule_lines:
            steps.append("initialise the submodule with the command named above")
        if placeholder_lines:
            steps.append("replace each placeholder with a value of your own (for a secret: "
                         "`openssl rand -hex 32`)")
        raise Refusal(
            f"refused: fix these before `{verb}` starts anything:\n"
            + "\n".join(submodule_lines + placeholder_lines)
            + "\nNothing was started. " + _sentence("; then ".join(steps)) + ", and re-run."
        )


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def cmd_up(manifest, state, root, console, runner, plane, every: bool, dry_run: bool, capture=None) -> int:
    ordered, mode = select_planes(manifest, state, plane, every, "up")
    if not ordered:
        console.line("# nothing enabled (`stack.py enable <plane|product>`, `stack.py init`, or `up --all`)")
        return EXIT_OK
    driven = [p for p in ordered if not manifest.manual(p)]
    _preflight(manifest, state, root, driven, "up", capture or subprocess_capture)
    if mode == "one":
        _requires_note(manifest, console, plane, driven)
    code = _drive(manifest, state, root, console, runner, ["up", "-d"], driven, dry_run, "up", capture)
    _manual_notes(manifest, console, [p for p in ordered if manifest.manual(p)], "start")
    return code


def cmd_down(manifest, state, root, console, runner, plane, every: bool, dry_run: bool) -> int:
    ordered, _mode = select_planes(manifest, state, plane, every, "down")
    if not ordered:
        console.line("# nothing enabled (`stack.py down --all` stops every declared plane)")
        return EXIT_OK
    driven = [p for p in reversed(ordered) if not manifest.manual(p)]
    code = _drive(manifest, state, root, console, runner, ["down"], driven, dry_run, "down")
    _manual_notes(manifest, console, [p for p in ordered if manifest.manual(p)], "stop")
    return code


def cmd_restart(manifest, state, root, console, runner, plane: str, dry_run: bool) -> int:
    if plane == "all":
        raise Refusal(
            "refused: `restart all` would restart every plane at once. Use `stack.py recover` for an ordered "
            "restart with health gates (scripts/recovery/emergency-recovery.ps1 is the Windows original), or "
            "`stack.py down` then `stack.py up`."
        )
    manifest.plane(plane)
    manual = manifest.manual(plane)
    if manual:
        raise Refusal(f"refused: {plane} is not driven by stack.py - restart it with {manual}")
    if not state.is_enabled(plane):
        console.line(f"# note: {plane} is not enabled on this machine (restarting it anyway)")
    return _drive(manifest, state, root, console, runner, ["restart"], [plane], dry_run, "restart")


def cmd_status(manifest, state, root, console, runner, plane=None, every: bool = False) -> int:
    ordered, _mode = select_planes(manifest, state, plane, every, "status", closure=False)
    if not ordered:
        console.line("# nothing enabled (`stack.py status --all` reports every declared plane)")
        return EXIT_OK
    failures = 0
    for plane in ordered:
        cmd = compose_command(
            manifest, plane, ["ps"], context=state.context_of(plane),
            profiles=effective_profiles(manifest, state, root, plane),
        )
        console.line(f"== {plane}")
        console.line(" ".join(cmd))
        if runner(cmd, root) != 0:
            failures += 1
        console.line("")
    return EXIT_REFUSED if failures else EXIT_OK


def _key_problems(manifest, state, root, planes, subject, capture, profile_map=None):
    """(problem lines, env files to edit, remedy commands) for enable/init refusals.

    A key problem is a manifest `keys` entry that is missing, blank or still its
    placeholder (blank_keys), or any other key a running service reads that is
    still its .env.example placeholder (shipped_placeholders). The remedy
    commands are the submodule inits and the `host_paths` remedies.
    """
    lines, files, submodules = [], [], []
    for plane in planes:
        for spec, reason in missing_host_paths(manifest, root, plane):
            lines.append("  " + host_path_line(spec, plane, reason))
            command = f"`{spec['remedy']}`"
            if command not in submodules:
                submodules.append(command)
        submodule = missing_submodule(manifest, root, plane)
        if submodule:
            # Its env file lives inside the submodule too, so listing every key
            # as "missing" there would send a newcomer to edit a file that
            # cannot exist yet. Name the one step that fixes all of it.
            lines.append(
                f"  {manifest.plane(plane)['compose']} is missing (plane {plane}): the {submodule} "
                f"submodule is not initialised - run {submodule_remedy(submodule)}"
            )
            if submodule_remedy(submodule) not in submodules:
                submodules.append(submodule_remedy(submodule))
            continue
        extra = (profile_map or {}).get(plane, ())
        found = blank_keys(manifest, root, plane) + shipped_placeholders(
            manifest, state, root, plane, capture, extra)
        for key, why, env_path in found:
            where = f" (read by plane {plane})" if plane != subject else ""
            lines.append(f"  {key} is {why} in {rel(root, env_path)}{where}")
            path = rel(root, env_path)
            if path not in files:
                files.append(path)
    return lines, files, submodules


def _key_remedy(files, submodules) -> str:
    """The requires-refusal names a command; this one must too.

    `submodules` holds ready-formatted remedy commands (submodule inits and
    `host_paths` remedies), as _key_problems returns them.
    """
    first = "".join(f"Run {command}. " for command in submodules)
    if submodules and not files:
        return first + "Then re-run (`python scripts/stack/stack.py doctor` lists every blank key on this machine)."
    where = " and ".join(files) if files else "the plane's env file"
    return (
        f"{first}Set them in {where} (a value still equal to the .env.example placeholder counts as unset), "
        "then re-run (`python scripts/stack/stack.py doctor` lists every blank key on this machine)."
    )


def _ambiguity_note(manifest, console, kind, target) -> None:
    """A name that is both a plane and a product resolves to the PLANE - say so.

    Both `enable` and `disable` print this: `disable` is the destructive half of
    the pair, so it is the one where a silent reading is worse.
    """
    if kind == "plane" and target in manifest.products:
        console.line(
            f"# note: '{target}' names both a plane and a product; acting on the PLANE "
            f"(use `--product {target}` for the product)"
        )


def cmd_enable(manifest, state, root, console, name, kind, headless: bool, capture=None) -> int:
    capture = capture or subprocess_capture
    kind, target = resolve_target(manifest, name, kind)
    _ambiguity_note(manifest, console, kind, target)

    if kind == "plane":
        missing = [
            dep for dep in manifest.requires(target)
            if not manifest.is_implicit(dep) and not state.is_enabled(dep)
        ]
        if missing:
            remedies = "; ".join(f"python scripts/stack/stack.py enable {d}" for d in missing)
            raise Refusal(
                f"refused: {target} requires "
                + ", ".join(missing)
                + (", which is not enabled" if len(missing) == 1 else ", which are not enabled")
                + f" ({remedies})"
            )
        problems, files, submodules = _key_problems(manifest, state, root, [target], target, capture)
        if problems:
            raise Refusal(
                f"refused: {target} cannot be enabled yet:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(files, submodules)
            )
        profiles = enable_plane_profiles(manifest, state, target, manifest.default_profiles(target))
        planes_touched = [target]
        profile_map = {target: profiles}
    else:
        product = manifest.product(target)
        wanted = list(product.get("planes", []))
        surfaces = product.get("surfaces", {}) or {}
        profile_map = {p: list(v) for p, v in (product.get("profiles", {}) or {}).items()}
        dropped_planes, dropped_profiles = [], []
        for plane, plane_profiles in surfaces.items():
            if headless:
                if plane not in wanted:
                    dropped_planes.append(plane)
                dropped_profiles.extend(f"{plane}:{p}" for p in plane_profiles)
                continue
            if plane not in wanted:
                wanted.append(plane)
            profile_map.setdefault(plane, [])
            for profile in plane_profiles:
                if profile not in profile_map[plane]:
                    profile_map[plane].append(profile)

        full = order_planes(manifest, dependency_closure(manifest, wanted))
        problems, files, submodules = _key_problems(manifest, state, root, full, target, capture, profile_map)
        if problems:
            raise Refusal(
                f"refused: product {target} cannot be enabled yet:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(files, submodules)
            )
        # An implicit plane (the anchor) is never written into state: `up` adds it
        # from the requires closure anyway, and leaving it out keeps the state file
        # a record of what the OPERATOR chose.
        planes_touched = [p for p in full if not manifest.is_implicit(p)]
        for plane in planes_touched:
            profiles = profile_map.get(plane, []) + manifest.default_profiles(plane)
            profile_map[plane] = enable_plane_profiles(manifest, state, plane, profiles)
        if headless:
            note = []
            if dropped_planes:
                note.append("planes " + ", ".join(dropped_planes))
            if dropped_profiles:
                note.append("profiles " + ", ".join(dropped_profiles))
            if note:
                console.line("# --headless: dropped surface " + "; ".join(note))

    state.save()
    console.line(f"enabled {kind} {target}:")
    for plane in planes_touched:
        profiles = run_profiles(manifest, state, plane)
        extra = ("  profiles: " + ", ".join(profiles)) if profiles else ""
        manual = manifest.manual(plane)
        tail = f"  [manual: {manual}]" if manual else ""
        console.line(f"  {plane}{extra}{tail}")
    pending = [
        f"{plane}:{p}"
        for plane in planes_touched
        for p in run_profiles(manifest, state, plane)
        if p in manifest.pending_profiles(plane)
    ]
    if pending:
        console.line(
            "# note: PENDING profiles enabled (" + ", ".join(pending) + ") - the compose files do "
            "not carry them yet, so enabling them changes nothing until the item that adds them lands."
        )
    console.line(f"state: {rel(root, state.path)}")
    return EXIT_OK


def cmd_disable(manifest, state, root, console, name, kind) -> int:
    kind, target = resolve_target(manifest, name, kind)
    _ambiguity_note(manifest, console, kind, target)
    planes = [target] if kind == "plane" else list(manifest.product(target).get("planes", [])) + list(
        (manifest.product(target).get("surfaces", {}) or {})
    )
    removed = []
    for plane in planes:
        if not state.is_enabled(plane):
            continue
        dependents = [
            other for other in manifest.order
            if other not in planes and state.is_enabled(other) and plane in manifest.requires(other)
        ]
        if dependents:
            raise Refusal(
                f"refused: {plane} is required by " + ", ".join(dependents)
                + f" (disable {' '.join(dependents)} first, or leave {plane} enabled)"
            )
        del state.planes[plane]
        removed.append(plane)
    if not removed:
        console.line(f"# {target} was not enabled; nothing to do")
        return EXIT_OK
    state.save()
    console.line("disabled: " + ", ".join(removed))
    console.line(f"state: {rel(root, state.path)}")
    return EXIT_OK


def cmd_doctor(manifest, state, root, console, runner, capture=None) -> int:
    capture = capture or subprocess_capture
    problems = 0
    console.line("== host")
    docker = shutil.which("docker")
    if docker:
        console.line(f"  [OK]   docker on PATH ({docker})")
        code = runner(["docker", "compose", "version"], root)
        if code == 0:
            console.line("  [OK]   docker compose responds")
        else:
            console.line(f"  [FAIL] `docker compose version` exited {code}")
            problems += 1
    else:
        console.line("  [FAIL] docker is not on PATH")
        problems += 1
    console.line(f"  [OK]   python {sys.version.split()[0]}")

    console.line("")
    console.line("== manifest / state")
    console.line(f"  [OK]   {rel(root, manifest.path)} parses ({len(manifest.planes)} planes, "
                 f"{len(manifest.products)} products)")
    if state.exists:
        console.line(f"  [OK]   {rel(root, state.path)}")
    else:
        console.line(f"  [ -- ] {rel(root, state.path)} absent; defaults to {', '.join(DEFAULT_ENABLED)}")

    console.line("")
    console.line("== enabled planes")
    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if not enabled:
        console.line("  [ -- ] none")
    for plane in order_planes(manifest, dependency_closure(manifest, enabled)):
        console.line(f"  {plane}")
        compose_path = root / Path(manifest.plane(plane)["compose"])
        submodule = missing_submodule(manifest, root, plane)
        if compose_path.is_file():
            console.line(f"    [OK]   compose {manifest.plane(plane)['compose']}")
        elif submodule:
            console.line(f"    [FAIL] compose file missing: {manifest.plane(plane)['compose']} - the "
                         f"{submodule} submodule is not initialised; run {submodule_remedy(submodule)}")
            problems += 1
            continue  # its env file and keys live inside the submodule too
        else:
            console.line(f"    [FAIL] compose file missing: {manifest.plane(plane)['compose']}")
            problems += 1
        for spec in manifest.plane(plane).get("host_paths", []):
            reason = host_path_problem(root, spec)
            if reason:
                console.line("    [FAIL] " + host_path_line(spec, plane, reason))
                problems += 1
            else:
                console.line(f"    [OK]   host path {spec['path']}")
        env_path = manifest.env_path(root, plane)
        loaded = (f"--env-file {manifest.env_file(plane)}" if manifest.env_file(plane)
                  else "compose loads it from the project dir")
        if env_path.is_file():
            console.line(f"    [OK]   env {rel(root, env_path)} ({loaded})")
        elif manifest.networks_only(plane) and not manifest.keys(plane):
            # The anchor declares networks and interpolates nothing, so a fresh
            # clone without a root .env is complete, not broken.
            console.line(f"    [ -- ] env {rel(root, env_path)} absent - not needed: this plane "
                         "declares only networks and reads no key")
        else:
            console.line(f"    [FAIL] env file missing: {rel(root, env_path)} ({loaded})")
            problems += 1
        for key, why, path in blank_keys(manifest, root, plane) + shipped_placeholders(
                manifest, state, root, plane, capture):
            console.line(f"    [FAIL] {key} is {why} in {rel(root, path)}")
            problems += 1
        if manifest.networks_only(plane) and docker and compose_path.is_file():
            problems += _doctor_networks(manifest, state, root, console, capture, plane)
        for requirement in manifest.plane(plane).get("host", []):
            console.line(f"    [ -- ] host: {requirement}")
    console.line("")
    console.line("OK" if problems == 0 else f"{problems} problem(s)")
    return EXIT_OK if problems == 0 else EXIT_REFUSED


def _doctor_networks(manifest, state, root, console, capture, plane) -> int:
    """One line per declared network: matches, absent (up creates it), or DIFFERS (a FAIL)."""
    try:
        rows = anchor_network_report(manifest, root, capture, plane, state.context_of(plane))
    except Refusal as refusal:
        console.line(f"    [FAIL] {str(refusal).splitlines()[0]}")
        return 1
    failed = 0
    for _project, name, _key, _spec, inspected, drift in rows:
        if inspected is None:
            console.line(f"    [ -- ] network {name} absent (`up` creates it)")
        elif drift:
            console.line(f"    [FAIL] network {name} differs from {manifest.plane(plane)['compose']}: "
                         + "; ".join(drift) + " (stack.py never alters it; `up` refuses)")
            failed += 1
        else:
            console.line(f"    [OK]   network {name} matches {manifest.plane(plane)['compose']}")
    return failed


def cmd_init(manifest, state, root, console, args, capture=None) -> int:
    capture = capture or subprocess_capture
    path = state.path
    if path.is_file() and not args.force:
        raise Refusal(f"refused: {rel(root, path)} already exists (re-run with --force to overwrite it)")

    fresh = State({}, path, False)
    contexts: dict[str, str] = {}
    for pair in args.context or []:
        plane, sep, ctx = pair.partition("=")
        if not sep or not ctx:
            raise Refusal(f"refused: --context takes plane=name, got '{pair}'")
        manifest.plane(plane)
        contexts[plane] = ctx

    selected = [p.strip() for p in (args.planes or "").split(",") if p.strip()]
    if not selected and not args.product:
        selected = list(DEFAULT_ENABLED)

    for plane in selected:
        manifest.plane(plane)
        enable_plane_profiles(manifest, fresh, plane, manifest.default_profiles(plane))

    for plane, ctx in contexts.items():
        enable_plane_profiles(manifest, fresh, plane, manifest.default_profiles(plane), context=ctx)

    # `init --product X` runs the SAME checks `enable X` does; a state file that
    # names a plane whose key is blank is a bring-up failure deferred, not avoided.
    if args.product:
        code = cmd_enable(manifest, fresh, root, console, args.product, "product", args.headless, capture)
        if code != EXIT_OK:
            return code
    else:
        chosen = order_planes(manifest, dependency_closure(manifest, fresh.planes))
        problems, files, submodules = _key_problems(manifest, fresh, root, chosen, "", capture)
        if problems:
            raise Refusal(
                "refused: that state file would not work yet:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(files, submodules)
            )
        fresh.save()

    console.line(f"wrote {rel(root, path)}")
    return cmd_list(manifest, State.load(path), root, console)


# --------------------------------------------------------------------------
# health probes
# --------------------------------------------------------------------------
#
# Fifteen of these sixteen probes are scripts/stack/stack.ps1's `health` sweep,
# one for one, with the same pass condition, the same [OK]/[FAIL] line shape and
# the same exit code (the number of FAILED probes). The sixteenth, inference
# serving depth, is NEW (sl-recovery-backups, 2026-09-21) and has no .ps1
# ancestor: the fifteen inherited ones all stayed green through a thirty-hour
# chat outage. The .ps1 is now a shim over this code, so the comments that were
# paid for in outages live HERE:
#
#   * a failing probe never stops the sweep. A stopped openwebui costs one
#     FAILED line, not the eight probes after it (see the owui-drift note).
#   * the owui-drift check REFUSES (exit 2, a sentence on stderr) rather than
#     reporting a clean bill, and REFUSED must read as FAIL. In PowerShell that
#     needed an $ErrorActionPreference dance; here it is just "the answer is
#     not a number".
#   * `search` gets TWO probes on purpose. /healthz said 200 through the whole
#     2026-09-11 outage - bing answered every query with ten results for its
#     first word, HTTP 200, no error. /health reports which engines actually
#     put results into recent payloads.


class CommandResult(NamedTuple):
    code: int
    stdout: str
    stderr: str


class HttpResult(NamedTuple):
    status: int
    body: str


def subprocess_capture(cmd, cwd) -> CommandResult:
    """Run a command and CAPTURE it. The runner seam streams; this one reads."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, errors="replace",
        )
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def urllib_get(url: str, timeout: int = 8) -> HttpResult:
    """GET a URL. Any failure is a status 0 with the reason as the body.

    Invoke-WebRequest throws on a non-2xx and the .ps1 probes let that be the
    FAIL; urllib raises HTTPError for the same cases, so both land on FAIL.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return HttpResult(getattr(response, "status", response.getcode()), body)
    except urllib.error.HTTPError as exc:  # a real answer, just not a 2xx
        return HttpResult(exc.code, "")
    except Exception as exc:  # noqa: BLE001 - URLError, timeout, ssl, OSError all mean "no answer"
        return HttpResult(0, str(exc))


# Runs INSIDE llm-gateway (`docker exec llm-gateway python -c`). It reads the
# caller key from the container's own environment, so the value never appears in
# this process, in an argv, or in a probe line. It prints exactly one line:
# `OK <sentence>` or `FAIL <sentence>` - the sweep quotes that sentence verbatim,
# which is why the failure text is written here, next to the request that
# produced it, rather than reconstructed from a status code by the caller.
#
# The 600 s read timeout is the cold-load budget: 257 s measured 2026-09-21 for
# qwen36-27b on this host, and a bigger model or a cold page cache is worse.
# The owui-drift probe's fresh-install test (ac-ci): the three tables
# check-owui-drift.ps1 compares, the same database path, opened read-only; counts only.
_OWUI_PLUGIN_CENSUS = (
    "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);"
    "print(sum(c.execute('select count(*) from '+t).fetchone()[0] for t in ('tool','function','skill')))"
)

_LANDING_COMPLETION = r"""
import json, os, time, urllib.error, urllib.request
KEY = os.environ.get("LITELLM_MASTER_KEY", "")
BASE = "http://localhost:8080"
HEAD = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
        "x-ai-stack-caller": "stack-health"}


def call(path, payload=None, timeout=30):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=HEAD)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8", "replace"))


try:
    _, listing = call("/v1/models")
    ids = [d.get("id", "") for d in (listing.get("data") or [])]
    # The embedding models load on a different upstream; a completion against one
    # proves nothing about the chat backend that was empty.
    chat = [i for i in ids if i and "embed" not in i.lower() and "bge" not in i.lower()]
    if not chat:
        print("FAIL the gateway advertises no chat model (models: %s)" % (ids or "none"))
        raise SystemExit(0)
    model = chat[0]
    started = time.time()
    status, answer = call("/v1/chat/completions", {
        "model": model, "max_tokens": 3,
        "messages": [{"role": "user", "content": "ping"}]}, 600)
    took = int(time.time() - started)
    if status == 200 and (answer.get("choices") or []):
        print("OK a 3-token completion through the gateway returned 200 in %ds (%s loaded)"
              % (took, model))
    else:
        print("FAIL %s returned HTTP %s with %d choice(s) after %ds"
              % (model, status, len(answer.get("choices") or []), took))
except urllib.error.HTTPError as exc:
    detail = ""
    try:
        detail = exc.read().decode("utf-8", "replace")[:200].replace("\n", " ")
    except Exception:
        pass
    print("FAIL the gateway answered HTTP %s: %s" % (exc.code, detail))
except Exception as exc:
    print("FAIL %s: %s" % (type(exc).__name__, str(exc)[:200].replace("\n", " ")))
"""


class HealthSweep:
    """Runs the probes, prints them, counts the failures."""

    # The planes that own probes, in sweep order. `run` probes a plane only when
    # it is in `planes` (None = every one, the pre-scoping behaviour).
    PROBED_PLANES = ("anchor", "inference", "frontend", "memory", "search", "coder", "ob1", "agent-org")

    def __init__(self, console: Console, root: Path, capture, http, planes=None):
        self.console = console
        self.root = root
        self.capture = capture
        self.http = http
        self.planes = None if planes is None else set(planes)
        self.failed = 0
        self.results: list[tuple[str, bool]] = []

    def on(self, plane: str) -> bool:
        return self.planes is None or plane in self.planes

    def probe(self, name: str, ok) -> None:
        try:
            passed = bool(ok() if callable(ok) else ok)
        except Exception:  # noqa: BLE001 - a probe that throws is a probe that failed
            passed = False
        self.console.line(("  [OK]   " if passed else "  [FAIL] ") + name)
        self.results.append((name, passed))
        if not passed:
            self.failed += 1

    # -- the shapes a probe can take ---------------------------------------

    def docker(self, *args) -> CommandResult:
        return self.capture(["docker", *args], self.root)

    def http_ok(self, url: str) -> bool:
        return self.http(url, 8).status == 200

    # -- the sweep ---------------------------------------------------------

    def run(self) -> int:
        # SCOPED TO THE ENABLED PLANES (ac-front-door). A probe for a plane this
        # machine does not run can only ever FAIL - a fresh clone running Open
        # WebUI alone used to get fourteen FAIL lines and exit 14. The skipped
        # planes are named on ONE line, so a reader can tell "not run here" from
        # "not checked". The anchor's probes always run: every plane needs it.
        skipped = [p for p in self.PROBED_PLANES if not self.on(p)]
        if self.on("anchor"):
            self.console.line("== container health (all projects)")
            unhealthy = [
                line.strip()
                for line in self.docker(
                    "ps", "--filter", "health=unhealthy", "--format", "{{.Names}}"
                ).stdout.splitlines()
                if line.strip()
            ]
            self.probe(f"0 unhealthy containers (found: {', '.join(unhealthy)})", len(unhealthy) == 0)

        self.console.line("== functional gates")
        if skipped:
            self.console.line("  [skip] not enabled on this machine, probes not run: " + ", ".join(skipped))
        if self.on("anchor"):
            self.probe(
                # Exists AND internal: llm-net is the isolation boundary, so a
                # non-internal one is a failure however healthy everything else is.
                "anchor: ai-stack_llm-net exists and is internal",
                lambda: self.docker(
                    "network", "inspect", "ai-stack_llm-net", "--format", "{{.Name}} {{.Internal}}"
                ).stdout.strip() == "ai-stack_llm-net true",
            )
        if self.on("inference"):
            self.probe(
                "inference: llm-gateway liveliness",
                # /health/liveliness, never /health: a GET of LiteLLM's /health
                # through the alias makes it load every model it advertises.
                lambda: self.docker(
                    "exec", "llm-gateway", "python", "-c",
                    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("
                    "'http://localhost:8080/health/liveliness', timeout=8).status==200 else 1)",
                ).code == 0,
            )
            depth, depth_ok = self.inference_serving_depth()
            self.probe(f"inference: {depth}", depth_ok)
        if self.on("frontend"):
            self.probe(
                "frontend: OWUI http://127.0.0.1:3000/health",
                lambda: self.http_ok("http://127.0.0.1:3000/health"),
            )
            # The frontend plane is profile-gated since sl-frontend-solo: a deployment
            # without the `tailscale` profile has no tailscale container, and telling
            # its operator that eight serve routes are missing is a FAIL line about a
            # container that is not meant to exist. This guard and its two fail-open
            # paths came across from that item's stack.ps1 when this sweep replaced it.
            deployed, note = self.tailscale_deployed()
            if deployed:
                if note:
                    self.console.line("  [warn] " + note)
                self.probe(
                    "frontend: 8 tailnet serve routes",
                    lambda: int(
                        self.docker(
                            "exec", "tailscale", "sh", "-c",
                            "tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null "
                            "| grep -c 'proxy http'",
                        ).stdout.strip()
                    ) >= 8,
                )
            else:
                self.console.line(
                    "  [skip] frontend: 8 tailnet serve routes (no tailscale profile in this deployment)"
                )
            shell = powershell_command()
            if shell is None:
                # check-owui-drift.ps1 is PowerShell-only. Off Windows, with no
                # `pwsh` on PATH, the probe cannot run - and saying so on a [skip]
                # line is honest where a FAIL about a missing interpreter is not.
                self.console.line(
                    "  [skip] frontend: owui/ manifest drift (scripts/checks/check-owui-drift.ps1 needs "
                    "PowerShell; neither Windows nor `pwsh` on PATH)"
                )
            elif self.owui_plugin_count() == 0:
                # ZERO DEPLOYED ROWS (ac-ci, 2026-09-25). check-owui-drift.ps1 REFUSES on
                # empty tables ("returned no readable rows"), which made the first `health`
                # after a clean quickstart FAIL wherever PowerShell exists. But a count of
                # zero has TWO readings and nothing here can tell them apart: a fresh install,
                # or a host whose plugins were wiped (a reset, an empty restore, the wrong
                # volume). So it neither fails nor passes silently: a [warn] line naming both,
                # and no probe counted. Only a counted zero lands here - a census that could
                # not be read (None) runs the real check, and so does one deployed row.
                self.console.line(
                    "  [warn] frontend: owui/ manifest drift NOT CHECKED - 0 plugins deployed in this "
                    "Open WebUI: a fresh install, or this host's plugins were wiped; paste them per "
                    "frontend/owui/README.md (\"Redeploy mechanism\")"
                )
            else:
                drift = self.owui_drift(shell)
                self.probe(f"frontend: owui/ manifest rows drifted from live webui.db: {drift}", drift == "0")
        if self.on("memory"):
            self.probe(
                "memory: cloud door http://127.0.0.1:8060/health",
                lambda: self.http_ok("http://127.0.0.1:8060/health"),
            )
        if self.on("search"):
            self.probe(
                "search: gateway http://127.0.0.1:8085/healthz",
                lambda: self.http_ok("http://127.0.0.1:8085/healthz"),
            )
            engines = self.search_engines()
            self.probe(f"search: {engines}", engines != "REFUSED" and not engines.startswith("DEGRADED"))
        if self.on("coder"):
            self.probe(
                "coder: little-coder daemon :8090/health",
                lambda: self.docker(
                    "exec", "little-coder", "curl", "-fsS", "--max-time", "8",
                    "http://localhost:8090/health",
                ).code == 0,
            )
        if self.on("ob1"):
            self.probe(
                "OB1: open_notebook API :5055/api/config",
                lambda: self.http_ok("http://127.0.0.1:5055/api/config"),
            )
            self.probe("OB1: ops door :8062/health", lambda: self.http_ok("http://127.0.0.1:8062/health"))
            # The curator answers 503 with {"ok":false,"db":false} when its DB is gone
            # and nothing at all while crash-looping (2026-09-05: "Module not found
            # pool.ts", noticed 14 h late from a disk check). Either reads as FAIL.
            self.probe(
                "OB1: research-curator http://127.0.0.1:8816/health",
                lambda: self.http_ok("http://127.0.0.1:8816/health"),
            )
            self.probe(
                "OB1: openbrain-db accepting connections",
                lambda: self.docker(
                    "exec", "openbrain-db", "pg_isready", "-U", "postgres", "-d", "openbrain", "-t", "5"
                ).code == 0,
            )
        if self.on("agent-org"):
            self.probe(
                "agent-org: mattermost ping",
                lambda: self.docker(
                    "exec", "agent-bridge", "python", "-c",
                    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("
                    "'http://mattermost:8065/api/v4/system/ping', timeout=8).status==200 else 1)",
                ).code == 0,
            )

        self.console.line("")
        if self.failed == 0:
            self.console.line("ALL HEALTH PROBES PASSED")
        else:
            self.console.line(f"{self.failed} probe(s) FAILED")
        return self.failed

    # -- the probes whose LABEL carries the measurement --------------------

    def inference_serving_depth(self):
        """(what the label should say, did it pass?). Never raises.

        THE OUTAGE THIS EXISTS FOR (2026-09-19 18:54 -> 2026-09-21 00:57, ~30 h):
        llama-cpp-upstream was recreated with LM_MODELS_DIR unset, so compose
        bound its default `../../data/models/gguf` - an empty directory - at
        /models. Every probe in this sweep stayed GREEN for thirty hours:
        the container was healthy, the anchor network existed, and LiteLLM's
        /health/liveliness answered 200. llama-swap's own /health answers
        without loading a model, so nothing anywhere asked the one question
        that mattered. The first real chat returned
        `500 upstream command exited prematurely`.

        So this probe asks it, in the cheapest order that still cannot be fooled:

          1. Are there any .gguf files under the upstream's /models? Zero is a
             FAIL that names the HOST path of the bind, and it is checked FIRST
             because a completion against an empty store costs a 500 and a
             confusing message instead of a diagnosis.
          2. Is a model already resident? llama-swap's /running says so. That is
             proof of serving depth at zero cost, and it is the normal case.
          3. Only if nothing is resident: ONE completion of at most three tokens
             through the GATEWAY (never around it - CLAUDE.md; the /models and
             /running reads above are the health/GPU/recovery exception, and they
             read, they do not serve). A cold load is minutes, not seconds - the
             2026-09-21 repair measured 257 s - so the in-container timeout is
             generous on purpose. A probe that times out at 30 s and calls that a
             failure would page the operator for a working stack.

        The caller key is LITELLM_MASTER_KEY. This reads inference/.env only to
        confirm it is CONFIGURED - an unmigrated host gets a named refusal
        instead of a 401 to decode - and never handles the value: the request is
        made inside llm-gateway by a script that reads the container's own
        environment, which compose populated from that same file
        (`LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}` in inference/compose/gateway.yml).
        Being precise, because the loose version of this sentence was wrong once:
        read_env_file returns a dict of EVERY value in that file, so the key IS
        briefly in this process's memory. What is guaranteed is narrower and is
        the part that matters - it is read for a PRESENCE CHECK only, and no
        secret value is ever passed as an argument, logged, or printed.
        """
        upstream = "llama-cpp-upstream"
        listing = self.docker(
            "exec", upstream, "sh", "-c",
            "find /models -maxdepth 4 -name '*.gguf' 2>/dev/null | head -n 5 | wc -l",
        )
        if listing.code != 0:
            why = (listing.stderr or listing.stdout or "no output").strip().splitlines()
            return (f"serving depth: cannot read {upstream}'s /models "
                    f"({why[0] if why else 'no output'}) - is the upstream running? "
                    f"(the `local` profile lives in inference/.env)"), False
        try:
            found = int((listing.stdout or "0").strip().splitlines()[-1])
        except (ValueError, IndexError):
            found = 0
        if found == 0:
            return (f"serving depth: {upstream}'s /models holds NO .gguf files "
                    f"(host bind: {self.models_bind(upstream)}) - llama-swap will answer "
                    f"/health and then 500 the first completion. Set LM_MODELS_DIR in "
                    f"inference/.env and recreate the upstream."), False

        running = self.docker(
            "exec", upstream, "curl", "-s", "--max-time", "10",
            "http://localhost:8080/running",
        )
        resident = ""
        try:
            for entry in (json.loads(running.stdout or "{}").get("running") or []):
                if (entry or {}).get("state") == "ready":
                    resident = str(entry.get("model") or "")
                    break
        except (ValueError, AttributeError, TypeError):
            resident = ""
        if resident:
            return f"serving depth: {resident} resident on {upstream} (/running)", True

        key_present = bool(read_env_file(self.root / "inference" / ".env").get("LITELLM_MASTER_KEY"))
        if not key_present:
            return ("serving depth: nothing resident and LITELLM_MASTER_KEY is missing from "
                    "inference/.env, so the landing completion cannot be attempted "
                    "(documentation/runbooks/env-split-migration.md)"), False

        result = self.docker("exec", "llm-gateway", "python", "-c", _LANDING_COMPLETION)
        answer = (result.stdout or "").strip().splitlines()
        answer = answer[-1] if answer else ""
        if answer.startswith("OK "):
            return f"serving depth: nothing was resident; {answer[3:]}", True
        detail = answer or (result.stderr or "no output").strip().splitlines()[-1:]
        return (f"serving depth: nothing resident and the landing completion FAILED - "
                f"{detail if isinstance(detail, str) else (detail[0] if detail else 'no output')}"), False

    def models_bind(self, container: str) -> str:
        """The HOST path bound at /models, or a stand-in. Names what to fix."""
        out = self.docker(
            "inspect", container, "--format",
            '{{range .Mounts}}{{if eq .Destination "/models"}}{{.Source}}{{end}}{{end}}',
        )
        return (out.stdout or "").strip() or "<no /models mount on the container>"

    def tailscale_deployed(self):
        """(is it part of THIS deployment?, a note to print first).

        WHICH SOURCE: the RENDERED project, not a parse of `.env`. That is
        compose's own answer after applying COMPOSE_PROFILES from the env file,
        from the environment, and its own precedence rules; reimplementing that
        here would drift the moment any of them changes.

        AND IT FAILS OPEN, twice over, because a checker that goes quiet on its
        own error is the failure mode this stack keeps paying for:
          * the render produced nothing (docker down, or an .env so incomplete
            that the compose file's fail-loud WEBUI_SECRET_KEY guard rejects it)
            -> probe anyway and say why;
          * the render says no tailscale while a container NAMED tailscale is
            running -> that is a host whose .env lost the frontend profiles from
            COMPOSE_PROFILES. Probe anyway, and say that too.
        """
        # frontend/.env, NOT frontend/.env.example, and the asymmetry with the
        # inventory generator is deliberate. This asks what THIS HOST DEPLOYS, so
        # it has to read this host's COMPOSE_PROFILES; render_env_path() asks what
        # the compose file DECLARES and uses the .example so the answer is the
        # same on a laptop, this host and a CI runner. Same command, two
        # questions. NO --env-file: compose loads frontend/.env natively from the
        # project directory, which is where this host's profiles live (D17).
        rendered = self.capture(
            ["docker", "compose", "-f", "frontend/docker-compose.yml",
             "config", "--services"],
            self.root,
        )
        services = [line.strip() for line in rendered.stdout.splitlines() if line.strip()]
        if rendered.code != 0 or not services:
            return True, ("the frontend plane rendered NOTHING (docker down, or .env "
                          "missing/incomplete) - cannot tell whether tailscale is deployed, so "
                          "probing it anyway")
        if "tailscale" in services:
            return True, ""
        # Exact match, never a substring: `--filter name=tailscale` also matches
        # stt-tts-tailscale, and the `^...$` anchors do not survive a shell.
        running = [
            line.strip()
            for line in self.docker("ps", "--filter", "name=tailscale",
                                    "--format", "{{.Names}}").stdout.splitlines()
            if line.strip() == "tailscale"
        ]
        if running:
            return True, ("tailscale is RUNNING but absent from the frontend render - "
                          "frontend/.env is probably missing the frontend profiles from "
                          "COMPOSE_PROFILES (per-plane since sl-env-split: the whole value "
                          "there is gpu,tailscale, and inference/.env carries local "
                          "separately). If frontend/.env is absent this host has not been "
                          "migrated - documentation/runbooks/env-split-migration.md")
        return False, ""

    def owui_plugin_count(self):
        """How many tool/function/skill rows the live webui.db holds, or None if unknown.

        Read-only (SQLite `mode=ro`), inside the container, counts only. None - the
        container, python3, the file or a table missing, or anything unparseable -
        is NOT zero: the caller then runs the real drift check, which says why.
        """
        result = self.capture(
            ["docker", "exec", "openwebui", "python3", "-c", _OWUI_PLUGIN_CENSUS], self.root
        )
        text = result.stdout.strip()
        if result.code != 0 or not text.isdigit():
            return None
        return int(text)

    def owui_drift(self, shell=("powershell",)) -> str:
        """'0', a drifted count, or 'REFUSED - <why>'. Never raises.

        owui/ plugins deploy BY PASTE: nothing links the repo file to the live
        webui.db row, so a committed fix can sit unpasted for weeks (the
        deep_research banner, 2026-09-04..06). check-owui-drift.ps1 -CountOnly
        prints the count on stdout, or the word REFUSED with the sentence on
        stderr and exit 2. Anything that is not a number is a FAIL.
        """
        script = self.root / "scripts" / "checks" / "check-owui-drift.ps1"
        result = self.capture(
            [*shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-CountOnly"],
            self.root,
        )
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        answer = values[-1] if values else "REFUSED"
        if not answer.isdigit():
            why = "the check produced no answer"
            errors = [line.strip() for line in result.stderr.splitlines() if line.strip()]
            if errors:
                why = errors[0]
                if why.startswith("REFUSED:"):
                    why = why[len("REFUSED:"):].strip()
            return f"REFUSED - {why}"
        return answer

    def search_engines(self) -> str:
        """'<verdict> - <n> engine(s) answering', or 'REFUSED'. Never raises.

        'unknown' (nothing searched since the gateway started) is NOT a failure
        here - only a measured DEGRADED is - and the probe prints the number.
        """
        response = self.http("http://127.0.0.1:8085/health", 8)
        if response.status != 200:
            return "REFUSED"
        try:
            payload = json.loads(response.body)
            best = 0
            for provider in (payload.get("providers") or {}).values():
                count = int((provider or {}).get("engines_answering_now") or 0)
                best = max(best, count)
            return f"{payload.get('search')} - {best} engine(s) answering"
        except (ValueError, AttributeError, TypeError):
            return "REFUSED"


def powershell_command():
    """The interpreter for the .ps1 checks, or None when there is none.

    `powershell` on Windows (5.1 is what those scripts are written for); off
    Windows, `pwsh` when it is on PATH. A function rather than a constant so a
    test can ask either question without touching the host.
    """
    if WINDOWS:
        return ("powershell",)
    return ("pwsh",) if shutil.which("pwsh") else None


def cmd_health(manifest, state, root, console, capture, http) -> int:
    """Exit code is the NUMBER OF FAILED PROBES among the probes that RAN.

    Only the planes this machine ENABLES, plus the implicit anchor, are probed;
    the rest are named on one [skip] line. Deliberately NOT the requires-closure
    `up` starts: `enable` already refuses a plane whose requirements are off, so
    on any state the driver wrote the two sets are equal, and on a hand-edited
    state that differs, probing a plane nobody enabled is exactly the noise this
    scoping removes.
    """
    planes = {p for p in manifest.order if state.is_enabled(p) or manifest.is_implicit(p)}
    return HealthSweep(console, root, capture, http, planes).run()


# --------------------------------------------------------------------------
# operate: the rendered plane (shared by recover, backup and restore)
# --------------------------------------------------------------------------
#
# ac-ops-portable. Everything below is derived from compose's OWN render of a
# plane (`docker compose -f <file> [--profile ...] config --format json`, with
# the same --profile flags `up` passes), never from a hand list: which services
# run, in what order (`depends_on`, plus `network_mode: service:X`), how long a
# healthcheck may take, and which named volumes the plane owns. A hand list is
# how scripts/recovery/emergency-recovery.ps1 ended up carrying eight service
# arrays that each had to be edited whenever a container moved.

# The throwaway container that reads and writes a volume for backup/restore.
# The same image every backup sidecar in this repo already runs
# (frontend/docker-compose.yml's `x-backup-sidecar`), so a host that has run
# any plane has it; it is pulled otherwise.
HELPER_IMAGE = "alpine:3.21"
# Images whose data directory is a live database. A running one is never tarred
# (a torn copy restores as a corrupt cluster); the plane's dump sidecar is named.
DB_ENGINES = ("postgres", "pgvector", "surrealdb")
GATE_POLL_SECONDS = 3
# How long a container with NO healthcheck must stay running, unrestarted, before
# its gate passes. A service that declares a longer restart delay
# (deploy.restart_policy.delay) gets that plus a poll instead, so one crash-and-
# restart always lands inside the window.
SETTLE_SECONDS = 15
# A container with no COMPOSE healthcheck may still have an IMAGE one; this is
# the budget for that case, since the render cannot say what the image declares.
DEFAULT_GATE_TIMEOUT = 300
GATE_MARGIN_SECONDS = 30
STOP_TIMEOUT_SECONDS = 30
BACKUP_MANIFEST = "manifest.json"
BACKUP_FORMAT = 1

# Seams for the gate loop, so a test can run a four-minute timeout in no time.
sleep = time.sleep
monotonic = time.monotonic


class Service(NamedTuple):
    key: str
    container: str | None       # container_name, when the render sets one
    depends: dict                # service key -> depends_on condition
    netns: str | None            # X in `network_mode: service:X`
    healthcheck: dict | None     # the compose healthcheck, None when absent or disabled
    image: str
    mounts: tuple                # ((type, source, target, read_only), ...)
    environment: dict = {}       # the rendered environment (never printed)
    restart_delay: float = 0.0   # deploy.restart_policy.delay, seconds
    restart: str = ""            # `restart:` (or deploy.restart_policy.condition), "" when unset
    healthcheck_disabled: bool = False  # `healthcheck: disable: true` / test NONE (overrides the image's)


class PlaneRender(NamedTuple):
    plane: str
    project: str
    services: dict               # key -> Service
    volumes: dict                # volume key -> {"name": ..., "external": bool}


def plane_render(manifest: Manifest, state: State, root: Path, plane: str, capture) -> PlaneRender:
    """The plane as compose renders it under the profiles `up` would pass.

    Interpolated (unlike the anchor's render), because depends_on conditions and
    healthcheck timings may come from variables. The output is parsed, never
    printed: an interpolated render carries the plane's secrets.
    """
    compose_rel = manifest.plane(plane)["compose"]
    submodule = missing_submodule(manifest, root, plane)
    if submodule:
        raise Refusal(f"refused: {compose_rel} is missing because the {submodule} submodule is not "
                      f"initialised - run {submodule_remedy(submodule)}")
    cmd = compose_command(manifest, plane, ["config", "--format", "json"], context=state.context_of(plane),
                          profiles=effective_profiles(manifest, state, root, plane))
    result = capture(cmd, root)
    if result.code != 0:
        why = (result.stderr or result.stdout or "no output").strip().splitlines()[:3]
        raise Refusal(f"refused: could not render {compose_rel} (`{' '.join(cmd)}` exited {result.code}):\n  "
                      + "\n  ".join(why))
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise Refusal(f"refused: the render of {compose_rel} is not JSON ({exc})") from None
    project = data.get("name") or ""
    services = {}
    for key, spec in (data.get("services") or {}).items():
        spec = spec or {}
        depends = spec.get("depends_on") or {}
        if isinstance(depends, list):
            depends = {name: "service_started" for name in depends}
        else:
            depends = {name: (cond or {}).get("condition", "service_started") for name, cond in depends.items()}
        mode = spec.get("network_mode") or ""
        health = spec.get("healthcheck") or None
        disabled = False
        if health and (health.get("disable") or list(health.get("test") or [])[:1] == ["NONE"]):
            health, disabled = None, True
        mounts = tuple(
            (m.get("type"), m.get("source"), m.get("target"), bool(m.get("read_only")))
            for m in (spec.get("volumes") or []) if isinstance(m, dict)
        )
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
        policy = (spec.get("deploy") or {}).get("restart_policy") or {}
        delay = policy.get("delay")
        restart = str(spec.get("restart") or "")
        if not restart and policy.get("condition"):
            restart = {"none": "no", "any": "always"}.get(str(policy["condition"]), str(policy["condition"]))
        services[key] = Service(key, spec.get("container_name"), depends,
                                mode[len("service:"):] if mode.startswith("service:") else None,
                                health, str(spec.get("image") or ""), mounts,
                                {str(k): str(v) for k, v in env.items() if v is not None},
                                parse_duration(delay, 0.0), restart, disabled)
    volumes = {}
    for key, spec in (data.get("volumes") or {}).items():
        spec = spec or {}
        volumes[key] = {"name": spec.get("name") or f"{project}_{key}", "external": bool(spec.get("external"))}
    return PlaneRender(plane, project, services, volumes)


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(h|ms|us|µs|ns|m|s)")
_DURATION_SECONDS = {"h": 3600, "m": 60, "s": 1, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}


def parse_duration(value, default: float) -> float:
    """Seconds from a compose duration: '15s', '1m0s', '1m30s', '500ms'.

    compose renders durations as Go strings; a bare number is Go's nanoseconds.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value) / 1e9
    parts = _DURATION.findall(str(value))
    if not parts:
        return default
    return sum(float(n) * _DURATION_SECONDS[unit] for n, unit in parts)


def gate_timeout(service: Service, override: int | None = None) -> int:
    """How long recover waits for one container.

    The healthcheck's OWN worst case, not a guess per service: docker marks a
    container unhealthy after `retries` consecutive failures once `start_period`
    is over, so start_period + retries x (interval + timeout) + one interval is
    the longest it can take to reach a verdict. A margin is added. Docker's
    defaults (30s / 30s / 3 / 0s) fill any field the compose file leaves out.
    """
    if override:
        return int(override)
    hc = service.healthcheck
    if not hc:
        return DEFAULT_GATE_TIMEOUT
    interval = parse_duration(hc.get("interval"), 30)
    probe = parse_duration(hc.get("timeout"), 30)
    retries = int(hc.get("retries") or 3)
    start = parse_duration(hc.get("start_period"), 0)
    return int(start + retries * (interval + probe) + interval + GATE_MARGIN_SECONDS)


def start_levels(render: PlaneRender) -> list[list[str]]:
    """The plane's services in dependency LEVELS: level n needs only levels < n.

    Edges: every `depends_on` naming a rendered service (a `required: false`
    dependency that its profile left out is simply absent), and `network_mode:
    service:X`, which is a harder edge than any depends_on - the tenant lives
    INSIDE X's network namespace. Ties inside a level are alphabetical, which is
    also the order compose's JSON render lists services in.
    """
    services = render.services
    deps = {}
    for key, svc in services.items():
        wanted = {d for d in svc.depends if d in services}
        if svc.netns:
            if svc.netns not in services:
                raise Refusal(f"refused: {render.plane}/{key} shares the network namespace of '{svc.netns}', "
                              "which this plane's render does not contain")
            wanted.add(svc.netns)
        deps[key] = wanted
    levels, placed, remaining = [], set(), set(services)
    while remaining:
        ready = sorted(k for k in remaining if deps[k] <= placed)
        if not ready:
            raise Refusal(f"refused: the depends_on edges of {render.plane} contain a cycle involving "
                          + ", ".join(sorted(remaining)))
        levels.append(ready)
        placed.update(ready)
        remaining.difference_update(ready)
    check_netns(render, levels)
    return levels


def check_netns(render: PlaneRender, levels) -> None:
    """THE NETNS RULE, as a check on the plan rather than a comment beside it.

    `tailscale` runs `network_mode: service:openwebui` (frontend/docker-compose.yml),
    so it lives inside openwebui's network namespace. Restarting openwebui makes a
    new namespace and orphans a tailscale that is not restarted AFTER it - "Up",
    but with no connectivity and no serve config. So, for every provider X in the
    plan: every tenant of X is in the plan too (X is never restarted alone), and
    each tenant is started in a LATER level than X and stopped in an EARLIER one
    (stop runs the levels in reverse). emergency-recovery.ps1 encodes the same
    rule by hand in Invoke-MinimalRecovery (restart openwebui, Wait-ForHealthy,
    then restart tailscale) and, for its full path, relies on the project's
    depends_on.
    """
    level_of = {key: n for n, level in enumerate(levels) for key in level}
    for key, svc in render.services.items():
        provider = svc.netns
        if not provider or (key not in level_of and provider not in level_of):
            continue
        if key not in level_of:
            raise Refusal(f"refused: {render.plane}/{provider} would be restarted without {key}, which shares "
                          "its network namespace; never restart one alone")
        if provider not in level_of or level_of[provider] >= level_of[key]:
            raise Refusal(f"refused: {render.plane}/{key} shares {provider}'s network namespace and must "
                          f"start after it")


def settle_seconds(service: Service) -> float:
    """The settle window for a container with no healthcheck (see wait_gate)."""
    if service.restart_delay:
        return max(SETTLE_SECONDS, service.restart_delay + GATE_POLL_SECONDS)
    return SETTLE_SECONDS


GATE_COMPLETES = "completes"   # something waits on it with service_completed_successfully
GATE_HEALTHY = "healthy"       # a compose healthcheck
GATE_ONE_SHOT = "one-shot"     # restart "no" (or none): exit 0 is success, or it settles running
GATE_SETTLE = "settle"         # a restart policy and no healthcheck: running, not restarted


def image_has_healthcheck(capture, root, docker, image: str):
    """True / False from `docker image inspect` (read-only), or None when the image is not here."""
    found = capture(docker + ["image", "inspect", "--format", "{{json .Config.Healthcheck}}", image], root)
    if found.code != 0:
        return None
    try:
        data = json.loads((found.stdout or "").strip() or "null")
    except ValueError:
        return None
    test = list((data or {}).get("Test") or [])
    return bool(test) and test[:1] != ["NONE"]


def check_depends_conditions(renders: dict, capture, root, dockers: dict) -> list[str]:
    """Refuse, BEFORE anything is stopped, a depends_on condition compose itself would refuse.

    recover starts each level with `up -d --no-deps`, which skips compose's own condition
    checks, so they are made here (review of ac-ops-portable, 2026-09-25: a service_healthy
    dependency on a target with no healthcheck waited out the settle window and printed
    "recovered", where plain `docker compose up` refuses the configuration):
      * `service_healthy` on a target with no healthcheck - none in the compose file (or
        `disable: true`) and none in its image (`docker image inspect`, read-only);
      * `service_completed_successfully` on a target with `restart: always` or
        `unless-stopped` - docker restarts it after it exits, so it can never complete.
    Returns warnings for what cannot be decided (an image not on this daemon); raises
    Refusal for a definite violation, naming plane, service and target.
    """
    problems, warnings = [], []
    for plane, render in renders.items():
        for svc in render.services.values():
            for target, condition in sorted(svc.depends.items()):
                dep = render.services.get(target)
                if dep is None:
                    continue
                if condition == "service_healthy" and not dep.healthcheck:
                    if dep.healthcheck_disabled:
                        problems.append(f"  {plane}/{svc.key} depends_on {target} with condition service_healthy, "
                                        f"but {target} disables its healthcheck in the compose file")
                        continue
                    has = image_has_healthcheck(capture, root, dockers[plane], dep.image)
                    if has is False:
                        problems.append(f"  {plane}/{svc.key} depends_on {target} with condition service_healthy, "
                                        f"but {target} has no healthcheck (none in the compose file, none in its "
                                        f"image {dep.image})")
                    elif has is None:
                        warnings.append(f"# WARNING: {plane}/{svc.key} depends_on {target} with condition "
                                        f"service_healthy; {target} has no compose healthcheck and its image "
                                        f"{dep.image} is not on this daemon, so whether it has one is unknown")
                if condition == "service_completed_successfully" and dep.restart in ("always", "unless-stopped"):
                    problems.append(f"  {plane}/{svc.key} depends_on {target} with condition "
                                    f"service_completed_successfully, but {target} has restart: {dep.restart}, "
                                    "so docker restarts it after it exits and it can never complete")
    if problems:
        raise Refusal("refused: a depends_on condition in these renders cannot be met - docker compose's own "
                      "`up` refuses such a configuration, and recover's `up -d --no-deps` would not check it:\n"
                      + "\n".join(problems)
                      + "\nNothing was stopped. Fix the compose file (add the healthcheck, or change the "
                        "condition), then re-run.")
    return warnings


def gate_kind(render: PlaneRender, key: str, completes=None) -> str:
    """Which gate a service gets, derived from the render (attempt 3).

    completes - another service depends_on it with `service_completed_successfully`:
                its dependants may start only after it EXITS 0. Attempt 2 let such a
                service pass the settle window while still running and started its
                dependant 3.4 s before it finished (tester, R2).
    healthy   - a compose healthcheck: wait for `healthy`.
    one-shot  - `restart: "no"` or no restart policy at all: exiting is allowed, so exit 0
                passes (attempt 2 refused an init job's exit 0 as "a crash", R1), a
                non-zero exit fails, and a service that keeps running settles as below.
    settle    - a restart policy and no healthcheck: running, unrestarted, for the window.
    """
    if completes is None:
        completes = one_shot_services(render)
    if key in completes:
        return GATE_COMPLETES
    svc = render.services[key]
    if svc.healthcheck:
        return GATE_HEALTHY
    if svc.restart in ("", "no"):
        return GATE_ONE_SHOT
    return GATE_SETTLE


def one_shot_services(render: PlaneRender) -> set[str]:
    """Services something waits on with `service_completed_successfully`: exit 0 is their success."""
    return {dep for svc in render.services.values()
            for dep, cond in svc.depends.items() if cond == "service_completed_successfully"}


def container_of(render: PlaneRender, key: str) -> str:
    """The container name recover inspects. compose's own default when none is set."""
    return render.services[key].container or f"{render.project}-{key}-1"


# `.State` plus the container's RestartCount, which docker keeps OUTSIDE .State
# (a top-level field). The first cut read it from .State, found None every time,
# and so no container without a healthcheck could ever pass its gate - the DinD
# rehearsal caught it; the unit fake had modelled the same misreading.
_STATE_FORMAT = "{{json .State}}|{{.RestartCount}}"


def container_state(capture, root, docker, name) -> dict | None:
    """The container's .State, with "RestartCount" added from the top level; None when absent."""
    found = capture(docker + ["inspect", "--format", _STATE_FORMAT, name], root)
    if found.code != 0:
        return None
    text, _sep, restarts = (found.stdout or "").strip().rpartition("|")
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data["RestartCount"] = int(restarts) if restarts.strip().isdigit() else None
    return data


def _last_health_output(state: dict) -> str:
    log = ((state or {}).get("Health") or {}).get("Log") or []
    if not log:
        return ""
    text = " ".join(str((log[-1] or {}).get("Output") or "").split())
    return text[:200]


def wait_gate(capture, root, docker, name: str, timeout: int, kind: str = GATE_SETTLE,
              settle: float = SETTLE_SECONDS):
    """(passed, what was seen). Polls `docker inspect` until a verdict or the timeout.

    healthy                          -> pass
    unhealthy                        -> fail at once (docker already gave up)
    exited / dead                    -> fail, unless a one-shot service exited 0
    restarting                       -> fail at once: docker is inside its restart
                                        policy, i.e. the container already crashed
    running with NO health status    -> a SETTLE WINDOW: it must stay `running`, with
                                        the same RestartCount and the same StartedAt,
                                        for `settle` seconds from the first poll that
                                        saw it running. Any restart or exit inside the
                                        window fails the gate with a named reason. A
                                        container that crashes LATER than the window
                                        still passes - that is the one thing this gate
                                        cannot see (findings O4).
    anything else (starting, created, absent) -> keep waiting

    kind=completes: only the EXIT counts - exit 0 passes, any other exit fails, and
    running (or restarting under an on-failure policy) keeps waiting until the timeout.
    kind=one-shot: as above for running containers, but an exit 0 at any point passes.
    """
    started = monotonic()
    window = None            # (monotonic at first running, RestartCount, StartedAt)
    last = "no container"
    while True:
        state = container_state(capture, root, docker, name)
        now = monotonic()
        elapsed = int(now - started)
        if state is not None:
            status = state.get("Status") or "?"
            health = (state.get("Health") or {}).get("Status")
            restarts = state.get("RestartCount")
            last = f"{status}/{health}" if health else status
            if kind == GATE_COMPLETES:
                if status in ("exited", "dead"):
                    code = state.get("ExitCode")
                    if code == 0:
                        return True, f"completed (exit 0) after {elapsed}s"
                    return False, (f"exited with exit code {code} - a service others wait on with "
                                   "service_completed_successfully must exit 0")
                if elapsed >= timeout:
                    return False, f"did not complete within {timeout}s (last seen: {last})"
                sleep(GATE_POLL_SECONDS)
                continue
            if health == "healthy":
                return True, f"healthy after {elapsed}s"
            if health == "unhealthy":
                output = _last_health_output(state)
                return False, "unhealthy" + (f" - last healthcheck output: {output}" if output else "")
            if status in ("exited", "dead"):
                code = state.get("ExitCode")
                if kind == GATE_ONE_SHOT and code == 0:
                    return True, f"exited 0 after {elapsed}s (restart: \"no\" - a one-shot, exit 0 is its success)"
                if window is not None:
                    return False, (f"exited with exit code {code} {int(now - window[0])}s after it was first "
                                   "seen running (a crash inside the settle window)")
                return False, f"{status} with exit code {code}"
            if status == "restarting":
                return False, f"restart loop: docker reports it restarting (RestartCount {restarts})"
            if status == "running" and not health:
                if window is None:
                    window = (now, restarts, state.get("StartedAt"))
                elif restarts != window[1] or state.get("StartedAt") != window[2]:
                    return False, (f"restart loop: it restarted {int(now - window[0])}s into the "
                                   f"{int(settle)}s settle window (RestartCount {window[1]} -> {restarts})")
                elif now - window[0] >= settle:
                    return True, (f"running and steady for {int(now - window[0])}s (no healthcheck; "
                                  f"RestartCount {restarts}, not restarted)")
        if elapsed >= timeout:
            return False, f"no verdict within {timeout}s (last seen: {last})"
        sleep(GATE_POLL_SECONDS)


# --------------------------------------------------------------------------
# recover - the portable equivalent of emergency-recovery.ps1's `recover`
# --------------------------------------------------------------------------


def _gate_note(render: PlaneRender, key: str, timeout: int) -> str:
    svc = render.services[key]
    kind = gate_kind(render, key)
    if kind == GATE_COMPLETES:
        what = "exits 0 (another service waits on it with service_completed_successfully)"
    elif kind == GATE_HEALTHY:
        what = "healthy (compose healthcheck)"
    elif kind == GATE_ONE_SHOT:
        what = (f"exits 0, or runs unrestarted for {int(settle_seconds(svc))}s (restart: "
                f"{svc.restart or 'unset'} - a one-shot may exit)")
    else:
        what = (f"running and not restarted for {int(settle_seconds(svc))}s (restart: {svc.restart}; no "
                "compose healthcheck; an image healthcheck is honoured if it has one)")
    return f"#   gate [{kind}]: {container_of(render, key)} {what}, up to {timeout}s"


def cmd_recover(manifest, state, root, console, runner, capture, plane, every: bool, dry_run: bool,
                timeout: int | None = None) -> int:
    """Stop the selected planes in reverse dependency order, then start them in order, gated.

    The ordered, health-gated restart that emergency-recovery.ps1's `recover`
    mode performs in its full path (Invoke-EmergencyRecovery: Phase 1 graceful
    shutdown in reverse order, Phase 3 restart in dependency order with
    Wait-ForHealthy), with every order derived instead of listed:

      planes     - the manifest's `requires` graph, ties broken by declaration
                   order: the same order `up` uses (order_planes).
      containers - each plane's render, in depends_on levels (start_levels), so
                   the netns rule (openwebui before tailscale, check_netns) and the
                   inference rule (both llama.cpp upstreams and llm-queue before
                   llm-gateway, inference/compose/gateway.yml's depends_on) come out
                   of the compose files that define them.
      gates      - EVERY container, not one per plane, each for its own
                   healthcheck's worst case (gate_timeout).

    Every plane is rendered BEFORE anything is stopped, so a plane that cannot be
    rendered is a refusal with the stack still running, not a half-stopped stack.
    The first failed gate STOPS the run with a line naming the plane, the service,
    the container and what docker reported; the planes after it stay stopped and
    are listed.

    NOT ported, deliberately: the pre-flight diagnostics and "minimal" path, the
    fall-through to `nuclear`, the GPU check and the tailscale ping - see
    scripts/stack/README.md (`recover`).
    """
    if plane and manifest.manual(plane):
        raise Refusal(f"refused: {plane} is not driven by stack.py - recover it with {manifest.manual(plane)}")
    ordered, mode = select_planes(manifest, state, plane, every, "recover")
    if not ordered:
        console.line("# nothing enabled (`stack.py enable <plane|product>`, `stack.py init`, or `recover --all`)")
        return EXIT_OK
    manual = [p for p in ordered if manifest.manual(p)]
    driven = [p for p in ordered if not manifest.manual(p)]
    # The anchor's networks are ENSURED first even for one plane: every plane
    # attaches to them externally, and a recovery on a daemon where they were
    # lost (a Docker Desktop reset, a `network prune`) would otherwise fail at
    # the first `up` with "declared as external, but could not be found".
    anchors = [p for p in driven if manifest.networks_only(p)]
    if mode == "one":
        anchors = [d for d in manifest.requires(plane) if manifest.networks_only(d)] + anchors
    work = [p for p in driven if not manifest.networks_only(p)]
    # A missing submodule is fatal (the plane cannot even be rendered, see
    # plane_render). A key still at its shipped placeholder is NOT, unlike `up`:
    # recover brings back a deployment that is already running on those values,
    # and refusing would leave a crashed host down until the key is rotated -
    # which on this repo's own host is deferred by operator decision D1. It is
    # printed first, loudly; `up`, `enable` and `doctor` still refuse it.
    for line in _placeholder_lines(manifest, state, root, work, capture):
        console.line("# WARNING (not a refusal for recover; `up` refuses it):" + line[1:])
    renders = {p: plane_render(manifest, state, root, p, capture) for p in work}
    levels = {p: start_levels(renders[p]) for p in work}
    one_shots = {p: one_shot_services(renders[p]) for p in work}
    # Compose's depends_on conditions, checked here because `up -d --no-deps` skips them.
    # Before the plan is printed and before anything stops - dry run included.
    condition_warnings = check_depends_conditions(
        renders, capture, root,
        {p: ["docker"] + (["--context", state.context_of(p)] if state.context_of(p) else []) for p in work})

    for line in condition_warnings:
        console.line(line)
    console.line(f"# recover: {', '.join(anchors + work)} (dependency order from {MANIFEST_NAME}; containers "
                 "in depends_on order from each plane's render)")
    if mode == "one":
        dependents = [p for p in manifest.order
                      if state.is_enabled(p) and plane in dependency_closure(manifest, [p]) and p != plane]
        if dependents:
            console.line(f"# note: {', '.join(dependents)} require {plane} and are not restarted by this; "
                         "they reconnect on their own or need `stack.py recover` (the whole enabled set)")
    docker = {p: ["docker"] + (["--context", state.context_of(p)] if state.context_of(p) else []) for p in work}

    console.line("== stop, reverse dependency order")
    for p in reversed(work):
        for level in reversed(levels[p]):
            cmd = compose_command(manifest, p, ["stop", "--timeout", str(STOP_TIMEOUT_SECONDS), *level],
                                  context=state.context_of(p),
                                  profiles=effective_profiles(manifest, state, root, p))
            console.line(" ".join(cmd))
            if not dry_run:
                code = runner(cmd, root)
                if code != 0:
                    console.line(f"# recover stopped: stopping {p} exited {code}; nothing was started")
                    return EXIT_REFUSED

    console.line("== start, dependency order, every container gated")
    for a in anchors:
        code = ensure_networks(manifest, state, root, console, runner, capture, a, dry_run)
        if code != 0:
            console.line(f"# recover stopped: {a} exited {code}; stopped and not started: {', '.join(work)}")
            return EXIT_REFUSED
    for index, p in enumerate(work):
        for level in levels[p]:
            cmd = compose_command(manifest, p, ["up", "-d", "--no-deps", *level], context=state.context_of(p),
                                  profiles=effective_profiles(manifest, state, root, p))
            console.line(" ".join(cmd))
            gates = [(key, gate_timeout(renders[p].services[key], timeout)) for key in level]
            for key, limit in gates:
                console.line(_gate_note(renders[p], key, limit))
            if dry_run:
                continue
            code = runner(cmd, root)
            failure = f"`up` exited {code}" if code != 0 else None
            if failure is None:
                for key, limit in gates:
                    name = container_of(renders[p], key)
                    svc = renders[p].services[key]
                    passed, seen = wait_gate(capture, root, docker[p], name, limit,
                                             gate_kind(renders[p], key, one_shots[p]), settle_seconds(svc))
                    console.line(f"  [{'ok' if passed else 'FAIL'}] {p}/{key} ({name}): {seen}")
                    if not passed:
                        failure = f"{key} ({name}) {seen}"
                        break
            if failure:
                console.line(f"refused: recover stopped at {p}: {failure}.")
                later = work[index + 1:]
                if later:
                    console.line(f"# stopped and not started: {', '.join(later)}")
                console.line(f"# fix it (`docker logs {container_of(renders[p], level[0])}`), then re-run "
                             "`stack.py recover` - it stops and restarts everything again in order")
                return EXIT_REFUSED
    if dry_run:
        console.line("# (dry run: the renders above were read; nothing was stopped, started or created)")
    else:
        console.line(f"recovered: {', '.join(work) or 'nothing'} - every container passed its gate")
    _manual_notes(manifest, console, manual, "recover")
    return EXIT_OK


# --------------------------------------------------------------------------
# backup / restore - per-volume archives of a plane's named volumes
# --------------------------------------------------------------------------


def subprocess_pipe(cmd, cwd, stdin_path=None, stdout_path=None) -> CommandResult:
    """Run a command with a FILE on stdin or stdout (binary), capturing stderr.

    The fourth seam: backup streams a tar OUT of a helper container into a file,
    restore streams one IN. Streaming, not a bind mount, so the same verb works
    on a Windows host, a Linux host, a DinD and a remote docker context alike.
    """
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE)
    except OSError as exc:
        return CommandResult(127, "", str(exc))
    finally:
        for handle in (stdin, stdout):
            if hasattr(handle, "close"):
                handle.close()
    out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else ""
    return CommandResult(proc.returncode, out, (proc.stderr or b"").decode("utf-8", "replace"))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


_SIDECAR_NAME = re.compile(r"backup|dump", re.IGNORECASE)


def _names_host(value: str, host: str) -> bool:
    """Does an env value point at `host`: `llm-gateway-db`, `openbrain-db:5432`, `http://surrealdb:8000`, `postgres://u:p@db/x`."""
    return bool(re.search(rf"(^|[/@]){re.escape(host)}(:\d+)?(/|$)", value.strip()))


def dump_sidecars(render: PlaneRender, engines, volume_key: str | None = None) -> list[str]:
    """The plane's dump sidecars for these database engines, derived from the render.

    A sidecar is a service of the SAME plane whose name marks it as a backup or
    dump (`*backup*`, `*dump*`) AND that is tied to an engine by any one of:
      * depends_on the engine service                (llm-gateway-backup, mattermost-db-backup)
      * an environment value naming the engine as a HOST - PGHOST, a URL, a DSN
        (openbrain-db-backup: PGHOST=openbrain-db, and no depends_on at all)
      * a mount of the database volume itself
    The first cut required depends_on alone and so told `backup ob1` that ob1 had
    no dump sidecar, while openbrain-db-backup was running (attempt 1, attack K).
    """
    hosts = set()
    for e in engines:
        hosts.add(e)
        if render.services[e].container:
            hosts.add(render.services[e].container)
    out = set()
    for svc in render.services.values():
        if svc.key in engines or not (_SIDECAR_NAME.search(svc.key) or _SIDECAR_NAME.search(svc.image)):
            continue
        tied = any(e in svc.depends for e in engines)
        tied = tied or any(_names_host(v, h) for v in svc.environment.values() for h in hosts)
        tied = tied or (volume_key is not None
                        and any(k == "volume" and src == volume_key for k, src, _t, _ro in svc.mounts))
        if tied:
            out.add(svc.key)
    return sorted(out)


def plane_volumes(render: PlaneRender) -> list[dict]:
    """The named volumes the plane's RUNNING services mount, with who mounts them.

    From the render, never a hand list. Each entry: key, name, external,
    consumers (service keys), engines (services that mount it read-write and
    whose image is a database), sidecars (services that depend_on an engine and
    are named *backup* - the plane's dump).
    """
    out = []
    for key, spec in sorted(render.volumes.items()):
        consumers, engines = [], []
        for svc in render.services.values():
            for kind, source, _target, read_only in svc.mounts:
                if kind == "volume" and source == key:
                    consumers.append(svc.key)
                    repo = svc.image.split("@")[0].rsplit(":", 1)[0].split("/")[-1]
                    if not read_only and repo in DB_ENGINES:
                        engines.append(svc.key)
        if not consumers:
            continue
        sidecars = dump_sidecars(render, engines, key)
        out.append({"key": key, "name": spec["name"], "external": spec["external"],
                    "consumers": sorted(set(consumers)), "engines": sorted(set(engines)), "sidecars": sidecars})
    return out


def running_users(capture, root, docker, volume: str):
    """Names of the RUNNING containers (any project, compose or not) that mount the volume.

    Asked of the daemon, not the render: a container from another project, or a
    one-off `docker run`, holding the volume counts just the same. None when the
    daemon could not be asked - a refusal, never an assumed "nobody".
    """
    found = capture(docker + ["ps", "--filter", f"volume={volume}", "--format", "{{.Names}}"], root)
    if found.code != 0:
        return None
    return sorted({line.strip() for line in (found.stdout or "").splitlines() if line.strip()})


HELPER_LABEL = "ai-stack.stack-py.helper=1"
LIVE_COPY_WARNING = ("taken while these containers ran; files an application holds open (a SQLite "
                     "database such as webui.db) may be mid-write in this archive - stop the plane "
                     "first (`stack.py down <plane>`) for a quiescent copy")


def helper_name(verb: str) -> str:
    return f"stack-py-{verb}-{os.getpid()}-{int(time.time() * 1000) % 100000000}"


def remove_helper(console, capture, root, docker, name: str) -> None:
    """After a failed helper run: the CLI can exit while the container lives on (seen in attempt 1:
    a backup onto a full disk left its helper holding the volume, and the next restore was refused
    because of it). Remove it by name and say whether it is gone."""
    capture(docker + ["rm", "-f", name], root)
    left = capture(docker + ["ps", "-a", "-q", "--filter", f"name=^{name}$"], root)
    if left.code == 0 and not (left.stdout or "").strip():
        console.line(f"  [ok]   helper {name} removed")
    else:
        console.line(f"  [FAIL] helper {name} may still exist - `docker rm -f {name}`")


def cmd_backup(manifest, state, root, console, capture, pipe, plane: str, dest=None) -> int:
    """Archive each named volume of one plane into backups/<plane>/manual-<UTC stamp>/.

    One `<volume>.tar.gz` per volume, written by a throwaway helper container
    (HELPER_IMAGE, `--network none`, the volume mounted READ-ONLY) whose tar
    stream is piped into the file; then `manifest.json` (volume, compose key,
    archive, bytes, sha256) and a `SHA256SUMS` that `sha256sum -c` reads.

    A database volume whose engine is RUNNING is not tarred: it is named, with
    the plane's dump sidecar, as "use the plane's dump for a consistent copy".
    With the engine stopped its data directory is at rest and is archived.
    Host bind mounts are not archived by this verb and the output says how many
    the plane has.
    """
    manifest.plane(plane)
    if manifest.networks_only(plane):
        raise Refusal(f"refused: {plane} declares networks and no service, so it has no volume to back up")
    render = plane_render(manifest, state, root, plane, capture)
    volumes = plane_volumes(render)
    binds = sorted({(s.key, src) for s in render.services.values()
                    for kind, src, _t, ro in s.mounts if kind == "bind" and not ro})
    if not volumes:
        raise Refusal(f"refused: {plane} mounts no named volume under the profiles it runs with "
                      f"({len(binds)} writable host bind mount(s), which this verb does not archive)")
    docker = ["docker"] + (["--context", state.context_of(plane)] if state.context_of(plane) else [])
    base = Path(dest).resolve() if dest else root / "backups"
    stamp = utc_stamp()
    out = base / plane / f"manual-{stamp}"
    suffix = 1
    while out.exists():
        suffix += 1
        out = base / plane / f"manual-{stamp}-{suffix}"
    out.mkdir(parents=True)
    console.line(f"# backup {plane}: {len(volumes)} named volume(s) from the render of "
                 f"{manifest.plane(plane)['compose']} -> {out}")

    archived, skipped, failed = [], [], 0
    for vol in volumes:
        name = vol["name"]
        if vol["external"]:
            skipped.append({"volume": name, "why": "external volume - not owned by this plane"})
            console.line(f"  [skip] {name}: external volume - not owned by this plane")
            continue
        if capture(docker + ["volume", "inspect", name], root).code != 0:
            skipped.append({"volume": name, "why": "absent on this daemon"})
            console.line(f"  [skip] {name}: absent on this daemon (the plane has not been up here)")
            continue
        note = ""
        if vol["engines"]:
            live = [container_of(render, e) for e in vol["engines"]
                    if (container_state(capture, root, docker, container_of(render, e)) or {}).get("Status")
                    in ("running", "restarting", "paused")]
            if live:
                dump = (f" ({', '.join(vol['sidecars'])} writes it)" if vol["sidecars"]
                        else f" (this plane has no dump sidecar: stop {', '.join(live)} and re-run for a cold copy)")
                why = (f"a live database data directory ({', '.join(live)} is running) - use the plane's dump "
                       f"for a consistent copy{dump}")
                skipped.append({"volume": name, "why": why})
                console.line(f"  [skip] {name}: {why}")
                continue
            note = f"cold copy: {', '.join(container_of(render, e) for e in vol['engines'])} stopped"
        users = running_users(capture, root, docker, name) or []
        live = []
        if not note and users:
            note = f"live copy: {', '.join(users)} running"
            live = users
        archive = f"{name}.tar.gz"
        partial = out / (archive + ".partial")
        helper = helper_name("backup")
        cmd = docker + ["run", "--rm", "--name", helper, "--label", HELPER_LABEL, "--network", "none",
                        "-v", f"{name}:/volume:ro", HELPER_IMAGE, "tar", "-czf", "-", "-C", "/volume", "."]
        console.line(" ".join(cmd) + f" > {archive}")
        result = pipe(cmd, root, stdout_path=partial)
        if result.code != 0:
            failed += 1
            partial.unlink(missing_ok=True)
            remove_helper(console, capture, root, docker, helper)
            why = (result.stderr or "no output").strip().splitlines()[-1:] or ["no output"]
            console.line(f"  [FAIL] {name}: the helper exited {result.code} ({why[0]}); no archive kept")
            continue
        partial.replace(out / archive)
        size = (out / archive).stat().st_size
        digest = sha256_of(out / archive)
        entry = {"volume": name, "key": vol["key"], "archive": archive, "bytes": size, "sha256": digest}
        if live:
            # Recorded, not only printed: after the fact an archive must not look like a
            # clean one when it was read under a running application (a SQLite webui.db
            # mid-write restores as whatever the pages on disk said at that instant).
            entry["live_copy"] = {"running": live, "warning": LIVE_COPY_WARNING}
        archived.append(entry)
        console.line(f"  [ok]   {name}: {archive}, {size} bytes, sha256 {digest}" + (f" ({note})" if note else ""))

    if binds:
        console.line(f"# not archived: {len(binds)} writable host bind mount(s) in this plane "
                     "(back those paths up with the host's own tools)")
    if not archived:
        shutil.rmtree(out, ignore_errors=True)
        console.line(f"refused: nothing was archived for {plane}; {out} was removed")
        return EXIT_REFUSED
    record = {"format": BACKUP_FORMAT, "plane": plane, "project": render.project,
              "compose": manifest.plane(plane)["compose"],
              "profiles": effective_profiles(manifest, state, root, plane),
              "created": stamp, "helper_image": HELPER_IMAGE, "volumes": archived, "skipped": skipped}
    with (out / BACKUP_MANIFEST).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    with (out / "SHA256SUMS").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{a['sha256']}  {a['archive']}\n" for a in archived))
    console.line(f"wrote {out / BACKUP_MANIFEST} ({len(archived)} archived, {len(skipped)} skipped"
                 + (f", {failed} FAILED" if failed else "") + ")")
    return EXIT_REFUSED if failed else EXIT_OK


# Runs in the helper with the volume at /volume and the archive on stdin. It
# extracts into a staging directory FIRST, so a torn or unreadable archive leaves
# the volume's previous contents exactly as they were; only a complete
# extraction replaces them. The volume root takes the archive root's mode/owner.
_RESTORE_SCRIPT = (
    'set -e; S=/volume/.stack-restore-staging; rm -rf "$S"; mkdir "$S"; '
    'if ! tar -xzf - -C "$S"; then rm -rf "$S"; '
    'echo "extraction failed; the previous contents are untouched" >&2; exit 1; fi; '
    'chmod "$(stat -c %a "$S")" /volume; chown "$(stat -c %u:%g "$S")" /volume; '
    'find /volume -mindepth 1 -maxdepth 1 ! -name .stack-restore-staging -exec rm -rf {} \\; ; '
    'find "$S" -mindepth 1 -maxdepth 1 -exec mv {} /volume/ \\; ; rmdir "$S"'
)


def _load_backup(source: str, volume: str | None):
    """(directory, parsed manifest.json, the volume a .tar.gz path implies or None)."""
    path = Path(source).resolve()
    implied = None
    if path.is_dir():
        directory = path
    elif path.name == BACKUP_MANIFEST:
        directory = path.parent
    elif path.name.endswith(".tar.gz") and path.is_file():
        directory, implied = path.parent, path.name
    else:
        raise Refusal(f"refused: --from {source} is neither a backup directory, its {BACKUP_MANIFEST}, "
                      "nor one of its .tar.gz archives")
    record_path = directory / BACKUP_MANIFEST
    if not record_path.is_file():
        raise Refusal(f"refused: {record_path} does not exist - restore reads the manifest `backup` wrote, "
                      "and will not restore an archive it cannot verify")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Refusal(f"refused: {record_path} is not valid JSON ({exc})") from None
    if record.get("format") != BACKUP_FORMAT:
        raise Refusal(f"refused: {record_path} is format {record.get('format')!r}; this driver reads "
                      f"{BACKUP_FORMAT}")
    if implied:
        match = [e for e in record.get("volumes") or [] if e.get("archive") == implied]
        if not match:
            raise Refusal(f"refused: {implied} is not listed in {record_path}")
        implied = match[0]["volume"]
        if volume and volume not in (implied, match[0].get("key")):
            raise Refusal(f"refused: --from names the archive of {implied} but --volume says {volume}")
    return directory, record, implied


def cmd_restore(manifest, state, root, console, capture, pipe, plane: str, source: str,
                volume: str | None = None) -> int:
    """Restore one plane's volumes (or the one --volume names) from a `backup` directory.

    Every check runs BEFORE anything is changed, in this order, and any failure
    is a refusal that changed nothing:
      1. the manifest is `backup`'s and names THIS plane;
      2. every selected archive's sha256 matches the manifest (a tampered or
         truncated archive is refused by name);
      3. every selected volume is one the plane's render declares;
      4. no RUNNING container mounts any selected volume (asked of the daemon).
    Only the selected volumes are touched: a missing one is created with the
    labels compose itself would have given it, then its contents are replaced
    by the archive's (staged, so a failed extraction leaves them as they were).
    """
    manifest.plane(plane)
    directory, record, implied = _load_backup(source, volume)
    if record.get("plane") != plane:
        raise Refusal(f"refused: {directory / BACKUP_MANIFEST} is a backup of plane "
                      f"'{record.get('plane')}', not '{plane}'")
    entries = list(record.get("volumes") or [])
    wanted = volume or implied
    if wanted:
        entries = [e for e in entries if wanted in (e.get("volume"), e.get("key"))]
        if not entries:
            listed = ", ".join(e.get("volume", "?") for e in record.get("volumes") or []) or "none"
            raise Refusal(f"refused: {wanted} is not in {directory / BACKUP_MANIFEST} (it holds: {listed})")
    if not entries:
        raise Refusal(f"refused: {directory / BACKUP_MANIFEST} lists no archived volume")

    bad = []
    for entry in entries:
        archive = directory / entry["archive"]
        if not archive.is_file():
            bad.append(f"  {entry['archive']}: missing")
            continue
        actual = sha256_of(archive)
        if actual != entry.get("sha256"):
            bad.append(f"  {entry['archive']}: sha256 {actual}, the manifest says {entry.get('sha256')}")
    if bad:
        raise Refusal("refused: archive verification failed - nothing was changed:\n" + "\n".join(bad))

    render = plane_render(manifest, state, root, plane, capture)
    declared = {v["name"]: v for v in plane_volumes(render)}
    unknown = [e["volume"] for e in entries if e["volume"] not in declared]
    if unknown:
        raise Refusal(f"refused: {', '.join(unknown)} is not a named volume {plane} mounts under the profiles "
                      f"it runs with ({', '.join(declared) or 'none'}) - nothing was changed")

    docker = ["docker"] + (["--context", state.context_of(plane)] if state.context_of(plane) else [])
    busy = []
    for entry in entries:
        users = running_users(capture, root, docker, entry["volume"])
        if users is None:
            raise Refusal(f"refused: could not ask the daemon which containers use {entry['volume']} "
                          "- nothing was changed")
        if users:
            busy.append(f"  {entry['volume']}: in use by {', '.join(users)}")
    if busy:
        raise Refusal("refused: restore will not write a volume a running container holds - nothing was "
                      "changed:\n" + "\n".join(busy)
                      + f"\nStop them first (`python3 scripts/stack/stack.py down {plane}`), then re-run.")

    console.line(f"# restore {plane}: {len(entries)} volume(s) from {directory} (sha256 verified)")
    for entry in entries:
        name = entry["volume"]
        if capture(docker + ["volume", "inspect", name], root).code != 0:
            create = docker + ["volume", "create",
                               "--label", f"com.docker.compose.project={render.project}",
                               "--label", f"com.docker.compose.volume={declared[name]['key']}", name]
            console.line(" ".join(create))
            made = capture(create, root)
            if made.code != 0:
                console.line(f"refused: creating {name} exited {made.code}: {(made.stderr or '').strip()}")
                return EXIT_REFUSED
        helper = helper_name("restore")
        cmd = docker + ["run", "--rm", "-i", "--name", helper, "--label", HELPER_LABEL, "--network", "none",
                        "-v", f"{name}:/volume", HELPER_IMAGE, "sh", "-c", _RESTORE_SCRIPT]
        console.line(" ".join(cmd[:-1]) + " '<staged extract>'" + f" < {entry['archive']}")
        result = pipe(cmd, root, stdin_path=directory / entry["archive"])
        if result.code != 0:
            why = (result.stderr or "no output").strip().splitlines()[-1:] or ["no output"]
            remove_helper(console, capture, root, docker, helper)
            console.line(f"refused: restoring {name} exited {result.code} ({why[0]})")
            return EXIT_REFUSED
        console.line(f"  [ok]   {name} <- {entry['archive']} ({entry.get('bytes')} bytes)")
    console.line(f"restored. Start the plane: python3 scripts/stack/stack.py up {plane}")
    return EXIT_OK


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

_QUEUE_BOARD = ("import urllib.request;print(urllib.request.urlopen("
                "'http://localhost:8080/observe/queue',timeout=5).read().decode())")


def _psql(capture, root, sql: str) -> list[list[str]] | None:
    """Rows from the LiteLLM ledger, or None when llm-gateway-db did not answer.

    `-c` with an argv, not stdin: a Python argument list reaches docker intact
    on every OS. (stack-stats.ps1 pipes stdin because PowerShell 5.1 mangles the
    double quotes PostgreSQL needs around LiteLLM's CamelCase identifiers.)
    """
    result = capture(["docker", "exec", "llm-gateway-db", "psql", "-U", "litellm", "-d", "litellm",
                      "-tA", "-F", "|", "-c", sql], root)
    if result.code != 0:
        return None
    return [line.split("|") for line in (result.stdout or "").splitlines() if line.strip()]


def _int(text) -> int:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _stats_rows(capture, root, ids):
    """(rows, ids that vanished). One `docker stats` for all; if that fails, one per container,
    so a container removed mid-run costs its own row, not the table. rows is None only when
    every container failed."""
    def parse(text):
        out = []
        for line in (text or "").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
    result = capture(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids], root)
    if result.code == 0:
        return parse(result.stdout), []
    rows, gone = [], []
    for cid in ids:
        one = capture(["docker", "stats", "--no-stream", "--format", "{{json .}}", cid], root)
        if one.code == 0:
            rows += parse(one.stdout)
        else:
            gone.append(cid)
    if not rows:
        return None, [(result.stderr or "").strip()[:200]]
    return rows, gone


def cmd_stats(manifest, state, root: Path, console: Console, runner, capture, hours: int = 1,
              bucket: int = 10) -> int:
    """Container CPU/memory/net for the enabled planes, then the inference ledger.

    On Windows this still hands off to scripts/stack/stack-stats.ps1, which the
    operator's host has always run. Everywhere else it is this report: the same
    two inference sources that script reads (llm-queue's /observe/queue board and
    the LiteLLM_SpendLogs ledger in llm-gateway-db), preceded by `docker stats
    --no-stream` for every running container of every enabled plane. When the
    inference plane is not enabled it says so - a zero is not a measurement.
    """
    if WINDOWS:
        script = root / "scripts" / "stack" / "stack-stats.ps1"
        if not script.is_file():
            raise Refusal(f"refused: {rel(root, script)} is missing, so there is nothing to report")
        return runner(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], root)

    enabled = [p for p in manifest.order if state.is_enabled(p) and not manifest.networks_only(p)]
    if not enabled:
        console.line("# nothing enabled (`stack.py enable <plane|product>` or `stack.py init`)")
        return EXIT_OK
    ids, notes = [], []
    for p in enabled:
        if missing_submodule(manifest, root, p):
            notes.append(f"{p}: compose file missing (submodule not initialised)")
            continue
        cmd = compose_command(manifest, p, ["ps", "-q"], context=state.context_of(p),
                              profiles=effective_profiles(manifest, state, root, p))
        found = capture(cmd, root)
        if found.code != 0:
            notes.append(f"{p}: `{' '.join(cmd)}` exited {found.code}")
            continue
        ids += [line.strip() for line in (found.stdout or "").splitlines() if line.strip()]
    console.line(f"== containers: docker stats --no-stream (enabled planes: {', '.join(enabled)})")
    for note in notes:
        console.line(f"  [warn] {note}")
    failed = 0
    if not ids:
        console.line("  no running container in the enabled planes")
    else:
        rows, gone = _stats_rows(capture, root, ids)
        if rows is None:
            console.line(f"  [FAIL] docker stats failed for every container: {gone[0] if gone else 'no output'}")
            failed += 1
        else:
            width = max([len(r.get("Name", "")) for r in rows] + [4])
            console.line(f"  {'NAME'.ljust(width)}  {'CPU %':>7}  {'MEM USAGE / LIMIT':<22}  {'MEM %':>6}  NET I/O")
            for r in sorted(rows, key=lambda r: r.get("Name", "")):
                console.line(f"  {r.get('Name', '?').ljust(width)}  {r.get('CPUPerc', '?'):>7}  "
                             f"{r.get('MemUsage', '?'):<22}  {r.get('MemPerc', '?'):>6}  {r.get('NetIO', '?')}")
            for cid in gone:
                console.line(f"  {cid[:12].ljust(width)}  (gone: the container disappeared between "
                             "`compose ps` and `docker stats`)")

    console.line("")
    if not state.is_enabled("inference"):
        console.line("== inference: NOT ENABLED on this machine - no llm-queue board and no LiteLLM spend "
                     "ledger to read (`python3 scripts/stack/stack.py enable inference` turns it on)")
        return EXIT_REFUSED if failed else EXIT_OK

    console.line("== llm-queue live board")
    board = capture(["docker", "exec", "llm-queue", "python", "-c", _QUEUE_BOARD], root)
    try:
        queue = json.loads(board.stdout) if board.code == 0 else None
    except ValueError:
        queue = None
    if not isinstance(queue, dict):
        console.line("  (llm-queue unreachable - is the `local` profile on in inference/.env?)")
    else:
        for model, m in sorted((queue.get("models") or {}).items()):
            m = m or {}
            console.line(f"  {model}: running={len(m.get('running') or [])} waiting={len(m.get('waiting') or [])} "
                         f"permits_free={m.get('permits_free')} avg_T={m.get('avg_T_s')}s")
            for r in m.get("running") or []:
                console.line(f"    RUNNING  {r.get('id')}  key={r.get('key')}  model={r.get('model')}  "
                             f"{_int(r.get('elapsed_s'))}s elapsed")
            waiting = m.get("waiting") or []
            for n, w in enumerate(waiting[:5]):
                console.line(f"    {'NEXT    ' if n == 0 else 'waiting '} {w.get('id')}  key={w.get('key')}")
            if len(waiting) > 5:
                console.line(f"    ... +{len(waiting) - 5} more")
        console.line(f"  connections held: {queue.get('held_total')}/{queue.get('max_total_connections')}")

    console.line("")
    console.line(f"== demand: last {hours} h in {bucket}-min buckets (requests | tokens | failures)")
    rows = _psql(capture, root, (
        "select to_char(date_trunc('hour', \"startTime\") + (floor(extract(minute from \"startTime\")"
        f"/{bucket})*{bucket}) * interval '1 minute', 'HH24:MI'), count(*), coalesce(sum(total_tokens),0), "
        "count(*) filter (where status='failure') from \"LiteLLM_SpendLogs\" "
        f"where \"startTime\" > now() - interval '{hours} hours' group by 1 order by 1"))
    if rows is None:
        console.line("  (llm-gateway-db unreachable - the spend ledger could not be read)")
        return EXIT_REFUSED
    if not rows:
        console.line("  (no requests in the window)")
    for f in rows:
        if len(f) >= 4:
            console.line(f"  {f[0]}  {_int(f[1]):>5} req  {_int(f[2]):>10,} tok  {_int(f[3]):>3} fail")

    console.line("")
    console.line(f"== by caller: last {hours} h")
    rows = _psql(capture, root, (
        "select coalesce(t.key_alias, s.api_key), count(*), coalesce(sum(s.total_tokens),0), "
        "count(*) filter (where s.status='failure') from \"LiteLLM_SpendLogs\" s "
        "left join \"LiteLLM_VerificationToken\" t on s.api_key = t.token "
        f"where s.\"startTime\" > now() - interval '{hours} hours' group by 1 order by 2 desc limit 12")) or []
    if not rows:
        console.line("  (idle)")
    for f in rows:
        if len(f) >= 4:
            console.line(f"  {f[0][:22]:<22} {_int(f[1]):>6} req  {_int(f[2]):>12,} tok  {_int(f[3]):>4} fail")

    console.line("")
    console.line("== global totals (the whole ledger)")
    rows = _psql(capture, root, (
        "select count(*), coalesce(sum(total_tokens),0), count(*) filter (where status='failure'), "
        "min(\"startTime\")::date from \"LiteLLM_SpendLogs\"")) or []
    for f in rows[:1]:
        if len(f) >= 4:
            console.line(f"  {_int(f[0]):,} requests | {_int(f[1]):,} tokens | {_int(f[2]):,} failures | "
                         f"since {f[3] or '-'}")
    return EXIT_REFUSED if failed else EXIT_OK


# --------------------------------------------------------------------------
# inventory - scripts/lib/stack-services.json, GENERATED
# --------------------------------------------------------------------------
#
# WHAT IS DERIVED AND WHAT IS CURATED, because the split is the whole design:
#
#   derived from stack.manifest.toml   the `projects` block - which compose file,
#                                      which env_file (null everywhere since
#                                      sl-env-split), the runnable command
#                                      line - and which planes belong here at
#                                      all: a `manual` plane (the portal) does
#                                      not, because the watchdog must not
#                                      auto-repair a plane whose whole point is
#                                      that a human starts it.
#   derived from the compose RENDER    which containers exist, their compose
#                                      SERVICE key, their profiles, and whether a
#                                      project owns any services at all.
#   curated, in the sidecar            the plane GROUPING and the row order, and
#                                      the hand-owned judgement: critical,
#                                      host_health, stale_pool_guard, note.
#
# The render is also the VERIFIER. A container in a render and not in the sidecar
# is drift (`--write` refuses - only a human can say which group it joins); a
# sidecar row whose project disagrees with the render is drift; a manifest
# `ports` entry no service publishes is drift, and so is a published port the
# manifest does not declare. That is what the manifest's [planes.*.ports] tables
# are FOR - they had no consumer at all until this verb.
#
# A project whose compose file is not on disk (OB1 is a submodule; CI does not
# check it out) is carried from the sidecar and reported UNVERIFIED by name -
# not silently. A check that cannot run must never look like one that passed.

INVENTORY_REL = Path("scripts") / "lib" / "stack-services.json"
CURATED_REL = Path("scripts") / "lib" / "stack-services.curated.json"

# Row key order in the generated file: derived fact first, then the curated
# judgement, then the prose, so a diff reads top-down.
ROW_KEY_ORDER = ["container", "service", "profile", "profiles", "project",
                 "critical", "host_health", "stale_pool_guard", "note"]


class Render(NamedTuple):
    """One compose project as the CLI renders it."""

    services: dict   # service key -> {container, profiles, ports}
    profiles: list   # every profile the compose file declares
    available: bool  # False when the compose file is not on disk
    why: str         # why not, when unavailable
    pinned: bool = False   # the compose file comes from a pinned submodule
    exclusive: bool = False  # its profiles cannot all be rendered together


def submodule_paths(root: Path) -> list[str]:
    """Repo-relative paths declared in .gitmodules, forward slashes, no trailing /.

    Read rather than assumed: `git submodule` is not available to a driver that
    has to run with nothing but Python and Docker, and .gitmodules is committed.
    """
    path = root / ".gitmodules"
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = raw.partition("=")
        if sep and key.strip() == "path":
            out.append(value.strip().replace("\\", "/").rstrip("/"))
    return out


def is_pinned_submodule(root: Path, compose_rel: str) -> bool:
    """Does this plane's compose file come from a submodule pinned to a commit?

    It decides whether a manifest-declared profile that the RENDER does not carry
    is drift or simply not-yet-pinned. For a plane whose compose file sits in this
    repo the two files land in the same commit and MUST agree - a mismatch is an
    error. OB1 is a submodule pinned by gitlink, so the manifest can legitimately
    describe the branch the gitlink will move to, and the difference resolves when
    someone bumps it. Calling that "drift" would be false, and suppressing it
    everywhere would blind the check for the seven planes where it is real.
    """
    first = compose_rel.replace("\\", "/").split("/")[0]
    return first in submodule_paths(root)


def render_env_path(manifest: Manifest, root: Path, plane: str) -> Path:
    """The env file a RENDER uses: the .example when there is one.

    Deterministic on purpose. Each plane's `.env.example` is kept COMPLETE for
    that plane (CLEANUP-PLAN v3 A.4; per-plane since sl-env-split), so compose
    interpolation resolves with no real secret - which is how the same inventory
    comes out of a laptop, this host and a CI runner. Since the plane env is
    `<project dir>/.env`, this returns `<project dir>/.env.example`.

    CONTRAST with HealthSweep.tailscale_deployed(), which renders the frontend
    plane with the REAL `.env`. That one asks what THIS HOST DEPLOYS and so must
    see this host's COMPOSE_PROFILES; this one asks what the compose file
    DECLARES, and an answer that changed with the machine would make the
    generated inventory unreproducible. The two uses look alike and are not.
    """
    real = manifest.env_path(root, plane)
    example = real.with_name(real.name + ".example")
    return example if example.is_file() else real


def render_project(manifest: Manifest, root: Path, plane: str, capture) -> Render:
    compose_rel = manifest.plane(plane)["compose"]
    compose_path = root / Path(compose_rel)
    if not compose_path.is_file():
        return Render(
            {}, [], False,
            f"{compose_rel} is not on disk (OB1 is a submodule - `git submodule update --init`)",
            is_pinned_submodule(root, compose_rel),
        )

    # A plane whose GITIGNORED env file is absent cannot be rendered here, and
    # that is not drift. agent-org's compose carries service-level `env_file:`
    # entries, which `config` STATS - so on any machine without
    # agent-org/docker/.env the render exits 1, the generator used to refuse, and
    # the pre-commit hook then printed "INVENTORY DRIFT - regenerate with
    # --write". Wrong twice over: nothing had drifted, and `--write` refuses the
    # same way, so the remedy it named could not work.
    #
    # Degrade the way the coverage guard and the missing-python path already do -
    # name the project and the file, skip its rows, print the gap, exit 0. A
    # compose file that EXISTS and fails to render for any other reason is still
    # a hard refusal: this exemption is one named, checkable condition.
    # The REAL env path, not render_env_path's `.example`. The two differ on
    # purpose (F28) and it is the real one that matters here: a service-level
    # `env_file:` inside the compose names `.env` literally, and compose stats it
    # whatever `--env-file` the CLI was given.
    env_path = manifest.env_path(root, plane)
    if not env_path.is_file():
        return Render(
            {}, [], False,
            f"{rel(root, env_path)} is absent - gitignored, so this is expected off the deploy "
            f"host, and {compose_rel} cannot be rendered without it",
            is_pinned_submodule(root, compose_rel),
        )

    base = ["docker", "compose", "-f", compose_rel,
            "--env-file", rel(root, render_env_path(manifest, root, plane))]
    listed = capture(base + ["config", "--profiles"], root)
    if listed.code != 0:
        raise Refusal(
            f"refused: `docker compose -f {compose_rel} config --profiles` exited {listed.code}\n"
            + (listed.stderr.strip() or listed.stdout.strip())
        )
    profiles = sorted({line.strip() for line in listed.stdout.splitlines() if line.strip()})

    def render_with(active):
        cmd = list(base)
        for profile in active:
            cmd += ["--profile", profile]
        return capture(cmd + ["config", "--format", "json"], root)

    def collect(payload, into):
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise Refusal(f"refused: the render of {compose_rel} is not JSON ({exc})") from None
        for key, spec in (data.get("services") or {}).items():
            spec = spec or {}
            into[key] = {
                "container": spec.get("container_name") or key,
                "profiles": list(spec.get("profiles") or []),
                "ports": sorted({str(port.get("published"))
                                 for port in (spec.get("ports") or []) if port.get("published")}),
            }

    # Every declared profile is switched ON for the render: the inventory must
    # list the profile-gated containers too (the watchdog repairs them), and a
    # bare `config` hides them. That is exactly why the old pre-commit verifier
    # never saw openbrain-idea-refinery - it rendered without profiles.
    services: dict = {}
    exclusive = False
    rendered = render_with(profiles)
    if rendered.code == 0:
        collect(rendered.stdout, services)
    else:
        # SOME PROFILES CANNOT COEXIST. sl-frontend-solo gave the frontend plane
        # `stock` and `gpu`, two definitions of the SAME container_name for two
        # different deployments, so compose refuses to render them together:
        #   services.openwebui: container name "openwebui" is already in use
        # That is not an error in the compose file and not drift - it is what
        # mutually exclusive means. Render the base and each profile on its own
        # and take the union, so every container is still seen exactly once.
        # Anything else that makes a render fail still raises: the fallback only
        # holds if EVERY individual render succeeds.
        # Each profile is rendered with its `requires` CLOSURE, not alone: the
        # frontend's `tailscale` names the `gpu` definition of openwebui in its
        # network_mode, so `--profile tailscale` by itself is not a deployment
        # and compose says so ("depends on undefined service openwebui"). The
        # manifest already declares that edge; this is the driver using it.
        attempts = [render_with([])] + [
            render_with(manifest.profile_order(plane, manifest.profile_closure(plane, {profile})))
            for profile in profiles
        ]
        if any(a.code != 0 for a in attempts):
            raise Refusal(
                f"refused: `docker compose -f {compose_rel} config --format json` exited "
                f"{rendered.code}\n" + (rendered.stderr.strip() or rendered.stdout.strip())
            )
        exclusive = True
        for attempt in attempts:
            collect(attempt.stdout, services)
    return Render(services, profiles, True, "", is_pinned_submodule(root, compose_rel), exclusive)


class Inventory:
    """Builds scripts/lib/stack-services.json and says where it disagrees."""

    def __init__(self, manifest: Manifest, root: Path, capture):
        self.manifest = manifest
        self.root = root
        self.capture = capture
        self.curated = self._load_curated()
        self._plane_of: dict = {}
        self._ambiguous: dict = {}
        # A THIRD kind of "not drift and not silence": one container, two
        # definitions, one per deployment. Kept apart from the gitlink bucket so
        # the advice that follows that one is not attached to this one.
        self.mutually_exclusive: list[str] = []
        self.drift: list[str] = []
        self.skipped: list[str] = []
        # Not drift and not silence: a third bucket, printed by name.
        self.declared_not_rendered: list[str] = []

    def _load_curated(self) -> dict:
        path = self.root / CURATED_REL
        if not path.is_file():
            raise Refusal(
                f"refused: no curated sidecar at {rel(self.root, path)}. It holds the plane grouping and "
                "the hand-owned fields (critical / host_health / notes); the generator cannot invent them."
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Refusal(f"refused: {rel(self.root, path)} is not valid JSON ({exc})") from None

    # -- generation --------------------------------------------------------

    def build(self) -> dict:
        projects = self.curated.get("projects") or {}
        self._check_plane_coverage(projects)
        self._plane_of = {name: spec["plane"] for name, spec in projects.items()}

        renders = {}
        for project, spec in projects.items():
            renders[project] = render_project(self.manifest, self.root, spec["plane"], self.capture)
            if not renders[project].available:
                self.skipped.append(f"{project}: {renders[project].why}")

        self._check_profile_accounting()
        return {
            "_comment": list(self.curated.get("_comment") or []),
            "projects": self._projects(projects, renders),
            "planes": self._planes(renders),
        }

    def _check_plane_coverage(self, projects: dict) -> None:
        """Every non-manual plane is claimed by exactly one project.

        ADDING A PLANE must force a decision here rather than letting it vanish
        from the inventory the watchdog routes repairs through.
        """
        claimed = {}
        for project, spec in projects.items():
            plane = spec.get("plane")
            if not plane:
                raise Refusal(f"refused: project '{project}' in {CURATED_REL.as_posix()} names no plane")
            self.manifest.plane(plane)
            if plane in claimed:
                raise Refusal(
                    f"refused: plane '{plane}' is claimed by two projects in {CURATED_REL.as_posix()} "
                    f"({claimed[plane]} and {project})"
                )
            claimed[plane] = project
        for plane in self.manifest.order:
            if self.manifest.manual(plane):
                if plane in claimed:
                    raise Refusal(
                        f"refused: plane '{plane}' is `manual` in {MANIFEST_NAME} "
                        f"({self.manifest.manual(plane)}) but claimed by project '{claimed[plane]}' in "
                        f"{CURATED_REL.as_posix()}. The inventory drives AUTOMATED repair; a plane a human "
                        "starts by hand must not be in it."
                    )
                continue
            if plane not in claimed:
                raise Refusal(
                    f"refused: plane '{plane}' has no project in {CURATED_REL.as_posix()}. Add a "
                    f'"<compose project name>": {{ "plane": "{plane}" }} entry - the project name is not '
                    "always the plane name (ob1 -> open-brain, anchor -> ai-stack)."
                )

    def _projects(self, curated: dict, renders: dict) -> dict:
        out = {}
        for project, spec in curated.items():
            plane = spec["plane"]
            render = renders[project]
            entry = {}
            if render.available and not render.services:
                # A project that owns NO services can never start anything.
                # file=null is what marks it unstartable for the watchdog, which
                # then skips the row instead of issuing `up -d` into the void -
                # the Part K anchor defect, in one field.
                entry["compose"] = "docker compose"
                entry["file"] = None
                entry["env_file"] = None
            else:
                compose_rel = self.manifest.plane(plane)["compose"]
                env_file = self.manifest.env_file(plane)
                entry["compose"] = ("docker compose -f " + compose_rel
                                    + (f" --env-file {env_file}" if env_file else ""))
                entry["file"] = compose_rel
                entry["env_file"] = env_file
            if spec.get("note"):
                entry["note"] = spec["note"]
            if spec.get("profiles"):
                entry["profiles"] = list(spec["profiles"])
            out[project] = entry
            if render.available:
                self._check_ports(plane, render)
                self._check_profiles(plane, render)
        return out

    def _planes(self, renders: dict) -> dict:
        # container -> (project, service key, profiles), from every render.
        # A container produced by MORE THAN ONE service key is ambiguous: the
        # frontend's `openwebui` is service `openwebui` under `gpu` and
        # `openwebui-stock` under `stock`, and which key is right depends on the
        # deployment. Neither `service` nor `profile` can be derived for such a
        # row, so neither is emitted - which is the same conclusion
        # sl-frontend-solo reached by hand and wrote into that row's note.
        seen = {}
        produced_by = {}
        for project, render in renders.items():
            for service, spec in render.services.items():
                produced_by.setdefault(spec["container"], []).append(service)
                seen[spec["container"]] = (project, service, spec["profiles"])
        self._ambiguous = {c: sorted(keys) for c, keys in produced_by.items() if len(keys) > 1}
        for container, keys in sorted(self._ambiguous.items()):
            self.mutually_exclusive.append(
                f"{container}: produced by {len(keys)} mutually exclusive services "
                f"({', '.join(keys)}), so `service` and `profile` cannot be derived and are "
                "deliberately absent from its row"
            )

        out = {}
        listed = set()
        for group, rows in (self.curated.get("planes") or {}).items():
            out[group] = [self._row(group, row, renders, seen) for row in rows]
            listed.update(row["container"] for row in rows)

        for container in sorted(seen):
            if container not in listed:
                self.drift.append(
                    f"MISSING from {CURATED_REL.as_posix()}: {container} (project {seen[container][0]}) - "
                    "add it to a plane group with its `critical` flag; the generator cannot choose the "
                    "group for you"
                )
        return out

    def _row(self, group: str, curated_row: dict, renders: dict, seen: dict) -> dict:
        """One generated row.

        `project` is OPTIONAL in the sidecar. Whenever the container turns up in
        a render, the render supplies it - which is what makes
        SERVICE-LIFECYCLE.md step 8 followable as written: add the service to its
        plane's compose file, add a sidecar row with its group and its `critical`
        flag, run `--write`. A tester followed that literally and the generator
        refused, because the row had no `project` and the old code compared the
        render's answer against `None`. The fix is to fill it in, not to add a
        field to the instructions.

        Where the sidecar DOES record a project it is still audited against the
        render, and it remains REQUIRED for a container whose project may not be
        renderable at all (the OB1 submodule is absent in CI) - there is nothing
        to fill it in from there, and the refusal says so.
        """
        container = curated_row["container"]
        project = curated_row.get("project")
        row = {"container": container, "project": project}

        if container in seen:
            # The render is authoritative here, AND it audits what the sidecar
            # recorded: a stale `service` or `profile` in the sidecar would
            # otherwise pass unnoticed on a machine that can render the project
            # and then produce a different file on one that cannot.
            rendered_project, service, profiles = seen[container]
            row["project"] = rendered_project
            if project is not None and rendered_project != project:
                self.drift.append(
                    f"WRONG project for {container}: the sidecar says '{project}', the render says "
                    f"'{rendered_project}'"
                )
            derived = {}
            if container in self._ambiguous:
                pass          # neither key is derivable - see _planes()
            else:
                if service != container:
                    derived["service"] = service
                if len(profiles) == 1:
                    derived["profile"] = profiles[0]
                elif profiles:
                    derived["profiles"] = sorted(profiles)

            # A container row's `profile` is DERIVED where the render carries one
            # and DECLARED where the plane's compose is a pinned submodule that
            # does not carry it yet. Nine OB1 rows are in the second state today:
            # openbrain-wiki and friends sit behind `wiki` on the OB1 branch, and
            # the gitlink still points at a commit with no such profile, so the
            # render reports none. Calling the curated value STALE there would be
            # the check lying; dropping it would lose a true declaration that the
            # watchdog and the coverage guard both read.
            unpinned = self.unpinned_profiles(rendered_project and self._plane_of.get(rendered_project),
                                              renders[rendered_project]) if rendered_project in renders else set()
            declared_profile = curated_row.get("profile")
            accepted_profile = (
                bool(declared_profile) and not derived.get("profile") and declared_profile in unpinned
            )
            if accepted_profile:
                self.declared_not_rendered.append(
                    f"{container}: `profile: {declared_profile}` is declared in "
                    f"{CURATED_REL.as_posix()} and the pinned compose carries no profile for it"
                )
                derived["profile"] = declared_profile

            # The STALE loop runs REGARDLESS, and exempts only the one key the
            # pinned-submodule rule is about. It used to sit in an `else`, so a
            # row in that bucket had its `service` and `profiles` unchecked too:
            # a bogus `service` on openbrain-wiki passed here and failed in CI,
            # where the submodule is absent and the bucket does not apply. A
            # check whose answer depends on which machine runs it is not a check.
            for key in ("service", "profile", "profiles"):
                if key == "profile" and accepted_profile:
                    continue
                if key == "profile" and container in self._ambiguous and not curated_row.get(key):
                    continue
                if curated_row.get(key) != derived.get(key):
                    self.drift.append(
                        f"STALE `{key}` for {container} in {CURATED_REL.as_posix()}: it records "
                        f"{json.dumps(curated_row.get(key))}, the render says "
                        f"{json.dumps(derived.get(key))}"
                    )
            row.update(derived)
        else:
            render = renders.get(project)
            if project is None:
                self.drift.append(
                    f"NO `project` for {container} in {CURATED_REL.as_posix()}, and no compose render "
                    "produced that container, so there is nothing to fill it in from. Either the "
                    "service is not declared in any plane's compose file yet, or its project cannot be "
                    "rendered here (the OB1 submodule is absent in CI) - in which case the row must "
                    'name it: "project": "<compose project>".'
                )
            elif render is None:
                self.drift.append(
                    f"UNKNOWN project '{project}' for {container} (group {group}) - it is not in "
                    f"{CURATED_REL.as_posix()}'s projects map"
                )
            elif render.available:
                self.drift.append(
                    f"NOT IN THE RENDER: {container} sits in the {group} group owned by project "
                    f"'{project}', which renders {len(render.services)} service(s) and none is that container"
                )
            # An unrenderable project carries its recorded facts, unverified.
            for key in ("service", "profile", "profiles"):
                if curated_row.get(key):
                    row[key] = curated_row[key]

        for key in ("critical", "host_health", "stale_pool_guard", "note"):
            if key in curated_row:
                row[key] = curated_row[key]
        if "critical" not in row:
            self.drift.append(f"NO `critical` flag for {container} in {CURATED_REL.as_posix()}")
        return {key: row[key] for key in ROW_KEY_ORDER if key in row}

    # -- the manifest's own declarations, now with a consumer --------------

    def _check_ports(self, plane: str, render: Render) -> None:
        declared = {str(port) for port in (self.manifest.plane(plane).get("ports") or {})}
        published = {port for spec in render.services.values() for port in spec["ports"]}
        for port in sorted(declared - published, key=int):
            self.drift.append(
                f"PORT {port} is declared under [planes.{plane}.ports] in {MANIFEST_NAME} but no service "
                "in that plane publishes it"
            )
        for port in sorted(published - declared, key=int):
            owner = next((spec["container"] for spec in render.services.values() if port in spec["ports"]), "?")
            self.drift.append(
                f"PORT {port} is published by {owner} but is NOT declared under [planes.{plane}.ports] "
                f"in {MANIFEST_NAME}"
            )

    def _check_profile_accounting(self) -> None:
        """Every declared profile says whether a default `up` starts it.

        This is the gate that stops a new profile from silently shrinking the
        running set. When a compose file gains a profile around services that
        run TODAY, the plane's manifest table must say `default = true` (the
        driver passes it) or `opt_in = true` (something else turns it on, and
        the description says what). Saying nothing is what a reduction looks
        like, and it is exactly the shape the ob1 research/wiki/notebook
        profiles arrive in.

        Manifest-only on purpose: COMPOSE_PROFILES lives in a gitignored env
        file, so it cannot be the thing CI checks. Every plane is checked,
        including the `manual` one the inventory itself skips.
        """
        for plane in self.manifest.order:
            for profile in self.manifest.unaccounted_profiles(plane):
                self.drift.append(
                    f"PROFILE '{profile}' under [planes.{plane}.profiles] in {MANIFEST_NAME} declares "
                    "neither `default = true` (the driver passes it on every invocation), `opt_in = true` "
                    "(COMPOSE_PROFILES, an operator or another script turns it on - say which in the "
                    "description) nor `pending = true` (not in the compose file yet). Until it does, a "
                    "bare `up` will not start what it gates and nothing says that was intended."
                )

    def unpinned_profiles(self, plane: str, render: Render) -> set:
        """Profiles the manifest declares that the PINNED compose does not carry.

        Empty for every plane whose compose file lives in this repo - there the
        two must agree. It is how `research` / `wiki` / `notebook` WERE described
        on ob1 until 2026-09-20: real on the OB1 branch, absent from the commit
        the gitlink pinned (5005197), and verified automatically the moment it
        bumped - which sl-ob1-gitlink did, to fe3e045. So this returns the empty
        set for every plane today; the mechanism stays for the next submodule
        profile that lands ahead of its gitlink.
        """
        if not (render.available and render.pinned):
            return set()
        pending = set(self.manifest.pending_profiles(plane))
        return (set(self.manifest.profiles(plane)) - pending) - set(render.profiles)

    def _check_profiles(self, plane: str, render: Render) -> None:
        pending = set(self.manifest.pending_profiles(plane))
        declared = set(self.manifest.profiles(plane)) - pending
        found = set(render.profiles)
        compose_rel = self.manifest.plane(plane)["compose"]
        unpinned = self.unpinned_profiles(plane, render)
        for profile in sorted(unpinned):
            self.declared_not_rendered.append(
                f"PROFILE '{profile}' ([planes.{plane}.profiles] in {MANIFEST_NAME}) is not in the PINNED "
                f"{compose_rel}. Declared, not rendered - it is real on the submodule's branch and the "
                "gitlink has not moved. Verified automatically once it does; until then a bare `up` neither "
                "passes nor needs it"
            )
        for profile in sorted(declared - found - unpinned):
            self.drift.append(
                f"PROFILE '{profile}' is declared under [planes.{plane}.profiles] in {MANIFEST_NAME} but "
                f"{compose_rel} declares no such profile (mark it `pending = true` if the item that adds "
                "it has not landed)"
            )
        for profile in sorted(found - declared):
            why = " (the manifest still marks it `pending = true`)" if profile in pending else ""
            self.drift.append(
                f"PROFILE '{profile}' exists in {compose_rel} but is not a live declaration under "
                f"[planes.{plane}.profiles] in {MANIFEST_NAME}{why}"
            )


def _inventory_text(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _inventory_diff(current: dict, wanted: dict) -> list[str]:
    """The rows that differ, named. A whole-file diff is not an answer."""
    lines = []
    if list(current.get("_comment") or []) != list(wanted.get("_comment") or []):
        lines.append(f"_comment: the header differs from {CURATED_REL.as_posix()}'s")

    have, want = current.get("projects") or {}, wanted.get("projects") or {}
    for project in sorted(set(have) | set(want)):
        if have.get(project) != want.get(project):
            lines.append(f"projects.{project}: committed {json.dumps(have.get(project))} != "
                         f"generated {json.dumps(want.get(project))}")

    have, want = current.get("planes") or {}, wanted.get("planes") or {}
    for group in sorted(set(have) | set(want)):
        rows_have = {row.get("container"): row for row in (have.get(group) or [])}
        rows_want = {row.get("container"): row for row in (want.get(group) or [])}
        for container in sorted(set(rows_have) | set(rows_want)):
            if rows_have.get(container) != rows_want.get(container):
                lines.append(f"planes.{group}[{container}]: committed {json.dumps(rows_have.get(container))} "
                             f"!= generated {json.dumps(rows_want.get(container))}")
        if ([row.get("container") for row in (have.get(group) or [])]
                != [row.get("container") for row in (want.get(group) or [])]):
            lines.append(f"planes.{group}: the row ORDER differs from the sidecar's")
    return lines or ["the files differ in a way the row diff does not describe"]


def cmd_inventory(manifest, root, console, capture, write: bool, check: bool) -> int:
    if write == check:
        raise Refusal(
            "refused: `inventory` needs exactly one of --write (regenerate "
            f"{INVENTORY_REL.as_posix()}) or --check (fail on drift, change nothing)"
        )
    inventory = Inventory(manifest, root, capture)
    data = inventory.build()
    path = root / INVENTORY_REL

    for note in inventory.skipped:
        console.line(f"  [ -- ] NOT VERIFIED - {note}")
    for note in inventory.mutually_exclusive:
        console.line(f"  [ ~~ ] mutually exclusive - {note}")
    for note in inventory.declared_not_rendered:
        console.line(f"  [ ~~ ] declared, not rendered - {note}")
    if inventory.declared_not_rendered:
        console.line(
            "  [ ~~ ] ^ those resolve themselves when the submodule gitlink bumps. AT THAT BUMP the "
            "profiles start gating real services, so run `python scripts/stack/stack.py init --product "
            "research --force` (or `enable research`) once, or a bare `up` will start fewer containers "
            "than it does today."
        )

    if inventory.drift:
        for line in inventory.drift:
            console.line(f"  [FAIL] {line}")
        console.line("")
        console.line(
            f"{len(inventory.drift)} problem(s). Fix {CURATED_REL.as_posix()} (or {MANIFEST_NAME}) and "
            "re-run; nothing is written while the inputs disagree with the compose files."
        )
        return EXIT_REFUSED

    wanted = _inventory_text(data)
    current = path.read_text(encoding="utf-8") if path.is_file() else None

    if check:
        if current is None:
            console.line(f"  [FAIL] {rel(root, path)} does not exist (run `inventory --write`)")
            return EXIT_REFUSED
        try:
            same = json.loads(current) == data
        except ValueError as exc:
            console.line(f"  [FAIL] {rel(root, path)} is not valid JSON ({exc})")
            return EXIT_REFUSED
        if not same:
            for line in _inventory_diff(json.loads(current), data):
                console.line(f"  [FAIL] {line}")
            console.line("")
            console.line(f"{rel(root, path)} is not what {MANIFEST_NAME}, "
                         f"{CURATED_REL.as_posix()} and the compose renders say. "
                         "Re-run with --write and commit the result.")
            return EXIT_REFUSED
        console.line(f"  [OK]   {rel(root, path)} matches the manifest, the sidecar and the compose renders")
        return EXIT_OK

    if current == wanted:
        console.line(f"  [OK]   {rel(root, path)} already up to date")
        return EXIT_OK
    path.parent.mkdir(parents=True, exist_ok=True)
    # LF on purpose: .gitattributes gives *.json eol=lf, so writing the Windows
    # default would rewrite every line and show the whole file as changed.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(wanted)
    console.line(f"wrote {rel(root, path)}")
    return EXIT_OK


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stack.py",
        description="Drive the ai-stack planes this machine enables, from stack.manifest.toml.",
    )
    parser.add_argument("--root", default=None,
                        help="repo root holding the manifest (default: two directories above this script)")
    parser.add_argument("--manifest", default=None, help=f"manifest path (default: <root>/{MANIFEST_NAME})")
    parser.add_argument("--state", default=None, help=f"state path (default: <root>/{STATE_REL.as_posix()})")
    sub = parser.add_subparsers(dest="verb")

    sub.add_parser("list", help="planes, their state, and the products that group them")

    for verb, helptext in (("status", "docker compose ps per plane (read-only)"),
                           ("up", "start planes in dependency order"),
                           ("down", "stop them in reverse order")):
        p = sub.add_parser(verb, help=helptext)
        p.add_argument("plane", nargs="?", default=None, help="act on exactly this plane")
        p.add_argument("--all", dest="every", action="store_true",
                       help="every declared plane except the `manual` ones, instead of the enabled set")
        if verb != "status":
            p.add_argument("--dry-run", action="store_true", help="print the docker commands, run nothing")

    p = sub.add_parser("restart", help="restart one plane in place")
    p.add_argument("plane")
    p.add_argument("--dry-run", action="store_true", help="print the docker command, run nothing")

    p = sub.add_parser("recover", help="ordered, health-gated restart (emergency-recovery.ps1 `recover`)")
    p.add_argument("plane", nargs="?", default=None, help="recover exactly this plane")
    p.add_argument("--all", dest="every", action="store_true",
                   help="every declared plane except the `manual` ones, instead of the enabled set")
    p.add_argument("--dry-run", action="store_true",
                   help="render the planes and print the plan; stop, start and create nothing")
    p.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                   help="one gate budget for every container (default: each healthcheck's own worst case)")

    p = sub.add_parser("backup", help="archive a plane's named volumes into backups/<plane>/manual-<stamp>/")
    p.add_argument("plane")
    p.add_argument("--dest", default=None, metavar="DIR",
                   help="write under DIR/<plane>/ instead of <root>/backups/<plane>/")

    p = sub.add_parser("restore", help="restore a plane's volumes from a `backup` directory")
    p.add_argument("plane")
    p.add_argument("--from", dest="source", required=True, metavar="ARCHIVE",
                   help="the manual-<stamp> directory, its manifest.json, or one of its .tar.gz archives")
    p.add_argument("--volume", default=None, help="restore only this volume (its name or compose key)")

    for verb, helptext in (("enable", "enable a plane or a product on this machine"),
                           ("disable", "disable a plane or a product")):
        p = sub.add_parser(verb, help=helptext)
        p.add_argument("name")
        p.add_argument("--plane", dest="kind", action="store_const", const="plane",
                       help="resolve the name as a plane")
        p.add_argument("--product", dest="kind", action="store_const", const="product",
                       help="resolve the name as a product")
        if verb == "enable":
            p.add_argument("--headless", action="store_true",
                           help="do not pull in the product's surfaces")
        p.set_defaults(kind="auto")

    sub.add_parser("doctor", help="docker, compose, env files and blank keys")
    sub.add_parser("health", help="the functional probe sweep (read-only; exit code = failed probes)")
    p = sub.add_parser("stats", help="container CPU/memory/net + the inference queue and ledger (read-only)")
    p.add_argument("--hours", type=int, default=1, help="ledger window in hours (default 1)")
    p.add_argument("--bucket-minutes", type=int, default=10, help="demand bucket size (default 10)")

    p = sub.add_parser("inventory", help="generate or verify scripts/lib/stack-services.json")
    p.add_argument("--write", action="store_true",
                   help="regenerate the inventory from the manifest, the sidecar and the compose renders")
    p.add_argument("--check", action="store_true",
                   help="fail on any drift and name the rows; writes nothing")

    p = sub.add_parser("init", help="write a state file for this machine")
    p.add_argument("--planes", default=None, help="comma-separated plane names")
    p.add_argument("--product", default=None, help="a product name")
    p.add_argument("--context", action="append", default=[], metavar="PLANE=NAME",
                   help="run one plane on a named docker context (repeatable)")
    p.add_argument("--headless", action="store_true", help="with --product: skip its surfaces")
    p.add_argument("--force", action="store_true", help="overwrite an existing state file")
    return parser


def main(argv=None, runner=None, stdout=None, capture=None, http=None, pipe=None) -> int:
    """Five seams, so every test is hermetic.

    `runner(cmd, cwd) -> int` STREAMS a command (docker compose up, the stats
    script); `capture(cmd, cwd) -> CommandResult` reads one back (the health
    probes, the compose renders); `http(url, timeout) -> HttpResult` is the
    only network call; `pipe(cmd, cwd, stdin_path=, stdout_path=)` moves a
    file through a command (backup's tar out, restore's tar in); `stdout` is
    the single output stream.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stdout)
    runner = runner or subprocess_runner
    capture = capture or subprocess_capture
    http = http or urllib_get
    pipe = pipe or subprocess_pipe

    if not args.verb:
        parser.print_help(console.stream)
        return EXIT_USAGE

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest).resolve() if args.manifest else root / MANIFEST_NAME
    state_path = Path(args.state).resolve() if args.state else root / STATE_REL

    try:
        manifest = Manifest.load(manifest_path)
        state = State.load(state_path)
        state.path = state_path

        if args.verb == "list":
            return cmd_list(manifest, state, root, console)
        if args.verb == "status":
            return cmd_status(manifest, state, root, console, runner, args.plane, args.every)
        if args.verb == "up":
            return cmd_up(manifest, state, root, console, runner, args.plane, args.every, args.dry_run,
                          capture)
        if args.verb == "down":
            return cmd_down(manifest, state, root, console, runner, args.plane, args.every, args.dry_run)
        if args.verb == "restart":
            return cmd_restart(manifest, state, root, console, runner, args.plane, args.dry_run)
        if args.verb == "enable":
            return cmd_enable(manifest, state, root, console, args.name, args.kind, args.headless, capture)
        if args.verb == "disable":
            return cmd_disable(manifest, state, root, console, args.name, args.kind)
        if args.verb == "doctor":
            return cmd_doctor(manifest, state, root, console, runner, capture)
        if args.verb == "init":
            return cmd_init(manifest, state, root, console, args, capture)
        if args.verb == "health":
            return cmd_health(manifest, state, root, console, capture, http)
        if args.verb == "stats":
            return cmd_stats(manifest, state, root, console, runner, capture, args.hours, args.bucket_minutes)
        if args.verb == "recover":
            return cmd_recover(manifest, state, root, console, runner, capture, args.plane, args.every,
                               args.dry_run, args.timeout)
        if args.verb == "backup":
            return cmd_backup(manifest, state, root, console, capture, pipe, args.plane, args.dest)
        if args.verb == "restore":
            return cmd_restore(manifest, state, root, console, capture, pipe, args.plane, args.source,
                               args.volume)
        if args.verb == "inventory":
            return cmd_inventory(manifest, root, console, capture, args.write, args.check)
    except Refusal as refusal:
        console.line(str(refusal))
        return EXIT_REFUSED

    parser.print_help(console.stream)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
