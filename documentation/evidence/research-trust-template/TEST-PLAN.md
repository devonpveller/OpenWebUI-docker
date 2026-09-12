# TEST-PLAN — harness item `research-trust-template`

**Branch:** `work/research-trust-template` (parent, base `e0fcd2d`) + `research-trust-template`
(OB1 submodule, base `990a2f5` = `origin/research-trust-report`, NOT pushed).
**Developer:** `wt-research-trust-template`. **Anchor:**
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust-template`, copy committed at
`documentation/implementation-guide/research-engine-for-OB/anchor-research-trust-template.json`
(amended once before confirmation: KEEP every template distinct, delete none).

The operator read the buyer's guide that job 64ac38cf delivered and said **"this looks good... set
this as a template for future use"**, and, on how many templates there should be, "we should have
about 4-5 now if not more". Both at once: make the approved shape the house shape, and keep the
templates distinct. There are ten and there are still ten.

Read `documentation/notes/research-trust-findings.md` section **L** first. The artefacts are in
`documentation/evidence/research-trust-template/` (human-facing copies) and in
`OB1/integrations/research-service/fixtures/` (canonical — an OB1 test may never read a file
outside OB1).

---

## Preconditions

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust-template`
and it holds the branch. Cases T1–T9 are **read-only executions** — running them changes no git
state. Do not `git add`, `git commit` or edit anything.

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat e0fcd2d..work/research-trust-template
git -C OB1 diff --stat 990a2f5..research-trust-template
```

**What ran against anything live, and what it was.** Three renders and their fidelity checks,
through the deployed LiteLLM path, relayed by `docker exec -i openbrain-research deno eval` (the
gateway has no host port and attaching to its network is forbidden); and two **read-only**
`psql SELECT`s — the active-claims sweep of T7 and the export of the recorded comparison job that
became a fixture. No container was restarted, rebuilt, retagged or reconfigured; nothing was
written to any database; no claim was touched; no job was enqueued. **Leases:** T1–T9 need none;
section D needs **open-brain**.

---

## T1 — Unit suites and lint, with ONLY the service directory present

```bash
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service:/w:ro" \
  -w /w denoland/deno:2.3.3 deno test -A --no-lock
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template" && ruff check .
```

**PASS:** research-service **`265 passed | 1 failed`** (the anchor requires at least 248);
research-curator **`40 passed | 0 failed`**; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — postgres at module load, T8 runs it properly.

**`--no-lock` matters and is not a convenience.** `deno.lock` is gitignored, so the first
read-only run after the dependency graph changes dies with `Failed writing lockfile`. The
alternative is a writable mount, and a writable mount lets a test run edit the code it is testing.

**FAIL:** any other failing test; research-service below 265; curator below 40; any ruff error;
any file read from outside `/w`.

**New files:** `template-renders.test.ts` (5), `fidelity.test.ts` +8 (20), `templates.test.ts` +4
(12), and `research-curator/meta-judge-prompt.test.ts` (4).

---

## T2 — ACCEPTANCE 1: ten templates, one skeleton, in order

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
deno test -A templates.test.ts
deno eval --ext=ts "import { TEMPLATES, SECTION_NAMES } from './templates.ts'; console.log(TEMPLATES.length); console.table(SECTION_NAMES);"
```

**PASS:** 12 passed. Ten templates; `templates.test.ts` walks every one and asserts the skeleton
sections IN ORDER with that template's own names, the area column, the Source column, and the
"every row carries its [Source N]" rule. The section table is in the `templates.ts` docblock and
in findings L.1.

**What to check by reading, not by running:** each template kept its own audience, hints and tone
(the test asserts they are all distinct, which is weaker than "unchanged" — diff them against
`990a2f5` if you want the stronger claim), and the buyer's guide kept the approved document's
exact headings and column names.

**One rule was added after measuring.** The first re-render of 64ac38cf **dropped the
failure-modes table**: the skeleton listed its sections without saying they were mandatory. Every
prompt now ends "Write EVERY section above, in this order... A section the evidence barely reaches
is written thin; it is never dropped." Try to break THAT: a synthesis with nothing for the table,
a one-line synthesis, a synthesis whose every line is a [GAP].

**FAIL:** fewer than ten templates; a template missing a skeleton section or carrying them out of
order; two templates rendering the same shape; the classifier able to return an id that is not in
TEMPLATES.

---

