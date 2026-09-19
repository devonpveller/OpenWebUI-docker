# Test plan - `sl-readmes`

**Item:** `sl-readmes` (stack-layers wave 4). **Branch:** `work/sl-readmes`,
rebased onto `development` at **`55ea48b`** (`sl-compose-anchors`); first
written against `f291cb3`. **Developer:** `wt-sl-readmes`.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-readmes.json`.
**Findings sink:** `documentation/notes/stack-layers-sl-readmes-findings.md`.

This is a **documentation** item. There is no RED-to-GREEN repro to run and
none is claimed. What it owes instead is *verification against the source of
truth*: every path, command, port, count, default and interval checked against
the file it describes, and every command's described effect checked against the
script that implements it. That is what these cases do, and T2 and T3 are
deliberately **unbounded** - executing the listed spot-checks is the floor, not
the ceiling.

## Before you start

**Read the blob, not the working tree.** `core.autocrlf` makes every text file
CRLF locally and the developer's worktree may hold uncommitted edits. Score the
branch against the branch:

```bash
git -C "<worktree>" show work/sl-readmes:<path>
```

Every `<worktree>` below is a checkout of `work/sl-readmes`. Your own harness
worktree is fine; you do **not** need the developer's.

**Never print a `.env` value.** `docker compose config` interpolates secrets in
plaintext - `grep` the section you need, or use `config --services` /
`config -q`, both of which are used below and neither of which prints values.

**Leases.** T1, T2, T3, T4, T6, T7, T8 and T9 are read-only: file reads, greps,
`config` renders and `--dry-run`. **No lease is needed for them.** T5 starts
containers, but in its own labelled project on a non-prod port and a private
network - it never touches the `frontend` project, and so needs no `frontend`
lease. If you would rather hold one anyway, that is not wrong.

**Two things you must NOT do, and the case that would tempt you:**

- do not run `docker compose -f frontend/docker-compose.yml up` without `-p`
  and the override from T5 - the file declares `name: frontend` and
  `container_name: openwebui`, both of which are the live deployment's;
- do not run the ANCHOR line that `stack.py up --dry-run` prints
  (`docker compose -f docker-compose.yml up -d`). It carries no `-p`, so the
  project name comes from your clone's DIRECTORY name, and on this host that
  can address the live `ai-stack_*` networks. T5 explains why the proof does
  not need it.

---

## T1 - criterion 1, the three new plane READMEs have all six sections

`frontend/README.md`, `inference/README.md` and `portal/README.md` must each
carry: **what starts** (services with container names), **requires** (other
planes, with the alias or URL that makes it so), **surfaces**, **host
requirements and keys**, **first run** (the exact driver commands), and **where
the live state is**. A missing section FAILS.

Run, from a checkout of the branch:

```bash
for f in frontend inference portal; do
  echo "== $f"
  git show work/sl-readmes:$f/README.md | grep -n '^## '
