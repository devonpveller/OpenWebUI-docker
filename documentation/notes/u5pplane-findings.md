# U5 — personal-plane exclusion, verified end to end (findings)

Branch `work/u5pplane`, 2026-08-30. Item: dark-factory-unification PLAN §2, U5, the
personal-plane half of its *Validated by* column ("an agent instructed to bypass hooks /
reach personal-plane data is mechanically stopped and the attempt is visible in an audit
record"). The hook-bypass half already exists as `scripts/checks/check-hook-attestation.ps1`
and was not touched.

Everything below was produced by a command that was run, not by reading. The commands are
named next to each claim.

---

## What already existed, and what it did NOT cover

`scripts/checks/smoke-agent-memory.ps1` (run: `.\scripts\checks\smoke-agent-memory.ps1`,
exit 0, 22 checks) already proves, against a real server and a throwaway database:

- a tainted write claiming `exposure:'ops'` is stamped `personal` anyway;
- PII content is stored and demoted, never rejected;
- the exposure label is mirrored onto the linked `thoughts` row;
- personal-plane memories are absent from a **default** recall.

It does **not** cover any of:

1. a caller that explicitly names the personal plane in a recall (`exposure: ["personal"]`);
2. the ops door at all — the smoke script speaks only to the server's REST twin;
3. the thoughts lane as an alternative route to the same content;
4. **any audit record of a refused or filtered access.** This was the real gap and it is F1.

---

## F1 — a refused personal-plane access left NO audit record (CLOSED)

Verified before the change by reading the read path
(`OB1/integrations/kubernetes-deployment/agent-memory.ts`, `performRecall`): the caller's
`exposure` was overwritten from `deps.doorExposure` and then **discarded**. The recall wrote
an `agent_memory_recall_traces` row whose `request_payload` was `{limit, include_unconfirmed}`
— no exposure on either side of the decision. So an agent that asked to read the personal
plane and an agent that asked for nothing produced byte-identical records, and the question
"did anything reach for the personal plane?" had no answer anywhere in the database.

Worse, `agent_memory_audit_events.event_type` has listed `'recall_requested'` since the table
was created (`OB1/docker/init-agent-memory.sql:227`) and **nothing on this server had ever
written one** (`grep -rn "recall_requested" OB1/` returns the schema, the sibling
`integrations/agent-memory-api/index.ts:408` — which is not deployed, it is absent from
`OB1/docker/docker-compose.yml` — and tests). The read path left operational traces and no
governance events at all.

**Closed by** `performRecall` now recording both sides of the decision in the trace payload
and writing a `recall_requested` audit row carrying `requested_exposure`,
`enforced_exposure`, `door_exposure` and `exposure_override_denied`. The decision itself is
a pure function, `decideRecallExposure`, in `agent-memory-policy.ts`.

Proven RED then GREEN at unit level:

- `deno test --allow-all` in `OB1/integrations/kubernetes-deployment` → 115 passed (was 104).
- With `exposure: exposureDecision.enforced` reverted to honour the caller → 1 failed
  ("the widening attempt is refused in the SQL, not only recorded").
- With the audit INSERT deleted → 3 failed (the flag test, the trace-link test, and the
  event-exists test).
- Both neuterings reverted → 115 passed again.

## F2 — a tool denied at the ops door left NO record either (CLOSED)

`openbrain-gateway/app.py` returned a JSON-RPC `-32601` for a tool outside the allow-list and
did nothing else: no line, no row, nothing. An agent probing the door for `search_thoughts`
was invisible.

**Closed by** an audit emitter in `app.py`: one JSON line per governance-relevant decision
(`tool_denied`, `read_filter_override`, `exposure_override_attempt`), on stdout, which compose
retains (`json-file`, 10m × 3). It is deliberately **not** the durable table — a denied call
never reaches openbrain-mcp, so there is no connection to write a row on. Values are
truncated to 64 chars and allowlisted to tool names, field names and plane labels; no content
and no argument blob is ever copied into it.

Byte-for-byte cloud-door behaviour is unchanged: `python openbrain-gateway/smoke_test.py
--defaults` → "cloud defaults preserved byte-for-byte".

## F3 — the drill, and its RED proofs

`scripts/checks/drill-personal-plane-exclusion.ps1`. Run:
`.\scripts\checks\drill-personal-plane-exclusion.ps1` → exit 0, 26 checks, no FAILs.

It plants a **synthetic** personal-plane record (`tainted=true`) plus an ops-plane control on
a throwaway database built from the real initdb chain, and attacks from the three positions an
agent actually occupies:

| Attack | Lane | Stopped by | Recorded in |
|---|---|---|---|
| 1 | internal REST (`/agent-memory/recall`, raw body) | `performRecall` forcing the door's plane | durable row, `exposure_override_denied=true` |
| 2 | ops door, MCP `agent_memory_recall` | the tool's zod schema (no `exposure` field) + the same forcing | gateway `exposure_override_attempt` line |
| 3 | ops door, MCP `search_thoughts` | the allow-list (`-32601`) | gateway `tool_denied` line |

Every green is paired with a proof it could have failed:

- the ops **control** must come back on the same query, or "stopped" would just mean "nothing
  matched" (and in the first run it caught exactly that — see F6);
- an ordinary recall must produce an **unflagged** audit row, so the flag is shown to
  discriminate rather than being a constant;
- **RED A**: a scratch copy of the server with one asserted line changed (the door no longer
  overrides the caller) is built as `openbrain-mcp-server:drill-red` and run against the same
  database — the personal fixture **is** returned. The repo tree is never weakened; the patch
  lives in `%TEMP%\pp-drill-red-src` and the drill refuses to build a "red" image if its
  anchor does not match exactly once.
- **RED B**: the ops door's allow-list is widened by env to include `search_thoughts`, and
  then its forced read filter is pointed at the personal plane — the fixture **is** readable.

The ops door's policy is **derived from `OB1/docker/docker-compose.yml`**, not restated in the
drill, so widening the real allow-list cannot leave the drill passing.

## F4 — a documented guarantee that was wrong (CORRECTED)

`documentation/implementation-guide/agent-memory-plane/PLAN.md` said the exposure label is
"mirrored onto the linked thought **so `search_thoughts` enforces the same boundary**".
`search_thoughts` enforces nothing of the kind: its SQL is similarity + an optional
caller-supplied `metadata_filter` and it has no exposure logic
(`OB1/integrations/kubernetes-deployment/index.ts:497`).

I initially took that to mean the mirror was decorative and wrote the drill's RED B expecting
that allowing `search_thoughts` at the ops door would leak the fixture. **It did not** — the
run returned only the ops control. The reason is the door's *second* guard: `_force_read_filter`
injects `metadata_filter={exposure:'ops'}`, and `search_thoughts` **does** honour
`metadata_filter` (`metadata @> $4::jsonb`), matching the mirrored label. So the mirror is the
label and the door's forced filter is the enforcement — genuine defence in depth, and better
than my hypothesis, not worse.

Both the doc line and the drill now say this precisely, and the drill asserts the two guards
separately (allow-list off → still held; allow-list off **and** filter flipped → leaked).

## F5 — the forced read filter is inert for `agent_memory_recall`, load-bearing for `search_thoughts`

`OB1/docker/docker-compose.yml` sets `GATEWAY_READ_FILTER_FIELD: exposure` on the ops door and
calls it "belt-and-braces". That is exactly right for `agent_memory_recall` and understated for
`search_thoughts`:

- `grep -n "metadata_filter" OB1/integrations/kubernetes-deployment/agent-memory*.ts` → **no
  matches**. `RECALL_SCHEMA` has no `metadata_filter` and no `exposure` field
  (`agent-memory-tools.ts:69`), so the injected filter cannot reach any SQL on that tool and
  the recall's boundary is entirely `performRecall`'s server-side forcing.
- for `search_thoughts` the same injected filter *is* the boundary (F4).

Consequence worth keeping in mind: on the MCP lane a caller's `exposure` argument is stripped
by the tool's own schema validation before `performRecall` sees it, so the **durable** row for
that call records `requested_exposure: null` and the **gateway** line is what makes the attempt
visible. The drill asserts this division explicitly (the flagged-durable-row count stays at 1
after the MCP probe) rather than leaving it implied.

## F6 — a drill that passed while every request was rejected (fixed in the drill)

First run: ATTACK 2 reported "STOPPED — the personal fixture is not in the ops door's response"
while the response body was `{"error":"unauthorized"}`. Cause: `Get-OpsGatewayEnv` scraped
`GATEWAY_*` keys out of compose and swept up `GATEWAY_KEY: ${OPS_GATEWAY_KEY:?...}`, handing the
container the literal unexpanded placeholder. An absent fixture is only evidence if the call
succeeded. The drill now excludes `GATEWAY_KEY` (a secret reference, not policy) and checks the
**control first**, failing loudly if the call did not run.

---

## Open — verified, deliberately not fixed here

- **The OB1 gitlink this branch bumps points at a commit that is not yet on the OB1 remote.**
  `git -C OB1 branch -r --contains <sha>` will be empty until someone runs
  `git -C OB1 push origin work/u5-personal-plane-audit`. CLAUDE.md requires the push to happen
  **before** the parent merge lands, or a fresh `--recurse-submodules` clone breaks. This
  session was instructed not to push; whoever merges must do it.
- **`documentation/implementation-guide/agent-memory-plane/PLAN.md`'s gate table is stale.**
  It says 1.2 is "PARTIAL — 2 of 7 tools" (all seven are registered in
  `agent-memory.ts` `registerAgentMemory`) and 1.4 is "NOT MET — built the wrong thing,
  reverted" (`openbrain-ops-gateway` is in `OB1/docker/docker-compose.yml:222` with
  `GATEWAY_PROFILE: ops` and its own key). Not this item's file to rewrite; flagged so the next
  reader does not trust it.
