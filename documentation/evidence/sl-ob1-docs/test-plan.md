# Test plan — `sl-ob1-docs2` (OB1's own profiles + variables documentation)

Anchor: `queue.ps1 -Show -Id sl-ob1-docs2`, full text at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-docs2.json`.

Reopen of `sl-ob1-docs`, REJECTED at review (misfits) 2026-09-20 on the
warning-count paragraph. Same ai-stack branch, same worktree, same OB1 branch;
the four earlier OB1 commits stand and a fifth was added. The reviewer's full
reason is at `C:/tod/sl-ob1-docs-review-reject.md` - the queue holds a
placeholder because the text exceeded the command-line limit.

**What changed, in one line:** two documentation files inside the OB1 submodule —
`OB1/docker/README.md` and `OB1/docker/.env.example` — so that a newcomer holding
only the OB1 checkout can see which compose profiles exist, what each gates, how
to turn them on, and which variables the plane reads. **No compose file and no
code changed, in either repo.**

| | |
|---|---|
| OB1 branch | `work/sl-ob1-docs`, 5 commits: **`aa4a31d`**, **`fdfb7af`** (a2: T3), **`1218eff`** (a3: C59), **`e7a39a7`** (a4: C66), **`556195b`** (review: the .env-state pairing) |
| OB1 base | cut from **`fe3e045`** = `origin/feature/integrated-knowledge-system` tip |
| OB1 files touched | `docker/README.md`, `docker/.env.example` — nothing else |
| ai-stack branch | `work/sl-ob1-docs`, based on `a2d3644` (the `sl-ob1-gitlink` merge, which is what pins OB1 at `fe3e045`) |
| ai-stack files | THIS plan + `documentation/notes/stack-layers-sl-ob1-docs-findings.md` — nothing else |
| pushed | **nothing**, per D4: the operator pushes OB1 |

## BEFORE YOU RUN ANYTHING — three rules

1. **Never push.** Not OB1, not ai-stack. D4 reserves the OB1 push for the
   operator, and the ai-stack gitlink bump is a separate follow-on item.
2. **Never `git add OB1`** in the ai-stack worktree. ` M OB1` in
   `git -C <wt> status` is the CORRECT state and `M  OB1` (staged) is a failure
   of this item. The bump is the next item's artifact, not this one's.
3. **Every count in this plan must be RENDERED, not computed.** The single
   defect this item's siblings shipped twice was arithmetic that looked right:
   20 + 1 + 2 in someone's head produced `22` for the two-profile pair and it
   propagated to eight files. If you find yourself adding deltas, stop and run
   the command.

## THE TRAP THAT WILL BITE YOU FIRST

**This host's `OB1/docker/.env` contains
`COMPOSE_PROFILES=research,wiki,notebook,idea-refinery` (line 44), and the
harness copied it into the worktree.** Compose loads that file natively, so a
"bare" `docker compose config --services` in this worktree renders **30**, not
the 20 the README's table says.

That is not a bug in the README — the table is explicitly the no-`COMPOSE_PROFILES`
case and says so — but it WILL read as one if you skip this paragraph. To render
the bare case, blank the variable in the shell, which takes precedence over the
env file:

```bash
cd "<wt>/OB1/docker"
COMPOSE_PROFILES= docker compose -f docker-compose.yml config --services | wc -l   # 20
docker compose -f docker-compose.yml config --services | wc -l                     # 30
```

Both of those are claims under T1 and T4. Confirm the env file actually has the
line before you conclude anything from either number:
`grep -n '^COMPOSE_PROFILES' "<wt>/OB1/docker/.env"`.

## THE SECOND TRAP — CRLF smudge in a fresh clone

Both committed blobs are **pure LF** (`git show <sha>:docker/.env.example |
grep -c $'\r'` → 0). But `core.autocrlf=true` is set on this host, so a fresh
`git clone` writes CRLF into the WORKING COPY. If you grep the working copy with
a `$`-anchored pattern you may match differently than against the blob, and
`git diff` may report the file as modified the moment anything touches it
(`warning: LF will be replaced by CRLF the next time Git touches it`).

This is not a defect in the commit and it is not something to "fix": check line
endings against the BLOB, not the checkout —
`git -C <clone>/OB1 show HEAD:docker/README.md | grep -c $'\r'` must be 0 for
both files. Attempt 1's tester worked in a scratch clone; this paragraph exists
so the next one does not open a finding about it.

## THE FOURTH TRAP — a count means nothing until you say what `.env` held

**Do not pass `--env-file` to any render in this plan.** Copy the env file you
want to `.env` in the project directory instead. That is the state every number
below was taken under, and it is the state the template tells its reader to use.

This item has been rejected once and failed twice on ONE paragraph, each time
for a different reason and always the same class — a number whose condition was
not stated:

| attempt | shipped | defect |
|---|---|---|
| 2 | "9 sites, 8 warnings — compose dedupes somewhere" | wrong count, unexamined command shape |
| 3 | "the include:d file still resolves against this directory's `.env`" | false mechanism from a non-discriminating experiment |
| 4 | "nine warnings without `--env-file`, eight with one" | two counts from two different `.env` states, paired |

The reviewer's matrix (reproduce it if you want the whole picture; `.env` fixed,
flag toggled, all four profiles, exit 0 everywhere):

| `.env` state | no `--env-file` | `--env-file` WITH the name | `--env-file` WITHOUT it |
|---|---|---|---|
| name present (**as shipped**) | 0 | 0 | 8 |
| name absent | 9 | 0 | 9 |

Toggling the flag alone changes nothing. **No state yields the pair (9, 8).**

**So: a sentence in either OB1 file that pairs two counts without naming a
single `.env` state for both is a FAIL, and so is any sentence explaining which
env file wins.** Neither is needed — see C76-C79.

## THE THIRD TRAP — renders are NOT stderr-clean on every seed

Attempt 1's plan said "stderr must be empty on every render". That was false for
the audience this item is written for, and the tester was right to refute it. See
T1 for the corrected bar: what stderr contains depends on which env file seeds
the render, and the expected content is now stated per case rather than assumed
empty.

---

# Part A — one case per acceptance criterion

## T1 — the render matrix (acceptance criterion 1)

Run from `<wt>/OB1/docker`, with `COMPOSE_PROFILES=` blanked on every line.
`wc -l` of `docker compose -f docker-compose.yml <flags> config --services`.

**The stderr bar, corrected — attempt 1 got this wrong.** It is not "empty on
every one"; it depends on the env file seeding the render, and each case is
measured:

**Copy the env file to `.env`; do NOT pass `--env-file`** (see the fourth trap
below — `--env-file` changes the answer and produced attempt 2's wrong number).

**Every row names the `.env` state it was taken under. A row that does not is
the defect this item was rejected for.**

| `.env` state | Expected stderr |
|---|---|
| a real `.env` that sets everything (the developer's worktree) | empty |
| **this commit's `.env.example`** — ships `OB_APP_MEMORY_PASSWORD=` | **0** |
| the PREVIOUS example (`aa4a31d`), or this one **minus** its `OB_APP_MEMORY_PASSWORD` line | **9** x `The "OB_APP_MEMORY_PASSWORD" variable is not set. Defaulting to a blank string.` |

The third row is why `OB_APP_MEMORY_PASSWORD` was added this round. **Nine sites,
nine warnings — there is no dedupe.** Identical for the bare and the all-four
render. Anything OTHER than these is a finding.

Check the exit code on every render you take a warning count from. A `config`
that exits 1 stops emitting partway and leaves a TRUNCATED stderr, which looks
exactly like a real count and is not one.

| Flags | Expected |
|---|---|
| (none) | 20 |
| `--profile idea-refinery` | 21 |
| `--profile research` | 22 |
| `--profile notebook` | 23 |
| `--profile wiki` | 24 |
| `--profile idea-refinery --profile research` | **23** |
| `--profile research --profile wiki` | 26 |
| `--profile research --profile notebook` | 25 |
| `--profile wiki --profile notebook` | 27 |
| `--profile research --profile wiki --profile notebook` | 29 |
| all four | 30 |

Plus the core count the table's first row asserts: the bare render minus the
five always-on scheduled services is **15**:

```bash
COMPOSE_PROFILES= docker compose -f docker-compose.yml config --services \
  | grep -vE "openbrain-(cron|gmail-pull|gmail-prune|digest|podcast)$" | wc -l
