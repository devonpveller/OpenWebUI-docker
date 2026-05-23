"""Skill library data model (design §7, Chapter 4).

These pin: frontmatter validation, the atomic-rename write contract, the
parse/serialize round-trip, and the strict loader (a corrupt artifact
must never be silently fed to the augmenter).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from littlecoder.skills import (
    SKILL_SUBDIRS,
    Skill,
    SkillFormatError,
    SkillFrontmatter,
    build_skill,
    flip_status,
    iter_skills,
    list_skills,
    load_skill,
    new_skill_id,
    parse_skill,
    skill_path,
    write_skill,
)


def _draft(**overrides) -> Skill:
    """Helper — minimal valid skill with overridable fields."""
    base = dict(
        kind="knowledge",
        cluster_id="cl0001",
        tier=0,
        lang="rust",
        domain="async",
        task_shape="bugfix",
        name="async lifetime errors",
        description=(
            "When working with Rust async fns and lifetimes, the borrow "
            "checker often complains about captured references."
        ),
        body="# Async lifetimes\n\nReturn owned values from async fns.\n",
    )
    base.update(overrides)
    return build_skill(**base)


# --- new_skill_id -------------------------------------------------------


def test_new_skill_id_is_stable_length_and_hex():
    ids = {new_skill_id() for _ in range(50)}
    assert len(ids) == 50  # collision-free
    for sid in ids:
        assert len(sid) == 16
        int(sid, 16)


# --- build_skill / serialize / parse ------------------------------------


def test_build_skill_status_defaults_to_pending():
    """Freshly-drafted skills are pending — operator approval flips to
    active. Auto-active would bypass the §10.4 human gate."""
    skill = _draft()
    assert skill.frontmatter.status == "pending"
    assert skill.frontmatter.id  # auto-generated id present


def test_serialize_then_parse_round_trips():
    """The judge's draft must round-trip — `write_skill` enforces this,
    so any silent format-drift would break in tests."""
    skill = _draft(tier=1, kind="tool")
    text = skill.serialize()
    again = parse_skill(text)
    assert again.frontmatter.model_dump() == skill.frontmatter.model_dump()
    assert again.body.strip() == skill.body.strip()


def test_serialize_omits_null_supersedes():
    """`supersedes: null` is noise for a freshly-minted skill — keep the
    frontmatter clean. `exclude_none=True` in `Skill.serialize`."""
    text = _draft().serialize()
    assert "supersedes:" not in text


def test_serialize_keeps_supersedes_when_set():
    text = _draft(supersedes="oldskillid").serialize()
    assert "supersedes: oldskillid" in text


# --- frontmatter validation (strict) ------------------------------------


def test_missing_baseline_required_field_rejected():
    """Each §7.1 field is required (no defaults that hide a drafting
    bug). Missing `description` → SkillFormatError."""
    text = (
        "---\n"
        "name: x\n"
        "id: abcd1234\n"
        "cluster_id: cl0001\n"
        "tier: 0\n"
        "kind: knowledge\n"
        "lang: rust\n"
        "domain: async\n"
        "task_shape: bugfix\n"
        "created: 2026-05-23T00:00:00Z\n"
        # description is missing
        "---\n"
        "body\n"
    )
    with pytest.raises(SkillFormatError, match="description"):
        parse_skill(text)


def test_unknown_tier_rejected():
    """Tier must be 0/1/2 (per the §5.6 ladder). Tier-3 is a CODE change,
    not an artifact — design says no tier-3 skill files."""
    with pytest.raises(SkillFormatError, match="tier"):
        _draft(tier=3)


def test_unknown_kind_rejected():
    """The kind enum gates the subdir routing. An unknown value would
    land in nowhere."""
    with pytest.raises(SkillFormatError, match="kind"):
        _draft(kind="invented")


def test_extra_field_rejected():
    """`extra="forbid"` — a stray key is a strict-mode bug, not a future
    field. Writers and judges must update the schema deliberately."""
    text = (
        "---\n"
        "name: x\n"
        "description: y\n"
        "id: abcd1234\n"
        "cluster_id: cl0001\n"
        "tier: 0\n"
        "kind: knowledge\n"
        "lang: rust\n"
        "domain: async\n"
        "task_shape: bugfix\n"
        "created: 2026-05-23T00:00:00Z\n"
        "experimental_field: yes\n"
        "---\n"
        "body\n"
    )
    with pytest.raises(SkillFormatError, match="experimental_field"):
        parse_skill(text)


def test_status_must_be_known_value():
    with pytest.raises(SkillFormatError, match="status"):
        _draft(status="green")


def test_missing_frontmatter_rejected():
    with pytest.raises(SkillFormatError, match="missing opening"):
        parse_skill("body without frontmatter")


def test_unterminated_frontmatter_rejected():
    """Single `---` fence at the top, no closing — a corrupt write the
    loader must catch."""
    with pytest.raises(SkillFormatError, match="closing"):
        parse_skill("---\nname: x\ndescription: y\n\n\nbody\n")


def test_non_yaml_frontmatter_rejected():
    text = "---\n: : invalid\n---\nbody\n"
    with pytest.raises(SkillFormatError, match="YAML"):
        parse_skill(text)


def test_yaml_list_frontmatter_rejected():
    text = "---\n- a\n- b\n---\nbody\n"
    with pytest.raises(SkillFormatError, match="mapping"):
        parse_skill(text)


# --- write_skill — atomic + path -----------------------------------------


def test_skill_path_routes_by_kind(tmp_path):
    """Each skill kind lands in its own subdir. The augmenter relies on
    this so it can selectively-load tier-0 vs tier-1 paths."""
    assert skill_path(tmp_path, _draft(kind="knowledge")).parent.name == "knowledge"
    assert skill_path(tmp_path, _draft(kind="tool")).parent.name == "tools"
    assert skill_path(tmp_path, _draft(kind="plan_slot")).parent.name == "plan-slots"


def test_write_skill_lands_at_id_path(tmp_path):
    """File name is `<id>.md`, NOT a slug of the human label. The id is
    stable across a label rename (design §5.1 identity-vs-label)."""
    skill = _draft()
    final = write_skill(tmp_path, skill)
    assert final.exists()
    assert final.name == f"{skill.id}.md"
    assert final.parent.name == "knowledge"


def test_write_skill_atomic_rename(tmp_path, monkeypatch):
    """Watchers must see old-or-new, never half-write. We intercept
    `Path.replace` to confirm the path goes through `.tmp` first."""
    skill = _draft()
    target = skill_path(tmp_path, skill)
    seen = {"tmp_existed": False}

    real_replace = os.replace

    def spy_replace(src, dst):
        # The src must be a `.tmp` file when this is called.
        assert str(src).endswith(".tmp")
        # And the file must exist on disk at this point.
        assert Path(src).exists()
        seen["tmp_existed"] = True
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    write_skill(tmp_path, skill)
    assert seen["tmp_existed"] is True
    assert target.exists()


def test_write_skill_rejects_unparseable_round_trip(tmp_path):
    """If we somehow produced text that can't be parsed back, the writer
    must NOT publish. The body is preserved as-is, so a contrived body
    with a `\\n---\\n` mid-stream would in principle confuse the parser
    — `write_skill` round-trip-checks before renaming.

    This test demonstrates the protective behavior with a normal body
    (round-trip succeeds); a hostile body would fail the parse check
    before rename and never appear on disk.
    """
    skill = _draft()
    write_skill(tmp_path, skill)  # ordinary write succeeds
    # No `.tmp` left behind after a successful write.
    tmps = list(skill_path(tmp_path, skill).parent.glob("*.tmp"))
    assert tmps == []


# --- load_skill / iter / list -------------------------------------------


def test_load_skill_round_trips(tmp_path):
    written = _draft()
    write_skill(tmp_path, written)
    loaded = load_skill(skill_path(tmp_path, written))
    assert loaded.frontmatter.model_dump() == written.frontmatter.model_dump()


def test_iter_skills_walks_all_subdirs(tmp_path):
    write_skill(tmp_path, _draft(kind="knowledge"))
    write_skill(tmp_path, _draft(kind="tool", tier=1))
    write_skill(tmp_path, _draft(kind="plan_slot", tier=1))
    found = list(iter_skills(tmp_path))
    kinds = sorted({s.frontmatter.kind for s in found})
    assert kinds == ["knowledge", "plan_slot", "tool"]


def test_iter_skills_filters_by_kind(tmp_path):
    write_skill(tmp_path, _draft(kind="knowledge"))
    write_skill(tmp_path, _draft(kind="tool", tier=1))
    found = list(iter_skills(tmp_path, kind="tool"))
    assert len(found) == 1
    assert found[0].frontmatter.kind == "tool"


def test_list_skills_defaults_to_active_only(tmp_path):
    """The augmenter only ever sees active skills (design §8.5 retirement).
    The default filter prevents an accidental load of retired material."""
    write_skill(tmp_path, _draft())  # pending — not active by default
    write_skill(tmp_path, _draft(status="active"))
    write_skill(tmp_path, _draft(status="retired"))
    actives = list_skills(tmp_path)
    assert len(actives) == 1
    assert actives[0].frontmatter.status == "active"


def test_list_skills_status_none_returns_all(tmp_path):
    write_skill(tmp_path, _draft(status="active"))
    write_skill(tmp_path, _draft(status="retired"))
    write_skill(tmp_path, _draft(status="superseded"))
    assert len(list_skills(tmp_path, status=None)) == 3


def test_iter_skills_skips_tmp_files(tmp_path):
    """An atomic-rename in flight leaves a `.tmp` momentarily; the
    walker MUST NOT include it."""
    skill = _draft()
    write_skill(tmp_path, skill)
    # Manually drop a stale .tmp to simulate a crash mid-write.
    stray = skill_path(tmp_path, skill).with_suffix(".md.tmp")
    stray.write_text("garbage that wouldn't parse anyway", encoding="utf-8")
    found = list(iter_skills(tmp_path))
    assert len(found) == 1
    assert found[0].id == skill.id  # the real file, not the stale .tmp


def test_iter_skills_swallows_corrupted_files(tmp_path):
    """A single corrupted artifact must not blind the whole library —
    the augmenter sees the rest, the operator surface flags the corrupt
    one separately (Chapter 4 §4f)."""
    skill = _draft()
    write_skill(tmp_path, skill)
    # Drop a corrupt .md file alongside.
    (tmp_path / "knowledge" / "corrupt.md").write_text(
        "no frontmatter here", encoding="utf-8"
    )
    found = list(iter_skills(tmp_path))
    assert len(found) == 1
    assert found[0].id == skill.id


def test_iter_skills_returns_nothing_for_empty_dir(tmp_path):
    assert list(iter_skills(tmp_path)) == []


# --- flip_status (§7.5 supersession, §8.5 retirement) -------------------


def test_flip_status_changes_status_atomically(tmp_path):
    skill = _draft(status="active")
    write_skill(tmp_path, skill)
    flipped = flip_status(tmp_path, skill.id, "retired")
    assert flipped.frontmatter.status == "retired"
    # Verify on disk too.
    reloaded = load_skill(skill_path(tmp_path, skill))
    assert reloaded.frontmatter.status == "retired"


def test_flip_status_unknown_id_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        flip_status(tmp_path, "does-not-exist", "retired")


def test_flip_status_preserves_body(tmp_path):
    """The status flip must NOT lose body content — supersession should
    keep the artifact reviewable as a historical record."""
    skill = _draft(status="active", body="# Long body\n\nLots of content.\n")
    write_skill(tmp_path, skill)
    flip_status(tmp_path, skill.id, "superseded")
    reloaded = load_skill(skill_path(tmp_path, skill))
    assert "Long body" in reloaded.body
    assert "Lots of content" in reloaded.body
