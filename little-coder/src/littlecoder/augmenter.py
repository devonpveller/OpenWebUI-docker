"""Per-task skill selection (design §7.4, Chapter 4).

The augmenter picks which active skill artifacts get inlined into the
agent's system prompt for a single task. Three stages, in order:

  1. **Hard tag filter** — keep only skills whose `lang`, `domain`, and
     `task_shape` match the task (or the skill carries `"*"` wildcard
     for that field). Cross-scope matching is never permitted — design
     §5.5 / §7.4 step 1.
  2. **Embedding rank** — score the survivors by similarity between the
     task prompt and the skill's `description` field (the description
     is what the judge wrote FOR augmenter retrieval; ranking against
     the body would weight long-bodied skills unfairly).
  3. **Hard token budget** — greedily add highest-scored skills until
     the budget is exceeded. Over-budget tiebreakers (design §7.4 step 3):
       a) cohort-proven wins (the artifact has post-merge evidence it
          helped — §8.5),
       b) tighter match wins (higher similarity score),
       c) **tier is NOT a tiebreaker on its own** — a well-matched
          tier-0 entry beats a loosely-matched tier-1 entry. Tier
          governs production discipline, not runtime selection.

Per-task selection is RETURNED in a `SkillSelection` dataclass that
includes rejections + reasons. The §8.4 in-context assertion reads this
to decide whether a validation gate measured anything meaningful — a
skill that wasn't selected into context can't have helped the task.

The augmenter is PURE — the similarity function and the cohort-proven
oracle are both injected. Tests use stubs; production wires the real
embedding-based similarity + the cohort store's efficacy flag (Stage 4).
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from .skills import Skill

# Rough char→token estimate. Real tokenizers vary (4 chars/token for
# English code is a reasonable default; CJK is denser, identifiers can
# split unevenly). Off-by-a-bit is acceptable because the budget is a
# soft fence — we never count exactly. A more accurate tokenizer would
# require a model-specific dep and the marginal accuracy doesn't change
# the selection outcome above the noise floor.
_CHARS_PER_TOKEN = 4
# Per-skill overhead: the wrapping ("## <name>\n\n<body>") + separation.
_OVERHEAD_TOKENS_PER_SKILL = 20


def estimate_tokens(text: str) -> int:
    """Cheap, conservative token estimate. Rounds up — over-counting is
    safer than under-counting because over-counting only costs us a
    slightly-smaller context, while under-counting can blow the model's
    actual context window."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def skill_token_cost(skill: Skill) -> int:
    """Estimated tokens this skill will spend in the system prompt."""
    return estimate_tokens(skill.body) + _OVERHEAD_TOKENS_PER_SKILL


# --- request + result ---------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TaskRequest:
    """What the augmenter sees about the current task. Derived by the
    daemon at trigger time from the trigger envelope + workspace state.

    `prompt` is the actual user prompt — it's what gets embedded for the
    rank step. `tool` is the early-tool-call hint (e.g. `"pytest"` if
    the agent's first action is to run tests); `None` is fine and just
    skips tool-axis filtering."""

    lang: str
    domain: str
    task_shape: str
    prompt: str
    tool: str | None = None


@dataclasses.dataclass(frozen=True)
class RejectedSkill:
    """A skill that DIDN'T make it into the final selection. The reason
    is consumed by the §8.4 in-context assertion (an artifact under
    validation that landed here means the gate measured nothing)."""

    skill_id: str
    reason: str
    score: float | None = None


@dataclasses.dataclass(frozen=True)
class SkillSelection:
    """The augmenter's verdict for one task.

    `selected` is the ordered list of skills to inline (highest-ranked
    first). `rejected` is everything else — filter rejections + budget
    rejections — with a reason each, for audit and the §8.4 assertion.
    `total_tokens` and `budget` are the budget accounting."""

    selected: list[Skill]
    rejected: list[RejectedSkill]
    total_tokens: int
    budget: int

    @property
    def selected_ids(self) -> list[str]:
        return [s.id for s in self.selected]

    def includes(self, skill_id: str) -> bool:
        """The §8.4 query: was this skill in-context for the task?"""
        return any(s.id == skill_id for s in self.selected)


# Injected oracles. The similarity function takes two strings (query +
# anchor) and returns a score in [0, 1]; the cohort-proven oracle is
# `bool(skill_id)`. Both are pure callables so the augmenter stays
# trivially mockable.
Similarity = Callable[[str, str], float]
CohortProven = Callable[[str], bool]


# --- the selection pipeline --------------------------------------------


def _scope_match(skill_value: str, task_value: str) -> bool:
    """Per-axis match: skill's value is either "*" (matches anything) or
    equal to the task's value. Case-insensitive on the common axes
    because we've seen `Rust` / `rust` drift in journals."""
    if skill_value == "*" or task_value == "*":
        return True
    return skill_value.lower() == task_value.lower()


