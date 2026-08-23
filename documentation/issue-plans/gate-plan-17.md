Verification complete. All cited paths/lines re-derived at the pinned base; the regex trace re-derived by hand (Python execution is blocked in this session too); executor and registry claims checked live.

## Plan verdict: GO

## Rubric
- **Grounded** (every cited path/line exists at the pinned base; claims re-derived, not trusted): **pass** â€” every citation checked and exact; two immaterial overstatements noted below, neither changing the work.
- **Dispatchable scope** (bounded for a small local model; triage honest; one issue, no drive-bys): **pass** â€” one function, one strip-set, one new test dir; the observed stack-map drift is explicitly deferred, not swept in.
- **Validation is real** (RED-at-base repro named; exact commands; T2/live steps assigned to the HOST harness): **pass** â€” RED is a named AssertionError string, ImportError is explicitly a FAIL not a RED, and the registered `check_cmd` matches byte-for-byte on the live bridge.
- **Live-surface honesty** (touches_live and bind-mount writes declared; OB1-submodule discipline): **pass** â€” no smolcrawl container exists even stopped, no compose/bind-mount/paste delivery path; no OB1 or wiki paths, so submodule discipline correctly not invoked.
- **Security screen** (no secret movement, no gateway bypass, no branch-policy violation, no unrelated file contact): **pass** â€” stdlib-only test, no `.env`/network/inference contact, cut from `development` â†’ PR â†’ human merge.

## Reasoning

**Base and scope.** `9e465758e` is on `development` (`git branch --contains`), and `git diff --stat 9e46575 HEAD -- smolcrawl/` is empty â€” so working-tree reads are reads of the pinned base. `smolcrawl/tests/` does not exist yet (Glob over `smolcrawl/**`), so step 4 creates, never overwrites.

**Citations, all exact.** `_extract_kb_name` `smolcrawl_pipeline.py:806-823` with the pattern list opening at `:814` and the two regexes at `:815-816`; `_normalize_kb_name:54-61` with the edge-strip at `:60`; `_extract_url:825-832`; the render string at `:760` (the plan transliterated `â†’` to `->`, otherwise verbatim). `smolcrawl/Dockerfile:17` is `COPY smolcrawl_pipeline.py /app/pipelines/`. `docker-compose.yml:28-30` is the orphan-volume comment as quoted. `owui_client.py:85,107,207` are the three `/api/v1/knowledge/*` calls. `a18aa0c` and `9223516` exist with the message text quoted accurately, including "zero log activity in 14 days" and the "Kept: smolcrawl/ source tree" rationale.

**Defect re-derived independently.** On `search_space = "</query> <mandatory>"` (20 chars): the leftmost alternation hit is `to` at index 15 in `manda|to|ry>` â€” there is no earlier `to`/`as`/`into` (`a` at 11 and 14 are followed by `n`/`t`, not `s`). From pos 17 the lazy group backtracks `r` â†’ `ry` â†’ `ry>` before `$` at 20 succeeds. `_normalize_kb_name` leaves it: `<[^>]*>` needs a complete tag, and `>` is absent from `.strip(".,;:!?)]}")`. Guard at `:821` accepts. Capture = `ry>`. Confirmed.

I also confirmed the planner's correction to the reporter: the report's `[^<>"'\n\r]` class cannot cross the apostrophe in `into Bill's Docs`, so both patterns fail and it regresses to the domain fallback. `[^<>\n\r]{1,80}?` avoids that. And `\b` alone kills the reported repro â€” `to` in `mandatory` is word-char-flanked on both sides.

**No delivery path.** `docker ps -a --filter name=smolcrawl` returns nothing (images linger, unused). `owui/manifest.csv` has no smolcrawl row, so it is not a paste artifact; `smolcrawl/` is not a code mount (CLAUDE.md: `status-pipe/` is the only one into OWUI). `touches_live: false` holds.

**Harness is executable, which is the part that usually kills these.** `smolcrawl_pipeline.py`'s only third-party module-level import is `from pydantic import BaseModel:28` â€” everything else at `:15-26` is stdlib, and the sole `os.makedirs` is inside `_save_job_state:76`, not at import. `class Valves(BaseModel):92` is plain annotated defaults, so the `type("BaseModel", (), {})` stub constructs fine, and the tests hit staticmethods without instantiating `Pipeline`. `python3` is present in the executor: `little-coder/docker/Dockerfile.open-terminal:49` shells the git-proxy through it. The live registry confirms `ai-stack` â†’ `python3 -m unittest discover -s smolcrawl/tests -v`, exactly as the plan asserts. RED at base is genuine: the repro assert fails carrying `ry>`, and the suffix case returns `Gaggia Docs" with max depth 2`.

**Two overstatements, neither load-bearing.** Pattern 2 is not strictly dead: `.` does not cross `\n` and `$` needs end-of-string, so on a multi-line search space pattern 1 fails and the `with|using|from` variant does fire. And with `\b` added, most tag tails no longer match a keyword at all, making the `<>` exclusion defense-in-depth rather than the mechanism. The plan reorders and hardens both patterns regardless, so the work is unchanged. The reply draft repeats the "always wins" phrasing â€” host lane, worth a word-fix before posting.

**Top execution risk to watch, not a gate failure.** The plan specifies "word-bounded" without pinning the literal regex. The naive `\b(?:into|to|as|kb:|knowledge[- ]?base[: ])\b` breaks the `kb:` route â€” a `\b` after `:` demands a following word char, and `kb: Gaggia Docs` has a space. The boundary must be per-alternative: `(?:\b(?:into|to|as)\b|kb:|knowledge[- ]?base[: ])`. The plan's own mandated `kb: Gaggia Docs` test catches this as a stuck-RED, so the contract holds â€” but expect the small model to burn a round or two there.

**One host-lane note.** `ai-stack`'s `check_cmd` is now *only* this smolcrawl suite. After merge, a green machine check on any future ai-stack issue means only these six-odd tests ran; worth broadening once this dry run closes.
