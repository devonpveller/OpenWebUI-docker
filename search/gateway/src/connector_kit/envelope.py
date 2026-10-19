"""The envelope: intake's stage-1 input, built and checked HERE, before a byte is sent.

THE SCHEMA IS INTAKE'S, EXACTLY (integrations/intake/intake/envelope.py, DESIGN-intake.md section 3
stage 1). `check()` below is a standard-library mirror of `intake.envelope.validate()`: the same
required and optional fields, the same types, lengths, slugs, content-type allow-list, charset rule,
raw cap (after base64 decoding), metadata and link caps. A connector container does not ship the
intake package, so the mirror lives here; `connector_tests/test_envelope.py` runs both over the same
corpus (valid envelopes and every single-field mutation of them) and fails on any disagreement.

`build()` only ever produces a SUBSET of what intake accepts:
  - raw is always sent as base64 (binary-safe; JSON escapes cannot inflate it past the request cap;
    intake's own 413 message says "send raw as base64"); a str raw is encoded as strict UTF-8
  - fetched_at is an ISO 8601 timestamp WITH a zone (UTC when the connector gives none)
  - metadata carries the connector's provenance under the reserved key "provenance"
    (provenance.py); a connector's own metadata may not use that key
and every envelope it returns has passed `check()`. The idempotency key intake computes,
(source, external_id, sha256(raw bytes)), is computed here too, so a connector knows it before
sending and the client can verify intake's answer against it.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from . import provenance as _prov

# -- intake's stage-1 constants (intake/envelope.py, intake/limits.py) -----------------------------
ALLOWED_TYPES = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "plain",
    "text/markdown": "markdown",
    "message/rfc822": "rfc822",
    "application/json": "json",
}
REQUIRED = ("source", "connector", "external_id", "fetched_at", "content_type", "raw")
OPTIONAL = ("raw_encoding", "metadata", "links")
RAW_MAX_BYTES = 5 * 1024 * 1024
MAX_URL_CHARS = 2048
MAX_METADATA_BYTES = 64 * 1024
MAX_ENVELOPE_LINKS = 1000
TEXT_LIMITS = {"source": 64, "connector": 64, "external_id": 1024, "fetched_at": 64, "content_type": 200}

_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MIME = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}")
_CHARSET = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,39}")


class InvalidEnvelope(ValueError):
    """The envelope fails intake's stage 1 (or the kit's provenance rule); never sent."""


class TooLarge(InvalidEnvelope):
    """Over a size cap (raw, metadata, links): the connector must split or skip the item."""


def is_slug(value: object) -> bool:
    return isinstance(value, str) and bool(_SLUG.fullmatch(value))


# -- the mirror of intake.envelope.validate ---------------------------------------------------------
def _text(obj: Mapping, name: str, max_len: int) -> str:
    v = obj[name]
    if not isinstance(v, str):
        raise InvalidEnvelope(f"{name} must be a string")
    if not v or len(v) > max_len:
        raise InvalidEnvelope(f"{name} must be 1..{max_len} characters")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
        raise InvalidEnvelope(f"{name} contains control characters")
    return v


def _content_type(value: str) -> str:
    parts = [p.strip() for p in value.split(";")]
    mime = parts[0].lower()
    if not _MIME.fullmatch(mime) or mime not in ALLOWED_TYPES:
        raise InvalidEnvelope(f"content type {mime!r} is not allowed")
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            if k.strip().lower() == "charset":
                v = v.strip().strip('"')
                if not _CHARSET.fullmatch(v):
                    raise InvalidEnvelope("charset parameter is malformed")
    return mime


def _raw_bytes(raw: object, encoding: str) -> bytes:
    if not isinstance(raw, str):
        raise InvalidEnvelope("raw must be a string")
    if encoding == "text":
        if len(raw) > RAW_MAX_BYTES:
            raise TooLarge(f"raw is over {RAW_MAX_BYTES} bytes")
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError:
            data = _SURROGATE.sub(chr(0xFFFD), raw).encode("utf-8")
    else:  # base64 (the caller checked the encoding name)
        if len(raw) > (RAW_MAX_BYTES + 2) // 3 * 4 + 4:
            raise TooLarge(f"raw is over {RAW_MAX_BYTES} bytes")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as e:
            raise InvalidEnvelope(f"raw is not valid base64: {e}") from None
    if len(data) > RAW_MAX_BYTES:
        raise TooLarge(f"raw is over {RAW_MAX_BYTES} bytes")
    return data


def check(obj: object) -> bytes:
    """Raise InvalidEnvelope unless intake's stage 1 accepts `obj`; return the raw bytes (what
    intake hashes for the idempotency key)."""
    if not isinstance(obj, Mapping):
        raise InvalidEnvelope("the envelope must be an object")
    keys = set(obj.keys())
    missing = [k for k in REQUIRED if k not in keys]
    if missing:
        raise InvalidEnvelope("missing field(s): " + ", ".join(missing))
    unknown = sorted(str(k) for k in keys - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise InvalidEnvelope("unknown field(s): " + ", ".join(unknown[:10]))
    for name in ("source", "connector"):
        if not _SLUG.fullmatch(_text(obj, name, TEXT_LIMITS[name])):
            raise InvalidEnvelope(f"{name} must match [a-z0-9][a-z0-9._-]*")
    _text(obj, "external_id", TEXT_LIMITS["external_id"])
    fetched_at = _text(obj, "fetched_at", TEXT_LIMITS["fetched_at"])
    try:
        dt.datetime.fromisoformat(fetched_at)
    except ValueError:
        raise InvalidEnvelope("fetched_at is not an ISO 8601 timestamp") from None
    _content_type(_text(obj, "content_type", TEXT_LIMITS["content_type"]))
    encoding = obj.get("raw_encoding", "text")
    if encoding not in ("text", "base64"):
        raise InvalidEnvelope("raw_encoding must be 'text' or 'base64'")
    raw = _raw_bytes(obj["raw"], encoding)
    metadata = obj.get("metadata", {})
    if not isinstance(metadata, Mapping) or not all(isinstance(k, str) for k in metadata):
        raise InvalidEnvelope("metadata must be an object with string keys")
    try:
        encoded = json.dumps(metadata, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise InvalidEnvelope("metadata is not plain JSON") from None
    if len(encoded) > MAX_METADATA_BYTES:
        raise TooLarge(f"metadata is over {MAX_METADATA_BYTES} bytes")
    links = obj.get("links", [])
    if not isinstance(links, list) or len(links) > MAX_ENVELOPE_LINKS:
        raise (TooLarge if isinstance(links, list) else InvalidEnvelope)(
            f"links must be a list of at most {MAX_ENVELOPE_LINKS} strings")
    for u in links:
        if not isinstance(u, str) or not u or len(u) > MAX_URL_CHARS:
            raise InvalidEnvelope(f"each link must be a string of 1..{MAX_URL_CHARS} characters")
    return raw


# -- what a connector sends -------------------------------------------------------------------------
@dataclass(frozen=True)
class Key:
    """Intake's idempotency key: DESIGN section 3 stage 2."""
    source: str
    external_id: str
    sha256: str

    def to_dict(self) -> dict:
        return {"source": self.source, "external_id": self.external_id, "sha256": self.sha256}