- **The drill leaves three images behind** (`openbrain-mcp-server:drill`, `:drill-red`,
  `openbrain-gateway:drill`) so re-runs are cache-warm. Deliberate. None is `:local`.
- **`promote_exposure` is still absent from the schema's review-action CHECK**, so a memory
  demoted to `personal` cannot be elevated. Pre-existing, already recorded in DECISIONS.md.

---

## DECISIONS entries to append

## 2026-08-30 · U5 · class 2 — the missing half of U5 was the AUDIT, and it is now two records
DECISION: U5's column is "mechanically stopped AND the attempt is visible in an audit
          record". The stopping was already built and already proven. The visibility was
          absent on BOTH lanes, and the two lanes needed different answers:
          (a) a recall that names another plane now writes a durable row -
          `agent_memory_audit_events(event_type='recall_requested')` with
          `requested_exposure` / `enforced_exposure` / `door_exposure` /
          `exposure_override_denied`, plus the same fields on the recall trace;
          (b) a tool DENIED at the ops door never reaches the server, so there is no
          connection to write a row on - it emits a structured audit line on the
          gateway's stdout instead (`tool_denied`, `exposure_override_attempt`,
          `read_filter_override`), which compose retains at 10m x 3.
CITED:    §C.2 class 2 (a discovered gap; the option chosen is the most reversible and
          reuses existing surface). `recall_requested` was ALREADY in the schema's
          event_type CHECK and had no writer, so this needed no migration - the durable
          half is additive code against a table that was waiting for it.
