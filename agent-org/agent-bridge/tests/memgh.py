"""A GitHub REST stand-in served from an in-memory repo (dict path -> bytes), for the ao-orient tests (round 4).

Same REST subset and shapes as the developer's tests/gitbacked_github.py (repo metadata, branches,
contents listings/files, commits, compare, and git/trees?recursive=1 in trees mode), but no git at
all: names no filesystem allows (newline, bidi, padding) work, and nothing runs with a cwd anywhere.
Two commits: HEAD (all files) whose parent P1 is a root commit; compare P1...HEAD lists `recent`.
"""
from __future__ import annotations

import base64
import httpx

HEAD = "a" * 40
P1 = "b" * 40


class MemGitHub:
    def __init__(self, files: dict[str, bytes | str], *, recent: list[str] | None = None,
                 links: dict[str, str] | None = None, submodules: list[str] | None = None,
                 trees: bool = True, owner: str = "demoowner", repo: str = "gym",
                 fail_on: tuple[str, ...] = (), fail_status: int = 500) -> None:
        self.files = {p: (c.encode() if isinstance(c, str) else c) for p, c in files.items()}
        self.links = dict(links or {})
        self.subs = list(submodules or [])
        self.recent = list(recent if recent is not None else [])
        self.trees = trees
        self.prefix = f"/repos/{owner}/{repo}"
        self.fail_on, self.fail_status = fail_on, fail_status
        self.seen: list[str] = []          # every request (path?query), in order
        self.dirs: set[str] = set()
        for p in list(self.files) + list(self.links) + self.subs:
            parts = p.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _kind(self, p: str) -> str | None:
        if p in self.files:
            return "file"
        if p in self.links:
            return "symlink"
        if p in self.subs:
            return "submodule"
        if p in self.dirs:
            return "dir"
        return None

    def _children(self, d: str) -> list[dict]:
        pre = d + "/" if d else ""
        seen = {}
        for p in list(self.files) + list(self.links) + self.subs + sorted(self.dirs):
            if not p.startswith(pre) or p == d:
                continue
            rest = p[len(pre):]
            if "/" in rest:
                continue
            seen[p] = {"name": rest, "path": p, "type": self._kind(p)}
        return list(seen.values())

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path            # decoded once (as Starlette / GitHub)
        full = path + "?" + request.url.query.decode() if request.url.query else path
        self.seen.append(full)
        if self.fail_on and any(h in full for h in self.fail_on):
            return httpx.Response(self.fail_status, json={"message": "injected"})
        if not path.startswith(self.prefix):
            return httpx.Response(404, json={"message": "Not Found"})
        rest = path[len(self.prefix):]
        if rest in ("", "/"):
            return httpx.Response(200, json={"default_branch": "main"})
        if rest.startswith("/branches/"):
            return httpx.Response(200, json={"name": "main", "commit": {"sha": HEAD}})
        if rest.startswith("/git/trees/"):
            if not self.trees:
                return httpx.Response(404, json={"message": "Not Found"})
            tree = [{"path": p, "type": "blob", "mode": "100644"} for p in self.files]
            tree += [{"path": p, "type": "blob", "mode": "120000"} for p in self.links]
            tree += [{"path": p, "type": "commit", "mode": "160000"} for p in self.subs]
            tree += [{"path": d, "type": "tree", "mode": "040000"} for d in sorted(self.dirs)]
            return httpx.Response(200, json={"sha": HEAD, "tree": tree, "truncated": False})
        if rest == "/contents" or rest.startswith("/contents/"):
            p = rest[len("/contents/"):] if rest.startswith("/contents/") else ""
            k = self._kind(p) if p else "dir"
            if k is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if k == "dir":
                return httpx.Response(200, json=self._children(p))
            if k == "symlink":
                return httpx.Response(200, json={"type": "symlink", "target": self.links[p], "path": p})
            if k == "submodule":
                return httpx.Response(200, json={"type": "submodule", "path": p})
            data = self.files[p]
            return httpx.Response(200, json={"type": "file", "path": p, "size": len(data),
                                             "encoding": "base64",
                                             "content": base64.b64encode(data).decode()})
        if rest.startswith("/commits/"):
            sha = rest[len("/commits/"):]
            parents = [{"sha": P1}] if sha == HEAD else []
            return httpx.Response(200, json={"sha": sha, "parents": parents})
        if rest.startswith("/compare/"):
            return httpx.Response(200, json={"files": [{"filename": f} for f in self.recent]})
        return httpx.Response(404, json={"message": "Not Found"})
