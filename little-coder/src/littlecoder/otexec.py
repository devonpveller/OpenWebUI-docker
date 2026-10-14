"""ot-exec — run a command in the open-terminal workspace plane.

little-coder's command-execution tool is wired (via the pi extension in
pi-extension/) to call `ot-exec` instead of a local shell, so every
build / test / git command runs in the network-isolated plane and passes
through the git-proxy (design §1.5, §3.3, §3.4). ot-exec is a drop-in
`bash -c` replacement:  `ot-exec -c "<command>"`.

When `LC_EVENT_STREAM` is set, ot-exec appends one JSON event per command.
That stream is the journaling seam: the AgentRunner turns it into
`tool_call` / `error` records (design §4) — no need to parse the agent's
free-text output.

Environment:
  LC_OPEN_TERMINAL_URL / LC_OPEN_TERMINAL_KEY  open-terminal endpoint + key
  LC_WORKSPACE        working directory for the command (default /workspace)
  LC_EXEC_TIMEOUT     per-command timeout, seconds
  LC_EVENT_STREAM     optional path to append command events to

How a command runs (ao-exechang, 2026-10-14). open-terminal runs a command under a PTY and calls
it finished only when the PTY reaches end-of-file, i.e. when EVERY process holding it has exited.
A command that starts a background job without redirecting its output (`( ... ) &`, `cmd &`)
therefore stayed "running" until the job ended or the timeout fired (bake-off C7d: 1,270 s).
So ot-exec sends a small wrapper (`_wrapper`), not the bare command: the wrapper runs the command
(passed in the environment, never spliced into shell text) with stdin from /dev/null, then writes
its exit status to `/tmp/lc-exec-<id>.rc`. ot-exec waits in short slices; when the process is
still "running" but the status file exists, the command itself has finished and only its
background jobs hold the PTY: ot-exec returns with that status and says so. Every process the
command starts carries `LC_EXEC_ID=<id>` in its environment.

On timeout ot-exec (1) takes the output so far, (2) runs a reaper in open-terminal that freezes
(SIGSTOP), then SIGTERMs + SIGCONTs, then SIGKILLs the command's processes - its process group and
session (the wrapper records the id), every process carrying its `LC_EXEC_ID`, and their
descendants - and counts survivors, (3) has open-terminal DELETE its record, and only then (4)
reports: exit 124 and a fixed message on stderr that names the command, says `timed out after N s,
killed`, states exactly what was checked, and how to proceed. The reaper does not wait on the
DELETE (which hangs on a stopped group leader); the whole kill is bounded by KILL_BUDGET_S. The
same kill runs when ot-exec itself gets SIGTERM (pi's abort) and when open-terminal stops
answering mid-command.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import signal
import sys
import time
import uuid

from . import git_artifact_filter as artifact_filter
from .credscrub import scrub_text
from .journals import utc_now
from .openterminal import OpenTerminalClient, OpenTerminalError, parse_exec_output

# A long command is waited on in slices this long, so a finished command whose background job
# still holds the PTY is noticed within one slice. open-terminal answers at once when the PTY
# closes, so a command without a background job never waits for a slice to end.
SLICE_S = 1.0
# After the status file appears: how long open-terminal gets to notice the PTY closed before the
# command counts as having left a background job behind (the file is written just before exit).
SETTLE_S = 1.0
TIMEOUT_EXIT = 124
STOPPED_EXIT = 143      # ot-exec got SIGTERM (pi's abort): the command was killed
UNREACHABLE_EXIT = 125
TIMEOUT_MARKER = "[ot-exec] timed out after"   # open-terminal-exec keys on this; keep in step
GIT_PROXY_MARKER = "git-proxy: DENIED"
_RC_PATH = "/tmp/lc-exec-{id}.rc"
_PG_PATH = "/tmp/lc-exec-{id}.pg"
_REAP_RE = re.compile(r"LC_REAP killed=(\d+) survivors=(\d+) group=(\S+)")
_REAP_WAIT_S = 6      # the reaper takes ~1.5-3 s; 6 s is its ceiling
_OUTPUT_FETCH_S = 3   # reading the output so far at the deadline
# what the kill may add after the deadline: output read + reaper + its kill + the DELETE
KILL_BUDGET_S = _OUTPUT_FETCH_S + (_REAP_WAIT_S + 1) + 2 + 2
_CMD_ECHO_CAP = 200
_HTTP_CAP_S = 360  # one request: open-terminal's 300-s wait ceiling + slack


def _wrapper(exec_id: str) -> str:
    """The shell text open-terminal runs (/bin/sh). The command itself arrives in LC_EXEC_CMD."""
    # $$ is the shell open-terminal started in a NEW session, so it is also the id of the
    # command's process group and session: the reaper kills by those as well as by the tag (round 2:
    # a process that drops LC_EXEC_ID but stays in the group/session is still found).
    return (
        "umask 000\n"
        f"echo $$ > {_PG_PATH.format(id=exec_id)}\n"
        "c=$LC_EXEC_CMD\n"
        "unset LC_EXEC_CMD\n"
        f'LC_EXEC_ID={exec_id} sh -c "$c" </dev/null\n'
        "rc=$?\n"
        f"rm -f {_PG_PATH.format(id=exec_id)}\n"
        f'echo "$rc" > {_RC_PATH.format(id=exec_id)}\n'
        'exit "$rc"\n'
    )


def _reaper(exec_id: str) -> str:
    """Stop the command. Its processes are: the members of its process group or session (the id
    the wrapper recorded), the processes carrying its LC_EXEC_ID, and - round 2 - every descendant
    of those (a child that left the session AND cleared its environment is still its parent's
    child). The tree is first frozen with SIGSTOP (repeated until no new member appears), so nothing
    forks, exits or is re-parented away while it is collected; then SIGTERM + SIGCONT, one second,
    then SIGKILL until none is alive (5 passes). It reports how many of THOSE processes are still
    alive - all it can know: a process re-parented away (its parent gone) with neither session nor
    tag is invisible to it. The reaper runs in its own session, so it never matches itself."""
    pg_path, rc_path = _PG_PATH.format(id=exec_id), _RC_PATH.format(id=exec_id)
    return (
        f"i={exec_id}; k=0; n=0; L=\n"
        f"pg=$(cat {pg_path} 2>/dev/null)\n"
        "members() {\n"
        '  t=" "; for e in $(grep -lzx "LC_EXEC_ID=$i" /proc/[0-9]*/environ 2>/dev/null); do\n'
        '    e=${e#/proc/}; t="$t${e%/environ} "; done\n'
        "  for d in /proc/[0-9]*; do\n"
        '    read -r s 2>/dev/null < "$d/stat" || continue\n'
        '    p=${d#/proc/}; s=${s##*) }; set -- $s\n'
        '    [ "$1" = Z ] && continue\n'
        "    m=0\n"
        '    if [ -n "$pg" ] && { [ "$3" = "$pg" ] || [ "$4" = "$pg" ]; }; then m=1; fi\n'
        '    case "$t" in *" $p "*) m=1;; esac\n'
        '    echo "$p $2 $m"\n'
        "  done | awk '{pp[$1]=$2; if ($3) m[$1]=1}\n"
        "    END {do {c=0; for (x in pp) if (!(x in m) && (pp[x] in m)) {m[x]=1; c=1}} while (c);\n"
        "         for (x in m) print x}'\n"
        "}\n"
        "alive() { for p in $1; do read -r s 2>/dev/null < /proc/$p/stat || continue; s=${s##*) };"
        ' [ "${s%% *}" = Z ] || echo "$p"; done; }\n'
        "while [ $n -lt 4 ]; do\n"  # freeze the tree; repeat while it still grows
        "  new=; for p in $(members); do case \" $L \" in *\" $p \"*) ;; *) new=\"$new $p\";; esac; done\n"
        '  [ -z "$new" ] && break\n'
        '  for p in $new; do kill -STOP "$p"; done 2>/dev/null\n'
        '  L="$L$new"; n=$((n+1))\n'
        "done\n"
        'for p in $L; do kill -TERM "$p"; kill -CONT "$p"; done 2>/dev/null\n'
        '[ -n "$L" ] && sleep 1\n'
        "n=0\n"
        "while [ $n -lt 5 ]; do\n"
        "  l=$(alive \"$L $(members)\" | sort -u)\n"
        '  [ -z "$l" ] && break\n'
        '  for p in $l; do kill -KILL "$p" 2>/dev/null && k=$((k+1)); done\n'
        "  n=$((n+1)); sleep 0.3\n"
        "done\n"
        "s=$(alive \"$L $(members)\" | sort -u | wc -l)\n"
        f"rm -f {rc_path} {pg_path}\n"
        'echo "LC_REAP killed=$k survivors=$s group=${pg:-none}"\n'
    )


class _Stopped(BaseException):
    """ot-exec received SIGTERM (BaseException: no `except Exception` may swallow it)."""


@dataclasses.dataclass
class RunOutcome:
    stdout: str              # open-terminal's PTY merges the command's stdout and stderr here
    stderr: str              # only non-PTY entries tagged stderr (empty on the PTY path, as before)
    exit_code: int | None
    status: str              # done | background | timed_out | stopped | unreachable
    duration_ms: int = 0
    killed: int | None = None     # processes the reaper had to SIGKILL (None: it did not answer)
    survivors: int | None = None  # processes still alive after the reaper (None: unknown)
    error: str = ""
    group_known: bool = False     # the reaper knew the command's group/session id

    @property
    def timed_out(self) -> bool:
        return self.status == "timed_out"


def run_command(client: OpenTerminalClient, command: str, timeout: float,
                exec_id: str | None = None, slice_s: float | None = None) -> RunOutcome:
    """Run `command` in open-terminal, bounded by `timeout` seconds (see the module docstring).
    Any way out other than the command finishing goes through `_kill` first, so the command is
    never left running - also when the start request itself failed or was interrupted, since the
    command may have started anyway (the reaper finds it by its LC_EXEC_ID, no process id needed)."""
    slice_s = SLICE_S if slice_s is None else slice_s
    exec_id = exec_id or uuid.uuid4().hex
    started = time.monotonic()
    deadline = started + timeout
    rc_path = _RC_PATH.format(id=exec_id)
    pid = ""
    with client._client(_HTTP_CAP_S) as http:
        try:
            data = client.start(_wrapper(exec_id), env={"LC_EXEC_CMD": command},
                                wait=min(slice_s, timeout), client=http)
            pid = str(data.get("id", ""))
            full = True   # does `data` carry the whole output?
            while str(data.get("status")) == "running":
                left = deadline - time.monotonic()
                if left <= 0:
                    try:
                        streams = _streams(client.status(pid, client=http,
                                                         http_timeout=_OUTPUT_FETCH_S))
                    except OpenTerminalError:
                        streams = ("", "")   # still a timeout: report it as one
                    return _kill(client, pid, exec_id, "timed_out", streams, started)
                rc_text = client.file_text(rc_path, client=http)
                if rc_text is not None:
                    # The command has ended. The wrapper exits right after writing the file, so
                    # give open-terminal a moment to see the PTY close; still running after that
                    # means a background job holds it.
                    data = client.status(pid, wait=min(SETTLE_S, max(left, 0.0)), client=http)
                    full = True
                    if str(data.get("status")) == "running":
                        client.delete_file(rc_path, client=http)
                        return RunOutcome(*_streams(data), _int(rc_text), "background",
                                          _ms(started))
                    break
                data = client.status(pid, wait=min(slice_s, left), tail=1, client=http)
                full = False
            if not full:
                data = client.status(pid, client=http)
            client.delete_file(rc_path, client=http)
            code = data.get("exit_code")
            return RunOutcome(*_streams(data), code if isinstance(code, int) else None, "done",
                              _ms(started))
        except _Stopped:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)   # finish the kill; pi's backstop SIGKILLs
            return _kill(client, pid, exec_id, "stopped", ("", ""), started)
        except OpenTerminalError as exc:
            out = _kill(client, pid, exec_id, "unreachable", ("", ""), started)
            out.error = str(exc)
            return out


def _kill(client: OpenTerminalClient, pid: str, exec_id: str, status: str,
          streams: tuple[str, str], started: float) -> RunOutcome:
    """Kill the command, then report. The reaper does the killing itself (TERM+CONT, then KILL, to
    the command's group, session and tagged processes), so it does not depend on open-terminal's
    DELETE, which waits for the group leader and hangs on a STOPPED one (round 2, D2). The DELETE
    after it only clears open-terminal's record. Uses a FRESH connection (the waiting one may have
    been cut by SIGTERM). Bounded: <= KILL_BUDGET_S in all (reaper <= 7 s, DELETEs <= 2 s each)."""
    killed = survivors = None
    group = False
    with client._client(_HTTP_CAP_S) as http:
        try:
            rep = client.start(_reaper(exec_id), wait=_REAP_WAIT_S, client=http,
                               http_timeout=_REAP_WAIT_S + 1)
            if str(rep.get("status")) == "running":
                client.kill(str(rep.get("id", "")), client=http, http_timeout=2)
            m = _REAP_RE.search("".join(_streams(rep)))
            if m:
                killed, survivors, group = int(m.group(1)), int(m.group(2)), m.group(3) != "none"
        except OpenTerminalError:
            pass
        if pid:
            client.kill(pid, client=http, http_timeout=2)
    code = {"timed_out": TIMEOUT_EXIT, "stopped": STOPPED_EXIT}.get(status, UNREACHABLE_EXIT)
    return RunOutcome(*streams, code, status, _ms(started), killed, survivors, group_known=group)


def _streams(data: dict) -> tuple[str, str]:
    return parse_exec_output(data.get("output"))


def _int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _command_echo(command: str) -> str:
    """The command as a message quotes it: first line, capped, URL credentials removed."""
    lines = command.strip().splitlines() or [""]
    text = lines[0] + (" ..." if len(lines) > 1 else "")
    if len(text) > _CMD_ECHO_CAP:
        text = text[:_CMD_ECHO_CAP] + " ..."
    return scrub_text(text)[0]


def _kill_note(out: RunOutcome) -> str:
    """Claims only what the reaper checked (round 2, D3): the command's process group and session,
    the processes carrying its tag, and their descendants. A process that is none of these (it left
    the session, cleared its environment AND its parent is gone) is invisible to that check, and
    the note says so instead of claiming "every process"."""
    if out.survivors is None:
        return ("WARNING: the kill could not be confirmed (open-terminal did not answer the "
                "process check); processes it started may still be running.")
    if out.survivors:
        return (f"WARNING: {out.survivors} of its processes could not be killed and may still "
                "be running.")
    scope = ("its process group and session, every process carrying its tag, and their "
             "descendants" if out.group_known else
             "every process carrying its tag and their descendants (its process group was not "
             "recorded)")
    extra = f"; {out.killed} needed SIGKILL" if out.killed else ""
    return (f"Killed {scope}; none of them is still running{extra}. A process that left the "
            "session and cleared its environment after its parent exited cannot be tracked and "
            "may still run.")


def timeout_message(command: str, timeout: float, out: RunOutcome) -> str:
    """The fixed text the model receives when the bound fires. open-terminal-exec keys on its
    first words (TIMEOUT_MARKER) to hand it to the model as an error result."""
    return (
        f"{TIMEOUT_MARKER} {int(timeout)} s, killed: `{_command_echo(command)}`\n"
        f"{_kill_note(out)} Any output above is what it printed before the kill.\n"
        "How to proceed: run a narrower, targeted command (one test file or one test, e.g. "
        "`python -m pytest -q path/to/test_x.py::test_name -x`) and put `timeout <seconds>` in "
        "front of anything that might hang. For a job that needs longer, start it in the "
        "background with its output in a log, then poll the log with short commands:\n"
        "  nohup CMD > /tmp/x.log 2>&1 &\n"
        "  tail -n 20 /tmp/x.log\n"
    )


def stopped_message(command: str, out: RunOutcome) -> str:
    return f"[ot-exec] stopped (SIGTERM): `{_command_echo(command)}`. {_kill_note(out)}\n"


def unreachable_message(command: str, out: RunOutcome) -> str:
    return (f"ot-exec: open-terminal unreachable ({out.error}) while running "
            f"`{_command_echo(command)}`; its result is unknown. {_kill_note(out)}\n")


BACKGROUND_NOTE = (
    "[ot-exec] note: the command finished, but background process(es) it started are still "
    "running (this call did not wait for them). Their output is not captured unless they write "
    "it to a file. To run a job in the background, use: nohup CMD > /tmp/x.log 2>&1 &  and poll "
    "the log.\n"
)


def _on_sigterm(signum, frame):  # noqa: ARG001
    raise _Stopped()


def _emit_filter_denial(command: str, decision: artifact_filter.Decision) -> None:
    """Journal a workspace-edge denial via the existing event stream so the
    daemon sees it through the same path as a git-proxy denial.

    Symmetric with `_emit_event`, but no real result exists — the
    command never reached open-terminal. `git_proxy_denied: True` flips the
    activity record's `denied` flag and the daemon writes a `git_blocked`
    error (design §3.3, plan open item #9)."""
    path = os.environ.get("LC_EVENT_STREAM")
    if not path:
        return
    line = (
        f"{artifact_filter.DENY_MARKER} (ot-exec:{decision.rule}) "
        f"— {decision.reason}"
    )
    event = {
        "ts": utc_now(),
        "kind": "command",
        "command": command,
        "exit_code": artifact_filter.EXIT_DENIED,
        "status": "done",
        "duration_ms": 0,
        "timed_out": False,
        "git_proxy_denied": True,
        "stderr_tail": line,
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass  # journaling is best-effort; the stderr marker is the primary signal


def _emit_event(command: str, result: RunOutcome) -> None:
    path = os.environ.get("LC_EVENT_STREAM")
    if not path:
        return
    event = {
        "ts": utc_now(),
        "kind": "command",
        "command": command,
        "exit_code": result.exit_code,
        "status": "done" if result.status in ("done", "background") else "killed",
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "git_proxy_denied": GIT_PROXY_MARKER in result.stderr,   # as ExecResult.git_proxy_denied
        "stderr_tail": result.stderr[-2000:],
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass  # journaling is best-effort; the command result still stands


def _parse_args(argv: list[str]) -> str:
    """bash-compatible: `ot-exec -c "<cmd>"` or `ot-exec <cmd...>`."""
    if not argv:
        return ""
    if argv[0] == "-c":
        return argv[1] if len(argv) > 1 else ""
    return " ".join(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = _parse_args(argv)
    if not command.strip():
        sys.stderr.write("ot-exec: no command given\n")
        return 2

    # Workspace-edge artifact filter — symmetric with the git-proxy
    # (design §3.3, plan open item #9). Blocks the obvious bash bypass
    # paths to `.git/config` / `.git/hooks/` / `.git/info/` before the
    # command crosses into open-terminal. Pure-classify; see module
    # docstring for the residual root-bypass.
    decision = artifact_filter.classify(command)
    if decision.action == "deny":
        marker = (
            f"{artifact_filter.DENY_MARKER} (ot-exec:{decision.rule}) "
            f"— {decision.reason}"
        )
        sys.stderr.write(marker + "\n")
        _emit_filter_denial(command, decision)
        return artifact_filter.EXIT_DENIED

    timeout = int(os.environ.get("LC_EXEC_TIMEOUT", "1800"))
    client = OpenTerminalClient(
        base_url=os.environ.get("LC_OPEN_TERMINAL_URL", "http://open-terminal:8000"),
        api_key=os.environ.get("LC_OPEN_TERMINAL_KEY", ""),
        default_cwd=os.environ.get("LC_WORKSPACE", "/workspace"),
        default_timeout=timeout,
    )
    # pi's abort SIGTERMs ot-exec: kill the command in open-terminal before exiting, so it does
    # not keep running there. (umask 000 is set by the wrapper: the workspace volume is shared by
    # two containers with different uids - integration-tasks Decision Log.)
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        result = run_command(client, command, timeout)
    except _Stopped:  # SIGTERM before any request was made: nothing was started
        sys.stderr.write("[ot-exec] stopped (SIGTERM) before the command started\n")
        return STOPPED_EXIT
    # the command is over (finished or killed): a late SIGTERM just ends this process
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    _emit_event(command, result)

    for stream, text in ((sys.stdout, result.stdout), (sys.stderr, result.stderr)):
        if text:
            stream.write(text if text.endswith("\n") else text + "\n")
    if result.status == "timed_out":
        sys.stderr.write(timeout_message(command, timeout, result))
    elif result.status == "stopped":
        sys.stderr.write(stopped_message(command, result))
    elif result.status == "unreachable":
        sys.stderr.write(unreachable_message(command, result))
    elif result.status == "background":
        sys.stderr.write(BACKGROUND_NOTE)
    sys.stdout.flush()
    sys.stderr.flush()
    return result.exit_code if result.exit_code is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
