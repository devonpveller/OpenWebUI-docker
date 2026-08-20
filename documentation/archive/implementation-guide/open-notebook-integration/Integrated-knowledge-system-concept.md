# Integrated Knowledge System — Architecture Concept Document

**Open Brain + Open Notebook + Obsidian LLM Wiki + Open WebUI**

Status: Concept / Pre-Implementation
Date: June 2026

---

## 1. Problem Statement

The current personal knowledge management workflow relies on four independent tools: Open Brain (OB1) as a Supabase-backed data infrastructure, an Obsidian vault with Karpathy-pattern LLM Wiki for structured knowledge, Open Notebook for source ingestion and interaction, and Open WebUI for LLM-driven research discovery. Each tool excels at its specific function but operates in isolation, requiring manual copy-paste to move information between them.

This fragmentation creates three specific problems. First, sources discovered in one tool are invisible to others unless manually transferred. Second, user-authored notes have no clear single home when multiple tools support note-taking, leading to duplication and confusion. Third, there is no shared concept of a research effort that spans tools, making it impossible to see all material related to a single line of inquiry in one view.

This document defines an integration architecture that unifies these tools around Open Brain as the single source of truth, with clear role separation that eliminates duplication and introduces a research thread primitive for cross-tool source organization.

---

## 2. Design Principles

> **Core Principle:** Sources have exactly one home (Open Brain). Every other tool borrows them. User notes have exactly one home (Obsidian). Nothing else creates persistent user-authored content.

1. **Single source of truth.** All sources, embeddings, entities, and thread metadata live in Open Brain's Supabase instance. No other tool maintains its own copy of source data.

2. **Clear role separation.** Each tool has one job. If two tools can do the same thing, only one should. Ambiguity in tool roles creates duplication for the user.

3. **User-controlled linking.** Cross-thread source associations are never created automatically. The system may suggest, but the user must deliberately act. Within-thread associations from active research sessions are automatic.

4. **Additive by default.** Linking a source to one thread never removes it from another. The only subtractive action is an explicit user removal, and even that is a soft operation (hidden, not deleted).

5. **Suggestions are persistent.** A rejected or hidden suggestion is never destroyed. It moves to a hidden pool the user can revisit at any time. Research context changes; what was irrelevant last month may be critical today.

6. **Maintainability over elegance.** Where two technical approaches exist, prefer the one with less ongoing maintenance burden, even if the upfront cost is higher.

---

## 3. Component Roles and Data Relationships

| Component               | Role                                                                                                           | Data Relationship                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Open Brain (OB1)**    | Canonical data store. Source of truth for all sources, threads, entities, and embeddings.                      | PostgreSQL + pgvector via Supabase. All other tools read from and write to this layer.                               |
| **Obsidian + LLM Wiki** | Thinking workspace. User-authored notes, claims, and connections. Wiki pages compiled from Open Brain sources. | User notes folder excluded from wiki generation. Wiki pages are a read-view compiled from Open Brain data.           |
| **Open Notebook**       | Research workbench. Source ingestion UI, multi-source Q&A, podcast generation, source interaction.             | Sources written directly to Open Brain (Supabase). SurrealDB retained only for local UI state and processing queues. |
| **Open WebUI**          | Discovery engine. LLM-driven research and source gathering.                                                    | Research results pushed to Open Brain as unthreaded sources tagged with session provenance.                          |

### 3.1 Open Brain (OB1) — Source of Truth

Open Brain is the canonical data store. It runs on Supabase, providing PostgreSQL for structured data, pgvector for semantic search, real-time subscriptions for change propagation, and edge functions for processing. All sources, regardless of how they entered the system, are stored here. The MCP server exposes capture, query, and thread management tools that any connected AI can call.

Open Brain also owns the entity extraction pipeline, knowledge graph, and wiki compiler. These processes run against the canonical source data and produce derived outputs (wiki pages, entity records, relationship edges) that are also stored in Supabase.

### 3.2 Obsidian + LLM Wiki — Thinking Workspace

Obsidian serves two distinct functions that must remain separated at the folder level.

**User notes folder:** This is where the user writes their own thoughts, claims, arguments, and connections. These notes are excluded from wiki generation and from Open Brain's source-of-truth data. They are the user's intellectual output, not system-managed content. This folder syncs via git.

**Wiki folder:** This contains LLM-compiled pages generated from Open Brain sources. These pages are summaries, entity profiles, concept maps, and cross-reference indexes. The user browses them but does not edit them directly. The wiki compiler regenerates them from Open Brain data.

This separation is already implemented in the current setup. The key integration point is ensuring Open Notebook's workflow respects this boundary and does not create a third category of notes.

