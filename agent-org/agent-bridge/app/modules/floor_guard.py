"""floor-guard — the deterministic enforcement of hard-rule #4 (§4.2, P3.3).

"Prompt for steering; hook for enforcement" — a prompt-level norm is advisory, and the
paper shows agents route around advisory norms (F3). So the truly irreversible/external
actions are BLOCKED at the tool layer unless a cleared Human-Operator decision authorized them.

**Additive vs irreversible (2026-07-02 correction).** A commit + a push to a *feature branch* is
ADDITIVE and reversible — it's how workers preserve, share, and hand off work (without it, a
`/project` wipe destroys everything a worker did). It is NOT gated. The gate is on the genuinely
irreversible/destructive ones: **publishing to the protected branch (`main`/`master`) — the merge
moment**, **destructive git** (force-push, ref/branch/tag delete, history rewrite, `reset --hard`),
**deploy**, **delete** (`rm -rf`, drop/truncate), **spend**, and **send-outside**. This mirrors what
the little-coder git-proxy already enforces (additive push allowed; destructive blocked).

This is "asymmetric neurosymbolic coupling": the symbolic floor can override the model, never the
reverse. The pure classifier + decision live here (unit-testable); `hooks/pretooluse_floor.py` is
the thin CLI wrapper the worker harness runs as a PreToolUse hook.
"""

from __future__ import annotations

import logging
import re

from .scope_ledger import ScopeLedger

log = logging.getLogger("agent_bridge.floor_guard")

# Hard-rule #4 action classes (OD-5). Ordered: git-specific before broad. An additive push to a
# feature branch matches NONE of these → reversible/routine.
IRREVERSIBLE_PATTERNS: dict[str, re.Pattern] = {
    # Publishing to the PROTECTED branch (the "merge to main" moment) — human-gated. A push to a
    # feature branch (e.g. `git push origin agent/x`) does NOT match.
    "publish-main": re.compile(r"\bgit\s+push\b[^|;&\n]*?\b(main|master|trunk)(?:\s|$)", re.I),
    # Destructive / history-rewriting / ref-deleting git ops (the truly irreversible ones).
    "destructive-git": re.compile(
        r"\bgit\s+push\b[^|;&\n]*(--force\b|--force-with-lease\b|-f\b|--mirror\b|--delete\b|\s:\S)"
        r"|\bgit\s+(rebase|filter-branch|filter-repo|reflog|update-ref)\b"
        r"|\bgit\s+reset\s+--hard\b"
        r"|\bgit\s+(branch|tag)\s+(-D|--delete|-d)\b",
        re.I,
    ),
    "deploy": re.compile(r"\b(deploy|docker\s+compose\s+up|kubectl\s+apply|helm\s+install)\b", re.I),
    "delete": re.compile(r"\b(rm\s+-rf|drop\s+(table|database)|truncate)\b", re.I),
    # NB: no bare "checkout" — it collides with `git checkout`; use unambiguous payment terms.
    "spend": re.compile(r"\b(purchase|charge|stripe|billing|payment|paypal|invoice)\b", re.I),
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
