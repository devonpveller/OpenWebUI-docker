"""The target policy: which URLs and addresses the gate may fetch (audit A1/A5/B3).

A PORT of OB1 integrations/kubernetes-deployment/ingest-egress.ts rule 2 (v4Reason, v6Reason,
ipBlockReason, urlBlockReason, INTERNAL_SUFFIXES), with the gaps PLAN-connectors.md names closed:

  * ports      only the configured ports (80, 443 by default); ingest-egress allowed any port
  * names      .localdomain and the wildcard-DNS services (nip.io, sslip.io, ...) that turn a name
               into any IP are refused by name; every other name is RESOLVED and every answer
               judged (resolve.py), and the connection is PINNED to a judged address
               (transport.py) - in proxy mode too, where ingest-egress did not resolve at all
  * numbers    WHATWG-style numeric hosts (2130706433, 0x7f.1, 0177.0.0.1, 127.1) are decoded and
               judged as the IPv4 they mean; a host whose last label is numeric but is not a valid
               IPv4 is refused (a resolver may read it differently than we do)
  * IPv6       besides the ingest-egress cases (mapped, compatible, 6to4, Teredo, NAT64, ULA,
               link-local, multicast), site-local fec0::/10 and a zone id are refused, and every
               address must also be `is_global` by Python's IANA tables (documentation ranges,
               the local-use NAT64 prefix 64:ff9b:1::/48, ...). The explicit rules come first so
               the refusal names the specific reason.

Pure functions; no I/O. The refusal corpus is tests/fixtures/guard_corpus.json.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import SplitResult, urlsplit

INTERNAL_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".lan", ".home", ".home.arpa", ".arpa",
    ".intranet", ".corp", ".private", ".docker", ".test", ".invalid", ".ts.net",
)
# Wildcard DNS services: <anything-embedding-an-ip>.<service> resolves to that IP.
WILDCARD_DNS = (
    "nip.io", "sslip.io", "xip.io", "traefik.me", "localtest.me", "lvh.me", "vcap.me",
    "lacolhost.com", "fbi.com", "readme.localtest.me", "1u.ms", "nip.direct",
)
MAX_URL_CHARS = 4096

_HOST_OK = re.compile(r"[a-z0-9.-]+")
_NUMERIC_PART = re.compile(r"(0x[0-9a-f]*|[0-9]+)")


class Refused(Exception):
    """A policy refusal. The MESSAGE is for tests and the corpus only and may hold values taken
    from a response or a redirect; what a caller gets is ``REASONS[kind]``, a fixed gate-authored
    string (search-gate review T-2: no site-chosen text in a non-ok answer, and no resolver
    oracle, T-5)."""

    def __init__(self, message: str, kind: str = "target") -> None:
        super().__init__(message)
        self.kind = kind if kind in REASONS else "target"


# The ONLY refusal texts a caller ever sees.
REASONS = {
    "target": "refused: the URL is not an allowed target (scheme, credentials, port, internal "
              "name or non-public address)",
    "redirect-target": "refused: a redirect led to a target that is not allowed",
    "resolve": "refused: the name does not resolve to public addresses only",
    "redirects": "refused: too many redirects",
    "type": "refused: the content type is not on the allow-list",
    "encoding": "refused: the content encoding is not supported",
    "size": "refused: the body is over the byte cap",
    "time": "refused: the time cap passed before a complete answer",
}


# -- addresses -------------------------------------------------------------------------------------
def _v4_reason(ip: ipaddress.IPv4Address) -> str | None:
    a, b, c, _ = ip.packed
    if a == 0:
        return "this-network address (0.0.0.0/8)"
    if a == 10:
        return "private address (10.0.0.0/8)"
    if a == 100 and 64 <= b <= 127:
        return "CGNAT / tailnet address (100.64.0.0/10)"
    if a == 127:
        return "loopback address (127.0.0.0/8)"
    if a == 169 and b == 254:
        return "link-local address (169.254.0.0/16)"
    if a == 172 and 16 <= b <= 31:
        return "private address (172.16.0.0/12)"
    if a == 192 and b == 0 and c == 0:
        return "IETF protocol address (192.0.0.0/24)"
    if a == 192 and b == 168:
        return "private address (192.168.0.0/16)"
    if a == 198 and b in (18, 19):
        return "benchmark address (198.18.0.0/15)"
    if a >= 224:
        return "multicast / reserved address (224.0.0.0/3)"
    if not ip.is_global:
        return f"non-global address ({ip})"
    return None


def _v6_reason(ip: ipaddress.IPv6Address) -> str | None:
    g = [int.from_bytes(ip.packed[i:i + 2], "big") for i in range(0, 16, 2)]
    if all(x == 0 for x in g):
        return "unspecified address (::)"
    if all(x == 0 for x in g[:7]) and g[7] == 1:
        return "loopback address (::1)"
    if ip.ipv4_mapped is not None:
        return _v4_reason(ip.ipv4_mapped)  # judge the IPv4 it maps
    if all(x == 0 for x in g[:6]):
        return "IPv4-compatible address (::a.b.c.d, deprecated)"
    if all(x == 0 for x in g[:4]) and g[4] == 0xFFFF and g[5] == 0:
        # SIIT IPv4-translated ::ffff:0:a.b.c.d (RFC 2765); review T-1: the TS original passes it
        return "IPv4-translated address (::ffff:0:0:0/96)"
    if ip.sixtofour is not None:
        r = _v4_reason(ip.sixtofour)
        return f"6to4 address embedding a {r}" if r else None
    if g[0] == 0x2001 and g[1] == 0:
        return "Teredo address (2001::/32)"
    if g[0] == 0x64 and g[1] == 0xFF9B and all(x == 0 for x in g[2:6]):
        v4 = ipaddress.IPv4Address(ip.packed[12:])
        return _v4_reason(v4)  # NAT64 well-known prefix: judge the IPv4
    if g[0] & 0xFE00 == 0xFC00:
        return "unique-local address (fc00::/7)"
    if g[0] & 0xFFC0 == 0xFE80:
        return "link-local address (fe80::/10)"
    if g[0] & 0xFFC0 == 0xFEC0:
        return "site-local address (fec0::/10, deprecated)"
    if g[0] & 0xFF00 == 0xFF00:
        return "multicast address (ff00::/8)"
    if not ip.is_global:
        return f"non-global address ({ip})"
    return None


def ip_block_reason(text: str) -> str | None:
    """Why an IP address (v4 or v6; brackets allowed) must not be fetched, or None if public.
    Something that is not an IP address is refused (the caller asked about an address)."""
    bare = text.strip()
    if bare.startswith("[") and bare.endswith("]"):
        bare = bare[1:-1]
    if "%" in bare:
        return "IPv6 address with a zone id"
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return "not an IP address"
    if isinstance(ip, ipaddress.IPv4Address):
        return _v4_reason(ip)
    return _v6_reason(ip)


def parse_numeric_ipv4(host: str) -> ipaddress.IPv4Address | None | bool:
    """WHATWG IPv4 host parsing. Returns the address, None when the host is not numeric, or False
    when it LOOKS numeric (last label numeric) but is not a valid IPv4 - refused by the caller."""
    labels = host.split(".")
    if labels and labels[-1] == "":
        labels = labels[:-1]
    if not labels or not _NUMERIC_PART.fullmatch(labels[-1]):
        return None
    if len(labels) > 4:
        return False
    nums: list[int] = []
    for part in labels:
        if not _NUMERIC_PART.fullmatch(part):
            return False
        if part.startswith("0x"):
            nums.append(int(part[2:] or "0", 16))
        elif len(part) > 1 and part.startswith("0"):
            if any(ch in "89" for ch in part):
                return False
            nums.append(int(part, 8))
        else:
            nums.append(int(part))
    if any(n > 255 for n in nums[:-1]) or nums[-1] >= 256 ** (5 - len(nums)):
        return False
    value = nums[-1]
    for i, n in enumerate(nums[:-1]):
        value += n * 256 ** (3 - i)
    return ipaddress.IPv4Address(value)


# -- URLs ------------------------------------------------------------------------------------------
class Target:
    """A URL that passed the policy: its parts, with the host canonical (lower case, IDNA ascii,
    no trailing dot) and a literal IP already judged."""

    __slots__ = ("url", "scheme", "host", "port", "path_qs", "ip_literal")

    def __init__(self, url: str, scheme: str, host: str, port: int, path_qs: str,
                 ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> None:
        self.url = url
        self.scheme = scheme
        self.host = host
        self.port = port
        self.path_qs = path_qs
        self.ip_literal = ip_literal

    @property
    def host_header(self) -> str:
        h = f"[{self.host}]" if ":" in self.host else self.host
        default = 443 if self.scheme == "https" else 80
        return h if self.port == default else f"{h}:{self.port}"


def _split(raw: str) -> SplitResult:
    if not isinstance(raw, str) or not raw or len(raw) > MAX_URL_CHARS:
        raise Refused("not a valid absolute URL")
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in raw) or "\\" in raw:
        raise Refused("URL contains control characters, blanks or backslashes")
    try:
        parts = urlsplit(raw)
        _ = parts.port  # raises ValueError on a bad port
    except ValueError:
        raise Refused("not a valid absolute URL") from None
    return parts


def check_url(raw: str, allowed_ports: frozenset[int]) -> Target:
    """Judge a URL; return the Target, or raise Refused naming why."""
    parts = _split(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise Refused(f"scheme {scheme or '(none)'}: is not http(s)")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise Refused("URL carries credentials (userinfo)")
    host_raw = parts.hostname  # lower-cased, brackets stripped
    if not host_raw:
        raise Refused("URL has no host")
    port = parts.port if parts.port is not None else (443 if scheme == "https" else 80)
    if port not in allowed_ports:
        raise Refused(f"port {port} is not an allowed port")
    path_qs = parts.path or "/"
    if parts.query:
        path_qs += "?" + parts.query

    ip_literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    if parts.netloc.split("@")[-1].startswith("["):  # an IPv6 literal
        why = ip_block_reason(host_raw)
        if why:
            raise Refused(f"target is a {why}")
        ip_literal = ipaddress.IPv6Address(host_raw)
        host = str(ip_literal)
    else:
        try:
            host = host_raw.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise Refused("host is not a valid domain name") from None
        host = host.rstrip(".")
        if not host or not _HOST_OK.fullmatch(host) or ".." in host:
            raise Refused("host is not a valid domain name")
        num = parse_numeric_ipv4(host)
        if num is False:
            raise Refused("host looks numeric but is not a valid IPv4 address")
        if num is not None:
            assert isinstance(num, ipaddress.IPv4Address)
            why = _v4_reason(num)
            if why:
                raise Refused(f"target is a {why}")
            ip_literal = num
            host = str(num)
        else:
            if host == "localhost":
                raise Refused("target is localhost")
            if "." not in host:
                raise Refused(f'target "{host}" is a single-label (internal) hostname')
            for s in INTERNAL_SUFFIXES:
                if host.endswith(s):
                    raise Refused(f'target "{host}" is an internal hostname (*{s})')
            for w in WILDCARD_DNS:
                if host == w or host.endswith("." + w):
                    raise Refused(f'target "{host}" is a wildcard-DNS name ({w})')
    canonical = f"{scheme}://{('[' + host + ']') if ':' in host else host}"
    if port != (443 if scheme == "https" else 80):
        canonical += f":{port}"
    canonical += path_qs
    return Target(canonical, scheme, host, port, path_qs, ip_literal)


def check_addresses(host: str, addresses: list[str]) -> str:
    """Judge every resolved address of `host`; return the one to pin (the first IPv4, else the
    first IPv6). Any non-public answer refuses the whole name (a mixed answer is a rebinding
    shape)."""
    if not addresses:
        raise Refused(f"{host} does not resolve")
    for a in addresses:
        why = ip_block_reason(a)
        if why:
            raise Refused(f"{host} resolves to a {why}")
    v4 = [a for a in addresses if ":" not in a]
    return (v4 or addresses)[0]
