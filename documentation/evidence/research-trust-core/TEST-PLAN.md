# TEST-PLAN — harness item `research-trust-core`

**Branch:** `work/research-trust-core` (parent, base `8d480a5`) + `research-trust-core`
(OB1 submodule, base `5c189cf`, NOT pushed).
**Developer:** `wt-research-trust`. **Anchor:**
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust-core`, and a copy is committed at
`documentation/implementation-guide/research-engine-for-OB/anchor-research-trust-core.json`.

Third item in a row on the same detector, and the third failure of the same shape: a rule that
was right for the case it was built from and wrong for the next one. Read
`documentation/notes/research-trust-findings.md` → "Deploy round 2" and section **F** first.
The failing run's full result is at
`documentation/evidence/research-trust-core/dryrun-6975d982.json`.

---

## Preconditions - where to run, and what this branch does NOT touch

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust-core`
and it holds the branch. Cases T1–T9 are **read-only executions** (`deno test`, `python`,
`git show`, `curl`) — running them there changes no git state. Do not `git add`, `git commit`
or edit anything.

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat 8d480a5..work/research-trust-core
git -C OB1 diff --stat 5c189cf..research-trust-core
```

**Nothing live was changed.** Live access was read-only: `GET :8085/search` captures. The only
things this branch can affect once deployed are the `openbrain-research` image and the OWUI
tool paste — section D. **Leases:** T1–T9 need none; section D needs **open-brain**.

---

## T1 - Unit suites and lint

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core" && ruff check .
```

**PASS:** research-service **`203 passed | 1 failed`** (the anchor requires at least 173);
research-curator `36 passed | 0 failed`; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — it opens a postgres pool at module load and needs
the throwaway DB of T8. It fails the same way on the base commit.

**FAIL:** any other failing test; research-service below 203; curator below 36; any ruff error.

**Test accounting, because two files were DELETED.** `entity.test.ts` (22) and
`entity-core.test.ts` (14) tested `entityCore()`, which no longer exists. `subject.test.ts`
(48) re-expresses every behavioural assertion they made against the set rule — brand omission,
neighbouring model, the M.2 guard, the spelling variants, the six collapse fixtures, every good
set — and adds the tester's five live sets and the five T7 candidates. Check that claim rather
than taking it: `git -C OB1 show research-trust-core~1:integrations/research-service/entity.test.ts`
lists what was there.

---

## T2 - ACCEPTANCE 1: the subject that failed in production now passes

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A subject.test.ts --filter "failing dry run"
deno test -A subject.test.ts --filter "REGRESSION ok: live-100hz"
```

**PASS:** the five-word subject `100Hz audio VR motion sickness` yields the set
`{100hz, vr}`, its own recorded hit set satisfies it at **0.85** (the anchor asks for ≥ 0.4),
`classifyHits` returns `ok`, and the Nagoya paper is asserted present in the set — the point
being that the deployed run discarded the pages it was sent to find.

Check it against the recorded failure rather than taking the test's word:

```bash
python -c "
import json
d=json.load(open('../../../documentation/evidence/research-trust-core/dryrun-6975d982.json',encoding='utf-8'))
r=d.get('result',d); sr=r.get('search_record') or {}
print(r.get('backstop'), r.get('outcome'))
[print(' ', q['verdict'], q.get('entityShare'), '|', q['query'][:62]) for q in sr.get('queries',[])]"
```

**PASS:** the recorded run shows `search_degraded` with three `collapsed` verdicts at share
`0` — the state this case exists to invert.

**FAIL:** a share below 0.4; any of that run's on-need queries still non-`ok`; the recorded
evidence not showing the failure it is supposed to show.

## T3 - ACCEPTANCE 2: the rule, and it is a SET rule now

Attempt 1 failed here, and the tester's verdict is the reason this case is rewritten rather
than patched: **four rules in four items, each picking a surface property to stand in for
identity** — eight pattern strings, a seven-character length, the token before the first
digit, the longest token. Each passed every subject somebody had written down.

The single-core-phrase idea is gone, and with it the length tiebreak and the `QUALIFIERS`
list. The rule, in one sentence:

> A subject is its SET of distinctive tokens — those bearing digits (a bare four-digit year
> excepted), those the planner capitalised, and those that are not common English — and a hit
> carries the subject when it contains at least half of them, rounded up.

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "THE RULE, and it is not a phrase" -Context 4,30
```

