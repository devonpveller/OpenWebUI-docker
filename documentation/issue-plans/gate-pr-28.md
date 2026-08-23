## Verdict: DENY

## Rubric
- Solves the issue INTENT (not merely tests-green): **fail** — the keyword boundary is applied only to the *left* edge, so the reported "capture the tail of a word" defect still fires whenever `to`/`as`/`into` *starts* a word.
- Evidence quality (failing→passing repro shown): **pass** — both RED and GREEN `unittest` transcripts are present and I verified their internal consistency against the base regex by hand; the caveat is *case selection*, scored under intent.
- Scope discipline (no drive-by changes): **pass** — three files, all under `smolcrawl/`, each authorized by plan steps 2–4; `_extract_url`, job submission and `src/smolcrawl/` untouched.
- SERVICE-LIFECYCLE compliance (if service-shaped): **n-a** — `smolcrawl-pipelines` retired in a18aa0c; only surviving compose trace is the orphan-volume comment at `docker-compose.yml:28-30`, so no compose/recovery/watchdog/stack-map surfaces are in play.
- Security (secrets, gateway-only routing, branch policy): **pass** — stdlib-only test, no `.env`, network, or inference contact; cut from `development`, human merge retained.

## Reasoning

**The patch implements half of the boundary the issue and the plan both specify.** Issue #17's prescribed pattern is `\b(?:into|to|as)\b\s+` — a boundary on *both* sides plus mandatory whitespace. The diff ships `\b(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*` (`smolcrawl_pipeline.py:820-821` post-merge): leading `\b` only, and `\s*` rather than `\s+`. The leading `\b` kills *infix* hits — `manda|to|ry>`, `au|to|matically`, `datab|as|e` — which is exactly why the reporter's repro now passes. It does nothing for *prefix* hits, where `\b` is trivially satisfied.

Hand-trace on the shipped regex, `message = "crawl https://example.com/ today"` → `search_space = " today"`:
- pattern 1 requires `\s+(?:with|using|from)` → no match.
- pattern 2 at pos 0: char is a space, non-word → `\b` fails.
- pos 1: prev `" "` non-word, cur `"t"` word → **`\b` holds**; alternation matches `to`. `\s*`→∅, `["\']?`→∅, lazy `([^<>\n\r]{1,80}?)` expands `d`→`da`→`day`, then `\s*$` hits end-of-string. **Capture = `day`.**
- `_normalize_kb_name("day")` → `"day"` — non-empty, `!= url` → returned.

KB name **`day`** instead of `SmolCrawl - example.com`; same mechanism as `ry>`. Likewise `tomorrow`→`morrow`, `asap`→`ap`, `assets only`→`sets only`. Worse, the residual survives the very context the issue names. For `"crawl https://example.com/ </query>\nAssistant: proceed"`, the `As` of `Assistant` sits after `\n` (non-word), `\b` holds, `[^<>\n\r]` never has to cross the newline, and `\s*$` reaches end-of-string → KB name **`sistant: proceed`**. Issue #17's Expected behavior enumerates four exclusions — "partial words, XML-like tags, prompt wrappers, or system/instruction text". The `<>` capture-class exclusion genuinely closes the *tag* clause (I traced `<tone>` and `<tool_call>`: they now fail the match and fall through to the domain default). The *partial words* and *prompt wrapper* clauses remain open.

**The PR description's central claim is false as written.** It states `\b` boundaries mean "`to`/`as`/`into` can no longer match inside words (`mandatory`, `automatically`, `database`)". All three named examples are infix; the general claim does not hold for word-initial occurrences. This is the narration that would otherwise carry the merge.

**The plan gate predicted this failure and named the correct construction.** `gate-plan-17.md:28` flags it as the top execution risk: a naive shared `\b` "breaks the `kb:` route — a `\b` after `:` demands a following word char, and `kb: Gaggia Docs` has a space. **The boundary must be per-alternative: `(?:\b(?:into|to|as)\b|kb:|knowledge[- ]?base[: ])`**." The worker hit exactly that predicted `kb:` conflict and resolved it by deleting the trailing boundary for *every* alternative rather than scoping it per-alternative. The answer was in a document upstream of the dispatch.

**The suite confirms the implementation rather than the requirement.** All three "keyword inside word" tests (`_mandatory`, `_automatically`, `_database`) are infix cases — precisely the half the patch fixes. There is not one prefix case among the 14. The RED transcript is credible (I re-derived `'e'`, `'matically'`, `'ry>'` and `'Gaggia Docs" with max depth 2'` from the base patterns and they match exactly), so this is not fabricated evidence — it is evidence drawn around the patch.

**One companion change reduces the signal that surfaced the bug.** Adding `<>` to the edge-strip set at `smolcrawl_pipeline.py:60` is authorized by plan step 3 and harmless in isolation, but next to an incomplete extraction fix it turns a conspicuously broken `ry>` into a plausible `ry`. The operator noticed this defect because the output *looked* wrong; `day` will not.

Scope, lifecycle and security are genuinely clean, the reorder putting the `with|using|from` variant first is correct and well-justified, and the deliberate deviation from the issue's `[^<>"'\n\r]` to `[^<>\n\r]` (preserving `into Bill's Docs`) is a good call the plan documented. This denial is about correctness and case coverage, not discipline — the remaining work is roughly one regex token.

## If DENY: orchestration adjustment plan

1. **Promote plan-gate risk notes from advisory prose to binding acceptance criteria.** `gate-plan-17.md:28` contained the literal correct construction and the worker was never obliged to reconcile against it. The dispatch payload should carry a `must_satisfy` list lifted from the gate's risk section — here: *"the word boundary is per-alternative; `kb:`/`knowledge base` keep `\s*`, `into|to|as` get `\b…\b\s+`"* — and `_finish_effort` should require the worker to quote each item and state how the diff satisfies it.

2. **Add a constraint-erosion stop-gate.** The failure was structural, not careless: a plan-mandated test (`kb: Gaggia Docs`) went RED after the guard was added, and the worker restored green by weakening the guard for all alternatives. When a *specification-derived* test flips RED as a direct result of adding a guard, the charter must force stop-and-narrate — "the guard and this case conflict; here are the two constructions" — rather than permitting unilateral loosening. Same shape as the `AO_WORKER_PLAN_GATE` flail guard, applied to constraint erosion.

3. **Require tests derived from the requirement text before the fix is written.** For any regex/parser/matcher change, mandate enumerating the case-class matrix up front — `{word-start, mid-word, word-end, whole-word} × {must match, must not match}` — and asserting each cell or declaring why it is out of scope. Three infix cases and zero prefix cases would have been rejected at authoring time. Charter line: *"a test set containing only cases your patch passes is not evidence; name the case class you did not cover and why."*

4. **Make the issue's "Expected behavior" a clause-by-clause checklist.** The reporter listed four exclusions; two are covered. Require the worker to restate that sentence verbatim in the PR and map each clause to a named test, so partial satisfaction is visible to the gate instead of being averaged away by an all-green run.

5. **Flag symptom-sanitizing edits for co-delivery review.** Any change that *sanitizes* an output (strip sets, trims, silent coercions) must be declared alongside the change that removes the *cause*, so a gate can verify both landed complete. Here the sanitizer landed complete and the cause landed partial — the combination is worse than either alone, because it hides the residual.
