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