```

**PASS** = every row matches. **FAIL** = any row off by any amount, including
rows the developer did not add (the whole table is this item's responsibility
now, not just the new row).

### T1b — the per-profile service lists

`comm -13 <bare sorted> <profiled sorted>` must yield exactly:

| Profile | Services added |
|---|---|
| `idea-refinery` | `openbrain-idea-refinery` |
| `research` | `openbrain-curator`, `openbrain-research` |
| `notebook` | `surrealdb`, `open_notebook`, `open-notebook-backup` |
| `wiki` | `openbrain-wiki`, `openbrain-wiki-backup`, `openbrain-wiki-viewer`, `openbrain-workbench` |

These are the rows of the README's "What each profile turns on" table. A service
in that table that the render does not add, or an added service the table omits,
is a FAIL.

## T2 — the two-way variable diff (acceptance criterion 2), unbounded

```bash
cd "<wt>/OB1/docker"
# compose-SUBSTITUTED names. TWO guards, both load-bearing, both established by
# a refutation rather than by design:
#   guard 1 - strip whole-line YAML comments: a name appearing ONLY in a comment
#             is never substituted (PUBLIC_DOMAIN, docker-compose.yml:867)
#   guard 2 - the leading (^|[^$]) excludes $${VAR} shell escapes inside the
#             backup containers' inline command: blocks (RETAIN_COUNT, BACKUP_INTERVAL)
grep -hvE '^[[:space:]]*#' docker-compose.yml docker-compose.scheduled.yml \
  | grep -oE '(^|[^$])\$\{[A-Za-z_][A-Za-z0-9_]*' \
  | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*' | sed 's/\${//' | sort -u > /tmp/cv.txt
grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env.example | sed 's/=$//' | sort -u > /tmp/ev.txt
comm -23 /tmp/ev.txt /tmp/cv.txt    # direction A
comm -13 /tmp/ev.txt /tmp/cv.txt    # direction B
```

- **Direction A - every example variable is read by a service. MUST BE EMPTY.**
  A name here is a variable the template invites an operator to set that nothing
  substitutes: a FAIL, and the specific failure the anchor calls out ("a variable
  added that nothing reads FAILS").
- **Direction B - compose reads it, the template lacks it. Expect 70 names**,
  all pre-existing; enumerated in the findings note section 3.
- **95** compose-substituted names, **25** template-declared.

**The arithmetic, since three different figures are now in circulation.** 82
before this item (with both guards). Ten added in commit 1 -> 72. Attempt 1's
plan and findings printed 96/72 using guard 2 ALONE, over-counting PUBLIC_DOMAIN
by one; the true figures at that commit were 95/71. One more variable
(OB_APP_MEMORY_PASSWORD) added this round -> **70**. So: a direction-B count of
**71 means you dropped guard 1, 72 means you dropped guard 2, 73 means you
dropped both** — measured, all four variants:

| variant | substituted | direction B |
|---|---|---|
| both guards | 95 | **70** |
| no comment strip | 96 | 71 |
| no `$$` guard | 97 | 72 |
| neither | 98 | 73 |

(Attempt 2's plan printed 72/84/85 here. Those figures occur under no variant at
all; they were written from memory rather than measured, which is the same defect
as C59 in a different place.)

Verify each guard yourself rather than trusting this paragraph:

```bash
grep -nE 'PUBLIC_DOMAIN' docker-compose.yml docker-compose.scheduled.yml   # ONE hit, :867, inside a #
grep -nE '\$+\{(RETAIN_COUNT|BACKUP_INTERVAL)\}' docker-compose.yml       # all $${...}, in command: blocks
```

A second proof of guard 1 that involves no line-reading at all: compose warns for
every unset name that has no default. Seeded from the previous example it warned
8 times for `OB_APP_MEMORY_PASSWORD` (unset, default-less) and **never** for
`PUBLIC_DOMAIN`, which is equally unset and equally default-less - because
compose never sees it.

### T2b — each added variable's default matches the compose

For each of the ten, `grep -nE "\\\$\{<NAME>(\}|:)" docker-compose.yml` and
compare the `${VAR:-default}` against the template's value:

| Variable | Template value | Compose | Line |
|---|---|---|---|
| `OPENBRAIN_DB_BACKUP_RETAIN_COUNT` | `2` | `${...:-2}` | `docker-compose.yml` openbrain-db-backup |
| `OPENBRAIN_DB_BACKUP_CRON` | `0 2 * * *` | `${...:-0 2 * * *}` | same service |
| `OPENBRAIN_WIKI_BACKUP_RETAIN_COUNT` | `2` | `${...:-2}` | openbrain-wiki-backup |
| `OPENBRAIN_WIKI_BACKUP_INTERVAL` | `86400` | `${...:-86400}` | same service |
| `OPEN_NOTEBOOK_BACKUP_RETAIN_COUNT` | `2` | `${...:-2}` | open-notebook-backup |
| `OPEN_NOTEBOOK_BACKUP_CRON` | `20 2 * * *` | `${...:-20 2 * * *}` | same service |
| `OPEN_NOTEBOOK_ENCRYPTION_KEY` | blank | `${...}` — **no default** | open_notebook |
| `OPEN_NOTEBOOK_LLM_API_KEY` | `not-needed` | `${...:-not-needed}` | open_notebook `EMBEDDING_API_KEY` |
| `SURREAL_USER` | blank | `${...}` — no default | surrealdb / open_notebook / open-notebook-backup |
| `SURREAL_PASSWORD` | blank | `${...}` — no default | same three |

The two deliberate departures from the old root template are **C42**
(`OPEN_NOTEBOOK_LLM_API_KEY`'s default) and **C43** (`POSTGRES_USER` /
`POSTGRES_DB` are not read at all) below; both are claims, verify them rather
than accepting the diff's word.

## T3 - the outside-OB1 consumer list (acceptance criterion 3)

**THIS IS THE CASE ATTEMPT 1 FAILED.** The table listed two consumer kinds and
closed with "BOTH consumers reach these services by container name" - a
universal over a set that was missing four surfaces. Attack the completeness
claim first; the individual rows were all correct last time and are the easy
half.

### T3a - every row has a line behind it

| README row | Check |
|---|---|
| 1 `openbrain-workbench:8000` | `portal/config/caddy/Caddyfile:136` |
| 2 `openbrain-wiki-viewer:8080` | `portal/config/caddy/Caddyfile:143` |
| 3 `open_notebook:5055`, `:8502` | `portal/config/caddy/Caddyfile:242`, `:250` |
| 4 tailscale companion -> `open_notebook` | `frontend/entrypoint.sh:60` (host default), route table `:97`, `:99` |
| 5 agent-bridge -> `openbrain-research` | `agent-org/docker/docker-compose.yml:208` delivers `AO_RESEARCH_URL`; `agent-org/agent-bridge/app/config.py:373` is the default; `app/modules/grounding.py:12` states the mechanism |
| 6 Deep Research tool -> `openbrain-research` | `owui/tools/deep_research.py:47`, deployed per `owui/manifest.csv:10` |
| 7 Server Status, TWO modules | `status-pipe/modules/system-health/service/system_health.py:58`, `:66`; `status-pipe/serve/tailscale_serve_pipe.py:119`, `:135`, `:652` |
| operator path | `scripts/backup/restore-from-snapshot.ps1:432` |

`critical: False` on the two system-health probes is a claim; confirm it, because
"degrades rather than alarms" depends on it.

### T3b - the row-4 correction, which is the subtle one

The README says row 4 reaches Open Notebook DIRECTLY but reaches the wiki
THROUGH portal Caddy. **Attempt 1's tester reported it as a direct
`openbrain-wiki-viewer` reach; that is wrong and the README now says why.**
Verify the correction rather than either previous claim:

```bash
grep -n "QUARTZ_HOST" frontend/docker-compose.yml frontend/entrypoint.sh frontend/.env frontend/.env.example
```

Expect `frontend/docker-compose.yml:366` = `${QUARTZ_HOST:-caddy}`,
`frontend/.env:133` = `caddy`, and `entrypoint.sh:66` = `${QUARTZ_HOST:-openbrain-wiki-viewer}`
- a fallback the compose always overrides. `frontend/docker-compose.yml:361-364`
gives the reason it moved behind Caddy. (The tester also cited `:350` for
QUARTZ_HOST; that line is OPEN_NOTEBOOK_HOST.) If you conclude the README is
wrong here, say which of those four lines says so.

### T3c - THE COMPLETENESS TEST: re-derive, do not spot-check

Run the README's own rebuild grep from the ai-stack worktree root, unbounded:

```bash
grep -rnE "(https?://|\"host\"[: ]+\"|target_host[\"'=: ]+|_HOST[=:] *|reverse_proxy +)(openbrain-research|openbrain-wiki|openbrain-wiki-viewer|openbrain-workbench|openbrain-curator|openbrain-idea-refinery|open_notebook|surrealdb)" . \
  --exclude-dir=OB1 --exclude-dir=.git --exclude-dir=node_modules \
  --exclude-dir=archive --exclude-dir=documentation --exclude-dir=backups
```

**Expect 23 lines in 15 files** (21 in configuration or code, plus two prose
mentions in `owui/README.md` and `CLEANUP-PLAN.md`). Read every one at its line
and classify it:

- a **runtime reach by container name** -> must be in the table (13 call sites
  across the 7 surfaces);
- an **.env / .env.example delivery line** for one of those 7
  (`agent-org/docker/.env:58`, `.env.example:114`, `frontend/.env:127`,
  `.env.example:127`) -> not a separate surface;
- **prose or inventory** (`stack.manifest.toml:466`, `:593`) -> not a surface.

Then widen it yourself in a way the developer did not, and say how you widened
it. Two suggestions that would have caught last round's misses: a bare
name-only grep (~60 files - mostly inventories, but read the outliers), and a
grep for the SERVICE PORTS (`8502`, `5055`, `8446`, `8818`, `8816`) in case a
consumer builds the host from a variable.

**A single runtime reach by container name that the table omits is a FAIL.** The
table now states its scope explicitly, so a host-side `127.0.0.1` probe or a
`docker exec` is NOT a miss - those are named in the excluded class. If you
believe something in that excluded class belongs in the table, that is a class-3
note, not a fail.

### T3d - the excluded class is named, not forgotten

Confirm the three named exclusions are real and really are host-side:

```bash
grep -nE "127.0.0.1:(8818|8816)" scripts/checks/check-openbrain-health.ps1
grep -nE "docker exec (open_notebook|openbrain-wiki-viewer)" scripts/checks/stack-watchdog.ps1 scripts/checks/wiki-latency-probe.ps1
```

If one of them turns out to reach by container name over a shared network after
all, it belongs in the table and this is a FAIL.

## T4 — how-to-set, measured (acceptance criterion 1, second half)

Four claims, four commands, from `<wt>/OB1/docker`:

1. **A CLI `--profile` REPLACES the env list.** With all four in `.env` (this
   host), `docker compose -f docker-compose.yml --profile research config
   --services | wc -l` → **22**, not 30, and not 52. FAIL if it unions.
2. **The env file is a working declaration site.** No flags, `.env` as-is →
   **30**, and `diff` against the four-flag render is clean:
   ```bash
   diff <(docker compose -f docker-compose.yml config --services | sort) \
        <(COMPOSE_PROFILES= docker compose -f docker-compose.yml --profile research \
          --profile wiki --profile notebook --profile idea-refinery config --services | sort)
   ```
3. **`stack.py enable research` resolves all four.** Use a SCRATCH state file —
   never the checkout's `.stack/state.json`:
   ```bash
   cd "<wt>"; python scripts/stack/stack.py --state /tmp/s.json enable research
   python scripts/stack/stack.py --state /tmp/s.json up ob1 --dry-run
   ```
   Expect `ob1  profiles: idea-refinery, research, wiki, notebook` and a dry-run
   line carrying all four `--profile` flags.
4. **The union makes `--headless` inert for this plane while `.env` declares the
   surfaces.** `enable research --headless` on a fresh scratch state prints
   `dropped surface profiles ob1:wiki, ob1:notebook` and `ob1  profiles:
   idea-refinery, research` — and then `up ob1 --dry-run` emits **all four**
   anyway. The mechanism is `effective_profiles()` in `scripts/stack/stack.py`,
   which unions `compose_profiles_env(...)`; read the function, do not infer it
   from the output. **This is the one claim in the README a reviewer is most
   likely to call wrong**, because it says a documented flag does not do what it
   says. It is measured; re-measure it.

## T5 — commit shape and blast radius (acceptance criteria 4 and 5)

```bash
git -C "<wt>/OB1" log --oneline -5                      # 556195b, e7a39a7, 1218eff, fdfb7af, aa4a31d
git -C "<wt>/OB1" merge-base HEAD origin/feature/integrated-knowledge-system   # fe3e045
git -C "<wt>/OB1" diff --stat fe3e045 HEAD             # exactly 2 files, both docs
git -C "<wt>/OB1" status --porcelain                    # clean
git -C "<wt>" status --porcelain                        # ` M OB1` + the 2 ai-stack files, NO `M  OB1`
git -C "<wt>/OB1" log origin/feature/integrated-knowledge-system..HEAD --oneline   # exactly 5 commits, unpushed
```

**The pre-commit's OB1 gates (5b recipe tests, 5c `deno check`, 5d integration
images) are not merely "unaffected" — they do not run at all.** All three key off
a **staged OB1 gitlink** (`.githooks/pre-commit` lines 109-153: "Skips itself
instantly when no OB1 gitlink is staged"). The ai-stack commit for this item
stages no gitlink, so the correct evidence is that those three steps print their
skip line, not that they pass. Verify by reading the hook and by watching the
ai-stack commit's hook output. If you see them actually executing, something
staged a gitlink and rule 2 was broken.

Renders before and after the OB1 commit must be identical — docs-only is a claim
too. `git -C "<wt>/OB1" stash` is NOT the way to check this; use
`git -C "<wt>/OB1" diff fe3e045 HEAD --stat` and confirm no `.yml` appears.

---

# Part B — every sentence introduced, as a claim

Check each against the file or command named. A claim you cannot reproduce is a
FAIL even if it sounds right; that is the whole reason this list exists.

## B.1 `OB1/docker/README.md` — render table and its notes

- **C1** `idea-refinery` + `research` renders **23** services. → T1
- **C2** Every number in the render table is the output of
  `docker compose -f docker-compose.yml <flags> config --services | wc -l`,
  not 20 plus the per-profile deltas. → T1, all eleven rows
- **C3** 22 is the count for `research` ALONE — the number historically written
  into the pair's row by mistake. → T1
- **C4** The table's renders assume `COMPOSE_PROFILES` is unset; on a host that
  declares all four in `.env`, the `(none)` row reads 30, not 20. → T4.2 and the
  trap section above

## B.2 `README.md` — "How to turn these on"

- **C5** There are three ways to turn profiles on, and they do not all behave the
  same. → T4 (the section enumerates exactly three; a fourth would make this false)
- **C6** A CLI `--profile` REPLACES `COMPOSE_PROFILES` rather than unioning with
  it; measured with all four in `.env`, `--profile research` alone renders 22,
  not 30. → T4.1
- **C7** Therefore a script that passes one flag cannot be rescued by an
  operator's env file, and a script that passes flags at all must pass every
  profile it wants. → follows from C6; check the reasoning, not a command
- **C8** `COMPOSE_PROFILES` in this directory's `.env` is this plane's own
  declaration site, the same as every other ai-stack plane since the per-plane
  env split (D17). → `frontend/.env`, `inference/.env` carry their own; CLAUDE.md
  "Environment (stack-layers L.2 / D10)"
- **C9** Compose loads `OB1/docker/.env` natively because that is the project
  directory, so no `--env-file` is needed and the working directory is
  irrelevant to it. → T4.2 renders 30 with no `--env-file`; run it from two
  different working directories with `-f` if you want the second half
- **C10** With that line present a bare `docker compose config --services`
  renders 30, service-for-service identical to the four-flag render (`diff`
  clean). → T4.2
- **C11** `python scripts/stack/stack.py enable research` resolves this plane's
  profiles from `stack.manifest.toml` and prints
  `ob1  profiles: idea-refinery, research, wiki, notebook`. → T4.3
- **C12** `up ob1` then emits all four `--profile` flags — 30 services. → T4.3
  for the flags, T1 for the 30
- **C13** `idea-refinery` is the plane's one `default` profile and `requires`
  `research`, so it pulls the engine in. → `[planes.ob1.profiles.idea-refinery]`
  in `stack.manifest.toml`: `default = true`, `requires = ["research"]`
- **C14** `wiki` and `notebook` come from the research product's `surfaces`. →
  `[products.research] surfaces = { ob1 = ["wiki", "notebook"] }`
- **C15** The driver unions this plane's `COMPOSE_PROFILES` into whatever it
  resolves, deliberately, because a `--profile` flag must never start FEWER
  containers than a bare invocation would. → `effective_profiles()` in
  `scripts/stack/stack.py` and its docstring
- **C16** The effect is that `stack.py enable research --headless` prints that it
  dropped `wiki` and `notebook` and then drives all four anyway when `.env`
  declares all four. → T4.4
- **C17** If you want `--headless` to mean anything for this plane, do not put
  the surface profiles in `.env`. → follows from C15/C16

## B.3 `README.md` — "Consumers outside this project" — **SUPERSEDED by B.7**

> Attempt 1 FAILED on this section and it was rewritten, not patched. C18-C25
> below describe the OLD two-row table and are kept only so a reader comparing
> the two attempts can see what changed. **Check B.7 instead.** C19 (paths are
> ai-stack-relative) and C25 (nothing in OB1 records these edges) survive
> verbatim as C56 and are still live claims.

- **C18** Turning `wiki`, `notebook` or `research` off breaks callers in other
  compose projects; none fails at start, all fail at request time. → T3, plus the
  observation that no ai-stack compose file `depends_on` an OB1 service (they are
  different projects; the reference is by container name over a shared network)
- **C19** The paths in the second column are relative to the ai-stack repo that
  carries this submodule, not to the OB1 checkout. → the files do not exist under
  `OB1/`
- **C20** portal Caddy reverse-proxies `openbrain-workbench:8000` (`wiki`); the
  `/workbench/*` route 502s when the profile is off. → `portal/config/caddy/Caddyfile`
- **C21** portal Caddy reverse-proxies `openbrain-wiki-viewer:8080` (`wiki`). → same file
- **C22** portal Caddy reverse-proxies `open_notebook:5055` and `:8502`
  (`notebook`). → same file, two separate blocks
- **C23** The OWUI Server Status pipe probes `open_notebook:5055/api/config`
  (`notebook`) with `critical: False`, so the panel degrades rather than
  alarming. → `status-pipe/modules/system-health/service/system_health.py`
- **C24** It probes `openbrain-research:8000/health` (`research`), same shape. → same file
- **C25** Both consumers reach these services by container name across the shared
  `ai-stack_*` networks, so nothing in the OB1 project records the dependency. →
  `grep -rn` the consumer paths inside `OB1/` finds nothing; that absence is the claim

## B.4 `OB1/docker/.env.example` — the `COMPOSE_PROFILES` line

- **C26** The line is COMMENTED OUT (`#COMPOSE_PROFILES=...`) and names
  `research,wiki,notebook,idea-refinery`. → read the file. A live assignment here
  would be a FAIL: it would make direction A of T2 non-empty and silently change
  what a fresh clone starts.
- **C27** Unset = core only, 20 services; the four render the full 30. → T1
- **C28** A CLI `--profile` REPLACES this list rather than adding to it. → T4.1
  (same claim as C6, repeated in the template on purpose — an operator editing
  `.env` may never open the README)

## B.5 `.env.example` — the section header

- **C29** These variables were documented ONLY in ai-stack's pre-split root
  `.env.example` and never in this file. → `git -C <ai-stack> show
  4934529:.env.example` has them; `grep -c '^<NAME>=' ` on the PRE-change
  `.env.example` (`git -C <wt>/OB1 show fe3e045:docker/.env.example`) returns 0
  for all ten
- **C30** Compose has always read them from here, because this project's
  directory is `OB1/docker` and `OB1/docker/.env` is what it substitutes from. →
  T4.2 mechanism; also `name: open-brain` + the file's location
- **C31** Every default given is the `${VAR:-default}` the compose file actually
  carries. → T2b

## B.6 `.env.example` — per-variable claims

- **C32** `OPENBRAIN_DB_BACKUP_RETAIN_COUNT` compose default 2; it is the number
  of snapshots retained, most recent first. → T2b + `backup/openbrain-db-backup.sh`
- **C33** `OPENBRAIN_DB_BACKUP_CRON` compose default `0 2 * * *`, container TZ
  UTC. → T2b; `TZ: UTC` is in the same service block
- **C34** All three scripts in `OB1/docker/backup/` run a PRECHECK before
  touching any file: they log a `PRECHECK SKIP` line and exit 0 if the source
  data is missing/empty or a liveness probe fails — `pg_isready` for postgres,
  the `/health` endpoint for surreal, a non-empty data dir containing `.git` for
  the wiki. → `grep -n PRECHECK OB1/docker/backup/*.sh`; each has an `exit 0`
  under it. The root template said "a TCP health probe", which is true of none of
  the three; the wording was corrected rather than copied, and that correction is
  itself the claim to check.
- **C35** `OPENBRAIN_WIKI_BACKUP_RETAIN_COUNT` default 2; the service is `wiki`
  profile and tars `openbrain-wiki-data` + `wiki-assets`. → T2b + the service block
- **C36** `OPENBRAIN_WIKI_BACKUP_INTERVAL` is a sleep-loop interval in SECONDS,
  not a cron expression, default 86400 — crond misses fire-windows on Docker
  Desktop VM clock jumps. → the service's `command:` is a `while true; sleep
  "$${BACKUP_INTERVAL}"` loop; the comment above it states the reason
- **C37** `OPEN_NOTEBOOK_BACKUP_RETAIN_COUNT` default 2, `notebook` profile. → T2b
- **C38** `OPEN_NOTEBOOK_BACKUP_CRON` default `20 2 * * *`, container TZ UTC. → T2b
- **C39** `SURREAL_USER` / `SURREAL_PASSWORD` are REQUIRED when `notebook` is on:
  substituted with NO default into surrealdb's own `start --user/--pass`, into
  `open_notebook` and into `open-notebook-backup`, so a blank value starts the
  datastore with empty credentials. → T2b; three substitution sites each
- **C40** SurrealDB v2 only honours `start --user/--pass` on FIRST boot; an
  existing datastore keeps the user it was created with until a `DEFINE USER`
  pass rotates it. → the compose comment on `open-notebook-backup` says so;
  corroborated by `documentation/notes/` (`surrealdb-v2-define-user-gotcha`)
- **C41** `OPEN_NOTEBOOK_ENCRYPTION_KEY` is required when `notebook` is on —
  substituted with no default, a blank value passes through as blank. → T2b
- **C42** `OPEN_NOTEBOOK_LLM_API_KEY`'s compose default is `not-needed`, the root
  template left it BLANK, and blank is not the same thing: a blank value
  overrides the default and sends an empty key. → T2b; `${OPEN_NOTEBOOK_LLM_API_KEY:-not-needed}`
  on `open_notebook`'s `EMBEDDING_API_KEY`, and `OPEN_NOTEBOOK_LLM_API_KEY=` in
  `git show 4934529:.env.example`. **This is a deliberate departure from the
  anchor's "the old root example's comments and defaults"** — the anchor's own
  next clause ("each default corrected to what the compose actually uses") is
  what licenses it.
- **C43** `POSTGRES_USER` and `POSTGRES_DB` are NOT read from this file. Compose
  sets both as LITERALS on `openbrain-db` and `openbrain-db-backup`; there is no
  `${POSTGRES_USER}` or `${POSTGRES_DB}` anywhere in either compose file, so
  setting them here has no effect. → `grep -nE 'POSTGRES_USER|POSTGRES_DB'` on
  both compose files returns four LITERAL assignments and zero substitutions.
  **This is why they are a prose note and not two assignments** — adding them
  would have failed T2 direction A. It is a deviation from the anchor's
  "thirteen"; see findings §2.
- **C44** `POSTGRES_PASSWORD` IS substituted and must be set. → three
  substitution sites; it was already in this template before the change, which is
  the second reason the count is ten and not thirteen

---

## B.7 Sentences introduced by attempt 2 (the T3 fix)

Attempt 1's B.3 claims C18-C25 are SUPERSEDED by these; the section they
described was rewritten, not patched.

- **C45** The table's scope is a runtime reach by container name to one of the
  ten profiled services over a shared `ai-stack_*` network. -> the scope sentence
  is the thing that makes completeness checkable at all; if you disagree with the
  scope, say so as a class-3 note rather than failing a row
- **C46** There are **seven** such surfaces at **thirteen** call sites, in three
  compose projects (`portal`, `frontend` - including the OWUI-hosted tool and
  pipes that run inside `openwebui` - and `agent-org`). -> T3a, T3c
- **C47** Rows 1-3: portal Caddy reaches `openbrain-workbench:8000` (`:136`),
  `openbrain-wiki-viewer:8080` (`:143`), `open_notebook:5055` (`:242`) and
  `:8502` (`:250`). -> T3a (unchanged from attempt 1, re-verified)
- **C48** Row 4: the `frontend` plane's `tailscale` companion reaches
  `open_notebook` at `:8502` and `:5055/api/config` through its serve route
  table, with the host defaulted at `entrypoint.sh:60`. -> T3a
- **C49** Row 4 reaches the wiki THROUGH portal Caddy, not directly:
  `frontend/docker-compose.yml:366` is `${QUARTZ_HOST:-caddy}` and the deployed
  value is `caddy:8446`; `entrypoint.sh:66`'s `openbrain-wiki-viewer` fallback is
  always overridden. So `wiki` off breaks the tailnet wiki route via row 2. ->
  **T3b. This contradicts attempt 1's tester and is the single most likely place
  for attempt 2 to be wrong. Check it first.**
- **C50** Row 5: `agent-org`'s `agent-bridge` reaches `openbrain-research:8000`;
  grounding (P4.0a) and the Tier-2 advisor stop producing when `research` is off.
  -> T3a
- **C51** Row 6: the deployed OWUI Deep Research tool reaches
  `openbrain-research:8000`. -> T3a
- **C52** Row 7 is TWO status-pipe modules, not one; the earlier table named only
  `modules/system-health/`, which does not cover `serve/tailscale_serve_pipe.py`.
  -> T3a
- **C53** `scripts/backup/restore-from-snapshot.ps1:432` `docker exec`s into
  `open-notebook-backup` and has it reach `surrealdb:8000`; both ends are
  `notebook`. -> T3a
- **C54** The excluded host-side class - `check-openbrain-health.ps1`
  (`127.0.0.1:8818`, `:8816`), `stack-watchdog.ps1` and `wiki-latency-probe.ps1`
  (`docker exec`) - addresses published ports or the docker CLI, not container
  names, and `stack-watchdog.ps1` repairs rather than consumes. -> T3d
- **C55** Inventories (`scripts/lib/stack-services.json`, `stack.manifest.toml`,
  `status-pipe/orchestrator.py`'s docstring) and `.env` delivery lines are not
  separate surfaces. -> T3c classification
- **C56** Nothing in the OB1 project records these edges, so the list must be
  re-derived rather than maintained; the README carries the grep that does it.
  -> `grep -rn` the consumer paths inside `OB1/` finds only the README's own rows
- **C57** That grep returns 23 lines in 15 files (21 config/code + 2 prose). -> T3c

## B.8 Sentences introduced by attempt 2 (`.env.example`)

- **C58** `OB_APP_MEMORY_PASSWORD` is REQUIRED: substituted with no default at
  nine sites, eight in `docker-compose.yml` and one in the scheduled file, every
  one a `DB_PASSWORD`. -> `grep -nE 'OB_APP_MEMORY_PASSWORD' docker-compose*.yml`;
  all nine are code lines, none a comment
- **C59 — REPLACED after attempt 2 FAILED on it.** It now reads: `docker compose
  config` warns NINE times, once per site. -> T1's stderr table. The retired
  version said 8 and called the gap "compose dedupes somewhere; measured, not
  explained". See C65-C68 for what replaced it and why the old number appeared.
- **C60** It was in neither ai-stack's root template nor this one before now, and
  it is a FOURTEENTH variable beyond the anchor's thirteen, added because
  acceptance criterion 2's literal wording asks for it and because it is the trap
  the item's goal names. -> `git show 4934529:.env.example | grep -c
  OB_APP_MEMORY_PASSWORD` -> 0; `git show fe3e045:docker/.env.example | grep -c`
  -> 0

## B.9 Corrected numbers (attempt 1 shipped these wrong)

- **C61** 95 compose-substituted names, not 96: `PUBLIC_DOMAIN` occurs once, in a
  YAML comment at `docker-compose.yml:867`. -> T2
- **C62** Direction B is 70: 82 before the item, 72 after ten were added, 71 once
  `PUBLIC_DOMAIN` is excluded, 70 with `OB_APP_MEMORY_PASSWORD` added. -> T2
- **C63** Direction A is still empty and 25 names are declared. -> T2
- **C64** Both committed blobs are pure LF; a `core.autocrlf=true` clone shows CR
  in the working copy only. -> the CRLF trap section

## B.10 Sentences introduced by attempt 3

- **C65** `OB_APP_MEMORY_PASSWORD` has nine substitution sites and `docker compose
  config` warns **nine** times, once per site; there is no dedupe. -> T1's stderr
  table, with `.env` = the previous example and NO `--env-file`. Measured three
  ways: bare render, all-four render, and this commit's example minus only that
  line.
- **C66 — REPLACED after attempt 3 FAILED on it.** It now claims only the
  observable: passing `--env-file` changes how the `include:`d
  `docker-compose.scheduled.yml` resolves variables and changes the count
  (measured 9 without, 8 with), so counts taken that way are not comparable. ->
  T1 / trap 4. **No claim is made about which file wins.** The retired version
  said the included file "still resolves against this directory's `.env`" and
  drew from it that a variable disagreeing across the two files renders "one way
  in the core services and the other way in the scheduled ones, silently" — both
  refuted by the tester, who gave the two files DIFFERENT values and found all
  nine sites, the scheduled one included, rendering the `--env-file` value. The
  measurements are in findings section 14; the artifact depends on none of them.
- **C67** Therefore `--env-file` should not be passed to this project at all;
  compose reads `.env` from the project directory on its own. -> follows from C66
- **C68** A `config` render that exits non-zero truncates its stderr, so a
  warning count taken without checking the exit code can be an artefact. -> check
  `$?` on any render you count warnings from. **Use `config --services`, not a
  bare `config`**: the full render also resolves `env_file:` targets, two of
  which (`../recipes/*/.env`) are gitignored, so a bare `config` exits 1 in a
  fresh clone with `env file ... not found` while `--services` exits 0 on the
  same tree.
- **C69** The rebuild grep returns **21 lines in 13 files in a fresh clone**, 23
  in 15 on a deployed host; the two extra are the gitignored
  `agent-org/docker/.env:58` and `frontend/.env:127`. Two of the 21 are prose in
  Markdown. -> T3c
- **C70** That grep does NOT surface `frontend/entrypoint.sh:60`
  (`OPEN_NOTEBOOK_HOST=${OPEN_NOTEBOOK_HOST:-open_notebook}`), because the
  pattern needs the service name to follow the host token directly and here it
  sits behind a `:-`. The grep is the sweep; reading the file is the audit. ->
  run the grep and confirm the absence

## B.11 Sentences introduced by attempt 4

- **C71 — RETIRED at review.** It claimed "9 without `--env-file`, 8 with", which
  no single `.env` state produces. Both files now make no `--env-file` claim at
  all. See C76-C79.
- **C72** The artifact asserts NO mechanism for which env file wins. -> read both
  files; a sentence naming which file a site "resolves against" is a regression.
  Attempt 3's version was refuted, attempt 2's was refuted, and the third attempt
  at one is not wanted.
- **C73** `open_notebook` carries `env_file: ./.env`
  (`docker-compose.yml:1252-1253`) — the only `./.env` env_file in either compose
  file — so the WHOLE of `.env` is injected into that one container as
  environment, including keys no other service sees. This is not a substitution
  and is outside the nine-site count. -> `grep -nE '^\s*-?\s*\./\.env\s*$'`
  over both compose files returns exactly that one line. Stated in
  `.env.example`'s header as a blast-radius note.
- **C74** A bare `docker compose config` exits 1 in a fresh clone on gitignored
  `env_file:` targets (`../recipes/*/.env`), while `config --services` exits 0 on
  the same tree. -> C68; run both and compare exit codes.
- **C75** (findings only) The attempt-3 sentinel experiment's NUMBER was correct
  and reproduces; the inference drawn from it was invalid, because the sentinel
  had been declared in `.env`, which makes it silent under every candidate model.
  -> findings section 14; the matrix there has the discriminating row that was
  never run.

## B.12 Sentences introduced by attempt 5 (the review rejection)

Every one names its `.env` state. That is the point of this round.

- **C76** With `OB_APP_MEMORY_PASSWORD` **absent from `.env`**, every
  `docker compose config` warns once per site — **nine** — and renders those nine
  services with a blank database password. -> T1 row 3; measured bare and
  all-four, exit 0.
- **C77** The template **ships** the line, so a `.env` seeded from it warns
  **zero** times. -> T1 row 2. **This is the sentence that makes the other one
  useful**: without it a reader following the template sees zero warnings and has
  no idea whether that is good news.
- **C78** Therefore warnings naming this variable mean exactly one thing: your
  `.env` has lost the line. -> follows from C76 + C77, which between them cover
  both states.
- **C79** A BLANK value is a real declaration: compose warns **zero** times and
  renders `DB_PASSWORD: ""` at all nine sites — nine in an all-four-profile
  render, five in a bare one. -> render with `.env` = the template unchanged and
  grep the output. This is the quiet failure the warning cannot catch.
- **C80** `README.md`'s cross-group rebuild render no longer passes
  `--env-file .env`; it contradicted the same section's advice and is unnecessary
  because compose loads `.env` from the project directory. -> the render is
  identical with and without those two tokens.
- **C81** Of the 25 names declared in `.env.example`, only **4** are substituted
  into `open_notebook` (its own `SURREAL_*` / `OPEN_NOTEBOOK_*` keys); the other
  **21** belong to other services and reach that container anyway through
  `env_file: ./.env`. -> parse the `open_notebook` service block for `${...}`
  names and intersect with the template's declared names.
- **C82** No harness process vocabulary (anchor, acceptance criterion, item
  workflow) survives in `.env.example`; a bare `ai-stack item <id>` reference is
  the shape the file already used at `README.md:66`. -> grep both files for
  `anchor`/`acceptance`/`attempt`.

# What a FAIL looks like, ranked

1. **A runtime reach by container name that the table omits** → this is what
   attempt 1 failed on, and the table's whole value is that it is complete. A
   gap makes it worse than no table, because a reader will trust it. Run T3c
   before anything else, and widen the sweep in a way the developer did not.
2. Any render count wrong → the item's core deliverable. Not fixable by editing
   prose; the number must be re-rendered.
3. T2 direction A non-empty → the template invites an operator to set something
   inert. The anchor names this an outright FAIL.
4. `M  OB1` staged in the ai-stack worktree, or anything pushed → scope breach,
   regardless of whether the content is right.
5. Any B-part claim you cannot reproduce → delete the sentence rather than keep
   a plausible one nobody has checked. The three to attack hardest:
   - **C49** (row 4 reaches the wiki via Caddy, not directly) — it contradicts
     attempt 1's tester, so one of the two is wrong;
   - **C34** and **C42** — inherited from the old root template and then
     corrected against the source, which is where a copied-through error hides;
   - **C76-C79** — this paragraph has now failed twice and been rejected once,
     for a wrong count, a false mechanism, and mismatched states. Check that
     every count names its `.env` state, that no sentence pairs two counts from
     different states, and that neither file has regrown a sentence saying which
     env file wins. **A mechanism claim in the artifact is a FAIL even if it is
     true**, because nothing in the evidence establishes it.

**Not a fail:** anything in the excluded host-side class (T3d), the CRLF smudge
in a fresh clone (trap 2), or a render that warns exactly as T1's stderr table
predicts. Each of those cost a previous attempt or tester time; they are written
down so the next one spends it elsewhere.
