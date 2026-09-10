# Test plan — `podlinks` (redirect-shell resolution)

Anchor: `queue.ps1 -Show -Id podlinks`.
Branch: `work/podlinks`. The OB1 commit under test is **whatever the parent tip
pins** - read it, never copy it from here:
`git -C <worktree> rev-parse HEAD:OB1`. (This line used to name `ab89394`, which
by round 9 was the commit a round had FAILED and the branch had already moved
past. A tester following it literally would have tested the regressed code.)

**What changed, in one line:** a tracker URL that answers 200 with a redirect
shell is now followed like any other hop, and a wrapper that could NOT be
resolved is marked and logged instead of disappearing through the
newsletter-self-link filter.

Everything below runs from
`<worktree>/OB1/recipes/daily-digest` unless a case says otherwise. `deno` is on
PATH; nothing needs the stack up except T7, which says so.

---

## BEFORE YOU RUN ANYTHING — two rules that protect the operator's checkout

**1. Every destructive step uses an ABSOLUTE path in YOUR worktree.**
Round 4's tester ran a plan step that said `Add-Content scripts\checks\...` with
its cwd set to the operator's MAIN CHECKOUT, and left the planted line there. That is a plan defect, not a tester error: a destructive step
should never carry a relative path when two trees have identical layouts. So:

- write/delete/`git checkout --` only via
  `<repo>\.claude\worktrees\wt-podtest\...`
- before any write, assert `(Resolve-Path .).Path` starts with `...\wt-podtest`
- before you finish, check BOTH trees:
  `git status --porcelain` in the MAIN checkout must show only
  `M system-prompts/general-system-prompt.md` and untracked notes under
  `documentation/notes/`. Anything else is yours — revert it and say so.

**2. Set MSYS_NO_PATHCONV=1**

Two testers in a row have hit this. Under Git Bash, a `docker exec … mkdir -p
/tmp/x` gets its path mangled and creates a directory called `C:\Users\…`
**inside the `/app` bind mount, which is the MAIN CHECKOUT**. Round 3's tester
found the debris from round 2's still sitting there, 4.5 hours old.

Prefix every `docker exec` / `docker cp` with `MSYS_NO_PATHCONV=1`, and before
you finish, check:

```powershell
Get-ChildItem -LiteralPath "D:\Open WebUI\ai-stack\OB1\recipes\daily-digest" -Directory |
  Where-Object { $_.Name -match '^[A-Za-z]:' }
```

Anything listed is yours or a predecessor's; remove it and say so in your report.

## ATTEMPT 9 - what round 8 found

Round 8 FAILED **T10 alone**, on a false figure in this document and in OB1
commit 2f5a351: "all 51 matched the oracle". Four did not - the four documents
that round exists to fix. The correction is in the ATTEMPT 8 section below.
It is the same class as attempt 2's "five new tests, all of which fail" (3 of 5)
and attempt 6's 500-vs-404, and it is the fifth time a claim about this item's
own evidence has been the failure rather than the code. **Read T10 as the case
most likely to fail, not the formality at the end of the list.**

Everything else passed, and the round produced evidence worth not repeating:

- **T4 is a genuine red-green.** The restored 970cae8 contains no occurrence of
  `interstitial|metaRefresh|scanDocument`; it prints STOPPED where the patched
  file prints FOLLOWED.
- **The regression sign is right.** The four solidus documents: c13fa1c -> all
  null, ab89394 -> all `https://evil.example/pwn`, 2f5a351 -> all null.
- **An INDEPENDENT parse5 oracle, both scripting modes**, over 52 targeted plus
  4,000 seeded-random documents: **0 unsafe divergences**, every divergence
  fail-closed. The exemption is not too NARROW either - the captured Substack
  `<noscript>` META, plain head meta, `type="module"`, minified arrow and
  bare-`if` shapes and `<script data-x="a>b">` all still resolve.
- **The rewritten `INERT_SUBTREE_ELEMENTS` comment is true**, checked against
  parse5, and the "chosen fail-closed trade" is what the code does: a refused
  document drove the real path to `unresolvedWrapper=true, isResearchable=true`.
