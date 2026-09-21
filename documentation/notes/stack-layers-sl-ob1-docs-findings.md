# Findings — `sl-ob1-docs` (OB1's own profile + variable documentation), 2026-09-20

Sink named by the anchor at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-docs.json`.
Everything below was checked by reading the named file or running the named
command in the worktree `.claude/worktrees/wt-sl-ob1-docs`. Nothing is carried
over from a sibling item's report without re-measuring it here.

Artifact: OB1 `work/sl-ob1-docs` @ **`aa4a31d`**, merge-base `fe3e045`
(= `origin/feature/integrated-knowledge-system` tip), two files, **not pushed**
(D4). ai-stack: this note + `documentation/evidence/sl-ob1-docs/test-plan.md`,
no gitlink staged.

---

## 1. The anchor says "the thirteen variables". Ten were added, and that is the correct number.

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

Ten assignments added: the three backup groups' retain/cron/interval knobs
(6), the two SurrealDB credentials, the Open Notebook encryption key, and its
embedding API key.

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

## 3. The two-way variable diff: direction A clean, direction B is 72 and was 82

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

| | before | after |
|---|---|---|
| compose-substituted names | 96 | 96 |
| template-declared names | 14 | 24 |
| **A**: in template, not substituted | **0** | **0** |
| **B**: substituted, not in template | 82 | **72** |

Direction A empty in both states is the criterion that passes. Direction B is
the pre-existing gap the anchor asked me to report rather than close.

### The 72 still missing (all pre-existing, none introduced here)

`BACKFILL_BATCH` `BACKFILL_CHAT_MODEL` `BACKFILL_CONCURRENCY`
`BACKFILL_FETCH_PROXY_URL` `BACKFILL_INTERVAL_MS` `BACKUPS_DIR` `CHUNK_BATCH`
`CHUNK_INTERVAL_MS` `CURATOR_CONFLICT_DISTANCE` `CURATOR_MERGE_FLOOR_DISTANCE`
`CURATOR_NEW_THREAD_MIN_CONFIDENCE` `CURATOR_SHORTLIST_K` `EXTRACT_MAX_BYTES`
`EXTRACT_STT_API_BASE` `EXTRACT_STT_PATH` `GAP_DIVE_CEILING` `GAP_DIVE_ENABLED`
`GAP_DIVE_MAX_AGE_DAYS` `GAP_DIVE_MAX_ATTEMPTS` `GAP_DIVE_MIN_TAGGED`
`GAP_DIVE_WAIT_MS` `IDEA_BRAINSTORM_OPERATORS` `IDEA_REFINERY_MM_TOKEN`
`IDEA_REFINERY_MM_URL` `MM_SITE_URL` `OB_APP_MEMORY_PASSWORD` `OB_DIGEST_LLM_KEY`
`OB_ENTITY_LLM_KEY` `OB_MCP_LLM_KEY` `OB_PODCAST_LLM_KEY` `OB_RESEARCH_LLM_KEY`
`OB_WIKI_LLM_KEY` `ON_JOB_WAIT_MS` `ON_PUBLIC_BASE` `PUBLIC_DOMAIN`
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

- **`OB_APP_MEMORY_PASSWORD`** — the only one of the 72 with **no default at
  all**: `${OB_APP_MEMORY_PASSWORD}` on NINE services (eight in
  `docker-compose.yml`, one in the scheduled file), every one of them a
  `DB_PASSWORD`. A fresh clone renders them blank. This is the strongest
  candidate for the next item.
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

## 8. The outside-OB1 consumers, verified in this tree

`portal/config/caddy/Caddyfile`:

| line | reverse_proxy | profile |
|---|---|---|
| 136 | `openbrain-workbench:8000` | `wiki` |
| 143 | `openbrain-wiki-viewer:8080` | `wiki` |
| 242 | `open_notebook:5055` | `notebook` |
| 250 | `open_notebook:8502` | `notebook` |

`status-pipe/modules/system-health/service/system_health.py`: `open_notebook`
:5055 `/api/config` and `openbrain-research` :8000 `/health`, both
`"critical": False`. `status-pipe/orchestrator.py` and
`status-pipe/serve/tailscale_serve_pipe.py` additionally list `surrealdb`,
`open_notebook`, `openbrain-wiki` and `openbrain-wiki-viewer` in their service
inventories — display-only, so they degrade rather than break, and I left them
out of the README table to keep it to consumers that actually reach the service.
Naming that choice because "the table is complete" is one of its claims and this
is the edge I decided sits outside it.

No `openbrain-curator` consumer outside OB1 was found. The Caddyfile has no
`openbrain-research` route either — the status pipe is its only outside caller.

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
