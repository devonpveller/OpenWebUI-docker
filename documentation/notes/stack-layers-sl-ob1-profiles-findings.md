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

## 5. SIX cross-group runtime references — and the method that finds them

> **CORRECTED after attempt 1 FAILED on T14.** This section said "two". It was
> wrong, and wrong in the way that matters: I enumerated by grepping the two
> compose files, which cannot see values delivered by `env_file:`. The tester
> (`wt-tester-ob1`) matched every **rendered** environment value against the
> profiled service names and found six. I reproduced their result before acting
> on it; the render is the evidence below.

No core service `depends_on` a profiled one. That part held — the tester
enumerated 22 edges across 18 owners independently and found none I had missed,
and the scheduled file has **zero** `depends_on` keys at all. Nothing had to be
removed or set `required: false`.

But `depends_on` is not the only way one service reaches another. Six
references cross a group boundary as environment URLs. None affects the render
and none stops a container starting — each is a `fetch` that fails at call
time, usually inside a `try/catch`. That is the dangerous kind: the stack comes
up green and a scheduled job quietly stops producing output.

| Caller | Caller group | Key | Target | Target profile | What goes dead | Declared in attempt 1? |
|---|---|---|---|---|---|---|
| `openbrain-ext` | core | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` | `wiki_trigger_recompile` refused; `wiki_*` readers go stale | yes |
| `openbrain-gmail-prune` | **core** | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` | **the nightly prune completes and never recompiles the vault** (`prune-short-term.ts:153`, inside a `try/catch`) | **no** |
| `openbrain-gmail-pull` | **core** | `WIKI_RECOMPILE_URL` | `openbrain-wiki` | `wiki` | nothing — inherited from the shared `env_file`; its code never reads the key | **no** |
| `openbrain-podcast` | core | `RESEARCH_URL` | `openbrain-research` | `research` | link-enrichment research; episode degrades to email-only | yes |
| `openbrain-podcast` | core | `ON_BASE` | `open_notebook` | `notebook` | **no audio rendered — the chain runs and produces no episode** | yes |
| `openbrain-idea-refinery` | `idea-refinery` | `RESEARCH_URL` | `openbrain-research` | `research` | **its only engine** (`index.ts:268` submits, `:295` polls) — the drain can never drain | **no** |

Six references across five callers. (The tester's summary says "five
references across four services"; their own table lists six rows across five
callers, and my render agrees with the table, not the summary. Counting
`openbrain-podcast` once instead of twice is the likely slip. It changes
nothing about the finding.)

**THE METHOD, which is the real lesson.** Grep the compose files and you find
four of six. `openbrain-gmail-pull` and `openbrain-gmail-prune` get
`WIKI_RECOMPILE_URL` from `env_file: ../recipes/email-history-import/.env`,
which is not in the compose text, and
`recipes/email-history-import/prune-short-term.ts:32` hard-codes the same URL as
its default — so even unsetting the variable would not remove the reference.
Match the render instead:

```bash
docker compose -f docker-compose.yml --env-file .env \
  --profile research --profile wiki --profile notebook --profile idea-refinery \
  config --format json
```

then match every `environment` **value** against every profiled service name,
and check `depends_on`, `network_mode`, `links`, network aliases and shared
named volumes the same way. Verified while redoing it: there are **no**
`network_mode`, `links` or network aliases anywhere in this project, and
`openbrain-wiki-data` is the only cross-group volume — written by
`openbrain-wiki` and `openbrain-workbench` (both `wiki`), mounted **read-only**
by `openbrain-ext` (core) and `open_notebook`, and declared top-level so the
core mount resolves with the wiki profile off.

This is CLAUDE.md's "verify against gitignored evidence" rule with a different
hat on: `.env` files are exactly where a "zero references" verdict dies. The
method is now stated in the compose header, in `OB1/docker/README.md`, in
`SERVICE-LIFECYCLE.md` row 8b, and in the test plan's T14 — because the next
person to add a profile here will reach for `grep` for the same reason I did.

All six are now commented at their line and tabulated with consequences in
`OB1/docker/README.md`.

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

## 5a. `idea-refinery` now REQUIRES `research` — the decision, and the two I rejected

`openbrain-idea-refinery`'s only engine is `openbrain-research`. It is also the
`ob1` plane's only `default = true` profile, so before this fix **every**
invocation of either driver started a drain with no engine —
`stack.py enable ob1` and `enable open-brain --headless` both did.

Compose has no "profile implies profile". The manifest does now:
`requires = ["research"]` on the profile, closed over transitively by
`stack.py`'s new `profile_closure` in both `run_profiles` (what gets passed)
and `cmd_enable` (what gets written to state, so the state file is not a lie
the driver silently corrects). A profile requiring an unknown profile is a
load-time `Refusal`, tested.