## T3 — ACCEPTANCE 2: the same skeleton on three real subjects

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
deno test -A template-renders.test.ts
```

Then **read the three documents against the approved one**:

```
documentation/evidence/research-trust-template/approved-document-64ac38cf.md   (what the operator approved)
documentation/evidence/research-trust-template/rendered-64ac38cf-buyers-guide.md
documentation/evidence/research-trust-template/rendered-a337520c-scientific-paper.md
documentation/evidence/research-trust-template/rendered-5ab36fe0-product-comparison.md
```

**PASS:** 5 passed. The buyer's-guide re-render carries the approved document's six headings in
order; the 100 Hz synthesis through scientific-paper gives Findings / Findings by theme; the
comparison synthesis gives Comparison at a glance / Options by criterion with the table AS the
grid — one row per criterion, one column per option, the options' real names in the header.

**Each fixture's header carries the numbers its own run produced**, and they are re-asserted by
the test:

| render | fidelity | grounding diff |
|---|---|---|
| buyers-guide | 33 of 33, 4 corrected, 0 unchecked | clean |
| scientific-paper | 57 of 59, 5 corrected, 2 unchecked | clean |
| product-comparison | 25 of 26, 1 corrected, 1 unchecked | `names ["VCS"]` |

**The acceptance says "keeps every citation" and that is not what a summarising render does.**
Measured: the three re-cite 15 of 16, 13 of 16 and 6 of 7 sources. The test asserts what is true
and what matters — **none invents a citation** and every number resolves — with the ratios
recorded. If you judge that the acceptance requires literal preservation, say so; it would mean
forbidding a report from leaving a source out, which no template asks for.

**FAIL:** a section missing or out of order in any of the three; a citation in a document that its
synthesis does not have; the comparison table not shaped as a grid; a document whose committed
header disagrees with the test.

---

## T4 — ACCEPTANCE 3: the three probes that used to yield nothing

The tester's own X1/X2 from `rt-tester-evidence-report-2.md`:

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "X1"
deno test -A fidelity.test.ts --filter "X2"
deno eval --ext=ts "import { countUnits } from './fidelity.ts';
for (const s of ['## Findings\n\nThe PSU makes it difficult to upgrade. [Source 13]',
                 '## Findings by area\n\nThe unit is impossible to upgrade and always fails within a year.',
                 '## Failure modes by subsystem\n| A | B | Source |\n|---|---|---|\n| Fans | Fans are proprietary | [Source 13] |'])
  console.log(countUnits(s));"
```

**PASS:** 4 and 2 passed (the `X1` filter also catches the two hedge-to-absolute cases the
previous item left in this file); the three probes print **1, 1, 1**. Before this item they were
0, 0, 0.

**How each is closed:** the citation goes back inside its sentence before splitting
(`normaliseCitations`); an uncited sentence in an evidence section is judged against the nearest
synthesis lines, and is UNSUPPORTED when nothing is near it; the word floor is gone from table
cells, where the row-label rule does the work it was doing.

**What is deliberately still unchecked, and why** — attack this if you disagree: an uncited
sentence in the EXECUTIVE SUMMARY, and the `[GAP]` questions in Limitations. The summary
compresses many sources by design and the gap questions are uncited by design; judging either
would replace honest writing with noise. The GROUNDING_RULES forbid an uncited claim; nothing
measures it there.

**FAIL:** any probe yielding 0 or more than 1 unit; a `[GAP]` question altered by the check; an
executive-summary sentence altered.

---

