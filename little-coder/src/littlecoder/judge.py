"""The judge — LLM that mints clusters from the unassigned pool (design §5.2,
§10.1, Chapter 3 §3e).

Observer's judge has ONE responsibility (Chapter 3 only): look at the
unassigned pool for a (lang, task_shape) scope and decide whether the
pool itself forms a coherent group worth minting as a new cluster. It
emits at most a handful of proposals per call, each carrying:

  - `label`           human-readable cluster name (mutable — design §5.1)
  - `discriminator`   the boundary text future occurrences are scored
                      against (also mutable; §5.2)
  - `baseline_covers` does the founding-knowledge baseline already say
                      what this cluster is about? Tier-0/tier-1 gate
                      (locked decision #17 — compliance gap vs knowledge
                      gap). Tier-0 only fires for `baseline_covers: false`
  - `reasoning`       why these signals cohere
  - `not_other_types` why this is one cluster and not N (the adversarial
                      half of the prompt — design §10.1)

What the judge does NOT do in Observer:
  - Draft artifacts (Chapter 4 — tier-0/1 knowledge / tool-craft entries)
  - Pick types (Chapter 4 — tier-1 tool-craft vs plan-slot)
  - Write anywhere (Observer is read-only)

Two design contracts pinned in code:

  1. **Counterfactual + adversarial framing (design §10.1).** The system
     prompt instructs the judge to argue both 'these cohere' AND 'these
     are noise / N separate things', then make the call. We invoke with
     `temperature=0` for determinism in tests; production runs may raise
     it.

  2. **Founding knowledge in the judge's context (locked #17).** Every
     judge call carries the operator-authored baseline files
     (`agent-knowledge/environment.md` + `engineering-principles.md`).
     The judge consults them to set `baseline_covers`. Without the
     baseline in context the compliance-vs-knowledge distinction
     collapses to "tier-0 everything", which would have meta re-teach
     the agent things it was already told.

Sanitization runs in `ChatClient` (enforcing mode in the control plane),
so this module never touches secrets directly — it composes prompts and
parses structured JSON. A SanitizerError propagates and aborts the call
(design §10.2 — never 'send anyway').
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field, ValidationError

from .clusters import Cluster, Occurrence, new_cluster_id
from .llm import ChatLike, ChatMessage, ChatResponse, LlmError


# --- structured output --------------------------------------------------


class ClusterProposal(BaseModel):
    """One newly-proposed cluster. The judge can emit zero or more of
    these per call (the unassigned pool may contain noise, one cluster,
    or several distinct clusters)."""

    label: str = Field(..., min_length=2, max_length=120)
    discriminator: str = Field(..., min_length=2, max_length=2000)
    # Which of the input signals (by index in the pool) belong to this
    # cluster. The judge's job is partitioning + labeling; we use the
    # indices to wire results back to original Occurrences.
    signal_indices: list[int] = Field(default_factory=list)
    baseline_covers: bool = Field(...)
    reasoning: str = Field(default="", max_length=4000)
    not_other_types: str = Field(default="", max_length=4000)


class JudgeOutput(BaseModel):
    """The structured response shape. `clusters` may be empty — "this pool
    is incoherent / too noisy / too small" is a legitimate answer."""

    clusters: list[ClusterProposal] = Field(default_factory=list)
    pool_too_small: bool = Field(default=False)
    pool_too_noisy: bool = Field(default=False)


@dataclasses.dataclass
class MintingResult:
    """What `Judge.mint_clusters` returns. `new_clusters` is the
    judge-minted Cluster objects (with fresh `cluster_id`s); `consumed`
    is the subset of input occurrences that the judge attached to one of
    the new clusters."""

    new_clusters: list[Cluster]
    consumed: list[Occurrence]
    raw_output: JudgeOutput


# --- prompt assembly ----------------------------------------------------


