"""main — FastAPI app + control surface + lifespan (PLAN §5.1).

The HTTP surface is the operator/hook/test control plane. The *primary* runtime surface is
the chat bus (Mattermost) consumed by the event-gateway; these endpoints are for the floor
hook (P3.3), operator actions from tooling, and deterministic tests.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .adapters.chat import FakeChatAdapter
from .adapters.mattermost import MattermostAdapter
from .config import get_settings
from .db import Database
from .orchestrator import Orchestrator
from .schemas import Concern, Decision, Trigger

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agent_bridge")


# Request-body models MUST live at module scope: with `from __future__ import annotations`
# FastAPI resolves body-param annotations via typing.get_type_hints against module globals,
# so a class defined inside create_app() would be mis-read as a query param.
class FloorCheck(BaseModel):
    subject: str
    action: str


class EffortIn(BaseModel):
    name: str


class ConcernIn(BaseModel):
    effort_id: str
    trigger: Trigger
    concern: Concern
    actor: str = "pm"


class DecisionIn(BaseModel):
    effort_id: str
    decision: Decision
    actor_role: str = "human"


class KillIn(BaseModel):
    on: bool = True


class LaneIn(BaseModel):
    name: str
    lane: str


class SuggestionIn(BaseModel):
    worker: str
    text: str
    effort_id: str | None = None


class RiskIn(BaseModel):
    effort_id: str
    risk: str  # routine | irreversible | cross_effort | cascading_refactor


class DryRunIn(BaseModel):
    effort_id: str
    passed: bool = True


class PrepareIn(BaseModel):
    effort_id: str
    request: str
    risk: str = "routine"


class ProjectIn(BaseModel):
    name: str
    repo_url: str
    token_env: str | None = None  # NAME of the env var holding this project's deploy token


class EgressIn(BaseModel):
    host: str  # a bare host or a repo URL


class LateralIn(BaseModel):
    effort_id: str
    from_role: str
    text: str


class HandoffIn(BaseModel):
    effort_id: str
    path: str
    workspace: str = "/workspace"


def build_chat(settings):
    if settings.chat_adapter == "mattermost":
        return MattermostAdapter(
            settings.mattermost_url, settings.mattermost_bot_token, settings.mattermost_ws_url,
            site_url=settings.mattermost_site_url,
        )
    return FakeChatAdapter()


def create_app(orch: Orchestrator | None = None) -> FastAPI:
    settings = get_settings()
    if orch is None:
        db = Database(settings.database_url)
        chat = build_chat(settings)
        orch = Orchestrator(settings, db, chat)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await orch.chat.start()
        await orch.setup()
        orch.events.start()
        log.info("agent-bridge up (adapter=%s, workers cap=%d)",
                 settings.chat_adapter, settings.max_concurrent_workers)
        yield
        await orch.events.stop()
        await orch.chat.stop()
        await orch.db.dispose()

    app = FastAPI(title="agent-bridge", version="0.1.0", lifespan=lifespan)
    app.state.orch = orch

    # ── health ────────────────────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    # ── floor hook (P3.3) ───────────────────────────────────────────────────────
    @app.post("/hook/floor-check")
    async def floor_check(body: FloorCheck) -> dict:
        allowed, reason = await orch.floor_guard.allowed(body.subject, body.action)
        return {"allowed": allowed, "reason": reason}

    # ── efforts / gate ──────────────────────────────────────────────────────────
    @app.post("/effort")
    async def create_effort(body: EffortIn) -> dict:
        effort_id, channel_id = await orch.router.ensure_effort_channel(body.name)
        orch.events.track_channel(channel_id)
        return {"effort_id": effort_id, "channel_id": channel_id}

    @app.get("/state/{effort_id}")
    async def state(effort_id: str) -> dict:
        return {
            "effort_id": effort_id,
            "gate_state": await orch.gate.state_of(effort_id),
            "can_dispatch": await orch.gate.can_dispatch(effort_id),
            "plan_status": await orch.planner.plan_status(effort_id),
        }

    @app.post("/concern")
    async def concern(body: ConcernIn) -> dict:
        await orch.raise_concern(body.effort_id, body.trigger, body.concern, actor=body.actor)
        return {"frozen": True, "state": await orch.gate.state_of(body.effort_id)}

    @app.post("/decision")
    async def decision(body: DecisionIn) -> dict:
        try:
            await orch.apply_operator_decision(
                body.effort_id, body.decision, actor_role=body.actor_role
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc
        return {"state": await orch.gate.state_of(body.effort_id)}

    @app.post("/kill-switch")
    async def kill(body: KillIn) -> dict:
        await orch.gate.kill_switch(on=body.on, actor="human")
        return {"kill_switch": await orch.gate.kill_switch_engaged()}

    # ── scheduler / audit ─────────────────────────────────────────────────────
    @app.get("/scheduler")
    async def scheduler() -> dict:
        return {"instances": await orch.scheduler.snapshot(), "cap": settings.max_concurrent_workers}

    @app.get("/audit")
    async def audit(effort_id: str | None = None) -> dict:
        return {"events": await orch.audit.replay(effort_id)}

    # ── profiles (Pc.3 lane flip) ──────────────────────────────────────────────
    @app.get("/profiles")
    async def profiles() -> dict:
        return {"profiles": {k: v.model_dump() for k, v in orch.profiles.all().items()}}

    @app.post("/profiles/lane")
    async def set_lane(body: LaneIn) -> dict:
        await orch.profiles.set_lane(body.name, body.lane)
        return {"profile": orch.profiles.get(body.name).model_dump()}

    # ── ground + dry-run, risk-gated (P4.0) ────────────────────────────────────
    @app.post("/effort/risk")
    async def set_risk(body: RiskIn) -> dict:
        try:
            st = await orch.exec_gate.set_risk(body.effort_id, body.risk)
        except KeyError as exc:
            raise HTTPException(404, f"unknown effort {exc}") from exc
        return {"effort_id": body.effort_id, "risk": body.risk, "dry_run_status": st}

    @app.post("/effort/dry-run")
    async def record_dry_run(body: DryRunIn) -> dict:
        try:
            await orch.exec_gate.record_dry_run(body.effort_id, passed=body.passed)
        except KeyError as exc:
            raise HTTPException(404, f"unknown effort {exc}") from exc
        return await orch.exec_gate.status(body.effort_id)

    @app.post("/effort/prepare")
    async def prepare_execution(body: PrepareIn) -> dict:
        return await orch.prepare_execution(body.effort_id, body.request, risk=body.risk)

    @app.get("/execution/{effort_id}")
    async def execution_status(effort_id: str) -> dict:
        return await orch.exec_gate.status(effort_id)

    # ── projects (multi-project registry — COMMS-MODEL §4) ─────────────────────
    @app.get("/projects")
    async def list_projects() -> dict:
        return {"projects": await orch.projects.list()}

    @app.post("/projects")
    async def add_project(body: ProjectIn) -> dict:
        proj = await orch.projects.add(
            body.name, body.repo_url, created_by="operator", token_env=body.token_env
        )
        chan = await orch.router.ensure_project_channel(proj["slug"])
        await orch.projects.set_channel(proj["slug"], chan)
        orch.events.track_channel(chan)
        if proj["git_host"]:
            await orch.egress.allow(proj["git_host"], added_by="operator", source="project")
            await orch.egress.sync()
        return {"project": proj, "channel_id": chan}

    # ── worker git-egress allowlist (remotely managed scope, governance §5) ─────
    @app.get("/egress")
    async def list_egress() -> dict:
        return {"hosts": await orch.egress.hosts()}

    @app.post("/egress")
    async def allow_egress(body: EgressIn) -> dict:
        host = await orch.egress.allow(body.host, added_by="operator", source="manual")
        await orch.egress.sync()
        return {"allowed": host, "hosts": await orch.egress.hosts()}

    # ── lateral concern (P4.8) + A→B hand-off (P5.4) — worker/role signals ──────
    @app.post("/lateral-concern")
    async def lateral_concern(body: LateralIn) -> dict:
        await orch.raise_lateral_concern(body.effort_id, body.from_role, body.text)
        return {"ok": True}

    @app.post("/handoff")
    async def handoff(body: HandoffIn) -> dict:
        owner = await orch.hand_off(body.effort_id, body.path, workspace=body.workspace)
        return {"owner": owner}

    # ── suggestion pool (P6.3) — recorded AND surfaced to #suggestions (CM.5) ───
    @app.post("/suggestion")
    async def suggestion(body: SuggestionIn) -> dict:
        sig = await orch.record_suggestion(body.worker, body.text, body.effort_id)
        return {"signature": sig}

    @app.get("/suggestions")
    async def suggestions() -> dict:
        return {"pool": await orch.learning.pool()}

    return app


app = create_app()


def main() -> None:  # pragma: no cover - container entrypoint
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