## T5 — ACCEPTANCE 3: the footer's denominator, and the note under the table

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
deno test -A template-renders.test.ts --filter "ACCEPTANCE 3"
deno test -A fidelity.test.ts --filter "K.9"
deno test -A fidelity.test.ts --filter "DELIVERED"
deno test -A fidelity.test.ts --filter "REPORTED, not deleted"
```

**PASS:** 1, 1, 1 and 1 passed. The footer reads
`render checked: N of M, K corrected, U unchecked`, and **M is `countUnits(delivered)`** — a pure
function of the document the reader holds. Recompute it yourself on all three renders; the test
does, and the fixture headers record the same numbers.

This is the direct answer to the tester's X4 and the reviewer's K.10: three artifacts carried four
counts of "the same" document, and the number in the footer was reproducible from none of them.
The first version of this fix produced **"checked 34 of 32"** — numerator from one document,
denominator from another. Both are now counted on the delivered document.

**The note layout (K.9):** a replaced table cell whose verbatim line runs past 25 words becomes
`see Note N below the table`, with the sentence whole beneath the table, citation intact. Nothing
is clipped to fit a column — clipping a grounded sentence is how "makes it difficult for users to
install aftermarket PSUs" becomes "...install aftermarket PSUs", which is the overstatement the
replacement was repairing.

**Superset citations** (X3) are reported in `prose_ungrounded.citations` and never deleted: a row
citing four sources where two contribute nothing is a provenance defect for a reader to see.

**FAIL:** N greater than M anywhere; M not reproducible by `countUnits` on the delivered file; a
replaced cell that breaks its row's column count or loses its Source cell; a clipped sentence; a
citation removed from a document by the engine.

---

## T6 — Parity: the two renderers, byte for byte

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
deno eval --ext=ts "import { renderResult } from './lib.ts'; Deno.writeTextFileSync('/tmp/ts-r.txt', renderResult({ synthesis: 'BODY [Source 1]', cited_sources: [{url:'https://a.example',title:'A'}], needs_status: [{need:'a',status:'answered'},{need:'b',status:'partial'}], search_record: { hits:30, fetched:4, readable:4, relevant:2, ok:0, collapsed:1, offtopic:1, empty:0, errors:0, entity_missing:0, entity_rejected:2, unfloored:1, query_padded:1 }, backstop: 'complete', gaps: ['x'], gap_pass: {added:3,answeredBefore:1,answeredAfter:2,total:2}, render_fidelity: {checked:30,units:34,unchecked:4,stronger:1,unsupported:1,rewritten:1,replaced:1} }));"
```

…and the same record through the Python fallback (`_render`), then `cmp`. Repeat with
`render_fidelity: {checked:0, units:0, unchecked:0, ..., error:'boom'}`.

**PASS:** `cmp` silent both times. The footer reads
`render checked: 30 of 34, 2 corrected, 4 unchecked` and `render check: not run`.
`owui/tools/deep_research.py` is **v1.5.2** and needs a re-paste (D.3).

**FAIL:** any byte of difference; the version not bumped.

---

## T7 — ACCEPTANCE 5: the meta judge's prompt, and the live sweep

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-curator"
deno test -A meta-judge-prompt.test.ts
```

```bash
# read-only sweep, your own export
docker exec openbrain-db psql -U postgres -d openbrain -t -A \
  -c "SELECT text FROM public.claims WHERE status='active';" > /tmp/active.txt
deno eval --ext=ts "import { classifyMetaClaim } from './claims.ts';
const l = Deno.readTextFileSync('/tmp/active.txt').split(/\r?\n/).filter((x) => x.trim());
console.log(l.length, l.filter((x) => classifyMetaClaim(x) === 'meta').length);"
```

**PASS:** 4 passed. The sweep prints about **7 930 active claims, 17 matched (0.21%)** — the exact
numbers move as the KB grows; what must hold is that the matches are all genuinely
source-referential. **Read them** (there are seventeen). I did, and every one is a statement whose
subject IS the source set.

**The three attributed/hedged world claims the judge refused** now classify WORLD; the fourth
refusal stays META **and should**: "Whether the 'Solved!' tag... is unclear, as no solution text is
visible in the provided source content" is a genuine meta question, and the findings said so when
they recorded it. The anchor says "the four hedged/attributed world claims"; three of the four are
world claims. Turning all four into WORLD would be fixing the number, not the defect — disagree
with that reading if you think the anchor meant otherwise.

**How a prompt is tested here:** the mock judge is built FROM `META_JUDGE_SYS` — it extracts the
attribution verbs, the hedge markers and the META examples out of the prompt and classifies with
those alone. Delete an example and the test fails. That is the property worth pinning, because
the prompt is the whole of the instruction the real judge gets.

**FAIL:** any of the three world claims classifying META; the "Solved!" question classifying
WORLD; any of the eight poison claims classifying world through `classifyMetaClaim`; a sweep whose
matches include a plain fact.

---

## T8 — Integration, image, and the run's own record

```bash
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> --network <net> \
  -e DB_HOST=<db> -e DB_PASSWORD=test -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check --no-lock orchestrator.test.ts
