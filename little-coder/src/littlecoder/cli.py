"""`lc` — the CLI operator surface (design §12.6).

In Tool this is the only surface. It runs inside the `little-coder` container
(the operator gets a shell via `docker exec`), so authentication IS host
shell access — operator commands never authenticate at the MCP server
(privilege separation, design §12.6). It drives the control daemon's internal
HTTP API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

_DAEMON = os.environ.get("LC_DAEMON_URL", "http://localhost:8090")
_TERMINAL = {"done", "abandoned", "rejected"}


def _err(msg: str) -> None:
    sys.stderr.write(f"lc: {msg}\n")


def _parse_duration(text: str) -> int:
    """Accept `30m`, `1h`, `90s`, or a bare number of seconds."""
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text)


def _request(method: str, path: str, **kwargs) -> dict:
    try:
        with httpx.Client(base_url=_DAEMON, timeout=60.0) as c:
            resp = c.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        _err(f"cannot reach the daemon at {_DAEMON}: {exc}")
        raise SystemExit(2)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        _err(f"{resp.status_code}: {detail}")
        raise SystemExit(1)
    return resp.json() if resp.content else {}


# -- daily-driver commands -------------------------------------------------


def cmd_task(args: argparse.Namespace) -> int:
    body: dict = {"prompt": args.prompt, "channel": "cli", "user_id": "cli"}
    if args.acceptance:
        body["acceptance_command"] = args.acceptance
    res = _request("POST", "/tasks", json=body)
    task_id = res["task_id"]
    print(f"task {task_id} {res['status']}")
    if args.no_wait:
        return 0
    last = None
    while True:
        state = _request("GET", f"/tasks/{task_id}")
        if state["status"] != last:
            print(f"  → {state['status']}")
            last = state["status"]
        if state["status"] in _TERMINAL:
            print(f"outcome: {state.get('outcome')}  ({state.get('detail', '')})")
            return 0 if state["status"] == "done" else 1
        time.sleep(2)


def cmd_project(args: argparse.Namespace) -> int:
    res = _request("POST", "/project", json={"repo": args.link, "actor": "cli"})
    print(f"{res['action']}: focus = {res['focus']}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(_request("GET", "/health"), indent=2))
    return 0


def cmd_tasks(_args: argparse.Namespace) -> int:
    tasks = _request("GET", "/tasks").get("tasks", [])
    if not tasks:
        print("no tasks")
        return 0
    for t in tasks:
        print(
            f"{t['task_id']}  {t['status']:<9} {str(t.get('outcome')):<10} "
            f"{t['channel']:<5} {t['prompt_preview']}"
        )
    return 0


# -- operator-admin commands ----------------------------------------------


def cmd_admin_shutdown(args: argparse.Namespace) -> int:
    body: dict = {}
    if args.drain_deadline:
        body["drain_deadline_seconds"] = _parse_duration(args.drain_deadline)
    res = _request("POST", "/admin/shutdown", json=body)
    print(f"draining (deadline {res['drain_deadline_seconds']}s)")
    return 0


def cmd_admin_task_confirm(args: argparse.Namespace) -> int:
    res = _request(
        "POST",
        f"/tasks/{args.task_id}/confirm",
        json={"outcome": args.outcome, "actor": "cli"},
    )
    print(res.get("detail", "outcome amended"))
    return 0


def cmd_admin_pending(_args: argparse.Namespace) -> int:
    pending = _request("GET", "/admin/pending").get("pending", [])
    print(f"{len(pending)} pending artifact(s)")  # empty until Chapter 4
    return 0


def cmd_admin_approve(args: argparse.Namespace) -> int:
    _request("POST", f"/admin/approve/{args.artifact_id}")
    return 0


def cmd_admin_reject(args: argparse.Namespace) -> int:
    _request("POST", f"/admin/reject/{args.artifact_id}")
    return 0


def cmd_admin_upstream_pull(_args: argparse.Namespace) -> int:
    _request("POST", "/admin/upstream/pull")  # stub until Chapter 5
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lc", description="little-coder operator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("task", help="trigger a coding task against the focused repo")
    t.add_argument("prompt")
    t.add_argument("--acceptance", help="command whose exit code decides pass/fail")
    t.add_argument("--no-wait", action="store_true", help="return after queueing")
    t.set_defaults(func=cmd_task)

    pr = sub.add_parser("project", help="switch the focused project")
    pr.add_argument("link", help="repo URL (SSH or HTTPS)")
    pr.set_defaults(func=cmd_project)

    sub.add_parser("status", help="daemon health + focus").set_defaults(func=cmd_status)
    sub.add_parser("tasks", help="list tasks").set_defaults(func=cmd_tasks)

    admin = sub.add_parser("admin", help="operator commands")
    asub = admin.add_subparsers(dest="admin_cmd", required=True)

    sd = asub.add_parser("shutdown", help="drain and stop (design §12.7)")
    sd.add_argument("--drain-deadline", help="e.g. 30m, 1h, 90s")
    sd.set_defaults(func=cmd_admin_shutdown)

    asub.add_parser("pending", help="list pending artifacts (empty until Ch.4)").set_defaults(
        func=cmd_admin_pending
    )
    ap = asub.add_parser("approve", help="approve an artifact (Ch.4)")
    ap.add_argument("artifact_id")
    ap.set_defaults(func=cmd_admin_approve)
    rj = asub.add_parser("reject", help="reject an artifact (Ch.4)")
    rj.add_argument("artifact_id")
    rj.set_defaults(func=cmd_admin_reject)

    aproj = asub.add_parser("project", help="project administration")
    aproj_sub = aproj.add_subparsers(dest="project_cmd", required=True)
    aps = aproj_sub.add_parser("switch", help="switch the focused project")
    aps.add_argument("link")
    aps.set_defaults(func=cmd_project)

    atask = asub.add_parser("task", help="task administration")
    atask_sub = atask.add_subparsers(dest="task_cmd", required=True)
    cf = atask_sub.add_parser("confirm", help="amend a task outcome (7-day window)")
    cf.add_argument("task_id")
    cf.add_argument("outcome", choices=["pass", "fail", "unverified"])
    cf.set_defaults(func=cmd_admin_task_confirm)

    aups = asub.add_parser("upstream", help="upstream fork-parent administration")
    aups_sub = aups.add_subparsers(dest="upstream_cmd", required=True)
    aups_sub.add_parser(
        "pull", help="pull the fork-parent (operative in Chapter 5)"
    ).set_defaults(func=cmd_admin_upstream_pull)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