done
```

**Expect** each file's H2 list to contain, in some order, headings that are
plainly these six. As committed they are: `What starts`, `Requires`,
`Surfaces`, `Host requirements and keys`, `First run`, `Where the live state
is` (plus `Gotchas` and `Changing this plane`, which are extra, not required).

Then confirm each section is *populated*, not just present:

- **what starts** - every row names a container name. Cross-check the set
  against the render:
  ```bash
  docker compose -f frontend/docker-compose.yml --env-file frontend/.env.example --profile gpu --profile tailscale config --services
  docker compose -f inference/docker-compose.yml --env-file inference/.env.example --profile local config --services
  docker compose -f portal/docker-compose.yml    --env-file portal/.env.example    --profile internet config --services
  ```
  (`--env-file <example>` is used ONLY here, for a render that is reproducible
  on any machine; it is not an instruction any of these READMEs gives.)
- **requires** - each names an alias or URL, not just a plane name.
- **first run** - contains driver commands, which T3 then checks the effect of.

**FAILS if** any of the six headings is absent from any of the three files, or
if a section exists as a heading with no content answering it - e.g. a
"Requires" that lists planes without saying what makes the dependency real.

---

## T2 - criterion 2, every stated fact resolves (UNBOUNDED)

**Method:** the 2026-09-19 audit method. For every path, command, port, service
count, default, interval and file name in a touched file, open the file it
describes and check. List each claim with its evidence. **A single false claim
FAILS the item.** Do not stop at the spot-checks below - they are the ones most
likely to be wrong, not the whole set.

Touched files, in full:

```bash
git -C "<worktree>" diff --name-only development...work/sl-readmes
```

**Every markdown link must resolve.** This finds a broken relative link in any
of them:

```bash
git -C "<worktree>" checkout work/sl-readmes
cd "<worktree>"
python - <<'PY'
import re, pathlib
files = ["README.md","CLAUDE.md","frontend/README.md","inference/README.md",
         "portal/README.md","search/README.md","coder/README.md","memory/README.md",
         ".claude/skills/stack-map/references/workspace-stacks.md",
         ".claude/skills/stack-map/SKILL.md",".claude/skills/stack-map/README.md",
         "documentation/runbooks/SERVICE-LIFECYCLE.md"]
bad = []
for f in files:
    base = pathlib.Path(f).parent
    for m in re.finditer(r"\]\(([^)#:]+?)(?:#[^)]*)?\)", pathlib.Path(f).read_text(encoding="utf-8")):
        t = m.group(1).strip()
        if t.startswith(("http", "mailto")):
            continue
        if not (base / t).exists():
            bad.append((f, t))
for b in bad:
    print("MISSING", *b)
