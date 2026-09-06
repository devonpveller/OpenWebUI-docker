# Deploy gate 2026-09-06 - findings sink (items curatorimg, gate5d, passplan; the reviewer merges sections)

## curatorimg

Recorded 2026-09-06 by the `curatorimg` developer (worktree `wt-curatorimg`,
parent `af0faad`, OB1 `d89c126`). Everything below was verified by reading the
named file at the named line or by running the named command in that worktree;
line numbers are post-edit (the curator healthcheck shifted everything after
compose `:513` by +6).

### Which OB1 containers `check-openbrain-health.ps1` still omits

The script references exactly these container names
(`grep -oE "'openbrain-[a-z-]+'" scripts/checks/check-openbrain-health.ps1 | sort -u`):
`openbrain-curator` (added by this item), `-db`, `-entity-worker`, `-gateway`,
`-idea-refinery`, `-mcp`, `-mcpo`, `-mcpo-ext`, `-postgrest`, `-research`,
`-rest`, `-wiki`, `-wiki-viewer`. `OB1/docker/docker-compose.yml` defines 24
services; the ones the script never names, with the compose line of the
service key and of its `container_name`:

| container | compose service | `container_name` | has `healthcheck:`? |
|---|---|---|---|
| openbrain-ext | `:156` | `:161` | no |
| openbrain-ops-gateway | `:243` | `:246` | yes |
| openbrain-suggestion-worker | `:429` | `:434` | no |
| openbrain-workbench | `:798` | `:803` | yes |
| openbrain-extract | `:860` | `:865` | yes |
| openbrain-chunk-worker | `:902` | `:907` | no |
| openbrain-grounding-backfiller | `:938` | `:943` | no |
| openbrain-db-backup | `:979` | `:984` | no |
| openbrain-wiki-backup | `:1026` | `:1028` | no |
| surrealdb / open_notebook / open-notebook-backup | `:1064` / `:1088` / `:1168` | - | (ON trio, in this project since K.5b) |

The "five other absent workers" named in
`documentation/implementation-guide/deploy-gate-and-curator-recovery/PLAN.md:82`
(chunk-worker, suggestion-worker, grounding-backfiller, workbench, extract) are
all confirmed absent. Of those five, only workbench and extract carry a compose
`healthcheck:`, so only they can ever surface through `stack.ps1 health`'s
`docker ps --filter health=unhealthy` (`scripts/stack/stack.ps1:127-129`); the
three workers, `-ext` and both backup sidecars are invisible on BOTH surfaces
today - the exact blind spot the curator sat in. Out of scope here by the
anchor; recorded for Phase C/D. `openbrain-idea-refinery` is defined in
`OB1/docker/docker-compose.scheduled.yml:235`, not the main file, which is why
it is absent from the table yet named by the script.

### openbrain-research and openbrain-mcp have no compose healthcheck

Counted with awk over each service block in `OB1/docker/docker-compose.yml`
at `d89c126`: `openbrain-mcp` block `:98-155` has 0 `healthcheck:` lines;
`openbrain-research` block `:535-633` has 0. Before this item, 7 of 24
services had one (db, gateway, ops-gateway, mcpo, mcpo-ext, workbench,
extract); the curator makes 8. Consequences worth stating plainly:

- `openbrain-research` now waits on the curator being healthy to START
  (`condition: service_healthy`, compose `:544-545`), but nothing waits on
  research, and compose does not stop a dependant when a dependency later goes
  unhealthy - the condition is an `up`-time gate only.
- A curator whose DB pool is dead answers 503, so the new healthcheck marks
  it `unhealthy` and `stack.ps1`'s unhealthy-count probe now catches that case
  too. `restart: unless-stopped` does NOT restart an unhealthy container (no
  autoheal in this project), so a stale-pool curator stays unhealthy until
  `check-openbrain-health.ps1 -Repair` restarts it - which is the same
  posture research already has.

### Harness: `andon.ps1 -Evaluate` cannot self-pass on this repository

Claim from the coordinating session: on 2026-09-06 the andon board fires
`policy-declared-unread` and `git-error-swallowed` (35 call sites) and reports
`protected-ref-moved` indeterminate (no baseline at
`.git/agent-worktrees/audit/andon-baseline.json`), so the `dark` gate profile
cannot self-pass and the anchors were confirmed under a delegated human
principal instead.

Verified by running `powershell -NoProfile -File scripts/agent-harness/andon.ps1 -Evaluate`
from `wt-curatorimg` on 2026-09-06 (exit code 6). Census line, verbatim:

    census  : unrecognised=0, fired=3, indeterminate=1, disabled=0, evaluated_ok=1 (total 5 of 5 in scope)

The claim holds and is slightly UNDER-stated: `fired=3`, not 2. The third is
`work-branch-on-remote` - `work/pod-key is on remote 'origin'
(refs/remotes/origin/work/pod-key)`. `git-error-swallowed` listed 35 sites,
matching the claim; `protected-ref-moved` read `no baseline recorded - run:
andon.ps1 -Baseline`. Nothing in this item changes any of the four conditions.

### Small things seen in passing

- `deno check` with `pool.ts` missing reports a SECOND error, `TS7006
  Parameter 'row' implicitly has an 'any' type` (T5 mutant B). The `row` type
  flows from `pool.ts`; the real build reports 0 errors. Not a defect - noted
  so a tester does not read the second error as a new one.
- The T3 image's `/health` `pool_rebuilds` counter climbs by ~1 per probe
  against a dead host (3 at +6 s, 9 at +92 s) - the ResilientPool discarding a
  pool it could not open. Expected behaviour, cheap, and a useful witness that
  the wrapper is the one answering.
