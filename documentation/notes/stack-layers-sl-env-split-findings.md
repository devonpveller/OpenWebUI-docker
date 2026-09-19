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

## 5. Two `.bat` files address the anchor project - broken since Part K, unrelated to this item

**CORRECTED 2026-09-19 after the tester (F4) showed the first version attached
one file's numbers to another.** The original text said "`update-stack.bat`
runs 28 bare commands … this item removed the `--env-file .env` from that
`.bat`'s 25 flag-carrying lines", which cannot be true of one file: 28 bare
lines and 25 flag-carrying ones are two different files. Re-measured, per file:

| file | compose invocations | of those BARE (no `-f`) | `--env-file` at base `4934529` | touched by this item |
|---|---:|---:|---:|---|
| `scripts/recovery/update-stack.bat` | 18 | **18** | **0** | **no** - not in the diff at all |
| `scripts/recovery/quick-fixes.bat` | 39 | **11** | **25** | yes - the 25 removals were here |

(counted as lines matching `^\s*@?docker compose `, so `echo`d advice does not
inflate them.)

**[read]** A bare `docker compose` from the repo root addresses the **anchor**
project, which has owned **zero services** since K.5b (2026-08-21), so every
one of those 29 lines fails with `no such service` - `update-stack.bat:237`
(`up -d openwebui`), `:183` (`build --no-cache openwebui`), `:80` (`ps
llama-cpp-upstream …`); in `quick-fixes.bat` the eleven are `down`, `up -d`,
`ps`, and `start` for tailscale / mnemory / surrealdb / open_notebook / the two
upstreams. `scripts/checks/dev-helper.ps1:63` (`docker compose config`) and
`:102` (`build --no-cache tailscale`) have the same shape.

**[pre-existing]**, caused by Part K, not by the env split. The deferral stands
- guessing which plane each of 29 lines meant is a service-lifecycle repair
with its own blast radius and does not belong in a different item. What was NOT
honest in the first version, and is recorded now: this item edited
`quick-fixes.bat` twenty-five times and left eleven known-broken calls in it
unmentioned. Editing a file that often without saying what is still wrong with
it is how a defect gets a fresh commit date and no owner. Worth its own item,
and the numbers above are what it should start from.

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

