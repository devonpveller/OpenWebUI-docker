# Daily-digest podcast: two mornings with no audio (2026-09-08, 2026-09-09)

Investigation + recovery notes. Provenance is stated per claim: *observed live*
means a command was run against the running stack on 2026-09-09 and its output
read; *read from source at file:line* means the code was opened, not the comment
above it.

Two INDEPENDENT problems were found. Only the first caused the missing audio.

---

## 1. No audio — the TTS server was unreachable, not broken

**Symptom (observed live, `docker logs openbrain-podcast`):** on both mornings the
run reached the audio stage, ON returned `failed` twice, and link-enrich exited 0:

```
[link-enrich] ON job ended 'failed' - resubmitting.
[link-enrich] ON job ended 'failed' - no audio.
[podcast] run finished (exit 0).
```

**Cause chain (observed live):**

| Layer | Evidence |
|---|---|
| ON's TTS credential | `credential:xbhle1zrpx8g7vkganpk.base_url = http://host.docker.internal:8000/v1` (SurrealDB query, 2026-09-09) |
| Host :8000 is published by | `stt-tts-tailscale`, in compose project `realtimeaudiochat_local_stt_llm_tts` (`C:\_git\realtimeAudioChat_Local_STT_LLM_TTS\docker-compose.yml`) |
| That container | `RestartCount=5091`, `ExitCode=0`, restarting every ~23s |
| Why it exits | `Received error: invalid key: API key does not exist` then `boot: failed to auth tailscale: tailscale up failed: exit status 1`. The repo `.env` carries the comment `# expires aug 8, 2026` above `TS_AUTHKEY`. |
| Why that killed TTS | `stt-tts-server` ran with `network_mode: "service:tailscale"` — it JOINED the sidecar's netns. Each sidecar restart rebuilt the namespace; the server's listener stayed in the old one. |
| What a caller saw | TCP accept then immediate close = `httpx.RemoteProtocolError: Server disconnected without sending a response.` — verbatim the error in `open_notebook` logs at 05:45:41 (09-08) and 05:48:14 (09-09). |

**Onset (observed live):** boots per hour from `docker logs -t stt-tts-tailscale`
go `1` on 09-06T07, then `32` at 09-07T23, then ~155/hour continuously. The loop
started **2026-09-07 23:48 UTC** — after episode 093 (09-07 05:04, 25m33s audio,
succeeded) and before 094.

**The server itself was never broken.** `curl` on its own loopback returned a
200 and 8,300 bytes of WAV during the outage. Its healthcheck ran inside the same
namespace, so Docker reported `stt-tts-server  Up 33 hours (healthy)` throughout.
**A healthcheck that runs inside the namespace it is testing cannot see a netns
break.** Nothing in `scripts/checks/stack-watchdog.ps1` probes this project at
all (grep for `stt-tts` across `scripts/`: zero hits).

### Fix applied 2026-09-09 (netns ownership inverted)

`C:\_git\realtimeAudioChat_Local_STT_LLM_TTS\docker-compose.yml`: `stt-tts-server`
now owns the namespace and publishes `8000:8000`; the `tailscale` sidecar joins it
via `network_mode: "service:stt-tts-server"` with `depends_on: service_healthy`.
This is the arrangement ai-stack already uses for openwebui/tailscale. A failing
or absent Tailscale key now costs the tailnet node only.

**Verified behaviourally, not structurally** — over 64s the sidecar restarted 4
times (`RestartCount` 0 to 4) while `http://host.docker.internal:8000/health`
polled from inside `open_notebook` returned 200 on every attempt. A TTS synthesis
POST from `open_notebook` returned 12,166 bytes of audio.

**STILL OPEN:** the sidecar keeps crash-looping until a fresh `TS_AUTHKEY` is put
in that repo's `.env` (operator action — login.tailscale.com/admin/settings/keys,
reusable + ephemeral). The tailnet HTTPS endpoint for the STT/TTS server is down
until then. The podcast no longer cares.

---

## 2. Research yield collapsed — Substack stopped 302-ing

Independent of the audio failure, and NOT the cause of the shorter episodes.

**The episode-length story (observed live, SurrealDB `episode.duration_seconds`):**

```
075  2026-08-21  24m31s     <- last episode before the raw-dump bug
076-089 (Aug 22 - Sep 4)    29m - 62m, mean ~42m
090  2026-09-04  24m15s     <- first episode after the podkey2 fix
091  28m15s   092  10m8s   093  25m33s
```

