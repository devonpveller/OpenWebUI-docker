"""Test helper (ao-orient): a GitHub REST stand-in computed from a REAL git repository.

`GitBackedGitHub(git_dir).transport()` is an httpx MockTransport that answers the REST subset the
bridge reads (repo metadata, branches, contents, commits, compare and - in `trees=True` mode, as on
real GitHub - `git/trees?recursive=1`) by running git against `git_dir`. `trees=False` mirrors the
C7 sandbox's GitHub stand-in (bakeoff/c7/stubs/gh_stub.py), which does not emulate the trees
endpoint, so the bridge's contents-walk fallback is exercised too. Answers follow GitHub's shapes,
including a symlink coming back from the contents API as `type: "symlink"` (real GitHub) or as a
file whose content is the link target (the stub).

Every git call uses an explicit `--git-dir` (never repo discovery), so nothing here can touch a
repository other than the one it was given. Fault injection: `fail` (status to answer instead; with `fail_on`, only for matching requests),
`gate` (an asyncio.Event every request waits on), `hold_paths` (only requests whose path+query contains
one of these wait on the gate), `delay_s`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote

import httpx


def git(git_dir: str | Path, *args: str, inp: bytes | None = None, check: bool = True) -> str:
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(git_dir).resolve().parent),
           "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", f"--git-dir={git_dir}", *args], input=inp, capture_output=True,
                       env=env, check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args[:2]} failed: {r.stderr.decode(errors='replace')[:300]}")
    return r.stdout.decode("utf-8", "replace")


def make_repo(root: Path, commits: list[dict[str, str | bytes | None]], *,
              links: dict[str, str] | None = None) -> Path:
    """A bare repo at root/'repo.git' whose `main` has one commit per dict in `commits`
    ({path: content or None to delete}). Built with plumbing only (hash-object/update-index/
    write-tree/commit-tree), so names that no filesystem allows (newlines, ':') work on Windows.
    `links` adds symlink entries (mode 120000) in the LAST commit."""
    gd = root / "repo.git"
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(gd)], check=True,
                   capture_output=True)
    env_index = {**os.environ, "GIT_INDEX_FILE": str(root / "index.tmp"),
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@x", "GIT_CEILING_DIRECTORIES": str(root.resolve())}
    parent = ""
    blob_ids: dict[bytes, str] = {}
    for n, files in enumerate(commits):
        info = []
        for path, content in files.items():
            if content is None:
                info.append(f"0 {'0' * 40}\t{path}")
                continue
            data = content.encode() if isinstance(content, str) else content
            if data not in blob_ids:
                blob_ids[data] = subprocess.run(
                    ["git", f"--git-dir={gd}", "hash-object", "-w", "--stdin"], input=data,
                    capture_output=True, check=True, env=env_index).stdout.decode().strip()
            info.append(f"100644 {blob_ids[data]}\t{path}")
        if n == len(commits) - 1:
            for path, target in (links or {}).items():
                sha = subprocess.run(["git", f"--git-dir={gd}", "hash-object", "-w", "--stdin"],
                                     input=target.encode(), capture_output=True, check=True,
                                     env=env_index).stdout.decode().strip()
                info.append(f"120000 {sha}\t{path}")
        # -z: NUL-terminated records, so a path may carry a newline
        subprocess.run(["git", f"--git-dir={gd}", "update-index", "-z", "--index-info"],
                       input="\0".join(info).encode() + b"\0", check=True, capture_output=True,
                       env=env_index)
        tree = subprocess.run(["git", f"--git-dir={gd}", "write-tree"], capture_output=True,
                              check=True, env=env_index).stdout.decode().strip()
        args = ["git", f"--git-dir={gd}", "commit-tree", tree, "-m", f"commit {n}"]
        if parent:
            args += ["-p", parent]
        parent = subprocess.run(args, capture_output=True, check=True,
                                env=env_index).stdout.decode().strip()
    subprocess.run(["git", f"--git-dir={gd}", "update-ref", "refs/heads/main", parent], check=True,
                   capture_output=True)
    return gd


class GitBackedGitHub:
    def __init__(self, git_dir: str | Path, *, owner: str = "demoowner", repo: str = "gym",
                 trees: bool = True, symlink_as_file: bool | None = None) -> None:
        self.gd = str(git_dir)
        self.owner, self.repo = owner, repo
        self.trees = trees
        # real GitHub reports a symlink as type "symlink"; the C7 stub as a file holding the target
        self.symlink_as_file = (not trees) if symlink_as_file is None else symlink_as_file
        self.requests: list[str] = []
        self.fail: int | None = None
        self.fail_on: tuple[str, ...] = ()   # with `fail`: only requests whose path+query match
        self.fail_body: object = None        # with `fail`: the JSON body to answer (e.g. 200 + {})
        self.gate: asyncio.Event | None = None
        self.hold_paths: tuple[str, ...] = ()
        self.held = asyncio.Event()       # set when a request starts waiting on the gate
        self.delay_s = 0.0

    def head(self) -> str:
        return git(self.gd, "rev-parse", "refs/heads/main").strip()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        full = path + ("?" + unquote(request.url.query.decode()) if request.url.query else "")
        self.requests.append(full)
        if self.gate is not None and (not self.hold_paths or any(h in full for h in self.hold_paths)):
            self.held.set()
            await self.gate.wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None and (not self.fail_on or any(h in full for h in self.fail_on)):
            return httpx.Response(self.fail, json=(self.fail_body if self.fail_body is not None
                                                   else {"message": "injected"}))
        # git runs in a worker thread, like the C7 stub's own process: the stand-in must not block
        # the event loop, or a loop-lag measurement would charge the stand-in's work to the bridge
        return await asyncio.to_thread(self._answer, path, dict(request.url.params))

    def _answer(self, path: str, q: dict) -> httpx.Response:
        prefix = f"/repos/{self.owner}/{self.repo}"
        if not path.startswith(prefix):
            return httpx.Response(404, json={"message": "Not Found"})
        rest = path[len(prefix):]
        if rest in ("", "/"):
            return httpx.Response(200, json={"default_branch": "main", "full_name":
                                             f"{self.owner}/{self.repo}"})
        if rest.startswith("/branches/"):
            sha = git(self.gd, "rev-parse", "--verify", "--quiet",
                      "refs/heads/" + rest[len("/branches/"):], check=False).strip()
            return (httpx.Response(200, json={"name": rest[10:], "commit": {"sha": sha}})
                    if sha else httpx.Response(404, json={"message": "Branch not found"}))
        if rest.startswith("/git/trees/"):
            if not self.trees:
                return httpx.Response(404, json={"message": "Not Found (stub: not emulated)"})
            sha = rest[len("/git/trees/"):]
            out = git(self.gd, "ls-tree", "-r", "-t", "-z", sha)
            tree = []
            for rec in filter(None, out.split("\0")):
                meta, name = rec.split("\t", 1)
                mode, typ, osha = meta.split()
                tree.append({"path": name, "mode": mode, "type": typ, "sha": osha})
            return httpx.Response(200, json={"sha": sha, "tree": tree, "truncated": False})
        if rest == "/contents" or rest.startswith("/contents/"):
            sub = rest[len("/contents/"):] if rest.startswith("/contents/") else ""
            sha = q.get("ref") or "main"
            if not sub:
                return httpx.Response(200, json=self._listing(sha, ""))
            typ = git(self.gd, "cat-file", "-t", f"{sha}:{sub}", check=False).strip()
            if typ == "tree":
                return httpx.Response(200, json=self._listing(sha, sub))
            if typ != "blob":
                return httpx.Response(404, json={"message": "Not Found"})
            mode = git(self.gd, "ls-tree", sha, "--", sub).split(" ", 1)[0]
            raw = subprocess.run(["git", f"--git-dir={self.gd}", "cat-file", "blob", f"{sha}:{sub}"],
                                 capture_output=True, check=True).stdout
            if mode == "120000" and not self.symlink_as_file:
                return httpx.Response(200, json={"type": "symlink", "path": sub,
                                                 "target": raw.decode(errors="replace")})
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "path": sub,
                                             "size": len(raw),
                                             "content": base64.b64encode(raw).decode()})
        if rest.startswith("/commits/"):
            sha = rest[len("/commits/"):]
            line = git(self.gd, "rev-list", "--parents", "-n1", sha, check=False).split()
            if not line:
                return httpx.Response(404, json={"message": "No commit"})
            return httpx.Response(200, json={"sha": line[0],
                                             "parents": [{"sha": p} for p in line[1:]]})
        if rest.startswith("/compare/") and "..." in rest:
            a, b = rest[len("/compare/"):].split("...", 1)
            names = git(self.gd, "diff", "--name-only", "-z", a, b, check=False)
            files = [{"filename": n, "status": "modified", "patch": "@@ CONTENT-NEVER-EMITTED @@"}
                     for n in filter(None, names.split("\0"))]
            return httpx.Response(200, json={"status": "ahead", "ahead_by": 1, "behind_by": 0,
                                             "files": files, "commits": []})
        if rest.endswith("/pulls"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})

    def _listing(self, sha: str, sub: str) -> list[dict]:
        spec = f"{sha}:{sub}" if sub else sha
        out = git(self.gd, "ls-tree", "-z", spec)
        items = []
        for rec in filter(None, out.split("\0")):
            meta, name = rec.split("\t", 1)
            mode, typ, osha = meta.split()
            kind = {"blob": "file", "tree": "dir", "commit": "submodule"}[typ]
            if mode == "120000" and not self.symlink_as_file:
                kind = "symlink"
            items.append({"name": name, "path": f"{sub}/{name}" if sub else name, "sha": osha,
                          "type": kind})
        return items
