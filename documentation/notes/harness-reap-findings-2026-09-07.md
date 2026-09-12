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

> **CORRECTED 2026-09-07 by item `drilllabel`, then corrected again 2026-09-08 by its
> tester.** The heading above ("that is still the leak") reads as if these scripts clean up
> badly. Mostly they do not - but "all six" and "every drill here" were both wrong, and the
> second version of this block asserted them.
>
> **NINE scripts in `scripts/checks/` create a persistent docker resource, not six.**
> Enumerated 2026-09-08 over `docker run -d` / `docker create` / `docker network create` /
> `Start-ObInitdb*`:
>
> | script | teardown construct |
> |---|---|
> | `drill-personal-plane-exclusion.ps1` | `finally` |
> | `prove-agent-memory-rls.ps1` | `finally` |
> | `smoke-agent-memory.ps1` | `finally` |
> | `drill-mcp-door-not-superuser.ps1` | `trap` + `Cleanup` on all ten abort paths |
> | `redprove-census-cannot-measure.ps1` | `trap` |
> | `redprove-fixture-cleanup.ps1` | `trap` |
> | `drill-app-role-not-superuser.ps1` | `trap` |
> | `drill-rls-boot-assertion.ps1` | `finally` (at column 0, after a `catch` - see the note below) |
> | `test-quartz4-offline.ps1` | **neither** - it force-deletes at the end of the happy path only |
>
> So the split among the six THIS ITEM EDITED is 3/3, and across all nine it is **4 `trap`,
> 4 `finally`, and exactly ONE with neither**: `test-quartz4-offline.ps1`, which force-deletes
> at line 429 on the happy path only.
>
> **The row above was wrong once more, and the way it was wrong is the point.** It said
> `drill-rls-boot-assertion.ps1` had "neither `trap` nor a `} finally {`" - which was a true
> statement about a GREP PATTERN and a false one about the script. Its `try` is at line 161,
> its `catch` at 728 and its `finally` at 736, with the braces at column 0, so the literal
> `} finally {` does not appear. That `finally` removes every container the run registered
> (`$containers += ...` before each creation) and sweeps both compose projects by run id. A
> tester caught it on attempt 2 by reading the file instead of the grep. A search is not
> evidence about code until someone has looked at what it missed.
>
> The structural point still stands and is the reason for the whole item: **no in-process
> construct survives the process being KILLED** - not `finally`, not `trap`, not a `Cleanup`
> you remembered to call everywhere. That is what happened. But "these scripts are all
> careful" was an overstatement: ONE script, outside this item's six, is still unlabelled and
> leaks on an exception as well as on a kill. It is the obvious next candidate for the
> retrofit.

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

## 9. Why the drills use the single-token `--label=k=v` form

> **This finding originally claimed a third PowerShell array behaviour: that an array
> written INLINE in a native call is space-joined into one argument. That is FALSE, and
> finding 2 above carries the correction.** Measured on the work line:
> `& docker create --name x @("--label","k=v") alpine true` exits 0 and applies the label.
> PowerShell expands a plain inline array into separate arguments for a native command.
>
> What actually failed, and produced the wrong diagnosis, was a NESTED array of my own
> making: a helper returning `,@("--label","k=v")` - the comma-wrap that stops an empty
> array vanishing - called as `@(Get-Args ...)`, which yields an array CONTAINING an array.
> A native call flattens that inner array into one space-joined argument,
> `--label ai-stack.harness.owner=x`, which docker rejects as an unknown flag. One level of
> self-inflicted nesting, mistaken for a language rule.

So the single-token form is a CHOICE, not a necessity. `lib/harness-owner.ps1` returns
`--label=k=v` as one argument because it splices inline anywhere without caring how the
surrounding call is built - the six drills assemble their `docker run` lines very
differently, several across backtick continuations, and a two-token flag would have meant
touching each of those constructions. `docker run`, `docker create` and
`docker network create` all accept it (verified).

The alternative - rewriting each multi-line `docker run -d ... -e ... -e ...` into an args
array so it could be splatted from a variable - is a large, risky diff to add one label, and
`docker @args` splatting into an executable is the form that does work (finding 2, row 2 of
the native cases). Either would have been correct; this one is smaller.

---

## 10. `docker run --rm` does not need a label, and labelling it would be noise

`--rm` sets `AutoRemove` on the container; the DAEMON removes it when it exits, so it
survives its client being killed and needs no help from a reaper. That is why
`lib/harness-owner.ps1` states the rule as "persistent creation sites are labelled,
`--rm` sites are not" rather than labelling everything.

The residual case, stated so nobody reads more into the rule than it carries: a `--rm`
container whose client is killed while the container is still RUNNING keeps running, and is
only auto-removed when it eventually exits. For the sites here that is a `curl` finishing in
seconds. A long-lived `--rm` container would deserve a label after all.

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
name-based delete back in turns exactly that case red. **Run the seed for the count** - a
figure written here went stale within one attempt, which is the fourth time a hardcoded
number in this change has done so; `documentation/evidence/reap/test-plan.md` seed C carries
the current expectation and is re-derived whenever the verifier changes.

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

---

## 15. A compose label on a CONTAINER may have come from its IMAGE

