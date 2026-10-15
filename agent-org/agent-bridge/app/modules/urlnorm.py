"""urlnorm - which host and which path a URL names, for source-trust decisions.

The REFERENCE implementation (kp-cleanup; coordinator decision FU-1 in the kp-contract review: the
web gate will be Python). The research engine's port, OB1 integrations/research-service/urlnorm.ts,
is held to the same verdicts by the shared parity corpus (tests/fixtures/url-normalise-corpus.json,
a copy of OB1's fixtures/url-normalise-corpus.json; tests/test_urlnorm.py here, urlnorm.test.ts
there). Change a rule here and regenerate the corpus, then port it to urlnorm.ts.

Moved from knowledge_package (`normalise_url`, `_path_escapes_ok` and the URL-shape part of
`url_ok`, hardened over kp-research..kp-harden). What it does, on the RAW string:
  - printable ASCII only (a real URL's non-ASCII is percent-encoded on the wire);
  - http or https (lower case), no userinfo (`@` anywhere), no port, no backslash, no
    pseudo-scheme (`javascript:` `vbscript:` `data:` `file:`) anywhere after the scheme;
  - host: lower case, ONE trailing root dot stripped, DNS labels only, a letter TLD, no numeric
    label (no IP literal), no punycode (`xn--`), no internal / wildcard-DNS / reserved name;
  - path: refuse raw dot segments (`.`, `..`, `...`), encoded dots / slashes / backslashes
    (`%2e` `%2f` `%5c`), `;`, `%25`, a bare `%`, an escaped control or space byte, and an escaped
    byte >= 0x80 unless it is strict UTF-8 that NFKC leaves unchanged and holds no dot/slash
    look-alike;
  - then decode escaped UNRESERVED characters (RFC 3986 6.2.2.2), remove dot segments (RFC 3986
    5.2.4) and split into path segments.
Policy that is not URL naming (which scheme a caller accepts, a length cap, secret-shaped query
parameters, which hosts are trusted) stays with the caller. Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PATH_RE = re.compile(r"^(?:/[A-Za-z0-9._~%!$&'()*+,;=:/-]*)?$")
_SCHEME_RE = re.compile(r"(https?)://")
_PSEUDO_SCHEME_RE = re.compile(r"(?:javascript|vbscript|data|file)\s*:")
_BAD_HOST_SUFFIXES = (".local", ".lan", ".internal", ".localdomain", ".corp", ".home", ".intranet",
                      ".svc", ".nip.io", ".sslip.io", ".xip.io", ".localtest.me", ".lvh.me",
                      ".test", ".example", ".invalid", ".onion", ".arpa")
_BAD_HOSTS = frozenset({"localhost", "nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me"})
_ENCODED_DOT_SLASH_RE = re.compile(r"%(?:2e|2f|5c)", re.I)
_PCT_RUN_RE = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_BAD_PCT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_UNRESERVED_PCT_RE = re.compile(r"%([0-9A-Fa-f]{2})")
# Dot / slash / backslash look-alikes a lenient server may fold to `.` `..` `...` `/` `\`. Most
# are NFKC-unstable (refused by that rule too); U+3002 IDEOGRAPHIC FULL STOP is NFKC-stable.
_LOOKALIKE_DOT_SLASH = frozenset("․‥…︙︰﹒．／＼"
                                 "﹨。")


def path_escapes_ok(path: str) -> bool:
    """kp-harden R3: every run of %XX escapes in a path decodes to bytes; refuse any byte <= 0x20
    (`%00`, `%09`, `%20`: NUL/space trimming), and any run holding a byte >= 0x80 unless it is
    STRICT UTF-8 (shortest form - so overlong 2/3/4-byte forms, RFC 2279 5/6-byte forms,
    surrogates and lone bytes are refused) whose text NFKC leaves unchanged and which holds no
    dot/slash look-alike."""
    for m in _PCT_RUN_RE.finditer(path):
        raw = bytes.fromhex(m.group(0).replace("%", ""))
        if any(b <= 0x20 for b in raw):
            return False
        if all(b < 0x80 for b in raw):
            continue
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return False
        if unicodedata.normalize("NFKC", text) != text or any(ch in _LOOKALIKE_DOT_SLASH
                                                              for ch in text):
            return False
    return True


def decode_unreserved(path: str) -> str:
    """RFC 3986 6.2.2.2: decode percent-encoded UNRESERVED characters only (ALPHA DIGIT - . _ ~);
    every other escape is left as is, upper-cased."""
    def sub(m: re.Match[str]) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if (ch.isascii() and (ch.isalnum() or ch in "-._~")) else m.group(0).upper()
    return _UNRESERVED_PCT_RE.sub(sub, path)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 5.2.4 remove_dot_segments."""
    inp, out = path, []
    while inp:
        if inp.startswith("../"):
            inp = inp[3:]
        elif inp.startswith("./"):
            inp = inp[2:]
        elif inp.startswith("/./"):
            inp = inp[2:]
        elif inp == "/.":
            inp = "/"
        elif inp.startswith("/../"):
            inp = inp[3:]
            if out:
                out.pop()
        elif inp == "/..":
            inp = "/"
            if out:
                out.pop()
        elif inp in (".", ".."):
            inp = ""
        else:
            i = inp.find("/", 1)
            seg, inp = (inp, "") if i == -1 else (inp[:i], inp[i:])
            out.append(seg)
    return "".join(out)