def _filter_by_tags(library: list[Skill], request: TaskRequest) -> tuple[
    list[Skill], list[RejectedSkill]
]:
    """§7.4 step 1 — hard filter by `lang`, `domain`, `task_shape`, and
    (when set) `tool`. Returns (survivors, rejections)."""
    survivors: list[Skill] = []
    rejected: list[RejectedSkill] = []
    for skill in library:
        fm = skill.frontmatter
        if not _scope_match(fm.lang, request.lang):
            rejected.append(
                RejectedSkill(skill.id, f"lang mismatch: skill={fm.lang!r} task={request.lang!r}")
            )
            continue
        if not _scope_match(fm.domain, request.domain):
            rejected.append(
                RejectedSkill(skill.id, f"domain mismatch: skill={fm.domain!r} task={request.domain!r}")
            )
            continue
        if not _scope_match(fm.task_shape, request.task_shape):
            rejected.append(
                RejectedSkill(
                    skill.id,
                    f"task_shape mismatch: skill={fm.task_shape!r} task={request.task_shape!r}",
                )
            )
            continue
        if (
            request.tool is not None
            and fm.tool != "*"
            and fm.tool.lower() != request.tool.lower()
        ):
            rejected.append(
                RejectedSkill(skill.id, f"tool mismatch: skill={fm.tool!r} task={request.tool!r}")
            )
            continue
        survivors.append(skill)
    return survivors, rejected


def _rank_by_similarity(
    survivors: list[Skill],
    request: TaskRequest,
    similarity: Similarity,
) -> list[tuple[Skill, float]]:
    """§7.4 step 2 — score each survivor by similarity(prompt, description)
    and sort descending. Stable sort over the original order on ties so
    cohort_proven doesn't get its priority pre-empted by chance ordering."""
    scored = [
        (skill, similarity(request.prompt, skill.frontmatter.description))
        for skill in survivors
    ]
    # Python's `sorted` is stable — equal-key items keep their relative
    # input order. We want highest-score first; same-score order is
    # broken later by the tiebreaker.
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _fit_to_budget(
    scored: list[tuple[Skill, float]],
    *,
    budget: int,
    is_cohort_proven: CohortProven,
) -> tuple[list[Skill], list[RejectedSkill], int]:
    """§7.4 step 3 — greedy fit with the budget. The input is already
    sorted by similarity; we re-sort to apply the tiebreaker, then walk
    in order until the budget is exhausted.

    Tiebreaker (design §7.4 step 3): for two equal-similarity skills,
    cohort-proven wins. Tier is NOT a tiebreaker.

    Returns (selected, rejected, tokens_consumed)."""

    # Stable re-sort with the tiebreaker: keep score descending, then
    # cohort-proven first within ties. We can't use `sorted` with a
    # single key that reverses on score but not on the bool, so use a
    # composite key: (-score, not cohort_proven).
    def sort_key(pair: tuple[Skill, float]) -> tuple[float, int]:
        skill, score = pair
        return (-score, 0 if is_cohort_proven(skill.id) else 1)

    ordered = sorted(scored, key=sort_key)

    selected: list[Skill] = []
    rejected: list[RejectedSkill] = []
    spent = 0
    for skill, score in ordered:
        cost = skill_token_cost(skill)
        if spent + cost > budget:
            rejected.append(
                RejectedSkill(
                    skill.id,
                    f"over budget: would spend {spent + cost} of {budget} tokens",
                    score=score,
                )
            )
            continue
        selected.append(skill)
        spent += cost
    return selected, rejected, spent


def select(
    library: list[Skill],
    request: TaskRequest,
    similarity: Similarity,
    *,
    token_budget: int = 4000,
    is_cohort_proven: CohortProven | None = None,
) -> SkillSelection:
    """Run the §7.4 selection pipeline once for one task.

    Inputs:
      - `library`: typically `list_skills(skill_dir, status="active")`
        (design §8.5 — only active skills are eligible).
      - `request`: the task envelope.
      - `similarity`: how to score (skill description, task prompt).
        Production wires `similarity.EmbeddingSimilarity`'s call-form;
        tests pass a stub.
      - `token_budget`: hard cap on inlined-skill tokens.
      - `is_cohort_proven`: oracle for the tiebreaker. Defaults to
        "no skill is proven yet" (the chapter-4-pre-Stage-4 reality).

    Output: `SkillSelection` with selected + rejected + accounting."""
    if is_cohort_proven is None:
        is_cohort_proven = lambda _id: False

    survivors, tag_rejections = _filter_by_tags(library, request)
    scored = _rank_by_similarity(survivors, request, similarity)
    selected, budget_rejections, spent = _fit_to_budget(
        scored, budget=token_budget, is_cohort_proven=is_cohort_proven
    )
    return SkillSelection(
        selected=selected,
        rejected=tag_rejections + budget_rejections,
        total_tokens=spent,
        budget=token_budget,
    )