### 3.3 Open Notebook — Research Workbench

Open Notebook is repositioned from a general-purpose note-taking tool to a focused research interaction surface. Its core value is in source consumption and exploration: uploading and processing documents, asking questions across multiple sources simultaneously, generating podcast overviews, and surfacing contradictions or connections within a source cluster.

**Open Notebook does not create persistent user-authored notes.** Its "notebooks" are live query views into Open Brain threads, not independent containers. When the user opens a notebook, they see all sources linked to that thread, regardless of where those sources were originally ingested.

**Storage repoint:** Open Notebook's source storage layer is rewritten to read from and write to Open Brain's Supabase instance directly. SurrealDB is retained only for Open Notebook's internal operational state: UI preferences, processing job queues, session history, and local caching. This is a deliberate fork from upstream Open Notebook. The tradeoff is accepted: this system is fundamentally different from the original project.

When a user adds a source through Open Notebook's UI, that source is written to Open Brain and linked to the currently active thread in a single operation. The user never needs to think about where the data lives.

### 3.4 Open WebUI — Discovery Engine

Open WebUI provides LLM-driven research and source discovery. Research sessions produce sources that are pushed to Open Brain tagged with session provenance metadata (timestamp, originating query, model used). These sources land in the thread the user initiated the session for, or in an unthreaded inbox if no thread context was provided.

---

## 4. Research Threads

The research thread is the central organizational primitive that spans all tools. It represents a durable, named line of inquiry that accumulates sources over time across sessions and ingestion points.

### 4.1 Thread Definition

A thread is a first-class entity in Open Brain's Supabase database. It has a unique ID, a user-assigned name, an optional description or guiding question, a creation timestamp, and an active/archived status flag. Threads are lightweight and cheap to create. The user should feel no friction in starting a new one.

### 4.2 Thread-Source Linking Model

The relationship between threads and sources is many-to-many. A source can belong to multiple threads. A thread contains many sources. The join is managed through a dedicated thread_sources table that records the link type, timestamp, and provenance.

| Tier           | Behavior                                                                                                                                 | User Action Required                                                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Automatic**  | Source discovered within a thread's active research session is linked to that thread immediately.                                        | None. This is bookkeeping for the session the user initiated.                                                                                                            |
| **Suggested**  | System detects cross-thread relevance via semantic similarity, keyword overlap, or co-occurrence. Suggestion surfaces in a triage queue. | User must deliberately accept to create the link. Rejecting hides the suggestion but does not delete it. Hidden suggestions are recoverable from a rejected/hidden pool. |
| **Deliberate** | User manually links a source to a thread it was not discovered in. May or may not originate from a suggestion.                           | Explicit user action. Full control.                                                                                                                                      |

> **Critical Rule:** Research performed in one thread is never autonomously added to a different thread. Cross-thread relevance is surfaced as a suggestion only. The user must deliberately accept the suggestion to create the link.

### 4.3 Suggestion Lifecycle

When the system detects that a source in thread A may be relevant to thread B (via semantic similarity to thread B's source cluster, keyword overlap with thread B's description, or co-occurrence patterns), it creates a suggestion record. This suggestion follows a defined state machine:

| State         | Visibility                                                                                                | Transition                                                                                   |
| ------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **Pending**   | Visible in the triage queue. Surfaces when user opens relevant thread or global review.                   | Accept → Confirmed. Hide → Hidden.                                                           |
| **Confirmed** | Source appears in thread view as a linked source. Indistinguishable from auto-linked or deliberate links. | User can unlink (soft remove). Thread-source join row marked inactive, recoverable.          |
| **Hidden**    | Removed from triage queue. Not visible in normal workflow.                                                | User can access hidden/rejected pool and restore to Pending or directly Accept to Confirmed. |

The hidden pool is always accessible. The user can open it at any time to review previously rejected suggestions. Research context evolves; a source dismissed during early exploration may become critical as the research direction sharpens. Nothing is permanently discarded from the suggestion system.

### 4.4 Sessions vs. Threads

A session is ephemeral. It represents a single burst of research activity: an OWUI search, an Open Notebook exploration, a batch upload. Sessions are timestamped and tagged with their origin tool and triggering query. They exist primarily for provenance tracking.

A thread is durable. It spans weeks or months and accumulates sources from many sessions across multiple tools. One session may produce sources relevant to several threads. One thread may draw from dozens of sessions.

Sessions feed threads but are not threads. The session record answers "where did this source come from?" The thread record answers "what research effort does this source serve?"

---

## 5. Data Model

All tables reside in Open Brain's Supabase (PostgreSQL) instance. This section describes the key entities and their relationships at a conceptual level. Exact column types, indexes, and RLS policies are deferred to implementation planning.

