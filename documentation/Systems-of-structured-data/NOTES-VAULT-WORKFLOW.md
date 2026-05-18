# Notes Vault Workflow (evolving-vault model)

The `openbrain-wiki-data` repo is now a **living Obsidian vault**, not a
disposable mirror. Two layers share it:

| Layer | Path | Owner | Lifecycle |
|---|---|---|---|
| Generated | `content/**` (`<type>/<slug>.md`, `topic/`, `entities.md`, `graph.json`, `topic.md`) | the compiler | regenerated **incrementally** from OpenBrain; never hand-edit |
| Notes | `notes/**` | **you** | hand-written; the compiler never touches it |
| Home | `index.md` (vault root) | the compiler | regenerated; Quartz landing page |

## How the loop works

1. Research in Open WebUI / capture thoughts → lands in OpenBrain.
2. The compiler (scheduled + on-demand) regenerates **only the entity
   pages whose evidence changed since the last compile** (`graph.json`,
   `entities.md`, `topic/*` always refresh — they're cheap aggregates).
   Unchanged pages are left byte-for-byte intact.
3. You read/explore in Obsidian or the Quartz viewer (`127.0.0.1:8812`),
   and write your own notes under `notes/`, freely `[[linking]]` into
   generated pages.
4. On the next compile the service **pulls your note commits**, ingests
   each note as a **tethered** OpenBrain record (one note ↔ one record,
   keyed by `note_path`: editing a note PATCHes the same row, never
   duplicates; deleting a note removes its row), runs entity extraction
   on it, and regenerates just the affected pages.
5. Commits accumulate as a normal git history and push fast-forward
   (no wipe, no force). Your notes and the generated diffs interleave.

## Your one-time setup (the "clone the remote" topology)

You author notes in a **local clone**, not in the Docker volume:

```bash
git clone git@github.com:devonpveller/openbrain-wiki-data.git
# open the clone folder as an Obsidian vault (it IS the vault root)
```

- Auth: this is your *personal* GitHub access (your own SSH key /
  credential manager) — NOT the container's deploy key. You own the repo.
- In Obsidian, the **Obsidian Git** community plugin (auto pull + commit
  + push on an interval) makes step 4 hands-off. Or use plain
  `git pull --rebase` / `git add notes && git commit && git push`.
- Write notes only under `notes/`. You may read/`[[link]]` anything in
  `content/`, but don't edit `content/**` or `index.md` — those are
  regenerated and your edits there would be overwritten (edit the
  underlying source/thought instead, or write a note).

## Rules of the model

- **Generated pages are never authoritative to hand-edit.** Wrong fact →
  fix the OpenBrain source/thought (or write a correcting note); the next
  compile reflects it. Anti-drift preserved.
- **Notes are durable**: they live in git *and* are tethered into
  OpenBrain, so they survive a volume loss and enrich future synthesis.
- **Link stability**: generated link targets are the entity slug
  (`<type>-<name>`). Stable across normal re-compiles; renames/merges are
  the one drift risk (a stable-slug/alias registry — spec req 8 — hardens
  this; see decision log for status).
- Incremental by default; a full rebuild only happens on first run, on a
  very large delta, or a manual `--batch`.

## When does it compile?

- **On boot** of `openbrain-wiki` (your restarts — deterministic).
- **Deterministic daily** at `WIKI_RECOMPILE_HOUR` *local* time (default
  01:00). Set the service `TZ` so that hour is *your* 1am, not container
  UTC. This replaced the old 24h-from-boot interval (which drifted and
  could fire during work).
- **Change-driven (event)**: a watcher polls OpenBrain every
  `WIKI_WATCH_INTERVAL_MIN` (3m) for sources/thoughts newer than the
  last compile; when activity *settles* (a tick with no further new
  rows — i.e. your research burst finished), it compiles once. So
  "research → wiki catches up" happens on its own within a few minutes,
  with no GPU churn while nothing changed.
- **On demand**: `wiki_trigger_recompile` MCP tool / `POST :8811/recompile`.

All four go through the same incremental compile (one at a time; a
concurrency guard prevents overlap).

## Knobs (compose env, service `openbrain-wiki`)

- `WIKI_RECOMPILE_HOUR` (1) + `TZ` — deterministic daily compile hour.
- `WIKI_WATCH_ENABLED` (true) / `WIKI_WATCH_INTERVAL_MIN` (3) —
  change-driven recompile + its poll/debounce cadence.
- `COMPILE_ON_BOOT` (true) — compile once on container start.
- `WIKI_GIT_FORCE` (`false`) — leave off; pull-rebase keeps pushes FF.
- `WORKER_DRAIN_MAX_MIN` (30) — cap on pre-compile extraction draining.
- `WIKI_NOTEBOOK` ("") — optional global notebook scoping.
