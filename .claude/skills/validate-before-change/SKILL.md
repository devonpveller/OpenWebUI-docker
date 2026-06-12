---
name: validate-before-change
description: >
  Discipline for the changes that are easy to get catastrophically wrong: deleting
  data/files/rows, editing code you have NOT confirmed is the live deployed path
  (vs dead/legacy/duplicate), and claiming "this fixes it". Invoke BEFORE any delete,
  BEFORE editing code whose deployment you haven't verified, and AFTER a change to
  prove it was both valid and necessary. Prevents the three failure modes: editing
  dead code, deleting before checking origin/connectivity/timeline, and presenting a
  no-op or unnecessary change as a verified fix.
---

# Validate before you change; prove it after

A change is not done when the edit lands or the row is deleted. It's done when you've
shown three things: the **target was the right one**, the change **runs on the path that
actually executes**, and it was **necessary**. This skill exists to stop three specific
failure modes:

1. **Editing dead code** — changing a file/function that isn't what's deployed (a legacy
   copy, a same-named duplicate, a pre-cutover version).
2. **Deleting before checking** — removing data/files before establishing what they are,
   what references them, when/what created them, and how to reverse it.
3. **Declaring an unvalidated fix** — claiming a change works without proving it runs live
   and that the problem was real (it may be a no-op, or already resolved elsewhere).

> **Core rule: evidence before action, proof after.** Never assume the artifact you're
> touching is the live one. Never delete before you can name it, its references, its
> origin, and its recovery path. Never claim a fix without showing it changed the live
> behavior and that the problem actually existed.

---

## 1 · Diagnose with evidence (before proposing any fix)

- **Observe the problem directly** in the real data/logs/UI — don't infer it from a
  plausible story.
- **Root-cause from evidence**: logs, journals, git history, the *running* config — not
  the first explanation that fits.
- State: *what's wrong, where, and the proof.* If you can't cite evidence, you're guessing
  — say so and go get it.

## 2 · Validate the TARGET is live + correct (before editing code)

The #1 trap is editing something that isn't the deployed path.

- **Is this artifact actually live?** Verify against reality, not the filename:
  - what the **compose / entrypoint / cron** actually runs;
  - what the service / OWUI tool actually **imports or calls**;
  - the file's **git log + mtime** (months-stale while the system moved on = suspect);
  - whether a **newer version replaced it** (thin client vs heavy tool, v2 vs v1,
    "migrated to …", logic moved server-side).
- **Trace the live path end-to-end** for the behavior you're changing: which container →
  which function → which file. Grep the LIVE entrypoint, not a same-named legacy file.
- If two files share a name/role, **prove which one executes** before editing either.
- 🚩 *Red flags you're about to edit dead code:* not referenced by any compose/entrypoint;
  a "thin client / v2 / replaced" note exists; the same logic now lives in a new service;
  the file hasn't changed since long before the symptom appeared.

## 3 · Validate before you DELETE (most important — usually irreversible)

- **Identify exactly**: the precise target and why it qualifies (the exact `WHERE` clause
  / glob / id list). No "these look like junk."
- **Check connectivity — quantified**: what references it? (claims, FKs, links, imports,
  callers, citations). Report a number: `0 claims cite them`, not a vibe.
- **Establish origin + timeline**: when and what created it (`created_at`, git, journals).
  This routinely changes the decision — e.g. it's already-resolved legacy, so the fix is
  "nothing" or "clean up", not "edit the code".
- **Capture the pre-state** so you can prove the effect *and* reverse it: counts + a
  sample (and know where a backup is). **Know the recovery path before deleting.**
- **Prefer the least-destructive option** that solves it (hide / flag / exclude from a
  view) unless deletion is clearly correct.
- Delete in a **transaction**; report **exact counts** of every table touched.

## 4 · Prove the change AFTER (valid AND necessary)

A change needs two separate proofs — neither alone is enough:

- **Valid** — it runs *and* on the live path:
  - it compiles / typechecks / `ast.parse`s;
  - the **live behavior changed**, demonstrated. "The `.md` changed" ≠ "the served page
    changed." "I edited the file" ≠ "the running service uses it."
- **Necessary** — the problem was real and this is what addresses it:
  - show the **before → after on the actual symptom**;
  - if the symptom was already gone (resolved by an earlier cutover, a no-op edit), that's
    a **finding, not a win** — report it and undo the unneeded change.
- For deletes: confirm the deleted count matched expectation, dependents were handled, and
  nothing intended-to-keep was touched.

---

## If you skip a step, say so out loud

Acting without a validation (genuine time pressure, the user insisted, it's trivial and
reversible) is sometimes fine — but **name the validation you skipped and why.** Never
present an unvalidated or unnecessary change as verified.

## The check before you hit enter

- **Editing?** → "Have I *proven* this file/function is what actually runs?"
- **Deleting?** → "Do I know exactly what it is, what references it, when/why it was
  created, and how to reverse it?"
- **Claiming a fix?** → "Can I show it changed the live behavior AND that it was needed?"
