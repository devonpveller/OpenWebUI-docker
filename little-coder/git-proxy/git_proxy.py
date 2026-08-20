#!/usr/bin/env python3
"""git-proxy — the safety choke point (design §3.3).

Wraps every git call inside `open-terminal`. It is the SOLE git path the agent
has: in the workspace image, `git` on `$PATH` is a symlink to this file, and
the real binary lives at `$GIT_PROXY_REAL_GIT` (default /usr/bin/git). The
operator bypasses the proxy by design — `docker exec open-terminal /usr/bin/git`
calls the real binary directly (design §3.3).

Policy (design §3.3):
  - Deny by default. Only whitelisted subcommands run.
  - A blocklist gives precise, journaled errors for specific dangerous combos
    even within an otherwise-whitelisted subcommand (push --force, branch -D,
    remote add, ...).
  - `merge` must use --no-ff; `reset --hard` only to a tag; `fetch`/`push`
    only to operator-pre-configured remotes; no new remotes mid-task.
  - History rewrites, submodules, and anything touching `.git/` directly are
    off the table.

`classify()` is a pure function so the adversarial tests (task 1h) can drive
it without a real repo. `main()` wires it to the real git binary.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Callable

EXIT_DENIED = 128  # git's own "fatal" exit code — agents already handle it


@dataclasses.dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "deny"
    rule: str  # which rule fired (for journaling)
    reason: str  # human-readable explanation


# Subcommands that always run (read-only / introspection — no safety surface).
_READ_ONLY = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree",
    "cat-file", "blame", "describe", "shortlog", "name-rev", "for-each-ref",
    "var", "help", "rev-list", "merge-base", "check-ignore", "check-attr",
    "show-ref", "show-branch", "whatchanged", "count-objects", "grep",
    "fsck", "version", "range-diff", "diff-tree", "diff-files", "diff-index",
    "cherry", "verify-commit", "verify-tag",
}

# Subcommands that mutate the working tree / index but cross no safety
# boundary (the workspace is wipeable; the network plane is the boundary).
# Some carry guards — see _guard().
_WRITE = {
    "add", "rm", "mv", "restore", "commit", "checkout", "switch", "branch",
    "tag", "merge", "cherry-pick", "revert", "stash", "reset", "clean",
    "apply", "am", "notes", "fetch", "push", "remote", "gc", "config",
}

_WHITELIST = _READ_ONLY | _WRITE

# Explicitly denied subcommands — each gets a specific, journaled message.
# History rewrites, submodules, raw-plumbing into .git/, and anything that
# defeats the rollback story (design §3.3, §15).
_HARD_DENY = {
    "filter-branch": "history rewrite",
    "filter-repo": "history rewrite",
    "rebase": "history rewrite",
    "replace": "object replacement / history rewrite",
    "submodule": "submodules are off the table (design §3.3)",
    "worktree": "worktrees are off the model (one repo, one task — design §3.4)",
    "update-ref": "raw ref manipulation",
    "pack-refs": "raw ref manipulation",
    "symbolic-ref": "raw ref manipulation",
    "reflog": "reflog manipulation can erase rollback points",
    "prune": "object pruning can erase rollback points",
    "maintenance": "background maintenance can prune objects",
    "init": "workspace setup is an operator action (design §12.3)",
    "clone": "cloning is an operator action via /project (design §12.3)",
    "daemon": "no git server inside the workspace",
    "fast-import": "raw object import",
    "fast-export": "bulk history export",
    "instaweb": "no web server inside the workspace",
    "update-server-info": "server-side plumbing",
    "credential": "the agent never touches credentials",
    "credential-cache": "the agent never touches credentials",
    "credential-store": "the agent never touches credentials",
    "p4": "foreign SCM bridge",
    "svn": "foreign SCM bridge",
    "hook": "hook management is operator-controlled (design §3.3)",
}

# Global options (before the subcommand) that let a caller escape the repo or
# inject config — denied outright; the agent has no legitimate need for them.
_DENIED_GLOBALS_EXACT = {
    "-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--config-env", "--super-prefix",
}
_DENIED_GLOBALS_PREFIX = (
    "--git-dir=", "--work-tree=", "--namespace=", "--exec-path=",
    "--config-env=", "--super-prefix=",
)
# Harmless global flags that may precede the subcommand.
_BENIGN_GLOBALS = {
    "-p", "--paginate", "-P", "--no-pager", "--no-replace-objects", "--bare",
    "--literal-pathspecs", "--no-optional-locks", "--icase-pathspecs",
    "--glob-pathspecs", "--noglob-pathspecs", "--version", "--help",
    "--html-path", "--man-path", "--info-path",
}


def _deny(rule: str, reason: str) -> Decision:
    return Decision("deny", rule, reason)


def _allow(rule: str) -> Decision:
    return Decision("allow", rule, "")


def _find_subcommand(argv: list[str]) -> tuple[str | None, list[str], Decision | None]:
    """Walk leading global options. Returns (subcommand, rest, early_decision).
    `early_decision` is set when a global option is itself denied or when the
    invocation has no subcommand."""
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _DENIED_GLOBALS_EXACT or tok.startswith(_DENIED_GLOBALS_PREFIX):
            return None, [], _deny(
                "blocklist:global-override",
                f"global option {tok!r} can escape the repo or inject config",
            )
        if tok in _BENIGN_GLOBALS:
            i += 1
            continue
        if tok.startswith("-"):
            return None, [], _deny(
                "blocklist:unknown-global", f"unrecognized global option {tok!r}"
            )
        return tok, argv[i + 1 :], None
    # No subcommand — `git` alone, or only benign globals (e.g. --version).
    return None, [], _allow("allow:no-subcommand")


def _has(rest: list[str], *flags: str) -> bool:
    return any(r in flags for r in rest)


# `main`/`master` is the client-facing PRODUCTION branch — HUMAN-APPROVAL-GATED (operator 2026-07-11:
# "nothing should've been pushed to main without my approval … main is client facing, the production
# branch"). A worker must NEVER push to it (live breach: a host-context run pushed a submodule bump
# straight onto the host's `main`). `main` changes ONLY via the App's operator-approved PR merge.
# Workers push to `agent/*` (or ordinary dev) branches. `development`/feature branches are NOT protected.
_PROTECTED_BRANCHES = {"main", "master"}


def _protected_push_target(rest: list[str], current_branch: str | None) -> str | None:
    """The protected branch a `git push` would land on (`main`/`master`), else None. Covers explicit
    refspecs (`main`, `HEAD:main`, `refs/heads/main`) AND the bare/HEAD push (→ the checked-out
    branch). Conservative: any refspec whose DESTINATION is protected trips it, regardless of position."""
    positional = [t for t in rest if not t.startswith("-")]
    refspecs = positional[1:] if len(positional) >= 2 else []
    explicit = False
    for rs in refspecs:
        dst = rs.split(":")[-1] if ":" in rs else rs
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        if dst in ("HEAD", ""):
            dst = current_branch or ""
        if dst:
            explicit = True
            if dst in _PROTECTED_BRANCHES:
                return dst
    # bare `git push` / `git push <remote>` → the destination is the checked-out branch
    if not explicit and current_branch in _PROTECTED_BRANCHES:
        return current_branch
    return None


def _guard(
    sub: str,
    rest: list[str],
    configured_remotes: set[str],
    is_tag: Callable[[str], bool],
    current_branch: str | None = None,
) -> Decision:
    """Per-subcommand argument guards for whitelisted-but-dangerous commands."""

    if sub == "push":
        if _has(rest, "-f", "--force", "--force-with-lease", "--force-if-includes"):
            return _deny("blocklist:push-force", "force-push is blocked (design §3.3)")
        if _has(rest, "--mirror"):
            return _deny("blocklist:push-mirror", "mirror-push is blocked")
        if _has(rest, "-d", "--delete"):
            return _deny("blocklist:push-delete", "remote ref deletion is blocked")
        if _has(rest, "--all", "--tags-only"):
            return _deny("blocklist:push-all", "bulk push is blocked")
        # A refspec deleting a remote ref looks like ":branch".
        for tok in rest:
            if not tok.startswith("-") and tok.startswith(":"):
                return _deny("blocklist:push-delete", "refspec deletes a remote ref")
        # PRODUCTION-BRANCH GUARD: never let a worker push to main/master (human-gated).
        prot = _protected_push_target(rest, current_branch)
        if prot:
            return _deny(
                "blocklist:push-protected-branch",
                f"push to '{prot}' is blocked — it is the human-gated PRODUCTION branch; push to an "
                f"agent/* (or dev) branch instead. `main` changes only via an operator-approved PR.",
            )
        return _guard_remote_arg("push", rest, configured_remotes)

    if sub == "fetch":
        if _has(rest, "--all"):
            return _deny("blocklist:fetch-all", "fetch --all is blocked (design §3.3)")
        return _guard_remote_arg("fetch", rest, configured_remotes)

    if sub == "branch":
        if _has(rest, "-D", "-d", "--delete", "-M", "-f", "--force"):
            return _deny(
                "blocklist:branch-delete",
                "branch deletion / force-overwrite is blocked (design §3.3)",
            )
        return _allow("allow:branch")

    if sub == "tag":
        if _has(rest, "-d", "--delete", "-f", "--force"):
            return _deny(
                "blocklist:tag-delete",
                "tag deletion / force-overwrite is blocked (design §3.3)",
            )
        return _allow("allow:tag")

    if sub == "remote":
        positional = [t for t in rest if not t.startswith("-")]
        verb = positional[0] if positional else ""
        if verb in ("add", "set-url", "remove", "rm", "rename", "prune",
                    "set-head", "set-branches"):
            return _deny(
                "blocklist:remote-mutate",
                f"`remote {verb}` is blocked — remotes are operator-baked "
                "at project-switch time (design §3.3)",
            )
        return _allow("allow:remote-read")

    if sub == "merge":
        if _has(rest, "--abort", "--continue", "--quit"):
            return _allow("allow:merge-control")
        if not _has(rest, "--no-ff"):
            return _deny(
                "blocklist:merge-ff",
                "merge must use --no-ff (design §3.3) — it preserves the "
                "merge commit for traceability and rollback",
            )
        return _allow("allow:merge")

    if sub == "reset":
        if not _has(rest, "--hard"):
            return _allow("allow:reset-soft")  # soft/mixed: no working-tree loss
        refs = [t for t in rest if not t.startswith("-")]
        if not refs:
            return _deny(
                "blocklist:reset-hard",
                "reset --hard needs an explicit tag target (design §3.3)",
            )
        target = refs[-1]
        if not is_tag(target):
            return _deny(
                "blocklist:reset-hard",
                f"reset --hard only to a tag; {target!r} is not a tag (design §3.3)",
            )
        return _allow("allow:reset-hard-tag")

    if sub == "gc":
        for tok in rest:
            if tok in ("--prune=now", "--prune=all"):
                return _deny(
                    "blocklist:gc-prune", "gc --prune=now erases rollback objects"
                )
        return _allow("allow:gc")

    if sub == "config":
        reads = ("--get", "--get-all", "--get-regexp", "--get-urlmatch",
                 "--list", "-l")
        if any(r in reads for r in rest):
            return _allow("allow:config-read")
        return _deny(
            "blocklist:config-write",
            "git config writes are blocked — .git/config is read-only to the "
            "agent (design §3.3)",
        )

    if sub == "commit":
        if _has(rest, "--amend"):
            return _deny(
                "blocklist:commit-amend",
                "commit --amend rewrites history (design §3.3)",
            )
        return _allow("allow:commit")

    return _allow(f"allow:{sub}")


def _guard_remote_arg(
    sub: str, rest: list[str], configured_remotes: set[str]
) -> Decision:
    """`fetch`/`push` may only target an operator-pre-configured remote, named
    (never a bare URL), and never `--all` (design §3.3)."""
    positional = [t for t in rest if not t.startswith("-")]
    if not positional:
        # No remote named → git uses the default upstream, which is itself a
        # configured remote. Allowed.
        return _allow(f"allow:{sub}-default-remote")
    candidate = positional[0]
    if "://" in candidate or candidate.startswith("git@") or candidate.endswith(".git"):
        return _deny(
            f"blocklist:{sub}-url",
            f"{sub} to an ad-hoc URL is blocked — only named, "
            "operator-configured remotes (design §3.3)",
        )
    if candidate not in configured_remotes:
        return _deny(
            f"blocklist:{sub}-remote",
            f"{candidate!r} is not an operator-configured remote "
            f"(known: {sorted(configured_remotes) or 'none'})",
        )
    return _allow(f"allow:{sub}")


def classify(
    argv: list[str],
    configured_remotes: set[str] | None = None,
    is_tag: Callable[[str], bool] | None = None,
    current_branch: str | None = None,
) -> Decision:
    """Decide whether `git <argv>` may run. Pure — no side effects."""
    configured_remotes = configured_remotes or set()
    is_tag = is_tag or (lambda _ref: False)  # fail closed: nothing is a tag

    sub, rest, early = _find_subcommand(argv)
    if early is not None:
        return early
    assert sub is not None

    if sub in _HARD_DENY:
        return _deny(f"blocklist:{sub}", f"`git {sub}` is blocked — {_HARD_DENY[sub]}")
    if sub not in _WHITELIST:
        return _deny(
            "not-whitelisted",
            f"`git {sub}` is not on the whitelist (design §3.3 — deny by default)",
        )
    if sub in _READ_ONLY:
        return _allow(f"allow:read-only:{sub}")
    return _guard(sub, rest, configured_remotes, is_tag, current_branch)


# --------------------------------------------------------------------------
# main() — wire classify() to the real git binary.
# --------------------------------------------------------------------------


def _real_git() -> str:
    return os.environ.get("GIT_PROXY_REAL_GIT", "/usr/bin/git")


def _current_branch(real_git: str) -> str | None:
    """The checked-out branch name — for the production-branch push guard (a bare `git push` on
    `main` must be denied). None on detached HEAD / any query failure; an EXPLICIT `push … main`
    is still caught by refspec, so detection failure never opens the protected branch."""
    try:
        out = subprocess.run(
            [real_git, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        name = (out.stdout or "").strip()
        return name if name and name != "HEAD" else None
    except (OSError, subprocess.SubprocessError):
        return None


def _configured_remotes(real_git: str) -> set[str]:
    """The set of remotes baked into .git/config. Fails closed: if git can't
    be queried, the set is empty and every remote-targeting op is denied."""
    try:
        out = subprocess.run(
            [real_git, "remote"], capture_output=True, text=True, timeout=10
        )
        if out.returncode != 0:
            return set()
        return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()


def _make_is_tag(real_git: str) -> Callable[[str], bool]:
    def is_tag(ref: str) -> bool:
        try:
            r = subprocess.run(
                [real_git, "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False  # fail closed

    return is_tag


def _journal_denial(argv: list[str], decision: Decision) -> None:
    """Append the blocked attempt to the in-plane proxy log. The daemon also
    turns a denial (recognized in stderr) into an `errors.jsonl` record."""
    log_path = os.environ.get("GIT_PROXY_LOG", "/var/log/git-proxy.log")
    from datetime import datetime, timezone

    line = (
        f"{datetime.now(timezone.utc).isoformat()} DENIED rule={decision.rule} "
        f"argv={argv!r} reason={decision.reason}\n"
    )
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass  # the stderr marker below is the primary signal


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    real_git = _real_git()

    # Only query the real repo for the commands that need it — keeps the
    # common path (status/diff/...) from paying two extra subprocess calls.
    sub, _, _ = _find_subcommand(argv)
    needs_repo = sub in {"fetch", "push", "reset"}
    remotes = _configured_remotes(real_git) if needs_repo else set()
    is_tag = _make_is_tag(real_git) if needs_repo else (lambda _r: False)
    # The checked-out branch — only needed to guard a bare `git push` on main/master.
    current_branch = _current_branch(real_git) if sub == "push" else None

    decision = classify(argv, remotes, is_tag, current_branch)
    if decision.action == "deny":
        _journal_denial(argv, decision)
        # The "git-proxy: DENIED" marker is what the daemon greps for.
        sys.stderr.write(f"git-proxy: DENIED ({decision.rule}) — {decision.reason}\n")
        return EXIT_DENIED

    # Allowed — hand off to the real binary. execv replaces this process so
    # git's exit code reaches the caller unchanged.
    try:
        os.execv(real_git, [real_git, *argv])
    except OSError as exc:  # pragma: no cover - real git should always exist
        sys.stderr.write(f"git-proxy: cannot exec real git at {real_git}: {exc}\n")
        return EXIT_DENIED


if __name__ == "__main__":
    raise SystemExit(main())
