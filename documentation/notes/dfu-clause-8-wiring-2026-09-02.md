# Clause 8 wired to the real record (item dfufq, 2026-09-02)

**What this item did:** made `scripts/checks/dfu-done.ps1` clause 8 measure the memory plane's
ACTUAL record against §C.8 clause 8 as it stands after `0ff5650` ("clause 8 ARMED, mirrors U7"),
and proved the probe falsifiable in both directions. It also restored, from the parked bundle
`D:\Open WebUI\_notes\parked-work\aistack-c8plane.bundle` (branch `work/c8plane`, tip `2bf47b1`),
the three check-plane files the clause's evidence depends on.

**Scope:** `scripts/checks/dfu-done.ps1` clause 8 only, plus the restored files below. PLAN.md,
DECISIONS.md and WALKTHROUGH.md were NOT touched. The `queue.ps1 -Resubmit` seam that the parked
branch also carried was deliberately NOT restored — another effort owns that file.

---

## 1. What clause 8 now requires, and what the probe reads

Armed (operator, 2026-09-02, mirroring clause 6/U7), clause 8 is MET when, evidenced by a run:

1. the plane holds memories written by **real efforts as they ran**, not seeded fixtures;
2. `agent_memory_recall_traces` -> `agent_memory_recall_items` -> a `memory_used` audit event
   **on the same trace** shows at least one recall consumed by a **later** effort, with that
   effort's own record citing what it was told;
3. the probe is **non-tautological** — UNMET against an empty fixture with a distinct sentence,
   passing against the real record.

The three probes now map one-to-one onto those bullets.

### Probe 1 — `plane-written-to-by-real-efforts`

The old test was `count(*) > 0`, which cannot tell the plane's contents apart from a fixture
corpus: seventeen of the twenty-one rows on this plane were written in one batch by
`scripts/checks/seed-defect-classes.ps1`, and a probe counting them as evidence that efforts
write to the plane would be measuring its own seeding.

The discriminator is the row's own provenance: a memory counts when it is **not** stamped
`metadata.seeded_by` **and** it names the work that produced it (`task_id`, or
`metadata.task_id` / `metadata.source_item`, which is what the agent-harness write seam stamps
when a tester files a finding). A memory that cannot say which effort produced it is not
evidence of an effort writing as it ran. Measured now: 21 rows, **3** real-effort
(`p2-acceptance-probe`, `u3find`, `watchdog-fix attempt 1 / wt-tester-3 evidence section C item
4`), 17 seeded.

### Probe 2 — `recall-returned-something` (restored from the parked branch, unchanged)

Reads the **item rows**, not `response_policy->>'returned'` — a number the recall writes about
itself. Four traces on this plane claim `returned = 1` while holding zero
`agent_memory_recall_items` rows; the probe reports that disagreement rather than resolving it
in favour of the larger number.

### Probe 3 — `recall-consumed-by-a-later-effort`

Three things on the same `(trace, memory)` pair:

- **a real delivery** — an `agent_memory_recall_items` row with `returned` true, joined to a
  `memory_used` audit event naming the same trace and the same memory. Joined, not asserted.
- **later than the write** — the `memory_used` event is strictly after the memory's
  `created_at`, and the write->recall and write->use intervals are **printed in the verdict**,
  so a short same-session loop is visible on the face of a green rather than hidden inside it.
- **the consuming effort's own record cites BOTH ids.** The trace id is the half that matters
  and the half the parked probe never required. A memory id alone could have been looked up by
  hand; only the trace citation says the work was informed by a *recall*.

The record searched is the **pre-run snapshot** of `DECISIONS.md` and `documentation/notes/*.md`
(normalised — HTML comments and fenced blocks stripped), so the authority cannot discharge the
clause with something the run wrote. Memories are matched by full uuid or by their leading eight
hex characters as a standalone token, because that is how the findings notes actually name them;
**traces are only ever matched in full.**

---

## 2. Both directions, run 2026-09-02 from a clean clone

Full verdicts are in the item's report. In summary:

| direction | verdict | the sentence that distinguishes it |
|---|---|---|
| real record | CLAUSE 8 **MET** | "... distinct (trace, memory) pair(s) are a recall DELIVERY ... cited by BOTH ids in the record the operator reads ..." |
| empty fixture (schema loaded, zero rows) | CLAUSE 8 **UNMET** | "agent_memories is EMPTY - nothing has ever written to the plane, so there is nothing any later effort could have been told" |
| the 2026-08-31 self-seeded loop, reconstructed | CLAUSE 8 **UNMET** | "... PLANTED BY A SEEDING SCRIPT ..." and "... the TRACE that delivered them is cited nowhere - a memory id on its own could have been looked up by hand ..." |

