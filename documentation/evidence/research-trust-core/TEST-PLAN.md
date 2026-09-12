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

**PASS:** research-service **`189 passed | 1 failed`** (the anchor requires at least 173);
research-curator `36 passed | 0 failed`; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — it opens a postgres pool at module load and needs
the throwaway DB of T8. It fails the same way on the base commit.

**FAIL:** any other failing test; research-service below 189; curator below 36; any ruff error.

---

## T2 - ACCEPTANCE 1: the subject that failed in production now passes

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-core/OB1/integrations/research-service"
deno test -A entity-core.test.ts --filter "ACCEPTANCE 1"
```

**PASS:** three cases. `entityCore("100Hz audio VR motion sickness")` is `["100","hz"]`; the
recorded hit set for that run's first query satisfies it at **share ≥ 0.4** and
`classifyHits` returns `ok`; both of that run's on-need queries classify `ok`. The first case
also asserts the Nagoya paper is in the set — the point is that the deployed run discarded the
pages it was sent to find.

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
`0`, i.e. the state this case exists to invert.

**FAIL:** any of the three cases failing; a share below 0.4; the recorded evidence not showing
the failure it is supposed to show.

---

## T3 - ACCEPTANCE 2: the rule, stated once and pinned on five subjects

The rule is one sentence in the `entityCore` docblock. Read it, then check each of its three
sub-decisions was **measured**, not asserted:

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "THE RULE" -Context 2,32
```

**PASS:** the docblock states the rule in one sentence and carries the measured shares for the
year, version and two-word-name decisions.

```bash
deno test -A entity-core.test.ts --filter "ACCEPTANCE 2"
```

**PASS:** five cases. The five subjects the anchor names reduce to
`100 hz` / `toyota prius` / `python 3 12` / `optiplex 3050` / `kubernetes crashloopbackoff`,
and each is then run against a **real hit set** with a reasoned verdict in the test's own
comment.

Re-measure the three decisions yourself — this is the part that must not be taken on trust:

```bash
python -c "
import json,re
def share(hits,toks):
    rx=re.compile('(?<![a-z0-9])'+'[^a-z0-9]{0,2}'.join(toks)+'(?![a-z0-9])',re.I)
    return round(sum(1 for h in hits if rx.search((h.get('title') or '')+' '+(h.get('snippet') or '')))/len(hits),2)
for f,cands in [('live-prius',[['toyota','prius'],['2026','toyota','prius'],['2026','prius']]),
                ('live-python312',[['python','3','12'],['3','12'],['python','3','12','asyncio']]),
                ('live-crashloop',[['kubernetes','crashloopbackoff'],['crashloopbackoff']])]:
    d=json.load(open('fixtures/%s.json'%f,encoding='utf-8'))
    print(f, [(share(d['hits'],c),' '.join(c)) for c in cands])"
```

**PASS:** `toyota prius` 0.95 > `2026 toyota prius` 0.80 > `2026 prius` 0.20;
`python 3 12` 0.35 against `3 12` 0.55 and 0.05 for the whole subject;
`kubernetes crashloopbackoff` 0.40 against 1.00 for the bare token. Your numbers should match
findings F.2.

**The judgements to push on**, because they are judgements:

- `crashloopbackoff` alone scores 1.00 and the rule yields 0.40. The developer's position is
  that the rule's job is to pass a good set, not to maximise share. If you think the extra
  0.60 of margin matters more, say so — it changes how aggressively the window shrinks.
- `python 3 12` at 0.35 is the thinnest good core measured anywhere in this workstream, and
  `ENTITY_SHARE` is 0.175. That is a factor of two of headroom for a case that will recur
  (every "library version X" question). Worth an opinion.

**FAIL:** a core no real page carries; a measured share that contradicts the docblock; a
sub-decision with no measurement behind it.

