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


# --- HTTPS git credentials: never in a remote URL (cf-lc-token, 2026-09-28) ----------------------
# Until 2026-09-28 every token-bearing path below spliced the token INTO the remote URL
# (`https://x-access-token:<token>@host/...`), so it was written into `.git/config` (and, for a
# private submodule, into the TRACKED `.gitmodules`) inside the shared workspace VOLUME - where any
# volume copy or backup carried it. The audit of 2026-09-27 found live `github_pat_` tokens there.
#
# Now every remote URL is token-free. The token reaches the executor only in the ENVIRONMENT of the
# one exec that needs it (`LC_GIT_TOKEN`, open-terminal's per-exec `env`, so it is not in the
# command string either), and that exec hands it to git's stock `credential-store` helper, whose
# file lives in the executor's container-local HOME (`/home/user` in open-terminal, not a volume in
# either compose file) - never under /workspace. `credential.useHttpPath` keys each entry on the
# full repo URL, so origin, a fork's read-only upstream and each submodule keep their own token.
# The helper is configured in the exec user's GLOBAL git config (also in HOME), so the worker's own
# `git push` through the git-proxy finds it without the agent ever touching a credential command.
# The file does not survive a recreate of the executor; the daemon re-asserts it on every focus and
# before every task (daemon.py `_ensure_git_credentials`).
GIT_TOKEN_ENV = "LC_GIT_TOKEN"
GIT_CRED_FILE = "$HOME/.lc-git-credentials"   # expanded by the executor's shell, never /workspace
GIT_CRED_USER = "x-access-token"
# Strip `user[:secret]@` from an http(s) URL (sed -E); used on submodule origins and by the scrub.
_SED_STRIP_USERINFO = r"s#^(https?://)[^/@]*@#\1#"


def _cred_config(g: str) -> str:
    """Shell: point the exec user's global git config at the credential-store file (idempotent)."""
    return (
        f"{g} config --global --replace-all credential.helper "
        f"\"store --file={GIT_CRED_FILE}\" && "
        f"{g} config --global credential.useHttpPath true"
    )


def _cred_store(g: str, url_expr: str) -> str:
    """Shell: store `$LC_GIT_TOKEN` for the repo at `url_expr` (a shell word that expands to a
    token-free URL). The token is read from the environment by the shell builtin `printf`, so it is
    never in an argv; credential-store replaces any older entry for the same URL (token refresh)."""
    return (
        f"(umask 077; printf 'url=%s\\nusername={GIT_CRED_USER}\\npassword=%s\\n\\n' "
        f"{url_expr} \"${GIT_TOKEN_ENV}\" | {g} credential-store --file=\"{GIT_CRED_FILE}\" store)"
    )


def _cred_has(g: str, url_expr: str) -> str:
    """Shell condition: the credential store already holds a password for `url_expr`. Asks
    credential-store itself (`get`), so matching is git's own (path included); the answer is
    consumed by `grep -q` and never printed."""
    return (
        f"printf 'url=%s\\n\\n' {url_expr} | {{ {g} credential-store --file=\"{GIT_CRED_FILE}\" get "
        f"2>/dev/null; }} | grep -q '^password='"
    )


