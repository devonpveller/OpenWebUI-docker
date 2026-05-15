# Personal Memory Stack — Three-Layer Setup

> **For Claude Code:** This document is a project spec. Read it end-to-end before taking any action. Treat the "Tasks" section as the work list. Before installing or modifying anything, summarize back to me what you're about to do and wait for confirmation. Do not run destructive commands without explicit approval.

## What we're building

A three-layer personal knowledge stack where each layer owns a distinct source and output shape, exposed to LLM agents (Claude Desktop, Claude Code, ChatGPT, Cursor, etc.) via MCP. The layers must not overlap in responsibility, and the agent must be able to route queries to the right one without ambiguity.

The three layers:

1. **mnemory** — agent-facing semantic memory. Stores short extracted facts _about the user_ (preferences, decisions, identity, working context). Auto-extracts from conversations.
   Repo: `https://github.com/fpytloun/mnemory`

2. **OB1 / Open Brain** — structured personal database on Supabase. Stores _records in domain tables_ (contacts, calendar, captured thoughts, projects, job applications, etc.).
   Repo: `https://github.com/NateBJones-Projects/OB1`

3. **Open Notebook** — compiled knowledge wiki. Stores _synthesized knowledge from ingested external documents_ (papers, articles, podcasts, books) as interlinked markdown-like notes. Implements the LLM Wiki pattern (compile-once, keep-current, not re-derive-on-every-query).
   Repo: `https://github.com/lfnovo/open-notebook`

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Agents: Claude Desktop, Claude Code, ChatGPT, Cursor    │
└────────────┬────────────────┬────────────────┬───────────┘
             │ MCP            │ MCP            │ MCP
             ▼                ▼                ▼
       ┌──────────┐     ┌──────────┐     ┌──────────┐
       │ mnemory  │     │ openbrain│     │ notebook │
       │          │     │  (OB1)   │     │          │
       │ Facts    │     │ Records  │     │ Wiki     │
       │ about    │     │ in       │     │ from     │
       │ user     │     │ tables   │     │ sources  │
       └────┬─────┘     └────┬─────┘     └────┬─────┘
            │                │                │
            ▼                ▼                ▼
       Qdrant +         Supabase         SurrealDB +
       S3/MinIO         (Postgres        file store
                         + pgvector)
```

Inter-layer flows (one-directional, periodic):

- OB1 `thoughts` → Open Notebook sources (high-signal thoughts become wiki ingest material)
- OB1 high-signal rows → mnemory facts (e.g., "interviewing at X next Tuesday" from job_hunt)
- Open Notebook synthesis pages → optionally surfaced to mnemory `fsck` for contradiction detection against user beliefs

Never the other direction. The arrows above are the only sanctioned cross-writes.

## Source-of-truth rules

These are absolute. Build the tool configs and the routing skill around them.

| Concern                                                      | Owner         | Other layers must                             |
| ------------------------------------------------------------ | ------------- | --------------------------------------------- |
| Facts about the user as a person                             | mnemory       | Never create "about me" pages or rows         |
| Domain records (contacts, calendar, recipes, thoughts, etc.) | OB1           | Never duplicate as memory facts or wiki pages |
| Knowledge synthesized from external sources                  | Open Notebook | Never re-summarize the same source elsewhere  |

Open Notebook's internal schema must explicitly state: **do not create entity pages about the user; defer to mnemory.**

## Routing rules (for the agent)

These get written into the system prompt or a Claude Code skill. Verbatim:

```
You have access to three knowledge tools. Route as follows:

- Questions about the user as a person — preferences, decisions, identity,
  working style, recent context — go to `mnemory`.

- Questions that look up structured records — a specific contact, a calendar
  event, a captured thought, a job application, a recipe — go to `openbrain`.

- Questions about external knowledge — what a paper says, what the literature
  argues, synthesis across articles or books the user has ingested — go to
  `notebook`. Prefer the compiled wiki pages over raw source retrieval; only
  fall back to raw sources if the wiki is insufficient.

