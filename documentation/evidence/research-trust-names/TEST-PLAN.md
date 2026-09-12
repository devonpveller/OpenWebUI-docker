# TEST-PLAN — harness item `research-trust-names`

**Branch:** `work/research-trust-names` (parent, base `ea6a2e5`) + `research-trust-names`
(OB1 submodule, base `e28c974`, NOT pushed).
**Developer:** `wt-research-trust-names`. **Anchor:**
`.\scripts\agent-harness\queue.ps1 -Show -Id research-trust-names`, copy committed at
`documentation/implementation-guide/research-engine-for-OB/anchor-research-trust-names.json`.

`renderGroundingDiff` has been REPORTING invented names for three items — ATX and SFX, then BSOD,
then OEM, and "non-OEM" in live run a205845d — into a field on a job row that the colleague the
report is written for never opens. This item makes the measurement act on what it finds.

Read `documentation/notes/research-trust-findings.md` section **N** first.

---

## Preconditions

The developer's worktree is `D:\Open WebUI\ai-stack\.claude\worktrees\wt-research-trust-names`.
Cases T1–T8 are **read-only executions**. Do not `git add`, `git commit` or edit anything.

```powershell
cd "D:\Open WebUI\ai-stack"
git diff --stat ea6a2e5..work/research-trust-names
git -C OB1 diff --stat e28c974..research-trust-names
```

**What ran against anything live:** the fidelity check (including its judge and rewriter) over the
three committed renders, through the deployed LiteLLM path, relayed by
`docker exec -i openbrain-research deno eval`. No container was restarted, rebuilt, retagged or
reconfigured; no database was written; no job was enqueued. **Leases:** T1–T8 need none; section D
needs **open-brain**.

---

## T1 — Unit suites and lint, with ONLY the service directory present

```bash
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service:/w:ro" \
  -w /w denoland/deno:2.3.3 deno test -A --no-lock
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-curator" && deno test -A
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names" && ruff check .
```

**PASS:** research-service **`301 passed | 1 failed`** (the anchor requires at least 273);
research-curator `40 passed | 0 failed`; ruff `All checks passed!`. The single failure must be
`./orchestrator.test.ts (uncaught error)` — postgres at module load, T7 runs it properly.

**FAIL:** any other failing test; research-service below 301; curator below 40; any ruff error;
any file read from outside `/w`.

---

## T2 — ACCEPTANCE 1: the gate, and the exemption

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "ACCEPTANCE: an abbreviation"
deno test -A fidelity.test.ts --filter "ACCEPTANCE: a unit using an unearned name"
deno test -A fidelity.test.ts --filter "the gate BLOCKS the names"
```

**PASS:** 1, 1 and 1 passed.

- **The gate fires before the judge.** In the second case the judge answers SAME to everything and
  the unit is corrected anyway: a name no source uses is a fact no source supports, and the judge
  was asked about the claim, not the vocabulary.
- **The exemption is an EXPANSION MATCH** (`grounding.ts` `expansionMatch`): a name is earned when
  the evidence writes it as a whole word, or when consecutive words on one line have initials that
  spell it. BSOD is earned by "Blue Screen of Death"; HTC is not earned by anything, and is
  blocked. There is no list of known abbreviations, and there should not be — four items in this
  workstream have failed on a hand-written list of surface strings.
- **The two documents kept as RECORDS are not edited to make the suite green**: the operator's
  approved document (ESR, HDD) and the attempt-1 ATX/SFX render. The third case asserts the gate
  blocks their names instead.

**Try to break it:** an acronym whose expansion is in a [GAP] line only (it must still be
blocked); a name that is a common English word; a name inside a code span; an expansion spanning
two lines (deliberately not matched — a phrase does not span a newline, and letting it would make
any long document contain every acronym).

**ONE reference for the gate and the reporter.** Attempt 2 passed `query=""` into the gate and the
real query into the reader-facing reporter, so the gate blocked `UI` and `TFVC` — words of the
QUESTION THE PERSON ASKED — that the report would never have flagged, and the footer's
`names: N blocked` could disagree with `prose_ungrounded.names` by construction. A name the person
asked about is not a name the report invented.

```bash
deno test -A fidelity.test.ts --filter "USER'S OWN QUESTION"
deno test -A fidelity.test.ts --filter "cannot disagree"
```

**PASS:** 1 and 1 passed. The recorded queries are now fields on the fixtures themselves
(`_query_provenance` says where each came from), so a test that omits the query is testing a
different check.

**FAIL:** a name the evidence never uses surviving in a corrected document; BSOD blocked; an
acronym with no expansion passed; either record document edited; the gate and the reporter reading
different references.

---

## T3 — The [GAP] decision, and what it costs

The judgement this item had to make, stated so you can reject it: **a [GAP] line is the
synthesizer's account of what it could not find, not a source's**, so the names check reads only
the GROUNDED lines as evidence and the limitations list is gated like any other section.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "the synthesizer's words"
deno test -A fidelity.test.ts --filter "an open question is rewritten"
```

