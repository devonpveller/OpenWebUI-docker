"""Repo-URL normalization (design §12.3) — SSH and HTTPS forms of the same
repo must collapse to one focus_key so a switch never wipes spuriously."""

import pytest

from littlecoder.urlnorm import RepoUrlError, normalize_repo_url

# Every form on each row names the SAME repo.
SAME_REPO = [
    "https://github.com/Acme/Widget.git",
    "https://github.com/acme/widget",
    "http://github.com/acme/widget/",
    "git@github.com:Acme/Widget.git",
    "ssh://git@github.com/acme/widget.git",
    "git://github.com/acme/widget",
]


@pytest.mark.parametrize("link", SAME_REPO)
def test_all_forms_share_one_focus_key(link):
    norm = normalize_repo_url(link)
    assert norm.focus_key == "github.com/acme/widget"
    assert norm.canonical_url == "https://github.com/acme/widget"


def test_nested_gitlab_subgroups():
    norm = normalize_repo_url("https://gitlab.com/Group/Sub/Project.git")
    assert norm.host == "gitlab.com"
    assert norm.owner == "group/sub"
    assert norm.repo == "project"
    assert norm.focus_key == "gitlab.com/group/sub/project"


def test_distinct_repos_differ():
    a = normalize_repo_url("https://github.com/acme/widget")
    b = normalize_repo_url("https://github.com/acme/gadget")
    assert a.focus_key != b.focus_key


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not-a-url", "https://github.com/onlyowner", "github.com"],
)
def test_garbage_is_rejected(bad):
    with pytest.raises(RepoUrlError):
        normalize_repo_url(bad)


# --- branch fragment (`#<branch>`) ----------------------------------------


def test_no_branch_fragment_means_branch_none():
    """The default — link without `#` carries no branch."""
    norm = normalize_repo_url("https://github.com/acme/widget")
    assert norm.branch is None


def test_branch_fragment_parsed_from_https():
    norm = normalize_repo_url("https://github.com/acme/widget#dev")
    assert norm.branch == "dev"
    # Identity unchanged — same repo, different branch.
    assert norm.focus_key == "github.com/acme/widget"


def test_branch_fragment_parsed_from_https_with_git_suffix():
    """`.git` suffix is stripped from path, fragment survives."""
    norm = normalize_repo_url("https://github.com/acme/widget.git#release-v2")
    assert norm.branch == "release-v2"
    assert norm.repo == "widget"


def test_branch_fragment_parsed_from_ssh_scp_form():
    """The scp-like SSH form (`git@host:owner/repo`) also accepts a
    trailing `#<branch>`."""
    norm = normalize_repo_url("git@github.com:acme/widget.git#feature/x")
    assert norm.branch == "feature/x"
    assert norm.focus_key == "github.com/acme/widget"


def test_branch_with_forward_slashes_allowed():
    """`feature/auth-rework` is a legal git branch name."""
    norm = normalize_repo_url(
        "https://github.com/acme/widget#feature/auth-rework"
    )
    assert norm.branch == "feature/auth-rework"


def test_canonical_url_excludes_branch_fragment():
    """Journals record repo identity, not working branch — the
    canonical_url stays clean."""
    norm = normalize_repo_url("https://github.com/acme/widget#dev")
    assert norm.canonical_url == "https://github.com/acme/widget"
    assert "#" not in norm.canonical_url


def test_focus_key_ignores_branch():
    """Switching from `repo#main` to `repo#dev` is NOT a project switch
    (same focus_key); operator uses `git checkout` inside the
    workspace for that."""
    a = normalize_repo_url("https://github.com/acme/widget#main")
    b = normalize_repo_url("https://github.com/acme/widget#dev")
    assert a.focus_key == b.focus_key


def test_empty_fragment_means_no_branch():
    """`url#` (trailing #) is treated as no branch."""
    norm = normalize_repo_url("https://github.com/acme/widget#")
    assert norm.branch is None


def test_bad_branch_fragment_rejected():
    """Branch names with spaces / control chars / leading punctuation
    fail the whitelist — better to surface a clear error than build
    a malformed `git clone -b` argv."""
    with pytest.raises(RepoUrlError, match="unusable branch"):
        normalize_repo_url("https://github.com/acme/widget#bad branch")
    with pytest.raises(RepoUrlError, match="unusable branch"):
        normalize_repo_url("https://github.com/acme/widget#-leading-dash")
    with pytest.raises(RepoUrlError, match="unusable branch"):
        normalize_repo_url("https://github.com/acme/widget#has..dots")
