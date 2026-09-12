# Findings sink — research-trust (harness item `research-trust`, 2026-09-11)

Anchor: `documentation/implementation-guide/research-engine-for-OB/anchor-research-trust.json`.
Plan: `documentation/implementation-guide/research-engine-for-OB/PLAN-research-trust-2026-09-11.md`.

Provenance label on every entry:
**[read-from-source]** = read in this tree at file:line ·
**[observed-live]** = measured against a running container on the date given ·
**[not-verifiable-here]** = could not be established from this tree/environment.

---

## 1. Phase 0 baseline

### 1.1 Test counts before any change — [observed-live 2026-09-11]

`deno test -A` in `OB1/integrations/research-service`: **79 passed, 1 failed**.
The failure is `orchestrator.test.ts`, which is an INTEGRATION test: it opens a
postgres pool at `DB_HOST` (default `ob-claims-test`) at module top level and dies
with `No such host is known. (os error 11001)` when that DB is absent
(`orchestrator.test.ts:12-16`, `:53`). So "deno test -A green in research-service"
is only reachable with the Phase 5.3 throwaway DB running — see §7.

`deno test -A` in `OB1/integrations/research-curator`: **17 passed, 0 failed**.
(`claims.integration.ts` is not a `*.test.ts` file and is not collected.)

### 1.2 Live engine health — [observed-live 2026-09-11]

`docker exec searxng` + `GET localhost:8080/search?format=json`, the 12 `needs`
of jobs `ce398d06` and `8c9b4f1d` verbatim, one request per query, term-overlap
ratio exactly as defined in `documentation/notes/search-engine-alternatives-2026-09-11.md` §0:

| | live (searxng/searxng:latest = 2026.5.17-d7e8b7cd1) |
|---|---|
| queries | 12 |
| **median term-overlap** | **0.00** |
| range | 0.00 – 0.00 (every query) |
| engines contributing a hit | **bing only**, 10 hits each, HTTP 200 |
| unresponsive, every query | `mojeek: Suspended: access denied` |
| unresponsive, query 12 | `wikipedia: too many requests` |
| google | 0 results, **no error reported** |

First hit per query: `MOST—Missouri's 529 Education Plan`, `SPECIFIC 中文…
Cambridge Dictionary`, `CAN Definition & Meaning — Merriam-Webster`, `Steps (pop
group) — Wikipedia`, `SHOULD Definition & Meaning`, `Visual Studio`,
`PHYSIOLOGICAL Definition & Meaning`, `SPECIFIC 中文…`, `Qualitative vs
Quantitative Research`, `DOES Definition & Meaning`, `THERE | English meaning`,
`DOES Definition & Meaning`.

**The plan's premise reproduces on 12 of 12 queries, not 3 of 12.** Acceptance 0 met.

### 1.3 The audit's two number claims, re-verified independently — [observed-live 2026-09-11]

- OptiPlex cited source 5 (medium.com, 1 881 chars) and source 6 (nvidia forum,
  8 000 chars): the digit string `95` occurs in **neither**. The `95 °C` figure was
  fabricated. Exported into `OB1/integrations/research-service/fixtures/job-optiplex.json`.
- 100 Hz cited source 2 (nature.com): `26%`, `56%` and `0.0055` all occur verbatim.
- All 8 poison claim ids were `status='active'` at export time.

---

## 2. Phase 1.3 — the engine measurement (before / after)

Rig: throwaway container `rig-searxng-probe`, image
`searxng/searxng:2026.9.11-61d660276`, label `ai-stack.harness.owner=research-trust`,
on `search_search-net` (the search project's own network, NOT an `ai-stack_*`
anchor network) so its egress went through `http://vpn:8888` exactly as live does.
Same 12 queries, same scorer, same Mullvad exit. The live container, its config
and the live compose project were **not touched**. — [observed-live 2026-09-11]

| variant | median overlap | engines answering per query | notes |
|---|---|---|---|
| **live today** (2026.5.17 image, current settings.yml) | **0.00** | 1 (bing) | mojeek suspended every call; google silent |
| new image + google/brave/qwant/yandex/`duckduckgo web`, **bing off**, `google cse` left at upstream default | 0.70 | 4–5 | `google cse` answered ~20 hits/query with no key configured |
| same, **bing ON** | **0.58** | 5–6 | bing added 10 hits at 0.00 to every query |
| **shipped**: new image + google/brave/qwant/yandex/`duckduckgo web`, bing off, mojeek off, `google cse` off | **0.76** | **4–5** | qwant CAPTCHA'd 0/12; brave rate-limited after query 5 |

Per-query overlap of the shipped variant: 0.91, 0.74, 0.72, 0.98, 0.90, 0.43,
0.84, 0.66, 0.60, 0.78, 0.58, 0.78. **Median 0.76, bar 0.6 — met.**

**The bing decision is from data, not taste.** Leaving bing enabled costs
0.70 → 0.58, i.e. it drops the merged result set BELOW the acceptance bar on its
own. It answers 12/12 at 0.00 with HTTP 200, so it is pure noise that also
suppresses the collapse detector's signal. Disabled, with that number in the
file comment (`search-gateway/searxng/settings.yml`).

**`google cse` was answering and nobody asked it to.** It is enabled by upstream
default on the new image and returned ~20 hits/query with no `api_key`/`cx`
configured — flagged as unexplained in
`search-engine-alternatives-2026-09-11.md` §8.5. Until somebody explains what it
is querying, it is the same unexamined exposure as keyless mojeek, so it is now
named and disabled rather than inherited silently. Removing it did not cost
relevance (0.70 → 0.76).

**Not verified:** whether brave survives a full production research job's pacing
(it rate-limited after ~5 burst queries here, as the exploration note also found),
and whether a non-US Mullvad relay changes anything. — [not-verifiable-here]

---

## 3. Findings that changed the design

### 3.1 The collapse token is usually a STOPWORD — [observed-live 2026-09-11]

The plan's detector ("at least 60 % of hits share one query token in their
titles") was first implemented over the same stopword-filtered token list the
overlap scorer uses. It classified `search-collapsed-dell` and
`search-collapsed-the100` correctly and **missed `search-collapsed-most`**, the
first fixture the audit names. The reason: the collapse token `most` is in the
scorer's stopword list, and so are `specific`, `can`, `steps`, `should`, `does`
and `there` — the collapse tokens of 7 of the 12 baseline queries.

The dominance test now runs over ALL query tokens (stopwords included) while the
overlap ratio keeps the published stopword list. `search-quality.ts` carries the
reason in a comment. A stopword-filtered dominance test would have been a check
that passed while checking nothing on the majority of the audited failure.

### 3.2 A digits-only grounding check is a spelling check — [observed-live 2026-09-11]

The first `applyNumericGrounding` downgraded the 100 Hz CAREN line ("a clinical
study of **30** motion sickness-susceptible participants…") because its source
writes the number as **"thirty"**. The audit had verified that line as supported.
`grounding.ts` now accepts the English word form for the small integers. Without
it the check would have manufactured a false ungrounded-figure finding on an
accurate report — the exact failure mode it exists to prevent, inverted.

### 3.3 `groundNumbers` on the audited line returns TWO misses, not one — [read-from-source]

The plan's RED expectation is `missing: ["95"]`. The line is
`[INFERRED] Thermal throttling and automatic shutdown at approximately 95°C is
documented for the NVIDIA DGX Spark …; this is NOT confirmed … for the Dell
OptiPlex 3050. [Source 5, 6]` — it also asserts `3050`, which neither DGX Spark
source mentions. Both are true misses. The expectation was written from the
audit's prose rather than from the line.

### 3.4 The 100 Hz run wrote 13 claims TOTAL, of which 3 are poison — [observed-live 2026-09-11]

The anchor says the filter must reject the 8 poison texts "and keep the 13
factual 100 Hz claims". `SELECT` over the run's synthesis id returns 13 claims,
and `eea04dc2`, `cd1d66fb`, `32061b2c` are among them *and* among the 8. The
number of claims that must survive is **10**, not 13. Fixtures:
`research-curator/fixtures/claims-poison.json` (8) and
`claims-100hz-world.json` (10). Raised in TEST-PLAN case 7.

### 3.5 "What the sources actually cover" is not in the repository — [read-from-source]

`grep -rn "sources actually cover"` across the whole worktree hits only the plan,
the audit and the anchor. The heading was written by the MODEL, inside the
`general-report` template's free-form "supporting detail under ## section
headers" instruction (`templates.ts:165-171` before this change). There is no
string to delete. The guarantee that the OptiPlex replay never prints it comes
from Phase 4.2/4.3: a `no_relevant_sources` run bypasses template rendering
entirely and emits a fixed failure notice.

### 3.6 A digit-only meta-claim pattern ate two real world claims — [observed-live 2026-09-11]

The Phase 3.4 sweep is not only a list of poison; it is the only honest test of
the filter's PRECISION, because a filter that silently deletes knowledge is
worse than the poison it removes. Running the *shipped* `classifyMetaClaim`
over all **7 744 active claims** (read-only export) matched **45**. Reading all
45 found two clear false positives:

- `d97ce55d` — "…the consent mechanism is embedded within the ChatGPT
  application's own settings interface …, though the exact UI pattern (toggle,
  checkbox, modal) **is not confirmed**." A world claim with an honest caveat.
  Caused by a bare `is not confirmed` pattern — **removed**; only
  `not confirmed as|for|in` (the "documented for X, not for what you asked"
  shape) survives, which still catches poison claim `7f2ac93b`.
- `1d448429` — "…the author found **no Sources** sheet and no Checks sheet…".
  A fact about a spreadsheet. Caused by a bare `no sources?` — **narrowed** to
  require a reporting verb within two words (`no source states/mentions/…`) or
  sentence-initial position.

After both narrowings: **43 of 7 744 match (0.56 %)**, all 8 named poison ids
still caught, and both false positives are pinned as regression tests in
`meta-claims.test.ts`. The first version of this filter would have deleted two
true claims — found only because the sweep was actually run and read.

**The sweep also exposed two claims that are the synthesizer's own prompt text**
(`7e126f61` "…Check constraints: - \"Write a thorough answer to the QUESTION
using ONLY the KNOWN CLAIMS and SOURCES provided.\"…" and `4ea270b6`
"`<a single assertion>. \" I will ensure the period is before the citation…`").
Prompt and reasoning leakage is being stored as grounded knowledge. Out of
scope here, recorded — a separate defect from meta-claims.

The full 43 are in the test plan's deploy section as an operator decision; only
the 8 the audit names get retraction SQL from this branch.

### 3.7 A sweep script that "found 0" — the failure this whole item is about

The first run of that sweep printed `active claims scanned: 7744 / matched: 0`.
The script was reading an NDJSON file line-by-line, and a botched edit left it
iterating over STRINGS: `c.text` was `undefined`, `classifyMetaClaim(undefined)`
returned `"world"`, and the scan count came out right because the file had one
line per claim. A check that passes while checking nothing, produced inside the
work item written to stop checks that pass while checking nothing. It was caught
only because 0 contradicted a known fact (the 8 poison claims are active). The
rewritten script asserts its input shape before scanning
(`if (!Array.isArray(rows) || typeof rows[0]?.text !== "string") throw`).

### 3.8 `orchestrator.test.ts` has been failing on a CORRECT database — [observed-live 2026-09-11]

Phase 5.3 built a throwaway `pgvector/pgvector:pg16` with all 30 init scripts
from `OB1/docker/docker-compose.yml` (56 tables; `find_or_create_claim`,
`link_claim_to_source`, `retract_claim`, `find_or_create_source` all present).
The integration test still failed — on the BASE commit as well as on this
branch, identically, at `FAIL: reused the grounded 'cats are mammals' claim`.

Cause: the test's `fakeEmbed` hashes a string to a single hot dimension, so
`"cats are mammals"` and `"Tell me about cats"` are near-ORTHOGONAL (distance
≈ 1.0), and `REUSE_MAX_DISTANCE` (0.55, added by change #5 after this test was
written) drops the claim before `decideReuse` is reached. With
`REUSE_MAX_DISTANCE=1.1 KB_SOURCES_MAX_DISTANCE=1.1` the BASE commit passes all
assertions, and so does this branch. **Pre-existing, not a regression**, and the
env override is in the test plan. Not fixed here: changing a shared recall
default to suit a test is how a test starts validating itself.

### 3.9 The collapse detector is wired in the HARNESS, not in `index.ts` — [read-from-source]

Phase 1.1 says "wire it into the `searchWeb` wrapper in `index.ts`". `searchWeb`
in `index.ts:324` is a `Deps` seam with no access to the run's counters, and
`fetchStats` is built in `harness.ts` (`RunResult.fetchStats`). Classifying
inside `index.ts` would have made `fetchStats.search` unreachable and untestable
through the mocked seams every other harness test uses. It is classified at the
call site in `harness.ts` instead. Same contract, one testable place.

---

## 3b. Attempt 2 — what the tester found, and what it changed (2026-09-11)

The tester FAILED the item on T7 and raised six beyond-plan findings. All were reproduced or
read before being acted on. Every fix below is RED → GREEN with the tester's own inputs as the
test data.

### 3b.1 T7 FAIL — the filter was eating real facts with an honest tail — [observed-live]

Five live claims, all `active` at 0.51, were being deleted whole because each ends in an
epistemic caveat: `51254103` (six-month wait after dissolution), `219dbaa6` (SR01 form
fields), `c1e411e4` (registered-office ordering), `d039348b` (Conventional Commits keywords),
`70f6a17c` (WSO2 licence). Each is the shape
`<a fact about the world>, but/though/; <the sources do not confirm it>`.

Two of the seven source-referential patterns — `sources (provided|given|available|held)` and
`sources (do|does) not` — accounted for 14 of the 43 live matches and caught **zero** of the 8
poison texts. That is where the precision was being spent.

**The rule now:** `headClause()` splits at the first `;` / `, but` / `, though` /
`, although` / `, however`, and the two families that can appear as a caveat
(`SOURCE_SUBJECT`, `EVIDENCE_ABSENCE`) are judged on the HEAD only. Only
`TRANSFER_DISCLAIMER` — the sentence whose whole point is that the evidence is about something
else — is judged on the whole text, because its head IS an ordinary world sentence (poison
`7f2ac93b` and `1306bb5a` both look like plain facts until the second clause).

Every honest claim is entitled to one caveat. A filter that deletes the claim because of it is
worse than the poison it removes.

### 3b.2 The second sweep, and three more false positives I found myself — [observed-live]

Re-running the rewritten classifier over all 7 744 active claims gave **28** matches. Reading
all 28 found three that are ordinary attributions, not transfer disclaimers:

- `2b508cd0` "The article notes that in August 2026, several AI browsers were demonstrated
  vulnerable…"
- `fd3e1a6d` "…(as noted in CNN's coverage) that commercial AI operators must license content…"
- `8683c8d3` "OpenAI stated that for Astra specifically, it 'invested in unspecified new
  techniques'…"

All three came from the transfer heuristic's `in` branch and its optional `that`. Reporting
what a named party said is not a transfer disclaimer; the poison shape is always "the evidence
covers X, and X is not what you asked about". Narrowed to `<verb> (this) FOR <Named thing>`
plus a contrast marker.

**Final sweep: 25 matches on 7 744 active claims (0.32 %), down from 43.** All 8 poison ids
are among them. I read all 25 and judge every one to be about the evidence set, a retrieved
page, or an explicit transfer disclaimer — the closest calls are listed in TEST-PLAN T7 so a
reader can disagree with a named claim rather than with a number.

Sweep precision across the three iterations: 43 (2 known false positives) → 28 (3 found by
reading) → **25 (none I can name)**. Each round found its errors by reading the list, not by
running the tests.

### 3b.3 B1 — the detector condemned a search that worked — [reproduced]

`classifyHits("Kubernetes CrashLoopBackOff diagnose", <5 perfect hits>)` returned
`{"verdict":"collapsed","overlap":0,"collapsedOn":"crashloopbackoff"}`. `overlapRatio`
demanded two DISTINCT query terms per hit, so a query whose subject is one strong token plus
generic words scored 0.00 on a flawless result set — and the dominance test then found that
token in 100 % of titles, which is the collapse signature exactly. The run would have ended
`search_degraded` and told the user a working search had failed.

Fix: a hit carrying the query's LONGEST term counts as overlapping even alone, when that term
is ≥ `ANCHOR_MIN_LEN` (7) characters. The threshold is the safety argument and it is drawn
from the measurement: the audited collapse tokens are `dell` (4), `most` (4) and `100` (3),
all below 7, so no recorded failure can be rescued by it. All three collapse fixtures still
classify `collapsed` (pinned as its own test).

