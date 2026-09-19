# Findings sink — wiki fallback parity (item `wiki-note-width`, 2026-09-03)

Findings sink for the anchored work "a not-yet-built note should look like the
built one, and refresh only when saved + idle". Everything below was turned up
while doing that work. Each entry says what was checked and how, so the next
reader does not have to re-derive it. Where a finding is out of scope it stays
here and did **not** get pasted into the artifact.

## 1. Two CSS rules in the fallback document were DEAD, and named the wrong cause

`OB1/docker/wiki-viewer/lib/page-document.mjs` shipped

```
#quartz-body{display:block}
.center{max-width:750px;margin:0 auto;padding:0 1.5rem}
```

Neither ever applied. The built stylesheet (checked in the running container,
`docker exec openbrain-wiki-viewer sed -e 's/}/}\n/g' /srv/current/index.css`)
carries

```
.page>#quartz-body{grid-template:"grid-sidebar-left grid-header grid-sidebar-right"…/320px auto 320px;display:grid}
.page>#quartz-body .center,.page>#quartz-body footer{min-width:100%;max-width:100%;margin-left:auto;margin-right:auto}
```

`.page>#quartz-body` (1,1,0) out-specifies a bare `#quartz-body` (1,0,0), and
`.page>#quartz-body .center` (1,2,0) out-specifies a bare `.center` (0,1,0), so
index.css won both. The document's real geometry was therefore: `#quartz-body`
is a 320px/auto/320px **grid**, and `.center` is its only child — CSS
auto-placement drops it in the first cell, the **320px left gutter**. That is
the "very narrow window" the operator reported.

The symptom is itself the proof the two rules never won: had `display:block`
applied, the note would have rendered full-width or at 750px, not narrow.

Worth generalising: an override written next to the markup it targets *looks*
authoritative. This one sat in the file for a week, was quoted in a code comment
as the reason the layout was safe, and did nothing. Read the cascade, not the
adjacent rule.

## 2. `Done editing` reloaded even when the save had FAILED (fixed — same criterion)

`NotesEditor.inline.ts` `exitEdit()` did `await save(body)` then
`location.reload()` unconditionally. `save()` returned `undefined` and swallowed
409/HTTP-error/network outcomes into a status string. So a note whose PUT came
back `409 changed elsewhere` was reloaded away, taking the only copy of the
user's text with it.

Fixed in this item rather than parked, because the anchor's SAVED-GATE criterion
is literally "no reload can discard a keystroke": `save()` now returns whether
the file took the edit, and `exitEdit()` leaves the editor open (beforeunload
flush still armed) when it did not.

## 3. `doMove()` ignores whether its pre-move save succeeded — NOT fixed

Same file, the `⤴ Move` handler flushes the open draft with
`try { await save(...) } catch {}` and moves regardless. A failed flush means the
file moved on disk is the pre-edit one. Less severe than #2 (the move itself
carries an `If-Match` guard on `lastHash`, so a genuinely stale hash is rejected
server-side), and it is a different code path from the one this item is about.
Left alone deliberately; `save()` now returns a boolean, so the fix is a
one-line `if (!(await save(...))) return` whenever someone picks it up.

## 4. Host `node --test` cannot run the viewer's lib tests

`lib/render-page.mjs` imports unified/remark/rehype, which are **Quartz's**
transitive deps and exist only inside the image. Running `node --test lib/` from
a checkout fails with MODULE_NOT_FOUND and tells you nothing. Run the real gate
instead — mount the lib over the image's copy:

```
docker run --rm --network none --entrypoint sh \
  -v "<repo>/OB1/docker/wiki-viewer/lib:/quartz/lib:ro" -w /quartz \
  openbrain-wiki-viewer:local -c "node --test lib/*.test.mjs"
```

Same shape for the esbuild gate, mounting the single `.inline.ts` over
`/quartz/quartz/components/scripts/`. Read-only, `--network none`, no lease
needed — it never touches the running plane.

## 5. The document is a template literal — a backtick in a comment breaks it

Already warned about in the file for the inline `<script>`; it bites the
`<style>` block too. A backtick inside a CSS comment terminated the literal and
the tests died with `SyntaxError: Private field '#quartz' must be declared in an
enclosing class` — a message that points nowhere near the actual edit. Long
explanations belong in real JS comments above the `return`, not inside the
emitted document (they also ship to every client on every fallback page).
