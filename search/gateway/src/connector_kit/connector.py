"""The connector base class: a connector COLLECTS items; the kit builds, checks and sends them.

    class MyConnector(Connector):
        id = "my-source"              # = the name its intake key carries (intake_caller_keys)
        source = "my-source"          # the envelope's source (idempotency key part)
        version = "1"
        def collect(self) -> Iterable[Item]: ...

    report = MyConnector(config).run()

THE CONTRACT (README.md has the full list; testkit/contract.py checks it):
  - a connector's only output is intake envelopes, built by the kit from the Items collect()
    yields, and sent by the kit's IntakeClient with the connector's own key;
  - it never writes an Open Brain table, never calls a model, and never opens a socket of its
    own: fetching a URL is allowed only through the search gate (plan step 3), whose hosts are
    the only ones a connector may declare in `allowed_hosts` (GATE_HOSTS);
  - `envelopes()` and `run()` are the kit's and cannot be overridden (refused at class creation).

AN ITEM'S external_id must be STABLE: the same thing collected twice gets the same id, so intake
answers a repeat (same bytes) or a new revision (changed bytes) - never a second item.
"""
from __future__ import annotations

import abc
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import ClassVar, Iterable, Iterator, Mapping

from . import envelope as _env
from .client import IntakeClient, RequestTooLarge, RetryPolicy, Submission
from .secret import load_key_file

log = logging.getLogger("connector_kit.connector")

# The hosts a connector may reach from collect(): the search gate only (PLAN-connectors.md step 3;
# the compose container name of search/gateway). Nothing else - not the web, not a model, not a DB.
GATE_HOSTS = frozenset({"search-gateway"})
KIT_METHODS = ("envelopes", "run", "__init_subclass__")


@dataclass(frozen=True)
class Item:
    external_id: str
    content_type: str
    raw: bytes | str
    fetched_at: dt.datetime | str | None = None
    origin: str | None = None
    redirect_chain: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    links: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorConfig:
    intake_url: str
    key_file: str
    options: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float = 60.0
    max_request_bytes: int = 8 * 1024 * 1024
    retry: RetryPolicy = RetryPolicy()


@dataclass(frozen=True)
class Prepared:
    external_id: str | None
    envelope: _env.Envelope | None
    refusal: str | None = None        # why the kit did not send it (nothing left the process)


@dataclass
class ItemReport:
    external_id: str | None
    sha256: str | None
    result: str            # record | stop | deferred | refused | too_large
    label: str | None = None
    http: int | None = None
    attempts: int = 0
    reason: str = ""
    intake_id: str | None = None
    repeat: bool = False
    revision: int | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class RunReport:
    connector: str
    items: list[ItemReport] = field(default_factory=list)
    stopped: str | None = None

    @property
    def counts(self) -> dict:
        c: dict[str, int] = {}
        for it in self.items:
            k = it.label if it.result == "record" else it.result
            c[k or "none"] = c.get(k or "none", 0) + 1
        return c

    def to_dict(self) -> dict:
        return {"connector": self.connector, "stopped": self.stopped, "counts": self.counts,
                "items": [i.to_dict() for i in self.items]}


class Connector(abc.ABC):
    id: ClassVar[str]
    source: ClassVar[str]
    version: ClassVar[str] = "1"
    allowed_hosts: ClassVar[tuple[str, ...]] = ()
    # modules beyond the class's own that hold connector code (the contract scans them too)
    extra_modules: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        for name in KIT_METHODS:
            if name in cls.__dict__:
                raise TypeError(f"{cls.__name__} overrides {name}(), which belongs to the kit "
                                "(a connector only collects; the kit builds and sends)")
        bad = [h for h in getattr(cls, "allowed_hosts", ()) if h not in GATE_HOSTS]
        if bad:
            raise TypeError(f"{cls.__name__} declares hosts outside the search gate: {bad} "
                            f"(allowed: {sorted(GATE_HOSTS)})")
        if not getattr(cls, "__abstractmethods__", None):
            for attr in ("id", "source"):
                if not _env.is_slug(getattr(cls, attr, None)):
                    raise TypeError(f"{cls.__name__}.{attr} must match [a-z0-9][a-z0-9._-]*")

    def __init__(self, config: ConnectorConfig):
        self.config = config

    @abc.abstractmethod
    def collect(self) -> Iterable[Item]:
        """Yield the items to send. No socket, no DB, no model (see the module docstring)."""

    # -- the kit's part -----------------------------------------------------------------------------
    def envelopes(self) -> Iterator[Prepared]:
        for item in self.collect():
            if not isinstance(item, Item):
                yield Prepared(None, None, f"not an Item ({type(item).__name__}): collect() yields Item objects")
                continue
            try:
                env = _env.build(connector=self.id, connector_version=self.version, source=self.source,
                                 external_id=item.external_id, content_type=item.content_type, raw=item.raw,
                                 fetched_at=item.fetched_at, origin=item.origin,
                                 redirect_chain=item.redirect_chain, metadata=item.metadata, links=item.links)
            except _env.InvalidEnvelope as e:
                too_large = isinstance(e, _env.TooLarge)
                yield Prepared(item.external_id if isinstance(item.external_id, str) else None, None,
                               ("too_large: " if too_large else "invalid: ") + str(e))
                continue
            yield Prepared(item.external_id, env)

    def client(self) -> IntakeClient:
        c = self.config
        return IntakeClient(c.intake_url, load_key_file(c.key_file), connector=self.id, timeout_s=c.timeout_s,
                            max_request_bytes=c.max_request_bytes, retry=c.retry)

    def run(self, client: IntakeClient | None = None) -> RunReport:
        client = client or self.client()
        report = RunReport(self.id)
        for p in self.envelopes():
            if p.envelope is None:
                result = "too_large" if (p.refusal or "").startswith("too_large") else "refused"
                report.items.append(ItemReport(p.external_id, None, result, reason=p.refusal or ""))
                log.warning("%s: item not sent (%s)", self.id, result)
                continue
            try:
                sub: Submission = client.submit(p.envelope)
            except RequestTooLarge as e:
                report.items.append(ItemReport(p.external_id, p.envelope.key.sha256, "too_large", reason=str(e)))
                log.warning("%s: item not sent (too_large)", self.id)
                continue
            o = sub.outcome
            report.items.append(ItemReport(p.external_id, p.envelope.key.sha256, sub.final, label=o.label,
                                           http=o.http, attempts=sub.attempts, reason=o.reason,
                                           intake_id=o.intake_id, repeat=o.repeat, revision=o.revision))
            if sub.final == "stop":
                report.stopped = o.reason or "stopped"
                break
            if sub.final == "deferred":
                report.stopped = f"intake unavailable after {sub.attempts} attempts ({o.reason})"
                break
        log.info("%s: run done %s%s", self.id, report.counts,
                 f"; stopped: {report.stopped}" if report.stopped else "")
        return report