print("checked", len(files), "files;", len(bad), "broken")
PY
```

**Expect exactly 2 broken, and both must be these two:**

```
MISSING search/README.md ../../documentation-plans-ai-stack/implementation-guide/web-search/guide-Private-Search-Gateway.md
MISSING coder/README.md  ../../documentation-plans-ai-stack/implementation-guide/little-coder/Self-improving-little-coder-design.md
```

Those are **correct links read from the wrong place**, not defects. `../../`
from `search/README.md` is the parent of the REPO ROOT, which is
`D:\Open WebUI\` in the main checkout and `.claude\worktrees\` in yours.
Confirm both targets exist rather than assuming:

```bash
ls "D:/Open WebUI/documentation-plans-ai-stack/implementation-guide/web-search/guide-Private-Search-Gateway.md"
ls "D:/Open WebUI/documentation-plans-ai-stack/implementation-guide/little-coder/Self-improving-little-coder-design.md"
```

Both are pre-existing and untouched by this item (`git diff
development...work/sl-readmes -- search/README.md coder/README.md` does not
contain either line). **Any OTHER broken link FAILS** - in particular the
stack-map reference sits four directories deep
(`.claude/skills/stack-map/references/`), so a link from it to the repo root
needs `../../../../`; this item corrected three that had three.

Spot-checks whose numbers are the easiest to get wrong - each with the command
that settles it:

| Claim | Where it is claimed | Command |
|---|---|---|
| inference renders 4 without a profile, 8 with `local` | `inference/README.md`, CLAUDE.md | `docker compose -f inference/docker-compose.yml --env-file inference/.env.example config --services \| wc -l` then again with `--profile local` |
| frontend renders 1 / 2 / 2 / 4 across none / stock / gpu / gpu+tailscale | `frontend/README.md` | see the note below - the **none** row is the one that will fool you |
| portal renders 10 without a profile, 12 with `internet` | `portal/README.md`, stack-map | `docker compose -f portal/docker-compose.yml --env-file portal/.env.example config --services \| wc -l`, then with `--profile internet` |
| only `cloudflared` and `tunnel-watcher` carry a `profiles:` key in the portal | `portal/README.md`, stack-map | `grep -n -B8 "profiles: \[internet\]" portal/docker-compose.yml` |
| OB1 is 30 containers, 29 against the pinned gitlink | CLAUDE.md, stack-map | `docker compose -f OB1/docker/docker-compose.yml config --services \| wc -l`; then with all four profiles; and `git ls-tree HEAD OB1` |
| `health` is 15 probes | README.md, CLAUDE.md, the plane READMEs | `grep -c '"' scripts/stack/test_stack.py` is NOT the check - read `PS1_PROBES` in `scripts/stack/test_stack.py` and count its entries |
| 8 tailnet serve routes = 7 route-table rows + the root OWUI serve | `frontend/README.md` | `sed -n '/^routes()/,/^EOF/p' frontend/entrypoint.sh` (count the non-comment rows), and `grep -n 'ts serve --https=443 --bg http://127.0.0.1:8080' frontend/entrypoint.sh` |
| backup interval defaults (86400 / 604800 / crons) | README.md backup table | `grep -n "BACKUP_INTERVAL\|BACKUP_CRON" <each plane compose>` and `OB1/docker/docker-compose.yml` |
| `llm-gateway-backup`'s 86400 is hard-coded, not a variable | README.md, `inference/README.md` | `grep -n "entrypoint" -A1 inference/compose/backups.yml` |
| NAS mirror is Sundays 04:00; weekly maintenance Sundays 03:15 | README.md | `grep -n "New-ScheduledTaskTrigger" scripts/backup/install-nas-backup-task.ps1 scripts/maintenance/weekly-maintenance.ps1` |
| the watchdog's 60-second loop | README.md | `grep -n "IntervalSeconds" scripts/checks/stack-watchdog.ps1` |
| `$Script:<Plane>Services` variables exist and `$MainStackServices` is empty | stack-map section 4 | `grep -n '^\$Script:.*Services' scripts/recovery/emergency-recovery.ps1` |
| the portal has no rows in the generated inventory | `portal/README.md`, findings 1.2 | a `python -c` over `scripts/lib/stack-services.json` filtering `project == "portal"` |
| `restore-from-snapshot.ps1` has `caddy` and `authelia` catalog entries | `portal/README.md` | `grep -n "^  'caddy'\|^  'authelia'" scripts/backup/restore-from-snapshot.ps1` |
| the portal hardening table: 12 / 11 / 10 / 10 for cap_drop+no-new-privileges, read_only, non-root user, cpus+memory+pids | `portal/README.md` | render and read the keys back, **do not read the file** - see the note below the table |
| the `x-` extension fields each plane README describes | `frontend/`, `inference/`, `portal/README.md` | `git grep -l '^x-[a-z-]*: &' -- '*.yml'` (expect 10 files) and `sed -n '/^x-/,/^services:/p' <file>` for the contents |

**The portal hardening table must be read from the RENDER, not the file.**
`sl-compose-anchors` moved that floor into `x-hardening` / `x-hardening-ro`
merge keys, so grepping `portal/docker-compose.yml` for `read_only` finds one
occurrence covering eleven services. This is the command:

```bash
python -c "
import json,subprocess
a=['docker','compose','-f','portal/docker-compose.yml','--env-file','portal/.env.example','--profile','internet','config','--format','json']
d=json.loads(subprocess.run(a,capture_output=True,text=True).stdout)
for n,s in sorted(d['services'].items()):
    lim=s.get('deploy',{}).get('resources',{}).get('limits',{})
    print(n, s.get('user','-'), s.get('read_only','-'), s.get('cap_drop','-'), sorted(lim) or '-')
