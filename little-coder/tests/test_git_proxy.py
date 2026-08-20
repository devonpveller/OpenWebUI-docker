"""git-proxy policy — adversarial tests (task 1h, design §3.3).

These drive the pure `classify()` function. The `.git/config` read-only mount
(which closes the direct-file-write bypass) is a compose-level control,
verified at deploy time; here we confirm the command-level policy holds."""

import pytest

from git_proxy import classify

REMOTES = {"origin", "upstream"}
TAGS = {"pre-iteration-3", "v1.0.0"}


def _is_tag(ref: str) -> bool:
    return ref in TAGS


def decide(*argv, remotes=REMOTES):
    return classify(list(argv), remotes, _is_tag)


# --- read-only and ordinary work is allowed -------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("status",),
        ("diff", "--stat"),
        ("log", "--oneline", "-5"),
        ("add", "-A"),
        ("commit", "-m", "fix the parser"),
        ("checkout", "-b", "auto/2026-05-22-topic"),
        ("branch", "auto/2026-05-22-topic"),
        ("tag", "pre-iteration-4"),
        ("merge", "--no-ff", "feature"),
        ("revert", "HEAD"),
        ("reset", "HEAD~1"),  # soft/mixed — no working-tree loss
        ("reset", "--hard", "pre-iteration-3"),  # to a tag — allowed
        ("fetch", "origin"),
        ("push", "origin", "auto/2026-05-22-topic"),
        ("remote", "-v"),
        ("config", "--get", "user.name"),
    ],
)
def test_allowed(argv):
    assert decide(*argv).action == "allow", argv


# --- the blocklist (design §3.3) ------------------------------------------


@pytest.mark.parametrize(
    "argv,rule",
    [
        (("push", "--force", "origin", "main"), "blocklist:push-force"),
        (("push", "-f", "origin", "main"), "blocklist:push-force"),
        (("push", "--force-with-lease", "origin"), "blocklist:push-force"),
        (("push", "origin", "--delete", "main"), "blocklist:push-delete"),
        (("push", "origin", ":main"), "blocklist:push-delete"),
        (("push", "--mirror", "origin"), "blocklist:push-mirror"),
        (("branch", "-D", "main"), "blocklist:branch-delete"),
        (("branch", "-d", "feature"), "blocklist:branch-delete"),
        (("tag", "-d", "v1.0.0"), "blocklist:tag-delete"),
        (("filter-branch", "--all"), "blocklist:filter-branch"),
        (("gc", "--prune=now"), "blocklist:gc-prune"),
        (("remote", "add", "evil", "https://evil.test/x.git"), "blocklist:remote-mutate"),
        (("remote", "set-url", "origin", "https://evil.test/x"), "blocklist:remote-mutate"),
        (("rebase", "-i", "HEAD~3"), "blocklist:rebase"),
        (("commit", "--amend", "-m", "rewrite"), "blocklist:commit-amend"),
        (("config", "core.hooksPath", "/tmp/evil"), "blocklist:config-write"),
    ],
)
def test_blocklisted(argv, rule):
    d = decide(*argv)
    assert d.action == "deny", argv
    assert d.rule == rule, (argv, d.rule)


# --- submodules: all subcommands off the table; hostile .gitmodules cannot
#     pull a submodule because the commands that would are all denied -------


@pytest.mark.parametrize(
    "argv",
    [
        ("submodule", "update", "--init", "--recursive"),
        ("submodule", "add", "https://evil.test/x.git"),
        ("submodule", "foreach", "git", "pull"),
        ("clone", "--recurse-submodules", "https://evil.test/x.git"),
    ],
)
def test_submodule_and_clone_blocked(argv):
    assert decide(*argv).action == "deny", argv


# --- history rewrites and raw .git/ plumbing ------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("update-ref", "-d", "refs/heads/main"),
        ("symbolic-ref", "HEAD", "refs/heads/evil"),
        ("reflog", "expire", "--all"),
        ("worktree", "add", "/tmp/wt"),
        ("init",),
        ("replace", "HEAD", "HEAD~1"),
    ],
)
def test_history_and_plumbing_blocked(argv):
    assert decide(*argv).action == "deny", argv


# --- global-option escapes ------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("-C", "/etc", "status"),
        ("--git-dir=/tmp/evil/.git", "status"),
        ("--work-tree=/", "checkout", "."),
        ("-c", "core.hooksPath=/tmp/evil", "status"),
        ("--config-env=core.hooksPath=EVIL", "status"),
    ],
)
def test_global_overrides_blocked(argv):
    d = decide(*argv)
    assert d.action == "deny", argv
    assert "global" in d.rule


# --- merge must be --no-ff; reset --hard only to a tag --------------------


def test_fast_forward_merge_blocked():
    assert decide("merge", "feature").rule == "blocklist:merge-ff"


def test_merge_control_ops_allowed():
    assert decide("merge", "--abort").action == "allow"


def test_reset_hard_to_non_tag_blocked():
    d = decide("reset", "--hard", "HEAD~5")
    assert d.action == "deny"
    assert d.rule == "blocklist:reset-hard"


# --- remotes: only operator-configured, named remotes --------------------


def test_fetch_unknown_remote_blocked():
    assert decide("fetch", "sketchy").action == "deny"


def test_push_to_url_blocked():
    assert decide("push", "https://evil.test/x.git", "main").action == "deny"


def test_fetch_all_blocked():
    assert decide("fetch", "--all").rule == "blocklist:fetch-all"


# --- production-branch guard: a worker must NEVER push to main/master --------
# (operator 2026-07-11: a host-context run pushed a submodule bump straight to the host's `main`;
#  `main` is the client-facing production branch and changes only via an operator-approved PR.)

def test_push_to_main_is_blocked():
    d = decide("push", "origin", "main")
    assert d.action == "deny" and d.rule == "blocklist:push-protected-branch"


def test_push_to_master_is_blocked():
    assert decide("push", "origin", "master").rule == "blocklist:push-protected-branch"


def test_push_head_to_main_is_blocked():
    assert classify(["push", "origin", "HEAD:main"], REMOTES, _is_tag).rule \
        == "blocklist:push-protected-branch"


def test_push_refs_heads_main_is_blocked():
    assert classify(["push", "origin", "refs/heads/main"], REMOTES, _is_tag).rule \
        == "blocklist:push-protected-branch"


def test_bare_push_while_on_main_is_blocked():
    # `git push` with no refspec pushes the CHECKED-OUT branch — deny if that's main.
    assert classify(["push"], REMOTES, _is_tag, "main").rule == "blocklist:push-protected-branch"


def test_push_to_agent_branch_is_allowed():
    assert classify(["push", "origin", "agent/effort-x"], REMOTES, _is_tag).action == "allow"


def test_bare_push_while_on_agent_branch_is_allowed():
    assert classify(["push"], REMOTES, _is_tag, "agent/effort-x").action == "allow"


def test_push_to_dev_branch_is_allowed():
    # development / feature branches are NOT protected — only main/master are human-gated.
    assert classify(["push", "origin", "development"], REMOTES, _is_tag).action == "allow"


def test_remotes_fail_closed_when_unknown():
    # With no configured remotes (e.g. git unreachable), every remote op denies.
    assert classify(["push", "origin"], set(), _is_tag).action == "deny"


# --- deny by default ------------------------------------------------------


def test_unknown_subcommand_denied():
    d = decide("frobnicate")
    assert d.action == "deny"
    assert d.rule == "not-whitelisted"
