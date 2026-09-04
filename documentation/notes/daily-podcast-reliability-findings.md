# Daily podcast reliability - findings (2026-08-29 incident, recorded 2026-09-04)

Findings sink for harness item `podcast-script-fallback`. Every claim below was
checked on 2026-09-04 against a named artifact, and the check is written next to
the claim so the next reader can re-run it rather than trust it. Where a claim
could not be checked, it says so.

Artifacts used:

- **`/reports`** - the podcast's episode reports. Bind mount, host side
  `D:\_data` (`docker inspect openbrain-podcast --format '{{range .Mounts}}...'`).
- **The LiteLLM spend ledger** - `LiteLLM_SpendLogs` in `llm-gateway-db`
  (`docker exec llm-gateway-db psql -U litellm -d litellm`). `startTime` is
  **UTC**: `max(startTime)` read `14:16` while the host clock read `10:16 EDT`.
- **The deployed Open Notebook image** - `docker exec open_notebook ...`,
  read-only.

## 1. The fallback marker, episode by episode

`renderEpisode` substitutes `(script generation unavailable - grounded material
follows)` plus the raw prompt block when the script LLM call returns null. That
string is a reliable marker of a degraded episode, because nothing else writes it.

    cd D:\_data
    grep -l "script generation unavailable" 0*-daily-*.md

| episode | report written (host mtime, EDT) | fallback marker | report size |
|---|---|---|---|
| 075 | 2026-08-21 02:31 | no | 10,296 B |
| 076 | 2026-08-22 01:53 | **yes** | 19,983 B |
| 077 | 2026-08-23 01:20 | **yes** | 18,444 B |
| 078 | 2026-08-24 01:11 | **yes** | 14,945 B |
| 079 | 2026-08-25 01:24 | **yes** | 38,128 B |
| 080 | 2026-08-26 01:09 | **yes** | 13,865 B |
| 081 | 2026-08-27 01:06 | **yes** | 10,452 B |
| 082 | 2026-08-28 01:21 | **yes** | 37,612 B |
| 083 | 2026-08-29 01:29 | **yes** | 47,555 B |
| 084 | 2026-08-30 01:08 | **yes** | 11,241 B |
| 085 | 2026-08-31 01:04 | **yes** | 5,109 B |
| 086 | 2026-09-01 20:41 | **yes** | 23,609 B |
| 087 | 2026-09-02 01:18 | **yes** | 24,502 B |
| 088 | 2026-09-03 01:15 | **yes** | 23,202 B |
| 089 | 2026-09-04 01:23 | **yes** | 41,559 B |

The anchor asked about **080-083**; all four took the fallback. The true range is
wider: **076 through 089, fourteen consecutive episodes, unbroken.** 075
(2026-08-21) is the last episode with a real script, which puts the first failure
on the night of the J.1 virtual-key flip.

Two things follow that are worth stating plainly:

- **This is not a historical incident.** Episode 089 was written this morning and
  carries the marker. See section 4.
- **The episode filenames were the only visible symptom, and they are unreadable
  as one.** A degraded run also loses `primaryTopic`, whose heuristic fallback
  slugifies the first `[SOURCED]` sentence - which is why the last fourteen
  filenames read `the-article-is-a-substack-post-by-nate-published-aug-28-2026`
  and the healthy ones read `ai-industry-news`. Nobody reads a filename as an
  alarm.

## 2. Episode 083: 47,296 chars -> a 21,822-token prompt -> the 5,000-token cap

**47,296 chars.** The `script` field of episode 083 is the marker line, a blank
line, and the prompt block: everything from the marker to the end of the file,
trailing newline stripped. Counted in **characters**, not bytes - the file is
47,555 bytes but 47,480 characters, because the em dash and the box-drawing
characters are multi-byte in UTF-8. Reproduce with a UTF-8 read of
`083-daily-the-article-is-a-substack-post-by-nate-published-aug-28-2026.md`,
slicing from the index of the marker line and taking `len()` of the result with
trailing newlines removed: **47296**.

**21,822 tokens.** Three ledger rows on 2026-08-29 carry `prompt_tokens=21822`,
and no other row in the table ever has:

    SELECT "startTime", model, prompt_tokens, completion_tokens, status
    FROM "LiteLLM_SpendLogs" WHERE prompt_tokens = 21822;

    2026-08-29 05:35:51.460 | openai/qwen36-27b:nothink | 21822 | 5000 | success
    2026-08-29 05:38:56.501 | openai/qwen36-27b:nothink | 21822 | 5000 | success
    2026-08-29 05:41:49.717 | openai/qwen36-27b:nothink | 21822 | 5000 | success

Episode 083's report was written 01:29 EDT = 05:29 UTC; these are 6, 9 and 12
minutes later, in the audio stage that follows. 47,296 chars / 21,822 tokens is
2.17 chars per token, which is what a tag-dense English prompt costs on this
tokenizer. **Marked as inference, not proof:** the arithmetic and the timing are
consistent with these three rows being the transcript segments built from that
script, but the ledger does not store the prompt text, so the identity is not
demonstrable from these artifacts alone.

**The 5,000-token cap.** `completion_tokens` is exactly 5000 on all three - not
near it, *at* it. The cap is in the deployed image, not inferred:

    docker exec open_notebook grep -n max_tokens \
      /app/.venv/lib/python3.12/site-packages/podcast_creator/nodes.py

    35:        "max_tokens": 3000,
    135:    merged_config = {"max_tokens": 5000, **transcript_config}

Line 135 is the transcript stage. Three segments each truncated at the ceiling is
what killed the episode.

## 3. The origin, from the ledger's own metadata

