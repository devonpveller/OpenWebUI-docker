"""Client for the open-terminal workspace plane (design §3.4).

open-terminal is "a computer you can curl" — a REST API on :8000. The agent's
build / test / git commands run THERE, inside the network-isolated plane;
this client is how the control plane reaches it. `git` inside open-terminal
is the git-proxy, so every git command routed through here is policed
(design §3.3).

API surface used (open-terminal `POST /execute`):
  - POST /execute            {command, cwd, env?}  ?wait=<0-300>
  - GET  /execute/{id}/status                      ?wait=<0-300>
  - DELETE /execute/{id}
  - GET  /files/read  ?path= / POST /files/write {path, content}
  - GET  /health
Auth: `Authorization: Bearer <OPEN_TERMINAL_API_KEY>`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time

import httpx

# open-terminal blocks at most 300s per call; we poll for anything longer.
_MAX_WAIT = 290
_GIT_PROXY_MARKER = "git-proxy: DENIED"


class OpenTerminalError(RuntimeError):
    """open-terminal was unreachable or returned an unusable response."""


class _NotFound(OpenTerminalError):
    """open-terminal answered 404 (no such process / file)."""


@dataclasses.dataclass
class ExecResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    status: str  # done | killed | running
    process_id: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "done" and self.exit_code == 0

    @property
    def git_proxy_denied(self) -> bool:
        """True when the git-proxy blocked the command (design §3.3). The
        daemon turns this into an `errors.jsonl` record."""
        return _GIT_PROXY_MARKER in self.stderr


def parse_exec_output(entries) -> tuple[str, str]:
    """Split open-terminal's `output` list into (stdout, stderr). The log
    entries may be plain strings or dicts carrying a stream tag — handle
    both defensively.

    open-terminal's own entries are `{"type": ..., "data": ...}` and each
    `data` is a raw chunk that carries its own newlines (a PTY read of up to
    4096 bytes, or a pipe line that ends in its newline), so consecutive
    `data` entries are concatenated as they are. Joining them with a newline
    (as before ao-exechang) put a stray line break into every output longer
    than one chunk. Other shapes are still joined line by line."""
    out: list[tuple[str, bool]] = []
    err: list[tuple[str, bool]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            out.append((entry, False))
            continue
        if not isinstance(entry, dict):
            out.append((str(entry), False))
            continue
        stream = str(entry.get("stream") or entry.get("type") or "stdout").lower()
        text, raw = "", False
        for key in ("text", "line", "data", "content", "message"):
            if entry.get(key) is not None:
                text, raw = str(entry[key]), key == "data"
                break
        (err if stream in ("stderr", "err", "2") else out).append((text, raw))
    return _join_entries(out), _join_entries(err)


def _join_entries(parts: list[tuple[str, bool]]) -> str:
    text = ""
    for i, (part, raw) in enumerate(parts):
        if i and not (raw and parts[i - 1][1]):
            text += "\n"
        text += part
    return text


class OpenTerminalClient:
    """Synchronous client. One task runs at a time (design §12.4), so a
    blocking client is the right shape; the daemon calls it off the event
    loop."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_cwd: str = "/workspace",
        default_timeout: int = 1800,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport  # tests inject an httpx.MockTransport
        self._headers = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )
        self.default_cwd = default_cwd
        self.default_timeout = default_timeout

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url, headers=self._headers, timeout=timeout,
            transport=self._transport,
        )

    def health(self) -> bool:
        try:
            with self._client(10.0) as c:
                return c.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def execute(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        """Run a shell command in open-terminal. Blocks up to `timeout`
        seconds, polling once the command outlives a single 290s window. On
        timeout the process is killed and `timed_out` is set."""
        cwd = cwd or self.default_cwd
        timeout = timeout or self.default_timeout
        deadline = time.monotonic() + timeout
        body: dict[str, object] = {"command": command, "cwd": cwd}
        if env:
            body["env"] = env

        first_wait = min(timeout, _MAX_WAIT)
        try:
            with self._client(first_wait + 30) as c:
                resp = c.post("/execute", json=body, params={"wait": first_wait})
                resp.raise_for_status()
                data = resp.json()
                pid = str(data.get("id", ""))
                status = str(data.get("status", "unknown"))

                while status == "running" and time.monotonic() < deadline:
                    wait = max(1, min(_MAX_WAIT, int(deadline - time.monotonic())))
                    poll = c.get(
                        f"/execute/{pid}/status",
                        params={"wait": wait},
                        timeout=wait + 30,
                    )
                    poll.raise_for_status()
                    data = poll.json()
                    status = str(data.get("status", "unknown"))

                timed_out = status == "running"
                if timed_out and pid:
                    try:
                        c.delete(f"/execute/{pid}", timeout=15)
                    except httpx.HTTPError:
                        pass
        except httpx.HTTPError as exc:
            raise OpenTerminalError(f"open-terminal execute failed: {exc}") from exc

        stdout, stderr = parse_exec_output(data.get("output"))
        return ExecResult(
            command=command,
            exit_code=data.get("exit_code"),
            stdout=stdout,
            stderr=stderr,
            status=status,
            process_id=pid,
            timed_out=timed_out,
        )

    # -- low-level calls for a caller that drives the wait itself (ot-exec, ao-exechang) --------
    # Every call is bounded by an HTTP timeout; open-terminal blocks at most `wait` seconds.

    def start(self, command: str, cwd: str | None = None, env: dict[str, str] | None = None,
              wait: float = 0.0, client: httpx.Client | None = None,
              http_timeout: float | None = None) -> dict:
        """POST /execute: start `command`, wait up to `wait` s for it to end; the response dict
        (`id`, `status`, `exit_code`, `output`)."""
        body: dict[str, object] = {"command": command, "cwd": cwd or self.default_cwd}
        if env:
            body["env"] = env
        wait = max(0.0, min(float(wait), _MAX_WAIT))
        return self._call(client, "POST", "/execute", http_timeout or wait + 30, json=body,
                          params={"wait": wait})

    def status(self, process_id: str, wait: float = 0.0, tail: int | None = None,
               client: httpx.Client | None = None, http_timeout: float | None = None) -> dict:
        """GET /execute/{id}/status: wait up to `wait` s for the process to end; the FULL output
        unless `tail` asks for the last N entries only."""
        wait = max(0.0, min(float(wait), _MAX_WAIT))
        params: dict[str, object] = {"wait": wait}
        if tail:
            params["tail"] = tail
        return self._call(client, "GET", f"/execute/{process_id}/status", http_timeout or wait + 30,
                          params=params)

    def kill(self, process_id: str, client: httpx.Client | None = None,
             http_timeout: float = 15) -> bool:
        """DELETE /execute/{id}: SIGTERM to the command's whole process group (open-terminal starts
        every command in its own session); open-terminal waits for the group leader to exit.
        False when the call failed (the caller decides what that means)."""
        try:
            self._call(client, "DELETE", f"/execute/{process_id}", http_timeout)
        except _NotFound:
            pass  # already ended and cleaned up: nothing left to signal
        except OpenTerminalError:
            return False
        return True

    def file_text(self, path: str, client: httpx.Client | None = None) -> str | None:
        """The text of `path` in the executor, or None when it does not exist."""
        try:
            return str(self._call(client, "GET", "/files/read", 15, params={"path": path})
                       .get("content", ""))
        except _NotFound:
            return None

    def delete_file(self, path: str, client: httpx.Client | None = None) -> None:
        """Best effort: remove `path` in the executor (a missing file is fine)."""
        with contextlib.suppress(OpenTerminalError):
            self._call(client, "DELETE", "/files/delete", 15, params={"path": path})

    def _call(self, client: httpx.Client | None, method: str, url: str, timeout: float,
              **kwargs) -> dict:
        try:
            if client is None:
                with self._client(timeout) as c:
                    resp = c.request(method, url, timeout=timeout, **kwargs)
            else:
                resp = client.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 404:
                raise _NotFound(f"{method} {url}: 404")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise OpenTerminalError(f"open-terminal {method} {url} failed: {exc}") from exc
        except ValueError as exc:  # not JSON
            raise OpenTerminalError(f"open-terminal {method} {url}: bad response: {exc}") from exc
        return data if isinstance(data, dict) else {}

    def read_file(self, path: str) -> str:
        try:
            with self._client(30.0) as c:
                resp = c.get("/files/read", params={"path": path})
                resp.raise_for_status()
                return str(resp.json().get("content", ""))
        except httpx.HTTPError as exc:
            raise OpenTerminalError(f"read_file failed: {exc}") from exc

    def write_file(self, path: str, content: str) -> int:
        try:
            with self._client(30.0) as c:
                resp = c.post(
                    "/files/write", json={"path": path, "content": content}
                )
                resp.raise_for_status()
                return int(resp.json().get("size", 0))
        except httpx.HTTPError as exc:
            raise OpenTerminalError(f"write_file failed: {exc}") from exc
