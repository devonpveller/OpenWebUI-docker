# SmolCrawl OWUI Pipeline

An Open WebUI Pipeline that crawls websites and uploads the content to a knowledge base — directly from the chat UI.

**This does NOT start a new Open WebUI instance.** It adds a Pipelines server that connects to your existing Open WebUI (e.g. the `ai-stack` at `127.0.0.1:3000`).

## Quick Start

### 1. Start the Pipelines server

```bash
cd integrations/open-webui
docker compose up -d
```

This starts only the **Pipelines server** at `http://localhost:9099` with SmolCrawl pre-installed.

### 2. Connect to your existing Open WebUI

In your Open WebUI admin panel:

1. Go to **Admin Panel → Settings → Connections**
2. Under **Pipelines**, set the URL to: `http://smolcrawl-pipelines:9099`
   - This works because the container joins the `ai-stack_default` Docker network.
3. Save and refresh — **SmolCrawl Knowledge Builder** will appear as a selectable model.

### 3. Use it

1. Select **SmolCrawl Knowledge Builder** as the model in your chat.
2. Type a URL to crawl:
   ```
   crawl https://docs.example.com
   ```
3. Watch live progress as SmolCrawl crawls, augments, and uploads to a knowledge base.

## Configuration

Configure via the OWUI admin panel under **Pipelines → SmolCrawl Knowledge Builder → Valves**:

| Setting               | Default                 | Description                                        |
| --------------------- | ----------------------- | -------------------------------------------------- |
| `owui_base_url`       | `http://localhost:3000` | Open WebUI API URL                                 |
| `owui_api_key`        | (empty)                 | API key for authentication                         |
| `knowledge_base_name` | (auto)                  | KB name (auto-generated from domain if empty)      |
| `server_intensity`    | `0.3`                   | Crawl aggressiveness (0.0 gentle – 1.0 aggressive) |
| `max_pages`           | `200`                   | Maximum pages to crawl                             |
| `upload_concurrency`  | `3`                     | Parallel upload workers                            |
| `augment_for_rag`     | `true`                  | Enable RAG metadata injection                      |

> **Note:** Since both containers share the `ai-stack_default` network, set `owui_base_url` to `http://openwebui:8080` (the container name and internal port) so the Pipelines container can reach Open WebUI directly.

## Alternative Install Methods

### URL Install (no Docker needed)

If you already have a Pipelines server, install the pipeline via the OWUI admin panel using the raw file URL of `smolcrawl_pipeline.py`. SmolCrawl must be pip-installable on that server.

### Local Dev

```bash
pip install smolcrawl
# Copy smolcrawl_pipeline.py to your Pipelines server directory
```

## How It Works

1. **Crawl** — Uses SmolCrawl's async crawler to fetch pages from the target URL
2. **Augment** — Normalizes headers and injects RAG metadata blocks
3. **Upload** — Uploads .md files to an OWUI knowledge base with incremental sync

Re-crawling the same URL performs an incremental sync — only changed pages are re-uploaded.
