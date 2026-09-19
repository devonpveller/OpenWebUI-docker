# sl-env-split — findings

Findings sink for the stack-layers item `sl-env-split` (PLAN §2.7 / L.2,
DECISIONS D10, D15, D16, D17). Written by the developer in
`.claude/worktrees/wt-sl-env-split` on **2026-09-19**, branch
`work/sl-env-split`, base `development` at `2f5c451`.

Provenance labels used below:

- **[measured]** — a command was run in this worktree (or in the short-path
  scratch clone `C:\wt-envsplit-check`) and the quoted output is what it
  printed. Docker Compose v5.3.0, Windows 11, PowerShell 5.1.
- **[read]** — asserted by reading the named file to the end; no command.
- **[pre-existing]** — true before this item touched anything. Not introduced
  here, and not necessarily fixed here.

Nothing in this note was deployed. No container was started, stopped or
recreated; no `.env` on the operator's host was read, written or printed.

---

## 1. Three root-`.env.example` names that no compose file interpolates — deleted

`BACKUP_INTERVAL`, `RETAIN_COUNT` and `MIN_AGE_SECS` were assigned in the root
`.env.example` (pre-split lines 448, 449, 450) and are the **container-side**
names. Every sidecar sets them itself in its own `environment:` block from a
prefixed host variable — `- BACKUP_INTERVAL=${MNEMORY_BACKUP_INTERVAL:-86400}`
(`memory/docker-compose.yml:111`), `- RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT:-2}`
(`frontend/docker-compose.yml:373`) — so setting the bare name in an env file
changed nothing.

The earlier appearance of these three in a naive `${VAR` grep is an artefact of
compose's `$$` escape: the compose file writes `$${RETAIN_COUNT}` inside a shell
`command:`, and a regex that does not strip `$$` first reports it as an
interpolation. **[measured]** The corrected extraction (strip `$$`, then parse
the file as YAML so comments are gone, then collect `${...}`) finds no
interpolation of any of the three in any plane.

They are **deleted**, not moved, with the reason written into the root
`.env.example`. `sl-closeout` identified them and handed them to this item
(`documentation/notes/stack-layers-sl-closeout-findings.md` §3).

**Same class, opposite direction:** `OPEN_TERMINAL_API_KEY` looked like a
frontend variable under a naive grep. It is not — the only frontend occurrence
is inside a **commented-out** YAML line (`frontend/docker-compose.yml:241`,
the `TERMINAL_SERVER_CONNECTIONS` example), and compose parses YAML before it
interpolates, so a comment is never a reference. **[measured]** `frontend`
renders clean with no `OPEN_TERMINAL_API_KEY` line in `frontend/.env.example`.
It lives in `coder/.env.example` only, and `coder/.env.example` says in as many
words what uncommenting that line would require.

## 2. Seven search-gateway tunables that setting would not change

`PROVIDER_PRIORITY`, `CACHE_TTL_SECONDS`, `REQUEST_TIMEOUT_SECONDS`,
`CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_SECONDS`, `LOG_LEVEL` and
`LOG_QUERIES` sat in the root `.env.example` (pre-split lines 178-184) carrying
their defaults, reading exactly like knobs.

**[read]** They are real pydantic Settings fields
(`search/gateway/src/gateway/config.py:28-40`) — but **nothing puts them into
the gateway container**. `env_file: ../.env` was removed from that service on
2026-08-28, and its `environment:` block names only `GATEWAY_API_KEY`,
`SEARXNG_URL` and `REDIS_URL` (`search/docker-compose.yml:139-146`). The
container therefore runs on the code defaults regardless of what any env file
says. **[pre-existing]** since 2026-08-28.

Neither dropping them nor moving them as live lines is honest, so
`search/.env.example` documents all seven in a block headed **NOT WIRED**, with
the code default beside each and the one-line instruction for making one real
(add it to that `environment:` block *and* uncomment it here, in the same
change). They are prose lines, not `#NAME=value` lines, so nobody can uncomment
one into a false expectation.

