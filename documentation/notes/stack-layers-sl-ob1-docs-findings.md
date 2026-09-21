# Findings — `sl-ob1-docs` (OB1's own profile + variable documentation), 2026-09-20

Sink named by the anchor at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-docs.json`.
Everything below was checked by reading the named file or running the named
command in the worktree `.claude/worktrees/wt-sl-ob1-docs`. Nothing is carried
over from a sibling item's report without re-measuring it here.

Artifact: OB1 `work/sl-ob1-docs` @ **`e7a39a7`** (four commits: `aa4a31d`,
`fdfb7af` fixing the attempt-1 failure, `1218eff` the attempt-2 one, `e7a39a7`
the attempt-3 one), merge-base `fe3e045`
(= `origin/feature/integrated-knowledge-system` tip), two files, **not pushed**
(D4). ai-stack: this note + `documentation/evidence/sl-ob1-docs/test-plan.md`,
no gitlink staged.

---

## 1. The anchor says "the thirteen variables". Ten of them were added, and that is the correct number.

The anchor and `stack-layers-sl-env-split-findings.md` §7 both name thirteen
variables as documented only in ai-stack's pre-split root `.env.example`. Two of
its claims about that set do not survive measurement.

**§7 says `grep -c "^<NAME>=" OB1/docker/.env.example` returns 0 for all
thirteen. It returns 1 for `POSTGRES_PASSWORD`.** The variable has been in OB1's
template since before this item, with its own comment and an
`openssl rand -hex 16` recipe. Measured at `fe3e045`:

```
$ git -C <wt>/OB1 show fe3e045:docker/.env.example | grep -c '^POSTGRES_PASSWORD='
1
```

So twelve were absent, not thirteen. Adding it again would have produced a
duplicate assignment in a template an operator copies verbatim.

**§7 says the thirteen's only `${...}` references are in
`OB1/docker/docker-compose.yml`. `POSTGRES_USER` and `POSTGRES_DB` have NO
`${...}` reference anywhere.** Compose sets both as literals, in two services:

```
docker-compose.yml:64:      POSTGRES_DB: openbrain          # openbrain-db
docker-compose.yml:65:      POSTGRES_USER: postgres
docker-compose.yml:1075:      POSTGRES_USER: postgres       # openbrain-db-backup
docker-compose.yml:1076:      POSTGRES_DB: openbrain
$ grep -nE '\$\{(POSTGRES_USER|POSTGRES_DB)' docker-compose.yml docker-compose.scheduled.yml
(no output)
```

Neither service carries `env_file:`, so nothing in this project delivers a value
for them from `OB1/docker/.env` either. The root template's own comment —
"Pull these from OB1/docker/.env — they MUST match what openbrain-db is running
with, else the dump fails auth" — describes a coupling that **does not exist**:
the values are pinned in the compose file on both sides, so they cannot drift,
and an operator who "fixed" a mismatch by editing `.env` would have changed
nothing.

`backup/openbrain-db-backup.sh:26` does read `POSTGRES_USER` (`:?` — it refuses
without it), and its header comment says "(from env_file ./OB1/docker/.env)".
That comment is stale in the same way; the value it actually receives is the
compose literal.

**Resolution.** The anchor's acceptance criterion is explicit that "a variable
added that nothing reads FAILS", and it collides here with "add the thirteen".
The criterion wins: `POSTGRES_USER` and `POSTGRES_DB` are documented in the
template as a **prose note** saying they are not read from that file and where
to change them instead, with no assignment. The documentation the root template
carried survives; the misleading invitation to set them does not.

Ten assignments added from the anchor's thirteen: the three backup groups'
retain/cron/interval knobs (6), the two SurrealDB credentials, the Open Notebook
encryption key, and its embedding API key. An ELEVENTH assignment,
`OB_APP_MEMORY_PASSWORD`, was added in attempt 2 from OUTSIDE that set - see
section 11 for why a fourteenth variable is in scope.

**Follow-on, NOT done here:** sl-env-split §7 says the root `.env.example`'s
pointer block should be deleted "once OB1's own example carries them". It still
names thirteen, including the two that are not read. Deleting or correcting it
is an ai-stack change and this item's only ai-stack surface is the plan and this
note, so it is left. Whoever takes it should correct the claim, not just delete
the block — the pointer is currently the only place the `POSTGRES_USER` fiction
is written down.

## 2. One default in the root template was wrong, not merely stale

`OPEN_NOTEBOOK_LLM_API_KEY=` (blank) in `git show 4934529:.env.example`, under
"REQUIRED secrets (blank = fill in)". The compose reads it as

```
docker-compose.yml:1246:      - EMBEDDING_API_KEY=${OPEN_NOTEBOOK_LLM_API_KEY:-not-needed}
```

Blank and unset are **not** the same value here. Unset takes `not-needed`, which
is what the local gateway expects; blank overrides the default and sends an
empty key. A fresh clone that copied the root template's line verbatim into
`OB1/docker/.env` would have shipped the failure mode, not the default. The
template now carries `OPEN_NOTEBOOK_LLM_API_KEY=not-needed` with the distinction
spelled out.

The other nine defaults match the compose exactly (`:-2`, `:-0 2 * * *`,
`:-86400`, `:-20 2 * * *`, and three with no default at all). Checked one by one,
not by inspection of the group.

## 3. The two-way variable diff: direction A clean, direction B is 70 and was 82

Method, and the guard that matters:

```bash
cd <wt>/OB1/docker
grep -oE '(^|[^$])\$\{[A-Za-z_][A-Za-z0-9_]*' docker-compose.yml docker-compose.scheduled.yml \
  | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*' | sed 's/\${//' | sort -u
```

**The `(^|[^$])` guard is load-bearing and I did not have it on the first pass.**
A naive `\$\{NAME` match returns 98 names; two of them, `RETAIN_COUNT` and
`BACKUP_INTERVAL`, are `$${RETAIN_COUNT}` / `$${BACKUP_INTERVAL}` inside the
backup containers' inline `command:` blocks — compose escapes `$$` to a literal
`$` for the container's shell and never substitutes them. Counting them would
have inflated the gap by two and, worse, invited someone to "fix" it by adding
two variables that compose does not read — the exact failure the anchor's
direction-A criterion exists to catch.

| | before | after commit 1 | after commit 2 |
|---|---|---|---|
| compose-substituted names | 95 | 95 | 95 |
| template-declared names | 14 | 24 | **25** |
| **A**: in template, not substituted | **0** | **0** | **0** |
| **B**: substituted, not in template | 82 | 72 | **70** |

> **CORRECTED after attempt 1 FAILED.** This table first said 96 substituted and
> 72 direction-B. Both were one too high: `PUBLIC_DOMAIN` occurs exactly once in
> either compose file, at `docker-compose.yml:867`, **inside a `#` comment**, so
> compose never substitutes it. The tester refuted it and I reproduced the
> refutation two ways — reading the line, and observing that compose emits unset
> warnings for `OB_APP_MEMORY_PASSWORD` (unset, default-less) and never for
> `PUBLIC_DOMAIN`, which is equally unset and equally default-less. The method
> therefore needs a SECOND guard, `grep -hvE '^[[:space:]]*#'`, to strip
> whole-line YAML comments before matching. Direction B then falls to 71, and to
> **70** once `OB_APP_MEMORY_PASSWORD` is added (§11).

Direction A empty in both states is the criterion that passes. Direction B is
the pre-existing gap the anchor asked me to report rather than close.

### The 70 still missing (all pre-existing, none introduced here)

`BACKFILL_BATCH` `BACKFILL_CHAT_MODEL` `BACKFILL_CONCURRENCY`
`BACKFILL_FETCH_PROXY_URL` `BACKFILL_INTERVAL_MS` `BACKUPS_DIR` `CHUNK_BATCH`
`CHUNK_INTERVAL_MS` `CURATOR_CONFLICT_DISTANCE` `CURATOR_MERGE_FLOOR_DISTANCE`
`CURATOR_NEW_THREAD_MIN_CONFIDENCE` `CURATOR_SHORTLIST_K` `EXTRACT_MAX_BYTES`
`EXTRACT_STT_API_BASE` `EXTRACT_STT_PATH` `GAP_DIVE_CEILING` `GAP_DIVE_ENABLED`
`GAP_DIVE_MAX_AGE_DAYS` `GAP_DIVE_MAX_ATTEMPTS` `GAP_DIVE_MIN_TAGGED`
`GAP_DIVE_WAIT_MS` `IDEA_BRAINSTORM_OPERATORS` `IDEA_REFINERY_MM_TOKEN`
`IDEA_REFINERY_MM_URL` `MM_SITE_URL` `OB_DIGEST_LLM_KEY`
`OB_ENTITY_LLM_KEY` `OB_MCP_LLM_KEY` `OB_PODCAST_LLM_KEY` `OB_RESEARCH_LLM_KEY`
`OB_WIKI_LLM_KEY` `ON_JOB_WAIT_MS` `ON_PUBLIC_BASE`
`RESEARCH_CHAT_MODEL` `RESEARCH_CLAIM_SHORTLIST_K` `RESEARCH_COLLAPSE_STREAK_MAX`
`RESEARCH_CONFIDENCE_FLOOR` `RESEARCH_FETCH_CONCURRENCY`
`RESEARCH_FETCH_DEGRADED_MIN_HITS` `RESEARCH_FETCH_DEGRADED_RATIO`
`RESEARCH_FETCH_MAX_CHARS` `RESEARCH_FETCH_PROXY_URL` `RESEARCH_FETCH_TIMEOUT_MS`
`RESEARCH_MAX_CONCURRENCY` `RESEARCH_MAX_FETCH` `RESEARCH_MAX_FETCH_TIMEOUTS`
`RESEARCH_MAX_ROUNDS` `RESEARCH_MAX_WALL_MS` `RESEARCH_MAX_WALL_MS_OWUI`
`RESEARCH_OWUI_API_KEY` `RESEARCH_OWUI_BASE_URL` `RESEARCH_PRELIM_GAP_LIMIT`
`RESEARCH_PRELIM_MAX_FETCH` `RESEARCH_READABLE_MIN_CHARS`
`RESEARCH_RELEVANT_TARGET` `RESEARCH_REUSE_MAX_DISTANCE`
`RESEARCH_SEARCH_API_BASE` `RESEARCH_SEARCH_K` `RESEARCH_SOURCE_SLICE_CHARS`
`RESEARCH_WAIT_MS` `SUGGESTION_THRESHOLD` `WIKI_BACKFILL_CONTINUE_MIN`
`WIKI_BACKFILL_IDLE_MIN` `WIKI_BACKFILL_PER_COMPILE` `WIKI_COMPILE_TIMEOUT_MIN`
`WIKI_DEPLOY_KEY_PATH` `WIKI_SYNTH_TIMEOUT_MIN` `WIKI_VIEWER_HEAP_MB`
`WIKI_VIEWER_POLL_INTERVAL_MS` `WIKI_WATCH_POLL_MS`

**Most of these are tuning knobs with a `${VAR:-default}` and are genuinely
optional.** Four are worth a follow-up, and I checked each substitution site
rather than sorting them by how the name reads:

- **`OB_APP_MEMORY_PASSWORD`** — was the only one with **no default at all**:
  `${OB_APP_MEMORY_PASSWORD}` on NINE services (eight in `docker-compose.yml`,
  one in the scheduled file), every one of them a `DB_PASSWORD`. Called "the
  strongest candidate for the next item" here in attempt 1; **it was added in
  attempt 2 instead** (§11), which is why the list above is 70 and not 71.
- **`OB_*_LLM_KEY` (six names: `MCP`, `ENTITY`, `RESEARCH`, `WIKI`, `PODCAST`,
  `DIGEST`)** — 17 substitution sites. **They all HAVE defaults**
  (`:-not-needed`, and `:-no-key` for the digest), which is exactly why they are
  worth documenting rather than why they are safe: since J.1 the gateway
  enforces per-caller virtual keys, so the default is a placeholder that now
  yields 401 at call time. Nothing fails at start, nothing appears in the
  template, and the failure surfaces as six services quietly not answering.
- **`IDEA_REFINERY_MM_TOKEN`** — `${IDEA_REFINERY_MM_TOKEN:-}`, one site,
  defaulting to empty. It is the Mattermost bot token that is the stated reason
  `openbrain-idea-refinery` is profile-gated at all. The README explains the
  gate; neither file names the token the gate is about.
- **`BACKUPS_DIR`** — `${BACKUPS_DIR:-../../backups}`, three sites, one per
  backup service. The default resolves relative to `OB1/docker`, i.e. into the
  ai-stack checkout's `backups/` tree. In a standalone OB1 clone with no parent
  that path points outside the repo and nothing says so.

I did not add them: the anchor scopes this item to the thirteen from the root
template, and each of those four needs a sentence about a subsystem I would be
describing rather than measuring. They are a clean follow-up item —
`OB1/docker/.env.example` is now the right place for it to land.

## 4. The README already had a Profiles section. Three things were missing from it, not all of it.

`sl-ob1-profiles` (2026-09-19) had already written "Compose profiles" into
`OB1/docker/README.md` with the per-service table, the no-core-`depends_on`
invariant, the six cross-group references, the rebuild method and a ten-row
render table. The anchor's deliverable reads as if the section were absent; it
is not, and writing it again would have produced two versions of the same
material in one file.

What was genuinely missing, and is what this commit adds:

1. **The driver's default pair.** The render table had every single profile and
   every combination of `research`/`wiki`/`notebook`, but not
   `idea-refinery + research` — which is the set BOTH of the ai-stack driver's
   bare-plane invocations produce, and the row the sibling item got wrong (22
   instead of 23, propagated to eight files). The row a reader most needs was
   the one row absent.
2. **How to turn them on.** The section showed `--profile` flags and nothing
   else — no `COMPOSE_PROFILES`, no driver. A reader with only the OB1 checkout
   could not have learned that the env file beside them is a declaration site.
3. **Consumers outside OB1.** Nothing named the portal or the status pipe.
   `sl-ob1-profiles` §12 recorded this as carried-from-the-tester and
   unaddressed in OB1's own docs.

I re-rendered every pre-existing row of that table rather than trusting it
(§5). They were all correct.

## 5. Every count re-measured, including the ones I did not write

`docker compose -f docker-compose.yml <flags> config --services | wc -l`, run in
the worktree with `COMPOSE_PROFILES=` blanked in the shell, stderr empty on all:

| Flags | Services |
|---|---|
| (none) | 20 |
| `idea-refinery` | 21 |
| `research` | 22 |
| `notebook` | 23 |
| `wiki` | 24 |
| **`idea-refinery` + `research`** | **23** |
| `research` + `wiki` | 26 |
| `research` + `notebook` | 25 |
| `wiki` + `notebook` | 27 |
| `research` + `wiki` + `notebook` | 29 |
| all four | 30 |

Core (bare minus the five always-on scheduled services) = **15**. Per-profile
membership by `comm -13` against the sorted bare render: `idea-refinery` → 1,
`research` → 2 (`openbrain-curator`, `openbrain-research`), `notebook` → 3
(`surrealdb`, `open_notebook`, `open-notebook-backup`), `wiki` → 4
(`openbrain-wiki`, `-wiki-backup`, `-wiki-viewer`, `-workbench`). All match both
the README's table and the manifest's profile descriptions.

Renders after the commit are identical to renders before it. Docs-only is a
claim and it was checked, not assumed.

## 6. The bare render in this worktree is 30, not 20 — the landing step already happened

`OB1/docker/.env` on this host now carries
`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` at line 44, and the
harness copied that file into the worktree. The variable NAME sets of the main
checkout's copy and the worktree's are identical (`diff` of `^NAME=` lines,
clean; values not compared and not printed).

`stack-layers-sl-ob1-gitlink-findings.md` §4 said "`OB1/docker/.env` has no
`COMPOSE_PROFILES` line" and named adding it as the orchestrator's landing step,
still open. **It is done.** Re-measured, not inferred from a timestamp: a bare
`docker compose config --services` in the worktree renders 30, and blanking the
variable in the shell renders 20.

Consequence for anyone reading this item's evidence: **a "bare" render here is
30 unless you blank the variable**, which is precisely the number a tester will
report as a contradiction of the README's `(none) = 20` row. The README now says
so explicitly and the test plan leads with it.

## 7. The two declaration sites compose, and that makes `--headless` inert for this plane

This is the finding I expected least and it is the one most likely to be called
a mistake.

`stack.py enable research --headless` exists to drop this plane's *surface*
profiles. On this host it prints that it did:

```
# --headless: dropped surface profiles ob1:wiki, ob1:notebook
  ob1  profiles: idea-refinery, research
```

…and then `up ob1 --dry-run` emits

```
docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery --profile research --profile wiki --profile notebook up -d
```

All four. Measured with a scratch state file; the checkout's `.stack/state.json`
was not touched.

The mechanism is deliberate and documented at
`scripts/stack/stack.py` `effective_profiles()`: because a CLI `--profile`
**replaces** `COMPOSE_PROFILES` rather than unioning with it, the driver must
union the plane's own env list into whatever it resolves, or passing any flag
would start FEWER containers than a bare invocation. That is the right call.
Its consequence is that once `wiki` and `notebook` are in `OB1/docker/.env`,
nothing the driver does can drop them — `--headless` becomes a no-op for `ob1`
specifically.

**Not a bug, and not this item's to fix.** But the two things now in tension
were each decided separately and correctly: `sl-ob1-profiles` §7/§5a chose
`opt_in` over `default` precisely so `--headless` could drop the surfaces, and
`sl-ob1-gitlink` §3 landed `COMPOSE_PROFILES` in the env file so bare-verb
scripts start all 30. Together they cancel. The README states the consequence
and says what to do if you want `--headless` back (do not declare the surface
profiles in `.env`); `sl-driver-parity` or the feature's planner should decide
whether that is the intended end state.

## 8. The outside-OB1 consumer list - I claimed completeness over a set I had never enumerated, and attempt 1 FAILED on it

This is the finding worth the most, and it is about method, not about the four
rows I missed.

My first version of the table had five rows in two consumer kinds and closed:
"**Both** consumers reach these services by container name across the shared
`ai-stack_*` networks." Every row was correct - the tester verified all five at
their lines - and the case failed anyway, because the sentence was a **universal
quantifier over a set I had never enumerated**. My own test plan made it the test
("no consumer of a profiled service may be missing ... the table's value is that
it is complete") and ranked it FAIL #3, and then I satisfied it by grepping the
two directories I had already thought of.

**What I actually did wrong:** I ran `grep` over `portal/` and `status-pipe/` -
the two places the `sl-ob1-profiles` findings section 12 had already named - and
treated reproducing a prior item's list as having derived my own. A carried
finding is a starting point, not an enumeration.

**The evidence was in front of me and I read past it.** `docker-compose.yml:864-867`
- the very lines I later used to prove `PUBLIC_DOMAIN` is comment-only - say that
"the portal Caddy ... and the tailscale container - which shares openwebui's
netns, and openwebui is on app-net - can reach openbrain-wiki-viewer:8080 by
name". OB1's own compose names the frontend tailscale companion as a consumer. I
quoted those lines for a different purpose and never read them for this one.

### The re-derived list: seven surfaces, thirteen call sites

Method: an unbounded grep over the whole ai-stack tree (excluding `OB1/`, `.git`,
`node_modules`, `archive`, `documentation`, `backups`) for a URL/host-field shape
in front of any of the ten profiled service names, then every hit read at its
line. **23 lines in 15 files** (21 config/code, 2 prose in Markdown). The grep is
in `OB1/docker/README.md` so the list can be rebuilt rather than maintained.

| # | Surface | Sites | Profile |
|---|---|---|---|
| 1-3 | portal Caddy | `portal/config/caddy/Caddyfile:136`, `:143`, `:242`, `:250` | `wiki`, `notebook` |
| 4 | `frontend`'s `tailscale` companion | `frontend/entrypoint.sh:97`, `:99` (host at `:60`) | `notebook` |
| 5 | `agent-org`'s `agent-bridge` | `agent-org/docker/docker-compose.yml:208` -> `app/config.py:373`, `app/modules/grounding.py:12` | `research` |
| 6 | OWUI Deep Research tool | `owui/tools/deep_research.py:47` (`owui/manifest.csv:10`) | `research` |
| 7 | OWUI Server Status - **two** modules | `status-pipe/modules/system-health/service/system_health.py:58`, `:66`; `status-pipe/serve/tailscale_serve_pipe.py:119`, `:135`, `:652` | `notebook`, `research` |
| + | operator path | `scripts/backup/restore-from-snapshot.ps1:432` (`docker exec open-notebook-backup` -> `surrealdb:8000`) | `notebook` |

Rows 4, 5, 6 and the second half of 7 are the four the tester found. The operator
path is not among their four; I found it while re-deriving.

### TWO CORRECTIONS TO THE TESTER'S OWN CITATIONS

A tester's report is not evidence until the part you act on is checked - the A9
rule applies to their output as much as to a subagent's. Both of these would have
gone into the README as errors if I had transcribed the report.

1. **Row 4 does NOT reach the wiki directly.** The tester wrote
   "`QUARTZ_HOST=openbrain-wiki-viewer:8080` (:60,66;
   `frontend/docker-compose.yml:350`) - hits notebook AND wiki". Measured:
   `frontend/docker-compose.yml:366` is `QUARTZ_HOST=${QUARTZ_HOST:-caddy}`,
   `frontend/.env:133` is `caddy`, `QUARTZ_PORT` is `8446`. `entrypoint.sh:66`'s
   `openbrain-wiki-viewer` default is never reached because the compose always
   supplies a value - and the comment at `frontend/docker-compose.yml:361-364`
   says why it moved ("Pre-Caddy this pointed straight at
   openbrain-wiki-viewer:8080, which had NO /workbench routing -> 404 on
   tailnet"). So the tailnet wiki route breaks THROUGH the portal Caddy row, not
   as an eighth direct edge. **The tester's conclusion - that row 4 is a missing
   surface - is right; their mechanism for the wiki half is not.**
2. **The cited `frontend/docker-compose.yml:350` is `OPEN_NOTEBOOK_HOST`**, not
   `QUARTZ_HOST`. The notebook half of row 4 is correct, at that line.

### The scope sentence is the actual fix

Adding four rows would have left the same defect: a table that asserts
completeness with no stated boundary cannot be checked, only doubted. The section
now states what counts as a consumer (a runtime reach by container name over a
shared `ai-stack_*` network), gives the count, and names the **excluded** class
explicitly - host-side probes on `127.0.0.1` (`check-openbrain-health.ps1` at
`:8818`/`:8816`), `docker exec` drivers (`stack-watchdog.ps1`, which repairs
rather than consumes; `wiki-latency-probe.ps1`), inventories
(`scripts/lib/stack-services.json`, `stack.manifest.toml`,
`status-pipe/orchestrator.py`'s docstring) and `.env` delivery lines.

That is what makes the next tester's job finite: they can disagree with the
boundary, which is a note, or find something inside it, which is a fail. Before,
every host-side probe was an argument.

## 9. The pre-commit's OB1 gates do not run for this item, and that is the right evidence

The anchor's last criterion says the OB1 recipe tests and `deno check` "are
unaffected because you change no code". Reading `.githooks/pre-commit`, the
stronger and more useful statement is that **they never execute**: steps 5b
(recipe tests), 5c (Deno type-check) and 5d (integration images) all key off a
**staged OB1 gitlink** and skip instantly without one (lines 109-153). This
item's ai-stack commit stages no gitlink, so the correct evidence is a skip line,
not a pass.

That is worth writing down because it cuts the other way too: **the gates that
would validate an OB1 change run at gitlink-bump time, in the NEXT item, not
here.** A docs-only OB1 commit gets no automated validation in this repo at all.
The renders in §5 are the only machine check this artifact receives.

## 10. Carried, not fixed

- **`scripts/checks/plan-store.ps1` still cannot run from a worktree.** Reported
  by `sl-ob1-profiles` §10 (2026-09-19) and re-confirmed by `sl-ob1-gitlink`
  (2026-09-20). Third item in a row to hit it. It resolves the plan store as a
  sibling of the repo root, which for a worktree is inside `.claude/worktrees/`.
  CLAUDE.md tells every planning session to run it at start and stop; for any
  agent obeying the worktree policy that instruction is unrunnable. The fix is
  `git rev-parse --git-common-dir` or a `-Store` override. Recorded a third time
  rather than fixed because it is a shared check and outside this item's surface
  — but three consecutive items is the point at which "carried" starts to mean
  "nobody owns it".
- **`stack-layers` still has no row in `documentation/implementation-guide/README.md`.**
  `grep -n stack-layers` returns nothing. Same reasoning as `sl-ob1-profiles` §11:
  writing the row means asserting the feature's overall status, which this item
  cannot honestly do.
- **`backup/openbrain-db-backup.sh`'s header comment is stale.** It says
  `POSTGRES_USER` comes "from env_file ./OB1/docker/.env"; per §1 it comes from a
  compose literal. Correcting it is a one-line OB1 change, but it is in a script,
  and this item's anchor says docs only and names the two files. Left.

## 11. `OB_APP_MEMORY_PASSWORD` added - a fourteenth, and why that is not scope creep

Attempt 1's section 3 named it "the strongest candidate for the next item" and
left it. Attempt 2 added it, for three reasons that only became visible once the
tester ran a render the way a NEWCOMER would:

1. **Acceptance criterion 2's literal wording asks for it** - "a variable the
   compose reads that the example lacks FAILS". I had read that as bounded by the
   anchor's thirteen; read literally it is unbounded, and this is the one name in
   the 71 where the literal reading and the item's GOAL agree.
2. **It is the only default-less one.** Nine substitution sites, every one a
   `DB_PASSWORD`. A newcomer following the template gets a fleet that cannot reach
   its store - precisely the failure the goal ("a newcomer ... can see every
   variable the plane reads") exists to prevent.
3. **It was measurably noisy.** A render seeded from the previous example emits
   **8** `OB_APP_MEMORY_PASSWORD is not set` warnings; seeded from this one,
   **zero**. That turned the tester's class-3 note - "state the expected warning
   and count" - into a fix rather than a caveat.

**That "honest loose end" was wrong, and attempt 3 fixed it - see section 14.**
Attempt 2 wrote here, and shipped into `OB1/docker/.env.example`, that there were
nine sites but eight warnings, "compose dedupes somewhere; measured, not
explained". It warns **nine** times. The eight was an artefact of my own command
shape.

Direction B is therefore **70**, not 71. The enumerated list in section 3 was
re-checked name-for-name against the live `comm` output after the change: 70 = 70,
nothing missing, nothing extra.

## 13. The count I added to catch the miss was itself wrong for twenty minutes

Worth recording because it is the same failure one layer up.

The fix for section 8 was a rebuild grep plus its expected yield, so the next
person can tell a complete list from a plausible one. I wrote "21 lines in 11
files" into the README from a run I had FILTERED (`| grep -vE "\.md:"`) and
counted by eye. Running the command exactly as the README prints it gives **23
lines in 15 files** - the two extra are prose mentions in `owui/README.md` and
`CLEANUP-PLAN.md`, which the printed command does not exclude.

I caught it only because the test plan told the tester to execute the command
and compare, and I executed the plan's own text before committing it. The OB1
commit had already been made with the wrong figure and was amended.

**The rule this argues for:** when a document tells someone to run a command and
expect a number, the number must come from running THAT command, as printed,
with no filter you applied in your shell and forgot. Copying a figure from a
neighbouring run is the same class of error as computing a render count instead
of rendering it - which is the defect this whole item exists to avoid, and which
I had already written into the test plan as rule 3.

## 12. The method gained a second guard, and both came from refutations

The variable diff now needs TWO guards, and **neither was designed - each was
added after something refuted the version without it**:

- `(^|[^$])` excludes `$${VAR}` shell escapes in the backup containers' inline
  `command:` blocks (`RETAIN_COUNT`, `BACKUP_INTERVAL`). Found by me, in attempt
  1, because the number looked wrong.
- `grep -hvE '^[[:space:]]*#'` strips whole-line YAML comments, because a name
  appearing only in a comment is never substituted (`PUBLIC_DOMAIN`). Found by
  the TESTER, in attempt 1, because I did not think to look.

The generalisable shape: **a text-matching audit over a structured file will
over-count until something independent contradicts it** - and the thing that
contradicted it here was not a closer reading, it was compose's own
unset-variable warnings, which are ground truth about what compose actually
substitutes. Where such a signal exists, prefer it to the grep it is checking.
That is how `PUBLIC_DOMAIN` can be proven comment-only without reading line 867
at all: it is unset and default-less, exactly like `OB_APP_MEMORY_PASSWORD`, and
compose warns eight times about one and never about the other.

## 14. "Measured, not explained" is not honesty - it is an unexamined instrument

Attempt 2 FAILED on one claim, and it is the one I was most pleased with.

I measured `docker compose config` emitting **8** `OB_APP_MEMORY_PASSWORD is not
set` warnings against **9** substitution sites, could not account for the gap,
and wrote the gap down: "9 sites, 8 warnings - compose dedupes somewhere;
measured, not explained". I put that in the test plan as a claim, flagged it to
the tester as deliberately unexplained, and shipped the sentence into
`OB1/docker/.env.example`, which is the artifact a newcomer reads.

It warns **nine** times, once per site. There is no dedupe.

### What the 8 actually was - OBSERVABLE ONLY

My command was `docker compose -f docker-compose.yml --env-file <a copy of the
old example> config --services`, run in `OB1/docker`, where a real `.env` exists
and declares `OB_APP_MEMORY_PASSWORD`. In that shape the render reports **8**
warnings; with the same values in `.env` and no `--env-file` at all it reports
**9**.

**That is the whole of what this item asserts.** Passing `--env-file` changes how
the `include:`d `docker-compose.scheduled.yml` resolves variables and changes the
count, so counts taken with it are not comparable with counts taken without it.
The artifact now says exactly that and stops - see section 16 for why.

**No claim is made about which file wins.** Attempt 3 shipped one ("the included
file still resolves against this directory's `.env`") and it is refuted below.

### The measurements, recorded as measurements

From the tester (`wt-tester-ob1docs`, attempt 3), each reproduced here before
being written down. Sentinel runs use a scratch copy of both compose files with
the scheduled file's one site renamed to `OB_SCHED_ONLY`; `.env` = this commit's
example with a value, `--env-file` = the `aa4a31d` example. All exit 0.

**A. Two env files, DIFFERENT values, all four profiles.** `.env` →
`OB_APP_MEMORY_PASSWORD=FROM_DOTENV`, `--env-file` → `...=FROM_ENVFILE`. All
**nine** rendered sites carry `FROM_ENVFILE`, **including the one in
`docker-compose.scheduled.yml`** (`openbrain-idea-refinery`). With no
`--env-file`, all nine carry `FROM_DOTENV`.

**B. The sentinel matrix.**

| sentinel declared in | `OB_APP` warnings | `SENTINEL` warnings |
|---|---|---|
| nowhere, `--env-file` passed | 8 | **1** |
| `.env` only, `--env-file` passed | 8 | 0 |
| `--env-file` only, `.env` passed | 8 | **0** |
| nowhere, NO `--env-file` | 0 | 1 |

**C. A tenth occurrence that is not a substitution.** The full render also shows
`open_notebook` carrying `OB_APP_MEMORY_PASSWORD` directly: that service has
`env_file: ./.env` (`docker-compose.yml:1252-1253`), which injects the entire
project `.env` as environment. It is the only `./.env` env_file in either compose
file, `--env-file` does not affect it, and it is outside the nine-site count. Now
noted in `.env.example`'s header, because it means everything in that file
reaches that one container.

A model consistent with all of A and B: the top-level file sees only
`--env-file`, and the included file sees `--env-file` first with the project
`.env` as a fallback, making the 9→8 drop that fallback. **This is recorded as
consistent-with, NOT verified as compose's rule, and nothing in the artifact
depends on it.**

### My sentinel experiment was not wrong; my inference from it was

Findings section 14 previously claimed the split was "proven by attribution, not
by subtraction": rename the scheduled site to a sentinel, re-run, get 8 for the
main name and 0 for the sentinel. **The number is correct** - it is row 2 of the
matrix above, and I reproduced it again. What the write-up omitted is that I had
appended `OB_SCHED_ONLY=v` **to `.env` myself** before running it. A sentinel
that is present in `.env` goes silent under every model, so the cell could not
discriminate between them. I set up an experiment that could only agree with me
and reported its agreement as proof.

Row 3 is the cell I never ran: the sentinel in the `--env-file` and **nowhere
else**. It also yields 0, which my model forbids. One minute of work, and it
separates the two models.

### The generalisation, which is the point

"Measured, not explained" reads like rigour. It is the opposite whenever the
measurement is cheap to repeat under a different shape: **an unexplained number
is evidence the instrument is wrong, not a fact awaiting an explanation.** The
correct response was to vary the command until the number moved - which takes
about a minute, and which I did only after a tester failed the claim.

This item has now produced the same class of error four times at four
magnifications: computing a render count instead of rendering it (the sibling
items' defect, which this plan's rule 3 warns about); copying a grep yield from a
filtered run (section 13); measuring the warning count under an unexamined
command shape (this section); and then explaining that count with an experiment
built to agree with me (section 16). The first three were figures obtained by a
method slightly different from the one the document told the reader to use. The
fourth is worse, and it is the subject of section 16.

## 15. Numbers corrected this round

| | shipped | measured |
|---|---|---|
| `OB_APP_MEMORY_PASSWORD` warnings (attempt 2) | 8 (9 sites) | **9** (9 sites) |
| which file the `include:`d site resolves against (attempt 3) | "still ... this directory's `.env`" | **refuted** - it renders the `--env-file` value; no mechanism is now asserted |
| "renders one way in the core services and the other way in the scheduled ones" (attempt 3) | shipped in README | **false** - with different values in the two files, all nine sites take the `--env-file` value |
| rebuild grep yield | 23 lines / 15 files | **21 / 13 fresh clone**, 23 / 15 with the two gitignored `.env` files |
| guard diagnostic: no comment strip | "72" | **71** |
| guard diagnostic: no `$$` guard | "84" | **72** |
| guard diagnostic: neither | "85" | **73** |

The guard figures were the worse of the two: 84 and 85 occur under no variant of
the command at all. They were written from memory in a paragraph whose entire
purpose was to let a tester diagnose a mismatched count - so a tester who dropped
a guard and got 71 would have found no row matching, and concluded the artifact
was broken rather than their command.

Also recorded: the rebuild grep does not surface `frontend/entrypoint.sh:60`
(`OPEN_NOTEBOOK_HOST=${OPEN_NOTEBOOK_HOST:-open_notebook}`) because the service
name sits behind a `:-` rather than following the host token directly. Row 4 of
the consumer table was found by reading the file. The README now says so, because
a grep presented as the way to rebuild a list that it cannot fully rebuild is the
same shape of defect as everything else in section 14.

## 16. An explanation that fits one non-discriminating experiment is not a mechanism

This is the tester's generalisation, and it is sharper than mine.

Section 14 already said: *an unexplained number is evidence the instrument is
wrong, not a fact awaiting an explanation*. I acted on that, varied the command,
found the 8, and then did the thing the rule does not cover - I **explained** it.
The explanation fit my measurement. I ran a confirming experiment. It confirmed.
I wrote "proven by attribution, not by subtraction" and shipped the mechanism
into the README and `.env.example`, where it told a reader that a variable
disagreeing across the two files would "render one way in the core services and
the other way in the scheduled ones, silently".

That consequence is false, and a reader acting on it would have gone looking for
a split that does not exist.

**The experiment I ran could not have contradicted me.** The sentinel was
declared in `.env`, so it goes silent whether the included file reads `.env`,
reads `--env-file` with `.env` as fallback, or reads both. The cell that
separates those - sentinel in the `--env-file` and nowhere else - takes the same
minute to run and I did not run it, because I already had an answer and was
looking for agreement rather than for the cell that would break it.

**The operational rule, which is now the artifact's:** when a measurement
surprises you, fix the instrument (section 14) and then **stop at the
observable**. State what changes and what to do about it. An explanation of a
tool's internals is a claim like any other, it needs a discriminating experiment
like any other, and a document does not need it to be useful - "pass no
`--env-file`, counts taken with it are not comparable" carries every consequence
a reader acts on, with no model attached. The mechanism sentences have been
removed from both files rather than corrected, on the coordinator's decision, and
that is the right call: the corrected mechanism would have been one more
unverified model in a file whose job is to be trusted by someone who cannot check
it.

**Three times in one item** an explanation went out where a measurement was owed.
CLAUDE.md's rule for briefing an agent - *name the claim, name what would
DISPROVE it* - is written for delegation, and every one of these was me failing
to do it for myself.
