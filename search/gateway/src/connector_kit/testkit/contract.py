"""The contract tests every connector imports.

    import unittest
    from connector_kit.testkit.contract import ConnectorContract

    class MyConnectorContract(ConnectorContract, unittest.TestCase):
        def make_connector(self, config):            # REQUIRED: a connector with test input
            return MyConnector(config)
        def make_oversize_connector(self, config):   # optional: one whose input is over a cap
            ...

Each test runs the connector against a FakeIntake inside the Sandbox, with a fresh random
CANARY key in a key file (the connector's config names the file, as in production).

  test_declaration              id/source are slugs, the kit's methods are not overridden, every
                                declared host is a search-gate host
  test_static_scan              the connector's source holds no DB driver, model SDK, network or
                                process module, no dynamic import/exec, no table-writing SQL
  test_sends_only_valid_envelopes  at least one request reached intake; EVERY body that reached
                                it passes intake's own stage 1 (the oracle) and the kit's
                                provenance rule, names this connector, and came through the kit's
                                client; no item was refused as invalid
  test_no_table_writes_or_model_calls  no DB driver / model SDK import, no sqlite3.connect, no
                                subprocess, at run time
  test_network_only_allowed_hosts  no name lookup or connect except the kit's client to intake
                                (and the declared search-gate hosts)
  test_no_secret_in_logs        the canary key (or its base64 / hex / sha256) is in no log record,
                                stdout, stderr, request body, or the run report
  test_idempotency_key          the same input collected twice gives the same keys (source,
                                external_id, sha256), external ids are unique in a run, and the
                                second run is all repeats at intake
  test_size_caps                every body is within the request cap and its raw within 5 MB; an
                                oversize input (make_oversize_connector) is refused locally and
                                nothing over a cap reaches intake
  test_outcome_handling         per intake outcome: clean/suspicious/pending/injected/rejected are
                                recorded once; 503 and 408 are retried, then recorded; 401, 403
                                and 413 stop the run after ONE request, nothing more is sent
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import logging
import os
import secrets
import shutil
import tempfile

from .. import envelope as _env
from ..client import RetryPolicy
from ..connector import GATE_HOSTS, KIT_METHODS, Connector, ConnectorConfig
from . import static_scan
from .fake_intake import FakeIntake
from .sandbox import Sandbox

FAST_RETRY = RetryPolicy(max_attempts=4, base_s=0.01, cap_s=0.05, retry_after_cap_s=0.05, budget_s=30)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg) + " " + repr(record.args))
        if record.exc_info:
            self.lines.append(logging.Formatter().formatException(record.exc_info))


class Run:
    """One connector run against a fake intake, everything captured."""

    def __init__(self, testcase, *, default="clean", scripts=None, oversize=False, connector=None, fake=None,
                 key=None):
        self.tc = testcase
        self.key = key or "ck-" + secrets.token_hex(24)
        self.given_fake = fake
        self.dir = tempfile.mkdtemp(prefix="connector-contract-")
        self.key_file = os.path.join(self.dir, "intake_key")
        with open(self.key_file, "w", encoding="ascii") as fh:
            fh.write(self.key)
        self.default = default
        self.scripts = scripts or {}
        self.oversize = oversize
        self.connector = connector
        self.report = None
        self.error: BaseException | None = None
        self.logs: list[str] = []
        self.stdout = self.stderr = ""
        self.violations = []

    def config(self, url: str) -> ConnectorConfig:
        return ConnectorConfig(intake_url=url, key_file=self.key_file, timeout_s=10.0, retry=FAST_RETRY)

    def go(self) -> Run:
        cid = self.tc.connector_class().id
        self.fake = self.given_fake or FakeIntake({cid: self.key}, default=self.default)
        self.fake.received.clear()
        for ext, outs in self.scripts.items():
            self.fake.script(ext, *outs)
        cap = _Capture()
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(cap)
        root.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with self.fake:
                cfg = self.config(self.fake.url)
                conn = self.connector or (self.tc.make_oversize_connector(cfg) if self.oversize
                                          else self.tc.make_connector(cfg))
                cls = type(conn)
                own = static_scan.connector_modules(cls)
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with Sandbox(intake=self.fake.address, gate_hosts=cls.allowed_hosts, own_modules=own) as sb:
                        try:
                            self.report = conn.run()
                        except Exception as e:  # noqa: BLE001 - recorded; the tests decide
                            self.error = e
                            logging.getLogger("connector_kit.contract").error(
                                "the connector raised %s", type(e).__name__, exc_info=True)
                self.violations = list(sb.violations)
        finally:
            root.removeHandler(cap)
            root.setLevel(old_level)
            shutil.rmtree(self.dir, ignore_errors=True)
        self.logs = cap.lines
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        return self

    @property
    def intake_bodies(self):
        return [r for r in self.fake.received if r.caller is not None]


class ConnectorContract:
    """Mix into a unittest.TestCase; implement make_connector(config)."""

    def make_connector(self, config: ConnectorConfig) -> Connector:  # pragma: no cover - required
        raise NotImplementedError("make_connector(config) returns the connector under test")

    def make_oversize_connector(self, config: ConnectorConfig) -> Connector | None:
        return None

    # -- helpers ------------------------------------------------------------------------------------
    def connector_class(self):
        if not hasattr(self, "_cls"):
            probe = self.make_connector(ConnectorConfig(intake_url="http://intake.invalid/v1/intake",
                                                        key_file=os.devnull))
            self._cls = type(probe)
        return self._cls

    def run_connector(self, **kw) -> Run:
        return Run(self, **kw).go()

    def _external_ids(self) -> list[str]:
        r = self.run_connector()
        return [i.external_id for i in (r.report.items if r.report else [])]

    # -- the contract -------------------------------------------------------------------------------
    def test_declaration(self):
        cls = self.connector_class()
        self.assertTrue(issubclass(cls, Connector), "a connector subclasses connector_kit.Connector")
        self.assertTrue(_env.is_slug(cls.id), f"id {cls.id!r} is not a slug")
        self.assertTrue(_env.is_slug(cls.source), f"source {cls.source!r} is not a slug")
        for klass in cls.__mro__[:cls.__mro__.index(Connector)]:
            for name in KIT_METHODS:
                self.assertNotIn(name, vars(klass), f"{klass.__name__} overrides {name}(), which is the kit's")
        self.assertLessEqual(set(cls.allowed_hosts), set(GATE_HOSTS), "only search-gate hosts may be declared")

    def test_static_scan(self):
        findings = static_scan.scan_connector(self.connector_class())
        self.assertEqual(findings, [], "the connector's source uses something a connector may not")

    def test_sends_only_valid_envelopes(self):
        r = self.run_connector()
        self.assertIsNone(r.error, f"the connector raised: {r.error!r}")
        bodies = r.intake_bodies
        self.assertGreater(len(bodies), 0, "nothing reached intake")
        cid = self.connector_class().id
        for rec in bodies:
            self.assertTrue(rec.valid, f"a body intake's stage 1 refuses reached intake ({r.fake.oracle}): {rec.error}")
            env = _env.from_body(rec.envelope)      # + the kit's provenance rule
            self.assertEqual(env.connector, cid)
        refused = [i for i in r.report.items if i.result == "refused"]
        self.assertEqual(refused, [], "items the kit refused as invalid (not envelopes)")
        intake_conns = [v for v in r.violations if "intake" in v.detail]
        self.assertEqual(intake_conns, [], "something other than the kit's client talked to intake")

    def test_no_table_writes_or_model_calls(self):
        r = self.run_connector()
        bad = [v for v in r.violations if v.kind in ("import", "db", "process")]
        self.assertEqual(bad, [], "a DB driver, model SDK, HTTP library or process was used")

    def test_network_only_allowed_hosts(self):
        r = self.run_connector()
        bad = [v for v in r.violations if v.kind in ("socket", "dns")]
        self.assertEqual(bad, [], "a socket or name lookup outside intake (via the kit) and the search gate")

    def test_no_secret_in_logs(self):
        r = self.run_connector()
        forms = {r.key, base64.b64encode(r.key.encode()).decode(), r.key.encode().hex(),
                 hashlib.sha256(r.key.encode()).hexdigest()}
        places = {"log": "\n".join(r.logs), "stdout": r.stdout, "stderr": r.stderr,
                  "report": repr(r.report.to_dict() if r.report else None),
                  "bodies": "\n".join(rec.body.decode("utf-8", "replace") for rec in r.fake.received)}
        for where, text in places.items():
            for form in forms:
                self.assertNotIn(form, text, f"the connector's key appears in its {where}")

    def test_idempotency_key(self):
        first = self.run_connector()
        self.assertIsNone(first.error, f"the connector raised: {first.error!r}")
        ids = [i.external_id for i in first.report.items]
        self.assertEqual(len(ids), len(set(ids)), "external ids repeat within one run")
        # the second run goes to the SAME fake store (same key): every item must be a repeat
        second = self.run_connector(fake=first.fake, key=first.key)
        self.assertIsNone(second.error, f"the connector raised: {second.error!r}")

        def keys(run):
            return [_env.from_body(rec.envelope).key for rec in run.intake_bodies if rec.envelope]
        self.assertTrue(keys(first), "nothing reached intake")
        self.assertEqual(keys(first), keys(second), "the same input gave different idempotency keys")
        recorded = [i for i in second.report.items if i.result == "record"]
        self.assertTrue(recorded and all(i.repeat for i in recorded),
                        "the second run of the same input was not all repeats at intake")

    def test_size_caps(self):
        r = self.run_connector()
        for rec in r.intake_bodies:
            self.assertLessEqual(len(rec.body), r.fake.max_request_bytes)
            self.assertLessEqual(len(_env.check(rec.envelope)), _env.RAW_MAX_BYTES)
        cfg_probe = ConnectorConfig(intake_url="http://intake.invalid/v1/intake", key_file=os.devnull)
        if self.make_oversize_connector(cfg_probe) is None:
            return
        big = self.run_connector(oversize=True)
        self.assertIsNone(big.error, f"the connector raised on oversize input: {big.error!r}")
        self.assertTrue(any(i.result == "too_large" for i in big.report.items),
                        "an oversize input was not refused locally as too_large")
        for rec in big.intake_bodies:
            self.assertLessEqual(len(_env.check(rec.envelope)), _env.RAW_MAX_BYTES)

    def test_outcome_handling(self):
        ids = self._external_ids()
        self.assertTrue(ids, "the connector produced no item")
        first = ids[0]
        for label in ("clean", "suspicious", "pending", "injected", "rejected"):
            with self.subTest(outcome=label):
                r = self.run_connector(default=label)
                self.assertIsNone(r.report.stopped)
                for item in r.report.items:
                    self.assertEqual((item.result, item.label, item.attempts), ("record", label, 1))
                self.assertEqual(len(r.intake_bodies), len(ids), "each item sent exactly once")
        for code in (503, "503:busy", "503:inflight", "503:no-key", 408):
            with self.subTest(outcome=code):
                r = self.run_connector(scripts={first: [code, code, "clean"]})
                it = r.report.items[0]
                self.assertEqual((it.result, it.label, it.attempts), ("record", "clean", 3), f"{code} is retried")
                self.assertIsNone(r.report.stopped)
        with self.subTest(outcome="503 forever"):
            r = self.run_connector(default=503)
            it = r.report.items[0]
            self.assertEqual((it.result, it.attempts), ("deferred", FAST_RETRY.max_attempts))
            self.assertIsNotNone(r.report.stopped)
            self.assertEqual(len(r.intake_bodies), FAST_RETRY.max_attempts, "nothing after the deferred item")
        for code in (401, 403, 413):
            with self.subTest(outcome=code):
                r = self.run_connector(default=code)
                self.assertEqual(len(r.intake_bodies), 1, f"{code}: exactly one request, then stop")
                self.assertEqual((r.report.items[0].result, r.report.items[0].http), ("stop", code))
                self.assertIsNotNone(r.report.stopped)
                self.assertEqual(len(r.report.items), 1, f"{code}: no item after the stop")
