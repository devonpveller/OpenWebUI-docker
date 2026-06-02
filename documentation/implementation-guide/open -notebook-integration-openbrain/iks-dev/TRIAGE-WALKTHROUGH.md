# Triage walkthrough (sandbox demo) — popover-behind-a-button design

Suggestions live **behind a button** (a floating popover), in two places —
no inline panels, no broken layout, easy to re-add after upstream updates.
Open **http://127.0.0.1:18502** (hard-refresh). Synthetic data; prod untouched.

## The setup

4 research threads ↔ 4 notebooks, 6 sources each (24 total). The OB1
suggestion worker proposed **10** cross-thread links (threshold 0.66); the
"bridge" sources (e.g. a self-hosting article that's really about privacy)
score highest. **9 sources** carry a suggestion.

## Placement 1 — the Sources page (`/sources`, in the nav)

The global sources list now reflects Open Brain sources. A new **Suggestions**
column shows a `💡(##)` button only on sources that have cross-thread
suggestions.

- Click the `💡(2)` on **"Why I Moved My AI Workloads In-House…"** → a
  shadow-boxed popover lists the **threads** this source is suggested for
  (e.g. *Private AI for small business*, *Cutting cloud costs*), each with
  **＋ Add** and **🚫 Hide**.
- **Add** links the source to that thread (it now belongs to it). **Hide**
  dismisses (recoverable). The badge count updates; when none remain, the
  button disappears.

## Placement 2 — inside a notebook (the header)

Open any notebook → a **💡 Suggestions (N)** button sits in the header (next
to Archive/Delete). It does **not** expand the page — it's a button; the list
floats above on click.

- Open **Private AI for small business** → **💡 Suggestions (3)** → popover
  lists the 3 **sources** suggested for this thread, each with **＋ Add** /
  **🚫 Hide**. Add pulls the source into this thread's source pool.

Same component, both places — the suggestion is always a (thread, source)
pair, so Add/Hide are symmetric.

## Why this shape

- **Non-invasive:** the popover portals (floats, `z-50`); it never touches
  Open Notebook's fixed-height layout. If ON updates, just re-drop the button.
- **No duplication:** the notebook's existing Sources list is untouched; we
  added a button, not a second list.
- **Focused:** you only ever see suggestions relevant to the source or thread
  in front of you — not one global pile.

## Reset the demo

```bash
cd iks-dev
python load-scenarios.py scenarios.json          # reload threads + sources
python wire-demo-notebooks.py                     # map 4 notebooks -> threads
curl -s -X POST "http://127.0.0.1:18810/suggest?threshold=0.66"   # regenerate the 10
```
