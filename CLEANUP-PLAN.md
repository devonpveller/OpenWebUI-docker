# ai-stack — comprehensive restructure, cleanup & security plan (v3)

**Date:** 2026-08-20. Supersedes the 2026-07-19 v2 **in place** (same file, same
part lettering, so v2 references stay meaningful). v2 was itself audited on
2026-08-20 by seven parallel read-only surveys (security/git hygiene, dead
code, a 280-file documentation triage, deploy plane, monoliths/duplication,
scripts inventory, agent-communication fabric). All file:line references are
from that 2026-08-20 snapshot.
**Status:** IN EXECUTION — the 2026-08-20 execution day (operator-authorized,
branch `refactor/ai-stack-cleanup`) landed the bulk of Parts A–E, G.1, D.1/D.2,
I and the J prep. Ledger below; unchecked items carry their blockers.

## Execution ledger — 2026-08-20 (~30 commits)

**DONE:** A.1 steps 2–4 (both key files defanged + `gw-` guard pattern; ROTATION
STILL PENDING = operator), A.2 (narrow mounts, verified in-container), A.3→D-2
(watchtower RETIRED; zero docker.sock mounts workspace-wide), A.4 all rows
(state files, root/root sidecar creds verified by live backup run, `.env.example`
complete, 6 backup files tracked, alerter dup removed, stale git-hook deleted,
SECURITY.md §0), B.2, B.3, B.4 with all five traps honored, B.5 (8 skills
exported + manifest; verified against live webui.db), C.1–C.9 (README rewrite,
copilot regen, update-docs merge, ~70 files archived, 7 junk deleted, runbooks/,
impl-guide index, agent-org docs/log split, CLAUDE.md/scripts-README/stack-map
truth sync), D.1 (compose split, config-diff proven identical), D.2 (entrypoint
318 lines, 8 routes verified live), D-5 (LM Studio fully retired incl. `.env`
boot-stall), D-13 + J.3 (lc-mcpo/search-mcpo retired, container rule applied),
D-14 (action deactivated + archived), D-15 (module archived, routing fixed),
E.2/J.2 (mm_lib, 6→1 loaders, bridge tests green), E.3 (module CLI extracted,
verified live), I.1/I.2 (ruff gate F+E9 clean in scope, CI workflow, 88+ dead
imports/vars removed), J.1 (cutover RUNBOOK prepared — deliberately not
flipped), D-9-lite (mnemory plugin defaults fixed; plane kept), D-11 (resolved
as document-not-delete: skills provenance README).

**IN FLIGHT:** F.1 extraction #1 (pending-decision semantics; gated on the
723-test Docker suite).

**OPERATOR DECISIONS 2026-08-20 (evening):** key rotation **DECLINED** —
accepted-risk posture recorded in SECURITY.md §0 (conditions: private remote
+ the `gw-` pre-commit guard; D-1 scrub deferred with it). D-4 **EXECUTED**
(20 GB data-backup deleted; update-stack.bat's obsolete xcopy step — which
created those snapshots from the dead bind-mount dir — replaced by a
sidecar-freshness check). #12 **EXECUTED** (data/openwebui deleted).
D-7 **EXECUTED** (old main tagged `archive/main-2025-05`, main re-pointed to
the integration line, 9 merged branches deleted; the 5 stale UNMERGED
branches still need individual keep/extract/drop calls). #11 **EXECUTED**
earlier (iks-dev overlay down, volumes kept, tree archived).

**STILL BLOCKED ON OPERATOR:** G.2 physical moves + watchdog rename (needs an
ELEVATED session to re-register Scheduled Tasks — commands in scripts/README);
J.1 flip (needs operator reachable); H.1 (OB1's uncommitted k8s work lands
first); H.2 (needs a GitHub repo created); the 5 unmerged stale branches.

**DEFERRED BY DESIGN:** E.1 gateway unification (coupled to D-9 + H.1);
F.1 #2–#9 (between gym rounds); F.2; E.4 (OB1-side); D.1 x-anchors polish;
D-12 (wire-or-demote stack-services.json).

## Execution ledger — 2026-08-21 (day 2, ~27 commits)

Day 2 cleared the operator-blocked queue and the operator's morning review:
G.2 **EXECUTED** (portal split to its own compose project, data migrated to
portal_* volumes; StackWatchdog re-registered elevated by operator); J.1
**EXECUTED** (virtual-keys flip — master_key ON, 13 sk- caller keys,
x-ai-stack-caller lane header via LaneHeaderInjector; proven on a throwaway
rig first); H.1 **EXECUTED** (OB1 converted to a pinned git submodule;
gateway = prebuilt image); tor **RETIRED** (all egress via Mullvad `vpn`,
zero-traffic validated); mnemory-gateway → `mnemory-cloud-gateway` rename;
CONTAINER-REGISTRY.md + doors matrix written; 5 stale branches triaged
(setup-prereqs extracted, 4 deleted); full-codebase validation pass fixed
38 loose ends (commit 38e6c28).

**Operator anomaly review (afternoon):**
- **#1 EXECUTED** — smolcrawl-pipelines + smolcrawl-backup retired (purpose
  died with OWUI Knowledge; zero logs 14 days). Main stack 28 → 27 default
  services (commit a18aa0c). `smolcrawl-data` volume kept for old crawl
  indexes — delete when confident.
- **#2 EXECUTED** — openbrain-db-backup + openbrain-wiki-backup moved INTO
  the OB1 project (OB1 commit 8d0a32a: scripts + Dockerfile.postgres under
  OB1/docker/backup/, services on native obnet/volumes). Output still lands
  in ai-stack/backups/ and container names are unchanged, so NAS mirror +
  freshness watchers (StackWatchdog, sysadmin check_backups) hold. ai-stack
  side: compose blocks + wiki-assets external decl removed (obnet +
  openbrain-wiki-data externals stay — open_notebook consumes them),
  recovery trio + coverage check + runbooks swept. Main stack 27 → 26;
  OB1 → 26 containers. Verified: fresh 1.3 GB pg_dump + wiki tar from the
  new project.
- **#3 OPEN** — "ai-stack as host" re-organization question (project-per-
  service-tree). Assessment delivered in-chat 2026-08-21; awaiting operator
  direction on scope.

Discipline for every phase (unchanged from v1/v2, plus one addition):

- **Archive (`git mv`), don't delete**, anything with history or inbound links;
  explain every removal in the commit message (what / why / what replaces it).
- **Verify a file is dead before moving it** — the audit found **four v2 items
  that would have broken live paths if executed as written** (B.4 traps below,
  one of them only caught on a second pass because a deployed artifact's OWUI
  id differs from its filename). The evidence here is a starting point, not a
  substitute for the check at execution time.
- **Dead-checks MUST include gitignored paths.** Default grep skips them, and
  both audit reversals hid exactly there: the live `.env` values (LM Studio)
  and the OWUI models export in `backup/models/` (skills). "Zero references"
  means nothing until `.env*` and deploy-artifact exports have been searched
  too (`grep --no-ignore`, or check the live `webui.db`).
- **Container rule:** adding/removing/moving a container or its files = change
  the compose file + the recovery scripts + the stack-map doc **together**.
- One part (or sub-part) per branch/PR. Never batch a security fix with a
  restructure.
- **NEW — check the working tree first.** In-flight uncommitted work exists
  (see "In-flight work" below); nothing in this plan may clobber or commit it.

---

## Audit ledger — what happened to v2 (2026-07-19 → 2026-08-20)

**Executed from v2: effectively nothing.** Two items self-resolved via
unrelated work; one item is partially delivered as a side effect:

- **B.3 (both rows) RESOLVED**: `owui/pipes/server_status.py` was rebuilt
  byte-identical to its source during the 0.11.0 upgrade; the
  `deep_research.py` "drift" was never real (the stored valve always overrode
  the in-code default) and the whole `smolcrawl/deep_research/` harness was
  retired 2026-08-20 (see In-flight work).