**PASS:** the docblock states it in one sentence, names the four rules it replaces, and
carries the tester's five counter-examples. `entityCore`, `corePhrase`, `hitCarriesEntity`,
`QUALIFIERS` and every length test are **absent from the file**:

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "entityCore|corePhrase|QUALIFIERS|ANCHOR_MIN_LEN|hitCarriesEntity"
```

**PASS:** no hits.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A subject.test.ts
```

**PASS:** `48 passed | 0 failed`, including `subjectTokens: digits, capitals, and anything
uncommon`, which pins the distinctive set for all eleven subjects the coordinator named, and
`tokenSet offers a run, its parts, and the glued neighbours`, which pins that `Z6III` and
`Z 6III` match each other and `100Hz` matches `100 Hz`.

**Two guards to check by hand**, because each was found by a fixture going the wrong way:

- `a BARE NUMBER never carries a subject on its own`. `100 Hz` is two tokens and half of two
  is one; every hit in the recorded `the100` fixture carries `100` (*The 100*, the TV
  series). Without this guard the founding fixture scored **1.00** and passed.
- `Postgres 17.2.1` glues to one token. A single global replace leaves `172` + `1`, because
  the two dot-matches overlap on the digit between them.

**FAIL:** any of the eleven subjects yielding a different set; either guard missing; any
length or phrase test still in the file.

## T4 - ACCEPTANCE 3: the subject is bounded at extraction, and nothing else claims to be

Attempt 2 failed this case twice over: it ran `entity-core.test.ts`, which that attempt had
deleted, and it asserted `shortenEntity` kept the caller's spelling when the shipped function
returned the lower-cased token set. Both are fixed by deciding rather than patching.

**`shortenEntity` and `entity_shortened` are DELETED.** They reported a subject shortened to
its core phrase; there is no core phrase and no shortening, because the subject is used whole
as a set. A footer clause that can never fire again is worse than no clause.

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "shortenEntity|entity_shortened"
git -C OB1 show research-trust-core:integrations/research-service/harness.ts | Select-String -Pattern "shortenEntity|entity_shortened" -Context 0,2
```

**PASS:** no hits in `search-quality.ts`; in `harness.ts` only the comment saying they were
removed and why. The shortening clause is gone from BOTH renderers — check that in T8 rather
than taking it. (`deep_research.py` is nevertheless at **v1.4.1**, not the deployed 1.4.0: a
DIFFERENT clause, the refused-search counter, arrived with T7b. The shortening clause being
gone and the file needing a paste are both true; attempt 3's "no re-paste" note is superseded
by D.3.)

What DOES still ship, and is what this case now tests:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A subject.test.ts --filter "entityStatusFor"
deno test -A subject.test.ts --filter "names nothing"
```

**PASS:** three cases across two filters. A subject the query is not about is `rejected`; an empty subject is
`missing`; a query carrying half the subject is `used`; and a rejected subject is not scored.
Both non-`used` cases fall back to the overlap rule and are counted in `fetchStats.search`
(`entity_missing` / `entity_rejected`) and shown in the footer — verified in T8.

The prompt half still ships too:

```powershell
git -C OB1 show research-trust-core:integrations/research-service/harness.ts | Select-String -Pattern "ENTITY RULES" -Context 3,8
```

**PASS:** `KEYWORDIZE_SYS` states the subject is a NAME of at most 3 words, says what it is
NOT, and uses the failing dry run as its own example.

**FAIL:** this case naming a file that does not exist; any assertion about a function that
does not ship; a fallback that is not counted; the OWUI version bumped when its output did not
change, or unchanged when it did.

## T5 - ACCEPTANCE 4: nothing that used to work stopped working

```bash
deno test -A subject.test.ts --filter "REGRESSION"
deno test -A search-quality.test.ts
```

**PASS:** twenty per-fixture regression cases (six `REGRESSION collapse:` and fourteen
`REGRESSION ok:`, one per set so a failure names the set rather than a loop index) plus
`search-quality.test.ts` green. Every collapse fixture scores exactly **0.00**; every good set
scores **≥ 0.5**.

**One assertion was CHANGED, and it is the cost of the rule** — check you accept it rather
than passing over it:

```powershell
git -C OB1 diff research-trust-core~1..research-trust-core -- integrations/research-service/search-quality.test.ts | Select-String -Pattern "^[-+]" | Select-String -NotMatch "^[-+][-+]"
```

