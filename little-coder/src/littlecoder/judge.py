"""The judge — LLM that mints clusters AND drafts skill artifacts (design
§5.2, §5.6, §10.1, Chapters 3 + 4).

The judge has two responsibilities that share a prompt-engineering shape
but write to different outputs:

  - **Cluster minting (Chapter 3 §3e)** — `mint_clusters` looks at the
    unassigned pool for a (lang, task_shape) scope and decides whether
    the pool itself forms a coherent group worth minting as a new
    cluster. Emits zero or more `ClusterProposal`s.
  - **Tier-0 drafting (Chapter 4 §4e)** — `draft_tier_0_skill` writes
    a knowledge entry for one already-established cluster. Emits a
    drafted skill (Agent Skills format) that `meta` materializes via
    `skills.build_skill` + `write_skill` with `status="pending"`. The
    operator approval surface (§4f) is what flips it to `active`.

Cluster minting:

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
from .cohorts import ClusterCounters
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


# --- tier-0 drafting (Chapter 4 §4e) ------------------------------------


_DRAFT_TIER_0_SYSTEM_PROMPT = """\
You are the META judge in DRAFTING mode for a self-improving coding
agent. Your job here is NOT to cluster signals — those are already
clustered. Your job is to draft ONE knowledge entry (tier-0) that helps
the agent avoid this specific craft gap on future tasks.

The entry will be inlined into the agent's system prompt by an augmenter
that retrieves it via tag-filter + embedding-rank on the `description`
field — so the description must be a precise when-to-use / what-it-does
sentence.