WHY NOT:  a durable row for the gateway denial was rejected: the gateway holds no database
          credential and the denied call never reaches openbrain-mcp. Inventing a
          write-back endpoint for it would be new attack surface to record an attack.
REVERT:   revert the OB1 commit + the gitlink (the audit INSERT and decideRecallExposure
          are additive; no schema change to unwind), and revert the `_audit` block in
          `openbrain-gateway/app.py`. The drill fails afterwards, loudly, which is the
          intended behaviour of removing the thing it checks.
EVIDENCE: `scripts/checks/drill-personal-plane-exclusion.ps1` exit 0 (26 checks);
          `deno test --allow-all` 115 passed; `smoke-agent-memory.ps1` exit 0 (no
          regression); `python openbrain-gateway/smoke_test.py --defaults` byte-for-byte.

## 2026-08-30 · U5 · class 2 — the drill derives the ops-door policy from compose
DECISION: `drill-personal-plane-exclusion.ps1` parses `openbrain-ops-gateway`'s
          `GATEWAY_*` environment out of `OB1/docker/docker-compose.yml` rather than
          carrying its own copy of the allow-list, and excludes `GATEWAY_KEY` (a secret
          REFERENCE, not policy).
CITED:    §C.2 class 2 + the house pattern in `scripts/checks/lib/ob-initdb.ps1`, which
          derives the initdb chain from compose for the same reason: a second copy goes
          stale and the check keeps passing against its own opinion.
REVERT:   replace `Get-OpsGatewayEnv` with a literal hashtable.

## 2026-08-30 · U5 · CORRECTION — "search_thoughts enforces the boundary" was wrong, and the truth is better
DECISION: Corrected `documentation/implementation-guide/agent-memory-plane/PLAN.md`, which
          said the mirrored exposure label means `search_thoughts` "enforces the same
          boundary". It does not: that tool has no exposure logic (index.ts:497). The
          enforcement is the ops door's forced `metadata_filter`, which `search_thoughts`
          DOES honour and which matches the mirrored label. Mirror = label; door = gate.
CITED:    §C.7 (an executable check, not prose, decides) and §0 A9 (verify before relaying).
          Found by writing the drill's RED phase to expect a leak and NOT getting one -
          the prose and my own hypothesis were both wrong in the same direction.
REVERT:   restore the previous sentence; the drill's two separate sub-assertions would
          then contradict the document.
