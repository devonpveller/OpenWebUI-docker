# PLAN — Research trust: honest reports, clean ingestion, resilient search

**Status:** plan, 2026-09-11. Written for an autonomous Opus 5 run in a later round.
**Evidence base:** `documentation/notes/research-audit-optiplex-100hz-2026-09-11.md` (read it first; every
number below comes from it). Jobs `ce398d06-32cd-47ad-92f0-a12cbf5ed114` (OptiPlex 3050) and
`8c9b4f1d-f9a8-43a8-8d18-3af46f93bbe9` (100 Hz VR).
**Governing spec:** `GROUNDING-MODEL.md` (unchanged by this plan).
**Anchor:** `anchor-research-trust.json` beside this file. **Findings sink:**
`documentation/notes/research-trust-findings.md`.

## Operator's intent (in their words)

1. The returned report must be **clear, concise, validated and trustworthy**.
2. What reaches Open Brain ingestion must be **correct, accurate and trustworthy** to the same standard.
3. Search must **consistently return parsable results**; time-to-search may grow. A correct, source-backed
   report beats "this information doesn't exist". **Many hits with few readable or relevant pages is a
   search or fetch failure, not evidence of absence** — the engine must say so instead of concluding absence.

## What is actually broken (from the audit)

| # | Defect | Where | Effect |
|---|--------|-------|--------|
| D1 | Only Bing answers. Google is CAPTCHA-suspended most of the day, mojeek 403s on every call, wikipedia is rate-limited. Bing behind Mullvad collapses multi-word queries to one token ("most", "Dell", "clinical", "The 100"). | search plane: `search-gateway/searxng/settings.yml`, Mullvad exit | Every fresh page in both runs was junk. |
| D2 | The relevance gate rejects the junk but the run **continues as if it had sources**. Nothing distinguishes "search collapsed" from "topic absent". | `harness.ts` gather loop, `filtering.ts` | Silent coverage loss since at least 2026-06-14 (dictionary pages staged daily). |
| D3 | KB-recall pages are **exempt** from the relevance gate (`protectedCount`). | `harness.ts` | Months-old off-topic sources became the whole cited pool; the fail-safe floor never fired. |
| D4 | Statements about the run ("the provided sources contain no information specific to…") are parsed and **stored as grounded claims** (one at 0.85). | `research-curator/claims.ts` `parseSynthesisClaims` → `writeClaims` | KB poisoning; the next OptiPlex query recalls them as knowledge. |
| D5 | A number ("95 °C") absent from every held source text landed in an [INFERRED] line and was stored at 0.51. The Skeptic exists but is dark. | synthesis; `SKEPTIC_ENABLED=0` | A fabricated figure with a citation. |
| D6 | `coverage 22%` is `1 - gap_ratio` over synthesis LINES while 0 of 6 needs were answered. The heading "What the sources actually cover" sits over a list of what no source covers. A zero-finding run was rendered as a *scientific paper* titled "Absence of Evidence…". | `lib.ts renderResult`, `templates.ts` | A convincing report with a wrong headline. The 100 Hz literature exists (PubMed 40128952). |
| D7 | Round-1 search queries are the DECOMPOSE questions verbatim (long natural language). | `harness.ts` | The worst input for a collapsing engine. |
| D8 | Fetch "errors" (18 of 50, 31 of 72) are one bucket: non-HTML, HTTP status and empty extract are indistinguishable. `FETCH_MAX_CHARS=8000`; the synthesizer sees 2 000 chars per source. | `index.ts fetchPage`, `harness.ts sourceLine` | Unreadable and irrelevant cannot be told apart; thin evidence per source. |

## Ground rules for the run

- Worktree-per-session: `scripts/agent-harness/new-worktree.ps1 -Id research-trust`, then `EnterWorktree` the
  printed path. Propose the anchor with `queue.ps1 -Propose -Id research-trust -Anchor <anchor file> -Developer wt-research-trust`
  **before** any code; work starts after `-ConfirmAnchor`.
- Research-service and curator code live in the **OB1 submodule**. Land OB1 commits on OB1's remote first,
  then bump the gitlink in the parent (CLAUDE.md, "OB1 submodule"). `search-gateway/`, compose tunables,
  `stack.ps1` and the OWUI tool are in the parent repo.