"
```

**Expect** `portal-init` as the only one with no `read_only` and with
`user=0:0`; `portal-cron` with no `user` at all; `caddy-backup` and
`authelia-backup` with `pids` only; all twelve with `cap_drop: ['ALL']`.
The README's table says exactly that - **the developer's first version of this
sentence got it wrong in three ways**, written from an excerpt instead of the
render, so check it rather than reading it.

**The frontend "no profile" row needs a stripped env file, and this is the trap
the developer fell into first.** `frontend/.env.example` ships
`COMPOSE_PROFILES=stock`, and compose READS that from whatever `--env-file` you
pass - so `--env-file frontend/.env.example` with no `--profile` flag renders
**2** services (the stock pair), not the 1 the README's table claims for an
EMPTY profile set. Both numbers are right; they answer different questions.
To check the README's row, strip the assignment first:

```bash
grep -v '^COMPOSE_PROFILES' frontend/.env.example > /tmp/fe-noprofile.env
docker compose -f frontend/docker-compose.yml --env-file /tmp/fe-noprofile.env config --services
```

**Expect** exactly `openwebui-backup` - one service, which is the README's
"a misconfigured `.env` looks like this" row. Then:

```bash
for p in stock gpu; do
  echo -n "$p: "
  docker compose -f frontend/docker-compose.yml --env-file frontend/.env.example \
    --profile $p config --services | wc -l
done
docker compose -f frontend/docker-compose.yml --env-file frontend/.env.example \
  --profile gpu --profile tailscale config --services | wc -l
```

**Expect** 2, 2, 4. (Passing `--profile stock` alongside the file's own `stock`
is harmless; a CLI `--profile` REPLACES the file's value, and here they agree.)

**FAILS if** any number, path, port, default or interval in a touched file
disagrees with the file it describes. Record the claim, the command and the
answer for each - a pass here is the list, not the verdict.

---

## T3 - criterion 3, every command's DESCRIBED EFFECT matches the script, read to the end

For every command any touched README tells a reader to run, open the script
that implements it and read the whole body. A described effect the script does
not have FAILS. **Do not describe a script's effect from its name or its
header comment** - three consecutive attempts on an earlier item got this
wrong by stopping at a comment.

Enumerate them:

```bash
git -C "<worktree>" show work/sl-readmes:README.md | grep -nE '^\s*(python|\.\\|docker|git)'
# and the same for frontend/, inference/, portal/, search/, coder/, memory/ READMEs
```

The claims to settle, and where the answer is:

1. **`stack.py init` with no arguments enables the frontend and nothing else,
   and refuses on a blank key.** Read `cmd_init` AND `DEFAULT_ENABLED` in
   `scripts/stack/stack.py`, to the end of the function - note the `--product`
   branch takes a different path from the default one.
2. **`stack.py up` with no plane starts the ENABLED set, `--all` starts every
   non-`manual` plane, and a plane name starts exactly that plane.** Read
   `select_planes` and `cmd_up`, including `_requires_note` and
   `_manual_notes`.
3. **`stack.py up portal` starts nothing and prints a pointer;
   `restart portal` refuses.** Read the `manual` branches in `cmd_up`,
   `cmd_down` and `cmd_restart`. Verify live - it is read-only:
   ```bash
   python scripts/stack/stack.py up portal
   python scripts/stack/stack.py restart portal; echo "rc=$?"
   ```
   **Expect** the pointer line naming `portal-on.ps1 / portal-off.ps1`, no
   docker command, and `rc=1` for the restart.
4. **`stack.ps1` forwards nine verbs and NOT `enable` / `disable` / `init`.**
   Read the `ValidateSet` in `scripts/stack/stack.ps1` and the `switch` below
   it. The READMEs and CLAUDE.md say this; if the set differs, FAIL.
5. **`stack.py health` exit code is the NUMBER of failed probes**, and a
   failing probe does not stop the sweep. Read `HealthSweep.probe` and
   `cmd_health`.
6. **`portal-on.ps1` pre-flights `portal/.env` against
   `[planes.portal] keys` in the manifest, then brings services up in five
   groups (six in production), and test mode passes NO `--profile`.** Read
   `scripts/portal/portal-on.ps1` to the end - the pre-flight block, the
   `$groups` array, the `if (-not $Test)` that appends the tunnel group, and
   the `-Test` branch that appends the override file.
   `portal/README.md` describes exactly this; its header comment does not, and
   the README says so. Safe live check:
   ```powershell
   .\scripts\portal\portal-on.ps1 -WhatIf
   ```
   `-WhatIf` is supported (`[CmdletBinding(SupportsShouldProcess=$true)]` +
   `$PSCmdlet.ShouldProcess` around every `docker compose`), which is WHY this
   case may run it. **Expect** printed `docker compose ... up -d <services>`
   lines and no container started (`docker ps` unchanged). If your
   `portal/.env` is absent or a key is blank it will REFUSE before printing
   anything - that is also a pass for this claim, record which you saw.
7. **`restore-from-snapshot.ps1` has MANDATORY `-SnapshotRoot` and `-Date`, and
   plans unless `-Apply`.** Read its `param(...)` block.
   **Do not run it.** `Mandatory = $true` on both, `[switch]$Apply` defaulting
   false, is the whole claim.
8. **`enable` refuses on an unmet `requires` and on a blank key, naming the
   remedy.** Exercised live in T5's clone, not here.

**FAILS if** any README sentence describes an effect the code does not have -
including a right answer attached to a wrong mechanism.

---

## T4 - criterion 4, the root README's product table equals the manifest

Diff the two mechanically rather than by eye. From a checkout:

```bash
python - <<'PY'
import tomllib
m = tomllib.load(open('stack.manifest.toml','rb'))
planes, products = m['planes'], m['products']
def closure(names):
    out, stack = set(), list(names)
    while stack:
        n = stack.pop()
        if n in out: continue
        out.add(n); stack += planes[n].get('requires', [])
    return out
