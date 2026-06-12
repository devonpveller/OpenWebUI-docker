# Wiki Compiler and Research Frontend — Implementation Guide

> **For Claude Code:** This document is the companion to `personal-memory-stack-setup.md`. It covers Task 5 of that setup (the wiki compiler) and the optional research frontend that replaces Open Notebook.
>
> **Critical reading order:**
>
> 1. Read this entire document before touching code
> 2. Execute Section 1 (Validation) and STOP — report findings before proceeding
> 3. Get user direction on which path to take in Section 2
> 4. Only then proceed to the implementation sections
>
> The validation step is non-negotiable. There is an existing wiki recipe in the OB1 repo, and we are obligated to evaluate it before building anything new.

---

## Section 1 — Validation (do this first)

OB1's `recipes/` directory contains a recipe described as: _"Compiles graph-backed entity pages and topic synthesis into a regenerable wiki layer you can run on demand or on a schedule."_

That description matches the architecture this guide is designed around almost exactly. Before assuming we need to build a wiki compiler, we have to evaluate what's already there.

### 1.1 Locate and inspect the existing recipe

In the cloned OB1 repo at `~/projects/OB1`:

1. `ls recipes/` — list all recipes. Look for one with "wiki" in the name (likely `wiki-compiler/`, `wiki/`, or similar).
2. Read its `README.md` end to end.
3. Read every source file in the recipe directory (likely a mix of SQL migrations, TypeScript edge functions, and prompt templates).
4. Note: file count, last commit date, the LLM model it uses, any dependencies it pulls in, any tables or columns it adds to OpenBrain.

### 1.2 Evaluate against the requirements

Score the existing recipe against these requirements. For each, answer: does the recipe satisfy it (Y), partially satisfy it (P), or not at all (N).

| #   | Requirement                                                                  | Score |
| --- | ---------------------------------------------------------------------------- | ----- |
| 1   | Reads from OpenBrain's `thoughts` table                                      |       |
| 2   | Reads from a `sources` table for external documents                          |       |
| 3   | Produces entity pages (one per person/org/concept referenced across sources) |       |
| 4   | Produces topic synthesis pages                                               |       |
| 5   | Output is markdown files in a directory                                      |       |
| 6   | Emits Obsidian-compatible `[[wikilinks]]` between pages                      |       |
| 7   | Emits a `graph.json` manifest with nodes and edges                           |       |
| 8   | Maintains a canonical entity registry to keep link targets stable            |       |
| 9   | Detects and surfaces contradictions across sources                           |       |
| 10  | Can be scoped to a notebook/project (compile only sources tagged X)          |       |
| 11  | Runnable on demand and on a schedule                                         |       |
| 12  | Output is fully regenerable — wiki can be deleted and rebuilt                |       |
| 13  | Exposes an MCP tool for querying compiled pages                              |       |
| 14  | Records provenance — every claim links back to its source row                |       |

After scoring, **stop and report to the user**. Show the table with scores, the recipe's last commit date, the model it uses, and a one-paragraph summary of what it actually does vs. what we need.

### 1.3 Decide which path

Based on the scoring, recommend one of three paths. **Do not pick this yourself; present the evidence and let the user decide.**

- **Path A — Use the existing recipe as-is.** Score is mostly Y, gaps are cosmetic. Run it, get a wiki, move on. Lowest effort. Use this path if you score 11+ as Y.
- **Path B — Extend the existing recipe.** Score is mixed, but the core (database integration, scheduling, basic markdown output) is solid. Add what's missing. Medium effort. Use this path if you score 7-10 as Y.
- **Path C — Build from scratch following the design in Section 3.** Score is mostly N or P, or the recipe is stale (no commits in 6+ months) or broken. Use this path only if Paths A and B are infeasible.

**Bias toward A or B.** Building from scratch is the last resort. If the existing recipe almost works, extending it is dramatically cheaper than rebuilding.

---