- Test images tag `:wt-research-trust` and carry `--label ai-stack.harness.owner=research-trust`; never attach
  test containers to `ai-stack_*` networks. Read-only probes (dry_run jobs against `openbrain-research`,
  `localhost:8085/search`) need no lease. Rebuilding or restarting `openbrain-research` / `openbrain-curator`
  needs the Open Brain plane lease; a SearXNG config change needs the search plane lease
  (`lease.ps1 -Acquire -Name <plane>`; names in `scripts/agent-harness/lease-names.conf`).
- Every fix ships **RED → GREEN**: write the failing test from the audited artefact first. Prose never verifies.
- Never route inference around LiteLLM; never GET LiteLLM `/health`. Never `docker volume prune`.
  Never **delete** claims: `retract_claim(id, reason)` only.
- Do not fill research gaps from model knowledge anywhere, including tests. The 100 Hz check in Phase 5 is a
  *retrieval* assertion (the engine finds a page), not a fact assertion.
- Time budget is not the constraint; correctness is. A phase that cannot reach its acceptance criteria stops
  and writes why to the findings sink. It does not lower the bar.

---

## Phase 0 — Fixtures and baseline (no behaviour change)

**0.1 Record the failure as fixtures** in `OB1/integrations/research-service/fixtures/`:
- `search-collapsed-most.json`, `search-collapsed-dell.json`, `search-collapsed-the100.json`: the live gateway
  JSON for the three replayed queries in the audit (fetch again via `localhost:8085/search?q=…&format=json`;
  they reproduce today).
- `search-good-optiplex.json`: a healthy result set. If no engine can produce one today, hand-build it from
  the Dell support URLs the audit's outside search returned and say so in the fixture header.
- `job-optiplex.json`, `job-100hz.json`: `research_jobs.result` for the two audited jobs plus the eleven and
  nine cited `sources.content` texts (`psql -Atc` exports; the audit lists the ids).
- `claims-poison.json`: the 8 claim ids and texts listed in the audit.

**0.2 Baseline measurements** (write to the findings sink, dated):
- Per-engine health now: `docker exec searxng wget -qO- http://localhost:8080/config` enabled engines;
  `docker logs searxng` suspension counts per engine per day over the whole log.
