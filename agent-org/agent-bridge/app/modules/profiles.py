"""profiles — the role primitive (C4, PLAN §5.4 / TASKS Pc.3).

A profile binds {lane, model, system_prompt_ref=charter, temperature, tool_access=scope,
caller_key} to a role name. Adding a role = adding a profile; flipping a role local<->cloud
is a one-field edit (`lane`). Profiles are versioned/audited like rules (§4.2).

v1 storage: seed from versioned JSON files under `profiles/`, mirror into the DB so the
bridge reads a single source and lane-flips persist. No new service.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..models import Profile
from ..schemas import ProfileSchema

log = logging.getLogger("agent_bridge.profiles")


class ConcurrentProfileChange(Exception):
    """Another request wrote the same profile's next version first (the (name, version) unique
    constraint refused this one); nothing of this request was written."""


class ProfileRegistry:
    def __init__(self, db: Database, profiles_dir: str) -> None:
        self.db = db
        self.dir = Path(profiles_dir)
        self._cache: dict[str, ProfileSchema] = {}

    async def load_from_disk(self) -> None:
        """Seed the DB (and cache) from JSON files. Existing DB rows win on lane
        (a persisted operator lane-flip is not clobbered by the seed file)."""
        if not self.dir.exists():
            log.warning("profiles dir %s missing — no profiles seeded", self.dir)
            return
        async with self.db.session_factory() as s:
            for f in sorted(self.dir.glob("*.json")):
                data = json.loads(f.read_text(encoding="utf-8"))
                ps = ProfileSchema(**data)
                existing = (
                    await s.execute(
                        select(Profile).where(
                            Profile.name == ps.profile, Profile.active.is_(True)
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    s.add(
                        Profile(
                            name=ps.profile,
                            version=1,
                            lane=ps.lane,
                            model=ps.model,
                            system_prompt_ref=ps.system_prompt_ref,
                            temperature=ps.temperature,
                            tool_access=ps.tool_access,
                            caller_key=ps.caller_key,
                        )
                    )
            await s.commit()
        await self.refresh()

    async def refresh(self) -> None:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(Profile).where(Profile.active.is_(True)))
            ).scalars().all()
        self._cache = {
            r.name: ProfileSchema(
                profile=r.name,
                lane=r.lane,
                model=r.model,
                system_prompt_ref=r.system_prompt_ref,
                temperature=r.temperature,
                tool_access=list(r.tool_access or []),
                caller_key=r.caller_key,
            )
            for r in rows
        }

    def get(self, name: str) -> ProfileSchema:
        if name not in self._cache:
            raise KeyError(f"unknown profile {name!r} — add it to profiles/")
        return self._cache[name]

    def all(self) -> dict[str, ProfileSchema]:
        return dict(self._cache)

    async def set_lane(self, name: str, lane: str, actor: str = "operator") -> None:
        """Flip a role local<->cloud as a new profile version (audited). One field."""
        assert lane in ("local", "cloud")
        async with self.db.session_factory() as s:
            cur = (
                await s.execute(
                    select(Profile).where(Profile.name == name, Profile.active.is_(True))
                )
            ).scalar_one()
            cur.active = False
            s.add(
                Profile(
                    name=cur.name,
                    version=cur.version + 1,
                    lane=lane,
                    model=cur.model,
                    system_prompt_ref=cur.system_prompt_ref,
                    temperature=cur.temperature,
                    tool_access=cur.tool_access,
                    caller_key=cur.caller_key,
                )
            )
            await s.commit()
        await self.refresh()
        log.info("profile %s lane -> %s (by %s)", name, lane, actor)

    async def active_row(self, name: str) -> dict | None:
        """The active row's lane, version and model as the DATABASE has them now (the cache can
        trail a write made through another path)."""
        async with self.db.session_factory() as s:
            cur = (
                await s.execute(
                    select(Profile).where(Profile.name == name, Profile.active.is_(True))
                )
            ).scalar_one_or_none()
            return None if cur is None else {"lane": cur.lane, "version": cur.version,
                                             "model": cur.model}

    async def set_model(self, name: str, model: str, *, registered: set[str],
                        dry_run: bool = False, expect_lane: str | None = None,
                        expect_version: int | None = None) -> dict:
        """Point a role at another gateway model name as a new profile version. One field: lane,
        charter, temperature, scope and caller key are carried over unchanged. The DB owns the live
        value (the seed files only seed a MISSING profile), so this is how an existing install
        moves - reached only through the governed operator intent (`kind=profile_model`), which
        audits who asked.

        Refuses (raises, writes nothing): an unknown profile (KeyError), a model the gateway does
        not register (`registered` - the caller fetches it; ValueError). Idempotent: the same model
        again writes nothing (`changed=False`). `dry_run` validates and reports, writing nothing.
        `expect_lane` / `expect_version`: what the caller validated against; if the active row no
        longer has them (another write landed meanwhile), ConcurrentProfileChange and nothing is
        written - as when the (name, version) constraint refuses a racing insert."""
        model = (model or "").strip()
        if not model:
            raise ValueError("no model named")
        if model not in registered:
            raise ValueError(f"model `{model}` is not registered at the gateway")
        async with self.db.session_factory() as s:
            cur = (
                await s.execute(
                    select(Profile).where(Profile.name == name, Profile.active.is_(True))
                )
            ).scalar_one_or_none()
            if cur is None:
                raise KeyError(name)
            if ((expect_lane is not None and cur.lane != expect_lane)
                    or (expect_version is not None and cur.version != expect_version)):
                raise ConcurrentProfileChange(name)
            result = {"profile": name, "before": cur.model, "after": model, "lane": cur.lane,
                      "version": cur.version, "changed": cur.model != model, "dry_run": dry_run}
            if not result["changed"] or dry_run:
                return result
            cur.active = False
            s.add(
                Profile(
                    name=cur.name,
                    version=cur.version + 1,
                    lane=cur.lane,
                    model=model,
                    system_prompt_ref=cur.system_prompt_ref,
                    temperature=cur.temperature,
                    tool_access=cur.tool_access,
                    caller_key=cur.caller_key,
                )
            )
            try:
                await s.commit()
            except IntegrityError as exc:
                await s.rollback()
                raise ConcurrentProfileChange(name) from exc
            result["version"] = cur.version + 1
        await self.refresh()
        log.info("profile %s model %s -> %s (v%d)", name, result["before"], model, result["version"])
        return result
