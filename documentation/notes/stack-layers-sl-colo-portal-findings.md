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
lines were being edited anyway.

**Completed on attempt 2** after the tester found the `notes/` half of the same class:
`documentation/notes/nas-backup-outage-2026-09-13.md` had three
`(../../config/alerter/alerter.ts#L…)` links (`:19`, `:106`, `:123`) that DID resolve before
the move and dangle after it, plus a live re-consent command at `:83`. All four repointed;
a dated "Path note" block was added at the top of that file instead of rewriting the two
prose lines (`:114`, `:116`) that deliberately describe what was on disk on 2026-09-13.
The remaining old-path hits under `documentation/archive/` and `CLEANUP-PLAN.md` are
history and stay as written (`CLEANUP-PLAN.md` is `sl-closeout`'s file).

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

---

## F9 — depth-coupled path inside a moved runtime file (the attempt-1 regression)

`portal/config/alerter/setup-token.ts:36` (as committed in attempt 1) read

```ts
const TOKEN_OUT_URL = new URL("../../secrets/google/portal-alerter/", import.meta.url);
```

`import.meta.url` is **the script's own location**, so the literal encodes the file's DEPTH
below the repo root. The move put the file one directory deeper and `../../` landed one
level short: `<root>/portal/secrets/google/portal-alerter/` instead of
`<root>/secrets/google/portal-alerter/`. Measured with Node, not reasoned:

```
../../    -> file:///.../wt-sl-colo-portal/portal/secrets/google/portal-alerter/   (wrong)
../../../ -> file:///.../wt-sl-colo-portal/secrets/google/portal-alerter/          (right)
```

Why it was worth failing the item over, and why no plan case could see it: `:144-146` (was `:136-138`) does
`Deno.mkdir(TOKEN_OUT_URL, {recursive:true})` then writes and prints `Wrote …` — it would
have CREATED the wrong directory and reported success, while `portal/docker-compose.yml`
kept mounting the repo-root `secrets/.../token.json`, so the alerter would keep reading the
old dead token with no error anywhere. And the new location is swept under `.gitignore`'s
`secrets/` rule, so a live OAuth refresh token would land somewhere invisible. This is a
documented operator procedure reached on the portal's worst day
(`documentation/runbooks/incident-response.md:101`, `backup-restore-runbook.md:253`,
`restore-from-snapshot.md:299`). None of the greps, the compose render, PSParser or ruff
touches TypeScript.

Fixed to `../../../`, with a `DEPTH-COUPLED` comment above the line so the next move sees
it, and the docstring at `:10` made explicit ("the REPO-ROOT … three levels up from this
file") instead of the bare relative path it used to name.

**Credit where it is due:** the tester found this (X6), not the author — and the author had
edited three *other* lines of that same file, so line 36 was read past, not unseen. That is
the whole argument for a tester who did not write the change.

## F10 — the location-relative sweep over every moved file (result: one hit, F9)

Prompted by F9: a `git mv` is only safe for a file whose paths are absolute, sibling-relative
or build-context-relative. Every one of the 23 tracked files under `portal/config/` was read
to the end and classified. The idiom grep is

```
git grep -n -E 'import\.meta\.url|__dirname|\$PSScriptRoot|\$\(dirname|dirname |readlink|realpath|BASH_SOURCE|\$0|\.\./' -- portal/config
```

which returns **four** lines after the fix (three before it — the fourth is the explanatory
comment the fix added), and reading each file to the end confirms the classification:

| File:line | Idiom | Class | Depth-safe? |
|---|---|---|---|
| `alerter/setup-token.ts:44` (was `:36`) | `new URL("../../../…", import.meta.url)` | **escapes its own tree** | **was NO — F9, fixed to `../../../`** |
| `alerter/setup-token.ts:36` (was `:35`) | `new URL("./credentials.json", import.meta.url)` | sibling ("next to me") | yes |
| `alerter/setup-token.ts:42` | the `DEPTH-COUPLED` comment the fix added, which quotes `"../../"` | comment, not a path | n/a |
| `alerter/alerter.ts:42-44` | `new URL(".", import.meta.url)` → `${SCRIPT_DIR}credentials.json`, `token.json` | sibling; runs INSIDE the container where the whole dir is `/app` | yes |

Everything else carries no location-relative path at all:

- **Container-absolute only** (unaffected by where the file lives on the host):
  `watcher/authelia-watch.sh` (`/data/known-ips.txt`, `/logs/…`, `/tmp/…`),
  `tripwire/integrity-tripwire.sh` (`/watch`, `/state`, `/tmp`; its `cd "$WATCH_DIR" && find .`
  is relative to a container-absolute dir), `auth-notification-bridge/bridge.sh`
  (`/data/notification.txt`), `tunnel-watcher/tunnel-watch.sh` (URLs only),
  `portal-cron/entrypoint.sh` (`/etc/portal-crontab` → `/tmp/crontab.rendered`),
  `authelia/configuration.yml` (`/config/users_database.yml`, `/data/…`),
  `caddy/Caddyfile` (`root * /srv/site`, `output file /data/caddy-access.log`),
  `alerter/alerter.ts`'s `/reports`, `/logs/…`, `/data/known-ips.txt`.
- **Build-context-relative**, and the context moved with the file: the only `COPY` in the four
  moved Dockerfiles, `portal-cron/Dockerfile:27` (`COPY --chmod=0555 entrypoint.sh …`).
- **No file includes**: every `import` in the Caddyfile is a *snippet* import
  (`tls_strong`, `security_headers`, `sanitize_proxy_headers`, `access_log`, `authelia_gate`,
  `wiki_app` — all defined in the same file at `:27,38,58,70,100,123`), not a path.
- **Absolute URL paths only** in `caddy/site/*.html` + `hub.css` (`/hub.css`, `/openwebui/`,
  `/notebook/`), served from `/srv/site`.
- **Data / template files** with no paths: `authelia/.gitignore`, `authelia/.healthcheck.env`,
  `authelia/users_database.yml.template`, `watcher/known-ips.txt`, `portal-cron/crontab`,
  `alerter/.env.example`.

The sweep is now a test case (T10) so the next colocation item inherits it rather than
rediscovering it.

## F11 — `portal/config/portal-cron/crontab` checks out CRLF (pre-existing, NOT fixed here)

Tester's class-3. No `.gitattributes` rule matches an extensionless `crontab`, so
`core.autocrlf=true` gives it CRLF on every Windows checkout, and it is bind-mounted into
`portal-cron`. Verified unchanged by this item: identical blob on both sides, zero CR bytes
in git, identical `git check-attr` output at the old and new path — and `portal-cron` has
been up six days with it. Now that the file lives in the plane's own tree, a one-line
`portal/config/portal-cron/crontab text eol=lf` in `.gitattributes` would close it. Left
alone deliberately: changing line-ending attributes is a separate, testable change and the
anchor's out-of-scope list forbids portal behaviour changes.
