# Daily podcast reliability - findings

Findings sink for harness item `podcast-script-fallback`. Every claim below was
re-checked on **2026-09-05** against a named artifact, and the check is written
next to the claim so the next reader can re-run it rather than trust it. Where a
claim cannot be checked from those artifacts, it says so.

An earlier draft of this note (branch `work/podfall`, 2026-09-04) was rebuilt
rather than merged - see section 6. Two of its claims did not survive re-checking
and are corrected here.

Artifacts used:

- **`/reports`** - the podcast's episode reports. Bind mount, host side `D:\_data`
  (`docker inspect openbrain-podcast --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'`).
- **The LiteLLM spend ledger** - `LiteLLM_SpendLogs` in `llm-gateway-db`
  (`docker exec llm-gateway-db psql -U litellm -d litellm`). `startTime` is
  **UTC**; the host clock is EDT (UTC-4).
- **The deployed Open Notebook image** and **the running `openbrain-podcast`**,
  read-only, via `docker exec`.

## 1. The fallback marker, episode by episode

`renderEpisode` substitutes `(script generation unavailable - grounded material
follows)` (with an em dash) plus the raw prompt block when the script LLM call
returns null. That string is a reliable marker of a degraded episode, because
nothing else writes it. Since this item it has one definition, exported as
`SCRIPT_UNAVAILABLE`.

    cd D:\_data
    grep -l "script generation unavailable" 0*-daily-*.md

| episode | report written (host mtime, EDT) | fallback marker | report size |
|---|---|---|---|
| 074 | 2026-08-20 02:06 | no | 10,722 B |
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
| 090 | 2026-09-04 15:48 | no | 11,732 B |
| 091 | 2026-09-05 01:44 | no | 9,165 B |

The anchor asked about **080-083**; all four took the fallback. The true range is
wider: **076 through 089, fourteen consecutive episodes, unbroken.** 075
(2026-08-21) is the last episode with a real script before the outage, which puts
the first failure on the night of the J.1 virtual-key flip. **090 is the first
recovered episode** - see section 4.

(There is one older, unrelated hit: `015-daily-tutorial-replace-your-2k-month-...`
from 2026-06. It predates this failure mode and is not attributed here.)

Two things follow that are worth stating plainly:

- **The episode filenames were the only visible symptom, and they are unreadable
  as one.** A degraded run also loses `primaryTopic`, whose heuristic fallback
  slugifies the first `[SOURCED]` sentence - which is why the degraded filenames
  read `083-daily-the-article-is-a-substack-post-by-nate-published-aug-28-2026`
  while the healthy ones on either side read `075-daily-ai-industry-news` and
  `091-daily-ai-in-education`. Nobody reads a filename as an alarm. That fallback
  now warns on its own line.
- **Report SIZE is not a usable signal either.** Episode 085's degraded report is
  5,109 B, *smaller* than healthy 075's 10,296 B. A threshold on file size would
  have missed it.

## 2. Episode 083: 47,296 chars -> a 21,822-token prompt -> the 5,000-token cap

**47,296 chars.** The `script` field of episode 083 is the marker line, a blank
line, and the prompt block: everything from the marker to the end of the file,
trailing newlines stripped. Counted in **characters**, not bytes - the file is
47,555 bytes but 47,480 characters, because the em dash and the box-drawing
characters are multi-byte in UTF-8. Reproduce with a UTF-8 read of
`083-daily-the-article-is-a-substack-post-by-nate-published-aug-28-2026.md`,
slicing from the index of the marker line and taking `len()` of the result with
trailing newlines removed: **47296**. Re-run 2026-09-05: 47296.

**21,822 tokens.**

    SELECT "startTime", model, prompt_tokens, completion_tokens, status
    FROM "LiteLLM_SpendLogs" WHERE prompt_tokens = 21822 ORDER BY 1;

    2026-07-27 15:11:56.096 | openai/qwen36-27b         | 21822 |   88 | success
    2026-08-29 05:35:51.460 | openai/qwen36-27b:nothink | 21822 | 5000 | success
    2026-08-29 05:38:56.501 | openai/qwen36-27b:nothink | 21822 | 5000 | success
    2026-08-29 05:41:49.717 | openai/qwen36-27b:nothink | 21822 | 5000 | success

**Correction to the 2026-09-04 draft**, which claimed *"no other row in the table
ever has"* that prompt size. A 2026-07-27 row does, with 88 completion tokens.
The claim that mattered - the three same-day rows at the cap - survives; the
absolute one did not, and it was the kind of "and nothing else" flourish that adds
nothing to the argument and can only ever be wrong.

Episode 083's report was written 01:29 EDT = 05:29 UTC; the three rows are 6, 9
and 12 minutes later, in the audio stage that follows. 47,296 chars / 21,822
tokens is 2.17 chars per token, which is what a tag-dense English prompt costs on
this tokenizer. **Marked as inference, not proof:** the arithmetic and the timing
are consistent with these three rows being the transcript segments built from that
dump, but the ledger does not store prompt text, so the identity is not
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

