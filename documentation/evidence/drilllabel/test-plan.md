# Test plan - drilllabel

Anchor: `queue.ps1 -Show -Id drilllabel` (confirmed 2026-09-07 by profnovice, amended
twice - read the amendment reasons, they change what this item claims). Branch
`work/drilllabel`, worktree `wt-drilllabel`, base **`177da6d` - the work line, which now
contains `reap`**.

**You are testing three claims:**

1. Every persistent docker resource the six scripts create now carries
   `ai-stack.harness.owner=<that run's id>`, and the id is on the screen (T1, T2).
2. The label is INERT: each drill still passes exactly as before, and a consumer of
   `lib/ob-initdb.ps1` that does not pass `-Owner` is byte-for-byte unaffected
   (T3, T4).
3. A KILLED run - the only case the existing teardown cannot cover - leaves
   resources that `reap.ps1 -Owner <id>` collects (T5).

## Read this before you plan your run

- **The `reap` dependency is now SATISFIED, and that changed what you are testing.**
  This branch was cut from `work/reap` at `bf8f775`. `reap` has since landed on the
  work line as merge `177da6d`, and this branch was rebased onto it, so `reap.ps1` here
  is the MERGED version - the one that deletes by docker ID. The `bf8f775` version
  deleted by NAME and had a production-deletion path; if you find yourself testing
  against that, the rebase did not take and you should stop. Check:
  `Select-String -Pattern 'Row.Id' -Path scripts/agent-harness/reap.ps1` must return matches (5 now).
- **These scripts are NOT sloppy, and the change does not claim they are.** All six
  tear down correctly on a normal exit and on an exception - three via `finally`,
  three via a script-level `trap` calling `Cleanup`. The gap is that no in-process
  construct survives a kill. A test that proves "the trap works" proves nothing about
  this change.
- **Cost warning.** `drill-personal-plane-exclusion.ps1` is the big one: it exports
  the OB1 gitlink, builds five images and starts ten-plus containers. Budget for it,
  and take the `open-brain` lease if you intend to run it, since it builds
  `openbrain-*` images. The other five are minutes.
- **Capture with `2>&1 6>&1`.** These scripts report with `Write-Host`; `2>&1` alone
  captures an empty string and every text assertion passes against nothing.
- **A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
  `queue.ps1 -PlanInadequate` naming the case.
- Every case must carry a bare `PASS` on its heading line when it passes.

## The six scripts and their owner ids

| Script | Owner id |
|---|---|
| `drill-personal-plane-exclusion.ps1` | `pp-drill-<RunId>` |
| `prove-agent-memory-rls.ps1` | `u5rls-<RunId>` |
| `drill-mcp-door-not-superuser.ps1` | `dfuc3-drill` |
| `redprove-census-cannot-measure.ps1` | `rpcensus-<Id>` |
| `redprove-fixture-cleanup.ps1` | `dfuc3-rp` |
| `smoke-agent-memory.ps1` | `am-smoke` |

---

## T1 - every persistent creation site is labelled, and the count is shown

For each of the six, enumerate creation sites and labelled sites yourself. Do not
take the table below on trust - it is the developer's count and reproducing it is the
case:

```powershell
foreach ($f in @("drill-personal-plane-exclusion.ps1","prove-agent-memory-rls.ps1",
                 "drill-mcp-door-not-superuser.ps1","redprove-census-cannot-measure.ps1",
                 "redprove-fixture-cleanup.ps1","smoke-agent-memory.ps1")) {
  $t = Get-Content "scripts\checks\$f" -Raw
  $persistent = ([regex]::Matches($t,'docker run -d|"run", "-d"|docker create|docker network create|"network", "create"')).Count
  $labelled   = ([regex]::Matches($t,'Get-HarnessOwnerLabel \$')).Count
  $initdb     = ([regex]::Matches($t,'-Owner \$')).Count
  "{0,-42} persistent={1} labelled={2} initdb={3}" -f $f,$persistent,$labelled,$initdb
}
```

Expected: `labelled` equals the number of inline persistent sites, and `initdb`
is 1 wherever the script calls `Start-ObInitdb*` (all but
`redprove-census-cannot-measure.ps1`). Developer's counts: plane drill 8+1, rls 2+1,
mcp-door 3+1, census 3+0, fixture-cleanup 1+1, smoke 3+1.

PASS = your enumeration matches, and you can point at each site.
FAIL = any persistent site with no label - **read the surrounding lines, do not
trust the regex.** A site the regex does not recognise is exactly the hole worth
finding, and this repo has a long record of counts that agreed while missing things.
Look in particular for a creation built through an args array or inside a helper.

Also confirm the `--rm` sites are NOT labelled and that
`scripts/checks/lib/harness-owner.ps1` states that rule with its reason.