**PASS:** 1 and 1 passed. Live run a205845d put "non-OEM" in a reader's limitations list because a
[GAP] line said "a non-OEM fan"; the same shape is in the fixtures (ESR, HDD, TFVC, UI).

**Two rules protect the questions themselves**, and both are asserted:

- an open question is **rewritten or left alone**, never answered with a grounded line;
- **"blocked" is measured on the delivered document** — the names are recomputed after the
  corrections, and a rewrite that failed is not a block. A surviving name stays visible in
  `prose_ungrounded.names`.

**Numbers and URLs keep the whole synthesis as their reference**, deliberately: a figure inside a
[GAP] question is a question, not an assertion.

### T3b — POLARITY: the defect attempt 2 shipped

Attempt 2 passed this case and shipped a polarity inversion. Under "What the evidence does not
settle", sentences saying what the sources do NOT establish were replaced by verbatim grounded
lines asserting what they DO — twice in the scientific paper with **no flagged name in the unit at
all**, so this is the fidelity judge and its fallback, live since research-trust-report, not the
names gate:

| the sentence that was removed | what replaced it |
|---|---|
| "the evidence does not describe the specific Azure DevOps components…" | an [INFERRED] line: "The pattern seen in GitLab … is analogous to what Azure DevOps does" — leaving the next sentence's "The sources ALSO do not address…" with no antecedent |
| "The EEG and GVS data … do not trace the resolution pathway." | a background claim about what causes the conflict state |
| "It is unclear whether the effects are additive, redundant, or potentially antagonistic." | a near-duplicate of the sentence before it, three possibilities flattened to one |

**The rule now — and nothing lexical decides it.** *A correction may never flip what a sentence
claims about the EVIDENCE. Attempts 2, 3 and 4 each tried to decide that with words — evidence
nouns, then a negation list, then absence-by-default with a narrow world-marker — and a tester
broke all three on delivered documents. The words are out of the decision path. Before ANY
correction is applied, rewrite or verbatim, in any section, the FLIP JUDGE is shown the original
and the proposed correction (with the section heading, and the sentence standing beside it) and
answers one question. FLIP, an error, or an answer that cannot be parsed leaves the unit exactly
as written. KEEP applies it. There is no lexical fast path: the first one built for this attempt
waved "Scarcely any of the sources quantify the failure rate" straight through, because neither it
nor the [SOURCED] line that would have replaced it carries a negation token.*

**The judge's prompt** (`FLIP_JUDGE_SYS`, `fidelity.ts`) draws one distinction and draws it with
both examples, because the two sides of it look identical grammatically:

| | ORIGINAL | CORRECTION | verdict |
|---|---|---|---|
| about the EVIDENCE | "Scarcely any of the sources quantify the failure rate." | "The failure rate is quantified at three percent across the reported fleet." | **FLIP** — the original says the evidence is thin, the correction says it is settled |
| about the WORLD | "The PSU is not proprietary." | "The SFF uses a proprietary power supply and a proprietary power connector." | **KEEP** — the report states a fact the sources contradict, and repairing that is what this module is for |

