# Source admission — staged filtering before a page becomes evidence

**Status: SHELVED 2026-08-20.** Design only, nothing built. Raised while tuning
the research fetch budget; parked deliberately so it can be done as its own
piece of work rather than bolted onto a tuning change.

Sibling to [supervised-research-pipeline](../supervised-research-pipeline/) —
that plan governs what a job is *allowed* to do (contracts) and whether its
synthesis survives challenge (Skeptic). This one governs what is allowed to
*become evidence* in the first place. They meet at the claim.

---

## 1. Current state (verified, not remembered)

`FetchOutcome` is `"ok" | "timeout" | "error"` (`harness.ts:65`). The counters
are already separated, and charge different ceilings:

| Outcome | Counter | Charges | Staged as a source? |
|---|---|---|---|
| `reuse` (already in OB, fresh) | `reuseHits` | nothing | yes |
| `ok` | `sourcesFetched` | `MAX_FETCH` | **yes** |
| `timeout` | `fetchTimeouts` | `MAX_FETCH_TIMEOUTS` | no |
| `error` | `fetchErrors` | nothing | no |

So the "errors and timeouts eat the fetch budget" problem **was** fixed:
non-2xx, wrong content-type, empty extraction and thrown exceptions all classify
`error` (`index.ts:306-316`) and cost no source budget.

**What is still wrong is the definition of `ok`.** It means *HTTP 200 whose HTML
extracted to a non-empty string* — nothing more. Every one of these is currently
an `ok`:

- captcha / "verify you are human" interstitials
- Cloudflare challenge pages
- cookie-consent and GDPR walls
- paywall stubs ("Subscribe to continue reading")
- login walls
- soft-404s (a "page not found" body served with status 200)

Each has non-empty text, so `index.ts:311` passes it. Two consequences, and the
second is the serious one:

1. It burns a slot of `MAX_FETCH` — the budget problem, real but merely wasteful.
2. `harness.ts:377` pushes it into `staged`, so **it becomes a candidate source
   for grounding**. Junk is admitted as evidence and competes for citation.

`screenSources()` (injection.ts) runs later, but it quarantines *hijack attempts*
— it is a prompt-injection defence, not a usefulness filter. A cookie wall is not
malicious; it is simply not a source.

Suspected contributor to the observed `coverage 25% · stopped early: max_fetch`:
a run can spend its whole budget on pages that were never going to ground
anything.

---

## 2. The shape being proposed

Stage the admission so cost rises only as confidence rises. Cheapest, most
deterministic gate first; the LLM only ever sees pages that survived the free
checks.

### Stage 0 — domain pattern (pre-fetch, free)

Judge the URL before spending a fetch. Prefer high-trust patterns (`.gov`,
`.edu`, `.ac.*`, known primary sources, arXiv/DOI); deprioritise or skip
content-farm and aggregator domains.

Open questions:
- **Skip, or merely rank?** A hard allow-list makes a private-search stack
  worse at exactly the long-tail questions it exists for. Ranking that reorders
  the fetch queue is probably right, with skipping reserved for a deny-list.
- Where does this live relative to `contract.ts` `permitsUrl()`, which already
  gates URLs per job? Likely the same seam — a contract expresses *policy for
  this job*, stage 0 expresses *standing quality priors*. Do not build a second
  URL gate; extend that one.

### Stage 1 — content validity (post-fetch, cheap, deterministic)

Does the retrieved body look like an article at all, or like a wall? Heuristics:
length floor after extraction, telltale phrases, `<title>` patterns, link/text
ratio, absence of paragraph structure.

**This is the stage that fixes the budget.** A page failing stage 1 becomes a
new outcome — `rejected` — which charges neither `MAX_FETCH` nor
`MAX_FETCH_TIMEOUTS`, and is never staged. `MAX_FETCH` then means what its
comment already claims: *source-yield*, valid opportunities only.

Needs its own ceiling (`MAX_FETCH_REJECTED`) so a search returning nothing but
walls cannot loop forever — the same reasoning that gave timeouts a separate
ceiling rather than letting them ride free.

### Stage 2 — semantic relevance to the anchor (post-fetch, LLM)

Only for pages that passed 0 and 1: does this page actually address the anchored
prompt? Not "is it on topic" — is the question *semantically present*.

Open questions:
- One call per page is expensive on a one-at-a-time queue. Batch several pages
  per call, or gate stage 2 behind an embedding similarity floor first (cheap,
  local, `bge-m3` is already in the stack).
- **Fail-open or fail-closed?** Everything else in this engine fails open toward
  honest gaps. A stage-2 rejection discards a page that was successfully
  retrieved, so a false negative silently narrows evidence. Leaning fail-open:
  on an ambiguous or errored verdict, admit the page and let the Skeptic catch it
  downstream.

---

## 3. The lifecycle the user described

```
source  ->  proposal  ->  claim
```

- **source** — a page admitted through stages 0–2. Retrieved, non-junk,
  semantically about the anchor.
- **proposal** — a candidate assertion extracted from a source. Not yet
  evidence. New term; nothing in the codebase uses "proposal" today.
- **claim** — a proposal *flipped* only when it is (a) validated against its
  source, (b) relevant to the anchor, and (c) **closes an open gap**.

The gap-closing condition is the interesting one and is not currently modelled:
today a synthesis grounds what it happens to find. Requiring a proposal to close
a *named* gap makes coverage a driver of the run rather than a number reported
at the end — and gives `coverage 25%` a mechanism to improve instead of just a
metric to observe.

Relationship to existing pieces, to be settled before building:
- the curator already writes grounded claims — does "proposal" live in the
  harness before the curator, or become a curator state?
- the Skeptic (Phase 2, shipped dormant behind `SKEPTIC_ENABLED`) already
  challenges a finished synthesis. Is proposal→claim validation the same
  judgement moved earlier, or a distinct one? **Do not build two adversarial
  reviewers that disagree.**

---

## 4. Do first, when this is picked up

1. **Measure before building.** Add outcome sub-classification and log it for a
   few real runs: of the pages charged to `MAX_FETCH`, how many were walls,
   soft-404s, or off-anchor? If it is 5%, stage 1 is not worth building; if it
   is 40%, it explains the coverage number outright. This decides the whole plan
   and costs a counter.
2. Stage 1 only, behind a flag, and re-measure coverage on the same query set.
3. Stage 0 as ranking, not skipping.
4. Stage 2 last, and only if 1–3 leave coverage short — it is the expensive one
   and the only one that can wrongly discard good evidence.

Baseline for comparison, captured 2026-08-20 before any change:
`coverage 25% · stopped early: max_fetch` at `MAX_FETCH=40` / `MAX_WALL_MS=5min`.
Ceilings were then raised to 80 / 15 min, so re-baseline before attributing any
improvement to this work.
