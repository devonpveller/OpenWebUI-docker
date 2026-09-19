# Item D (dfuw2d) — findings turned up while making §2's columns name their check

Work item: PLAN.md §2's *Validated by* columns for U0/U1/U2/U4/U5/U7 + the matching
WALKTHROUGH.md paragraphs. Commit `0935ec4` on `work/dfuw2d`.

Measured from a clean clone at `0935ec4`
(`C:\Users\yamao\AppData\Local\Temp\dfuw2d-clean`, `git status --porcelain` empty,
1101 tracked files, OB1 at `b604d55`), `dfu-done.ps1 -Only @(1,5)`, 2026-09-02.

None of these are in this item's scope. They are recorded here rather than fixed, per
§C.10 and the "findings go to documentation/notes" rule.

---

## 1. `smoke-agent-memory.ps1` was RED on its first execution and GREEN on its second, in the SAME run and the SAME sandbox

The one command WALKTHROUGH.md's U1 `How to run` marker names is executed twice by a
`-Only @(1,5)` run — once for clause 1, once for clause 5 — in the same sandbox clone
`dfu-done-clean-f87e0fbe`, minutes apart, with the sandbox `git reset --hard` + `git clean -qfd`
between phases. It came back:

```
clause 1 / U1 (exit 1)   powershell ... -File scripts/checks/smoke-agent-memory.ps1
clause 5 / U1 (exit 0)   powershell ... -File scripts/checks/smoke-agent-memory.ps1
```

The probe's captured stderr for the red is one character (`^`), which says nothing.
Re-run standalone from the same clean clone immediately afterwards: **exit 0**,
`ALL AGENT-MEMORY SMOKE CHECKS PASSED`, 23 checks. So: **red once, green twice, cause not
determined.**

What this is NOT: it is not caused by item D's change. That change edited two markdown
files and did not touch the command string, the script, or anything the script reads —
and the identical command string ran green in clause 5 of the very same run.

Why it matters: this red is the ONLY reason clause 1 reports **UNMET** rather than
UNEVALUATED at `0935ec4`. A check whose verdict depends on whether it is the first or the
second execution in a sandbox is a check that cannot be trusted either way, and U1's row is
the one row whose marker builds a container image and binds a port (`:18099`), which is
where an order- or contention-dependent failure would live. Whoever owns
`scripts/checks/smoke-agent-memory.ps1` should make the failure say what failed: one `^` on
stderr is not a diagnosis.

## 2. U6's column has the same "names no runnable artifact" defect — it is only hidden because U6 has no marker

`dfu-done.ps1:2092-2097` only emits `<id>-check-matches-section-2` for a phase that HAS
walkthrough commands. U6's `How to run` marker was deliberately removed, so the probe is not
emitted for it — but §2's U6 column ("Gym: an unattended run that hits each andon condition
halts-and-raises; one that hits none lands with a complete audit trail") still names no
runnable artifact. The 2026-09-01 report in `documentation/notes/dfu-done-final-2026-09-01.md`
shows this directly: back when U6 still had a marker, it carried
`[indeterminate] U6-check-matches-section-2` and a `MANUAL:PENDING section-2-column-mapping-U6`.

So the moment U6's marker is restored, U6 lands back on the wall item D just cleared.
Deliberately NOT fixed here: the freeze brief is explicit that U3 and U6 name their checks and
record them RED on purpose, and touching U6's column while its marker is absent risks the
appearance of the exact substitution that decision refused. It is a latent, not a live, defect.

## 3. The manual check `dfu-done.ps1` registers for this case can never lift the probe it accompanies

Read from the code, and it is the reason item D exists rather than a hand-recorded answer:

- `dfu-done.ps1:2092-2097` sets `<id>-check-matches-section-2` to `indeterminate`
  **unconditionally** when the column names no artifact, and calls `Add-ManualCheck` for
  `section-2-column-mapping-<id>`.
- `Add-ManualCheck` (`:454-469`) reads any recorded answer and sets the manual entry's state
  to `recorded` / `recorded-fail` / `PENDING`. It does not touch the probe.
- `Resolve-ClauseVerdict` (`:372-416`) tests `$indet.Count -gt 0` → **unevaluated** at line
  393, BEFORE it looks at `$pendingManual` at line 413.

So recording a passing manual answer in `dfu-done-manual.json` moves the manual entry out of
PENDING and changes nothing: the indeterminate probe still forces the clause to `unevaluated`.
The probe's own remedy text offers two options — "confirm by hand ... **or** make the column
name its check" — and only the second one actually works. Not fixed here (`dfu-done.ps1` is
out of item D's scope), and it no longer bites clause 1 because the columns now name their
checks; it will bite again for any future phase whose column does not.

## 4. U1's column now names four artifacts and one of them is re-run — deliberately visible

§2's U1 column asked for "the memory-plane plan's own per-phase gates". Those gates
(sibling repo `documentation-plans-ai-stack/implementation-guide/agent-memory-plane/PLAN.md`,
gates 1.3 / 1.4 / 2.5) name four runnable artifacts in this repository:
`scripts/checks/smoke-agent-memory.ps1`, `scripts/checks/test-quartz4-offline.ps1 -Phase unit`,
`openbrain-gateway/smoke_test.py`, and the `agent-org/agent-bridge` suite. The walkthrough's
marker runs the first. The second is already recorded RED in WALKTHROUGH.md and filed in
`documentation/notes/dfu-c15-clean-clone-check-audit.md`; the third and fourth were not run in
this round and are not claimed. The column now names all four and says the one does not stand
in for the other three, so the shortfall is stated in the anchor rather than left implied.
That is a red that should stay red until someone runs the other three.