### 5.1 Core Tables

**sources** — The canonical record for every piece of ingested material. Contains the content body, metadata (URL, title, author, publication date), content hash (for deduplication), vector embedding, and provenance (which tool ingested it, which session, when).

**threads** — Named research efforts. Fields: id, name, description/guiding question, status (active/archived), created_at, updated_at.

**thread_sources** — Join table linking sources to threads. Fields: thread_id, source_id, link_type (automatic/suggested/deliberate), status (confirmed/pending/hidden), created_at, confirmed_at, suggestion_reason (nullable, stores the rationale for system-generated suggestions).

**sessions** — Provenance records for research activity. Fields: id, origin_tool (owui/open_notebook/manual), query_text, thread_id (nullable, for sessions initiated within a thread context), created_at. Light records, primarily for audit trail.

**session_sources** — Join table linking sources to the session that discovered them. A source may appear in multiple sessions if independently rediscovered. Fields: session_id, source_id.

### 5.2 Deduplication

When a source is ingested from any tool, Open Brain checks for duplicates using URL match and content hash comparison. If a duplicate is found, no new source record is created. Instead, the existing source is linked to the current thread and session. The user sees a notification that the source already exists and has been added to their current research thread.

### 5.3 Existing Open Brain Entities

Open Brain's existing tables (thoughts, entities, knowledge graph edges, wiki pages) continue to function as designed. The new tables (threads, thread_sources, sessions, session_sources) extend the schema without modifying existing structures. The wiki compiler and entity extraction pipeline continue to operate on the sources table as before.

---

## 6. Data Flow by Scenario

### 6.1 OWUI Research Session

1. User initiates a research session in OWUI, either within an existing thread context or creating a new thread.
2. OWUI's LLM discovers relevant sources through web search, paper databases, or other retrieval methods.
3. Each discovered source is written to Open Brain's sources table with a content hash check for deduplication.
4. A session record is created. Each source is linked to the session via session_sources.
5. Each source is automatically linked to the active thread via thread_sources with link_type = automatic.
6. Open Brain's background processes generate embeddings, run entity extraction, and queue wiki recompilation as needed.

### 6.2 Open Notebook Source Upload

1. User opens a notebook (which is a thread view) in Open Notebook.
2. User uploads a PDF, pastes a URL, or adds a document through the Open Notebook UI.
3. Open Notebook writes the source directly to Open Brain's Supabase sources table (not SurrealDB).
4. The source is linked to the current thread with link_type = automatic.
5. Open Notebook can now process the source locally for its interaction features (Q&A, podcast generation) by reading from Supabase.

### 6.3 Open Notebook Source Interaction

1. User opens a notebook (thread view). Open Notebook queries Open Brain for all sources where thread_sources.thread_id matches and status = confirmed.
2. Sources from OWUI, previous Open Notebook sessions, and manual additions all appear together.
3. User interacts with the combined source pool: asks questions, generates podcast, explores contradictions.
4. If the session produces insights the user wants to capture, a "send to Obsidian inbox" action drops a stub into the user notes folder. This stub is a starting point, not a finished note.

### 6.4 Cross-Thread Suggestion

1. Background process in Open Brain compares new source embeddings against existing thread source clusters.
2. If similarity exceeds a configured threshold, a suggestion record is created in thread_sources with link_type = suggested, status = pending, and suggestion_reason populated.
3. Suggestion surfaces in the user's triage queue (accessible from Open Notebook or a dedicated review interface).
4. User accepts (status → confirmed), hides (status → hidden), or ignores (remains pending).
5. Hidden suggestions are accessible from the hidden/rejected pool at any time for reconsideration.

### 6.5 Obsidian Note Writing

1. User writes a note in their user notes folder in Obsidian. This note may reference sources by linking to wiki pages or citing source IDs.
2. The note is not ingested into Open Brain as a source. It remains in the user notes folder, synced via git, excluded from wiki generation.
3. The wiki folder continues to be regenerated from Open Brain data by the wiki compiler. User notes and wiki pages coexist in the same vault but are clearly separated.

---

## 7. Open Notebook Storage Architecture

The decision to repoint Open Notebook's source storage from SurrealDB to Supabase is the most significant technical change in this architecture. This section clarifies the boundary.

### 7.1 What Moves to Supabase

- Source records (documents, URLs, processed content, metadata)
- Source-thread linkages
- Source embeddings and search indexes
- Any derived source data (summaries, extracted entities, chunk indexes)

### 7.2 What Stays in SurrealDB

