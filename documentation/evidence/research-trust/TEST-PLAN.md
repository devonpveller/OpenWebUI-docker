# TEST-PLAN — harness item `research-trust`

**Branch:** `work/research-trust` (parent) + `research-trust` (OB1 submodule, NOT pushed).
**Developer:** `wt-research-trust`. **Anchor:** confirmed by profnovice; read it with
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust`.
**Written for a tester who did not build this.** Every case says what to run, what passing
looks like, and what failing looks like. Findings that are true but out of scope are in
`documentation/notes/research-trust-findings.md`.

---

## Preconditions - where to run, and what this branch does NOT touch

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust` and
it holds the branch, so you cannot make a second worktree on it. Cases T1–T16 are **read-only
executions** (`deno test`, `pytest`, `git show`) — running them in that directory changes no
git state and is safe. Do not `git add`, `git commit` or edit anything there.

Review the DIFF from the blob, never the working tree (`core.autocrlf` rewrites line endings
locally):

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat refactor/ai-stack-cleanup..work/research-trust
git show work/research-trust:search-gateway/searxng/settings.yml
git -C OB1 diff --stat 9a772aa..research-trust        # the OB1 half
```

**Nothing live was changed by the developer.** No container was restarted, rebuilt or
reconfigured; no claim was retracted; no compose project was brought up or down. The only
live access was read-only: `psql` SELECTs, `GET /search`, `docker logs`. Sections D and E
are the steps that DO touch live, and they are for after this plan passes.

**Leases.** T1–T16 need none (nothing running is touched). Section D needs the **open-brain**
plane lease, and the **search** plane lease for the SearXNG change
(`.\scripts\agent-harness\lease.ps1 -Acquire -Name <plane>`; names in
`scripts\agent-harness\lease-names.conf`).

---

## Declared - the anchor criterion that was amended, and what T7 tests

**Anchor criterion 4 was AMENDED after attempt 1** and now reads: "...rejects all 8 texts in
claims-poison.json and keeps the **10** factual 100 Hz claims (the run wrote 13; 3 of them are
among the 8 poison)". Nothing is in dispute any more; this section records why the number
changed, because the fixtures are split on it.

The 100 Hz run (`8c9b4f1d`) wrote 13 claims in total, and `eea04dc2`, `cd1d66fb` and
`32061b2c` are among them *and* among the 8 poison. Verify, read-only:

```bash
docker exec openbrain-db psql -U postgres -d openbrain -Atc "select count(*) from claims where id in ('eea04dc2-d88c-47bc-b23d-65867813324e','cd1d66fb-b4f5-4496-8090-33568126be7d','32061b2c-a641-4ed3-a4d4-869a6c3d6ab2')"
```
-> `3`. So 8 must be rejected and **10** kept: `claims-poison.json` (8) and
`claims-100hz-world.json` (10).

## T1 - Unit suites, and the one failure that is not this branch's

**Run** (read-only, in the developer's worktree):

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/search-gateway/gateway" && PYTHONPATH=src python -m pytest tests/test_engine_health.py -q
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust" && ruff check .
```

**PASS:** research-service `143 passed | 1 failed`; research-curator `36 passed | 0 failed`;
pytest `8 passed`; ruff `All checks passed!`.

The single research-service failure must be **`./orchestrator.test.ts (uncaught error)` …
`No such host is known. (os error 11001)`** — it opens a postgres pool at `DB_HOST` (default
`ob-claims-test`) at module load. It fails identically on the base commit; T9 runs it
properly.