order = list(planes)
for name, p in products.items():
    declared = list(p.get('planes', []))
    surf = p.get('surfaces', {}) or {}
    allp = declared + [k for k in surf if k not in declared]
    full = [x for x in order if x in closure(allp)]
    keys = []
    for pl in full:
        for k in planes[pl].get('keys', []):
            if k not in keys: keys.append(k)
    print(f"{name}\n  up      : {', '.join(full)}"
          f"\n  profiles: {p.get('profiles', {})}"
          f"\n  surfaces: {surf}\n  keys    : {', '.join(keys)}\n")
PY
```

Compare, row by row, against the product table in `README.md`:

- **the set of product names must be identical** - a product in one and not the
  other FAILS;
- the "Starts (planes, in order)" cell must equal the `up` line;
- the "Profiles it turns on" cell must equal the `profiles` line;
- the "Surfaces" cell must equal the `surfaces` line;
- the "Keys it will ask for" cell must be the `keys` line (the README writes
  `+ X` where a row inherits the row above's keys - expand that before
  comparing);
- the "host" cell must be the union of `planes[p]['host']` over the `up` set.

Cross-check the same names against the driver's own output:

```bash
python scripts/stack/stack.py list
```

**Expect** its `products:` block to list exactly the ten the README's table
does, with matching descriptions.

**FAILS if** a product is listed in one and not the other, or a row's planes,
profiles or surfaces differ from the manifest's.

---

## T5 - criterion 5, the quickstart works in a scratch clone and starts nothing else

**This case must not touch the live frontend.** The constraints below are the
reason it does not; each has a specific failure it prevents (findings 4.1-4.3).

Set up a scratch clone at a SHORT path (deep paths break on Windows):

```bash
mkdir -p /d/qs-tester && cd /d/qs-tester
git -c core.longpaths=true clone --no-recurse-submodules \
    --branch work/sl-readmes "D:/Open WebUI/ai-stack" qs
cd /d/qs-tester/qs
```

Now follow the README's quickstart **exactly as written**, with the two
substitutions the constraints require:

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env     # ships COMPOSE_PROFILES=stock
grep -n '^COMPOSE_PROFILES\|^WEBUI_SECRET_KEY' frontend/.env   # names only; do not print other values
python scripts/stack/stack.py init
```

**Expect** `wrote .stack/state.json` followed by the `list` output with
`frontend  enabled` and `up would start: anchor, frontend`. If `init` refuses,
the quickstart needs an undocumented step - FAIL.

```bash
python scripts/stack/stack.py up --dry-run
```

