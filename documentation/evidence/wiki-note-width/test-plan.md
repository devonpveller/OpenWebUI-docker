# Test plan — `wiki-note-width`

Branch `work/wiki-note-width`, worktree `wt-wiki-note-width`.
Changes: `OB1/docker/wiki-viewer/lib/page-document.mjs`,
`OB1/docker/wiki-viewer/lib/render-page.test.mjs`,
`OB1/docker/wiki-viewer/quartz-overlay/quartz/components/scripts/NotesEditor.inline.ts`,
plus the parent gitlink bump and `documentation/notes/wiki-fallback-parity-findings.md`.

**You are testing whether a not-yet-built note looks like a built one, and
whether the automatic refresh can eat someone's typing.** Cases 1–3 are cheap
and mechanical. Cases 4–7 need a browser and are where a real defect would hide;
do not substitute a grep for them. A grep proves SHIPPED, not BEHAVES.

## Environment

Build a test image from the worktree — tag `:wt-wiki-note-width`, never
`:local`, and do not attach anything to the `ai-stack_*` anchor networks:

```
docker build -t openbrain-wiki-viewer:wt-wiki-note-width \
  <worktree>/OB1/docker/wiki-viewer
```

Cases 4–7 need a running viewer + workbench. Take the leases for the planes you
bring up before you start (`scripts/agent-harness/lease.ps1 -Acquire -Name <plane>
-Owner wt-<your id>`, names in `lease-names.conf`, all planes in ONE call).
Cases 1–3 need no lease and no running stack.

---

## Case 1 — the unit gate passes (and the image builds)

The two new asserts run as an image-build gate.

```
docker run --rm --network none --entrypoint sh \
  -v "<worktree>/OB1/docker/wiki-viewer/lib:/quartz/lib:ro" -w /quartz \
  openbrain-wiki-viewer:local -c "node --test lib/*.test.mjs"
```

**Pass:** 23/23, including `LAYOUT: the fallback places .center in the built
page's grid column` and `REFRESH: the takeover reloads only when the page is
built, saved AND the user has paused`.
**Fail:** any failure, or fewer than 23 tests (a lost test is a lost gate).
Also fail if the `docker build` above does not complete — the same command runs
inside it, along with the esbuild compile check of the patched inline scripts.

## Case 2 — the new asserts are not vacuous (RED against the old file)

A check that passes while checking nothing is the failure mode this repo keeps
hitting. Prove these two bite:

```
mkdir /tmp/redlib && cp <worktree>/OB1/docker/wiki-viewer/lib/*.mjs /tmp/redlib/
cd <worktree>/OB1 && git show HEAD~1:docker/wiki-viewer/lib/page-document.mjs > /tmp/redlib/page-document.mjs
docker run --rm --network none --entrypoint sh -v "/tmp/redlib:/quartz/lib:ro" \
  -w /quartz openbrain-wiki-viewer:local -c "node --test lib/render-page.test.mjs"
```

(`HEAD~1` = the OB1 commit before this change. Confirm with `git log --oneline -2`
that you grabbed the pre-change file — it should still contain `max-width:750px`.)

**Pass:** exactly the two new tests fail; every pre-existing test still passes.
**Fail:** they pass against the old file (the asserts test nothing), or an
unrelated test also fails (the fixture is wrong, not the code).

## Case 3 — the A-1 / A-4 invariants are untouched

Read the diff of `page-document.mjs`.
**Pass:** read-only pages still emit no `<script src=`, no `data-note-path` /
`data-folder-rel` / `data-entity-id` etc.; `editorContract()` is unchanged;
every caller in `serve.mjs` still sends `cache-control: no-store`.
**Fail:** any identity attribute or bundle tag reachable from a non-note page,
or a changed cache header.

---

## Case 4 — LAYOUT: a pre-build note matches the built note (THE headline case)

You need one note served from the fallback and one served from a build. The
fallback is what you get right after creating a note (`x-wiki-render: fresh` or
`db` in the response headers); the built page has no such header. Check the
header with devtools — do not guess which one you are looking at.

At **each** of three window widths — ~1600px (desktop), ~1000px (tablet),
~500px (mobile) — open a fallback note and a built note and compare:

- the **width** of the prose column, and
- the **left edge** of the prose column.

Measure them (devtools element box, or a screenshot with a ruler). "Looks about
right" is not a result.

**Pass:** at every width the two agree — same column width, same left edge,
within a few px. In particular at 1600px the fallback note is NOT a ~320px strip
in the far-left gutter (that is the reported bug) and NOT a 750px column centred
in the window (that is the fix's predecessor).
**Fail:** any width where they visibly differ. Report the measured numbers for
both pages at all three widths either way — the numbers are the evidence, the
verdict is not.

## Case 5 — REFRESH does still happen

On a fallback note page (`x-wiki-render` present), with **no editor open**:
leave the mouse and keyboard alone and wait for the wiki to rebuild that page.

**Pass:** within a few seconds of the page becoming built, the browser reloads
itself and you land on the full built page (sidebars, explorer, TOC).
**Fail:** it never reloads. The new gates must not become a permanent block —
this is the case that catches an over-tight guard, and it is the one most likely
to be skipped. Do not pass the item without it.

## Case 6 — REFRESH does not fire while you are working

Same setup as Case 5, but this time:

  a. **Typing.** Open the editor (`✎ Edit this note`), type continuously past
     the moment the page becomes built, keep typing for another 30s.
     **Pass:** no reload; nothing you typed disappears.
  b. **Reading.** Close the editor. Scroll / move the pointer continuously past
     the moment the page becomes built, for ~30s.
     **Pass:** no reload while you are moving. Stop moving; it reloads a few
     seconds later.
  c. **Mid-autosave.** Type a distinctive word, then STOP for ~1.6s so the
     autosave fires, and watch the network panel.
     **Pass:** no reload lands between the keystroke and the PUT's response.
     **Fail:** a reload during that window — that is a lost keystroke.

**Fail** any of a/b/c that reloads while you are active, or that loses text.

## Case 7 — a FAILED save does not get reloaded away

Open the editor on a note, type something. Force the save to fail, then press
`✓ Done editing`. Easiest forcing: devtools → Network → block the request URL
(`/workbench/notes/...`) — offline mode also works.

**Pass:** the page does NOT reload. The editor stays open with your text still
in it and the status line shows the failure (`✗ …` or `⚠ changed elsewhere`).
Unblock the request and press Done again: it saves and then refreshes.
**Fail:** the page reloads and your text is gone. (This is the pre-existing bug
this change fixes; on the OLD code it reloads.)

---

## Reporting

Attach: the Case 1 and Case 2 TAP output, the Case 4 measurements (6 numbers
minimum: width + left edge, fallback vs built, at 3 widths), and a one-line
verdict per case. If a case cannot be run, say so explicitly rather than marking
it passed — a skipped Case 4 or 5 means this item is not tested.
