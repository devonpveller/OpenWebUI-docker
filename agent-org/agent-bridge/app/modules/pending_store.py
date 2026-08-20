"""pending-store — the DB-backed store of decisions awaiting the operator's `approve <id>`.

The orchestrator holds proposals (lifecycle plans, capability actions, Stage-3 effort plans) in
in-memory dicts so they resolve fast. But an in-memory-only proposal is LOST on a bridge restart —
the operator rebuilds/bounces the container and the hard gate they were about to clear silently
vanishes (`no id's in the chat`). That violates the fail-safe posture (§3): a pending human decision
must survive a bounce. This module owns the durable mirror AND the pending-decision semantics
(jsonify for persistence; boot rehydration; merge-gate reconciliation; enumerating/rendering what's
pending) — the orchestrator keeps thin same-signature delegators plus the in-memory dicts
themselves, and passes them (with its collaborators) in explicitly.

Mirrors `ParkStore` (capacity_park) — same shape, second kind of durable orchestrator state."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select

from ..config import Settings
from ..db import Database
from ..models import PendingApproval
from ..schemas import LifecyclePlan, Plan
from .audit_sink import AuditSink
from .capabilities import read_open_pr_numbers
from .github_app import GitHubApp

log = logging.getLogger("agent_bridge.pending_store")


class PendingStore:
    def __init__(self, db: Database, audit: AuditSink) -> None:
        self.db = db
        self.audit = audit

    async def save(self, pid: str, kind: str, payload: dict) -> None:
        """Persist (or refresh) a pending proposal. Upsert so a re-draft under the same id replaces
        the stored payload. `payload` MUST already be JSON-safe (the caller dumps any pydantic plan)."""
        async with self.db.session_factory() as s:
            row = await s.get(PendingApproval, pid)
            if row is None:
                s.add(PendingApproval(id=pid, kind=kind, payload=payload))
            else:
                row.kind, row.payload = kind, payload
            await s.commit()

    async def delete(self, pid: str) -> None:
        """Remove a proposal the instant it's decided (approve/abort). Idempotent — no-op if absent."""
        async with self.db.session_factory() as s:
            row = await s.get(PendingApproval, pid)
            if row is not None:
                await s.delete(row)
                await s.commit()

    async def all(self) -> list[dict]:
        """Every persisted pending proposal (oldest first) for rehydration on boot."""
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(PendingApproval).order_by(PendingApproval.created_at))
            ).scalars().all()
        return [{"id": r.id, "kind": r.kind, "payload": r.payload} for r in rows]

    # ── pending-decision semantics (moved from the orchestrator, which keeps thin delegators) ──

    @staticmethod
    def jsonify_pending(entry: dict) -> dict:
        """A JSON-safe copy of a pending-store entry for persistence: any pydantic plan under `plan`
        is `model_dump`'d; everything else is already str/None. The in-memory dict keeps the live
        object — only the persisted mirror is flattened."""
        out = dict(entry)
        plan = out.get("plan")
        if hasattr(plan, "model_dump"):
            out["plan"] = plan.model_dump(mode="json")
        return out

    async def rehydrate(
        self, pending_lifecycle: dict, pending_capability: dict,
        pending_plan: dict, pending_merge: dict,
    ) -> None:
        """Boot: restore the in-memory pending dicts from the durable store so a proposal held
        across a restart is still resolvable (a bare/keyed `approve` finds it). A payload that no
        longer deserializes (schema drift) is dropped, not fatal — boot must never wedge on it."""
        for row in await self.all():
            pid, kind, payload = row["id"], row["kind"], dict(row["payload"])
            try:
                if kind == "lifecycle":
                    payload["plan"] = LifecyclePlan(**payload["plan"])
                    pending_lifecycle[pid] = payload
                elif kind == "capability":
                    pending_capability[pid] = payload
                elif kind == "effort_plan":
                    payload["plan"] = Plan(**payload["plan"])
                    pending_plan[pid] = payload
                elif kind == "merge":
                    pending_merge[pid] = payload
                else:
                    continue
            except Exception as exc:  # noqa: BLE001 — a drifted row must not crash boot; drop it
                log.warning("dropping unrehydratable pending %s (%s): %s", pid, kind, exc)
                await self.delete(pid)
        n = (len(pending_lifecycle) + len(pending_capability)
             + len(pending_plan) + len(pending_merge))
        if n:
            log.info("rehydrated %d pending approval(s) held across a restart", n)

    async def reconcile_merge_gates(
        self, pending_merge: dict, *, github: GitHubApp | None, settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Drop merge gates whose PR is no longer OPEN on the remote (merged / closed / repo
        cleaned up) — 11 stale gates once buried the ONE real decision behind a wall of dead
        options (operator 2026-07-08: bare `approve` listed 14 items). Batched per repo;
        fail-open — an unreadable remote never drops a gate."""
        if not pending_merge or github is None or not settings.github_app_enabled:
            return
        by_repo: dict[str, list[str]] = {}
        for mid, e in list(pending_merge.items()):
            by_repo.setdefault((e.get("repo") or "").strip(), []).append(mid)
        for repo, mids in by_repo.items():
            if not repo:
                continue
            nums = await read_open_pr_numbers(
                github, repo, api_base=settings.github_api_base,
                transport=transport)
            if nums is None:
                continue   # unreadable → keep everything (fail-open)
            for mid in mids:
                pr = int((pending_merge.get(mid) or {}).get("pr_number") or 0)
                if pr and pr not in nums:
                    pending_merge.pop(mid, None)
                    await self.delete(mid)
                    await self.audit.log(
                        "merge_gate_pruned",
                        effort_id=(mid[len("merge-"):] if mid.startswith("merge-") else None),
                        payload={"merge_id": mid, "pr": pr, "repo": repo})

    async def decisions(
        self, pending_lifecycle: dict, pending_capability: dict,
        pending_plan: dict, pending_merge: dict, *,
        reconcile: Callable[[], Awaitable[None]],
        snapshot: Callable[..., Awaitable[list[dict]]],
        status_map: Callable[[list[dict]], Awaitable[dict[str, str]]],
    ) -> list[str]:
        """Every item currently awaiting an explicit operator decision — drafted lifecycle plans
        (P-APL.3), proposed capability actions (P-APL.1), held Stage-3 effort plans (P3.9), and
        efforts frozen on a concern (§3). De-duped, insertion order. Used so a bare `approve`/`abort`
        (no id) can resolve THE single pending item unambiguously instead of erroring with a usage
        string — the operator typed the decision verb explicitly; we only fill an unambiguous
        target. Merge gates are RECONCILED against the remote first (stale ones pruned)."""
        await reconcile()
        ids: list[str] = [
            *pending_lifecycle.keys(),
            *pending_capability.keys(),
            *pending_plan.keys(),
            *pending_merge.keys(),
        ]
        try:
            efforts = await snapshot(open_only=True)
            smap = await status_map(efforts)
            ids += [e["id"] for e in efforts if smap.get(e["id"]) == "paused"]
        except Exception as exc:  # noqa: BLE001 — status enumeration must never break the command
            log.debug("_pending_decisions status sweep failed: %s", exc)
        seen: set[str] = set()
        return [i for i in ids if not (i in seen or seen.add(i))]

    @staticmethod
    def render_pending(
        pending_lifecycle: dict, pending_capability: dict,
        pending_merge: dict, pending_plan: dict, only: str | None = None,
    ) -> str:
        """The queue of proposals awaiting an `approve <id>` — drafted plans, proposed forks, held
        effort plans — rendered for `/status` so a restart-restored (or scrolled-past) hard gate is
        VISIBLE without re-asking. `only` limits it to a single id (targeted `/status <id>`). Empty
        string when nothing (matching) is pending."""
        items: list[tuple[str, str]] = []
        for pid, e in pending_lifecycle.items():
            plan = e.get("plan")
            goal = (getattr(plan, "goal", None) or e.get("intent") or "plan").strip()
            n = len(getattr(plan, "steps", []) or [])
            items.append((pid, f"📋 plan: {goal} ({n} step{'' if n == 1 else 's'})"))
        for aid, e in pending_capability.items():
            items.append((aid, f"🛠️ fork `{e.get('parent', '?')}`"))
        for mid, e in pending_merge.items():
            items.append((mid, f"🔀 merge PR #{e.get('pr_number', '?')} on "
                               f"`{(e.get('repo') or '').split('github.com/')[-1]}` — say “merge it”"))
        for eid, e in pending_plan.items():
            plan = e.get("plan")
            feat = (getattr(plan, "feature_overview", None) or e.get("request") or "").strip()
            items.append((eid, f"📋 effort plan: {feat[:80]}"))
        if only is not None:
            items = [(i, d) for (i, d) in items if i == only]
        if not items:
            return ""
        lines = "\n".join(f"- `{i}` — {d}" for i, d in items)
        hint = ("_Reply `approve` or `abort` — it's the only thing pending._" if len(items) == 1
                else "_Reply `approve <id>` or `abort <id>`._")
        return "**⛔ Awaiting your approval:**\n" + lines + "\n" + hint
