# Research engine audit: OptiPlex 3050 and 100 Hz VR runs (2026-09-11)

Audited jobs (research_jobs):
- `ce398d06-32cd-47ad-92f0-a12cbf5ed114` OptiPlex 3050 used-purchase, owui, 2026-09-11 11:59-12:05 UTC
- `8c9b4f1d-f9a8-43a8-8d18-3af46f93bbe9` 100 Hz tone vs VR motion sickness, owui, 2026-09-10 20:26-20:32 UTC

## What actually happened (both runs, same mechanism)

1. **Search returned junk.** SearXNG's only responding general engine is Bing.
   Google is CAPTCHA-suspended (1 h at a time, 2026-08-22 onward, twice on 09-11),
   mojeek 403s on every call, wikipedia is rate-limited. Bing behind Mullvad
   "collapses" multi-word queries to one salient token. Replayed today via
   `localhost:8085/search`:
   - "What are the most common hardware failure modes ... OptiPlex 3050?" -> results for **"most"** (MOST 529 plan, Merriam-Webster)
   - "Dell OptiPlex 3050 common hardware failure modes capacitor CPU socket defects" -> results for **"Dell"** (dell.com home, Best Buy, Amazon)
   - "clinical trial 100 Hz tone VR motion sickness reduction study" -> results for **"clinical"** (dictionaries, clinicaltrials.gov)
   - "100 Hz sound motion sickness vestibular study" -> results for **"The 100"** (TV series)
   Chronic, not new: dictionary pages for "most", "specific", "actual", "full", "context",
   "verbatim", "how", "have", "no", "article" have been staged as sources almost daily since 2026-06-14.
2. **The relevance gate (filtering.ts) correctly rejected every fetched web page.**
   OptiPlex: 32 fetched OK + 31 cache hits, 0 survived. 100 Hz: 41 fetched OK + 26 cache hits, 0 survived.
   The gate hides the search failure instead of surfacing it: the run continues as if it had sources.
3. **KB-recall pages are exempt from the relevance gate** (harness.ts `protectedCount`).
   `retrieveRelevantSources` (distance <= 0.55) pulled semantically-near stored sources
   (DGX Spark maintenance, Compaq d220, ASUS BIOS FAQ for the OptiPlex run; the June-19
   motion-sickness set for the 100 Hz run) and, together with reuse-claim sources, they became
   the ENTIRE citable pool. Every cited source in both runs predates the job by months.
   Because protected pages existed, the fail-safe floor never fired and no "no sources" signal was raised.
4. **The synthesizer was honest about the pool** (all [GAP]s, "sources contain no information
   specific to the OptiPlex 3050"), but the curator then **stored that meta-statement as a
   grounded claim at confidence 0.85** (`0c2b7c4e-2755-42e1-9a10-f854a9d1e34b`) plus four
   0.51 "documented for the DGX Spark, NOT the OptiPlex" claims. These sit in the claims table
   with the query's own vocabulary and will be recalled as "known claims" by the next OptiPlex query.
   Same pattern in the 100 Hz run: three [INFERRED]/[UNCERTAIN] lines about what the pool
   does NOT say were stored as claims (eea04dc2, cd1d66fb, 32061b2c).
5. **Report rendering made it worse.**
   - `## What the sources actually cover` heads a list of what no source covers (general-report template).
   - `coverage 22%` is `1 - gap_ratio` over synthesis LINES (4 cited lines / 18), while 0 of 6 needs were answered.
   - The 100 Hz run was classified "scientific-paper" and titled *Absence of Evidence for 100 Hz Auditory Tones...*,
     which reads as a literature finding. It is not: the engine never retrieved a relevant page.
     The literature exists and is one query away: Nagoya University 2025, "Just 1-min exposure to a
     pure tone at 100 Hz ... may improve motion sickness", Environmental Health and Preventive Medicine,
     PubMed 40128952 (verified 2026-09-11 with an outside search).

## Claim-by-claim accuracy check (against stored source text in `sources`)

OptiPlex (11 cited, all pre-existing KB sources):
- "Restore AC Power Loss" for ASUS + Compaq [Source 2, 9]: **present** in both stored texts (OCR-mangled "Re tore AC Power Lo").
- "automatic shutdown at approximately 95 C ... DGX Spark" [Source 5, 6]: **the number 95 is in NEITHER stored source** (medium.com 1,881 ch; nvidia forum 8,000 ch) and no prior claim carries it. Fabricated figure inside an [INFERRED] line, now stored at 0.51.
- Out-of-RAM [Source 7]: source is a 453-char HTML shell (title + stylesheet tags). The claim came from a reused 06-14 claim, not from the text.
- Maintenance principle [Source 1]: only the word "proactive" matches; thin.

100 Hz (9 cited, all from the 2026-06-19 session):
- EEG 14 subjects, 1-10 Hz slow waves [S1]: **verbatim in source.**
- GVS 10 participants, 26% / 56%, p = 0.0055, "virtual environments" [S2]: **verbatim.**
- 0.2 Hz / 2 Hz thresholds [S3]: **verbatim.** (brainstem/cerebellum is in S4 only; S3 over-cited.)
- CAREN trial "30 participants" [S5, S9]: source says "thirty" recruited, n = 15 + 14 analysed; MSAQ + every 60 s: **supported.**
- AAFP slow intermittent exposure [S6], vestibular rehab [S8], parallax [S7]: **supported** ("cybersickness" is the report's word, not the source's).
Verdict: the 100 Hz report's grounded lines are accurate to their sources; its headline conclusion is an artifact of a failed search.

## Fix list (not done here; findings only)
1. Search plane: treat a collapsed result set as an engine failure. Cheap detector: fraction of hits whose title+snippet contain >= 2 non-stopword query terms; below threshold -> hits = [] and backstop reason `search_degraded`. Restore engine diversity (Google is suspended most of the day; consider a paid API behind the gateway). Alert on google/mojeek suspension streaks.
2. Harness: gate KB-recall pages too, or at minimum when zero web pages survive; when the pool is recall-only AND every need is a gap, stop with `backstop=no_relevant_sources`, render a short failure notice with the queries tried, and **do not delegate to the curator**.
3. Curator: never persist claims that describe the run ("the provided sources contain no...", "not confirmed for", "pertain to X, not Y"); retract the 5 OptiPlex claims of 2026-09-11 12:05 and the 3 meta claims of 2026-09-10 20:32 (`retract_claim`).
4. Rendering: coverage = needs answered / needs; a zero-finding run must not be rendered through scientific-paper; fix the "What the sources actually cover" heading to "What the sources cover instead".
5. Numeric grounding: every number in a [SOURCED]/[INFERRED] line must appear in a cited source's text or the line is downgraded (the 95 C case). The Skeptic exists but is dark (SKEPTIC_ENABLED=0).
6. Round-1 queries are the DECOMPOSE questions verbatim (long natural language). Start with keyword queries.