- **Compose files are line-count-neutral - WITH TWO EXCEPTIONS, both added
  later and both disclosed.** Every guard reword and header edit in `frontend/`,
  `inference/` (+ its four included files), `memory/`, `search/` and `coder/`
  was written to occupy the same number of lines, verified per file: nine files,
  `lines N -> N`. The two that GREW did so in attempt 2 and attempt 3, for the
  same reason each time - a claim that needed stating where the reader is:
  `portal/docker-compose.yml` **616 -> 641** (the guard's header block) and the
  root `docker-compose.yml` **54 -> 62** (the per-plane env paragraph, then the
  three-mechanism refusal table that replaced the false claim in section 13).
  Every citation into both was re-derived by construct - see section 20.
  **[measured]** This bullet said "all eight report `lines N -> N`" through
  attempt 2, by which time it was already wrong for portal, disclosed one
  section away. The tester found the anchor half (X-F9). Bookkeeping that
  contradicts a disclosure in the same document is worth as little as no
  disclosure.
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

---

# Attempt 2 (2026-09-19) — what the tester found, and what changed

Attempt 1 was tested at `e777d11` by `wt-tester-env` and FAILED Case 12 (11/12
pass), with the plan marked inadequate. Their evidence is the authority; this
section records what I did about it and what I measured myself.

## 12. THE REGRESSION: the portal plane did not fail loud, and four sentences said it did

**This is the one that mattered, and I missed it because I checked that the
planes I had guarded still refused, instead of checking that every plane
refused.** The `:?` guards were pre-existing on five planes; the portal never had
one, because it never needed one: all three of its drivers passed
`--env-file <repo root>/.env`, and **compose hard-refuses a NAMED env file that
is absent** (`couldn't find env file: …`, exit 1). Removing the flag removed that
refusal, and I put nothing in its place — on the internet-exposed plane.

**[measured] The regression, in the shape the tester found it:** with
`portal/.env` absent,
`docker compose -f portal/docker-compose.yml --profile internet config` exited
**0**, blanking `PUBLIC_DOMAIN`, `ACME_EMAIL`, all three Authelia secrets,
`CLOUDFLARE_TUNNEL_TOKEN`, `WORKBENCH_KEY` and the digest addresses. Nine
unguarded, defaultless `${VAR}` consumers; `portal-on.ps1` would then have
started caddy, authelia and cloudflared on those blanks.

### What went in

**A `${AUTHELIA_JWT_SECRET:?}` guard** on the authelia service, naming
`portal/.env`. One guard, matching what every other plane carries, because the
thing that regressed is the ABSENT FILE and one guard restores exactly that.
Among the portal's five required keys it is the one whose blankness is an
auth-correctness event rather than an outage: a blank `CLOUDFLARE_TUNNEL_TOKEN`
fails **safe** (nothing reaches the internet at all), a blank identity-signing
secret does not. The argument is written into the compose header so the next
person can disagree with it on the merits.

**A pre-flight in `portal-on.ps1`**, because a guard answers "no file" and not
"file with a blank line in it", and the portal is the plane where the difference
is a security event. It refuses before any docker call if `portal/.env` is
missing, or if any key in `stack.manifest.toml`'s `[planes.portal] keys` is
blank. The key list is **parsed out of the manifest** rather than copied, so
adding a key there arms it here; if that parse fails it says so in yellow and
falls back to a built-in list, because a check that silently checks nothing is
this repo's recurring defect and I am not adding another. It is deliberately not
routed through `stack.py doctor`: the portal is a `manual` plane, absent from
the driver's enabled set, so `doctor` never reaches it — which is precisely why
the operator's own entrypoint is where this belongs.

**[measured] Refuse / pass proof, all six directions:**

| case | result |
|---|---|
| `portal/.env` absent, `config` (no profile) | exit **1** |
| `portal/.env` absent, `config --profile internet` | exit **1**, `required variable AUTHELIA_JWT_SECRET is missing a value: set in portal/.env (copy portal/.env.example) …` |
| `--env-file portal/.env.example --profile internet config -q` | exit **0**, stderr EMPTY |
| `portal/.env` present (copied from the example), native load | exit **0** |
| `portal-on.ps1`, file absent | exit **1**, `REFUSED: portal/.env not found`, no docker call reached |
| `portal-on.ps1`, file present with three keys blank | exit **1**, `REFUSED: portal/.env is missing a value for: CLOUDFLARE_TUNNEL_TOKEN, AUTHELIA_SESSION_SECRET, AUTHELIA_STORAGE_ENCRYPTION_KEY` |

81 containers running before and after.

`portal/.env.example` gives `AUTHELIA_JWT_SECRET` a non-empty PLACEHOLDER for the
same reason `frontend`'s `WEBUI_SECRET_KEY` and `search`'s
`MULLVAD_WG_PRIVATE_KEY` have one: the committed example is what the pre-commit
compose check and CI render against, and an empty value trips the guard. The
file says so, and says why the other two Authelia secrets stay blank.

### The four sentences, corrected

Each was true of five planes and stated of all of them.

| where | was | now |
|---|---|---|
| `docker-compose.yml:11` | "each carries a `${VAR:?}` guard" | names the **six**, says the portal's is new and why, and states that OB1 and agent-org carry none |
| `README.md` plane paragraph | "the plane files fail loud without it" | "each of those **six** carries a `${VAR:?}` guard … `OB1/docker/` and `agent-org/docker/` … have no such guard and render with blanks" |
| `env-split-migration.md` step 2 | "a file you forget to fill fails loud on its `:?` guard" | names the six guarded variables, one per plane, then states the asymmetry plainly: forget one of those six and the plane refuses; forget any other and you get a blank string and a stderr warning — which is why step 3 demands an EMPTY stderr and not just exit 0 |
| `env-split-migration.md` Rollback | "There is no partial state in which a plane silently runs on the wrong value" | deleting the six is safe **because** all six now refuse — with the portal's history stated — and the case that is genuinely NOT covered (a file that exists and is incomplete) is named, with the two mechanisms that do cover it |

## 13. ~~"Every plane refuses" is false for agent-org and OB1~~ — WRONG, AND CORRECTED IN ATTEMPT 3

**This section shipped a `measured`-tagged claim that measurement contradicts,
in the commit whose whole purpose was to fix a `measured`-tagged claim that
measurement contradicted.** The tester caught it (attempt 2, X-F12). Kept here
rather than overwritten, because what went wrong is more useful than the
conclusion.

**What it said:** agent-org and OB1 carry no guard and "render exit 0 with
blanks, measured 2026-09-19".

**What is true. [measured 2026-09-19, attempt 3, reading the PROCESS exit code]**
Both refuse, by different mechanisms:

| plane | mechanism | exit | message |
|---|---|---|---|
| `agent-org/docker/` | a service-level `env_file: .env` on agent-bridge (`:109-110`) which compose stats | **1** | `env file ...gent-org\docker\.env not found: GetFileAttributesEx ...` |
| `OB1/docker/` | its own `:?` guard at `OB1/docker/docker-compose.yml:252` | **1** | `required variable OPS_GATEWAY_KEY is missing a value: openbrain-ops-gateway needs its OWN key, never the cloud one` |

So **all eight planes refuse** an absent env file, by three mechanisms: a
`${VAR:?}` guard (the six in-repo), a service-level `env_file:` (agent-org),
and OB1's own guard.

**HOW I GOT IT WRONG, because the mechanism matters more than the fact.** I ran

```
mv agent-org/docker/.env /tmp/ && docker compose -f ... config -q 2>&1 | head -2; echo "exit=$?"
```

and `$?` after a pipeline is **`head`'s** exit status, not compose's. `head`
succeeds whatever compose does. The warnings I saw and quoted were real; the
`exit=0` beside them was `head`. It is the same defect the tester found in the
plan's stderr idiom on the same day (X-F13) - I wrote a measurement harness
that could only return the answer I expected, twice, in different languages.
Every exit code in attempt 3 is read from the process: `cmd /c "... 2>file"`
then `$LASTEXITCODE`, or a bare invocation then `$?` with nothing after it.

**The error direction was safe** - it understated how protected those two
planes are, so nobody would act dangerously on it - which is why the tester
graded it class 2. That is luck, not design.

**A second thing the bad measurement nearly cost.** On the strength of it I also
wrote that `scripts/stack/stack.py`'s `render_project` comment - which asserts
agent-org's service-level `env_file:` makes the render exit 1 when that file is
absent - was STALE, and recorded it as a note for whoever next touched that code
path. It is not stale. It is exactly right, and the corrected measurement above
is the proof. That claim is withdrawn; the comment stands unedited, which is
also the outcome of having left it alone.

Both planes remain out of scope by the anchor - nothing about either was
changed. What changed is the prose describing them, in
`docker-compose.yml`'s header, `README.md`'s plane paragraph and the plan's
Case 1b, all three of which now say all eight refuse and name the mechanism.

## 14. The agent-org wildcard that reaches into the root `.env` — a cross-plane consequence I had missed

Found by my own sweep while checking §13, not by the tester.

**[measured]** `agent-org/docker/docker-compose.yml:300` and `:398` give
`ao-worker-1` / `ao-worker-2` **`env_file: ../../.env`** — the whole ROOT file.
Of the names the pre-split root `.env.example` carried, **151 reach those
containers that way and are not overridden by the services' own `environment:`
block** (which sets ten).

**ATTEMPT 2 NAMED ONE. THERE ARE TWO.** The tester found the second (X-F10),
and finding it exposed that I had asserted "the one that matters" without doing
the enumeration that sentence implies. Attempt 3 does it: intersect the 151 with
every variable the `little-coder:local` image reads CONTAINER-side - the direct
`os.environ` reads under `little-coder/src/littlecoder/**` and
`little-coder/pi-extension/**`, **plus the two INDIRECT ones**, where the config
names the variable rather than the code (`config.py:38 api_key_env`, `:91
open_terminal_key_env`, both read at `meta_wiring.py:42,48`). An enumeration
that missed the indirections would have missed the second one too - which is
how it was missed.

| name | how the image reads it | after step 5 |
|---|---|---|
| **`LC_DEPLOY_TOKEN`** | the clone path, for private work repos | **BREAKS** - public repos still clone, private ones fail |
| **`LC_LLAMA_API_KEY`** | INDIRECT: `config.py:38 api_key_env` -> `meta_wiring.py:42,48` `os.environ.get(..., "")`, for BOTH the embedder and the chat client | **BREAKS** - an empty bearer to `llama-cpp`, which LiteLLM rejects **401** on every call since the J.1 virtual-key flip |
| `LITTLE_CODER_VERSION` | **build ARG only** - `little-coder/docker/Dockerfile.agent:36-37`; the workers RUN the prebuilt image | no effect. Ruled out by reading the Dockerfile, not by assuming |

`LC_LLAMA_API_KEY` is the one worth understanding, because the workers LOOK
covered: they set `LLAMACPP_API_KEY=${LC_LLAMA_API_KEY}`. That is a HOST-side
interpolation out of `agent-org/docker/.env` injecting a DIFFERENTLY-NAMED
container variable. The name little-coder actually reads is
`LC_LLAMA_API_KEY`, and the wildcard is its only route in. (The workers' configs
come from `agent-org/scripts/gen-worker-configs.py`, which copies
`little-coder/config/little-coder.config.yaml` verbatim except
`workspace.open_terminal_url`, so `api_key_env: LC_LLAMA_API_KEY` at `:12` is
what they run with.) `agent-org/docker/.env.example` carries neither name.

Nothing breaks immediately — a running container keeps the environment it
started with — but on the workers' next recreate one clones with no deploy
token and both talk to the gateway with an empty bearer. Same silent class as
the 2026-08 `ao-worker stale deploy token` incident, arriving from the other
direction, and it would have been mine.

**Handled in the runbook, not in agent-org's compose:** a blocking step **4b**
before the trim, naming the two services, the enumeration, BOTH variables (and
the third that the enumeration surfaces and rules out), and the recreate. Editing another plane's compose to name its variables is the
real fix and is out of scope here — it is precisely what
`scripts/checks/check-env-file-scope.ps1` exists to prevent, and those two grants
are grandfathered past it.

**OPEN, follow-up in agent-org:** replace `env_file: ../../.env` on
`ao-worker-1`/`-2` with the named variables they actually need, so the check can
stop grandfathering them.

## 15. F1 — five live operator messages still quoted the retired global value

All five were in files this item edited, which is the uncomfortable part: I
reworded six sentences in `stack-watchdog.ps1` and missed a seventh in the same
file.

| site | was | now |
|---|---|---|
| `scripts/recovery/emergency-recovery.ps1:196` | "put the frontend's profiles into COMPOSE_PROFILES in .env - this host's FULL value is `local,gpu,tailscale` … (one authoritative section at the top of .env.example)" | "put `COMPOSE_PROFILES=gpu,tailscale` in `frontend\.env` — PER-PLANE since sl-env-split, so that is now the WHOLE correct value there … editing the ROOT .env changes nothing for this plane", plus a line for the not-yet-migrated case pointing at the runbook |
| `scripts/checks/stack-watchdog.ps1:306` | the same sentence, in the WARN | the same per-plane rewrite |
| `scripts/stack/stack.py:1228` | fail-open reason ending "(this host: local,gpu,tailscale)", naming no file | names `frontend/.env`, gives the per-plane value, and points at the runbook if the file is absent |
| `inference/compose/upstreams.yml:39` | "`local` is one name in the GLOBAL COMPOSE_PROFILES value in .env … naming `local` alone there would drop the frontend's profiles" | "`local` on its own is the whole correct value in `inference/.env`" — the old advice is now exactly backwards |
| `inference/compose/upstreams.yml:129` | same | same |

`:196` was the worst of them: it is the recovery path's guidance, it fires only
when the frontend is already broken, and after the split it actively misdirected.

**[measured]** `git grep "local,gpu,tailscale"` outside archive/evidence/notes
now returns five hits, all in the runbook and the stack-map reference, all
describing the OLD value the operator is splitting — the one context where
naming it is correct.

## 16. F2 — the merge-to-migration window is now stated, loudly

Between this landing on `development` (the live-hosted line) and the operator
running steps 1-2, `<plane>/.env` does not exist. The runbook now opens with a
blockquote table: `new-worktree.ps1` provisions worktrees that render 5 of 6
planes as errors (warning in yellow, naming each), `restore-from-snapshot.ps1`
cannot stop or start a frontend/inference/memory/coder service,
`emergency-recovery.ps1` cannot start a plane, and `portal-on.ps1` refuses. All
loud, no data loss, and **every one of them is a tool you would reach for during
an incident** — which is the argument for doing steps 1-4 at merge time rather
than "soon". Step 7's "the restart can wait" is true of the running containers
and was being read as true of the tooling; it now says which.

## 17. F3-F7 — the small ones

* **F3** `scripts/sysadmin-mcp/register-sysadmin-telegram.ps1:34` and
  `telegram_notify.py:31` read the root `.env` for
  `SYSADMIN_TELEGRAM_BOT_TOKEN` / `_CHAT_ID`. Host tooling, never templated in
  `.env.example`, so no behaviour change and no runbook row — but they were
  missing from the sweep's "NOT affected" list, which was presented as
  exhaustive. Added to it, in the plan, with that provenance.
* **F5** `little-coder/README.md:53-57` told the operator to set
  `LC_SELF_REMOTE_URL` / `_PAT` "in `.env`". Repointed to `coder/.env`, and it
  now also states the fact only `coder/.env.example` carried: **nothing reads
  either name anywhere in the repo today**.
* **F6** `search/.env.example` said the seven NOT-WIRED tunables were "shipped
  COMMENTED OUT" and told the reader to "uncomment" one — of lines that are
  prose, not `#NAME=value`. Substance was right, three sentences described a
  shape the file does not have. Reworded to say they are prose **deliberately**,
  because a commented-out assignment invites an uncomment that would change
  nothing while looking like it had; the seven runbook table rows now say "prose
  only" rather than "commented out".
* **F7** `breach-killswitch.ps1` writes `portal/.env` with
  `Set-Content -Encoding utf8`, which emits a BOM on PS 5.1, and compose now
  parses that file natively on every portal command. **[pre-existing]** — the
  same code wrote the root `.env` before this item — but the blast radius
  changed, because a BOM on the first line can make the first variable parse as
  a name with a leading zero-width character. Not fixed here: it is a one-line
  encoding change inside an incident-response script and deserves a test proving
  the rotated file still parses, not a drive-by. **OPEN.**

## 18. What the tester was right about that I have NOT changed

`--profile` REPLACES `COMPOSE_PROFILES` rather than unioning with it, so the
`--profile local` added to the `lm-models` restore entry (§10) narrows that one
invocation's render to `local` alone. Harmless for the two services that entry
names, and the entry is strictly better off with the flag — but the tester is
right that "can only make the restore work in more cases" is a statement about
**that entry**, not about the mechanism. §10 is left as written with this
correction recorded beside it rather than softened in place: its job was to flag
the change for judgement, and the judgement has now been made.

## 19. Plan repairs (the `-PlanInadequate` half)

* **Case 1b's snippet was a no-op on PS 5.1.** `Rename-Item <path> <path>`
  throws (`-NewName` takes a leaf), so the file never moved, the render read the
  still-present `.env`, and the case reported exit 0 for every plane — it passed
  while checking nothing, which is this repo's signature defect and I wrote one
  into a test plan. Rewritten with `Move-Item`, a stash outside the repo,
  restore-before-report, **portal added as a sixth case**, and an assertion that
  the message names both the variable and the plane file. The warning is left in
  the plan rather than quietly fixed.
* **Case 1b now also covers the two halves of the portal fix** (compose guard,
  `portal-on.ps1` pre-flight) and tells the tester to confirm no docker call is
  reached, plus the agent-org / OB1 scoping measurement.
* **Case 12** now enumerates **seven guard lines across six planes** in a table —
  plane, guarded variable, and WHY that variable — instead of asserting "five
  guards", and invites the tester to disagree with the portal's single-guard
  design on the merits.
* **Case 7** now states what a tester sees on a host that has **not** migrated
  (the expected state while this is in review): five or six renders refusing is
  a PASS provided `new-worktree.ps1` warned by name, and a silent provisioning
  is the FAIL. It says to record which host state was seen rather than reporting
  green renders nobody saw.
* **Case 2's** coder expectation said "no hits outside `coder/.env.example`",
  which is false: there are three prose hits. Corrected to name all three and to
  say the FAIL condition is anything that *executes*.
* **Case 5** gains the `portal-on.ps1` pre-flight as row 3b, and the two
  `sysadmin-mcp` readers in the "swept and NOT affected" list.

## 20. Citation sweep, attempt 2 — and a basename false-positive class worth naming

Re-derived by construct against `development` (4934529) after every other edit,
both the full-path and the bare-basename form, across every tracked file outside
`OB1/`. `portal/docker-compose.yml` gained 25 lines and `scripts/portal/portal-on.ps1`
gained 66, so this pass had real work to do.

**[measured] Two LIVE citations repointed, and both were ALREADY WRONG at base:**

| citing | was | base reality | now |
|---|---|---|---|
| `stack.manifest.toml:485` | `portal/docker-compose.yml:603-605` for "app-net, external: true, name: ai-stack_app-net" | at base, `:603` is `notify-net:` — the app-net block was `:607-609`, four lines further down | `:632-634` (verified by reading the block) |
| `stack.manifest.toml:490` | `portal/docker-compose.yml:601-602` for the caddy/app-net comment | at base, that comment was `:605-606` | `:630-631` |

Both were off by four BEFORE this item touched the file, so shifting them by +25
would have produced a number that was newly wrong in a different way. They were
re-derived from the constructs they name, not from arithmetic, and the manifest
now says so in place.

**A false-positive class this sweep must guard against, named because it bit the
first pass of it:** the root file's path, `docker-compose.yml`, is a SUFFIX of
every plane's path, so an unanchored pattern reports
`memory/docker-compose.yml:36`, `search/docker-compose.yml:25-28`,
`coder/docker-compose.yml:28` and `OB1/docker/docker-compose.yml:15-16` as
citations into the ANCHOR file. Four of the six manifest hits in the first run
were that. The fix is a `(?<![\w/.-])` look-behind; with it the live set drops
from 58 hits to 19. The same class produced attempt 1's one false positive
(`owui/README.md:63` matching `README.md:63`). **A basename sweep is a list to
read, not a list to apply** — which is also why the plan's Case 11 says so.

**[measured] Everything else: 15 flagged, all judged, none needing action.**
Five are this item's own repoints (three from attempt 1 — `andon.ps1:431`,
`drill-dark-factory.ps1:365`, `queue.ps1:939` — plus the two manifest ones
above); three are attempt 1's `.gitignore` repoints, re-verified against the
current file (`:11` the backup-rules comment, `:54` `!backup/generic-tar-backup.sh`,
`:34` `.mcp.json`); and seven were already stale before this item and stay
recorded rather than silently renumbered — `CLEANUP-PLAN.md:266`, `:330`
(claims an `openwebui` bind mount the anchor compose has not had since Part K),
`:358`, `:438`, `:893`, `DECISIONS.md:1705` (`prove-clone-recursive` is in no
version of `ci.yml`), `WALKTHROUGH.md:384`.

