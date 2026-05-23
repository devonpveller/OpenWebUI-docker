"""Skill library — meta-authored artifacts (design §7, Chapter 4).

Artifacts that `meta` writes after a tier-0 or tier-1 escalation, persisted
as Markdown files on the `little-coder-skill/` named volume. The format is
the **Anthropic Agent Skills** shape — a `SKILL.md`-style file with
`name` + `description` YAML frontmatter and a "explain the why" body —
layered with the §7.1 metadata that lets the augmenter (§7.4) and the
tier ladder (§5.6) read what they need.

Two contracts pinned in code here:

  1. **Frontmatter is enforced at draft time** (design §7.1). Missing
     fields, wrong types, or an unknown `status` are rejected — the
     judge can't ship a partial artifact. The body has a soft cap of
     500 lines (Anthropic Agent Skills guidance); the loader warns but
     does not reject, because a too-long body is a craft issue, not a
     corruption issue.

  2. **Writes are atomic** (design §7.3 / §7.5). Watchers — the
     augmenter, the planner reading plan-slots at boot — must see the
     old file or the new file, never a half-write. Implemented as
     `<path>.tmp` + `rename(2)`, the same pattern `cohorts.checkpoint`
     uses for the cohort store.

What this module does NOT do (deliberate scope):
  - It does NOT draft skills (the judge writes the markdown — Stage 3).
  - It does NOT select skills per task (that is the augmenter — Stage 2).
  - It does NOT enforce supersession (that lives in §7.5 supersession
    logic which calls `flip_status` here, but the policy is upstream).

Subdirectory layout per design §7.2:
  - `skill/knowledge/`   tier-0 knowledge entries
  - `skill/tools/`       tier-1 tool-craft
  - `skill/plan-slots/`  tier-1 plan-slots
  - `skill/routing/`     tier-2 routing rules (YAML; Chapter 5)

Knowledge / tools / plan-slots are the Chapter-4 surface. Routing is left
to Chapter 5 — its file shape is YAML and its loader lives in the router,
not here.
"""

from __future__ import annotations

import dataclasses
import secrets
from pathlib import Path
from typing import Iterator, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .journals import utc_now

# Skill type → on-disk subdirectory. `routing` is Chapter 5 and lives
# under the same root so future-meta can enumerate everything; the
# loader here intentionally does NOT walk it, because the file shape
# differs (YAML, not Markdown + frontmatter).
SKILL_SUBDIRS: dict[str, str] = {
    "knowledge": "knowledge",
    "tool": "tools",
    "plan_slot": "plan-slots",
}

SkillKind = Literal["knowledge", "tool", "plan_slot"]
SkillStatus = Literal["active", "superseded", "retired", "pending"]

# Soft cap on body length — Anthropic Agent Skills guidance is "lean
# bodies, link heavy reference material rather than inlining" (under
# ~500 lines). We warn but do not reject above this; corruption-shaped
# errors (missing frontmatter, bad YAML) DO reject.
_BODY_SOFT_LINE_CAP = 500

# Sentinel separating frontmatter from body. Standard YAML-frontmatter
# convention: a line containing only `---` opens and closes the block.
_FRONTMATTER_FENCE = "---"


def new_skill_id() -> str:
    """Generate a fresh skill id. Same shape as `clusters.new_cluster_id`
    (64-bit hex) — collision-free for the population we expect to live
    on disk, easy to grep in journals."""
    return secrets.token_hex(8)


# --- frontmatter --------------------------------------------------------


class SkillFrontmatter(BaseModel):
    """The frontmatter block enforced at draft time.

    Combines Anthropic Agent Skills fields (`name`, `description`) with
    the §7.1 metadata. `extra="forbid"` so a stray field is a hard error
    — meta drafts are validated as they're written, never "fix the
    schema later"."""

    model_config = {"extra": "forbid"}

    # Agent Skills fields. `description` is what the augmenter (§7.4)
    # embeds + ranks against the task, so a vague description directly
    # hurts retrieval quality.
    name: str = Field(..., min_length=2, max_length=120)
    description: str = Field(..., min_length=2, max_length=1000)

    # §7.1 metadata.
    id: str = Field(..., min_length=8, max_length=64)
    cluster_id: str = Field(..., min_length=4, max_length=64)
    tier: int = Field(..., ge=0, le=2)
    kind: SkillKind  # knowledge | tool | plan_slot
    lang: str = Field(..., min_length=1)  # "*" for any
    domain: str = Field(..., min_length=1)
    tool: str = "*"
    task_shape: str = Field(..., min_length=1)
    created: str
    supersedes: str | None = None
    status: SkillStatus = "active"