**PASS:** the `T11` spelling case keeps its four spelling variants and replaces its adjacency
assertion — `OptiPlex 7080 and the 3050-era chipset` used to be refused and is now accepted —
with the reason in the test, plus a new assertion that junk carrying ONE token is still
refused. Adjacency is what a set rule gives up; four items of false search failures are what
requiring it cost.

**FAIL:** any collapse set scoring above 0.00; any good set below 0.5; a changed assertion
without its reason.

## T6 - ACCEPTANCE 5: the share table is recomputed and the threshold still holds

Findings section **G.3**, and `ENTITY_SHARE` in `search-quality.ts`.

**PASS:** `ENTITY_SHARE` is still `0.175`, and the recomputed table (findings **H.3**) covers
all 28 recorded sets including the tester's three junk sets and their three good counterparts:
GOOD runs 1.00 down to **0.35** (`live-semaglutide50`, the lowest); the six collapse fixtures,
`probe-collapsed-semaglutide` and all three junk sets are **0.00**.

Recompute it yourself with `subjectTokens` + `entityShare` over every fixture in `fixtures/`.

**PASS:** your numbers match, and **no set lands within 0.1 of 0.175** — the nearest are 0.00
and 0.55. The gap is wider than under any previous rule (it was 0.05 → 0.30), which is the
check that matters here: a threshold whose margin grows when the rule improves is a threshold
that was measuring the right thing.

Note `live-100hz-ssq` moved 0.05 → 0.55 and is now `ok`. Its hits are VR sickness papers and
the subject contains `vr`, so they genuinely carry half of it. If you think that query should
still read as a search failure, say so — it is a judgement about what this detector is for.

**FAIL:** a set within 0.1 of 0.175 with the threshold unchanged; a table that does not
reproduce; a set omitted from it.

## T7 - The rule is a rule, and it now has an evidence FLOOR

Attempt 2 failed here. The set rule was sound and had no floor: `ceil(k/2)` is **1** when
k <= 2, and a one-or-two-token subject is exactly what the tightened KEYWORDIZE produces, so a
single matched token carried a hit. The tester's three live junk sets, all shipped as fixtures:

| subject | junk set | attempt-2 share | now |
|---|---|---|---|
| `Signal` | digital-signal-processing pages | 1.00 | **0.00** |
| `Arc browser` | arc-welding pages | 0.60 | **0.00** |
| `MacBook M2` | M.2 NVMe heatsink pages | 1.00 | **0.00** |

The third also brought back the **M.2 collision** every earlier rule guarded, because the glue
expansion turns `M.2` into the token `m2`.

The rule now reads:

> A subject is its SET of distinctive tokens — digit-bearing (a bare four-digit year excepted),
> capitalised by the planner, or not common English — and a hit carries it when it contains at
> least half of them, rounded up, **AND at least two distinct non-stopword terms of the query**
> (a distinctive subject token counts as one).

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A subject.test.ts
deno test -A subject.test.ts --filter "T7 candidate"
```

**PASS:** `48 passed | 0 failed` (40 of them attempt 3's, 8 added by T7b below), including
the three junk sets at 0.00, their three GOOD counterparts (`live-signal` 0.75, `live-arcbrowser` 0.90, `live-macbookm2` 1.00), and the five
candidates attempt 1 listed without pinning.

**The floor reverses two earlier judgements. Check you accept both:**

- `probe-collapsed-semaglutide` was declared `ok` by a previous item — the engine understood
  the subject, the relevance gate would filter per need — and is now refused. That judgement
  predates the floor; the tester then produced three live sets of exactly that shape where the
  shared token meant something else. One token cannot be told from the other.
- **B1 changes shape.** A hit whose only query word is the subject no longer carries it. B1
  stays fixed because a page really about CrashLoopBackOff says so in more than one word (its
  live set scores 0.95), but the synthetic control had to become realistic.

**Try to break it.** Three moving parts remain: the NLTK word list, the half threshold, and
the two-term floor. Look for a subject whose tokens are all stopwords; a good set whose pages
name the subject and nothing else of the query (`live-semaglutide50` is the closest recorded
case at 0.35 — find a worse one); a junk set that carries two query terms by coincidence. If
the floor pushes a REAL good set below 0.175, report the set and the numbers — do not assume
the floor should be weakened.

**FAIL:** any junk set above 0.00; any recorded good set below the line; a reversal without
its test and its reason.

### T7b - the QUERY side of the floor (attempt 3 failed here)

Attempt 3's floor had an unguarded door, and the run's own query builder walked through it. A
query with under two content words skipped the floor entirely (`if (qt.length < 2) return true`),
and `keywordQuery` produced exactly that whenever a need's every word is a stopword:

    keywordQuery("Signal", "What is it?")  ->  "Signal"   terms ["signal"]   FLOOR SKIPPED

Through the shipped `runResearch` against this branch's own `junk-signal-dsp`: share **1.00**,
verdict `ok`, **8 junk pages fetched**, backstop `complete` — versus collapsed twice and
`no_relevant_sources` with a normal three-term query. No model misbehaviour was involved.
`KEYWORDIZE_SYS` asks for 3–7 terms and nothing enforced it. **Both ends are fixed:**

1. **`shapeQuery` guarantees two content words** (`keywordQuery` delegates to it; `reformulate`
   shares it): the NEED's own first content word, or the class word `overview` when the need has
   none. A padded query is counted as `search.query_padded`.
2. **A query that still cannot carry the floor is refused**: `entityStatusFor` returns a fourth
   status `unfloored`, `classifyHits` returns `offtopic` (no fetch, feeds the degraded streak),
   `search.unfloored` counts it, and the footer names it. The `return true` is gone. The
   overlap fallback is deliberately NOT used — a one-term query scores high overlap on anything
   carrying that term, which is the same hole one door down.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A subject.test.ts --filter "T7 query side"
deno test -A subject.test.ts --filter "shapeQuery"
deno test -A subject.test.ts --filter "floorable query"
deno test -A harness-trust.test.ts --filter "T7:"
deno test -A harness-trust.test.ts --filter "the footer names a refused search"
```

