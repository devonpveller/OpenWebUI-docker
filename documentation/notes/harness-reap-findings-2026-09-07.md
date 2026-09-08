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

> **CORRECTED TWICE, on attempts 1 and 2, each time by a tester.** Round 1: the finding
> illustrated the trap with `& $script @("-Owner", "x")` and attributed a measured output
> to that line, when the measurement had been taken with a variable - the snippet beside
> the number was not the thing measured. Round 2: the correction's own first row named a
> DIFFERENT param declaration from the one measured (`[CmdletBinding()]` written INSIDE
> `param(...)` rather than above it), and that changes the result. Every row below states
> the exact script it was run against, because that is what went wrong both times.

**The script measured against**, written exactly, `bindprobe.ps1`:

```powershell
[CmdletBinding()] param([string]$Owner="",[string]$RemoveOrphan="") "Owner=[$Owner] RemoveOrphan=[$RemoveOrphan]"
```

The attribute is ABOVE `param(...)`. That matters: `param([CmdletBinding()] [string]$Owner...)`
puts the attribute on the PARAMETER instead of the script, and row 1 then returns
`Owner=[-Owner x]` rather than failing. Measured 2026-09-07:

| What you write | What the script receives |
|---|---|
| `& $s @("-Owner","x")` — inline array LITERAL, not a splat | one array-valued argument for `$Owner`; against the script above it fails the cast: `Cannot process argument transformation on parameter 'Owner'` |
| `$a = @("-Owner","x"); & $s @a` — splat a VARIABLE | `Owner=[-Owner] RemoveOrphan=[x]` — **binds POSITIONALLY; this is the trap** |
| `& $s @{Owner="x"}` — inline hashtable LITERAL, not a splat | `Owner=[System.Collections.Hashtable] RemoveOrphan=[]` |
| `$h = @{Owner="x"}; & $s @h` — splat a HASHTABLE VARIABLE | `Owner=[x] RemoveOrphan=[]` — **the only form that binds by name** |

So the rule is not "arrays bad, hashtables good". It is: **splatting requires a variable,
and only the hashtable form carries parameter NAMES.** An array splat passes values in
order, and a leading `"-Owner"` is just the first value - which is why it lands in the
first positional parameter instead of naming it.

Consequence in this item: `verify-reap.ps1` spent its first run silently exercising
`-RemoveOrphan` in every case that claimed to test `-Owner`. Fixed there by naming the
parameters explicitly at each call site.

`observe-oracle-on-stall.ps1` does NOT have this bug: its `$QArgs` is a `[hashtable]` and
it splats the variable (`& $queue @QArgs`), which is row 4 above.

Splatting an array into a native **.exe** is also fine: each element becomes one
command-line argument, which is the intent at `drill-dark-factory.ps1:195`,
`gate-audit.ps1:123`, `check-ob1-integration-images.ps1:188`, `dfu-done.ps1:292` and
`verify-dfu-done.ps1:100`. Those five were checked and are correct as written.

> **A claim that used to sit here was FALSE, with the sign inverted, and a tester caught
> it.** It said an array appearing inline in a native call space-joins into one argument:
> `& docker create --name x @("--label","k=v") alpine true` was supposed to fail. It does
> not. Measured 2026-09-07: exit 0, and `docker inspect` shows `{"k":"v"}` - PowerShell
> expands a plain inline array into separate arguments for a native command, which is the
> convenient behaviour, not a trap.
>
> The real cause of the failure that produced that claim was a NESTED array of my own
> making: a helper returning `,@("--label","k=v")` (the comma-wrap that stops an empty
> array vanishing) called as `@(Get-Args ...)` yields an array CONTAINING an array, and a
> native call flattens that inner array to one space-joined argument -
> `--label ai-stack.harness.owner=x` - which docker rejects as an unknown flag. One level
> of nesting, entirely self-inflicted, misdiagnosed as a language rule.

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
- **7** tags matching `:drill*` (`openbrain-gateway`, `openbrain-mcp-server`,
  `openbrain-ext-server`). This said 6 until a tester counted them on 2026-09-07; it was an
  undercount from the start, not drift - all seven were 6-8 days old at both readings.
- 2 `:test` tags, 10 dangling images

Counts as of 2026-09-07. Re-derive rather than trusting them:
`docker images --format '{{.Repository}}:{{.Tag}}' | Select-String ':drill'`.

`reap.ps1 -Report` prints the counts on every run, so the number is visible without anyone
having to remember to look for it.

---

## 7. `bundlegen` and `amtest` had no traceable creator

