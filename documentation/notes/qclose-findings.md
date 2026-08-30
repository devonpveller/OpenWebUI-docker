# Findings — the stale anchor-draft queue rows (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · harness · class 2 — close the rows out, do not stop creating them
DECISION: Six queue rows (`ampolicy`, `dfu-anchor`, `dfu-mem0`, `hookattest`,
          `lc-restore`, `memplane1`) sat in `anchor-draft` while their work was
          merged. Closed with a new terminal state `closed-outside-gates` and a
          per-row reason naming how many merges landed. The queue mechanism is
          KEPT — the answer to "close them out or stop creating them" is the
          first.
CITED:    §C.1 — U0–U7 items do not run through queue.ps1's gates. These rows
          were created before that clause and were never going to reach a
          terminal state through a pipeline this effort does not use.
WHY NOT -Reject: 'rejected' asserts a reviewer turned the work down. It merged.
          Recording it as rejected would put a false statement into the audit
          trail §C.7 makes the deliverable's twin, and that trail is only worth
          anything because nobody writes convenient things into it.
WHY KEEP THE QUEUE: three rows are NOT stale — `bridge-bg-task-note`,
          `podcast-delivery-key`, `podcast-script-fallback` have zero merges and
          belong to other efforts. The mechanism is in use; only this effort
          bypasses it.
REVERT:   Each row's `history` records the state it was in; set `state` back and
          drop `closed_reason`.

---

## F1 — the operator counted six, there are nine

Six were stale. The other three (`bridge-bg-task-note`, `podcast-delivery-key`,
`podcast-script-fallback`, all `developer=wt-podcast-fix`) have **no merge on the
line** and are genuinely open work from another effort. They were deliberately
left alone.

Verified per row with `git log --oneline refactor/ai-stack-cleanup | grep -c "work/<id>"`:
ampolicy 2, dfu-anchor 1, dfu-mem0 1, hookattest 2, lc-restore 1, memplane1 1,
and 0 for each of the three.

## F2 — `-CloseOut` refuses without a reason

A row closed with no reason is a row nobody can account for later, which is the
same failure the stale rows already demonstrated in the other direction. The
refusal is verified: `queue.ps1 -CloseOut -Id ampolicy` (no `-Reason`) errors.
