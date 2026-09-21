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

Verbs:  status  up  down  restart <plane>  enable  disable  list  doctor  init
        health  stats  inventory --write|--check
        status/up/down take an optional plane, or --all for every declared
        plane (the `manual` ones excepted); with neither they act on the set
        this machine ENABLES.
        `--dry-run` on up/down/restart prints the exact docker command lines and
        runs nothing. `status`, `health` and `inventory --check` are READ-ONLY:
        no lease, nothing started, stopped or recreated.

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
import json
import os
import shutil
import subprocess
import sys
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


def blank_keys(manifest: Manifest, root: Path, plane: str) -> list[tuple[str, str, Path]]:
    """[(key, 'blank'|'missing', env path)] for every key that would fail the plane."""
    wanted = manifest.keys(plane)
    if not wanted:
        return []
    env_path = manifest.env_path(root, plane)
    values = read_env_file(env_path)
    problems = []
    for key in wanted:
        if key not in values:
            problems.append((key, "missing", env_path))
        elif values[key].strip() == "":
            problems.append((key, "blank", env_path))
    return problems


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


def _drive(manifest, state, root, console, runner, verb_args, planes, dry_run, label) -> int:
    for plane in planes:
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


def cmd_up(manifest, state, root, console, runner, plane, every: bool, dry_run: bool) -> int:
    ordered, mode = select_planes(manifest, state, plane, every, "up")
    if not ordered:
        console.line("# nothing enabled (`stack.py enable <plane|product>`, `stack.py init`, or `up --all`)")
        return EXIT_OK
    driven = [p for p in ordered if not manifest.manual(p)]
    if mode == "one":
        _requires_note(manifest, console, plane, driven)
    code = _drive(manifest, state, root, console, runner, ["up", "-d"], driven, dry_run, "up")
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
            "refused: `restart all` would restart every plane at once. Use `stack.py down` then "
            "`stack.py up`, or scripts/recovery/emergency-recovery.ps1 for an ordered restart with health gates."
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


def _key_problem_lines(manifest, root, planes, subject) -> list[str]:
    lines = []
    for plane in planes:
        for key, why, env_path in blank_keys(manifest, root, plane):
            where = f" (read by plane {plane})" if plane != subject else ""
            lines.append(
                f"  {key} is {why} in {rel(root, env_path)}{where}"
            )
    return lines


