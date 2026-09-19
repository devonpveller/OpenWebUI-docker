# Findings — stack-layers `sl-colo-portal` (portal config colocation)

Worker: harness item `sl-colo-portal`, branch `work/sl-colo-portal`, base `development` b28cbc5.
Date: 2026-09-19. Everything below was checked against the file or command named.

The work itself: `config/{alerter,auth-notification-bridge,authelia,caddy,portal-cron,tripwire,tunnel-watcher,watcher}`
→ `portal/config/<same>`, pointers repointed. These are the things that turned up
*around* that work and are NOT part of the deliverable.

---

## F1 — MERGE HAZARD (operator action required): three gitignored files are left
## behind at the old path when this branch merges into the live checkout

`git mv` moved the whole directory in this worktree, so ignored files inside it came
along. **A merge does not do that.** A merge applies a tree change: tracked files are
deleted at `config/...` and created at `portal/config/...`. Untracked/ignored files
inside `config/*` stay exactly where they are.

Verified present in the operator's main checkout `D:\Open WebUI\ai-stack` (read-only `ls`,
2026-09-19 — none of these exist in this worktree, which is why the worktree render can
never surface the problem):

| Leftover after merge | Size | Why it matters |
|---|---|---|
| `config/authelia/users_database.yml` | 739 B | **The live user database with the password hash.** Two problems: (a) `integrity-tripwire` mounts `./config/authelia/users_database.yml`, which after the merge points at `portal/config/authelia/users_database.yml` — a path that will NOT exist, so on the next portal recreate Docker creates a *directory* there and the tripwire baseline breaks; (b) at the old path it is no longer covered by any ignore rule (the rule moved with the file, and `config/authelia/.gitignore` moved too), so a broad `git add` would stage a secret. |
| `config/alerter/credentials.json` | 0 B | Un-ignored at the old path after the merge. Zero-byte decoy — see F2. |
| `config/alerter/token.json` | 0 B | Same. |

`config/tripwire/baseline.sha256` is NOT on disk in the main checkout (state lives in the
`tripwire-data` volume), so there is nothing to move for it.

**Migration step for whoever merges this** (before the next `portal-on.ps1`):

```powershell
# from the main checkout, after the merge lands
Move-Item 'config\authelia\users_database.yml' 'portal\config\authelia\users_database.yml'
Remove-Item 'config\alerter\credentials.json','config\alerter\token.json'   # 0-byte decoys, see F2
Remove-Item 'config\authelia','config\alerter' -Recurse   # only if now empty
git check-ignore -v portal\config\authelia\users_database.yml   # must print the rule
git status --short                                               # must NOT list users_database.yml
```

Running containers are unaffected until they are recreated: a running bind mount is
resolved at create time and holds the old absolute host path.

## F2 — the zero-byte `credentials.json` / `token.json` decoys are still there

`config/alerter/{credentials,token}.json` in the main checkout are 0 bytes, dated
2026-08-21. The compose file mounts the REAL files from `../secrets/google/portal-alerter/`
*over* them (`portal/docker-compose.yml` lines 75-76 pre-move, 79-80 post-move), so they
are inert — but they are the exact trap already documented in
`documentation/notes/nas-backup-outage-2026-09-13.md` and `CLEANUP-PLAN.md:270`
("`config/alerter` cred duplication | still open"). The move is the natural moment to
delete them; I did not, because deleting operator files in the live checkout is not
mine to do and the anchor's out-of-scope list is explicit. Folded into F1's migration step.

## F3 — four relative doc links under `documentation/runbooks/` were ALREADY broken

`documentation/runbooks/monitoring-access.md` used `../config/...` from a file that sits in
`documentation/runbooks/`, so the link resolved to `documentation/config/...` — a directory
that has never existed. Pre-existing, on `development` before this branch:

- `monitoring-access.md:35` → `(../config/watcher/known-ips.txt)`
- `monitoring-access.md:120` → `(../config/tunnel-watcher/tunnel-watch.sh)`
- `monitoring-access.md:145` → `(../config/caddy/Caddyfile)`

Fixed in passing to `../../portal/config/...` (two levels up, then the new home) since the
lines were being edited anyway. Worth a sweep for the same `../` class elsewhere under
`documentation/runbooks/` — not done here.

## F4 — the portal compose header carried a false statement about networks/volumes

`portal/docker-compose.yml:10` said *"Networks/volumes are defined in the root file."*
They are defined at the bottom of that same file (`networks:` line 588, `volumes:` line
607, pre-move numbering). The only external network is `app-net` → `ai-stack_app-net`.
Corrected as part of this item (the anchor calls it out).

## F5 — `README.md`'s repo map has no `config/` row at all

The anchor's artifact list names "README.md repo map". There is nothing to repoint: the
table at `README.md:139` lists `docker-compose.yml`, the plane dirs, `owui/`, `scripts/`,
the service source trees, `agent-org/`, `OB1/`, `backup/`+`backups/`, two documentation
dirs and `CLEANUP-PLAN.md` — `config/` is not mentioned anywhere in README.md (verified:
the only `config` hit in the file is `git config core.hooksPath`). I did not invent a row;
`sl-readmes` (wave 4) owns the README rewrite and `sl-colo-inference` will empty `config/`
of the remaining five litellm/llama-swap entries, at which point the directory disappears
and a row would be wrong anyway.

## F6 — the anchor's goal says "nine config trees"; there are eight

`sl-colo-portal.json` `goal` says *"its nine config trees"*; the `artifact` field then lists
eight by name, and eight is what exists on disk. Eight moved. No ninth candidate exists —
the remainder of `config/` is `litellm/`, `litellm.config.yaml`, `litellm.ui.config.yaml`,
`llama-swap.config.yaml`, `chat-template.jinja`, all inference-owned and explicitly
out of scope.

## F7 — `ruff check .` has one PRE-EXISTING failure on `development`

```
E501 Line too long (103 > 100)  llm-queue\src\llm_queue\__init__.py:9:101
```
The line is a plan-store path in a module docstring, made long by the 2026-09-18 plan
migration that rewrote in-repo plan paths to `../documentation-plans-ai-stack/...`.
Untouched by this item (the file is not in my diff). `ruff check .` therefore exits 1 both
before and after this branch. CLAUDE.md describes the gate as "F + E9", which E501 is not
in, so the root `ruff.toml` is stricter than the documented gate — someone should reconcile
one with the other; not this item.

## F8 — `scripts/checks/test-quartz4-offline.ps1` mounts the Caddyfile for `caddy validate`

Not a defect, a note for the tester: that check (line 76) is the one place outside
`portal/` that reads a portal config file by path, and it is `${rootFwd}/portal/config/...`
after this change. It needs Docker and the quartz fixtures, so it is not run here; the
change is a single path literal and the file PSParser-parses.
