# Findings — U3: executable acceptance criteria (2026-08-30)

## DECISIONS entries to append

### 2026-08-30 · U3 · class 2 — acceptance criteria may be prose OR a command
DECISION: An `acceptance` entry is now either a STRING (prose a tester judges) or
          an OBJECT `{check, why}` whose `check` is a command the tester RUNS.
          Both readers accept both forms; `executable_criteria()` and
          `prose_criteria()` partition the list, and `anchor_schema.py <anchor>`
          runs the executable half.
CITED:    §2's U3 row — "executable-criteria support in anchors" — and §10's
          finding→check pipeline: "prose lands in the plan only when no
          executable form exists, and says so".
WHY BOTH FORMS: forcing every criterion to be a command would produce commands
          that pass for the wrong reason. Some criteria genuinely cannot be
          executed; the seam separates them so a tester runs what can be run and
          judges only what cannot, instead of judging everything.
REVERT:   Drop `item_kinds` and the two rules keys from anchor.schema.json and the
          object branch from both readers. Existing string criteria are
          unaffected — backward compatibility is asserted by a test, because
          every anchor in the queue today uses strings.

---

## F1 — the cross-reader test earned itself again

The first run disagreed: Python's `json.dumps` defaults to `", "` / `": "` while
PowerShell's `ConvertTo-Json -Compress` emits none. Both readers refused the same
anchor for the same reason and produced DIFFERENT text, which the test caught
because it compares PROBLEMS rather than verdicts. Two readers that agree on
"invalid" while disagreeing on why have already drifted.

## F2 — the runner lists prose, never judges it

`anchor_schema.py <anchor.json>` exits 1 if any executable criterion fails, 2 if
the anchor cannot be read or is not usable, 0 otherwise — and prints the prose
criteria under "For a human to judge (NOT run)".

A runner that silently ignored prose would let a reviewer read "all criteria
passed" over criteria nobody looked at. The count is printed either way so an
anchor with zero executable criteria cannot be mistaken for one that passed.

## F3 — nothing yet CONSUMES this in the pipeline

`queue.ps1` does not run an item's executable criteria at test time. That is the
next U3 slice (tester-finding → durable-check), not this one — but recorded so
nobody reads "executable criteria supported" as "executable criteria enforced".
The capability exists and is runnable by hand; the pipeline does not call it yet.
