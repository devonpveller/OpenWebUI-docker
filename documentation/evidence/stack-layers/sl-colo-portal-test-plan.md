# Test plan — `sl-colo-portal` (portal config colocation)

**Branch under test:** `work/sl-colo-portal` · **Base:** `development` b28cbc5 · **Written by:** the developer (who does not execute this plan).
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-colo-portal.json`
**Findings sink:** `documentation/notes/stack-layers-sl-colo-portal-findings.md` (read F1 before you start — it is a merge hazard, not a test case).

## What changed, in one line

`config/{alerter,auth-notification-bridge,authelia,caddy,portal-cron,tripwire,tunnel-watcher,watcher}`
moved to `portal/config/<same>`; the 19 bind-mount/build-context paths in
`portal/docker-compose.yml` went from `../config/...` to `./config/...`; four `.gitignore`
rules, three `SECURITY.md` pointers, eight runbook pointers, one check script and five
in-file self-references were repointed; the compose header's false
"networks/volumes are defined in the root file" line was corrected.

## Setup — read this first

1. **Do not start, stop, restart or recreate anything.** The portal is LIVE on this host
   (`caddy`, `authelia`, `cloudflared`, `authelia-watcher`, `authelia-notif-bridge`,
   `integrity-tripwire`, `portal-cron`, `portal-alerter`, `caddy-backup`, `authelia-backup`
   were all `Up 6 days` at 2026-09-19 10:14). `docker compose ... config` renders only; it
   never touches a container. No lease is required.
2. Provision your own worktree (`scripts/agent-harness/new-worktree.ps1`) and check out
   `work/sl-colo-portal` in it. Never run git in `D:\Open WebUI\ai-stack`.
3. **To read a file's content as committed, use `git show work/sl-colo-portal:<path>`** —
   e.g. `git show work/sl-colo-portal:portal/docker-compose.yml | head -20`. Do not read
   the operator's main checkout to judge this branch.
4. Expect **`portal/config/authelia/users_database.yml` to be ABSENT** in your worktree. It
   is gitignored (the live user database with a password hash) and has never existed in a
   worktree. It was equally absent at `config/authelia/users_database.yml` on `development`
   — verify that yourself in T1c rather than taking my word for it.
5. `<WT>` below means the absolute path of YOUR worktree.

---

## T1 — The portal project renders, and every moved bind source / build context exists on disk

*Anchor criterion 1: "the portal renders with every bind-mount source resolving to a path
that exists under `portal/config/`; a mount to a missing path FAILS".*

### T1a — render exits 0

```bash
cd "<WT>"
docker compose -f portal/docker-compose.yml --env-file .env.example --profile internet config > /dev/null
echo "exit=$?"
```

**PASS:** `exit=0`, no stderr. **FAIL:** anything else.

### T1b — every rendered bind source and build context exists

```bash
cd "<WT>"
docker compose -f portal/docker-compose.yml --env-file .env.example --profile internet config --format json > /tmp/after.json
python - <<'PY'
import json,io,os
d=json.load(io.open("/tmp/after.json",encoding="utf-8"))
miss=[]
for name,svc in sorted(d["services"].items()):
    ctx=svc.get("build",{}).get("context")
    if ctx:
        print(("OK  " if os.path.exists(ctx) else "MISS"),"build",name,ctx)
        if not os.path.exists(ctx): miss.append((name,"build",ctx))
    for v in svc.get("volumes",[]) or []:
        if v.get("type")=="bind":
            s=v["source"]
            print(("OK  " if os.path.exists(s) else "MISS"),"bind ",name,s)
            if not os.path.exists(s): miss.append((name,"bind",s))
