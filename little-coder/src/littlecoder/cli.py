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
        marker = (state["status"], state.get("commands", 0))
        if marker != last:
            cmds = marker[1]
            suffix = f" · {cmds} command(s)" if cmds else ""
            print(f"  → {state['status']}{suffix}")
            last = marker
        if state["status"] in _TERMINAL:
            for a in state.get("activity") or []:
                mark = "ok" if a.get("ok") else f"exit {a.get('exit_code')}"
                print(f"    $ {a.get('command', '')}  [{mark}]")
            answer = (state.get("answer") or "").strip()
            if answer:
                print("\n" + answer)
            print(f"\noutcome: {state.get('outcome')}  ({state.get('detail', '')})")
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


def cmd_admin_pending(args: argparse.Namespace) -> int:
    """`lc admin pending` — list pending skill drafts. With `--json`,
    dump the raw daemon response; otherwise render a human summary."""
    pending = _request("GET", "/admin/pending").get("pending", [])
    if getattr(args, "json", False):
        print(json.dumps(pending, indent=2))
        return 0
    if not pending:
        print("0 pending artifact(s)")
        return 0
    print(f"{len(pending)} pending artifact(s):\n")
    for row in pending:
        print(f"  [{row['id']}] tier-{row['tier']} {row['kind']} — {row['name']}")
        print(f"    lang={row['lang']} domain={row['domain']} task_shape={row['task_shape']}")
        print(f"    cluster_id={row['cluster_id']}")
        cluster = row.get("cluster") or {}
        if cluster:
            print(
                f"    cluster: '{cluster.get('label')}' "
                f"(baseline_covers={cluster.get('baseline_covers')}, "
                f"observed={cluster.get('observed')})"
            )
        print(f"    description: {row['description']}")
        body_preview = row["body"][:200].replace("\n", " ")
        print(f"    body (first 200 chars): {body_preview}")
        print(f"    approve: lc admin approve {row['id']}")
        print(f"    reject:  lc admin reject {row['id']}")
        print()
    return 0


def cmd_admin_approve(args: argparse.Namespace) -> int:
    _request("POST", f"/admin/approve/{args.artifact_id}")
    return 0


def cmd_admin_reject(args: argparse.Namespace) -> int:
    _request("POST", f"/admin/reject/{args.artifact_id}")
    return 0


def cmd_admin_bootstrap_agents(args: argparse.Namespace) -> int:
    """`lc admin bootstrap-agents [--mode commit|nocommit|revert]` —
    explicit operator-trigger for the §3.7 layer-3 cycle. Routes
    through `/admin/bootstrap-agents` so the prompt strings stay in
    ONE place (the daemon's `bootstrap_agents` module) — CLI and OWUI
    Pipe both go through the same endpoint."""
    body = {"mode": args.mode, "actor": "cli"}
    res = _request("POST", "/admin/bootstrap-agents", json=body)
    task_id = res["task_id"]
    print(f"bootstrap-agents (mode={res['mode']}): task {task_id} {res['status']}")
    if args.no_wait:
        return 0
    # Same streaming shape as `cmd_task` so the operator can watch
    # the agent's work without reaching for `lc tasks`.
    last = None
    while True:
        state = _request("GET", f"/tasks/{task_id}")
        marker = (state["status"], state.get("commands", 0))
        if marker != last:
            cmds = marker[1]
            suffix = f" · {cmds} command(s)" if cmds else ""
            print(f"  → {state['status']}{suffix}")
            last = marker
        if state["status"] in _TERMINAL:
            for a in state.get("activity") or []:
                mark = "ok" if a.get("ok") else f"exit {a.get('exit_code')}"
                print(f"    $ {a.get('command', '')}  [{mark}]")
            answer = (state.get("answer") or "").strip()
            if answer:
                print("\n" + answer)
            print(f"\noutcome: {state.get('outcome')}  ({state.get('detail', '')})")
            return 0 if state["status"] == "done" else 1
        time.sleep(2)


def cmd_admin_upstream_pull(args: argparse.Namespace) -> int:
    """`lc admin upstream pull --new-commit <sha> [--old-commit <sha>]` —
    journal an upstream pull and list tier-3 skills to review per
    design §12.2. The actual `git fetch` + merge happens on the host
    BEFORE calling this; the daemon just records and surfaces what
    the operator should look at next."""
    if not args.new_commit:
        _err("--new-commit is required (the SHA you pulled to)")
        return 2
    body = {"new_commit": args.new_commit}
    if args.old_commit:
        body["old_commit"] = args.old_commit
    res = _request("POST", "/admin/upstream/pull", json=body)
    review = res.get("tier3_to_review") or []
    print(
        f"upstream pull journaled: {res.get('old_commit') or '?'} → "
        f"{res.get('new_commit') or '?'}"
    )
    if not review:
        print("no live tier-3 skills to review.")
        return 0
    print(f"{len(review)} tier-3 skill(s) to review:")
    for row in review:
        print(
            f"  [{row['id']}] status={row['status']} "
            f"cluster={row['cluster_id']} — {row['name']}"
        )
    print(
        "If the upstream pull now provides any of these, retire them via "
        "`lc admin reject <id>` (the rejection will be journaled as the "
        "design §12.2 `invalidated_by_upstream` trail)."
    )
    return 0


