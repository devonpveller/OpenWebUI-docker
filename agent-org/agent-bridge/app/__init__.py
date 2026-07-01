"""agent-bridge — the governance-gated orchestration seam.

Implements the SAFETY-AND-WORKFLOW-governance-model.md spec as a set of
single-responsibility modules (PLAN §3.1.1). The governance gate (machine A)
is deliberately isolated from WebSocket/REST plumbing so P2's safety tests can
target it deterministically.
"""

__version__ = "0.1.0"