print("MISSING:",len(miss))
for m in miss: print("   ",m)
PY
```

**PASS:** all **19** paths containing `\portal\config\` print `OK`, and the `MISSING` list is
exactly these six, no more and no fewer:

| Missing path | Why it is expected |
|---|---|
| `backups\authelia` | produced-artifact dir, gitignored, absent in every worktree |
| `backups\caddy` | same |
| `secrets\google\portal-alerter\credentials.json` | gitignored secret |
| `secrets\google\portal-alerter\token.json` | gitignored secret |
| `reports\portal-digest` | gitignored output dir (`/reports` in `.gitignore`) |
| `portal\config\authelia\users_database.yml` | gitignored live user DB — see T1c |

**FAIL:** any path under `portal\config\` prints `MISS` other than `users_database.yml`; or
a seventh entry appears in `MISSING`; or one of the six above is *not* missing (that would
mean a secret or artifact got committed).

### T1c — the `users_database.yml` absence is pre-existing, not caused by the move

```bash
cd "<WT>"
git show development:portal/docker-compose.yml 2>&1 | head -1      # expect: fatal / does not exist
git ls-tree development -- config/authelia/                         # the OLD tree listing
git ls-tree work/sl-colo-portal -- portal/config/authelia/          # the NEW tree listing
```

**PASS:** `development`'s `config/authelia/` lists `.gitignore`, `.healthcheck.env`,
`configuration.yml`, `users_database.yml.template` and **not** `users_database.yml`; the new
listing has the identical four names under `portal/config/authelia/`. The file was never
tracked, so the move cannot have lost it.
**FAIL:** the old tree contained `users_database.yml` and the new one does not.

---

## T2 — History is preserved through the move

*Anchor criterion 2: `git log --follow --oneline -- portal/config/caddy/Caddyfile | wc -l` > 1;
spot-check one file per moved tree.*

```bash
cd "<WT>"
for f in portal/config/caddy/Caddyfile \
         portal/config/alerter/alerter.ts \
         portal/config/authelia/configuration.yml \
         portal/config/watcher/authelia-watch.sh \
         portal/config/tripwire/integrity-tripwire.sh \
         portal/config/portal-cron/crontab \
         portal/config/tunnel-watcher/tunnel-watch.sh \
         portal/config/auth-notification-bridge/bridge.sh ; do
  printf '%-58s ' "$f"; git log --follow --oneline -- "$f" | wc -l
done
git show --stat --find-renames work/sl-colo-portal | grep -c '=>'
```

**PASS:** every one of the eight counts is **greater than 1** (one per moved tree), and the
commit's stat shows the moves as renames (`old => new`), not add+delete.
**FAIL:** any count is 0 or 1, or the stat shows separate `A`/`D` entries for a moved file.

---

## T3 — No live pointer to the old `config/<portal tree>` path survives

*Anchor criterion 3: an unbounded grep hits only `archive/`, `notes/` or `CLEANUP-PLAN.md`.*

The literal anchor grep matches the substring `config/caddy` inside the *correct* new
spellings `portal/config/caddy` and (in `portal/docker-compose.yml`) `./config/caddy` —
`./config/...` is correct there because compose resolves relative paths against the compose
file's own directory, which is `portal/`. So run the literal grep first to see everything,
then the discriminating one.

```bash
cd "<WT>"
# (a) literal anchor grep, everything it hits:
git grep -n -E "config/(alerter|auth-notification-bridge|authelia|caddy|portal-cron|tripwire|tunnel-watcher|watcher)" | cut -d: -f1 | sort | uniq -c

# (b) OLD-path hits only — excludes the "portal/config/" and "./config/" prefixes:
git grep -n -E "(^|[^./a-zA-Z_-])config/(alerter|auth-notification-bridge|authelia|caddy|portal-cron|tripwire|tunnel-watcher|watcher)" | cut -d: -f1 | sort | uniq -c

# (c) the backslash spelling:
git grep -n -E "config\\\\(alerter|auth-notification-bridge|authelia|caddy|portal-cron|tripwire|tunnel-watcher|watcher)"

