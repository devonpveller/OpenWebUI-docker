# Findings — `sl-ob1-profiles` (Open Brain compose profiles), 2026-09-19

Sink named by the anchor at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-profiles.json`.
Everything below was checked by reading the file or running the command named;
nothing is inferred from another agent's report.

---

## 1. The brief said OB1's compose has no profiles today. It has one.

The task brief noted that `scripts/stack/stack.ps1`'s `--profile idea-refinery`
is "currently inert" because "the OB1 compose has NO profiles today". That is
wrong, and it matters.

`OB1/docker/docker-compose.scheduled.yml` line 236 carries
`profiles: ["idea-refinery"]` on `openbrain-idea-refinery`, and the scheduled
file is pulled into the same project by `include:` at
`OB1/docker/docker-compose.yml:15-16`. The flag is load-bearing: without it that
container does not start, and `docker ps` shows `openbrain-idea-refinery`
running right now, i.e. the flag is doing its job on this host.

Evidence:

```
$ docker compose -f docker-compose.yml --env-file .env config --services | wc -l
20
$ docker compose -f docker-compose.yml --env-file .env --profile idea-refinery config --services | wc -l
21
```

`stack.manifest.toml` already had this right (`default = true`, with a
description saying stack.ps1 passes it every time). The brief's parenthetical
was the only place that got it wrong; no code assumed it.

## 2. The manifest's `research` profile description named a service that is core

Before this change, `[planes.ob1.profiles.research]` read:

> description = "openbrain-research + the grounding backfiller"

The anchor's core list puts `openbrain-grounding-backfiller` in **core**, and
reading the service agrees with the anchor: its own comment in
`OB1/docker/docker-compose.yml` calls it "general brain-health (grounds every
inlet's claims, not just the podcast)", it drains the `ungrounded_claims` view
for whatever wrote into it, and its second job (`POST /refetch`) heals thin web
**sources** — nothing about either is research-specific. Its only `depends_on`
is `openbrain-db`.

So the description described a grouping that was never going to be built. It
has been corrected to `openbrain-research + openbrain-curator`. **A manifest
description is the only statement of what a profile means to a reader who is
not going to open the compose file** — a wrong one is worse than none, because
an operator enabling `research` to get the backfiller would have got it anyway
and drawn the wrong conclusion about what the profile does.

Nothing consumed the description programmatically, so this was cosmetic in
effect and substantive in meaning.

## 3. `check-project-configs.ps1`'s row diff would have gone quiet, not red

This is the one that could have shipped unnoticed.

`scripts/checks/check-project-configs.ps1` verifies that every
`container_name` in a rendered compose config has a row in
`scripts/lib/stack-services.json`. Its OB1 render target passed **no profiles**:

```powershell
$renderTargets += @{ P = 'open-brain'; F = 'OB1\docker\docker-compose.yml'; A = @() }
```

The check only walks compose → json. Rows in the json with no compose
counterpart are not flagged. So after this change the OB1 render would have
dropped from 30 container names to 20, and the check would have gone on
printing `stack-services.json inventory matches the compose configs` while
silently no longer verifying ten rows — the wiki, the research engine and the
Open Notebook trio. Not a failure. A narrowing.

Fixed by passing all four profiles in that render target, with the reasoning in
a comment at the line so the next person widening a profile set sees it.

**Generalisation for the next profiled plane:** `agent-org` is not in
`$renderTargets` at all, so its `workers`/`cloud` rows have never been machine-
verified in either direction. That is a pre-existing gap, not one this item
created, and it is the same shape as this one. Worth an item.

## 4. Adding the profiles to that check forced a documented gap closed

`documentation/notes/stack-services-unlisted-containers.md` (2026-08-30)
recorded `openbrain-idea-refinery` as a live container with no row in
`scripts/lib/stack-services.json`, and said the fix was the `profile` key shape
the agent-org rows had just adopted.

Pointing the OB1 render at all four profiles turned that documented gap into a
`MISSING from stack-services.json` pre-commit failure. The row is now added
with `"profile": "idea-refinery"`, and the note is marked resolved with the
reason. Recording it here because the fix was **forced by mechanism, not chosen
on merit** — it is the kind of change that looks like unrelated scope creep in a
diff unless the causal chain is written down.

## 5. Two core → profiled references survive at RUNTIME

No core service `depends_on` a profiled one — audited across all 24 services of
the main file and all 6 of the scheduled file (the scheduled file has **zero**
`depends_on` keys at all; `grep -n depends_on OB1/docker/docker-compose.scheduled.yml`
exits 1). So nothing had to be removed or set `required: false`. That is a
clean result, not an absent check: every `depends_on` either stays inside a
group or points from a profiled service down into core, which is always safe.

Two references do cross core → profiled, as environment URLs. They do not
affect the render and the caller still starts, but the capability behind them
is dead when the profile is off:

| Caller (core) | Reference | Dead without |
|---|---|---|
| `openbrain-ext` | `WIKI_RECOMPILE_URL: http://openbrain-wiki:8000/recompile` | `--profile wiki` |
| `openbrain-podcast` | `RESEARCH_URL: http://openbrain-research:8000`, `ON_BASE: http://open_notebook:5055` | `--profile research`, `--profile notebook` |

Both are commented at their line in the compose file and listed in
`OB1/docker/README.md`. The podcast one is why the `digest` product's profile
set grew `notebook` (see below).

`openbrain-ext` also mounts the `openbrain-wiki-data` volume read-only. The
volume is declared top-level, so the mount resolves with the wiki profile off —
`openbrain-ext` starts and serves its 39 tools; the `wiki_*` readers just see
whatever the vault last held.