Docker labels are inherited. Several `:local` images in this stack were built BY compose, so
they carry compose's labels and stamp them on every container run from them:

```
docker image inspect openbrain-mcp-server:local --format '{{json .Config.Labels}}'
  {"com.docker.compose.project":"open-brain",
   "com.docker.compose.service":"openbrain-mcp",
   "com.docker.compose.version":"5.3.0"}
```

So `reap.ps1`'s original key-presence guard - protect anything carrying
`com.docker.compose.project` - refused `drill-mcp-door-not-superuser.ps1`'s own throwaway
container, and the network it occupied survived with it. Found 2026-09-08 by the first real
consumer of the labelling convention, on the very drill the `drilllabel` anchor names as its
demonstration case.

**No edit at the creation site can fix it.** `--label com.docker.compose.project=` produces
`{"com.docker.compose.project":""}` and `docker ps -aq --filter label=com.docker.compose.project`
still matches it - the filter tests the KEY, not the value.

**The discriminator, measured 2026-09-08 across all 81 production containers on this host:**
every one carries `com.docker.compose.config-hash`, `com.docker.compose.container-number`
AND `com.docker.compose.oneoff`. Zero exceptions. A compose-built IMAGE carries none of the
three - only `{project, service, version}`. Compose writes the runtime keys when it starts a
container; they cannot be inherited from an image because no image has them.

`reap.ps1`'s exception is therefore narrow and FAILS CLOSED. All three must hold:

1. the resource carries THIS harness's owner label;
2. it carries NONE of the compose runtime keys;
3. its IMAGE carries the same `project` value, proving the label was inherited.

Miss any one and it stays protected.

**TWO SENTENCES THAT STOOD HERE ARE RETRACTED, and the second was inverted.** They said
rule 1 was "the load-bearing gate" because "a production container never does" carry the
owner label, and that "if compose ever stops writing the runtime keys, rule 1 still holds
the line".

The whole reason this guard exists is the container that DOES carry the label - a stray
`labels:` block in a plane's compose file, which is the scenario `reap.ps1`'s header names.
For that container rule 1 is SATISFIED, so it protects nothing, and if rule 2 also stopped
working the outcome would rest on rule 3 alone. **Measured on this host: 17 of 81 live
containers already satisfy rule 3** - their image carries the same project value - so the
promised fallback covers seventeen real containers in the wrong direction. (Counted with
`docker inspect` over every container carrying `com.docker.compose.project`, comparing the
container's project value against its image's: 17 match, 14 mismatch, 50 have no image
label.)

The load-bearing gate is the CONJUNCTION - **as a FALLBACK argument.** No rule is safe to
lean on once the others are gone, which is why `reap.ps1` states all three and says "miss
any one".

Stated flatly as "no single rule holds the line" this would be its own unqualified claim,
and an attempt-2 tester said so: measured today, 0 of 81 production containers carry the
ownership label and 81 of 81 carry all three runtime keys, so rules 1 and 2 EACH hold the
line alone right now. That is the point of `reap.ps1`'s own emphasis that rule 2 is the
mechanism. What fails is the FALLBACK reasoning - "if rule 2 goes, rule 1 still has us" -
because the container this guard exists for is the one where rule 1 is satisfied.

A paragraph retracting an unqualified claim is a poor place to make one.

This correction exists because an attempt-1 tester followed the corrected header's own
citation and arrived here, at the model the correction was written to retract. **A
correction that leaves its own source standing has not been made** - it has been moved. `verify-reap.ps1` CASE 7g covers both directions, and BOTH halves are
seeded red: restoring the key-presence guard fails "the INHERITED-label container is
reaped", and removing the runtime-key check fails "the RUNTIME-KEYED container is REFUSED".

**The second seed is why the case is trustworthy.** Its first version built the
runtime-keyed fixture from plain `alpine`, whose image carries no compose label - so rule 3
refused it on its own and the runtime-key check was never what kept it alive. Seeding that
check out left the verifier GREEN. The fixture is now built from the same compose-labelled
image, so only the runtime keys stand between it and deletion.

---

## 16. `lib/harness-owner.ps1` reads the config by a second route (latent, not yet divergent)

`scripts/checks/lib/harness-owner.ps1` resolves the ownership label by raw-reading
`scripts/agent-harness/harness.config.json` at a fixed relative path. `reap.ps1` resolves it
through `Get-HarnessSetting`, which layers **built-in defaults < harness.config.json <
harness.local.json < environment** (`config.ps1`).

So the library ignores `harness.local.json` and `AI_STACK_HARNESS_CONFIG`. Set either and
the drills would label with one key while the reaper swept for another.

**There is no divergence today**, verified 2026-09-08: no `harness.local.json` exists, and
`config.ps1`, `config.py`, `harness.config.json` and the library's own fallback literal all
say `ai-stack.harness.owner`.

It is still a second spelling by another route - the exact failure the library's own comment
says it exists to prevent ("does not fail loudly, it just makes every sweep quietly find
nothing"). Found by the `drilllabel` reviewer 2026-09-08 and left for its own item, because
fixing it changes behaviour: the library would have to dot-source `config.ps1`, which pulls
the whole harness module into `scripts/checks/`, or reimplement the layering. Neither is a
documentation change, and the item that surfaced it was a documentation fix.
