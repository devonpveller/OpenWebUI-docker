# P28 — the off-theme filter becomes deterministic (design §6.5/§6.6/§11)

## The principle (operator, 2026-07-24)

Separate **deterministic** from **reasoning**, and place each realistically — bias to determinism where
a mechanical signal exists, but never force it onto an interpretive task:
- **Deterministic = the mechanical, tracked fact.** The real work a task does is its **staged diff to
  the product** — the code that changed. Git operations, commit-message edits, and workspace navigation
  touch no product and are deterministically *not* the work.
- **Reasoning (LLM) = the interpretation.** Whether a task is *relevant / aligned to the goal* is
  evaluative and must be reasoned (before dispatch). You cannot make relevance a regex.

## Where P26 went wrong

P26 F26.1 forced **reasoning** onto a job that has a **deterministic** signal: telling git-meta noise
apart from product work. Asked to judge a fuzzy "is this off-theme?" category, the LLM did what §6.5
warns ("an LLM grading an LLM is a mirror") — it **inverted**. gym-026 evidence, a crude regex tag over
the actual run:
- **Pruned as "off-theme": 10 of 10 were real product code** — `IsADirectoryError` handling, the
  `_repl_argv_add` parser bug, `save_items` exception handling, `priority`/`id` validation. None named a
  commit.
- **Kept and worked as "product": ~13 were git-meta** — "add a verification result to the first commit
  message", "split the second commit for bisecting", "reduce the commit body to 1-3 lines", "add bodies
  to empty merge commits", "annotate duplicate commits b40a91c and ad19ce3".

The lexical signature is clean and separates the two sets with **no overlap**: git-meta *names the git
artifact* (commit / message / body / a SHA / merge / bisect / history); product work *names code*
(functions, files, inputs).

## The trend across every prior attempt

Every **model-judgment** filter of this class has failed; every **deterministic** one has held:

| Attempt | Kind | Result |
|---|---|---|
| DEFECT/GAP/PREFERENCE grading (P13.6/P15.2) | model judgment | commit-hygiene mis-graded DEFECT → counted (gym-009/011) |
| P26 off-theme sort (F26.1) | model judgment | inverted — pruned product, kept commit-hygiene (gym-026) |
| `_drop_false_absences` (P17 F11), `_drop_false_defects` (P18 F17), content-addressing (P10.3), test-count AST (P19) | deterministic | all hold |

This is §11's spine — every boundary an executable contract, not a prose judgment.

## The fix — F28.1 (this pass, small surface)

Replace `_sort_off_theme`'s LLM call with a **deterministic git-artifact classifier** (`_GIT_META_RE`):
a candidate task whose body names a git commit/history artifact — `commit`/`commits`, a commit SHA
(7–40 hex with at least one letter, so a decimal number never matches), `git history`/`log`/`tree`,
`rebase`/`bisect`/`cherry-pick`/`amend` — is git-meta → constraint (keep P26's §6.6 plumbing:
misalignment → constraint, excluded from the count). Everything else stays a task.

- **Fail-safe by construction:** it prunes only a *named pattern*. Product code never names a commit, so
  it **cannot** amputate real work — the failure that just happened. "No match ⇒ keep" has nothing to be
  wrong about, unlike "unsure ⇒ keep" with a confidently-wrong model.
- **Goal-relevance stays reasoning**, untouched — the goal_alignment lens + gap-analysis (report vs goal
  → gaps). We are NOT deterministizing relevance.
- Validated against the exact gym-026 strings: all 10 wrongly-pruned product tasks survive; the
  commit-hygiene set is pruned; in-code docstring tasks (product) survive.

## Deferred (deliberately out of this pass)
- **The staged-diff backstop** — verify each drain task produced a real product-code change, else don't
  count it. This is the *truest* expression of "staged changes are the work" (operator), and the
  authority we ultimately want; bigger surface (post-execution diff inspection wired into the count), so
  it lands after the cheap text filter proves out in the gym.
- **Dev-tooling** (`pyproject.toml`, CI, linting) and **README** touches — not commit-hygiene; a
  separate "is project infra part of *this* product?" call. The filter leaves them as tasks for now.
- **The `project_documentation` lens is the *source*** (it's designed to critique git history, §6.5);
  round-0'ing its output is the deeper option once the filter is proven.

## Plan
1. Add `_GIT_META_RE`; rewrite `_sort_off_theme` as the deterministic classifier (no LLM call, no goal).
2. Tests against the real gym-026 task strings: product tasks kept, commit/SHA tasks pruned, decimal
   numbers and product identifiers never matched, docstring tasks kept; the drain wiring still records
   the constraint + excludes from the count.
3. Deploy → wipe arena → gym-027. **Success:** off-theme pruning matches the git-meta class (audit
   `off_theme_pruned` examples all name commits/SHAs), NO product task is pruned, and the count reflects
   real theme-work.