## 3. `.gitignore` and the secret guard already covered `<plane>/.env` — verified, not assumed

The anchor asked for `.gitignore` to cover `*/.env` and for the secret guard's
globs to do the same. **Both already did, for structural reasons, and neither
needed a logic change:**

- **[measured]** `git check-ignore -v frontend/.env` → `.gitignore:9:.env`.
  Same for `inference/`, `memory/`, `search/`, `coder/`, `portal/`. A gitignore
  pattern with **no leading slash matches at every level**, so the single `.env`
  line has always covered them. `frontend/.env.example` is **not** ignored
  (verified separately) because no rule matches it.
- **[read + measured]** `scripts/checks/check-staged-secrets.ps1` rule 1
  matches on the file's **leaf** (`$leaf -eq '.env'`, `$leaf -like '.env.*'`),
  not on its path. Staging `portal/.env` and `frontend/.env` in the scratch
  clone produced `COMMIT BLOCKED — ENV FILE STAGED: frontend/.env` and
  `... portal/.env`; unstaging them returned `[secrets] staged files clean
  (51 scanned)`.

What this item added to both files is a **comment saying so**, because the
failure mode is a later reader "tidying" `.env` into `/.env` and silently making
six live secret files committable. A rule that is right by accident and
undocumented is one edit away from being wrong.

## 4. `sync-worktree-env.ps1` had already drifted from `harness.config.json`

**[read]** `scripts/agent-harness/new-worktree.ps1:52` reads the list from
configuration (`Get-HarnessSetting "worktree.env_files"`). `sync-worktree-env.ps1:26`
carried a **hardcoded** `@(".env", ".env.test", "OB1/docker/.env")` — three
entries against the config's six. The two OB1 recipe env files and
`agent-org/docker/.env` were therefore never refreshed by a sync, silently, and
the script still printed `N file(s) refreshed`. **[pre-existing]**, and this
item's six new plane files would have made it four of twelve.

Fixed: `sync-worktree-env.ps1` now reads the same setting.

## 5. `scripts/recovery/update-stack.bat` and `dev-helper.ps1` address the anchor project — broken since Part K, unrelated to this item

**[read]** `scripts/recovery/update-stack.bat` runs 28 **bare** `docker compose`
commands (`docker compose up -d openwebui` at :237, `build --no-cache openwebui`
at :183, `ps llama-cpp-upstream …` at :80) with no `-f`. From the repo root that
addresses the **anchor** project, which has owned **zero services** since K.5b
(2026-08-21). Every one of those lines fails with `no such service`.
`scripts/checks/dev-helper.ps1:63` (`docker compose config`) and `:102`
(`docker compose build --no-cache tailscale`) have the same shape.

**[pre-existing]**, caused by Part K and not by the env split. This item removed
the `--env-file .env` from that `.bat`'s 25 flag-carrying lines for consistency
with everything else, but did **not** fix the missing `-f`: that is a service-
lifecycle repair with its own blast radius, and guessing which plane each of 28
lines meant is exactly the kind of change that should not ride along in a
different item. Worth its own item.

## 6. The `WORKBENCH_KEY` placeholder in the watchdog is a precedence hazard the split did not create

**[read]** `scripts/checks/stack-watchdog.ps1:45-46` sets
`$env:WORKBENCH_KEY = 'healthcheck-noop-placeholder'` in its own process when the
variable is unset, so a `docker compose config` it runs does not warn. A shell
environment variable **wins over an env file** in compose's precedence order, so
if that process ever rendered the portal plane it would override the real
`WORKBENCH_KEY` in `portal/.env`.

It does not: the watchdog renders only `frontend` and its repair targets, and
the portal is a `manual` plane with no inventory rows. **[measured]**
`check-watchdog-repair-targets.ps1` lists 24 containers across coder, frontend,
inference, memory, open-brain and search — no portal row. So this is latent, not
live. Recorded because the split moved `WORKBENCH_KEY` into `portal/.env` and
made the two things look adjacent; the comment at `:26-44` still says
"docker-compose.yml" where it now means `portal/docker-compose.yml`.