- **A.4 bridge-state row FIXED**: `scripts/claude-sessions-bridge/state/` no
  longer tracked (0 files); ignore rules extended to the sysadmin state dirs.
- **I.2 partially delivered**: a real pre-commit chain now exists at
  `.githooks/pre-commit` (staged-secrets guard + line endings + gateway-routing
  check, `core.hooksPath` set) — but see A.1: the secret guard cannot detect
  the one token class this repo actually leaked.

**Got worse since v2:**

| What | v2 | now |
|---|---|---|
| `orchestrator.py` | 11,758 lines | **13,505** (+1,747; 254 methods on one class; tests 604 → 723) |
| Mattermost token loaders | 3 copies | **5 Python + 1 shell** (sysadmin plane forked its own pair) |
| Watchtower scope | updates `openwebui` | updates **4 unlabeled containers**, incl. `surrealdb` on floating `:v2` + `pull_policy: always` |
| Leaked gateway key | 1 tracked file | **2 tracked files**, and the key was **never rotated** |
| Service-inventory restatement | 7 scripts | **12 sites**, incl. two divergent 14-row backup tables |
| Backup-sidecar schedulers | 2 flavors | **3 flavors** (crond 3 / supercronic 4 / sleep-loop 7) |

**v2 claims the audit overturned (do NOT execute v2 as written):**

1. **`tailscale_serve_admin.py` v1/v2 is INVERTED.** v1 is the live one —
   dispatched by `scripts/ai_pipes/tailscale_serve_pipe.py:1607` — and is 5
   months *newer* than v2. Archive **v2**, keep v1. (v2's B.4 said the
   opposite.)
2. **`system-prompts/` is not archivable as a unit.**
   `git-helper-system-prompt.md` is read by the deployed `githelper` pipe
   (`owui/pipes/githelper.py:102`); the research prompts are referenced and one
   is being edited right now.
3. **Root `skills/` is not dead — it holds deployed OWUI Skills.** All three
   files appear in the `skillIds` of two `is_active` custom models in the OWUI
   models export (`backup/models/models-export-*.json`); one deploys under an
   id (`github-repo-analyzer`) that differs from its filename, which is why
   every filename grep (v2's and the first audit pass) missed it. Deploy
   artifacts, not orphaned paste notes.
4. **`tools/` migration toolkit is not zero-reference.** Live module code
   advertises it to users (`modules/custom-tools/service/custom_tools.py:148`,
   `modules/help-system/service/help_system.py:149-151`). Archiving requires
   paired edits to those two files.
5. **`modules/emergency-recovery` is NOT unreachable** (CLAUDE.md is wrong).
   It is dispatch-reachable from OWUI today: whole-repo mount →
   `server_status.py` → `core/router.py:536-538` routes the keywords
   `recovery|fix|repair|emergency|restart|ollama` into it. A user typing
   "ollama" in OWUI gets routed into a stale module — a *reachable wrong
   answer*, not dead code. (New decision D-15.)
6. **`scripts/lib/stack-services.json` is canonical in name only.** Exactly one
   script reads it (`status_check.py:370`); the recovery scripts carry their
   own inventories; it has been frozen since 2026-06-12 and omits 44 live
   containers. v2's D.3 premise ("canonical but restated") is backwards. (New
   decision D-12.)
7. **Project-memory claims "Open Notebook RETIRED" and "mnemory replaced" are
   both false in the repo.** `open_notebook` is live ("being retired (Quartz
   workbench); still live for podcasts" — `scripts/lib/stack-services.json:50`)
   with forward work landing (podcast on-demand audio). `mnemory` +
   `mnemory-gateway` are live with many consumers; only the *Claude Code MCP
   client lane* moved to openbrain (`.mcp.json` has no mnemory entry). (New
   decisions D-9, D-10.)
8. **Recovery-script drift did not happen.** `emergency-recovery.ps1`/`.bat`
   were updated 2026-07-31 and reconcile *exactly* against all three compose
   projects (46 = 34 + 12 portal; OB1 24/24; agent-org 6 + 8 profile-gated).
   The container rule is working — keep it, and drop v2's implied fear.
9. **v2's C.5 "fix agent-bridge/README.md '55 tests'" is a no-op** — that file
   does not exist.
10. **v2's D-5 (LM Studio) was already decided on the operator's own record —
    and the "dead" path has a live runtime cost.** The 0.11.0 upgrade removed
    both LM Studio connections from OWUI
    (`documentation/implementation-guide/update-owui-to-0-11-0/UPGRADE-PLAN.md:691-693`);
    no LM Studio inference endpoint survives. Meanwhile the live `.env`
    overrides the sane compose default with the dead link-local host
    (`.env:122-126`: `LMSTUDIO_HOST=169.254.83.107`, `LMSTUDIO_PORT=5506`,
    `LMSTUDIO_ENABLED=true`), so `entrypoint.sh:158-159` probes it on **every
    `tailscale` container boot** — a guaranteed ~5 s failing stall + misleading
    warning on the container the whole tailnet depends on.

**In-flight work (uncommitted, 2026-08-20 — do not clobber, do not commit as
part of this plan):** staged deletion of `smolcrawl/deep_research/` (18 files,
−6,575 lines) + `smolcrawl/deep_research_thin_client.py`; unstaged:
`owui/tools/deep_research.py` v1.2.0 (async completion callback; deliberately
ahead of live `webui.db`), `owui/README.md`,
`agent-org/agent-bridge/app/modules/grounding.py`,
`system-prompts/research-system-prompt-engine.md`, two docs, and this file's
B.3 note. In the OB1 tree: `integrations/kubernetes-deployment/index.ts` has
+251 uncommitted lines.

---

## Part A — Immediate security remediation (do before anything else)

### A.1 CRITICAL — committed live bearer token: unrotated, and now in TWO files

Verified 2026-08-20: the `gw-…` Open Brain gateway key at `.vscode/mcp.json:14`
**matches the live `OPENBRAIN_GATEWAY_KEY` in `OB1/docker/.env`** — rotation
never happened. A second byte-identical copy is tracked at
`openbrain-gateway/smoke_test.py:26` (added in `05f868b`). Neither path is
gitignored; no `.example` exists.

1. **Rotate the key** at the gateway (`OB1/docker/.env` + every client copy:
   `.mcp.json:7` on disk, `.vscode/mcp.json`, any other client).
2. `git rm --cached .vscode/mcp.json`; add `.vscode/mcp.json` to `.gitignore`;
   commit a `.vscode/mcp.json.example` with env indirection. **Note:** the
   `.vscode` copy is also *structurally malformed* (the `openbrain` block is
   nested inside `docker-gateway.args` and can never load) — the example should
   fix the shape too.
3. Fix `openbrain-gateway/smoke_test.py` to read the key from env; commit.
4. **Close the guard gap:** `scripts/check-staged-secrets.ps1:55-63` has no
   `gw-` pattern — the exact class of token this repo leaked is the one class
   it cannot detect. Add a `gw-[A-Za-z0-9]{20,}` rule (and consider a generic
   long-random-string entropy rule).
5. **D-1 rescoped:** history now carries more than this key — the
   `.env.bak-pre-mtp` and `.env.bak-pre-qwen38` commits each held ~25 live
   credentials (caught only by GitHub push protection; `.gitignore:4-12` rules
   added after). If a history scrub is ever done, those commits are the
   driver, not just A.1.

### A.2 HIGH — whole-repo mount into the internet-exposed frontend — unchanged

`docker-compose.yml:18` still mounts `.:/host_project:ro` into `openwebui`.
Interim fix unchanged: narrow to `./core`, `./modules`, `./schemas`,
`./scripts` (recovery dispatch), `./system-prompts`. Verified consumers as of
2026-08-20: `core/router.py:23-27`,
`scripts/ai_pipes/unified_openwebui_pipe.py`, `owui/pipes/githelper.py:102`
(→ `system-prompts/git-helper-system-prompt.md`),
`modules/emergency-recovery/service/emergency_recovery.py`. Durable fix is
still Part G.1.

