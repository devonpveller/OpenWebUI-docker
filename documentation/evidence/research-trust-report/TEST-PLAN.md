# TEST-PLAN — harness item `research-trust-report`

**Branch:** `work/research-trust-report` (parent, base `7f830cb`) + `research-trust-report`
(OB1 submodule, base `1b88347` = `origin/research-trust-core`, NOT pushed).
**Developer:** `wt-research-trust`. **Anchor:**
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust-report`, copy committed at
`documentation/implementation-guide/research-engine-for-OB/anchor-research-trust-report.json`
(11 acceptance items; amended once before confirmation).

This item is the first in the series that is not about whether the engine LIES. It is about
whether what it hands over is usable. The operator's words, on the first live report off the
deployed stack: it is "organized only as facts, sources and gaps, which is not a greatly
formatted report that I could hand off in a professional environment" — the information is
accurate and must stay that way — and "when the answer isn't complete enough, there should be a
recommendation to perform an additional run, or better yet, perform the additional run before
sending an incomplete result back to the end user".

Read `documentation/notes/research-trust-findings.md` section **J** first. The artefact is job
**33250e9b**, whole, at `OB1/integrations/research-service/fixtures/live-owui-33250e9b.result.json`,
with the delivered document beside it (`rendered-BEFORE-33250e9b.md`) and this branch's render of
the SAME synthesis (`rendered-AFTER-33250e9b.md`). The parent repo keeps byte-identical
human-facing copies under `documentation/evidence/research-trust-report/`; the fixtures are
canonical, because an OB1 test may never read a file outside OB1.

---

## Preconditions — where to run, and what this branch does NOT touch

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust-report`
and it holds the branch. Cases T1–T10 are **read-only executions** (`deno test`, `python`,
`git show`) — running them there changes no git state. Do not `git add`, `git commit` or edit
anything.

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat 7f830cb..work/research-trust-report
git -C OB1 diff --stat 1b88347..research-trust-report
```

**What ran against anything live, and what it was.** One inference call, through the deployed
LiteLLM path, to produce `rendered-AFTER-33250e9b.md`: the buyer's-guide system prompt from this
branch plus the RECORDED synthesis, sent with
`docker exec -i openbrain-research deno eval` → `$CHAT_API_BASE/chat/completions`. No container
was restarted, rebuilt, retagged or reconfigured; nothing was written to any database; no claim
was touched; no job was enqueued. It is a chat completion and nothing else, and it is what makes
acceptance 1 checkable before deploy rather than after. **Leases:** T1–T10 need none; section D
needs **open-brain**.

---

## T1 — Unit suites and lint, with ONLY the service directory present

Run the research-service suite in a container that mounts **nothing but that directory**. This is
how OB1 is built and tested on its own, and it is the only way to see a test that reads a file
outside the submodule — which is exactly what the first submission of this item did: three
fixtures loaded from the PARENT repo's `documentation/evidence/`, which passes in a full checkout
and fails everywhere else (`NotFound: readfile '/documentation/evidence/…'`, 212 passed / 2
failed). Running it from the worktree cannot catch that class of defect, so do not.

```bash
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service:/w:ro" \
  -w /w denoland/deno:2.3.3 deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report" && ruff check .
```

**PASS:** research-service **`233 passed | 1 failed`** (the anchor requires at least 203);
research-curator `36 passed | 0 failed`; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — it opens a postgres pool at module load and needs the
throwaway DB of T9. It fails the same way on the base commit.

**FAIL:** any other failing test; research-service below 233; curator below 36; any ruff error;
**any file read outside `/w`** — a `NotFound` on a path starting `/documentation` is that defect
returning.

**Check the fixtures are inside the submodule**, rather than trusting the run:

```powershell
git -C OB1 ls-files integrations/research-service/fixtures | Select-String -Pattern '33250e9b'
Select-String -Path OB1\integrations\research-service\*.ts -Pattern 'documentation/evidence'
```

**PASS:** three fixture files tracked inside OB1 (`live-owui-33250e9b.result.json`,
`rendered-BEFORE-33250e9b.md`, `rendered-AFTER-33250e9b.md`, each carrying a provenance header);
the second search returns only a COMMENT in `report-doc.test.ts`'s docblock, never a path a test
reads. The parent keeps byte-identical human-facing copies under
`documentation/evidence/research-trust-report/` with a README saying which is canonical — `cmp`
them if you want to.

**New file:** `report-doc.test.ts` (21 cases) covers the delivered document end to end. The gap
pass adds 7 cases to `harness-trust.test.ts` and the renderer 3 to `lib.test.ts` (one of which is
a REWRITE — see T10).

---

## T2 — ACCEPTANCE 2: the footer agrees with the body

The live run grounded 26 cited lines across seven needs and printed
**`needs answered 0 of 7 (7 partly)`**. That footer is in the committed BEFORE document; it is
the RED for this case, and it was written by the engine in production rather than composed for a
test - the file is a recording, not a construction.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A report-doc.test.ts --filter "ACCEPTANCE 2"
deno test -A report-doc.test.ts --filter "grounded lines"
grep -c "needs answered 0 of 7" fixtures/rendered-BEFORE-33250e9b.md
```