The other ten leftovers map to a drill script by name prefix. `bundlegen`
(`openbrain-wiki-viewer:graphsel`, never started, 10 days old) and `amtest`
(`pgvector/pgvector:pg16`, 9 days old) do not: **as of 2026-09-07, before this item was
written**, a grep over `scripts/` and `documentation/` for either name returned nothing.
They were hand-run, by a person or an agent working interactively, and nothing in the
repository recorded why.

> The measurement no longer reproduces, and that is this item's own doing: the names now
> appear in this note, in `documentation/evidence/reap/test-plan.md` and in
> `scripts/agent-harness/README.md`. A tester flagged the bare claim on attempt 3. The
> SUBSTANCE - no script creates them - still holds; re-check it by excluding the files this
> item added.

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

---

## 11. A docker NAME can be another resource's ID, and the id wins

Docker resolves a reference by id before it consults the name index. So a container whose
NAME is a live container's full 64-char id **shadows that container**: every `docker
inspect` / `rm` / `stop` naming that string hits the live one.

**Measured 2026-09-07**, non-destructively, against `openbrain-research`:

```
docker create --name <openbrain-research's full 64-char id> \
       --label ai-stack.harness.owner=t9probe alpine:3.21 true
docker inspect <that same 64-char string> --format '{{.Name}} (image {{.Config.Image}})'
  -> /openbrain-research (image openbrain-research:local)
```

The decoy exists, is a legitimate reap candidate, and is unreachable by its own name. It was
removed by its own id afterwards; `openbrain-research` was never touched.

`reap.ps1` deleted by NAME until this was found by a tester, so `-Owner t9probe` would have
run `docker rm -f <that name>` and destroyed a production container. It now keys its entire
inventory on the id and deletes by id; `verify-reap.ps1` CASE 7c builds the shadowing pair
out of its own throwaway fixtures and asserts the victim survives. Seeding the old
name-based delete back in turns exactly that case red (measured: 47 passed / 2 failed).

**The general rule for any script that deletes docker resources:** enumerate with
`--no-trunc --format {{.ID}}`, carry the id, and act on the id. A name is a display string,
not an identity.

---

## 12. `Out-String` wraps at the console width and can manufacture a line

`... | Out-String` breaks long lines at the host's width, so one output line becomes two and
a `Select-String` / `-match` sweep over the result can match a fragment that never existed
as a line. A tester hit this on attempt 2 while sweeping `-Report` output and saw a phantom
entry.

Use `Out-String -Width <n>` with a width larger than any line you care about (this repo's
scripts print well under 200 columns), or match against the array of lines before joining
them. It sits beside finding 1: those are the two ways a text assertion silently stops
meaning what it says.

---

## 13. PowerShell compares strings case-INSENSITIVELY; docker is case-SENSITIVE

`-eq`, `-ne`, `-contains` and `-like` all ignore case by default. Docker resource names and
label values do not. Both spellings can exist at once. Measured 2026-09-07:

```
'atk-case' -eq 'ATK-CASE'   -> True
@('t4c') -contains 'T4C'    -> True
docker create --name atk-case ... ; docker create --name ATK-CASE ...   -> both exit 0
```

So a script that looks a resource up by name with `-eq` resolves to **whichever docker
listed first**, not to the one the caller named. In `reap.ps1` that meant `-RemoveOrphan`
picked the wrong row and then evaluated its safety guards against it: a tester named the
labelled `t12-owned` and watched the unlabelled `T12-OWNED` be deleted, exit 0, no refusal.
Reversing the creation order produced the opposite outcome, a refusal citing an owner that
belonged to a different container.

The case-sensitive operators are `-ceq`, `-cne`, `-ccontains`, `-clike`. `reap.ps1` now uses
those for every name and owner comparison. **Any script here that matches a docker name,
image tag or label value with a bare `-eq` has this bug**; the operators look identical at a
glance, which is what makes it worth writing down.

---

## 14. `docker rm --force` EXITS 0 when the resource does not exist

Measured 2026-09-07 on this daemon:

```
docker container rm --force 0000...0000
  Error response from daemon: No such container: 0000...0000     (on stderr)
  exit code: 0
```

So `$LASTEXITCODE -eq 0` after a delete means "docker had no objection", NOT "the resource
is gone". `reap.ps1` trusted it and printed `removed` for a delete that removed nothing -
and that printed line is the only evidence anyone reads afterwards. It now re-inspects by id
and reports a failure if the resource is still there.

Note the interaction with finding 1: the error text goes to STDERR, so a caller that
captured only stdout would see neither the message nor a non-zero exit. Two independent
signals, both absent.
