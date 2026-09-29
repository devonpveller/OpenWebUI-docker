"""The one-shot scrub of tokens left in workspace git configs (cf-lc-token, 2026-09-28)."""

import io
import os

from littlecoder.credscrub import find_git_configs, main, scrub, scrub_text

DUMMY = "github" + "_pat_" + "DUMMY" + "0" * 36   # assembled: no token-shaped literal in the tree


def _gitdir(path, config_text):
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "HEAD"), "w") as fh:
        fh.write("ref: refs/heads/main\n")
    with open(os.path.join(path, "config"), "w", newline="") as fh:
        fh.write(config_text)


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    _gitdir(str(ws / ".git"),
            '[core]\n\tbare = false\n[remote "origin"]\n'
            f"\turl = https://x-access-token:{DUMMY}@github.com/acme/widget\n"
            '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
            '[remote "upstream"]\n'
            f"\turl = https://x-access-token:{DUMMY}@github.com/parent/widget\n"
            "\tpushurl = DISABLED-fork-parent-is-fetch-only\n"
            '[submodule "vendor/lib"]\n'
            f"\turl = https://x-access-token:{DUMMY}@github.com/acme/lib\n")
    _gitdir(str(ws / ".git" / "modules" / "vendor" / "lib"),
            '[remote "origin"]\n'
            f"\turl = https://x-access-token:{DUMMY}@github.com/acme/lib\n")
    (ws / ".gitmodules").write_text(
        f'[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = https://x-access-token:{DUMMY}@github.com/acme/lib\n')
    # a non-git file that merely looks like a config: never touched
    (ws / "src").mkdir()
    (ws / "src" / "config").write_text(f"url = https://u:{DUMMY}@example.com/\n")
    return ws


def test_scrub_text_strips_userinfo_only():
    new, n = scrub_text(f"url = https://x-access-token:{DUMMY}@github.com/a/b\n"
                        "url = git@github.com:a/b.git\n"
                        "url = https://github.com/a/b\n"
                        f'[url "https://{DUMMY}@github.com/"]\n')
    assert n == 2
    assert DUMMY not in new
    assert "url = https://github.com/a/b\n" in new
    assert "git@github.com:a/b.git" in new           # ssh scp-form left alone
    assert '[url "https://github.com/"]' in new


def test_finds_repo_and_submodule_configs_not_lookalikes(tmp_path):
    ws = _workspace(tmp_path)
    configs, gitmodules = find_git_configs(str(ws))
    rel = sorted(os.path.relpath(c, ws) for c in configs)
    assert rel == [os.path.join(".git", "config"),
                   os.path.join(".git", "modules", "vendor", "lib", "config")]
    assert [os.path.relpath(g, ws) for g in gitmodules] == [".gitmodules"]


def test_scrub_removes_tokens_then_is_a_noop_and_never_prints_one(tmp_path):
    ws = _workspace(tmp_path)
    out = io.StringIO()
    assert scrub(str(ws), out=out) == 4
    first = out.getvalue()
    assert DUMMY not in first and "github_pat" not in first
    assert "removed credentials from 3 URL(s): " + os.path.join(".git", "config") in first
    assert "WARNING: tracked file" in first            # .gitmodules reported...
    assert DUMMY in (ws / ".gitmodules").read_text()   # ...not rewritten
    assert DUMMY in (ws / "src" / "config").read_text()   # lookalike untouched
    for cfg in (ws / ".git" / "config", ws / ".git" / "modules" / "vendor" / "lib" / "config"):
        text = cfg.read_text()
        assert DUMMY not in text and "x-access-token" not in text
        assert "https://github.com/acme/" in text
    assert "pushurl = DISABLED-fork-parent-is-fetch-only" in (ws / ".git" / "config").read_text()
    # second run: nothing to do, and it says so
    out2 = io.StringIO()
    assert scrub(str(ws), out=out2) == 0
    assert "0 credential URL(s) removed" in out2.getvalue()
    assert "removed credentials from" not in out2.getvalue()


def test_dry_run_changes_nothing(tmp_path):
    ws = _workspace(tmp_path)
    before = (ws / ".git" / "config").read_text()
    out = io.StringIO()
    assert scrub(str(ws), dry_run=True, out=out) == 4
    assert (ws / ".git" / "config").read_text() == before
    assert "would remove" in out.getvalue() and DUMMY not in out.getvalue()


def test_main_rejects_a_missing_root(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2
