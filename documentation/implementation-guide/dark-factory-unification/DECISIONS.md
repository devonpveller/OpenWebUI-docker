# DECISIONS — dark-factory unification implementation log

Append-only. One entry per class-2/3 call made under PLAN.md §C. Format:

```
## <UTC timestamp> · <phase> · class <2|3>
DECISION: <what was decided>
CITED:    <the §C rule / pinned anchor / house pattern relied on>
REVERT:   <the concrete path back if the operator disagrees>
```

Class-3 entries are QUESTIONS batched for the operator — answered defaults,
never blockers. Check this log for precedent before deciding anything: the
same question is never asked (or re-decided) twice.

---

## 2026-08-29 · plan · class 2 (seed entry, by the planning session)
DECISION: §4's four open decisions resolved to standing defaults — reviewer
          verdict folds into U2; dark-mode is per-anchor; the unified config
          is a shared org.config.json with multiple readers; the cadence
          scheduler is supercronic.
CITED:    §C.3 (operator pre-authorized the recommended defaults, 2026-08-29,
          when granting autonomous execution).
REVERT:   Each is a config or naming change; none is load-bearing until U2+
          lands. Flip the default and re-run the affected phase's validation.

---

## 2026-08-29 · U1 Phase 0 · class 2
DECISION: The anchor names three bugs in `audit_sink._mirror`; there are FOUR.
          The tool argument is `metadata_extra`, not `metadata`
          (`OB1/integrations/kubernetes-deployment/index.ts:848`, schema at
          `:361`), so the old call would have dropped its provenance even with
          the path fixed. Fixed alongside the other three and covered by a test.
          Also: the anchor calls `x-brain-key` a "wrong auth" bug. It is NOT —
          `x-brain-key` is correct for openbrain-mcp (`index.ts:2053`, and the
          gateway sends the same header upstream at `app.py:142`). It only
          looks wrong against the CLOUD gateway, which we are no longer
          targeting. Anchor amended on the record rather than "fixed" by
          changing a correct header.
CITED:    §C.1 (amend on the record and continue) + §C.2 class 2 (a discovered
          gap; the option consistent with the pinned wire evidence).
REVERT:   Both are one-line changes in `openbrain_client.py`; the tests name
          the exact source lines, so re-verification is a re-read, not a rerun.

## 2026-08-29 · U1 Phase 0 · class 2
DECISION: `ProjectContext.invalidate` became `async` and now clears the durable
          row too. It had ZERO callers, so no signature was broken.
CITED:    §C.2 class 2 — persistence introduced a regression in a method that
          previously worked: clearing only memory would be silently undone by
          the next restart, so "invalidate" would stop invalidating.
REVERT:   Drop `delete_fn` + the `await` and make it `def` again; the durable
          row simply outlives an invalidate, as it would have anyway.

## 2026-08-29 · U1 Phase 0 · class 1/2
DECISION: TWO journal backup sidecars (one per volume), not one covering both.
CITED:    §C.2 class 1 (house pattern) — every entry in
          `restore-from-snapshot.ps1` maps ONE archive to ONE volume. A
          combined archive would have had no mechanical restore path.
          Both are `profiles: ["workers"]`: `check_backups.py` treats a RUNNING
          sidecar with no artifacts as stale, so ungated they would alarm
          precisely when the org is correctly quiesced.
REVERT:   Merge into one sidecar + one archive, and add a restore type that
          extracts subdirectories into separate volumes.

## 2026-08-29 · U1 Phase 0 · class 2
DECISION: Applied the FULL SERVICE-LIFECYCLE checklist, not only the anchor's
          row 7 — the phase adds two CONTAINERS, which triggers CLAUDE.md's
          container rule. Rows deliberately NOT actioned, each with a checked
          reason: row 4 (recovery) — `$Script:AgentOrgServices` lists only
          default-profile services (`ao-worker-*` are absent for the same
          reason); row 5/6 (health probe, watchdog) — no port, no serve route,
          consistent with the existing backup sidecars; row 8
          (`stack-services.json`) — agent-org has NO rows in that file at all,
          so adding only these two would misrepresent the plane.