The body follows the **Anthropic Agent Skills** authoring conventions:

  - LEAN. Under 500 lines; link heavy reference rather than inlining it.
  - PROGRESSIVE disclosure — the most-important point first.
  - "EXPLAIN THE WHY" — state the failure mode the agent kept hitting,
    then the rule that addresses it, then a one-paragraph rationale.
  - SPECIFIC to this cluster — generic advice the founding knowledge
    already gave is NOISE. (You have founding knowledge in context;
    don't restate it.)
  - INSTRUCTIONS in the imperative voice, addressed to the agent.

You also receive the cluster's CURRENT CONTEXT:
  - `cluster_id`, `label`, `discriminator` (what the cluster was minted
    on, design §5.1).
  - `lang`, `task_shape`, `observed_count`, top repos by occurrence.
  - A sample of `signal_text` values — the actual error messages the
    agent emitted across N occurrences in this cluster.

The cluster IS NOT baseline-covered (the operator already checked) — so
you can assume the founding knowledge does NOT address this. If reading
the signals you realise the baseline DOES cover it, set
`baseline_covers: true` in the response and DON'T draft; that lets the
caller reroute to tier-1 enforcement.

OUTPUT FORMAT — a single JSON object:

{
  "name": "human-readable skill name (≤ 120 chars)",
  "description": "WHEN to retrieve this + WHAT it does. The augmenter \
embeds this; be precise. (≤ 1000 chars)",
  "body": "the markdown body (no frontmatter — the runtime adds it). \
explain-the-why structure. Under 500 lines.",
  "reasoning": "one paragraph: which signals fed this draft and why \
this rule addresses the cluster",
  "baseline_covers": false
}

Return ONLY the JSON. No prose before or after."""


class DraftOutput(BaseModel):
    """The structured output of `Judge.draft_tier_0_skill`.

    `baseline_covers` is here so a judge that re-discovers in-drafting
    that the baseline actually does cover the cluster (despite the
    minting-time `false`) can escape and signal "don't ship this as
    tier-0; reroute to tier-1". The Chapter-4 `meta` integration reads
    this flag and skips drafting when true."""

    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., min_length=2, max_length=1000)
    body: str = Field(..., min_length=10)
    reasoning: str = Field(default="", max_length=4000)
    baseline_covers: bool = Field(default=False)


@dataclasses.dataclass
class DraftResult:
    """Return shape of `draft_tier_0_skill`. `output` is the parsed
    judge response; `escaped_to_compliance` is True when the judge
    second-guessed the baseline-covers flag and refused to draft."""

    output: DraftOutput | None
    escaped_to_compliance: bool


def _draft_user_message(
    cluster: Cluster,
    counter: ClusterCounters | None,
    sample_signals: list[str],
) -> str:
    observed = counter.observed if counter else 0
    top_repos: list[tuple[str, int]] = (
        sorted(counter.per_repo_observed.items(), key=lambda kv: kv[1], reverse=True)[:3]
        if counter
        else []
    )
    signals_block = "\n".join(f"- {s}" for s in sample_signals)
    repos_block = (
        ", ".join(f"{r}={n}" for r, n in top_repos) if top_repos else "(none)"
    )
    return (
        f"CLUSTER\n"
        f"  id:            {cluster.cluster_id}\n"
        f"  label:         {cluster.label}\n"
        f"  discriminator: {cluster.discriminator}\n"
        f"  lang:          {cluster.lang}\n"
        f"  task_shape:    {cluster.task_shape}\n"
        f"  observed:      {observed}\n"
        f"  top repos:     {repos_block}\n"
        f"  baseline_covers (minting): {cluster.baseline_covers}\n\n"
        f"SIGNAL SAMPLE ({len(sample_signals)})\n{signals_block}\n"
    )


def parse_draft_response(content: str) -> DraftOutput:
    """Inverse of the JSON object above. Same strictness as
    `parse_response` for minting — non-compliance is a hard fail."""
    stripped = _strip_code_fence(content)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LlmError(f"draft reply was not JSON: {exc.msg}") from exc
    try:
        return DraftOutput.model_validate(data)
    except ValidationError as exc:
        raise LlmError(f"draft reply did not match schema: {exc}") from exc


# Patch Judge with the tier-0 drafting method. Done as a function so the
# minting-only judge stays the principal class definition above and the
# Chapter-4 extension is visually separated.


def _draft_tier_0_skill(
    self: "Judge",
    cluster: Cluster,
    counter: ClusterCounters | None,
    sample_signals: list[str],
    *,
    max_signals: int = 16,
) -> DraftResult:
    """Draft one tier-0 knowledge entry for an established cluster.

    Caller is expected to have already called `tier_ladder.evaluate_tier_0`
    and confirmed eligibility — this method does not re-check; it drafts
    unconditionally except for the in-prompt second-look at
    baseline-covers (if the judge sees that founding knowledge already
    covers the signals, it sets `baseline_covers: true` and we DON'T
    materialize a skill, surfacing it as `escaped_to_compliance`).

    The sample of signals is windowed at `max_signals` to bound prompt
    size — same approach as `mint_clusters.max_pool_size`. The choice
    of which signals to include is the caller's; passing the most-recent
    is a reasonable default."""
    windowed = sample_signals[:max_signals]
    messages = [
        ChatMessage(role="system", content=_DRAFT_TIER_0_SYSTEM_PROMPT),
        ChatMessage(role="system", content=_founding_knowledge_block(self.founding_knowledge_paths)),
        ChatMessage(
            role="user", content=_draft_user_message(cluster, counter, windowed)
        ),
    ]
    response: ChatResponse = self.chat.chat(
        messages,
        model=self.model,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    output = parse_draft_response(response.content)
    if output.baseline_covers:
        # Judge second-guessed itself: the founding knowledge actually
        # covers this. Don't ship a tier-0 entry — let meta retry as
        # tier-1 enforcement (Stage 4+).
        return DraftResult(output=None, escaped_to_compliance=True)
    return DraftResult(output=output, escaped_to_compliance=False)


Judge.draft_tier_0_skill = _draft_tier_0_skill  # type: ignore[attr-defined]


# --- tier-1 drafting (Chapter 4 §4e + §5.7 type selection) -------------


_DRAFT_TIER_1_SYSTEM_PROMPT = """\
You are the META judge in TIER-1 DRAFTING mode for a self-improving
coding agent. A tier-0 knowledge entry has been live for this cluster
and the cluster is STILL recurring — OR the cluster is baseline-
covered, and the agent isn't following the founding-knowledge
instruction it already has. Either way, INSTRUCTION isn't enough: the
gap needs ENFORCEMENT.

You must pick ONE of two intervention types (design §5.7) and argue
both sides before choosing:

  - **tool_craft** — a skill that shapes HOW the agent calls a tool
    in this domain. Example: "always pass `--no-pager` to git in
    headless contexts" or "use `cargo fmt --check` before declaring
    a refactor complete". Use this when the gap is in the agent's
    tool-call shape (wrong flags, wrong order, wrong command choice).

  - **plan_slot** — a slot the agent's planner loads at boot, that
    inserts a fixed plan step. Example: "before any refactor in a
    repo with tests, run the test suite to capture the baseline".
    Use this when the gap is in the plan's STRUCTURE (a missing
    step the agent keeps skipping).

