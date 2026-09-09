# Test plan — `podlinks` (redirect-shell resolution)

Anchor: `queue.ps1 -Show -Id podlinks`.
Branch: `work/podlinks` (parent) + `work/podlinks` in the OB1 submodule (`df83254`).

**What changed, in one line:** a tracker URL that answers 200 with a redirect
shell is now followed like any other hop, and a wrapper that could NOT be
resolved is marked and logged instead of disappearing through the
newsletter-self-link filter.

Everything below runs from
`<worktree>/OB1/recipes/daily-digest` unless a case says otherwise. `deno` is on
PATH; nothing needs the stack up except T7, which says so.

---

## ATTEMPT 2 — what the first round found

Attempt 1 PASSED every case and the tester withheld `-PlanAdequate`, then wrote up
two defects no case here would ever have surfaced. Both are fixed; both are now
cases.

| Finding | Fix | New case |
|---|---|---|
| F3: marking an unresolved wrapper was right, but FILTERING IT OUT of research lost links the pre-fix code kept — `isRedirectWrapper` matches bare substrings (`click.`, `links.`, `email.`, `trk.`) against the whole URL, so a genuine article can trip it; and a real tracker whose unwrap merely TIMED OUT was dropped where `extract.ts` used to follow it at fetch time | keep the candidate, keep the log line | T11 |
| F2: `scriptedLocationTarget` ran over raw HTML with no script context — it read `<div data-location = "eu-west">` and `window.analytics.location = "…"` as client-side redirects | search only inside `<script>` elements, and require a real global | T12 |
| F4: `decodeSubstackRedirect` cited at links.ts:184; it is at 188 (184 is `NOISE_TEXT_RE`) | corrected, and both citations now name the SHA they are relative to | T10 |

The tester also MEASURED F3's incidence across 95 historical link reports and got
zero. That is real and it is **not** why the fix is safe — the population moves
once wrappers resolve again, so a count taken before the fix does not predict
behaviour after it. Do not let a zero stand in for the argument.

## T1 — the suite is green

```
deno test --allow-net --allow-env src/enrich/links.test.ts
```

PASS: 19 passed, 0 failed.
FAIL: any failure, or fewer than 19 tests (a case was deleted rather than fixed).

## T2 — the pre-existing suite did not regress

`extract.ts` and `types.ts` were touched, so the older suite has to still hold.

```
deno test --allow-net --allow-env src/podcast/script-renderer.test.ts
```

PASS: 34 passed, 0 failed.

## T3 — type-check, because `deno run` does not

This is the defect class that shipped an unimported `retryUntil` on 2026-09-04.

```
deno check src/enrich/links.ts src/enrich/links.test.ts src/enrich/extract.ts \
           src/enrich/types.ts link-enrich.ts src/podcast/script-renderer.ts \
           send-digest.ts podcast-server.ts
```

PASS: every file reports `Check`, no diagnostics.

## T4 — THE CASE THAT MATTERS: prove it is RED without the fix

A test that passes on the new code proves nothing on its own. Do this one by
hand; it is the case that decides whether the change is the thing that fixes it.

1. From `<worktree>/OB1`, get the pre-fix file:
   `git show HEAD~1:recipes/daily-digest/src/enrich/links.ts > /tmp/links.orig.ts`
2. Copy `recipes/daily-digest/src/enrich/links.ts` somewhere safe.
3. Save this as `redproof.ts` in `recipes/daily-digest` — it imports ONLY
   `unwrapRedirect`, which exists in both versions, so the same script runs
   against either:

```ts
Deno.env.set("FETCH_PROXY_URL", "");
const { unwrapRedirect } = await import("./src/enrich/links.ts");
const ac1 = new AbortController(), ac2 = new AbortController();
const dest = Deno.serve({ port: 0, signal: ac1.signal, onListen: () => {} },
  () => new Response("<html><body>the article</body></html>", { headers: { "content-type": "text/html" } }));
const destBase = `http://127.0.0.1:${(dest.addr as Deno.NetAddr).port}`;
const target = `${destBase}/p/the-post?utm_source=substack&utm_medium=email`;
const shell = `<head><noscript><META http-equiv="refresh" content="0;URL=${target.replace(/&/g, "&#38;")}"></noscript>` +
  `<title>x</title></head><script>window.opener = null; location.replace("${target}")</script>`;
