"""Workspace-edge filter for writes to `.git/` policy artifacts.

The git-proxy (design §3.3) catches `git config` / `git remote add` at the
command level, but a raw `echo "[remote] ..." >> .git/config` from `bash`
bypasses it. The design wants `.git/config`, `.git/hooks/`, and `.git/info/`
to be read-only at the kernel level (filesystem mount). That is "awkward for
a path inside a named volume" (plan open item #9), so its full closure is
deferred.

This filter is the **second** defense layer — symmetric with the git-proxy:
catch the obvious bash bypass patterns at the open-terminal edge and journal
the attempt. The rule shape is the same — pure `classify()` + a marker
recognised by the daemon (the existing `git-proxy: DENIED` substring).

Known residual gap: the agent's commands still run as root inside
open-terminal, so a determined attacker can reach `.git/config` via
`python -c '...write...'`, base64-encoded shell, or a heredoc routed
through a renamed util. This filter blocks the OBVIOUS paths
(redirects / `cp` / `mv` / `tee` / `sed -i` / `awk -i inplace` /
`perl -i`). Closing the residual gap requires either dropping
`CAP_DAC_OVERRIDE` from open-terminal (so chmod 0444 enforces) or a uid
split between setup and agent execution — both bigger than this layer.

`classify()` is a pure function so it is testable without a running
container (the test suite drives it like the git-proxy's `classify`).
"""

from __future__ import annotations

import dataclasses
import re

# Marker the daemon already greps for in stderr (see
# openterminal.OpenTerminalClient._GIT_PROXY_MARKER). Emitting the same
# prefix lets a denial flow through the existing `git_blocked` journal path
# with no plumbing change; the `rule` field carries the `ot-exec:` source so
# meta can later cluster the two denial origins separately.
DENY_MARKER = "git-proxy: DENIED"
EXIT_DENIED = 128  # match git_proxy.EXIT_DENIED — the agent already handles it


@dataclasses.dataclass(frozen=True)
class Decision:
    action: str  # "allow" | "deny"
    rule: str  # which detector fired (for journaling)
    reason: str  # human-readable explanation


# A locked path is `.git/config` (single file) or anything under `.git/hooks/`
# or `.git/info/`. Match with or without a leading `./` and inside common
# quoting so `'> ".git/config"'` is still caught. Trailing `\b` on `config`
# avoids matching `.git/configured-foo`.
_LOCKED = (
    r"""(?:["']?)(?:\./)?\.git/(?:config\b|hooks/|info/)"""
)

# 1. Shell redirect to a locked path. Covers `> path`, `>> path`, `>| path`,
#    and `| tee [-a] path`. The `\s*` is permissive — `>.git/config` (no
#    space) is a legal bash redirect and is a common evasion shape.
_REDIRECT_TO_LOCKED = re.compile(
    r"(?:>>?|>\|)\s*" + _LOCKED + r"|"
    r"\|\s*tee(?:\s+-a)?\s+" + _LOCKED,
    re.IGNORECASE,
)

# 2. Write-oriented command with a locked path as one of its arguments.
#    Targets `cp`, `mv`, `install`, `truncate`, `dd of=...`. The regex
#    intentionally matches anywhere in the command string so chained
#    invocations (`a && cp x .git/config`) are caught.
_WRITE_CMD_TO_LOCKED = re.compile(
    r"\b(?:cp|mv|install|truncate)\b[^;&|]*?" + _LOCKED + r"|"
    r"\bdd\b[^;&|]*\bof=" + _LOCKED,
    re.IGNORECASE,
)

# 3. In-place editors. `sed -i`, `awk -i inplace`, `perl -pi` / `perl -i`.
#    These overwrite the file in place — the locked path may appear after
#    the editor flags and any -e expressions.
_INPLACE_TO_LOCKED = re.compile(
    r"\b(?:sed\s+(?:-[A-Za-z]*i|--in-place)|"
    r"awk\s+-i\s+inplace|"
    r"perl\s+-\w*i\w*)\b[^;&|]*?" + _LOCKED,
    re.IGNORECASE,
)


def _deny(rule: str, reason: str) -> Decision:
    return Decision("deny", rule, reason)


def _allow(rule: str) -> Decision:
    return Decision("allow", rule, "")


def classify(command: str) -> Decision:
    """Decide whether `bash -c <command>` may run.

    Pure — no side effects, no I/O. The detector is conservative on the
    write side (high recall on obvious shapes) and permissive on reads —
    `cat .git/config | head` to inspect is intentionally allowed; the
    threat is tampering, not reading (deploy tokens are injected per-task
    via URL and never persisted into `.git/config`, design §10.3).
    """
    if not command or not command.strip():
        return _allow("empty-command")

    if _REDIRECT_TO_LOCKED.search(command):
        return _deny(
            "blocklist:redirect-to-git-artifact",
            "redirecting output into .git/config|hooks/|info/ is blocked — "
            "those paths are operator-controlled (design §3.3, open item #9)",
        )
    if _WRITE_CMD_TO_LOCKED.search(command):
        return _deny(
            "blocklist:write-cmd-to-git-artifact",
            "writing into .git/config|hooks/|info/ via cp/mv/install/dd is "
            "blocked — those paths are operator-controlled (design §3.3)",
        )
    if _INPLACE_TO_LOCKED.search(command):
        return _deny(
            "blocklist:inplace-edit-git-artifact",
            "in-place editing (sed -i / awk -i / perl -i) of "
            ".git/config|hooks/|info/ is blocked (design §3.3)",
        )
    return _allow("allow:no-locked-write")