### A.3 HIGH/MED — Watchtower — worse than v2 stated

`docker-compose.yml:99-121`: image still `containrrr/watchtower:latest`
(:100), docker.sock still mounted (:108 — the only sock mount in the
workspace), whole-repo `/compose-dir` mount still present (:110). **New:**
there is no `WATCHTOWER_LABEL_ENABLE` and zero `enable=true` labels, so scope
is "everything not labeled false" — **4 containers lack the opt-out label**:
`openwebui`, `watchtower` itself, **`surrealdb`** (floating `surrealdb/surrealdb:v2`
+ `pull_policy: always` at :826 — an unflagged auto-update of the datastore
behind `open_notebook`), and `open_notebook`. Interim: label `surrealdb` and
`open_notebook` false, pin the watchtower image. Durable: **D-2** (retire
Watchtower for the explicit update runbook — the case is now stronger).

### A.4 MEDIUM/LOW items — updated table

| Item | Status 2026-08-20 | Action |
|---|---|---|
| Bridge runtime state in git | **FIXED** (0 tracked files) | — (done) |
| Redaction test fixture PAT | **RESOLVED** — `ghp_ABCD…` is an obvious placeholder | strike |
| `.claude/settings.local.json` tracked + ignored (`.gitignore:61`) | still open | `git rm --cached` |
| `open-notebook-backup` hardcoded root/root | still open, now `docker-compose.yml:1387-1388` | switch to `${SURREAL_USER}`/`${SURREAL_PASSWORD}` |
| `.env.example` drift | **grew: 58 vars missing**, incl. the 8 v2 named **and secret `WEBUI_SECRET_KEY`** (compose:23 — a fresh clone silently breaks encrypted-at-rest values), `LLM_QUEUE_*` ×10, `LLAMA_*`, backup knobs; line 1 still `OLLAMA_HOST` | regenerate the file from compose `${VAR}` refs; drop the ollama line |
| `backup/` allowlist rot | **partially fixed** (`generic-tar-backup.sh`, `pg-backup.sh` committed in `eca57da`); **6 live files still untracked**: `open-notebook-backup.sh`, `openbrain-db-backup.sh`, `openbrain-wiki-backup.sh`, `Dockerfile`, `Dockerfile.postgres`, `Dockerfile.surreal` — a fresh clone still cannot build/run those sidecars | extend `.gitignore:47-48` re-includes; commit all 6 |
| `config/alerter` cred duplication | still open; compose mounts the `secrets/` copies (:1646-1647) but `:1645` still bind-mounts the dup dir RW into `/app` | keep `secrets/` as home; remove `config/alerter/{credentials,token}.json`; keep the code files |
| SECURITY.md scope gap | untouched since 2026-05-31; covers none of A.1–A.3; `:22` "no docker.sock in the portal slice" is technically-true-and-misleading | update after A.1–A.3 land |
| **NEW: stale `.git/hooks/pre-commit`** | live hook is `.githooks/` (via `core.hooksPath`), but the old 2-check copy in `.git/hooks/` remains — and a fresh clone that doesn't set `core.hooksPath` gets **no secret guard at all** | delete the stale copy; add a bootstrap note/script (`git config core.hooksPath .githooks`) to README + CLAUDE.md |
| **NEW: `.mcp.json` literal token on disk** | gitignored (`.gitignore:27`) but holds the live `gw-` key in plaintext; same for `OB1/docker/mcpo*.config.json` (`MCP_ACCESS_KEY`) | after rotation, prefer env indirection where the client supports it; at minimum document these as secret-bearing files in SECURITY.md |

---

## Part B — Quick wins: cruft, junk, drift (low risk)

### B.1 Git hygiene

- **Commit the 6 untracked live backup files** (table above) — highest-value
  single commit in this part.
- **Branch cleanup is now a two-step (D-7 rescoped):** `main` is **15 months
  stale** (2025-05-31), so `--merged main` yields nothing and any CI/hygiene
  rule based on main operates on a fiction. Step 1: fast-forward or re-point
  `main` to the de-facto integration branch (`feature/orchestration-automations`)
  — an explicit operator action. Step 2: delete the **9 branches fully
  contained in HEAD** (`dev`, `feature/external-access-front-end`,
  `feature/function-pipe-systems-qol`, `feature/integrated-knowledge-system`,
  `feature/litellm-proxy`, `feature/little-coder`,
  `feature/research-tooling-openbrain-opennotebook`,
  `update/optimize-tools-functions-for-owui-0.9.6`, `update/owui-to-v-0-9-6`).
  The 5 unmerged stale branches (`update/emergency-recovery-quick-fixes`,
  `feature/enable-lm-studio-via-tailscale` (ties to D-5),
  `update/enable-emergency-network-module`, `dev-prerequisitePowershell`,
  `dev-automatic1111ContainerSupport`) each need a keep/extract/drop decision —
  they hold commits reachable nowhere else.

### B.2 Junk and stale artifacts

- Empty botched-redirect dirs (now **4**): `config/authelia;C/`,
  `config/caddy/Caddyfile;C/`, `OB1/integrations/kubernetes-deployment;C/`,
  and `documentation/implementation-guide/update-owui-to-0-9-6/migration;C/` —
  delete.
- Root `gym-*.log` — now **7** files (gym-009…015) → delete or move to
  `logs/`; point the gym runner's log path there.
- `data-backup/` 20 GB March snapshot — unchanged (Decision D-4).
- `data/openwebui/` leftover — confirm and delete, **but `data/` itself is not
  wholesale-deletable**: `data/tailscale/` is live (mtime today).
- Config stubs `config/ollama.conf`, `config/openwebui.conf` — archive. Note
  they are currently shipped into the OWUI container via the `./config` dir
  mount (:17), so this is also a (tiny) A.2-adjacent cleanup.
- Committed `__pycache__` dirs: `documentation/…/iks-dev/__pycache__/`,
  `tools/owui-knowledge-to-openbrain/__pycache__/` — remove + gitignore.
- `logs/tailscale-health.log` rotation — still unaddressed; add rotation in
  the watchdog script (see G.2 rename).

### B.3 Deploy-drift — ~~both rows~~ RESOLVED 2026-08-20

- ~~`server_status.py` lags its source~~ — rebuilt byte-identical during the
  0.11.0 upgrade (`owui/README.md:63-67`).
- ~~`deep_research.py` defaults to unreachable host~~ — was never drifted (the
  stored valve `http://openbrain-research:8000` overrides the in-code
  default); the old comparison target was retired 2026-08-20 with the
  `smolcrawl/deep_research/` harness. The v1.2.0 client is deliberately
  **ahead** of live `webui.db` — paste only with the matching engine build
  (`RESEARCH_OWUI_API_KEY` configured).

### B.5 NEW — OWUI Skills are an uncatalogued deploy-artifact class

