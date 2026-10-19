"""The nonce fence (DESIGN-intake.md section 6), Python side. I4 uses it for the second
opinion's prompt; I6 builds the read-side helpers (TS + Python) on the same shape.

    <<UNTRUSTED-{12 hex, CSPRNG} src={label} verdict={verdict}>>
    ...text...
    <<END-{same nonce}>>

  * Built per prompt, never stored: a fresh nonce every call (secrets.token_hex), and never a
    nonce that already occurs in the text.
  * The text cannot open or close a fence: every run of two or more angle brackets ('<' and
    its fullwidth / small-form lookalikes, also when split by blanks or zero-width format
    characters) becomes single angle quotation marks, so `<<END-...>>`, `<< END`, `<<<`,
    `\uff1c\uff1c` and `<\u200b<` are all broken. The rules (stage 5) already make a forged
    fence a STRONG hit; this is the belt to that brace.
  * `src` is reduced to [A-Za-z0-9._:/@-] (anything else becomes '_'), at most 64 characters;
    `verdict` must be one of the known labels.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

FENCE_VERDICTS = ("pending", "clean", "suspicious", "unscreened", "internal")
NONCE_HEX = 12
_NONCE = re.compile(r"[0-9a-f]{12}")
_LT = "<\uff1c\ufe64"
_GT = ">\uff1e\ufe65"
_GAP = r"[\s\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]*"
# A run of 2+ bracket characters, optionally separated by blanks / format characters. Linear:
# each repetition must consume a bracket character.
_LT_RUN = re.compile(f"[{_LT}](?:{_GAP}[{_LT}])+")
_GT_RUN = re.compile(f"[{_GT}](?:{_GAP}[{_GT}])+")
_SRC_BAD = re.compile(r"[^A-Za-z0-9._:/@-]")


@dataclass(frozen=True)
class Fenced:
    text: str
    nonce: str


def new_nonce() -> str:
    return secrets.token_hex(NONCE_HEX // 2)


def _quotes(ch: str, run: str) -> str:
    return ch * sum(1 for c in run if c in (_LT + _GT))


# attempt 1 T-F6: one-character double angle brackets (much-less-than, guillemets, the
# mathematical and CJK double angle brackets) are not `<<`, so they cannot form a marker; they are
# still mapped to single angle quotation marks so a fence-shaped run reads the same to a model
# whichever character spells it (I6's read side uses the same set).
_DOUBLE = str.maketrans({"\u226a": "\u2039", "\u00ab": "\u2039", "\u27ea": "\u2039", "\u300a": "\u2039",
                         "\u226b": "\u203a", "\u00bb": "\u203a", "\u27eb": "\u203a", "\u300b": "\u203a"})


def neutralise(text: str) -> str:
    text = text.translate(_DOUBLE)
    text = _LT_RUN.sub(lambda m: _quotes("\u2039", m.group(0)), text)
    return _GT_RUN.sub(lambda m: _quotes("\u203a", m.group(0)), text)


def label(src: str) -> str:
    return _SRC_BAD.sub("_", src)[:64] or "_"


def wrap(text: str, *, src: str, verdict: str, nonce: str | None = None) -> Fenced:
    if verdict not in FENCE_VERDICTS:
        raise ValueError(f"verdict {verdict!r} is not one of {FENCE_VERDICTS}")
    if nonce is not None and not _NONCE.fullmatch(nonce):
        raise ValueError("a nonce is 12 lower-case hex characters")
    body = neutralise(text)
    while nonce is None or nonce in body:
        nonce = new_nonce()
    head = f"<<UNTRUSTED-{nonce} src={label(src)} verdict={verdict}>>"
    return Fenced(f"{head}\n{body}\n<<END-{nonce}>>", nonce)