The plan §5.7 rule: argue BOTH for tool_craft and BOTH for plan_slot,
then pick. The argument is the journal entry — operators read it on the
pending-artifacts surface.

You also receive founding knowledge in context. If reading the signals
you realise the agent IS following the baseline and the cluster is
something different than you thought, set `baseline_covers: true` and
DON'T draft — the caller will skip materialization (same escape as
tier-0 drafting).

OUTPUT FORMAT — JSON:

{
  "kind": "tool_craft" | "plan_slot",
  "name": "human-readable skill name (≤ 120 chars)",
  "description": "WHEN to retrieve + WHAT it enforces (≤ 1000 chars)",
  "body": "markdown body — no frontmatter; ≤ 500 lines",
  "argument_for_tool_craft": "why this could be tool_craft",
  "argument_for_plan_slot": "why this could be plan_slot",
  "argument_for_pick": "why the chosen kind beats the other",
  "baseline_covers": false
}

Return ONLY the JSON."""


class Tier1DraftOutput(BaseModel):
    """Structured response for tier-1 drafting. `kind` constrains the
    judge to one of the two §5.7 intervention types; the three argument
    fields are required so the operator surface can render the §5.7
    "argue both then pick" trail."""

    kind: str = Field(...)  # "tool_craft" | "plan_slot"
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., min_length=2, max_length=1000)
    body: str = Field(..., min_length=10)
    argument_for_tool_craft: str = Field(default="", max_length=4000)
    argument_for_plan_slot: str = Field(default="", max_length=4000)
    argument_for_pick: str = Field(default="", max_length=4000)
    baseline_covers: bool = Field(default=False)


@dataclasses.dataclass
class Tier1DraftResult:
    output: Tier1DraftOutput | None
    escaped_to_compliance: bool


def parse_tier_1_response(content: str) -> Tier1DraftOutput:
    stripped = _strip_code_fence(content)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise LlmError(f"tier-1 reply was not JSON: {exc.msg}") from exc
    try:
        out = Tier1DraftOutput.model_validate(data)
    except ValidationError as exc:
        raise LlmError(f"tier-1 reply did not match schema: {exc}") from exc
    if out.kind not in ("tool_craft", "plan_slot"):
        raise LlmError(
            f"tier-1 reply.kind must be tool_craft|plan_slot, got {out.kind!r}"
        )
    return out


def _draft_tier_1_skill(
    self: "Judge",
    cluster: Cluster,
    counter: ClusterCounters | None,
    sample_signals: list[str],
    *,
    max_signals: int = 16,
) -> Tier1DraftResult:
    """Draft one tier-1 intervention. Same shape as tier-0 drafting —
    counterfactual+adversarial framing, founding-knowledge in context,
    judge can refuse (escape to compliance). Output picks tool_craft
    vs plan_slot per design §5.7."""
    windowed = sample_signals[:max_signals]
    messages = [
        ChatMessage(role="system", content=_DRAFT_TIER_1_SYSTEM_PROMPT),
        ChatMessage(role="system", content=_founding_knowledge_block(self.founding_knowledge_paths)),
        ChatMessage(
            role="user", content=_draft_user_message(cluster, counter, windowed)
        ),
    ]
    response: ChatResponse = self.chat.chat(
        messages,
        model=self.model,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    output = parse_tier_1_response(response.content)
    if output.baseline_covers:
        return Tier1DraftResult(output=None, escaped_to_compliance=True)
    return Tier1DraftResult(output=output, escaped_to_compliance=False)


Judge.draft_tier_1_skill = _draft_tier_1_skill  # type: ignore[attr-defined]
