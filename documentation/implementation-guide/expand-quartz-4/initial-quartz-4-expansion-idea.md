Here’s the complete layout of the Quartz 4 feature tasks, stripped of infrastructure work and focused purely on application capabilities. I’ve grouped them by data layer and execution dependency so you can see how they fit together.

---

### 📋 Quartz 4 Feature Tasks Overview

| #   | Feature                            | Primary Goal                                                             | Complexity |
| --- | ---------------------------------- | ------------------------------------------------------------------------ | ---------- |
| 1   | **Source Visibility & Provenance** | Display underlying research sources alongside AI-generated wiki pages    | Medium     |
| 5   | **User Notes System**              | Obsidian-style note authoring, stored separately from wiki & sources     | High       |
| 6   | **Source Editing Interface**       | Allow in-app editing, annotation, or correction of captured sources      | High       |
| 7   | **Direct Source Import Pipeline**  | Accept documents (PDF, DOC, DOCX, PPT, MD, TXT) → ingest into Open Brain | Very High  |

---

### 🔍 Detailed Breakdown

#### 1. Source Visibility & Provenance

**Goal:** Make the AI's research traceable. Users should see _what_ informed each wiki page, not just the summary.

- **Scope:**
  - Per-wiki-page source list with metadata (source type, capture date, URL/title, collection)
  - Click-through to raw source view or diff
  - Toggle between "wiki view" and "source view"
- **Key Components:**
  - Query open-brain by topic/collection tag
  - Join results with wiki page IDs
  - Cache provenance maps to avoid embedding/query latency on page load
- **Integration Points:** Open Brain semantic search, existing wiki generation pipeline, frontend routing

#### 5. User Notes System

**Goal:** Give users a personal knowledge layer that coexists with (but doesn't pollute) AI-generated content.

- **Scope:**
  - Markdown note editor with live preview
  - Bi-directional linking (`[[wiki/topic]]`, `[[note/xyz]]`)
  - Separate storage namespace from wiki & sources
  - Tagging & basic folder/collection support
- **Key Components:**
  - Dedicated open-brain collection (`user_notes`) or isolated DB table
  - Markdown parser/renderer + link resolver
  - Conflict/sync strategy if editing from multiple sessions
- **Integration Points:** Open Brain vector store, markdown rendering layer, note link indexer

#### 6. Source Editing Interface

**Goal:** Allow users to correct, annotate, or refine captured research without losing the original.

- **Scope:**
  - Inline editor for source text
  - Versioning: preserve `original` vs `edited` snapshot
  - Metadata editing (tags, collection, confidence score, notes)
  - Re-embedding trigger when content changes significantly
- **Key Components:**
  - Diff/version control layer (simple hash-based or lightweight git-like)
  - Mutation API to update open-brain records
  - Embedding pipeline hook (full re-embed vs. delta embed decision)
- **Integration Points:** Open Brain record mutator, embedding service, audit log

#### 7. Direct Source Import Pipeline

**Goal:** Accept physical/digital documents and pipeline them into Open Brain as first-class research sources.

- **Scope:**
  - Drag-and-drop or file-picker upload UI
  - Format parsers: PDF, DOC/DOCX, PPT, MD, TXT, images (OCR if applicable)
  - Chunking strategy + metadata extraction (title, author, date, page refs)
  - Embedding generation + open-brain ingestion
  - Progress tracking & error handling (corrupt files, unsupported formats)
- **Key Components:**
  - Parser matrix (`pdfplumber`/`PyMuPDF`, `python-docx`, `python-pptx`, `markdown`, `Pillow`+OCR fallback)
  - Chunking & overlap logic tuned for your embedding model
  - Async ingestion queue (avoid blocking UI on large files)
  - Ingestion status dashboard
- **Integration Points:** Open Brain ingestion API, embedding service, chunking pipeline, storage layer, smolcrawl metadata schema

---

### 🧱 Architecture Mapping (Data Flow)

```
[Upload UI] → [Format Parser] → [Chunker] → [Embedding API] → [Open Brain Collection]
      ↑              ↑              ↑              ↑                  ↑
   UX Layer      Parser Matrix   Chunk Strategy  Embedding Queue   Vector Store + Metadata
```

- **Wiki Pages** read from generated synthesis layer
- **Sources** live in open-brain `research_sources` collection
- **User Notes** live in open-brain `user_notes` collection (isolated vector space)
- **Editing** mutates source records, triggers re-embedding if text delta > threshold
- **Provenance** joins wiki topics → source tags → open-brain query results

---

### 📅 Recommended Execution Order

| Phase | Task                           | Why First?                                                                                                              |
| ----- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| **1** | Source Visibility & Provenance | Lowest risk, highest UX value. Establishes the source→wiki linkage pattern needed for editing later.                    |
| **2** | User Notes System              | Self-contained data layer. Doesn't touch existing source/wiki pipelines. Validates open-brain multi-collection routing. |
| **3** | Source Editing Interface       | Builds on provenance UI. Requires versioning + re-embedding logic that will also serve the import pipeline.             |
| **4** | Direct Import Pipeline         | Heaviest dependency chain. Reuses chunking/embedding/editing infrastructure built in phases 1–3.                        |

---

### 🚧 Design Decisions to Lock Before Coding

1. **Notes vs Sources Storage:** Keep notes in a separate open-brain collection, or same collection with `type: "note"` filter? _(Separate recommended for query isolation & permission scoping)_
2. **Source Editing Versioning:** Simple `original_text` / `edited_text` fields, or full revision history? _(Start with dual-field, add revision log if demand appears)_
3. **Re-embedding Threshold:** What % text change triggers a full re-embed? _(Suggest 20%+ token delta or explicit "Re-embed" button)_
4. **Import Chunking Strategy:** Fixed token size + overlap, or semantic/sentence boundary chunking? _(Semantic chunking yields better retrieval but costs more compute)_

Want to spec out **Task 1 (Source Visibility)** first, or jump straight into the ingestion pipeline architecture? I can draft the data model + API contract for whichever you tackle first.
