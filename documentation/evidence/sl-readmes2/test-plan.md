# Test plan - `sl-readmes2`

**Item:** `sl-readmes2`, the fix item reopened after `sl-readmes` merged as
`17878b9`. **Branch:** `work/sl-readmes2` from `development` at `17878b9`.
**Developer:** `wt-sl-readmes2`.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-readmes2.json`.
**Findings sink:** `documentation/notes/stack-layers-sl-readmes-findings.md`, section 7.

Documentation item; no RED-to-GREEN repro and none claimed. What it owes is
verification against source.

**Why the cases are shaped the way they are.** `sl-readmes`' tester audited
this same file and reported **41 claims checked, 0 false** - honestly, and six
false claims survived it, because the plan enumerated claim CLASSES and the
file's content is claim INSTANCES in table cells. **So this plan enumerates
every table and every ordering item by name.** If you skip one you will have to
skip a named case, visibly, rather than finish a list while whole tables go
untouched.

## Before you start

**Read the blob, not the working tree** (`core.autocrlf` makes everything CRLF
locally):

```bash
git -C "<worktree>" show work/sl-readmes2:<path>
```

**Which shell.** Commands in this plan are POSIX (Git Bash on this host) unless
the block says `powershell`. The artifact's own commands are written for
PowerShell; run them in the shell the artifact names.

**Never print a `.env` value.** Every render below uses
`--env-file <plane>/.env.example` (committed, so the render is the same on any
machine) and `config --format json` piped into a field selector - no secret is
printed.

**Leases: none.** Every case here is a file read or a `config` render. Nothing
is started, stopped or recreated.

**Build the render set once and reuse it.** Almost every case below is answered
by the same eight renders:

```bash
cd "<worktree>"
mkdir -p /tmp/sm && for p in \
  "frontend frontend/docker-compose.yml frontend/.env.example --profile gpu --profile tailscale" \
  "inference inference/docker-compose.yml inference/.env.example --profile local" \
  "memory memory/docker-compose.yml memory/.env.example" \
  "search search/docker-compose.yml search/.env.example" \
  "coder coder/docker-compose.yml coder/.env.example" \
  "portal portal/docker-compose.yml portal/.env.example --profile internet" ; do
  set -- $p; n=$1; f=$2; e=$3; shift 3
  docker compose -f "$f" --env-file "$e" "$@" config --format json > /tmp/sm/$n.json
done
docker compose -f agent-org/docker/docker-compose.yml --profile workers --profile cloud \
  config --format json > /tmp/sm/agent-org.json
docker compose -f OB1/docker/docker-compose.yml --profile research --profile wiki \
  --profile notebook --profile idea-refinery config --format json > /tmp/sm/open-brain.json