const wrap = Deno.serve({ port: 0, signal: ac2.signal, onListen: () => {} },
  () => new Response(shell, { headers: { "content-type": "text/html" } }));
const wrapUrl = `http://127.0.0.1:${(wrap.addr as Deno.NetAddr).port}/redirect/40874a4f?j=e`;
const got = await unwrapRedirect(wrapUrl);
console.log("resolved :", got);
console.log(got === target ? "FOLLOWED (fix present)" : "STOPPED at the wrapper (regression present)");
ac1.abort(); ac2.abort();
await dest.finished.catch(() => {}); await wrap.finished.catch(() => {});
```

4. `cp /tmp/links.orig.ts src/enrich/links.ts` then `deno run -A redproof.ts`
5. Restore the patched `links.ts`, then `deno run -A redproof.ts` again.
6. Delete `redproof.ts` and confirm `git status` in OB1 is clean.

PASS: step 4 prints **STOPPED at the wrapper**, step 5 prints **FOLLOWED**.
FAIL: step 4 already prints FOLLOWED — then the old code was not actually
restored, or the shell fixture is not exercising the changed path, and T1 is
not evidence of anything.

## T5 — a real article is not treated as a redirect

The risk this change introduces is over-eager following: an article page that
mentions `location.replace`, or carries a meta refresh, must be left alone.
T1 covers this with `an article containing location.replace is NOT followed`
and `a large page with a head meta-refresh is not treated as a shell`.

Verify by attacking it: edit the article fixture in `links.test.ts` to make the
document SMALLER (drop the `.repeat(40)` bodies to `.repeat(1)`) and re-run.

PASS: the test now FAILS — a small, near-empty document with a
`location.replace` IS a shell by this design, which is the intended trade-off.
FAIL: it still passes, which would mean the size/text conditions are not
actually load-bearing and the guard is decorative.

Restore the fixture afterwards.

## T6 — the hop cap terminates

Covered by `a chain of interstitials stops at maxHops` and `a shell that
refreshes to ITSELF terminates immediately`. Confirm by reading the assertions:
the first asserts `hits() === 3` for `maxHops: 3` (not merely that it returned),
the second asserts `hits() === 1`.

FAIL: if either only asserts the return value, an infinite loop would pass.

## T7 — LIVE, through the real egress (needs the stack up)

Direct HTTP and proxied HTTP can behave differently, and the pipeline only ever
uses the proxied path. `openbrain-podcast` must be running.

1. `docker exec openbrain-podcast mkdir -p /tmp/pl/src`
2. `docker cp src/enrich openbrain-podcast:/tmp/pl/src/enrich`
3. Save as `/tmp/pl/live.ts` in the container (`docker cp` a local file):

```ts
import { gatherAnchors } from "./src/enrich/links.ts";
console.log("proxy =", Deno.env.get("FETCH_PROXY_URL"));
const urls = [
  "https://substack.com/redirect/40874a4f-3ed1-44f8-82bd-1ea1c10c30b4?j=e",
  "https://substack.com/redirect/f71bee37-ff3b-4634-9208-d93788e2b15b?j=e",
];
for (const c of await gatherAnchors(urls.map((url) => ({ url, text: "Read the full piece" })))) {
  console.log(c.domain, "unresolved=" + (c.unresolvedWrapper ?? false), c.url);
}
```

4. `docker exec -w /tmp/pl openbrain-podcast deno run -A --unstable-net --quiet live.ts`
5. `docker exec openbrain-podcast rm -rf /tmp/pl`

PASS: `proxy = http://vpn:8888`, and both candidates come back with a domain
that is NOT `substack.com` and `unresolved=false`. On 2026-09-09 these resolved
to `blog.google` and `aiandeducation.mit.edu`.
FAIL: either candidate still shows `substack.com`, or `unresolved=true`.