## 7. Thirteen OB1 variables were documented in the root file and nowhere else

`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `OPENBRAIN_DB_BACKUP_RETAIN_COUNT`,
`OPENBRAIN_DB_BACKUP_CRON`, `OPENBRAIN_WIKI_BACKUP_RETAIN_COUNT`,
`OPENBRAIN_WIKI_BACKUP_INTERVAL`, `OPEN_NOTEBOOK_BACKUP_RETAIN_COUNT`,
`OPEN_NOTEBOOK_BACKUP_CRON`, `OPEN_NOTEBOOK_ENCRYPTION_KEY`,
`OPEN_NOTEBOOK_LLM_API_KEY`, `SURREAL_USER`, `SURREAL_PASSWORD`.

**[measured]** Their only `${...}` references are in `OB1/docker/docker-compose.yml`
(and `OB1/docker/backup/*.sh` read them from the container). They were **never**
read from the root `.env` — the OB1 project's directory is `OB1/docker`, so
compose has always loaded `OB1/docker/.env` for them. **[measured]**
`grep -c "^<NAME>=" OB1/docker/.env.example` returns 0 for all thirteen.

So the root `.env.example` was the only *template* documenting them, and OB1 is
a pinned submodule this item may not edit. Removing the assignments (required by
D10 — they are plane variables) would have deleted that documentation outright,
so the root file now carries a **named list** of the thirteen under
`OB1/docker/.env`, plus the sentence that OB1's own example does not carry them.

**OPEN, for a follow-up item inside the submodule:** add the thirteen to
`OB1/docker/.env.example`, then delete the pointer block from the root file.
Until then the pointer is the only thing standing between a fresh clone and
thirteen undocumented variables.

## 8. Citation sweep: what moved, what was already stale

Re-derived as the last step, by construct: for every file whose **line count
changed** in this item's diff, both the full-path form (`scripts/checks/x.ps1:18`)
and the bare-basename form (`x.ps1:18`) were searched across every tracked file
outside `OB1/`. **[measured]** 200 hits, 23 target files.

The classifier compared the OLD file's content at the cited line against the NEW
file, so "still correct", "moved", and "was never correct" are distinguished
rather than guessed.

**Repointed (correct before this item, moved by it) — 7:**

| citing file | citation | was | now |
|---|---|---|---|
| `scripts/agent-harness/andon.ps1:431` | `check-project-configs.ps1` | `:18` | `:19` |
| `scripts/agent-harness/drill-dark-factory.ps1:365` | `check-project-configs.ps1` | `:18` | `:19` |
| `CLEANUP-PLAN.md:324` | `.gitignore` | `:4-12` | `:11-19` |
| `CLEANUP-PLAN.md:361` | `.gitignore` | `:47-48` | `:54-55` |
| `CLEANUP-PLAN.md:365` | `.gitignore` | `:27` | `:34` |
| `scripts/agent-harness/queue.ps1:939` | `CLAUDE.md` | `:131` | `:200` |

(The `queue.ps1` one was **already** stale — old `CLAUDE.md:131` is the
plan-store routing bullet, not the "eight found in a day" sentence, which was at
`:186` and is now at `:200`. Repointed anyway: leaving a pointer that is now
doubly wrong is worse than correcting one this item did not break.)

**One false positive the bare-basename form produced, worth knowing:**
`CLEANUP-PLAN.md:413` matched `README.md:63` — but the citation is
`owui/README.md:63-67`, a file this item never touched. A basename sweep **must**
be read, not applied. **[measured]**

**Already stale before this item, NOT touched — recorded instead:**

| citing file | citation | why it is stale |
|---|---|---|
| `CLEANUP-PLAN.md:266` | `scripts/lib/stack-services.json:50` | the quoted `open_notebook` note is at `:228`; `:50` was the `env_file` schema comment before this item too |
| `CLEANUP-PLAN.md:318` | `scripts/checks/check-staged-secrets.ps1:55-63` | the filename-rules block is `:48-63`; and the row's ask (a `gw-` pattern) has since been **done** — the pattern is live at `:85` |
| `CLEANUP-PLAN.md:358` | `.gitignore:61` | old `:61` was `/reports`, not the `.claude/settings.local.json` rule the row describes |
| `CLEANUP-PLAN.md:437,438,893` | `.env.example:1`, `:52-56` | Ollama / LM Studio audit rows; that content was retired in 2026-08 and has no correct target |
| `DECISIONS.md:1705` | `.github/workflows/ci.yml:127` | `prove-clone-recursive` appears nowhere in `ci.yml`, before or after |
| `DECISIONS.md:1836` | `README.md:392` | README has been < 200 lines throughout |
| `WALKTHROUGH.md:384` | `.gitignore:88-89` | the `.quadrant/` rules were at `:94-101` before this item and are at `:101-108` now |

`DECISIONS.md` and `WALKTHROUGH.md` are additionally **read by**
`scripts/checks/dfu-done.ps1` / `verify-dfu-done.ps1`, so editing their prose is
not free; that is the second reason they are recorded rather than repaired here.

**Not repointed, by rule:** ~170 hits inside `documentation/evidence/*` and
`documentation/notes/*`. Those are dated records of what a worker or tester saw
on a given day. Renumbering them would make the record say something nobody
observed. Each is listed under its target in the sweep output; the ones whose
targets this item moved are `.env.example` (23), `stack.manifest.toml` (44),
`README.md`, `CLAUDE.md`, `scripts/stack/stack.py`, `test_stack.py`,
`check-project-configs.ps1`, `harness.config.json`, `memory/README.md`,
`coder/README.md`, `restore-from-snapshot.ps1`, `portal-on.ps1`, `ci.yml`.

## 9. What was NOT changed, and why

- **Compose files are line-count-neutral.** `stack.manifest.toml` cites plane
  compose files at 99 `path:line` anchors. Every guard reword and header edit in
  `frontend/`, `inference/` (+ its four included files), `memory/`, `search/`,
  `coder/`, `portal/` and the root `docker-compose.yml` was written to occupy
  the same number of lines, verified per file. **[measured]** all eight report
  `lines N -> N`.
- **`check-watchdog-repair-targets.ps1` and `stack-watchdog.ps1`'s repair-arg
  builder needed no change.** Both already read `env_file` from
  `scripts/lib/stack-services.json` behind an `if ($EnvFile)` / `if
  ($Proj.env_file)` guard, so the field going `null` for every project simply
  stops adding the flag. **[measured]** `check-watchdog-repair-targets.ps1`
  → `REPAIR TARGETS OK: 24 container(s)`.
- **`scripts/checks/check-env-file-scope.ps1` needed no change.** Its rule is
  "a grant is broad when the `env_file:` target resolves to a `.env` that is NOT
  beside the compose file naming it". Per-plane env files make that rule *more*
  natural, not less. **[measured]** `no new shared-.env grants staged`.
- **`agent-org/docker/.env` and `OB1/docker/.env`** — explicitly out of scope
  (anchor `out_of_scope`), and both already per-plane.
- **No variable's value or default changed.** Every moved line carries its
  original value and its original comment text.

## 10. One behavioural improvement that rode along, flagged deliberately

`scripts/backup/restore-from-snapshot.ps1`'s `lm-models` entry gained
`ComposeArgs = @('--profile','local')`. It previously passed `--env-file .env`
and no profile, which worked only because the root `.env` happened to carry
`COMPOSE_PROFILES=local,...`. After the split that value lives in
`inference/.env`; an operator who splits the profiles wrongly (see the runbook's
"single most dangerous mistake") would get a restore that names two
profile-gated upstreams, exits 1 on `stop`, and logs progress having stopped
nothing.

The `openwebui` and `tailscale` entries already pass their profiles explicitly
for exactly this reason and say so in a comment; this makes `lm-models` match.
It can only make the restore work in **more** cases, never fewer — but it is a
behaviour change in a disaster-recovery path, so it is named here rather than
buried in a diff.

## 11. Rebase onto `4934529` — what the second sweep found

The item was queued at `df3e603` (base `2f5c451`), `development` then took
`sl-colo-frontend` (`4934529`), and the branch was rebased before any tester
claimed it (D18). Re-recorded tip below.

**One conflict, `CLAUDE.md`'s plane table.** Their Frontend row gained a
build-inputs sentence; mine dropped `--env-file` from five rows and rewrote the
`COMPOSE_PROFILES` clause. Kept both. Everything else auto-merged, and each
auto-merge was **verified by reading the result**, not assumed:
`frontend/docker-compose.yml` carries `context: .` at `:159`/`:280` **and** both
reworded `WEBUI_SECRET_KEY` guards; `stack.manifest.toml` carries the
`frontend/entrypoint.sh` citations **and** no `env_file` key;
`stack-watchdog.ps1` joins `frontend\entrypoint.sh` at `:459` **and** passes no
`--env-file` (the two remaining mentions are the null-safe `$Proj.env_file`
branch and a prose line).

**[measured] The moved build inputs add no variable.** `frontend/.env.example`
needed no change: the two `build.context` values are the literal `.`, so the
two-way diff still reports `frontend refs=30 assigns=30`, identical to
pre-rebase.

**[measured] Compose line-count neutrality holds against the NEW base.**
`git diff --numstat development..HEAD -- '*.yml'` gives equal insertions and
deletions for all eleven compose files, and all twelve (including
`portal/local-test.override.yml`) report the same line count as `development`'s
version. `sl-colo-frontend` was itself neutral on `frontend/docker-compose.yml`
(484 -> 484), so the two items' neutrality composes.

**[measured] Citation anchors re-derived against the new base, by construct.**
Rather than sampling, every `<compose file>:NN` citation in every tracked file
outside `OB1/` — **both** the full-path and the bare-basename form, 212
references — was checked by comparing `git show development:<file>` line N
against `HEAD`'s line N. `stack.manifest.toml` alone accounts for 37.

**Zero moved.** Five differ, and all five are the *same line number* carrying my
own reword, not a displaced anchor:

| citing file | anchor | what changed on that line |
|---|---|---|
| `documentation/notes/stack-layers-sl-closeout-findings.md:282` | `inference/compose/backups.yml:41` | `.env` -> `inference/.env` in the disable comment |
| `documentation/notes/stack-layers-sl-driver-parity-findings.md:74` | `docker-compose.yml:11` | the anchor header's env sentence |
| `documentation/notes/stack-layers-sl-manifest-findings.md:134` | `docker-compose.yml:10` | same header |
| `inference/.env.example:104` | `inference/compose/queue.yml:50` | my own file citing my own reword; still the admission-sizing comment |
| `inference/.env.example:163` | `inference/compose/backups.yml:41` | same |

The three in `documentation/notes/` are dated records quoting text that has since
been reworded on the same line. By the rule in §8 they are left alone: the
pointer still lands on the right subject, and editing a note to quote text its
author never saw is the failure the rule exists to prevent. The two in
`inference/.env.example` are mine and are accurate as they stand.

**[measured] The whole battery was re-run on the rebased tip**, not carried over:
11 profile renders + the anchor (all exit 0, no warnings), the two-way diff
(byte-identical to pre-rebase), `ruff` clean, 107 tests, `inventory --check`
`[OK]`, `up --all --dry-run` with zero `--env-file`,
`check-watchdog-repair-targets.ps1` 24/24, and — in a short-path clone with the
whole 59-file delta staged on top of `4934529` — `check-project-configs.ps1`
green at 9 projects, the routing check, the env-file-scope check, doc-placement
and line-endings.

**[measured] And the compose check was proved non-vacuous on the new base**:
blanking `MCP_API_KEY` in `memory/.env.example` alone turned it RED with
`COMPOSE INVALID: memory - … required variable MCP_API_KEY is missing a value:
set in memory/.env (copy memory/.env.example)` — which also demonstrates that
the check renders each plane against **that plane's own example** and that the
reworded guard text is what a failure now tells you.