`LiteLLM_SpendLogs.metadata` records the key the caller presented. Searching for
the literal placeholder the pre-fix `makeScriptChat` sent:

    SELECT "startTime", model,
           metadata->'error_information'->>'error_code',
           left(metadata->'error_information'->>'error_message', 120)
    FROM "LiteLLM_SpendLogs" WHERE metadata->>'user_api_key' = 'not-needed';

    401 | 401: LiteLLM Virtual Key expected. Received=not-****eded, expected to start with 'sk-'.

(The gateway masks the value it received; the note quotes the masked form.)

On 2026-08-29 three such rows land at `05:29:38.534`, `.567` and `.597` -
`prompt_tokens=0`, `status=failure`, raised from
`litellm/proxy/auth/user_api_key_auth.py:1347`, i.e. rejected at the door before
any inference. That is nine seconds after episode 083's report was written.

This is the strongest single piece of evidence in the file: the ledger names the
placeholder string from the source, so the link from "the code sent
`Bearer not-needed`" to "the episode degraded" is not reconstructed, it is logged.

## 4. The fallback is still happening. The fix is not deployed.

The 2026-08-29 fix (OB1 `85c5be8`, "daily-digest: authenticate the podcast chat
calls, and retry instead of degrading") is on a work branch. It is **not** in the
OB1 commit the main checkout has loaded:

    cd "D:\Open WebUI\ai-stack\OB1" && git log --oneline -1
    5224928 fix(wiki-pages): warnOnce is once per KIND, not once per process

    grep -n "not-needed" OB1/recipes/daily-digest/src/podcast/script-renderer.ts
    75:  headers: { Authorization: `Bearer ${cfg.apiKey ?? "not-needed"}`, ... }

`openbrain-podcast` bind-mounts that directory as `/app`, so the live code is the
placeholder version, and the ledger agrees - rows with
`metadata->>'user_api_key' = 'not-needed'` are still arriving every day:

    2026-09-02 | 18    2026-09-03 | 11    2026-09-04 | 14

Today's cluster at `05:23:44.066/.108/.126` UTC is the run that produced episode
089 at 01:23 EDT. (Daily totals before 2026-08-23 are in the thousands; those
predate the placeholder being isolated to this path and are **not** attributed
here to the podcast.)

**Two separate gates stand between this branch and a fixed podcast**, and only the
first is a merge:

1. merging `podcast-delivery-key` and this item, and
2. **recreating the container.** Per
   [`deno-recipe-bind-mount-delivery-gap.md`](deno-recipe-bind-mount-delivery-gap.md),
   `openbrain-podcast` is `deno run podcast-server.ts` as PID 1 against a bind
   mount: it imports the entry module once at process start. Moving the submodule
   changes the file and not the running module. `docker compose ... up -d
   --force-recreate openbrain-podcast` is the deploy step, and the proof is a
   ledger day with zero `not-needed` rows.

Neither is in scope for this item, which only makes the failure audible. They are
recorded here so that a merge is not mistaken for the fix.

## 5. What this item changed, and what it deliberately did not

Changed in `OB1/recipes/daily-digest/src/podcast/script-renderer.ts`:

- `renderEpisode` now emits, on the fallback path, a line naming the stage, the
  episode, the size of the dump, and the cause carried out of `chat()`. Observed
  output, forcing a 401 against a stub gateway:

      [script] DEGRADED: episode 083 "Daily #083 - ..." is shipping raw grounded
      material instead of a written script (200 chars, which becomes the transcript
      prompt downstream). Stage: script generation (renderEpisode/S4a).
      Cause: HTTP 401 (giving up after 1): Authentication Error, LiteLLM Virtual Key expected

- `primaryTopic` warns on its own heuristic fallback, so the sentence-shaped
  filename has a log line to go with it.
- `makeScriptChat` records the reason it last returned null; `failureReason()`
  reads it and degrades safely to `"reason not recorded by this chat function"`
  for a hand-rolled `ChatFn`. Without it, every degrading call site could only say
  "the LLM returned null", which is the uninformative line the whole item exists
  to replace.

**The gap-dive triage path was left alone, deliberately.** The anchor allows this
if the reason is written down.
`OB1/recipes/daily-digest/src/enrich/gap-dive.ts:198` already warns:

    [gap-dive] triage LLM returned null (call failed/timed out); N candidate(s) left unscored.

That predates this work - it came in with the feature (`364b45a`), not with the
2026-08-29 fix - and it already names the stage and the consequence. Since
2026-08-29 the underlying cause arrives immediately above it on its own line,
because `link-enrich.ts` gives that chat a `label: "gap-triage"`. Adding
`failureReason()` there would inline the same text one line lower and change no
outcome, so it was not touched. If it is ever revisited, the cheap version is the
same one-line `${failureReason(chat)}` interpolation.

**Not touched, still open (out of scope by the anchor):**

- `podcast_creator`'s 5,000-token transcript cap. A real headroom problem; it
  lives in a vendored patch overlay and is its own decision.
- *Why* `chat()` returns null when the key is right (timeout vs prompt size). This
  item makes the failure visible; diagnosing it needs that visibility first.

## 6. Adjacent, verified elsewhere, not acted on

`recipes/daily-digest/src/clients/llm.ts` carries the same shape of latent trap -
a `"no-key"` bearer default behind `env("LOCAL_LLM_BEARER", "no-key")` - which is
**not** firing, because the env var is set. Checked and written up in
[`daily-podcast-delivery-findings.md`](daily-podcast-delivery-findings.md) by the
sibling item `podcast-delivery-key`; not independently re-verified here, and
cross-referenced rather than duplicated.