**FAIL:** any other failing test; a research-service count **below 143**; a curator count
**below 36**; any ruff error. A count below the baseline (79 / 17) means tests were removed,
which is a fail regardless of the colour of the output. (Attempt 1 stood at 121 / 28 and attempt 2 at
136 / 33; the growth is the tester's findings, each pinned as its own case.)

---

## T2 - The collapse detector, against the three recorded failures (anchor criterion 2)

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno test -A search-quality.test.ts
deno test -A harness-trust.test.ts --filter "entity"
```

**PASS:** `19 passed | 0 failed` from `search-quality.test.ts`, including all three
`collapsed fixture:` cases and `good fixture: a real multi-engine OptiPlex result set is ok`;
and the round-1 test passes, proving every round-1 query contains `optiplex 3050` and is
≤ 10 tokens even though the mocked model returned queries WITHOUT the entity.

**Check the fixtures are real, not written to fit** (they are the live gateway's own JSON):

```bash
python -c "import json;d=json.load(open('fixtures/search-collapsed-dell.json',encoding='utf-8'));print(d['_provenance']);print(d['query']);print([h['title'] for h in d['hits'][:3]])"
curl -s -G "http://127.0.0.1:8085/search" --data-urlencode "q=Dell OptiPlex 3050 common hardware failure modes capacitor CPU socket defects" --data-urlencode "format=json" | python -c "import json,sys;d=json.load(sys.stdin);print([r['title'] for r in d['results'][:3]])"
```

**PASS:** the live GET still returns the same class of junk (`Dell USA` / `Support Home` /
`Dell - Wikipedia`), i.e. the fixture is a faithful capture of a failure that still
reproduces. This GET is read-only and needs no lease.

**FAIL:** a `collapsed` fixture classified `ok`; the good fixture classified `collapsed`; a
round-1 query missing the entity or longer than 10 tokens; a fixture whose `_provenance`
says it was hand-built.

---

## T3 - The audited 95 °C line is flagged; the 100 Hz figures are not (anchor criterion 3)

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno test -A grounding.test.ts
```

**PASS:** `10 passed | 0 failed`. In particular the audited line is flagged with
`missing: ["95", "3050"]` and the 100 Hz `26% / 56% / p = 0.0055 [Source 2]` line is `ok`,
and **no [SOURCED] line of the 100 Hz run is downgraded**.

Note the deviation from the plan's literal expectation, and satisfy yourself it is right:
the plan predicted `missing: ["95"]`. The line also asserts the model number **3050**, which
neither DGX Spark source mentions. Confirm both directly:

```bash
python -c "
import json;j=json.load(open('fixtures/job-optiplex.json',encoding='utf-8'))
t=j['cited_texts'][4]['content']+j['cited_texts'][5]['content']
print('95 in sources 5+6:', '95' in t, '| 3050 in sources 5+6:', '3050' in t)"
```
→ both `False`. Two true misses, not one.

**FAIL:** the 95 °C line passing; any 100 Hz `[SOURCED]` line downgraded; the line count or
any `[Source N]` marker changed by `applyNumericGrounding` (tested, but re-read the assertion).

---

## T4 - The OptiPlex replay ends as a search failure with zero curator calls (anchor criterion 1)

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno test -A harness-trust.test.ts
```

**PASS:** `22 passed | 0 failed`. The `REPLAY ce398d06:` cases must all pass:
`outcome === "no_relevant_sources"`, **zero** `delegateToCurator` calls, **zero**
`deps.chat` synthesizer calls, zero cited sources, and the rendered text contains
`search failure, not evidence of absence`. Also assert-by-reading: the case
`a 'complete' run citing 0 relevant sources is impossible` is the anchor's "a 'complete' run
citing 0 relevant sources FAILS".

**Read the replay's own honesty** before accepting it — the mock serves the payload the live
gateway recorded for a Dell query, against queries that all carry `Dell OptiPlex 3050`
(`harness-trust.test.ts`, `REPLAY_ENTITY` / `DELL_HITS`, with the reason in a comment). If
you think that pairing is unfaithful, that is a real finding: say so.

**FAIL:** any curator call in a replay; an `outcome` of `complete` with zero cited sources;
a synthesis produced from an empty pool.

---

## T5 - The report says how much of the question was answered (anchor criterion 5)

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno test -A report.test.ts
deno test -A templates.test.ts
deno test -A lib.test.ts
deno test -A harness-trust.test.ts --filter "coverage"
```

**PASS:** `report.test.ts` 7/7, `templates.test.ts` 8/8, `lib.test.ts` all green, and the
replay case asserts the rendered text contains `needs answered 0 of` and contains **none**
of `coverage NN%`, `absence of evidence`, `what the sources actually cover`, and that
`reportType` is empty (no topic template on a zero-finding run).

**Diff what was REMOVED**, which is the point of this case:

```powershell
cd "D:\Open WebUI\ai-stack"
git -C OB1 diff 9a772aa..research-trust -- integrations/research-service/lib.ts | Select-String -Pattern "^\-.*coverage"
git -C OB1 diff 9a772aa..research-trust -- integrations/research-service/lib.test.ts | Select-String -Pattern "coverage 75"
```

**PASS:** `coverage ${...}%` is gone from `lib.ts`, and the `lib.test.ts` assertion
`coverage 75%` was CHANGED (not deleted) to `needs answered 3 of 4` plus an explicit
`assertEquals(/coverage \d+%/.test(out), false)`. A behaviour change to a renderer that every
research job passes through, with its old assertion rewritten, is exactly the thing a
reviewer must see: satisfy yourself the replacement is stronger, not weaker.

Also check what a PRE-EXISTING job row now renders as: `renderResult` with no `needs_status`
prints **no** coverage number at all (test:
`a job recorded BEFORE needs_status prints no coverage number at all`). If you think old
jobs should keep showing something, that is a finding for the operator.

**FAIL:** `coverage` surviving in either renderer; the footer printed twice (test:
`the footer is not printed twice when prose already carries it`); a template whose prompt
still permits an absence title.

---

## T6 - The heading the anchor names does not exist in the code, and never did

Anchor criterion 5 requires the replay never prints the heading
`What the sources actually cover`.

```powershell
cd "D:\Open WebUI\ai-stack"
git grep -n -i "sources actually cover" refactor/ai-stack-cleanup -- ':!documentation'
git grep -n -i "sources actually cover" work/research-trust -- ':!documentation'
git -C OB1 grep -n -i "sources actually cover" research-trust
```

**PASS:** no hits in code on either branch. The heading was written by the MODEL, inside the
`general-report` template's free-form "supporting detail under `##` section headers"
instruction. There is no string to delete, so the guarantee has to come from somewhere else
— and it does: a `no_relevant_sources` run never reaches a template at all (T4), and the
rewritten `general-report` structure prescribes exactly three sections
(`git show work/research-trust:OB1` is a gitlink; read it via
`git -C OB1 show research-trust:integrations/research-service/templates.ts`).

**FAIL:** the string existing in code and still being emitted; or the developer claiming to
have deleted a string that was never there.

---

## T7 - The curator refuses statements about the run (anchor criterion 4)

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-curator"
deno test -A meta-claims.test.ts
```

**PASS:** `19 passed | 0 failed`. All 8 `claims-poison.json` texts classify `meta`; all 10
`claims-100hz-world.json` texts classify `world`; `writeClaims` writes 0 of the 8 and
records `metaSkipped: 8`; the LLM judge is asked ONLY about what the patterns did not
recognise and **fails open** on an error.

### What changed after attempt 1, and the rule you are checking

The tester failed this case by running the sweep below and reading it: five live claims were
being deleted whole because each ended in an honest epistemic tail. The filter now judges the
**HEAD CLAUSE** — the text before the first `;` or `, but` / `, though` / `, although` /
`, however` — for the two families that CAN appear as a caveat, and the whole text only for
the family that cannot:

| family | scope | what it recognises |
|---|---|---|
| `SOURCE_SUBJECT` | head clause | the sentence's subject IS the evidence set: "The sources…", "No source…", "The retrieved pages…", "This report could not…" |
| `EVIDENCE_ABSENCE` | head clause | absence of EVIDENCE without the word "source": "No evidence exists that…", "The literature is silent on…", "No study has tested…" |
| `TRANSFER_DISCLAIMER` | whole text | the sentence's point is that the evidence is about something ELSE: "documented for the DGX Spark … NOT confirmed for the OptiPlex", "pertain to X, not Y", an inline "(Source 3)" |

Read the rule and satisfy yourself it is a rule and not a fit to the eight strings:

```powershell
git -C OB1 show research-trust:integrations/research-curator/claims.ts | Select-String -Pattern "headClause" -Context 0,12
```

**PASS:** `headClause()` splits on the first caveat marker; `SOURCE_SUBJECT` and
`EVIDENCE_ABSENCE` are applied to the head only; `TRANSFER_DISCLAIMER` to the whole text. The
two whole-text families the tester named as spending the precision — `sources provided|given`
and `sources do|does not` as free-floating patterns, and `but no source` — are **gone**.

**X1 — every clause of the head sentence is judged, not just the first.** The
`SOURCE_SUBJECT` patterns are `^`-anchored, which is what makes them test the SUBJECT rather
than fire on any mention of "the sources" — and a LEADING subordinate clause walked past them:
"While the provided sources do not address the Dell OptiPlex 3050, they describe the NVIDIA DGX
Spark…" is a restatement of the 0.85 poison that attempt 1 caught and attempt 2 did not. The
head is now split at a LEADING `While|Although|Though|Whereas|Even though` and both clauses are
judged.

A subordinator LATER in the sentence is not split on, and that distinction cost a live false
positive when it was missing: "…is architecturally distinct from the adversarial-prevention
layer, **since the sources describe these as independent properties**" (`083b830e`) is a world
claim with a justification, the same kind of tail as "but no source confirms it".

```bash
deno test -A meta-claims.test.ts --filter "X1"
```

**PASS:** three cases — the four leading-subordinate poison rewordings classify `meta`; four
leading-subordinate WORLD claims stay `world`; and the `since …` / `because …` reason clauses
stay `world`.

Confirm the five the tester found are kept, and the B4 shapes are caught:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-curator"
deno test -A meta-claims.test.ts --filter "tail"
deno test -A meta-claims.test.ts --filter "B4"
```

**PASS:** `a world fact with an epistemic TAIL survives (the five the tester found)`,
`the head clause is what is judged, and a tail alone never condemns a claim`,
`B4: absence-of-EVIDENCE statements are meta even without the word 'source'` and
`B4: absence in the WORLD is not absence of evidence` all pass. The five live ids are in the
test file with their stored text, so you can check them against the DB:

```bash
docker exec openbrain-db psql -U postgres -d openbrain -Atc "select id, left(text,70) from claims where id in ('51254103-920b-4446-87e0-08babb5ad6ea','219dbaa6-c81c-4c29-b6e1-9991f55601c6','c1e411e4-5035-4114-bd6a-2ca036595721','d039348b-d38a-4fc5-a8e0-3e66c3f18e45','70f6a17c-1edb-4587-9624-41841e607679')"
```

### The live precision sweep — this is the part that failed last time

```bash
docker exec openbrain-db psql -U postgres -d openbrain -Atc "select coalesce(json_agg(json_build_object('id',id,'text',text,'created_at',created_at))::text,'[]') from claims where status='active'" > "$TMP/active.json"
```

Classify every row with the SHIPPED `classifyMetaClaim` (a short deno script importing
`./claims.ts`; make it assert its input is an array of objects before scanning — the
developer's first version silently scanned strings and reported 0 matches), **and read every
match**.

**Expected: 25 matches on 7 744 active claims (0.32 %), down from 43.** All 8 poison ids are
among them. This is the SAME 25 the tester read and judged evidence-side in attempt 2 — the
id sets are identical, verified by diffing the two sweeps — even though X1 widened recall.
Widening recall without losing precision is the thing to check here. The developer's judgement of the other 17: every one has the evidence set, a
retrieved page, or an explicit transfer disclaimer as its subject. The closest calls, stated
so you can disagree with them:

- `ea96affc`, `e195acb0`, `c93f70ca`, `a67b9130`, `216046b8` — "The sources note/confirm/
  reference/suggest that <world content>". The world content is real, but the sentence's
  subject is the evidence set, which is poison `cd1d66fb`'s exact shape. The developer's
  position: the fact should have been asserted directly, and attributing it to "the sources"
  makes the claim about the sources.
- `8ed6a678` — "The tabbit.com page (Source 6) features testimonials … indicating real users
  are already operating the product". Carries a world inference, but its subject is a page
  and it cites a source index inline.
- `7c895760`, `6fbbb70c` — "The (provided) source is a Google Scholar profile page for X".
  A description of a retrieved artefact.

**FAIL:** any of the 8 kept; any of the 10 rejected; a judge failure dropping a claim; a
sweep that is not clean **by your own reading** — that is, a match you judge to be about the
world. This clause is unchanged from attempt 1 and it is the one that caught the defect.

---

## T8 - The engine change is measured, not argued (anchor criterion 7)

Read `documentation/notes/research-trust-findings.md` §1.2 and §2, then reproduce the
**baseline** — the part that can be checked against the live system today, read-only:

```bash
docker exec searxng python -c "
import json,urllib.request,urllib.parse
q='100 Hz sound motion sickness vestibular study'
d=json.load(urllib.request.urlopen('http://localhost:8080/search?'+urllib.parse.urlencode({'q':q,'format':'json'})))
print(len(d['results']), sorted({r.get('engine') for r in d['results']}))
print([r['title'][:60] for r in d['results'][:3]])
print(d.get('unresponsive_engines'))"
```

**PASS:** one engine (`bing`) contributes every hit and the titles are about *The 100* (TV
series) — i.e. the note's baseline (median overlap 0.00, bing only) still describes the live
plane.

**Do NOT require `mojeek` in `unresponsive_engines`.** The attempt-1 plan did, and the tester
found it empty on 4 of 4 probes: a suspension is transient state, not a property of the
change. The mojeek claim to check is a CONFIGURATION one — `mojeek: disabled: true` in the
shipped `settings.yml` with the robots.txt/ToS reason in its comment, and no mojeek hit in any
live result set.

Check the SHIPPED config against the measurement:

```powershell
git show work/research-trust:search-gateway/searxng/settings.yml
git show work/research-trust:search/docker-compose.yml | Select-String -Pattern "SEARXNG_IMAGE" -Context 3,1
git show work/research-trust:.env.example | Select-String -Pattern "SEARXNG_IMAGE" -Context 4,0
```

**PASS:** `google`, `brave`, `qwant`, `yandex`, `duckduckgo web` enabled; `bing`,
`duckduckgo` (old), `mojeek`, `google cse` disabled, each with its measured reason in the
comment; `startpage`/`presearch` entries removed; the image pinned to
`searxng/searxng:2026.9.11-61d660276` in both the compose default and `.env.example`.

**The bing decision must be readable as DATA:** the file says bing scored 0.00 on 12/12 and
that leaving it on drops the merged median from 0.70 to 0.58. If you want to re-measure,
Section D's throwaway-rig recipe is the same one the developer used — but note this is
optional for a pass, because the shipped claim is a measurement recorded with its method.

**FAIL:** `settings.yml` enabling an engine the note measured at 0.00; the image left
floating at `:latest` (the whole engine policy silently does nothing on the old image —
§5.1 of the findings); a reason in a comment that the notes do not support.

---

## T9 - The integration test, against a throwaway database (Phase 5.3)

This proves the whole path — reuse recall → gather → gate → synthesize → curator delegate —
still works with every change in this branch, using the real schema.

```bash
# 1. Copy the 30 init scripts the compose file mounts, in order, into one dir.
#    (They are ./init*.sql under OB1/docker; the names are the numbered targets in
#     OB1/docker/docker-compose.yml under openbrain-db.)
# 2. Throwaway DB + network, both labelled:
docker network create --label ai-stack.harness.owner=research-trust wt-rt-test-net
docker run -d --name ob-claims-test --label ai-stack.harness.owner=research-trust \
  --network wt-rt-test-net -e POSTGRES_DB=openbrain -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=test -v "<initdb dir>:/docker-entrypoint-initdb.d:ro" \
  pgvector/pgvector:pg16
# 3. Run it. REUSE_MAX_DISTANCE=1.1 is REQUIRED - see below.
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=research-trust \
  --network wt-rt-test-net -e DB_HOST=ob-claims-test -e DB_PASSWORD=test \
  -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check orchestrator.test.ts
```

**PASS:** `ALL ORCHESTRATOR ASSERTIONS PASSED`, and the DB reports 56 public tables with
`find_or_create_claim`, `link_claim_to_source`, `retract_claim`, `find_or_create_source`
present.

**Why `REUSE_MAX_DISTANCE=1.1`, and why that is not this branch's bug:** the test's
`fakeEmbed` hashes a string into one hot dimension, so `"cats are mammals"` and
`"Tell me about cats"` are near-orthogonal and the default 0.55 recall bar drops the seeded
claim before the run ever sees it. **Verify it is pre-existing** by running the identical
command against the BASE commit (read-only mount of the main checkout, which sits at OB1
`9a772aa`):

```bash
-v "D:/Open WebUI/ai-stack/OB1/integrations/research-service:/app:ro"
```
It fails the same way without the override and passes the same way with it.

**Also build the images** (the `COPY *.ts` in both Dockerfiles is the guard against a
repeat of the crash-loop-on-a-missing-module failure; three new modules ship in this branch):

```bash
docker build --label ai-stack.harness.owner=research-trust -t openbrain-research:wt-research-trust \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
docker build --label ai-stack.harness.owner=research-trust -t openbrain-curator:wt-research-trust \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-curator"
docker run --rm openbrain-research:wt-research-trust sh -c "ls /app/*.ts"
```

**PASS:** both builds succeed (their `RUN deno check index.ts` is the module-drift gate), and
the research image contains `grounding.ts`, `report.ts` and `search-quality.ts` and **no**
`*.test.ts`.

**FAIL:** a build failure; a missing new module in the image; the base commit PASSING without
the override (which would mean the failure IS this branch's).

**Clean up:** `docker rm -f ob-claims-test; docker network rm wt-rt-test-net` and
`.\scripts\agent-harness\reap.ps1 -Report` should list nothing owned by `research-trust`.

---

## T10 - Nothing live was changed, and nothing true was silently removed

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat refactor/ai-stack-cleanup..work/research-trust
git -C OB1 diff --stat 9a772aa..research-trust
git status --short                                   # the main checkout
docker exec openbrain-db psql -U postgres -d openbrain -Atc "select count(*) from claims where status='retracted' and metadata->>'retraction_reason' like '%research-audit 2026-09-11%'"
```

**PASS:** the parent diff touches only `documentation/`, `search-gateway/`,
`search/docker-compose.yml`, `.env.example`, `scripts/stack/stack.ps1`,
`owui/tools/deep_research.py`, `OB1/docker/docker-compose.yml`; the **OB1 gitlink is NOT
staged or committed in the parent** (`git diff refactor/ai-stack-cleanup..work/research-trust -- OB1`
is empty — the reviewer lands it, §E); the retracted-claim count is **0** (the retraction is
a post-deploy step and was deliberately not run).

Then diff removals specifically:

```powershell
git -C OB1 diff 9a772aa..research-trust | Select-String -Pattern "^\-" | Select-String -NotMatch "^\-\-\-"
```

**PASS:** every removed line is accounted for by a replacement in the same hunk. The
substantive removals are: the `coverage NN%` footer in `lib.ts` and in
`owui/tools/deep_research.py` (replaced by `needs answered X of N`); the old single-pass
gather loop in `harness.ts` (replaced by the yield-target loop); the `bing`/`mojeek`
enablement in `settings.yml` (replaced with measured `disabled: true` plus reasons); the
`startpage`/`presearch` entries (engines removed upstream — verified on the pinned image).
`SKEPTIC_ENABLED` is UNCHANGED at `0`.

**FAIL:** a live container restarted/rebuilt; any claim retracted; a removed behaviour with
no replacement and no note; the gitlink bumped in the parent.

---

---

## T11 - The detector judges whether the results are about the SUBJECT

This case failed attempt 2, and its FAIL clause below is the one that caught it, unchanged.
Read the history before running it, because the point is what kind of rule is acceptable here.

- **Attempt 1** required two distinct query terms per hit, so five perfect CrashLoopBackOff
  results scored 0.00 and were condemned as `collapsed`.
- **Attempt 2** added `ANCHOR_MIN_LEN = 7`: a hit carrying the query's longest term counted,
  if that term was at least 7 characters. The tester showed that separates SHORT collapse
  tokens from LONG ones, not collapse tokens from subject entities — `capacitor`,
  `motherboard`, `vestibular` and `semaglutide` are the head nouns of exactly the questions
  this engine is for. Two of four live probes were classified **`ok` at overlap 0.90 and
  0.70** on ten pages that never mention the OptiPlex.
- **Attempt 3** stops picking constants. The classifier now takes the **subject entity** —
  the thing `KEYWORDIZE` already extracts and `keywordQuery` already forces into every query —
  and asks whether the results contain it. That is a structural fact of the run, not a number
  fitted to an incident.

### The rule

`classifyHits(query, hits, entity)`. When an entity is supplied, the set is `ok` only if the
entity PHRASE (tokens adjacent, case-insensitive, tolerant of `-`/spacing) appears in
title+snippet of at least `ENTITY_SHARE` of hits. Otherwise it is `collapsed` (one query token
dominates the titles) or `offtopic` (none does). Overlap is still computed and reported, and
is **secondary evidence, not the gate**. With no entity — article-mode preliminary gaps and
legacy callers — it falls back to the old overlap rule and says so in the code.

```powershell
git -C OB1 show research-trust:integrations/research-service/search-quality.ts | Select-String -Pattern "ENTITY_SHARE" -Context 2,22
git -C OB1 show research-trust:integrations/research-service/harness.ts | Select-String -Pattern "classifyHits" -Context 6,1
```

**PASS:** `ENTITY_SHARE = 0.5` with the per-fixture measurement in its comment (good sets
0.75 / 1.00 / 1.00; failed sets 0.00 across six), and the harness passes `subjectEntity` at
its `searchWeb` call site.

### Run it

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno test -A search-quality.test.ts
deno test -A harness-trust.test.ts --filter "T11"
```

**PASS:** `19 passed | 0 failed` and `1 passed`. Specifically:

- `T11: the tester's live probes are search failures, not 'ok'` — the three probes that were
  classified `ok` (`capacitor bulging OptiPlex 3050 repair`,
  `motherboard VRM failure OptiPlex 3050`, `vestibular suppression 100 Hz auditory tone`) are
  now not-`ok` with `entityShare === 0`. The fixtures are the live payloads, re-captured
  2026-09-11 with provenance headers naming this as the tester's T11 failure.
- `T11: entity PRESENT in every hit is never collapsed, whatever the token lengths` — the
  tester's own `Kubernetes pod evicted OOMKilled` and `iPhone 18 Pro camera sensor` sets pass,
  plus a property over five entities including 2-character ones.
- `T11: the three ORIGINAL collapse fixtures still collapse under the entity rule`, naming
  `dell`, `most`, `100`.
- `T11: the run's subject entity reaches the classifier` (harness) — the capacitor payload
  through a real `runResearch`: `search.ok === 0`, nothing fetched, nothing reaching the
  relevance gate, `search_degraded`, `no_relevant_sources`.

### Re-run the live probes yourself

```bash
for q in "capacitor bulging OptiPlex 3050 repair" "motherboard VRM failure OptiPlex 3050"          "vestibular suppression 100 Hz auditory tone" "semaglutide gastroparesis incidence"; do
  curl -s -G "http://127.0.0.1:8085/search" --data-urlencode "q=$q" --data-urlencode "format=json"
done
```

then classify each against its entity (`OptiPlex 3050`, `OptiPlex 3050`, `100 Hz`,
`semaglutide`). **PASS:** the first three are not `ok`.

**One deliberate exception, and you should push on it.** `semaglutide gastroparesis incidence`
returns ten real semaglutide pages that never mention gastroparesis, so `entityShare` is 1.00
and the verdict is **`ok`** — pinned by
`T11: an entity-present set whose hits miss the NEED is a weak search, not a broken engine`.
The argument: the engine understood the subject; what it missed is the NEED, and the relevance
gate is what rejects a page that does not answer a need. Calling that "the search failed"
would be attempt 1's error pointed the other way. If you disagree, say so — it is a judgement,
and it is written down here so it can be disagreed with.

**Try to break it.** The safety argument is no longer a constant, so attack the entity instead:
an entity the KEYWORDIZE pass would plausibly emit, where the entity is PRESENT in junk (the
semaglutide shape, deliberately allowed) or ABSENT from good results (a page that answers the
question without naming the subject — a manual titled only "Owner's Manual"). If you can make a
genuinely collapsed set classify `ok` **for a reason other than the semaglutide exception
above**, that is a FAIL and worth more than the rest of this case.

**FAIL:** any recorded collapse fixture or live probe classifying `ok` with the entity
supplied; either good set classifying `collapsed`/`offtopic`; the harness not passing the
entity; `ENTITY_SHARE` documented without the measurement behind it.

---

## T12 - B2: a page of pure noise is not a successful search

Ten hits with zero query-term overlap were classified `ok` and counted in `SearchStats.ok`, so
they spent the fetch and relevance-gate budget while reporting the search as healthy. An
engine that drifts semantically rather than collapsing onto a token was invisible.

```bash
deno test -A search-quality.test.ts --filter "B2"
deno test -A harness-trust.test.ts --filter "B2"
```

**PASS:** four cases. `B2: zero overlap across a full page of hits is never 'ok'` returns
verdict `offtopic` for the tester's own "Best Buy Deals" set; `B2: a THIN result set is not
condemned` keeps a 3-hit set `ok` (below `OFFTOPIC_MIN_HITS` = 5, because three hits is not a
verdict about an engine); `B2: a weak-but-not-empty set stays 'ok'` keeps a set where 3 of 10
hits do mention the subject; and the harness case shows an off-topic set yields NO pages,
increments `fetchStats.search.offtopic`, leaves `search.ok` at 0, feeds the degraded streak
and ends the run `search_degraded` / `no_relevant_sources`.

**FAIL:** `offtopic` sets still counted in `search.ok`; an off-topic set being fetched; a
3-hit set condemned; `searchHealthLabel` or the footer ignoring `offtopic` (it is folded in as
"junk" - `report.ts`).

---

## T13 - B3: the empty-pool guarantee holds on EVERY path

Anchor criterion 1 is unconditional; the implementation gated it on `topicPath`, so on the
article / sources-only / `disable_web_search` paths an empty pool still reached the
synthesizer ("write from nothing") and, with one recalled claim, still reached the curator
with `sources: []` and "The provided sources contain no information..." as its headline claim.

```bash
deno test -A harness-trust.test.ts --filter "B3"
```

**PASS:** five cases. Four modes - article with empty seed content, sources-only with empty
seed content, `disableWebSearch` with no seeds, article with no seeds - each assert
`outcome === "no_relevant_sources"`, **zero** synthesizer calls, **zero** curator calls, zero
cited sources, a backstop other than `complete`, and the failure notice in the prose. The
fifth is the tester's worst case: an empty pool WITH a recalled reuse claim, asserting the
claim really was recalled (`reuseClaims.length === 1`) and the curator still refused.

Then confirm the NON-empty paths are untouched:

```bash
deno test -A harness-trust.test.ts --filter "still"
```
plus the T9 integration run.

**PASS:** `sources-only mode still grounds strictly from the caller's seeds` and `article mode
still stages the seed article and is never gated away` both pass, and the T9 run still prints
`ALL ORCHESTRATOR ASSERTIONS PASSED` - that run exercises the reuse path, which this change
restructured (the reuse-source lookup moved ABOVE the empty-pool decision, because the
decision has to know whether a reused claim brings a citable source with it).

**FAIL:** any mode returning `outcome: "complete"` with zero cited sources; any synthesizer
call on an empty pool; any curator call with `sources: []`; a digest/article run with a REAL
pool behaving differently from the base commit.

---

## T14 - B5: the grounding check checks grounding, not digit presence

`[Source N]` was stripped from the LINE but never from the SOURCE text, so a fabricated
"95 C" was grounded by any stray `[95]`, `page 95`, `Source 95` or `95 mm` - and the word
table grounded `100` from "several hundred", `1` from "no one" and `2` from "two engineers",
which are among the commonest words in English.

```bash
deno test -A grounding.test.ts
```

**PASS:** `10 passed | 0 failed`, including the three B5 cases:
`a citation index in the SOURCE text does not ground a figure` (five pointer shapes),
`a figure with a UNIT must match that unit in the source` (95 mm does NOT ground 95 C;
"95 C", "95C" and "95 C." all do), and `the commonest words in English no longer ground small
integers` (0, 1, 2, hundred and thousand removed from the word table; **"thirty" still grounds
30**, which is the 100 Hz CAREN line the word table exists for).

**The regression that matters** is in the same file: `applyNumericGrounding leaves the 100 Hz
[SOURCED] lines alone` must still pass. A stricter check is easy to over-tighten - the unit
rule initially downgraded the verified GVS line, because a `\b` after `%` needs a word
character next to it and "26% motion sickness" has a space.

**FAIL:** any of the five pointer shapes grounding a figure; a unit mismatch grounding a
figure; "thirty" no longer grounding 30; any of the ten 100 Hz `[SOURCED]` lines downgraded.

---

## T15 - B7: the recall-gating test now exercises recall

`harness-trust.test.ts` had a case named "recall pages are gated too" that ran against a stub
returning zero rows, so the audited failure's actual mechanism - months-old pages about a
different computer being EXEMPT from the relevance gate and becoming the whole cited pool -
had no test anywhere.

```bash
deno test -A harness-trust.test.ts --filter "recall"
```

**PASS:** two cases. `REPLAY ce398d06: KB-recall pages face the gate and none survives` runs
against a client that serves the audited run's own three recalls (the DGX Spark maintenance
guide, the Compaq d220 manual, the ASUS BIOS FAQ), asserts the gate was actually ASKED about
each of them by name, and asserts zero cited sources, zero curator calls,
`outcome: "no_relevant_sources"` and `fetchStats.reused === 3`. `a recall page that IS on
topic survives the gate and is citable` proves the gate is about relevance, not provenance.

This case also found a second defect - check that you believe the fix. The **fail-safe floor
was re-admitting the recalls**: they are gated before round 1, when the collapse counters are
still zero, so the floor's "the gate would empty the pool" branch fired and put the DGX Spark
pages straight back into the pool. The floor now never applies to recalls at all
(`gateAndKeep(..., floorMayApply=false)`), because a recall's only credential is vector
proximity and re-admitting it on an empty pool IS the audited failure.

```powershell
git -C OB1 show research-trust:integrations/research-service/harness.ts | Select-String -Pattern "floorMayApply" -Context 2,4
```

**FAIL:** an off-topic recall reaching `citedSources`; the gate not being asked about the
recalls (check `relevanceAsked`); an on-topic recall being dropped.

---

## T16 - The OWUI tool's fallback renderer, executed rather than read

`owui/tools/deep_research.py` is a paste-deploy surface (D.5) carrying hand-written parity
logic against `lib.ts`/`report.ts`. T5 only reads its diff. Execute it - save this as `t16.py`
in the worktree root and run `python t16.py`:

```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dr", "owui/tools/deep_research.py")
m = importlib.util.module_from_spec(spec); sys.modules["dr"] = m; spec.loader.exec_module(m)

new = m._render({
    "synthesis": "Answer [1].",
    "cited_sources": [{"url": "https://a.example", "title": "A"}],
    "gaps": [], "backstop": "complete", "reuse_ratio": 0.22,
    "needs_status": [{"need": "a", "status": "answered"}, {"need": "b", "status": "open"}],
    "search_record": {"hits": 40, "fetched": 30, "readable": 28, "relevant": 9,
                      "collapsed": 0, "offtopic": 0, "ok": 6, "empty": 0},
})
print("NEW     :", "needs answered 1 of 2" in new,
      "| sources line:", "sources 9 relevant of 30 fetched" in new,
      "| no coverage:", "coverage " not in new,
      "| not degraded:", "DEGRADED" not in new)

legacy = m._render({"synthesis": "Answer [1].", "cited_sources": [], "gaps": [],
                    "backstop": "complete", "reuse_ratio": 0.22})
print("LEGACY  :", "coverage" not in legacy and "needs answered" not in legacy)

dup = m._render({"prose": "# R
Body.

_- needs answered 1 of 2_",
                 "needs_status": [{"need": "a", "status": "answered"}], "backstop": "complete"})
print("NO DUP  :", dup.count("needs answered") == 1)

off = m._render({
    "synthesis": "", "cited_sources": [], "gaps": [], "backstop": "search_degraded",
    "needs_status": [{"need": "a", "status": "search_failed"}],
    "search_record": {"hits": 30, "fetched": 0, "readable": 0, "relevant": 0,
                      "collapsed": 1, "offtopic": 2, "ok": 0, "empty": 0},
})
print("OFFTOPIC:", "3 junk" in off,
      "| DEGRADED:", "search: DEGRADED (3 of 3 searches returned junk)" in off,
      "| no stale wording:", "collapsed" not in off)
```

**PASS:** every printed boolean is `True`. `_render` prefers `result["rendered"]` when present,
so this exercises the FALLBACK path - what OWUI shows for any job written before this change or
whose `rendered` field is missing.

**The OFFTOPIC line is X4**, which the tester found in attempt 2: `report.ts` folded `offtopic`
into the junk count and the DEGRADED label and this file did not, so the two renderers
disagreed in wording ("collapsed" vs "junk") AND content (no DEGRADED line at all). Check they
now agree exactly, by running the TypeScript side on the same record:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust/OB1/integrations/research-service"
deno eval --ext=ts 'import { coverageFooter } from "./report.ts"; console.log(coverageFooter([{need:"a",status:"search_failed"}],{queries:[],hits:30,fetched:0,readable:0,relevant:0,collapsed:1,offtopic:2,ok:0,empty:0,errors:0},"search_degraded"))'
```

**PASS:** it prints
`needs answered 0 of 1 · sources 0 relevant of 0 fetched (30 hits, 3 junk) · search: DEGRADED (3 of 3 searches returned junk) · stopped early: search_degraded`
and the Python `off` footer line is the SAME STRING.

**FAIL:** `coverage NN%` anywhere; a legacy row printing a coverage number; the footer printed
twice; the two renderers producing different strings for the same record; an exception. (The
module imports `aiohttp` and `pydantic` at top level; if they are absent in your environment
that is a plan inadequacy, not a pass - say so, and `pip install` into a throwaway venv rather
than the operator's python.)

# D. Deploy (AFTER this plan passes — operator or reviewer, under leases)

These steps change live systems and are **not part of the test pass**.

### D.1 Search plane — lease `search`

1. `.\scripts\agent-harness\lease.ps1 -Acquire -Name search`
2. Set the pin in the **live `.env` line 169**: `SEARXNG_IMAGE=searxng/searxng:2026.9.11-61d660276`
   (the compose default now matches, but `.env` overrides it, so the file must change).
2a. **Verify the digest before bringing it up** (tester's gap, attempt 1). A tag is mutable
   and this is a privacy-critical container on a kill-switched network: if the tag were ever
   re-pushed, `up -d` would silently get different bytes than the ones measured on
   2026-09-11. Compare before and after a pull:

   ```bash
   docker image inspect searxng/searxng:2026.9.11-61d660276 --format '{{index .RepoDigests 0}}'
   docker pull searxng/searxng:2026.9.11-61d660276
   docker image inspect searxng/searxng:2026.9.11-61d660276 --format '{{index .RepoDigests 0}}'
   ```

   If the digest moved, STOP: the measurement in the findings note no longer describes what
   you are about to run. Setting `SEARXNG_IMAGE` to the `searxng/searxng@sha256:...` form
   instead of the tag removes the question permanently and is the better choice if the
   operator wants it.
3. Per `search-gateway/README.md`'s own rule for an image bump: diff the new image's
   `settings.template.yml` against ours and re-verify `routes/searxng_compat.py` still
   matches the new payload shape. This is a **4-month** jump on privacy infrastructure and
   is the only real risk in this deploy.
4. `docker compose -f search/docker-compose.yml --env-file .env up -d searxng gateway`
5. Verify:
   `curl -s -G "http://127.0.0.1:8085/search" --data-urlencode "q=Dell OptiPlex 3050 capacitor failure" --data-urlencode "format=json"`
   → on-topic Dell support/manual pages from more than one engine, not `Dell USA`.
   Then `curl -s http://127.0.0.1:8085/health` → `"search":"ok"` with ≥ 2 engines in
   `engines_answering_recent` (it reads `unknown` until the first search is served).
6. `.\scripts\stack\stack.ps1 health` → the new line reads `search: ok - N engine(s) answering`.
7. Release the lease.

**Rollback:** restore `SEARXNG_IMAGE=searxng/searxng:latest` in `.env` and
`git checkout` the previous `search-gateway/searxng/settings.yml`, then `up -d searxng`.

### D.2 Open Brain plane — lease `open-brain`

1. `.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain`
2. `docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env build openbrain-research openbrain-curator`
   then `... up -d openbrain-research openbrain-curator`.
   **OB1 reads `OB1/docker/.env`, not the main `.env`** — the new `RESEARCH_*` overrides go
   there if you want anything other than the compose defaults.
3. `.\scripts\stack\stack.ps1 health` → curator `:8816/health` 200, research service up.

**Rollback — capture this BEFORE step 2** (tester's gap, attempt 1: D.1 had a rollback and
this did not). The previous `:local` images are the only fallback and the rebuild retags
them, so tag them first:

```bash
docker tag openbrain-research:local openbrain-research:pre-research-trust
docker tag openbrain-curator:local  openbrain-curator:pre-research-trust
```

To roll back: retag `:pre-research-trust` back to `:local` for both, then
`docker compose -f OB1/docker/docker-compose.yml up -d openbrain-research openbrain-curator`.
No schema change ships in this item, so a rollback needs no database step — and if D.4 has
already run, note that a retraction is NOT undone by rolling back the image.

### D.3 The live dry runs (anchor criterion 6 — POST-DEPLOY, not in this plan)

```bash
curl -s -X POST http://127.0.0.1:8818/research -H "x-brain-key: $MCP_ACCESS_KEY" \
  -H "content-type: application/json" -d '{"query":"Dell OptiPlex 3050 used purchase: common failure modes, known defects, red flags, thermal issues, capacitor/CPU socket problems, how to verify hardware health","origin":"owui","options":{"dry_run":true}}'
```
Poll `GET /research/jobs/<id>`. **Either** ≥ 5 relevant OptiPlex sources cited, **or**
`result.outcome = "no_relevant_sources"` with `result.search_record.collapsed > 0` and
`result.curator.state = "skipped"`. Anything else fails.

Second dry run, the 100 Hz query: this is a **retrieval** assertion only — with the plane
healthy the cited set should include a URL matching `pubmed.ncbi.nlm.nih.gov/40128952` or
`nagoya-u.ac.jp`; with the plane degraded, `search_degraded` and no absence title. Do not
treat it as a fact assertion.

Regression: the next scheduled notebook-origin jobs must still stage fresh sources —
`select count(*) from sources s join research_jobs j on true where s.created_at >= j.started_at`
scoped to the new job.

### D.4 Retract the 8 poison claims — ONLY after D.2 is green

Run **after** the new curator is live, or the next run re-creates them. One statement per id:

```sql
SELECT public.retract_claim('0c2b7c4e-2755-42e1-9a10-f854a9d1e34b', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('7f2ac93b-6680-40b9-84a6-6ce49e52fac1', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('ed40c734-bf3f-444d-99c1-cd11091f6da4', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('1306bb5a-560c-4d79-a96a-960c35038495', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('f4c0bd7a-9707-4dd2-aecd-d6ea446b338f', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('eea04dc2-d88c-47bc-b23d-65867813324e', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('cd1d66fb-b4f5-4496-8090-33568126be7d', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
SELECT public.retract_claim('32061b2c-a641-4ed3-a4d4-869a6c3d6ab2', 'research-audit 2026-09-11: meta-claim about a run, not a fact');
```

Verify:

```sql
SELECT id, status FROM claims WHERE id IN (
 '0c2b7c4e-2755-42e1-9a10-f854a9d1e34b','7f2ac93b-6680-40b9-84a6-6ce49e52fac1',
 'ed40c734-bf3f-444d-99c1-cd11091f6da4','1306bb5a-560c-4d79-a96a-960c35038495',
 'f4c0bd7a-9707-4dd2-aecd-d6ea446b338f','eea04dc2-d88c-47bc-b23d-65867813324e',
 'cd1d66fb-b4f5-4496-8090-33568126be7d','32061b2c-a641-4ed3-a4d4-869a6c3d6ab2');
```
→ all 8 `retracted`. Then a `dry_run` job for the OptiPlex query must return
`reuse_claims = []`.

**Never DELETE a claim.** `retract_claim` only.

**The wider sweep is an OPERATOR DECISION, not part of this.** **25** of 7 744 active claims
match the shipped patterns as of attempt 3 (findings §3b.2; it was 43 when the patterns were
looser, and the tester independently judged all 25 to be evidence-side). Only these 8 are
retracted here.

### D.5 Re-paste the OWUI tool

`owui/tools/deep_research.py` is now **v1.3.0** and its fallback renderer changed. Open WebUI
loads tools from its own database, not from this repo, so the file must be **pasted into the
OWUI tool editor by the operator** and `owui/manifest.csv` checked. Until then, OWUI's
fallback path still prints the old footer for any job whose `rendered` field is absent.

---

# E. Landing

1. **OB1 first.** The OB1 changes are committed on branch `research-trust` inside the
   developer's worktree submodule and are **NOT pushed**. Push that branch to OB1's remote
   (`devonpveller/OB1.git`) BEFORE bumping the gitlink — a pinned SHA that is not reachable
   on the remote breaks every fresh `--recurse-submodules` clone.
2. **Then the gitlink.** In the parent, `git add OB1` and commit the pointer with a message
   saying what moved. The developer deliberately did NOT stage it (T10 checks this).
3. **The merge.** The work line `refactor/ai-stack-cleanup` is the branch **checked out in
   the operator's main checkout**. Per CLAUDE.md, the reviewer does not touch the operator's
   working copy: rebase and hand the `--no-ff` merge back to the operator, with the test
   evidence in the message.
4. **Container rule:** no service added, removed or moved — only a health-probe line in
   `scripts/stack/stack.ps1` and a new unauthenticated `/health` route on an existing
   service. `/stack-map` should show no drift.
5. **Memory:** the `research-engine-plan` note should record what shipped; the audit note
   stays as the record.
