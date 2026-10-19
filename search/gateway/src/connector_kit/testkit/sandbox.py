"""The run-time sandbox the contract tests run a connector in (process-wide patches, for tests only).

While it is active, it RECORDS and REFUSES:
  socket  a name lookup or a connect to any address except intake's (and then only from the kit's
          client: a connector talking to intake on its own is a bypass) and the search gate hosts
          the connector declared (connector.GATE_HOSTS); a unix socket
  import  a DB driver, a model SDK, or a third-party HTTP library (FORBIDDEN_MODULES), from
          anywhere but the kit; a stdlib network module (NETWORK_MODULES) from the connector's
          own modules
  db      sqlite3.connect (an already-imported driver cannot be un-imported, so its entry point is
          trapped)
  process subprocess / os.system / os.popen / os.exec* / os.spawn* / os.posix_spawn (a curl is a
          fetch that bypasses the gate)

A refusal raises inside the connector (PermissionError / ImportError / socket.gaierror); the
connector may swallow it, which is why the contract fails on the RECORD, not on the exception.
"""
from __future__ import annotations

import builtins
import importlib
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Iterable

FORBIDDEN_MODULES = frozenset({
    # Open Brain's database and every other DB driver / ORM
    "psycopg", "psycopg2", "psycopg_pool", "asyncpg", "pg8000", "sqlalchemy", "sqlite3", "pymysql",
    "MySQLdb", "redis", "pymongo", "supabase", "postgrest",
    # model SDKs and runtimes
    "openai", "anthropic", "litellm", "transformers", "torch", "ollama", "llama_cpp", "google",
    "cohere", "mistralai", "langchain", "langchain_core", "huggingface_hub",
    # third-party HTTP clients (a fetch outside the search gate)
    "requests", "httpx", "aiohttp", "urllib3", "websocket", "websockets", "pycurl",
})
NETWORK_MODULES = frozenset({"socket", "ssl", "http", "urllib.request", "ftplib", "smtplib", "poplib",
                             "imaplib", "telnetlib", "xmlrpc", "asyncio", "selectors", "subprocess",
                             "multiprocessing", "ctypes"})
KIT_PREFIX = "connector_kit"
CLIENT_MODULE = "connector_kit.client"


@dataclass(frozen=True)
class Violation:
    kind: str     # socket | dns | import | db | process
    detail: str


def _root(name: str) -> str:
    return name.split(".", 1)[0]


def _network_name(name: str) -> bool:
    return name in NETWORK_MODULES or _root(name) in NETWORK_MODULES - {"urllib"} or name.startswith("urllib.request")


class Sandbox:
    """with Sandbox(intake=("127.0.0.1", port), gate_hosts=(), own_modules=("my_pkg.mod",)) as sb: ..."""

    _lock = threading.Lock()

    def __init__(self, *, intake: tuple[str, int] | None = None, gate_hosts: Iterable[str] = (),
                 own_modules: Iterable[str] = ()):
        self.intake = intake
        self.gate_hosts = frozenset(gate_hosts)
        self.own = tuple(own_modules)
        self.violations: list[Violation] = []
        self._gate_ips: set[str] = set()
        self._saved: list[tuple[object, str, object]] = []

    # -- bookkeeping --------------------------------------------------------------------------------
    def _record(self, kind: str, detail: str) -> None:
        self.violations.append(Violation(kind, detail[:300]))

    def _patch(self, obj, name, value) -> None:
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    @staticmethod
    def _from_client() -> bool:
        f = sys._getframe(2)
        while f is not None:
            if f.f_globals.get("__name__") == CLIENT_MODULE:
                return True
            f = f.f_back
        return False

    def _importer_is_kit(self, globals_) -> bool:
        name = (globals_ or {}).get("__name__", "") or ""
        return name == KIT_PREFIX or name.startswith(KIT_PREFIX + ".")

    def _importer_is_own(self, globals_) -> bool:
        name = (globals_ or {}).get("__name__", "") or ""
        return any(name == m or name.startswith(m + ".") for m in self.own)

    # -- the patches --------------------------------------------------------------------------------
    def __enter__(self):
        Sandbox._lock.acquire()
        sb = self
        real_getaddrinfo = socket.getaddrinfo
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        real_import = builtins.__import__
        real_import_module = importlib.import_module

        def getaddrinfo(host, port, *a, **k):
            h = host.decode() if isinstance(host, bytes) else host
            if sb.intake and h == sb.intake[0]:
                return real_getaddrinfo(host, port, *a, **k)
            if h in sb.gate_hosts:
                res = real_getaddrinfo(host, port, *a, **k)
                sb._gate_ips.update(r[4][0] for r in res)
                return res
            sb._record("dns", f"name lookup of {h!r}")
            raise socket.gaierror(socket.EAI_NONAME, "refused by the connector sandbox")

        def check(sock, address, real):
            if sock.family not in (socket.AF_INET, socket.AF_INET6):
                sb._record("socket", f"a non-IP socket ({sock.family!r})")
                raise PermissionError("refused by the connector sandbox")
            host, port = address[0], address[1]
            if sb.intake and (host, port) == tuple(sb.intake):
                if not sb._from_client():
                    sb._record("socket", f"a connect to intake {host}:{port} from outside the kit's client")
                    raise PermissionError("refused by the connector sandbox: only the kit's client talks to intake")
                return real(sock, address)
            if host in sb._gate_ips or host in sb.gate_hosts:
                return real(sock, address)
            sb._record("socket", f"a connect to {host}:{port}")
            raise PermissionError("refused by the connector sandbox")

        def connect(sock, address):
            return check(sock, address, real_connect)

        def connect_ex(sock, address):
            return check(sock, address, real_connect_ex)

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
            if level == 0 and not sb._importer_is_kit(globals):
                if _root(name) in FORBIDDEN_MODULES:
                    sb._record("import", f"import {name}")
                    raise ImportError(f"{name} is not allowed in a connector (connector sandbox)")
                if sb._importer_is_own(globals) and _network_name(name):
                    sb._record("import", f"import {name} (a network module) from the connector")
                    raise ImportError(f"{name} is not allowed in a connector (connector sandbox)")
            return real_import(name, globals, locals, fromlist, level)

        def import_module(name, package=None):
            if _root(name) in FORBIDDEN_MODULES or _network_name(name):
                sb._record("import", f"importlib.import_module({name!r})")
                raise ImportError(f"{name} is not allowed in a connector (connector sandbox)")
            return real_import_module(name, package)

        def refuse(kind, what):
            def _r(*_a, **_k):
                sb._record(kind, what)
                raise PermissionError(f"{what} is refused by the connector sandbox")
            return _r

        self._patch(socket, "getaddrinfo", getaddrinfo)
        self._patch(socket.socket, "connect", connect)
        self._patch(socket.socket, "connect_ex", connect_ex)
        self._patch(builtins, "__import__", guarded_import)
        self._patch(importlib, "import_module", import_module)
        if "sqlite3" in sys.modules:
            self._patch(sys.modules["sqlite3"], "connect", refuse("db", "sqlite3.connect"))
        self._patch(subprocess, "Popen", refuse("process", "subprocess.Popen"))
        for name in ("system", "popen", "posix_spawn", "posix_spawnp", "execv", "execve", "execvp", "execl",
                     "execlp", "spawnv", "spawnve", "spawnl"):
            if hasattr(os, name):
                self._patch(os, name, refuse("process", f"os.{name}"))
        return self

    def __exit__(self, *exc):
        try:
            while self._saved:
                obj, name, value = self._saved.pop()
                setattr(obj, name, value)
        finally:
            Sandbox._lock.release()
        return False
