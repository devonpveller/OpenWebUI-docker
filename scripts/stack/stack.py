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
        `--dry-run` on up/down/restart prints the exact docker command lines and
        runs nothing.

Everything - refusals included - is written to STDOUT, never stderr, and the
exit code carries the failure. PowerShell 5.1 turns a native command's stderr
into a terminating NativeCommandError under `$ErrorActionPreference = 'Stop'`
(the trap that once ate nine probes out of stack.ps1's health sweep), and this
driver is meant to be callable from a .ps1 without that dance.

Exit codes: 0 fine, 1 refused / a docker command failed, 2 usage error.

Design and every verb's refusal cases: scripts/stack/README.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

MANIFEST_NAME = "stack.manifest.toml"
STATE_REL = Path(".stack") / "state.json"
STATE_VERSION = 1

# A fresh clone with no state file runs Open WebUI and nothing else.
DEFAULT_ENABLED = ("frontend",)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


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

    def default_profiles(self, name: str) -> list[str]:
        return [p for p, spec in self.profiles(name).items() if _spec(spec).get("default")]

    def pending_profiles(self, name: str) -> list[str]:
        return [p for p, spec in self.profiles(name).items() if _spec(spec).get("pending")]

    def keys(self, name: str) -> list[str]:
        return list(self.plane(name).get("keys", []))

    def env_file(self, name: str) -> str | None:
        """The --env-file the compose CLI is given, or None when compose loads its own."""
        value = self.plane(name).get("env_file")
        return value or None

    def env_path(self, root: Path, name: str) -> Path:
        """The env file the plane actually READS - which is what a key check must read.

        With an explicit env_file that is the file; without one, compose loads the
        .env sitting in the compose file's own directory (that is exactly why ob1
        and agent-org pass no --env-file).
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


def run_profiles(manifest: Manifest, state: State, plane: str) -> list[str]:
    """State profiles unioned with the plane's `default = true` ones.

    The union is what keeps parity with stack.ps1, which passes OB1's
    idea-refinery profile on every invocation whatever the operator asked for.
    """
    wanted = set(state.profiles_of(plane)) | set(manifest.default_profiles(plane))
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
            profiles=run_profiles(manifest, state, plane),
        )
        console.line(" ".join(cmd))
        if dry_run:
            continue
        code = runner(cmd, root)
        if code != 0:
            console.line(f"# {label} stopped: {plane} exited {code}")
            return EXIT_REFUSED
    return EXIT_OK


def cmd_up(manifest, state, root, console, runner, dry_run: bool) -> int:
    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if not enabled:
        console.line("# nothing enabled (`stack.py enable <plane|product>` or `stack.py init`)")
        return EXIT_OK
    ordered = order_planes(manifest, dependency_closure(manifest, enabled))
    driven = [p for p in ordered if not manifest.manual(p)]
    skipped = [p for p in ordered if manifest.manual(p)]
    code = _drive(manifest, state, root, console, runner, ["up", "-d"], driven, dry_run, "up")
    for plane in skipped:
        console.line(f"# {plane} is not driven by stack.py - start it with {manifest.manual(plane)}")
    return code


def cmd_down(manifest, state, root, console, runner, dry_run: bool) -> int:
    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if not enabled:
        console.line("# nothing enabled")
        return EXIT_OK
    ordered = order_planes(manifest, dependency_closure(manifest, enabled))
    driven = [p for p in reversed(ordered) if not manifest.manual(p)]
    skipped = [p for p in ordered if manifest.manual(p)]
    code = _drive(manifest, state, root, console, runner, ["down"], driven, dry_run, "down")
    for plane in skipped:
        console.line(f"# {plane} is not driven by stack.py - stop it with {manifest.manual(plane)}")
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


def cmd_status(manifest, state, root, console, runner) -> int:
    enabled = [p for p in manifest.order if state.is_enabled(p)]
    if not enabled:
        console.line("# nothing enabled")
        return EXIT_OK
    failures = 0
    for plane in order_planes(manifest, enabled):
        cmd = compose_command(
            manifest, plane, ["ps"], context=state.context_of(plane),
            profiles=run_profiles(manifest, state, plane),
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
        profiles = manifest.default_profiles(target)
        state.enable(target, profiles)
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
            profile_map[plane] = manifest.profile_order(plane, set(profiles))
            state.enable(plane, profile_map[plane])
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
        loaded = "--env-file" if manifest.env_file(plane) else "compose loads it from the project dir"
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
        fresh.enable(plane, manifest.default_profiles(plane))

    for plane, ctx in contexts.items():
        fresh.enable(plane, manifest.default_profiles(plane), context=ctx)

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
    sub.add_parser("status", help="docker compose ps per enabled plane (read-only)")

    for verb, helptext in (("up", "start enabled planes in dependency order"),
                           ("down", "stop them in reverse order")):
        p = sub.add_parser(verb, help=helptext)
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

    p = sub.add_parser("init", help="write a state file for this machine")
    p.add_argument("--planes", default=None, help="comma-separated plane names")
    p.add_argument("--product", default=None, help="a product name")
    p.add_argument("--context", action="append", default=[], metavar="PLANE=NAME",
                   help="run one plane on a named docker context (repeatable)")
    p.add_argument("--headless", action="store_true", help="with --product: skip its surfaces")
    p.add_argument("--force", action="store_true", help="overwrite an existing state file")
    return parser


def main(argv=None, runner=None, stdout=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stdout)
    runner = runner or subprocess_runner

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
            return cmd_status(manifest, state, root, console, runner)
        if args.verb == "up":
            return cmd_up(manifest, state, root, console, runner, args.dry_run)
        if args.verb == "down":
            return cmd_down(manifest, state, root, console, runner, args.dry_run)
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
    except Refusal as refusal:
        console.line(str(refusal))
        return EXIT_REFUSED

    parser.print_help(console.stream)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
