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

### 3.6 The collapse detector is wired in the HARNESS, not in `index.ts` — [read-from-source]

Phase 1.1 says "wire it into the `searchWeb` wrapper in `index.ts`". `searchWeb`
in `index.ts:324` is a `Deps` seam with no access to the run's counters, and
`fetchStats` is built in `harness.ts` (`RunResult.fetchStats`). Classifying
inside `index.ts` would have made `fetchStats.search` unreachable and untestable
through the mocked seams every other harness test uses. It is classified at the
call site in `harness.ts` instead. Same contract, one testable place.

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

## 6. Test counts, RED → GREEN

| module | before | after | new tests |
|---|---|---|---|
| research-service | 79 passed / 1 env-failed | (see TEST-PLAN §1) | `search-quality.test.ts`, `grounding.test.ts`, `harness-trust.test.ts`, `report.test.ts` |
| research-curator | 17 passed | (see TEST-PLAN §1) | `meta-claims.test.ts` |

Filled in at §7 below as each phase lands.

---

## 7. Phase-by-phase record

(appended as the work lands)