`LiteLLM_SpendLogs.metadata` records the key the caller presented. The pre-fix
`makeScriptChat` sent the literal placeholder `not-needed`:

    SELECT "startTime", metadata->'error_information'->>'error_code',
           left(metadata->'error_information'->>'error_message', 120)
    FROM "LiteLLM_SpendLogs" WHERE metadata->>'user_api_key' = 'not-needed';

    401 | 401: LiteLLM Virtual Key expected. Received=not-****eded, expected to start with 'sk-'.

(The gateway masks the value it received; the note quotes the masked form.)

On 2026-08-29 three such rows land at `05:29:38` UTC with `prompt_tokens=0` and
`status=failure` - rejected at the door before any inference, nine seconds after
episode 083's report was written.

This is the strongest single piece of evidence in the file: the ledger names the
placeholder string from the source, so the link from "the code sent
`Bearer not-needed`" to "the episode degraded" is not reconstructed, it is logged.

## 4. The outage is OVER, and the ledger says when

**Correction to the 2026-09-04 draft**, whose section 4 read "The fallback is
still happening. The fix is not deployed." That was true when written and is not
true now. Both gates it named have since been passed, and the artifacts show it.

Daily count of rows presenting the placeholder key:

    SELECT date_trunc('day',"startTime")::date, count(*) FROM "LiteLLM_SpendLogs"
    WHERE metadata->>'user_api_key' = 'not-needed' GROUP BY 1 ORDER BY 1;

    ... 2026-08-31 | 4    2026-09-02 | 18    2026-09-03 | 11
        2026-09-04 | 14   2026-09-05 | (no row)

`max("startTime")` is `2026-09-05 06:29:05` UTC, so the ledger is live and today's
zero is an absence, not a gap in collection.

The mechanism, matching [`deno-recipe-bind-mount-delivery-gap.md`](deno-recipe-bind-mount-delivery-gap.md):

- `openbrain-podcast` bind-mounts `D:\Open WebUI\ai-stack\OB1\recipes\daily-digest`
  as `/app` and runs `deno run` as PID 1, so it imports the entry module once at
  process start. Moving the submodule changes the file, not the running module.
- The container's `StartedAt` is `2026-09-04T19:09:16Z` = **15:09 EDT**.
- Episode 090 was written at **15:48 EDT**, 39 minutes later, and is the first
  episode since 075 with a real script.
- The live code is the fixed code:
  `docker exec openbrain-podcast grep -n CHAT_API_KEY /app/src/podcast/script-renderer.ts`
  gives `115:  const apiKey = cfg.apiKey || Deno.env.get("CHAT_API_KEY") || "";`

**The general point, which is why this section is kept rather than deleted:** a
merge is not a deploy for this service, and the note that said so was right to say
so. The proof of deploy is a ledger day with zero placeholder rows plus an episode
without the marker - not a green gate.

## 5. What this item changed, and what it deliberately did not

Changed in `OB1/recipes/daily-digest/src/podcast/script-renderer.ts`:

- **`renderEpisode` emits a DEGRADED line** on the fallback path, naming the
  stage, the episode, the SIZE of the dump (it becomes the transcript prompt
  downstream - that is the number that killed 083) and the cause carried out of
  `chat()`. Observed, forcing a 401 against a stub gateway:

      [script] DEGRADED: episode 092 "Daily #092 - ..." is shipping RAW GROUNDED
      MATERIAL instead of a written script (206 chars, which becomes the transcript
      prompt downstream). Stage: script generation (renderEpisode/S4a).
      Cause: HTTP 401 after 1 attempt(s)

- **`primaryTopic` warns on its own heuristic fallback**, so the sentence-shaped
  filename has a log line to go with it.
- **`makeScriptChat` records WHY its last call returned null**, and any call that
  returns text CLEARS it, so a later degrade can never quote a stale cause.
  `failureReason(chat)` reads it through the optional `ChatFnWithReason` interface
  and degrades to `"reason not recorded by this chat function"` for a hand-rolled
  `ChatFn`, so the `ChatFn` contract is unchanged and every stub keeps working.
  Without it a degrading caller could only say "the LLM returned null", which is
  the uninformative line this item exists to replace.

The raw-material fallback itself is still deliberate: a degraded episode ships,
because a degraded episode beats no episode. It is now audible.

**The classifier split was not disturbed.** `salvageTruncated` defaults FALSE
because a truncated classifier reply is worse than none - it still parses.
Recording a reason is the only change on that path; the return value is still
null. Verified by driving the real `isPromoBody` through a real `makeScriptChat`
configured as `bodyClassifyChat` is, against a gateway that truncates at
`max_tokens`: verdict **KEEP** (the safe default), with
`lastFailure = "TRUNCATED at max_tokens=32 with the ceiling (32) reached"`.