### 3b.4 B2 — ten pages of noise were a "successful search" — [reproduced]

`classifyHits("OptiPlex 3050 capacitor bulging repair", <10 "Best Buy Deals" hits>)` returned
`{"verdict":"ok","overlap":0}`: with no single QUERY token dominating the titles, the old rule
had nothing to say. Those ten pages were then fetched, judged by the relevance gate, and
counted in `SearchStats.ok` as engine health.

New verdict `offtopic`: overlap 0 across at least `OFFTOPIC_MIN_HITS` (5) hits. It yields no
pages, feeds the degraded streak, and is folded into "junk" by `searchHealthLabel` and the
footer. A THIN set (3 hits) is still `ok` — three hits is not a verdict about an engine.

### 3b.5 B3 — the empty-pool guarantee covered one of four paths — [read, then reproduced]

`no_relevant_sources` was gated on `topicPath`, so article / sources-only /
`disable_web_search` runs with an empty pool still asked the synthesizer to write from nothing
(`synthCalls=1` on all four), and with one recalled claim still reached the curator with
`sources: []` and the poison sentence as the package's headline claim. The anchor states
criterion 1 unconditionally.

The decision is now made on the CITABLE POOL — staged pages with content, plus the grounding
sources of any reused claim — on every path. That required hoisting `getReuseSources()` above
the decision, since the decision has to know whether a reused claim brings a source with it. A
non-empty pool behaves exactly as before on all four paths; the integration test (which
exercises the reuse path end to end against the real schema) still passes.

### 3b.6 B5 — the grounding check was checking digit presence — [read, then reproduced]

`[Source N]` was stripped from the LINE and never from the SOURCE text, so a fabricated
"95 °C" was grounded by `[Source 95]`, `Source 95`, `page 95`, a bare `[95]` reference marker,
or "95 mm" — and real pages are full of those. The word table was worse: `100` → "hundred",
`1` → "one", `2` → "two" made the check weakest on exactly the integers a fabrication is most
likely to use.

Fixes: pointer spans (citations, page/figure/table/section numbers) are cut out of the source
text before comparison; a figure that carries a UNIT must match that unit in the source; the
word table now runs 3..90 only. "thirty" still grounds 30 — the 100 Hz CAREN line is why the
table exists — and all ten of that run's `[SOURCED]` lines still survive.

**Over-tightening is the risk here and it bit once during the fix:** the first unit rule ended
in a word boundary, which needs a word character after the unit, so "26% motion sickness"
failed to ground the claim's own "26%" and downgraded the verified GVS line. Caught by the
existing 100 Hz regression test.

### 3b.7 B7 — and the second defect it exposed — [observed]

The test named "recall pages are gated too" ran against a stub returning zero rows: the
audited failure's actual mechanism had no test. It now runs against a client serving the
audited run's own three recalls (DGX Spark guide, Compaq d220 manual, ASUS BIOS FAQ) and
asserts the gate was ASKED about each by name.

Writing that test exposed a real defect: **the fail-safe floor was re-admitting the recalls.**
They are gated before round 1, when the collapse counters are still zero, so the floor's "the
gate would empty the pool" branch fired and put the DGX Spark pages straight back —
reproducing the audited failure through the mechanism added to prevent it. The floor now never
applies to recalls: their only credential is vector proximity, and the floor exists to
second-guess a model verdict about a page the run went out and FETCHED for this question.

### 3b.8 A field that was read and never written — [read-from-source]

Found while wiring `offtopic` through: `SearchRecord.ok/collapsed/empty/errors` were declared
in attempt 1, read by `searchHealthLabel()` and `coverageFooter()`, and **never assigned by
the harness**. No real run could have printed `search: DEGRADED`; only the hand-built fixtures
in `report.test.ts` ever did. The harness now copies the counters into the record, and a test
asserts the RUN's own record carries them and that the footer says DEGRADED.

Nobody's test caught this, including the tester's. It is the same failure class as the sweep
script that reported 0 matches: a check that passes while checking nothing.

### 3b.9 Test counts after attempt 2 — [observed-live 2026-09-11]

| suite | baseline | attempt 1 | attempt 2 |
|---|---|---|---|
| `research-service` | 79 passed / 1 env-failed | 121 / 1 | **136 / 1** |
| `research-curator` | 17 passed | 28 | **33** |
| `gateway` `test_engine_health.py` | n/a | 8 | 8 |
| `ruff check .` | clean | clean | clean |
| integration (`orchestrator.test.ts`, throwaway DB) | pre-existing fail | PASSED | **PASSED** |

The one `research-service` failure is `orchestrator.test.ts` in every column (§3.8).

### 3b.10 Tester notes taken as-is

- **D.1 pinned a tag, not a digest, and had no pre-pull check.** Added: compare
  `RepoDigests` before and after `docker pull`, with the digest-pin form offered as the better
  option.
- **D.2 had no rollback where D.1 did.** Added: tag the `:local` images to
  `:pre-research-trust` BEFORE the rebuild, with the note that a rollback does not undo a
  retraction if D.4 has already run.
- **The plan's "Declared" section quoted the pre-amendment anchor.** Rewritten: the anchor now
  says 10 and the section records why the number changed rather than raising a dispute.
- **T5's changed `coverage 75%` assertion** — the tester judged the replacement strictly
  stronger. Kept as is.

---

## 3c. Attempt 3 — the guard stops being a constant (2026-09-11)

The tester failed T11 on attempt 2. Both failed attempts had the same shape: **a guard whose
safety argument was a constant fitted to the recorded incident** — first an 8-string pattern
list, then a 7-character token length. The third attempt does not pick a better constant.

### 3c.1 What failed — [reproduced on live]

`ANCHOR_MIN_LEN = 7` separated SHORT collapse tokens from LONG ones, not collapse tokens from
subject entities. Bing collapses onto the query's first salient token, and the head nouns of
the questions this engine exists for are long. Four read-only `GET :8085/search` probes,
re-captured by me today and now shipped as fixtures:

| query | shipped verdict (attempt 2) | what came back |
|---|---|---|
| `capacitor bulging OptiPlex 3050 repair` | **ok, overlap 0.90** | Capacitor – Wikipedia, How Capacitors Work |
| `motherboard VRM failure OptiPlex 3050` | **ok, overlap 0.70** | Motherboard – Wikipedia, Motherboards \| Amazon |
| `vestibular suppression 100 Hz auditory tone` | collapsed (by luck) | Vestibular Disorders, Vestibular system – Wikipedia |
| `semaglutide gastroparesis incidence` | collapsed (by luck) | Semaglutide – Wikipedia, Drugs.com |

The first two are the audited failure exactly — one engine, ten pages on topic for a single
token, nothing about the subject — reported as a healthy search. Downstream that means
`searchStats.ok++`, so the junk counter never rises, the streak never fires, and ten junk pages
spend the fetch budget while the operator is told the search worked.

The last two "passed" only because the longest term happened to differ from the collapse token.

### 3c.2 The structural fact that was already there — [read-from-source]

The run already knows its **subject entity**: `KEYWORDIZE_SYS` extracts it and `keywordQuery()`
forces it into every round-1 and DEEPEN query. Nothing was asking whether the results contained
it. `classifyHits(query, hits, entity)` now does, and the entity is a property of the question,
not a number chosen to fit an incident.

### 3c.3 ENTITY_SHARE, measured — [observed-live 2026-09-11]

Entity-phrase share across every fixture in `research-service/fixtures/`:

| set | share | provenance |
|---|---|---|
| `search-good-optiplex` | **0.75** (9/12) | throwaway rig, pinned image — 3 hits are 3060/general Dell |
| `probe-good-oomkilled` | **1.00** | hand-built control (the tester's) |
| `probe-good-iphone` | **1.00** | hand-built control (the tester's) |
| `search-collapsed-dell` / `-most` / `-the100` | **0.00** | live gateway, the audited failure |
| `probe-collapsed-capacitor` / `-motherboard` / `-vestibular` | **0.00** | live gateway, the tester's T11 probes |

`ENTITY_SHARE = 0.5` is the midpoint of a gap that runs from 0.00 to 0.75 — the widest a
threshold can sit in. Overlap is still computed and reported; it is now secondary evidence
rather than the gate, which is the whole correction.

### 3c.4 The deliberate exception, stated so it can be argued with

`semaglutide gastroparesis incidence` returns ten real semaglutide pages that never mention
gastroparesis: entity share 1.00, verdict **`ok`**, overlap 0.00. The engine understood the
SUBJECT and missed the NEED, and the relevance gate is what rejects a page that does not answer
a need. Declaring a search broken because the engine returned pages about the right thing would
be attempt 1's error pointed the other way. Pinned as its own test and written into T11 so a
reader can disagree with the judgement rather than discover it.

### 3c.5 Where there is no entity

Article-mode preliminary gap searches and any legacy caller pass none, and fall back to the
old two-term overlap rule — weaker, because without the subject it cannot tell "ten pages about
capacitors" from "ten pages about this capacitor". The fallback is documented in the code and
pinned by a test that constructs the set which separates the two rules (overlap 1.00, entity
share 0.00). The prelim-gap call site now at least *classifies* its results and skips a
non-`ok` set instead of fetching it. A wrong verdict there costs one tentative paragraph; the
topic path, where the audited failure lives, always has an entity.

### 3c.6 X1 — the `^` anchor, and the false positive fixing it produced

`SOURCE_SUBJECT` is `^`-anchored on the head clause, which is what makes it test the SUBJECT
rather than fire on any mention of "the sources" — and a LEADING subordinate clause defeated
it. The head is now split at a leading `While|Although|Though|Whereas|Even though` and every
resulting clause is judged.

The first version of that split also stripped `since|because|if|when|given that` **anywhere**
in the head, and the sweep immediately caught the cost: `083b830e`, "…is architecturally
distinct from the adversarial-prevention layer, **since the sources describe these as
independent properties**", went meta. A reason clause is a justification for a world claim —
the same kind of tail as "but no source confirms it". Only a LEADING contrast subordinator
restructures the sentence; that is now the rule, and both directions are pinned as tests.

**Sweep: 25 of 7 744, the SAME 25 ids as attempt 2** (verified by diffing the two runs), which
the tester independently read and judged evidence-side. Recall widened by four reworded poison
shapes; precision did not move.

### 3c.7 X4 — the two renderers now emit the same string

`offtopic` reached `report.ts` and not `owui/tools/deep_research.py`, so the two disagreed in
wording ("collapsed" vs "junk") and content (the Python side had no DEGRADED line at all). Both
now produce, for the same record, byte-identically:

```
needs answered 0 of 1 · sources 0 relevant of 0 fetched (30 hits, 3 junk) · search: DEGRADED (3 of 3 searches returned junk) · stopped early: search_degraded
```

Verified by running both and comparing the strings; T16 makes the tester do the same.

### 3c.8 Counts after attempt 3 — [observed-live 2026-09-11]

| suite | baseline | attempt 1 | attempt 2 | attempt 3 |
|---|---|---|---|---|
| `research-service` | 79 / 1 | 121 / 1 | 136 / 1 | **143 / 1** |
| `research-curator` | 17 | 28 | 33 | **36** |
| `gateway` pytest | n/a | 8 | 8 | 8 |
| `ruff check .` | clean | clean | clean | clean |
| integration (throwaway DB) | pre-existing fail | PASSED | PASSED | **PASSED** |

### 3c.9 Plan hygiene the tester flagged

- D.4 quoted the stale "43 of 7 744" — now 25, with the provenance of that number.
- T8 required `mojeek` in `unresponsive_engines`; it was `[]` on 4 of 4 probes. A suspension is
  transient state, not a property of this change. Restated as a CONFIGURATION check: mojeek
  disabled in the shipped `settings.yml` with its contractual reason, and absent from live
  results.
- Preconditions said "T1–T9"; there are 16 cases.

---

## 4. Out of scope, recorded rather than built

- **The `engines=` fallback hazard.** `search-engine-alternatives-2026-09-11.md`
  §8.10: naming an unknown engine in `engines=` makes SearXNG silently query the
  full default set instead of erroring. Our gateway does not forward `engines=`
  (verified: a request to `127.0.0.1:8085/search` with `engines=yandex` still
  returned bing-only results, while the same parameter against `searxng:8080`
  returned yandex) — so the hazard is not reachable through the gateway today.
  Nothing built. — [observed-live 2026-09-11]
- **Wikipedia is being sent whole sentences as page titles.** Same note, §5:
  `/page/summary/The%20article%20does%20not%20disclose%20pricing…` → HTTP 400.
  That is the wikipedia engine's own query handling, not ours. Not touched.
- **Skeptic trial (Phase 2.4)** — see §7.

---

## 5. Deployment consequences the operator must know

1. **The engine policy REQUIRES the pinned image.** On the 2026.5.17 image that
   is live today, `google`, `brave`, `qwant` and `duckduckgo web` do not answer
   (TLS fingerprint rejection / CAPTCHA / engine does not exist). Deploying
   `settings.yml` without `SEARXNG_IMAGE` would leave **yandex alone** answering.
   The two changes land together or not at all.
2. `duckduckgo web` is a **different engine** from `duckduckgo`; the latter still
   CAPTCHAs and stays disabled.
3. The image jump is 2026-05-17 → 2026-09-11 on privacy infrastructure. The
   README's own rule (re-verify `routes/searxng_compat.py` against the new
   `settings.template.yml`) applies; the deploy section of the test plan carries it.

---

## 6. Test counts, RED → GREEN — [observed-live 2026-09-11]

| suite | before | after | delta |
|---|---|---|---|
| `research-service` `deno test -A` | **79 passed, 1 failed** | **121 passed, 1 failed** | **+42** |
| `research-curator` `deno test -A` | **17 passed, 0 failed** | **28 passed, 0 failed** | **+11** |
| `search-gateway/gateway` `pytest tests/test_engine_health.py` | n/a (new file) | **8 passed** | **+8** |
| `ruff check .` (parent) | clean | **clean** | — |

The one `research-service` failure is `orchestrator.test.ts` in both columns and
is §3.8 — it needs a database, and with one it needs
`REUSE_MAX_DISTANCE=1.1`. Under those conditions it passes on this branch AND on
the base commit (Phase 5.3, verified 2026-09-11).

New test files: `search-quality.test.ts` (9), `grounding.test.ts` (7),
`report.test.ts` (7), `harness-trust.test.ts` (13), additions to
`templates.test.ts` (3) and `lib.test.ts` (3);
`research-curator/meta-claims.test.ts` (11);
`search-gateway/gateway/tests/test_engine_health.py` (8).

RED was shown before GREEN for every one of these: the module-absent type-check
failure for `search-quality.ts`, `grounding.ts`, `report.ts` (report.ts was
moved aside and the suite re-run to demonstrate it) and `meta-claims.test.ts`,
and named assertion failures for the harness replays before the harness changes
landed.

---

## 7. Phase-by-phase record

| phase | state | where |
|---|---|---|
| 0.1 fixtures | done | `OB1/integrations/research-service/fixtures/` (6), `research-curator/fixtures/` (2) |
| 0.2 baseline | done | §1.2 above |
| 0.3 scaffolding | done | §1.1, §6 |
| 1.1 collapse detector | done | `search-quality.ts:classifyHits`, wired at `harness.ts` `runSearch` |
| 1.2 keyword round 1 | done | `harness.ts:KEYWORDIZE_SYS`, `search-quality.ts:keywordQuery/reformulate` |
| 1.3 engine health + measured change | done | `engine_health.py`, `routes/health.py:/health`, `stack.ps1`, `settings.yml`, `search/docker-compose.yml`, `.env.example`; §2 |
| 1.4 yield target + collapse streak | done | `harness.ts` gather loop, `RELEVANT_TARGET`, `COLLAPSE_STREAK_MAX` |
| 1.5 readable vs relevant vs absent | done | `FetchOutcome` split in `index.ts`/`harness.ts`, `fetch_degraded`, `FETCH_MAX_CHARS` 8000→16000, slice 2000→4000 |
| 2.1 gate recall pages | done | `harness.ts` `kbRecalled` + `gateAndKeep` (topic path only) |
| 2.2 `no_relevant_sources` | done | `harness.ts` early return; `lib.ts:classifyCuratorOutcome` skipped branch |
| 2.3 numeric grounding | done | `grounding.ts`, wired before `buildCitedAndRenumber` |
| **2.4 Skeptic trial** | **NOT DONE — deferred** | see §8 |
| 3.1 meta-claim filter | done | `claims.ts:classifyMetaClaim` + `writeClaims`; judge in `research-curator/index.ts` |
| 3.2 omnibus downgrade | done | `claims.ts:isOmnibusCitation` |
| **3.3 retraction** | **NOT RUN — post-deploy** | SQL shipped in TEST-PLAN §D |
| 3.4 sweep | done (read-only) | §3.6; 43 of 7 744 |
| 4.1 honest coverage | done | `report.ts:coverageFooter`, `lib.ts:renderResult`, `deep_research.py` |
| 4.2 template by evidence | done | `report.ts:shouldClassifyTemplate`, `harness.ts` |
| 4.3 answer-first structure | done | `templates.ts`, `report.ts:failureNotice` |
| 4.4 OWUI parity | done | `owui/tools/deep_research.py` v1.3.0 (operator must re-paste) |
| 5.1 unit + lint | done | §6 |
| 5.2 replay | done | `harness-trust.test.ts` replays both audited runs from fixtures |
| 5.3 test images + throwaway DB | done | images `openbrain-research:wt-research-trust`, `openbrain-curator:wt-research-trust` built; `deno check index.ts` passes in both; all three new modules verified present in the image (`COPY *.ts` — checked, because a COPY-by-name list is a repeat failure here) |
| **5.4 live dry_run** | **NOT MINE — post-deploy** | TEST-PLAN §D |
| 5.6 land | tester/reviewer | TEST-PLAN §E |
| 5.7 docs | done | `documentation/implementation-guide/README.md` row |

---

## 8. What could NOT be done, and why

1. **Phase 2.4 — the Skeptic trial.** It requires setting `SKEPTIC_ENABLED=1` on
   a running image and replaying two audited jobs plus three notebook jobs
   through a real LLM. Every test in this branch runs on mocked chat/search/fetch
   seams by instruction; a Skeptic trial is by definition a live-model
   experiment, and the two audited jobs cannot be re-run without the deployed
   service. `SKEPTIC_ENABLED` is therefore UNCHANGED (still `0`) and nothing in
   this branch turns it on. Deferred to the post-deploy step, with the plan's own
   adoption rule intact: adopt only if it downgrades the 95 °C line and
   downgrades none of the verified 100 Hz lines. — [not-verifiable-here]
2. **Phase 3.3 — the retraction.** A `retract_claim` is a write to the live
   knowledge base and a post-deploy step by the plan's own ordering (it must run
   AFTER 3.1 is deployed, or the next run re-creates the poison). The exact SQL
   for all 8 ids is in TEST-PLAN §D.
