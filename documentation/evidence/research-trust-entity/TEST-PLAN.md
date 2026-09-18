# TEST-PLAN — harness item `research-trust-entity`

**Branch:** `work/research-trust-entity` (parent, base `295b66c`) + `research-trust-entity`
(OB1 submodule, base `9b2cb34`, NOT pushed).
**Developer:** `wt-research-trust`. **Anchor:** confirmed by profnovice;
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust-entity`.

This item exists because the *previous* item deployed and two live dry runs then reported
working searches as failures. Read `documentation/notes/research-trust-findings.md` →
"Deploy 2026-09-11" and the `research-trust-entity` section before starting: the failure, the
measurements and the judgements are all there, and several cases below ask you to disagree
with one.

---

## Preconditions - where to run, and what this branch does NOT touch

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust-entity`
and it holds the branch. Cases T1–T10 are **read-only executions** (`deno test`, `python`,
`git show`, `curl`) — running them there changes no git state. Do not `git add`, `git commit`
or edit anything.

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat 295b66c..work/research-trust-entity
git -C OB1 diff --stat 9b2cb34..research-trust-entity
```

**Nothing live was changed.** No container restarted, rebuilt or reconfigured; no claim
touched; no compose action. Live access was read-only: `GET :8085/search` captures and `psql`
SELECTs. The only services this branch can affect once deployed are `openbrain-research`
(image) and the OWUI tool (paste) — section D.

**Leases.** T1–T10 need none. Section D needs the **open-brain** plane lease.

---

## T1 - Unit suites and lint

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-service" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity" && ruff check .
```

**PASS:** research-service **`173 passed | 1 failed`** (the anchor requires at least 143);
research-curator `36 passed | 0 failed`; ruff `All checks passed!`.

The single failure must be `./orchestrator.test.ts (uncaught error)` — it opens a postgres
pool at module load and needs the throwaway DB of T9. It fails the same way on the base commit.

**FAIL:** any other failing test; research-service below 173 or curator below 36 (either would
mean tests were removed); any ruff error.

---

## T2 - ACCEPTANCE 1: the 100 Hz sets classify ok for every spelling

