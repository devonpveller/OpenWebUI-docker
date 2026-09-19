# Wiki drain: status check 2026-08-29/30 (+ two findings)

Checked 2026-08-30 ~01:10 UTC, in response to "no movement in progress —
completed, or did the drain fail to relaunch?"

## Verdict: the backfill COMPLETED. It did not stall.

`openbrain-wiki` booted 2026-08-29 03:54Z (OB1 compose recreate) and ran the
backfill chain to exhaustion: 6,170 queued -> 0 backfill-eligible, 129 compiles,
129 `compile ok`, 0 failed pages. Last drain-driven compile 19:53Z; the two
compiles after it (20:18Z, 21:12Z) were normal change-watch runs.

Evidence:
- `/healthz` -> `{"ok":true,"running":false}` (service alive, nothing compiling).
- Ran the service's OWN `backfillSlice()` over the live
  `/wiki/planned.json` inside the container: **0 eligible**. All 868 entries
  carry `unlinked: true` (below the sweep link threshold — residue by design,
  not work).
- The drain supervisor (lib/drain-supervisor.mjs) is present in the running
  `/app/wiki-service.mjs` and is correctly silent: 0 eligible means nothing to
  restart.

Silence since 21:17Z is normal — change-watch only logs when it sees new items.

## Finding 1 — the log's "N still queued" can never reach 0 (cosmetic, but it
## is what makes a finished drain look stalled)

`planEntityQueue()` (OB1 `docker/wiki-service/lib/entity-links.mjs:66`) counts
`linkedQueued` / `unlinkedQueued` per ENTITY, but writes into `planned` keyed by
`content/<type>/<slug>`. Colliding slugs silently overwrite, last-writer-wins,
while both counters keep counting. The counts and the map therefore disagree —
exactly the divergence the comment at `wiki-service.mjs:998` says cannot happen.

Reproduced against live data with the service's own code, `pageExists` pointed
at `/wiki/content`:

    linkedQueued=3  unlinkedQueued=870  sum=873  mapKeys=868  LOST=5
    linked entries surviving in the map: 0

So the log line `planned manifest: 3 queued page(s)` is permanent: the drain and
the supervisor read the map (0 eligible, correctly idle) while the operator
reads the counter (3, forever). Same run over the whole vault (`pageExists` ->
`/wiki`) shows the general scale: 46,845 entities -> 46,103 keys, **742
collisions**.

The collisions are near-duplicate entities, and the loser is often the
better-linked one — the surviving key is the last id, not the strongest:

| key | loser | winner |
|---|---|---|
| `tool/tool-open-webui` | id 58 "Open WebUI", 154 links | id 5878 "Open-WebUI", 3 links |
| `tool/tool-c` | id 999 "C#", 245 links | id 1611 "C++", 115 links |
| `tool/tool-ai` | id 216 "AI", 174 links | id 1707 ".ai", 1 link |
| `tool/tool-docker-compose` | id 5317 "docker compose", 35 links | id 7027 "docker-compose", 10 links |

Two separable problems: (a) the manifest should not lose entries to a key
collision (or the counters must count map writes, not loop iterations); (b) the
entity layer is minting `C#`/`C++`, `AI`/`.ai`, `Open WebUI`/`Open-WebUI` as
distinct entities that slugify identically — an extractor/dedup issue.

Real-world cost of (a) today: 3 link-worthy entities have no page and can never
be picked up by the backfill, because their manifest slot is held by an unlinked
homonym. Small, but permanent, and it is invisible to every check.

## Finding 2 — one unreaped `git` zombie per compile

`ps` inside `openbrain-wiki`: **127 processes in state `Z`, all `[git]`**, against
129 compiles since boot — i.e. roughly one leaked child per compile, never reaped
by PID 1 (`node wiki-service.mjs`). The container has no cgroup pid cap
(`pids.max = max`), so nothing breaks today; it is a monotonic leak on a
long-lived container and it makes `ps` output in an incident nearly unreadable.

Likely the `execFile` git calls in wiki-service.mjs; worth confirming which call
site does not await/close.