It also answers a second question in the same call: does the correction only repeat the sentence
BESIDE it (`"duplicate": true`)? The overlap test that exists to catch that shares 6 content words
of 10 with the pair it missed on the 100 Hz render, and the pair reappeared the moment the judge
stopped refusing it for polarity.

**One exception, stated because it is the arguable one:** a unit carrying a NAME the evidence never
uses is corrected even when the judge calls the correction a restatement of its neighbour. A
reader seeing a point twice is a smaller harm than an invented name shipping, and a gate a
readability guard can talk out of firing is not a gate. **Polarity has no such exception** — a
flip is never applied to get rid of a name, and when that happens the name is NOT counted as
blocked.

**Every condemned unit that ends uncorrected is counted exactly once** — `polarity_skipped`
(the judge refused), `duplicate_skipped` (the correction was already in the document), or
`no_candidate` (nothing existed to correct it with) — comes OUT of the checked count, and appears
in the footer's `U unchecked` with its reason:

```
render checked: 43 of 44, 3 corrected, 1 unchecked · left as written: 1 (0 would invert, 1 already said, 0 nothing to cite) · names: 3 blocked
```

Attempt 4 printed nothing at all on the case where nothing could be corrected: `checked: 0` meant
the whole clause was skipped, so the disclosure went silent exactly where the reader needed it.
An EDIT that landed is counted too, even when the unit stays condemned — the buyer's guide changed
three lines and reported "2 corrected" because the count was booked on the way out of a path a
refusal never reaches.

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "FLIP"
deno test -A fidelity.test.ts --filter "POLARITY"
deno test -A fidelity.test.ts --filter "duplication is not a polarity refusal"
deno test -A fidelity.test.ts --filter "NEAR-duplicate"
```

**PASS:** 7, 9, 1 and 1 passed. The seven FLIP cases are: the prompt carries both examples and
every unusable answer is a refusal; **the five sentences the tester landed plus this plan's own
escape-hatch example, end to end through a judge answering FLIP**; **the over-protection side —
"does not support DDR4-3200", "The PSU is not proprietary", and the same claim in a table cell,
corrected by a judge answering KEEP**; **the condemned sentence nothing could correct ("No bent
pins were reported" against a synthesis with no absence line), counted and disclosed**; a judge
that ERRORS refusing; the 100 Hz stutter refused as a duplication by the judge; and the name that
beats a stutter. The nine POLARITY cases are the attempt-2/3/4 pins, which still hold: the
lexicon survives only as a recorded classification, never as a decision.

**The five sentences that LANDED, all now refused:**

| sentence | why every lexicon missed it |
|---|---|
| "Scarcely any of the sources quantify the failure rate." | no negation token |
| "Whether 100 Hz helps is far from settled." | no negation token |
| "The resolution pathway is hardly documented anywhere." | no negation token |
| "Does any provided source trace the resolution pathway?" | an absence written as a question |
| "The readings do not capture the resolution pathway." | cleared the denial gate, then the world-marker waved it through |
| "The trace does not include the fault." | this plan's own attack example, same escape hatch |

**Try to break it — from BOTH sides.** The judge is a model, so attack it as one: a sentence whose
subject is ambiguous between the world and the evidence ("the manual is wrong about the PSU"); a
correction that keeps the denial but changes what is denied; a flip inside a table cell; an
absence in a language the prompt never mentions. And attack the OTHER side just as hard: a world
negative that contradicts its own cited line and must still be corrected ("the unit does not
support DDR4-3200" against a line saying it takes DDR4-2400), a false absence in a section heading
that says otherwise, a named unit whose only repair looks like its neighbour. Over-protection is
now a failure, not a safe default.

**FAIL:** any corrected unit that asserts what the original denied, or denies what it asserted;
any grounded [SOURCED]/[INFERRED] line pasted over a sentence about what the evidence lacks; **a
false world negative left as written that contradicts its own cited synthesis lines**; a `[GAP]`
or `[SOURCED]` tag reaching the reader; a condemned unit that ends uncorrected and is not counted
in exactly one of the three reasons; a footer that reports fewer corrections than the document has
changed lines; a name left in the document while the footer calls it blocked.

**FAIL (T3 as a whole):** a grounded line pasted over an open question; a name counted as blocked
while still in the document; a [GAP] question deleted; **any polarity inversion**.

---

## T4 — ACCEPTANCE 2: per-sentence correction inside a coarse unit

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "only the failing sentence"
deno test -A fidelity.test.ts --filter "whose every sentence fails"
```