- **The 404 correction reproduces** independently inside `openbrain-podcast`.

METHOD NOTE, AND IT IS A TRAP - keep it if you write an oracle for T12:
**parse5 defaults to `scriptingEnabled: true`, under which `<noscript>` content
is RAW TEXT.** Round 8's first fuzz run against that default alone reported 27
"unsafe" hits, all `<noscript><meta refresh>` - the exact shape this item exists
to follow. The oracle was wrong, not the code. Run BOTH scripting modes, or an
oracle-based T12 will manufacture a finding against the feature itself.

## ATTEMPT 8 - what round 7 found

Round 7 FAILED T12 and nothing else, at MEDIUM-LOW and REACHABLE, on a regression
the round-6 fix introduced. It marked the plan ADEQUATE.

The round-6 fix let a trailing solidus self-close a suppressing element. That is
true ONLY in foreign content: the HTML parser IGNORES a trailing solidus on an
HTML element, so `<select/>` and `<template/>` DO open a region. Treating them as
self-closed meant an inert `<meta refresh>` or `<script>` after one was read as
LIVE, followed on the real path, and emitted by `gatherAnchors` with
`unresolvedWrapper=false` - unmarked and silent. The exemption is now `svg`/`math`
only, and both directions are permanent cases.

HOW ROUND 7 FOUND IT is the part to repeat, and the case list below now says so:
the tester ran the resolver against **parse5**, a spec-compliant parser, in both
scripting modes, over 51 attack documents. Reading the spec's prose is what got
the solidus rule backwards in the first place. **If you are attacking T12, use an
oracle, not an argument.** (`npm:parse5` is reachable from the recipe container;
`deno run -A --node-modules-dir=none` will fetch it. If it is not reachable in
your environment, say so in your report rather than substituting your own reading
of the spec - that substitution is the defect this round found.)

That same comparison is also EVIDENCE FOR the rest of the stack, and you do not
have to redo it - but read the count carefully, because the first version of this
paragraph got it wrong. **47 of the 51 matched. The four that did NOT are the
four `<select/>`/`<template/>` documents this round fixes** - they are listed as
mismatches in that run's own evidence table. "All 51 matched" appeared in OB1
commit 2f5a351 and in this section, and it is false: it turns the one divergence
the run found into zero, while being used to tell the next tester they need not
repeat the run. Round 8 caught it; this is the correction, and the commit message
cannot be rewritten because its SHA is already pinned by the parent.

What the 47 cover - `</template>` inside style,
title, textarea, iframe, xmp, noembed, a script body, a comment, a comment inside
an svg inside the template, quoted and unquoted attributes, a bogus comment, an
entity, a nested template, plaintext, `</script>` inside a JS string, `<!-->`,
unterminated quotes, entity-encoded srcdoc, CDATA, foreignObject. Unterminated
regions fail closed. Spend this round's attack budget somewhere the previous
rounds did NOT reach.

Round 7's Finding 2 was accepted: the `INERT_SUBTREE_ELEMENTS` comment claimed a
`<meta>` in `svg`/`math` "is not a document-level meta". It is - `<meta>` is on
the HTML breakout list. The code still refuses it, deliberately; the comment now
says that is a CHOSEN fail-closed trade rather than a claim about parsing. T10
covers whether the note and commit message state it that way.

## ATTEMPT 7 - what round 6 found

Round 6 FAILED T12 at HIGH severity, and marked the plan ADEQUATE — the plan named
`scanDocument`, told the tester to attack it, and its FAIL clause covered exactly
what was found.

`scanDocument`'s main loop is context-aware; the two DEPTH COUNTERS inside it were
not. Skipping `<template>` and the inert subtrees used raw-string regex token
counts, so they counted `</template>` / `</select>` occurring inside comments,
attribute values, script bodies and `<textarea>`. Ten inert documents produced a
target **on the real path**, with `unresolvedWrapper=false` — not even marked, so
the operator log line never fired and the URL entered research silently. The same
counter over-swallowed in the other direction, hiding rendered prose from the
guard so a full article resolved.

