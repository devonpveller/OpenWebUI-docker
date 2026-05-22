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