3. **Phase 5.4 — live dry-run jobs.** Requires the rebuilt images running under
   the Open Brain plane lease. Not a development step.
4. **Whether the new engine mix survives production pacing.** brave rate-limited
   after ~5 burst queries on the rig, as the exploration note also found. The
   collapse detector is what catches it if it regresses — that is the point of
   shipping both together. — [not-verifiable-here]
5. **The `/health` route itself is not covered by an HTTP test.** `pytest` for
   the gateway needs `redis` and `respx`, which are not installed on the host
   python; installing them would mutate the operator's environment. The LOGIC is
   pure and fully tested (`test_engine_health.py`, 8 cases); the route is thin
   wiring. TEST-PLAN case 12 has the tester curl it after deploy.

## Deploy 2026-09-11 (section D executed by the operator's session) - findings

Provenance: observed-live on 2026-09-11 via `POST :8818/research` dry runs
8f875b10 (OptiPlex, before retraction), 1f2ff740 (OptiPlex, after), b7e701ef (100 Hz),
and gateway replays via `GET :8085/search`.

- **D.1 exposed a latent compose defect, not this branch's.** Commit d504e9e (2026-08-28)
  removed `env_file` from the search gateway and named only `SEARXNG_URL`/`REDIS_URL`;
  `Settings.gateway_api_key` is `Field(...)` (required), so the first recreate crash-looped
  on `GATEWAY_API_KEY Field required`. Fixed in `search/docker-compose.yml` (208f384) under
  the search lease. The old container had survived on the pre-d504e9e environment.
- **Search plane after D.1:** `GET /health` -> 4 engines answering (google, brave, yandex,
  duckduckgo web; qwant unresponsive once); the OptiPlex capacitor query returns 20 on-topic
  pages (Dell support, CNET capacitor-plague, repair forums). `stack.ps1 health`:
  `search: ok - 4 engine(s) answering`.
- **D.3 OptiPlex: criterion MET on branch 1** - 11 (then 9) relevant OptiPlex sources cited
  (Dell diagnose pages, manuals, defect DB, a used-OptiPlex blog). Post-retraction the reuse
  pass no longer recalls 0c2b7c4e. Concerns: (a) the Answer block and TITLE were built on the
  weakest cited source (a Walmart review page, 21 ratings, "38% one-star"); the answer-first
  template needs source weighting. (b) `needs answered 0 of 6` while 12 relevant sources were
  cited and the report states findings - the COVERAGE_STAGED judge marks nothing answered;
  footer and body disagree. (c) 2 of 6 searches were called `collapsed onto "dell"` because
  the entity was "Dell OptiPlex 3050" and hits say "OptiPlex 3050" without the brand.
  (d) reuse recall still pulls 12 semantically-near DGX-Spark claims at distance <= 0.55.
- **D.3 100 Hz: retrieval criterion NOT MET, and the search plane is not why.** All 3
  searches were classified `collapsed onto "motion"/"sickness"` -> `search_degraded`, 0
  fetched, honest footer + no absence title (that part held). Replaying the same queries
  through the gateway: 20 hits each, 8-9 of 20 contain "100 Hz", and PMC11955832 /
  J-STAGE "Just 1-min exposure to a pure tone at 100 Hz" (the Nagoya paper) is hit #1 in two
  of them. Deployed `classifyHits` on those exact hits: entity "100Hz audio" -> share 0.00,
  "100Hz" -> 0.15, "100 Hz" -> 0.45 (< the 0.5 line), "motion sickness" -> 0.95 ok. The
  entity phrase is matched too literally (no `100Hz`~`100 Hz` normalisation, no core-token
  fallback, brand/qualifier words included), and ENTITY_SHARE=0.5 sits ON a real edge
  (0.45), not in an empty gap as the fixtures suggested. This is the tester's X2 doubt
  materialised from the other side: the input to the rule was an over-specific model output.
  Failure direction is over-caution (nothing fabricated, curator skipped) - but it turns a
  working search into "search failure", which is exactly the outcome the anchor exists to
  prevent. FOLLOW-UP ITEM REQUIRED (not a re-open of research-trust): normalise unit/number
  spacing and hyphens, match on the entity's distinctive core tokens (drop brand/qualifier
  words: Dell, audio, VR), validate/log the KEYWORDIZE entity, and re-measure ENTITY_SHARE
  against these two live sets before choosing a threshold.