**Compose line-count neutrality holds everywhere except `portal/` and the root
anchor** - the two files a claim had to be written into. Verified per file
against `development`; the `stack.manifest.toml` citations into the other nine
compose files are byte-identical at their cited lines.

**Attempt 3 re-ran the whole sweep after its own edits** (the anchor grew again,
58 -> 62): **19 live citations into changed files, 15 flagged, 0 new work** -
the same 15, each already judged above. Five are this item's own repoints, three
are attempt 1's `.gitignore` repoints re-verified against the current file, and
seven were stale before this item and stay recorded. `CLEANUP-PLAN.md:330` now
lands on the anchor's new refusal table, which changes nothing: it cites an
`openwebui` bind mount the anchor has not had since Part K, so there is no
correct line to point it at.

---

# Attempt 3 (2026-09-19) — three defects the FIX commit introduced

Attempt 2 (`e958187`) passed all twelve cases: the attempt-1 regression is gone,
and the tester independently agreed with the one-guard-plus-pre-flight design
and proved the key list is manifest-driven. It still failed, on three defects
**the fix commit itself shipped**. That is the shape worth naming: each of the
three is a *repair* that was not checked as hard as the thing it repaired.

Sections 13 and 14 above are rewritten in place rather than appended to, because
leaving a wrong `measured` claim standing with a correction underneath is how a
reader ends up quoting the wrong half.

