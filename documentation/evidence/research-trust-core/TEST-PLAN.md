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

**PASS:** research-service **`194 passed | 1 failed`** (the anchor requires at least 173);
research-curator `36 passed | 0 failed`; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — it opens a postgres pool at module load and needs
the throwaway DB of T8. It fails the same way on the base commit.

**FAIL:** any other failing test; research-service below 194; curator below 36; any ruff error.

**Test accounting, because two files were DELETED.** `entity.test.ts` (22) and
`entity-core.test.ts` (14) tested `entityCore()`, which no longer exists. `subject.test.ts`
(41) re-expresses every behavioural assertion they made against the set rule — brand omission,
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

**PASS:** `41 passed | 0 failed`, including `subjectTokens: digits, capitals, and anything
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

## T4 - ACCEPTANCE 3: the entity is bounded at extraction and the cut is counted

```bash
deno test -A entity-core.test.ts --filter "ACCEPTANCE 3"
deno test -A harness-trust.test.ts --filter "subject"
```

**PASS:** three unit cases (a five-word subject shortens to the name; the caller's spelling is
kept, so `Python 3.12` not `Python 3 12`; a subject that IS a name comes back byte-identical)
and two end-to-end cases: the failing run's own subject is shortened, **counted**
(`fetchStats.search.entity_shortened === 1`), the search is NOT reported as a failure, pages
ARE fetched, and the footer carries `subject shortened to its name (1x)`; while an
already-named subject is not counted.

Read the prompt half too — it is the cheaper fix and the one that stops the case arising:

```powershell
git -C OB1 show research-trust-core:integrations/research-service/harness.ts | Select-String -Pattern "ENTITY RULES" -Context 3,8
```

**PASS:** `KEYWORDIZE_SYS` states the entity is a NAME of at most 3 words, says what it is NOT
(the topic, an intent, a bare year), and uses the failing case as its own example.

**A judgement to check:** the counter fires only when the raw subject was longer than three
words, so stripping a brand (`Dell OptiPlex 3050` → `OptiPlex 3050`) is not reported. The
argument is that a brand strip happens on most product runs and would bury the case that
matters. If you think every correction should be visible, that is a real disagreement.

**FAIL:** a five-word entity not shortened; a shortening not counted; the footer silent; the
two renderers disagreeing (T8).

---

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

**PASS:** `ENTITY_SHARE` is still `0.175`, and the recomputed table covers every recorded set
including the tester's five: GOOD sets run 1.00 down to 0.55; the six collapse fixtures are all
0.00; `live-budget2026` is `entity_rejected` rather than scored.

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

## T7 - The rule is a rule, and its candidates are PINNED

Attempt 1 listed candidates here and pinned none, which the tester flagged: a skipped optional
half reads as clean. All five are now tests, each with the set the rule produces and a
judgement of whether that set names the thing.

```bash
deno test -A subject.test.ts --filter "T7 candidate"
deno test -A subject.test.ts --filter "tester's five"
```

**PASS:** five candidate cases and the tester's five live sets.

| candidate | distinctive set | why it is right |
|---|---|---|
| `3050 OptiPlex thermal` | `3050 optiplex thermal` | order is irrelevant to a set — the fourth rule failed precisely because it depended on where the number sat |
| `100 hertz tone` | `100 hertz tone` | a unit longer than four characters is just a token |
| `Postgres 17.2.1 logical replication` | `postgres 1721 logical replication` | the version glues; MySQL pages do not carry it |
| `OptiPlex 3050 versus ThinkCentre M910q` | `optiplex 3050 versus thinkcentre m910q` | a comparison page carries half; an unrelated page carries none |
| `e-bike 750 W hub motor` | `e bike 750 w hub motor` | hyphens are separators like any other |

And the tester's five, on their own live hit sets, all captured today with provenance headers
naming them as the attempt-1 failure: `live-semaglutide50` 0.90, `live-nikonz6iii` 0.85,
`live-mullvad` 0.95, `live-rpi5nvme` 1.00 — all `ok`, where the previous rule scored them
0.00, 0.05, 1.00-on-junk and 0.00. `live-budget2026` yields an EMPTY set and is
`entity_rejected`, which is the honest answer: "2026 budget" names nothing.

**Two costs are DECLARED as tests**, not hidden — check you accept them:

- `DECLARED: a sibling model counts as carrying the subject`. `OptiPlex 3060` holds 2 of
  `{dell, optiplex, 3050}`. The phrase rule refused it, and that refusal is what produced four
  successive false failures. Dell's own home page, carrying only `dell`, is still refused.
- **Adjacency no longer matters** (`search-quality.test.ts`, the `T11` spelling case):
  `OptiPlex 7080 and the 3050-era chipset` now carries the subject.

If you think either cost is too high, that is the finding — it is a judgement about which
direction of error is worse, and four items of history say false FAILURES have been the
expensive ones.

**Try to break it anyway.** The rule has three moving parts left: the common-word list, the
half threshold, and the bare-number guard. Look for a subject whose distinctive set is all
common words (does it reject, as `2026 budget` does?); a two-token subject where one token is
generic enough that half is too weak; a junk set that happens to carry half a five-token
subject. Anything that makes a genuinely collapsed set pass, or a genuinely good set fail, is
a FAIL.

**FAIL:** a candidate whose set names nothing a page would carry; a subject reduced to
nothing when it does name something; either declared cost appearing without its test.

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
deno eval --ext=ts "import { coverageFooter } from './report.ts'; console.log(coverageFooter([{need:'a',status:'answered'},{need:'b',status:'partial'}],{queries:[],hits:30,fetched:4,readable:4,relevant:2,collapsed:1,offtopic:0,ok:2,empty:0,errors:0,entity_missing:0,entity_rejected:2,entity_shortened:1},'complete'))"
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
                    "entity_rejected": 2, "entity_shortened": 1}})
print([l for l in out.split("\n") if "needs answered" in l][0].strip().strip("_").replace("\u2014 ","").strip())
```

