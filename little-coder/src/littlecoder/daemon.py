"""Control daemon — the little-coder container's main process (design §3.1).

Owns the FIFO task queue (one task at a time, design §12.4), the task
lifecycle and journals, project focus (design §12.3), and SIGTERM drain
(design §12.7). Exposes an internal HTTP API on `lc-net` — reachable by the
`lc` CLI and, from Chapter 2, by `lc-mcpo`. It is NOT the task-trigger
authentication surface; that is `lc-mcpo` (design §12.6).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import __version__
from .agent import AgentRunner, TaskTimeout, kill_process_group, read_activity_file
from .audit import AuditLog
from .config import Config, load_config
from .journals import Journals, utc_now
from .meta import should_trigger
from .meta_wiring import build_meta_runner
from .observer import report_dict
from .openterminal import OpenTerminalClient
from .sanitize import Sanitizer, redact_secrets
from .tasks import TaskContext, TaskState, TaskStatus
from .ulid import new_ulid
from .urlnorm import NormalizedRepo, RepoUrlError, normalize_repo_url
from .workspace import (
    SwitchAction,
    WorkspaceManager,
    decide_switch,
    detect_primary_language,
)
from . import metrics

_VALID_CHANNELS = {"owui", "cli", "validation", "batch"}
_VALID_OUTCOMES = {"pass", "fail", "unverified"}
_WORKER_STOP = "\x00stop"  # sentinel pushed onto the queue to end the worker


def _count_started_tasks(journals_dir: str) -> int:
    """Count `task_started` records across all `outcomes.jsonl` segments.
    Used as a durable "tasks observed since the journal began" counter
    for efficacy snapshots (design §8.5). Cheap line-scan; the actual
    JSON parse is only for the event field."""
    import json as _json
    from pathlib import Path as _Path

    total = 0
    root = _Path(journals_dir)
    for path in sorted(root.glob("outcomes*.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if rec.get("event") == "task_started":
                        total += 1
        except OSError:
            continue
    return total


# --------------------------------------------------------------------------
# Request bodies.
# --------------------------------------------------------------------------


class TriggerRequest(BaseModel):
    prompt: str
    channel: str = "cli"
    user_id: str = "cli"
    session_id: str | None = None
    acceptance_command: str | None = None


class ProjectRequest(BaseModel):
    repo: str
    actor: str = "cli"
    # Optional per-request deploy token (overrides the global LC_DEPLOY_TOKEN). Lets a caller (the
    # agent-org bridge) supply a DIFFERENT token per project — e.g. a personal PAT for one repo and
    # an org PAT for another — instead of one ambient token for everything. Env only on the caller;
    # transits the internal network per request; never journaled raw (clone redacts the token).
    token: str | None = None
    # Optional FORK parent. When `repo` is a fork, `upstream` is the parent repo URL; the daemon
    # bakes it as a read-only `upstream` remote AFTER the clone (operator setup path) so the worker
    # can `git fetch upstream` / `merge --no-ff upstream/...` but push only to `origin` (the fork).
    # Re-applied on every focus — the workspace is wiped on switch, so `upstream` is never assumed
    # to persist; the caller (agent-org bridge) holds the source of truth. `upstream_token` is a
    # read-scoped PAT for a PRIVATE parent (public parents need none). Never journaled raw.
    #
    # NB — distinct from little-coder's OWN `/admin/upstream/pull` (the fork-parent of the *tool*,
    # self-improvement). This `upstream` is a git remote in the *project* workspace.
    upstream: str | None = None
    upstream_token: str | None = None
    # Force a clean re-clone even when already focused on this URL (NOOP). The URL can point at a
    # DIFFERENT repo than the cached workspace — e.g. the operator deleted + recreated it — leaving a
    # stale, unrelated history that fails to push. Operator-plane composition sets this so it always
    # starts from the true remote state.
    fresh: bool = False


class SubmoduleRequest(BaseModel):
    """Add a git submodule to the FOCUSED (composition) repo — operator-plane setup for
    autonomous-project-lifecycle P-APL.1b. `url` is the submodule (usually a public fork → no token);
    `path` is where it mounts; `token` is a read-scoped token for a PRIVATE submodule. The daemon must
    already be focused on the composition repo (its origin carries the push token)."""

    url: str
    path: str
    commit_message: str | None = None
    token: str | None = None
    actor: str = "agent-bridge"


class ConfirmRequest(BaseModel):
    outcome: str
    actor: str = "cli"


class ShutdownRequest(BaseModel):
    drain_deadline_seconds: int | None = None


# --------------------------------------------------------------------------
# Daemon.
# --------------------------------------------------------------------------


class LittleCoderDaemon:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.journals = Journals(
            config.journals.dir,
            rotation_max_bytes=config.journals.rotation_max_bytes,
            fsync_on_terminal=config.journals.fsync_on_terminal,
        )
        self.audit = AuditLog(config.journals.dir)
        self.sanitizer = Sanitizer(
            mode=config.sanitization.mode,
            max_body_bytes=config.sanitization.max_body_bytes,
        )
        self.ot = OpenTerminalClient(
            base_url=config.workspace.open_terminal_url,
            api_key=os.environ.get(config.workspace.open_terminal_key_env, ""),
            default_cwd=config.workspace.path,
            default_timeout=config.workspace.exec_timeout_seconds,
        )
        self.workspace = WorkspaceManager(self.ot, workspace_path=config.workspace.path)
        self.agent = AgentRunner(config, self.journals, self.ot)

        # Observer outer loop (design §3.2, Chapter 3). Constructed
        # unconditionally so `/admin/observe` works even when disabled —
        # disabled mode returns the "Observer is off" skeleton, never
        # silently nothing. The judge (Stage 3) is wired in only when
        # BOTH `observer.enabled` AND `observer.judge_enabled` are on —
        # the second flag exists so the operator can flip the LLM-in-the-
        # loop on AFTER they've dry-run-calibrated the prompt
        # (design §13, open item #2).
        self.meta = build_meta_runner(config)

        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: dict[str, TaskState] = {}
        self.contexts: dict[str, TaskContext] = {}
        self.current_focus: NormalizedRepo | None = None
        self.in_flight: str | None = None
        self.draining = False
        self._drain_deadline = config.shutdown.drain_deadline_seconds
        self._worker_task: asyncio.Task | None = None
        self._metrics_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        metrics.set_build_info(__version__, "Tool")
        if self.cfg.metrics.enabled:
            metrics.start_metrics_server(self.cfg.metrics.port)
        self._seed_focus()
        self._worker_task = asyncio.create_task(self._worker(), name="lc-worker")
        self._metrics_task = asyncio.create_task(
            self._metrics_loop(), name="lc-metrics"
        )

    def _seed_focus(self) -> None:
        """Derive the current focus from ground truth — the actual clone in
        the workspace — rather than carrying separate state that could drift."""
        if not self.workspace.is_focused():
            return
        try:
            res = self.ot.execute(
                "git config --get remote.origin.url",
                cwd=self.cfg.workspace.path,
                timeout=30,
            )
            if res.ok and res.stdout.strip():
                self.current_focus = normalize_repo_url(res.stdout.strip())
        except Exception:  # open-terminal not up yet — corrected on first /project
            pass

    async def shutdown(self) -> None:
        """SIGTERM drain (design §12.7): refuse new triggers, let the
        in-flight task finish to the deadline, abandon stragglers."""
        if self.draining:
            return
        self.draining = True
        deadline = time.monotonic() + self._drain_deadline
        while self.in_flight is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        if self.in_flight is not None:
            tid = self.in_flight
            self.journals.write(self.contexts[tid].abandoned("shutdown"))
            self.tasks[tid].status = TaskStatus.ABANDONED
            self.tasks[tid].detail = "abandoned: drain deadline exceeded"
            kill_process_group(self.tasks[tid].agent_process)  # free the worker
        await self.queue.put(_WORKER_STOP)
        if self._worker_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._worker_task), 30)
        if self._metrics_task is not None:
            self._metrics_task.cancel()
        self.audit.write(
            "shutdown", actor="system", drain_deadline_seconds=self._drain_deadline
        )

    # -- worker ------------------------------------------------------------

    async def _worker(self) -> None:
        """Single consumer — one task at a time (design §12.4)."""
        while True:
            task_id = await self.queue.get()
            try:
                if task_id == _WORKER_STOP:
                    return
                state = self.tasks.get(task_id)
                if state is None or state.status is not TaskStatus.QUEUED:
                    continue
                if self.draining:
                    # Queued but unstarted when the drain began — abandon it.
                    self.journals.write(self.contexts[task_id].abandoned("shutdown"))
                    state.status = TaskStatus.ABANDONED
                    state.ended_ts = utc_now()
                    state.detail = "abandoned: shutting down"
                    metrics.record_task("abandoned")
                    continue
                await self._run_task(state)
            finally:
                self.queue.task_done()

    async def _run_task(self, state: TaskState) -> None:
        self.in_flight = state.task_id
        ctx = self.contexts[state.task_id]
        state.status = TaskStatus.RUNNING
        state.started_ts = utc_now()
        # Detect language now that the workspace is focused (envelope reads
        # state.lang / state.repo live).
        state.lang = detect_primary_language(self.cfg.workspace.path)
        timeout = self.cfg.tasks.abandoned_timeout_seconds.get(state.channel, 21600)
        self.journals.write(ctx.started())
        try:
            result = await asyncio.to_thread(self.agent.run_task, ctx, timeout)
        except TaskTimeout:
            self.journals.write(ctx.abandoned("timeout"))
            state.status = TaskStatus.ABANDONED
            state.detail = f"abandoned: exceeded {timeout}s"
            metrics.record_task("abandoned")
        except Exception as exc:  # never let a task crash the worker
            self.journals.write(ctx.error("daemon_error", str(exc)))
            self.journals.write(ctx.ended("unverified"))
            state.status = TaskStatus.DONE
            state.outcome = "unverified"
            state.detail = f"daemon error: {exc}"
            metrics.record_task("unverified")
        else:
            if state.status is TaskStatus.ABANDONED:
                # The drain already abandoned this task — don't double-close.
                pass
            else:
                self.journals.write(ctx.ended(result.outcome, result.signal))
                state.status = TaskStatus.DONE
                state.outcome = result.outcome
                state.signal = result.signal
                state.detail = f"{result.commands_run} command(s)"
                metrics.record_task(result.outcome)
        finally:
            state.ended_ts = utc_now()
            self.in_flight = None
            await self._maybe_trigger_meta()

    async def _maybe_trigger_meta(self) -> None:
        """Evidence-triggered Observer iteration (design §3.2). Called once
        per task completion; the threshold + single-flight in MetaRunner
        keep this cheap. Errors are swallowed and journaled to `audit` —
        a failed iteration must not affect interactive task throughput
        (design §12.5: interactive lanes always win)."""
        if not self.cfg.observer.enabled or not self.cfg.observer.auto_iterate_on_task_end:
            return
        try:
            count = self.meta._count_journal_records()
        except Exception:
            return  # don't let an FS hiccup affect the worker loop
        if not should_trigger(
            self.meta.state, count, self.cfg.observer.evidence_trigger_records
        ):
            return
        # Fire and forget — single-flight in MetaRunner means a second
        # trigger arriving mid-iteration returns None.
        asyncio.create_task(self._run_meta_iteration())

    async def _run_meta_iteration(self) -> None:
        try:
            result = await asyncio.to_thread(self.meta.iterate)
        except Exception as exc:
            # The judge can raise LlmError on transport problems; the
            # projection should never raise but defensive-catch keeps a
            # bug here from killing the worker. Audit-log so the operator
            # sees the event.
            metrics.record_meta_iteration_failed()
            self.audit.write(
                "observer_iteration_failed",
                actor="meta",
                error=str(exc)[:500],
            )
            return
        if result is None:
            return  # single-flight dropped this trigger
        metrics.record_meta_iteration(
            clusters_total=result.clusters_total,
            occurrences_total=result.occurrences_total,
            unassigned_total=result.unassigned_total,
            minted=len(result.minted_cluster_ids),
        )
        self.audit.write(
            "observer_iteration_completed",
            actor="meta",
            records_consumed=result.records_consumed,
            clusters_total=result.clusters_total,
            occurrences_total=result.occurrences_total,
            unassigned_total=result.unassigned_total,
            minted_cluster_ids=list(result.minted_cluster_ids),
        )

    async def _metrics_loop(self) -> None:
        while True:
            metrics.refresh(
                self.journals,
                self.sanitizer,
                queue_depth=self.queue.qsize(),
                task_in_flight=self.in_flight is not None,
            )
            metrics.poll_llama_slots(self.cfg.inference.base_url)
            await asyncio.sleep(10)

    # -- task operations ---------------------------------------------------

    @property
    def busy(self) -> bool:
        """A task is in flight if one is running OR queued — a project switch
        must not wipe the workspace out from under queued work (design §12.3)."""
        return self.in_flight is not None or self.queue.qsize() > 0

    def enqueue(self, req: TriggerRequest) -> TaskState:
        if self.draining:
            raise HTTPException(503, "shutting down — not accepting new triggers")
        if req.channel not in _VALID_CHANNELS:
            raise HTTPException(422, f"channel must be one of {sorted(_VALID_CHANNELS)}")
        if self.current_focus is None:
            raise HTTPException(409, "no project focused — run /project first")
        if not req.prompt.strip():
            raise HTTPException(422, "empty prompt")
        state = TaskState(
            task_id=new_ulid(),
            session_id=req.session_id or new_ulid(),
            channel=req.channel,
            user_id=req.user_id,
            prompt=req.prompt,
            repo=self.current_focus.canonical_url,
            acceptance_command=req.acceptance_command,
        )
        self.tasks[state.task_id] = state
        self.contexts[state.task_id] = TaskContext(state)
        self.queue.put_nowait(state.task_id)
        return state

    def confirm(self, task_id: str, req: ConfirmRequest) -> TaskState:
        """Outcome amendment (design §4.2) — 7-day window, frozen outside."""
        if req.outcome not in _VALID_OUTCOMES:
            raise HTTPException(422, f"outcome must be one of {sorted(_VALID_OUTCOMES)}")
        state = self.tasks.get(task_id)
        if state is None:
            raise HTTPException(404, f"unknown task {task_id}")
        if state.status not in (TaskStatus.DONE, TaskStatus.ABANDONED):
            raise HTTPException(409, "task has not ended yet")
        ended = state.ended_ts or state.created_ts
        age = time.time() - _parse_ts(ended)
        if age > self.cfg.tasks.outcome_amend_window_seconds:
            raise HTTPException(409, "amendment window (7 days) has closed")
        prior = state.outcome or "unverified"
        ctx = self.contexts[task_id]
        # The amendment lands in outcomes.jsonl (cohort math uses it from here
        # on) AND audit.jsonl (it is an operator action) — design §4.2, §4.4.
        self.journals.write(ctx.amended(req.outcome, prior, req.actor))
        self.audit.write(
            "task_outcome_amended",
            actor=req.actor,
            task_id=task_id,
            outcome=req.outcome,
            prior_outcome=prior,
        )
        state.outcome = req.outcome  # type: ignore[assignment]
        state.detail = f"outcome amended {prior} → {req.outcome} by {req.actor}"
        return state

    def cancel(self, task_id: str) -> dict:
        """Interrupt a task — operator-triggered abandonment (an OWUI 'stop',
        or `lc admin task cancel`). Kills the agent if it is running. This is
        abandonment, not a mid-task write — consistent with design §12.4."""
        state = self.tasks.get(task_id)
        if state is None:
            raise HTTPException(404, f"unknown task {task_id}")
        if state.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            return {
                "task_id": task_id,
                "status": state.status.value,
                "detail": "task already finished",
            }
        ctx = self.contexts.get(task_id)
        if ctx is not None:
            self.journals.write(ctx.abandoned("cancelled"))
        kill_process_group(state.agent_process)  # agent + all descendants
        state.status = TaskStatus.ABANDONED
        state.detail = "cancelled by the operator"
        state.ended_ts = utc_now()
        metrics.record_task("abandoned")
        return {"task_id": task_id, "status": "cancelled"}

    async def switch_project(self, req: ProjectRequest) -> dict:
        try:
            requested = normalize_repo_url(req.repo)
        except RepoUrlError as exc:
            raise HTTPException(422, str(exc)) from exc
        decision = decide_switch(requested, self.current_focus, self.busy)

        # `fresh`: the cached workspace may be STALE/unrelated (repo deleted+recreated at the same
        # URL). Force a clean re-clone instead of trusting the NOOP. Only when no task is in flight.
        if req.fresh and decision.action is SwitchAction.NOOP and not self.busy:
            await asyncio.to_thread(self.workspace.wipe)
            self.current_focus = None
            decision = decide_switch(requested, None, self.busy)   # → CLONE (fresh)

        # A NOOP is only valid when the workspace actually HOLDS a repo — the in-memory focus
        # can outlive the tree (live 2026-07-07: focus said the repo, the volume was empty →
        # the worker was dispatched into a void it cannot escape, since the git-proxy rightly
        # blocks it from cloning for itself). An empty workspace means the focus record lies:
        # drop it and clone.
        if decision.action is SwitchAction.NOOP and not self.busy \
                and not await asyncio.to_thread(self.workspace.is_focused):
            self.current_focus = None
            decision = decide_switch(requested, None, self.busy)   # → CLONE (workspace vanished)

        if decision.action is SwitchAction.NOOP:
            # Already focused — the workspace was NOT wiped, so `origin` is intact… but the token
            # EMBEDDED in its URL at the original clone is SHORT-LIVED (a GitHub App installation
            # token lives 1h). A NOOP re-focus hours later would push with a DEAD credential (the
            # live "expired token in origin" failure). Re-bake origin's auth with the caller's
            # current token — a cheap `remote set-url`; the work in the tree is untouched.
            out: dict = {"action": "noop", "focus": requested.canonical_url}
            noop_token = req.token or os.environ.get("LC_DEPLOY_TOKEN") or None
            if noop_token:
                res = await asyncio.to_thread(
                    self.workspace.refresh_origin_auth, requested, noop_token
                )
                out["origin_reauthed"] = bool(res.ok)
            # `upstream` may be absent (never baked, or baked before the caller knew the parent):
            # ensure it here so a fork's `git fetch upstream` works on a re-focus, not only on the
            # first clone. Idempotent + skipped when already present (has_remote).
            if req.upstream and not await asyncio.to_thread(
                self.workspace.has_remote, "upstream"
            ):
                upstream_ok = await self._bake_upstream(req, requested)
                out["upstream"] = req.upstream
                out["upstream_ok"] = upstream_ok
            return out
        if decision.action is SwitchAction.REJECT:
            raise HTTPException(409, decision.reason)

        if decision.action is SwitchAction.SWITCH:
            label = f"lc-switch-{utc_now().replace(':', '').replace('-', '')}"
            await asyncio.to_thread(self.workspace.tag_prior_state, label)
            await asyncio.to_thread(self.workspace.wipe)
        elif decision.action is SwitchAction.CLONE:
            # Design §12.3 assumes "no current focus" implies an empty
            # workspace, but a prior failed clone can leave a stale
            # `.git/` behind — which then makes the next `git clone`
            # fail with exit 128 "destination path already exists and
            # is not an empty directory". Wipe defensively. The wipe
            # is a no-op when the workspace is genuinely empty.
            await asyncio.to_thread(self.workspace.wipe)

        # Per-request token (from the caller) overrides the global LC_DEPLOY_TOKEN, so different
        # projects can use different PATs (personal vs org). Falls back to the ambient token.
        token = req.token or os.environ.get("LC_DEPLOY_TOKEN") or None
        result = await asyncio.to_thread(self.workspace.clone, requested, token)
        if not result.ok:
            # Surface BOTH streams (git writes some fatals to stdout) so a clone failure is never a
            # blank "(exit 128):"; redacted (a clone-auth error can echo the token-bearing URL).
            tail = (f"{result.stderr}\n{result.stdout}").strip()[-400:]
            raise HTTPException(
                502, f"clone failed (exit {result.exit_code}): {redact_secrets(tail) or '(no output)'}"
            )
        self.current_focus = requested
        self.audit.write(
            "project_switched",
            actor=req.actor,
            repo=requested.canonical_url,
            action=decision.action.value,
        )
        # Fork workflow: bake the read-only `upstream` remote right after the clone, so it's
        # re-derived from the caller's persistent record on EVERY focus (the workspace is
        # ephemeral). Non-fatal — a fork that can't reach its parent is still a usable clone;
        # surface the failure in the response + journal so it isn't a silent stall.
        upstream_ok: bool | None = None
        if req.upstream:
            upstream_ok = await self._bake_upstream(req, requested)
        out = {"action": decision.action.value, "focus": requested.canonical_url}
        if upstream_ok is not None:
            out["upstream"] = req.upstream
            out["upstream_ok"] = upstream_ok
        return out

    async def _bake_upstream(self, req: ProjectRequest, requested) -> bool:
        """Bake (or refresh) the read-only `upstream` remote for a fork and journal the
        outcome. Shared by the clone/switch path (fresh clone) and the NOOP re-focus path
        (workspace not wiped, remote may be missing). Returns whether the git op succeeded —
        non-fatal, but surfaced so a fork that can't reach its parent isn't a silent stall."""
        up = await asyncio.to_thread(
            self.workspace.add_upstream_remote, req.upstream, req.upstream_token
        )
        ok = bool(up.ok)
        self.audit.write(
            "project_upstream_set",
            actor=req.actor,
            repo=requested.canonical_url,
            upstream=req.upstream,
            ok=ok,
        )
        return ok

    async def add_submodule(self, req: SubmoduleRequest) -> dict:
        """Add a submodule to the currently-focused composition repo (operator-plane, real git —
        P-APL.1b). Requires a focused repo; the caller (bridge capability) focuses it first with a
        short-lived push token in origin. Non-fatal errors surface with a redacted detail so a
        failure reads clearly and never leaks a token."""
        if self.current_focus is None or not self.workspace.is_focused():
            raise HTTPException(409, "no project focused — focus the composition repo first")
        res = await asyncio.to_thread(
            self.workspace.add_submodule, req.url, req.path,
            commit_message=req.commit_message, token=req.token,
        )
        self.audit.write(
            "submodule_added",
            actor=req.actor,
            repo=self.current_focus.canonical_url,
            path=req.path,
            ok=bool(res.ok),
        )
        if not res.ok:
            # Surface BOTH streams — git writes some fatals to stdout — so the failure is never a
            # blank "(exit 128):". Redacted; token never leaked.
            tail = (f"{res.stderr}\n{res.stdout}").strip()[-400:]
            raise HTTPException(
                502, f"submodule add failed (exit {res.exit_code}): {redact_secrets(tail) or '(no output)'}"
            )
        return {"ok": True, "path": req.path}


def _parse_ts(ts: str) -> float:
    from datetime import datetime

    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()


# --------------------------------------------------------------------------
# HTTP API.
# --------------------------------------------------------------------------


def build_app(daemon: LittleCoderDaemon) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await daemon.start()
        yield
        await daemon.shutdown()

    app = FastAPI(title="little-coder control daemon", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "draining" if daemon.draining else "ok",
            "version": __version__,
            "chapter": "Tool",
            "focus": daemon.current_focus.canonical_url if daemon.current_focus else None,
            "queue_depth": daemon.queue.qsize(),
            "in_flight": daemon.in_flight,
        }

    @app.post("/tasks")
    def trigger(req: TriggerRequest) -> dict:
        state = daemon.enqueue(req)
        return {"task_id": state.task_id, "status": state.status.value}

    @app.get("/tasks")
    def list_tasks() -> dict:
        return {"tasks": [t.public() for t in daemon.tasks.values()]}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        state = daemon.tasks.get(task_id)
        if state is None:
            raise HTTPException(404, f"unknown task {task_id}")
        data = state.public()
        # While the task runs, surface live activity straight from the
        # ot-exec event stream so the chat/CLI surface can show progress.
        if state.status is TaskStatus.RUNNING and state.event_stream_path:
            live = read_activity_file(state.event_stream_path)
            data["activity"] = live
            data["commands"] = len(live)
        return data

    @app.get("/tasks/{task_id}/events")
    def task_events(task_id: str, offset: int = 0) -> dict:
        """Live pi `--mode json` event stream, from line `offset` onward —
        the chat surface polls this to render the process as it unfolds."""
        state = daemon.tasks.get(task_id)
        if state is None:
            raise HTTPException(404, f"unknown task {task_id}")
        events: list[str] = []
        if state.events_path:
            try:
                with open(state.events_path, encoding="utf-8") as fh:
                    lines = fh.readlines()
                events = [ln.rstrip("\n") for ln in lines[offset:]]
            except OSError:
                events = []
        return {
            "task_id": task_id,
            "status": state.status.value,
            "done": state.status.value in ("done", "abandoned", "rejected"),
            "events": events,
            "next_offset": offset + len(events),
        }

    @app.post("/tasks/{task_id}/confirm")
    def confirm(task_id: str, req: ConfirmRequest) -> dict:
        return daemon.confirm(task_id, req).public()

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict:
        return daemon.cancel(task_id)

    @app.get("/focus")
    def focus() -> dict:
        f = daemon.current_focus
        return {"focus": f.canonical_url if f else None}

    @app.post("/project")
    async def project(req: ProjectRequest) -> dict:
        return await daemon.switch_project(req)

    @app.post("/project/submodule")
    async def project_submodule(req: SubmoduleRequest) -> dict:
        return await daemon.add_submodule(req)

    @app.get("/admin/observe")
    def observe(iterate: bool = False) -> dict:
        """Observer's read-only report (design §3f, Chapter 3). Reads
        the on-disk cohort store; with `?iterate=true` runs a fresh
        iteration first (single-flight — second concurrent call returns
        the existing snapshot). Disabled-Observer mode returns the
        report skeleton with `enabled: false`."""
        if not daemon.cfg.observer.enabled:
            return {
                "enabled": False,
                "note": "Observer is disabled — set observer.enabled in the config to turn on",
                "last_iteration": None,
                "knowledge_gaps": [],
                "compliance_gaps": [],
                "unassigned": [],
            }
        if iterate:
            # Operator-triggered iteration shares the same telemetry path
            # as the auto-trigger (single-flight, metrics, audit).
            try:
                result = daemon.meta.iterate()
            except Exception as exc:
                metrics.record_meta_iteration_failed()
                daemon.audit.write(
                    "observer_iteration_failed",
                    actor="operator",
                    error=str(exc)[:500],
                )
                raise HTTPException(503, f"iteration failed: {exc}") from exc
            if result is not None:
                metrics.record_meta_iteration(
                    clusters_total=result.clusters_total,
                    occurrences_total=result.occurrences_total,
                    unassigned_total=result.unassigned_total,
                    minted=len(result.minted_cluster_ids),
                )
                daemon.audit.write(
                    "observer_iteration_completed",
                    actor="operator",
                    records_consumed=result.records_consumed,
                    clusters_total=result.clusters_total,
                    occurrences_total=result.occurrences_total,
                    unassigned_total=result.unassigned_total,
                    minted_cluster_ids=list(result.minted_cluster_ids),
                )
        store = daemon.meta.load_store()
        out = report_dict(store, daemon.meta.state.last_result)
        out["enabled"] = True
        return out

    # Operator approval surface (design §4f, §12.6).
    @app.get("/admin/pending")
    def pending() -> dict:
        """List pending skill drafts. Each row carries the artifact's
        text + frontmatter + cluster provenance, so the operator can
        decide without leaving the surface."""
        from .skills import iter_skills

        out: list[dict] = []
        if daemon.cfg.observer.enabled:
            store = daemon.meta.load_store()
        else:
            store = None
        for skill in iter_skills(daemon.cfg.paths.skill_dir, status="pending"):
            fm = skill.frontmatter
            row: dict = {
                "id": skill.id,
                "name": fm.name,
                "description": fm.description,
                "body": skill.body,
                "tier": fm.tier,
                "kind": fm.kind,
                "lang": fm.lang,
                "domain": fm.domain,
                "task_shape": fm.task_shape,
                "cluster_id": fm.cluster_id,
                "created": fm.created,
            }
            # Cluster provenance for at-a-glance review.
            if store is not None:
                cluster = store.clusters.get(fm.cluster_id)
                counter = store.counters.get(fm.cluster_id)
                row["cluster"] = (
                    {
                        "label": cluster.label,
                        "discriminator": cluster.discriminator,
                        "baseline_covers": cluster.baseline_covers,
                        "observed": counter.observed if counter else 0,
                        "top_repos": (
                            sorted(
                                counter.per_repo_observed.items(),
                                key=lambda kv: kv[1],
                                reverse=True,
                            )[:3]
                            if counter
                            else []
                        ),
                    }
                    if cluster
                    else None
                )
            out.append(row)
        return {"pending": out}

    @app.post("/admin/approve/{artifact_id}")
    def approve(artifact_id: str) -> dict:
        """Approve a pending skill — flip status pending → active so the
        augmenter starts retrieving it. Journals to `audit.jsonl` WITH
        a snapshot of `(observed_at_approve, tasks_at_approve)` per
        design §8.5 — efficacy reversion compares post-merge rate
        against this snapshot, so the snapshot MUST be captured at
        merge time. Without it, tier-1 escalation + retirement can
        never run on this artifact.

        Validation gate (§4d) is NOT auto-run here: the Polyglot oracle
        runs against an operator-imported corpus that may not exist
        yet. The operator inspects the body + cluster provenance and
        makes the call."""
        from .skills import flip_status, SkillFormatError, iter_skills

        target_skill = None
        for s in iter_skills(daemon.cfg.paths.skill_dir, status="pending"):
            if s.id == artifact_id:
                target_skill = s
                break
        if target_skill is None:
            raise HTTPException(404, f"no pending skill with id={artifact_id!r}")
        try:
            flipped = flip_status(daemon.cfg.paths.skill_dir, artifact_id, "active")
        except (SkillFormatError, FileNotFoundError) as exc:
            raise HTTPException(500, f"approve failed: {exc}") from exc

        # Capture the §8.5 snapshot. `observed_at_approve` reads the
        # cluster's current counter; `tasks_at_approve` is derived
        # from journals (durable across restarts; metric counters
        # reset on bounce).
        observed_at_approve = 0
        if daemon.cfg.observer.enabled:
            store = daemon.meta.load_store()
            counter = store.counters.get(flipped.frontmatter.cluster_id)
            if counter:
                observed_at_approve = counter.observed
        tasks_at_approve = _count_started_tasks(daemon.cfg.journals.dir)

        daemon.audit.write(
            "approve_decision",
            actor="operator",
            artifact_id=artifact_id,
            cluster_id=flipped.frontmatter.cluster_id,
            tier=flipped.frontmatter.tier,
            kind=flipped.frontmatter.kind,
            observed_at_approve=observed_at_approve,
            tasks_at_approve=tasks_at_approve,
        )
        return {
            "status": "approved",
            "id": artifact_id,
            "cluster_id": flipped.frontmatter.cluster_id,
            "snapshot": {
                "observed_at_approve": observed_at_approve,
                "tasks_at_approve": tasks_at_approve,
            },
        }

    @app.post("/admin/reject/{artifact_id}")
    def reject(artifact_id: str) -> dict:
        """Reject a pending skill — flip status pending → retired (kept
        on disk for audit; never selected by the augmenter, design
        §8.5 retirement semantics). Journals to `audit.jsonl`.

        The operator can also reject an already-active skill the same
        way — useful when a previously-merged artifact turns out to
        be wrong before efficacy reversion catches it."""
        from .skills import flip_status, SkillFormatError, iter_skills

        target_skill = None
        for s in iter_skills(daemon.cfg.paths.skill_dir, status=None):
            if s.id == artifact_id and s.frontmatter.status in ("pending", "active"):
                target_skill = s
                break
        if target_skill is None:
            raise HTTPException(
                404,
                f"no pending or active skill with id={artifact_id!r}",
            )
        prior_status = target_skill.frontmatter.status
        try:
            flipped = flip_status(daemon.cfg.paths.skill_dir, artifact_id, "retired")
        except (SkillFormatError, FileNotFoundError) as exc:
            raise HTTPException(500, f"reject failed: {exc}") from exc
        daemon.audit.write(
            "approve_decision",
            actor="operator",
            artifact_id=artifact_id,
            decision="reject",
            prior_status=prior_status,
            cluster_id=flipped.frontmatter.cluster_id,
            tier=flipped.frontmatter.tier,
        )
        return {
            "status": "rejected",
            "id": artifact_id,
            "prior_status": prior_status,
        }

    @app.post("/admin/bootstrap-agents")
    def bootstrap_agents(req: dict = None) -> dict:
        """Operator-triggered AGENTS.md bootstrap (design §3.7 layer 3).

        Three modes — `commit` (default: write + separate commit),
        `nocommit` (write but leave uncommitted), `revert` (find +
        revert the bootstrap commit OR delete the file + drop the
        `.no-agents-md` opt-out marker).

        The endpoint validates the mode, builds the matching prompt
        from `bootstrap_agents.prompt_for(mode)`, and triggers a task
        via the existing `enqueue` path so the agent does the actual
        work. Returns `{task_id, mode}` — the caller streams the task
        with `/tasks/{task_id}/events` like any normal trigger."""
        from .bootstrap_agents import VALID_MODES, prompt_for

        req = req or {}
        mode = str(req.get("mode") or "commit").strip().lower()
        actor = str(req.get("actor") or "operator").strip() or "operator"

        if mode not in VALID_MODES:
            raise HTTPException(
                422,
                f"invalid mode {mode!r}; valid: {sorted(VALID_MODES)}",
            )
        if daemon.current_focus is None:
            raise HTTPException(
                409,
                "no project focused — run `/project repo: <url>` first; "
                "bootstrap needs a workspace to act on",
            )

        try:
            prompt = prompt_for(mode)  # type: ignore[arg-type]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        trigger = TriggerRequest(
            prompt=prompt,
            channel="cli",
            user_id=actor,
            session_id=None,
        )
        state = daemon.enqueue(trigger)
        daemon.audit.write(
            "bootstrap_agents_triggered",
            actor=actor,
            mode=mode,
            task_id=state.task_id,
            focus=daemon.current_focus.canonical_url if daemon.current_focus else None,
        )
        return {
            "task_id": state.task_id,
            "status": state.status.value,
            "mode": mode,
        }

    @app.post("/admin/upstream/pull")
    def upstream_pull(req: dict = None) -> dict:
        """Operator-initiated upstream pull (design §12.2, Chapter 5 §5i).

        The actual `git fetch` + merge happens on the HOST (the operator
        runs it against the little-coder source checkout — the daemon
        container doesn't own the source). This endpoint:
          1. Journals `upstream_pulled` with old + new commit ids.
          2. Lists the cluster ids of all live self-authored tier-3
             artifacts so the operator can review which (if any) the
             upstream now provides — those should be retired via the
             existing `/admin/reject/<id>` endpoint, and the rejection
             journaled as `invalidated_by_upstream`.

        Body (POST): `{old_commit: str, new_commit: str}`. Both are
        free-text (the operator supplies them from `git rev-parse`)
        — we don't try to verify them inside the container."""
        from .skills import iter_skills

        req = req or {}
        old_commit = str(req.get("old_commit") or "").strip()
        new_commit = str(req.get("new_commit") or "").strip()
        if not new_commit:
            raise HTTPException(
                422,
                "new_commit required — supply the SHA you pulled to "
                "(typically `git rev-parse HEAD`)",
            )

        tier3_review: list[dict] = []
        if daemon.cfg.paths.skill_dir:
            for skill in iter_skills(daemon.cfg.paths.skill_dir, status=None):
                if skill.frontmatter.tier == 3:
                    tier3_review.append(
                        {
                            "id": skill.id,
                            "cluster_id": skill.frontmatter.cluster_id,
                            "status": skill.frontmatter.status,
                            "name": skill.frontmatter.name,
                        }
                    )
        daemon.audit.write(
            "upstream_pulled",
            actor="operator",
            old_commit=old_commit,
            new_commit=new_commit,
            tier3_to_review_count=len(tier3_review),
        )
        return {
            "status": "journaled",
            "old_commit": old_commit,
            "new_commit": new_commit,
            "tier3_to_review": tier3_review,
            "note": (
                "operator must inspect the tier3_to_review list — for each "
                "self-authored tier-3 skill the upstream now provides, "
                "call /admin/reject/<id> and that retirement will be "
                "journaled per design §12.2"
            ),
        }

    @app.post("/admin/shutdown")
    def admin_shutdown(req: ShutdownRequest) -> dict:
        if req.drain_deadline_seconds is not None:
            daemon._drain_deadline = req.drain_deadline_seconds
        # Trigger uvicorn's graceful path → lifespan shutdown → drain.
        os.kill(os.getpid(), signal.SIGTERM)
        return {"status": "draining", "drain_deadline_seconds": daemon._drain_deadline}

    return app


def main() -> None:
    config = load_config(os.environ.get("LC_CONFIG", "/app/config/little-coder.config.yaml"))
    daemon = LittleCoderDaemon(config)
    app = build_app(daemon)
    uvicorn.run(app, host=config.daemon.host, port=config.daemon.port, log_level="info")


if __name__ == "__main__":
    main()