## Section 2 — What we're building (regardless of path)

The wiki compiler is an agent that reads from OpenBrain and produces a directory of synthesized markdown pages plus a graph manifest. It is a one-way pipeline:

```
OpenBrain (Supabase)
    │
    │  Compiler reads sources, thoughts, relations
    ▼
Wiki compiler (scheduled Python or TS script)
    │
    │  - Identifies entities, concepts, topics
    │  - Generates entity pages, concept pages, topic pages
    │  - Resolves wikilinks against a canonical entity registry
    │  - Detects contradictions and creates comparison pages
    │  - Emits a graph.json describing nodes and edges
    ▼
Output directory: ~/wiki/
    ├── index.md
    ├── entities/
    │   ├── alice-chen.md
    │   ├── stripe.md
    │   └── ...
    ├── concepts/
    │   ├── rag.md
    │   ├── memory-architecture.md
    │   └── ...
    ├── topics/
    │   ├── llm-memory-systems.md
    │   └── ...
    ├── sources/
    │   ├── 2026-04-04-karpathy-llm-wiki.md
    │   └── ...
    ├── log.md
    └── graph.json
```

Outputs are markdown with frontmatter, wikilinks, and consistent slug-based filenames so links resolve.

The compiler runs on schedule (default: daily) or on demand via a trigger.

### Outputs

- **Markdown pages** — Obsidian/Quartz/Perlite compatible. Frontmatter includes type, source_count, last_updated, related_entities, confidence.
- **`graph.json`** — JSON manifest of nodes (each page) and edges (each wikilink), with metadata for filtering and visualization.
- **`index.md`** — catalog of all pages, organized by type, with one-line summaries.
- **`log.md`** — append-only chronological record of compile runs, ingests reflected, and changes.

### MCP tools the wiki layer exposes

- `wiki_search(query, page_type?)` — semantic + keyword search across compiled pages
- `wiki_read_page(page_slug)` — fetch a single page's content
- `wiki_get_backlinks(page_slug)` — pages that link to this one
- `wiki_get_related(page_slug, depth=1)` — graph traversal from a page
- `wiki_list_pages(type?, notebook?)` — list pages with optional filtering
- `wiki_trigger_recompile(scope?)` — manually trigger a compile run (admin only)

---

## Section 3 — Design (use only if Path C)

If Path A or B was chosen, skip this section. Use only when building from scratch.

### 3.1 Required additions to OpenBrain

Add these tables to Supabase (or extend existing tables if they exist):

**`wiki_entities`** — canonical registry of entity pages. Prevents the `[[GPT-4]]` vs `[[GPT 4]]` problem.

```sql
create table wiki_entities (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null unique,
  slug text not null unique,        -- kebab-case, used in filenames
  entity_type text not null,        -- person, org, place, product, concept, project
  aliases text[] default '{}',
  first_seen_at timestamptz default now(),
  last_referenced_at timestamptz default now(),
  source_count int default 0,
  notebook text,                    -- optional grouping
  metadata jsonb default '{}'::jsonb
);

create index on wiki_entities (slug);
create index on wiki_entities (canonical_name);
create index using gin on wiki_entities (aliases);
```

**`wiki_relations`** — typed edges between entities/pages. The graph backbone.

```sql
create table wiki_relations (
  id uuid primary key default gen_random_uuid(),
  from_entity_id uuid references wiki_entities(id) on delete cascade,
  to_entity_id uuid references wiki_entities(id) on delete cascade,
  relation_type text not null,      -- mentions, contradicts, supports, derived_from, etc.
  source_id uuid,                   -- which source this relation came from
  confidence float default 1.0,
  created_at timestamptz default now()
);

create index on wiki_relations (from_entity_id);
create index on wiki_relations (to_entity_id);
```

**`wiki_pages`** — the compiled output cache. Optional but useful for fast MCP lookups without reading the filesystem.

