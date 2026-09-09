# Test plan — `podlinks` (redirect-shell resolution)

Anchor: `queue.ps1 -Show -Id podlinks`.
Branch: `work/podlinks` (parent) + `work/podlinks` in the OB1 submodule (`8becc14`).

**What changed, in one line:** a tracker URL that answers 200 with a redirect
shell is now followed like any other hop, and a wrapper that could NOT be
resolved is marked and logged instead of disappearing through the
newsletter-self-link filter.

Everything below runs from
`<worktree>/OB1/recipes/daily-digest` unless a case says otherwise. `deno` is on
PATH; nothing needs the stack up except T7, which says so.

---

## ATTEMPT 3 — what round 2 found

Round 2 FAILED (T5, T9, T10, T11, T12). Read this before the cases; four of those
five were defects in THIS PLAN or in my test, not in the resolver, and the plan
you are holding has been corrected for each.

- **T11 was decorative.** The tester put the F3 regression back into the real
  filter and the whole suite stayed green — the test asserted a hand-typed COPY,
  and `link-enrich.ts` is a top-level script no test can import. The rule now
  lives in exported `isResearchable()`; T11 now demands you prove the attack
  bites.
- **T12 was half a fix.** `metaRefreshTarget` had never been narrowed and runs
  FIRST, so seven inert contexts still steered the resolver. Worse, the
  visible-text guard strips comments before counting, so a mostly-commented
  document read as a shell to the guard and as a redirect to the matcher. Both
  now see the same document.
- **The narrowing had broken real shapes**: the prefix class omitted `)` and `>`,
  so minified `setTimeout(()=>location.replace(…))` and `if(!a)location.href=…`
  stopped resolving. Fixed with a negative lookbehind.
- **T4 said `HEAD~1`**, which by attempt 2 was already a fixed version — following
  it literally met the case's own FAIL clause. Now pinned to `970cae8`.
- **T9 contradicted T11** outright: it required the filter to consult
  `unresolvedWrapper`, which T11 forbids. My error; corrected.
- **T5's inference was wrong** — its fixture is rejected on context, not on the
  size/text guards it claimed to be testing.
- **T10 caught a false claim in my commit message**: "five new tests, all of which
  fail on attempt 1's code" — only three of five do.

## What round 1 found (still relevant)

Attempt 1 PASSED every case and its tester withheld `-PlanAdequate`, then wrote up
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

PASS: 33 passed, 0 failed.
FAIL: any failure, or fewer than 33 tests (a case was deleted rather than fixed).

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
   `git show 970cae8:recipes/daily-digest/src/enrich/links.ts > /tmp/links.orig.ts`

   **Use the SHA, not `HEAD~n`.** This plan said `HEAD~1` through attempt 2, by
   which point `HEAD~1` was the *first* fix — which already resolves
   interstitials, so following the plan literally printed FOLLOWED at step 4 and
   met the plan's own FAIL clause. `970cae8` is the pre-fix commit and stays
   correct however many attempts this item takes.
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

CORRECTED for attempt 3. Through attempt 2 this case said: shrink the article
fixture's `.repeat(40)` bodies to `.repeat(1)` and the test should then FAIL,
proving the size/text guards are load-bearing. A tester ran it, it did NOT fail,
and the plan's inference ("then the guard is decorative") was WRONG — the fixture
puts its `location.replace` inside `<pre><code>`, which is not a `<script>`, so
the change rejects it on CONTEXT before size or text is ever consulted. The case
was attributing the rejection to the wrong guard.

So attack the size and text guards where they are actually the deciding factor —
put the redirect in a REAL script and vary only the document:

```ts
// a) shell-sized, no visible text  -> followed
`<html><head><script>location.replace("https://good.example/a")</script></head></html>`
// b) same script, 300+ chars of visible prose in the body -> NOT followed
// c) same script, body padded past 16KB                   -> NOT followed
```

PASS: (a) resolves, (b) and (c) return null. That isolates each guard: (b) can
only be the text limit, (c) can only be the byte cap.
FAIL: (b) or (c) still resolves — then that guard is decorative.

Do NOT conclude anything about the guards from the `<pre><code>` fixture; it is
testing the context rule (T12), not these.

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

