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


#: Model tiers (mt-policy, tracker H2). A profile's `tier` names the model SIZE its task kind
#: deserves; which model ROLE that is per lane is Settings.profile_tier_models_local / _cloud.
TIERS = ("large", "small")


def parse_tier_models(spec: str) -> dict[str, str]:
    """`"large=local-large,small=local-small"` -> {"large": "local-large", "small": "local-small"}.
    Raises ValueError on a malformed entry or an unknown tier - a typo here would silently route a
    role to the wrong model, so it is loud instead."""
    out: dict[str, str] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        tier, sep, model = part.partition("=")
        tier, model = tier.strip(), model.strip()
        if not sep or not model or tier not in TIERS:
            raise ValueError(f"bad tier-model entry {part!r} (want <tier>=<model>, tier in {TIERS})")
        out[tier] = model
    return out


#: The env var behind each lane's spec, so a refusal names the thing an operator edits.
TIER_MODEL_ENV = {"local": "AO_PROFILE_TIER_MODELS_LOCAL", "cloud": "AO_PROFILE_TIER_MODELS_CLOUD"}


def check_tier_models(settings) -> None:
    """Raise ValueError naming the env var unless BOTH lanes' specs parse and name a model for
    EVERY tier. tier_drift() calls it first, so a half-written spec ('large=local-large') is one
    clear refusal rather than a KeyError deep inside whichever profile happened to need `small`."""
    for lane, env in TIER_MODEL_ENV.items():
        spec = (settings.profile_tier_models_cloud if lane == "cloud"
                else settings.profile_tier_models_local)
        try:
            models = parse_tier_models(spec)
        except ValueError as exc:
            raise ValueError(f"{env}={spec!r}: {exc}") from exc
        missing = [t for t in TIERS if t not in models]
        if missing:
            raise ValueError(f"{env}={spec!r} names no model for tier(s) {', '.join(missing)} "
                             f"(want e.g. large=<model>,small=<model>)")


def tier_model(settings, tier: str, lane: str) -> str:
    """The model role a profile of `tier` asks for on `lane` ("local" | "cloud" - the EFFECTIVE
    lane: a cloud profile with the cloud lane disabled routes local, as ModelRouter does)."""
    spec = (settings.profile_tier_models_cloud if lane == "cloud"
            else settings.profile_tier_models_local)
    models = parse_tier_models(spec)
    if tier not in models:
        raise ValueError(f"no model configured for tier {tier!r} on the {lane} lane")
    return models[tier]


class ConcurrentProfileChange(Exception):
    """Another request wrote the same profile's next version first (the (name, version) unique
    constraint refused this one); nothing of this request was written."""


class UnknownProfile(KeyError):
    """No active profile of that name. A KeyError subclass so existing `except KeyError` callers
    still work; the HTTP route catches only this, so a stray KeyError is never mistaken for it."""


class ProfileRegistry:
    def __init__(self, db: Database, profiles_dir: str) -> None:
        self.db = db
        self.dir = Path(profiles_dir)
        self._cache: dict[str, ProfileSchema] = {}
        # name -> tier, from the seed FILES on every boot (policy, not a DB column): a tier edit
        # reaches an existing install at once, while its model still moves only by the intent.
        self._tiers: dict[str, str] = {}

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
                if ps.tier:
                    self._tiers[ps.profile] = ps.tier
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
                tier=self._tiers.get(r.name),
            )
            for r in rows
        }

    def get(self, name: str) -> ProfileSchema:
        if name not in self._cache:
            raise KeyError(f"unknown profile {name!r} — add it to profiles/")
        return self._cache[name]

    def all(self) -> dict[str, ProfileSchema]:
        return dict(self._cache)

    def tier_drift(self, settings) -> list[dict]:
        """Every active profile whose live `model` is not its tier's model role, with the EXACT
        governed command that would move it (`set profile <name> model <role>`). Read-only: the
        landing - or the operator - sends the command; this never writes a profile. A profile with
        no tier is not judged. Raises ValueError (naming the env var) when a tier-model spec is
        malformed or incomplete - the caller reports drift as unavailable, never 500s the listing."""
        check_tier_models(settings)
        out: list[dict] = []
        for name, p in sorted(self._cache.items()):
            if not p.tier:
                continue
            lane = "cloud" if p.lane == "cloud" and settings.cloud_enabled else "local"
            want = tier_model(settings, p.tier, lane)
            if p.model != want:
                out.append({"profile": name, "tier": p.tier, "lane": lane, "model": p.model,
                            "expected": want, "command": f"set profile {name} model {want}"})
        return out

    async def set_lane(self, name: str, lane: str, actor: str = "operator") -> None:
        """Flip a role local<->cloud as a new profile version (audited). One field.

        Refuses (raises, writes nothing): a lane that is not local|cloud (ValueError), an unknown
        profile (UnknownProfile), and a lost race - another request wrote the same profile's next
        version first, so the (name, version) constraint refused this insert
        (ConcurrentProfileChange). On a refused write the cache is re-read from the DB."""
        if lane not in ("local", "cloud"):
            raise ValueError(f"lane must be local or cloud, not {lane!r}")
        async with self.db.session_factory() as s:
            cur = (
                await s.execute(
                    select(Profile).where(Profile.name == name, Profile.active.is_(True))
                )
            ).scalar_one_or_none()
            if cur is None:
                raise UnknownProfile(name)
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
            try:
                await s.commit()
            except IntegrityError as exc:
                await s.rollback()
                await self.refresh()
                raise ConcurrentProfileChange(name) from exc
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
                # C4 (f2-runtime): the lost race re-reads the cache like set_lane does - when the
                # winner is another process (a second registry), only this refresh brings this
                # process's cache in line with the DB.
                await self.refresh()
                raise ConcurrentProfileChange(name) from exc
            result["version"] = cur.version + 1
        await self.refresh()
        log.info("profile %s model %s -> %s (v%d)", name, result["before"], model, result["version"])
        return result