CITED:    CLAUDE.md container rule + SERVICE-LIFECYCLE; §C.2 class 2 (the
          binding workspace rule outranks the anchor's narrower wording).
REVERT:   Each surface is an additive row; delete the two rows to undo.

## 2026-08-29 · U1 Phase 0 · class 3 (QUESTION, defaulted, not blocking)
DECISION: `AO_OPENBRAIN_URL` placeholders were said to be missing from the root
          `.env.example`. They already exist in `agent-org/docker/.env.example`
          — the file the agent-org project actually reads — with the
          unreachable gateway value. Fixed there; root `.env.example` left
          alone rather than adding keys nothing reads.
CITED:    §C.2 class 3 — no technical winner, the operator may prefer the root
          file to mirror every key. Defaulted to "the file the compose project
          reads" and kept moving.
REVERT:   Add the two keys to the root `.env.example`; nothing consumes them.

---

## 2026-08-29 · U1/U2 · OPERATOR OVERRIDE (not a class-2 call)
DECISION: Self-reviewed and merged `work/dfu-mem0` and `work/dfu-anchor` into
          `refactor/ai-stack-cleanup`, bypassing BOTH the anchor gate (neither
          item was operator-confirmed) and separation of duties (neither was
          tested by a second party).
CITED:    Explicit operator instruction, 2026-08-29: "You're free to review,
          merge, then confirm success with validation." NOT §C — §C.2 lists
          merges to `main` as class 4 and says nothing that authorises
          self-merge to the work line; this is the operator overriding their
          own pipeline, recorded as such. The concern was raised once and
          reaffirmed, which per house rules makes it their decision.
          The queue items were deliberately NOT marked `merged`: they never
          went through the pipeline, and recording otherwise would corrupt the
          one record that says what the pipeline actually did. They remain
          `anchor-draft` with the work merged — accurate, if untidy.
          No reviewer identity was invented to satisfy the queue's exit-4
          check; passing a different worktree id would have been gaming a
          mechanical guard, which is the failure class this plan exists to stop.
REVERT:   `git revert -m 1 5c18c26` and `git revert -m 1 0f528d1`, in that
          order. Both merges are --no-ff, so each is one revertible commit.

## 2026-08-29 · U1 Phase 0 · ANCHOR CONTRADICTED BY REALITY (§C.1 amendment)
DECISION: The dfu-mem0 anchor's goal says the work lands "with no change to
          live behaviour, because every flag stays off." **That premise is
          false in production.** `agent-org/docker/.env` sets
          `AO_OPENBRAIN_MIRROR_ENABLED=true`, and the RUNNING agent-bridge has
          it true. The mirror is not dormant — it has been enabled and failing
          silently. Evidence: 26 events of mirrorable kinds in
          `agent-bridge-db`, every one `mirrored=false`; last such event
          2026-08-24 (which is why a 72h log window showed no warnings).
          Amended on the record and continued, per §C.1. The work is MORE
          valuable than the anchor claimed, not less: it repairs a live path,
          not a hypothetical one.
CITED:    §C.1 (amend on the record, log it, continue). Root cause of the bad
          premise: I verified the code default (`config.py`) and the compose
          default (`${...:-false}`) and never opened the prod `.env` — exactly
          the trap CLAUDE.md names ("verify against gitignored evidence …
          `.env*` values are where 'zero references' verdicts die").
REVERT:   Nothing to revert; this is a correction to the record, not a change.

## 2026-08-29 · U1 Phase 0 · class 4 — NOT DONE, handed to the operator
DECISION: Did NOT repoint production's `AO_OPENBRAIN_URL` at
          `http://openbrain-mcp:8000`, even though that is the fix and the
          merge is now live. Reason: the prod `AO_OPENBRAIN_KEY` is the
          GATEWAY key (`gw-` prefix) and does not equal `MCP_ACCESS_KEY`, so
          repointing the URL without also swapping the credential would turn a
          silent connect-failure into a silent 401. Swapping it is a
          credential change.
CITED:    §C.2 class 4 — "Anything touching the personal data plane,
          credentials, or secret values." The operator's grants this session
          cover merging and container restarts/rebuilds; neither covers
          rotating a secret into a new service.
REVERT:   n/a (nothing done). To ACT: set both
          `AO_OPENBRAIN_URL=http://openbrain-mcp:8000` and
          `AO_OPENBRAIN_KEY=<MCP_ACCESS_KEY from OB1/docker/.env>` in
          `agent-org/docker/.env`, recreate agent-bridge, then confirm a
          mirrorable event flips `mirrored` to true.

## 2026-08-29 · U1 Phase 0.3 · class 2 (deploy sequencing)
DECISION: Recreated `ao-worker-1/2` to attach the journals volumes, but
          EXTRACTED the existing journals first and restored them into the new
          volumes, fixing ownership to `lc` (10002:10002) afterwards.
CITED:    §C.2 class 2 + the operator's container grant ("be precise with the
          containers"). A plain `up -d` would have silently destroyed 156K and
          236K of journals — six days of corpus living on the containers'
          writable layer, which is the very hole 0.3 exists to close. Doing the
          upgrade carelessly would have caused the loss it prevents.
REVERT:   Remove the two volume lines + the two sidecars from the compose file
          and recreate; the rescued copies also remain under the session
          scratchpad `journal-rescue/`.