def host_shape_ok(host: str) -> bool:
    """A DNS host the trust checks can name: lower-case labels, a letter TLD, no numeric label (no
    IP), no punycode (`xn--`: homograph risk), nothing internal or wildcard-DNS. `host` is already
    lower-cased and stripped of one root dot."""
    if not host:
        return False
    labels = host.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL_RE.match(lb) for lb in labels):
        return False
    if not re.fullmatch(r"[a-z]{2,24}", labels[-1]) or any(lb.isdigit() for lb in labels):
        return False
    if any(lb.startswith("xn--") for lb in labels):
        return False
    return not (host in _BAD_HOSTS or any(host.endswith(suf) for suf in _BAD_HOST_SUFFIXES))


def normalise_url(url: Any) -> tuple[str, list[str]] | None:
    """(host, path segments) as host and host/path-prefix entries compare them, or None when the
    URL cannot be named safely (see the module docstring). None is a REFUSAL. Segments are the
    path after its leading `/` (`/a/b/` -> ["a", "b", ""]; `/` or no path -> [""]).

    The match is at the PARSER level: under RFC 3986, WHATWG URL and httpx the raw and normalised
    paths name the same resource. It is not a SERVER-level guarantee: a server that folds further
    (NFKC look-alikes, trimming `%00`/`%20`, decoding overlong UTF-8) could serve a resource
    outside a prefix. The folding forms known here are refused (`path_escapes_ok`), but no shape
    check can know every server's normalisation."""
    if not isinstance(url, str) or not url:
        return None
    if not all(0x21 <= ord(ch) <= 0x7E for ch in url):
        return None
    m = _SCHEME_RE.match(url)
    if not m or "@" in url or "\\" in url:
        return None
    if _PSEUDO_SCHEME_RE.search(url[len(m.group(1)) + 1:].lower()):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.endswith("."):
        host = host[:-1]                                   # a trailing root dot names the same host
    if port is not None or not host_shape_ok(host):
        return None
    path = parts.path or ""
    if not _PATH_RE.match(path):
        return None
    if _ENCODED_DOT_SLASH_RE.search(path):
        return None                                        # %2e %2f %5c: a hidden segment trick
    if ";" in path or "%25" in path or _BAD_PCT_RE.search(path):
        return None                                        # server-quirk forms (`..;`, `%252e`, `%u002e`)
    if not path_escapes_ok(path):
        return None                                        # control/space, non-strict-UTF-8, look-alike
    if any(seg and set(seg) == {"."} for seg in path.split("/")):
        return None                                        # `.` `..` `...`: dot segments escape a prefix
    norm = remove_dot_segments(decode_unreserved(path or "/")) or "/"
    return host, norm.split("/")[1:]