NOTE — these are live third-party URLs. If Substack changes the endpoint again
between now and the test, a `substack.com` result here is NOT automatically a
code failure: check what the endpoint returns
(`docker exec openbrain-podcast deno eval ...` a manual fetch) before failing
this case, and say in the result which it was. Do NOT write the change off on a
single ambiguous run.

## T8 — you must NOT write to the live checkout

`/app` in `openbrain-podcast` is a bind mount of the MAIN checkout's
`OB1/recipes/daily-digest`. Editing it is a deploy, not a test.

PASS: after T7, `git status` in the MAIN checkout's `OB1` is unchanged, and the
container work happened under `/tmp`.

## T9 — the log line an operator would actually see

The unresolved-wrapper warning is the thing that would have made this failure
visible on day one, so it has to be reachable, not just present in source.

Read `link-enrich.ts` around the `gatherAnchors` call and confirm the
`⚠ unresolved redirect wrapper` line is inside the same loop that feeds
`external`, and that `external` filters on `!c.unresolvedWrapper`.

FAIL: if the log line can be reached for a candidate that is nonetheless still
researched, or if it is unreachable because the filter drops the candidate first.

## T10 — claims in the commit message and the findings note

Both are held to the artifact's standard.

- The commit message says "34 pre-existing tests still green" — check the number
  against T2's actual output.
- `documentation/notes/podcast-audio-outage-2026-09-09.md` section 2 states the
  cause. Its `links.ts:145-179` line reference was taken against the PRE-fix
  file; confirm it still points at `unwrapRedirect` in whichever version the
  note is read against, or that the note says which version it means.

FAIL: any figure that does not reproduce from the command that allegedly
produced it.

---

## T11 — an unresolved wrapper is SAID, not dropped (the F3 regression)

The item's goal is that more links get researched. Attempt 1 had a path that
researched fewer. This case exists so that path cannot come back.

Read `link-enrich.ts` at the `gatherAnchors` call site. The filter must be
`c.domain && !c.domain.endsWith("substack.com")` — `unresolvedWrapper` must NOT
appear in it. The log line must still fire for marked candidates.

Then prove it behaviourally rather than by reading:

```
deno test --allow-net --allow-env --filter "marked but NOT dropped" src/enrich/links.test.ts
```

Now attack it: edit `link-enrich.ts`'s filter to add `&& !c.unresolvedWrapper`
back, and confirm the test in `links.test.ts` that asserts the filter expression
FAILS. (It asserts against a copy of the expression, so if it still passes, the
test is not actually pinned to the shipping filter — that is a finding.) Restore
the file and confirm `git status` is clean.

PASS: the filter does not mention the mark; the test passes; re-adding the drop
makes a test fail.
FAIL: the mark is back in the filter, or nothing fails when you put it back.

## T12 — a page that is not trying to redirect is not followed (the F2 regression)

```
deno test --allow-net --allow-env --filter "NOT a scripted redirect" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "OUTSIDE any script" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "IS still followed" src/enrich/links.test.ts
```

PASS: all three green. The third is the one that stops the fix from being a
blanket disable — a real `window.location.replace` inside a `<script>` must still
resolve, which is what the whole item depends on.

Then try to break it yourself. Write a document that is a redirect shell by every
other measure (tiny, no visible text) and get `interstitialTarget` to follow
something from a context that is not a script — an inline event handler
(`onclick="location.href='…'"`), a `<template>`, an HTML comment, an attribute
whose name merely ends in `location`. Report anything that resolves.

FAIL: any non-script context that still steers the resolver.

## Out of scope for this plan

Episode length, the TTS/netns outage, the promo filter, gap-dive, and sysadmin
alerting for research yield (that is the `crashloop` item). A finding in any of
those is real and goes to
`documentation/notes/podcast-audio-outage-2026-09-09.md`, not into this patch.