That was round 3's nested-template finding in a new spelling: fixing the nesting
reintroduced the context blindness. Both counters are gone — the walk now
maintains a suppression STACK, closed only by a close tag the same walk reached,
so exactly one mechanism decides what is inside what.

Round 6 also confirmed **the TLD rule holds** under 57 attacking URLs, and found
one documentation defect worth knowing: the note's claim that `127.0.0.1` returns
500 through `vpn:8888` does not reproduce — it returns **404**, because the proxy
connects to its own loopback. The service-name 500s do reproduce. Corrected in
the note.

## ATTEMPT 5 — what round 4 found

Round 4 confirmed the twelve round-3 bypasses are closed, then found four more,
a ONE-CHARACTER defeat of the host screen, and the guard/matcher disagreement
still open for six elements.

**The security finding is the serious one.** `http://openbrain-curator.:8000/` -
the exact target a test asserts is refused - RESOLVED, because a trailing dot
makes the root label satisfy `host.includes(".")` and breaks every exact/suffix
comparison. The tester pointed `http://localhost.:PORT/` at a live loopback
listener and CONNECTED. IPv4-mapped IPv6 leaked too, and my first fix for THAT
also leaked because the URL parser normalises `[::ffff:127.0.0.1]` to
`[::ffff:7f00:1]`. Both are fixed and both are cases now.

**`<plaintext>` was a regression I introduced** - its tokenizer state is terminal
and the regex version handled it; the scanner dropped it. Plus bogus comments
(`<?foo`, `</3`) which run to the next `>` - the swallowed meta's own - and
`<math>`/`<select>`/`<svg>` subtrees.

**The guard/matcher disagreement is now closed by construction**, not by
agreement: `extractTextFromHtml` erases `<nav>`, `<header>`, `<footer>`,
`<aside>`, `<form>` and `<svg>` before counting, so text there was invisible to
the guard and the document read as a shell. The guard now measures `liveText`
from the SAME walk that decides what is live.

**T4 in this plan was self-contradictory** and round 4 was right to mark the plan
inadequate: its `redproof.ts` targets loopback, and T14's screen refuses
loopback, so step 5 could not print FOLLOWED on correct code. Corrected below to
use a public destination, which needs no hatch and is the stronger form.

Round 4 also reported, correctly and unprompted, that the DNS-time case
(`localtest.me` resolving to 127.0.0.1) reaches loopback and that a textual
screen cannot close it. That is recorded as a KNOWN LIMIT, not a pass.

## What round 3 found

Round 3 FAILED T12 with **twelve** bypasses, and marked the plan ADEQUATE: the
plan was right and the code was wrong. Five of the twelve drove the REAL path —
`unwrapRedirect` followed them and `gatherAnchors` emitted the attacker's URL as
an ordinary candidate with `unresolvedWrapper=false`.

`stripInertRegions` was closing-tag-anchored regex, so every UNTERMINATED inert
region survived it, NESTED templates survived it, and it had never heard of
`<style>`, `<title>`, `<noscript>`, an iframe `srcdoc`, or a `<script>` inside an
attribute VALUE. For `<style>`/`<noscript>` the guard and the matcher still
disagreed in exactly the direction the previous commit claimed to have fixed for
comments.

**The fix is a different tool, not twelve more patterns:** one left-to-right scan
(`scanDocument`) that tracks quoted attribute values, counts template nesting,
and treats `<noscript>` as transparent for `<meta>` but never executes scripts in
it — and, when it meets something it cannot place, reports `ambiguous` so the
caller REFUSES to treat the document as a redirect at all.

Failing closed is the load-bearing decision. Refusing costs one unresolved
wrapper, which is logged and still researched. Guessing costs a wrong URL
entering the research corpus silently. **T12 now tests both directions**, because
a fix that fails closed is worthless if it closes on the document this item
exists for.

