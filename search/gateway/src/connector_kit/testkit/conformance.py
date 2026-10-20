"""The conformance runner: the whole contract against one connector, one verdict.

    python -m connector_kit.testkit.conformance <module>:<factory> [<module>:<oversize factory>] [--json]

<factory> is a callable taking a ConnectorConfig and returning the connector WITH test input (for a
connector that reads a source, the factory points it at a fixture). Exit 0: every contract test
passed. Exit 1: a test failed or errored, or the factory could not be loaded (a class the kit
refuses - an overridden kit method, a non-gate host - fails here, at import). Exit 2: usage.

From Python: `run(factory, oversize_factory=None) -> Verdict` (the mutant tests use this).
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import sys
import traceback
import unittest
from dataclasses import dataclass, field

from .contract import ConnectorContract

TESTS = tuple(n for n in dir(ConnectorContract) if n.startswith("test_"))


@dataclass
class Verdict:
    passed: bool
    results: dict = field(default_factory=dict)      # test name -> "pass" | "fail: ..." | "error: ..."
    load_error: str | None = None

    @property
    def failed(self) -> list[str]:
        return [n for n, v in self.results.items() if v != "pass"]

    def to_dict(self) -> dict:
        return {"passed": self.passed, "failed": self.failed, "results": self.results, "load_error": self.load_error}


def _case_class(factory, oversize_factory):
    class _Case(ConnectorContract, unittest.TestCase):
        def make_connector(self, config):
            return factory(config)

        def make_oversize_connector(self, config):
            return oversize_factory(config) if oversize_factory else None

    return _Case


def run(factory, oversize_factory=None) -> Verdict:
    case = _case_class(factory, oversize_factory)
    suite = unittest.TestSuite(case(n) for n in TESTS)
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    results = dict.fromkeys(TESTS, "pass")
    def name(test):  # a failing subTest is reported under the test it belongs to
        return getattr(test, "test_case", test)._testMethodName

    for test, tb in res.failures:
        results[name(test)] = "fail: " + tb.strip().splitlines()[-1][:300]
    for test, tb in res.errors:
        results[name(test)] = "error: " + tb.strip().splitlines()[-1][:300]
    for test, _reason in res.skipped:
        results[name(test)] = "skipped"
    return Verdict(all(v == "pass" for v in results.values()), results)


def load(spec: str):
    mod, _, attr = spec.partition(":")
    if not mod or not attr:
        raise ValueError(f"{spec!r} is not <module>:<factory>")
    return getattr(importlib.import_module(mod), attr)


def run_spec(spec: str, oversize_spec: str | None = None) -> Verdict:
    try:
        factory = load(spec)
        oversize = load(oversize_spec) if oversize_spec else None
    except Exception as e:  # noqa: BLE001 - a class the kit refuses fails the whole contract
        tb = traceback.format_exception_only(type(e), e)[-1].strip()
        return Verdict(False, dict.fromkeys(TESTS, "error: not loaded"), load_error=tb[:500])
    return run(factory, oversize)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m connector_kit.testkit.conformance")
    ap.add_argument("factory")
    ap.add_argument("oversize_factory", nargs="?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    v = run_spec(a.factory, a.oversize_factory)
    if a.json:
        print(json.dumps(v.to_dict(), indent=1))
    else:
        if v.load_error:
            print(f"NOT LOADED: {v.load_error}")
        for name in TESTS:
            print(f"  {'PASS' if v.results[name] == 'pass' else 'FAIL'}  {name}"
                  f"{'' if v.results[name] == 'pass' else '  - ' + v.results[name]}")
        print("CONFORMANT" if v.passed else "NOT CONFORMANT")
    return 0 if v.passed else 1


if __name__ == "__main__":
    sys.exit(main())