_SYSTEM_PROMPT = """\
You are the META judge for a self-improving coding agent's outer loop.

You are NOT writing code, fixing bugs, or speaking to the agent. Your job
is to examine a pool of error signals the agent emitted across many tasks
in one (language, task-shape) scope, and decide whether the pool forms a
coherent cluster — one underlying craft gap — or whether it is noise,
distinct gaps, or too small to call yet.

You have THREE inputs:
  1. The FOUNDING KNOWLEDGE — the operator-authored baseline the agent
     ALREADY reads at every task start. This is the floor.
  2. The unassigned POOL — signals the assignment function couldn't
     route to an existing cluster.
  3. The (language, task_shape) SCOPE.

For each coherent cluster you find, you MUST argue BOTH sides:
  - WHY these signals cohere (one craft gap, one fix).
  - WHY they might NOT — could they be two clusters? Noise? Already
    covered by an existing cluster you weren't shown?
Then decide. If the adversarial side wins, return zero clusters.

For each cluster you DO mint, set `baseline_covers`:
  - TRUE  if the founding knowledge already states (clearly or
          plausibly) what the cluster needs. A recurring cluster the
          baseline already covers is a COMPLIANCE GAP — the agent isn't
          following an instruction it already has. This must NOT become
          a tier-0 knowledge entry (which would just restate what's
          already there); it escalates to tier-1 enforcement.
  - FALSE if the founding knowledge is silent on this. Genuine knowledge
          gap — tier-0 candidate.
Be honest here. A wrong `true` blocks tier-0 unnecessarily; a wrong
`false` lets meta re-teach the agent something it already knows.

OUTPUT FORMAT — a JSON object matching this schema:
{
  "clusters": [
    {
      "label": "short human-readable name (≤ 120 chars)",
      "discriminator": "boundary text — future signals will be scored \
against this. Be specific enough that an unrelated signal scores low.",
      "signal_indices": [0, 2, 5],  // which pool entries belong here
      "baseline_covers": false,
      "reasoning": "WHY these cohere — what's the underlying gap?",
      "not_other_types": "WHY this is one cluster, not N — argue the \
adversarial position before concluding."
    }
  ],
  "pool_too_small": false,
  "pool_too_noisy": false
}

Constraints:
  - signal_indices MUST be non-overlapping across clusters and within
    range [0, pool_size-1]. A signal can belong to at most one new
    cluster on this pass; ambiguous signals stay in the pool.
  - Returning zero clusters with `pool_too_noisy: true` is a perfectly
    valid answer — Observer never forces minting.
  - Reasoning fields are bounded. Be specific and brief.

Return ONLY the JSON. No prose before or after."""


def _founding_knowledge_block(paths: Iterable[Path]) -> str:
    """Inline the founding knowledge files as one block. Order matters:
    environment (constraints) → project-context (orientation) →
    engineering-principles (craft). Missing files are skipped silently
    (the judge can still partly do its job; meta logs the gap)."""
    sections: list[str] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        sections.append(f"=== {Path(path).name} ===\n{text.strip()}")
    if not sections:
        return "(no founding-knowledge files were provided to the judge)"
    return "\n\n".join(sections)


def _pool_block(occurrences: list[Occurrence]) -> str:
    """Render the pool as numbered items. Indices are 0-based to match
    the schema's `signal_indices` semantics."""
    lines: list[str] = []
    for i, occ in enumerate(occurrences):
        kind = f"({occ.source_kind})" if occ.source_kind else ""
        lines.append(f"[{i}] {kind} {occ.signal_text}")
    return "\n".join(lines)


def build_messages(
    pool: list[Occurrence],
    *,
    lang: str,
    task_shape: str,
    founding_knowledge_paths: Iterable[Path],
) -> list[ChatMessage]:
    """Assemble the chat messages for one minting call."""
    fk = _founding_knowledge_block(founding_knowledge_paths)
    pool_text = _pool_block(pool)
    user = (
        f"SCOPE: lang={lang or '<unknown>'}, task_shape={task_shape or '<unknown>'}\n"
        f"POOL SIZE: {len(pool)}\n\n"
        f"--- FOUNDING KNOWLEDGE ---\n{fk}\n"
        f"--- POOL ---\n{pool_text}\n"
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user),
    ]