**PASS:** `3`, `2`, `2`, `2` and `1` passed (the third filter also matches the *unfloorable*-query case, which is the point of it). The end-to-end case is the tester's own
reproduction: KEYWORDIZE mocked to return the subject `Signal` with the single need
`What is it?`, serving the recorded DSP payload, asserting the issued query has two content
words, `query_padded >= 1`, the set counted collapsed/offtopic, `search.ok === 0`,
**0 fetched**, backstop not `complete`, and `unfloored === 0` — zero because the builder
guarantees two, which is what makes the counter a tripwire rather than a routine stat.

**The reproduction, by hand, if you want it without the mocks:**

```bash
deno eval --ext=ts 'import { keywordQuery, classifyHits, entityShare, subjectTokens } from "file:///D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service/search-quality.ts";
const f = JSON.parse(Deno.readTextFileSync("D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service/fixtures/junk-signal-dsp.json"));
console.log(JSON.stringify(keywordQuery("Signal", "What is it?")));
console.log(entityShare(subjectTokens("Signal"), f.hits, "Signal"), classifyHits("Signal", f.hits, "Signal").verdict, classifyHits("Signal", f.hits, "Signal").entityStatus);'
```

**PASS:** `"Signal overview"` — two content words, not one — and the one-word query scores
`0 offtopic unfloored` rather than `1 ok`.

**Break-attempt targets for this half:** a need whose content words are all stopwords in some
other shape (an empty need, punctuation only, a need that repeats one word); a KEYWORDIZE
proposal of one word (the mock supplies `["Signal"]` and the harness still pads); a DEEPEN
proposal of one word (round 2+ goes through the same guarantee); a padded query that no longer
finds the good set — `T7: the same subject with a real need still works` pins the opposite, so
find a subject where padding with `overview` costs a real set its hits.

**FAIL:** any path from a need to a search whose query has under two content words; a one-term
query classified `ok`; `unfloored` counted in a run whose queries were all padded; the padding
changing any recorded fixture query.

## T8 - Integration, image, and renderer parity

```bash
# Throwaway DB from the 30 init scripts under OB1/docker (recipe in the research-trust plan).
# REUSE_MAX_DISTANCE=1.1 is required - pre-existing, see findings 3.8.
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> --network <net> \
  -e DB_HOST=<db> -e DB_PASSWORD=test -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check orchestrator.test.ts
docker build --label ai-stack.harness.owner=<you> -t openbrain-research:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
docker run --rm openbrain-research:wt-<you> sh -c "ls /app/*.ts"
```

**PASS:** `ALL ORCHESTRATOR ASSERTIONS PASSED`; the build succeeds (its `RUN deno check` is the
module-drift gate); the image carries `search-quality.ts`, `report.ts`, `grounding.ts` and no
`*.test.ts`.