- For 12 canonical queries (the two jobs' six needs each): hits, engines answering, **term-overlap ratio**
  (fraction of hits whose title plus snippet contain at least 2 non-stopword query terms), fetched OK,
  readable (extract ≥ 400 chars), relevant (current gate). This table is the before/after for Phase 1.

**0.3 Test scaffolding.** Confirm `deno test -A` runs green in `research-service/` and `research-curator/`
as-is and record the counts. Everything later is measured against these numbers.

**Acceptance 0:** fixtures exist and load; the baseline table is in the sink with the collapse reproduced on
at least 3 of 12 queries. If it does NOT reproduce, stop and report: the plan's premise changed.

---

## Phase 1 — Search plane: parsable results, and "broken" is not "absent"

**1.1 Query-collapse detector (harness side).** New `search-quality.ts` (pure):
`classifyHits(query, hits) → { verdict: "ok" | "collapsed" | "empty", overlap: number, collapsedOn?: string }`.
A result set is *collapsed* when overlap < 0.3 **and** at least 60 % of hits share one query token in their
titles (the "most" / "Dell" signature). Wire it into the `searchWeb` wrapper in `index.ts`: a collapsed set
is returned as `[]` and counted in a new `fetchStats.search = { calls, ok, collapsed, empty, errors }`.
- RED: fixtures `search-collapsed-*` → `collapsed`; `search-good-optiplex` → `ok`. GREEN after implementation.
- Property: a hand-built set where every title contains all query terms is never `collapsed`.

**1.2 Keyword queries from round 1.** New `KEYWORDIZE_SYS` prompt: for each need, emit ONE 3-to-7-term web
query that must contain the subject entity from the QUESTION (for example "OptiPlex 3050"). Round 1 searches
these, never the raw need. Keep DEEPEN for later rounds and give it the same entity constraint.
- Test (mocked chat): every generated query contains the entity string; none exceeds 10 tokens.
- On a `collapsed` verdict, retry the need once with a **reformulated** query (drop stopwords and verbs, keep
  the entity plus two nouns, append one of `problems | guide | review | study | forum` by template class).
  A second collapse marks the need `search_failed`, not `open`.

**1.3 Engine health, measured and surfaced.**
- Extend the search gateway `/health` with engines enabled and `engines_answering_recent` derived from the
  `engines` field of the last N SearXNG payloads (the gateway already sees them in `normalize_searxng_payload`).
- `scripts/stack/stack.ps1 health` prints `search: N engines answering (bing only = DEGRADED)`.
- **Experiment, not assumption.** Under the search plane lease, measure (same 12 queries, overlap ratio) each
  of: `language=en-US` on the SearXNG call; `safesearch=0` versus `1`; enabling `qwant`, `brave`, `duckduckgo`,
  `startpage` one at a time behind the current Mullvad exit; a different Mullvad relay. Adopt whatever raises
  median overlap above 0.6 with at least 2 engines answering. Record every result in the sink, including the
  negatives. If nothing gets there, write the case for a keyed API engine (for example Brave Search API via
  the gateway, egress unchanged) as an **operator decision**. Do not add keys.

**1.4 Search until there is evidence, not until round 3.** Replace the fixed `MAX_ROUNDS` stop with a yield
target: continue rounds while `relevantPages < RELEVANT_TARGET` (default 8, tunable) **and** un-searched
reformulations remain, up to `MAX_WALL_MS` (raise to 20 minutes in compose for the owui origin; keep the
digest and article path bounds untouched). A collapse streak of 3 consecutive calls ends gathering with
`backstop = "search_degraded"`.
- Test (mocked deps): with a search that returns junk forever, the run ends `search_degraded`, produces no
  synthesis from recall only, and wastes at most 3 calls after the streak begins. With a search that returns
  good pages, the run stops at the target, not at round 3.

**1.5 Readable versus relevant versus absent.** Split `fetchErrors` into `{ http, non_html, empty_extract, network }`
and add `readable` (extract ≥ 400 chars). Raise `FETCH_MAX_CHARS` to 16 000 and the synthesizer slice for fresh
sources from 2 000 to 4 000 chars (measure LLM wall time on one replay; keep if under +50 %). When
`hits ≥ 20 && readable / hits < 0.2` → `backstop = "fetch_degraded"`, never `complete`.
- Test: fixtures with 25 hits and 3 readable → `fetch_degraded`; 25 hits, 15 readable, 0 relevant → proceeds
  to Phase 2's `no_relevant_sources`.

**Acceptance 1:** on the 12 baseline queries, median term-overlap ≥ 0.6 and at least 5 relevant readable
sources for the OptiPlex needs, **or** `search_degraded` with the collapse counted (the honest failure is
acceptable; a `complete` run with 0 relevant sources is FAILING). Unit tests for 1.1, 1.2, 1.4, 1.5 green
from RED.

---

## Phase 2 — Harness honesty when evidence is thin

**2.1 Gate recall pages too.** Run `partitionRelevant` over KB-recall pages (protect only caller seeds in
article and sources-only modes). Recall pages that fail are dropped *before* the fail-safe floor is evaluated.
- RED: replay `job-optiplex` with mocked search = collapsed fixtures and mocked relevance = IRRELEVANT for
  non-OptiPlex titles. Today the pool has 11 sources; after the fix, 0.

**2.2 A first-class "no relevant sources" outcome.** When the post-gate pool is empty, or every need is
`search_failed`, the run:
- does **not** synthesize from reuse claims alone unless `COVERAGE_SYS` marked at least 1 need covered;
- returns `outcome: "no_relevant_sources"`, the Phase 1 `backstop`, and a `search_record` (queries tried with
  verdicts; hits, fetched, readable, relevant counts);
- **skips the curator entirely** (`curator: { state: "skipped", reason }` through the existing `CuratorOutcome`
  path so `research_jobs` stays truthful);
- renders the Phase 4.3 notice, not a report.
- Test: an end-to-end mocked run asserts no `delegateToCurator` call and that the rendered text contains
  "search failure, not evidence of absence".

**2.3 Numeric grounding check.** New pure `groundNumbers(line, citedTexts) → { ok, missing: string[] }`:
every numeric token in a [SOURCED] or [INFERRED] line (unit-aware: `95 °C`, `26%`, `p = 0.0055`, `14`) must
occur in at least one cited source's held text (normalised whitespace and unicode). A miss downgrades the line
to [UNCERTAIN], appends `(unverified figure: 95 °C)`, and the run records `ungrounded_numbers`.
- RED: the audited "95 °C … [Source 5, 6]" line against the two held texts → `missing: ["95"]`. The 100 Hz
  "26% / 56% / p = 0.0055 [Source 2]" line → `ok`.
- Run it **before** `buildCitedAndRenumber` (index-safe: line count and citations unchanged).

**2.4 Skeptic trial.** Set `SKEPTIC_ENABLED=1` on the test image; replay the two audited jobs plus three recent
notebook jobs. Adopt in compose only if it downgrades the 95 °C line and downgrades **none** of the nine
verified 100 Hz lines. Record the verdicts in the sink either way.

**Acceptance 2:** replaying `job-optiplex` end to end (mocked search = collapsed fixtures) yields
`no_relevant_sources`, zero curator calls, zero claims. Replaying `job-100hz` with its real pool yields the
same nine [SOURCED] lines unchanged, with the meta-lines handled by Phase 3.

---

## Phase 3 — Ingestion trust (curator)

**3.1 Meta-claim filter.** In `claims.ts`, before `writeClaims`: reject a claim that is *about the sources or
the run* rather than about the world. Two layers: deterministic patterns
(`^the (provided )?sources? (contain|do(es)? not|lack)`, `^no (provided )?source`, `not confirmed for`,
`pertain(s)? to .+, not `, `documented for .+; this is not`), then a nothink LLM judge for the rest
("WORLD or META, one word", fail-open = keep). Rejections are counted (`claims.metaSkipped`) and logged with text.
- RED: all 8 texts in `claims-poison.json` → META; the 10 factual 100 Hz claims (13 were written; 3 are among the 8 poison) → WORLD. GREEN after.

**3.2 Omnibus-citation downgrade.** A single line citing more than 4 sources is a smell (the 0.85 "all eleven
sources say nothing" claim). Parse it as [UNCERTAIN] at most; never write `states` edges for more than 4 sources
from one line.

**3.3 Retract the existing poison** (gated by the confirmed anchor; execute after 3.1 is deployed so the next
run cannot re-create it). One statement per id:
`SELECT public.retract_claim('<id>', 'research-audit 2026-09-11: meta-claim about a run, not a fact');`
OptiPlex: `0c2b7c4e-2755-42e1-9a10-f854a9d1e34b`, `7f2ac93b-6680-40b9-84a6-6ce49e52fac1`,
`ed40c734-bf3f-444d-99c1-cd11091f6da4`, `1306bb5a-560c-4d79-a96a-960c35038495`, `f4c0bd7a-9707-4dd2-aecd-d6ea446b338f`.
100 Hz: `eea04dc2-d88c-47bc-b23d-65867813324e`, `cd1d66fb-b4f5-4496-8090-33568126be7d`, `32061b2c-a641-4ed3-a4d4-869a6c3d6ab2`.
Verify: `SELECT status FROM claims WHERE id IN (…)` is `retracted` for all 8; a `dry_run` job for the OptiPlex
query returns `reuse_claims = []`.

**3.4 Sweep for older poison.** `SELECT id, text FROM claims WHERE status = 'active' AND (text ~* '^the (provided )?sources? (contain|do not)' OR text ~* '^no (provided )?source')`.
List the result with counts in the sink; retract only what the 3.1 patterns match. The operator can widen later.

**Acceptance 3:** curator tests green from RED; the 8 ids retracted; the sweep recorded; a fresh replay of
`job-optiplex` through the *deployed* curator writes 0 claims.

---

## Phase 4 — Report clarity

**4.1 Honest coverage.** Persist per-need status (`answered | partial | open | search_failed`) from the
COVERAGE_STAGED verdicts into `result.needs_status`. `coverage` becomes `answered / needs`. The footer reads:
`needs answered 0 of 6 · sources 0 relevant of 32 fetched (63 hits, 41 collapsed) · search: DEGRADED (bing only)`.
- Test: the OptiPlex replay renders `needs answered 0 of 6` and never `coverage 22%`.

**4.2 Template by evidence, not by topic.** `classifyTemplate` is consulted only when `answered ≥ 3`;
otherwise `general-report`. `no_relevant_sources` bypasses templates entirely (4.3).

**4.3 One concise structure, answer first.** Rewrite `general-report`:

```
# <Title that states the answer, not the topic>
**Answer.** 2-4 sentences. If nothing was found: "No source relevant to <subject> was
retrieved. This is a search failure (<reason>), not evidence that the information does not exist."
## What the evidence supports   - cited bullets, one claim each, at most 12
## What was not found            - the [GAP]s as questions (omit if none)
## Search record                 - needs x status table; queries tried; hits / fetched / readable / relevant
```

Target at most 600 words for up to 6 needs. Delete the heading "What the sources actually cover" everywhere.
The scientific-paper template gains the same **Answer** block at the top and a Search record section. Its
title may never assert absence ("Absence of evidence…") unless `answered ≥ 3` and the gaps concern
sub-questions only.
- Test: render fixtures for (a) `no_relevant_sources`, (b) the 100 Hz pool, (c) a healthy run. Assert the
  Answer block is first, the word counts, and that (a) contains the search-failure sentence.

**4.4 OWUI callback parity.** `renderResult` in `lib.ts` and `_render` in `owui/tools/deep_research.py` stay
in step for the fallback path (the tool trusts `result.rendered`; add the `needs answered` footer to the
Python fallback too). Re-pasting the tool into OWUI is an operator step. Say so in the hand-off.

**Acceptance 4:** the three render fixtures pass; a human reading the OptiPlex replay output can answer
"did it find anything?" from the first line.

---

## Phase 5 — End-to-end validation and deploy

1. **Unit:** `deno test -A` in research-service and research-curator all green; counts at least the Phase 0
   counts plus the new tests. `ruff check .` clean in the parent.
2. **Replay:** the two audited jobs plus three recent notebook jobs through the harness with recorded search
   fixtures. Outcomes as specified in Acceptance 1 to 4. Save the rendered outputs beside the fixtures.
3. **Test images:** `openbrain-research:wt-research-trust` and `openbrain-curator:wt-research-trust`
   (`docker build` in OB1 with the owner label). Run them on a private network against a throwaway
   `pgvector/pgvector:pg16` initialised with the OB1 init chain (the P1 recipe in the `research-engine-plan`
   memory) to prove the curator gate and the retraction verification without touching live.
4. **Live, under the Open Brain plane lease:** rebuild and restart `openbrain-research` and `openbrain-curator`
   (`docker compose -f OB1/docker/docker-compose.yml build … ; up -d …`); health via `stack.ps1 health`. Then:
   - `dry_run` job, OptiPlex query: outcome is **either** at least 5 relevant OptiPlex sources cited **or**
     `no_relevant_sources` with `search.collapsed > 0` and `curator.state = "skipped"`. Anything else fails.
   - `dry_run` job, 100 Hz query: **retrieval** assertion only. With the search plane healthy, the cited set
     includes a URL matching `pubmed.ncbi.nlm.nih.gov/40128952` or `nagoya-u.ac.jp`. With the plane degraded,
     `search_degraded` and no "Absence of evidence" title.
   - Regression: the next scheduled notebook-origin jobs still stage fresh sources
     (`sources.created_at >= job.started_at` count > 0) and the digest/article path tests are untouched.
5. **Retraction (3.3)** executed and verified only after step 4 is green.
6. **Land:** OB1 commits pushed to OB1's remote → parent gitlink bump plus the `search-gateway`, compose,
   `stack.ps1` and OWUI-tool changes in one PR on a work branch cut from `development`; `queue.ps1 -Submit` with
   the test plan; a tester who did not write it runs steps 1 to 4; the reviewer merges `--no-ff` with the
   evidence. Container-rule surfaces: none added or removed (health probe text only). Confirm with `/stack-map`.
7. **Docs and memory:** update the `research-engine-for-OB/` row in `documentation/implementation-guide/README.md`
   and the `research-engine-plan` memory with what shipped. The audit note stays as the record.

## Out of scope (write to the sink, do not build)

- A second adversarial reviewer beside the Skeptic (memory: "don't build two adversarial reviewers").
- Source length floors (memory: research-source-admission-plan; thin-but-true evidence is the point).
- Wiki history and purge decisions; Open Notebook; the source → proposal → claim lifecycle.
- Adding paid search API keys (operator decision; the case is prepared in 1.3).

## Definition of done (what the tester checks, in order)

- [ ] Phase 0 baseline table in the sink; the collapse reproduced.
- [ ] `search-quality.ts` plus tests; collapsed fixtures classified; round-1 queries carry the entity.
- [ ] `search_degraded`, `fetch_degraded`, `no_relevant_sources` reachable and tested; no `complete` run with 0 relevant sources.
- [ ] Recall pages gated; curator skipped on empty pools; numeric grounding downgrades the 95 °C line.
- [ ] Meta-claim filter rejects all 8 poison texts and keeps the 13 factual ones; the 8 ids retracted.
- [ ] Reports open with an Answer block; the footer shows `needs answered X of N`; no "Absence of evidence" title on a zero-finding run.
- [ ] Live dry runs behave per Phase 5.4; notebook jobs unaffected; images rebuilt from the bumped gitlink.
