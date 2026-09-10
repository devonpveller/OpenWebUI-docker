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
   container checks" — search `stack-watchdog.ps1` for `Test-BackupRecency`).
   Cited by NAME rather than by line: this said `:1683`, the line was 1685 even
   in the commit that wrote the citation, and the `crashloop` item is moving that
   file's line numbers as this lands. A line number in a note about another
   branch's file is stale on arrival.
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
- **The response body is bounded** at 16KB: the read stops once that much has
  arrived and anything over it is discarded undecoded rather than parsed, so a
  hostile page cannot make the link stage buffer arbitrarily. The cut is a
  ceiling on what is KEPT and can overshoot by at most one read chunk, because a
  stream does not hand out partial ones — `readHtmlPrefix` was made precise about
  that in OB1 `53f014c` and this bullet had kept the exact-cut phrasing.
- **Where a resolved URL may point is now screened** (`isPubliclyRoutableUrl`),
  because that URL is chosen by the page we just fetched and we then fetch it.
  Denied by shape: non-http(s), loopback, RFC1918, `169.254.x` (cloud metadata),
  `100.64/10` (CGNAT — and the tailnet), multicast, `.local`/`.internal`, and a
  hostname with no dot.
  **CORRECTED 2026-09-10:** this bullet used to stop at "no dot, which is what
  every docker service name looks like". That is precisely the reasoning
  `links.ts:265` records as *not enough* — `openbrain-curator.open-brain_obnet`
  has a dot and docker's embedded DNS answers it. The rule that closes the class
  is the TLD SHAPE test — the `looksLikeTld` check in `isPubliclyRoutableUrl`
  (cited by name: `:274-278` is the COMMENT that states the rule, the code is
  twenty lines below it, and line numbers in this file have been wrong twice) —
  where the rightmost label must look like
  a public suffix, which a docker network name does not. Describing the screen by
  the rule it OUTGREW made this note contradict the code it documents.
  Applied to the `Location` hop as well as the interstitial one.
- **The egress proxy was already a boundary, measured on 2026-09-09:**
  `openbrain-curator:8000`, `llama-cpp:8080` and `openbrain-db:5432` all
  returned 500 through `http://vpn:8888` while public URLs resolved normally.
  **CORRECTED 2026-09-10:** this list originally included `127.0.0.1`, and that
  half does NOT reproduce — re-measured through the same mechanism `egress.ts`
  uses, it returns **404**, because the proxy connects to its OWN loopback
  (`search-vpn` listens on `:::8000`). The service-name 500s do reproduce. No
  exploitable path through this code — `isPubliclyRoutableUrl` refuses literal
  `127.0.0.1` outright — but the figure was wrong and it was being used as the
  stated mitigation for the DNS-time limit below, so it mattered. The code screen is defence in depth — it makes that a
  property the code asserts rather than one the network configuration happens to
  provide, which matters because `egress.ts` documents `FETCH_PROXY_URL=""` as a
  supported opt-out to direct fetching.
- **Still true and NOT closed by this item:** `fetchAndExtract` in `extract.ts`
  performs no host screening of its own, so a URL reaching it by any other route
  is unscreened. Every route this item touches now screens before handing a URL
  on, but the general control belongs in the fetch layer.
- **KNOWN LIMIT, stated rather than papered over (found in test 2026-09-09):**
  the screen is TEXTUAL, so it cannot stop a *public hostname that RESOLVES to a
  private address*. A tester demonstrated `localtest.me` (a real public name
  pointing at 127.0.0.1) reaching loopback. Closing it needs a resolve-then-check
  at connect time, which belongs in the fetch layer alongside the point above,
  not in a URL screen. The egress proxy remains the effective control for that
  case — measured, but a property of the network config rather than of this code.
