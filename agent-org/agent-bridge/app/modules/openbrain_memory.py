"""Agent-memory write paths for agent-bridge (memory-plane PLAN §2.1).

Governance: agent-org writes what it LEARNED, never what it should DO. Everything this
module produces is evidence-only and pending review — the locked §1 write defaults — so a
memory written here cannot direct future behaviour until a human confirms it. The server
enforces that too; this module does not get to choose it.

THE EXPOSURE STAMP IS THE POINT OF THE `tainted` ARGUMENT (§1.1, operator-DECIDED
2026-08-25). An agent-org effort is ops-clean BY CONSTRUCTION unless it consumed Tier-2
advisor output — that corpus includes personal-plane, gmail-derived sources — or its goal
text came from a personal-plane surface. The orchestrator knows both; this module reports
what it is told and never guesses. The value can only ever DEMOTE: `stampExposure`
server-side has no path that widens, so a caller that lies about taint makes its own memory
narrower, never wider.

FAIL-SOFT IS A LAW HERE, not a nicety. A memory write must never take the bridge down and
must never change the outcome of an effort. Every public coroutine swallows everything and
returns False; the underlying client already does the same. An effort that finishes
correctly while Open Brain is down is the required behaviour, not a degraded one.

Both flags default OFF (`AO_MEMORY_WRITEBACK_ENABLED`, `AO_MEMORY_RECALL_ENABLED`), house
style: a write path that turns itself on at deploy time is how the audit mirror spent weeks
failing silently in production while its code default said it was off.
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from .openbrain_client import OpenBrainClient

log = logging.getLogger(__name__)

# The workspace every agent-org memory belongs to (PLAN §1).
WORKSPACE = "ai-stack"

# What the summary is allowed to be. Long enough to be useful to a reviewer, short enough
# that the corpus does not fill with pasted output — §1's write-back content rules say
# compact summaries and source refs, never raw transcripts.
SUMMARY_MAX = 300
CONTENT_MAX = 4000


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Marked, not silently cut: a reviewer must be able to tell a truncated memory from a
    # short one, or they will read an ellipsis as the end of the thought.
    return text[: limit - 15].rstrip() + " …[truncated]"


def build_outcome_memory(
    *,
    effort_id: str,
    project: str,
    succeeded: bool,
    head: str = "",
    done_word: str = "",
    where: str = "",
    branch: str = "",
    pr_url: str = "",
    effort_url: str = "",
    tainted: bool = False,
) -> dict[str, Any]:
    """The writeback payload for one finished effort. PURE — no network, no clock.

    Separated from the write so the payload shape is testable without a transport, which is
    the half that actually goes wrong: the agent-memory writeback shipped once with an
    insert into a column that did not exist and every stubbed test passed.

    `memory_type` is 'output' on a clean close and 'failure' otherwise. Both are worth
    keeping — an effort that failed and why is the more useful memory of the two, and a
    corpus of successes only is a corpus that cannot warn anyone.
    """
    parts = [p for p in (head, done_word, where) if p]
    summary = _clip(" — ".join(parts) or f"effort {effort_id} finished", SUMMARY_MAX)

    lines: list[str] = []
    if head:
        lines.append(head)
    if done_word:
        lines.append(f"Outcome: {done_word}")
    if where:
        lines.append(f"Where: {where}")
    if branch:
        lines.append(f"Branch: {branch}")
    refs = [u for u in (pr_url, effort_url) if u]
    if refs:
        lines.append("Sources: " + " ".join(refs))

    return {
        "workspace_id": WORKSPACE,
        "project_id": project or None,
        "summary": summary,
        "content": _clip("\n".join(lines), CONTENT_MAX),
        "memory_type": "output" if succeeded else "failure",
        # IDEMPOTENT BY EFFORT. _finish_effort is reachable more than once for one effort
        # (re-close, abort-after-finish), and the plan keys on this exactly so a re-run is a
        # no-op rather than a second memory saying the same thing.
        "idempotency_key": f"outcome-{effort_id}",
        "tainted": bool(tainted),
        "metadata": {
            "runtime_name": "agent-bridge",
            "task_id": effort_id,
            "source": "effort_outcome",
        },
    }


def build_constraint_memory(
    *,
    constraint_id: str,
    effort_id: str,
    project: str,
    text: str,
    tainted: bool = False,
) -> dict[str, Any]:
    """A failure-derived constraint, promoted from effort scope to project scope.

    §2.3 explicitly REJECTS auto-writing `acceptance_checks` from these: those are
    executable merge gates, and turning prose into a hard gate without a human changes the
    propose-not-dispose posture. This writes a reviewable memory instead; a human can still
    elevate it into a real acceptance check through the existing flow.
    """
    return {
        "workspace_id": WORKSPACE,
        "project_id": project or None,
        "summary": _clip(text, SUMMARY_MAX),
        "content": _clip(text, CONTENT_MAX),
        "memory_type": "constraint",
        # Keyed by the CONSTRAINT, not the effort: the same constraint promoted twice is one
        # memory, and a second effort hitting the same wall must not duplicate it.
        "idempotency_key": f"constraint-{constraint_id}",
        "tainted": bool(tainted),
        "metadata": {
            "runtime_name": "agent-bridge",
            "task_id": effort_id,
            "source": "constraint_promotion",
            "constraint_id": constraint_id,
        },
    }


class OpenBrainMemory:
    """Write paths into the agent-memory plane. Every method fails soft."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.client = OpenBrainClient(settings)

    @property
    def transport(self):  # noqa: ANN201 - mirrors the house injectable-transport pattern
        return self.client.transport

    @transport.setter
    def transport(self, value) -> None:  # noqa: ANN001
        self.client.transport = value

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.s, "memory_writeback_enabled", False))

    async def write(self, payload: dict[str, Any]) -> bool:
        """Write one memory. Returns False for every failure, including 'switched off'.

        A caller must never branch on this in a way that changes an effort's outcome — see
        the fail-soft law in the module docstring.
        """
        if not self.enabled:
            return False
        if not self.s.openbrain_key:
            # Said once, at the point of use. A silent no-op here is how the audit mirror
            # went unnoticed: everything looked configured and nothing was written.
            log.warning("agent-memory writeback enabled but openbrain_key is empty")
            return False
        try:
            return await self.client.call_tool("agent_memory_writeback", payload)
        except Exception as exc:  # noqa: BLE001 - the fail-soft law
            log.warning("agent-memory writeback failed: %s", exc)
            return False

    async def write_effort_outcome(self, **kw: Any) -> bool:
        return await self.write(build_outcome_memory(**kw))

    async def write_constraint(self, **kw: Any) -> bool:
        return await self.write(build_constraint_memory(**kw))