def _key_remedy(manifest, root, planes) -> str:
    """The requires-refusal names a command; this one must too."""
    files = []
    for plane in planes:
        path = rel(root, manifest.env_path(root, plane))
        if blank_keys(manifest, root, plane) and path not in files:
            files.append(path)
    where = " and ".join(files) if files else "the plane's env file"
    return (
        f"Set them in {where}, then re-run "
        "(`python scripts/stack/stack.py doctor` lists every blank key on this machine)."
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


def cmd_enable(manifest, state, root, console, name, kind, headless: bool) -> int:
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
        problems = _key_problem_lines(manifest, root, [target], target)
        if problems:
            raise Refusal(
                f"refused: {target} needs these keys before it can be enabled:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(manifest, root, [target])
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
        problems = _key_problem_lines(manifest, root, full, target)
        if problems:
            raise Refusal(
                f"refused: product {target} needs these keys before it can be enabled:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(manifest, root, full)
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


def cmd_doctor(manifest, state, root, console, runner) -> int:
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
        if compose_path.is_file():
            console.line(f"    [OK]   compose {manifest.plane(plane)['compose']}")
        else:
            console.line(f"    [FAIL] compose file missing: {manifest.plane(plane)['compose']}")
            problems += 1
        env_path = manifest.env_path(root, plane)
        loaded = (f"--env-file {manifest.env_file(plane)}" if manifest.env_file(plane)
                  else "compose loads it from the project dir")
        if env_path.is_file():
            console.line(f"    [OK]   env {rel(root, env_path)} ({loaded})")
        else:
            console.line(f"    [FAIL] env file missing: {rel(root, env_path)} ({loaded})")
            problems += 1
        for key, why, path in blank_keys(manifest, root, plane):
            console.line(f"    [FAIL] {key} is {why} in {rel(root, path)}")
            problems += 1
        for requirement in manifest.plane(plane).get("host", []):
            console.line(f"    [ -- ] host: {requirement}")
    console.line("")
    console.line("OK" if problems == 0 else f"{problems} problem(s)")
    return EXIT_OK if problems == 0 else EXIT_REFUSED


def cmd_init(manifest, state, root, console, args) -> int:
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
        code = cmd_enable(manifest, fresh, root, console, args.product, "product", args.headless)
        if code != EXIT_OK:
            return code
    else:
        chosen = order_planes(manifest, dependency_closure(manifest, fresh.planes))
        problems = _key_problem_lines(manifest, root, chosen, "")
        if problems:
            raise Refusal(
                "refused: these keys must be set before that state file would work:\n"
                + "\n".join(problems)
                + "\n" + _key_remedy(manifest, root, chosen)
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

    def __init__(self, console: Console, root: Path, capture, http):
        self.console = console
        self.root = root
        self.capture = capture
        self.http = http
        self.failed = 0
        self.results: list[tuple[str, bool]] = []

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
        self.probe(
            "anchor: ai-stack_llm-net exists",
            lambda: self.docker(
                "network", "inspect", "ai-stack_llm-net", "--format", "{{.Name}}"
            ).stdout.strip() == "ai-stack_llm-net",
        )
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
        drift = self.owui_drift()
        self.probe(f"frontend: owui/ manifest rows drifted from live webui.db: {drift}", drift == "0")
        self.probe(
            "memory: cloud door http://127.0.0.1:8060/health",
            lambda: self.http_ok("http://127.0.0.1:8060/health"),
        )
        self.probe(
            "search: gateway http://127.0.0.1:8085/healthz",
            lambda: self.http_ok("http://127.0.0.1:8085/healthz"),
        )
        engines = self.search_engines()
        self.probe(f"search: {engines}", engines != "REFUSED" and not engines.startswith("DEGRADED"))
        self.probe(
            "coder: little-coder daemon :8090/health",
            lambda: self.docker(
                "exec", "little-coder", "curl", "-fsS", "--max-time", "8",
                "http://localhost:8090/health",
            ).code == 0,
        )
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
        Nothing secret reaches this process, its argv, or the probe line.
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

    def owui_drift(self) -> str:
        """'0', a drifted count, or 'REFUSED - <why>'. Never raises.

        owui/ plugins deploy BY PASTE: nothing links the repo file to the live
        webui.db row, so a committed fix can sit unpasted for weeks (the
        deep_research banner, 2026-09-04..06). check-owui-drift.ps1 -CountOnly
        prints the count on stdout, or the word REFUSED with the sentence on
        stderr and exit 2. Anything that is not a number is a FAIL.
        """
        script = self.root / "scripts" / "checks" / "check-owui-drift.ps1"
        result = self.capture(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-CountOnly"],
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


def cmd_health(root, console, capture, http) -> int:
    """Exit code is the NUMBER OF FAILED PROBES, exactly as stack.ps1 health."""
    return HealthSweep(console, root, capture, http).run()


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------


def cmd_stats(root: Path, console: Console, runner) -> int:
    """Delegate to scripts/stack/stack-stats.ps1.

    That script reads the llm-queue /observe board and the LiteLLM spend ledger
    through `docker exec ... psql`, and it is PowerShell-only today. Rather than
    reimplement a hundred lines of report formatting for a host that cannot be
    the one asking, this verb REFUSES off Windows and says what to run instead.
    A verb that silently prints nothing and exits 0 is the failure class this
    repo hunts; `stats` is not going to join it.
    """
    script = root / "scripts" / "stack" / "stack-stats.ps1"
    if not script.is_file():
        raise Refusal(f"refused: {rel(root, script)} is missing, so there is nothing to report")
    if not WINDOWS:
        raise Refusal(
            "refused: `stats` reads the LiteLLM ledger through scripts/stack/stack-stats.ps1, which is "
            "PowerShell 5.1 only and is not ported. Run it on the Windows host (powershell -NoProfile "
            "-ExecutionPolicy Bypass -File scripts/stack/stack-stats.ps1), or read the queue board "
            "directly at llm-queue's /observe/queue."
        )
    return runner(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], root)


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
    sub.add_parser("stats", help="inference demand + queue statistics (delegates to stack-stats.ps1)")

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


def main(argv=None, runner=None, stdout=None, capture=None, http=None) -> int:
    """Four seams, so every test is hermetic.

    `runner(cmd, cwd) -> int` STREAMS a command (docker compose up, the stats
    script); `capture(cmd, cwd) -> CommandResult` reads one back (the health
    probes, the compose renders); `http(url, timeout) -> HttpResult` is the
    only network call; `stdout` is the single output stream.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stdout)
    runner = runner or subprocess_runner
    capture = capture or subprocess_capture
    http = http or urllib_get

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
            return cmd_up(manifest, state, root, console, runner, args.plane, args.every, args.dry_run)
        if args.verb == "down":
            return cmd_down(manifest, state, root, console, runner, args.plane, args.every, args.dry_run)
        if args.verb == "restart":
            return cmd_restart(manifest, state, root, console, runner, args.plane, args.dry_run)
        if args.verb == "enable":
            return cmd_enable(manifest, state, root, console, args.name, args.kind, args.headless)
        if args.verb == "disable":
            return cmd_disable(manifest, state, root, console, args.name, args.kind)
        if args.verb == "doctor":
            return cmd_doctor(manifest, state, root, console, runner)
        if args.verb == "init":
            return cmd_init(manifest, state, root, console, args)
        if args.verb == "health":
            return cmd_health(root, console, capture, http)
        if args.verb == "stats":
            return cmd_stats(root, console, runner)
        if args.verb == "inventory":
            return cmd_inventory(manifest, root, console, capture, args.write, args.check)
    except Refusal as refusal:
        console.line(str(refusal))
        return EXIT_REFUSED

    parser.print_help(console.stream)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
