# PLAN — Podcast on-demand audio loading (Open Notebook fork)

**Date:** 2026-08-02
**Status:** APPROVED — build immediately after this plan (operator go-ahead in
Mattermost thread; rollback = image retag, worst case restore from backups).
**Repo touched:** `D:\Open WebUI\open-notebook` (IKS fork). No ai-stack compose,
recovery-script, or container-inventory changes — same container, new image.

## Problem

Opening the Podcasts page starts **full MP3 downloads for every episode card
near the viewport**, in parallel. Nothing is playable until a file completely
arrives; a throttled/backgrounded tab aborts the fetches and the cards land in a
dead-end "audio unavailable" state. The user has to wait through downloads of
episodes they never intended to play.

## Root cause (code-grounded)

`frontend/src/components/podcasts/EpisodeCard.tsx`:

- An `IntersectionObserver` (300px rootMargin) marks each card `inView`, which
  triggers `fetch(directAudioUrl, { headers })` + `response.blob()` — the
  **entire file**, downloaded eagerly, held in memory as an object URL.
- The blob dance exists only to attach an `Authorization: Bearer` header, which
  a plain `<audio src>` cannot send.
- **But in this deployment no password is configured**: the ai-stack compose
  passes no `OPEN_NOTEBOOK_PASSWORD`, and the fork's `.env` is `.dockerignore`d
  out of the image — `PasswordAuthMiddleware` (api/auth.py:32) skips auth
  entirely when no password is set. The blob machinery is dead weight.
- The backend already streams: `FileResponse` on Starlette **0.50** honors HTTP
  `Range` requests (support since 0.36). A direct `<audio preload="none">`
  src streams on play — sub-second time-to-first-audio on the tailnet, working
  seek, no full download, no blob memory.
- Bonus: direct src + native controls avoids iOS Safari's autoplay block that
  can hit "fetch a blob, then call play()" flows (the user-gesture activation
  expires during the await) — relevant since episodes are played from the phone.

The episodes list response already carries all card metadata (name, status,
transcript, outline, briefing). The only thing missing for a metadata-first
card is **duration** — not stored anywhere today.

## Design

1. **Metadata-first cards.** No audio bytes fetched on page load or scroll.
   Delete the IntersectionObserver + blob fetch + loading state.
2. **Direct streaming playback.** `<audio controls preload="none"
   src={resolvedAudioUrl}>`. The browser fetches nothing until the user hits
   play, then streams via Range requests. This is the "play button gates the
   download" idea — but with streaming, so play is near-instant rather than
   download-the-file slow.
3. **Stamp duration on the episode row** (`duration_seconds`, float).
   - At generation completion (`commands/podcast_commands.py`, where
     `audio_file` is set) — probe with `ffprobe` (already in the image).
   - Lazy backfill in the list endpoint: episodes with audio on disk but no
     stamped duration get probed (off the event loop via `asyncio.to_thread`)
     and persisted. Existing episodes self-heal on the first list call after
     deploy (one-time ~50–100 ms per episode); steady state costs nothing.
   - Card shows `m:ss` / `h:mm:ss` next to the created-time label — numeric
     format, so **no new i18n keys** across the 9 locale files.
4. **Auth-proof the audio path.** Exempt
   `^/api/podcasts/episodes/[^/]+/audio$` from `PasswordAuthMiddleware`
   (regex, since `excluded_paths` is exact-match). Media elements can't send
   auth headers; today this is a no-op (no password), but it keeps playback
   working if a password is ever set. Acceptable: API is loopback + tailnet
   only — Tailscale device auth is the trust boundary. Revisit with signed
   URLs if ON ever goes behind the Portal.
5. **Recoverable error state.** `onError` on the audio element renders the
   existing `podcasts.audioUnavailable` text **plus a retry button** that
   remounts the element — no more dead-end state. (Reuses existing
   `podcasts.retry` locale key.)

## Changes by file (all in `D:\Open WebUI\open-notebook`)