For ambiguous questions, ask once which lane the user means before searching.
Never query more than two of these tools in a single turn unless the user
explicitly asks for a cross-layer answer.
```

## Tasks

Work through these in order. After each task, report what was done and verify before moving on.

### Task 1 — Prerequisites check

Confirm the following are present on the machine:

- Docker + Docker Compose
- Python 3.11+ and `uv` (or `pipx`)
- Node.js 20+
- An OpenAI-compatible API key in `OPENAI_API_KEY` (used by mnemory and Open Notebook)
- A Supabase project (cloud or self-hosted) — required for OB1
- Disk: ~10GB free for vector stores and containers

If anything is missing, list the gaps and stop. Do not auto-install system-level dependencies.

### Task 2 — Install mnemory

1. Pull the repo to `~/projects/mnemory` (or report if already present).
2. Read `README.md`, `docs/quickstart.md`, `docs/deployment.md`, and `docs/configuration.md`.
3. For initial setup, use the Docker Compose path (it brings up Qdrant + MinIO + mnemory together) rather than `uvx mnemory`. The Compose path is what the production deployment doc describes.
4. Set required environment variables in a `.env` file at the repo root: `OPENAI_API_KEY`, `MNEMORY_API_KEY` (generate one — used by clients), and any Qdrant/S3 overrides if not using the bundled containers.
5. Start the stack and verify the management UI at `http://localhost:8050` is reachable.
6. Do NOT yet wire it into MCP clients — that happens in Task 5.

### Task 3 — Install OB1 (Open Brain)

1. Pull the repo to `~/projects/OB1`.
2. Read `docs/01-getting-started.md` and `docs/04-ai-assisted-setup.md`.
3. The full beginner path involves Supabase + an AI gateway + Slack capture + an MCP server. For this stack, we want the database and the MCP server; Slack capture is optional (skip for now unless the user asks for it).
4. Set up the Supabase project: create the base schema, deploy edge functions per the getting-started doc.
5. Build only **Extension 1 (Household Knowledge Base)** initially as a smoke test of the schema and MCP wiring. Do not install all extensions yet — let the user pick which ones matter to them after the smoke test.
6. Deploy the OB1 MCP server and confirm it responds to a basic tool call.

### Task 4 — Install Open Notebook