The live sets that made dry run b7e701ef report `search_degraded` with 0 pages fetched.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-service"
deno test -A entity.test.ts --filter "ACCEPTANCE 1"
```

**PASS:** two cases. `live-100hz-mechanism` classifies `ok` with entity `100Hz audio`,
`100Hz` **and** `100 Hz`; `live-100hz-studies` classifies `ok` with `100Hz audio` and
`100 Hz`. The first case also asserts the Nagoya paper is in the recorded set — the point is
that the deployed run threw away the pages it was sent to find.

Confirm the fixtures are live captures, not written to fit, and that they still reproduce:

```bash
python -c "import json;d=json.load(open('fixtures/live-100hz-mechanism.json',encoding='utf-8'));print(d['_provenance']);print(d['_dry_run'],'|',d['query'])"
curl -s -G "http://127.0.0.1:8085/search" --data-urlencode "q=100Hz audio VR motion sickness physiological mechanism" --data-urlencode "format=json" > /tmp/live.json
python -c "import json;d=json.load(open('/tmp/live.json',encoding='utf-8'));print(len(d['results']));print([r['title'][:60] for r in d['results'][:3]])"
```

**PASS:** the provenance names dry run `b7e701ef` and a read-only live GET, and the live
re-capture still returns ~20 hits with 100 Hz material near the top. (The engine mix varies
between captures; the fixture is the record, the live call is the sanity check.)

**FAIL:** any of the three spellings classifying non-`ok`; a fixture whose provenance says
hand-built; a live re-capture that returns nothing about 100 Hz — that would mean the search
plane regressed and this case cannot be judged, so report it as such rather than passing it.

---

## T3 - ACCEPTANCE 2: the OptiPlex sets classify ok with the branded entity

```bash
deno test -A entity.test.ts --filter "ACCEPTANCE 2"
```

**PASS:** `live-optiplex-thermal` is `ok` with entity `Dell OptiPlex 3050` **and** with
`OptiPlex 3050`; `live-optiplex-health` stays `ok`.

**This one deserves your judgement, not just a green tick.** Only 6 of the thermal set's 20
hits name the OptiPlex 3050 (share 0.30); the rest are generic Dell overheating pages and
other OptiPlex models. Read them:

```bash
python -c "
import json;d=json.load(open('fixtures/live-optiplex-thermal.json',encoding='utf-8'))
[print(i,'|',h['title'][:78]) for i,h in enumerate(d['hits'],1)]"
```

The developer's position: 6 on-subject pages out of 20 — including *Optiplex 3050m NVMe
Overheating* — is a usable search, and the relevance gate judges pages one at a time
afterwards. If you think 0.30 should read as a failed search, that is a real disagreement and
it changes `ENTITY_SHARE` (T5). Say so rather than passing around it.

**FAIL:** either set classifying non-`ok`; the branded and unbranded entities disagreeing.

---

## T4 - ACCEPTANCE 3: nothing that used to collapse stops collapsing

The regression that matters. Six recorded collapse sets and three good ones.

```bash
deno test -A entity.test.ts --filter "ACCEPTANCE 3"
deno test -A search-quality.test.ts
```

**PASS:** `19 passed | 0 failed` from `search-quality.test.ts`, and both ACCEPTANCE 3 cases:
`search-collapsed-dell` / `-most` / `-the100` / `probe-collapsed-capacitor` / `-motherboard` /
`-vestibular` all classify non-`ok`; `probe-good-oomkilled`, `probe-good-iphone` and
`search-good-optiplex` all classify `ok`.

`search-collapsed-the100` is the one to check by hand: entity `100 Hz`, and every hit carries
the token `100` (*The 100*, the TV series) while none carries `hz`. If core extraction ever
reduced `100 Hz` to `100`, that set would pass and this whole module would be undone —
`entityCore never strips a unit away from its number` pins it.

**FAIL:** any collapse set classifying `ok`; any good set classifying `collapsed`/`offtopic`.

---

## T5 - ACCEPTANCE 5: ENTITY_SHARE is measured, and off every edge

```powershell
git -C OB1 show research-trust-entity:integrations/research-service/search-quality.ts | Select-String -Pattern "ENTITY_SHARE" -Context 34,2
```

**PASS:** `ENTITY_SHARE = 0.175` with the full measured table in its docblock, matching
findings §E.3: GOOD sets at 1.00, 1.00, 0.75, 0.65, 0.55, 0.35, 0.30; the off-need live set at
0.05; six collapse sets at 0.00.

Re-measure it yourself rather than trusting the comment — a short deno script importing
`entityCore` and `entityShare` over every fixture in `fixtures/`.

**PASS:** no recorded set within 0.1 of 0.175 (nearest are 0.05 and 0.30, both 0.125 away),
and your numbers match the table.

**The reasoning to check:** the previous item chose 0.5 from the curated fixtures, where the
gap looked like 0.00 → 0.75. The live sets put four GOOD sets below that line. The real
headroom is 0.125 each side. If you can find a recorded set the table omits, or an argument
that 0.175 is too permissive (T3 is where to look), that is the finding.

**FAIL:** a threshold on a measured edge; a table that does not reproduce; a set left out of it.

---

## T6 - ACCEPTANCE 4: the entity itself is validated, and the fallback is counted

```bash
deno test -A entity.test.ts --filter "ACCEPTANCE 4"
```

**PASS:** four cases. An entity absent from the query gives `entityStatus: "rejected"`, no
`entityShare`, and the verdict falls back to the overlap rule. An empty/undefined/whitespace
entity gives `"missing"`. An entity the query carries gives `"used"` with a numeric share. A
DEEPEN query carrying only the core satisfies a branded entity.

Then the counting and the footer:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-service"
deno eval --ext=ts "import { coverageFooter } from './report.ts'; console.log(coverageFooter([{need:'a',status:'answered'},{need:'b',status:'partial'},{need:'c',status:'open'}],{queries:[],hits:30,fetched:4,readable:4,relevant:2,collapsed:1,offtopic:0,ok:2,empty:0,errors:0,entity_missing:0,entity_rejected:2},'complete'))"
```

**PASS:** it prints exactly

```
needs answered 1 of 3 (1 partly) · sources 2 relevant of 4 fetched (30 hits, 1 junk) · entity gate: 2 search(es) judged without it (2 rejected the run's subject)
```