**The gap-dive triage path was left alone, deliberately.** The anchor allows this
if the reason is written down.
`OB1/recipes/daily-digest/src/enrich/gap-dive.ts:198` already warns:

    [gap-dive] triage LLM returned null (call failed/timed out); N candidate(s) left unscored.

That predates this work - it came in with the feature, not with the 2026-08-29
fix - and it already names the stage and the consequence. Since the sibling item
the underlying cause arrives immediately above it on its own line, because
`link-enrich.ts:158` gives that chat `label: "gap-triage"`. Adding
`failureReason()` there would inline the same text one line lower and change no
outcome, so it was not touched. If it is ever revisited, the cheap version is the
same one-line `failureReason(chat)` interpolation.

**Not touched, still open (out of scope by the anchor):**

- `podcast_creator`'s 5,000-token transcript cap. A real headroom problem; it
  lives in a vendored patch overlay and is its own decision.
- *Why* `chat()` returns null when the key is right (timeout vs prompt size).
  This item makes the failure visible; diagnosing it needs that visibility first.

## 6. Process findings

### A stale branch was REBUILT, not merged (2026-09-05)

`work/podfall` sat 33 commits behind its line with an OB1 pin four merged items
old. The two lineages had each restructured `makeScriptChat`'s internals and each
added a `giveUp` helper - a **semantic** conflict that a textual merge would have
resolved into something neither side had tested. The branch was reset to the line
tip, its OB1 re-provisioned at the current pin, and the anchor's *intent*
re-implemented against the file as it stands. The old diff was discarded; this
note is the part of it that was worth keeping, corrected.

The re-assessment mattered: the sibling item had already satisfied one of the four
acceptance criteria outright (gap-dive) and most of a second (the evidence chain,
recorded in [`daily-podcast-delivery-findings.md`](daily-podcast-delivery-findings.md)),
so the work actually remaining was smaller and more sharply defined than the
original diff. Re-deriving it from the anchor cost less than reconciling the diff
would have.

### `deno check` passed a log line that printed its own source (2026-09-05)

The first cut of `primaryTopic`'s warning built its cause by concatenating a
template literal that ended in a dollar-brace with the ternary and a closing
brace. That is valid TypeScript, and it is not an interpolation - it emitted the
literal *source text* of the ternary into the log:

    ... Stage: primary topic (primaryTopic). Cause:  +
            (headlines ? failureReason(chat) : "no segment headlines to name a topic from") +

`deno check` was clean and the test passed, because the test asserted
`line.includes("primaryTopic")` - the stage word, which was rendered - rather than
the cause, which was not. **The assertion has to name the thing a reader has to be
able to read**, not a nearby token that happens to survive. Two assertions were
added: the line ends with the expected cause, and no emitted line contains an
unrendered dollar-brace.

Same family as the defects in
[`daily-podcast-delivery-findings.md`](daily-podcast-delivery-findings.md), section
"Three rounds, three defects the unit suite could not see": a green check that
checks something adjacent to the goal.

### Neither hook gate can see the tests this item added (2026-09-05)

The gitlink bump ran both OB1 gates and both went green, and neither one looked at
`script-renderer.test.ts`:

- **5b `check-ob1-recipe-tests`** globs `*.test.mjs` and runs `node --test`. This
  recipe is Deno TypeScript, so its suite is not in the set. The gate reported
  `test FILES 8 -> 8, test CASES 48 -> 48` across the bump that took this file from
  24 cases to 34: those 48 belong to other recipes entirely. **The shrink floor
  cannot detect a revert of this suite**, because it has never counted it.
- **5c `check-ob1-deno-recipes`** does cover this recipe, but `deno check` only,
  and it excludes `*.test.ts` explicitly (`check-ob1-deno-recipes.ps1:198`) for a
  stated reason - the remote `jsr:` import. Its own output says so: "Type check
  only - the test suite is NOT run."

Both limitations are DOCUMENTED by the gates' own headers, so this is a known hole
rather than a new defect - and 5c closed the half that mattered most (an unbound
identifier now fails at commit time). It is recorded here because of what it means
for anyone verifying this item: **a green hook chain says nothing about these 34
cases.** Running `deno test --allow-net --allow-env src/podcast/script-renderer.test.ts`
by hand is the only thing that does, and it is step 1 of the test plan for that
reason.

## 7. Adjacent, verified elsewhere, not acted on

`recipes/daily-digest/src/clients/llm.ts` carries the same shape of latent trap -
a `"no-key"` bearer default behind `env("LOCAL_LLM_BEARER", "no-key")` - which is
**not** firing, because the env var is set. Checked and written up in
[`daily-podcast-delivery-findings.md`](daily-podcast-delivery-findings.md) by the
sibling item `podcast-delivery-key`; not independently re-verified here, and
cross-referenced rather than duplicated.
