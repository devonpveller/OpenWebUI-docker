"""Workspace-edge artifact filter — adversarial tests (open item #9).

These drive the pure `classify()` function. Symmetric with the git-proxy
tests: the policy is testable without a running container, the deploy-level
controls (read-only mount, capability drop) close the residual root-bypass.
"""

import pytest

from littlecoder.git_artifact_filter import classify


def decide(command: str):
    return classify(command)


# --- ordinary work is allowed --------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "",
        "   ",
        "ls -la",
        "git status",
        "git log --oneline -5",
        "cat README.md",
        # Reading the locked artifacts is intentionally allowed — the
        # threat is tampering, not reading.
        "cat .git/config",
        "cat .git/config | grep remote",
        "ls .git/hooks/",
        "head -20 .git/info/exclude",
        # Writes to OTHER .git paths (objects/refs/index) — the agent
        # needs these to commit/branch/checkout.
        "echo hi > .git/HEAD",  # not in the locked set; commits need it
        "echo ref: refs/heads/main > .git/HEAD",
        "rm .git/index.lock",
        # Writes to NON-.git paths — never our concern.
        "echo hi > out.txt",
        "cp a b",
        "sed -i 's/foo/bar/' src/main.py",
        # `.git/configure` is not `.git/config` — \b after config matters.
        "cat .git/configure-script",
    ],
)
def test_allowed(cmd):
    assert decide(cmd).action == "allow", cmd


# --- shell redirects into the locked set ---------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "echo '[remote] url=http://evil' > .git/config",
        "echo x >> .git/config",
        "echo x >|.git/config",
        "echo x > ./.git/config",
        # Quoted target.
        'echo x > ".git/config"',
        "echo x > '.git/config'",
        # No space between `>` and target — common evasion shape.
        "echo x >.git/config",
        # tee — both modes.
        "echo x | tee .git/config",
        "echo x | tee -a .git/config",
        # Hooks dir.
        "echo '#!/bin/sh\\nrm -rf /' > .git/hooks/pre-commit",
        "echo x >> .git/hooks/post-checkout",
        # Info dir.
        "echo '*.secret' > .git/info/exclude",
        # Heredoc → redirect into the locked path.
        "cat > .git/config <<EOF\n[remote]\nEOF",
        # Chained — the redirect appears mid-command.
        "ls && echo x > .git/config",
        "true; echo x > .git/config",
    ],
)
def test_blocked_redirect(cmd):
    d = decide(cmd)
    assert d.action == "deny", cmd
    assert d.rule == "blocklist:redirect-to-git-artifact", cmd


# --- write-oriented commands ---------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "cp /tmp/evil-config .git/config",
        "mv /tmp/evil-config .git/config",
        "cp /tmp/h .git/hooks/pre-commit",
        "install -m 0755 /tmp/h .git/hooks/post-commit",
        "truncate -s 0 .git/config",
        "dd if=/tmp/evil of=.git/config",
        "ls; cp x .git/config",
    ],
)
def test_blocked_write_cmd(cmd):
    d = decide(cmd)
    assert d.action == "deny", cmd
    assert d.rule == "blocklist:write-cmd-to-git-artifact", cmd


# --- in-place editors ----------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "sed -i 's/origin/evil/' .git/config",
        "sed --in-place 's/origin/evil/' .git/config",
        "sed -i.bak 's/x/y/' .git/config",  # combined flags
        "awk -i inplace '{print}' .git/config",
        "perl -pi -e 's/x/y/' .git/config",
        "perl -i -pe 's/x/y/' .git/hooks/pre-commit",
    ],
)
def test_blocked_inplace(cmd):
    d = decide(cmd)
    assert d.action == "deny", cmd
    assert d.rule == "blocklist:inplace-edit-git-artifact", cmd


# --- documented residual gap (NOT closed by this filter) -----------------
#
# These pass through the filter today because they don't match the
# obvious-shape patterns. The design's full closure (read-only mount or
# uid split) is what catches them; until then, the git-proxy + this filter
# cover the OBVIOUS surface, not the determined attacker.


@pytest.mark.parametrize(
    "cmd",
    [
        # python -c is uncatchable without a real parser
        "python -c \"open('.git/config','w').write('x')\"",
        # base64 obfuscation
        "echo LmdpdC9jb25maWc= | base64 -d | xargs -I{} sh -c 'echo x > {}'",
        # rename a util then redirect through it
        "cp /bin/cp /tmp/cp2 && /tmp/cp2 a .git/config",
    ],
)
def test_residual_root_bypass_passes(cmd):
    """Document the gap — these are NOT denied. Full closure needs the
    read-only mount (design §3.3) or open-terminal running as non-root."""
    assert decide(cmd).action == "allow", cmd


# --- structural sanity ---------------------------------------------------


def test_deny_decision_carries_reason():
    d = decide("echo x > .git/config")
    assert d.action == "deny"
    assert d.reason  # non-empty
    assert d.rule.startswith("blocklist:")