Renderer parity — the OWUI tool is a paste surface and its footer changed, so its version must
have changed too:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno eval --ext=ts "import { coverageFooter } from './report.ts'; for (const u of [0,1]) console.log(coverageFooter([{need:'a',status:'answered'},{need:'b',status:'partial'}],{queries:[],hits:30,fetched:4,readable:4,relevant:2,collapsed:1,offtopic:0,ok:2,empty:0,errors:0,entity_missing:0,entity_rejected:2,unfloored:u,query_padded:0},'complete'))"
```

then the same record through the Python fallback (`t8.py` in the worktree root):

```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dr", "owui/tools/deep_research.py")
m = importlib.util.module_from_spec(spec); sys.modules["dr"] = m; spec.loader.exec_module(m)
out = m._render({"synthesis": "x", "cited_sources": [], "gaps": [], "backstop": "complete",
  "needs_status": [{"need": "a", "status": "answered"}, {"need": "b", "status": "partial"}],
  "search_record": {"hits": 30, "fetched": 4, "readable": 4, "relevant": 2, "collapsed": 1,
                    "offtopic": 0, "ok": 2, "empty": 0, "entity_missing": 0,
                    "entity_rejected": 2, "unfloored": U, "query_padded": 0}})
print([l for l in out.split("\n") if "needs answered" in l][0].strip().strip("_").replace("\u2014 ","").strip())
```

Run it twice, `U = 0` then `U = 1` — the second is the new `unfloored` clause, which must match
the TypeScript side exactly, including whether it appears at all when the count is zero.

**PASS:** both print, character for character — at `U = 0`:

```
needs answered 1 of 2 (1 partly) · sources 2 relevant of 4 fetched (30 hits, 1 junk) · entity gate: 2 search(es) judged without it (2 rejected the run's subject)
```

and at `U = 1` the same line with one clause appended:

```
 · entity gate refused 1 search(es): the query had fewer than two content words
```

Note what that clause is NOT: it is not folded into the `judged without it` count. Those
searches were judged WITHOUT the gate; a refused one was thrown away BY it, and reporting both
in one number would misstate each.

`owui/tools/deep_research.py` now says **`version: 1.4.1`** and **DOES need a re-paste**
(section D.3). The rendered bytes differ only in a run that refused a search — zero in every
run the query guarantee covers — but the deployed copy cannot render the clause at all, and a
counter whose whole purpose is to be loud is worth the paste. Attempt 3's "no re-paste needed"
was true of attempt 3; it is stated here as changed rather than left for the diff to reveal.

**FAIL:** a build failure; a missing module; the two renderers differing at either value of
`U`; the version bumped when the rendered output did not change, or unchanged when it did; the
refused count folded into the `judged without it` number.

**Clean up:** remove the container, network and image; `reap.ps1 -Report` must show nothing
owned by you.

---

## T9 - Nothing live changed, and nothing was removed without an account of it

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --name-only 8d480a5..work/research-trust-core
git diff 8d480a5..work/research-trust-core -- OB1
git status --short
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
```

**PASS:** the parent diff touches only `documentation/` and `owui/tools/deep_research.py`; the
OB1 gitlink diff is **EMPTY** (not bumped); the main checkout is unchanged; the running
container is untouched.

**Then reconcile the deleted TESTS, not only the removed source lines.** Three test files were
deleted across this item's attempts, and attempt 2 withdrew one case with no replacement and no
reason and narrowed another in silence — which the tester found and the source-line diff did
not show.

```powershell
# every test name that existed at the base of this item…
git -C OB1 show 5c189cf:integrations/research-service/entity.test.ts | Select-String -Pattern '^Deno.test\("' 
# …and at the tip
git -C OB1 show research-trust-core:integrations/research-service/subject.test.ts | Select-String -Pattern '^Deno.test\("'
```

```powershell
# ...and the SECOND deleted file, which attempt 3's table covered only in a group
git -C OB1 show 2d339df:integrations/research-service/entity-core.test.ts | Select-String -Pattern 'Deno.test'
```

Compare those lists against the **removed-tests table in findings section H.5**, which maps each
removed case to its replacement or states why it has none. It is **36 rows in two tables** — 22
for `entity.test.ts`, 14 for `entity-core.test.ts` — one row per case, no grouping. Attempt 3
had 14 rows covering 22 of the 36, with one grouped row scoped to the wrong file; the tester
verified every uncovered case did have a replacement, and those rows are now written out.

**PASS:** every removed case appears in the table and the counts match (22 + 14 = 36); each
row's replacement actually exists (search `subject.test.ts`, and `search-quality.test.ts` for
the `T11:` rows); and the four rows that say WITHDRAWN carry a reason you accept — a sibling
model now carrying the subject, and the three `shortenEntity` cases going with the function.

