# Findings — the exposure leak in merged code (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U5 · class 2 — every read tool forces the exposure plane
DECISION: `performInspect`, `listForReview`, `performRecallTrace` and
          `performReportUsage` now force the exposure plane server-side from
          `deps.doorExposure`, defaulting to `ops`. A refused access writes an
          `access_refused` audit row (new event type, additive migration, live
          volume applied).
CITED:    U5's Validated by column — an agent reaching for personal-plane data
          "is mechanically stopped AND the attempt is visible in an audit
          record". Found by an adversarial verifier against MERGED code.
REVERT:   Revert the OB1 commit and the gitlink; re-run the same ALTER with the
          original ten event types. No row becomes invalid unless an
          `access_refused` event has been written.

---

## F1 — I proved a boundary on one tool and described the PLANE as contained

`performRecall` forces the plane and `smoke-agent-memory.ps1` proves it end to
end. Three read tools added later, on the same allow-list, did not — and my own
commit message for that slice said "reads are forced by the door".

That sentence was true of `recall` and false of everything I added afterwards.
The verifier found it by calling the two allow-listed read tools the drill never
tried.

**The lesson is narrower than "add a filter":** a boundary proven on one entry
point says nothing about the others, and describing it as a property of the
*plane* is where the over-claim entered. Every read tool is a door.

## F2 — the gateway's forced filter was never going to work here

`openbrain-gateway/app.py`'s `_force_read_filter` injects `metadata_filter`. The
inspect and queue tools have no such field in their zod schemas, so the MCP SDK
strips it before the handler sees it.

A filter applied at a door the callee ignores is not a filter. Belt-and-braces
only works when both are fastened — and the compose comment calling the gateway
filter "belt-and-braces" was accurate about the *intent* and wrong about the
*effect* for these tools.

## F3 — refusal is `not_found`, and that is what makes the audit row load-bearing

"This id exists but you may not see it" confirms the memory to anyone who can
guess an id. So a refused read is indistinguishable from an absent one *to the
caller* — and the only place the difference survives is the audit row. A
genuinely absent memory writes none, or every typo becomes a refusal record and
the signal that matters is buried in noise.

## F4 — still open: the cloud door has no test for this

Verifier 4 noted that `.mcp.json` points every Claude Code / cloud agent at
`127.0.0.1:8061` — the CLOUD door — and that the claim agent-memory thoughts are
excluded there rests on a code comment plus the `search_thoughts` probe in
`smoke_test.py --boundaries`. The four `agent_memory_*` READ tools are denied at
that door by the allow-list, which is tested. What is NOT tested is a cloud-door
read of the *thoughts* an agent memory creates, beyond the one marker probe.

Not claimed as covered. Recorded as the next containment slice.