---

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
deno test -A entity.test.ts
deno test -A search-quality.test.ts
```

**PASS:** both green. The six collapse fixtures still classify non-`ok`; the good sets
(`oomkilled`, `iphone`, `good-optiplex`, `live-100hz-mechanism`, `live-100hz-studies`,
`live-optiplex-thermal`, `live-optiplex-health`) all still classify `ok`.

**Two expectations were CHANGED, not deleted** — check the replacements are stronger, not
weaker:

```powershell
git -C OB1 diff 5c189cf..research-trust-core -- integrations/research-service/entity.test.ts | Select-String -Pattern "^[-+].*entityCore"
```

**PASS:** `Lenovo ThinkCentre M910q` moves from `m 910 q` to `thinkcentre m 910 q` (more
specific, still brand-free, still matches a page writing only "ThinkCentre M910q"), and
`Apple MacBook Air M2` from `macbook air m 2` to `air m 2` — the guard that test exists for,
never the bare `m 2`, is unchanged and still asserted against the `M.2` string that appears in
the OptiPlex fixture's own titles. Both carry their reason in the test.

**A label change, not an outcome change:** three replay assertions now accept `collapsed` OR
`offtopic`. With the entity shortened to its name, a round-1 query carries `OptiPlex 3050`
rather than `Dell OptiPlex 3050`, so the Dell junk no longer piles onto a token the query
contains (findings F.4). Satisfy yourself the outcome is identical — nothing fetched, streak
fed, run degraded.

**FAIL:** any collapse set classifying `ok`; any good set classifying junk; a changed
expectation that is weaker than what it replaced.

---

## T6 - ACCEPTANCE 5: the share table is recomputed and the threshold still holds

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "ENTITY_SHARE" -Context 34,2
```

and findings section **F.6**.

**PASS:** `ENTITY_SHARE` is still `0.175`, with the recomputed table showing GOOD sets at
1.00, 1.00, 0.95, 0.75, 0.65, 0.55, 0.40, 0.35, 0.35, 0.30; the off-need live set at 0.05; the
six collapse fixtures at 0.00. Nearest sets are 0.05 and 0.30 — both 0.125 away — so the
no-set-within-0.1 rule holds and the threshold is not re-chosen.

Recompute it yourself over every fixture with `entityCore` + `entityShare`.

**PASS:** your numbers match; in particular `live-100hz-mechanism` and `live-100hz-studies`
moved from 0.00 to 0.55 and 0.35 — they were the false failures — and no set moved TOWARD the
line.

**FAIL:** a set within 0.1 of 0.175 with the threshold left unchanged; a table that does not
reproduce; a set omitted from it.

---

## T7 - The rule is a rule, not a fit to these subjects

Three items in a row have failed on a guard fitted to the case in front of it: eight pattern
strings, then a seven-character token length, then a core anchored on the token before the
first number. Attack this one the same way.

```powershell
git -C OB1 show research-trust-core:integrations/research-service/search-quality.ts | Select-String -Pattern "export function entityCore" -Context 0,70
```

```bash
deno test -A entity-core.test.ts
```

**PASS:** `14 passed | 0 failed`, including the bound case, which asserts the MEASURED core
length for eight subjects and says why the bound is in words rather than tokens.

**Try to break it.** Candidates the developer did not try:

- a subject whose model number comes first and is not a year: `3050 OptiPlex thermal`;
- a unit longer than four characters: `100 hertz tone`, `50 micrograms semaglutide`;
- a version with three parts: `Postgres 17.2.1 logical replication`;
- a subject that is two named things: `OptiPlex 3050 versus ThinkCentre M910q`;
- a non-English or hyphen-heavy subject: `e-bike 750 W hub motor`.

For each, ask two questions: is the core a phrase a real page would carry, and does a
plausible junk set for that query still classify non-`ok`? Anything that makes a genuinely
collapsed set pass, or a genuinely good set fail, is a FAIL and worth more than the rest of
this case.

**FAIL:** a core no page would write; an entity reduced to nothing; a rule whose only
justification is the subjects listed in the anchor.

---

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
