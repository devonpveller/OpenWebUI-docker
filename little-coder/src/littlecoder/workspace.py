"""Workspace handling + project focus (design §3.4, §12.3).

open-terminal hosts one focused project at a time, cloned directly into the
workspace volume — no worktrees, no per-task subdirectories. Switching is an
explicit operator action; the decision logic below is `decide_switch()`, kept
pure so the §12.3 branch table is testable.

The repo clones into open-terminal but the workspace volume is shared, so the
control plane reads the filesystem directly for cheap checks (is it focused?)
and routes git / wipe / clone through open-terminal (the network plane).
"""

from __future__ import annotations

import dataclasses
import os
import shlex
from enum import Enum

from .openterminal import ExecResult, OpenTerminalClient
from .urlnorm import NormalizedRepo


_EXT_LANG = {
    ".py": "python", ".rs": "rust", ".go": "go", ".js": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".java": "java", ".rb": "ruby", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp", ".php": "php",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".sh": "shell",
    ".lua": "lua", ".ex": "elixir", ".exs": "elixir",
}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "target", "dist", "build"}


def detect_primary_language(workspace_path: str) -> str:
    """Best-effort primary language for the journal envelope (design §4.1).
    Counts source-file extensions; returns "" when nothing is recognizable."""
    counts: dict[str, int] = {}
    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            lang = _EXT_LANG.get(os.path.splitext(name)[1].lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    return max(counts, key=counts.get) if counts else ""


class SwitchAction(str, Enum):
    CLONE = "clone"  # no current focus → clone and set focus
    NOOP = "noop"  # already focused on this repo → proceed
    REJECT = "reject"  # different repo but a task is in flight → reject
    SWITCH = "switch"  # different repo, workspace clear → tag, wipe, clone


@dataclasses.dataclass(frozen=True)
class SwitchDecision:
    action: SwitchAction
    requested: NormalizedRepo
    reason: str


def decide_switch(
    requested: NormalizedRepo,
    current: NormalizedRepo | None,
    task_in_flight: bool,
) -> SwitchDecision:
    """The /project decision table (design §12.3). Pure — the daemon executes
    the returned action. URL normalization upstream guarantees the SSH and
    HTTPS forms of one repo compare equal, so this never wipes spuriously."""
    if current is None:
        return SwitchDecision(SwitchAction.CLONE, requested, "no current focus")
    if requested.focus_key == current.focus_key:
        return SwitchDecision(
            SwitchAction.NOOP, requested, "already focused on this repo"
        )
    if task_in_flight:
        return SwitchDecision(
            SwitchAction.REJECT,
            requested,
            "a task is in flight — cancel it or wait, then retry the switch",
        )
    return SwitchDecision(
        SwitchAction.SWITCH, requested, "different repo, workspace clear"
    )


class WorkspaceManager:
    """Filesystem + git operations on the focused project. Clone/wipe/tag run
    inside open-terminal; cheap state checks read the shared volume directly."""

    def __init__(
        self,
        ot_client: OpenTerminalClient,
        workspace_path: str = "/workspace",
        real_git: str = "/usr/bin/git.real",
        clone_timeout: int = 1800,
    ) -> None:
        self.ot = ot_client
        self.workspace_path = workspace_path
        # The real git binary — clone at project-switch time is an operator
        # action and bypasses the proxy by design (design §3.3). The custom
        # open-terminal image relocates real git here; `git` on $PATH is the
        # proxy.
        self.real_git = real_git
        self.clone_timeout = clone_timeout

    def is_focused(self) -> bool:
        """True when a repo is currently cloned. The workspace volume is
        shared, so this is a direct filesystem check."""
        return os.path.isdir(os.path.join(self.workspace_path, ".git"))

    def clone(self, repo: NormalizedRepo, deploy_token: str | None = None) -> ExecResult:
        """Clone `repo` into the (empty) workspace. With `deploy_token` the
        clone uses an HTTPS token URL — least-privilege, injected per switch,
        never the self-improvement PAT (design §10.3).

        Full-history clone (no `--depth 1`): the agent needs all branches +
        history to switch branches, `git log` past initial commit, and
        inspect prior work. Disk is cheap relative to re-cloning. If
        `repo.branch` is set (via the `#<branch>` link fragment), git's
        `-b <branch>` checks it out as HEAD; otherwise the remote's default
        branch is used.

        The returned ExecResult.command still contains the token; the caller
        MUST journal a redacted form, never the raw command."""
        url = repo.canonical_url
        if deploy_token:
            url = url.replace(
                "https://", f"https://x-access-token:{deploy_token}@", 1
            )
        branch_flag = ""
        if repo.branch:
            branch_flag = f" -b {shlex.quote(repo.branch)}"
        # umask 000 so the clone is usable from the agent's other plane too
        # (the workspace volume is shared across two containers' uids).
        cmd = (
            f"umask 000; {shlex.quote(self.real_git)} clone{branch_flag} "
            f"{shlex.quote(url)} {shlex.quote(self.workspace_path)}"
        )
        return self.ot.execute(cmd, cwd="/", timeout=self.clone_timeout)

    def wipe(self) -> ExecResult:
        """Empty the workspace, keeping the mount point itself. open-terminal
        owns the files it created, so the wipe runs there."""
        cmd = f"find {shlex.quote(self.workspace_path)} -mindepth 1 -delete"
        return self.ot.execute(cmd, cwd="/", timeout=300)

    def tag_prior_state(self, label: str) -> ExecResult:
        """Tag the focused project's current HEAD before a wipe (design
        §12.3) and push the tag best-effort, so the prior state stays
        recoverable from the remote. A push failure is logged, not fatal."""
        # Runs through the git-proxy: `tag` and `push <tag>` are whitelisted.
        cmd = (
            f"git tag {shlex.quote(label)} && "
            f"git push origin {shlex.quote(label)} || "
            f'echo "tag-push skipped (no push access)"'
        )
        return self.ot.execute(cmd, cwd=self.workspace_path, timeout=120)