- **THREE defeats of this screen were found by testing, not by me writing it**,
  and the third is the one that mattered. **CORRECTED 2026-09-10:** this said
  "two" and "both were trivial", and that count is what a reader would have
  trusted. In order:
  1. a TRAILING DOT — `http://localhost.:PORT/` connected to a live listener,
     because the trailing root label satisfies a `host.includes('.')` test.
  2. IPv4-MAPPED IPv6 — and my first fix leaked too, because the URL parser
     normalises `[::ffff:127.0.0.1]` to `[::ffff:7f00:1]` before the screen sees
     it.
  3. **`<service>.<network>` — NOT trivial, and it was never written down here
     at all.** Round 5's tester proved live, at the shipping default, that
     `unwrapRedirect` FOLLOWED `http://openbrain-curator.open-brain_obnet:8000/`
     and the target answered `200 {ok:true,db:true}`; `llama-cpp.ai-stack_llm-net:8080`
     answered 401. A docker `<service>.<network>` name HAS a dot, so every
     dot-based test passed it and docker's embedded DNS resolved it (172.25.0.18).
     Fixed in OB1 `c13fa1c` by the TLD-shape rule, and `links.ts:265` calls it
     "the miss that matters most" — while this note, its own findings sink,
     omitted it and kept describing the superseded rule. Found by round 10's
     tester.

  The lesson is not "host screens are hard", it is the one this item keeps
  paying for: **a count is a claim.** "Two, both trivial" turned the one
  non-trivial defeat into no defeat at all, in the document a future reader would
  consult before trusting the screen.

### Residuals: everything a tester reported and did not fail us on

**HOW THIS LIST WAS BUILT, so its completeness is checkable rather than
asserted.** Round 12 failed this note for saying "reported by TWO independent
testers" about a numbered list of four, from which the previous round had taken
item 2 and left its siblings. Partial enumeration presented as complete — the
defect this item has now produced five times. So the method, which anyone can
re-run: read the *out-of-scope / findings / residuals* section of EVERY
`podlinks.attemptN.evidence.md` in `.git/agent-worktrees/queue/`, plus every
verdict `reason` in `queue.ps1 -Show -Id podlinks`, and carry across everything a
tester reported as true-but-not-a-failure. Every entry below names the round that
found it and was RE-MEASURED against OB1 `b0cc0af` on 2026-09-10 before being
written here. **AND THE METHOD HAS A FAILURE MODE OF ITS OWN, which it produced immediately.**
Reading forward from the record carries entries that a LATER round closed. The
`<nav>` gap below is exactly that: found in round 4, fixed in round 5 by
`58ef169`, and written into this section by round 13 as an open, "re-measured"
residual. So each entry needs a second question after "was it reported?" —
**"is it still true?"** — and that question needs a CONTROL, not a repetition.

The way it fooled me is worth stating plainly, because it is subtle and it will
recur: I re-ran round 4's literal fixture, it resolved, and I called that a
reproduction. It resolves because the document has no text and is a legitimate
shell — a `<div>` version resolves identically. Without the `<div>` control the
measurement showed nothing about `<nav>` at all, and the second half of the claim
(`extractTextFromHtml` returning 0) measured a function the guard had stopped
consulting four rounds earlier. **A measurement without a control is an
anecdote**, and re-running a fixture is not the same as re-testing a finding.

The escape hatch referred to below is `RESEARCH_ALLOW_PRIVATE_TARGETS`, which is
the lever for the host screen (`INTERSTITIAL_FOLLOW=0` is the separate kill
switch for interstitial following, and an earlier draft of this section named the
wrong one).

**Host screen — allowed, none of them a demonstrated reach here.**