1. Pull the repo to `~/projects/open-notebook`.
2. Read the README and any setup docs in `docs/`.
3. Use the Docker Compose setup. Open Notebook bundles SurrealDB, so don't install a separate one.
4. Set required environment variables: `OPENAI_API_KEY` (or another supported provider — check the repo's current provider list). If a search provider key is required (Tavily, Serper, etc.), configure that too.
5. Start the stack and verify the web UI is reachable on its configured port.
6. Create one test notebook with one source to confirm ingest works end-to-end before proceeding.
7. **Important:** Open Notebook may or may not ship an MCP server in the current release. Check the repo. If it does, note its endpoint. If it does not, we'll need a thin MCP wrapper around its API — flag this and we'll decide whether to build one in Task 6.

### Task 5 — Wire MCP clients

For each of the user's MCP clients in use (ask which ones — at minimum Claude Desktop and Claude Code), add three MCP server entries with **distinct, non-overlapping descriptions**.

Use exactly these names and descriptions; the agent's routing depends on them being unambiguous.

```json
{
  "mcpServers": {
    "mnemory": {
      "type": "streamable-http",
      "url": "http://localhost:8050/mcp",
      "headers": {
        "X-Agent-Id": "claude-code",
        "Authorization": "Bearer <MNEMORY_API_KEY>"
      },
      "description": "Personal facts, preferences, and decisions about the user. Use for questions about who the user is, what they prefer, and what they've decided."
    },
    "openbrain": {
      "type": "<as documented in OB1 MCP setup>",
      "url": "<OB1 MCP endpoint>",
      "description": "Structured personal records: contacts, calendar events, captured thoughts, household data, projects. Use for looking up or filtering specific records."
    },
    "notebook": {
      "type": "<as documented in Open Notebook or in the wrapper from Task 6>",
      "url": "<Notebook MCP endpoint>",
      "description": "Synthesized knowledge from ingested external documents (papers, articles, books, podcasts). Use for what the literature says about a topic. Prefer compiled wiki pages over raw source retrieval."
    }
  }
}
```

Verify each tool responds individually before moving on.

### Task 6 — Open Notebook MCP wrapper (conditional)

Only if Task 4 found that Open Notebook does not expose an MCP server natively.

Build a minimal MCP server (Python, FastMCP or the official Anthropic SDK) that wraps Open Notebook's REST API with at least these tools:

- `notebook_search(query, notebook_id?)` — semantic search across notes/sources
- `notebook_read_page(page_id)` — fetch a compiled wiki page
- `notebook_list_notebooks()` — list notebooks
- `notebook_list_sources(notebook_id)` — list sources in a notebook

Keep it read-only for now. Writes (ingesting sources, asking Open Notebook to generate a podcast, etc.) stay in the Open Notebook UI until we explicitly want agent-driven ingest.

### Task 7 — Open Notebook schema document

Open Notebook (and the LLM Wiki pattern generally) works best with an explicit schema/conventions document that tells any LLM acting on it how the wiki is structured. Create `~/projects/open-notebook/WIKI_SCHEMA.md` with the following content (this is content to write, not instructions to follow):

```markdown
# Wiki Schema and Conventions

## Roles

- The LLM agent owns all wiki page writes. The human owns sources, questions,
  and directional guidance.
- Sources are immutable. The agent reads them but never modifies the source files.

## Page types

- **Source page** — one per ingested document. Title = source title. Contains
  a structured summary, key claims, and links to relevant entity/concept pages.
- **Entity page** — one per real-world entity referenced across two or more
  sources (a person, an organization, a place, a product).
- **Concept page** — one per recurring idea or topic that spans sources.
- **Comparison page** — created when two or more sources disagree or offer
  contrasting frames on the same topic.
- **Synthesis page** — created on demand from a user question that is worth
  preserving as a compiled answer.

## What this wiki does NOT cover

- Do not create entity or concept pages about the user as a person. The user's
  preferences, decisions, identity, and working context live in mnemory (a
  separate system). If a source discusses the user, file it as a source page
  only; do not promote details about the user into entity pages here.
- Do not duplicate structured records that live in OB1 (contacts, calendar,
  thoughts, recipes, job applications). Reference them by name if needed, but
  the canonical record stays in OB1.

## Ingest workflow

1. Read the new source end to end.
2. Discuss top 3-5 takeaways with the user.
3. Create the source page.
4. Update index and relevant entity / concept / comparison pages.
5. Append a log entry: `## [YYYY-MM-DD] ingest | <source title>`.
6. A single ingest may touch 10-15 pages — this is normal.

## Query workflow

1. Check the wiki first (compiled pages).
2. Fall back to raw sources only if the wiki is insufficient.
3. If the answer is novel and worth preserving, offer to file it as a
   synthesis page.

## Lint workflow

Run on demand:

- Detect contradictions across pages.
- Detect orphan pages.
- Detect stale claims superseded by newer sources.
- Suggest concepts that recur in sources but lack their own page.
- Suggest cross-references that should exist but don't.
```

### Task 8 — Inter-layer sync jobs (defer)

Do not implement these yet. Note them in `~/projects/memory-stack/TODO.md` so we can pick them up in a follow-up session:

1. **OB1 thoughts → Open Notebook ingest** — periodic job (cron, GitHub Action, or Supabase scheduled function) that pulls captured thoughts above a quality threshold from OB1 and queues them as Open Notebook sources.
2. **OB1 high-signal rows → mnemory facts** — periodic job that mirrors specific structured events (e.g., upcoming interviews, calendar items in the next 7 days) into mnemory as short facts so the agent can recall them by user-facing semantic query.
3. **Open Notebook synthesis → mnemory `fsck` input** — optional. Provide mnemory's health checker with a list of relevant Open Notebook page URLs so it can surface contradictions between user beliefs and ingested knowledge.

For each job, document: trigger, source query, transform, target API call, idempotency strategy. Build none of them until the three base systems are stable.

### Task 9 — Write the routing skill

Create a Claude Code skill at `~/.claude/skills/memory-stack-routing/SKILL.md` (or wherever the user's skills live). Content:

```markdown
---
name: memory-stack-routing
description: Routes questions about user facts, personal records, or
  external knowledge to the correct memory layer (mnemory, openbrain,
  notebook). Use proactively when a user question could plausibly hit
  one of those three lanes.
---

# Routing logic

- Questions about the user as a person — preferences, decisions, identity,
  recent working context — call `mnemory` tools.
- Questions looking up structured records — a contact, a calendar event,
  a captured thought, a recipe, a job application — call `openbrain` tools.
- Questions about external knowledge — what a paper says, the synthesis
  across articles or books — call `notebook` tools. Prefer compiled wiki
  pages over raw source retrieval.

Never query more than two layers in one turn unless the user explicitly
asks for a cross-layer answer.

For genuinely ambiguous questions ("what do I think about X" — is that a
recorded opinion in mnemory or a wiki synthesis page in notebook?), ask
the user once which lane they mean before searching.
```

### Task 10 — Smoke tests

Once everything is wired, run these end-to-end:

1. **mnemory:** In Claude Desktop or Claude Code, say "Remember that I prefer Python over JavaScript for new projects." Confirm it shows up in mnemory's management UI.
2. **OB1:** Add a contact to the household knowledge extension. Ask the agent to retrieve it. Confirm it comes back through `openbrain`.
3. **Open Notebook:** Ingest one paper or article. Ask the agent a question whose answer is in that source. Confirm it comes back through `notebook`.
4. **Routing:** Ask three questions back-to-back that should each hit a different layer. Confirm the agent picks the right tool each time without confusion.

If any smoke test fails, report which one and stop before proceeding to Task 8 work.

## Constraints — read before doing anything

- **Do not install or modify anything system-wide without confirming first.** Use Docker containers, virtual environments, and per-project directories. No `sudo apt install`, no global `npm install -g`, no modifying shell rc files without showing the diff first.
- **Do not commit secrets.** API keys go in `.env` files that are in `.gitignore`. If a repo doesn't have `.gitignore` covering it, add it.
- **Do not auto-share or push anything.** All repos stay local until the user explicitly asks to push.
- **Do not run destructive commands** (`rm -rf`, dropping databases, deleting volumes) without explicit confirmation in chat.
- **Be wary of overlap creep.** If at any point you find yourself creating "memory" features in OB1, "structured records" in mnemory, or "user profile" pages in Open Notebook, stop and re-read the source-of-truth rules table above. That's a sign something is bleeding across layers.

## Open questions to ask the user before starting

1. Which MCP clients should be wired up? (Claude Desktop, Claude Code, both, plus others?)
2. Is the Supabase project for OB1 cloud-hosted or self-hosted? If self-hosted, is it already running?
3. Which OB1 extensions does the user actually want (household, home maintenance, family calendar, meal planning, professional CRM, job hunt)? Don't install all of them.
4. Does the user want Slack capture wired in for OB1? (Optional; skippable.)
5. Which LLM provider should Open Notebook use? (OpenAI default, but it supports several.)
6. Does the user want podcast/audio generation in Open Notebook enabled? (Costs more in API usage.)

Ask these before starting Task 2.
