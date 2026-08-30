# Evidence-ledger reconciliation — memory-plane PLAN §0.1 (2026-08-30)

The canonical plan lives in the sibling private repo
(`d:\Open WebUI\documentation-plans-ai-stack\implementation-guide\agent-memory-plane\PLAN.md`).
That repo has **exactly one commit ever** (`cab30c8`, "Initial commit"), so its §0.1
"verified assumptions" ledger is precisely as written on 2026-08-25 and has never been
reconciled against anything that shipped since.

§B makes those anchors what U7 judges design changes against, so an anchor that production
has already disproved is not a stale note — it is a wrong yardstick.

Checked here: every anchor that is checkable from this repo. Anchors 9–14 describe
agent-org internals that Phases 2–3 have not touched yet and are recorded as unverified
rather than confirmed.

---

## #6 — "The audit-mirror bug is three bugs" — **WRONG IN TWO OF THREE, AND INCOMPLETE**

The ledger says: wrong path (REST `/capture_thought` exists nowhere), wrong auth (sends the
header the gateway strips), wrong network (`AO_OPENBRAIN_URL=http://openbrain-gateway:8061`,
unresolvable) — "masked by best-effort except + flag default False … It has almost certainly
never written a row."

**a. "Wrong auth" is disproved.** `x-brain-key` is the CORRECT header for
`openbrain-mcp` (`OB1/integrations/kubernetes-deployment/index.ts:2053`), and the gateway
itself *sets* that header on its upstream call (`openbrain-gateway/app.py:142`) rather than
stripping it. The header only looks wrong when the target is the cloud gateway. The real
auth failure in production was a different thing entirely: `AO_OPENBRAIN_KEY` held the
**gateway** key (`gw-` prefix), not `MCP_ACCESS_KEY` — a wrong credential VALUE, not a wrong
header NAME. Fixing what the ledger described would not have fixed production.

**b. "Wrong path" is right about the gateway and misleading about the fix.** The remedy was
not to correct a REST path; it was to change target and protocol — MCP JSON-RPC `tools/call`
against `openbrain-mcp`. An implementer working from the anchor would have gone looking for
the right REST route on a server that has none.

**c. "Wrong network" is CONFIRMED.** `openbrain-gateway:8061` was unresolvable from the
bridge; the live failure text was "Name or service not known".

**d. A FOURTH BUG THE LEDGER NEVER NAMES, and the fatal one for provenance.** The tool
argument is `metadata_extra`, not `metadata` (`index.ts:848`, schema at `:361`). With all
three named bugs fixed, every mirrored row would still have landed with its provenance
silently dropped — a row that exists and cannot be traced, which is worse than no row,
because nothing downstream can tell the difference. Proven the other way after the fix:
thought `13314` carries `metadata.source='agent-org'` and `metadata.kind='validation'`.

**e. "Masked by … flag default False" describes the CODE, not production.**
`config.py:336` does default to `False` — and `agent-org/docker/.env:27` has had
`AO_OPENBRAIN_MIRROR_ENABLED=true` in production. The mirror was not dormant. It was
**enabled and failing, silently, live**. "Almost certainly never written a row" is true in
outcome and wrong in mechanism, and the mechanism is the part that matters: *off* means
nothing was lost; *failing* means 26 safety-critical events (2026-07-06 → 2026-08-24 — kill
switches, effort freezes, concerns, operator decisions) accumulated with no provenance. They
were replayed on operator instruction, each stamped `backfilled: true` with its original
`event_ts`.

I made the same mistake the ledger did, in the same direction: I asserted the mirror was off
from the code default and the compose default without opening the production `.env`.

---

## #8 — "88 files / 723 tests" — **STALE**

`agent-org/agent-bridge/tests/` now holds **91** files, and the acceptance baseline for this
effort was pinned by the operator at **731** tests, not 723. The rest of the anchor (pytest
+ pytest-asyncio auto mode, file-backed SQLite fixture, injectable `self.transport`,
`_ADDITIVE_COLUMNS` for new columns) is still accurate.

## #15 — "Gates a change must pass" — **INCOMPLETE, and growing**

The anchor lists ruff F+E9, and pre-commit = secrets / line-endings / gateway-routing /
compose+ps1 structural. Since then the gate set has grown, and a change that does not know
about them will be refused without knowing why:

- **hook attestation**, and it now covers **merge commits** — clean merges run
  `.githooks/pre-merge-commit`, gated on the hook existing at the fork point;
- **`.githooks/commit-msg`** — a commit staging a gitlink may not name a SHA that resolves
  nowhere;
- **strict-JSON validation** of staged `.json`, deliberately using Python's parser because
  PowerShell's accepts a raw newline inside a string and this repo has two readers for
  `harness.config.json`.

Also worth folding in: SERVICE-LIFECYCLE rows 2/7/10 apply even with no new container (the
anchor says this) — and the full ADD checklist applies when there is one.

---

## Confirmed as written

| # | Anchor | Evidence it still holds |
|---|---|---|
| 1 | schema applies cleanly to the live DB | Applied and verified by query; `PROMOTION-RUNBOOK.md` records the run. |
| 2 | no vector/dimension conflict | Recall rides `thoughts.embedding vector(1024)` via `thought_id`; the sidecar tables still carry no embedding column. |
| 3 | new SQL reaches the live DB in TWO places | Held for every migration since, and the offline harness now derives the chain from compose and checks both directions. |
| 4 | agent-bridge can reach `http://openbrain-mcp:8000` | Proven in production — this is the lane the mirror now uses. |
| 5 | the cloud gateway is not viable for the bridge | Confirmed by the same failure that disproved #6c. |
| 7 | MCP tools go in `index.ts`, 12k result cap | Where `registerAgentMemory` is called from. |

## Not verified

Anchors **9–14** (brief-injection seams, brief token budget, outcome chokepoint, promotion
seam, ao-worker expertise minting, claude-sessions hook points) describe agent-org internals
that Phases 2–3 have not reached. They are recorded as *unverified*, not as confirmed — an
anchor nobody has tested is not evidence, and §B would otherwise let U7 judge against it.

---

## What to do with this

These corrections belong in the canonical PLAN's §0.1, in the sibling repo — that is the
document §B points U7 at, and it is the one that has never been updated. This file is the
reconciliation, not the fix; the ledger is not mine to rewrite.