## 6. `digest` needed `notebook` as a PROFILE, not a surface

The manifest had `[products.digest] profiles = { ob1 = ["research"] }`.

`openbrain-podcast` is the chain's tail and is **always-on core** in the
scheduled slice. It reaches `open_notebook:5055` for audio rendering
(`ON_BASE`) and `openbrain-research:8000` for enrichment (`RESEARCH_URL`), both
in `OB1/docker/docker-compose.scheduled.yml`. Neither is a *surface* a person
reads — they are engine dependencies of a scheduled job with no human in the
loop. Putting `notebook` under `surfaces` would let `--headless` drop it, and a
headless digest would run the whole chain nightly and produce no episode, with
nothing failing loudly.

So `digest` now names `profiles = { ob1 = ["research", "notebook"] }`, pinned by
`test_digest_needs_the_notebook_profile_not_just_research`.

## 7. Two drivers now disagree about OB1's profiles, deliberately

- `scripts/stack/stack.ps1` (the pre-manifest driver, still the full script on
  this base — `sl-driver-parity` has not merged) passes **all four** profiles.
- `scripts/stack/stack.py` with a bare `enable ob1` passes **only**
  `idea-refinery`, because only that one is `default` in the manifest.

This is not drift, it is a forced choice, and it is written in both files:

- stack.ps1 must keep starting what is running on this host (30 containers).
  Leaving it at `idea-refinery` would have silently stopped bringing up the
  wiki, the research engine and Open Notebook on the next `stack.ps1 up` — a
  deployment change, from an item whose own acceptance says it deploys nothing.
- The manifest cannot mark the three `default`, because `default` means "passed
  on every invocation" and the whole point of the research product's
  `profiles`/`surfaces` split is that `--headless` can drop wiki and notebook.
  Making them `default` would make `--headless` a no-op for this plane.

`stack.py enable research` produces exactly the stack.ps1 line, so the two agree
on the *intended* set; they disagree only on what a bare plane enable means.
**`sl-driver-parity` must resolve this deliberately** — the obvious wrong fix is
to make the three `default` so the drivers match, which breaks `--headless`.

## 8. Pre-existing, not mine: `ruff check .` exits 1 on this base

```
E501 Line too long (103 > 100)
  --> llm-queue\src\llm_queue\__init__.py:9:101
Found 1 error.
```

`llm-queue` carries its own stricter ruff config (`line-length = 100`,
`select = ["E","F","I","UP","B","ASYNC"]`) rather than the repo root's
`F + E9` gate. The offending line is a docstring path that grew when
`cfa7d4e documentation: move the plans to the plan store` rewrote it to
`../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md`.

That commit is on my base (`development` at 9f64b84 → this line is untouched by
this item; `git log --oneline -1 -- llm-queue/src/llm_queue/__init__.py` names
cfa7d4e). It is reported here so a reviewer running `ruff check .` does not
attribute it to this branch. It is a one-character fix someone should take — the
plan-store move introduced it and no gate caught it, which suggests root
`ruff check .` is not actually run in CI over subproject configs.

## 9. Scheduled services render with no profile, by design and out of scope

The anchor's first acceptance criterion names the 15 core services and says any
research/wiki/notebook service in the no-profile render FAILS. The no-profile
render is **20** services: those 15 plus the 5 always-on scheduled ones
(`openbrain-cron`, `-gmail-pull`, `-gmail-prune`, `-digest`, `-podcast`), which
come in through `include:` and are explicitly out of scope for this item. Zero
research, wiki or notebook services appear. The criterion is met; the count is
20 rather than 15 and a tester reading the criterion literally should know why
before calling it a failure.

Whether the digest chain itself should become a `digest` profile is a real
question — it is five always-on containers a chat-only or core-only deployment
does not want — but it is the next item's, not this one's.

## 10. `scripts/checks/plan-store.ps1` cannot run from a worktree

CLAUDE.md tells every planning session to run this at start and at stop. From a
harness worktree it throws before doing any work:

```
plan store not found at D:\Open WebUI\ai-stack\.claude\worktrees\documentation-plans-ai-stack
```

It resolves the store as a **sibling of the repo root**, and a worktree's root
is `…\.claude\worktrees\wt-<id>`, so the sibling it looks for is inside
`.claude\worktrees\`. The real store is beside the MAIN checkout at
`D:\Open WebUI\documentation-plans-ai-stack`.

Two standing policies therefore contradict each other: "every git-mutating
session works in its own worktree" and "run `plan-store.ps1` at session start
and before you stop". Today the second is simply unrunnable for any agent
obeying the first. The fix is small — resolve the store from the worktree's
*common* git dir (`git rev-parse --git-common-dir`) or accept a `-Store`
override — but it is a change to a shared check, not to this item's surface, so
it is written here rather than made.

## 11. `stack-layers` has no row in the implementation-guide index

`documentation/implementation-guide/README.md` is meant to carry one status row
per feature "wherever that feature's plan lives" (CLAUDE.md, "Plans live in the
plan store"). `grep -n "stack-layers" documentation/implementation-guide/README.md`
returns nothing, although the feature has a plan set in the store, merged items
(`sl-inference-split`, `sl-colo-portal` both have evidence and findings files in
this repo) and more in flight.

Deliberately **not** fixed here. Writing that row means asserting the feature's
overall state — which phases shipped, what is outstanding — and this item touched
one slice of it. Inventing a status to satisfy a check is the failure mode the
findings-note rule exists to prevent. The feature's planner should add it; the
row is one line.