**Expect exactly two lines**, the anchor's and the frontend's.
**Do not run the first one** (findings 4.3): it has no `-p`, so its project
name is this directory's, and it would address the live `ai-stack_*` networks.
The proof does not need it - the `stock` profile uses only the project-local
`owui-net`, and compose does not require an unused external network to exist,
which this case then demonstrates.

Write the test overlay (non-prod names, non-prod port, owner label - compose
v5.3.0 has no `--label` flag, findings 4.1):

```bash
cat > qs.override.yml <<'YML'
services:
  openwebui-stock:
    container_name: qs-openwebui
    ports: !override
      - "127.0.0.1:13000:8080"
    labels:
      ai-stack.harness.owner: wt-sl-readmes
  openwebui-backup:
    container_name: qs-openwebui-backup
    labels:
      ai-stack.harness.owner: wt-sl-readmes
YML
```

Render first, and check the isolation BEFORE starting anything:

```bash
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml config --services
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml config \
  | grep -nE 'container_name|published|^  name:|ai-stack_'
```

**Expect** exactly `openwebui-stock` and `openwebui-backup`; container names
`qs-openwebui` and `qs-openwebui-backup`; published `13000`; network
`wt-sl-readmes-qs_owui-net`; volume `wt-sl-readmes-qs_openwebui-data`; and **no
`ai-stack_` string anywhere in the render**. If an `ai-stack_` network appears,
**stop** - do not run `up`, and report it.

```bash
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml up -d
curl -sS -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:13000/health
curl -sS http://127.0.0.1:13000/health; echo
docker ps --filter "label=ai-stack.harness.owner=wt-sl-readmes" --format "{{.Names}} {{.Status}}"
```

**Expect** `HTTP 200`, the body `{"status":true}`, and exactly two containers -
`qs-openwebui` healthy and `qs-openwebui-backup` up. No GPU is used; no other
plane is started.

Confirm the live frontend is untouched:

```bash
docker ps --format "{{.Names}}" | grep -x openwebui   # still there, still the live one
docker inspect openwebui --format '{{.Config.Labels}}' | grep -c harness.owner   # expect 0
```

Tear down and leave nothing:

```bash
docker compose -p wt-sl-readmes-qs -f frontend/docker-compose.yml -f qs.override.yml down -v
docker ps -a --filter "label=ai-stack.harness.owner=wt-sl-readmes" --format "{{.Names}}"   # empty
```

While you are in that clone, exercise the "add one thing" section's documented
refusals (read-only - `enable` writes only `.stack/state.json` in the clone):

```bash
python scripts/stack/stack.py enable memory
python scripts/stack/stack.py enable research
```

**Expect** the requires-refusal naming `python scripts/stack/stack.py enable
inference`, and the key refusal listing eight keys across `inference/.env`,
`search/.env` and `OB1/docker/.env` - which is what `README.md`'s "Add one
thing" promises.

**FAILS if** the quickstart needs a step the README does not give; if OWUI does
not answer `/health` with 200; if more than the two containers start; or if
anything in the render or the run names an `ai-stack_*` network.

---

## T6 - criterion 6, nothing true was silently dropped

Diff the previous version of each rewritten file against the new one and check
every REMOVED statement. A removed claim must be **false**, **moved** (say
where), or **recorded in the sink**.

```bash
git -C "<worktree>" diff development...work/sl-readmes -- README.md CLAUDE.md \
    .claude/skills/stack-map/references/workspace-stacks.md \
    .claude/skills/stack-map/SKILL.md .claude/skills/stack-map/README.md \
    search/README.md coder/README.md memory/README.md \
    documentation/runbooks/SERVICE-LIFECYCLE.md
```

Work through the `-` lines. For each claim that is gone, find its disposition
in **section 5 of `documentation/notes/stack-layers-sl-readmes-findings.md`**,
which is the developer's ledger of exactly this. Then do the harder half:
**check that the ledger is complete** - a removed claim that is NOT in the
ledger and NOT obviously carried over elsewhere is the failure this case
exists for.