| Spelling | Found | Measured today |
|---|---|---|
| `[::ffff:0:7f00:1]` — IPv4-**translated** (RFC 2765), a different `/96` from the mapped one the code decodes | attempt 5 | ALLOWED |
| `[64:ff9b::7f00:1]` — NAT64 well-known prefix | attempt 5 (re-reported 6) | ALLOWED |
| `[2002:7f00:1::]` — 6to4 | attempt 5 (re-reported 6) | ALLOWED |
| `a.lan`, `a.corp`, `a.intranet`, `a.home.arpa` — private-use suffixes | attempt 6 | ALLOWED (`.local`/`.internal` are refused) |
| `mybox.tail1a2b3c.ts.net` — MagicDNS | attempt 6 | ALLOWED |
| `svc.mynet`, `svc.bridge` — single-word alphabetic docker network | attempt 6 | ALLOWED (disclosed in `links.ts`; docker's DNS does answer `<svc>.<bare-net>` on a network so named, and none exists here) |

Refused, for contrast, all measured the same run: `127.0.0.1`, `[::1]`,
`10.0.0.5`, `[::ffff:127.0.0.1]`.

The three IPv6 spellings are RESIDUAL rather than live, and round 12 measured
that rather than reasoning about it: against a loopback listener inside
`openbrain-podcast`, `[::ffff:127.0.0.1]` CONNECTED 200 while all three allowed
spellings failed to connect. For `[64:ff9b::7f00:1]` and `[2002:7f00:1::]` the
reason is a missing relay — NAT64 and 6to4 both need one, and this host has
neither, so on a host that HAS one the same URL is a live defeat. That is a fact
about this network, not about the code.

  `[::ffff:0:7f00:1]` is inert for a different reason and there is no relay in
  its story at all: it is the deprecated RFC 2765 IPv4-translated prefix, which
  nothing routes. Round 12 reported this distinction and round 13 folded it in
  wrongly, lumping all three under "needs a relay" — which UNDERSTATES how inert
  that one is, the opposite of this item's usual failure direction, and is still
  worth being right about.

The tailnet entry deserves its own sentence, because this note offers
`100.64/10` as tailnet coverage: that is an ADDRESS check, and MagicDNS names are
TLD-shaped, so the name form is not covered by it.

The shape under all of these is the one attempt 6 named: **the IPv6 branch denies
by an explicit list and ALLOWS by default, while the IPv4 branch denies by
range.** An allow-by-default screen is one unlisted prefix away from wrong,
permanently. Closing it properly means screening the RESOLVED ADDRESS at connect
time — the same fix the `localtest.me` DNS-time limit needs, in the same place
(the fetch layer, not a function handed a string).

**Interstitial matching — silent false negatives and false positives, all in the
safe direction or thin.** (This heading read "none fixed" until round 14, which
is a universal sitting directly above its own struck-through counterexample. It
was defensible — strikethrough means retracted, so it quantified over the live
bullets — and in a document whose claims have failed four test rounds (6, 10, 12,
13; two of those were universals, one a figure, one an omission), "defensible if
you know the convention" is not the bar. Round 15 caught the replacement
quantifier being loose about which four; round 16 verified the enumeration above
against the queue's own record and also caught "seventy lines under a count that
enumerates exactly" for naming neither the distance nor the count. The count
meant was the "five times" partial-enumeration tally in the method note above.)

- **An UNQUOTED `content=` on a meta refresh is never followed** (attempt 4;
  measured today: `null`). `metaRefreshTarget` requires quotes; browsers honour
  the unquoted form. Substack quotes it, so this did not affect the outage — it
  is a false negative waiting for a future publisher.
- **`LOCATION_ASSIGN_RE` fires inside JS comments and string literals** (attempt
  4; measured today: both resolve). `// location.replace("…")` and
  `var d = 'location.replace("…")'` are followed. Not a bypass — anyone who
  controls the page can redirect for real — but a small legitimate page carrying
  a commented-out redirect would be followed, if it were under 16KB with under
  200 visible characters.
- ~~The guard and the matcher disagree about `<nav>`, `<header>`, `<footer>`,
  `<aside>` and `<form>`~~ (attempt 4) — **CLOSED in round 5 by OB1 `58ef169`,
  and this entry was WRONG to carry it.** The guard no longer calls
  `extractTextFromHtml`; it measures the `liveText` produced by the same walk as
  the matcher, so the two cannot disagree about any element. Pinned by tests at
  `links.test.ts` — the five per-element tests plus the non-vacuity case
  `the same shape with NO text still resolves`. **Cited by test NAME, not by
  line**: this said `:487-506`, then `:487-506 as of bd9f1db`, and at `bd9f1db`
  that range is a different block entirely (the guard tests begin at 587 there).
  A stamp added to stop a citation relying on a reader's luck instead asserted a
  specific commit at which it is verifiably wrong. A test name survives every
  edit that a line number does not. Re-measured with a
  CONTROL: `<nav>`, `<header>`, `<footer>`, `<aside>` and `<form>` behave
  identically to `<div>` and `<span>` — empty shells resolve for all seven, and
  336 characters of text inside any of them returns `null`. The element is
  scenery.

  Left visible instead of deleted, because how it got here is the finding.
  Round 13 caught it. See the note under the method above.
- **The readahead figure is chunk-size AND sample-time dependent** (attempts 15
  and 17; re-measured here). The transport's readahead is ABSOLUTE rather than
  proportional to the body - 1.58 MiB at 16 KiB chunks, 1.69 at 64 KiB, 6.75 at
  256 KiB, the same whether the body is 16 MiB or 64 MiB - and it reads ~4%
  higher if sampled a few hundred ms later. At 1 MiB chunks the whole 16 MiB
  body arrives before the reader stops, so a server-side byte count stops
  discriminating entirely up there. It is recorded here as well as in the code
  because it is the measurement that refuted the first threshold this item
  shipped for that test, and Residuals is where a reader would look for it.
- **The stated cost "a numeric rightmost label on a genuinely public host would
  be refused" is vacuous** (attempt 6; measured today: `new URL("http://example.123/")`
  THROWS). The URL parser refuses those before the TLD rule is consulted. The
  underscored half of that claim is real; the numeric half is not.

