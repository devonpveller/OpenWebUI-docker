# Findings sink - harness container reaping (item `reap`, 2026-09-07)

The findings sink named in the `reap` anchor. Everything here is true and OUT OF SCOPE for
that item's artifact. Each entry says what was checked, how, and when, so the next person
acts on evidence rather than on a claim.

---

## 1. In-process capture of a `Write-Host` script returns NOTHING (one live defect found)

`Write-Host` writes to the **information stream (6)**, not stdout. So an in-process call

```powershell
$out = @(& $someScript -Arg x 2>&1)     # $out is EMPTY if the script reports with Write-Host
```

collects nothing, while the text still appears on the console - which makes the loss
invisible. `2>&1 6>&1` captures it.

**Measured 2026-09-07**, from `.claude/worktrees/wt-reap`:

```
$out = @(& queue.ps1 -List 2>&1)        -> lines=0
$out = @(& queue.ps1 -List 2>&1 6>&1)   -> lines=44
```

This does NOT affect the many callers that spawn a child process
(`& $PsExe -NoProfile -File $Script ...` in `verify-queue-defects.ps1`,
`drill-dark-factory.ps1`, `drill-u6-dark-gate.ps1`): a separate process writes to the
console handle at the OS level, so the pipe receives everything. It affects only
**in-process `& script.ps1`** calls.

**The one live instance found:** `scripts/agent-harness/observe-oracle-on-stall.ps1:139`

```powershell
try { $out = @(& $queue @QArgs 2>&1) } finally { ... }
if ($LASTEXITCODE -ne 0) {
    ...
    $out | ForEach-Object { Write-Host "  $_" }     # prints nothing
```

`$queue` is `queue.ps1` (line 110) and is called in-process. On a queue refusal the script
correctly detects the non-zero exit and halts, but the block that exists to SHOW WHY prints
zero lines. So the failure is reported and its reason is silently dropped - the observer
tells you it refused and cannot tell you what the queue said.

Not fixed here: it is a different script, on a path this item does not touch, and changing
it needs its own before/after evidence. `$QArgs` there is a `[hashtable]`, so it is NOT
affected by finding 2.

Scope of the sweep behind this claim: `grep` over `scripts/agent-harness/*.ps1` and
`scripts/checks/*.ps1` for `= & $var ... 2>&1`. Other repositories and the OB1 submodule
were not searched.

---

## 2. Array splatting into a PowerShell SCRIPT binds POSITIONALLY, not by name

```powershell
& $script @("-Owner", "x")
```

does not set `-Owner`. It sets the first positional parameter to the literal string
`"-Owner"` and the second to `"x"`.

**Measured 2026-09-07:** a script declaring `param([string]$Owner, [string]$RemoveOrphan)`
invoked that way printed `Owner=[-Owner] RemoveOrphan=[x]`.

Consequence in this item: `verify-reap.ps1` spent its first run silently exercising
`-RemoveOrphan` in every case that claimed to test `-Owner`. Fixed there by naming the
parameters explicitly.

**Hashtable splatting is unaffected** - `@{ Owner = "x" }` binds by name correctly, which
is why `observe-oracle-on-stall.ps1` (a `[hashtable]$QArgs`) does not have this bug.
Splatting an array into a native **.exe** is also fine: each element becomes one
command-line argument, which is the intent at `drill-dark-factory.ps1:195`,
`gate-audit.ps1:123`, `check-ob1-integration-images.ps1:188`, `dfu-done.ps1:292` and
`verify-dfu-done.ps1:100`. Those five were checked and are correct as written.

No other in-repo instance of array-splatting into a `.ps1` was found.

---

## 3. A Go template with a QUOTED key does not survive PowerShell 5.1

```powershell
docker ps --format '{{index .Labels "com.docker.compose.project"}}'
```

fails with `failed to parse template: template: :1: function "com" not defined`. PowerShell
strips the inner quotes when handing the argument to a native `.exe`, so docker receives
`{{index .Labels com.docker.compose.project}}`. The same applies to the `{{.Label "k"}}`
form. Verified 2026-09-07 against Docker Desktop on this host, for both `docker ps` and
`docker network ls`.

Quote-free ways to ask the same question, all verified working here:

- `docker ps -a --filter label=<key>` and `--filter label=<key>=<value>` (no template)
- `docker inspect <name> --format {{json .Config.Labels}}` (whole map, parsed in PowerShell)
- `docker network inspect <name> --format {{json .Labels}}`

`reap.ps1` uses only these. Any future script that reads a docker label from PowerShell
will hit the same wall.

---

## 4. `docker network inspect ... {{len .Containers}}` counts RUNNING containers only

