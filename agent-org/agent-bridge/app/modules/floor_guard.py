"""floor-guard — the deterministic enforcement of hard-rule #4 (§4.2, P3.3).

"Prompt for steering; hook for enforcement" — a prompt-level norm is advisory, and the
paper shows agents route around advisory norms (F3). So the irreversible/external actions
(push / deploy / delete / spend / send-outside) are BLOCKED at the tool layer unless a
cleared Human-Operator decision authorized them.

This is "asymmetric neurosymbolic coupling": the symbolic floor can override the model,
never the reverse. The weaker the model, the more the floor carries — so on our local
fleet the enforcement, not the prompt, is load-bearing.

The pure classifier + decision live here (unit-testable); `hooks/pretooluse_floor.py` is
the thin CLI wrapper the worker harness runs as a PreToolUse hook.
"""

from __future__ import annotations

import logging
import re

from .scope_ledger import ScopeLedger

log = logging.getLogger("agent_bridge.floor_guard")

# Hard-rule #4 action classes (OD-5 — explicit, enforced at the tool layer).
IRREVERSIBLE_PATTERNS: dict[str, re.Pattern] = {
    "push": re.compile(r"\bgit\s+push\b", re.I),
    "deploy": re.compile(r"\b(deploy|docker\s+compose\s+up|kubectl\s+apply|helm\s+install)\b", re.I),
    "delete": re.compile(r"\b(rm\s+-rf|drop\s+(table|database)|truncate|git\s+push\s+.*--delete)\b", re.I),
    "spend": re.compile(r"\b(purchase|checkout|charge|stripe|billing)\b", re.I),
    "send-outside": re.compile(r"\b(send(mail)?|smtp|curl\s+-X\s+POST\s+https?://(?!localhost|127\.))\b", re.I),
}


class FloorGuard:
    def __init__(self, scope: ScopeLedger) -> None:
        self.scope = scope

    @staticmethod
    def classify(action: str) -> str | None:
        """Return the irreversible class the action falls into, or None if reversible."""
        for cls, pat in IRREVERSIBLE_PATTERNS.items():
            if pat.search(action):
                return cls
        return None

    async def allowed(self, subject: str, action: str) -> tuple[bool, str]:
        """(allowed, reason). Reversible actions are always allowed. An irreversible
        action is allowed ONLY if the subject holds a scope grant for that class — and
        irreversible grants can only come from the human (enforced in ScopeLedger.grant)."""
        cls = self.classify(action)
        if cls is None:
            return True, "reversible"
        if await self.scope.authorized(subject, cls):
            return True, f"cleared: {subject} holds a human-granted {cls} scope"
        return False, (
            f"BLOCKED by floor (hard-rule #4): irreversible action '{cls}' requires a "
            f"cleared Human-Operator decision; {subject} has no such grant"
        )