| File | Change |
|------|--------|
| `open_notebook/podcasts/models.py` | Add `duration_seconds: Optional[float]` to `PodcastEpisode`. |
| `open_notebook/database/migrations/17.surrealql` (+`_down`) | **New.** `DEFINE FIELD IF NOT EXISTS duration_seconds ON TABLE episode TYPE option<float>` — see deployment finding below. |
| `open_notebook/database/async_migrate.py` | Register migration 17 (the runner uses an explicit list, not directory discovery). |
| `open_notebook/utils/audio.py` | **New.** `probe_duration_seconds(path)` — ffprobe subprocess, returns float seconds or `None`, never raises. |
| `commands/podcast_commands.py` | After `episode.audio_file` is set at generation completion, stamp `episode.duration_seconds` before the save. |
| `api/routers/podcasts.py` | Add `duration_seconds` to `PodcastEpisodeResponse` (list + detail). In the list loop: if audio exists on disk and duration is unstamped, probe via `asyncio.to_thread` and persist. |
| `api/auth.py` | Regex exemption for the audio path (comment explains why). |
| `api/routers/podcasts.py` (audio endpoint) | `content_disposition_type="inline"` — a streaming endpoint should not advertise `attachment`. **Follow-up 2026-08-03:** `?download=1` query param serves `attachment` instead, restoring save-as-file (the inline switch had removed the browser's download-with-filename behavior, and iOS never offers downloads from an inline player). |
| `frontend/src/components/podcasts/EpisodeCard.tsx` (follow-up) | Explicit Download button in the card action row (anchor to `{audioSrc}?download=1`, `t('common.download')`, shown only when audio exists) — a deliberate affordance that works on every platform incl. iPhone. |
| `frontend/src/lib/types/podcasts.ts` | Add `duration_seconds?: number \| null` to `PodcastEpisode`. |
| `frontend/src/components/podcasts/EpisodeCard.tsx` | Remove observer/blob/loading machinery. Small shared `EpisodeAudio` sub-component (used in card + details dialog): direct src, `preload="none"`, `onError` → unavailable + retry. Show formatted duration in the meta line. |

Old-image compatibility: pydantic ignores unknown DB fields by default, so rows
stamped with `duration_seconds` load fine under the pre-change image → rollback
stays clean even after backfill has run.

**Deployment finding (2026-08-02):** the `episode` table is SCHEMAFULL — its
fields were defined outside the fork's migration files — so the first deploy's
`UPDATE … MERGE` writes silently dropped `duration_seconds` (the `updated`
timestamp changed; the new field didn't stick; no error anywhere). Diagnosed by
querying SurrealDB directly (`INFO FOR TABLE episode`). Fix = migration 17
(`DEFINE FIELD IF NOT EXISTS`), also applied to the live DB by hand
(idempotent with the migration). **Rule for future episode-table fields: a
model/pydantic change is not enough — every new field needs a
`DEFINE FIELD` migration or SurrealDB drops it on write without any error.**

## Deploy + rollback

Per the IKS promotion runbook (frontend changes ⇒ full image rebuild):

```powershell
# 1. Keep the current image for instant rollback
docker tag open_notebook:iks open_notebook:iks-pre-ondemand-audio

# 2. Rebuild the fork image
docker build -f Dockerfile.single -t open_notebook:iks "D:\Open WebUI\open-notebook"

# 3. Recreate the container (ai-stack project; open_notebook is standalone —
#    not part of the openwebui/tailscale netns pair)
docker compose up -d open_notebook
```

**Rollback:** `docker tag open_notebook:iks-pre-ondemand-audio open_notebook:iks`
then `docker compose up -d open_notebook`. Data changes are additive-only; the
backups stack (surrealdb/notebook data sidecars) is the belt-and-suspenders.

## Verification

1. `Range` streaming: `curl -s -D - -o /dev/null -H "Range: bytes=0-1023"
   http://127.0.0.1:5055/api/podcasts/episodes/<id>/audio` → **206 Partial
   Content** with a `Content-Range` header.
2. List backfill: `GET http://127.0.0.1:5055/api/podcasts/episodes` →
   completed episodes carry non-null `duration_seconds` (second call confirms
   values persisted, i.e. no re-probe).
3. Frontend up: podcasts page on :8503 returns 200 with the new bundle.
4. Manual (operator, phone ok): open Podcasts via :8443 — cards render
   immediately with duration; network tab shows **zero** audio requests until
   play; play starts within ~1 s; backgrounding the tab before playing no
   longer produces "audio unavailable".
5. Next generated episode arrives with `duration_seconds` already stamped
   (checks the generation-time path, not just backfill).

## Out of scope / future

- Signed/short-lived audio URLs if Open Notebook is ever exposed through the
  Portal (internet) rather than tailnet-only.
- Pagination of the episodes list (list response includes full transcripts;
  fine at current episode counts, revisit if the page itself gets heavy).
- Upstreaming to the public open-notebook repo.
