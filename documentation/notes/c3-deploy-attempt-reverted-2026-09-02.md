# C3 deployment ATTEMPTED and REVERTED - the door does not stamp the mirror

**2026-09-02, orchestrator.** Authorised under the narrowed C.2 deploy grant, executed with the
operator's guardrails, and **reverted at step 2 because a consumer broke.**

## What was done, in order

| step | action | result |
|---|---|---|
| 0 | Read OPENBRAIN-CONSUMER-REGISTRY.md for every consumer touched | agents' door, both gateways, mcpo/-ext -> OWUI tools, agent-bridge audit-mirror WRITES, harness clause-8 seam |
| 0 | Before-state | **24** superuser connections; 1,129 personal rows; no ob_app_memory |
| 1 | Create the role (scripts/checks/sql/init-app-role-memory.sql) | ob_app_memory\|false\|false - **kept**, inert |
| 1b | ALTER ROLE PASSWORD -> gitignored OB1/docker/.env | done, value never printed |
| 2 | Point openbrain-mcp at the role, recreate | started clean, no errors |
| 3 | **Verify at the door** | **reads PASS, writes FAIL** -> reverted |

## The clause-3 property WAS PROVEN before the revert

As the app role with NO SET ROLE at all:

    ob_app_memory | super=false | personal_visible=0 | ops_visible=13011

1,129 personal rows invisible to the agent plane; 13,011 ops rows still read. A boundary, not a
blackout - exactly what clause 3 asks. The role works. The door does not.

## Why it was reverted

capture_thought returned, correctly, isError:true - "new row violates row-level security policy
for table thoughts". Isolated as ob_app_memory:

    INSERT exposure='ops' column only, no mirror  -> ERROR: violates RLS policy
    INSERT exposure='ops' + metadata mirror       -> INSERT 0 1

**The live thoughts_ops_plane policy checks the JSONB MIRROR (metadata->>'exposure'), not the
typed column.** openbrain-mcp writes the column but not the mirror - true all along, invisible
because the door connected as a bypassrls superuser so the check never ran.

**The gmail outage's class exactly**: a producer that does not satisfy the write contract, hidden
by a bypass, surfacing the moment the bypass is removed. The registry made this a ten-minute
diagnosis instead of a two-day outage - but it lists openbrain-mcp as a DOOR and never as a
producer, and its capture_thought write is one. **That row should be added.**

## State now - verified, not assumed

- openbrain-mcp back on DB_USER: postgres; capture_thought write succeeds again
- probe rows deleted, remaining: 0
- **1,129 personal rows intact** (read-only throughout except my own probes)
- superuser connections back to 23 (was 24; delta is pooling, not a leak)
- ob_app_memory role RETAINED: inert, connects to nothing, revertible via
  scripts/checks/sql/revert-app-role-memory.sql
- compose byte-restored from a pre-change copy, not hand-edited back

## What would close clause 3

**One code change, not a deployment retry.** openbrain-mcp's write path must stamp the jsonb
mirror as well as the column - the same two-halves fix pull-gmail.ts already carries. Then step 2
re-applies unchanged.

Alternatively re-point the policy at the typed column now that H3 makes it NOT NULL - strictly
better since the column cannot be absent - but that touches every consumer of the predicate.

**Do not retry the promotion before one of those lands.** Re-applying step 2 without it
reproduces this failure exactly.
