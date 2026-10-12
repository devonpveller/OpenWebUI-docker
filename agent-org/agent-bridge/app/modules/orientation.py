"""orientation - a deterministic, model-free PROJECT ORIENTATION map (ao-orient, 2026-10-11).

Why. C7 workers spent most of their calls orienting one file per call. The old answer was a model
"survey" run as a worker turn on the SHARED worker pool (`Router.survey_project`, removed here); it
raced real work for slots three rounds in a row (blocking intake, dispatch waits, slot occupancy, a
preemption that left a slot busy until restart) and cached its failures for every later effort
(findings/ao-survey.md). This module replaces it with facts the bridge reads itself:

* no worker turn, no pooled slot, no model, no /check - the repo is read through the GitHub REST
  API with the App's installation token, the same path the bridge already uses for the expected base
  (`capabilities.read_default_branch_head`) and the planner's repo state (`read_repo_state`). The
  bridge has no checkout of its own and needs none; nothing here opens a new egress path (the bridge
  already talks to `AO_GITHUB_API_BASE`; workers' git egress proxy is not involved, so there is no
  race with its allowlist reload).
* the map: file tree (bounded depth and lines; vendored/generated dirs skipped), languages by
  extension, key files (manifests, entry points, configs), test folders and the likely test command
  (derived from manifests; the command is chosen from a fixed vocabulary, never copied from a file),
  README headings, recently changed files (paths only).
* cached per project + commit: same base = a dict lookup; base moved = one rebuild. A FAILED build is
  never cached: that effort gets "orientation unavailable" and the next call retries.

Threat model (repo content is untrusted): no file content leaves this module except README headings,
which are bounded, sanitised (control characters removed) and redacted; every other line is a path,
a count or a fixed phrase. Paths are sanitised and redacted too (a token can be planted in a file
name). There is no filesystem, so a symlink cannot be followed anywhere: a symlink is just a path
entry and is never read. Every build is bounded in time (`timeout_s`), API calls, bytes per response
and output size, so a huge or hostile repo cannot stall intake.

The map is facts to start from, not a decision: the worker still chooses what to read within its
scope (the injected header says so).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

from .capabilities import _headers, parse_owner_repo
from .github_app import GitHubAppError

log = logging.getLogger("agent_bridge.orientation")

# ── bounds (a huge or hostile repo must not stall intake) ─────────────────────────────────────
MAX_RESPONSE_BYTES = 8 * 1024 * 1024    # any single API response
MAX_TREE_ENTRIES = 20_000               # paths considered at all
BFS_LISTING_BUDGET = 150                # directory listings when the trees endpoint is absent
BFS_CONCURRENCY = 6
BFS_MAX_DEPTH = 8
MAX_BLOB_BYTES = 256 * 1024             # a manifest/README larger than this is not read
MAX_MANIFEST_READS = 12
RECENT_COMMITS = 10
RECENT_PATHS = 15
README_HEADINGS = 25
HEADING_CHARS = 100
PATH_CHARS = 160
TREE_LINES = 40
LIST_ITEMS = 12
MAX_MAP_CHARS = 4000

HEADER = "PROJECT ORIENTATION"

# Directory names never descended into (vendored, generated, caches). Matched per path segment.
SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "third_party", "third-party", "dist", "build", "out",
    "target", "__pycache__", ".venv", "venv", "env", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".cache", "coverage", "htmlcov", ".next", ".nuxt",
    "site-packages", ".gradle", ".idea", ".vscode", "bower_components", ".terraform", "Pods",
    "bin", "obj", "generated", "__generated__", ".eggs",
})

LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".cs": "C#", ".c": "C", ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala", ".lua": "Lua",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue",
    ".svelte": "Svelte", ".md": "Markdown", ".rst": "reStructuredText", ".yml": "YAML",
    ".yaml": "YAML", ".toml": "TOML", ".json": "JSON", ".dart": "Dart", ".ex": "Elixir",
    ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell", ".ml": "OCaml", ".r": "R", ".jl": "Julia",
    ".tf": "Terraform", ".dockerfile": "Dockerfile", ".zig": "Zig", ".nim": "Nim",
}

# Manifests: their presence is a key file; their CONTENT (read, never emitted) picks a test command.
MANIFESTS = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini", "tox.ini",
    "package.json", "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "Makefile", "CMakeLists.txt", "deno.json", "mix.exs",
})
CONFIG_FILES = frozenset({
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "tsconfig.json", "ruff.toml", ".ruff.toml", "mypy.ini", ".pre-commit-config.yaml",
    "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js", "conftest.py",
    "noxfile.py", ".gitmodules", ".env.example",
})
ENTRY_POINTS = frozenset({
    "main.py", "__main__.py", "app.py", "manage.py", "cli.py", "server.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts", "main.go",
    "main.rs", "lib.rs", "Program.cs", "main.c", "main.cpp",
})
TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs", "testing", "e2e"})
_TEST_FILE = re.compile(
    r"(^test_.*\.py$|.*_test\.py$|.*_test\.go$|.*\.(test|spec)\.[cm]?[jt]sx?$|.*Tests?\.cs$"
    r"|.*_spec\.rb$|.*Test\.java$|^test-.*\.ps1$|.*\.Tests\.ps1$)")

# ── redaction (paths and README headings are the only repo-derived text that is emitted) ─────
# The bridge has no redactor of its own on this line; these are the house shapes from little-coder's
# `sanitize.py` (redact_secrets + the Sanitizer's secret patterns), plus one shape-agnostic rule: a
# long token-shaped run mixing letters and digits is masked whatever its prefix. Over-redaction of a
# heading or a file name costs nothing; a leaked secret costs a rotation.
REDACTED = "[REDACTED]"
_SECRET_SHAPES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@"),       # scheme://user:secret@
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)\b\s*[:=]\s*\S+"),
]
_LONG_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def redact(text: str) -> str:
    """Mask secret-shaped substrings. Non-raising, idempotent."""
    if not text:
        return text
    for pat in _SECRET_SHAPES:
        text = pat.sub(REDACTED, text)

    def _mask_run(m: re.Match[str]) -> str:
        s = m.group(0)
        has_digit = any(ch.isdigit() for ch in s)
        has_alpha = any(ch.isalpha() for ch in s)
        return REDACTED if has_digit and has_alpha else s
    return _LONG_RUN.sub(_mask_run, text)


def clean(text: str, limit: int) -> str:
    """One safe display line from untrusted text: control/format characters (newlines, bidi
    overrides, zero-width) become '?', whitespace collapses, then redaction, then the length bound."""
    text = _CONTROL.sub("?", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = redact(text)
    return text if len(text) <= limit else text[: limit - 1] + "~"


# ── the repo reader seam ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    path: str
    kind: str   # "file" | "dir" | "link" | "submodule"


class RepoSource(Protocol):
    async def resolve_head(self) -> str: ...
    async def list_tree(self, sha: str) -> tuple[list[Entry], str]: ...
    async def read_file(self, path: str, sha: str) -> bytes | None: ...
    async def recent_paths(self, sha: str) -> list[str]: ...
    async def aclose(self) -> None: ...


class OrientationError(RuntimeError):
    """A build could not complete (unreadable repo, API error, bound exceeded)."""


class GitHubRestSource:
    """Reads one repo through the GitHub REST API with the App's installation token.

    Uses only endpoints the bridge's existing GitHub callers already rely on (repo metadata,
    branches, contents, commits, compare), plus `git/trees?recursive=1` when the server has it (one
    call on real GitHub; the C7 GitHub stand-in does not emulate it, so the contents API is walked
    breadth-first under a listing budget instead)."""

    def __init__(self, github, owner: str, repo: str, *, api_base: str,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._github = github
        self._owner, self._repo = owner, repo
        self._base = f"{api_base.rstrip('/')}/repos/{owner}/{repo}"
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.calls = 0

    async def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            try:
                token = await self._github.installation_token()
            except GitHubAppError as exc:
                raise OrientationError(f"no installation token: {exc}") from exc
            self._client = httpx.AsyncClient(timeout=10.0, transport=self._transport,
                                             headers=_headers(token), follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str) -> tuple[int, object]:
        """(status, parsed JSON or None). The body is read in chunks and abandoned past the bound."""
        c = await self._c()
        self.calls += 1
        try:
            async with c.stream("GET", f"{self._base}{path}") as r:
                if r.status_code >= 400:
                    return r.status_code, None
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_RESPONSE_BYTES:
                        raise OrientationError(f"response over {MAX_RESPONSE_BYTES} bytes: {path[:80]}")
                status = r.status_code
        except httpx.HTTPError as exc:
            raise OrientationError(f"GET {path[:80]} failed: {type(exc).__name__}") from exc
        try:
            return status, json.loads(bytes(buf) or b"null")
        except ValueError as exc:
            raise OrientationError(f"GET {path[:80]}: not JSON") from exc

    async def resolve_head(self) -> str:
        st, meta = await self._get("")
        if st != 200 or not isinstance(meta, dict):
            raise OrientationError(f"repo metadata unreadable (HTTP {st})")
        ref = meta.get("default_branch") or "main"
        st, br = await self._get(f"/branches/{quote(str(ref), safe='')}")
        sha = ((br or {}).get("commit") or {}).get("sha", "") if isinstance(br, dict) else ""
        if st != 200 or not sha:
            raise OrientationError(f"default branch head unreadable (HTTP {st})")
        return str(sha)

    async def list_tree(self, sha: str) -> tuple[list[Entry], str]:
        st, body = await self._get(f"/git/trees/{quote(sha, safe='')}?recursive=1")
        if st == 200 and isinstance(body, dict) and isinstance(body.get("tree"), list):
            out: list[Entry] = []
            for t in body["tree"][:MAX_TREE_ENTRIES]:
                if not isinstance(t, dict) or not isinstance(t.get("path"), str):
                    continue
                typ, mode, path = t.get("type"), str(t.get("mode", "")), t["path"]
                segs = path.split("/")
                if any(seg in SKIP_DIRS for seg in segs[:-1]):
                    continue                        # inside a skipped dir
                kind = ("dir" if typ == "tree" else "submodule" if typ == "commit"
                        else "link" if mode == "120000" else "file")
                if kind == "dir" and segs[-1] in SKIP_DIRS:
                    kind = "skipped"
                out.append(Entry(path, kind))
            note = "tree (truncated by the server)" if body.get("truncated") else "tree"
            if len(body["tree"]) > MAX_TREE_ENTRIES:
                note = f"tree (first {MAX_TREE_ENTRIES} entries)"
            return out, note
        if st not in (404, 405, 409, 422, 501):
            raise OrientationError(f"tree unreadable (HTTP {st})")
        return await self._walk_contents(sha)

    async def _walk_contents(self, sha: str) -> tuple[list[Entry], str]:
        """Breadth-first over the contents API, shallow levels first, under a listing budget."""
        ref = quote(sha, safe="")
        out: list[Entry] = []
        level, depth, listings, exhausted = [""], 0, 0, False
        sem = asyncio.Semaphore(BFS_CONCURRENCY)

        async def listing(d: str):
            async with sem:
                p = f"/contents/{quote(d)}?ref={ref}" if d else f"/contents?ref={ref}"
                return d, await self._get(p)

        while level and depth <= BFS_MAX_DEPTH:
            room = BFS_LISTING_BUDGET - listings
            if room <= 0:
                exhausted = True
                break
            batch, rest = level[:room], level[room:]
            exhausted = exhausted or bool(rest)
            listings += len(batch)
            results = await asyncio.gather(*(listing(d) for d in batch))
            nxt: list[str] = []
            for d, (st, body) in results:
                if st != 200 or not isinstance(body, list):
                    if d == "":
                        raise OrientationError(f"root listing unreadable (HTTP {st})")
                    continue
                for it in body:
                    if not isinstance(it, dict) or not isinstance(it.get("path"), str):
                        continue
                    typ, path = it.get("type"), it["path"]
                    name = path.rsplit("/", 1)[-1]
                    if typ == "dir":
                        if name in SKIP_DIRS:
                            out.append(Entry(path, "skipped"))
                            continue
                        out.append(Entry(path, "dir"))
                        nxt.append(path)
                    else:
                        out.append(Entry(path, {"symlink": "link", "submodule": "submodule"}
                                         .get(str(typ), "file")))
                    if len(out) >= MAX_TREE_ENTRIES:
                        return out, f"contents walk (first {MAX_TREE_ENTRIES} entries)"
            level, depth = sorted(nxt), depth + 1
        note = "contents walk" + (f" (listing budget {BFS_LISTING_BUDGET} reached; deeper dirs "
                                  "not listed)" if exhausted or level else "")
        return out, note

    async def read_file(self, path: str, sha: str) -> bytes | None:
        st, body = await self._get(f"/contents/{quote(path)}?ref={quote(sha, safe='')}")
        if st != 200 or not isinstance(body, dict) or body.get("type") != "file":
            return None      # a symlink/submodule/dir is never read through
        if int(body.get("size") or 0) > MAX_BLOB_BYTES or body.get("encoding") != "base64":
            return None
        try:
            raw = base64.b64decode(body.get("content") or "", validate=False)
        except ValueError:
            return None
        return raw[:MAX_BLOB_BYTES]

    async def recent_paths(self, sha: str) -> list[str]:
        """Paths changed over the last RECENT_COMMITS first-parent commits (compare oldest..sha)."""
        cur, oldest = sha, ""
        for _ in range(RECENT_COMMITS):
            st, body = await self._get(f"/commits/{quote(cur, safe='')}")
            if st != 200 or not isinstance(body, dict):
                break
            parents = body.get("parents") or []
            if not parents or not isinstance(parents[0], dict) or not parents[0].get("sha"):
                break
            cur = oldest = str(parents[0]["sha"])
        if not oldest:
            return []
        st, cmp_ = await self._get(f"/compare/{quote(oldest, safe='')}...{quote(sha, safe='')}")
        if st != 200 or not isinstance(cmp_, dict):
            return []
        seen: list[str] = []
        for f in cmp_.get("files") or []:
            name = f.get("filename") if isinstance(f, dict) else None
            if isinstance(name, str) and name not in seen:
                seen.append(name)
        return seen


def rest_source_for(github, repo_url: str, *, api_base: str,
                    transport: httpx.AsyncBaseTransport | None = None) -> GitHubRestSource | None:
    """A reader for `repo_url`, or None when the App cannot read it (no App, not a GitHub URL, or
    not the App's own account - the same rule as every other App read in the bridge)."""
    if github is None or not repo_url:
        return None
    try:
        owner, repo = parse_owner_repo(repo_url)
    except ValueError:
        return None
    if owner.lower() != (getattr(github, "owner", "") or "").lower():
        return None
    return GitHubRestSource(github, owner, repo, api_base=api_base, transport=transport)


# ── the map (pure: entries + a few file reads -> bounded text) ────────────────────────────────

def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _depth(path: str) -> int:
    return path.count("/")


def _test_command(name: str, content: str) -> str | None:
    """A test command from a FIXED vocabulary, chosen by what a manifest declares. File content
    picks the command; it is never copied into the map."""
    if name == "pyproject.toml":
        return "python -m pytest" if re.search(r"\[tool\.pytest|\bpytest\b", content) else None
    if name in ("pytest.ini",):
        return "python -m pytest"
    if name == "tox.ini":
        return "python -m pytest" if re.search(r"\[pytest\]|\bpytest\b", content) else "tox"
    if name == "setup.cfg":
        return "python -m pytest" if "[tool:pytest]" in content else None
    if name == "package.json":
        try:
            scripts = (json.loads(content) or {}).get("scripts") or {}
        except (ValueError, AttributeError):
            return None
        t = scripts.get("test") if isinstance(scripts, dict) else None
        if isinstance(t, str) and t.strip() and "no test specified" not in t:
            return "npm test"
        return None
    if name == "go.mod":
        return "go test ./..."
    if name == "Cargo.toml":
        return "cargo test"
    if name == "Makefile":
        return "make test" if re.search(r"(?m)^test\s*:", content) else None
    if name == "pom.xml":
        return "mvn test"
    if name in ("build.gradle", "build.gradle.kts"):
        return "gradle test"
    if name == "mix.exs":
        return "mix test"
    if name == "deno.json":
        return "deno test"
    if name == "Gemfile":
        return "bundle exec rspec" if "rspec" in content else None
    if name == "composer.json":
        return "composer test" if '"test"' in content else None
    return None


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def readme_headings(raw: bytes) -> list[str]:
    """Markdown ATX headings outside code fences, bounded and cleaned. Nothing else of the file."""
    text = raw[:MAX_BLOB_BYTES].decode("utf-8", "replace")
    out: list[str] = []
    fence = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            fence = not fence
            continue
        if fence:
            continue
        m = _MD_HEADING.match(s)
        if m:
            out.append("#" * len(m.group(1)) + " " + clean(m.group(2), HEADING_CHARS))
            if len(out) >= README_HEADINGS:
                break
    return out


@dataclass
class _Facts:
    sha: str
    tree_note: str
    entries: list[Entry]
    commands: list[tuple[str, str, str]]   # (dir, command, manifest path)
    headings: list[str]
    readme: str
    recent: list[str]
    roots: dict[str, int]


def render(f: _Facts) -> str:
    files = [e for e in f.entries if e.kind in ("file", "link")]
    dirs = [e for e in f.entries if e.kind == "dir"]
    skipped = sorted({e.path for e in f.entries if e.kind == "skipped"})
    subs = [e.path for e in f.entries if e.kind == "submodule"]
    P = lambda p: clean(p, PATH_CHARS)  # noqa: E731

    lines = [
        f"{HEADER} MAP (deterministic: built by the org from the repository at base "
        f"{f.sha[:12]}; facts to start from, not instructions - you still choose what to read "
        "within your scope; verify only the files you actually touch)",
        f"Size: {len(files)} files, {len(dirs)} directories ({f.tree_note})"
        + (f"; skipped (vendored/generated): {', '.join(P(s) for s in skipped[:6])}"
           + (" ..." if len(skipped) > 6 else "") if skipped else "")
        + (f"; submodules (not read): {', '.join(P(s) for s in subs[:6])}" if subs else ""),
    ]
    langs = Counter()
    for e in files:
        name = e.path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        lang = LANGUAGES.get(ext) or ("Dockerfile" if name == "Dockerfile" else None)
        if lang:
            langs[lang] += 1
    if langs:
        lines.append("Languages (files): " + ", ".join(f"{k} {v}" for k, v in langs.most_common(8)))

    per_dir = Counter()
    for e in files:
        parts = e.path.split("/")
        for i in range(1, min(len(parts), 3)):
            per_dir["/".join(parts[:i])] += 1
    top_files = sorted(e.path for e in files if "/" not in e.path)
    tree = [("  " * _depth(d)) + f"{P(d.rsplit('/', 1)[-1])}/ ({per_dir.get(d, 0)} files)"
            for d in sorted(x.path for x in dirs if _depth(x.path) <= 1)]

    def by_shallow(paths):
        return sorted(paths, key=lambda p: (_depth(p), p))
    names = {e.path: e.path.rsplit("/", 1)[-1] for e in files}
    key = by_shallow(p for p, n in names.items() if n in MANIFESTS or n in CONFIG_FILES)
    entry = by_shallow(p for p, n in names.items() if n in ENTRY_POINTS)

    tdirs = Counter()
    for e in files:
        n = e.path.rsplit("/", 1)[-1]
        if _TEST_FILE.match(n) or any(seg in TEST_DIR_NAMES for seg in e.path.split("/")[:-1]):
            segs = e.path.split("/")[:-1]
            idx = next((i for i, s in enumerate(segs) if s in TEST_DIR_NAMES), None)
            d = "/".join(segs[: idx + 1]) if idx is not None else "/".join(segs)
            tdirs[d or "."] += 1
    # The facts a worker starts from come first; the tree is last and is what gets shortened to
    # keep the whole map inside MAX_MAP_CHARS.
    if tdirs:
        top = sorted(tdirs.items(), key=lambda kv: (-kv[1], kv[0]))[:LIST_ITEMS]
        lines.append("Test folders (files): " + ", ".join(f"{P(d)}/ ({n})" for d, n in top)
                     + (f" ... (+{len(tdirs) - LIST_ITEMS})" if len(tdirs) > LIST_ITEMS else ""))
    if f.commands:
        lines.append("Likely test commands (from manifests):")
        for d, cmd, src in f.commands[:6]:
            where = f"cd {P(d)} && {cmd}" if d else cmd
            lines.append(f"  {where}   [{P(src)}]")
        if len(f.commands) > 6:
            lines.append(f"  ... {len(f.commands) - 6} more")
    elif tdirs:
        lines.append("Likely test command: not declared in a manifest (look at the test folders)")
    if key:
        lines.append("Key files: " + ", ".join(P(p) for p in key[:LIST_ITEMS * 2])
                     + (f" ... (+{len(key) - LIST_ITEMS * 2})" if len(key) > LIST_ITEMS * 2 else ""))
    if entry:
        lines.append("Entry points: " + ", ".join(P(p) for p in entry[:LIST_ITEMS])
                     + (f" ... (+{len(entry) - LIST_ITEMS})" if len(entry) > LIST_ITEMS else ""))
    if f.headings:
        heads, used = [], 0
        for h in f.headings:                       # bounded as a whole, not only per heading
            if used + len(h) > 1000:
                heads.append("...")
                break
            heads.append(h)
            used += len(h) + 3
        lines.append(f"README headings ({P(f.readme)}): " + " | ".join(heads))
    if f.recent:
        lines.append(f"Recently changed (last {RECENT_COMMITS} commits, paths only): "
                     + ", ".join(P(p) for p in f.recent[:RECENT_PATHS])
                     + (f" ... (+{len(f.recent) - RECENT_PATHS})" if len(f.recent) > RECENT_PATHS else ""))
    if top_files:
        lines.append("Top-level files: " + ", ".join(P(p) for p in top_files[:20])
                     + (" ..." if len(top_files) > 20 else ""))
    head = "\n".join(lines)
    room = MAX_MAP_CHARS - len(head) - 120
    shown: list[str] = []
    for t in tree[:TREE_LINES]:
        if room - (len(t) + 3) < 0:
            break
        shown.append("  " + t)
        room -= len(t) + 3
    out = head
    if shown:
        out += "\nTree (depth 2, file counts include subdirectories):\n" + "\n".join(shown)
        if len(tree) > len(shown):
            out += f"\n  ... {len(tree) - len(shown)} more directories"
    if len(out) > MAX_MAP_CHARS:
        out = out[: MAX_MAP_CHARS - 40].rsplit("\n", 1)[0] + "\n... (map truncated at its size bound)"
    return out


def test_roots(entries: list[Entry]) -> dict[str, int]:
    """{project root of a test folder: test files under it}. A test folder is a dir named like
    `tests/` or one holding test-named files; its ROOT is the dir above the outermost `tests`-like
    segment (where a runner is started), else the folder itself. Paths only."""
    roots: Counter = Counter()
    for e in entries:
        if e.kind != "file":
            continue
        segs = e.path.split("/")
        idx = next((i for i, s in enumerate(segs[:-1]) if s in TEST_DIR_NAMES), None)
        if idx is not None:
            roots["/".join(segs[:idx])] += 1
        elif _TEST_FILE.match(segs[-1]):
            roots["/".join(segs[:-1])] += 1
    return dict(roots)


def _covers(d: str, root: str) -> bool:
    """A runner started in `d` reaches tests under `root`."""
    return d == "" or root == d or root.startswith(d + "/")


async def build_map(source: RepoSource, sha: str) -> str:
    """Read the facts through `source` and render the bounded map. Raises OrientationError."""
    entries, note = await source.list_tree(sha)
    if not any(e.kind == "file" for e in entries):
        raise OrientationError("no files in the tree")
    files = [e for e in entries if e.kind == "file"]
    roots = test_roots(entries)

    def relevance(e: Entry) -> tuple[int, int, str]:
        d = _dir_of(e.path)
        near = any(r == d or (d and r.startswith(d + "/")) for r in roots)
        return (0 if near else 1, _depth(e.path), e.path)
    manifests = sorted((e for e in files if e.path.rsplit("/", 1)[-1] in MANIFESTS
                        and e.path.rsplit("/", 1)[-1] not in ("requirements.txt", "CMakeLists.txt")),
                       key=relevance)[:MAX_MANIFEST_READS]
    readme = next((e.path for e in sorted(files, key=lambda e: e.path)
                   if "/" not in e.path and e.path.lower() in ("readme.md", "readme.markdown")), "")
    reads = [m.path for m in manifests] + ([readme] if readme else [])
    blobs = dict(zip(reads, await asyncio.gather(*(source.read_file(p, sha) for p in reads))))

    commands: list[tuple[str, str, str]] = []
    for m in manifests:
        raw = blobs.get(m.path)
        if raw is None:
            continue
        cmd = _test_command(m.path.rsplit("/", 1)[-1], raw.decode("utf-8", "replace"))
        d = _dir_of(m.path)
        if cmd and not any(c[0] == d and c[1] == cmd for c in commands):
            commands.append((d, cmd, m.path))
    # pytest-style tests with no manifest declaring a runner are still a pytest project
    paths = [e.path for e in files]
    for r in sorted(roots, key=lambda p: (_depth(p), p)):
        if any(_covers(d, r) for d, _c, _s in commands):
            continue
        pyish = any(p.endswith(".py") and _covers(r, _dir_of(p)) and (
            p.rsplit("/", 1)[-1].startswith("test_") or p.endswith("_test.py")) for p in paths)
        if pyish:
            commands.append((r, "python -m pytest",
                             "pytest-style test files; no manifest declares a runner"))
    commands.sort(key=lambda c: (_depth(c[0]) if c[0] else -1, c[0]))
    headings = readme_headings(blobs[readme]) if readme and blobs.get(readme) else []
    recent = [p for p in await source.recent_paths(sha)
              if not any(seg in SKIP_DIRS for seg in p.split("/")[:-1])]
    return render(_Facts(sha=sha, tree_note=note, entries=entries, commands=commands,
                         headings=headings, readme=readme, recent=recent, roots=roots))


# ── the per-project cache + the one entry point the orchestrator calls ─────────────────────────

@dataclass
class Orientation:
    text: str = ""           # the map ("" unless ok)
    ok: bool = False
    attempted: bool = False  # False = skipped (disabled / no repo / the App cannot read it)
    reason: str = ""
    sha: str = ""
    cached: bool = False
    ms: int = 0

    def block(self) -> str:
        """What goes into the first coding wake: the map, the 'unavailable' line, or nothing."""
        if self.ok:
            return "\n\n" + self.text
        if self.attempted:
            return (f"\n\n{HEADER}: unavailable for this effort ({clean(self.reason, 160)}); "
                    "orient yourself from the repository as usual.")
        return ""


SourceFn = Callable[[str], "RepoSource | None"]
AuditFn = Callable[..., Awaitable[None]]


class ProjectOrientation:
    """Per-project, per-commit cache of orientation maps.

    Only a SUCCESS is cached (by the build task itself, so a caller that gives up early does not
    lose it). A failure is returned to the callers waiting on that build and then forgotten: the
    next call builds again. Concurrent callers for the same project + commit share ONE build.
    Nothing here touches the worker pool."""

    def __init__(self, source_fn: SourceFn, *, enabled: bool = True, timeout_s: float = 20.0,
                 audit: AuditFn | None = None, max_projects: int = 64) -> None:
        self._source_fn = source_fn
        self._enabled = enabled
        self._timeout_s = max(1.0, float(timeout_s))
        self._audit = audit
        self._max = max_projects
        self._cache: OrderedDict[str, tuple[str, str]] = OrderedDict()   # project -> (sha, map)
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}

    def cached(self, project: str) -> tuple[str, str] | None:
        return self._cache.get(project)

    async def get(self, project: str, repo: str, base_sha: str = "", *,
                  effort_id: str | None = None) -> Orientation:
        """The map for `repo` at `base_sha` (the default-branch head when not given). Bounded by
        `timeout_s` in total; never raises (except cancellation)."""
        if not self._enabled:
            return Orientation(reason="disabled")
        if not repo:
            return Orientation(reason="no repo")
        source = self._source_fn(repo)
        if source is None:
            return Orientation(reason="the GitHub App cannot read this repo")
        key = project or repo
        t0 = time.monotonic()
        sha = base_sha
        shared = False
        try:
            if not sha:
                sha = await asyncio.wait_for(source.resolve_head(), self._timeout_s)
            hit = self._cache.get(key)
            if hit is not None and hit[0] == sha:
                self._cache.move_to_end(key)
                await source.aclose()
                return Orientation(text=hit[1], ok=True, attempted=True, sha=sha, cached=True,
                                   ms=int((time.monotonic() - t0) * 1000))
            task = self._inflight.get((key, sha))
            shared = task is not None
            if shared:
                await source.aclose()
            else:
                remaining = max(1.0, self._timeout_s - (time.monotonic() - t0))
                task = asyncio.ensure_future(self._build(key, source, sha, remaining, effort_id))
                self._inflight[(key, sha)] = task
                task.add_done_callback(lambda _t, k=(key, sha): self._inflight.pop(k, None))
            text = await asyncio.shield(task)
        except asyncio.CancelledError:
            await source.aclose()
            raise
        except Exception as exc:  # noqa: BLE001 - orientation is a bonus; never block intake/dispatch
            await source.aclose()
            reason = (f"timed out after {self._timeout_s:.0f}s"
                      if isinstance(exc, asyncio.TimeoutError) else str(exc) or type(exc).__name__)
            return Orientation(attempted=True, reason=reason, sha=sha,
                               ms=int((time.monotonic() - t0) * 1000))
        return Orientation(text=text, ok=True, attempted=True, sha=sha, cached=shared,
                           ms=int((time.monotonic() - t0) * 1000))

    async def _build(self, key: str, source: RepoSource, sha: str, timeout_s: float,
                     effort_id: str | None) -> str:
        t0 = time.monotonic()
        try:
            text = await asyncio.wait_for(build_map(source, sha), timeout_s)
        except Exception as exc:
            reason = (f"timed out after {timeout_s:.0f}s" if isinstance(exc, asyncio.TimeoutError)
                      else str(exc) or type(exc).__name__)
            log.info("orientation for %s@%s unavailable: %s", key, sha[:12], reason)
            await self._log("project_orientation_failed", effort_id,
                            {"project": key, "sha": sha, "reason": reason[:200],
                             "ms": int((time.monotonic() - t0) * 1000),
                             "api_calls": getattr(source, "calls", None)})
            raise
        finally:
            await source.aclose()
        self._cache[key] = (sha, text)          # replaces the map of an older base
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        await self._log("project_orientation", effort_id,
                        {"project": key, "sha": sha, "len": len(text),
                         "ms": int((time.monotonic() - t0) * 1000),
                         "api_calls": getattr(source, "calls", None)})
        return text

    async def _log(self, kind: str, effort_id: str | None, payload: dict) -> None:
        if self._audit is None:
            return
        try:
            await self._audit(kind, effort_id=effort_id, actor="orientation", payload=payload)
        except Exception as exc:  # noqa: BLE001 - audit trouble must not fail the caller
            log.debug("orientation audit failed: %s", exc)

    def invalidate(self, project: str | None = None) -> None:
        if project is None:
            self._cache.clear()
        else:
            self._cache.pop(project, None)