**PASS:** 1 and 1 passed. The tester's sample — `The PSU fails with a brief green LED. [Source 7]
The connector is proprietary [Source 13].` — is ONE coarse unit (the splitter will not break
before a citation, which is what keeps "…to upgrade. [Source 13]" whole). With a judge condemning
only the second sentence, the first is byte-identical and only the second is replaced. A span
whose every sentence fails is still corrected whole, so the fallback pastes one grounded line and
not two.

**Seen on the real document** — the OEM sentence lived in exactly that shape:

```
BEFORE  ...is not confirmed by any source. The proprietary connector makes aftermarket
        substitution difficult [Source 13], but the availability of an OEM replacement part is
        left open.
AFTER   ...is not confirmed by any source. The proprietary connector makes aftermarket
        substitution difficult [Source 13], but the availability of a replacement part is
        left open.
```

Attempts 2-4 replaced that sentence with the grounded [Source 13] line instead. Under the judge it
is the REWRITE that stands: asked about the verbatim paste, the live judge called it a restatement
of the sentence beside it ("Whether Dell sells a direct-replacement 180 W SFF PSU separately is not
confirmed by any source") — which it is. The name still goes, because a name the evidence never
uses is not something a readability verdict may keep; the sentence keeps its own shape.

**FAIL:** a sibling sentence changed; a span split in a way that separates a citation from the
sentence it closes.

---

## T5 — ACCEPTANCE 4: the four INVARIANTS still hold

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "INVARIANT"
deno test -A template-renders.test.ts --filter "INVARIANT"
```

**PASS:** 3 and 1 passed — byte-identity, idempotence, the welding shapes, and the committed
renders unchanged.

**Read this, because the fixtures moved.** The three committed renders were regenerated by
applying the gate to the documents `research-trust-template` committed — not by re-rendering — so
`git -C OB1 diff e28c974..research-trust-names -- integrations/research-service/fixtures` shows
exactly what the gate changed. Their headers carry the record, and the invariants are asserted on
the regenerated files: a document the gate has already cleaned has nothing left to correct.

Each document is now paired with **its own** synthesis in those tests. Pairing all three with one
synthesis was harmless while the check only judged claims; with a names gate it makes every name
in a document unearned, and the checker "corrects" a document it should never have been shown.

**The headers now attribute EVERY changed line.** Attempt 2's headers said "exactly what the gate
changed and nothing else", and that was false for 3 of 11 hunks — two of them the polarity
inversions above. Each header carries a table: line, which half of the check changed it (gate /
judge), the polarity before and after, and the text. Check it against

```powershell
git -C OB1 diff e28c974..research-trust-names -- integrations/research-service/fixtures
```

**FAIL:** any invariant failing; a fixture whose header disagrees with what the check produces; a
changed line missing from its header's table; a row whose polarity column says a flip happened.

---

## T6 — The footer, and renderer parity

```bash
cd "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
deno test -A fidelity.test.ts --filter "how many names were blocked"
deno eval --ext=ts "import { renderResult } from './lib.ts'; Deno.writeTextFileSync('/tmp/ts.txt', renderResult({ synthesis: 'BODY [Source 1]', cited_sources: [{url:'https://a.example',title:'A'}], needs_status: [{need:'a',status:'answered'}], search_record: { hits:10, fetched:4, readable:4, relevant:2, ok:1, collapsed:0, offtopic:0, empty:0, errors:0 }, backstop: 'complete', render_fidelity: {checked:44,units:44,unchecked:0,rewritten:2,replaced:1,names_blocked:['ESR','HDD','OEM']} }));"
```

…and the same record through `deep_research.py` `_render`, then `cmp`. Repeat with
`names_blocked: []`, and with the record of a run that could correct NOTHING:
`{checked:0, units:1, unchecked:1, rewritten:0, replaced:0, polarity_skipped:1,
duplicate_skipped:0, no_candidate:0, names_blocked:[]}`.

