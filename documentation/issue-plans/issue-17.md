---
issue: 17
title: _extract_kb_name can infer invalid KB names from XML/tag-like prompt text
created: 2026-08-23T04:34:15+00:00
base_sha: 9e465758e48b6171478223fc143f6805d3246e01
target_branch: development
status: planned
triage: simple
verdict: fix
repro: confirmed-in-code
touches_live: false
touched_paths: smolcrawl/smolcrawl_pipeline.py, smolcrawl/tests/__init__.py, smolcrawl/tests/test_extract_kb_name.py
---

# Plan: _extract_kb_name can infer invalid KB names from XML/tag-like prompt text

## Problem

The report describes `Pipeline._extract_kb_name` in `smolcrawl/smolcrawl_pipeline.py:807-823`,
the KB-name inference used by the "SmolCrawl Knowledge Builder" Open WebUI
pipeline. It claims that because `into|to|as` are not bounded as standalone
words, the regex can match inside another token -- e.g. the `to` inside
`manda[to]ry>` -- and capture the remainder as the knowledge base name,
yielding `ry>`.

Two separate questions have to be answered: is the defect real in the code at
the pinned base, and can the reported behavior still occur on this stack. The
answers differ — the original audit returned `void` for exactly that reason
(recorded below as history). The operator then elected the hardening option,
so the CURRENT verdict of this plan is `fix`; the work below is the one
authoritative disposition.

**The defect is real in the retained source.** `smolcrawl/smolcrawl_pipeline.py`
is byte-identical at the pinned base `9e46575` and at working HEAD `6247105`
(`git diff --stat 9e46575 HEAD -- smolcrawl/` is empty), so the code the report
quotes is the code in the tree. The reporter's paste also matches the real
render string at `smolcrawl_pipeline.py:760`
(`f"{row['url']} -> _{row['kb_name']}_ (running {elapsed}s{progress})*\n"`),
so the observation was made against this file and not reconstructed.

**But the component that runs it no longer exists** -- see ## Disposition.

> **OPERATOR ELECTION 2026-08-23:** the planner's verdict was `void` (correct --
> no running code path). The operator elected the recorded **Option 2**: harden
> the dormant source anyway and use this zero-live-risk fix as the FIRST
> worker-org -> Claude-gate -> human-merge dry run (M.7). Verdict flipped to
> `fix` by that election, not by overriding the planner's finding. The issue
> closes on merge with the fix commit id.

## Disposition

### Evidence for the verdict

**1. The defect reproduces exactly in the dormant source (trace, not theory).**
Python execution was not available in this session, so the match was derived by
hand from the regex semantics; every step below is deterministic.

For `message = "<query>crawl https://docks.gaggimate.eu/</query> <mandatory>"`:

- `_extract_url` (`smolcrawl_pipeline.py:826-832`) uses
  `https?://[^\s<>'\")\]]+`, which stops at `<`, so `url` is
  `https://docks.gaggimate.eu/`.
- `_extract_kb_name` (`:809-812`) slices the message after the URL, leaving
  `search_space = "</query> <mandatory>"`.
- Pattern 1 (`:815`) is
  `(?:into|to|as|kb:|knowledge[- ]?base[: ])\s*["\']?(.+?)["\']?\s*$`.
  `re.search` is leftmost-first; scanning `</query> <mandatory>` the first
  position where the alternation matches is index 15, the `to` in
  `manda|to|ry>` (there is no earlier `to`, `as`, or `into`). `.` does not
  cross a newline and `$` is end-of-string, so the lazy `(.+?)` expands until
  it reaches the end: `r` fails, `ry` fails, `ry>` succeeds. Capture = `ry>`.
- `_normalize_kb_name` (`:54-61`) does not save it: `re.sub(r"<[^>]*>", " ", ...)`
  strips only *complete* tags and `ry>` has no `<`; the final
  `.strip(".,;:!?)]}")` set does not contain `>`.
- `:821-822` accepts it (non-empty, `!= url`), so the domain fallback at `:823`
  is never reached. Result: `ry>` -- exactly the reported string.