The two alternatives, and why not:

- **Mark `research` itself `default = true`.** Rejected: `default` profiles
  survive `--headless` (that is what `default` means), so this would make
  `--headless` a no-op for the plane and destroy the whole surfaces split. The
  tester independently verified this mechanism rather than taking the claim on
  trust, which is worth noting — it is the obvious-looking fix.
- **Drop `default` from `idea-refinery`.** This is the *better* long-term
  answer. The service's own compose comment says it should be off until
  deliberately enabled; it is on here only because `stack.ps1` has always passed
  it. But dropping it STOPS a container that has been up six days, and this item
  deploys nothing. Left for the operator / `sl-driver-parity`, and stated in the
  manifest at the line rather than left as a silent judgement.

**Consequence, stated rather than discovered later:**
`enable open-brain --headless` now yields `idea-refinery, research` instead of
`idea-refinery` alone. A headless knowledge core pulls the research engine in,
because the profile the driver always passes needs it. That is the honest
resolution of a contradiction that already existed; removing `default` is what
removes the research engine, not removing `requires`.

## 5b. The first version of my own coverage guard was worthless

Worth recording because it is a failure mode I have now produced twice in one
item, in two different materials.

The tester's class-2 note asked for an expected-row-count guard in
`check-project-configs.ps1`, since a narrowed render silently unverifies rows.
My first implementation derived the expected set as *rows whose profile the
render target passes*. Dropping `--profile wiki` then removed the four wiki rows
from **both** sides and the check stayed green at 26/26 — I ran the tester's own
break case and watched it pass.

The expectation has to come from something the render target cannot move. It is
now **every inventory row for the project**, full stop; a profiled row is
covered by passing its profile. Re-running the three break cases:

| Break | Before | After |
|---|---|---|
| drop `--profile wiki` from the OB1 target | green, 26 rows silently unverified | **exit 1**, names the four rows and says `add --profile wiki` |
| delete the `openbrain-idea-refinery` row | exit 1 (already worked) | exit 1 |
| point the target at a non-existent compose file | green (`continue` on empty render) | **exit 1**, `RENDER PRODUCED NOTHING … 30 inventory row(s) went unverified` |

Green path now prints what it actually verified:
`[rows verified/expected: inference:8/8 frontend:4/4 memory:3/3 search:4/4 coder:4/4 open-brain:30/30]`.

**That change exposed a pre-existing hole in another plane.** With expected =
all rows, `inference` failed at 4/8: its render target passed no
`--profile local`, so `llama-cpp-upstream`, `llama-cpp-embed-upstream`,
`llm-queue` and `lm-models-backup` had **never** been verified by this check —
the exact defect the OB1 profiles would have introduced, already present and
unnoticed since `sl-inference-split`. Fixed by passing `--profile local` there
too (verified: that render emits all 8). This is scope I took on because the
guard cannot be correct without it.

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

## 12. Carried from the tester, not fixed here (their class 3)

Recorded so they are not lost when the evidence file is cleaned up.

- **`[planes.inference.profiles.local] pending = true` is stale on this base**
  (`stack.manifest.toml`). `sl-inference-split` merged at 9f64b84 and the
  `local` profile exists in `inference/compose/*.yml`, so the manifest declares
  as "not yet in the compose file" something that is in it — the same lie this
  item removed for `ob1`. Consequence today: no product pulls `local`, so
  `stack.py up` for the inference plane prints no `--profile local` and would
  not start `llm-queue` or the upstreams (it does not stop what is already
  running). Belongs to `sl-inference-split` / `sl-driver-parity`. **Note this is
  adjacent to, but not the same as, the `check-project-configs.ps1` fix in §5b**:
  I passed `--profile local` to that check's *render target*, which is a
  verification concern. The manifest's stale `pending` is a driver concern and I
  have left it alone.
- **Profiles have cross-project blast radius no doc named.** Turning `wiki` or
  `notebook` off breaks consumers outside OB1: `portal/config/caddy/Caddyfile`
  reverse-proxies `openbrain-workbench`, `openbrain-wiki-viewer` and
  `open_notebook`; `status-pipe/modules/system-health/` probes `open_notebook`
  and `openbrain-research`. All degrade at request time, none at start. Partly
  addressed — `SERVICE-LIFECYCLE.md` row 8a now names "who reaches it from
  another project" as a fourth place to declare a profile, and the stack-map OB1
  section lists these consumers — but no check enforces it.
- **The handoff destination was not in the commit message.** Attempt 1's message
  named the OB1 branch and SHA but not the branch to push it onto, so an
  operator reading only the commit knew what to push and not where. Fixed: the
  destination is in this attempt's ai-stack commit message and in plan T27.