The `skills/` correction (Audit ledger #3) exposed a gap: `owui/` +
`owui/manifest.csv` catalog tools/pipes/filters/actions, but **OWUI Skills
have no manifest and an incomplete repo snapshot** — the models export lists
`doc-coauthoring`, `skill-creator`, `docx` in `skillIds` with no corresponding
repo file. Work item: verify skill registrations against the **live**
`webui.db` (not the 2026-04 export), export the missing skills, move
`skills/` under `owui/skills/`, and extend `manifest.csv` (or a sibling) with
the `file → owui_id` map — the exact mechanism that already prevents
filename-vs-id confusion for the other four artifact classes.

### B.4 Dead code — archive with evidence (CORRECTED — read the traps)

| Set | Evidence (2026-08-20) | Action |
|---|---|---|
| Ollama remnants | Commented block `docker-compose.yml:230-268`; `OLLAMA_HOST` env :26; `entrypoint.sh:134-146,678,1001-1067,1160`; `emergency_recovery.py` (35+ hits); `.env.example:1`; README ×17 lines / copilot-instructions ×5; **+ missed by v2:** `scripts/quick-fixes.bat:316`, and the live routing keyword `ollama` at `core/router.py:536` (see D-15) | one "retire ollama" commit across all of it; the `router.py` keyword goes with D-15 |
| LM Studio (all of it) | `Fix-LMStudioTailscale.ps1`, `fix-lmstudio-tailscale.sh`, `lmstudio-proxy-supervisor.sh`, `lmstudio_fix.py` (v1), `lmstudio_fix_v2.py` (+ registration `emergency_recovery.py:48`) **+ missed by v2:** `quick-fixes.bat:476-513`, `emergency_recovery.py:1063`, doc twin in `documentation/AI/`, **live `.env:122-126` pointing at the dead host → boot stall (Audit ledger #10)** | **D-5 RESOLVED — LM Studio is fully retired as an inference target (operator record, 0.11.0 upgrade).** Archive all five scripts, drop `entrypoint.sh:148-210` + the `:705-721` monitor branch, and **in the SAME commit** clean the `.env` keys (`LMSTUDIO_ENABLED/HOST/PORT`) and align `.env.example:52-56` — otherwise the always-failing boot probe outlives the cleanup. **Clarified:** the `C:\Users\yamao\.lmstudio\models` mount is NOT LM Studio — it's the shared GGUF store for `llama-cpp-upstream` (compose :1500-1502 says so). Keep the mount. |
| Legacy per-capability pipes | 5 `*_pipe.py` + template + `scripts/templates/` — still zero code refs. **Trap:** `custom_tools.py:65-67` globs `*_pipe.py` into a user-facing tools inventory, so archiving changes that output (accepted, but not zero-impact). `scripts/utilities/` is **NOT dead** — imported by 2 live modules + little-coder CLI; it moves in G.1/G.2, not to archive | archive the 5 dead pipes + template + templates/; keep `tailscale_serve_pipe.py` (LIVE via `custom_tools.py:361,365`) and `scripts/utilities/` |
| `tools/` migration toolkit | Code-dead but **string-advertised by live modules** (`custom_tools.py:148`, `help_system.py:149-151`) + `modules/migration_report.json` | archive **with paired edits** to the two module files (and the README refs die in C.1) |
| **`tools/owui-knowledge-to-openbrain/` — NEWLY DEAD** | Its purpose (OWUI Knowledge → OB promotion) ended with commit `9223516` ("retire the rest of OWUI knowledge"); SenseGlove promotion done in June; zero consumers | archive (v2 said keep — reversed). Related decision **D-14**: `owui/actions/add_web_sources_to_knowledge.py` still deployed but writes into the retired Knowledge layer |
| `test/` broken tests | Unchanged: 4 of 6 broken `sys.path`; root `test_pipe_lmstudio.py` still the functional twin | as v2: move the functional twin into `test/`, archive the broken ones (convert survivors to pytest in G.1) |
| `modules/custom-tools` v1/v2 | **INVERTED — see Audit ledger #1** | archive **`_v2.py`**; keep `tailscale_serve_admin.py` (v1); its only other consumer (`lmstudio_fix_v2.py`) is itself archived under D-5 |
| Root `skills/*.md` (3) | **TRAP — NOT dead (Audit ledger #3):** deployed OWUI Skills on active models; `github-chat-mcp.md` deploys as `github-repo-analyzer` | **do not archive** — see B.5 (verify live, catalog, move under `owui/`) |
| `system-prompts/` | **NOT a unit — see Audit ledger #2.** `general-system-prompt.md` is UNVERIFIED (same filename-vs-deployed-id risk as skills/; needs a live `webui.db` check) | keep the dir (it's a live mount consumer); archive nothing until each file is verified against the live deployment |
| **`modules/environment_config.py` — NEWLY DEAD** | zero references workspace-wide (grep-based — re-check gitignored paths per the new discipline rule before moving) | archive |
| Misfiled bridge docs in `agent-org/docs/` | both files still there; target `scripts/claude-sessions-bridge/docs/` doesn't exist yet | `mkdir` + `git mv` |
| `reports/` | **LIVE — portal digests written daily** (compose :1653 mount; `portal-status.ps1` reads it) | never archive |

---

## Part C — Documentation truth pass

Grounded in the 2026-08-20 triage of **280 markdown files**: 170 LIVE, 12
RUNBOOK, **57 STALE, 29 SUPERSEDED, 7 SCRATCH/JUNK, 5 CODE-IN-DOCS** — i.e.
**~98 files (35%) are the clutter** the workspace feels. (One triage verdict
was later overturned: root `skills/*.md` are deploy artifacts, not stale docs
— they follow B.5, not this part.) The rest falls into a small number of
block moves:

1. **`README.md` rewrite** — unchanged and still the single most-read,
   most-wrong file: 6× `ai_stack_router.py` (exists nowhere), 23× Ollama, dead
   port 11434 ×2, "Last Updated October 6, 2025". Replace with a short README
   (topology table from CLAUDE.md, quickstart, pointers to `/stack-map`,
   runbooks, SECURITY.md); salvage the Tailscale/GPU debugging content into
   `documentation/runbooks/`.
2. **`.github/copilot-instructions.md`** — regenerate from CLAUDE.md +
   stack-map or delete (**D-6**, unchanged).
3. **Update docs: merge, don't keep both.** `UPDATE-QUICK-START.md` (root) and
   `documentation/UPDATE-MANAGEMENT.md` are BOTH Ollama-era stale. Fold into
   one accurate update doc (the 0.11.0 upgrade plan is the current reference
   procedure).
4. **The pipe-era archive block — one `git mv` commit (~18 files):**
   `documentation/` STALE set (`UNIFIED_PIPE_SYSTEM`, `PIPE_IMPLEMENTATION_
   COMPLETE`, `PIPE_RECOVERY_SYSTEM_TEST_RESULTS`, `HELP_SYSTEM_COMMANDS`,
   `ROUTING_FIX_OCTOBER_2025`, `AUTONOMOUS-RECOVERY-GUIDE`,
   `LM_STUDIO_TAILSCALE_SETUP`, `TAILSCALE_SERVE_EXECUTION_GUIDE`,
   `UPDATE-MANAGEMENT` post-merge) + all 5 `documentation/AI/` files + 2
   `documentation/AICodeAgentGuides/` files + 2
   `modules/emergency-recovery/docs/` files → `documentation/archive/`.
5. **Scratch deletes (7):** `documentation/{Links.md, Readme.md,
   JSON_ISSUE_FIXED.md, TIMESTAMP_FIX_APPLIED.md,
   quality-effect-on-code-agents.md}`, `tools/code-generation/.agent/context.md`
   (never populated), + the `migration;C/` dir (B.2).
6. **Runbooks consolidation:** move the 12 RUNBOOK-verdict files
   (incident-response, monitoring-access, backup-conventions,
   backup-restore-runbook, restore-from-snapshot, PREVENTION-GUIDE,
   TAILSCALE_SERVE_QUICK_REFERENCE (trim LM Studio bits), PROMOTION-RUNBOOK ×2,
   PHASE6-WIRING-HANDOFF, little-coder UPDATE-NOTES, agent-org P7) into
   `documentation/runbooks/` (+ salvaged README content).
7. **implementation-guide hygiene:**
   - Move **`iks-dev/` out of docs** (compose file, `seed.sql`, 4 `.py`,
     `__pycache__`, 14 absolute-path bind mounts) → a real code home; fix the
     leading-space folder name `open -notebook-integration-openbrain/`; fix the
     `open-source0authentication…` filename typo.
   - Archive the **superseded plan/task sets for shipped work**: the
     `daily-digests-autonomous-podcasts/` trio (claims "not started"; the
     plane is deployed), `update-owui-to-0-9-6/` (banner already added),
     integration-plan/-tasks pairs under LiteLLM-Proxy, little-coder,
     web-search, auth-front-end, teams-chat (5 files incl. self-declared
     superseded OUTLINE), expand-quartz-4 TASKS, Systems-of-structured-data
     INTEGRATION-TASKS, research-engine TASKS, idea-refinery's parked
     OpenRouter plan, `expanding-daily-digest-with-auto-podcast/`,
     `hybrid-openwebui-update-approach.md`,
     `system-prompts/research-system-prompt.md` (v1 — after verifying it
     against the live deployment per the B.4 system-prompts caveat).
   - Tag remaining subfolders `shipped/` vs `proposed/` per the triage (the
     honest drafts — control-tower, vllm, supervised-research, automations,
     level-4, quartz-production-build, wsl-governance — stay as `proposed/`).
   - **Two content decisions the files can't answer:** the
     `autonomous-updates-with-security/` build state (its plan/tasks have no
     completion markers and both real upgrades ran manually — fold into D-2),
     and the `Systems-of-structured-data/INTEGRATION-PLAN.md` header that
     contradicts its own folder's PHASE6 doc ("Phase 1 not started" vs
     "Phases 0–5 complete") — fix the header.
8. **agent-org docs split** (unchanged in spirit): `docs/design/`
   (ORCHESTRATION-DESIGN, open proposals, two-lane plan) vs `docs/log/`
   (**25 P-series files + gym-010** — move, don't delete; first lift any
   still-binding rules from late P-files (P30, P32) into ORCHESTRATION-DESIGN).
   Drop v2's "fix agent-bridge README test count" (file doesn't exist).
9. **NEW — fix the false claims in living docs:** CLAUDE.md's
   "emergency-recovery module … not part of the live recovery path" (it is
   OWUI-reachable — reword per D-15 outcome); CLAUDE.md's stack table should
   mention Mattermost lives in the agent-org compose; rewrite `scripts/README.md`
   (2025-12 vintage, predates every current subsystem).
10. **NEW — `.claude/skills/` mirror (D-11):** 15 of 17 skill dirs are
    byte-identical copies of `OB1/skills/` (~40 files) that will silently
    drift on the next OB1 pull. Decide: keep the copy (pin + document), or
    thin out to the ai-stack-native five (`stack-map`,
    `validate-before-change`, `agent-org-*`) + a sync note.
11. **Prevent recurrence (feeds I.4):** every new doc carries a status header
    (`LIVE | DRAFT | SUPERSEDED-BY: <path> | ARCHIVED`); superseding a doc
    means banner-ing the old one in the same PR; completion notes go to
    `documentation/archive/` at write time, not never.

---

## Part D — Deploy plane refactor

### D.1 Modularize `docker-compose.yml` — unchanged, counts corrected

Now 2,249 lines / still exactly 46 services (12 portal-profile-gated; the new
July–August subsystems added zero root services — sysadmin/telegram are host
scripts, Idea Refinery is OB1-side). Split by plane via `include:` exactly as
v2 laid out. Corrections: **12** backup sidecars in root (+2 in agent-org), and
the scheduler split to unify is now **three-way** (crond ×3 / supercronic ×4 /
sleep-loop ×7 — the sleep-loops exist because busybox crond misses fire windows
on Docker Desktop clock jumps, compose :1341-1345; standardize on one
mechanism and document why). Anchors to introduce: `x-hardening` (44
`security_opt` copies), `x-watchtower-disable` (43 labels),
`x-healthcheck-http`, `x-backup-sidecar`. The inline slot-watchdog healthcheck
is now at :345 → move to a mounted script. Container rule applies (recovery
scripts + stack-map in the same PR).

### D.2 Rewrite `entrypoint.sh` — unchanged (verified byte-identical since 2026-07-05)

Still 1,181 lines, 7 `setup_*_serve()` ×3 restatements, dead Ollama block, LM
Studio block (drop — D-5 resolved), ~480-line monitor loop. Replace with one
data-driven serve table + one loop; make cross-project routes (Mattermost,
Quartz wiki, LiteLLM UI) config-flagged.

### D.3 Service-inventory single source of truth — REFRAMED (D-12)

The JSON is not the de-facto source (Audit ledger #6). Restatement sites grew
to **12**, including two independent 14-row backup-recency tables with
**divergent thresholds** (`check-tailscale-health.ps1:1008` vs
`scripts/sysadmin-mcp/check_backups.py:49` — 52h vs 36h, etc.) and a wrong
comment (lm-models "weekly cron" — it's a sleep-loop now). Decision **D-12**:

- **Option A (wire it):** regenerate `stack-services.json` from the compose
  files (script), then make the recovery scripts, watchdog, `check_backups.py`
  and `portal-off.ps1` load it (PS5.1: `ConvertFrom-Json` → PSCustomObject,
  ASCII no-BOM), with one shared threshold table.
- **Option B (demote it):** delete the JSON, declare compose the single
  source, and generate any needed inventory at check time
  (`docker compose config --services`).
Either way: collapse the two backup tables into one.

### D.4 Windows host-path portability — unchanged list, one addition

All v2 paths confirmed (lines shifted: compose 285/1518, 825/902/1397, OB1
scheduled 72/102/133/**207**; `notify-mattermost.sh:18,21`;
`ai_pipes/config.json:5,7`; `.claude/settings.local.json:32,35`). Addition:
the `iks-dev/` dev compose carries 14 more absolute `D:/…` mounts — resolved
by the C.7 move rather than parameterization.

### D.5 NEW — what does NOT need work

Recovery scripts are in sync (Audit ledger #8). `quick-fixes.bat` is the one
recovery-adjacent script still carrying dead strata (ollama :316, LM Studio
:476-513) — clean it in the B.4 commits.

---

## Part E — Code quality: dedup and shared foundations

1. **Unify the twin privacy gateways** — unchanged, still ~75% byte-identical
   (`openbrain-gateway/app.py` 236 lines vs `mnemory-gateway/app.py` 267; 1:1
   function inventory, `labels`↔`metadata` rename). One package, two policy
   configs. Sequence with **D-9** (mnemory's fate) and **H.1** (gateway moves
   into OB1 or becomes an image). Bonus: kills `smoke_test.py` (A.1 file #2).
2. **One Mattermost/bot client lib** — scope grew: **5 Python + 1 shell**
   loaders (`bridge.py:84-111`, `approval_server.py:57-78` (verbatim dup),
   `mattermost-mcp/server.py:58-112`, `sysadmin-mcp/resolve_channel.py:19-29`,
   `sysadmin-mcp/telegram_notify.py:31-55`, `notify-mattermost.sh:42`) plus 4
   call sites that reuse `mattermost-mcp/server.py` via `sys.path` injection +
   `os.environ["MM_TOKEN"]` mutation. Extract `scripts/lib/mm_lib.py`
   (identity → token, post, read, channel resolution) and kill the two
   hardcoded channel IDs (`notify-mattermost.sh:19`,
   `mattermost-mcp/server.py:47`). This is also **J.2**.
3. **Collapse the `health()` scaffolding** — unchanged: 7 modules, the
   trailing CLI/health block hash-identical across 6 of 7. One base class in
   `core/`.
4. **OB1 workers shared lib** — unchanged, plus two files v2 missed:
   `recipes/email-history-import/pull-gmail.ts` (1,596) and the per-worker
   `_shared/helpers.ts` (835) that should be the cross-worker lib. The k8s MCP
   server is now 2,055 lines *in the working tree* (+251 uncommitted) —
   coordinate with whoever owns that change before splitting.
5. **Health-endpoint convention** — moved to Part J.6 (it's a comms-fabric
   concern).

---

## Part F — Monolith decomposition

### F.1 `orchestrator.py` — the flagship refactor, now more urgent

13,505 lines (+1,747 since v2), 254 methods on one class, docstring still
claims "thin glue". Tests grew 604 → **723** across 89 files — the safety net
holds (note: they run in Docker; the local `.testvenv` is stale/missing PyJWT,
so don't trust local `pytest --collect-only`). The 9-cluster extraction plan
stands with refreshed line ranges:

1. Pending decisions (13046–13162) — **partially extracted**: `modules/
   pending_store.py` exists but holds CRUD only; move the
   rehydrate/render/reconcile semantics next. Still the right first PR.
2. Handoff protocol (12219–12468 + outliers at 297, 9787).
3. Worker plan gate / flail replan (12475–12881 — grew ~47%).
4. Drain loop + QA lenses (10388–11658, ~1,271 lines — doubled; plus
   `_finish_effort` 11659–12136 (~478) and drain fragments at 1190, 2600,
   7416–7883 — the extraction should reunify these five regions).
5. Burndown campaign (7920–8708).
6. Delivery pipeline (8709–9802 + satellites) — still the largest.
7. Intake/NL classification (2963–4223; `nl_intake` alone now 792 lines).
8. Project onboarding (2786–3176) — `modules/projects.py` exists; finish the
   move.
9. `_nl_*` scatter → command registry (8 methods in two clumps + the 343-line
   `_handle_command` elif-chain at 13163–13505).

Cadence guard unchanged: extractions between gym rounds, mechanical
move-plus-delegate, tests green after each.

### F.2 `bridge.py` — unchanged (1,796 lines, +21)

Split into poller / session table / follow registry / turn worker after the
in-flight session work settles.

### F.3 OWUI plugin monoliths — unchanged, one addition

`fileshed.py` 11,000 / `superpowers_tool.py` 3,592 / `code_agent.py` 3,018 —
same posture (don't decompose paste-deploy artifacts; `code_agent` is an
archive candidate, see also D-14 for its Ollama default endpoint). **Addition:**
deprecation markers exist only in `owui/README.md`, never in file headers —
add a header banner to `code_agent.py` (and any future inactive plugin) so a
direct reader gets the signal. `owui/` totals 25,143 LOC with zero tests;
that's structural, but the build-source pattern (server_status) is the escape
hatch when one needs work.

### F.4 smolcrawl — REWRITTEN: resolved by retirement

`smolcrawl/deep_research/` (~10k LOC, zero tests) is **gone** (retired
2026-08-20 to `OB1/integrations/research-service/`; deletion currently staged,
uncommitted). Residue: `smolcrawl/` proper is 3,442 LOC, 13 files, still zero
tests (`smolcrawl_pipeline.py` 834, `src/smolcrawl/owui_client.py` 576). Same
policy as v2: smoke tests before any refactor; otherwise leave until it breaks.

### F.5 NEW — `scripts/ai_pipes/tailscale_serve_pipe.py` (1,749 lines)

The largest file in the status-pipe subsystem, live, and invisible to v2.
Fold into **G.1**: when the subsystem moves, split serve-route table /
dispatch / rendering. (Its hardcoded dispatch of `tailscale_serve_admin.py`
v1 at :1607 is the trap from Audit ledger #1.)

---

## Part G — Repo restructure (taxonomy)

### G.1 Consolidate the status-pipe subsystem — unchanged target, four new notes

The `status-pipe/` consolidation (v2 layout) stands, and remains the durable
A.2 fix. Execution notes from the audit:

- Include `system-prompts/` in the narrowed mount set (githelper consumer).
- The live admin script is `tailscale_serve_admin.py` **v1**; `_v2` is the
  archive candidate.
- Retire the `ollama` keyword route and settle **D-15**
  (`modules/emergency-recovery`: either fix its content or remove the
  `emergency-recovery` routing branch + render whitelist entry) as part of
  this move.
- `scripts/utilities/` moves here too (it's part of the pipe subsystem +
  help/custom-tools modules), not to archive.

### G.2 Sort `scripts/` — REVISED taxonomy

v2's flat 7 buckets no longer fit: the subsystems added since July are
internally cohesive and cut across buckets. New rule: **subsystem dirs stay
whole; buckets are only for loose scripts.**

```
scripts/
  claude-sessions-bridge/   (as-is; + docs/ from the misfiled agent-org pair)
  sysadmin-mcp/             (as-is — MCP server, telegram channel, compaction,
                             scheduled checks; coupled via config.json + state/)
  mattermost-mcp/           (as-is → absorb into lib/mm_lib consumers over time)
  recovery/    emergency-recovery.ps1/.bat, quick-fixes.bat, namespace_reset.py,
               nuclear_option.py, rebuild_tailscale.py, restart_openwebui.py,
               gpu_check.py, status_check.py, update-stack.bat
  portal/      portal-*.ps1, breach-killswitch.ps1, access-query.ps1
  backup/      backup-to-nas.ps1, install-nas-backup-task.ps1,
               set-nas-credential.ps1, restore-from-snapshot.ps1
  checks/      stack-watchdog.ps1 (RENAMED from check-tailscale-health.ps1 —
               it is a 1,555-line Docker-engine/backup/bridge watchdog now;
               rename the Windows service/task with it), check-*-health.ps1,
               check-backup-coverage.ps1, check-llm-gateway-routing.ps1,
               check-staged-secrets.ps1, validate-lineendings.ps1,
               install-service.ps1, simple-monitor.ps1
  lib/         stack-services.json (pending D-12), portal-alerter-client.ps1,
               mm_lib.py (E.2/J.2)
  archive/     lmstudio cohort incl. fix_v2 (D-5), backfill-syntheses.sh,
               test_router.py, templates/, legacy pipes (B.4)
```

Cautions: **`state/` dirs are untracked — a `git mv` of a subsystem dir leaves
them behind; move manually and re-point configs.** Scheduled Tasks reference
absolute paths (sysadmin registrars, bridge, NAS backup, health service) —
re-register in the same change. Path consumers to sweep: compose mounts
(`post-update-hook.sh` :109), `.mcp.json`, `.claude/settings.local.json`
hooks, `emergency_recovery.py` registry, docs. Rewrite `scripts/README.md`
as part of this PR. `gym-watch-effort.py` moves to `agent-org/`, not a bucket.

### G.3 Service dirs stay at root — unchanged (recommend against `services/`).

---

## Part H — Submodule strategy (verified, unchanged)

- **H.1 OB1 — convert now.** Both couplings confirmed at
  `OB1/docker/docker-compose.yml:155` (builds `../../openbrain-gateway`) and
  `:585` (mounts `../../secrets/openbrain-wiki-deploy_key`). Same 4-step plan
  (move/publish the gateway → parameterize the key path → un-ignore +
  `git submodule add` pinned → bump-via-PR rule). Sequence **after A.1**
  (key rotated) and coordinate with the +251 uncommitted lines in OB1's k8s
  `index.ts`.
- **H.2 little-coder — second** (unchanged; consumed by root compose builds +
  agent-org egress builds at `agent-org/docker/docker-compose.yml:440,516`).
- **H.3 agent-org — defer** until F.1 lands and P-series cadence slows
  (unchanged; it remains the hottest dev area).
- **H.4 stays in main repo** — unchanged list.

---

## Part I — Guardrails: keep it clean

1. **CI (GitHub Actions):** run the four real pytest suites (agent-bridge now
   **723** tests — in the Docker context, not the stale `.testvenv`;
   little-coder; llm-queue 38; search-gateway 29); `docker compose config -q`
   on all five compose files; the routing check; **gitleaks** (would have
   caught A.1 both times). Prereq: D-7 step 1 (a truthful `main`) so CI has a
   real base.
2. **Pre-commit:** PARTIALLY DONE (`.githooks/`: secrets guard + line endings
   + routing check). Remaining: `gw-` + entropy patterns in the guard (A.1.4),
   ruff (`ruff.toml`), delete the stale `.git/hooks/pre-commit`, and a
   documented bootstrap (`git config core.hooksPath .githooks`) so clones are
   protected.
3. **Drift checks as scripts:** gated on **D-12** — build
   `checks/verify-stack-map.ps1` only against whichever inventory source wins.
4. **Conventions doc** (one page): the 3-places container rule, health/port
   conventions (Part J.6), kebab-case, archive-don't-delete, secrets only in
   `.env`/`secrets/`, **doc lifecycle rules from C.11**, deploy-artifact
   manifests (B.5), and where a new service/script/doc goes.
5. **OB1 fork-sync routine** — unchanged, lands with H.1.

---

## Part J — NEW: agent-communication fabric

The audit mapped every agent-facing surface (3 stdio MCP servers, 4 mcpo
wrappers, 2 privacy gateways, 3 LiteLLM-plane services, the search gateway,
2 bridges + Telegram, the agent-org bus, 8 OWUI plugin targets, 8 OB1 inlets).
Verdict: the stack has **six incompatible auth schemes, six health
conventions, fully hardcoded discovery, two approval systems, and exactly one
reliable-delivery implementation**. For "clean, expandable, supports future
agent implementations," these five moves have the strongest evidence:

### J.1 Make caller identity survive the chain (highest leverage, smallest diff)
`llm-queue`'s per-caller priority lanes (`llm-queue/src/llm_queue/policy.py:64-81`
— owui-chat 0/30s … ob-research 3/1800s) are **structurally defeated**:
LiteLLM rewrites every upstream `Authorization` to `Bearer dummy`
(`config/litellm.config.yaml:95-105`), so lane assignment survives only if a
caller volunteers the OpenAI `user` field. Fix = the config file's own noted
path: enable LiteLLM `master_key` + per-caller virtual keys, pass keys
through to llm-queue. One move buys real per-agent priority, spend
attribution, and rate limiting — the prerequisite for any multi-agent future
where agents share one GPU. **This resolves D-3 as "add keys" rather than
"accept."** (Keep `background_health_checks: false` and the llama-swap
gotchas — those are correct.)

### J.2 One bot/chat client library (= E.2)
Single `mm_lib` (token/identity, post, read, channel registry) replacing 6
loaders + 4 `sys.path`-injection reuses + 2 hardcoded channel IDs. Keep the
Telegram channel deliberately separate (it's the Docker-independent
break-glass path — that's a feature).

### J.3 Prune and standardize the MCP edge
- **Retire `search-mcpo`** (compose :1076-1105): keyless, and unreachable by
  any in-network consumer (it sits on internal `search-net`; OWUI isn't).
  Container rule applies.
- **`lc-mcpo` (D-13):** dormant by its own compose comment, bypassed by both
  real callers. Either give it a consumer (OWUI tool-server path) or retire
  it; don't keep a third parallel little-coder door.
- Keep the OB1 mcpo pair (exists for a real upstream bug, documented).
- Standardize the remaining edges on one auth header convention and one key
  issuance path; fix `.vscode/mcp.json` shape (A.1.2).

### J.4 One privacy-gateway implementation + settle the memory lanes (= E.1, D-9)
Merge the twin gateways; as part of D-9, either recommit to mnemory (then fix
the two OWUI plugins whose default URL `http://localhost:8050` has never been
publishable — `owui/tools/mnemory.py:22`,
`owui/filters/mnemory_persistent_memory.py:26`) or migrate its remaining
consumers to Open Brain and retire the mnemory plane properly (compose +
recovery + stack-map + backup sidecar + OWUI plugins together).

### J.5 Promote the good bus, converge the duplicates
agent-org's `event_gateway` (at-least-once, idempotent, cursor catch-up) and
`comms_router.Intent` taxonomy are the only production-grade messaging
contracts in the workspace — treat them as the standard for future
agent-to-agent work instead of inventing new polling loops. Longer-term
convergence targets (not this quarter): the two approval systems
(`approval_server.py` vs agent-org `pending_store`/gates), the three
little-coder dispatch clients, and the file-drop IPC
(`state/follow-req-*.json`).

### J.6 Conventions (lands with I.4)
`/health` everywhere (+`/healthz` alias where it already exists); a one-page
port registry; discovery via env vars, not literals baked into source;
message-format notes (MCP vs REST vs MM-props). Adopt per-service as each is
touched — no big-bang.

---

## Execution order (updated)

| Phase | Parts | Risk | Depends on |
|---|---|---|---|
| 1. Security now | A.1 (rotate + 2 files + guard pattern), A.2 interim, A.3 labels+pin, A.4 rows | Low (surgical) | — |
| 2. Quick wins | B.1 (backup files, D-7 two-step), B.2, B.4 **with corrections**, B.5 skills catalog | Low | D-5, D-7 decisions |
| 3. Caller identity | J.1 (LiteLLM virtual keys → llm-queue lanes) | Low-medium (config + client keys) | — (can parallel 1–2) |
| 4. Docs truth pass | C (block moves 4–7 are one-afternoon `git mv` commits) | None | ideally after 2 |
| 5. Deploy plane | D.1–D.4 (incl. D-12 outcome) | Medium (container rule) | 2 (ollama retired) |
| 6. Shared foundations | E.1–E.3 / J.2, J.3 prune | Low-medium | D-9, D-13 |
| 7. Status-pipe consolidation | G.1 (+ A.2 durable, F.5, D-15) | Medium | 2, 4 helps |
| 8. Scripts reorg | G.2 (subsystem-preserving) | Medium (scheduled-task re-registration) | 7 |
| 9. OB1 submodule | H.1 | Low-medium | A.1 rotated; OB1 uncommitted work landed |
| 10. Orchestrator decomposition | F.1 (9 PRs, between gym rounds) | Medium per-PR | tests green in Docker |
| 11. little-coder split | H.2 | Medium | 1–2 |
| 12. Guardrails | I (gitleaks + hooks bootstrap first; CI after D-7 step 1) | Low | incremental from phase 1 |

Phases 1–4 remain a weekend-sized effort and remove most of the risk and the
confusion (the ~98-file doc archive is where the "clutter" feeling goes away).
Phase 3 is new and deliberately early: it is small and unlocks the
agent-communication end-state everything later builds on.

---

## Decisions needed (updated sign-off checklist)

- **D-1 (rescoped):** History scrub — the driver is now the two `.env.bak-*`
  commits (~25 live credentials each) plus the twice-committed gateway key.
  After rotation: scrub (rewrites all SHAs) or accept-and-document?
- **D-2:** Retire Watchtower for an explicit update runbook (case strengthened
  by the surrealdb floating-tag regression), or keep it pinned + labeled +
  narrowed? Fold the dormant `autonomous-updates-with-security/` design into
  this decision.
- **D-3: RESOLVED by J.1 recommendation** — add LiteLLM master_key + virtual
  keys (per-caller identity) rather than accepting the permissive posture.
  Sign off on that direction.
- **D-4:** Delete the 20 GB `data-backup/` March snapshot?
- **D-5: RESOLVED — LM Studio fully retired** (Audit ledger #10: the 0.11.0
  upgrade removed both OWUI connections; no LM Studio endpoint survives, and
  the live `.env` still pays a boot stall for the dead path). Sign off on the
  full archive: 5 scripts + `emergency_recovery.py:48` registration +
  entrypoint blocks + `quick-fixes.bat:476-513` + `.env` keys +
  `.env.example:52-56`. Also settles the fate of the stale
  `feature/enable-lm-studio-via-tailscale` branch (B.1).
- **D-6:** `.github/copilot-instructions.md` — regenerate from CLAUDE.md or
  delete?
- **D-7 (two-step):** (a) fast-forward/re-point the 15-months-stale `main` to
  the integration branch; (b) delete the 9 fully-merged branches; decide the 5
  unmerged stale ones individually.
- **D-8:** Submodule tiering (OB1 now → little-coder → agent-org deferred) —
  unchanged, still needs the green light.
- **D-9 (new):** mnemory's fate — recommit (fix the OWUI plugin URLs, keep the
  plane) or migrate remaining consumers to Open Brain and retire it properly?
- **D-10 (new):** `open_notebook` transition — it is "being retired… still
  live for podcasts". Decide the podcast plane's target (finish the ON
  retirement after podcast-on-demand settles, or keep ON long-term) so the
  aux plane stops being half-in/half-out.
- **D-11 (new):** `.claude/skills/` — keep the 15-dir byte-copy of
  `OB1/skills/` (and accept drift) or thin to native skills + pointer?
- **D-12 (new):** `stack-services.json` — wire it (generated from compose,
  loaded by scripts) or demote/delete it? (Blocks I.3.)
- **D-13 (new):** `lc-mcpo` — give it a real consumer or retire it?
- **D-14 (new):** `owui/actions/add_web_sources_to_knowledge.py` — deployed,
  but writes into the OWUI Knowledge layer retired on 2026-08-20. Retire the
  action?
- **D-15 (new):** `modules/emergency-recovery` — it IS reachable from OWUI
  (router keywords incl. `ollama`). Fix its content, or remove the routing
  branch + render whitelist and let `scripts/emergency-recovery.ps1` be the
  only recovery story? (Either way, correct the CLAUDE.md line.)


## Part K — NEW (2026-08-21): project-per-plane restructure ("ai-stack as chassis")

Operator direction (anomaly review #3, approved same day): the main stack's
monolithic compose project dissolves into self-contained compose projects —
one per service tree — exactly like `portal`, `OB1`, and `agent-org` already
are. The ai-stack repo remains the CHASSIS: shared networks, recovery/checks,
backups tree + NAS mirror, docs/registry, .env conventions.

### K.0 — Design decisions (locked with operator 2026-08-21)

1. **Network anchor:** the root `docker-compose.yml` (project `ai-stack`)
   becomes the platform anchor — it keeps ONLY the shared seam networks
   (`llm-net`, `app-net`, `default`) plus the transitional aux plane. Project
   name stays `ai-stack`, so every existing external reference
   (`ai-stack_llm-net`, `ai-stack_app-net`, `ai-stack_default` from OB1 /
   portal / agent-org) survives UNCHANGED. Plane-internal networks move with
   their plane and go native (`llm-backend-net` → inference,
   `search-net` → search, `lc-net` → coder).
2. **Layout:** one top-level dir per project (`inference/`, `frontend/`,
   `memory/`, `search/`, `coder/`), each holding `docker-compose.yml` with an
   explicit `name:` key — the portal precedent, kept consistent. Source trees
   (`little-coder/`, `search-gateway/`, `llm-queue/`, …) do NOT move today.
3. **.env:** single root `.env` stays. Plane projects are driven with
   `--env-file <root>/.env` (portal precedent) via a new
   `scripts/stack/stack.ps1` driver. Fail-loud guard: each plane compose marks
   one critical variable with `${VAR:?...}` interpolation so a bare
   `docker compose up` without the env file STOPS instead of silently
   starting with empty credentials.
4. **Volumes move with their plane** — new project-native volumes, data
   copied (operator's portal-split preference; no name-pinning).
   Container NAMES never change (DNS, watchers, NAS paths all hold).
5. **Backups sidecars follow their store's plane** (the OB1-adoption
   precedent): openwebui/tailscale → frontend; llm-gateway/lm-models →
   inference; mnemory → memory; little-coder → coder; open-notebook stays
   with aux in the root project. Artifact output paths under `./backups/`
   are invariant.
6. **open-terminal moves to the coder project** — it is the coder plane's
   executor (control-plane DECIDES / open-terminal EXECUTES) and the last
   `lc-net` member outside it; moving it makes `lc-net` fully plane-native.
7. **The notebook/aux trio moves into the OB1 project** (operator amendment,
   2026-08-21 mid-ladder): surrealdb + open_notebook + open-notebook-backup
   are tethered to OB1 on every axis (IKS canonical store = OB1 Postgres via
   obnet, wiki-vault volume mount, digest→podcast chain), so they join OB1 —
   same playbook as the backup-sidecar adoption. NO retirement implied: ON
   stays live until the wiki workbench matures (D-10 direction unchanged).
   Bonus: the root ai-stack project ends the ladder as a PURE network anchor.
8. **openwebui + tailscale stay one project** (`frontend`) — tailscale runs
   `network_mode: service:openwebui`; the netns coupling is physical.

### K.1–K.5 — The ladder (one plane per step, test gate between)

Order: inference → memory → search → coder → frontend (frontend last: the
netns + tailnet-serve restore path is the most delicate).

Per-plane playbook (portal-split procedure, now run 3×):
  a. Create `<plane>/docker-compose.yml` (`name: <plane>`); move service defs
     verbatim from `compose/<plane>.yml`; shared nets → `external: true` +
     `name: ai-stack_*`; plane nets native; binds re-pointed `../…`;
     output binds for backups stay `../backups/...`.
  b. Create plane volumes; stop old services; copy data (alpine `cp -a`);
     `up -d` under the new project; verify health + one functional probe.
  c. Same commit: root include/volume trims, recovery trio edits,
     stack-services.json project fields, stack-map + CONTAINER-REGISTRY +
     CLAUDE.md counts (container rule).
  d. Test gate before the next plane (per-plane probes listed in K ledger).

### K.6 — Chassis consolidation (after the ladder)

- Root compose = anchor networks + aux plane only; volumes section trimmed.
- `scripts/stack/stack.ps1` — single driver: `up|down|ps|logs [plane|all]`,
  dependency-ordered, always passes `--env-file`.
- emergency-recovery.ps1/.bat rewritten data-driven: an ordered project
  registry (compose path + services + health gate per project) replaces the
  monolithic service arrays.
- check-llm-gateway-routing.ps1 file lists re-pointed at the new layout.
- Docs sweep: stack-map, CONTAINER-REGISTRY, CLAUDE.md, copilot-instructions,
  runbooks (backup-conventions, UPDATE-MANAGEMENT).

### K ledger

- **K.1 inference — EXECUTED 2026-08-21.** `inference/docker-compose.yml`
  (8 services, `name: inference`, fail-loud `${LITELLM_DB_PASSWORD:?}`);
  llm-backend-net native; llm-net external; data copied to
  `inference_llm-gateway-db-data` / `inference_llm-queue-data`; old ai-stack_*
  volumes retained until the K.6 sweep. Cross-plane `depends_on` on
  llm-gateway removed from core/coder/memory (retry posture, like OB1).
  Recovery trio rewired (Start-/Stop-InferenceStack; granular per-service
  gates collapsed into the project's own depends_on; nuclear/GPU-reset paths
  reroute; watchdog Test-ServiceHealth got a cross-project name fallback).
  VERIFIED: all 8 healthy under project `inference`; /v1/models + a real
  completion via the `llama-cpp` alias from openwebui (HTTP 200, MTP
  drafting); llm-queue lane attribution intact (`key: owui-chat`). Root main
  stack = 18 default services.
- **K.2 memory — EXECUTED 2026-08-21.** `memory/docker-compose.yml`
  (3 services; `${MCP_API_KEY:?}` guard); mnemory-data copied to
  `memory_mnemory-data`; llm-net external, project-local default bridge.
  TRAP found: a fresh mnemory `--build` crash-loops (unpinned newer `mcp`
  package moved fastmcp) — pinned to the proven image as `mnemory:local`
  with a rebuild-deliberately comment. Recovery scripts: generic
  `Start-/Stop-PlaneStack` driver added (memory uses it; later planes will
  too). VERIFIED: all three healthy under project `memory`; cloud door
  :8060/health = ok from host; mnemory:8050 reachable from openwebui over
  llm-net. Root main stack = 15 default services.
- **K.3 search — EXECUTED 2026-08-21.** `search/docker-compose.yml`
  (4 services; `${MULLVAD_WG_PRIVATE_KEY:?}` guard); search-net native;
  `vpn` + `gateway` attach EXTERNALLY to ai-stack_default so the DNS names
  OB1 (FETCH_PROXY http://vpn:8888, research/podcast) and OWUI resolve keep
  working. No data volumes (redis in-memory by design). VERIFIED: all four
  healthy under project `search`; :8085/healthz ok; a REAL query returned
  Mullvad-egressed results; `gateway`/`vpn` resolve from openbrain-research.
  Root main stack = 11 default services.