## 21. X-F8 — the F1 repair shipped a path that does not exist, twice

Both messages rewritten in attempt 2 to fix attempt 1's stale
`COMPOSE_PROFILES` advice print:

```
documentationunbooks\env-split-migration.md
```

**[measured]** `od -c` confirms the byte is absent, not mis-rendered:
`d o c u m e n t a t i o n u n b o o k s`. The `\r` of `\runbooks` was consumed
as a carriage return.

**Cause, exactly.** I applied both edits through a Python helper whose
replacement strings were ordinary (non-raw) literals containing
`documentation\runbooks\env-split-migration.md`. Python turned `\r` into CR.
The other escapes in the same strings (`\c`, `\.`, `\[`) are *invalid* and
Python emitted a `SyntaxWarning` for each — which I saw and ignored as noise,
because the edits applied and the asserts passed. `\r` is a **valid** escape, so
it produced no warning at all: the one that silently did damage was the one that
looked clean. Every warning in that output was a signal that the string was not
being read literally.

**[measured] Swept the whole delta for the class**, not just the two known
sites: for every file this item touches, strip legitimate CRLF and count what
remains. Three hits, all mine, all fixed:

| file | what | fix |
|---|---|---|
| `scripts/recovery/emergency-recovery.ps1:196` | CR inside the runbook path | byte-exact replace of CR+`unbooks` with `\runbooks` |
| `scripts/checks/stack-watchdog.ps1:306` | same | same |
| `scripts/portal/portal-on.ps1:78` | a real CR **and** a real LF inside the pre-flight's single-quoted regex (X-F14) | `\r?\n` as escapes |

