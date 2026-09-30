"""Scrub credentials out of the git config files in a little-coder workspace (cf-lc-token).

Until 2026-09-28 little-coder spliced its deploy token into remote URLs
(`https://x-access-token:<token>@github.com/...`), so every clone it made carried the token in
`.git/config` - inside the workspace VOLUME, where a volume copy or a backup takes it along. The
code no longer does that (workspace.py keeps the token in the executor's credential store); this
one-shot rewrites what earlier clones left behind.

What it does, per git config file under ROOT (a repository's `.git/config`, a submodule's
`.git/modules/**/config`, a linked worktree's config): removes the `user[:secret]@` part from every
http(s) URL, in place. Nothing else in the file changes. It never copies a token anywhere - pushes
authenticate again once the daemon re-stores the credential (every /project and every task).

It prints WHAT it changed (file, how many URLs) and never a value. Idempotent: a second run finds
nothing and says so. `.gitmodules` files (tracked content) are REPORTED, not rewritten - changing
them is a commit, which is the repository owner's call.

Stdlib only, so it runs wherever a python3 and the volume meet - including the open-terminal
executor, which owns the files:

    docker exec -i open-terminal python3 - /workspace < little-coder/src/littlecoder/credscrub.py

Exit codes: 0 = done (changed or already clean), 2 = ROOT is not a directory.
"""

from __future__ import annotations

import os
import re
import sys

# `scheme://userinfo@` in an http(s) URL. userinfo cannot contain `/`, `@` or whitespace; stopping
# at a quote keeps a quoted section header (`[url "https://t@host/"]`) intact apart from the userinfo.
_USERINFO = re.compile(r"(?i)\b(https?://)[^/@\s\"']+@")


def scrub_text(text: str) -> tuple[str, int]:
    """Return `text` with userinfo removed from every http(s) URL, and how many were removed."""
    return _USERINFO.subn(r"\1", text)


def _is_git_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "HEAD")) and os.path.isfile(
        os.path.join(path, "config"))


def find_git_configs(root: str) -> tuple[list[str], list[str]]:
    """(git config files, .gitmodules files) under `root`. A git config file is the `config` of a
    directory that is a git dir (has HEAD + config) and sits at or below a `.git` path component -
    that covers `.git/`, `.git/modules/**` and `.git/worktrees/*`. Symlinks are not followed."""
    configs: list[str] = []
    gitmodules: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        parts = os.path.normpath(os.path.relpath(dirpath, root)).split(os.sep)
        if ".git" in parts and "config" in filenames and _is_git_dir(dirpath):
            configs.append(os.path.join(dirpath, "config"))
        if ".gitmodules" in filenames and ".git" not in parts:
            gitmodules.append(os.path.join(dirpath, ".gitmodules"))
    return sorted(configs), sorted(gitmodules)


def scrub(root: str, dry_run: bool = False, out=None) -> int:
    """Scrub every git config under `root`; return the number of URLs changed."""
    out = out or sys.stdout
    configs, gitmodules = find_git_configs(root)
    changed = 0
    for path in configs:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
            text = fh.read()
        new, n = scrub_text(text)
        if not n:
            continue
        changed += n
        rel = os.path.relpath(path, root)
        verb = "would remove" if dry_run else "removed"
        print(f"{verb} credentials from {n} URL(s): {rel}", file=out)
        if not dry_run:
            # In place (same inode): keeps the file's owner and mode, which a replace would not.
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                fh.write(new)
    for path in gitmodules:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            n = len(_USERINFO.findall(fh.read()))
        if n:
            print(f"WARNING: tracked file carries credentials in {n} URL(s), NOT rewritten "
                  f"(a commit is the owner's call): {os.path.relpath(path, root)}", file=out)
    print(f"{len(configs)} git config file(s) checked under {root}; "
          f"{changed} credential URL(s) {'found' if dry_run else 'removed'}", file=out)
    return changed


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    root = args[0] if args else "/workspace"
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    scrub(root, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