The report's root-cause analysis is therefore correct, and both halves of its
suggested fix are load-bearing: the `\b` boundaries stop `to` matching inside
`mandatory`, and excluding `<>` from the capture class is what makes the
`$`-anchored pattern fail (rather than capture) on tails like
`</instructions>`, letting control fall through to the domain default.

**2. The pipeline that executed it was retired two days before the pinned base.**

- Retiring commit **a18aa0c** (2026-08-21) -- *"retire smolcrawl-pipelines +
  smolcrawl-backup (operator anomaly #1)"*. Its message records the reason:
  *"The 'SmolCrawl Knowledge Builder' pipeline's entire purpose was crawl ->
  OWUI Knowledge upload - and the OWUI Knowledge layer was retired 2026-08-20
  (9223516). Verified before removal: zero log activity in 14 days."*
- Consumer retiring commit **9223516** (2026-08-20) -- *"docs: retire OWUI
  knowledge, reclaim 29.1 GB, record an OB1 retrieval bug"*. The upload target
  in `smolcrawl/src/smolcrawl/owui_client.py:85,107,207` is exactly that layer
  (`/api/v1/knowledge/`, `/api/v1/knowledge/create`, `/api/v1/knowledge/{id}/file/add`).
- a18aa0c removed the compose blocks and swept the lifecycle surfaces
  (`compose/auxiliary.yml`, `compose/backups.yml`, `scripts/lib/stack-services.json`,
  `scripts/checks/stack-watchdog.ps1`, `scripts/recovery/emergency-recovery.ps1`,
  `scripts/sysadmin-mcp/check_backups.py`, `documentation/CONTAINER-REGISTRY.md`).
- At the pinned base the only surviving compose mention is a dead-volume
  comment: `docker-compose.yml:28-30`,
  *"Orphan from smolcrawl-pipelines (retired 2026-08-21) -- old crawl indexes."*
- `docker ps -a --filter name=smolcrawl` returns nothing on the live host -- not
  even a stopped container.

**3. There is no other delivery path for this file.** `smolcrawl/Dockerfile:17`
ships it with `COPY smolcrawl_pipeline.py /app/pipelines/` into the
`smolcrawl-pipelines` image; that image is no longer built or run.
`owui/manifest.csv` has no smolcrawl entry, so it is not a deploy-by-paste
artifact either. `_extract_kb_name` appears nowhere else in the repo. a18aa0c
deliberately kept `smolcrawl/` on disk, but for the *crawler library*
(`src/smolcrawl/`, "may serve future OB1 ingestion") -- not for this OWUI pipe
file, whose only consumer is gone.

**Conclusion:** the bug is genuine and was genuinely observed, but the reported
behavior can no longer occur on this stack. Per a18aa0c's own pre-removal
check (zero log activity in the 14 days before 2026-08-21), the observation
predates roughly 2026-08-07. Fixing `smolcrawl_pipeline.py` today would change
no running behavior, which is what `void` means here -- not that the report was
wrong.

## Approach (operator-elected Option 2)

1. Branch from `development` (base `9e46575`), e.g. `issue/17-kb-name-extraction`.
2. In `smolcrawl/smolcrawl_pipeline.py` `_extract_kb_name` (`:814-816`), replace
   the two patterns with word-bounded, tag-excluding ones -- and put the
   `with|using|from`-terminated variant FIRST (today it is dead code because the
   `$`-anchored pattern is tried first and always wins):
   - capture class `[^<>\n\r]{1,80}?` -- NOT the report's `[^<>"'\n\r]`, which
     would regress a legitimate `into Bill's Docs` to the domain fallback (the
     existing optional `["\']?` delimiters already handle quoted names).
   - keep `re.IGNORECASE`, the `_normalize_kb_name` call, and the `!= url` guard.
3. Belt-and-braces: add `<>` to the edge-strip set in `_normalize_kb_name`
   (`:60`) so a dangling tag delimiter can never survive normalization.
