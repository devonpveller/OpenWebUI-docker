# Test plan — `sl-ob1-docs` (OB1's own profiles + variables documentation)

Anchor: `queue.ps1 -Show -Id sl-ob1-docs`, full text at
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ob1-docs.json`.

**What changed, in one line:** two documentation files inside the OB1 submodule —
`OB1/docker/README.md` and `OB1/docker/.env.example` — so that a newcomer holding
only the OB1 checkout can see which compose profiles exist, what each gates, how
to turn them on, and which variables the plane reads. **No compose file and no
code changed, in either repo.**

| | |
|---|---|
| OB1 branch | `work/sl-ob1-docs`, commit **`aa4a31d`** |
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

---

# Part A — one case per acceptance criterion

## T1 — the render matrix (acceptance criterion 1)

Run from `<wt>/OB1/docker`, with `COMPOSE_PROFILES=` blanked on every line.
`wc -l` of `docker compose -f docker-compose.yml <flags> config --services`.
**stderr must be empty on every one** (a render that warns is a render you
cannot trust).

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
# compose-SUBSTITUTED names. The leading (^|[^$]) is load-bearing: it excludes
# $${VAR} shell escapes inside the backup containers' command blocks.
grep -oE '(^|[^$])\$\{[A-Za-z_][A-Za-z0-9_]*' docker-compose.yml docker-compose.scheduled.yml \
  | grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*' | sed 's/\${//' | sort -u > /tmp/cv.txt
grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env.example | sed 's/=$//' | sort -u > /tmp/ev.txt
comm -23 /tmp/ev.txt /tmp/cv.txt    # direction A
comm -13 /tmp/ev.txt /tmp/cv.txt    # direction B
```

- **Direction A — every example variable is read by a service. MUST BE EMPTY.**
  A name here is a variable the template invites an operator to set that nothing
  substitutes: a FAIL, and the specific failure the anchor calls out ("a variable
  added that nothing reads FAILS").
- **Direction B — compose reads it, the template lacks it. Expect 72 names**,
  all pre-existing; they are enumerated in the findings note §3. 82 was the count
  before this change; 72 is 82 minus the ten added. A count above 72 means the
  developer missed one of the ten; a count below 72 means they added something
  outside the anchor's scope.
- Counts to confirm while you are there: **96** compose-substituted names, **24**
  template-declared names.

**If you drop the `(^|[^$])` guard you will get 98 and 84** and conclude the
numbers are wrong. `RETAIN_COUNT` and `BACKUP_INTERVAL` are the two extra names,
and they are `$${...}` — escaped for the container's shell, never substituted by
compose. Check them yourself rather than trusting this paragraph:
`grep -nE '\$+\{(RETAIN_COUNT|BACKUP_INTERVAL)\}' docker-compose.yml`.

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

## T3 — the outside-OB1 consumer list (acceptance criterion 3)

In the **ai-stack** tree (the worktree root, not `OB1/`):

```bash
grep -nE "openbrain-workbench|openbrain-wiki-viewer|open_notebook" portal/config/caddy/Caddyfile
grep -rnE "open_notebook|openbrain-research" status-pipe/modules/system-health/
```

Every row of the README's "Consumers outside this project" table must have a
line behind it, and **no consumer of a profiled service may be missing from the
table**. Search widely enough to be able to say that second half honestly:
`grep -rn` the four wiki/notebook/research service names across `portal/`,
`status-pipe/`, `scripts/`, `owui/` and the other planes' compose files. If you
find a consumer the table omits, that is a FAIL — the table's value is that it
is complete.

`critical: False` on the two status-pipe probes is a claim (C19/C20); confirm it
in `status-pipe/modules/system-health/service/system_health.py`, because the
"degrades rather than alarms" wording depends on it.

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
git -C "<wt>/OB1" log --oneline -1                      # aa4a31d
git -C "<wt>/OB1" merge-base HEAD origin/feature/integrated-knowledge-system   # fe3e045
git -C "<wt>/OB1" show --stat HEAD                      # exactly 2 files, both docs
git -C "<wt>/OB1" status --porcelain                    # clean
git -C "<wt>" status --porcelain                        # ` M OB1` + the 2 ai-stack files, NO `M  OB1`
git -C "<wt>/OB1" log origin/feature/integrated-knowledge-system..HEAD --oneline   # exactly 1 commit, unpushed
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

## B.3 `README.md` — "Consumers outside this project"

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

# What a FAIL looks like, ranked

1. Any render count wrong → the item's core deliverable is wrong. Not fixable by
   editing prose; the number must be re-rendered.
2. T2 direction A non-empty → the template invites an operator to set something
   inert. The anchor names this an outright FAIL.
3. A consumer of a profiled service missing from the C20-C24 table → the table
   claims completeness; a gap makes it worse than no table, because a reader will
   trust it.
4. `M  OB1` staged in the ai-stack worktree, or anything pushed → scope breach,
   regardless of whether the content is right.
5. Any B-part claim you cannot reproduce → delete the sentence rather than keep
   a plausible one nobody has checked. C34 and C42 are the two that were
   inherited from the old root template and then corrected against the source;
   they are where a copied-through error would have survived.