The ~1h episodes were the **bug**, not the baseline: 076-089 shipped a 14-47KB
raw grounded-material dump to ON instead of a written script (the J.1 401, see the
`podcast-json-structured-output-fix` memory). 24-28 min matches 075. So "shorter
since 5 days ago" is the pipeline working correctly again.

**But there IS a real regression underneath it.** Every email on 09-07, 09-08 and
09-09 logged `0 ext link(s), 0 selected + body-fallback`. Not one external article
was researched on any of those days; every source was the newsletter body.

**Cause (reproduced live 2026-09-09, both direct and through `FETCH_PROXY_URL=http://vpn:8888`):**
`https://substack.com/redirect/<uuid>?j=e` now answers **200 with a JS +
`<noscript>` meta-refresh interstitial**, not a 302:

```
status 200  len 1515  location: null
<head><noscript><META http-equiv="refresh" content="0;URL=https://blog.google/...">
</head><script>window.opener = null; location.replace("https://blog.google/...")</script>
```

`unwrapRedirect()` (read from source at
`OB1/recipes/daily-digest/src/enrich/links.ts:145-179` **at OB1 `970cae8`, the
pre-fix commit** — the line moves once the fix lands, so the SHA is part of the
citation) only follows a `Location` header on a 3xx; on a 200 it returns
`res.url` — i.e. the substack URL unchanged.
`link-enrich.ts:374` then drops it:
`.filter(c => c.domain && !c.domain.endsWith("substack.com"))`.
That filter exists to drop the newsletter's own posts, and it cannot tell an
unresolved wrapper from a genuine self-link. Result: **every external link in
every Substack newsletter is silently discarded.**

`decodeSubstackRedirect()` (links.ts:**188** at OB1 `970cae8` — this note first
said 184, which is the `NOISE_TEXT_RE` regex; corrected after a tester resolved
the citation) handles only the `/redirect/2/<base64>` form. The links in these
newsletters are the `/redirect/<uuid>?j=e` form, which it returns `null` for.

Proof the same URL used to work: `40874a4f-3ed1-44f8-82bd-1ea1c10c30b4` appears in
`/reports/podcast-link-report-2026-09-04.json` with `status: "enriched"`, and
returns the 200 interstitial today.

**Not yet fixed** — routed through the agent harness. The fix should be GENERAL,
not substack-specific: follow an interstitial (meta-refresh / `location.replace`)
as another redirect hop, so the next publisher that does this does not silently
zero the day's research. A wrapper that could not be resolved should also be
distinguishable from a real self-link rather than being swallowed by the same
`endsWith` filter.

---

## 3. What was silent that should not have been

Both failures ran green. Gaps found while investigating (all verified above):

1. **No crash-loop detection anywhere.** 5,091 restarts of one container over 33
   hours drew no attention. A generic `RestartCount`-delta check across all
   containers would have caught this within one watchdog pass (60s).
2. **The stt-tts project is not monitored at all** even though the daily podcast
   hard-depends on it. It is a separate compose project outside this repo.
3. **A netns-joined container's healthcheck lies.** `stt-tts-server` reported
   healthy for the entire outage. Any joined pair needs a probe from OUTSIDE the
   namespace — this applies to openwebui/tailscale too.
