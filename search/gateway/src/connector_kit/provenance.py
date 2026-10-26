"""Provenance: what the connector knows about where an item came from (DESIGN-intake.md section 6).

Every envelope the kit builds carries `metadata.provenance`:

  connector          the connector id (= the envelope's connector = the name its intake key carries)
  connector_version  the connector's own version string
  fetched_at         when it was fetched (= the envelope's fetched_at)
  origin             where it was read from: a URL, or a connector-defined locator such as
                     "file:<name>" or "stdin" (never a host path); null when there is none
  redirect_chain     the URLs the fetch passed through, in order, origin first excluded; for a
                     connector that fetches through the search gate (plan step 3) this is the
                     gate's reported chain; [] when nothing redirected
  labels             the section 6 label fields a connector can know BEFORE intake:
                     {connector, source, external_id}

What a connector may NOT say: the trust labels are intake's. `verdict`, `intake_id`,
`classifier` and `flags` (and any key not listed above) are refused, so no connector can
pre-label its own content (a `trusted-docs` flag, say) - intake and the read policy assign them.
"""
from __future__ import annotations

from typing import Iterable, Mapping

PROVENANCE_KEYS = ("connector", "connector_version", "fetched_at", "origin", "redirect_chain", "labels")
LABEL_KEYS = ("connector", "source", "external_id")
TRUST_LABELS = ("verdict", "intake_id", "classifier", "flags")
MAX_REDIRECTS = 20
MAX_URL_CHARS = 2048
MAX_VERSION_CHARS = 64


def _err(msg: str):
    from .envelope import InvalidEnvelope
    return InvalidEnvelope("provenance: " + msg)


def _locator(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_CHARS:
        raise _err(f"{what} must be a string of 1..{MAX_URL_CHARS} characters")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise _err(f"{what} contains control characters")
    return value


def make(*, connector: str, connector_version: str, source: str, external_id: str, fetched_at: str,
         origin: str | None, redirect_chain: Iterable[str]) -> dict:
    prov = {"connector": connector, "connector_version": connector_version, "fetched_at": fetched_at,
            "origin": origin, "redirect_chain": list(redirect_chain),
            "labels": {"connector": connector, "source": source, "external_id": external_id}}
    check(prov, connector=connector, source=source, external_id=external_id, fetched_at=fetched_at)
    return prov


def check(prov: object, *, connector: str, source: str, external_id: str, fetched_at: str) -> None:
    if not isinstance(prov, Mapping):
        raise _err("must be an object")
    keys = set(prov)
    trust = sorted(keys & set(TRUST_LABELS))
    if trust:
        raise _err(f"a connector may not set trust labels ({', '.join(trust)}); intake assigns them")
    if keys != set(PROVENANCE_KEYS):
        raise _err(f"keys must be exactly {', '.join(PROVENANCE_KEYS)}")
    if prov["connector"] != connector:
        raise _err("connector differs from the envelope's")
    v = prov["connector_version"]
    if not isinstance(v, str) or not v or len(v) > MAX_VERSION_CHARS:
        raise _err(f"connector_version must be a string of 1..{MAX_VERSION_CHARS} characters")
    if prov["fetched_at"] != fetched_at:
        raise _err("fetched_at differs from the envelope's")
    if prov["origin"] is not None:
        _locator(prov["origin"], "origin")
    chain = prov["redirect_chain"]
    if not isinstance(chain, list) or len(chain) > MAX_REDIRECTS:
        raise _err(f"redirect_chain must be a list of at most {MAX_REDIRECTS} URLs")
    for u in chain:
        _locator(u, "each redirect_chain entry")
    labels = prov["labels"]
    if not isinstance(labels, Mapping):
        raise _err("labels must be an object")
    trust = sorted(set(labels) & set(TRUST_LABELS))
    if trust:
        raise _err(f"a connector may not set trust labels ({', '.join(trust)}); intake assigns them")
    if dict(labels) != {"connector": connector, "source": source, "external_id": external_id}:
        raise _err("labels must be exactly {connector, source, external_id} of this envelope")