# --- response parsing ---------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """Models that ignore the 'JSON only' instruction sometimes wrap the
    output in ```json … ```. Strip those fences before parsing — the
    structured content is still recoverable."""
    s = text.strip()
    if s.startswith("```"):
        # ```json\n{...}\n```  or ```\n{...}\n```
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


def parse_response(content: str) -> JudgeOutput:
    """Parse the LLM's reply into `JudgeOutput`. Raises LlmError if the
    reply doesn't validate — judge non-compliance is a hard fail, never
    'best-effort cluster anyway' (design §1: nothing fails open)."""
    stripped = _strip_code_fence(content)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LlmError(f"judge reply was not JSON: {exc.msg}") from exc
    try:
        return JudgeOutput.model_validate(data)
    except ValidationError as exc:
        raise LlmError(f"judge reply did not match schema: {exc}") from exc


# --- the judge ----------------------------------------------------------


class Judge:
    """Constructs the prompt, calls the LLM, parses the result, and turns
    proposals into Cluster + Occurrence pairs ready for `cohorts.apply_*`.

    A new instance is cheap; `meta` constructs one per iteration so any
    config drift (model, sanitizer mode) takes effect on the next run."""

    def __init__(
        self,
        chat: ChatLike,
        founding_knowledge_paths: Iterable[Path],
        *,
        model: str | None = None,
        min_pool_size: int = 3,
        max_pool_size: int = 64,
    ) -> None:
        self.chat = chat
        self.founding_knowledge_paths = tuple(founding_knowledge_paths)
        self.model = model
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size

    def mint_clusters(
        self,
        pool: list[Occurrence],
        *,
        lang: str,
        task_shape: str,
    ) -> MintingResult:
        """Try to mint clusters from one scope's unassigned pool.

        Returns an empty `MintingResult` if the pool is too small —
        below `min_pool_size`, the judge isn't called at all. Pools
        larger than `max_pool_size` are truncated (the judge sees a
        prefix, not a sample; the rest stays in the pool for next time).
        That keeps prompt size bounded; the cost is one extra iteration
        for very-large pools, which is fine because Observer is
        evidence-triggered (design §3.2)."""
        if len(pool) < self.min_pool_size:
            return MintingResult(
                new_clusters=[],
                consumed=[],
                raw_output=JudgeOutput(pool_too_small=True),
            )
        windowed = pool[: self.max_pool_size]
        messages = build_messages(
            windowed,
            lang=lang,
            task_shape=task_shape,
            founding_knowledge_paths=self.founding_knowledge_paths,
        )
        response: ChatResponse = self.chat.chat(
            messages,
            model=self.model,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        output = parse_response(response.content)
        return self._materialize(output, windowed, lang=lang, task_shape=task_shape)

    def _materialize(
        self,
        output: JudgeOutput,
        pool: list[Occurrence],
        *,
        lang: str,
        task_shape: str,
    ) -> MintingResult:
        """Turn JudgeOutput into real Cluster + Occurrence pairs. Validates
        that signal_indices stay in bounds and don't overlap across
        proposed clusters — overlap would corrupt cohort accounting."""
        new_clusters: list[Cluster] = []
        consumed: list[Occurrence] = []
        claimed: set[int] = set()
        for proposal in output.clusters:
            indices = [i for i in proposal.signal_indices if 0 <= i < len(pool)]
            # Enforce non-overlap. The judge's instructions ask for it;
            # silently dropping a duplicate-claim is safer than aborting
            # on a marginal LLM mistake.
            indices = [i for i in indices if i not in claimed]
            if not indices:
                continue
            claimed.update(indices)
            cluster = Cluster(
                cluster_id=new_cluster_id(),
                label=proposal.label.strip(),
                discriminator=proposal.discriminator.strip(),
                lang=lang,
                task_shape=task_shape,
                baseline_covers=proposal.baseline_covers,
            )
            new_clusters.append(cluster)
            for i in indices:
                consumed.append(pool[i])
        return MintingResult(
            new_clusters=new_clusters,
            consumed=consumed,
            raw_output=output,
        )
