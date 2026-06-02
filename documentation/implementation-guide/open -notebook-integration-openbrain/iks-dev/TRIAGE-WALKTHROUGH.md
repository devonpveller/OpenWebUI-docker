# Triage walkthrough (sandbox demo)

Realistic scenario loaded into `iks-db` to show how the triage options fit a
real workflow. Open **http://127.0.0.1:18502** → **Triage** (left nav, Process
section). Data is synthetic; your real stack is untouched.

## The setup

Four research threads a self-hoster might actually run, each with 6 confirmed
sources:

- **Self-hosting AI on a home server**
- **Private AI for small business**
- **Building a private second brain**
- **Cutting cloud and SaaS costs**

The OB1 suggestion worker embedded all 24 sources (bge-m3) and proposed **10
cross-thread links** at a tuned similarity ≥ 0.66 (lower thresholds drown the
signal — open-Q #1: this is exactly the kind of tuning the worker exposes).

## The workflow each option serves

**1. A source obviously belongs in another thread → Accept.**
- *Private AI for small business* ← **"Why I Moved My AI Workloads In-House to
  Keep Data Off the Cloud"** (0.74). You filed it under self-hosting, but it's
  fundamentally a privacy argument. **Accept** → it now also shows in the
  Private-AI thread's source list, indistinguishable from a source you added
  there directly. Your research in one thread enriched another **without you
  re-finding it** — and it was never auto-added; you chose.
- *Cutting cloud costs* ← **"The Real Cost Math: Self-Hosted GPU vs Pay-Per-Token
  API"** (0.72). Clearly relevant. Accept.

**2. A source is plausibly related but not what this thread is about → Hide.**
- *Private AI for small business* ← **"Self-Hosting Local RAG: Semantic Search
  Over Your Own Notes"** (0.68). Defensible, but it's about *personal notes*,
  not *customer data*. **Hide** → it leaves the queue and lands in the Hidden
  pool. Nothing is deleted.

**3. Research direction shifts → Restore.**
- Switch to the **Hidden pool** tab. The source you hid is still there. If the
  business later decides its internal knowledge base IS in scope, **Restore** →
  it returns to the queue for reconsideration. "What was irrelevant last month
  may be critical today" (concept §2 principle 5).

**4. You formed a take → Send to Obsidian** (backend wired; button placement
in the notebook header is the remaining FE touch). Drops a drafting *stub*
into the wiki `notes/` folder — a starting point, not a finished note.

## Try this exact sequence

1. Open Triage. You'll see **10** suggestions across the threads (badges: 3/3/3/1).
2. **Accept** "Why I Moved My AI Workloads In-House…" → watch it drop from the
   queue (it's now confirmed in *Private AI for small business*).
3. **Hide** "Self-Hosting Local RAG…" → it leaves Suggestions.
4. Open the **Hidden pool** tab → it's there. **Restore** it → back in Suggestions.
5. **Refresh** to confirm state persists (it's all in OB1, not browser state).

## Reset the demo

```bash
cd iks-dev
python load-scenarios.py scenarios.json     # reload threads + sources (clears prior)
curl -s -X POST "http://127.0.0.1:18810/suggest?threshold=0.66"   # regenerate the 10
```