**PASS:** 1 and 1 passed; the BEFORE document contains that footer once. Re-reconciling the
recorded synthesis gives **6 answered, 1 partly** (the anchor's floor is 5), and the need that
stays partly-answered is the red-flags/water-damage one, which the synthesis really does carry a
single line about.

**Check the measurement, not the number.** The first version of this rule scored **17–19 lines
for every one of the seven needs** out of 19 grounded lines, because `dell`, `optiplex` and
`3050` are in every need and nearly every line — the subject wearing a per-need label, and
`answered` would have been free for every run. What ships subtracts the terms a need shares with
half its siblings and then asks for one of the need's OWN words plus a second word of the need
(`report.ts:116`, `:152`). Try to break THAT: a run whose needs are near-duplicates of each
other; a need of two words; a synthesis whose lines quote the need verbatim without grounding
anything.

**FAIL:** fewer than 5 of 7 answered; a need with no grounded line counted answered; a
`search_failed` need reopened; the per-need counts in `report-doc.test.ts` not matching a
recomputation you do yourself.

---

## T3 — ACCEPTANCE 1: the document a person could hand over

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A report-doc.test.ts --filter "ACCEPTANCE 1"
deno test -A report-doc.test.ts --filter "BEFORE document"
```

Then **read both documents**, because this case is about whether a person could use one:

```
OB1/integrations/research-service/fixtures/rendered-BEFORE-33250e9b.md
OB1/integrations/research-service/fixtures/rendered-AFTER-33250e9b.md
(byte-identical copies under documentation/evidence/research-trust-report/)
```

**PASS:** 1 and 1 passed. The AFTER document has: a title stating the finding; `## Executive
summary`; `## What to check in person` with at least five `- [ ]` checks; `## Failure modes by
subsystem` as a table whose every row carries a `[Source N]` and which covers PSU, socket, RAM,
BIOS and capacitors; exactly one `## Limitations and open questions`, ending with one sentence
recommending a further run in the reader's terms; and nothing addressed to the model. The BEFORE
document has `## What was not found` AND `**Open gaps** (NOT grounded …)` AND the INCOMPLETE
banner — all three gone from the AFTER.

**The AFTER document is a RENDER, not a fixture invented for the test.** Same synthesis, same
`[Source N]` numbers, same model as production. Reproduce it if you want to (Preconditions names
the exact call); the two documents differ only by the template.

**FAIL:** any required section missing; an uncited table row; two lists of unknowns; the model
directive in the body; a title asserting absence.

---

## T4 — ACCEPTANCE 5: the template is chosen for the reader's PURPOSE

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A report-doc.test.ts --filter "ACCEPTANCE 5"
deno test -A report-doc.test.ts --filter "purpose"
deno test -A report-doc.test.ts --filter "falls closed"
deno test -A templates.test.ts
```

**PASS:** 2, 1, 1 and 8 passed. Selection fires at `answered + partial >= 3`
(`report.ts:251`) — the live run had 0 answered and 7 partial and therefore got the general
report; the classifier returns `{"purpose","template"}` and a purpose with an unusable id still
lands on that purpose's template; garbage falls closed to `general-report`.

**Read this one before judging it.** The anchor says "the classifier is given the QUESTION's
purpose". It is implemented as *the classifier decides and states the purpose, then picks a
template for it*, in one call, and the purpose is recorded on the run and in the progress log.
It is NOT a cue-word list mapping "should I buy" to the buyer's guide, and that is a deliberate
refusal: four items in this workstream have failed on a hand-written list of surface strings
(pattern strings, a 7-char length, token-before-first-digit, longest token), and a list of buying
words would be the fifth. If you judge that the acceptance item requires the purpose to be
computed OUTSIDE the classifier and handed to it, say so — it is a real reading of the words and
this is the reason it was not built that way.

**FAIL:** a run with three evidenced needs still falling to `general-report`; the purpose absent
from the classifier's contract; a keyword list deciding the template.

---

## T5 — ACCEPTANCE 3: one section of unknowns, and one machine line

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A report-doc.test.ts --filter "every template has exactly one limitations"
deno test -A lib.test.ts --filter "do-not-fabricate"
deno test -A lib.test.ts --filter "addressed to the model"
deno test -A lib.test.ts --filter "no machine line at all"
```

**PASS:** 1, 1, 1 and 1 passed. Every template's rendered system prompt contains exactly one
`## Limitations and open questions` and no second gaps section; an incomplete run ends with

```
<!-- engine: incomplete (gaps_open); N need(s) not fully answered; do not fill them from your own knowledge - call deep_research with a query targeting the open question -->
```

as its LAST line and nothing else machine-addressed anywhere in the body; a complete run emits
no such line at all.

**FAIL:** both gap blocks appearing together anywhere; the directive in prose; the line present
on a complete run; more than one such line.

---

## T6 — ACCEPTANCE 3 (parity): the two renderers, byte for byte

The OWUI tool is deploy-by-paste, so a divergence here is one the operator cannot see.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno eval --ext=ts "import { renderResult } from './lib.ts'; Deno.writeTextFileSync('/tmp/ts-r.txt', renderResult({ synthesis: 'BODY [Source 1]', cited_sources: [{url:'https://a.example',title:'A'}], needs_status: [{need:'a',status:'answered'},{need:'b',status:'partial'}], search_record: { hits:30, fetched:4, readable:4, relevant:2, ok:0, collapsed:1, offtopic:1, empty:0, errors:0, entity_missing:0, entity_rejected:2, unfloored:1, query_padded:1 }, backstop: 'complete', gaps: ['what about X?'], gap_pass: {added:3,answeredBefore:1,answeredAfter:2,total:2} }));"
```

```python
# same record through the Python fallback
import importlib.util, io
spec = importlib.util.spec_from_file_location("dr", "owui/tools/deep_research.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
res = {"synthesis":"BODY [Source 1]","cited_sources":[{"url":"https://a.example","title":"A"}],
 "needs_status":[{"need":"a","status":"answered"},{"need":"b","status":"partial"}],
 "search_record":{"hits":30,"fetched":4,"readable":4,"relevant":2,"ok":0,"collapsed":1,"offtopic":1,
                  "empty":0,"errors":0,"entity_missing":0,"entity_rejected":2,"unfloored":1,
                  "query_padded":1,"queries":[]},
 "backstop":"complete","gaps":["what about X?"],
 "gap_pass":{"added":3,"answeredBefore":1,"answeredAfter":2,"total":2}}
io.open("/tmp/py-r.txt","w",encoding="utf-8",newline="").write(m._render(res))
```

then `cmp /tmp/py-r.txt /tmp/ts-r.txt`.

**PASS:** `cmp` is silent. Both end with the `<!-- engine: incomplete … -->` line, both carry
`gap-closing pass: +3 sources, needs answered 1 of 2 -> 2 of 2`, and neither prints a gap list.

**Note a second duplication this item removed.** `lib.ts` carried its OWN footer implementation —
no partly count, no entity-gate clause, "1 collapsed" where `report.ts` says "2 junk" — and it
only ran on the fallback path, so nothing ever compared the two. Both renderers now call
`coverageFooter` (`lib.ts:402`). If you run the T6 commands against the BASE commit you get two
different footers, which is the RED.

`owui/tools/deep_research.py` is **v1.5.0** and **DOES need a re-paste** (section D.3).

**FAIL:** any byte of difference; the version not bumped; a renderer carrying its own copy of a
line the other one also writes.

---

## T7 — ACCEPTANCE 8/9/11: the gap-closing pass

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A harness-trust.test.ts --filter "ACCEPTANCE 8"
deno test -A harness-trust.test.ts --filter "ACCEPTANCE 11"
deno test -A harness-trust.test.ts --filter "INTERIM"
deno test -A harness-trust.test.ts --filter "article path"
deno test -A harness-trust.test.ts --filter "still grounded and still measured"
deno test -A report-doc.test.ts --filter "gap queries"
```

**PASS:** 3, 1, 1, 1, 1 and 2 passed. In the mocked run the pass runs **once** (one interim
event, one summary event), adds sources, re-synthesizes, and the footer reads
`gap-closing pass: +K sources, needs answered X of N -> Y of N`; the curator is called **exactly
once**, with the final synthesis; no pass runs when every need is answered, when the wall clock
is spent (a `budget.wallMs` of 1), or on the article path.

**How it is bounded, so you can attack the bounds** (`harness.ts:1261`): `gapRound` is set to
null *before* it is awaited; at most 3 queries; only under 0.6 of the wall clock; only on the
topic path; only when `backstop === "complete"`. The queries come from the synthesis's own
`[GAP]` lines through `shapeQuery`, so the pass cannot emit the one-word query the entity floor
exists to refuse (previous item, T7b).

**Two things the pass must not do**, both regressions caught while building it:

- it must not RELABEL the run — the first version let its own budget check overwrite
  `fetch_degraded` (a diagnosis) with `max_fetch` (a budget note), and
  `harness-trust.test.ts --filter "fetch_degraded"` went red;
- it must not skip the numeric gate — the second synthesis goes through the same
  `applyNumericGrounding` + `buildCitedAndRenumber`, because a pass that adds sources is exactly
  when an uncited figure arrives.

**Try to break it:** a run where the pass's own searches collapse; a run whose gap lines are all
stopwords; a contract with `rounds: 1`; a synthesis with no `[GAP]` lines at all but an open
need; a second pass reachable by any path you can find.

**FAIL:** two passes; a pass with nothing open; a pass past the clock; more than one
`delegateToCurator` call; an intermediate synthesis persisted; the backstop relabelled; the
footer line absent when a pass ran (including when it added nothing).

---

## T8 — ACCEPTANCE 4/9: the chat message is rewritten, not stacked

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
deno test -A report-doc.test.ts --filter "ACCEPTANCE 4+9"
deno test -A report-doc.test.ts --filter "actually SAID"
grep -n "rewriteChatBody" index.ts lib.ts
```

**PASS:** 1 and 1 passed; `index.ts:535` uses it for `message.content` and the `output` array is
filtered of the engine's previous block. The interim body replaces the waiting line, the final
body replaces the interim, and the reader's first line is the report title.

**The contract to check, because it decides what gets deleted.** The engine strips exactly one
string, `HANDOFF_WAIT_LINE` (`lib.ts:293`), which `deep_research.py` instructs the model to reply
with verbatim. Anything else the model wrote is KEPT and the report is appended under it. A
pattern that guessed at "whatever the model said while waiting" would be deleting a person's
assistant's words on a heuristic; this is a contract between the two halves of the engine, and it
degrades to the old append behaviour when the model does not honour it.

**FAIL:** a second write stacking a second copy; the waiting line surviving; text the model wrote
that was not the waiting line being deleted; the final body starting with anything but the report.

---

## T9 — Integration, image, and the run's own record

```bash
# Throwaway DB from the 30 init scripts under OB1/docker (recipe in the research-trust plan).
# REUSE_MAX_DISTANCE=1.1 is required - pre-existing, see findings 3.8.
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> --network <net> \
  -e DB_HOST=<db> -e DB_PASSWORD=test -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check orchestrator.test.ts
docker build --label ai-stack.harness.owner=<you> -t openbrain-research:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-report/OB1/integrations/research-service"
docker run --rm openbrain-research:wt-<you> sh -c "ls /app/*.ts; ls /app/*.test.ts 2>/dev/null || echo NO_TESTS_SHIPPED"
```

**PASS:** the orchestrator suite passes against a real schema; the image builds; `/app` carries
the source modules and **no** `*.test.ts`.

**Clean up:** remove the container, network and image; `reap.ps1 -Report` must show nothing owned
by you.

**FAIL:** a build failure; tests shipped in the image; the orchestrator suite failing for any
reason other than the environment.

---

## T10 — Nothing live changed, and nothing was removed without an account of it

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --name-only 7f830cb..work/research-trust-report
git diff 7f830cb..work/research-trust-report -- OB1
git status --short
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
```

**PASS:** the parent diff touches only `documentation/` (the plan, the findings, the anchor copy
and the human-facing evidence copies) and `owui/tools/deep_research.py`; the
OB1 gitlink diff is **EMPTY** (not bumped); the main checkout is unchanged; the running container
is untouched (the one inference call in Preconditions neither restarts it nor changes its image
or its start time).

**Removed / rewritten tests — reconcile the CASES, not the source lines.** One case was rewritten
and none was deleted:

| case | file | what happened |
|---|---|---|
| `renderResult: gaps and early stops carry the do-not-fabricate directive` | `lib.test.ts` | **REWRITTEN in place.** It asserted the four-sentence INCOMPLETE banner and the `- what about X?` gap bullets, both of which this item removes. What it protects — an incomplete run still telling the model not to fabricate — is asserted against the line that now carries it, plus two NEW cases: `a complete run emits no machine line at all` and `nothing in the body is addressed to the model`. The reason is in a comment above the case. |

```powershell
git -C OB1 diff 1b88347..research-trust-report -- integrations/research-service | Select-String -Pattern '^-Deno.test'
```

**PASS: NO output at all.** No test case was deleted or renamed anywhere in this item. The one
rewritten case keeps its name, so the only way to see the rewrite is to read the case - which is
why the reason for it is written in a comment directly above it, and why this table exists.

**FAIL:** a live container restarted or rebuilt; the gitlink bumped; any test case removed
without a row here; a behaviour that disappeared with no entry at all.

---

# D. Deploy (AFTER this plan passes — operator or reviewer)

Only ONE image changes: `openbrain-research`. Curator, gateway, search and engine policy are
untouched (all named out of scope in the anchor).

### D.1 Open Brain plane — lease `open-brain`

```powershell
.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain -By <you>
docker tag openbrain-research:local openbrain-research:pre-research-trust-report   # rollback tag FIRST
docker build -t openbrain-research:local "D:\Open WebUI\ai-stack\OB1\integrations\research-service"
docker compose -f OB1\docker\docker-compose.yml up -d openbrain-research
docker logs --tail 20 openbrain-research
```

**Rollback:** `docker tag openbrain-research:pre-research-trust-report openbrain-research:local`
and `up -d` again.

### D.2 The 100 Hz dry run — unchanged proof

The previous items' proof must still hold: the 100 Hz dry run still cites **PMC11955832**, and
its three searches still classify `ok`. Run it exactly as the research-trust-core plan's D.2
describes. This item changed the coverage judge, so read the FOOTER too: it should now say more
needs answered than before, and it must not say fewer.

### D.3 The OWUI tool — re-paste (v1.4.1 → v1.5.0)

`owui/tools/deep_research.py` changes in three ways a reader will see: the duplicated gap block
and the INCOMPLETE banner are gone, the machine line is added, and the hand-off notice now tells
the model to reply with one exact line (which is what lets the engine replace it). Paste it over
the existing tool in Open WebUI (Workspace → Tools → deep_research), keeping its id, and confirm
the header shows `1.5.0`. `owui/manifest.csv` already maps the file; no new row.

**If the paste is skipped:** the async path still delivers correctly (the engine renders
server-side and stores `result.rendered`), but the model will not be told the exact waiting line,
so the final message will keep whatever it said above the report — the old behaviour.

### D.4 The live OWUI run — the proof this item exists for

Ask the deployed tool the OptiPlex question again, from a chat, and watch the MESSAGE:

1. while the first pass runs, the body is the waiting line;
2. **read the message between the two writes** — when the gap-closing pass starts it must say
   `First pass complete: X of N needs answered - running a gap-closing pass to close the rest`
   and nothing else;
3. when the run finishes, the body must START with the report title, carry the checklist and the
   subsystem table, have ONE limitations section, and end with the footer plus the invisible
   machine line.

```sql
-- after the run, from the ops plane (SELECT only)
SELECT id, status, progress->>'phase', result->'gap_pass', result->'prose_ungrounded'
FROM research_jobs ORDER BY created_at DESC LIMIT 1;
```

**The run is the proof; the job row is the audit.** `gap_pass` says what the second pass bought,
and `proseUngrounded` says whether the rendered report introduced anything the grounded answer
does not hold.