Round 3's other results, all still relevant: T11's attack now genuinely bites,
T4 with `970cae8` is properly RED, T7 resolved cleanly through the VPN, and every
figure in every OB1 commit message reproduced.

## What round 2 found

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

PASS: 96 passed, 0 failed.
FAIL: any failure, or fewer than 96 tests (a case was deleted rather than fixed).

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
   against either.

   **CORRECTED after round 4.** This script used a loopback stub, and T14's host
   screen — added in the same revision — refuses loopback, so step 5 could not
   print FOLLOWED on correct code and the case produced a spurious failure.
   Round 4 was right to call the plan inadequate for it. Two of my own changes
   contradicted each other and neither was wrong on its own.

   The fix is to set the loopback hatch for this proof, since it is exercising
   the RESOLVER and not the screen:

```ts
Deno.env.set("FETCH_PROXY_URL", "");
Deno.env.set("RESEARCH_ALLOW_PRIVATE_TARGETS", "1");  // stub servers are loopback
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
deno test --allow-net --allow-env --filter "solidus" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "still suppresses its subtree" src/enrich/links.test.ts
```

The last two are round 7's regression: 4 + 2 cases, all green. They are the pair
that stops the self-closing exemption from widening again - one direction proves
`<select/>` and `<template/>` still suppress, the other proves `<svg/>`, `<math/>`
and a NON self-closed `<svg>`/`<math>` still behave. A fix that makes either
direction pass alone is not a fix.

PASS: all three green. The third is the one that stops the fix from being a
blanket disable — a real `window.location.replace` inside a `<script>` must still
resolve, which is what the whole item depends on.

Round 3 found TWELVE contexts that still steered it, and they are now cases:

```
deno test --allow-net --allow-env --filter "a browser would not navigate" src/enrich/links.test.ts
```

PASS: twelve green — `<script>` in `<noscript>`, iframe `srcdoc` (script and
meta), nested `<template>` (script and meta), unterminated comment (script and
meta), unterminated `<textarea>`, unterminated `<template>`, `<script>` in
`<title>`, `<meta refresh>` in `<style>`, and a `<script>` inside an attribute
VALUE.

**Now the other direction, which matters just as much.** The fix works by FAILING
CLOSED on an ambiguous document, and a fix that fails closed is worthless if it
closes on the document this item exists for:

```
deno test --allow-net --allow-env --filter "failing closed did not break" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "an inert context" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "IS followed" src/enrich/links.test.ts
```

PASS: the real Substack `<noscript>` meta, a plain head meta, `type=module` and
`type=MODULE`, the minified arrow-function and bare-`if` shapes, and
`<script data-x="a>b">` all still resolve.
FAIL: any of those returning null — that is the fix eating its own purpose, and
it would pass every bypass case while breaking the feature.

Then attack it yourself, which is what found the last twelve. The scanner is in
`scanDocument`. Try: a `<script>` whose closing tag sits inside a JS string; SVG
foreign content; `<!-->` and other degenerate comment forms; a tag with an
unterminated attribute quote; `<plaintext>`; a `srcdoc` with entity-encoded
markup; CDATA. For each, say whether it resolves, and whether that is the safe
answer or the wrong one.

FAIL: any inert or unparseable context that produces a target, or any genuinely
executable one that stops producing one.

## T13 - the fallback lever, and what it costs

The operator required a fallback before this lands. It must degrade to the OLD
behaviour, not to a hole.

```
deno test --allow-net --allow-env --filter "INTERSTITIAL_FOLLOW" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "lever OFF" src/enrich/links.test.ts
```

PASS: with `INTERSTITIAL_FOLLOW=0` the wrapper is NOT followed and unwrapRedirect
returns it unchanged; with it restored, it resolves; and a marked wrapper is
still `isResearchable`.

Then check the DOCUMENTED procedure is real, because a lever nobody can find is
not a lever. `documentation/notes/podcast-audio-outage-2026-09-09.md` must carry
the variable name, the file it goes in, the recreate command, and what pulling it
costs. FAIL if the note names a variable the code does not read - grep
`INTERSTITIAL_FOLLOW` in `links.ts` and confirm it is read PER CALL, not cached
at import (a lever needing a restart is not a lever).