4. **Podcast delivery has no outcome check.** `openbrain-podcast` exits 0 whether
   or not audio exists. Same shape as the sidecar problem `Test-BackupRecency`
   already solves ("a running sidecar that produces nothing is invisible to
   container checks", stack-watchdog.ps1:1683).
5. **Expiring credentials are tracked only in a comment.** `# expires aug 8, 2026`
   above `TS_AUTHKEY`. Same class as the digest's 7-day OAuth token.
6. **Research yield has no floor.** Three consecutive days of `0 ext link(s)` on
   every email is a total content-sourcing failure with a green pipeline.

---

## Rollback lever for the link-resolution change (`podlinks`)

**If the daily digest starts researching wrong or unexpected URLs, or the link
stage misbehaves in any way, pull this first — it does not need a revert, a
rebuild or a redeploy:**

```
# OB1/recipes/daily-digest/.env   (gitignored)
INTERSTITIAL_FOLLOW=0
```

then `docker compose -f OB1/docker/docker-compose.yml up -d --force-recreate openbrain-podcast`.

That restores the pre-2026-09-09 behaviour exactly: `Location` headers are
followed, 200 redirect shells are not. It degrades to the OLD behaviour rather
than to a hole — an unresolved wrapper is still marked, logged and **still
researched**.

**What it costs:** precisely the outage described in section 2 — Substack
wrappers stop resolving and every email falls back to its newsletter body, so
episodes are built from bodies alone. A thin morning, not a missing one. Set it
back to `1` (or delete the line) to re-enable.

**A related switch, for tests only:** `RESEARCH_ALLOW_PRIVATE_TARGETS=1` disables
the host screen that stops a resolved redirect pointing at internal
infrastructure. `links.test.ts` sets it because its stub servers are loopback.
**It must never be set in production**, and nothing in the deployed config sets
it.

## Security posture of the link stage (asked 2026-09-09)

- **No JavaScript is ever executed.** The scanner reads the document as text and
  lifts a URL *string* out of a script body; nothing evaluates it. There is no
  `eval`, no `new Function`, no DOM and no headless browser in
  `OB1/recipes/daily-digest/src/enrich/` — verified by grep, not by assumption.
- **The response body is bounded** at 16KB and is discarded undecoded past that,
  so a hostile page cannot make the link stage buffer arbitrarily.
- **Where a resolved URL may point is now screened** (`isPubliclyRoutableUrl`),
  because that URL is chosen by the page we just fetched and we then fetch it.
  Denied by shape: non-http(s), loopback, RFC1918, `169.254.x` (cloud metadata),
  `100.64/10` (CGNAT — and the tailnet), multicast, `.local`/`.internal`, and any
  hostname with no dot, which is what every docker service name looks like.
  Applied to the `Location` hop as well as the interstitial one.
- **The egress proxy was already a boundary, measured on 2026-09-09:**
  `openbrain-curator:8000`, `llama-cpp:8080`, `openbrain-db:5432` and
  `127.0.0.1` all returned 500 through `http://vpn:8888` while public URLs
  resolved normally. The code screen is defence in depth — it makes that a
  property the code asserts rather than one the network configuration happens to
  provide, which matters because `egress.ts` documents `FETCH_PROXY_URL=""` as a
  supported opt-out to direct fetching.
- **Still true and NOT closed by this item:** `fetchAndExtract` in `extract.ts`
  performs no host screening of its own, so a URL reaching it by any other route
  is unscreened. Every route this item touches now screens before handing a URL
  on, but the general control belongs in the fetch layer.

## Recovery performed

- 095 (2026-09-09) resubmitted to ON from its already-rendered script at
  `/reports/095-daily-gpt-6-astra.md`, job `command:l0lh7cslwu8th815egsk`.
  Completed: 26m32s of audio.
- 094 (2026-09-08) resubmitted the same way, job `command:0bqh2v8giabn5xqyy8ov`.
  Completed: 25m2s of audio.

Both were re-rendered from the scripts the failed runs had ALREADY written, so
no LLM script generation was repeated - only the TTS stage that had failed.

---

## 4. The pre-commit gate cannot see the tests that were just added

**Provenance: read from source at `scripts/checks/check-ob1-recipe-tests.ps1:149`,
and observed live in the `podlinks` commit output, 2026-09-09.**

Committing the `podlinks` fix printed:

    [check-ob1-recipe-tests] shrink floor OK - test FILES 8 -> 8, test CASES 54 -> 54 (970cae8 -> df83254).
    [check-ob1-recipe-tests] OK - 8 test file(s), # tests 54 | # pass 54 | # fail 0

That commit ADDED a test file with 14 cases. The counts did not move because the
gate globs `-Filter '*.test.mjs'` (line 149) and the daily-digest recipe's suites
are Deno `*.test.ts`. Its sibling `check-ob1-deno-recipes` runs `deno check` over
"36 **non-test** *.ts" and says so itself: "Type check only - the test suite is
NOT run."

So **no pre-commit gate runs the daily-digest Deno tests**, and the shrink floor
that exists to stop tests being quietly deleted does not cover them either:
deleting all 34 cases in `script-renderer.test.ts` and all 14 in
`links.test.ts` would print the same reassuring `54 -> 54`.

This matters twice over. A reader of that output would reasonably conclude the
new tests were run — they were not; they were run by hand
(`deno test --allow-net --allow-env`). And the protection the floor is meant to
give the `script-renderer.test.ts` suite, written after the 2026-09-04
five-defect run, does not exist.

Not fixed here: it is a gate change, not a link-resolver change, and it belongs
with the `crashloop` family of "reports green while checking nothing". The same
open item is already recorded in the `agent-harness-findings-note-audit` memory
as "Deno suite ungated by 5b AND 5c" — this is a second, independent sighting.
