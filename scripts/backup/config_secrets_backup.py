#!/usr/bin/env python3
"""Encrypted nightly backup of the stack's gitignored config + secrets.

WHY (2026-10-04, tracker G34): Authelia crash-looped 205 times because
portal/config/authelia/users_database.yml was gone and had never been backed
up. Every sidecar backs up a VOLUME; nothing backed up the gitignored files the
stack cannot run without (every plane's .env, secrets/, the users DB, the OAuth
token files bind-mounted into containers), and check-backup-coverage.ps1 said
"clean" because it only looks at named volumes.

WHAT. One host job (a Windows scheduled task, like the NAS sync - the sources
are files in this checkout, so a sidecar would need the whole repo and every
secret mounted into a long-running container):
  1. derives the inventory (config-secrets.toml says how; `inventory` prints it),
  2. streams a tar of those files straight into `age` - NO plaintext ever
     touches the disk: tar is built in memory and piped to age's stdin, and age
     writes only ciphertext (to <name>.partial, renamed when age succeeded),
  3. encrypts to the operator's PUBLIC age key (the host never holds the private
     key, so a restore needs the operator - by design),
  4. writes backups/config-secrets/config-secrets-<UTC>.tar.age + .sha256 (the
     sidecar sentinel format) + .files.txt (the list of paths, no contents and
     no hashes: a hash of a short secret file is brute-forceable),
  5. prunes by count (retain_count), but never prunes the LAST archive that
     still holds a file the newer ones no longer have,
  6. pins every archived gitignored path into .git/info/exclude (pin-ignores,
     below), and
  7. logs to logs/config-secrets-backup-<date>.log, with a completion marker
     that scripts/sysadmin-mcp/check_backups.py reads.

The NAS sync (scripts/backup/backup-to-nas.ps1) mirrors backups/ weekly, so the
archives go off-site with no change there.

Subcommands:
  run          make an archive (the scheduled task runs this)
  check        coverage gate: FAILS while any derived file is not in the newest
               archive, a required file is missing, a bind source that should be
               a file is absent or an empty directory, a file is oversize, a
               plane cannot be rendered, or no recipient is configured
  inventory    print what would be archived and why (paths only, never values)
  pin-ignores  write the archived, currently-ignored paths into the repo's
               .git/info/exclude so an OLD branch (whose .gitignore predates a
               rule) cannot turn them into untracked files that a GUI client's
               "stash changes" then removes - the 2026-09-28 root cause.

Exit codes: 0 ok; 1 problems (run: archive written but something is missing or
uncovered - no completion marker); 2 refused (no/invalid recipient, unpinned age
binary, bad policy); 3 the archive could not be written.

NEVER prints, logs or copies a file's contents. Stdlib only, Python >= 3.11.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent.parent
DEFAULT_POLICY = HERE / "config-secrets.toml"

PREFIX = "config-secrets"
SUBDIR = "config-secrets"
ARCHIVE_SUFFIX = ".tar.age"
LIST_SUFFIX = ".files.txt"
SHA_SUFFIX = ".sha256"
PARTIAL_SUFFIX = ".partial"
LOG_PREFIX = "config-secrets-backup-"
START_MARKER = "=== config-secrets backup start ==="
SUCCESS_MARKER = "=== config-secrets backup complete ==="
AGE_MAGIC = b"age-encryption.org/v1\n"

EXIT_OK, EXIT_PROBLEMS, EXIT_REFUSED, EXIT_FAILED = 0, 1, 2, 3

PIN_BEGIN = "# >>> config-secrets pinned ignores (scripts/backup/config_secrets_backup.py pin-ignores) >>>"
PIN_END = "# <<< config-secrets pinned ignores <<<"

RENDER_TIMEOUT = 120


class Refused(Exception):
    """Configuration that must stop the job before it reads a single source file."""


class Failed(Exception):
    """The archive could not be produced."""


# --------------------------------------------------------------------------- policy

@dataclass
class Policy:
    retain_count: int = 14
    recipients_file: str = "secrets/config-backup/age-recipients.txt"
    age_binary: str = ""
    age_sha256: list = field(default_factory=list)
    max_file_bytes: int = 5 * 1024 * 1024
    pin_ignores: bool = True
    env_names: list = field(default_factory=list)
    env_name_ignore: list = field(default_factory=list)
    secret_dirs: list = field(default_factory=list)
    prune_names: list = field(default_factory=list)
    prune_paths: list = field(default_factory=list)
    file_like_names: list = field(default_factory=list)
    include: list = field(default_factory=list)
    required: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    outside_ok: list = field(default_factory=list)


def load_policy(path: Path) -> Policy:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise Refused(f"no policy file at {path}")
    except tomllib.TOMLDecodeError as exc:
        raise Refused(f"policy {path} is not valid TOML: {exc}")
    known = {"retain_count", "recipients_file", "age_binary", "max_file_bytes", "pin_ignores",
             "env_names", "env_name_ignore", "secret_dirs", "prune_names", "prune_paths",
             "file_like_names", "age_pin", "include", "required", "exclude", "outside"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise Refused(f"policy {path}: unknown key(s) {unknown}")
    # A plain key written AFTER an [[array-of-tables]] header silently joins that
    # table in TOML; refuse it instead of running with the default.
    for table in ("age_pin", "include", "required", "exclude", "outside"):
        allowed = {"age_pin": {"sha256", "what"}, "include": {"glob", "path", "why"},
                   "required": {"path", "plane", "why"}, "exclude": {"glob", "reason"},
                   "outside": {"path", "reason"}}[table]
        for entry in data.get(table, []):
            extra = sorted(set(entry) - allowed)
            if extra:
                raise Refused(f"policy {path}: [[{table}]] has unexpected key(s) {extra} - a plain key "
                              "placed after a [[table]] header belongs to that table; move it up")
    pol = Policy()
    for key in ("retain_count", "recipients_file", "age_binary", "max_file_bytes", "pin_ignores",
                "env_names", "env_name_ignore", "secret_dirs", "prune_names", "prune_paths",
                "file_like_names"):
        if key in data:
            setattr(pol, key, data[key])
    pol.age_sha256 = [str(e["sha256"]).lower() for e in data.get("age_pin", [])]
    pol.include = list(data.get("include", []))
    pol.required = list(data.get("required", []))
    pol.exclude = list(data.get("exclude", []))
    pol.outside_ok = list(data.get("outside", []))
    for entry in pol.exclude:
        if not entry.get("reason"):
            raise Refused(f"policy {path}: every [[exclude]] needs a reason ({entry.get('glob')!r} has none)")
    for entry in pol.outside_ok:
        if not entry.get("reason"):
            raise Refused(f"policy {path}: every [[outside]] needs a reason ({entry.get('path')!r} has none)")
    env_retain = os.environ.get("CONFIG_SECRETS_BACKUP_RETAIN_COUNT", "").strip()
    if env_retain:
        try:
            pol.retain_count = int(env_retain)
        except ValueError:
            raise Refused(f"CONFIG_SECRETS_BACKUP_RETAIN_COUNT={env_retain!r} is not a whole number")
    if not isinstance(pol.retain_count, int) or isinstance(pol.retain_count, bool):
        raise Refused(f"policy {path}: retain_count must be a whole number")
    if pol.retain_count < 1:
        raise Refused("retain_count must be at least 1")
    return pol


# --------------------------------------------------------------------------- helpers

def rel_of(root: Path, path: Path) -> str | None:
    """Repo-relative forward-slash path, or None when `path` is outside `root`."""
    full = os.path.abspath(path)
    base = os.path.abspath(root)
    if os.path.normcase(full) == os.path.normcase(base):
        return ""
    prefix = base.rstrip("\\/") + os.sep
    if os.path.normcase(full).startswith(os.path.normcase(prefix)):
        return full[len(prefix):].replace("\\", "/")
    return None


def match_any(rel: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(root: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                          input=stdin, capture_output=True, timeout=120)


def git_tracked(root: Path) -> set[str]:
    proc = git(root, "ls-files", "-z", "--recurse-submodules")
    if proc.returncode != 0:
        raise Refused(f"`git ls-files` failed in {root} (exit {proc.returncode}); "
                      "the job cannot tell tracked files from gitignored ones")
    return {p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def is_file_like(name: str, pol: Policy) -> bool:
    """Does a bind source NAME say it is meant to be a file? Compose does not record
    whether an absent source was a file or a directory, so the name decides: a suffix
    (`users_database.yml`, `token.json`, `.healthcheck.env`) or a listed name."""
    if name in pol.file_like_names:
        return True
    return bool(Path(name).suffix) or (name.startswith(".") and "." in name[1:])


# --------------------------------------------------------------------------- render

def plane_env_present(root: Path, compose_rel: str) -> bool:
    return (root / Path(compose_rel).parent / ".env").is_file()


def compose_render(root: Path, compose_rel: str, profiles):
    """`docker compose config --format json` for one plane. `profiles` None = what
    this host deploys (compose reads COMPOSE_PROFILES from the plane's own .env);
    a list = exactly those profiles. Needs the docker CLI, not the daemon. The JSON
    holds interpolated secret values: it stays in memory and only bind/config/secret
    SOURCE PATHS are read out of it. Returns the parsed dict, or None on failure."""
    env = dict(os.environ)
    env.pop("COMPOSE_PROFILES", None)
    cmd = ["docker", "compose", "-f", compose_rel]
    if profiles is not None:
        env["COMPOSE_PROFILES"] = ",".join(profiles)
        for prof in profiles:
            cmd += ["--profile", prof]
    cmd += ["config", "--format", "json"]
    try:
        proc = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, timeout=RENDER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def render_sources(rendered: dict) -> list[str]:
    """Every host-side SOURCE path in a compose render: bind mounts, plus file-backed
    top-level configs/secrets."""
    out = []
    for svc in (rendered.get("services") or {}).values():
        for vol in svc.get("volumes") or []:
            if isinstance(vol, dict) and vol.get("type") == "bind" and vol.get("source"):
                out.append(vol["source"])
    for top in ("configs", "secrets"):
        for spec in (rendered.get(top) or {}).values():
            if isinstance(spec, dict) and spec.get("file"):
                out.append(spec["file"])
    return out


def profile_closure(spec: dict, profile: str) -> list[str]:
    """`profile` plus every profile it `requires` in the manifest, transitively
    (frontend's tailscale cannot render without gpu, whose service it names)."""
    declared = spec.get("profiles", {})
    out, todo = [], [profile]
    while todo:
        p = todo.pop()
        if p in out:
            continue
        out.append(p)
        todo.extend((declared.get(p) or {}).get("requires", []))
    return out


def default_renderer(root: Path):
    """plane -> [(label, render-or-None)]: what this host deploys, no profile at all,
    and each declared profile with its `requires` closure - so a bind mount behind a
    profile that is off today is still known."""
    def render(plane: str, spec: dict):
        compose = spec["compose"]
        results = [("(this host's profiles)", compose_render(root, compose, None)),
                   ("(no profile)", compose_render(root, compose, []))]
        for prof in spec.get("profiles", {}):
            results.append((prof, compose_render(root, compose, profile_closure(spec, prof))))
        return results
    return render


# --------------------------------------------------------------------------- inventory

@dataclass
class Inventory:
    files: dict = field(default_factory=dict)       # rel -> source tag
    problems: list = field(default_factory=list)    # (KIND, rel-or-path, detail)
    excluded: dict = field(default_factory=dict)    # rel -> reason
    notes: list = field(default_factory=list)

    def add(self, rel: str, tag: str) -> None:
        if rel not in self.files:
            self.files[rel] = tag

    def problem(self, kind: str, what: str, detail: str) -> None:
        item = (kind, what, detail)
        if item not in self.problems:
            self.problems.append(item)


def _is_link(path) -> bool:
    return os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def walk_untracked(root: Path, pol: Policy, tracked: set[str], links: list | None = None) -> list[str]:
    """Untracked files under `root`, pruned per the policy. Symlinks and junctions are
    never followed; their repo-relative paths go into `links` (when given) so the
    caller can REPORT the ones that sit where a secret would be archived."""
    prune_paths = {p.strip("/") for p in pol.prune_paths}
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = rel_of(root, Path(dirpath)) or ""
        keep = []
        for d in dirnames:
            full = Path(dirpath) / d
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if d in pol.prune_names or "venv" in d.lower() or rel in prune_paths:
                continue
            if _is_link(full):
                if links is not None and rel not in tracked:
                    links.append(rel)
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for f in filenames:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            full = Path(dirpath) / f
            if rel in tracked:
                continue
            if _is_link(full):
                if links is not None:
                    links.append(rel)
                continue
            out.append(rel)
    return sorted(out)


def derive_inventory(root: Path, pol: Policy, manifest: Path | None = None, renderer=None,
                     tracked: set[str] | None = None) -> Inventory:
    inv = Inventory()
    root = Path(root)
    tracked = git_tracked(root) if tracked is None else tracked
    links: list[str] = []
    untracked = walk_untracked(root, pol, tracked, links)
    untracked_set = set(untracked)

    # 1. env files, 2. secret dirs, 4. include globs (over the walked set)
    secret_prefixes = [d.strip("/") + "/" for d in pol.secret_dirs]
    include_globs = [e["glob"] for e in pol.include if e.get("glob")]
    for rel in untracked:
        name = rel.rsplit("/", 1)[-1]
        if match_any(name, pol.env_names) and not match_any(name, pol.env_name_ignore):
            inv.add(rel, "env")
        elif any(rel.startswith(p) for p in secret_prefixes):
            inv.add(rel, "secret-dir")
        elif match_any(rel, include_globs):
            inv.add(rel, "include")
    # A link where a secret would be archived is never followed (it could point
    # anywhere) - and never silently dropped either: the secret behind it would
    # then have no backup and no warning.
    exclude_globs = [e["glob"] for e in pol.exclude]
    for rel in sorted(links):
        name = rel.rsplit("/", 1)[-1]
        if match_any(rel, exclude_globs):
            continue
        if (any(rel.startswith(p) for p in secret_prefixes)
                or (match_any(name, pol.env_names) and not match_any(name, pol.env_name_ignore))
                or match_any(rel, include_globs)):
            inv.problem("LINK", rel, "is a symlink/junction where a secret would be archived; links "
                        "are not followed - replace it with the real file, or [[exclude]] it with a reason")
    # literal includes may sit in a pruned tree (.claude/settings.local.json)
    for e in pol.include:
        lit = e.get("path")
        if lit and (root / lit).is_file() and lit not in tracked:
            inv.add(lit, "include")

    # 3. bind mounts of every plane whose own .env exists on this host
    manifest = manifest or (root / "stack.manifest.toml")
    planes = {}
    if manifest.is_file():
        with manifest.open("rb") as fh:
            planes = tomllib.load(fh).get("planes", {})
    else:
        inv.notes.append(f"no {manifest.name}: bind mounts not derived")
    renderer = renderer or default_renderer(root)
    outside_ok = {os.path.normcase(os.path.abspath(e["path"])): e["reason"] for e in pol.outside_ok}
    active_planes = set()
    for plane, spec in planes.items():
        if not plane_env_present(root, spec["compose"]):
            continue
        active_planes.add(plane)
        for label, rendered in renderer(plane, spec):
            if rendered is None:
                what = label if label.startswith("(") else "--profile " + " --profile ".join(
                    profile_closure(spec, label))
                inv.problem("RENDER", plane, f"`docker compose -f {spec['compose']} config` with {what} "
                            "failed; those bind mounts are not checked (re-run it by hand to see why)")
                continue
            for src in render_sources(rendered):
                _classify_source(root, Path(src), plane, pol, tracked, untracked, untracked_set,
                                 outside_ok, inv)

    # [[required]]
    for e in pol.required:
        rel = e["path"]
        plane = e.get("plane")
        if plane and plane not in active_planes:
            continue
        full = root / rel
        if full.is_file():
            inv.add(rel, "required")
        elif full.is_dir():
            inv.problem("MISSING", rel, "is a DIRECTORY where a file belongs (Docker creates one "
                        "when a bind source is absent) - " + e.get("why", ""))
        else:
            inv.problem("MISSING", rel, "required file is absent - " + e.get("why", ""))

    # [[exclude]]
    required_paths = {e["path"] for e in pol.required}
    for rel in list(inv.files):
        for e in pol.exclude:
            if fnmatch.fnmatchcase(rel, e["glob"]):
                if rel in required_paths:
                    inv.problem("POLICY", rel, f"is [[required]] and also matches [[exclude]] {e['glob']!r}")
                    break
                inv.excluded[rel] = e["reason"]
                del inv.files[rel]
                break

    # size cap
    for rel in list(inv.files):
        try:
            size = (root / rel).stat().st_size
        except OSError as exc:
            inv.problem("UNREADABLE", rel, f"cannot stat ({exc.__class__.__name__})")
            del inv.files[rel]
            continue
        if size > pol.max_file_bytes:
            inv.problem("OVERSIZE", rel, f"{size} bytes > max_file_bytes {pol.max_file_bytes}; "
                        "exclude it with a reason or raise the cap")
            del inv.files[rel]
    return inv


def _classify_source(root, src: Path, plane, pol, tracked, untracked, untracked_set, outside_ok, inv):
    rel = rel_of(root, src)
    name = src.name
    if rel is None:  # outside this checkout
        if src.is_file():
            key = os.path.normcase(os.path.abspath(src))
            if key not in outside_ok:
                inv.problem("OUTSIDE", str(src), f"plane {plane} bind-mounts this FILE from outside the "
                            "checkout; the job cannot archive it - add an [[outside]] entry with a reason "
                            "(who backs it up) or move it under the repo")
        elif not src.exists() and is_file_like(name, pol):
            inv.problem("MISSING", str(src), f"plane {plane} bind-mounts this absent file")
        return
    if rel == "":
        return
    if _is_link(src):
        inv.problem("LINK", rel, f"plane {plane} bind-mounts a symlink/junction; links are not "
                    "followed - mount the real file, or [[exclude]] it with a reason")
        return
    if src.is_file():
        if rel not in tracked:
            inv.add(rel, f"bind:{plane}")
        return
    if src.is_dir():
        try:
            empty = not any(src.iterdir())
        except OSError:
            empty = False
        if empty and is_file_like(name, pol) and rel not in tracked:
            inv.problem("MISSING", rel, f"plane {plane} bind-mounts it as a file but it is an EMPTY "
                        "DIRECTORY - Docker made it when the file was absent; restore the file")
            return
        prefix = rel + "/"
        for u in untracked:
            if u.startswith(prefix):
                inv.add(u, f"bind:{plane}")
        return
    # absent
    if is_file_like(name, pol) or rel in tracked:
        inv.problem("MISSING", rel, f"plane {plane} bind-mounts this file and it is absent - the next "
                    "container start turns it into an empty directory")


# --------------------------------------------------------------------------- recipients / age

def resolve_recipients(root: Path, pol: Policy, override: str | None) -> Path:
    raw = override or os.environ.get("CONFIG_SECRETS_BACKUP_RECIPIENTS") or pol.recipients_file
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        raise Refused(f"NO AGE RECIPIENT CONFIGURED: {path} does not exist. Nothing was backed up. "
                      "Install the operator's PUBLIC key there - "
                      "documentation/runbooks/config-secrets-backup.md, 'One-time setup'")
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        raise Refused(f"{path} starts with a byte-order mark (PowerShell 5.1 `-Encoding utf8`/`unicode` "
                      "writes one). Write it as plain ASCII (`Set-Content -Encoding ascii`). "
                      "Nothing was backed up.")
    text = raw.decode("utf-8", errors="replace")
    if "AGE-SECRET-KEY-" in text.upper():
        raise Refused(f"{path} holds an age PRIVATE key. This host must hold ONLY the public key "
                      "(age1...). Move the private key offline, delete it here, and put the "
                      "public key in this file. Nothing was backed up.")
    recipients = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not s.startswith("age1") or " " in s:
            raise Refused(f"{path} line {n} is not an age X25519 recipient (expected age1...). "
                          "Nothing was backed up.")
        recipients.append(s)
    if not recipients:
        raise Refused(f"NO AGE RECIPIENT CONFIGURED: {path} has no age1... line. Nothing was backed up.")
    return path


def resolve_age(root: Path, pol: Policy, override: str | None) -> Path:
    raw = override or os.environ.get("CONFIG_SECRETS_BACKUP_AGE") or pol.age_binary or "age"
    cand = Path(raw)
    if cand.is_absolute() or len(cand.parts) > 1:
        path = cand if cand.is_absolute() else root / cand
        if not path.is_file():
            raise Refused(f"age binary not found at {path}. Nothing was backed up.")
    else:
        found = shutil.which(raw)
        if not found:
            raise Refused(f"`{raw}` is not on PATH and no age binary is configured "
                          "(--age / CONFIG_SECRETS_BACKUP_AGE / age_binary). Nothing was backed up.")
        path = Path(found)
    digest = sha256_file(path)
    if digest not in pol.age_sha256:
        raise Refused(f"{path} (sha256 {digest}) is not a pinned age build ([[age_pin]] in the policy). "
                      "Install the pinned release (documentation/runbooks/config-secrets-backup.md) "
                      "or pin this build deliberately. Nothing was backed up.")
    return path


# --------------------------------------------------------------------------- archives

@dataclass
class ArchiveSet:
    name: str           # config-secrets-<ts>.tar.age
    path: Path

    @property
    def sha(self) -> Path:
        return self.path.with_name(self.name + SHA_SUFFIX)

    @property
    def listing(self) -> Path:
        return self.path.with_name(self.name + LIST_SUFFIX)

    def files(self) -> set[str]:
        try:
            lines = self.listing.read_text(encoding="utf-8").splitlines()
        except OSError:
            return set()
        return {ln for ln in lines if ln and not ln.startswith("#")}


def complete_sets(out_dir: Path) -> list[ArchiveSet]:
    """Archives with both sidecars, newest first (the UTC stamp in the name sorts)."""
    if not out_dir.is_dir():
        return []
    sets = []
    for p in out_dir.iterdir():
        if p.is_file() and p.name.startswith(PREFIX + "-") and p.name.endswith(ARCHIVE_SUFFIX):
            s = ArchiveSet(p.name, p)
            if s.sha.is_file() and s.listing.is_file():
                sets.append(s)
    return sorted(sets, key=lambda s: s.name, reverse=True)


def _age_reason(stderr: str) -> str:
    """age's own error lines, minus its 'report unexpected errors' footer. age never
    prints plaintext; it names files and recipients."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and "report unexpected" not in ln]
    return "; ".join(lines)[:1000]


def write_archive(root: Path, files, out_dir: Path, recipients: Path, age: Path, now: dt.datetime) -> ArchiveSet:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{PREFIX}-{now.strftime('%Y%m%dT%H%M%SZ')}{ARCHIVE_SUFFIX}"
    final = out_dir / name
    partial = out_dir / (name + PARTIAL_SUFFIX)
    proc = subprocess.Popen([str(age), "--encrypt", "--recipients-file", str(recipients), "--output", str(partial)],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        try:
            # mode "w|": a pure stream, nothing is seeked or staged. Each file is read
            # into memory (they are capped at max_file_bytes) and handed to age's stdin.
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for rel in sorted(files):
                    full = root / rel
                    data = full.read_bytes()
                    info = tarfile.TarInfo(rel)
                    info.size = len(data)
                    info.mtime = int(full.stat().st_mtime)
                    info.mode = 0o600
                    tar.addfile(info, io.BytesIO(data))
                    del data
            proc.stdin.close()
        except OSError as exc:
            # age died while we were writing (a bad recipient, killed, disk full): the
            # pipe error says nothing - age's own stderr says why.
            try:
                proc.stdin.close()
            except OSError:
                pass
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            rc = proc.wait(timeout=60)
            partial.unlink(missing_ok=True)
            raise Failed(f"age stopped reading (exit {rc}): {_age_reason(err) or exc}")
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        rc = proc.wait(timeout=300)
    except Failed:
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        partial.unlink(missing_ok=True)
        raise
    if rc != 0:
        partial.unlink(missing_ok=True)
        raise Failed(f"age exited {rc}: {_age_reason(err)}")
    with partial.open("rb") as fh:
        head = fh.read(len(AGE_MAGIC))
    if head != AGE_MAGIC:
        partial.unlink(missing_ok=True)
        raise Failed("age output does not start with the age header; refusing to keep it")
    os.replace(partial, final)
    s = ArchiveSet(name, final)
    s.sha.write_text(f"{sha256_file(final)}  {name}\n", encoding="ascii", newline="\n")
    s.listing.write_text("# paths in " + name + " (contents are encrypted; no hashes on purpose)\n"
                         + "".join(f"{r}\n" for r in sorted(files)), encoding="utf-8", newline="\n")
    return s


def prune(out_dir: Path, retain: int, log) -> None:
    sets = complete_sets(out_dir)
    keep, old = sets[:retain], sets[retain:]
    covered: set[str] = set()
    for s in keep:
        covered |= s.files()
    for s in old:
        held = s.files()
        unique = held - covered
        if unique:
            covered |= held
            log("WARN", f"kept {s.name} past retain_count={retain}: it holds the LAST copy of "
                        f"{len(unique)} file(s) no newer archive has: {', '.join(sorted(unique))}")
            continue
        for p in (s.path, s.sha, s.listing):
            p.unlink(missing_ok=True)
        log("INFO", f"pruned {s.name}")
    # A .partial is our own ciphertext from an interrupted run; never plaintext.
    for p in out_dir.glob(f"{PREFIX}-*{ARCHIVE_SUFFIX}{PARTIAL_SUFFIX}"):
        p.unlink(missing_ok=True)
        log("INFO", f"removed interrupted {p.name}")


# --------------------------------------------------------------------------- pin-ignores

def _gitignore_escape(rel: str) -> str:
    out = "".join("\\" + c if c in "\\[]*?" else c for c in rel)
    if out.endswith(" "):
        out = out[:-1] + "\\ "
    return "/" + out


class NotMainWorktree(Exception):
    """pin-ignores was asked to run in a LINKED worktree; it does not touch info/exclude."""


def _git_path(root: Path, flag: str) -> Path:
    proc = git(root, "rev-parse", flag)
    if proc.returncode != 0:
        raise Failed(f"`git rev-parse {flag}` failed (exit {proc.returncode})")
    p = Path(proc.stdout.decode("utf-8", "surrogateescape").strip())
    return p if p.is_absolute() else root / p


def is_main_worktree(root: Path) -> bool:
    """True in the main checkout (its git dir IS the common dir), False in a linked
    worktree (its git dir is <common>/worktrees/<name>)."""
    gd = os.path.normcase(os.path.realpath(_git_path(root, "--absolute-git-dir")))
    cd = os.path.normcase(os.path.realpath(_git_path(root, "--git-common-dir")))
    return gd == cd


def pin_ignores(root: Path, files) -> tuple[bool, list[str]]:
    """Pin every archived path that git IGNORES today into <common-dir>/info/exclude.
    info/exclude is not versioned, so it applies on EVERY branch: an old branch
    whose .gitignore predates a rule can no longer show the file as untracked, and a
    client that stashes untracked changes on a branch switch (GitHub Desktop's
    '!!GitHub_Desktop<branch>' stash, 2026-09-28) leaves it alone. Paths git does
    NOT ignore today (an uncommitted new file) are never pinned - that would hide
    work in progress. Returns (changed, pinned).

    ONLY THE MAIN CHECKOUT WRITES IT. info/exclude lives in the COMMON git dir and is
    shared by every worktree, and the block is replaced, not merged; a run from a
    linked worktree (whose inventory is its own copy of .env files) would otherwise
    drop the main checkout's pins (cfg-backup attempt 1). In a linked worktree this
    raises NotMainWorktree before reading or writing anything. Lines outside the
    marked block are never changed, and the file's line endings are kept."""
    if not is_main_worktree(root):
        raise NotMainWorktree(f"{root} is a linked worktree; info/exclude is shared by every "
                              "worktree and only the main checkout writes the pinned block - not touched")
    files = sorted(files)
    if not files:
        return False, []
    proc = git(root, "check-ignore", "-z", "--stdin", stdin="\0".join(files).encode("utf-8") + b"\0")
    if proc.returncode not in (0, 1):
        raise Failed(f"`git check-ignore` failed (exit {proc.returncode})")
    ignored = sorted(p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p)
    exclude = _git_path(root, "--git-common-dir") / "info" / "exclude"
    raw = exclude.read_bytes() if exclude.is_file() else b""
    try:
        old = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Failed(f"{exclude} is not UTF-8 (byte {exc.start}); it was NOT rewritten - fix the "
                     "file by hand, then re-run")
    eol = "\r\n" if "\r\n" in old else "\n"
    lines = old.splitlines()
    if PIN_BEGIN in lines and PIN_END in lines:
        b, e = lines.index(PIN_BEGIN), lines.index(PIN_END)
        before, after = lines[:b], lines[e + 1:]
    else:
        before, after = lines, []
    block = [PIN_BEGIN] + [_gitignore_escape(p) for p in ignored] + [PIN_END]
    new = eol.join(before + block + after) + eol
    if new == old:
        return False, ignored
    exclude.parent.mkdir(parents=True, exist_ok=True)
    tmp = exclude.with_name("exclude.config-secrets.tmp")
    tmp.write_bytes(new.encode("utf-8"))
    os.replace(tmp, exclude)
    return True, ignored


# --------------------------------------------------------------------------- logging

class Log:
    def __init__(self, logs_dir: Path | None):
        self.path = None
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            self.path = logs_dir / f"{LOG_PREFIX}{dt.date.today().isoformat()}.log"

    def __call__(self, level: str, msg: str) -> None:
        line = f"[{dt.datetime.now().astimezone().isoformat(timespec='seconds')}] [{level}] {msg}"
        print(line, flush=True)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# --------------------------------------------------------------------------- commands

def _report_problems(inv: Inventory, log) -> None:
    for kind, what, detail in inv.problems:
        log("ERROR", f"{kind} {what} - {detail}")


def cmd_run(args) -> int:
    root = Path(args.repo_root).resolve()
    log = Log(Path(args.logs_dir) if args.logs_dir else root / "logs")
    log("INFO", START_MARKER)
    try:
        return _run(args, root, log)
    except Exception as exc:  # noqa: BLE001 - a crash must leave an [ERROR] line, not just a traceback
        log("ERROR", f"run FAILED unexpectedly: {exc.__class__.__name__}: {exc}")
        return EXIT_FAILED


def _run(args, root: Path, log) -> int:
    try:
        pol = load_policy(Path(args.policy))
        recipients = resolve_recipients(root, pol, args.recipients)
        age = resolve_age(root, pol, args.age)
        inv = derive_inventory(root, pol)
    except Refused as exc:
        log("ERROR", f"REFUSED: {exc}")
        return EXIT_REFUSED
    out_dir = Path(args.backups_dir) if args.backups_dir else root / "backups" / SUBDIR
    _report_problems(inv, log)
    for note in inv.notes:
        log("WARN", note)
    if not inv.files:
        log("ERROR", "the inventory is EMPTY - nothing to archive; refusing to write an empty backup")
        return EXIT_FAILED
    now = dt.datetime.now(dt.timezone.utc)
    try:
        s = write_archive(root, inv.files, out_dir, recipients, age, now)
    except (Failed, OSError) as exc:
        log("ERROR", f"archive FAILED: {exc}")
        return EXIT_FAILED
    log("INFO", f"wrote {s.name} ({s.path.stat().st_size} bytes, {len(inv.files)} files, "
                f"{len(inv.excluded)} excluded) -> {out_dir}")
    prune(out_dir, pol.retain_count, log)
    pin_failed = False
    if pol.pin_ignores and not args.no_pin:
        try:
            changed, pinned = pin_ignores(root, inv.files)
            log("INFO", f"pin-ignores: {len(pinned)} path(s) pinned in info/exclude"
                        + (" (updated)" if changed else " (unchanged)"))
        except NotMainWorktree as exc:
            log("INFO", f"pin-ignores skipped: {exc}")
        except (Failed, OSError) as exc:
            log("ERROR", f"pin-ignores FAILED (the archive is fine): {exc}")
            pin_failed = True
    if pin_failed and not inv.problems:
        log("ERROR", "pin-ignores failed - the run is NOT complete")
        return EXIT_PROBLEMS
    if inv.problems:
        log("ERROR", f"{len(inv.problems)} problem(s) above - the archive was written but the run is "
                     "NOT complete")
        return EXIT_PROBLEMS
    log("INFO", SUCCESS_MARKER)
    return EXIT_OK


def coverage(root: Path, pol: Policy, out_dir: Path, recipients_override=None, renderer=None,
             tracked=None, manifest=None) -> tuple[Inventory, list]:
    """Inventory + the coverage gaps: [(KIND, path, detail)]. Empty gaps = clean."""
    inv = derive_inventory(root, pol, manifest=manifest, renderer=renderer, tracked=tracked)
    gaps = list(inv.problems)
    try:
        resolve_recipients(root, pol, recipients_override)
    except Refused as exc:
        gaps.append(("NOT-CONFIGURED", "age recipient", str(exc)))
    sets = complete_sets(out_dir)
    if not sets:
        for rel in sorted(inv.files):
            gaps.append(("UNCOVERED", rel, "no config-secrets archive exists"))
        return inv, gaps
    newest = sets[0]
    have = newest.files()
    for rel in sorted(inv.files):
        if rel not in have:
            gaps.append(("UNCOVERED", rel, f"not in the newest archive {newest.name}"))
    return inv, gaps


def cmd_check(args) -> int:
    root = Path(args.repo_root).resolve()
    try:
        pol = load_policy(Path(args.policy))
        out_dir = Path(args.backups_dir) if args.backups_dir else root / "backups" / SUBDIR
        inv, gaps = coverage(root, pol, out_dir, args.recipients)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return EXIT_REFUSED
    sets = complete_sets(out_dir)
    newest = sets[0] if sets else None
    print(f"  config+secrets inventory: {len(inv.files)} file(s), {len(inv.excluded)} excluded; "
          f"newest archive: {newest.name if newest else 'NONE'}")
    if newest:
        stamp = newest.path.stat().st_mtime
        changed = [r for r in sorted(inv.files) if (root / r).stat().st_mtime > stamp]
        for r in changed:
            print(f"  [INFO] {r} changed after the newest archive (the next nightly run picks it up)")
    for kind, what, detail in gaps:
        print(f"  [{kind}] {what} - {detail}")
    if gaps:
        print(f"  config+secrets coverage: {len(gaps)} GAP(S)")
        return EXIT_PROBLEMS
    print("  config+secrets coverage: CLEAN")
    return EXIT_OK


def cmd_inventory(args) -> int:
    root = Path(args.repo_root).resolve()
    try:
        pol = load_policy(Path(args.policy))
        inv = derive_inventory(root, pol)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        return EXIT_REFUSED
    for rel, tag in sorted(inv.files.items()):
        print(f"  {tag:<18} {rel}")
    for rel, reason in sorted(inv.excluded.items()):
        print(f"  {'excluded':<18} {rel} - {reason}")
    for kind, what, detail in inv.problems:
        print(f"  [{kind}] {what} - {detail}")
    for note in inv.notes:
        print(f"  [NOTE] {note}")
    return EXIT_PROBLEMS if inv.problems else EXIT_OK


def cmd_pin(args) -> int:
    root = Path(args.repo_root).resolve()
    try:
        pol = load_policy(Path(args.policy))
        inv = derive_inventory(root, pol)
        changed, pinned = pin_ignores(root, inv.files)
    except NotMainWorktree as exc:
        print(f"pin-ignores skipped: {exc}")
        return EXIT_OK
    except (Refused, Failed) as exc:
        print(f"REFUSED: {exc}")
        return EXIT_REFUSED
    print(f"pin-ignores: {len(pinned)} path(s) pinned" + (" (updated)" if changed else " (unchanged)"))
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["run", "check", "inventory", "pin-ignores"])
    ap.add_argument("--repo-root", default=str(DEFAULT_ROOT))
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--backups-dir", help="default: <repo-root>/backups/config-secrets")
    ap.add_argument("--logs-dir", help="default: <repo-root>/logs")
    ap.add_argument("--recipients", help="age recipients file (public keys); default from the policy")
    ap.add_argument("--age", help="age binary; default from the policy, else `age` on PATH")
    ap.add_argument("--no-pin", action="store_true", help="run: skip pin-ignores")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    return {"run": cmd_run, "check": cmd_check, "inventory": cmd_inventory,
            "pin-ignores": cmd_pin}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