```

Then this one-liner prints a plane's services with everything the tables claim:

```bash
sm() { python -c "
import json,sys
d=json.load(open('/tmp/sm/'+sys.argv[1]+'.json'))
for k,s in sorted(d['services'].items()):
    n=s.get('networks'); n=sorted(n) if isinstance(n,dict) else n
    print(f\"{k:30} cn={s.get('container_name','-'):28} prof={s.get('profiles') or []} nets={n} \"
          f\"ports={[str(p.get('host_ip','')) + ':' + str(p.get('published')) for p in (s.get('ports') or [])]} \"
          f\"dep={sorted((s.get('depends_on') or {}).keys())}\")
print('NETWORKS', sorted((d.get('networks') or {}).items()))
print('VOLUMES ', sorted((d.get('volumes') or {}).keys()))
" "$1"; }
```

**The single habit that finds this file's defect class:** a service that
declares no `networks:` key still GETS one - the project default. **Read the
render, never the compose text**, or every such row reads `—`.

---

## T1 - the six corrections the anchor names

Each must be present and TRUE when re-derived. Any one still false FAILS.

| # | Check | Command |
|---|---|---|
| 1 | The §1 Networks preamble names `llm-net`, `app-net`, `default` as the anchor's, and does NOT say "the first three" | `grep -n 'networks:' -A 25 docker-compose.yml` - expect exactly those three declared, nothing else. Then number the table's rows and confirm they are rows **1, 7, 10** |
| 2 | The preamble no longer calls the table the whole network surface, and points at agent-org's three | `sm agent-org` -> NETWORKS must include `ao-net`, `ao-worker-net`, `ao-cloud-egress-net` |
| 3 | §1 Volumes `open-brain` row lists **four**, incl. `wiki-viewer-srv` | `sm open-brain` -> VOLUMES; and `git ls-tree HEAD OB1` must still be `5005197` |
| 4 | §1 Volumes has an `agent-org` row | `sm agent-org` -> VOLUMES. **Count them: the answer is 15, not the 14 the anchor's artifact text says** - see the declaration at the end of this plan |
| 5 | Ordering item 7 says which sidecars carry `depends_on` and which do not | for each of `openwebui-backup tailscale-backup llm-gateway-backup lm-models-backup mnemory-backup little-coder-backup`, read `dep=` out of `sm <plane>`. Expect `depends_on` on the first, third and fifth ONLY |
| 6 | Ordering item 1 no longer says every other plane fails to render without the anchor | `grep -n 'UNUSED external' frontend/docker-compose.yml`; and `sed -n '/^networks:/,$p' memory/docker-compose.yml coder/docker-compose.yml` - each declares its own `default: driver: bridge` |

**FAILS if** any of the six is absent, or present and still false.

---

## T2 - the ROW AUDIT: every table, named, no sampling

Twenty tables - **two of them live inside section 2's blockquote** (rows 11 and
12 below), which is exactly where a naive table-counting script drops them.
**Check every data row of each against the render or the script it names, and
record each row with its evidence.** A row the sources
refute FAILS. The counts beside each table are what the developer measured; a
different count is itself a finding.

| # | Table | Where | Rows | What settles a row |
|---|---|---|---|---|
| 1 | §1 Networks | after "### Networks" | 11 | `docker-compose.yml` for the three anchor rows; each plane's NETWORKS line from `sm` for the rest |
| 2 | §1 Backups | "**Backups (unified snapshot sidecars…)**" | 6 | `sm open-brain`, `sm agent-org`, `sm portal` - nets and profile per row |
| 3 | §1 Portal containers | "**Portal (internet-exposed front-end…)**" | 10 | `sm portal`. **10 rows for 12 services is correct** and the preamble now says so - the two backups are rows in table 2 |
| 4 | §1 Volumes | after "### Volumes" | 8 | the VOLUMES line of all eight renders |
| 5 | §1a frontend profiles | "| Profile | Services | For |" | 4 | four `config --services` runs: none/stock/gpu/gpu+tailscale. **The "none" row needs the profile line stripped first** - `grep -v '^COMPOSE_PROFILES' frontend/.env.example > /tmp/fe.env` - because the example ships `stock` |
| 6 | §1a frontend containers | §1a | 4 | `sm frontend` |
| 7 | §1b inference containers | §1b | 8 | `sm inference` |
| 8 | §1c memory containers | §1c | 3 | `sm memory` |
| 9 | §1d search containers | §1d | 4 | `sm search` |
| 10 | §1e coder containers | §1e | 4 | `sm coder` |
| 11 | §2 OB1 profile groups | the blockquote's "| Profile | Turns on | Why not core |" | 4 | `grep -n 'profiles:' OB1/docker/docker-compose.yml OB1/docker/docker-compose.scheduled.yml` - at pin `5005197` only `idea-refinery` exists; the preamble says so |
| 12 | §2 cross-group references | "| Caller | Key | Target profile |" | 6 | grep each key in the OB1 compose files; note two arrive via `env_file:` and are invisible to a grep of the compose text |
| 13 | §2 OB1 networks | §2 "### Networks" | 5 | `sm open-brain` -> NETWORKS |
| 14 | §2 OB1 containers | §2 "### Containers" | 27 | `sm open-brain`. **27 + the 3 in table 15 = 30** |
| 15 | §2 Open Notebook trio | "### Open Notebook trio" | 3 | `sm open-brain` |
| 16 | §3 agent-org planes/profiles | §3 "### Planes / profiles" | 3 | `grep -n 'profiles:' agent-org/docker/docker-compose.yml` |
| 17 | §3 agent-org networks | §3 "### Networks" | 5 | `sm agent-org` -> NETWORKS |
| 18 | §3 agent-org containers | §3 "### Containers" | 13 | `sm agent-org`. **13 rows for 16 services** - three rows each collapse a `-1`/`-2` pair |
| 19 | §4 Recovery stack | §4 | 3 | `ls scripts/recovery/`; read `emergency-recovery.ps1`'s verbs; the two `.bat` files' `cd /d` + bare `docker compose` lines |
| 20 | the sidecar split table | inside ordering item 7 | 3 | T1 #5 - it is this item's own addition and must be checked like any other |

Count the rows you checked and report the number. The developer's count on the
corrected file, by a script walking header-separator lines **after stripping a
leading `> `**: **20 tables, 134 data rows** (the merged file measured 19 /
129). Without that strip the same script reports 18 / 124 - it silently drops
the two blockquote tables, and the developer's first count did.

**FAILS if** any row disagrees with its source. **Also FAILS if** you report a
pass without a per-table row count - this case exists because a claim-count
pass hid six defects.

---

## T3 - the ORDERING LIST, all ten items

"## Cross-stack dependency order", items 1-10. Walk each against the compose
file it names - `depends_on` edges inside a plane, and `stack.manifest.toml`'s
`requires` for the order between planes.

| Item | Claim to settle | Command |
|---|---|---|
| 1 | anchor: creates three networks, starts nothing; who really fails without it; the two exceptions | T1 #6 |
| 2 | inference: upstreams -> llm-queue -> llm-gateway-db -> llm-gateway | `sm inference`, read `dep=`. **That arrow chain is a valid linear EXTENSION, not the `depends_on` graph** - there is no `llm-queue -> llm-gateway-db` edge; the real edges are `llm-queue -> llama-cpp-upstream`, `llm-gateway -> {both upstreams, llm-gateway-db, llm-queue}`, and `llm-gateway-ui`/`llm-gateway-backup -> llm-gateway-db`. Settle the EDGES against §1b's container table, which states them per service; settle only the ORDER here, and a linear extension is all an ordering list can carry |
| 3 | frontend: openwebui -> tailscale; backup waits on whichever definition is active | `sm frontend` - `openwebui-backup` must show BOTH openwebui names in `dep=` |
| 4 | memory: mnemory -> gateway, backup also waits on mnemory | `sm memory` |
| 5 | search: vpn+redis parallel -> searxng -> gateway | `sm search` |
| 6 | coder: open-terminal -> little-coder; lc-egress waits on nothing | `sm coder` |
| 7 | the sidecar split (three wait, three do not) | T1 #5 |
| 8 | OB1 after llm-gateway; also `requires` search | `grep -n 'requires' -A 2 stack.manifest.toml` in `[planes.ob1]` |
| 9 | agent-org after OB1 | `[planes.agent-org]` requires + the manifest's declaration order |
| 10 | portal is `manual`, five ordered groups + a sixth in production | read `scripts/portal/portal-on.ps1` to the end - the `$groups` array and the `if (-not $Test)` append |

**FAILS if** an item states an order or a mechanism the source does not have.

---

## T4 - the two class-3 carries

1. **`README.md`'s `stats` caveat names the script and quotes the refusal.**
   Read the branch that raises it:
   ```bash
   sed -n '/^def cmd_stats/,/^# ---/p' scripts/stack/stack.py
   ```
   **Expect** `if not WINDOWS:` raising a `Refusal` whose text names
   `scripts/stack/stack-stats.ps1`, the PowerShell invocation and
   `/observe/queue`. The README's block quote must match that text, and
   **`stats` must be the ONLY Windows-gated verb** - `grep -n 'WINDOWS' scripts/stack/stack.py`
   should show the constant's definition and this one use.
2. **`coder/README.md` carries no backslash driver invocation in PROSE.**
   ```bash
   grep -n 'stack.ps1' coder/README.md
   ```
   **Expect** the prose line to read `scripts/stack/stack.ps1 up coder`. One
   backslash form remains inside the ` ```powershell ` fence of "Checking it";
   **that is deliberate and is declared at the end of this plan** - do not fail
   the item on it without reading that declaration first.

---

## T5 - the sink section is true and complete

`documentation/notes/stack-layers-sl-readmes-findings.md` section 7 must name
the six, the seventh non-finding, what the 41/0 audit covered, and the
row-by-row method. Check three of its claims at random against source, plus
these two, which assert facts rather than history:

```bash
grep -rn "three compose files" .claude/skills/stack-map/   # expect NO hits (the 7th)
grep -n 'LLM_QUEUE_SLOTS' inference/compose/queue.yml      # expect the header's "3" and the env's "2"
```

**FAILS if** an entry is false, or if any entry lacks a `[source]` /
`[measured]` / `[not verifiable here]` label.

---

## T6 - gates

```powershell
ruff check .
python scripts/stack/stack.py inventory --check; echo "rc=$LASTEXITCODE"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-doc-placement.ps1 -All
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\plan-store.ps1 `
  -Store "D:\Open WebUI\documentation-plans-ai-stack"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\check-hook-attestation.ps1 `
  -Branch work/sl-readmes2 -Base development
```

**`-Store` is required from a worktree** and is not a workaround for a failing
check - `$Store` defaults to the parent of `$Root`, which in a worktree is
`.claude/worktrees/`. The failure is a throw, never a false green.

**Expect** ruff clean; `inventory --check` exit 0 (its three OB1 profile lines
print `[ ~~ ] declared, not rendered` where `OB1/docker/.env` exists and
`[ -- ] NOT VERIFIED` in a clone without it - **both are a pass**); doc
placement clean; plan store clean; attestation `[OK]` on every commit.

Markdown-only diff, and no stray CR:

```bash
git -C "<worktree>" diff --name-only development...work/sl-readmes2     # all *.md
for f in $(git -C "<worktree>" diff --name-only development...work/sl-readmes2); do
  git -C "<worktree>" cat-file -p work/sl-readmes2:$f | python -c \
    "import sys;d=sys.stdin.buffer.read();print(sys.argv[1], d.count(b'\r'))" "$f"
done
```

**Expect** `0` for every file. Do NOT use `grep -c $'\r'` inside a command
substitution - it degrades to an empty pattern and matches every line, which
produced an invented CRLF finding on a previous round.

---

## T7 - every sentence this fix introduces, as a claim

The `sl-readmes` T10 rule: a fix round's sentences are unreviewed by
construction, and on that item they were the sole failure twice. Diff and read
every added line as fresh:

```bash
git -C "<worktree>" diff 17878b9..work/sl-readmes2 -- '*.md'
```

The claims this item introduces, with what settles each. **The list is the
developer's; the diff is the authority - an added claim missing from this list
is itself a finding.**

| Where | New claim | Settled by |
|---|---|---|
| stack-map §1 networks preamble | the anchor owns exactly three, at rows 1/7/10 | `docker-compose.yml`; number the rows |
| same | agent-org declares three more, OB1 owns obnet + a project default | `sm agent-org`, `sm open-brain` NETWORKS |
| same | "anything not owned by **anchor** is `<project>_<name>` and dies with its project" | the `name:` field of each non-anchor network in every render |
| §1 networks, `obnet` row | owner is the open-brain project; nothing in the anchor project attaches to it | the anchor has 0 services (`docker-compose.yml` has no `services:` key) |
| §1 volumes, open-brain row | four volumes; `wiki-viewer-srv` is the viewer's `/srv` snapshots, derived and NOT backed up | OB1 volumes block; `scripts/checks/check-backup-coverage.ps1` for the backup claim |
| §1 volumes, agent-org row | fifteen, and only the two `*-journals` are backed up | `sm agent-org` VOLUMES; §3's own Volumes prose |
| §1b `llm-gateway-ui` row | `llm-net, app-net`, and app-net is for the portal Caddy | `sm inference`; `portal/config/caddy/Caddyfile` proxies `llm-gateway-ui:8080` |
| §1c memory rows | the `default` there is `memory_default` | `sm memory` NETWORKS |
| §1e / §2 / §3 sidecar rows | `little-coder-backup`, `openbrain-wiki-backup`, `ao-worker-*-journals-backup` are each on their project default because they declare no `networks:` key | the three renders, plus `grep -n 'networks:' -A 2` around each service |
| §2 networks | `search-gw-net` is the Mullvad `vpn` proxy, **not tor**; a `default` row exists | `FETCH_PROXY_URL` in OB1's two compose files |
| §2 `openbrain-digest` | `obnet, llm-net` | `sm open-brain` |
| §2 volumes prose | four, at the pinned gitlink | OB1 volumes block |
| portal table preamble | "the table below is TEN of the twelve" | count the rows; `sm portal` gives 12 |
| ordering item 1 | who fails without the anchor, and the two exceptions | T1 #6 |
| ordering item 7 | the three/three split; a failed precheck is a SKIP and exit 0 | the six service definitions; `backup/generic-tar-backup.sh`'s precheck |
| `README.md` | `stats` is the driver's one Windows-gated verb; the quoted refusal | T4 #1 |
| sink §7a | the 41/0 audit checked claim classes, not rows | read `documentation/evidence/sl-readmes/test-plan.md` T2 |
| sink §7c | **20 tables / 134 rows** now, **19 / 129** merged | re-run the counting script from T2 - the one that strips a leading `> `. The naive parser's 18 / 124 and 17 / 119 are the numbers T2 warns about, and an earlier draft of THIS row quoted them |

**FAILS if** any introduced sentence is false.

---

## Declarations - two places this item disagrees with its own anchor

Per the merge protocol: where the work shows an acceptance criterion is wrong,
say so rather than quietly testing the intent.

1. **The anchor's artifact text says agent-org has "fourteen volumes". It has
   fifteen.** `awk` over the `volumes:` block and the render agree, and the
   stack-map's §3 Volumes prose already listed fifteen correctly. The new §1 row
   says fifteen. **Do not fail it for disagreeing with the anchor** - count them.
2. **The anchor asks that "coder/README.md carries no backslash driver
   invocation".** The PROSE one is fixed. One remains inside a
   ` ```powershell ` fence in "Checking it", and it is the house style shared
   with `memory/README.md` and `search/README.md`, which this item was not asked
   to touch: `.\` is correct PowerShell and the fence says PowerShell, so
   nothing there is false. Changing coder alone makes it inconsistent with its
   two siblings; changing all three is out of scope. **Recorded for the gate to
   decide** rather than resolved unilaterally.

## If a case cannot be run here

`-Fail -PlanInadequate`, naming what the plan should have made runnable. Never
a scoped pass.