and the Python fallback prints the SAME STRING for the same record (T9's script).

**FAIL:** a fallback that is not counted; `entity_missing`/`entity_rejected` absent from
`search_record`; the two renderers differing by one character.

---

## T7 - ACCEPTANCE 7: the footer no longer contradicts its own report

```bash
deno test -A coverage.test.ts
```

**PASS:** `8 passed | 0 failed`. The fixture `dryrun-optiplex-1f2ff740.json` is the recorded
live result: 11 cited sources, 25 grounded lines, all 6 needs `open`, footer
`needs answered 0 of 6`. After `reconcileNeedsStatus` no need the synthesis grounds is left
`open`, and the footer carries `(N partly)`.

Read what the rule refuses to do — this is the half that could have been done dishonestly:

```powershell
git -C OB1 show research-trust-entity:integrations/research-service/report.ts | Select-String -Pattern "reconcileNeedsStatus" -Context 18,10
```

**PASS:** `answered` is never manufactured (the judge is not overruled by a term overlap),
`search_failed` is never reopened, and an empty synthesis changes nothing — each pinned by
`reconciliation never downgrades, and never invents an 'answered'`.

**FAIL:** a need with a grounded line still `open`; any `answered` produced by reconciliation;
a `search_failed` need turned `partial`; the footer omitting the partial count.

---

## T8 - The entity rule is a rule, not a fit to these five sets

Two previous attempts failed by picking a constant that encoded the recorded incident. Read
this one and attack it:

```powershell
git -C OB1 show research-trust-entity:integrations/research-service/search-quality.ts | Select-String -Pattern "entityCore" -Context 30,20
```

```bash
deno test -A entity.test.ts --filter "entityCore"
deno test -A entity.test.ts --filter "entityTokens"
deno test -A entity.test.ts --filter "hitCarriesEntity"
```

**PASS:** twelve cases covering tokenisation (`100Hz` = `100 Hz` = `100-Hz`), core extraction
(`Dell OptiPlex 3050` → `optiplex 3050`, `100Hz audio` → `100 hz`, `VR motion sickness` →
`motion sickness`), the unit-protection rule, entities that must be left alone, the `3050m`
model-variant suffix, and five cases that came out of the developer attacking this rule the
way this case asks you to:

- **a brand of ANY length is dropped.** The first version dropped a leading token only when it
  was under five characters — which worked for `Dell` by accident and left `Lenovo`, `NVIDIA`
  and `Microsoft` in the core. A model number anchors the identity instead, so
  `Lenovo ThinkCentre M910q` matches a page saying only `ThinkCentre M910q`.
- **a neighbouring model is not the same machine:** `RTX 3050 benchmark` does not satisfy
  `RTX 3050 Ti`, and `OptiPlex 3060` does not satisfy `OptiPlex 3050`.
- **a one-character model code never becomes the identity:** `MacBook Air M2` must not reduce
  to `m 2`, which matches the `M.2` SSD form factor — a string that appears in the OptiPlex
  fixture's own hit titles.
- **a bare number is never an identity:** `Surface Laptop 5` must not trim to `5`.

**Try to break it further.** The QUALIFIERS list is closed and short; everything else is
structural (a digit-bearing token, a length, the position of the first number). Look for an
entity whose core reduces to something no page would write; a product whose model number comes
FIRST (`3050 OptiPlex`); a unit longer than four characters (`100 hertz`); a core that
collides with an unrelated product line. Anything that makes a genuinely collapsed set classify
`ok`, or a genuinely good set collapse, is a FAIL and worth more than the rest of this case.

**FAIL:** a core no real page carries; `entityCore` emptying an entity; a rule whose only
justification is the five recorded sets.

---

## T9 - Integration, image, and renderer parity

```bash
# 1. Throwaway DB from the 30 init scripts under OB1/docker (recipe in the previous item's plan).
docker network create --label ai-stack.harness.owner=<you> wt-rte-test-net
docker run -d --name ob-claims-test-<you> --label ai-stack.harness.owner=<you> \
  --network wt-rte-test-net -e POSTGRES_DB=openbrain -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=test -v "<initdb dir>:/docker-entrypoint-initdb.d:ro" pgvector/pgvector:pg16
# 2. REUSE_MAX_DISTANCE=1.1 is required (pre-existing; previous item's findings section 3.8).
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> --network wt-rte-test-net \
  -e DB_HOST=ob-claims-test-<you> -e DB_PASSWORD=test -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check orchestrator.test.ts
# 3. The image must build (its RUN deno check is the module-drift gate).
docker build --label ai-stack.harness.owner=<you> -t openbrain-research:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-entity/OB1/integrations/research-service"
docker run --rm openbrain-research:wt-<you> sh -c "ls /app/*.ts"
```

**PASS:** `ALL ORCHESTRATOR ASSERTIONS PASSED`; the build succeeds; the image contains
`search-quality.ts`, `report.ts`, `grounding.ts` and no `*.test.ts`.

Then the OWUI fallback renderer — a paste-deploy surface (D.3) with hand-written parity logic.
Save as `t9.py` in the worktree root and run `python t9.py`:

```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("dr", "owui/tools/deep_research.py")
m = importlib.util.module_from_spec(spec); sys.modules["dr"] = m; spec.loader.exec_module(m)
rec = {"hits": 30, "fetched": 4, "readable": 4, "relevant": 2, "collapsed": 1,
       "offtopic": 0, "ok": 2, "empty": 0, "entity_missing": 0, "entity_rejected": 2}
ns = [{"need": "a", "status": "answered"}, {"need": "b", "status": "partial"},
      {"need": "c", "status": "open"}]
out = m._render({"synthesis": "x", "cited_sources": [], "gaps": [], "backstop": "complete",
                 "needs_status": ns, "search_record": rec})
line = [l for l in out.split("\n") if "needs answered" in l][0]
print(line.strip().strip("_").replace("\u2014 ", "").strip())
```

**PASS:** it prints the SAME STRING as T6's deno one-liner, character for character, and the
file header says `version: 1.4.0` (it must be re-pasted — D.3).

**FAIL:** a build failure; a missing module; the two renderers differing; the version
unchanged, since an un-bumped paste surface is one the operator will not re-paste.

**Clean up:** remove the container, network and image; `reap.ps1 -Report` must show nothing
owned by you.

---

## T10 - Nothing live changed, nothing true was silently removed

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --name-only 295b66c..work/research-trust-entity
git diff 295b66c..work/research-trust-entity -- OB1
git status --short
git -C OB1 diff 9b2cb34..research-trust-entity | Select-String -Pattern "^\-" | Select-String -NotMatch "^\-\-\-"
```

**PASS:** the parent diff touches only `documentation/` and `owui/tools/deep_research.py`; the
OB1 gitlink diff is **EMPTY** (not bumped); the main checkout is unchanged. Every removed OB1
line is replaced in the same hunk — the substantive removals are the old
`entityPhrase`/`entityShare` pair (replaced by core-based versions), `ENTITY_SHARE = 0.5`
(replaced by the measured 0.175 with its table), and the single-line `needs answered` footer
(replaced by the partial-aware one).

Also confirm the deployed services are untouched:

```bash
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
```

**FAIL:** a live container restarted or rebuilt; the gitlink bumped; a removed behaviour with
no replacement and no note.

---

# D. Deploy (AFTER this plan passes — operator or reviewer)

Only ONE image changes: `openbrain-research`. The curator, the gateway and the engine policy
are untouched — all three are named out of scope in the anchor.

### D.1 Open Brain plane — lease `open-brain`

1. `.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain`
2. **Record the fallback first** — the rebuild retags the only image you can roll back to:
   ```bash
   docker tag openbrain-research:local openbrain-research:pre-research-trust-entity
   ```
3. `docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env build openbrain-research`
   then `... up -d openbrain-research`.
4. `.\scripts\stack\stack.ps1 health` — the research service up, curator untouched.

**Rollback:** `docker tag openbrain-research:pre-research-trust-entity openbrain-research:local`
then `up -d openbrain-research`. No schema change ships, so there is nothing else to undo.

### D.2 The two dry runs — this is the live proof (anchor acceptance 6 and 7)

```bash
curl -s -X POST http://127.0.0.1:8818/research -H "x-brain-key: $MCP_ACCESS_KEY" \
  -H "content-type: application/json" \
  -d '{"query":"Research on using 100Hz (or specific audio frequencies) sound to reduce or counteract motion sickness in VR","origin":"owui","options":{"dry_run":true}}'
```

Poll `GET /research/jobs/<id>`. **PASS:** the run FETCHES pages and cites a URL matching
`pubmed.ncbi.nlm.nih.gov/40128952`, `pmc.ncbi.nlm.nih.gov/PMC11955832` or `jstage.jst.go.jp`.
Anything else fails — including a clean `no_relevant_sources`, which is what the deployed
build produced and what this item exists to stop.

Then the OptiPlex query (the one dry run 1f2ff740 used). **PASS:** the footer's
`needs answered X of N (Y partly)` is consistent with the body — if the report states a
finding for a need, that need is not counted `open`. Read the rendered report and check it by
eye; that is the acceptance criterion and no unit test can stand in for it.

Record both job ids and the footer lines in the findings sink.

### D.3 Re-paste the OWUI tool

`owui/tools/deep_research.py` is **v1.4.0** (was 1.3.0): the footer gained the partial count
and the entity-gate clause. OWUI loads tools from its own database, so paste it through the
tool editor or `POST /api/v1/tools/id/deep_research/update` (the API path refreshes the tool
cache — see the previous item's D.5), then check `owui/manifest.csv`.

---

# E. Landing

1. **OB1 first.** The OB1 changes sit on branch `research-trust-entity` in this worktree's
   submodule and are NOT pushed. Push to OB1's remote BEFORE bumping the gitlink — a pinned
   SHA unreachable on the remote breaks every fresh `--recurse-submodules` clone.
2. **Then the gitlink:** `git add OB1` in the parent, committed with what moved. The developer
   deliberately did not stage it (T10 checks this).
3. **The merge.** The work line is checked out in the operator's main checkout, so the reviewer
   rebases and hands the `--no-ff` merge back to the operator with the evidence.
4. **Container rule:** no service added, removed or moved. `/stack-map` should show no drift.