docker build --label ai-stack.harness.owner=<you> -t openbrain-research:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-service"
docker build --label ai-stack.harness.owner=<you> -t openbrain-curator:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-template/OB1/integrations/research-curator"
docker run --rm openbrain-research:wt-<you> sh -c "ls /app/*.ts; ls /app/*.test.ts 2>/dev/null || echo NO_TESTS_SHIPPED"
```

**PASS:** the orchestrator suite passes against a real schema; both images build; `/app` carries
the source modules and **no** `*.test.ts`.

**Clean up:** remove the containers, network and images; `reap.ps1 -Report` must show nothing
owned by you.

---

## T9 — Nothing live changed, and nothing was removed without an account of it

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --name-only e0fcd2d..work/research-trust-template
git diff e0fcd2d..work/research-trust-template -- OB1
git status --short
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
docker inspect openbrain-curator  --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
git -C OB1 diff 990a2f5..research-trust-template -- integrations | Select-String -Pattern '^-Deno.test'
```

**PASS:** the parent diff touches only `documentation/` and `owui/tools/deep_research.py`; the OB1
gitlink diff is **EMPTY**; the main checkout is unchanged; both containers untouched (the three
renders are chat completions and the two SELECTs are reads).

**Removed / rewritten tests — reconcile the CASES.** One case was rewritten and none deleted:

| case | file | what happened |
|---|---|---|
| `the answer-first templates lead with an Answer block` | `templates.test.ts` | **REWRITTEN in place** as `every template answers FIRST, in a named executive summary`. The skeleton replaces the `**Answer.**` lead-in with `## Executive summary` — the heading the operator approved — and gives it to all ten instead of two. The property (answer before background) is asserted more widely than before, and the reason is in a comment above the case. |

**PASS:** the `^-Deno.test` search returns only that one line, re-added in the same diff.

**FAIL:** a live container restarted or rebuilt; the gitlink bumped; a test case removed without a
row here.

---

# D. Deploy (AFTER this plan passes — operator or reviewer)

TWO images change: `openbrain-research` (templates, fidelity, renderer) and `openbrain-curator`
(the meta-judge prompt).

### D.1 Open Brain plane — lease `open-brain`

```powershell
.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain -By <you>
docker tag openbrain-research:local openbrain-research:pre-research-trust-template
docker tag openbrain-curator:local  openbrain-curator:pre-research-trust-template
docker build -t openbrain-research:local "D:\Open WebUI\ai-stack\OB1\integrations\research-service"
docker build -t openbrain-curator:local  "D:\Open WebUI\ai-stack\OB1\integrations\research-curator"
docker compose -f OB1\docker\docker-compose.yml up -d openbrain-research openbrain-curator
docker logs --tail 20 openbrain-research; docker logs --tail 20 openbrain-curator
```

**Rollback:** re-tag both `:pre-research-trust-template` back to `:local` and `up -d` again.

### D.2 The 100 Hz dry run — unchanged proof

Run it exactly as the research-trust-core plan's D.2 describes: it must still cite **PMC11955832**
and its three searches must still classify `ok`. It now renders through the scientific-paper
skeleton, so ALSO read the document: Findings, Findings by theme, and one Limitations section.

### D.3 The OWUI tool — re-paste (v1.5.1 → v1.5.2)

Paste `owui/tools/deep_research.py` over the existing tool in Open WebUI (Workspace → Tools →
deep_research), keeping its id, and confirm the header shows `1.5.2`. **Then update the manifest
digest**, which records what is DEPLOYED and is therefore not touched by the branch:

```
tools/deep_research.py,tool,Deep Research,deep_research,<sha256 of the pasted file>
```

```powershell
(Get-FileHash "D:\Open WebUI\ai-stack\owui\tools\deep_research.py" -Algorithm SHA256).Hash.ToLower()
```

**If the paste is skipped:** the async path still delivers correctly (the engine renders
server-side into `result.rendered`), but the synchronous fallback prints the old footer without
the `N of M` denominator.

### D.4 The live OWUI run — the proof

Ask the deployed tool the OptiPlex question again, from a chat. The delivered document must carry
the canonical structure — title stating the finding, Executive summary, What to check in person,
Failure modes by subsystem, What the evidence does not settle, one Limitations section, Sources —
and its footer's **M must equal `countUnits` of the delivered body**:

```bash
# paste the delivered body (without the footer line) into /tmp/body.md
deno eval --ext=ts "import { countUnits } from './fidelity.ts'; console.log(countUnits(Deno.readTextFileSync('/tmp/body.md')));"
```

```sql
-- from the ops plane, SELECT only
SELECT id, status, result->'render_fidelity', result->'prose_ungrounded'
FROM research_jobs ORDER BY created_at DESC LIMIT 1;
```

**The run is the proof; the job row is the audit.** And check the curator log for `META` refusals:
an attributed or hedged world fact refused there is the defect of T7 returning.