The sweep now reports zero stray CRs across all changed files, and the
repo-convention line the tester cited as intact
(`stack-watchdog.ps1:170`, `documentation\runbooks\SERVICE-LIFECYCLE.md`) is
untouched. All byte-level edits in attempt 3 are built from `bytes([...])` or
verified by hex dump, not from escape-bearing literals.

Worth stating plainly: the content of both rewrites was correct and the tester
checked it line by line. What shipped broken was the **remediation pointer**, in
an error an operator reads during a frontend outage. A repair that gets the
diagnosis right and the "here is what to do" wrong is not a repair.

## 22. X-F14 — the pre-flight regex carried literal CR and LF

Same root cause, different blast radius:

```
'(?s)\[planes\.portal\](.*?)(?:<CR>?<LF>\[)'
```

It works today — the tester verified the pre-flight still parses five keys and
that the yellow fallback line does **not** appear — because CR-?-LF matches what
a CRLF file contains. But if `stack.manifest.toml` is ever normalised to LF, the
CR vanishes and the pattern becomes `(?:?\n\[)`: `Quantifier ... following
nothing`, an `ArgumentException` thrown **outside any try**, in an
incident-adjacent script. `.gitattributes` pins `eol=lf` for `*.sh` only, so
nothing forces it today. Now `\r?\n`, which costs nothing and cannot rot.