# (d) prove (b)'s exclusion is not hiding a real miss — every "./config/<tree>" outside portal/:
git grep -n -E "\./config/(alerter|auth-notification-bridge|authelia|caddy|portal-cron|tripwire|tunnel-watcher|watcher)" -- . ':!portal' ':!documentation/archive' ':!scripts/archive'
```

**PASS:**
- (b) hits only `CLEANUP-PLAN.md` (3), `documentation/archive/**` (5 files, 41 lines) and
  `documentation/notes/nas-backup-outage-2026-09-13.md` (2) — all four in the leave-alone set.
  Expected totals as written: CLEANUP-PLAN.md 3, audit-plan… 3, integration-task-document.md 19,
  plan-internet-exposed-front-end.md 17, MERGE-PREP-quartz-4.md 1, TASKS-quartz-4-expansion.md 1,
  nas-backup-outage 2.
- (c) prints nothing.
- (d) prints only `documentation/notes/nas-backup-outage-2026-09-13.md` (leave-alone) and
  `documentation/runbooks/backup-restore-runbook.md:266`, which is *quoting* the compose
  line and so must read `./config/alerter:/app`.
- (a)'s extra hits over (b) are exactly `portal/docker-compose.yml` (19), `.gitignore` (4),
  `SECURITY.md` (3), the four runbooks, and files inside `portal/config/` — all new-path spellings.

**FAIL:** any hit in (b) or (c) outside `archive/`, `notes/`, `CLEANUP-PLAN.md`; or (d)
shows a root-level script or doc still reaching `./config/<portal tree>`.

**Also spot-read three repointed pointers and confirm the target exists:**

```bash
cd "<WT>"
git show work/sl-colo-portal:SECURITY.md | grep -n 'portal/config'
git show work/sl-colo-portal:documentation/runbooks/monitoring-access.md | grep -n 'portal/config'
git show work/sl-colo-portal:scripts/checks/test-quartz4-offline.ps1 | grep -n 'portal/config'
ls portal/config/caddy/Caddyfile portal/config/watcher/known-ips.txt portal/config/tunnel-watcher/tunnel-watch.sh
```

`monitoring-access.md`'s three markdown links must be `../../portal/config/...` (two levels
up from `documentation/runbooks/`). If they read `../portal/config/...` that is a FAIL — it
would resolve to `documentation/portal/config/...`. (The originals were `../config/...`,
i.e. already broken; see findings F3.)

---

## T4 — `.gitignore` moved with the files and still ignores the same four paths

*Not a separate anchor criterion — it is the part of criterion 3 that a grep cannot see. A
secret that silently stops being ignored is the worst outcome of this change.*

```bash
cd "<WT>"
# NEW paths must be ignored:
for p in portal/config/authelia/users_database.yml \
         portal/config/tripwire/baseline.sha256 \
         portal/config/alerter/credentials.json \
         portal/config/alerter/token.json ; do
  printf '%-48s => ' "$p"; git check-ignore -v "$p" || echo "NOT IGNORED"
done
# the nested ignore file travelled:
git show work/sl-colo-portal:portal/config/authelia/.gitignore
git cat-file -e work/sl-colo-portal:config/authelia/.gitignore 2>&1   # expect: does not exist
```

**PASS:** all four print a matching rule —
`portal/config/authelia/.gitignore:1` for `users_database.yml` (a relative rule, so it
travelled with the directory and needed no edit) and root `.gitignore:57/58/62` for the
other three. `portal/config/authelia/.gitignore` contains `users_database.yml`. There is no
`config/authelia/.gitignore` left on the branch.
**FAIL:** any of the four prints `NOT IGNORED`; or a rule still names the old `config/...`
path (which would ignore nothing, since that directory is gone on this branch).

**Baseline for comparison** (what the developer measured on `development` before the move):
`config/authelia/users_database.yml` → `config/authelia/.gitignore:1`;
`config/tripwire/baseline.sha256` → `.gitignore:57`;
`config/alerter/credentials.json` → `.gitignore:58`;
`config/alerter/token.json` → `.gitignore:62`. Re-derive it if you want:
`git stash` is not needed — `git checkout development -- .gitignore` in a THROWAWAY worktree.

---

## T5 — Normalized render differs from `development`'s only in bind source / build context paths

*Anchor criterion 4: "a changed service, network, volume name or image FAILS".*

```bash
cd "<WT>"
git worktree list                     # confirm you are NOT in the main checkout
mkdir -p /tmp/slcp && cd /tmp/slcp
# render the BASE from a detached copy so nothing on disk is mutated:
git -C "<WT>" archive development | (mkdir -p base && tar -x -C base)
```

Simpler and sufficient, if `git archive` is awkward on Windows: render the base by
checking out `development` into a **second throwaway worktree** and rendering there.
Either way you need two `--format json` renders: one from `development`, one from
`work/sl-colo-portal`.

```bash
docker compose -f <BASE>/portal/docker-compose.yml --env-file <BASE>/.env.example --profile internet config --format json > /tmp/slcp/before.json
docker compose -f "<WT>"/portal/docker-compose.yml --env-file "<WT>"/.env.example --profile internet config --format json > /tmp/slcp/after.json
python - <<'PY'
import json,io
a=json.load(io.open("/tmp/slcp/before.json",encoding="utf-8"))
b=json.load(io.open("/tmp/slcp/after.json",encoding="utf-8"))
def flat(o,p=""):
    if isinstance(o,dict):
        for k,v in o.items(): yield from flat(v,p+"/"+k)
    elif isinstance(o,list):
        for i,v in enumerate(o): yield from flat(v,p+f"[{i}]")
    else: yield p,o
fa,fb=dict(flat(a)),dict(flat(b))
print("added  :",[k for k in fb if k not in fa])
print("removed:",[k for k in fa if k not in fb])
d=[(k,fa[k],fb[k]) for k in fa if k in fb and fa[k]!=fb[k]]
print("changed leaves:",len(d))
print("changed keys  :",sorted({k.rsplit('/',1)[-1] for k,_,_ in d}))
for k,x,y in d: print("  ",k,"\n     -",x,"\n     +",y)
PY
```

Note: the two worktrees sit at different absolute paths, so **every** absolute path in the
render will differ, not just the config ones. Normalize the worktree roots out first (replace
each worktree's absolute prefix with a token) and then compare — otherwise you cannot tell a
real difference from a path-prefix difference.

**PASS:** `added` and `removed` are empty; `changed keys` is exactly `['context','source']`;
the changed leaves number **19** (5 `context` + 14 `source`), and each one differs only by
`\config\` → `\portal\config\`. Service names, network names (`edge-net`, `auth-net`,
`notify-net`, `app-net` → `ai-stack_app-net`), volume names (`caddy-data`, `caddy-config`,
`authelia-data`, `tripwire-data`), images, users, profiles, healthchecks and `depends_on` are
byte-identical.
**FAIL:** any added/removed key, any changed key other than `context`/`source`, or a
`source`/`context` change that is not the config-prefix rewrite.

Developer's measurement, for comparison (both renders from the SAME worktree, before and
after the edit, so paths were directly comparable): `changed leaves: 19`,
`changed keys: ['context','source']`, `added: [] removed: []`, and after replacing
`\config` / `\portal\config` with a single token the two JSONs were byte-identical.

---

## T6 — The portal lifecycle scripts parse and still resolve

*Anchor criterion 5.*

```powershell
Set-Location '<WT>'
foreach ($f in 'scripts\portal\portal-on.ps1','scripts\portal\portal-off.ps1',
               'scripts\portal\portal-status.ps1','scripts\portal\breach-killswitch.ps1',
               'scripts\portal\access-query.ps1','scripts\checks\test-quartz4-offline.ps1') {
  $errs=$null; $null=[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw $f),[ref]$errs)
  if ($errs.Count -eq 0) { "PARSE OK   $f" } else { "PARSE FAIL $f"; $errs | % { "   $($_.Message)" } }
}
powershell -NoProfile -ExecutionPolicy Bypass -File 'scripts\checks\check-project-configs.ps1'; "EXIT=$LASTEXITCODE"
ruff check .   # see below
```

**PASS:**
- all six parse clean;
- `check-project-configs.ps1` exits 0 and prints "all 7 compose projects render clean" +
  "stack-services.json inventory matches the compose configs";
- `portal-on.ps1` / `portal-off.ps1` contain **no** `config/` path at all — verify with
  `git grep -n "config" -- scripts/portal/`, which should return only
  `portal-off.ps1:32` ("Volumes and configuration are preserved."). They pass
  `-f <projectRoot>\portal\docker-compose.yml`, and compose resolves the file's relative
  paths against `portal/`, which is exactly why the new `./config/...` spelling is correct
  and why these two scripts needed no edit. **If you think they should have been edited,
  say so — that is the developer's reasoning, test it, don't accept it.**
- `ruff check .` reports exactly **one** error, `E501` in
  `llm-queue/src/llm_queue/__init__.py:9` — **pre-existing on `development`** and not in this
  diff. Confirm that by running `ruff check .` in a `development` worktree too. If ruff
  reports anything else, FAIL.

---

## T7 — The pre-commit hooks pass on the committed work

*Anchor criterion 5, second half.*

```bash
cd "<WT>"
git log -1 --format='%H %s' work/sl-colo-portal
git config core.hooksPath                    # expect .githooks
# replay the hook against the committed tree without creating a commit:
git checkout -b verify/sl-colo-portal work/sl-colo-portal
git reset --soft HEAD~1                      # re-stage the same change set
.githooks/pre-commit ; echo "hook exit=$?"
git reset --hard work/sl-colo-portal ; git checkout - ; git branch -D verify/sl-colo-portal
```

**PASS:** hook exit 0 — secret guard, LF check, gateway-routing check and the compose/ps1
structural check all pass. The developer committed WITH hooks enabled and never used
`--no-verify`; verify that too: `git log -1 --format=%H` and confirm the commit exists with
the moves as renames (T2).
**FAIL:** a non-zero hook exit, or evidence of `--no-verify`.

---

## T8 — The live portal is untouched

*Anchor criterion 6.*

```bash
docker ps --format '{{.Names}}\t{{.Status}}\t{{.CreatedAt}}' | sort > /tmp/ps-before.txt
# ... run every case above ...
docker ps --format '{{.Names}}\t{{.Status}}\t{{.CreatedAt}}' | sort > /tmp/ps-after.txt
diff /tmp/ps-before.txt /tmp/ps-after.txt && echo "IDENTICAL"
```

Take `ps-before.txt` as your FIRST action and `ps-after.txt` as your LAST.

**PASS:** the only differences are the monotonic `Up N …` uptime strings; `CreatedAt` is
identical for every container, and the ten portal containers (`caddy`, `authelia`,
`cloudflared`, `portal-alerter`, `authelia-watcher`, `authelia-notif-bridge`,
`integrity-tripwire`, `portal-cron`, `caddy-backup`, `authelia-backup`) are all still
present with the same `CreatedAt` and a health status no worse than before.
**FAIL:** any container missing, restarted (`CreatedAt` changed, or `Up` reset to seconds/
minutes), or newly unhealthy.

Developer's baseline at 2026-09-19 10:14, before any of this work: all ten portal containers
`Up 6 days`, `caddy` and `authelia` `(healthy)`.

**Nothing in this plan starts, stops or recreates a container.** `docker compose config`
reads files only. If any step would require `up`, `down`, `restart`, `build` or `pull` —
stop and report it instead of running it.

---

## T9 — The compose header no longer lies

*Anchor artifact: "the portal compose header (which also wrongly says networks/volumes live
in the root file — correct it in passing)".*

```bash
cd "<WT>"
git show work/sl-colo-portal:portal/docker-compose.yml | sed -n '1,15p'
git show work/sl-colo-portal:portal/docker-compose.yml | grep -n '^networks:\|^volumes:\|external: true\|ai-stack_app-net'
```

**PASS:** the header no longer contains "Networks/volumes are defined in the root file"; it
states that networks and volumes are defined at the bottom of that file and that `app-net`
(`ai-stack_app-net`) is the only external one — and `grep` confirms `networks:` at line 592,
`volumes:` at line 611, with `external: true` / `name: ai-stack_app-net` present. It also
explains which mounts are `./` (the plane's own config) and which stay `../` (`secrets/`,
`backup/`, `backups/`, `reports/`).
**FAIL:** the false claim survives, or the new claim is itself wrong.

---

## What is NOT tested here, and why

- **Portal behaviour.** Nothing is started. That the portal still authenticates, tunnels and
  alerts is only provable by the operator's next deliberate `portal-on.ps1`, which is a
  deploy, not a test.
- **`caddy validate` / the tripwire baseline.** `scripts/checks/test-quartz4-offline.ps1`
  would run `caddy validate` against the moved Caddyfile, but it needs the quartz fixtures
  and a Docker pull. The change there is a single path literal (T3 reads it) and the file
  parses (T6).
- **The merge-time leftovers.** Findings F1 describes three gitignored files that a merge
  will strand at the old path in the operator's live checkout — including the live
  `users_database.yml`. That is a migration step for the merger, not a test case, and it
  cannot be observed from a worktree. **Read F1 and make sure the reviewer sees it.**