- **D.4:** all 8 ids `retracted` with reason `research-audit 2026-09-11: ...`; count 8.
- **D.5:** deep_research.py v1.3.0 written through `POST /api/v1/tools/id/deep_research/update`
  (the API path refreshes OWUI's tool cache; valves compared before/after).

---

# research-trust-entity (item 2, 2026-09-11)

Follow-up to the deploy findings above. The deployed detector reported two working
searches as failures because it compared the subject entity as a literal string.

## E.1 The RED baseline, re-measured on live payloads — [observed-live 2026-09-11]

All five queries re-captured today from `GET :8085/search` (3-4 engines answering) and
shipped as fixtures with provenance. The DEPLOYED `classifyHits` on them:

| fixture | entity | verdict | share |
|---|---|---|---|
| `live-100hz-mechanism` | `100Hz audio` | **collapsed on "motion"** | 0.00 |
| `live-100hz-mechanism` | `100Hz` | **collapsed on "motion"** | 0.15 |
| `live-100hz-mechanism` | `100 Hz` | ok | 0.55 |
| `live-100hz-studies` | `100Hz audio` | **collapsed on "motion"** | 0.00 |
| `live-100hz-studies` | `100 Hz` | **collapsed on "motion"** | 0.35 |
| `live-optiplex-thermal` | `Dell OptiPlex 3050` | **collapsed on "dell"** | 0.15 |
| `live-optiplex-health` | `Dell OptiPlex 3050` | ok | 0.55 |

`live-100hz-mechanism` and `-studies` both carry the Nagoya paper
("Just 1-min exposure to a pure tone at 100 Hz…", PMC11955832 / J-STAGE) — at rank 1 in
`-studies`. The pages the run was sent to find were in the results it threw away.

## E.2 The rule: match the distinctive CORE — [read-from-source]

`search-quality.ts` — `entityTokens()`, `entityCore()`, `hitCarriesEntity()`.

1. **Tokenise splitting digit/letter runs**, so `100Hz`, `100 Hz` and `100-Hz` all become
   `["100","hz"]`. The deployed version tokenised on `[a-z0-9]+`, making `100Hz` the single
   token `100hz`, which matches nothing a page writes.
2. **The CORE** is what survives dropping leading and trailing tokens that carry no
   distinctiveness — a brand (`Dell`), an article, a medium or context word (`audio`, `VR`) —
   while a distinctive token remains. Distinctive = contains a digit, or ≥5 chars and not in a
   short closed QUALIFIERS list. Worked: `Dell OptiPlex 3050` → `optiplex 3050`;
   `100Hz audio` → `100 hz`; `VR motion sickness` → `motion sickness`.
3. **A unit is never orphaned from its number** — reducing `100 Hz` to `100` would match
   *The 100* (TV series), which is one of the six collapse fixtures. Only a token of ≤4
   characters can be a unit: an early version protected `desktop` beside `3050` and left the
   core as `optiplex 3050 desktop`, which no page writes.
4. **A numeric final token may carry up to two trailing letters**: the OptiPlex 3050 is
   written `3050m`, `3050 SFF`, `3050MT`. `3060` is still a different machine, and `3050`
   still cannot match inside `30500`.

## E.3 ENTITY_SHARE re-measured over ALL recorded sets — [observed-live 2026-09-11]

| share | set | class |
|---|---|---|
| 1.00 | `probe-good-oomkilled` | GOOD (control) |
| 1.00 | `probe-good-iphone` | GOOD (control) |
| 0.75 | `search-good-optiplex` | GOOD (throwaway rig) |
| 0.65 | `live-optiplex-health` | GOOD (live, dry run 1f2ff740) |
| 0.55 | `live-100hz-mechanism` | GOOD (live, dry run b7e701ef) |
| 0.35 | `live-100hz-studies` | GOOD (live, dry run b7e701ef) |
| 0.30 | `live-optiplex-thermal` | GOOD (live, dry run 1f2ff740) |
| **0.175** | — | **ENTITY_SHARE** |
| 0.05 | `live-100hz-ssq` | OFF-NEED (live; the query is about SSQ scores) |
| 0.00 | `search-collapsed-dell`, `-most`, `-the100` | COLLAPSED |
| 0.00 | `probe-collapsed-capacitor`, `-motherboard`, `-vestibular` | COLLAPSED |

(`probe-collapsed-semaglutide` sits at 1.00 and is deliberately `ok` — the entity-present
weak-search exception decided in the previous item.)

**0.175 is the only value satisfying the anchor's no-edge rule.** The real gap is
0.05 → 0.30, so a threshold must be >0.15 and <0.20 to keep every recorded set more than 0.1
away; 0.175 sits 0.125 from each side.

**The thinness is the finding.** Chosen from the curated fixtures alone the gap looked like
0.00 → 0.75 and 0.5 looked safe. Against live sets the headroom is 0.125, and four GOOD sets
were below the old line. A threshold measured only on the examples that motivated it will
look well-separated and be on an edge.

## E.4 Validating the entity itself — [read-from-source]

`entityStatusFor()` returns `used` / `missing` / `rejected`. KEYWORDIZE is a model and can
return a subject the query never mentioned; gating on that would condemn every search for a
question it misread. The entity is used only when the QUERY carries its core (core-matched
too, because a DEEPEN query may carry `OptiPlex 3050` while the entity is branded).

Both non-`used` cases fall back to the overlap rule **and are counted** —
`fetchStats.search.entity_missing` / `entity_rejected`, carried into `search_record` and
printed in the footer by both renderers:

```
… · entity gate: 2 search(es) judged without it (2 rejected the run's subject)
```

A silent fallback is a gate reporting health it never measured.

## E.5 Coverage vs footer — [observed-live, recorded as a fixture]

Live dry run 1f2ff740 (`fixtures/dryrun-optiplex-1f2ff740.json`): 11 cited sources, 25
grounded lines, and a footer reading `needs answered 0 of 6`. COVERAGE_STAGED marked every
need open.

Both halves are true — sources can support many facts without settling any one sub-question —
and what was false was the footer's silence about the second number. `reconcileNeedsStatus()`
(`report.ts`) turns an `open` need the synthesis actually grounded into `partial`, and the
footer prints `needs answered X of N (Y partly)`.

Three things it deliberately does NOT do: manufacture an `answered` (overruling the judge on a
term overlap would be a worse lie than the one being fixed); reopen a `search_failed` need (a
line grounded from the reuse pool does not mean the search succeeded); or change anything when
there is no synthesis.

## E.6 A regex with a literal backspace in it — [observed]

While writing `groundedNeeds()`, an escaping slip put `\x08` where `\b` belonged:
`/\[Sources?\x08[^\]]*\]/i`. It is invisible in an editor, the file type-checks, and the
filter it guards silently matched **zero** lines — so `reconcileNeedsStatus` returned every
need unchanged and looked like it was working. Caught because the test asserted a POSITIVE
(`partial > 0`), not merely "no exception". Third instance in this workstream of a check that
passes while checking nothing; the first two were a sweep script iterating over strings and a
`SearchRecord` field read but never written.

## E.7 Counts — [observed-live 2026-09-11]

| suite | before (merged research-trust) | after |
|---|---|---|
| `research-service` `deno test -A` | 143 passed / 1 env-failed | **173 / 1** |
| `research-curator` | 36 | 36 (untouched) |
| `ruff check .` | clean | clean |
| integration (`orchestrator.test.ts`, throwaway DB) | PASSED | **PASSED** |

New files: `entity.test.ts` (22), `coverage.test.ts` (8). New fixtures: five live payloads
plus the recorded dry-run result.

## E.8 Out of scope, recorded

- **Source weighting for the Answer block.** The OptiPlex report's title and Answer were built
  on a Walmart review page (21 ratings). Named out of scope in this anchor; still true.
- **Reuse recall at distance ≤ 0.55** still pulls semantically-near DGX Spark claims.
- `live-100hz-ssq` (share 0.05) is genuinely off-need: that query asks about Simulator
  Sickness Questionnaire scores and the engine answered it. Marking that need `search_failed`
  is correct, not a defect.

## E.9 Attacking my own rule found two more brand-shaped holes — [observed]

The first version of `entityCore` dropped a leading token only when it was under five
characters. That works for `Dell` **by accident**: it is the length of the brand in the
incident, not a property of brands. Running the plan's own "try to break it" instructions
before shipping:

| entity | first core | page it failed to match |
|---|---|---|
| `Lenovo ThinkCentre M910q` | `lenovo thinkcentre m 910 q` | "ThinkCentre M910q Tiny teardown" |
| `NVIDIA GeForce RTX 3050 Ti` | `nvidia geforce rtx 3050 ti` | — |
| `Microsoft Surface Laptop 5` | `microsoft surface laptop 5` | — |

The rule is now anchored on the MODEL NUMBER: the identity starts at the token immediately
before the first digit-bearing token, so the brand falls away whatever its length. Two guards
came out of the same exercise:

- `Apple MacBook Air M2` reduced to `m 2`, which matches **`M.2`** — a string that appears in
  the OptiPlex fixture's own hit titles. A one-character number is not a model code.
- `Microsoft Surface Laptop 5` trimmed to `5`, which matches any page with a 5 in it. A bare
  number is never an identity.

`RTX 3050 benchmark` still does not satisfy `RTX 3050 Ti`, and `OptiPlex 3060` still does not
satisfy `OptiPlex 3050`. All five are pinned as tests.

This is the fourth time in this workstream that the fix for a fitted constant was itself
fitted, and the first time it was caught before submission rather than by the tester.

## E.10 The consequence of the bare-number guard: weak cores that admit unrelated text — [reproduced 2026-09-11, reviewer; live counter-measurement observed by the tester]

Written at merge time (reviewer rt-reviewer, 2026-09-11) because E.9 records the two guards
and not what one of them leaves behind. The tester raised it as X1 in
`.git/agent-worktrees/queue/research-trust-entity.attempt1.evidence.md`; it is recorded here so
the next item reads it without going back to an attempt file.

`entityCore` drops a leading token when it is in the closed `QUALIFIERS` list, and the
bare-number guard then restores the untrimmed pair rather than the full entity. For a product
whose token before the model number happens to be a qualifier, the surviving core is not
distinctive. Reproduced by the reviewer against the shipped `entityCore` at OB1 `5c189cf`:

| entity | core | matches |
|---|---|---|
| `Microsoft Surface Laptop 5` | `laptop 5` | "Best laptop 5 years running", "This laptop 5 hour battery test" |
| `Tesla Model 3` | `model 3` | "Model 3 of the regression showed a weaker effect" |
| `Dell OptiPlex 3050` | `optiplex 3050` | (unaffected — "optiplex" is not a qualifier) |

A twenty-hit set in which four rows carry such a phrase scores **0.20**, which is above
`ENTITY_SHARE = 0.175`, so `classifyHits` returns `ok`. The boundary still holds in the other
direction: `Surface Laptop 6 battery drain` does not match the `laptop 5` core.

**No live harm has been found, and the entry should not be read as a defect report.** The
tester built the junk sets above by tuning them to sit just over the threshold, then measured
the real thing: a read-only search for `Microsoft Surface Laptop 5 battery drain fix` returned
20 hits across brave / duckduckgo web / yandex at share **0.35**, every matching row genuinely
the right machine (iFixit, r/Surface, Microsoft Q&A). Publishers write the full product string
far more often than a constructed set assumes. That measurement is the tester's, made
2026-09-11 against the live gateway, and has not been re-run since.

What this is for: the margin, not the mechanism. `ENTITY_SHARE` sits 0.125 from the nearest
recorded set on each side (E.3), and this is the shape that eats into the lower half of that
margin — a brand-qualifier-number subject, of which `Surface Laptop N` and `Model N` are only
the two that were tried. Anyone re-measuring the threshold, or extending `QUALIFIERS`, should
start here: widening the list makes MORE entities reduce to a weak core, which is the opposite
of the intuitive direction.

Not attempted, and worth saying so rather than implying it was ruled out: whether a weak core
plus a genuinely collapsed engine (the audited first-token failure) can combine to reach 0.175
on live data. Both halves are documented; nobody has put them together.

## Deploy round 2, 2026-09-11 (research-trust-entity at OB1 5c189cf) - dry-run findings

Provenance: observed-live 2026-09-11 via `POST :8818/research` dry runs 6975d982 (100 Hz)
and 4826d896 (OptiPlex) on `openbrain-research` label 5c189cf; classifier replays via a
labelled deno container against the deployed source.

- **OptiPlex (4826d896): acceptance 7 MET.** 5 of 5 searches `ok` (entity shares 0.875 /
  1.00 / 0.75 / 0.625 / 0.625), 25 fetched, 12 cited, footer `needs answered 1 of 6
  (5 partly)` beside a body that states findings - consistent. Title leads with documented
  failure modes (RAM, power cabling, M.2 limitation), not a retail review.
- **100 Hz (6975d982): acceptance 6 NOT MET - a third mechanism.** KEYWORDIZE returned the
  subject `"100Hz audio VR motion sickness"` (five words; the prompt's own examples are
  "OptiPlex 3050", "semaglutide"). `entityCore` anchors on the token before the first
  digit-bearing token; here the digit token is FIRST, so nothing is dropped and the core is
  all six tokens `[100 hz audio vr motion sickness]`, a phrase no page carries -> share 0.00
  on a hit set where "100 Hz" is in 9 of 20 hits and PMC11955832 is hit #1 -> `collapsed
  onto "motion"` x3 -> `search_degraded`, 0 fetched. Replayed on the deployed source:
  entity "100Hz audio" -> core `[100 hz]` -> 0.45 ok; the five-word entity -> 0.00 collapsed.
  So the rule is right for a NAME and wrong for a TOPIC handed to it as if it were a name;
  nothing bounds the entity's length and `entityStatusFor` only checks presence in the query.
  Direction of failure: over-caution (no fabrication, curator skipped) - but the report still
  says "search: DEGRADED" about a healthy search plane. Item `research-trust-core` opened.

---

# research-trust-core (item 3, 2026-09-11)

Follow-up to deploy round 2. The deployed detector reported three healthy searches as
failures because the subject it was handed was a TOPIC, not a name.

## F.1 The mechanism, reproduced — [observed-live 2026-09-11]

Dry run 6975d982: KEYWORDIZE returned the subject `"100Hz audio VR motion sickness"`. The
deployed `entityCore` anchors on the token BEFORE the first digit-bearing token; here the
digit token is FIRST, so nothing was dropped and the core became all six tokens
`[100 hz audio vr motion sickness]` — a phrase no page carries. Share **0.00** on a hit set
where "100 Hz" is in 9 of 20 rows and PMC11955832 is rank 1; `collapsed onto "motion"` three
times; `search_degraded`; nothing fetched. With the entity `"100Hz audio"` the same code gives
core `[100 hz]` → 0.45 → ok.

Two gaps, not one: the core rule had no case for a digit-first subject, and **nothing bounded
the entity's length** — `entityStatusFor` only checked that the query contained it.

## F.2 The rule, in one sentence

> A subject of more than a couple of tokens is a TOPIC, not a name, so it is reduced to the
> shortest window of at most three WORDS around its most distinctive token — the first
> digit-bearing token that is not a bare year, else the longest token — keeping whatever is
> glued to that token: its own typed run, a version's second number, a preceding model word,
> or a following unit.

Three sub-decisions, each **measured against a live hit set** rather than assumed (read-only
`GET :8085/search`, 2026-09-11, 3 engines answering; fixtures `live-prius`, `live-python312`,
`live-crashloop`):

| subject | candidate cores and their measured share | chosen |
|---|---|---|
| `2026 Toyota Prius` | `toyota prius` **0.95** · `2026 toyota prius` 0.80 · `2026 prius` 0.20 · `prius` 1.00 | `toyota prius` — the year dates a subject, it does not name one |
| `Python 3.12 asyncio` | `python 3 12` **0.35** · `3 12` 0.55 · `python 3 12 asyncio` 0.05 | `python 3 12` — `3 12` alone matches any 3.12 anywhere; the whole subject is a topic |
| `Kubernetes CrashLoopBackOff` | `crashloopbackoff` 1.00 · `kubernetes crashloopbackoff` **0.40** | both pass; the rule's job is to pass a good set, not to maximise the share |

`python 3 12` at 0.35 is the thinnest good core measured anywhere in this workstream. It is
above the 0.175 line, and it is the number to watch if the threshold is ever revisited.

## F.3 The cap counts WORDS, not tokens — [observed]

Written as a three-TOKEN cap first. `HP EliteDesk 800 G4` is three words and five tokens, so
the cap cut the `G4` off and `RTX 3050 benchmark` then satisfied `RTX 3050 Ti` — undoing the
neighbouring-model guard the previous item had just built. The window now extends by whole
runs (words as typed), which also keeps `M910q` intact without a special case.

Two further corrections found by re-running every pinned case after each change:

- **The trim removed anything short, not only qualifiers**, so it undid the window it had been
  given: `MacBook Air M2` became `m 2`, and `m 2` matches `M.2` — a string in the OptiPlex
  fixture's own hit titles. The trim now removes QUALIFIERS only.
- **A measurement is complete at number+unit.** `100Hz tone` was absorbing the trailing word,
  giving `100 hz tone`, which is narrower than what pages write.

Two expectations from the previous item were CHANGED, not deleted, each with its reason in the
test: `Lenovo ThinkCentre M910q` now yields `thinkcentre m 910 q` (more specific, still
brand-free) and `Apple MacBook Air M2` yields `air m 2` (the guard it was written for — never
the bare `m 2` — still holds and is still asserted against the M.2 string).

## F.4 A label change worth knowing about

With the entity shortened to its name, a round-1 query carries `OptiPlex 3050` rather than
`Dell OptiPlex 3050`. The recorded Dell junk set therefore no longer piles onto a token the
QUERY contains, so its verdict moves `collapsed` → `offtopic`. Both are junk, both yield
nothing, both feed the degraded streak; three replay assertions were widened to accept either
and say why. Nothing about the outcome changed — only which of the two junk labels is
reported.

## F.5 The entity is bounded at extraction — [read-from-source]

`KEYWORDIZE_SYS` now states the entity is a NAME of at most 3 words, says what it is NOT
(the topic, an intent, a bare year), and carries the failing case as its example:
*for "how 100Hz audio affects VR motion sickness" the entity is "100 Hz", NOT "100Hz audio VR
motion sickness"*.

A prompt is a request, not a guarantee, so `harness.ts` shortens deterministically with
`shortenEntity()` — which returns the caller's own spelling (`Python 3.12`, not
`Python 3 12`), so the progress line and the footer name something a person would recognise.

The correction is **counted only when the raw subject was longer than three words**. Dropping
a brand (`Dell OptiPlex 3050` → `OptiPlex 3050`) is ordinary core extraction and happens on
most product runs; counting it would put a line in the footer of nearly every report and bury
the case that matters.

`fetchStats.search.entity_shortened` → `search_record` → both renderers, byte-identical:

```
… · subject shortened to its name (1x) · entity gate: 2 search(es) judged without it (…)
```

## F.6 ENTITY_SHARE re-measured under the new core rule — [observed-live 2026-09-11]

Every recorded set, recomputed:

| share | set | core | class |
|---|---|---|---|
| 1.00 | `probe-good-oomkilled` | `oomkilled` | GOOD |
| 1.00 | `probe-good-iphone` | `iphone 18 pro` | GOOD |
| 0.95 | `live-prius` | `toyota prius` | GOOD (live, new) |
| 0.75 | `search-good-optiplex` | `optiplex 3050` | GOOD |
| 0.65 | `live-optiplex-health` | `optiplex 3050` | GOOD (live) |
| 0.55 | `live-100hz-mechanism` | `100 hz` | GOOD (live) — **was 0.00** |
| 0.40 | `live-crashloop` | `kubernetes crashloopbackoff` | GOOD (live, new) |
| 0.35 | `live-100hz-studies` | `100 hz` | GOOD (live) — **was 0.00** |
| 0.35 | `live-python312` | `python 3 12` | GOOD (live, new) |
| 0.30 | `live-optiplex-thermal` | `optiplex 3050` | GOOD (live) |
| **0.175** | — | — | **ENTITY_SHARE (unchanged)** |
| 0.05 | `live-100hz-ssq` | `100 hz` | OFF-NEED (live) |
| 0.00 | six collapse fixtures | `optiplex 3050` / `100 hz` | COLLAPSED |

All three shares measured with the entity the failing run actually produced
(`100Hz audio VR motion sickness`), not with a cleaned-up one.

**The threshold does not move.** The nearest sets are still 0.05 below and 0.30 above, both
0.125 away, so the no-set-within-0.1 rule holds unchanged. Two GOOD sets moved from 0.00 to
0.55 and 0.35 — they were the false failures — and nothing moved toward the line.

## F.7 Counts — [observed-live 2026-09-11]

| suite | before (merged research-trust-entity) | after |
|---|---|---|
| `research-service` `deno test -A` | 173 passed / 1 env-failed | **189 / 1** |
| `research-curator` | 36 | 36 (untouched) |
| `ruff check .` | clean | clean |

New file: `entity-core.test.ts` (14). New fixtures: `live-prius`, `live-python312`,
`live-crashloop`.

## F.8 Out of scope, recorded

- The weak-core class of E.10 (`laptop 5`, `model 3`) is unchanged: the new rule does not fix
  it and the anchor did not ask it to. `Microsoft Surface Laptop 5` still yields `laptop 5`.
- URL matching in `entityShare` (X3) still not done: a hit whose URL says `optiplex-3050`
  while its title does not still scores as a miss.
- The Answer block's source weighting (the Walmart-review headline) remains open.

---

## G. research-trust-core attempt 2 — the set rule (2026-09-11)

The tester failed T7 on attempt 1. Their verdict, which I accept in full: **four rules in four
items, each picking a surface property to stand in for identity**, each passing every subject
somebody had written down and failing on the first one nobody had.

| item | the proxy | what broke it |
|---|---|---|
| research-trust | 8 pattern strings | 5 live world claims eaten |
| research-trust (a2) | a 7-character token length | `capacitor`, `motherboard`, `vestibular` |
| research-trust-entity | the token before the first digit | a digit-first subject |
| research-trust-core (a1) | the longest token | `50 micrograms semaglutide`, `Nikon Z 6III`, `Mullvad WireGuard port forwarding`, `2026 budget`, `Raspberry Pi 5 NVMe HAT` |

### G.1 The rule, in one sentence

> A subject is its SET of distinctive tokens — those bearing digits (a bare four-digit year
> excepted), those the planner capitalised, and those that are not common English — and a hit
> carries the subject when it contains at least half of them, rounded up.

No length test, no single core phrase, no qualifier list, nothing that must appear verbatim.
`entityCore`, `corePhrase`, `hitCarriesEntity`, the `QUALIFIERS` list and the length tiebreak
are all **deleted**.

Spelling is handled by expanding BOTH sides: every alphanumeric run yields each contiguous
stretch of its digit/letter parts, and two runs are also glued when the boundary between them
is a digit/letter transition. `Z6III` offers `{z6iii, z, 6, iii, z6, 6iii}` and `Z 6III` offers
the same, so either spelling finds the other. Two guards:

- **A bare number never carries a subject alone.** `100 Hz` is two tokens and half of two is
  one; every hit in the recorded `the100` fixture carries `100` — *The 100*, the TV series.
  Without this the founding fixture scored 1.00 and passed.
- **A three-part version needs a repeated glue.** `17.2.1` came out as `172` + `1` from one
  global pass, because the two dot-matches overlap on the digit between them.

### G.2 Distinctive sets, measured

| subject | distinctive tokens |
|---|---|
| `100Hz audio VR motion sickness` | `100hz vr` |
| `Dell OptiPlex 3050` | `dell optiplex 3050` |
| `50 micrograms semaglutide` | `50 semaglutide` |
| `Nikon Z 6III autofocus firmware` | `nikon z 6iii autofocus` |
| `Mullvad WireGuard port forwarding` | `mullvad wireguard` |
| `2026 budget` | *(empty — rejected)* |
| `Raspberry Pi 5 NVMe HAT` | `raspberry pi 5 nvme hat` |
| `Kubernetes CrashLoopBackOff` | `kubernetes crashloopbackoff` |
| `Python 3.12 asyncio` | `python 312 asyncio` |
| `MacBook Air M2` | `macbook air m2` |
| `2026 Toyota Prius` | `toyota prius` |

`autofocus` and `asyncio` survive where the coordinator's sketch dropped them — they are not
common English. It costs nothing: the half rule means an extra token raises the bar by half a
token and gives one more way to clear it, and both sets score 0.85+ on their live hit sets.

### G.3 The share table, every recorded set — [observed-live 2026-09-11]

| share | set | class |
|---|---|---|
| 1.00 | `probe-good-oomkilled`, `probe-good-iphone`, `search-good-optiplex`, `live-prius`, `live-crashloop`, `live-rpi5nvme` | GOOD |
| 0.95 | `live-mullvad` | GOOD (tester's) |
| 0.90 | `live-python312`, `live-semaglutide50` | GOOD |
| 0.85 | `live-100hz-mechanism`, `live-100hz-studies`, `live-nikonz6iii` | GOOD |
| 0.70 | `live-optiplex-health` | GOOD |
| 0.55 | `live-optiplex-thermal`, `live-100hz-ssq` | GOOD / see below |
| **0.175** | — | **ENTITY_SHARE (unchanged)** |
| 0.00 | all six collapse fixtures | COLLAPSED |
| n/a | `live-budget2026` | `entity_rejected` — the subject names nothing |

**The threshold does not move.** Nearest sets are 0.00 below and 0.55 above; nothing lands
within 0.1 of 0.175. The gap is WIDER than under any previous rule (it was 0.05 → 0.30).

`live-100hz-ssq` moves from 0.05 (off-need, collapsed) to 0.55 (ok): its hits are VR
sickness papers and the subject contains `vr`, so they genuinely carry half of it. That query
asks about Simulator Sickness Questionnaire scores and the engine answered it; whether those
pages answer the NEED is the relevance gate's question, not this one.

### G.4 Two costs, declared rather than hidden

1. **Adjacency no longer matters.** `OptiPlex 7080 and the 3050-era chipset` now carries
   `Dell OptiPlex 3050`. Requiring adjacency is exactly what produced four false search
   failures; a page naming OptiPlex models and 3050 IS evidence the engine understood the
   subject, which is the only question this detector asks.
2. **A sibling model counts.** `OptiPlex 3060` holds 2 of `{dell, optiplex, 3050}`. Same
   argument, same boundary: Dell's own home page carries only `dell` and is still refused.

Both are pinned as tests named `DECLARED` and `CHANGED` so they cannot be mistaken for
oversights. The E.10 weak-core class disappears with the QUALIFIERS list that caused it.

### G.5 Test accounting — [observed-live 2026-09-11]

`entity.test.ts` (22) and `entity-core.test.ts` (14) are **deleted**: their subject was
`entityCore()`, which no longer exists. `subject.test.ts` (41) re-expresses every behavioural
assertion they made against the set rule — brand omission, neighbouring model, the M.2 guard,
the spelling variants, the six collapse fixtures, every good set — plus the tester's five live
sets and the five T7 candidates attempt 1 listed without pinning.

| suite | attempt 1 | attempt 2 |
|---|---|---|
| `research-service` | 189 / 1 env-failed | **194 / 1** |
| `research-curator` | 36 | 36 |
| `ruff check .` | clean | clean |

### G.6 `shortenEntity` kept, as a display

It now returns the distinctive set joined by spaces — what the run actually searched on — and
feeds the progress line and the `entity_shortened` footer clause. Kept rather than deleted
because a reader who sees `search: DEGRADED` is entitled to know which tokens the verdict was
about; the counter still fires only when the planner's subject was longer than three words.

---

## H. research-trust-core attempt 3 — the evidence floor (2026-09-11)

The set rule was sound and had **no floor on how much evidence "half" is**. `ceil(k/2)` is 1
when k ≤ 2, and a one-or-two-token subject is exactly what the tightened KEYWORDIZE produces,
so ONE matched token carried a hit. The tester's live counter-examples, each a real page about
a different subject sharing a single token:

| subject | junk set | share on attempt 2 |
|---|---|---|
| `Signal` | digital-signal-processing pages | **1.00** |
| `Arc browser` | arc-welding pages | **0.60** |
| `MacBook M2` | M.2 NVMe heatsink pages | **1.00** |

The last also brought back the **M.2 collision** every previous rule guarded, because the glue
expansion turns `M.2` into the token `m2`.

### H.1 The rule, with the floor

> A subject is its SET of distinctive tokens — those bearing digits (a bare four-digit year
> excepted), those the planner capitalised, and those that are not common English — and a hit
> carries the subject when it contains **at least half of them, rounded up, AND at least two
> distinct non-stopword terms of the query** (a distinctive subject token counts as one).

The floor uses a signal the run already had: `overlapRatio`'s per-hit test. No fourth list, no
length rule. One token is not evidence — and no rule can tell "Signal the messenger" from
"signal processing" when a page offers only that one word.

### H.2 The word list is now general and cited

`COMMON` was a closed list that had grown case by case as each item's findings landed
(`firmware`, `port`, `forwarding`, `micrograms`, `budget`) — the fourth way this module has
tried to encode particular incidents into a rule. It is now the **NLTK English stopword list**
(179 words, `https://www.nltk.org/nltk_data/` → `corpora/stopwords/english`) verbatim, plus the
closed set of SI and imperial unit names, and nothing else.

Two measured consequences, both accepted:

- **Subjects got bigger.** `100Hz audio VR motion sickness` → `{100hz, audio, vr, motion,
  sickness}`; `Mullvad WireGuard port forwarding` → all four. It costs nothing: the half rule
  raises the bar by half a token and gives one more way to clear it, and every live set still
  scores ≥ 0.35.
- **`2026 budget` is no longer rejected.** `budget` is not an NLTK stopword, so the subject
  names something and its live set — genuinely about federal budgets — scores 0.75 and is
  `ok`. The earlier empty-set rejection was an artifact of the tuned list. The tester's other
  four attempt-1 cases all still pass.

### H.3 The share table, 28 sets — [observed-live 2026-09-11]

| share | set | class |
|---|---|---|
| 1.00 | `probe-good-iphone`, `search-good-optiplex`, `live-prius`, `live-rpi5nvme`, `live-macbookm2` | GOOD |
| 0.95 | `live-crashloop`, `live-mullvad` | GOOD |
| 0.90 | `live-python312`, `live-nikonz6iii`, `live-arcbrowser` | GOOD |
| 0.85 | `live-100hz-mechanism` | GOOD |
| 0.80 | `live-100hz-studies` | GOOD |
| 0.75 | `live-signal`, `live-budget2026`, `live-optiplex-health` (0.70) | GOOD |
| 0.55 | `live-optiplex-thermal` | GOOD |
| 0.50 | `probe-good-oomkilled` | GOOD |
| 0.40 | `live-100hz-ssq` | off-need, now ok |
| 0.35 | `live-semaglutide50` | GOOD — the lowest |
| **0.175** | — | **ENTITY_SHARE (unchanged)** |
| 0.00 | `junk-signal-dsp`, `junk-arc-welding`, `junk-macbook-m2-nvme` | JUNK (the tester's) |
| 0.00 | the six collapse fixtures, `probe-collapsed-semaglutide` | COLLAPSED |

**The threshold does not move.** Nearest sets are 0.00 below and 0.35 above; nothing lands
within 0.1 of 0.175. Every junk set the tester built is now exactly 0.00.

`live-semaglutide50` fell 0.90 → 0.35: its pages name semaglutide and little else of the
query. It is still twice the line, and it is the set to watch if the floor is ever revisited.

### H.4 Two reversals and one deletion, declared

- **`probe-collapsed-semaglutide` reverses from `ok` to collapsed.** The previous item declared
  that set `ok` — the engine understood the subject, the relevance gate would filter per need —
  and that judgement was made when there was no floor. The tester then produced three live sets
  of exactly that shape where the shared token meant something else entirely. One token cannot
  be told from the other, so this set goes with them.
- **B1's fix changes shape.** A hit whose ONLY query word is the subject no longer carries it.
  B1 stays fixed because a page really about CrashLoopBackOff says so in more than one word —
  the live set scores 0.95 — but the synthetic control had to become realistic to pass.
- **`shortenEntity` and `entity_shortened` are DELETED.** They existed to report a subject
  shortened to its core phrase. There is no core phrase and no shortening: the subject is used
  whole, as a set. Keeping a footer clause that could never fire again would be worse than
  removing it. `deep_research.py` goes back to **1.4.0** and its rendered footer is byte-identical
  to the deployed version, so **no re-paste is needed**.

### H.5 Removed tests — every case, name → replacement or reason

**36 cases were removed** across attempts 2 and 3: 22 from `entity.test.ts` and 14 from
`entity-core.test.ts`. All 36 have a row. The attempt-3 table had 14 rows covering 22 cases —
the tester found the gap, verified each missing case DID have a replacement, and the rows are
added here. The clause exists because attempt 2 hid two real regressions in exactly this way, so
a table that covers most of the removals is the same defect one level up.

**From `entity.test.ts` (22 cases):**

| removed test | replacement / reason |
|---|---|
| `entityTokens splits a digit/letter run…` | → `tokenSet offers a run, its parts, and the glued neighbours` |
| `entityCore drops a brand or qualifier…` | → `a hit carries the subject at half its tokens, rounded up` + `REGRESSION: a page that omits the brand still carries the subject` |
| `entityCore never strips a unit away from its number` | → `a BARE NUMBER never carries a subject on its own` |
| `entityCore leaves an already-distinctive entity alone` | → `subjectTokens: digits, capitals, and anything uncommon` |
| `entityCore refuses to reduce an entity to nothing` | → `a subject that names nothing is REJECTED, not guessed at` |
| `a brand of ANY length is dropped; the product line and model are kept` | → same; a set has no brand to drop |
| `a page that omits the brand still carries the entity` | → `REGRESSION: a page that omits the brand still carries the subject` (same case, set rule) |
| `a NEIGHBOURING model is not the same machine` | **WITHDRAWN, declared**: a sibling model now carries the subject — `DECLARED: a sibling model counts as carrying the subject` states it and why |
| `a one-character model code never becomes the whole identity` (M.2) | → `REGRESSION: an unrelated product does not carry the subject`, and the M.2 case is now covered for ALL subject sizes by `junk-macbook-m2-nvme` (2-token subject), which is what attempt 2 silently narrowed |
| `a bare number is never an identity` | → `a BARE NUMBER never carries a subject on its own` |
| `hitCarriesEntity matches the core phrase in the shapes engines write it` | → `tokenSet offers a run, its parts, and the glued neighbours` + `T11: the subject is matched in every spelling engines write it` (`search-quality.test.ts`) |
| `hitCarriesEntity accepts the core without the brand` | → `REGRESSION: a page that omits the brand still carries the subject` |
| `ACCEPTANCE 1: the 100 Hz mechanism set is ok for all three spellings` | → `REGRESSION ok: live-100hz-mechanism` (0.85) + `T11: the subject is matched in every spelling engines write it` |
| `ACCEPTANCE 1: the 100 Hz studies set is ok too` | → `REGRESSION ok: live-100hz-studies` (0.80) |
| `ACCEPTANCE 2: the OptiPlex thermal set is ok with the branded entity` | → `REGRESSION ok: live-optiplex-thermal` (0.55) |
| `ACCEPTANCE 2: the OptiPlex health set stays ok` | → `REGRESSION ok: live-optiplex-health` (0.70) |
| `ACCEPTANCE 3: the six recorded collapse sets still collapse` | → the six `REGRESSION collapse: <fixture>` cases, one per fixture, each asserting share 0.00 — one case per set rather than one case for six |
| `ACCEPTANCE 3: the two good sets stay ok` | → `REGRESSION ok: search-good-optiplex` + `REGRESSION ok: probe-good-iphone` |
| `ACCEPTANCE 4: an entity absent from the query is REJECTED, not trusted` | → `entityStatusFor: used, missing, rejected` |
| `ACCEPTANCE 4: an empty entity is MISSING, and falls back` | → `entityStatusFor: used, missing, rejected` |
| `ACCEPTANCE 4: an entity the query DOES carry is used` | → `entityStatusFor: used, missing, rejected` |
| `ACCEPTANCE 4: the query is matched on the CORE too` | → `entityStatusFor accepts a query carrying half the subject` (the set rule's form of the same question: half the subject, not the core) |

**From `entity-core.test.ts` (14 cases):**

| removed test | replacement / reason |
|---|---|
| `ACCEPTANCE 1: the five-word subject reduces to the name inside it` | → `subjectTokens: digits, capitals, and anything uncommon` + `REGRESSION: the failing dry run's own subject passes its own hit set` |
| `ACCEPTANCE 1: and the recorded hit set then satisfies it at >= 0.4` | → `REGRESSION: the failing dry run's own subject passes its own hit set` |
| `ACCEPTANCE 1: all three of that run's queries classify ok` | → `REGRESSION ok: live-100hz-mechanism` / `-studies` / `-ssq` |
| `ACCEPTANCE 2: the five named subjects reduce as the rule says` | → `the tester's five subjects, on their own live hit sets` |
| `ACCEPTANCE 2: a bare YEAR is not the name — measured, not assumed` | → `subjectTokens: a bare YEAR dates a subject, it does not name one` |
| `ACCEPTANCE 2: a VERSION number keeps its language…` | → `the tester's five subjects…` + `T7 candidate: a three-part version` |
| `ACCEPTANCE 2: a two-word technical name keeps both words` | → `the tester's five subjects…` + `REGRESSION ok: live-crashloop` |
| `ACCEPTANCE 2: a product subject with trailing intent keeps the model` | → `the tester's five subjects…` + `REGRESSION ok: live-rpi5nvme` |
| `a token typed as ONE word is never split across the window boundary` | → `tokenSet offers a run, its parts, and the glued neighbours`: a set has no window to split across, and the glue expansion is what the case was protecting |
| `the window is bounded, and the bound is in WORDS not tokens` | **no replacement, no longer meaningful**: there is no window |
| `a subject with no digits and no long word is left alone` | → `subjectTokens: digits, capitals, and anything uncommon` (nothing is "left alone" or not — every token is kept or dropped on its own merits) |
| `ACCEPTANCE 3: a five-word subject is shortened to the name inside it` | **WITHDRAWN with `shortenEntity`** — H.4: there is no shortening |
| `ACCEPTANCE 3: shortening keeps the CALLER's spelling` | **WITHDRAWN with `shortenEntity`** — H.4; attempt 2 withdrew it with no reason, which the tester caught |
| `ACCEPTANCE 3: a subject that IS a name is returned unchanged` | **WITHDRAWN with `shortenEntity`** — H.4; the subject is always used whole now, so "unchanged" is the only behaviour there is |


### H.6 Counts

| suite | attempt 2 | attempt 3 | attempt 4 |
|---|---|---|---|
| `research-service` | 194 / 1 env-failed | 192 / 1 | **203 / 1** |
| `research-curator` | 36 | 36 | 36 |
| `ruff check .` | clean | clean | clean |

The attempt-3 drop is the `shortenEntity` pair going with the function; the attempt-4 rise is
the eleven cases of section I.

---

## I. research-trust-core attempt 4 — the query side of the floor (2026-09-12)

The floor held everywhere it ran. The tester could not break it with shared query terms, and the
one attack that looked promising — a constructed set of "group chat app" pages each saying "works
even on weak signal", share 1.00 — died on live data: the REAL population for
`best group chat apps for teams` scores 0.00, because real group-chat articles do not say
"signal". The construction was the artefact.

What broke was the door in front of the floor.

### I.1 The defect — [observed-live 2026-09-12, tester]

    hitCarriesSubject(...):
      if (qt.length < 2) return true;          // <-- nothing to ask two of

A query with under two content words skipped the floor entirely, and the run's OWN query builder
produced exactly that whenever a need's every word is a stopword:

    keywordQuery("Signal",     "What is it?")  -> "Signal"      terms ["signal"]     FLOOR SKIPPED
    keywordQuery("Notion",     "What is it?")  -> "Notion"      terms ["notion"]     FLOOR SKIPPED
    keywordQuery("Kubernetes", "What is it?")  -> "Kubernetes"  terms ["kubernetes"] FLOOR SKIPPED

End to end through the shipped `runResearch`, serving this branch's own `junk-signal-dsp`:

| | query | share | stats | fetched | backstop |
|---|---|---|---|---|---|
| one-word need | `Signal` | **1.00** | ok=1 collapsed=0 | **8 junk pages** | complete |
| normal need | `Signal disappearing messages` … | 0.00 | ok=0 collapsed=2 | 0 | fetch_degraded / `no_relevant_sources` |

No model misbehaviour anywhere in it. `KEYWORDIZE_SYS` asks for 3–7 terms and nothing enforced it.

### I.2 The fix, at both ends

1. **The query builder guarantees two content words.** `shapeQuery` (which `keywordQuery` now
   delegates to, and which `reformulate` shares) fills from the NEED's own content words first
   and appends the class word `overview` only when the need has none — the same KIND of term
   `REFORMULATION_SUFFIXES` uses, chosen because a FIRST search should not be biased toward
   "problems". Padding is counted (`search.query_padded`), because a run that had to invent a
   word to make its query searchable is a fact about the run.
2. **A query that still cannot carry the floor is refused, not floored true.**
   `entityStatusFor` returns a fourth status `unfloored`, `classifyHits` returns `offtopic` for
   it (no fetch, feeds the degraded streak), `search.unfloored` counts it and the footer names
   it. The `return true` is gone. Falling through to the overlap fallback would have been the
   same hole one door down: a one-term query scores high overlap on anything carrying that term.

Together these make the branch **unreachable in production and loud if reached** — the harness
test asserts `unfloored === 0` precisely because the builder guarantees two.

### I.3 What it cost: nothing measurable

The share table is **unchanged, every row**. Verified by computing it twice over the same
fixtures — once with the attempt-3 module read out of git (`d9cc44f`), once with the fix — and
diffing: identical, including `live-100hz-mechanism` 0.85, `live-100hz-studies` 0.80,
`live-100hz-ssq` 0.40, `live-semaglutide50` 0.35, every junk and collapse set 0.00. Nothing
within 0.1 of 0.175. Every recorded fixture query already has two content words, so the
guarantee never fires on one: `shapeQuery` returns them unpadded and unchanged.

### I.4 The footer changes, so the tool is re-pasted

`deep_research.py` goes **1.4.0 → 1.4.1**. The new clause fires only when a search was refused,
so the rendered bytes are identical in every run that has none — but the DEPLOYED copy would not
render it in a run that does, and the whole point of the counter is to be visible. Both renderers
verified byte-identical at `unfloored` 0 and 1:

    entity gate refused 1 search(es): the query had fewer than two content words

It is NOT folded into the `judged without it` count: those searches were judged without the gate;
these were refused BY it.

### I.5 Eleven tests, RED before GREEN

Seven of them fail against the attempt-3 behaviour, measured by restoring the two early returns
and the un-guaranteed builder and re-running (7 failed / 67 passed), then restoring the fix
(74 passed). The three that pass either way are the ones that pin behaviour the fix must NOT
change — the good set, the zero-case footer, and the unpadded fixture queries.

| test | pins |
|---|---|
| `T7 query side: a one-term query never floors a hit true` | the reversal itself |
| `T7 query side: the whole live junk set scores 0.00 on a one-term query` | the tester's set, and the good counterpart unharmed |
| `T7 query side: an unfloorable query is REFUSED, not judged by overlap` | `unfloored` → `offtopic`, and that the overlap fallback would have said `ok` |
| `queryCanCarryFloor: two DISTINCT content words, not two words` | "Signal signal" is one word twice |
| `shapeQuery guarantees two content words for a need that has none` | "What is it?", "Why?", "", stopwords + punctuation, "and then?" |
| `shapeQuery takes the second word from the NEED when the need has one` | nothing is invented when the need has a word |
| `keywordQuery and reformulate both emit a floorable query` | six subject/need pairs incl. no-entity |
| `the guarantee does not disturb a query that already has two` | six recorded fixture queries unchanged |
| `T7: a stopword-only need cannot produce a query that skips the floor` | the tester's reproduction through `runResearch`: padded, counted, DSP refused, 0 fetched, `unfloored === 0` |
| `T7: the same subject with a real need still works` | `live-signal` still ok, fetched, unpadded |
| `the footer names a refused search rather than hiding it` | the clause at 1, silence at 0 |

### I.6 Carried forward, unchanged

`entityShare` still reads `title + " " + snippet` and never `url` (seventh item running);
`research_jobs.status` is still `done` with `error` NULL for a run that retrieved nothing. Both
are the tester's, both are outside this item, both are recorded here rather than fixed quietly.
And the pattern the tester named is worth keeping in view: each rule in this workstream has been
sound in its body and broken at an edge the body did not cover — the pattern list had no
head-clause split, the length anchor had no notion of identity, the set rule had no floor, the
floor had no behaviour when there was nothing to floor against. The edges keep being found by a
tester rather than by the rule's own construction.

### H.7 On a PADDED query the pad word is the floor's second content word — [reproduced 2026-09-12, reviewer; live measurement observed by the tester 2026-09-12]

Written at merge time (reviewer rt-reviewer, 2026-09-12), from the tester's X1 in
`.git/agent-worktrees/queue/research-trust-core.attempt4.evidence.md`, so the next item reads it
without going back to an attempt file. The item merged as `aca6bce`; nothing here is a defect
report against it.

`shapeQuery` guarantees two content words by appending `QUERY_PAD_SUFFIX` (`overview`) when the
need supplies none. The evidence floor then asks each hit for two distinct content query terms —
and on a padded query the second of those two IS the pad word. So the share becomes partly a
measure of which pages happen to use a word chosen precisely because it has no discriminating
power.

**What the tester measured, live (theirs, 2026-09-12, not re-run since).** On a freshly captured
`Signal overview` result set the hits that carried were exactly the hits containing the word
"overview": `signal.org` — the canonical page for the subject — was REFUSED, while a Linux
`signal(7)` man page was ACCEPTED. Three captured padded queries landed 0.40–0.50, comfortably
over the 0.175 line, and the DSP junk stayed at 0.00 because those pages do not use the word
either.

**What the repository reproduces (reviewer, against the shipped code at OB1 `1b88347`).** The
same dependence cuts the other way, and the fixture directory demonstrates it:

    live-signal.json, its own recorded query "Signal messenger encryption"  ->  0.75  ok
    live-signal.json, the padded query "Signal overview"                    ->  0.00  collapsed

None of that set's twenty genuine Signal-messenger pages uses the word "overview", so a padded
query drops a real, on-subject set to zero. The junk set scores 0.00 on the same query for the
same reason (0 of 20 hits carry the pad word). **Both directions are the same coin: the pad word
decides, and neither good nor junk pages reliably carry it.**

The failure is in the SAFE direction — nothing is fetched and the run reports a search failure
rather than an absence, which is this workstream's whole thesis — but a need whose every word is
a stopword can now lose a search that would have worked. The shape that crosses the line the
other way is a subject whose good pages rarely use the pad word while the junk does.

**The missing RED case, for whoever takes this up:** inject "overview" into some titles and
snippets of `junk-signal-dsp` and assert the verdict. No case in the suite measures a padded
query against junk that uses the pad word; the reassurance that junk sits at 0.00 comes from
junk that does not use it.

Two things not to break while fixing it: the pad must stay non-discriminating for a FIRST search
(the retry list's "problems"/"review" would bias the first look at a subject toward complaints),
and `query_padded` already counts every padded query, so the population is measurable before
anyone changes the rule.

**Also note, for anyone reading the regression suite as the specification:** the `REGRESSION ok:`
loop asserts `share >= 0.3` for every good set, which is stricter than the shipped
`ENTITY_SHARE = 0.175`. A legitimate future good set landing between the two fails the suite
while the product classifies it correctly. That is deliberate as a tripwire; it is not the
threshold.

### H.8 The anchor named two functions this item deleted — [read-from-source 2026-09-12, reviewer]

Recorded so the anchor is not later read as a description of what shipped. Anchor
`anchor-research-trust-core.json`, confirmed before the work, states:

  * criterion 1 — "`entityCore('100Hz audio VR motion sickness')` yields a core …"
  * criterion 3 — "KEYWORDIZE's returned entity longer than 3 tokens is shortened
    deterministically …, counted in `fetchStats.search` (`entity_shortened`) and shown in the
    footer"

`entityCore` and `shortenEntity`/`entity_shortened` are both DELETED by this item: there is no
core phrase and no shortening, because the subject is used whole as a token set. The substance of
criterion 1 is met by a different function (`subjectTokens` + the set rule, measured at 0.85 on
the recorded set against the anchor's ≥ 0.4), and criterion 3 was withdrawn as no longer
meaningful — "a footer clause that can never fire again is worse than no clause".

**This was declared, not routed around**: the TEST-PLAN's T4 states both deletions and the reason,
and H.4/H.5 record them case by case, which is what the merge protocol asks of work that finds an
acceptance criterion obsolete. What did NOT happen is an amendment of the anchor text itself, so
the confirmed anchor on the board still names both functions. If a later item diffs the artifact
against that anchor, this is the entry that explains the gap.

## Deploy round 3, 2026-09-12 (research-trust-core at OB1 1b88347) - the proof is green

Provenance: observed-live via `POST :8818/research` dry runs a337520c (100 Hz) and
d8dfb1e0 (OptiPlex) on `openbrain-research` label 1b88347.

- **100 Hz (a337520c): acceptance MET.** Subject extracted as `"100Hz"` (the tightened
  KEYWORDIZE now returns a name). 4 of 6 searches `ok` (shares 0.625 / 0.625 / 0.625 / 0.5),
  2 collapsed (0.125 / 0), 48 hits, 18 fetched, 14 relevant, 16 cited including
  pmc.ncbi.nlm.nih.gov/PMC11955832, pure.fujita-hu.ac.jp (the paper's institutional
  record), sciencedaily, ridecalm, hearinghealthmatters. Title: "100 Hz Bone-Conducted Tone
  Reduces Motion Sickness via Otolith Stimulation; VR Relevance Is Inferred from a
  Driving-Simulator Proxy". Footer: `needs answered 0 of 6 (4 partly)`. The same query on
  2026-09-11 returned "Absence of Evidence..." with 0 fetched.
- **OptiPlex (d8dfb1e0): acceptance MET.** 5 of 5 searches `ok` (0.75-1.00), 26 fetched,
  14 cited (Dell diagnose pages, manuals, defect DB, two used-OptiPlex articles), footer
  `needs answered 1 of 6 (5 partly)` consistent with the body.
- Open for the next item (not blockers): H.7 padded-query dependence on the pad word;
  "needs answered 0 of 6 (4 partly)" on a run that clearly answered the mechanism need -
  the coverage judge is still conservative; two 100 Hz searches still collapsed at 0.125/0.

## First live OWUI run on the deployed stack, 2026-09-12 (job 33250e9b) - what the engine recorded

Provenance: observed-live 2026-09-12 04:00-04:03 UTC; `research_jobs.result` for 33250e9b
(origin owui, callback into chat 2bc45c77 / message 30d5d1bb), `openbrain-curator` log.

- The chat model rewrote the operator's question into a 7-need query ("...motherboard
  capacitor failures, thermal problems, PSU, BIOS mod, RAM slots, CPU socket, water damage").
  Subject "OptiPlex 3050"; 7 of 7 searches `ok` (shares 0.5-1.0), 56 hits, 42 fetched, 23
  readable, 19 relevant (yield target reached), 17 cited (Dell KB/community/manuals,
  Win-Raid, iFixit, PCWorld, hardware-corner, dfarq). Title states findings (180 W PSU
  failure, fragile LGA1151 pins, BIOS-mod difficulty); Answer block first; footer
  `needs answered 0 of 7 (7 partly) - sources 19 relevant of 42 fetched (56 hits)`.
  Curator filed into thread "Used Desktop Hardware Diagnostics": 18 claims (0.51-0.79),
  20 edges, 1 meta refused, 0 ungrounded.
- **Precision miss, meta judge (follow-up):** the LLM judge refused a WORLD claim - "The
  same used-purchase analysis recommends the Dell OptiPlex 3060 (8th-gen Intel) as a better
  secondhand option for Windows 11 compatibility..." - attribution phrasing ("the ...
  analysis recommends") read as being about the sources. A real fact was lost; the
  deterministic layer did not fire, the judge did. Candidate fix: the judge prompt should
  treat "<source> recommends/reports/notes <world content>" as WORLD.
- **Precision miss, groundNumbers (follow-up):** "[UNCERTAIN] ... removing the CMOS battery
  for 15 minutes did not resolve the issue (unverified figure: 15) [Source 8]" - the figure
  is very likely in the Dell community thread beyond the 4000-char slice the synthesizer
  sees, or written as "15 min". Downgrade direction is safe; the held-text window is the
  limiter.
- Coverage judge still conservative: "0 of 7 (7 partly)" on a run whose body states seven
  concrete findings.

---

## J. research-trust-report — the document, and closing the gaps before delivering (2026-09-12)

The engine had become trustworthy and was still not USEFUL. The operator, on the first live
report off the deployed stack: it is "organized only as facts, sources and gaps, which is not a
greatly formatted report that I could hand off in a professional environment" — the information
is accurate and must stay that way — and "when the answer isn't complete enough, there should be
a recommendation to perform an additional run, or better yet, perform the additional run before
sending an incomplete result back to the end user".

The artefact is job **33250e9b** (a used Dell OptiPlex 3050 buyer's question), kept whole at
`documentation/evidence/research-trust-report/live-owui-33250e9b.result.json`, with the
delivered document beside it as `rendered-BEFORE-33250e9b.md` and this branch's render of the
SAME synthesis as `rendered-AFTER-33250e9b.md`.

### J.1 Four mechanisms, all of them measured — [read-from-source + observed-live 2026-09-12]

| # | What the reader saw | Why |
|---|---|---|
| 1 | A bare facts/sources/gaps list for a buyer's question | Template selection ran only at **3 ANSWERED** needs (`shouldClassifyTemplate(answered)`), and the coverage judge had marked all seven needs `partial`, so `answered` was 0 |
| 2 | `needs answered 0 of 7 (7 partly)` above 26 cited findings | `reconcileNeedsStatus` could only lift a judge's `open` to `partial`, however much the synthesis grounded |
| 3 | The same seven open questions printed twice, the second copy headed "Open gaps (**NOT grounded**)" | The template writes its own gaps section and `lib.ts:284` appended a second one — mislabelled, because needs the report had partly answered are not ungrounded |
| 4 | A paragraph telling the reader "Do NOT fill them from your own knowledge… call deep_research again" | `lib.ts:297` — a directive to the MODEL, rendered in the human's document. `deep_research.py` mirrored 3 and 4 at :304/:318 |

### J.2 Coverage: the count, not the flag — and the trap inside it

A need with **≥2 grounded, cited lines** about it is now `answered`; exactly one is `partial`;
zero leaves the judge's verdict alone; `search_failed` is never reopened
(`report.ts:180`, `ANSWERED_MIN_LINES` at :166).

The first implementation of that rule was **wrong in the way this workstream keeps being wrong**,
and the measurement caught it: matching a line to a need by two shared distinctive terms gave

    per-need grounded line counts: 19 17 17 17 18 17 17   (of 19 grounded lines)

— every need "answered" by nearly every line, because `dell`, `optiplex` and `3050` are in all
seven needs and in almost every line. It was measuring the SUBJECT with a per-need label on it,
which is the same shape as an entity rule that matches on one shared token, and it would have
made `answered` free for every run forever.

What ships subtracts the common core: each need's terms MINUS the ones it shares with half the
other needs, computed from the needs of this run, no list (`discriminatingTerms`, `report.ts:152`).
A line counts for a need when it carries **one of that need's own discriminating words AND two of
the need's words overall** — deliberately the same two-part shape as the entity floor from the
previous item, for the same reason: one distinctive word is a coincidence, corroboration alone is
the subject. Measured on the recorded synthesis:

| need | grounded lines | verdict |
|---|---|---|
| capacitor degradation symptoms | 7 | answered |
| thermal / fan failure | 5 | answered |
| PSU reliability | 6 | answered |
| BIOS modification | 4 | answered |
| RAM slot problems | 6 | answered |
| CPU socket / bent pins | 9 | answered |
| red flags / water damage when buying | **1** | **partial** |

**6 of 7 answered, 1 partly** — against `0 of 7 (7 partly)` in production and the anchor's floor
of 5. The one that stays `partial` is the need the synthesis really does carry one line about,
which is the evidence that the measure discriminates at all.

### J.3 The document

`buyers-guide` is one entry in TEMPLATES (`templates.ts:68`) — title stating the finding,
executive summary, a **What to check in person** checklist, **failure modes by subsystem** as a
table with a citation in every row, one limitations section, sources. Template selection now
counts `answered + partial` (`shouldClassifyTemplate`, `report.ts:251`): a need with a grounded
finding about it is evidence a template can stand on, whether or not the judge called the
sub-question settled.

`LIMITATIONS_SECTION` (`templates.ts:60`) is one string shared by every template, so there is
exactly ONE section of unknowns in any report, and it ends by asking for the next run in the
reader's terms — or, when nothing is open, by saying the question is answered.

The classifier states the reader's PURPOSE (buy / build / learn / compare / decide) and picks a
template for it in one call (`classifyReport`, `templates.ts:297`). **The purpose is not derived
from cue words in the question**, and that is deliberate: four items in this workstream have now
failed on a hand-written list of surface strings, and a list of buying words would be the fifth.
The model reads the question; `PURPOSE_FALLBACK` (`templates.ts:245`) is used only when the
returned template id does not exist, so a classifier that says "buy" and misspells the id still
gets a buyer's guide.

### J.4 The gap-closing pass

When the first synthesis leaves a need open or partly answered and most of the wall clock is
unspent, the run does one more gather round aimed at the synthesis's own `[GAP]` lines, then
re-synthesizes over the merged pool (`harness.ts:1261`).

Bounded by construction, not by intention: `gapRound` is set to null before it is awaited, so a
second pass is unreachable; at most `GAP_PASS_MAX_QUERIES` (3) searches; only while elapsed is
under `GAP_PASS_MAX_ELAPSED` (0.6) of the budget; only on the topic path, because nothing else
assigns `gapRound`; and only when `backstop === "complete"` — a run that already tripped a
backstop has said why it stopped, and a second round of an exhausted budget closes nothing.

Two things the pass had to be prevented from doing, both found by existing tests:

- **Relabelling the run.** The first version let the pass's own `backstopDecision` write
  `backstop`, and a `fetch_degraded` run — a real diagnosis, "the pages would not read" — came
  back as `max_fetch`, a budget note. The pass now stops on a budget and never renames the run.
- **Skipping the numeric gate.** The second synthesis goes through the same
  `applyNumericGrounding` + `buildCitedAndRenumber` (`harden`), because a pass that ADDS sources
  is exactly when a new uncited figure can arrive.

The footer says what it cost and what it bought, including when it bought nothing:
`gap-closing pass: +K sources, needs answered X of N -> Y of N`. The curator is still delegated
to once, with the final synthesis; no intermediate synthesis is ever persisted.

### J.5 The chat message is rewritten, not appended

`deliverReport` appended, which is why the final message began with the model's "research is
running in the background" line and why a second write was impossible. It now REWRITES
(`index.ts:535` via `rewriteChatBody`, `lib.ts:321`), so the callback runs twice: an interim
`First pass complete: X of N needs answered - running a gap-closing pass to close the rest` when
the pass starts, then the report.

The waiting line is removed by an **exact-string contract**, not a pattern: `deep_research.py`
tells the model to reply with `HANDOFF_WAIT_LINE` verbatim, and the engine strips that one string.
Anything else the model said is kept and the report is appended under it. Deleting "whatever the
model wrote before the report arrived" on a guess would be deleting a person's assistant's words.

The interim write persists nothing canonical: no result, no job state, no curator — it rewrites
the chat body, which the final write replaces.

### J.6 The last place a fact can enter ungrounded — [observed-live 2026-09-12]

The template render is a model writing prose, and it is downstream of every grounding gate this
engine has. Diffing the buyer's-guide render of 33250e9b against its own synthesis found the
renderer inventing **procedure**: "wait 30 seconds", "repeat three times", and `BSOD` for a
phrase the answer spells out.

The grounding rules now forbid acronyms the answer does not use and quantities in instructions
(`templates.ts` GROUNDING RULES), and the re-render is clean of numbers and URLs. What survives
is two standards: the model wrote "no standard **ATX** or **SFX** connector present" from a
source that says only "proprietary".

So the run now measures its own report the way it already measures its figures:
`renderGroundingDiff` (`grounding.ts:269`) records `numbers` / `urls` / `names` the grounded
answer does not hold, on every run, as `RunResult.proseUngrounded`, with a progress line when it
is non-empty. Nothing is blocked and nothing is rewritten — a report is not thrown away over an
acronym — and nothing is hidden. The test pins the two names exactly rather than tolerating a
threshold: a THIRD name appearing in that document fails it.

| document | numbers | urls | names |
|---|---|---|---|
| the render as first written | `30`, and the rest of the header noise | none | `ATX`, `BSOD` |
| after the tightened rules (shipped) | **none** | **none** | `ATX`, `SFX` |

### J.7 Counts

| suite | before | after |
|---|---|---|
| `research-service` | 203 / 1 env-failed | **233 / 1** |
| `research-curator` | 36 | 36 |
| `ruff check .` | clean | clean |

The one failure is `orchestrator.test.ts`, which opens a postgres pool at module load; it fails
identically on the base commit.

### J.8 Out of scope, recorded

- `entityShare` still reads `title + " " + snippet` and never `url` (eighth item running).
- `research_jobs.status` is still `done` with `error` NULL for a run that retrieved nothing.
- The BEFORE document's own footer is the RED for J.2: it was produced in production, not by a
  test harness, and it says `needs answered 0 of 7 (7 partly)` under seven answered needs.

---

## K. research-trust-report attempt 2 — the sentence that said more than its source (2026-09-12)

Attempt 1 passed all ten cases. The tester sent it back anyway, on something they found by
READING the document, which is the only way it could have been found.

### K.1 The defect — [read-from-source, tester 2026-09-12]

The failure-modes table, row "Power connector / upgradeability", citing `[Source 13]`:

> Physical connector is non-standard; **no off-the-shelf ATX or SFX drop-in available**

The line it was built from:

> The Dell OptiPlex 3050 SFF uses a proprietary power supply and proprietary power connector,
> which **makes it difficult** for users to install aftermarket PSUs to support higher-power GPUs.

Two moves in one cell. **ATX** and **SFX** are names the synthesis does not contain —
`renderGroundingDiff` catches those, and the fixture header disclosed them. And "makes it
difficult" became "no … available": **a hedge turned into an absolute**, under a citation that
does not support it. The tester proved the second is invisible by construction:

| sentence, against a synthesis line saying "makes it difficult" | `renderGroundingDiff` |
|---|---|
| "No off-the-shelf drop-in is available." | `{numbers:[],urls:[],names:[]}` |
| "It is impossible to install aftermarket PSUs." | `{numbers:[],urls:[],names:[]}` |
| "The unit always fails within a year." | `{numbers:[],urls:[],names:[]}` |

The diff compares numbers, URLs and names. Modality is none of those — and `prose_ungrounded` is
a field on a job row, which the colleague the report is written for never sees.

### K.2 Two guards, because neither is enough

**The rules.** `GROUNDING_RULES` now forbid, in the renderer's own prompt: strengthening a hedge
(difficult → impossible, some → all, reported → always, may → does, plus rankings and counts the
answer does not make), and naming a standard, product, model or organisation the grounded answer
does not name. Measured: re-rendering 33250e9b with the rules alone removed ATX and SFX and
restored the hedge — the executive summary now says "make it difficult to install aftermarket
PSUs" and the connector row "proprietary PSU and connector limit aftermarket replacement".

**The check.** `fidelity.ts` presents every rendered sentence or table cell that carries a
citation, together with the synthesis lines it cites, for one word: SAME / WEAKER / STRONGER /
UNSUPPORTED. A prompt is an instruction and a judge is a measurement; this workstream has learned
twice that the instruction alone is not the guard.

Graduated, because the cheapest correction that works is the right one:

1. STRONGER / UNSUPPORTED → **one targeted re-render** of those sentences only, told that usually
   one clause is the problem and to hedge or cut that clause rather than restate the line.
2. Still bad on the re-judge → **replaced by the cited line verbatim**, tag stripped, citation
   kept. This cannot fail, because the replacement IS the evidence.

Fail-open, like the skeptic: a judge that throws, times out or answers nonsense leaves the
document byte-for-byte as the renderer wrote it and records `error`. A report is never withheld
because a checker broke. And a rewrite is never trusted unchecked: if the re-judge cannot run,
everything the first judge condemned is replaced rather than kept.

### K.3 What it did to the real document — [observed-live 2026-09-12]

Rendered through the deployed LiteLLM path and then through the shipped check, exactly as a live
run does:

    render fidelity : {"checked":32,"stronger":3,"unsupported":1,"rewritten":2,"replaced":2}
    grounding diff  : numbers [] urls [] names [BSOD]   (was [ATX, SFX])

Both replacements are visible in `rendered-AFTER-33250e9b.md`, and **the cost is visible with
them**: a table cell that claimed "defective board traces" the sources never mention, and an
executive-summary sentence that turned "will become less useful after Windows 10 end-of-life"
into "narrows its practical use to Linux or Windows 10", now carry their grounded lines verbatim.
The summary reads less smoothly for it. Truth over polish is the trade, and the fixture shows it
rather than describing it. `rendered-AFTER-v1-33250e9b.md` keeps the attempt-1 render so the
defect and its fix sit side by side.

`BSOD` is what the name check still reports: the synthesis writes "Blue Screen of Death" and the
report abbreviates it. Recorded, not hidden.

### K.4 Three things the build got wrong first, all caught by measurement

- **The verbatim fallback pasted every line sharing a citation.** One summary sentence citing
  `[Source 13, 17]` became five long lines in the executive summary — true, and worse than what it
  replaced. It is now ONE line for one sentence, chosen by word overlap with the sentence being
  replaced.
- **A table row's LABEL was judged as a claim.** "Thermal / fans" was rewritten into a paragraph,
  which shifted every column of that row. The first populated cell is a label; the claims are
  after it. (A five-word floor catches the rest.)
- **The sentence splitter broke "e.g. SSDs".** A break now needs two ordinary characters or a
  closing bracket before the stop, which separates an abbreviation's full stop from a sentence's
  without a list of abbreviations.

### K.5 The footer is the half the reader sees

`render checked: N sentences, K corrected`, byte-identical in both renderers, and
`render check: not run` when the judge failed. The tester's point stands beyond this item: a
measurement recorded only on the job row is a measurement the audience never gets.
`deep_research.py` → **1.5.1**.

### K.6 The contract bounds the pass (tester X3)

`contract.budget.rounds: 1` now stops the gap-closing pass. The pass's own bounds — one-shot,
three queries, 0.6 of the clock, topic path, `backstop === "complete"` — are the engine's, not the
caller's, and a caller who caps a job at one round is capping the work it may do. A round is what
the pass spends.

### K.7 Counts

| suite | attempt 1 | attempt 2 |
|---|---|---|
| `research-service` (service directory only) | 233 / 1 env-failed | **248 / 1** |
| `research-curator` | 36 | 36 |
| `ruff check .` | clean | clean |

+15: twelve in `fidelity.test.ts`, two in `report-doc.test.ts` (the v1-versus-shipped comparison
and the fixture's own record), one in `harness-trust.test.ts` (X3).

### K.8 Carried forward, not fixed

- The tester's **X2**: the answered measure is a bag of words, and two lines *about capacitors*
  that happen to carry the PSU need's discriminating word plus one more score the PSU need as
  answered. Constructed, not observed — but "answered" is now a claim the document makes to a
  colleague, and it rests on overlap rather than aboutness. Recorded for whoever touches coverage
  next.
- A need answered completely by ONE thorough line still reads `partial`. Deliberate, and it does
  mean a well-written single-line answer under-reports.

### K.9 The verbatim replacement in a table cell: keep it, and fix it in the LAYOUT — [measured 2026-09-12, reviewer]

Written at merge time (reviewer rt-reviewer; the item merged as `f815116`). The question put to
review was whether `fidelity.ts`'s terminal fallback — replacing a still-wrong unit with the
cited synthesis line verbatim — is the right house behaviour in a TABLE, or whether a replaced
cell should be shortened by rule.

**The cost is real and measurable.** Word counts of the `What goes wrong` column in the shipped
`rendered-AFTER-33250e9b.md`:

    Power supply (PSU)                  10        BIOS / firmware        19
    CPU socket / processor              21        Thermal / fans         21
    Motherboard (incl. capacitors)      54        RAM / DIMM slots       64

The 64-word cell opens "A Dell OptiPlex 3050 SFF user reported that after cleaning the unit and
reapplying thermal paste…" — a narrative sentence in a column whose siblings are clauses. As a
table it is worse. As evidence it is exactly right.

**Keep the verbatim fallback; a length rule over the words would be a misfit.** The only safe way
to shorten a grounded sentence is to preserve its MODALITY, and modality is precisely what this
module exists to protect: a rule that clips "makes it difficult for users to install aftermarket
PSUs" to fit a column can land on "…install aftermarket PSUs" and re-create the defect the
replacement was repairing. The house answer already exists one file over — `grounding.ts`
downgrades an ungrounded figure and annotates the line, and never deletes it, "because the
sentence around the figure may still be right". True and ugly beats short and wrong, and that
trade is the whole of this workstream.

**What IS worth doing needs no rule about words.** When a replaced unit is a table cell over some
length, put a short marker in the cell and the verbatim line beneath the table. The column stays
readable, the sentence stays exact, and nothing decides where to cut a claim. `applyUnit` already
addresses a unit by line and cell, so the placement is a rendering choice, not a new judgement.

### K.10 A fourth count of the same document, and where the 32 comes from — [measured 2026-09-12, reviewer]

Extending the tester's X4 (three counts that do not reconcile) with an independent measurement,
because a number the reader cannot reproduce is what that entry is about. Running the shipped
`citedUnits` over each committed artifact:

    rendered-AFTER-v1-33250e9b.md    34 units   (9 prose, 25 table cells)
    rendered-AFTER-33250e9b.md       31 units   (19 prose, 12 table cells)
    rendered-BEFORE-33250e9b.md      12 units   (12 prose, 0 table cells)

So the tester's 33 for v1 is a fourth number beside my 34, and **neither committed render
produces 32**. The 32 is recorded in the header of `rendered-AFTER-33250e9b.md` itself
(`render fidelity : {"checked":32,...}`), captured from the run that produced that document — so
it was counted on that run's PRE-CHECK input, which is not committed. `rendered-AFTER-v1` is a
different render (attempt 1's, kept as the defect), not that input. The recorded run JSON
`live-owui-33250e9b.result.json` carries no `render_fidelity` field at all; it predates the check.

That fully explains the spread and none of it is a defect in the code — but it means three
artifacts in one directory carry four counts of "the same" document, and the footer invites a
reader to reproduce the one number that no committed file holds. The fix is a sentence in the
fixture header naming which document the 32 was counted on, and, in the product, the `not
checked: M` counter that X1 wants anyway.

**One caution for whoever picks up X1.** Do not count skipped sentences by calling `citedUnits`
line by line: a table row returns 0 units when judged alone, because cells are only recognised
after the header rule has been seen. A per-line sweep of the shipped render reports 7 "citing but
unchecked" lines, 6 of which are ordinary table rows that the whole-document call checks
correctly (the seventh is this fixture's own header comment, which quotes a citation). I nearly recorded that as a finding before checking it — the same shape as the
tester's own X5, where a truncated print statement invented a broken table.

## Second live OWUI run, 2026-09-12 12:13 UTC (job 64ac38cf) - the hand-off document, delivered

Provenance: observed-live; `research_jobs.result` for 64ac38cf (origin owui, callback chat
2bc45c77 / message c48bec70) on `openbrain-research` label 990a2f5; curator log.

- The chat model rewrote the question into 5 needs; the reuse pass recalled 11 grounded
  claims from the earlier runs (the KB compounding as designed), leaving 3 gaps to search:
  3 of 3 searches `ok` (0.875 / 1.0 / 0.375), 24 hits, 19 fetched, 13 relevant, 16 cited.
  needs_status: 5 of 5 `answered` -> the gap-closing pass correctly did NOT run, so the
  interim chat write is still unexercised live (unit-tested only). Template: buyers-guide
  (purpose: buy). Delivered document: title stating the finding; executive summary; 9-item
  "What to check in person" checklist, every item cited; failure-modes-by-subsystem table
  (7 rows, all cited); "What the evidence does not settle"; ONE limitations section with 4
  open questions and one recommendation sentence; footer `needs answered 5 of 5 - sources 13
  relevant of 19 fetched (24 hits) - render checked: 17 sentences, 2 corrected`. The
  hand-off notice was stripped; the body starts with the model's one-line lead-in, a rule,
  then the report. The two "Open gaps"/INCOMPLETE blocks are gone.
- **Render fidelity in production:** 17 units checked, 2 STRONGER -> 0 rewritten, 2 replaced
  verbatim. Visible cost exactly as K.9 predicted: the BIOS/firmware row's "What goes wrong"
  cell and the proprietary-PSU row's "What it looks like" cell now hold long verbatim
  synthesis lines. The layout fix (marker in the cell, line beneath the table) is in the
  proposed `research-trust-fidelity` item.
- **Meta judge precision, third and fourth instances:** refused as META two hedged WORLD
  claims - "The OptiPlex 3050 SFF's proprietary PSU connector may also limit the ability to
  replace the PSU with a higher-wattage Dell unit if the original 180 W unit is failing..."
  and "The user in that thread speculated that the CPU may have been damaged during the
  thermal-paste service, and the thread was marked Solved! but contained no post..." (the
  pattern-refused third, "Whether the Solved! tag ... indicates ...", is a genuine meta
  question). Two runs, four lost facts, all attribution/hedge phrasing. The judge prompt
  needs the WORLD examples "<source/user> reports/speculates/recommends <content>" and
  "<thing> may <effect> if <condition>". Add to `research-trust-fidelity` or its own item.
- `ungrounded_numbers` ["7","17"]: the "7-character" Service Tag figure and a "(Source 17)"
  pointer inside a synthesis line - the first sits outside the held source window, the
  second is a citation-shaped token the number check should skip. Minor; note for the
  fidelity item.

---

## L. research-trust-template — one shape for ten templates, and the coverage the footer claims (2026-09-12)

The operator read the buyer's guide job 64ac38cf delivered and said: "this looks good... set this
as a template for future use", and separately, on how many templates there should be, "we should
have about 4-5 now if not more". Both instructions at once: make the approved shape the house
shape, and keep the templates distinct. None was deleted; there are ten.

### L.1 The skeleton — [read-from-source]

Every template is now one entry in `SHAPES` and `buildStructure` lays the sections out in order
(`templates.ts`): title stating the finding, Executive summary, a purpose-specific action or
findings section, a findings-by-area table with a citation per row, What the evidence does not
settle, the shared LIMITATIONS_SECTION, and the Sources the renderer appends. Each template keeps
its own audience, hints, tone, and its own names for the two sections that vary:

| template | action / findings section | table | area column |
|---|---|---|---|
| buyers-guide | What to check in person | Failure modes by subsystem | Subsystem |
| scientific-paper | Findings | Findings by theme | Theme |
| technical-proposal | Recommendation | Technical factors by area | Area |
| nontechnical-proposal | Recommendation | Factors by area | Area |
| programming-doc | How to use it | Behaviour by area | Area |
| engineering-doc | Specifications and constraints | Specifications by subsystem | Subsystem |
| product-comparison | Comparison at a glance | Options by criterion | Criterion |
| market-analysis | Key players and trends | Market factors by area | Area |
| value-proposition | The value offered | Benefits by area | Area |
| general-report | What the evidence supports | Findings by area | Area |

One rule had to be added after measuring: **every section is written, in order, always**. The
first re-render of 64ac38cf dropped the failure-modes table entirely — the skeleton listed the
sections but never said they were mandatory, and a model summarising a long synthesis simply left
one out. "A section the evidence barely reaches is written thin; it is never dropped" is now in
every prompt, and the re-render carries all six.

### L.2 Three subjects, three templates — [observed-live 2026-09-12]

Each rendered through the deployed LiteLLM path and then through the SHIPPED fidelity check, in
that order, exactly as a live run does. Committed as fixtures with the numbers in their headers.

| render | sections | fidelity (N of M, corrected, unchecked) | grounding diff |
|---|---|---|---|
| `rendered-64ac38cf-buyers-guide.md` | the approved document's six, in order | 33 of 33, 4 corrected, 0 unchecked | clean |
| `rendered-a337520c-scientific-paper.md` | Findings / Findings by theme | 57 of 59, 5 corrected, 2 unchecked | clean |
| `rendered-5ab36fe0-product-comparison.md` | Comparison at a glance / Options by criterion | 25 of 26, 1 corrected, 1 unchecked | `names ["VCS"]` |

The comparison render is the one that shows the skeleton is not a hardware form: its table IS the
comparison grid, one row per criterion, one column per option, the options' real names in the
header. The 100 Hz render shows the other end — a literature question divides into themes, and
the template allows sub-headings INSIDE a section without breaking the skeleton.

**"Keeps every citation" is not literally what a summarising render does**, and the acceptance
wording is worth correcting rather than testing loosely: measured, the three renders re-cite 15
of 16, 13 of 16 and 6 of 7 sources. What is true, testable and what matters is that **none
invents a citation the synthesis does not have** and every number resolves to a source. That is
what the test asserts, with the ratios recorded above.

### L.3 The coverage the footer claims — [measured 2026-09-12]

The tester's X1/X2/X3/X4 and the reviewer's K.9/K.10, built:

- **A citation after the full stop** ("…to upgrade. [Source 13]") yielded ZERO units: the claim
  half had no citation, the citation half was two words. `normaliseCitations` puts the bracket
  back inside the sentence before anything is split. Each of the tester's three probes now yields
  exactly one unit.
- **An uncited claim in an evidence section** is judged against the NEAREST synthesis lines by
  word overlap, and is UNSUPPORTED when nothing reaches the floor — no model needed to say that a
  sentence resting on nothing is unsupported. The executive summary and the Limitations section
  are deliberately excluded: the summary compresses many sources by design, and the `[GAP]`
  questions are uncited by design.
- **No word floor inside a table.** The floor existed to keep row LABELS out, and the label rule
  (first populated cell) does that directly. "Fans are proprietary" is a four-word claim.
- **The footer says `render checked: N of M, K corrected, U unchecked`**, and both N and M are
  counted on the DELIVERED document by `countUnits`, a pure function the reader can run. This is
  the direct answer to X4 and K.10, where three artifacts carried four counts of "the same"
  document and the one in the footer was reproducible from none of them.
- **Superset citations are reported, not deleted** (`prose_ungrounded.citations`): a row citing
  four sources where two contribute nothing is a provenance defect a reader can follow, and
  removing a citation would be the engine editing a claim's evidence.
- **A long verbatim-replaced table cell** becomes `see Note N below the table` with the sentence
  whole beneath it (K.9). Nothing is clipped to fit a column, because clipping a grounded sentence
  can re-create the overstatement the replacement was repairing.

### L.4 Three defects the measurement caught, which no unit test would have

- **`normaliseCitations` ate newlines.** `\s*` after the citation bracket matched the newline at
  the end of a line ending "…replacement. [Source 11]", and welded nine checklist items and the
  heading after them into one line. Found by running the shipped check over the approved
  document; the regression test now plants a whole checklist.
- **"checked 34 of 32".** The denominator came from the delivered document and the numerator from
  the pre-correction one. Both are now counted on the same artifact, against the set of texts the
  check saw or wrote.
- **The re-judge flip-flopped.** A unit the rewriter never touched was re-asked of the same judge,
  which said SAME, and ten condemned cells in the comparison render were left standing. Only
  REWRITTEN units are re-judged now; an unrepaired unit keeps its first verdict.

And one rule the judge needed: **an honest absence statement is SAME.** "Not described in the
sources" claims nothing about the world, and replacing it with a grounded line about something
else would have made the comparison grid worse and less true.

### L.5 The meta judge's prompt — [read-from-source + measured]

`META_JUDGE_SYS` moved to `claims.ts`, beside the deterministic half of the same decision, and
gained WORLD examples for the two shapes that were losing facts: ATTRIBUTED ("<source or user>
reports / notes / recommends / speculates / argues <content>") and HEDGED ("<thing> may <effect>
if <condition>"). It also states the precedence explicitly: read the MAIN CLAUSE — a fact with a
caveat is judged on the fact, and a sentence whose main clause is about the evidence is META
"however many world-sounding words it contains".

The test mocks the judge **from the prompt**: it extracts the attribution verbs, the hedge markers
and the META examples out of `META_JUDGE_SYS` and classifies with those alone, so deleting an
example makes the test fail. The four claims the judge refused across the two live runs:

| claim | run | verdict now |
|---|---|---|
| "The same used-purchase analysis **recommends** the OptiPlex 3060…" | 33250e9b | WORLD |
| "…proprietary PSU connector **may** also limit… **if** the original 180 W unit is failing…" | 64ac38cf | WORLD |
| "The user in that thread **speculated** that the CPU may have been damaged…" | 64ac38cf | WORLD |
| "**Whether** the 'Solved!' tag… **is unclear**, as no solution text is visible…" | 64ac38cf | **META, and rightly** |

The anchor says "the four hedged/attributed world claims". Three of the four are world claims; the
fourth is a genuine meta question and the findings above said so when they recorded it. Turning
all four into WORLD would have been fixing the number rather than the defect.

**Sweep, re-run over the live claims table** (read-only SELECT, 2026-09-12): **17 of 7 930 active
claims** match `classifyMetaClaim` (0.21%). I read all seventeen: every one is genuinely
source-referential ("The provided source is a Google Scholar profile page…", "No source provided
mentions any separate fee…", "The article's text (Source 1) is heavily truncated…"). No false
positive. The earlier sweep's 43 of 7 744 (0.56%) was taken before the filter was live; those
claims are no longer written.

### L.6 Counts

| suite | before | after |
|---|---|---|
| `research-service` (service directory only) | 248 / 1 env-failed | **265 / 1** |
| `research-curator` | 36 | **40** |
| `ruff check .` | clean | clean |

`deno.lock` is gitignored, so a READ-ONLY mount fails with "Failed writing lockfile" the first
time the dependency graph changes. T1 runs the suite with `--no-lock`, which is the honest fix:
the alternative is a writable mount, and a writable mount is how a test run edits the thing it is
testing.

### L.7 Carried forward

- The tester's X2 remainder: an uncited absolute in the EXECUTIVE SUMMARY is still unchecked, by
  the same decision that protects the summary's right to compress. The GROUNDING_RULES forbid it;
  nothing measures it.
- An uncited sentence with nothing near it is counted UNSUPPORTED and left in the document when
  the rewriter cannot mend it — there is no grounded line to replace it with. It is in the record
  and in the footer's corrected count only when a repair happened.
- `entityShare` still reads `title + " " + snippet` and never `url` (ninth item running).

---

## M. research-trust-template attempt 2 — a detector that edited what it inspected (2026-09-12)

### M.1 The defect — [reproduced on the branch, tester 2026-09-12]

`normaliseCitations` ran on the text that SHIPS. It moved a citation from after the full stop to
before it, and in doing so ate the space that followed:

| written | delivered |
|---|---|
| `...across teams and services. [Source 4, 5] The answer...` | `...across teams and services [Source 4, 5].The answer...` |
| `Check the vents, e.g. [Source 3] dust...` | `Check the vents, e.g [Source 3].dust...` |
| `The unit draws approx. [Source 2] 180 W...` | `The unit draws approx [Source 2].180 W...` |

On the committed renders: the buyer's guide was untouched (its after-stop citations all sit at
line end), the scientific paper changed, and the comparison's executive summary acquired one weld
— T4's own FAIL limb. And the three fixture headers claimed they had been produced through this
check, while the check still changed two of them: **the path was not idempotent**, which is the
same statement.

### M.2 The fix is a rule, not a better regex

**Detection may not edit.** Units are derived with a normalised VIEW of each span — the text the
judge reads — and every unit keeps its ORIGINAL span. Every rewrite and every replacement is
applied to that span, so a document with nothing to correct is delivered byte for byte.

Two invariants, over all three committed renders **and the approved document**, with a judge that
answers SAME to everything:

- `INVARIANT: a document with nothing to correct is returned BYTE-FOR-BYTE`
- `INVARIANT: running the check on its own output changes nothing (idempotence)`
- and, in `template-renders.test.ts`,
  `INVARIANT: the check leaves every COMMITTED render exactly as it is` — the fixtures' headers
  make a claim about the pipeline, and this executes it.

**The abbreviations need no list.** A citation is moved in the view only when two alphanumerics
precede the stop AND the citation ENDS the span. A citation with text after it did not close a
sentence — `approx. [Source 2] 180 W` — so there is nothing to move. `Fig.`, `Inc.`, `vs.`,
`e.g.` and `approx.` are pinned on one line with two ordinary sentences, and the splitter
separates the sentences while keeping each abbreviation inside its own.

**The cost, stated:** where a citation sits mid-line between two sentences, the unit now spans
both. The judge sees both sentences and both sources together — coarser, and the price of never
touching what ships.

### M.3 The tester's X2, the other half — [measured]

- **A bullet that wraps onto a second line** yielded M = 0: not checked, not counted, not in
  `U unchecked`. It is one unit spanning both lines now, counted in M and marked unjudgeable, so
  it is never edited (an edit addressed by line and cell cannot span two lines) and lands in `U`
  where a reader sees it.
- **A citation inside a fenced block or an inline code span is not a citation.** A
  `programming-doc` render is asked for code samples; one was being presented to the judge as a
  claim and could be rewritten.

### M.4 The same mistake as the last item, in the test that exists to prevent mistakes

The first version of the byte-identity invariant read the approved document from the PARENT repo
— exactly the defect `research-trust-report` was sent back for one item ago. It passed in the
worktree and died the moment the suite ran with only the service directory mounted. The approved
document is a fixture now, and the sweep for other escapes is clean.

### M.5 The section map (tester X3)

The head docblock said "Adding a template = one entry in TEMPLATES"; a template is an entry in
`SHAPES` with ten fields. Fixed, and the docblock now carries a row per template saying where each
OLD heading's content went — Verdict, Per-option detail, Decision factors, Risks & mitigations,
Pitfalls & caveats, Standards & compliance, Abstract, Background, Discussion, Outlook, Target fit,
What was not found. `templates.test.ts` asserts the map exists, so "nothing was lost" is checkable
by reading rather than by diffing twenty prompt bodies.

### M.6 The three fixtures, regenerated through the fixed path

| render | fidelity (N of M, corrected, unchecked) | grounding diff |
|---|---|---|
| `rendered-64ac38cf-buyers-guide.md` | 48 of 52, 5 corrected, 4 unchecked | `names ["OEM"]` |
| `rendered-a337520c-scientific-paper.md` | 65 of 67, 9 corrected, 2 unchecked | clean |
| `rendered-5ab36fe0-product-comparison.md` | 25 of 25, 1 corrected, 0 unchecked | clean |

Their headers now say what they are and what was done to them, and the invariant test proves the
claim rather than restating it.

### M.7 Counts

| suite | attempt 1 | attempt 2 |
|---|---|---|
| `research-service` (service directory only, `--no-lock`) | 265 / 1 env-failed | **273 / 1** |
| `research-curator` | 40 | 40 |
| `ruff check .` | clean | clean |

### M.8 Two checks looked at the same sentence and neither acted — [measured 2026-09-12, reviewer]

Written at merge time (reviewer rt-reviewer; the item merged as `eb14055`). The tester raised X1
(a coarse unit's replacement would delete a sibling sentence) and X2 ("OEM" reached the delivered
document though the synthesis never uses the word). **They are the same sentence**, and putting
them together says more than either does alone. From the committed
`fixtures/rendered-64ac38cf-buyers-guide.md`, under "What the evidence does not settle":

    The proprietary connector makes aftermarket substitution difficult [Source 13], but the
    availability of an OEM replacement part is left open.

Measured against the shipped `citedUnits` at OB1 `e28c974`: the render yields **52 units, all 52
judgeable, 0 unchecked**, and this sentence is ONE of them - section "What the evidence does not
settle", citations `[13]`, `judgeable: true`. So:

  * it carries a MID-SENTENCE citation, which is X1's shape: a STRONGER verdict here would have
    replaced the whole span, taking the honest "is left open" clause with it;
  * its UNCITED half carries the invented name, and the run's own grounding diff recorded it -
    `grounding diff : numbers [] urls [] names ["OEM"]`, in the header of that very fixture;
  * the fidelity judge was given the unit and passed it, correctly by its own rule: the claim is
    hedged and asserts nothing the source denies.

**Two independent checks saw this sentence; one reported and one approved; neither gates.** That
is the finding. The gap is not a missing check - it is that "the renderer used a word the
synthesis never used" is currently only ever an observation. Three items in a row have produced
one (`BSOD`, then the ATX/SFX pair, now `OEM`), each recorded and none blocked.

The decision the follow-up (`research-trust-names`) should make FIRST, before any work on span
boundaries: is an unsupported NAME in a delivered document reportable or blocking? Two things
worth weighing when it is made. The three observed instances were all harmless in substance -
nothing false was asserted - so a hard gate would have rewritten three good sentences to remove
three true words. And the names arrive in UNCITED halves of cited sentences, where the fidelity
judge has nothing to compare against; the grounding diff is the check that can see them, and it
is the one with no teeth.

### M.9 Two sentences that no longer describe their own code — [read-from-source 2026-09-12, reviewer]

The class this workstream keeps paying for: the code is right, the prose beside it is not. Both
are one-line fixes and neither changes behaviour.

1. **`fidelity.ts:166`**, in the `citedUnits` docblock: *"A sentence with no citation is not
   checked. It is summary, structure or a heading, and there is nothing to compare it against"*.
   This item makes that false - an uncited unit in an evidence section IS checked, against the
   nearest synthesis lines, which is how the "drop the citation" bypass was closed. The interface
   doc at `fidelity.ts:88` and the inline comment at `fidelity.ts:234` both state the real rule,
   so a reader gets the truth inside the same function; the summary sentence above them
   contradicts it, and a summary is what gets read.

2. **`templates.ts:110-112`**, the WHERE THE OLD HEADINGS WENT map: the `scientific-paper` rows
   cover Abstract, Background and Discussion, but not its `**Answer.**` lead-in, which the
   skeleton replaces with `## Executive summary`. The map records exactly that move for
   `general-report`'s own Answer block at `templates.ts:140`, and the TEST-PLAN's removed-tests
   table explains it where the corresponding test was rewritten - so nothing is UNACCOUNTED. The
   defect is only that the map is introduced as the record that "nothing was dropped" and is
   complete but for that one row. Every other old heading in all ten templates does have a row;
   the reviewer checked by extracting the base file's headings per template rather than reading
   the map against itself.