```sql
create table wiki_pages (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  page_type text not null,          -- entity, concept, topic, source, comparison, synthesis
  title text not null,
  body text not null,
  frontmatter jsonb default '{}'::jsonb,
  notebook text,
  source_count int default 0,
  last_compiled_at timestamptz default now(),
  embedding vector(1536)             -- for semantic search via pgvector
);

create index on wiki_pages (slug);
create index on wiki_pages (page_type);
create index on wiki_pages using ivfflat (embedding vector_cosine_ops);
```

### 3.2 Compiler pipeline

The compiler runs in stages. Each stage is idempotent and can be re-run independently.

**Stage 1: Entity extraction.** Read every `sources` and `thoughts` row added or modified since the last successful compile. For each, run an LLM extraction pass that identifies entities (people, orgs, places, products, concepts, projects) referenced in the text. For each entity, check the `wiki_entities` registry by canonical name and alias. If found, increment `source_count` and update `last_referenced_at`. If not found, create a new row. Use a cheaper model here (gpt-4o-mini, claude-haiku, or equivalent local) — the work is high-volume and the precision requirement is modest.

**Stage 2: Relation extraction.** For each new source, run a second pass that identifies relations between entities mentioned in the same source. Write rows to `wiki_relations`. This is where contradictions get flagged — if source A says "Sarah Chen joined Stripe in 2024" and source B says "Sarah Chen joined Stripe in 2023", emit a `contradicts` relation with both sources attached.

**Stage 3: Page generation.** For each entity whose `source_count` is above a threshold (default 2) or that has been referenced in the last N days, generate or update the entity page. The page generation prompt receives:

- The entity's canonical name, aliases, and metadata
- All relations involving this entity
- A list of all other canonical entity names (for wikilink awareness)
- The text content of the top-N most relevant source rows (semantic search against the entity name + summary)

The prompt instructs the model to write the page in markdown with `[[Canonical Name]]` syntax for any reference to another known entity. The output is parsed, validated (broken links flagged), and written to both the filesystem and the `wiki_pages` table.

**Stage 4: Topic and synthesis pages.** Cluster entities and sources into topic groups (using embedding cosine similarity on titles + summaries). For each cluster above a size threshold, generate a topic page that synthesizes across its members. These are the "what's the literature say about X" pages.

**Stage 5: Index and graph manifest.** Walk `wiki_pages` and emit `index.md` (organized by type and notebook). Walk `wiki_relations` joined with `wiki_pages` and emit `graph.json` with nodes (each page as a node, typed) and edges (each relation as a typed edge).

**Stage 6: Log.** Append a run entry to `log.md`: timestamp, sources processed, entities created, pages written, contradictions found.

### 3.3 Link awareness

The single biggest failure mode of LLM-generated wikis is link inconsistency. The model writes `[[GPT-4]]` in one page, `[[GPT 4]]` in another, `[[gpt4]]` in a third, and none of them resolve to the same target.

Solution: every page generation prompt includes a registry section like:

```
# Known entities (use these exact wikilink targets):

People: [[Alice Chen]], [[Bob Smith]], ...
Orgs: [[Anthropic]], [[Stripe]], ...
Products: [[GPT-4]], [[Claude Sonnet 4]], ...
Concepts: [[RAG]], [[Memory Architecture]], ...

# Linking rules:
- For any reference to a known entity, use `[[Canonical Name]]` exactly.
- Do not invent new wikilink targets that aren't in the list above.
- If you mention an entity not in the list, do NOT use wikilink syntax.
```

After generation, parse the output for `[[...]]` patterns. For each match, look it up in `wiki_entities`. If not found, log it as an unresolved link and either (a) auto-strip the brackets or (b) flag it for human review depending on configuration.

### 3.4 Scheduling and triggers

The compiler should support three trigger modes:

- **Scheduled** — cron job (`0 3 * * *` for daily 3am) calling the compile entrypoint
- **On-demand** — `wiki_trigger_recompile()` MCP tool that an agent can call
- **Event-driven** (optional, advanced) — Supabase trigger or webhook fires when a row is added to `sources` or `thoughts`, queuing an incremental compile

For an incremental compile, only process sources added/modified since the last successful run. For a full rebuild, re-run all stages on all data. The `log.md` records which mode was used per run.

---

## Section 4 — Research frontend (replacing Open Notebook's UI)

The wiki layer makes the agent-side and Obsidian-side experience good. What's missing — and what Open Notebook provided — is the _research surface_: a UI for organizing sources, conversations, and notes within a bounded "notebook" so you can take in a research project at a glance.

This section is about whether and how to build that.

### 4.1 What Open Notebook actually gave you

Stripping away the marketing, Open Notebook's UI value was three things:

1. **Notebook-scoped view.** You open a notebook and see only the sources, notes, and chats relevant to that project — not your entire knowledge base.
2. **Tabbed interaction surface.** Within a notebook: a sources panel, a chat panel for asking questions of just those sources, and a notes panel for saving outputs.
3. **Polished ingest UX.** Drop a URL or a PDF and it's added with one click. No CLI, no curl, no schema thinking.

The agent-side capabilities (semantic search, summarization, podcast generation) were features of the _backend_, which we're replicating in OpenBrain + wiki. The UI was a separate value.

### 4.2 Do you actually need a frontend?

Honest answer: maybe not. Before building one, evaluate whether the following stack covers your research workflow:

- **Ingest** via `openbrain_ingest_url` MCP tool, called from any chat agent. ("Ingest these five URLs into the LLM-memory notebook.")
- **Source browsing** via the OpenBrain dashboard (community-built — see the OB1 repo's `dashboards/` for Next.js and SvelteKit options). Filter by notebook to see project-scoped sources.
- **Topic-level navigation** via Obsidian opened on the wiki output directory. Graph view filtered to one notebook's pages gives you the bird's-eye view.
- **Q&A within a project** via an agent in Claude Code / Claude Desktop, with the routing skill, scoped by mentioning the notebook name in the question.

If that stack feels usable to you for a week of real research, do not build a frontend. The frontend is the most expensive piece of this project and the easiest one to over-engineer.

### 4.3 If you do build one — the minimum viable design

If the stack above doesn't cover the workflow, build a narrow research-focused frontend rather than a general-purpose Open Notebook clone. Three components, in order of value:

**Component 1: Notebook overview page (highest value, lowest cost).** A single web view per notebook that shows: list of sources with titles/dates/tags, list of wiki pages for that notebook, list of recent thoughts/notes, and the graph view of the notebook's compiled pages. Pure read view. Build this with Next.js or SvelteKit against the Supabase API and the wiki directory.

**Component 2: Scoped chat (medium value, medium cost).** A chat box on the notebook overview page that talks to an LLM with a system prompt restricting it to that notebook's sources and wiki pages. Effectively a "chat with these sources" experience. This requires the chat backend to know how to scope OpenBrain queries by notebook — make sure the OpenBrain MCP tools accept a `notebook` filter parameter.

**Component 3: Ingest form (low value, low cost).** A "+ Source" button on the notebook overview that POSTs to `openbrain_ingest_url`. Replaces the agent-driven ingest with a click-driven one. Skip this if `openbrain_ingest_url` via chat is acceptable.

Together that's roughly 2-4 weeks of focused work with an LLM helping. Considerably less than reproducing Open Notebook end-to-end, because you're not rebuilding the backend.

### 4.4 Tech recommendations

If building:

- **Framework:** Next.js (App Router) or SvelteKit. Both have Supabase clients and Vercel/Netlify deploy templates. Check OB1's `dashboards/` first — there are community-built dashboards already.
- **Wiki rendering:** Use `react-markdown` or `mdsvex` with a wikilink plugin. The wikilinks need to resolve to internal routes (`[[Sarah Chen]]` → `/wiki/sarah-chen`).
- **Graph view:** `react-force-graph` or `cytoscape.js`. Both consume `graph.json` directly. Cytoscape gives you more layout control; react-force-graph is faster to set up.
- **Chat:** Vercel AI SDK or LangChain. The LLM call needs to inject the notebook's sources and wiki pages as context, scoped via Supabase queries.
- **Auth:** Supabase Auth, single-user mode. Don't over-engineer; this is a personal tool.
- **Hosting:** Run it locally with `npm run dev` for personal use, or deploy to Vercel with the Supabase URL as an env var. If you want mobile access, Vercel + the Supabase project URL is the path.

### 4.5 What NOT to build

- **Do not build editing.** The wiki is generated. The dashboard is read-only. Editing source content happens in OpenBrain's dashboard or via the MCP. Editing wiki pages happens by editing OpenBrain rows and recompiling.
- **Do not build sync.** Single source of truth is Supabase. There is no offline mode, no local cache to keep in sync. Online-only is fine for a personal research tool.
- **Do not build mobile-first.** Build desktop-first; mobile is a read-only PWA at most.
- **Do not build podcast/audio generation** unless you have a working STT/TTS pipeline and want to invest in it. This is content generation, not memory.

---

## Section 5 — Operational concerns

### 5.1 Where the wiki lives

The compiler writes markdown to a directory. Three deployment options for serving it:

- **Local filesystem only.** Compiler writes to `~/wiki/`. Open with Obsidian or VS Code. Single-device; no mobile.
- **Git-backed directory.** Compiler writes to a git repo, commits after each successful compile. Push to a private GitHub/Gitea repo. Pull on other devices. Works with Obsidian Sync, Obsidian Git plugin, or just `git pull`.
- **Web-served.** Compiler writes to a directory served by Quartz, Perlite, or a custom Next.js app. Read-only web access from any device. Combine with git-backed for the best of both.

Pick one based on how many devices need access. Git-backed + web-served is the most flexible.

### 5.2 Failure modes to watch for

- **Compile errors silently leave a stale wiki.** Set up alerting: if the scheduled compile fails, log it and notify (Telegram, email, whatever). Don't trust "the wiki has my latest thinking" without verifying the last compile timestamp.
- **Link entropy.** Over time, the model will occasionally invent wikilink targets that don't match the registry. Run a "broken link" lint pass weekly and either auto-fix or flag.
- **Entity proliferation.** Every "Sarah" mentioned as a first name only might get a new entity row. Add a deduplication pass that prompts the LLM to merge near-duplicate canonical names with high embedding similarity.
- **Cost.** If you have 1000 sources and the compiler regenerates every entity page on every run, you'll burn tokens. Make page generation incremental: only regenerate entity pages whose related sources or relations have changed since the last compile.
- **Contradiction overload.** Comparison pages are valuable but only if there are real contradictions. Tune the threshold — minor wording differences shouldn't trigger comparison pages.

### 5.3 Backup and recovery

The wiki is regenerable, so backups of the _output_ aren't critical. What you must back up:

- The Supabase database (sources, thoughts, wiki_entities, wiki_relations)
- The `.env` files with API keys and connection strings
- The compiler code and its prompts (in version control)

If everything except OpenBrain is destroyed, you can recompile the entire wiki from scratch. That's the architectural payoff.

---

## Section 6 — What to report after Section 1

When Claude Code finishes Section 1, the report to the user should include:

1. The scored requirements table (14 rows)
2. The recipe's last commit date and current maintainer activity
3. The LLM model the recipe uses (and its cost implications)
4. The recipe's dependencies (does it pull in anything heavy or unfamiliar?)
5. A one-paragraph plain-English summary of what the recipe does
6. The recommended path (A, B, or C) with a one-sentence rationale
7. The estimated effort for each path

Then stop and wait. Do not begin implementation until the user picks a path.