**PASS:** `cmp` silent every time. With names:
`render checked: 44 of 44, 3 corrected, 0 unchecked · names: 3 blocked`. Without: no `names:`
clause at all — a counter that says "0 blocked" on every report teaches the reader to skip the
line. With nothing correctable:
`render checked: 0 of 1, 0 corrected, 1 unchecked · left as written: 1 (1 would invert, 0 already
said, 0 nothing to cite)` — attempt 4 printed NO fidelity clause at all for that record, because
the whole block was gated on `checked > 0`. `owui/tools/deep_research.py` is **v1.5.5** and needs
a re-paste (D.3).

**FAIL:** any byte of difference; the `names:` clause printed at zero; no clause on a run that
left something as written; the version not bumped.

---

## T7 — Integration, image, and the run's own record

```bash
MSYS_NO_PATHCONV=1 docker run --rm --label ai-stack.harness.owner=<you> --network <net> \
  -e DB_HOST=<db> -e DB_PASSWORD=test -e REUSE_MAX_DISTANCE=1.1 -e KB_SOURCES_MAX_DISTANCE=1.1 \
  -v "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service:/app:ro" \
  -w /app denoland/deno:2.3.3 deno test -A --no-check --no-lock orchestrator.test.ts
docker build --label ai-stack.harness.owner=<you> -t openbrain-research:wt-<you> \
  "D:/Open WebUI/ai-stack/.claude/worktrees/wt-research-trust-names/OB1/integrations/research-service"
docker run --rm openbrain-research:wt-<you> sh -c "ls /app/*.ts; ls /app/*.test.ts 2>/dev/null || echo NO_TESTS_SHIPPED"
```

**PASS:** the orchestrator suite passes against a real schema; the image builds; `/app` carries the
source modules and **no** `*.test.ts`. **Clean up:** remove the container, network and image;
`reap.ps1 -Report` must show nothing owned by you.

---

## T8 — Nothing live changed, and nothing was removed without an account of it

**Derive the base; do not trust a literal.** Attempt 2's T8 named `ea6a2e5`, which stopped being
this branch's merge-base when the reviewer rebased, so the case reported 20 changed paths and a
moved gitlink and neither was a defect. Compute it:

```powershell
cd "D:\Open WebUI\ai-stack"
$base = git merge-base HEAD work/research-trust-names
git diff --name-only $base..work/research-trust-names
```

**The assertion is about the DEVELOPER'S OWN commits**, not about everything the work line has
moved by since. List them and check what they touch:

```powershell
# the developer's commits on this branch, newest first
git log --format='%h %s' $base..work/research-trust-names
# and what each one changes
git log --format='%h' $base..work/research-trust-names | ForEach-Object { "$_"; git diff --name-only "$_^" "$_" }
```

**PASS:** every commit authored for THIS item touches only `documentation/` and
`owui/tools/deep_research.py`. A commit that is the reviewer's landing commit is identified by its
message and is not the developer's.

**The gitlink expectation, BY STAGE** — this is what attempt 2's T8 got wrong, and the stage is
visible in the log:

| stage | what to check | why |
|---|---|---|
| before the landing commit (what the developer submits) | `git diff $base..HEAD -- OB1` is **EMPTY** | the developer never bumps the pin; the OB1 work sits on a local branch |
| the reviewer's landing commit | **exactly one** commit on this branch changes the gitlink, and its new pin resolves on the OB1 remote | the reviewer pins the submodule as part of landing |

**The second row cannot be checked with a diff from the derived base.** The tester found this:
`git merge-base` returns the landing commit itself, so `$base..HEAD -- OB1` is empty BY
CONSTRUCTION and the row can never fail. Check the branch's HISTORY instead:

```powershell
# every commit on this branch that touches the gitlink, oldest last
$gitlinkCommits = git log --format='%h %s' $base..work/research-trust-names -- OB1
$gitlinkCommits            # expect: none from the developer; one from the reviewer, if landed
# …and for each, the pin it sets must be on the OB1 remote
foreach ($c in ($gitlinkCommits | ForEach-Object { ($_ -split ' ')[0] })) {
  $pin = (git ls-tree $c OB1) -replace '^\S+ \S+ (\S+)\s+OB1$','$1'
  "$c -> $pin"
  git -C OB1 ls-remote origin | Select-String $pin
}
```

**PASS:** at a developer-submitted head, that log is EMPTY. At a landed head it names exactly one
commit, that commit is the reviewer's (identified by its message), and `ls-remote` resolves its
pin. An empty diff proves nothing on its own and is not accepted as evidence for this row.

**The container.** `docker inspect openbrain-research` shows `RestartCount 0` and an image
`openbrain-research:local`. Its `StartedAt` moves when the item is DEPLOYED, which section D
schedules after a pass — so a fresh `StartedAt` at a landed head is the deploy, not a test-time
mutation. What must hold at every stage: **RestartCount 0**, and no image built or retagged by
this plan's cases.

```powershell
docker inspect openbrain-research --format "{{.Config.Image}} {{.State.StartedAt}} {{.RestartCount}}"
git -C OB1 diff e28c974..research-trust-names -- integrations | Select-String -Pattern '^-Deno.test'
```

**PASS:** no removed `Deno.test` line.

**One expectation CHANGED, and it is the item's own subject:**

| case | file | what happened |
|---|---|---|
| `ACCEPTANCE 6: the grounding diff over the re-rendered document` | `report-doc.test.ts` | pinned `names == ["BSOD"]` as the one name that still leaked. BSOD is not a leak — the synthesis writes "Blue Screen of Death" — and the expansion match now says so, so the case pins `[]`. The reason is in a comment above the assertion. |

**FAIL:** a developer commit touching anything outside those paths; a gitlink change at a
developer-submitted head; a landed gitlink whose pin `ls-remote` cannot resolve; `RestartCount`
above 0; a test case removed without a row here.

---

# D. Deploy (AFTER this plan passes — operator or reviewer)

ONE image changes: `openbrain-research`.

### D.1 Open Brain plane — lease `open-brain`

```powershell
.\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain -By <you>
docker tag openbrain-research:local openbrain-research:pre-research-trust-names
docker build -t openbrain-research:local "D:\Open WebUI\ai-stack\OB1\integrations\research-service"
docker compose -f OB1\docker\docker-compose.yml up -d openbrain-research
docker logs --tail 20 openbrain-research
```

**Rollback:** re-tag `:pre-research-trust-names` back to `:local` and `up -d`.

### D.2 The 100 Hz dry run — unchanged proof

Still cites **PMC11955832**, three searches still `ok`. It now also prints `names: K blocked` if
the render used a name the evidence did not.

### D.3 The OWUI tool — re-paste (v1.5.4 → v1.5.5)

Paste `owui/tools/deep_research.py` over the existing tool (Workspace → Tools → deep_research),
keep its id, confirm `1.5.5`, then update the manifest digest, which records what is deployed:

```powershell
(Get-FileHash "D:\Open WebUI\ai-stack\owui\tools\deep_research.py" -Algorithm SHA256).Hash.ToLower()
```

### D.4 The live OWUI run — the proof

Ask the deployed tool the OptiPlex question from a chat, then read the delivered document and the
job row:

```sql
SELECT id, result->'render_fidelity'->'names_blocked', result->'prose_ungrounded'->'names'
FROM research_jobs ORDER BY created_at DESC LIMIT 1;
```

**PASS:** the document's BODY contains no name the grounding diff flags — only expansion-matched
abbreviations survive — and the footer states the blocked count when any were blocked.

**Read the acceptance's wording with this correction.** A name can survive in a LIMITATIONS
QUESTION if both rewrite attempts decline: the engine will not answer an open question with
evidence, and it will not cut words out of someone's question. Such a name is never counted as
blocked and appears in `prose_ungrounded.names`, so the SQL above is where you see it. A surviving
name in the body, or a blocked count that does not match what the document lost, is a failure; a
surviving name in a question, reported, is the documented behaviour.