- Open Notebook UI state (panel positions, view preferences, last-opened notebook)
- Processing job queues (PDF parsing jobs, podcast generation jobs in progress)
- Session artifacts (chat histories within Open Notebook, temporary processing outputs)
- Local caching for performance (recently accessed source content for faster rendering)

### 7.3 Implications

This is a deliberate fork from upstream Open Notebook. The source data layer is rewritten to use Supabase client libraries instead of SurrealDB queries. Open Notebook's UI code changes minimally since it still receives the same data shapes; only the data access layer is swapped. However, pulling upstream updates to Open Notebook will require careful merging for any changes that touch source storage.

The tradeoff is accepted: the maintenance cost of the fork is lower than the maintenance cost of running a bidirectional sync daemon between two databases, with all the duplication logic, conflict resolution, and failure modes that entails.

---

## 8. Unified User Workflow

### 8.1 Research Loop

**Step 1 — Discover.** User runs research in OWUI or uploads sources through Open Notebook. All sources land in Open Brain, linked to the active research thread.

**Step 2 — Explore.** User opens the thread in Open Notebook. All sources from all ingestion points appear in one view. User talks to the sources, generates a podcast overview, identifies key claims and contradictions.

**Step 3 — Synthesize.** When the user has formed a take, they write it in Obsidian. The note lives in the user notes folder. It may reference wiki pages or source IDs. Optionally, Open Notebook can send a session summary stub to the Obsidian inbox as a drafting starting point.

**Step 4 — Triage.** Periodically, the user reviews cross-thread suggestions. They accept relevant ones, hide irrelevant ones, and may discover unexpected connections between research efforts.

**Step 5 — Iterate.** Return to step 1. Each cycle adds sources, deepens the wiki, and builds the user's note corpus. The wiki compiler continuously regenerates from the growing Open Brain dataset.

### 8.2 What Each Tool Is Not

Open Notebook is not a note-taking app. It does not create persistent user-authored content. Its notebooks are query views, not document containers.

Obsidian is not a source ingestion tool. Sources enter through OWUI or Open Notebook, not by dropping files into the vault.

OWUI is not a reading environment. It discovers sources; it does not provide the multi-source interaction features that Open Notebook offers.

Open Brain is not a user-facing tool. The user never interacts with Supabase directly. It is infrastructure that the other tools consume.

---

## 9. Required MCP Tool Extensions

Open Brain's MCP server currently exposes capture and query tools. The following additional tools are needed to support the thread and suggestion system:

- **create_thread** — Create a new research thread with a name and optional description.
- **list_threads** — Return all active threads, optionally filtered by status.
- **get_thread_sources** — Return all confirmed sources for a given thread ID.
- **add_to_thread** — Link an existing source to a thread (deliberate link type).
- **remove_from_thread** — Soft-remove a source from a thread (mark inactive, recoverable).
- **get_suggestions** — Return pending suggestions for a thread or globally.
- **accept_suggestion** — Confirm a pending suggestion, creating the thread-source link.
- **hide_suggestion** — Move a suggestion to hidden status.
- **get_hidden_suggestions** — Return all hidden suggestions for review.
- **restore_suggestion** — Move a hidden suggestion back to pending.
- **capture_with_thread** — Extended capture tool that accepts a thread_id parameter, writing the source and creating the thread link in one operation.

---

## 10. Open Questions for Implementation Planning

1. **Suggestion threshold tuning.** What semantic similarity score triggers a cross-thread suggestion? Too low produces noise; too high misses connections. This likely needs iterative tuning with real data.

2. **Triage UX location.** Where does the user review suggestions? Open Notebook is the natural candidate (it is the source interaction surface), but a standalone lightweight UI or an Obsidian daily review note are also options.

3. **Wiki recompilation triggers.** Should the wiki compiler run on every source addition, on a schedule, or on-demand? Frequent recompilation keeps the wiki fresh but consumes compute. Batch recompilation is cheaper but introduces staleness.

4. **Open Notebook upstream tracking.** How much effort is allocated to tracking upstream Open Notebook changes post-fork? If the upstream project evolves significantly, at what point is the fork treated as an independent project?

5. **Obsidian inbox stub format.** What should the "send to Obsidian" output from an Open Notebook session contain? Session summary, key source references, extracted claims, all of the above? The stub should be useful without being so complete that the user skips writing their own note.

6. **Thread archival and lifecycle.** When a research effort concludes, what happens to the thread? Archival (hidden from active views but data preserved) seems right, but the UX for reopening archived threads needs design.

7. **Git sync scope.** User notes sync via git. Do Open Notebook session stubs sent to the Obsidian inbox also sync? They should, since they land in the user notes folder, but this should be confirmed.