**PASS:** both print, character for character:

```
needs answered 1 of 2 (1 partly) · sources 2 relevant of 4 fetched (30 hits, 1 junk) · subject shortened to its name (1x) · entity gate: 2 search(es) judged without it (2 rejected the run's subject)
```

and `owui/tools/deep_research.py` says `version: 1.5.0` (was 1.4.0).

**FAIL:** a build failure; a missing module; the two renderers differing; the version
unchanged, since an un-bumped paste surface is one the operator will not re-paste.

**Clean up:** remove the container, network and image; `reap.ps1 -Report` must show nothing
owned by you.

---

## T9 - Nothing live changed, nothing true was silently removed

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --name-only 8d480a5..work/research-trust-core
git diff 8d480a5..work/research-trust-core -- OB1
git status --short
git -C OB1 diff 5c189cf..research-trust-core | Select-String -Pattern "^\-" | Select-String -NotMatch "^\-\-\-"
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
```

**PASS:** the parent diff touches only `documentation/` and `owui/tools/deep_research.py`; the
OB1 gitlink diff is **EMPTY** (not bumped); the main checkout is unchanged; the running
container is untouched. Every removed OB1 line is replaced in the same hunk — the substantive
removals are the old digit-anchored `entityCore` body (replaced by the run-windowed one), the
old `KEYWORDIZE_SYS` prompt (replaced by the bounded one), and the two changed test
expectations of T5.

**FAIL:** a live container restarted or rebuilt; the gitlink bumped; a removed behaviour with
no replacement and no note.

---

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
deploys produced and what this item exists to stop. Record the subject KEYWORDIZE returned and
whether `entity_shortened` fired.

Then the OptiPlex query, which PASSED on the previous deploy (dry run 4826d896: 5/5 searches
ok, 12 cited, footer `needs answered 1 of 6 (5 partly)`). **PASS:** it still cites at least 5
relevant sources with a footer consistent with the body. This is the regression half — the
entity for that run shortens from `Dell OptiPlex 3050`, so it exercises the change.

Record both job ids, the footer lines and the per-search entity shares in the findings sink.

### D.3 Re-paste the OWUI tool

`owui/tools/deep_research.py` is **v1.5.0** (was 1.4.0): the footer gained the
`subject shortened to its name` clause. Paste through the tool editor or
`POST /api/v1/tools/id/deep_research/update`, then check `owui/manifest.csv`.

---

# E. Landing

1. **OB1 first.** The OB1 changes sit on branch `research-trust-core` in this worktree's
   submodule and are NOT pushed. Push to OB1's remote BEFORE bumping the gitlink.
2. **Then the gitlink:** `git add OB1` in the parent, committed with what moved. The developer
   deliberately did not stage it (T9 checks this).
3. **The merge.** The work line is checked out in the operator's main checkout, so the reviewer
   rebases and hands the `--no-ff` merge back to the operator with the evidence.
4. **Container rule:** no service added, removed or moved. `/stack-map` should show no drift.
