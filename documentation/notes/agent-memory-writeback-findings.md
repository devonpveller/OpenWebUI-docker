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

## F2 — CLOSED 2026-08-29 by the Phase 1.3 smoke script

`scripts/checks/smoke-agent-memory.ps1` starts the real container against a throwaway
database and a stub embedding endpoint and calls both doors over HTTP. The REST twin now
has: a bad key rejected 401; a good key returning the writeback contract (which is what
proves the route is reachable — the MCP catch-all also answers 401, so only a SUCCESSFUL
call distinguishes the two orderings); the row present in `agent_memories` with the policy
defaults; the audit event in the same transaction; idempotent retry; the cross-tenant case;
422 + `refused: secret_shaped` with nothing written; 400 on malformed JSON; and the
plane-agreement invariant end to end — write with defaults, recall with defaults, get it
back.

Found while writing it, and worth keeping: on PS 5.1 the body of an HTTP error response is
in `$_.ErrorDetails.Message`, NOT in `GetResponseStream()` — `Invoke-WebRequest` has already
drained it, so re-reading returns an empty string. Every assertion about a refusal body
would have compared against `$null` and failed a CORRECT server. That is the cry-wolf
failure, and it only surfaced loudly because StrictMode happened to be in effect.

---

## F3 — CLOSED (earlier, by the offline harness)

`test-quartz4-offline.ps1` executes the writeback and recall statements against the real
schema on a fresh volume, which is what caught the `detail`-vs-`payload` column error. The
audit-event insert is covered there, and again through the door by the smoke script above.

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