def _submodule_cred_script(g: str) -> str:
    """The per-submodule script for `git submodule foreach`: make the submodule's origin token-free
    (it may still carry a token from before 2026-09-28) and, for a same-host GitHub origin, store the
    caller's token for it. Runs in a child shell, so `$LC_GIT_TOKEN` arrives through the env."""
    c_word = '"$c"'
    return (
        f"u=$({g} config --get remote.origin.url 2>/dev/null); "
        f"c=$(printf '%s' \"$u\" | sed -E '{_SED_STRIP_USERINFO}'); "
        f"case \"$c\" in https://github.com/*) "
        f"{g} remote set-url origin \"$c\" && {_cred_store(g, c_word)} ;; esac"
    )


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
        """True when a REAL repo is currently cloned. The workspace volume is shared, so this is a
        direct filesystem check. Requires `.git/HEAD` — not just a `.git` directory — because a
        crashed/partial clone can leave a `.git` holding only `modules/` (submodule gitdirs) with
        no main-repo data (live 2026-07-10: such a `.git` made `git` itself report "not a git
        repository", yet the old `isdir('.git')` check said focused → the daemon NOOP'd onto a
        broken tree and every check failed with MSB1009). A missing HEAD ⇒ re-clone."""
        return os.path.isfile(os.path.join(self.workspace_path, ".git", "HEAD"))

    def has_remote(self, name: str) -> bool:
        """True when `name` is a configured git remote. `git remote` is a
        read-only, proxy-allowed op — used to skip an idempotent re-bake of
        `upstream` on a NOOP re-focus (the workspace wasn't wiped, so a remote
        already present needn't be re-added). Fails closed: any error → False
        (treat as absent → the caller re-bakes, which is idempotent anyway)."""
        res = self.ot.execute(
            f"{shlex.quote(self.real_git)} -C {shlex.quote(self.workspace_path)} remote",
            cwd=self.workspace_path,
            timeout=30,
        )
        if not res.ok:
            return False
        return name in {ln.strip() for ln in res.stdout.splitlines() if ln.strip()}

    def _token_env(self, token: str | None) -> dict[str, str] | None:
        """The per-exec environment that carries a token to the executor, or None without one."""
        return {GIT_TOKEN_ENV: token} if token else None

    def clone(self, repo: NormalizedRepo, deploy_token: str | None = None,
              recurse: bool = False) -> ExecResult:
        """Clone `repo` into the (empty) workspace. With `deploy_token` the clone
        authenticates over HTTPS through the credential store (see GIT_TOKEN_ENV above) —
        least-privilege, injected per switch, never the self-improvement PAT (design §10.3).
        The remote URL written to `.git/config` is the token-free canonical URL.

        Full-history clone (no `--depth 1`): the agent needs all branches +
        history to switch branches, `git log` past initial commit, and
        inspect prior work. Disk is cheap relative to re-cloning. If
        `repo.branch` is set (via the `#<branch>` link fragment), git's
        `-b <branch>` checks it out as HEAD; otherwise the remote's default
        branch is used.

        `recurse`: populate the FULL nested submodule tree (`--init --recursive`)
        via the privileged real-git path — a COMPOSITION BUILD needs the deep
        tree (engine → vendored fork → the fork's OWN submodules), which the
        worker cannot init itself (the git-proxy hard-denies `submodule`) and
        which a direct-only init misses. Off by default (recursing forks' deep
        deps is slow / can hit private repos); the bridge opts in for a
        composition check.

        The token travels in the exec's `env`, never in the command string, so
        ExecResult.command carries no token."""
        url = repo.canonical_url
        branch_flag = ""
        if repo.branch:
            branch_flag = f" -b {shlex.quote(repo.branch)}"
        g = shlex.quote(self.real_git)
        ws = shlex.quote(self.workspace_path)
        # umask 000 so the clone is usable from the agent's other plane too
        # (the workspace volume is shared across two containers' uids). Then POPULATE any
        # submodules (non-fatal) — a composition repo's worker must SEE the vendored source to
        # reference it, and the worker itself can't init them (the git-proxy hard-denies `submodule`).
        # `|| true`: a private/unreachable submodule must not fail the whole focus.
        recurse_flag = " --recursive" if recurse else ""
        # CLONE means START FRESH. `/workspace` is a PERSISTENT shared mount that can hold a corrupt
        # partial clone from an interrupted/failed focus (a `.git` with no HEAD + leftover dirs) —
        # `git clone` into a non-empty dir fails "destination path already exists and is not an empty
        # directory" (live 2026-07-10: this exact exit-128 wedged a composition focus and the effort
        # sat silent ~2h). Wipe the CONTENTS first (keep the mount point), then clone. Safe: a CLONE
        # is only decided when there's nothing to preserve (no focus, or switching repos).
        # chmod first: leftover BUILD ARTIFACTS can be owned by a different uid than the exec user
        # (live 2026-07-14: dotnet `bin/Debug` files under vendor/ were undeletable → the wipe
        # silently left 379M behind → clone failed 'destination not empty'). u+w only helps
        # own files; the durable cross-uid heal is the worker entrypoint's recursive chmod.
        wipe_ws = (f"chmod -R u+w {ws} 2>/dev/null; "
                   f"find {ws} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} + 2>/dev/null; "
                   f"find {ws} -mindepth 1 -delete 2>/dev/null || true")
        # With a token, store it for this URL BEFORE the clone (the store is what the clone
        # authenticates with); the clone URL itself stays token-free.
        creds = ""
        if deploy_token:
            creds = f"{_cred_config(g)} && {_cred_store(g, shlex.quote(url))}; "
        cmd = (
            # THE CLONE'S EXIT CODE IS THE RESULT — captured in `rc` and re-raised by the trailing
            # `exit $rc`, so no best-effort suffix can mask a failed clone (live 2026-07-14: the
            # token-reauth suffix ended `|| true`, a failed clone reported ok=True in 0.3s, the
            # daemon claimed focus on a VOID workspace, and the bridge quarantine-looped both
            # workers for hours with an idle GPU — a silent false "ok" at the very bottom of the
            # stack defeated every honesty gate above it).
            f"{wipe_ws}; {creds}umask 000; {g} clone{branch_flag} {shlex.quote(url)} {ws}; rc=$?; "
            # Default: DIRECT submodules only. `recurse`: the full nested tree, which a composition
            # build requires — the operator-privileged clone is the ONLY place `submodule` can run
            # (the proxy denies it to the worker), so recursive init MUST happen here or never.
            f"if [ $rc -eq 0 ]; then "
            f"(cd {ws} && {g} submodule update --init{recurse_flag} 2>/dev/null || true); fi"
        )
        if deploy_token:
            # WORK-IN-HOST delivery: a composition fix is edited in-place inside a vendored
            # submodule and must be PUSHED to THAT submodule's own remote. The proxy denies the
            # worker `submodule`, so store the deploy token for every populated same-host submodule
            # origin here (privileged). Runs on EVERY focus with a token — NOT just recursive ones
            # (2026-07-12): a composition fix happens on a NORMAL task focus (non-recursive) too, and
            # without it the worker's submodule push has no credential and the engine's gitlink
            # points at an unreachable commit (live: the atlas fix landed on the engine but its murder
            # branch couldn't push). `foreach --recursive` only visits the submodules actually
            # populated by this focus (direct-only on a task focus). Best-effort + non-fatal.
            reauth = (
                f"cd {ws} && {g} submodule foreach --recursive "
                f"{shlex.quote(_submodule_cred_script(g))} 2>/dev/null || true"
            )
            # Gated on the clone's rc and never the last word on the exit code — the reauth's
            # `|| true` must not bless a failed clone (the 2026-07-14 false-focus incident).
            cmd += f" ; if [ $rc -eq 0 ]; then ({reauth}); fi"
        cmd += " ; exit $rc"
        return self.ot.execute(cmd, cwd="/", env=self._token_env(deploy_token),
                               timeout=self.clone_timeout)

    def refresh_origin_auth(
        self, repo: NormalizedRepo, deploy_token: str | None, *, if_missing: bool = False
    ) -> ExecResult:
        """Re-store `origin`'s credential with a FRESH deploy token (real git — operator setup path,
        like `clone`/`add_upstream_remote`). The token given at clone time is SHORT-LIVED (a GitHub App
        installation token lives 1h): a NOOP re-focus hours later, or a task that outlives the token,
        would `git push` with a dead credential — the live "expired token in origin" failure. Cheap +
        idempotent. The credential store lives in the executor's HOME, which a recreate of the
        executor empties — so the daemon also calls this before every task (`_ensure_git_credentials`).

        What it cleans of a clone made before 2026-09-28 (token in the URL): it ALWAYS resets
        `origin` to the token-free URL. Submodule origins are reset only WITH a token (the submodule
        step runs only then), and the `upstream` remote is never touched here. So a re-focus without
        a token leaves legacy submodule/upstream tokens in place; the landing scrub (credscrub.py)
        is what removes those.

        `if_missing`: store the token only when the store holds NO credential for origin's URL yet
        (and then the submodules' too). The daemon uses it for a focus it SEEDED from disk after its
        own restart, where it does not know the caller's token: an existing entry (e.g. a GitHub App
        token from agent-bridge's last /project) is kept rather than overwritten with the env PAT."""
        url = repo.canonical_url
        g = shlex.quote(self.real_git)
        q = shlex.quote
        ws = q(self.workspace_path)
        cmd = f"cd {ws} && {g} remote set-url origin {q(url)}"
        if deploy_token and if_missing:
            reauth = (
                f"cd {ws} && {g} submodule foreach --recursive "
                f"{shlex.quote(_submodule_cred_script(g))} 2>/dev/null || true"
            )
            cmd += (f" && if ! {_cred_has(g, q(url))}; then "
                    f"{_cred_config(g)} && {_cred_store(g, q(url))} ; ({reauth}); fi")
        elif deploy_token:
            cmd += f" && {_cred_config(g)} && {_cred_store(g, q(url))}"
            # SYMMETRIC WITH clone() (live 2026-07-12: the cursor fix's murder commit couldn't push).
            # A composition fix edited inside a vendored submodule must be PUSHED to THAT submodule's
            # own remote — but a NOOP re-focus (persistent workspace, no re-clone) never re-runs
            # clone()'s submodule step, so the worker's `git -C <sub> push` would have no credential
            # → the engine's gitlink points at an unreachable commit (broken gitlink → not buildable
            # from a fresh clone). `foreach --recursive` visits only the submodules this focus
            # populated. Best-effort + non-fatal.
            reauth = (
                f"cd {ws} && {g} submodule foreach --recursive "
                f"{shlex.quote(_submodule_cred_script(g))} 2>/dev/null || true"
            )
            cmd += f" ; ({reauth})"
        return self.ot.execute(cmd, cwd=self.workspace_path,
                               env=self._token_env(deploy_token), timeout=60)

    def add_upstream_remote(
        self, upstream_url: str, token: str | None = None
    ) -> ExecResult:
        """Bake a read-only `upstream` remote for a FORK workflow — a fork's worker needs
        two remotes: `origin` (the fork, its push target) and `upstream` (the parent, to
        pull others' changes). Adding a remote is an OPERATOR SETUP action, so — like
        `clone` — it runs the REAL git binary directly, bypassing the git-proxy (which
        blocks `git remote add`: "remotes are operator-baked", design §3.3/§12.3). The
        worker itself can never add/mutate remotes; only this setup path does.

        Called AFTER a fresh clone, so the source of truth for `upstream` is the caller
        (the agent-org bridge's persistent Project record), re-applied on every focus —
        the workspace is ephemeral (wiped on switch), so `upstream` is never assumed to
        persist. Idempotent: `remote add` on a fresh clone succeeds; if it already exists
        (a non-wiped re-focus) it falls back to `set-url`.

        Push is fenced to a no-op URL so `git push upstream` fails fast — the worker
        publishes only to `origin` (its fork). NOT `main`-related and additive, so it's
        routine per the corrected floor. A PRIVATE upstream needs a read-scoped `token`,
        stored for the upstream URL in the credential store (keyed by path, so it never
        replaces origin's); the remote URL itself stays token-free."""
        g = shlex.quote(self.real_git)
        q = shlex.quote
        creds = f"{_cred_config(g)} && {_cred_store(g, q(upstream_url))} && " if token else ""
        # `remote add` fails (exit 3) if `upstream` already exists — fall back to set-url so
        # a re-focus onto an unwiped workspace is still correct. Then fence the push side.
        cmd = (
            f"cd {q(self.workspace_path)} && {creds}"
            f"({g} remote add upstream {q(upstream_url)} || "
            f"{g} remote set-url upstream {q(upstream_url)}) && "
            f"{g} remote set-url --push upstream DISABLED-fork-parent-is-fetch-only"
        )
        return self.ot.execute(cmd, cwd=self.workspace_path, env=self._token_env(token),
                               timeout=120)

    def add_submodule(
        self, url: str, path: str, *, commit_message: str | None = None,
        token: str | None = None,
    ) -> ExecResult:
        """Add `url` as a git SUBMODULE at `path` in the focused (composition) repo, then commit +
        push. This is an OPERATOR SETUP action (autonomous-project-lifecycle P-APL.1b) — like `clone`
        / `add_upstream_remote`, it runs the REAL git binary directly, bypassing the git-proxy (which
        HARD-DENIES `submodule` to the worker, design §3.3). The worker can never restructure the
        repo topology; only this governed setup path does.

        The URL recorded in `.gitmodules` (a TRACKED file, pushed to the remote) and `.git/config`
        is ALWAYS the token-free one. Before 2026-09-28 a private submodule's read token was spliced
        into it, so it was committed and pushed; now a `token` is stored for that URL in the
        credential store instead. The composition repo's own origin authenticates the push through
        the credential its focus stored. First submodule on a freshly-cloned empty repo creates the
        initial commit + default branch, so we `push -u`."""
        msg = commit_message or f"Add {path} submodule"
        g = shlex.quote(self.real_git)
        q = shlex.quote
        creds = f"{_cred_config(g)} && {_cred_store(g, q(url))} && " if token else ""
        # IDEMPOTENT: if `path` is already a submodule (a partial/repeated compose), skip cleanly
        # instead of failing 'already exists' — so re-running a plan adds only what's missing.
        cmd = (
            f"cd {q(self.workspace_path)} && "
            f"if {g} submodule status {q(path)} >/dev/null 2>&1; then "
            f"echo 'submodule {path} already present — skipping'; "
            f"else "
            f"{creds}"
            f"{g} submodule add {q(url)} {q(path)} && "
            f"{g} commit -m {q(msg)} && "
            f"{g} push -u origin HEAD; "
            f"fi"
        )
        return self.ot.execute(cmd, cwd=self.workspace_path, env=self._token_env(token),
                               timeout=600)

    def wipe(self) -> ExecResult:
        """Empty the workspace, keeping the mount point itself. open-terminal owns the files it
        created, so the wipe runs there. FORCE-remove (`rm -rf` per top-level entry, not `find
        -delete`) so git's read-only pack objects + nested submodule `.git` dirs of a stale/populated
        clone are cleared — otherwise a leftover file makes the next `git clone` fail 'destination not
        empty' (exit 128)."""
        ws = shlex.quote(self.workspace_path)
        cmd = (
            f"chmod -R u+w {ws} 2>/dev/null; "
            f"find {ws} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} + 2>/dev/null; "
            f"find {ws} -mindepth 1 -delete 2>/dev/null || true"
        )
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
