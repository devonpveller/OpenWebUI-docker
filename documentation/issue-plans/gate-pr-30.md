All verification is done. Writing the gate verdict.

## Verdict: RECOMMEND-MERGE

## Rubric
- Solves the issue INTENT (not merely tests-green): **pass** — the digest caller is actually wired end-to-end (compose env → `send-digest.ts:63` reads exactly `LOCAL_LLM_BEARER`), not just a lane added; the `not-needed` split-out and host-tier assignments were sanctioned by the approved plan, so worker scope is fully delivered.
- Evidence quality (failing→passing repro shown): **pass** — RED against pre-change policy is mechanically sound (verified: no existing `_DEFAULT_CLASSES` key substring-matches `ob-digest`, so it classifies `default`/rank-2 pre-change), GREEN 41/41 disclosed honestly as a stub-runner run; real-pytest re-run is a named pre-merge host condition below.
- Scope discipline (no drive-by changes): **pass** — files touched = plan's `touched_paths` + the mandated test; sole noise is the disclosed, involuntary `100644→100755` mode bits (parent ×3 + the OB1 compose file).
- SERVICE-LIFECYCLE compliance (if service-shaped): **n-a** — config-only on existing services, no container added/removed/moved, so the full checklist is correctly not triggered.
- Security (secrets, gateway-only routing, branch policy): **pass** — no key value anywhere in either diff (value lands host-side in gitignored `OB1/docker/.env`); routing stays on the `llama-cpp`/`llama-cpp-embed` aliases; PR targets `development` from a work branch; submodule pushed-first flow honored.

## Reasoning

I independently verified the opaque half of this PR — the gitlink bump — rather than trusting the description:

- **OB1 commit `3b4e502` is real, reachable, and correctly based.** It exists on the OB1 remote (`origin/issue/27-digest-gateway-key-wiring` contains it) and is a single commit whose parent is `48a84aec` — exactly the pin `development` carries today (`git rev-parse development:OB1` = `48a84aec...`), so the bump is a clean fast-forward of the committed pin. The CLAUDE.md "push to OB1 remote FIRST, never bump to an unreachable SHA" rule is satisfied.
- **The OB1 diff does precisely what's claimed and nothing else:** `docker/docker-compose.scheduled.yml` +6/−3 — the `LOCAL_LLM_BEARER: ${OB_DIGEST_LLM_KEY:-no-key}` line with a J.1 comment in the `openbrain-digest` environment block, plus the corrected header comment (the old text "No LLM call … does NOT join llm-net" was factually false; the service is on `llm-net`). At that commit, `recipes/daily-digest/send-digest.ts:59-63` reads exactly this var with default `no-key` against the gateway aliases — the wiring contract closes.
- **Parent diff applies to the real base.** The `_DEFAULT_CLASSES` context in the diff matches `development`'s `llm-queue/src/llm_queue/policy.py:64-84` byte-for-byte; the new lane (`rank=3, acceptable_wait_s=600.0, max_concurrency=2`) matches plan step 5 exactly, and `test_policy.py::test_ob_digest_lane` pins all four fields.
- **The RED transcript is trustworthy on inspection:** the classifier's substring matching (see `test_substring_match`) cannot map `ob-digest` to any pre-existing key, so pre-change classification to `default` rank-2 is exactly what the worker showed.
- **Conflict risk with the in-flight wiki line is nil on content:** the wiki branch (now 6 commits past the pin, `14bcedd..ac2dcef`) touches `docker/docker-compose.scheduled.yml` zero times — reconciling the two OB1 lines is a trivial merge/rebase for the operator.
- **J1 doc:** the dedicated `ob-digest` row carries the miss story inline, matching the doc's house style (compare the OWUI-embed row at line 58). Plan step 7's ON compose-embed row correction (`:62`) did **not** land — minor doc gap, should ride the `not-needed` follow-up issue, not block this.

**Conditions to attach at merge (host-harness tiers per the evidence assignment — not worker debt):** (1) re-run real `pytest` in `llm-queue/` — the worker's stub runner (stubbed `pydantic_settings`/`structlog`/`aiosqlite`) was honest but is not pytest, and this gated review session couldn't execute Python to close that gap itself; (2) mint the `ob-digest` key and store `OB_DIGEST_LLM_KEY` in `OB1/docker/.env` (the #26 trap — not the root `.env`); (3) **recreate** `openbrain-digest` (up -d, not restart) outside the 03:00–07:30 UTC band and restart `llm-queue` for the lane; (4) verify the next 05:00 chain shows keyed `ob-digest` successes and zero `no-key` rows; (5) optionally strip the mode bits at merge. Two observations for the follow-up issue: the `ob-research` 35-failure triage (plan step 2, host SQL) is still open on the issue, and pre-existing `policy.py:82` labels the `ob-podcast` lane with class name `"ob-research"` — an attribution quirk worth folding into the podcast-side fix.

## If DENY: orchestration adjustment plan
N/A — recommending merge. One orchestration note worth carrying forward anyway: the sandbox's missing `pytest` + filtered PyPI forced a hand-rolled runner; either bake `pytest`/`pytest-asyncio` into the worker sandbox image or amend the evidence-assignment template so "suite run" explicitly names the fallback runner contract, so the next PR's GREEN doesn't need a disclosure paragraph.
