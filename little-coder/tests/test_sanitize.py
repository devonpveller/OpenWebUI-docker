"""Sanitization filter — pinned and tested against a fixed test set with
seeded false-negatives and false-positives (design §10.2)."""

import pytest

from littlecoder.sanitize import Sanitizer, SanitizerError, redact_secrets

# Seeded FALSE-NEGATIVE guards: each MUST be detected. A regression that
# stops catching one of these is a real leak.
MUST_REDACT = {
    "github_pat": "token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 here",
    "aws_key": "AKIAIOSFODNN7EXAMPLE in the config",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dozjgNryP4J3jVmNHDpoEZ",
    "assigned_secret": 'API_KEY = "s3cr3t-value-abcdefghij"',
    "bearer": "Authorization: Bearer abcdef0123456789abcdef",
    "pem": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890\n"
        "-----END RSA PRIVATE KEY-----"
    ),
    "email": "ping me at alice.dev@example.com about it",
    "ipv4": "the box answered on 203.0.113.42 yesterday",
}

# Seeded FALSE-POSITIVE guards: clearly-benign strings that must NOT be
# flagged. Guards against the filter becoming over-aggressive.
MUST_NOT_REDACT = [
    "The quick brown fox jumps over the lazy dog.",
    "def add(a, b):\n    return a + b",
    "Refactored the parser; version bumped to 1.2.3.",
    "commit a1b2c3d4e5f6 fixed the off-by-one",
    "See the README for setup instructions.",
]


@pytest.mark.parametrize("name", sorted(MUST_REDACT))
def test_known_secrets_are_caught(name):
    s = Sanitizer(mode="enforcing")
    result = s.scan(MUST_REDACT[name])
    assert result.would_redact, f"{name} slipped through the filter"


@pytest.mark.parametrize("text", MUST_NOT_REDACT)
def test_benign_text_is_not_flagged(text):
    s = Sanitizer(mode="enforcing")
    assert not s.scan(text).would_redact


def test_enforcing_mode_scrubs_the_value():
    s = Sanitizer(mode="enforcing")
    out = s.apply("key ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 done")
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in out.cleaned
    assert "«REDACTED:github_token»" in out.cleaned


def test_shadow_mode_records_but_does_not_block():
    s = Sanitizer(mode="shadow")
    secret = "AKIAIOSFODNN7EXAMPLE"
    out = s.apply(f"value {secret}")
    # Shadow mode reports what it WOULD do but leaves the text untouched.
    assert secret in out.cleaned
    assert out.would_redact
    assert s.redacted == 1


def test_rejection_rate_tracks_redacted_fraction():
    s = Sanitizer(mode="shadow")
    s.apply("clean text")
    s.apply("secret ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert s.processed == 2
    assert s.redacted == 1
    assert s.rejection_rate == 0.5


def test_large_body_reduced_to_structural_digest():
    s = Sanitizer(mode="enforcing", max_body_bytes=64)
    out = s.digest_body("x" * 5000)
    assert "«DIGEST:body" in out.cleaned
    assert "x" * 100 not in out.cleaned
    assert out.would_redact


def test_small_body_passes_through():
    s = Sanitizer(mode="enforcing", max_body_bytes=64)
    out = s.digest_body("short body")
    assert out.cleaned == "short body"
    assert not out.would_redact


def test_masked_preview_never_leaks_the_value():
    s = Sanitizer(mode="enforcing")
    result = s.scan("AKIAIOSFODNN7EXAMPLE")
    for r in result.redactions:
        assert "AKIA" not in r.preview  # only length + hash prefix


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        Sanitizer(mode="bogus")


# ── redact_secrets — the always-on git-credential net for the activity/answer path ──

def test_redact_secrets_masks_token_in_remote_url():
    """The demonstrated leak: a deploy token baked into a remote URL, printed by `git remote -v`
    into the effort thread. The token must be masked; the host, path, and username must survive so
    the line stays readable."""
    fine = "github_pat_11ACZHHIA0AF79tuqAn2vE_pkN6GZ3xGCJNaAwRIN4b62JOJJdmCrBW0S3OAYHkgZfZ"
    line = f"origin\thttps://x-access-token:{fine}@github.com/devonpveller/monogame-engine.git (fetch)"
    out = redact_secrets(line)
    assert fine not in out                                   # token gone
    assert "«REDACTED»" in out
    assert "x-access-token" in out                           # non-secret username kept
    assert "github.com/devonpveller/monogame-engine.git" in out  # host + path readable
    assert "(fetch)" in out


def test_redact_secrets_masks_bare_github_pats():
    # fine-grained github_pat_ (which the egress Sanitizer's gh[pousr]_ pattern misses) AND classic
    assert "github_pat_" not in redact_secrets("token=github_pat_11ABCDEFGHIJKLMNOP0123456789abcd")
    assert "ghp_" not in redact_secrets("here is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ok")
    assert redact_secrets("gho_0123456789ABCDEFGHIJKLMNOPQRSTUVWX") == "«REDACTED»"


def test_redact_secrets_leaves_clean_text_untouched():
    for benign in [
        "https://github.com/MonoGame/MonoGame.git",   # clean URL — no credential to mask
        "git status -sb",
        "The workspace is essentially empty.",
        "",
    ]:
        assert redact_secrets(benign) == benign
