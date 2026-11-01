"""
title: Research Intake (add a source)
author: ai-stack / Open Brain
version: 1.0.0
description: >
  The Open WebUI inlet of the research workspace's add (research-workbench P5a, rw-p5a-intake). Adds a link
  - a web page, a PDF or a YouTube video - to one of the chatting user's idea refineries, through the SAME
  path the workspace's intake panel uses: openbrain-workbench `POST /workbench/intake/add`, origin `owui`.
  The workbench has the research service fetch it (its proxy-bound fetch, never a fetch here or in
  openbrain-mcp) and holds it in the refinery (`held`: never admitted to the knowledge database on arrival).
  It shows in that user's intake panel, with its phase, retry and cancel.

  Nothing typed beyond the link: the general add asks which refinery (with none named, the tool answers the
  user's refineries to choose from; it creates none).

  Whose add: the tool sends its user's Authentik subject in `x-on-behalf-of`, read from Open WebUI's own user
  record (the `oauth` JSON, `<provider>.sub`), with Open WebUI's OWN workbench service key - the line
  `owui=<key>` of WORKSPACE_SERVICE_KEYS on openbrain-workbench (set `service_key` to it). The workbench
  accepts that header from this key alone, on the add alone. A user with no Authentik link cannot add
  (there is no subject to act for).
"""

import asyncio
import inspect
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, Field

CONTENT_TYPES = ("web_article", "pdf", "youtube_transcript")


class Tools:
    class Valves(BaseModel):
        workbench_url: str = Field(
            default="http://openbrain-workbench:8000",
            description="Base URL of openbrain-workbench (Open WebUI reaches it on ai-stack_default).",
        )
        service_key: str = Field(
            default="",
            description="Open WebUI's own workbench key: the `owui=<key>` entry of WORKSPACE_SERVICE_KEYS (never MCP_ACCESS_KEY).",
        )
        oauth_provider: str = Field(
            default="oidc",
            description="The key of the Authentik link in Open WebUI's user record (oauth JSON); `oidc` for OPENID_PROVIDER_URL.",
        )
        wait_sec: float = Field(default=20.0, description="How long to wait for the add to be held before answering.")

    def __init__(self):
        self.valves = self.Valves()

    async def add_research_source(
        self,
        url: str,
        refinery: str = "",
        content_type: str = "",
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Add a link (web page, PDF or YouTube video) to one of the user's idea refineries in the research workspace.

        :param url: The link to add.
        :param refinery: The refinery it joins - its title or id. Leave empty to be shown the user's refineries.
        :param content_type: Optional: web_article, pdf or youtube_transcript (otherwise taken from the link).
        :return: What happened: held in the refinery, the reason it was refused, or the refineries to choose from.
        """
        v = self.valves
        if not v.service_key:
            return "The research intake is not configured: set the tool's service_key valve (Open WebUI's owui=<key> in WORKSPACE_SERVICE_KEYS)."
        sub = await _authentik_subject(__user__, v.oauth_provider)
        if not sub:
            return "Your Open WebUI account has no Authentik sign-in link, so there is no research workspace user to add this for."
        if content_type and content_type not in CONTENT_TYPES:
            return f"Not a content type the research workspace takes: {content_type} (it takes {', '.join(CONTENT_TYPES)})."
        base = v.workbench_url.rstrip("/")
        headers = {"Content-Type": "application/json", "x-brain-key": v.service_key, "x-on-behalf-of": sub}
        body: dict[str, Any] = {"url": url.strip()}
        if content_type:
            body["content_type"] = content_type
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                async def add(extra: dict) -> tuple[int, dict]:
                    async with s.post(f"{base}/workbench/intake/add", json={**body, **extra}, headers=headers) as r:
                        try:
                            return r.status, await r.json(content_type=None)
                        except Exception:  # noqa: BLE001
                            return r.status, {}

                status, out = await add({})
                if status == 400 and out.get("needs") == "refinery_id":
                    choices = out.get("refineries") or []
                    pick = _pick(choices, refinery)
                    if not pick:
                        if not choices:
                            return "You have no idea refinery yet: start one from a document in the research workspace, then add links to it."
                        names = "; ".join(f"{c.get('title')} ({c.get('id')})" for c in choices)
                        return f"Which refinery does it join? Your refineries: {names}. Call again with `refinery` set."
                    status, out = await add({"refinery_id": pick})
                if status != 202:
                    return f"The research workspace refused the add ({status}): {out.get('error', 'no reason given')}"
                job_id = str(out.get("job_id", ""))
                job = await _wait(s, base, headers, job_id, v.wait_sec)
        except Exception as e:  # noqa: BLE001 - surface, never crash the chat
            return f"Could not reach the research workspace: {e}"
        if job and job.get("status") == "held":
            return f"Held in your refinery (job {job_id}); it shows in your research workspace's Intake panel."
        if job and job.get("status") == "failed":
            return f"The add failed: {(job.get('detail') or {}).get('error') or 'no reason given'} (job {job_id}; retry it from the Intake panel)."
        return f"Added (job {job_id}): it is {(job or {}).get('status', 'queued')} - follow it in your research workspace's Intake panel."


def _pick(choices: list, wanted: str) -> Optional[str]:
    w = (wanted or "").strip().lower()
    if not w:
        return None
    for c in choices:
        if str(c.get("id", "")).lower() == w or str(c.get("title", "")).strip().lower() == w:
            return str(c.get("id"))
    return None


async def _wait(s: "aiohttp.ClientSession", base: str, headers: dict, job_id: str, wait_sec: float) -> Optional[dict]:
    if not job_id:
        return None
    loop = asyncio.get_event_loop()
    end = loop.time() + max(0.0, wait_sec)
    job: Optional[dict] = None
    while True:
        async with s.get(f"{base}/workbench/intake/jobs/add/{job_id}", headers=headers) as r:
            if r.status == 200:
                job = (await r.json(content_type=None)).get("job")
        if (job and job.get("status") in ("held", "failed", "cancelled")) or loop.time() >= end:
            return job
        await asyncio.sleep(0.5)


async def _authentik_subject(user: Optional[dict], provider: str = "oidc") -> Optional[str]:
    """The user's Authentik subject from Open WebUI's user record, or None (as deep_research.py reads it)."""
    if not user:
        return None
    oauth = user.get("oauth")
    if not isinstance(oauth, dict) and user.get("id"):
        try:
            from open_webui.models.users import Users  # type: ignore

            rec = Users.get_user_by_id(user["id"])
            if inspect.isawaitable(rec):
                rec = await rec
            oauth = getattr(rec, "oauth", None)
            if not isinstance(oauth, dict) and getattr(rec, "oauth_sub", None):
                user = {**user, "oauth_sub": getattr(rec, "oauth_sub")}
        except Exception:  # noqa: BLE001 - outside Open WebUI, or a record without the field
            oauth = None
    if isinstance(oauth, dict):
        entry = oauth.get(provider)
        sub = entry.get("sub") if isinstance(entry, dict) else None
        if isinstance(sub, str) and sub.strip():
            return sub.strip()
    legacy = user.get("oauth_sub")
    if isinstance(legacy, str) and legacy.startswith(provider + "@") and legacy[len(provider) + 1 :].strip():
        return legacy[len(provider) + 1 :].strip()
    return None