Two dispositions in that ledger assert a fact; verify them rather than
accepting them:

- "`entrypoint.sh` is 321 lines, not 318" - `wc -l frontend/entrypoint.sh`;
- "the hook has eleven numbered blocks, so '10 checks' was unreliable" -
  `grep -n "^# --- [0-9]" .githooks/pre-commit`.

**FAILS if** a statement was removed that is true, still relevant, and appears
neither in the new documents nor in the sink.

---

## T7 - criterion 7, the repo's own gates pass

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\plan-store.ps1 `
  -Store "D:\Open WebUI\documentation-plans-ai-stack"
echo "rc=$LASTEXITCODE"
```

**`-Store` is required from a worktree and this is not a workaround for a
failing check** - see finding 5b. The default is
`Join-Path (Split-Path $Root -Parent) 'documentation-plans-ai-stack'`, and
`$Root` in a worktree is the worktree, so the bare form looks for the store
under `.claude/worktrees/` and throws. The failure is loud, never a false
green. Run the bare form first if you want to see it, then the flagged one.

**Expect** exit 0 and `plan store: clean (versioned, pushed, indexed)`, with
"Tracked feature directories here (plan store Phase-2 backlog): 3" (the two
kept directories plus the index - the expected standing state) and "No
untracked paths under implementation-guide". This item wrote nothing
plan-shaped; if it reports something that predates this branch, say so.

Pre-commit hooks ran on every commit in this branch; re-run the staged-aware
ones against the whole diff:

```bash
cd "<worktree>"
git diff --name-only development...work/sl-readmes
```