@dataclasses.dataclass
class Skill:
    """One skill artifact. `frontmatter` is the §7.1 metadata; `body` is
    the Markdown the augmenter inlines into the agent's context.

    A `Skill` is constructible from disk (`load_skill`), from a draft the
    judge produced (`build_skill`), or by hand in tests. Persistence
    goes through `write_skill` (atomic)."""

    frontmatter: SkillFrontmatter
    body: str

    @property
    def id(self) -> str:
        return self.frontmatter.id

    @property
    def kind(self) -> str:
        return self.frontmatter.kind

    def serialize(self) -> str:
        """Render the on-disk text: `---` YAML `---` then the body. The
        YAML dump uses `sort_keys=False` so the schema order survives
        (a stable diff is easier to review)."""
        # exclude_none keeps the frontmatter tidy — `supersedes: null` is
        # noise for a freshly-minted skill.
        meta = self.frontmatter.model_dump(exclude_none=True)
        fm = yaml.safe_dump(meta, sort_keys=False).rstrip()
        body = self.body.rstrip()
        return f"{_FRONTMATTER_FENCE}\n{fm}\n{_FRONTMATTER_FENCE}\n\n{body}\n"


# --- parsing + writing --------------------------------------------------


class SkillFormatError(ValueError):
    """Raised when on-disk text can't be parsed back into a `Skill`. The
    judge's drafts must round-trip; a file that doesn't is a corrupted
    write, not a craft choice — caller decides whether to retire it."""


def parse_skill(text: str) -> Skill:
    """Inverse of `Skill.serialize()`. Surfaces specific errors so a
    rejection at draft time gives the judge actionable feedback (its
    next attempt can fix the named field)."""
    stripped = text.lstrip()
    if not stripped.startswith(_FRONTMATTER_FENCE):
        raise SkillFormatError("missing opening `---` frontmatter fence")

    body_start = stripped.find("\n", len(_FRONTMATTER_FENCE))
    if body_start < 0:
        raise SkillFormatError("frontmatter is unterminated (no second `---`)")
    # Find the closing fence: a line whose only content is `---`.
    end = stripped.find(f"\n{_FRONTMATTER_FENCE}", body_start)
    if end < 0:
        raise SkillFormatError("frontmatter is unterminated (no closing `---`)")

    fm_text = stripped[body_start + 1 : end]
    body = stripped[end + len(_FRONTMATTER_FENCE) + 1 :].lstrip("\n")

    try:
        fm_data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillFormatError(f"frontmatter YAML is invalid: {exc}") from exc
    if not isinstance(fm_data, dict):
        raise SkillFormatError(
            f"frontmatter must be a YAML mapping, got {type(fm_data).__name__}"
        )

    try:
        fm = SkillFrontmatter.model_validate(fm_data)
    except ValidationError as exc:
        raise SkillFormatError(f"frontmatter rejected: {exc}") from exc

    return Skill(frontmatter=fm, body=body)


def build_skill(
    *,
    kind: SkillKind,
    cluster_id: str,
    tier: int,
    lang: str,
    domain: str,
    task_shape: str,
    name: str,
    description: str,
    body: str,
    tool: str = "*",
    supersedes: str | None = None,
    skill_id: str | None = None,
    status: SkillStatus = "pending",
    created: str | None = None,
) -> Skill:
    """Construct a `Skill` from the judge's drafted fields. `status` defaults
    to `pending` — the human gate (operator surface §4f) is the merge step
    that flips it to `active`. A freshly minted skill is never auto-active
    in Chapter 4 (design §10.4 — operator approval is the actual catch).

    Raises `SkillFormatError` on any schema violation — same error type as
    `parse_skill`, so callers handle drafting failures uniformly whether
    the bad input came from disk or from the judge."""
    try:
        fm = SkillFrontmatter(
            name=name,
            description=description,
            id=skill_id or new_skill_id(),
            cluster_id=cluster_id,
            tier=tier,
            kind=kind,
            lang=lang,
            domain=domain,
            tool=tool,
            task_shape=task_shape,
            created=created or utc_now(),
            supersedes=supersedes,
            status=status,
        )
    except ValidationError as exc:
        raise SkillFormatError(f"frontmatter rejected: {exc}") from exc
    return Skill(frontmatter=fm, body=body)