## T2 - the owner id is on the screen

Run each of the six (see the cost warning) or, at minimum, the five cheap ones. Each
must print, near the start and before it creates anything:

```
  harness owner label : ai-stack.harness.owner=<id>
  if this run dies    : scripts\agent-harness\reap.ps1 -Owner <id>
```

PASS = present in all runs, and the id shown matches what the resources actually
carry (`docker inspect <container> --format '{{json .Config.Labels}}'`).
FAIL = the banner is missing, prints after the first resource is created, or names
an id different from the one on the resources.

For `drill-personal-plane-exclusion.ps1` specifically, confirm the banner prints
**before** the OB1 export and image builds - a run that dies during a 10-minute build
must still have told you its id.

## T3 - the drills still pass

Run each of the six to completion and compare against the same script at the base
commit `bf8f775`. The label must change nothing about the verdict.

PASS = each script's pass/fail counts and exit code match the base run.
FAIL = any difference. If a script fails at BOTH commits, that is a pre-existing
condition, not this change - say so explicitly and give both outputs rather than
recording a pass or a fail.

## T4 - a consumer that does not pass -Owner is unchanged

`lib/ob-initdb.ps1` gained an `-Owner` parameter and has 8 consumers; only 5 pass it.
The other 3 (`drill-rls-boot-assertion.ps1`, `drill-app-role-not-superuser.ps1`,
`test-quartz4-offline.ps1`) must be untouched in behaviour.

```powershell
. scripts\checks\lib\ob-initdb.ps1
$d = Join-Path $env:TEMP "t4-init"; New-Item -ItemType Directory -Force $d | Out-Null
$b = Start-ObInitdbDetailed -Name t4-noowner -InitDir $d -TimeoutSec 90
docker inspect t4-noowner --format '{{json .Config.Labels}}'    # MUST be {}
docker rm -f t4-noowner
```

PASS = `{}` - no label at all, and the container came up. Then run
`drill-rls-boot-assertion.ps1` and confirm it still passes.
FAIL = any label appears, or a non-passing consumer changes behaviour.

## T5 - THE CASE THIS ITEM EXISTS FOR: a killed run is recoverable

Use `drill-mcp-door-not-superuser.ps1` (fixed owner `dfuc3-drill`, so no id to
capture). **Kill the process - do not Ctrl+C and do not make it throw.** Both of
those run the trap, which already worked, and would prove nothing.

```powershell
$p = Start-Process powershell -PassThru -ArgumentList `
     '-NoProfile','-File','scripts\checks\drill-mcp-door-not-superuser.ps1'
# wait until it has created its containers - watch for wt-dfuc3-drill-db to appear
Stop-Process -Id $p.Id -Force
docker ps -a --format '{{.Names}}' | Select-String 'wt-dfuc3-drill'
.\scripts\agent-harness\reap.ps1 -Report          # they appear under HARNESS-OWNED
.\scripts\agent-harness\reap.ps1 -Owner dfuc3-drill
docker ps -a --format '{{.Names}}' | Select-String 'wt-dfuc3-drill'
docker network ls --format '{{.Name}}' | Select-String 'wt-dfuc3-drill'
```

PASS = after the kill the resources exist and are listed under **HARNESS-OWNED**
(not ORPHANS); after the reap, nothing matching `wt-dfuc3-drill` remains, container
or network.
FAIL = they appear as ORPHANS (the label did not land), or anything survives.

**Prove the counterfactual too**: check out the base commit `bf8f775`, repeat the
kill, and confirm the same leftovers appear under **ORPHANS** with no owner. Without
that half you have not shown the change did anything.

## T6 - the findings note's new claims

Finding 9 states three PowerShell array behaviours with a measurement each. Re-run
them; a table that does not reproduce is a FAIL:

- `& $script @("-Owner","x")` into a `.ps1` binds POSITIONALLY.
- `& $exe @argv` splatted into an `.exe` expands correctly.
- `& docker create --name x @("--label","k=v") alpine true` fails with
  `unknown flag: --label ai-stack.harness.owner` (the elements space-joined into one
  argument).
- `--label=k=v` as a single token is accepted by `run`, `create` and
  `network create`.

Finding 5 carries a CORRECTION block claiming all six scripts tear down on normal
exit and on exceptions, three via `finally` and three via `trap`. Verify the split
by reading each script, and verify the `trap` really does call `Cleanup`.

FAIL = any measurement that does not reproduce, or a script whose cleanup construct
is not what the note says it is.

## What is deliberately NOT in scope

Do not label the `--rm` sites. Do not touch `scripts/agent-harness/` - item `reap`
is in testing there. Do not rewrite any script's `trap` into a `finally` or vice
versa; both work and neither survives a kill, so swapping them fixes nothing.