**Expect** only `.md` files. Then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-doc-placement.ps1 -All; echo "rc=$LASTEXITCODE"
python scripts\stack\stack.py inventory --check; echo "rc=$LASTEXITCODE"
ruff check .
```

**Expect** `check-doc-placement` to pass (this item added no plan file or
feature directory to this repo); `inventory --check` to end with
`scripts/lib/stack-services.json matches the manifest, the sidecar and the
compose renders` at exit 0, with the `[ ~~ ] declared, not rendered` lines for
the three OB1 profiles present and **not** counted as drift; and `ruff` clean
(this item changed no Python).

**The `/stack-map` drift check.** Run the skill (or follow
`.claude/skills/stack-map/SKILL.md`'s Process section by hand) against the
branch and check step 3: the reference must not name a container the compose
files lack, nor lack one they have. Specifically confirm the skill's own Quick
map no longer lists `watchtower`, `tor`, `mcpo`, `smolcrawl-pipelines`,
`llama-cpp` or `llama-cpp-embed` as containers, since none of those is one.

**FAILS if** any of the four exits non-zero, or if the drift check reports a
container mismatch between the reference and the compose files.

---

## T8 - criterion 8, no live `--env-file` instruction survives (UNBOUNDED)

```bash
cd "<worktree>"
git grep -n -- '--env-file' -- '*.md'
```

**Expect** every hit to be one of:

- **a touched file describing the mechanism as RETIRED** - `README.md`,
  `CLAUDE.md` and the stack-map reference each say so; read the sentence, do
  not count the hit;
- **a historical record**: `CLEANUP-PLAN.md` (5 hits, a CLOSED plan) and
  `documentation/evidence/*/test-plan.md` (past items' executed plans). Neither
  is a touched file and neither may be rewritten - section 6 of the sink says
  why;
- **`.github/copilot-instructions.md`**, which states the retirement.

Then the four files the criterion names, individually:

```bash
for f in README.md CLAUDE.md .claude/skills/stack-map/references/workspace-stacks.md \
         documentation/runbooks/SERVICE-LIFECYCLE.md; do
  echo "== $f"; git show work/sl-readmes:$f | grep -n -- '--env-file'
done
```

**Expect** `SERVICE-LIFECYCLE.md` to have none at all, and each hit in the other
three to be a sentence saying the flag is gone.

Also check the positive half - that the plane READMEs tell a reader the right
thing:

```bash
for f in frontend inference portal search coder memory; do
  echo "== $f"; git show work/sl-readmes:$f/README.md | grep -n 'env-split-migration\|\.env\.example'
done
```

**Expect** every one of the six to link
`documentation/runbooks/env-split-migration.md` at the point where it tells the
reader to create a `.env`.

**FAILS if** any hit in a touched file is a live instruction to pass
`--env-file`, or if a README tells a reader to create a `.env` without linking
the migration runbook.

---

## T9 - the documentation carries the anchor added at proposal

Four small things earlier items left for this one. Each is pass/fail on its
own.

1. **The D18 ancestry sentence**, in
   `documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md`
   section 2 **step 4**:
   ```bash
   git show work/sl-readmes:documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md \
     | grep -n 'is-ancestor' -B6 -A6
   ```
   **Expect** it inside step 4 (the reviewer step), saying that a rebase which
   rewrites commits is a requeue for re-test and never a hand-off, **because**
   `-Merged` requires the tested commit to be an ancestor. Verify the mechanism
   rather than the sentence:
   ```bash
   grep -n 'merge-base' scripts/agent-harness/queue.ps1
   ```
   **Expect** `git merge-base --is-ancestor $item.tested_at_sha $Sha` with a
   `Die` on a non-zero exit. FAILS if the sentence is absent, is in a different
   step, or states a mechanism the tool does not have.

2. **Re-derived frontend citations.** The claim is that the two notes named in
   `documentation/notes/stack-layers-sl-frontend-solo-findings.md` now carry
   correct numbers, and that the TABLE in that note does too. Re-derive by
   construct, never by counting:
   ```bash
   grep -n 'RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT' frontend/docker-compose.yml
   grep -n 'driver: nvidia' frontend/docker-compose.yml
   grep -n 'frontend/docker-compose.yml:' documentation/notes/stack-layers-sl-closeout-findings.md \
        documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md \
        documentation/notes/stack-layers-sl-frontend-solo-findings.md
   ```
   **Expect**, on `55ea48b`, `RETAIN_COUNT` at **`:406`** and `driver: nvidia`
   at **`:301`** inside the reservation block **`:297-303`** - and every
   citation printed by the third command to match those. **Do not take the
   numbers in this sentence on trust**: derive them with the two greps and
   compare. They moved once already (they were `:373` and `:257-263` before
   `sl-compose-anchors` landed), which is the whole point of the case.
   FAILS on any mismatch.

3. **The stack-map Backups row's profile cell.** `openbrain-wiki-backup` is
   `[profile wiki]`, and the Backups table used to give its profile cell as
   "default (open-brain)".
   ```bash
   git show work/sl-readmes:.claude/skills/stack-map/references/workspace-stacks.md \
     | grep -n 'openbrain-wiki-backup'
   ```
   **Expect** the cell to name `wiki`, with the pinned-gitlink caveat.
   Cross-check the caveat is true: `grep -n "profiles:" OB1/docker/docker-compose.yml
   OB1/docker/docker-compose.scheduled.yml` should find only `["idea-refinery"]`.

4. **The sink exists and is held to the artifact's standard.** Read
   `documentation/notes/stack-layers-sl-readmes-findings.md` and check three of
   its entries at random against the files they name - the sink is what the
   NEXT item reads and acts on, so a false claim there is worse than one in the
   deliverable, not better. Confirm every entry carries a **[source]**,
   **[measured]** or **[not verifiable here]** label.

**FAILS if** any of the four is missing, or if a sink entry states a conclusion
its own labelled provenance does not support.

---

## If a case cannot be run here

Report it with `-Fail -PlanInadequate` and say what the plan should have made
runnable. **Never a scoped pass** - `-Pass` refuses any heading whose last word
is not the bare token `PASS`, and a `PASS (scoped ...)` is how an unbuilt image
once shipped.

## If an acceptance criterion itself looks wrong

Say so in your evidence rather than quietly testing its intent. That routes the
disagreement to the gate, which is the operator's to decide.
