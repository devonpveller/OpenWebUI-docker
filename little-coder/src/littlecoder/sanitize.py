"""Outbound sanitization filter (design §10.2).

One filter, all egress (judge calls, PR bodies, future exports). In Tool it
runs in SHADOW mode: it computes what it WOULD redact and records the counts,
but blocks nothing — nothing leaves the stack in Tool. From Observer onward it
runs ENFORCING: the cleaned text replaces the original at the call site, and a
filter error ABORTS the call — never "send anyway" (design §1, §10.2).

Three jobs:
  - redact secrets / key-shaped strings,
  - reduce large file bodies to a structural digest,
  - strip PII (emails, IP addresses).

The filter is pinned and tested against a fixed test set with seeded
false-positives and false-negatives (tests/test_sanitize.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Detection patterns. Ordered roughly most-specific first.
# --------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # PEM private-key blocks.
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # GitHub tokens: ghp_ (PAT), gho_, ghu_, ghs_, ghr_.
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    # AWS access key id.
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # JSON Web Tokens.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    # Bearer / authorization headers.
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # key/secret/token/password = <value>  (assignment-shaped).
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)\b"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9._\-/+=]{12,})[\"']?"
        ),
    ),
]

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
]

_PLACEHOLDER = "«REDACTED:{kind}»"


@dataclass
class Redaction:
    """One thing the filter removed (or, in shadow mode, would remove)."""

    kind: str  # secret kind, "email"/"ipv4", or "large_body"
    category: str  # secret | pii | large_body
    preview: str  # short, already-masked descriptor — never the raw value


@dataclass
class SanitizeResult:
    original_len: int
    cleaned: str
    redactions: list[Redaction] = field(default_factory=list)

    @property
    def would_redact(self) -> bool:
        return bool(self.redactions)


class SanitizerError(RuntimeError):
    """The filter itself failed. In enforcing mode the caller MUST abort."""


def _mask(value: str) -> str:
    """A short, safe descriptor of a redacted value — length + a hash prefix,
    never the value itself."""
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
    return f"len={len(value)} sha256:{digest}"


class Sanitizer:
    """The pinned outbound filter. One instance per process; thread-safe
    (stateless except for monotonic counters)."""

    def __init__(self, mode: str = "shadow", max_body_bytes: int = 8192) -> None:
        if mode not in ("shadow", "enforcing"):
            raise ValueError(f"sanitizer mode must be shadow|enforcing, got {mode!r}")
        self.mode = mode
        self.max_body_bytes = max_body_bytes
        # Counters for the metrics endpoint. "rejection rate" = the fraction
        # of processed items that triggered at least one redaction
        # (design §9.3, §10.2 drift trigger).
        self.processed = 0
        self.redacted = 0
        self.errors = 0

    @property
    def rejection_rate(self) -> float:
        return self.redacted / self.processed if self.processed else 0.0

    def scan(self, text: str) -> SanitizeResult:
        """Compute redactions for `text`. Never raises for ordinary input;
        a genuine internal failure raises SanitizerError."""
        if not isinstance(text, str):
            text = str(text)
        try:
            return self._scan(text)
        except SanitizerError:
            raise
        except Exception as exc:  # defensive: a regex/encoding blow-up
            self.errors += 1
            raise SanitizerError(f"sanitizer internal failure: {exc}") from exc

    def _scan(self, text: str) -> SanitizeResult:
        redactions: list[Redaction] = []
        cleaned = text

        for kind, pattern in _SECRET_PATTERNS:
            def _sub(m: re.Match[str], _kind: str = kind) -> str:
                redactions.append(
                    Redaction(kind=_kind, category="secret", preview=_mask(m.group(0)))
                )
                return _PLACEHOLDER.format(kind=_kind)

            cleaned = pattern.sub(_sub, cleaned)

        for kind, pattern in _PII_PATTERNS:
            def _sub(m: re.Match[str], _kind: str = kind) -> str:
                redactions.append(
                    Redaction(kind=_kind, category="pii", preview=_mask(m.group(0)))
                )
                return _PLACEHOLDER.format(kind=_kind)

            cleaned = pattern.sub(_sub, cleaned)

        return SanitizeResult(
            original_len=len(text), cleaned=cleaned, redactions=redactions
        )

    def digest_body(self, text: str) -> SanitizeResult:
        """Reduce an oversized file body to a structural digest (design §10.2).
        Bodies at or below the threshold pass through unchanged."""
        self.processed += 1
        raw = text if isinstance(text, str) else str(text)
        size = len(raw.encode("utf-8", "replace"))
        if size <= self.max_body_bytes:
            return SanitizeResult(original_len=len(raw), cleaned=raw)
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        lines = raw.count("\n") + 1
        summary = f"«DIGEST:body {size} bytes, {lines} lines, sha256:{digest[:16]}»"
        self.redacted += 1
        return SanitizeResult(
            original_len=len(raw),
            cleaned=summary,
            redactions=[
                Redaction(
                    kind="large_body",
                    category="large_body",
                    preview=f"{size} bytes, {lines} lines",
                )
            ],
        )

    def apply(self, text: str) -> SanitizeResult:
        """Sanitize one outbound string. Updates counters.

        In SHADOW mode the result's `.cleaned` is the ORIGINAL text (nothing
        is blocked) but `.redactions` reports what enforcing mode would strip.
        In ENFORCING mode `.cleaned` is the scrubbed text. A SanitizerError
        propagates either way — the enforcing-mode caller must abort the call;
        the shadow-mode caller logs it."""
        self.processed += 1
        result = self.scan(text)
        if result.would_redact:
            self.redacted += 1
        if self.mode == "shadow":
            # Record what would happen, change nothing.
            return SanitizeResult(
                original_len=result.original_len,
                cleaned=text,
                redactions=result.redactions,
            )
        return result