CORRECTED for attempt 3. This case previously required `external` to filter on
`!c.unresolvedWrapper` — which is the exact regression T11 forbids. The two cases
contradicted each other and a tester caught it; whichever way the code went, one
of them had to fail. That was my error in revising the plan, not a defect in the
change.

The warning is the thing that would have made the outage visible on day one, so
it must be REACHABLE — and it must be the only consequence of the mark.

Read `link-enrich.ts` around the `gatherAnchors` call and confirm:
- the `⚠ unresolved redirect wrapper` line runs over the candidates BEFORE any
  filtering, so a marked candidate always reaches it;
- `external` is `gathered.filter(isResearchable)` and `isResearchable` does NOT
  consult `unresolvedWrapper` (that is T11's job to prove behaviourally).

PASS: a marked candidate is both LOGGED and RESEARCHED.
FAIL: the log line is unreachable, or the mark changes what gets researched.

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
researched fewer, and attempt 2's guard against it DID NOT WORK: a tester put the
regression back and the whole suite stayed green, because the test asserted a
hand-typed COPY of the filter and `link-enrich.ts` is a top-level script no test
can import.

The rule now lives in `isResearchable()` in `links.ts`. Do the attack, and this
time it must bite:

1. In `src/enrich/links.ts`, add `if (c.unresolvedWrapper) return false;` to
   `isResearchable`.
2. `deno test --allow-net --allow-env src/enrich/links.test.ts`
3. Restore the file; confirm `git status` in OB1 is clean.

PASS: step 2 FAILS, naming `isResearchable: a marked wrapper is still researched`.
FAIL: step 2 stays green — the guard is decorative again, and everything else in
this case is worthless.

Then confirm the shipping caller actually uses it: `link-enrich.ts` must read
`gathered.filter(isResearchable)`. FAIL if the filter has been re-inlined, since
that would route around the rule the test pins.

## T12 — a page that is not trying to redirect is not followed (the F2 regression)

```
deno test --allow-net --allow-env --filter "NOT a scripted redirect" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "OUTSIDE any script" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "IS still followed" src/enrich/links.test.ts
```

PASS: all three green. The third is the one that stops the fix from being a
blanket disable — a real `window.location.replace` inside a `<script>` must still
resolve, which is what the whole item depends on.

Round 2 found seven inert contexts that still steered it, and they are now cases
of their own. Run them:

```
deno test --allow-net --allow-env --filter "an inert context" src/enrich/links.test.ts
```

PASS: eight green — comment/template/textarea × meta and script, plus two
non-executing `type` values. Note that BOTH matchers must be covered:
`metaRefreshTarget` runs first and had never been narrowed, so a `<meta refresh>`
inside a comment was the live bypass, not just a scripted one.

Then the guard-vs-matcher agreement case, which is the subtle one:

```
deno test --allow-net --allow-env --filter "mostly-commented" src/enrich/links.test.ts
```

`extractTextFromHtml` STRIPS comments before counting visible text, so a document
that is almost entirely one commented-out block reads as "no visible text" to the
size guard. If the matcher can still read inside that comment, the two disagree
about what the document contains and the guard protects nothing.

And confirm the fix did not become a blanket disable — round 2 found it had, for
the shapes that matter most:

```
deno test --allow-net --allow-env --filter "IS followed" src/enrich/links.test.ts
```

PASS: an arrow function (`setTimeout(()=>location.replace(…),0)`), a bare
`if(!a)location.href=…`, and a `<script data-x="a>b">` all still resolve. A
minified interstitial looks exactly like these, so failing them would defeat the
anchor's "next publisher" goal while passing every substack case.

Then try to break it yourself. Build a document that is a shell by every other
measure and get `interstitialTarget` to follow something from a context a browser
would not execute: an inline event handler (`onclick="location.href='…'"`), an
SVG `<script>`, a `<script>` inside `<noscript>`, a `srcdoc` iframe, a
`<script type="module">` (which IS executable — it must still resolve). Report
anything that resolves and anything real that stops resolving.

FAIL: any inert context that steers the resolver, or any executable one that no
longer does.

## Out of scope for this plan

Episode length, the TTS/netns outage, the promo filter, gap-dive, and sysadmin
alerting for research yield (that is the `crashloop` item). A finding in any of
those is real and goes to
`documentation/notes/podcast-audio-outage-2026-09-09.md`, not into this patch.