The third direction is the one that mattered. The parked branch's own note recorded that its
greens came from memories the same effort had seeded seventy seconds earlier, reached by a recall
whose query read *"probe test of the report-usage loop"*. That shape was rebuilt row for row in a
throwaway Postgres — seeded memory, that same trace id, its item row, its `memory_used` event —
and run against the **real** repository record. It goes UNMET, on two independent probes. The
gate that kills it is the trace citation: that trace is written down nowhere, so no amount of
database state can discharge the clause with it.

The empty fixture was a throwaway `postgres:16-alpine` container carrying the four tables'
DDL dumped from `openbrain-db` (`pg_dump --schema-only`), on the default bridge — never attached
to an `ai-stack_*` network, and `openbrain-db` itself was only ever read.

---

## 3. The honest caveat, which arming does not erase

The pass carries the caveat **in the verdict sentence itself**, not only in the plan: what is
demonstrated is *the loop wired and run one full cycle*, never unforced compounding. §C.8 clause
8 records why — the strongest signal to date (U5's graph round recalling the *"fixed one, left
the sibling"* class) had that class **named in the send-back brief and typed verbatim into the
recall query**, so the plane CONFIRMED a class already in hand rather than SURFACING one the
effort lacked. `u5graph-findings.md` corrects its own round-2 heading for exactly this reason.
Nothing in this item deletes or softens that, and the probe's green is worded so it cannot be
read as the stronger claim.

**This note deliberately does NOT quote any full trace uuid.** Probe 3 searches
`documentation/notes/*.md`, so a note written by the effort that wired the probe, containing the
ids the probe looks for, would become citation evidence manufactured by the author of the check.
The pass stands on `documentation/notes/u5graph-findings.md`, which was merged on the work line
on 2026-08-31 by a different round; it needs no help from here.

---

## 4. Restored from the parked bundle, and why each

| file | why the clause needs it |
|---|---|
| `scripts/checks/recall-sibling-class.ps1` | the seam that produced the traces probe 3 reads. `u5graph-findings.md` names it by path; without it that merged record points at a script the work line does not contain. |
| `scripts/checks/seed-defect-classes.ps1` | the writer of the seventeen seeded rows, so their provenance is discoverable in the repo rather than only in the database. **Not run by this item** — the rows already exist, and re-seeding would be planting a fixture. |
| `scripts/checks/defect-classes.json` | that writer's payload, provenance-linked to `DECISIONS.md`. |

---

## 5. Open, reported not worked around

- **`agent_memory_recall_items.used` is written by nothing.** `performReportUsage`
  (`agent-memory-tools.ts`) writes the audit event and never updates the item row, which is the
  column the schema designates for this signal. `used IS NOT NULL` is 0 on every row. The probe
  reads the audit event — the strongest record the deployed system emits — and prints the gap in
  every verdict. Closing it is an OB1 change: a push, a gitlink bump and an `openbrain-mcp`
  rebuild.
- **`agent_memory_recall` discards `trace_id` and `memory_id`** (`agent-memory.ts:558-563`), so
  a caller going through the door cannot name what it was told and cannot call
  `agent_memory_report_usage` at all. `recall-sibling-class.ps1` works around this by reading the
  ids back out of the trace tables, and says so on every run. Fixing the item-row defect without
  fixing this one would be class-8 shaped.
- **The plane records no effort identity on consumption.** `actor_label`, `runtime_name` and
  `task_id` are NULL on all 13 `memory_used` events (measured 2026-09-02). So "a LATER EFFORT"
  is evidenced by a later timestamp plus a citation in a findings note that is the consuming
  effort's own record — the probe cannot mechanically prove the consumer is a different agent
  from the writer. What would close it: stamp `actor_label` / `task_id` on the usage report, at
  which point the probe can require writer != consumer instead of printing intervals for a human
  to judge.
- **Four traces claim `returned > 0` with no item row** (FK is `ON DELETE CASCADE` and the
  fixture memories were cleaned up). Reported by probe 2 on every run; not repaired here.