## T14 - where a resolved target may point

The redirect target is chosen by the page we just fetched, and we then fetch it.

```
deno test --allow-net --allow-env --filter "internal infrastructure" src/enrich/links.test.ts
deno test --allow-net --allow-env --filter "may not point" src/enrich/links.test.ts
```

PASS: docker service names (no dot), loopback, RFC1918, 169.254 metadata,
100.64/10, bracketed IPv6 loopback, `.local`/`.internal`, and non-http schemes
are all refused; public URLs still resolve; and the `Location` hop is screened
too, not just the interstitial one.

ATTACK IT, because this is a security control and a green here is worth little
on its own:
- Confirm `RESEARCH_ALLOW_PRIVATE_TARGETS` is NOT set in any deployed env
  (`grep -rn RESEARCH_ALLOW_PRIVATE_TARGETS` across the repo and
  `docker exec openbrain-podcast env`). It exists only so the suite's loopback
  stubs work. FAIL if it appears anywhere outside the test file.
- Verify the two screen cases DELETE that variable before asserting - otherwise
  they are testing the hatch, not the policy.
- Try to get a private target past it: decimal/octal/hex IPv4
  (`http://2130706433/`, `http://0177.0.0.1/`), IPv4-mapped IPv6
  (`http://[::ffff:127.0.0.1]/`), a trailing-dot FQDN, userinfo
  (`http://public.example@127.0.0.1/`), and a public hostname that RESOLVES to a
  private address. Report each. The last one is DNS-time and this screen is
  textual - if it gets through, say so plainly rather than treating the case as
  passed.
- Confirm no JavaScript is executed: `grep -nE "eval\(|new Function|import\(|jsdom|puppeteer|playwright" src/enrich/*.ts` must be empty.

FAIL: any private/internal target that resolves, or any evidence of execution.

## T15 - a figure in a shipped CODE COMMENT is a claim too

T10 covers the commit message and the findings note. It does NOT cover source
comments, and round 9 landed a review block there: `links.ts` still told the
reader that four internal targets "all came back 500 from the proxy ... so the
Mullvad tunnel is ALREADY an SSRF boundary", while the findings note IN THE SAME
DIFF corrected that figure to 404 and called it load-bearing. The diff
contradicted itself and the wrong half was in the code - which is the surface the
anchor names as the audience ("understand FROM THE CODE").

The measurement is the same one T10 makes for the note; what is new is WHERE you
look.

```
# every number, and every "measured"/"probed"/"came back" claim, in the changed source
git -C <worktree>/OB1 diff 970cae8..HEAD -- recipes/daily-digest/src recipes/daily-digest/link-enrich.ts |
  grep -n '^+.*\(measured\|probed\|came back\|returned\|all four\|always\|never\|every\)'
```

For each hit, re-derive it or find where it is derived. Specifically:

- the 404-not-500 egress figure, re-measured inside `openbrain-podcast` through
  `http://vpn:8888` (404 is the gluetun control server answering - the proxy
  CONNECTED to loopback; 500 is a refusal, and the difference is the whole point)
- `INTERSTITIAL_MAX_BYTES` and the claim about what is buffered
- the "no JavaScript is ever executed" claim (grep the path, do not read the
  comment that asserts it)
- the "every docker network name here carries a hyphen or an underscore" claim,
  against live `docker network ls`
- any claim of the form "all N", "every", "never" - this item has produced four
  false universals across nine rounds and every one of them was in a place a
  reader would trust

FAIL: any figure in shipped source that does not reproduce, OR a source comment
that contradicts the findings note shipping in the same diff. A claim that is
merely IMPRECISE is a note in your report, not a FAIL - say which you found.

## Out of scope for this plan

Episode length, the TTS/netns outage, the promo filter, gap-dive, and sysadmin
alerting for research yield (that is the `crashloop` item). A finding in any of
those is real and goes to
`documentation/notes/podcast-audio-outage-2026-09-09.md`, not into this patch.
