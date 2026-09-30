"""The seed file owns a profile's `model`; the DB owns its `lane` (model-roles, mr-consumers).

Before this, `load_from_disk` seeded a profile only when no active row existed, so moving
the shipped profiles from a concrete model id to a gateway ROLE changed nothing on a host
whose DB already held them: every live profile kept asking for the old id.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sqlalchemy import select

from app.models import Profile
from app.modules.profiles import ProfileRegistry

SHIPPED = Path(__file__).resolve().parents[1] / "profiles"


def _copy_profiles(tmp_path: Path) -> Path:
    d = tmp_path / "profiles"
    shutil.copytree(SHIPPED, d)
    return d


def _set_model(d: Path, name: str, model: str) -> None:
    f = d / f"{name}.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["model"] = model
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _rows(db, name: str) -> list[Profile]:
    async with db.session_factory() as s:
        return list((await s.execute(
            select(Profile).where(Profile.name == name).order_by(Profile.version))).scalars().all())


async def test_every_shipped_profile_asks_for_a_role(db):
    reg = ProfileRegistry(db, str(SHIPPED))
    await reg.load_from_disk()
    assert reg.all(), "no profiles shipped"
    assert {p.model for p in reg.all().values()} == {"local-large"}


async def test_a_changed_seed_model_becomes_a_new_version_and_keeps_a_flipped_lane(db, tmp_path):
    d = _copy_profiles(tmp_path)
    _set_model(d, "pm", "qwen36-27b")          # what a host seeded before the move
    reg = ProfileRegistry(db, str(d))
    await reg.load_from_disk()
    await reg.set_lane("pm", "cloud")            # an operator flip, persisted as v2
    assert reg.get("pm").model == "qwen36-27b"

    _set_model(d, "pm", "local-large")           # the shipped file after the move
    reg2 = ProfileRegistry(db, str(d))           # a restart
    await reg2.load_from_disk()
    pm = reg2.get("pm")
    assert pm.model == "local-large"
    assert pm.lane == "cloud"                    # the DB still owns lane
    rows = await _rows(db, "pm")
    assert [(r.version, r.active, r.lane, r.model) for r in rows] == [
        (1, False, "local", "qwen36-27b"),
        (2, False, "cloud", "qwen36-27b"),
        (3, True, "cloud", "local-large"),
    ]


async def test_an_unchanged_seed_writes_no_new_version(db, tmp_path):
    d = _copy_profiles(tmp_path)
    reg = ProfileRegistry(db, str(d))
    await reg.load_from_disk()
    await ProfileRegistry(db, str(d)).load_from_disk()
    for name in reg.all():
        assert [r.version for r in await _rows(db, name)] == [1], name


async def test_reverting_the_seed_file_moves_the_profile_back(db, tmp_path):
    d = _copy_profiles(tmp_path)
    _set_model(d, "po", "qwen36-27b")
    await ProfileRegistry(db, str(d)).load_from_disk()
    _set_model(d, "po", "local-large")
    await ProfileRegistry(db, str(d)).load_from_disk()
    _set_model(d, "po", "qwen36-27b")
    reg = ProfileRegistry(db, str(d))
    await reg.load_from_disk()
    assert reg.get("po").model == "qwen36-27b"
    assert [(r.version, r.active) for r in await _rows(db, "po")] == [(1, False), (2, False), (3, True)]