**Fail-closed costs, measured rather than assumed.** From attempt 4: `<!-->` and
`<!--->` (legal empty comments, refused as unterminated), an unquoted attribute
containing an apostrophe (`class=don't`), and a malformed `<iframe/>` with no
closing tag. From attempt 7, which listed them "for completeness" and which this
section omitted until round 13 pointed it out — all still refusing at `b0cc0af`:
an `<svg>` broken out of by `<p>`, `<svg><foreignObject><meta>`,
`<svg><desc><meta>` and `<math><mtext><meta>` (both integration points),
`<svg><![CDATA[…]]>`, a `<select>` implicitly closed by `<textarea>`, and the
degenerate comments `<!-->` and `<!-- --!>`.

All of them are contrived for a tracker interstitial, all fail in the safe
direction, and each costs at most one unresolved wrapper — which is logged and
still researched. That last clause is why this whole list is a cost sheet rather
than a defect list.

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
deleting **every case** in `script-renderer.test.ts` and **every case** in
`links.test.ts` would print the same reassuring `54 -> 54`. (This carried a count
twice — 14, then 96 — and both went stale, the second inside the sentence added
to explain why counts about a moving suite go stale. Worse, a commit message
claimed this very fix had been made a round before it was: the patch that made it
aborted on a later error and never wrote the file, and I described the intent
rather than checking the diff. The third version carries no count, because the
true claim is "all of them" and that cannot rot.)

This matters twice over. A reader of that output would reasonably conclude the
new tests were run — they were not; they were run by hand
(`deno test --allow-net --allow-env`). And the protection the floor is meant to
give the `script-renderer.test.ts` suite, written after the 2026-09-04
five-defect run, does not exist.

Not fixed here: it is a gate change, not a link-resolver change, and it belongs
with the `crashloop` family of "reports green while checking nothing". The same
open item is already recorded in the `agent-harness-findings-note-audit` memory
as "Deno suite ungated by 5b AND 5c" — this is a second, independent sighting.
