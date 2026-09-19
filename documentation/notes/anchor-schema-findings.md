# Findings — anchor schema (U2 slice 1), 2026-08-29

The anchor's `findings_sink`. True problems found while building the shared anchor schema
that belong to OTHER work. Checked against `work/dfu-anchor` at `d7d1676`.

---

## F1 — CLOSED 2026-08-30 — the third reader now reads the same file

`agent-bridge` bind-mounts `scripts/agent-harness/anchor.schema.json` and
`anchor_schema.py` read-only at `/app/anchor/`. It reads **the same file** the harness does;
there is no copy anywhere.

**The finding was wrong on one point, and that point was the whole problem.** It said the
schema is unreachable "at build time and at run time" because the build context is
`../agent-bridge`. The build-time half is right — Docker cannot `COPY` from outside the
context. The run-time half is not: a bind mount is a **host path resolved by the daemon at
container start**, and has nothing to do with the build context.

That single wrong clause is why all three options weighed here carried a cost — generate-in
adds a build step that can be forgotten, widening the context pulls in `OB1/` and every
worktree, moving the canonical file touches the harness's stated module boundary. Each one
assumed a copy had to exist somewhere. None had to.

The cross-reader test now asks the containerised reader the same questions, as this finding
required: `test_the_containerised_reader_sees_the_schema_at_all` (the delivery half) and
`test_all_THREE_readers_report_the_same_problems`, which compares the PROBLEMS and not just
the verdicts — two readers that agree on "invalid" while disagreeing on why have already
drifted, and a third reader does not get a weaker bar. Both run against `agent-bridge:local`
and skip only if Docker or the image is absent; verified as RUN, not skipped.

What this unblocks: the `set_goal` seam consuming anchors, which is the reader U2 actually
needed and the reason this was a blocker rather than a tidy-up.

---

## F2 — merged items carry `fits_anchor`, which answered a different question

Items merged before 2026-08-29 (`coder-rm`, `search-rm`, `watchdog-fix`, `coder-readme`,
`search-readme`) have `fits_anchor` in their queue JSON. From this item on the field is
`fits_codebase`, and it is deliberately NOT a rename of the stored data: the old field
recorded a reviewer's answer to *"is this the thing that was asked for?"*, the new one
records *"does this belong in this codebase?"*. Migrating the value would assert those are
the same judgement.

Nothing reads either field today (`grep -rn fits_anchor` finds only the writer), so this
costs nothing now. It matters the moment anyone builds reporting over merge history — a
naive `fits_anchor OR fits_codebase` would silently merge two different questions.

---

## F3 — the anchor gate has been satisfied by a proxy identity, not a human

`coder-rm`, `search-rm` and `watchdog-fix` all record
`anchor_confirmed_by: claude-orchestrator-proxy`. Only `dfu-inbox` records `profnovice`.

The anchor gate is the mechanism PLAN §0 A2 credits with turning the harness from "passes
every check, ships the wrong artifact" into something that produced the right one. If an
agent-side identity can satisfy it, then for those items the gate recorded a confirmation
that no human gave, and the queue cannot tell the two apart after the fact.

This is A7's finding applied to the harness's own gate: *"Cloud/worktree agents can be
governed normatively" — FALSIFIED. Mechanical wins.* An operator-confirm that is a naming
convention is normative governance, not containment.

**Deliberately not fixed here.** It is U5's subject (containment parity), it needs an
operator decision about what counts as operator identity, and — the honest reason — a
session that cannot self-confirm should not be the one designing the check that stops it
self-confirming. Recorded, not acted on.

---

## F4 — `$reach` is assigned and never read (cosmetic, pre-existing)

`queue.ps1` (the sha-containment guard) does:

    $reach = Invoke-GitCapture @("merge-base", "--is-ancestor", $item.branch, $Sha)
    if ($LASTEXITCODE -ne 0) { ... }

`$reach` is never used; the guard reads `$LASTEXITCODE`. PSScriptAnalyzer flags it. The
logic is correct — `merge-base --is-ancestor` signals through its exit code and prints
nothing — so this is noise, not a defect. Left alone deliberately: this item added
*coverage* for that guard and changing its code in the same breath would have meant the new
drill cases never ran against the guard as it actually was.