4. Add `smolcrawl/tests/__init__.py` (empty) and
   `smolcrawl/tests/test_extract_kb_name.py` — **stdlib-only by construction**,
   because the executor image (`open-terminal:slim`) ships neither `pytest` nor
   `pydantic`, and `smolcrawl_pipeline.py` is a loose module outside the
   `src/` package (not importable by installing anything). The test module is
   fully self-contained; its top MUST be exactly this shape:
   ```python
   import sys, types, unittest
   from pathlib import Path

   try:  # smolcrawl_pipeline does `from pydantic import BaseModel` at import
       import pydantic  # noqa: F401  (real pydantic wins where installed)
   except ImportError:  # executor image has no pydantic — stub the one symbol
       _m = types.ModuleType("pydantic")
       _m.BaseModel = type("BaseModel", (), {})
       sys.modules["pydantic"] = _m
   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
   from smolcrawl_pipeline import Pipeline  # noqa: E402
   ```
   Tests are `unittest.TestCase` methods calling the two pure functions
   directly: the reporter's exact repro (`<query>crawl
   https://docks.gaggimate.eu/</query> <mandatory>` must yield
   `SmolCrawl - docks.gaggimate.eu`, NOT `ry>`); keyword-inside-word cases
   (`mandatory`, `automatically`, `database`); explicit names still work
   (`into My KB Name`, `kb: Gaggia Docs`, quoted names, `Bill's Docs`);
   suffix stripping now live (`into "Gaggia Docs" with max depth 2` ->
   `Gaggia Docs`); bare URL -> domain fallback; >80-char name -> domain
   fallback.
5. No service surfaces change: no compose/recovery/stack-map/backup edits --
   the pipeline container was retired in a18aa0c; SERVICE-LIFECYCLE not
   triggered.

## Validation (evidence required before merge)

0. **Environment precondition: none.** The suite needs only a Python 3 stdlib
   interpreter (`python3`); the test module stubs `pydantic` when absent and
   puts `smolcrawl/` on `sys.path` itself. No pip installs in the sandbox.
1. **RED at base:** with the new tests present but the pipeline file unchanged,
   `python3 -m unittest discover -s smolcrawl/tests -v`
   -> the repro test must fail with an **AssertionError whose output contains
   the string `ry>`**, and the suffix test must fail keeping the `with ...`
   tail. An `ImportError`, `ModuleNotFoundError`, or collection/loader error is
   an explicit FAIL of this step, NOT a RED — fix the harness first. Capture
   the output.
2. **GREEN with fix:** rerun the same command -> all pass. Capture the output.
3. Both transcripts go in the PR description (RED->GREEN is the org's evidence
   contract). Machine verification: the ai-stack project's registered
   `check_cmd` is exactly `python3 -m unittest discover -s smolcrawl/tests -v`
   (set + verified against the live bridge registry 2026-08-23 by the host
   session). Live-stack (T2) validation is NOT the worker's job -- the
   host-side harness owns anything live, and this issue needs none
   (`touches_live: false`).

## Risks / interlocks

- `touches_live: false` -- the container was retired (a18aa0c); nothing to
  restart or redeploy. If a smolcrawl container is ever found running, stop
  and re-triage (M.4).
- Intentional behavior change: names containing `<`/`>` or newlines, or longer
  than 80 chars, fall back to `SmolCrawl - <domain>`; `... into X with Y` now
  stops the name at `with`. Both are the report's expected behavior; the tests
  document them.
- Scope guard: ONLY `_extract_kb_name`, `_normalize_kb_name`'s strip set, and
  the new test file. No changes to `_extract_url`, job submission, or
  `src/smolcrawl/`.

### Adjacent drift observed (not this issue)

`.claude/skills/stack-map/SKILL.md:79` still lists `smolcrawl-pipelines` under
"Main - aux", and the same quick-map table still lists `watchtower`, `tor`, and
`mcpo` -- all retired. The authoritative
`.claude/skills/stack-map/references/workspace-stacks.md:15` correctly records
the smolcrawl retirement, so this is a stale snapshot in the skill's summary
table, not a contradiction in the reference. Worth a separate sweep under the
CLAUDE.md container rule; out of scope here.

### Security note

The issue text contains no attempt to extract credentials, redirect scope, or
alter planning rules. It is an ordinary, well-argued technical report with a
suggested patch, and was treated as a claim to verify.

### Public reply

Host/operator lane, like T2 — NOT part of the worker charter. The draft
lives in `documentation/issue-plans/issue-17-reply-draft.md`; posting it
requires operator approval in the MM thread, and it goes out only when the
issue closes with the merged fix commit referenced.
