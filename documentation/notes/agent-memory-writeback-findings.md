# Findings — agent-memory writeback (Phase 1.2 slice 2), 2026-08-29

Checked against `work/amwrite` at `920e8a2`, OB1 at `3823d95`.

---

## F1 — I built to the PRODUCTION image tag, and had to undo it

`docker compose build openbrain-mcp` tags `openbrain-mcp-server:local` — the tag production
runs from. CLAUDE.md is explicit: *"Test images tag `:wt-<id>`; prod containers and `:local`
tags are a gated deploy, not a test."* I built to `:local` to satisfy the anchor's "the
container still builds" criterion, which briefly left the production tag pointing at an
unreviewed image carrying an unreleased tool.

No container was recreated, so nothing ran it — but a watchdog restart or an operator
`up -d` in that window would have deployed it silently. The tag was rebuilt from the merged
line afterwards and verified to contain only `index.ts` again.

**The real fix, which this item did not do:** build test images with `-t
openbrain-mcp-server:wt-amwrite` via `docker build` rather than `docker compose build`,
which always uses the compose-declared tag. Worth a helper, because the compose form is the
obvious thing to reach for and it is the wrong one for a test. Until then, anyone proving a
build must restore the tag afterwards, which is easy to forget.

---

## F2 — the REST twin is registered but nothing exercises it end to end

`POST /agent-memory/writeback` shares `performWriteback` with the MCP tool, so their
behaviour cannot diverge in the logic. What is NOT covered is the plumbing either side of
it: the auth predicate, JSON parsing, the 422-on-refusal mapping, and — most importantly —
that the route is reachable at all.

It is registered before `app.all("*")`, which matters: the MCP catch-all would otherwise
swallow it. That ordering is currently guaranteed only by a comment and by where the call
sits in `index.ts`. A route-level test belongs with the smoke script (Phase 1.3), which can
start the server and actually call both doors.

**Until that exists, treat "the REST twin works" as unproven.** The tool's logic is tested;
the door is not.

---

## F3 — `agent_memory_audit_events` columns were inferred, not verified

The insert writes `(memory_id, workspace_id, event_type, detail)`. Those names came from
reading the schema's CREATE TABLE, not from exercising an insert against a real database.
The unit tests stub the pool, so a wrong column name would pass every test here and fail
only on first real use.

The same applies to the `RETURNING id` shapes. This is the honest limit of a stubbed-pool
test suite: it proves the control flow and the policy, not the SQL.

**The smoke script (1.3) is where this gets caught**, and it should be written before this
path is enabled anywhere. Recorded so nobody reads the green suite as evidence the SQL is
right.

---

## F4 — `content_hash` is computed in SQL, which is right but worth stating

The insert calls `agent_memory_hash_text($7)` rather than hashing in TypeScript, so the hash
is defined by the database function for every writer — the REST twin, the MCP tool, and any
future first-party caller — instead of by whichever client happens to write.

That is deliberate and worth keeping. If a caller ever computes the hash itself, dedupe
silently stops working across writers, and nothing errors: two identical memories simply
get different hashes.

---

## F5 — the embedding-size boundary is still unhandled (carried from the policy slice)

Unchanged from the policy findings: `detectUnsafeContent` caps content at 20,000
CHARACTERS, while bge-m3 rejects above ~512 TOKENS. A memory can pass the gate and still
fail to embed, and this path calls `getEmbedding` with no truncate-or-halve retry — the
exact shape that broke the daily digest re-ingestion.

`performWriteback` will therefore throw on an oversized-but-permitted memory rather than
degrading. That is better than writing a memory with no embedding (which would be
unrecallable and invisible), but it is not the handled behaviour the digest path settled on.
It belongs with recall's embedding work, and it is a real gap until then.
