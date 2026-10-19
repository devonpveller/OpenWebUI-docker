"""The static half of the contract: read a connector's SOURCE before it runs.

scan_connector(cls) parses the module that defines the connector class (plus the modules it names
in `extra_modules`) and reports:
  import    a DB driver, model SDK or HTTP library (sandbox.FORBIDDEN_MODULES), or a stdlib network /
            process module (sandbox.NETWORK_MODULES: socket, http.*, urllib.request, subprocess, ...)
  dynamic   __import__, importlib, exec, eval, compile (each can hide an import or a call)
  call      os.system / os.popen / os.exec* / os.spawn*, *.create_connection, *.open_connection
  sql       a string literal that writes a table (INSERT INTO, UPDATE ... SET, DELETE FROM, COPY ...
            FROM, TRUNCATE, ALTER/CREATE/DROP TABLE, MERGE INTO, UPSERT)

It complements the sandbox: the sandbox sees what ran, this sees what COULD run on a path the test
input did not reach. A connector that needs a flagged construct does not belong behind this kit
(fetching is the search gate's job, writing is intake's).
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from dataclasses import dataclass

from .sandbox import FORBIDDEN_MODULES, NETWORK_MODULES

_SQL = re.compile(r"\b(insert\s+into|update\s+[\w.\"]+\s+set|delete\s+from|copy\s+[\w.\"]+\s+from|truncate\s+"
                  r"|alter\s+table|create\s+table|drop\s+table|merge\s+into|upsert\s)", re.I)
_DYNAMIC = {"__import__", "exec", "eval", "compile"}
_OS_CALLS = {"system", "popen", "execv", "execve", "execvp", "execl", "execlp", "spawnv", "spawnve", "spawnl",
             "posix_spawn", "posix_spawnp", "fork", "forkpty"}
_NET_ATTRS = {"create_connection", "open_connection", "urlopen", "socket"}


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    line: int


def _bad_module(name: str) -> bool:
    root = name.split(".", 1)[0]
    if root in FORBIDDEN_MODULES:
        return True
    if root == "urllib":
        return name.startswith("urllib.request") or name == "urllib"  # `import urllib` hides .request
    if root == "importlib":
        return True
    return root in NETWORK_MODULES or name in NETWORK_MODULES


def scan_source(text: str, filename: str = "<connector>") -> list[Finding]:
    out: list[Finding] = []
    tree = ast.parse(text, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _bad_module(a.name):
                    out.append(Finding("import", f"import {a.name}", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and _bad_module(mod):
                out.append(Finding("import", f"from {mod} import ...", node.lineno))
            elif node.level == 0 and mod == "urllib" and any(a.name == "request" for a in node.names):
                out.append(Finding("import", "from urllib import request", node.lineno))
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _DYNAMIC:
                out.append(Finding("dynamic", f"{f.id}(...)", node.lineno))
            elif isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else ""
                if base == "os" and f.attr in _OS_CALLS:
                    out.append(Finding("call", f"os.{f.attr}(...)", node.lineno))
                elif f.attr in _NET_ATTRS:
                    out.append(Finding("call", f"{base or '...'}.{f.attr}(...)", node.lineno))
                elif base == "importlib" or f.attr == "import_module":
                    out.append(Finding("dynamic", f"{base or '...'}.{f.attr}(...)", node.lineno))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            m = _SQL.search(node.value)
            if m:
                out.append(Finding("sql", f"a table write in a string literal ({m.group(1).strip()!r})",
                                   getattr(node, "lineno", 0)))
    return out


def connector_modules(cls) -> list[str]:
    return [cls.__module__, *getattr(cls, "extra_modules", ())]


def scan_connector(cls) -> list[Finding]:
    out: list[Finding] = []
    for name in connector_modules(cls):
        mod = sys.modules.get(name)
        if mod is None:
            out.append(Finding("source", f"module {name} is not loaded", 0))
            continue
        try:
            path = inspect.getsourcefile(mod)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (TypeError, OSError) as e:
            out.append(Finding("source", f"the source of {name} cannot be read ({type(e).__name__})", 0))
            continue
        out.extend(Finding(f.kind, f"{name}:{f.line}: {f.detail}", f.line) for f in scan_source(text, path))
    return out