A stopped container attached to a network does not appear in `.Containers`, and
`docker network rm` will remove the network out from under it.

Found while writing `verify-reap.ps1` CASE 5: the occupant was created as
`alpine:3.21 true`, which exits the instant it starts, so the network reported zero
attached containers and the case "passed" while proving the opposite of its claim. The
case now runs `sleep 300` and asserts the attached count is 1 before drawing any
conclusion.

Consequence for `reap.ps1`: its in-use check is a check on RUNNING occupants. A network
whose only occupants are stopped containers is genuinely removable, and removing it is
correct - but anyone reading `IN USE` should know it means "running containers attached",
not "containers attached".

---

## 5. The drills still clean up in a `finally`, and that is still the leak

`scripts/checks/drill-personal-plane-exclusion.ps1` (teardown at line 803-807, `finally` at
2791) and `scripts/checks/prove-agent-memory-rls.ps1` (`u5rls-*` resources, line 82-89) tear
down their own docker resources at the end of a `finally` block. That block does not run
when the script is killed, which is how ten containers and two networks reached 6-10 days
old on this daemon.

This item deliberately did NOT retrofit the ownership label into those two scripts - it is
named in the anchor's out-of-scope list. Doing so is a small, well-defined follow-up: add
`--label ai-stack.harness.owner=<run id>` to each `docker run`/`docker network create`, and
their leftovers become reapable without touching the existing `finally` at all. Until then
their droppings are ORPHANS, which `reap.ps1 -Report` lists but never auto-deletes.

---

## 6. ~5 GB of test images remain, by decision

Out of scope per the operator (2026-09-07): the reaper reports images and never deletes
them. Present on the daemon at the time of the sweep:

- 9 tags matching `:wt-*` (largest: `openbrain-wiki-viewer:wt-tester-note-width` and
  `:wt-wiki-note-width`, 866 MB each)
- 6 tags matching `:drill*` (`openbrain-gateway`, `openbrain-mcp-server`,
  `openbrain-ext-server`)
- 2 `:test` tags, 10 dangling images

`reap.ps1 -Report` prints the counts on every run, so the number is visible without anyone
having to remember to look for it.

---

## 7. `bundlegen` and `amtest` had no traceable creator

The other ten leftovers map to a drill script by name prefix. `bundlegen`
(`openbrain-wiki-viewer:graphsel`, never started, 10 days old) and `amtest`
(`pgvector/pgvector:pg16`, 9 days old) do not: a grep over `scripts/` and `documentation/`
for either name returns nothing. They were hand-run, by a person or an agent working
interactively, and nothing in the repository records why.

That is the case the ownership label is for, and also the case where an orphan must never
be auto-deleted - which is why `-RemoveOrphan` requires the name to be typed.

---

## 8. The one-shot sweep - what was removed, 2026-09-07

Recorded BEFORE the removal, so this list is the record and not a reconstruction of it.
Every entry was unlabelled, carried no `com.docker.compose.project`, and was removed by
name with `reap.ps1 -RemoveOrphan`, which requires each name to be typed.

Containers (all in a terminal state - `exited`, or created and never started):

| Name | Image | State | Age | Origin |
|---|---|---|---|---|
| `pp-drill-5e705d0f-rest` | `caddy:2-alpine` | exited (0) | 6d | `drill-personal-plane-exclusion.ps1` |
| `pp-drill-5e705d0f-pgrest` | `postgrest/postgrest:v12.2.3` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-ext` | `openbrain-ext-server:drill-5e705d0f` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-cloud` | `openbrain-gateway:drill-5e705d0f` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-ops` | `openbrain-gateway:drill-5e705d0f` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-mcp` | `openbrain-mcp-server:drill-5e705d0f` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-embed` | `denoland/deno:2.3.3` | exited (255) | 6d | same |
| `pp-drill-5e705d0f-db` | `pgvector/pgvector:pg16` | exited (255) | 6d | same |
| `amtest` | `pgvector/pgvector:pg16` | exited (255) | 9d | unknown - see finding 7 |
| `bundlegen` | `openbrain-wiki-viewer:graphsel` | created, never started | 10d | unknown - see finding 7 |

Networks (both with zero attached containers):

| Name | Age | Origin |
|---|---|---|
| `pp-drill-net-5e705d0f` | 6d | `drill-personal-plane-exclusion.ps1` line 672 |
| `u5rls-net-671762` | 8d | `prove-agent-memory-rls.ps1` line 89 |

Nothing else was touched. The IMAGES those containers referenced were NOT removed - the
`:drill-5e705d0f` and `:graphsel` tags remain, per finding 6.