**[measured]** After the fix the pre-flight still refuses correctly and still
names all three blank keys from the manifest — so the regex is doing its job,
not merely parsing.

## 23. X-F12 — see section 13

Rewritten in place above, including how the bad measurement was produced
(`$?` after a pipeline is `head`'s), and the withdrawal of the second claim it
had propped up (that `stack.py`'s `render_project` comment was stale — it is
correct).

## 24. X-F10 — see section 14

Rewritten in place above with the full enumeration: the 151 wildcard-delivered
names intersected against everything the image reads container-side **including
the two config-named indirections**, yielding two live breakages
(`LC_DEPLOY_TOKEN`, `LC_LLAMA_API_KEY`) and one ruled out with a reason
(`LITTLE_CODER_VERSION`, a build ARG). Step 4b checks both names.

## 25. The class-3 items, and the one I am NOT fixing

* **X-F9** — the anchor `docker-compose.yml` grew (54 -> 58 in attempt 2, -> 62
  in attempt 3) while findings §9 still said all eight compose files were
  line-count-neutral and the plan's Case 11 kept the anchor in the loop whose
  PASS criterion is "every line OK". §9 now states both exceptions and why each
  grew; Case 11 checks the two growing files separately, by construct. No live
  citation into the anchor broke — the only one is `CLEANUP-PLAN.md:330`, stale
  since Part K for an unrelated reason.
* **X-F11** — the runbook's window table said `new-worktree.ps1` leaves "5 of 6
  planes" failing to render. The portal guard made it **6 of 6**. Written
  against attempt 1's behaviour and not re-read after the change that
  invalidated it. Now 6 of 6, with the reason it moved.
* **X-F13** — the plan's own stderr idiom. See section 26.
* **X-F15** — the pre-flight guards `portal-on.ps1` and nothing else: a
  `portal/.env` that EXISTS with a blank `AUTHELIA_SESSION_SECRET` still renders
  for `portal-off.ps1`, `restore-from-snapshot.ps1`'s caddy/authelia entries and
  a hand-typed compose command. The tester recorded it as narrower-than-regressed
  and did not hold it against the item; I have not widened it, because a
  plane-wide blank-key gate belongs in the driver and the driver deliberately
  does not reach a `manual` plane. What I HAVE done is say so in the script, so
  nobody reads the pre-flight as a plane-wide guarantee.
* **X-F7** (BOM, from attempt 2) remains **OPEN** and unaddressed, correctly.

## 26. X-F13 — the plan told the tester to measure with an instrument that reads nothing

`$err = & docker compose ... 2>&1 1>$null` on PS 5.1 merges stderr into the
success stream and then discards it. **[measured] on the same failing render:**

| idiom | chars captured | exit |
|---|---:|---|
| `2>&1 1>$null` (what the plan said) | **0** | 1 |
| `2>&1 \| Out-String` | 553 | 1 (but includes stdout - useless with `config`) |
| `cmd /c "... config -q 2>file 1>nul"` | **173** | 1 |

Consequences in the plan as shipped: Case 1's PASS criterion "stderr empty" was
satisfied **unconditionally** and could not detect the `variable is not set`
warning it exists to detect; Case 1b's `names-var-and-file=True` was
**unreachable** and prints `False` for all six correct planes. The tester
re-ran both with a working capture and the underlying behaviour passed — so the
item was fine and the instrument was not.

Both cases now use one helper built on the third idiom, and Case 1 opens by
making the tester **prove the capture on all three outcomes first** — a clean
render (exit 0, 0 chars), a warning-only render (exit 0, >0 chars: drop
`MULLVAD_WG_ADDRESSES` from the search example), and a refusal (exit 1, >0
chars, naming the variable and the file) — with the instruction to stop and say
so rather than record a PASS if the capture reports 0 chars for the last two.

**[measured] all three, exactly as written into the plan:** `exit=0 chars=0`,
`exit=0 chars=133` (`The "MULLVAD_WG_ADDRESSES" variable is not set`), and
`exit=1 chars=173` naming `MCP_API_KEY` and `memory/.env`.

This is the same failure as section 13's `$? == head's exit`, in PowerShell
instead of bash, written on the same day. Two measurement harnesses that could
only return the answer I expected. The lesson the repo already had - a check
that passes while checking nothing - applies to the instruments in a test plan
exactly as it applies to the checks in `scripts/checks/`.