def cmd_admin_task_cancel(args: argparse.Namespace) -> int:
    res = _request("POST", f"/tasks/{args.task_id}/cancel")
    print(res.get("status", "cancelled"))
    return 0


def cmd_admin_docs_sync(args: argparse.Namespace) -> int:
    """`lc admin docs sync` — regenerate the auto module index in
    AGENTS.md. Runs LOCALLY against the source checkout (does NOT
    talk to the daemon — the daemon container's source mount is
    read-only, so the write target is the host checkout)."""
    from .docs_sync import main as docs_sync_main

    forwarded: list[str] = []
    if args.source:
        forwarded.extend(["--source", args.source])
    if args.agents_md:
        forwarded.extend(["--agents-md", args.agents_md])
    if args.check:
        forwarded.append("--check")
    return docs_sync_main(forwarded)


def cmd_admin_observe(args: argparse.Namespace) -> int:
    """`lc admin observe` — show the Observer report (design §3f).
    With `--iterate`, run a fresh meta iteration first."""
    from .observer import render_text

    params = {"iterate": "true"} if args.iterate else {}
    res = _request("GET", "/admin/observe", params=params)
    if not res.get("enabled", False):
        print(res.get("note", "Observer is disabled"))
        return 0
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(render_text(res))
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

    pending_parser = asub.add_parser(
        "pending", help="list pending skill drafts (Chapter 4)"
    )
    pending_parser.add_argument(
        "--json", action="store_true", help="raw JSON output"
    )
    pending_parser.set_defaults(func=cmd_admin_pending)
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

    cc = atask_sub.add_parser("cancel", help="interrupt a running or queued task")
    cc.add_argument("task_id")
    cc.set_defaults(func=cmd_admin_task_cancel)

    boot = asub.add_parser(
        "bootstrap-agents",
        help=(
            "operator-trigger an AGENTS.md bootstrap for the focused repo "
            "(design §3.7 layer 3). Three modes: commit (default — bootstrap "
            "+ separate commit), nocommit (bootstrap but leave uncommitted), "
            "revert (undo bootstrap + drop .no-agents-md opt-out marker)."
        ),
    )
    boot.add_argument(
        "--mode",
        choices=["commit", "nocommit", "revert"],
        default="commit",
        help="bootstrap mode (default: commit)",
    )
    boot.add_argument(
        "--no-wait",
        action="store_true",
        help="return after queueing the task; don't stream progress",
    )
    boot.set_defaults(func=cmd_admin_bootstrap_agents)

    aups = asub.add_parser("upstream", help="upstream fork-parent administration")
    aups_sub = aups.add_subparsers(dest="upstream_cmd", required=True)
    pull_parser = aups_sub.add_parser(
        "pull", help="journal an upstream pull + list tier-3 skills to review"
    )
    pull_parser.add_argument(
        "--new-commit", required=True, help="the SHA you pulled to"
    )
    pull_parser.add_argument(
        "--old-commit", default="", help="the SHA before the pull (optional)"
    )
    pull_parser.set_defaults(func=cmd_admin_upstream_pull)

    docs_parser = asub.add_parser(
        "docs", help="documentation utilities (e.g. AGENTS.md sync)"
    )
    docs_sub = docs_parser.add_subparsers(dest="docs_cmd", required=True)
    sync_parser = docs_sub.add_parser(
        "sync",
        help=(
            "regenerate the auto module index in AGENTS.md. Runs LOCALLY "
            "against the host checkout — does not talk to the daemon."
        ),
    )
    sync_parser.add_argument(
        "--source",
        default="src/littlecoder",
        help="path to the src/littlecoder dir (default: src/littlecoder)",
    )
    sync_parser.add_argument(
        "--agents-md",
        default="AGENTS.md",
        help="path to AGENTS.md (default: AGENTS.md)",
    )
    sync_parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when AGENTS.md is out of date; don't write (pre-commit mode)",
    )
    sync_parser.set_defaults(func=cmd_admin_docs_sync)

    obs = asub.add_parser(
        "observe", help="show the Observer report (design §3f, Chapter 3)"
    )
    obs.add_argument(
        "--iterate",
        action="store_true",
        help="run a fresh meta iteration before reading the report",
    )
    obs.add_argument("--json", action="store_true", help="raw JSON output")
    obs.set_defaults(func=cmd_admin_observe)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