**FAIL:** a live container restarted or rebuilt; the gitlink bumped; a removed test missing
from the table; a table row naming a replacement that does not exist; a behaviour that
disappeared with no entry at all.


# D. Deploy (AFTER this plan passes — operator or reviewer)

Only ONE image changes: `openbrain-research`. Curator, gateway and engine policy are untouched
(all named out of scope in the anchor).

### D.1 Open Brain plane — lease `open-brain`

1. `.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain`
2. **Record the fallback first** — the rebuild retags the only image you can roll back to:
   ```bash
   docker tag openbrain-research:local openbrain-research:pre-research-trust-core
   ```
3. `docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env build openbrain-research`
   then `... up -d openbrain-research`.
4. `.\scripts\stack\stack.ps1 health`.

**Rollback:** retag `:pre-research-trust-core` back to `:local` and `up -d openbrain-research`.
No schema change ships.

### D.2 The two dry runs — the live proof (anchor acceptance 5)

```bash
curl -s -X POST http://127.0.0.1:8818/research -H "x-brain-key: $MCP_ACCESS_KEY" \
  -H "content-type: application/json" \
  -d '{"query":"Research on using 100Hz (or specific audio frequencies) sound to reduce or counteract motion sickness in VR","origin":"owui","options":{"dry_run":true}}'
```

Poll `GET /research/jobs/<id>`. **PASS:** the run FETCHES pages and cites a URL matching
`pubmed.ncbi.nlm.nih.gov/40128952`, `pmc.ncbi.nlm.nih.gov/PMC11955832` or `jstage.jst.go.jp`.
Anything else fails, including a clean `no_relevant_sources` — that is what the two previous
deploys produced and what this item exists to stop. Record the subject KEYWORDIZE returned and its
distinctive token set (the progress line names it).

Then the OptiPlex query, which PASSED on the previous deploy (dry run 4826d896: 5/5 searches
ok, 12 cited, footer `needs answered 1 of 6 (5 partly)`). **PASS:** it still cites at least 5
relevant sources with a footer consistent with the body. This is the regression half — the
entity for that run shortens from `Dell OptiPlex 3050`, so it exercises the change.

Record both job ids, the footer lines and the per-search entity shares in the findings sink.

### D.3 The OWUI tool DOES need a re-paste (v1.4.0 -> v1.4.1)

This reverses what attempt 3's plan said, and the reversal is the point: attempt 3 deleted the
`shortenEntity` footer clause and the file went back to the deployed bytes, so no paste was
needed. Attempt 4 adds a different clause - the refused-search counter - so the paste is needed
again.

`owui/tools/deep_research.py` is **v1.4.1**. Paste it over the existing tool in Open WebUI
(Workspace -> Tools -> deep_research), keeping its id, and confirm the header shows 1.4.1.
`owui/manifest.csv` already maps the file to its OWUI id; no new row.

**What actually changes for a reader:** nothing, in any run whose queries were all searchable -
the clause fires only when `search.unfloored` is non-zero, which the query guarantee is built to
make impossible. It is pasted so that if the tripwire ever does trip, the footer the operator
reads says so instead of quietly dropping the count. The two renderers are byte-identical at
both values (T8).

**If the paste is skipped:** nothing breaks. The deployed 1.4.0 renders every other clause
correctly and simply omits this one; the engine-side footer (report.ts) and the stored
`result.rendered` already carry it, and that is the path most runs use.

---

# E. Landing

1. **OB1 first.** The OB1 changes sit on branch `research-trust-core` in this worktree's
   submodule and are NOT pushed. Push to OB1's remote BEFORE bumping the gitlink.
2. **Then the gitlink:** `git add OB1` in the parent, committed with what moved. The developer
   deliberately did not stage it (T9 checks this).
3. **The merge.** The work line is checked out in the operator's main checkout, so the reviewer
   rebases and hands the `--no-ff` merge back to the operator with the evidence.
4. **Container rule:** no service added, removed or moved. `/stack-map` should show no drift.
