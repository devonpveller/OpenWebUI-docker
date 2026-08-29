# Findings — anchor schema (U2 slice 1), 2026-08-29

The anchor's `findings_sink`. True problems found while building the shared anchor schema
that belong to OTHER work. Checked against `work/dfu-anchor` at `d7d1676`.

---

## F1 — the third reader has nowhere to read from

`agent-bridge` is the reader U2 actually needs (the `set_goal` seam consuming anchors), and
it **cannot see the schema file**. Its build context is `../agent-bridge`
(`agent-org/docker/docker-compose.yml`), and its Dockerfile copies only `app/ profiles/
charters/ floor/ hooks/` — all inside `agent-org/agent-bridge/`. Docker cannot `COPY` from
outside the context, so `scripts/agent-harness/anchor.schema.json` is unreachable from the
image, at build time and at run time.

This is why this item ships two host-side readers and stops there: a third reader is a
*delivery* problem, not a parsing one, and solving it inside a schema item would have been
the wrong shape.

Three options, none obviously right, all cheap to trial:

1. **Generate it in**, the way `agent-org/scripts/gen-worker-configs.py` already generates
   per-worker little-coder configs from the canonical one. Precedent exists; adds a build
   step that can be forgotten.
2. **Widen the build context** to the repo root. Honest but expensive — the context would
   include `OB1/`, `backups/`, every worktree.
3. **Move the canonical schema** to a neutral shared directory that is inside both consumers'
   reach, and have the harness read it from there. Cleanest conceptually; touches the
   harness's module boundary (`MODULE.md` states its public surface).

Whichever wins, the cross-reader test is what keeps it honest — it must be extended to ask
the *containerised* reader the same questions, or the copy will drift silently, which is
exactly the failure `test_harness_config.py` was written to prevent.

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
