"""Priority policy — a Strategy (Open/Closed): new callers/classes plug in via
config, not code edits (design §10.2). Resolves (key, model) -> PriorityClass.

SECURITY (design §10.3.2): priority is COOPERATIVE, not a trust boundary. The
gateway runs permissive (no master_key) and callers self-assert plaintext keys,
so a caller *could* present a high-priority key to jump the queue. This is an
optimization among trusted internal callers, not a security control — it tightens
automatically when master_key + real virtual keys land. Priority is ALWAYS
derived server-side from the attributed key, NEVER from a client-supplied
X-Priority-style header (header-trust = injection).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


@dataclass(frozen=True)
class PriorityClass:
    """An ordering tier. Lower ``rank`` dispatches first."""

    name: str
    rank: int
    acceptable_wait_s: float
    max_concurrency: int | None = None  # per-key in-flight cap (None = unlimited)


class PriorityPolicy:
    """Maps a caller key to its PriorityClass. Config-driven, no code edits."""

    def __init__(self, classes: dict[str, PriorityClass], default: PriorityClass) -> None:
        self._classes = classes
        self._default = default

    def classify(self, key: str | None) -> PriorityClass:
        if key:
            # Exact match first, then substring (keys are self-asserted strings
            # like "ollama"/"llama"/"owui-chat"; match generously).
            if key in self._classes:
                return self._classes[key]
            for token, cls in self._classes.items():
                if token and token in key:
                    return cls
        return self._default

    @property
    def classes(self) -> dict[str, PriorityClass]:
        return dict(self._classes)

    @property
    def default(self) -> PriorityClass:
        return self._default

    def set_key(self, key: str, cls: PriorityClass) -> None:
        """Runtime override (control API POST /keys/{key}/policy, design §4.2)."""
        self._classes[key] = cls


# Default ordering from design §8c, expressed over the key strings callers
# actually present today (legacy direct callers sent "ollama", little-coder
# sends "llama", others send junk/empty). Tune in P2 as real keys
# are attributed. Budgets: interactive short, batch long.
_DEFAULT_CLASSES: dict[str, PriorityClass] = {
    "owui-chat": PriorityClass("owui-chat", rank=0, acceptable_wait_s=30.0),
    "ollama": PriorityClass("ollama", rank=1, acceptable_wait_s=60.0),
    "ob-mcp": PriorityClass("ob-mcp", rank=1, acceptable_wait_s=60.0),
    "llama": PriorityClass("lc-coder", rank=2, acceptable_wait_s=120.0),
    "lc-coder": PriorityClass("lc-coder", rank=2, acceptable_wait_s=120.0),
    "ob-entity": PriorityClass("ob-entity", rank=3, acceptable_wait_s=600.0, max_concurrency=2),
    "ob-wiki": PriorityClass("ob-wiki", rank=3, acceptable_wait_s=600.0, max_concurrency=2),
    # Overnight deep-research / daily-digest podcast lane (callers set the OpenAI
    # `user` field — research-service sends "ob-research"). This is an ASYNC fan-out
    # that is happy to wait a long time for a deep dive, so it gets the most generous
    # acceptable-wait budget — the queue HOLDS its requests through a saturation
    # window instead of 429-ing them (which under sustained load blew past LiteLLM's
    # 3 retries and killed the job). max_concurrency caps it at 2 of the 3 slots so a
    # research burst can never starve interactive owui-chat (rank 0 preempts anyway).
    "ob-research": PriorityClass("ob-research", rank=3, acceptable_wait_s=1800.0,
                                 max_concurrency=2),
    "ob-podcast": PriorityClass("ob-research", rank=3, acceptable_wait_s=1800.0, max_concurrency=2),
    # Morning digest lane (openbrain-digest: weather brief, synthesizer, semantic
    # search) — wired to the gateway in #27 (LOCAL_LLM_BEARER / OB_DIGEST_LLM_KEY).
    "ob-digest": PriorityClass("ob-digest", rank=3, acceptable_wait_s=600.0, max_concurrency=2),
}


# ── research-branch lane (research-workbench P4a, D16) ─────────────────────────
#: The caller key (LiteLLM virtual key metadata.lane -> x-ai-stack-caller) of research-tree
#: branches: steers, gap work and on-demand idea research. Operator-provisioned key.
RESEARCH_BRANCH_KEY = "research-branch"


def research_branch_concurrency(settings: Settings) -> tuple[int, str | None]:
    """The lane's concurrency: ALL of the plane's slots (D16 as amended 2026-10-26).

    Returns ``(n, warning)``; ``warning`` is set when LLM_QUEUE_SLOTS is unset or blank - the lane
    then runs at the queue's default slot count, which is also the scheduler's P.
    """
    if not settings.slots_declared:
        return settings.slots, (
            "LLM_QUEUE_SLOTS is not set: the research-branch lane runs at the queue's default "
            f"slot count ({settings.slots})"
        )
    return settings.slots, None


def _research_branch_class(n: int) -> PriorityClass:
    # rank 3 = the batch tier: chat (rank 0) and interactive callers are dispatched first by the
    # queue's ordinary priority policy; the lane only bounds how much of the pool branches hold.
    return PriorityClass(RESEARCH_BRANCH_KEY, rank=3, acceptable_wait_s=1800.0, max_concurrency=n)


def build_policy(
    policy_json: str, default_wait_s: float, research_branch: int | None = None
) -> PriorityPolicy:
    """Build the policy from optional JSON config, falling back to the §8c defaults.

    ``policy_json`` shape: {"<key>": {"class": str, "rank": int,
    "acceptable_wait_s": float, "max_concurrency": int|null}, ...}

    ``research_branch`` (P4a): the derived lane concurrency. When given, the research-branch
    class always carries it - a policy entry for the key keeps its rank / wait budget but its
    max_concurrency is replaced (one number describes the plane; no second, hand-set cap).
    """
    policy = _build_policy(policy_json, default_wait_s)
    if research_branch is not None:
        cur = policy.classes.get(RESEARCH_BRANCH_KEY)
        cls = (replace(cur, max_concurrency=research_branch) if cur is not None
               else _research_branch_class(research_branch))
        policy.set_key(RESEARCH_BRANCH_KEY, cls)
    return policy


def _build_policy(policy_json: str, default_wait_s: float) -> PriorityPolicy:
    default = PriorityClass("default", rank=2, acceptable_wait_s=default_wait_s)
    if not policy_json.strip():
        return PriorityPolicy(dict(_DEFAULT_CLASSES), default)

    raw = json.loads(policy_json)
    classes: dict[str, PriorityClass] = {}
    for key, spec in raw.items():
        classes[key] = PriorityClass(
            name=spec.get("class", key),
            rank=int(spec.get("rank", 2)),
            acceptable_wait_s=float(spec.get("acceptable_wait_s", default_wait_s)),
            max_concurrency=spec.get("max_concurrency"),
        )
    return PriorityPolicy(classes, default)