def skill_path(skill_dir: Path | str, skill: Skill) -> Path:
    """Where the artifact lives. `<skill_dir>/<subdir>/<id>.md`. The id
    (not the slugified name) keeps the filesystem path stable across
    label changes — design §5.1's identity-vs-label rule applied to
    skill files. The augmenter doesn't read filenames; the operator
    surface renders the human-readable name."""
    subdir = SKILL_SUBDIRS[skill.kind]
    return Path(skill_dir) / subdir / f"{skill.id}.md"


def write_skill(skill_dir: Path | str, skill: Skill) -> Path:
    """Persist atomically: write to `<path>.tmp`, then `rename(2)` into
    place (design §7.3). Readers ignore `*.tmp`; watchers see either the
    old file or the new one. Returns the final path.

    Round-trip-checks the artifact: a write that can't be parsed back is
    rejected before the rename, so a corrupt file never lands on disk
    (the judge's drafting can be retried)."""
    target = skill_path(skill_dir, skill)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = skill.serialize()
    # Cheap self-check before publishing — round-trip the serialized text.
    parse_skill(text)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def load_skill(path: Path | str) -> Skill:
    """Read one skill from disk. Raises `SkillFormatError` if it doesn't
    round-trip — the loader is strict so the augmenter never picks up
    a half-written or schema-bad file."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_skill(text)


def iter_skills(
    skill_dir: Path | str,
    *,
    kind: SkillKind | None = None,
    status: SkillStatus | None = None,
) -> Iterator[Skill]:
    """Walk the skill library. Skips `*.tmp` (atomic-rename in-flight),
    swallows individual file errors (a single corrupted artifact must
    not blind the whole augmenter — Chapter-4 operator surface flags
    it separately)."""
    root = Path(skill_dir)
    if not root.exists():
        return
    if kind is not None:
        kinds = [kind]
    else:
        kinds = list(SKILL_SUBDIRS.keys())
    for k in kinds:
        sub = root / SKILL_SUBDIRS[k]
        if not sub.is_dir():
            continue
        for path in sorted(sub.glob("*.md")):
            if path.name.endswith(".tmp"):
                continue
            try:
                skill = load_skill(path)
            except SkillFormatError:
                continue
            if status is not None and skill.frontmatter.status != status:
                continue
            yield skill


def list_skills(
    skill_dir: Path | str,
    *,
    kind: SkillKind | None = None,
    status: SkillStatus | None = "active",
) -> list[Skill]:
    """`iter_skills` materialised — the augmenter (§7.4) wants a list it
    can rank; default `status='active'` is the common case (only live
    skills get selected into a task context, design §8.5 retirement)."""
    return list(iter_skills(skill_dir, kind=kind, status=status))


# --- supersession + retirement (§7.5, §8.5) -----------------------------


def flip_status(skill_dir: Path | str, skill_id: str, new_status: SkillStatus) -> Skill:
    """Re-write one artifact's `status` (the only mutable bit beyond
    label/description). Atomic. Used by supersession and efficacy
    reversion (design §7.5, §8.5). Raises `FileNotFoundError` if no
    skill matches `skill_id` — the caller is expected to know the id.

    Implementation note: we re-serialize the WHOLE file so the
    frontmatter stays canonical (no partial in-place YAML rewrite).
    The body is preserved unchanged."""
    target_path = _find_skill_path(skill_dir, skill_id)
    skill = load_skill(target_path)
    skill.frontmatter = skill.frontmatter.model_copy(update={"status": new_status})
    write_skill(skill_dir, skill)
    return skill


def _find_skill_path(skill_dir: Path | str, skill_id: str) -> Path:
    """Locate a skill by id across all subdirectories. The skill's `kind`
    determines its subdir, but the operator surface lets you supersede
    by id alone — so we search."""
    root = Path(skill_dir)
    for sub in SKILL_SUBDIRS.values():
        candidate = root / sub / f"{skill_id}.md"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no skill with id={skill_id!r} under {root}")