@dataclass(frozen=True)
class Envelope:
    """A checked envelope. Build one with build() (or from_body() for a body already made);
    the client re-checks it before sending, so a hand-made instance gains nothing."""
    body: Mapping[str, object]
    key: Key

    @property
    def connector(self) -> str:
        return str(self.body["connector"])

    def to_bytes(self) -> bytes:
        return json.dumps(dict(self.body), ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")


def from_body(obj: object) -> Envelope:
    """Check a body dict (intake's schema + the kit's provenance rule) and wrap it."""
    raw = check(obj)
    assert isinstance(obj, Mapping)
    meta = obj.get("metadata") or {}
    if "provenance" not in meta:
        raise InvalidEnvelope("metadata.provenance is required (the kit's provenance rule)")
    _prov.check(meta["provenance"], connector=str(obj["connector"]), source=str(obj["source"]),
                external_id=str(obj["external_id"]), fetched_at=str(obj["fetched_at"]))
    key = Key(str(obj["source"]), str(obj["external_id"]), hashlib.sha256(raw).hexdigest())
    return Envelope(body=json.loads(json.dumps(dict(obj))), key=key)


def iso_utc(value: dt.datetime | str | None) -> str:
    """fetched_at as ISO 8601 with a zone: None -> now (UTC); a naive datetime is refused."""
    if value is None:
        value = dt.datetime.now(dt.timezone.utc)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            raise InvalidEnvelope("fetched_at is not an ISO 8601 timestamp") from None
        if parsed.tzinfo is None:
            raise InvalidEnvelope("fetched_at needs a time zone")
        return value
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise InvalidEnvelope("fetched_at must be a timezone-aware datetime")
    return value.isoformat()


def build(*, connector: str, connector_version: str, source: str, external_id: str,
          content_type: str, raw: bytes | str, fetched_at: dt.datetime | str | None = None,
          origin: str | None = None, redirect_chain: Iterable[str] = (),
          metadata: Mapping[str, object] | None = None, links: Iterable[str] = ()) -> Envelope:
    """The one way a connector makes an envelope. Raises InvalidEnvelope (nothing is sent)."""
    if isinstance(raw, str):
        try:
            data = raw.encode("utf-8")
        except UnicodeEncodeError:
            raise InvalidEnvelope("raw text is not encodable as UTF-8 (a lone surrogate)") from None
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
    else:
        raise InvalidEnvelope("raw must be bytes or str")
    if len(data) > RAW_MAX_BYTES:
        raise TooLarge(f"raw is over {RAW_MAX_BYTES} bytes")
    meta = dict(metadata or {})
    if "provenance" in meta:
        raise InvalidEnvelope("metadata.provenance is reserved for the kit")
    trust = sorted(set(meta) & set(_prov.TRUST_LABELS + ("labels",)))
    if trust:
        raise InvalidEnvelope(f"a connector may not set trust labels in metadata ({', '.join(trust)}); "
                              "intake assigns them")
    when = iso_utc(fetched_at)
    meta["provenance"] = _prov.make(connector=connector, connector_version=connector_version,
                                    source=source, external_id=external_id, fetched_at=when,
                                    origin=origin, redirect_chain=redirect_chain)
    body = {"source": source, "connector": connector, "external_id": external_id,
            "fetched_at": when, "content_type": content_type, "raw_encoding": "base64",
            "raw": base64.b64encode(data).decode("ascii"), "metadata": meta, "links": list(links)}
    return from_body(body)
