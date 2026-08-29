# coder plane - findings

Things that are true about the `coder` compose plane but do not belong in
`coder/README.md`. They were established while reworking that README
(2026-08-28) into an operator document; the README now tells you how to run the
plane, and this file holds the discrepancies and defects that surfaced on the
way.

**Nothing here has been fixed.** Each entry says where the wrong claim lives so
the fix is a lookup, not another investigation. Where a wrong claim is repeated
in several files, fixing one and leaving the others is how these survive.

**How to read the evidence column.** "Verified 2026-08-28" means it was checked
against the tree in this pass. "Carried forward" means the location was named by
the previous documentation pass and not re-checked here - the *fact* is verified,
the *list of places repeating it* may be incomplete. Grep the quoted phrase
rather than trusting a line number.

---

## 1. Documentation that disagrees with the compose file

`coder/docker-compose.yml` is the source of truth for all of these.

### 1.1 "the 7 coder volumes" - there are six

- **Reality:** six named volumes, `coder_little-coder-{journals,skill,cohorts,polyglot,sessions,workspace}`.
- **Evidence:** the `volumes:` block of `coder/docker-compose.yml` declares six.
  Verified 2026-08-28.
- **Where the wrong number lives:** `CLAUDE.md` (the Coder row, "owns `lc-net` +
  the 7 coder volumes"); `CLEANUP-PLAN.md`'s Part K.4 block ("7 volumes copied to
  `coder_little-coder-*`"). The K.4 block is the **ancestor** - the other
  restatements are downstream of it, so fix it first or they grow back.

### 1.2 "expertise x5 + sessions + workspace" - there are four expertise volumes

- **Reality:** four expertise volumes (journals, skill, cohorts, polyglot), plus
  sessions and workspace, which are different in kind.
- **Evidence:** design doc §3.6 states "the first four are the **expertise
  volumes**" and separates sessions (per-session-id state) and workspace
  (project-scoped, re-clonable). Verified 2026-08-28.
- **Where the wrong number lives:** `.claude/skills/stack-map/references/workspace-stacks.md`;
  `CLEANUP-PLAN.md` K.4 (same sentence as 1.1, so one edit fixes both); and
  `coder/docker-compose.yml`'s own `volumes:` comment, "The five expertise
  volumes + per-chat sessions + the shared workspace".

### 1.3 The backup "covers the four expertise volumes" - it tars five volumes

- **Reality:** `little-coder-backup` mounts **five** volumes read-only under
  `/data` (journals, skill, cohorts, polyglot, **sessions**) and runs
  `tar czf ... -C /data .`, so the archive contains all five. Four of those five
  are expertise volumes; `sessions` is the fifth and is not one.
- **Evidence:** the service's `volumes:` list in `coder/docker-compose.yml` and
  the `tar` line in `backup/little-coder-backup.sh`. The mounts decide what is in
  the tarball. Verified 2026-08-28.
- **Where the wrong count lives, in both directions:** the
  `little-coder-backup` comment in `coder/docker-compose.yml` and the header of
  `backup/little-coder-backup.sh` - **the script that actually does the
  tarring** - both undercount the volumes ("the four expertise volumes"), while
  `documentation/runbooks/backup-restore-runbook.md` overcounts the expertise
  ones ("volume tar (5 expertise vols)"). Carried forward for the runbook.

### 1.4 `little-coder-backup` networks listed as "-"

- **Reality:** it declares no `networks:` key, so compose attaches it to the
  project's default bridge, `coder_default` - which is the internet-capable one.
- **Evidence:** the service has no `networks:` stanza in
  `coder/docker-compose.yml`. Verified 2026-08-28.
- **Where:** the stack-map reference table.

### 1.5 "agent-org reaches this plane's daemon" - only OWUI does

- **Reality:** agent-org consumes this plane's **images**, never its daemon. It
  runs its own pooled worker pairs.
- **Evidence:** `grep -rn "little-coder:8090\|LC_DAEMON_URL\|daemon_url\|http://little-coder" agent-org/`
  returns nothing. Meanwhile `agent-org/docker/docker-compose.yml` runs
  `image: little-coder:local` (no build stanza) for its workers,
  `${AO_OT1_IMAGE:-little-coder-open-terminal:local}` for their executors, and
  **builds** `little-coder-egress:local` from `../../little-coder` in two of its
  own services. Verified 2026-08-28.
- **Why the direction matters:** the image coupling runs **both ways**. A
  `--build` in agent-org silently changes what this plane's `lc-egress` runs on
  its next recreate, including the egress allowlist that is this plane's
  blast-radius boundary. Neither project pins a digest and neither warns about
  the other. (The README states the coupling as a rebuild instruction; the
  "who calls whom" correction is what belongs here.)
- **Where the wrong claim lives:** `coder/docker-compose.yml`'s header comment
  and its `lc-mcpo` retirement note; `CLEANUP-PLAN.md` K.4 ("OWUI pipe +
  agent-org reach the daemon there" - the **ancestor**); the stack-map's `coder`
  blockquote; and three rows in `documentation/CONTAINER-REGISTRY.md` (the
  `little-coder` row, the door table's "OWUI / agent-org -> little-coder:8090",
  and the retired-services row for `lc-mcpo`, "both real callers use the daemon
  directly"). Carried forward.

### 1.6 The context window "falls back to 131072, which is what we want anyway"

- **Reality:** `little-coder/config/models.json` declares `contextWindow: 90000`,
  and that file's own `_comment` records **131072 as a latent overflow bug** - it
  was above the then-current 87552 per-request lane before 2026-07-09.
- **Evidence:** `little-coder/config/models.json`. Verified 2026-08-28.
- **Where:** the `LITTLE_CODER_NO_CTX_PROBE` comment in
  `coder/docker-compose.yml`. Skipping the probe is still correct; do not
  "restore" 131072 on the strength of that comment.

### 1.7 "`.git/config`, `.git/hooks/`, `.git/info/` are mounted read-only"

- **Reality:** there is no such mount. The workspace volume
  (`little-coder-workspace`) is read-write in both `open-terminal` and
  `little-coder`, and nothing near `.git` is `:ro`.
- **Evidence:** the `volumes:` stanzas of both services in
  `coder/docker-compose.yml`. Verified 2026-08-28.
- **What actually exists** is *partial closure*, and the design doc's own
  "Enforcement status" paragraph says so: `core.hooksPath` baked to an empty
  0555 directory at image build, plus a workspace-edge bash filter against the
  obvious direct-write bypasses. The residual is recorded explicitly -
  `open-terminal` runs as root, so a `python -c '...write...'`, a
  base64-obfuscated path or a renamed utility still reaches `.git/config`. Full
  closure needs `CAP_DAC_OVERRIDE` dropped or a uid split, and is deferred.
- **Where the headline claim lives:** design §3.3's headline sentence (**the
  ancestor** - its own next paragraph retracts it); §13's threat-model line
  (which at least flags the enforcement gap); git-proxy's own denial message
  (`little-coder/git-proxy/git_proxy.py`, `blocklist:config-write`), which is
  where the "read-only" phrasing propagates from; and the docstring of
  `little-coder/tests/test_git_proxy.py`, which describes the mount as existing.
  Carried forward.
- Treat the headline as intent and the residual as the fact. The README states
  this as an instruction ("this bounds accidents, not a hostile repo") rather
  than as a defect.

---

## 2. Operational defects - not documentation problems

These are real bugs in scripts. They were deliberately **not** fixed by the
README rework, and the README carries only the workaround as an instruction.

### 2.1 The watchdog can detect a dead `open-terminal` but cannot restart it

`scripts/checks/stack-watchdog.ps1` still calls bare `docker compose` in its
repair paths. The root project has been a pure network anchor with **zero
services** since K.5b (`docker-compose.yml` declares only `volumes:` and
`networks:`, verified 2026-08-28), so any `docker compose <verb> <service>`
without `-f <plane>/docker-compose.yml` silently does nothing.

Surviving call sites, and this is a **class** of bug rather than one instance:

| Call | Hits |
|---|---|
| `docker compose up -d open-terminal` (`Repair-OpenTerminal`) | this plane's executor |
| `docker compose up -d $ServiceName` (`Confirm-AuxiliaryContainer`) | **generic** - every auxiliary service it repairs, including `little-coder`, `lc-egress` and `little-coder-backup` |
| `docker compose up -d llama-cpp-embed-upstream` | the inference plane |
| `docker compose restart llama-cpp-embed-upstream` | the inference plane |

Other call sites in the same file *do* pass `-f`, and `Test-ServiceHealth` was
migrated to name-based `docker inspect` at K.10 precisely because of this - so
**detection was fixed and remediation was not**.

### 2.2 `stack.ps1 health` cannot see `open-terminal` at all

`scripts/stack/stack.ps1`'s coder probe curls `little-coder:8090/health`, and
that endpoint reports only the daemon's own state - status, version, focus,
queue depth, in-flight count (`little-coder/src/littlecoder/daemon.py`, the
`@app.get("/health")` handler). It never touches the executor. `open-terminal`
appears in `stack.ps1` exactly once, in the project registry's `Note` string.
**A dead `open-terminal` therefore passes `stack.ps1 health` green.** Verified
2026-08-28.

Combined with 2.1: the watchdog sees the failure and cannot repair it, and the
health sweep cannot see it. The README tells the operator to probe the executor
directly and to restart it with the full `-f` form; that is a workaround, not a
fix.

---

## 3. Pending change to `coder/docker-compose.yml` (uncommitted elsewhere)

Checked 2026-08-28. On `refactor/ai-stack-cleanup` - the committed line this
README was written against - both `open-terminal` and `little-coder` carry
`env_file: - ../.env`, and the README's environment section describes that.

An edit removing those two `env_file` stanzas exists **uncommitted in the
operator's main checkout** (`git status` shows `M coder/docker-compose.yml`),
along with a new `scripts/checks/check-env-file-scope.ps1` that would enforce
the rule. The rationale recorded in that edit: `env_file: ../.env` injected all
111 variables of the root `.env` into both containers - Cloudflare tunnel token,
Authelia secrets, Mullvad key, Tailscale auth key, Mattermost bot tokens - none
of which the little-coder source references, and it was redundant from the day
it was written because `${VAR}` in an `environment:` block is interpolated by the
compose CLI from `--env-file` / the shell, never by `env_file:`.

**If that change lands, `coder/README.md` needs one update**: the paragraph under
"Bringing it up and down" that describes the two env mechanisms should drop the
`env_file: ../.env` half and say that each service names the variables it needs.
The `--env-file .env`-from-the-repo-root instruction is correct either way and
does not change.

---

## 4. Incidental

`little-coder/README.md` links to `../documentation/little-coder/` for its three
pointers (the design doc, `integration-plan.md`, `integration-tasks.md`). None of
those paths exist - the design doc now lives under
`../documentation/implementation-guide/little-coder/`. Found while verifying that
`coder/README.md`'s own pointer to that file resolves. Verified 2026-08-28.
