# Test plan - reap

Anchor: `queue.ps1 -Show -Id reap` (confirmed 2026-09-07 by profnovice). Branch
`work/reap`, worktree `wt-reap`, base `6e46abe`.

**You are testing three claims:**

1. `scripts/agent-harness/reap.ps1` deletes exactly the containers and networks
   labelled `ai-stack.harness.owner=<the owner you asked for>`, and nothing else
   (T1, T2, T3).
2. It cannot be aimed at the live stack. A compose-managed resource is refused even
   when it carries the ownership label, and no compose-managed resource is ever
   offered up for deletion (T4, T5) - this is the claim to attack hardest.
3. `remove-worktree.ps1` reaps only on a removal that actually proceeds, and a docker
   failure can never turn a successful worktree removal into a failed one (T6, T7).

Plus T8-T11 on the findings note, which is held to the same standard as the code.

## Hard rules for this run

- **THE STACK IS LIVE.** 81 containers are running and serving the operator. Every
  case below uses throwaway `alpine:3.21` containers with a `reapv-` or `t<case>-`
  name prefix. You may never attach a test container to an `ai-stack_*` network,
  reuse a prod `container_name`, or run `reap.ps1` in any mode other than
  `-Report` / `-WhatIfOnly` against an owner id you did not create yourself.
- **No plane lease is needed** and you should not take one: nothing here touches a
  plane. Creating a standalone container on the default bridge and reading
  `docker ps` are the pointwise-sandbox and read-only cases MERGE-PROTOCOL 1.4
  and 1.5 permit without a lease. If you believe a case needs a lease, that is a
  plan inadequacy - say so rather than taking one silently.
- **A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
  `queue.ps1 -PlanInadequate` naming the case. Never write `PASS (scoped)`,
  `SKIPPED`, or a pass on partial evidence.
- Every case must carry a bare `PASS` on its heading line when it passes.
- **Capture PowerShell output with `2>&1 6>&1`.** These scripts report with
  `Write-Host`, which writes to the information stream; `2>&1` alone captures an
  EMPTY string and every text assertion you make will pass against nothing. This
  is finding 1 in the sink and it bit the author twice. If a case's evidence is a
  quote from output, prove the capture was non-empty first.

## Two things to know before you start

- `reap.ps1` exit codes: `0` ok, `1` usage or partial failure, `2` harness off,
  `4` docker unreachable. Exit 1 on `-Owner` means at least one resource was
  refused or failed - it does NOT mean nothing was deleted, so check the daemon,
  not only the code.
- The ownership label accepts both spellings of an id: `x` and `wt-x` are the same
  owner. That is deliberate (the README uses both), and T2 is the check on it.

---

## T1 - an owner reaps its own, and only its own

```powershell
docker create --name t1-mine  --label ai-stack.harness.owner=t1 alpine:3.21 true
docker create --name t1-other --label ai-stack.harness.owner=t1-elsewhere alpine:3.21 true
docker create --name t1-bare  alpine:3.21 true
.\scripts\agent-harness\reap.ps1 -Owner t1
docker ps -a --format '{{.Names}}' | Select-String '^t1-'
```

PASS = `t1-mine` is gone; `t1-other` and `t1-bare` both still exist; exit 0.
FAIL = `t1-other` or `t1-bare` was deleted (the reaper is not owner-scoped), or
`t1-mine` survived while the script reported success.

Clean up: `docker rm -f t1-other t1-bare`.

## T2 - `x` and `wt-x` are one owner, not two

```powershell
docker create --name t2-pfx --label ai-stack.harness.owner=wt-t2 alpine:3.21 true
.\scripts\agent-harness\reap.ps1 -Owner t2
```

PASS = `t2-pfx` is gone. Then repeat in the other direction: label a container
`ai-stack.harness.owner=t2b` and reap with `-Owner wt-t2b`; it must also go.
FAIL = either direction leaves the container behind.

## T3 - a busy network is reported, not silently "reaped"

```powershell
docker network create --label ai-stack.harness.owner=t3 t3-net
docker run -d --name t3-occupant --network t3-net alpine:3.21 sleep 300
docker network inspect t3-net --format '{{len .Containers}}'     # MUST print 1 first
.\scripts\agent-harness\reap.ps1 -Owner t3
```

PASS = attached count is 1 before the reap; the reap exits non-zero, says `IN USE`,
and `t3-net` still exists afterwards. Then `docker rm -f t3-occupant` and re-run:
now the network is removed and the reap exits 0.

FAIL = the network is removed while occupied, or the run exits 0 while leaving a
network it could not delete.

**Do not skip the attached-count line.** An occupant running `true` instead of
`sleep` exits instantly, `.Containers` counts running containers only, and the case
then passes while proving the opposite of its claim. That is finding 4 in the sink
and it is how the author's first version of this case lied.

## T4 - THE SAFETY CASE: a compose label beats an ownership label

```powershell
docker create --name t4-fake --label ai-stack.harness.owner=t4 `
  --label com.docker.compose.project=t4-not-a-real-project alpine:3.21 true
.\scripts\agent-harness\reap.ps1 -Owner t4
docker inspect --type container t4-fake --format '{{.Name}}'
.\scripts\agent-harness\reap.ps1 -RemoveOrphan t4-fake
docker inspect --type container t4-fake --format '{{.Name}}'
```

PASS = `t4-fake` survives BOTH calls; both report a refusal naming it; both exit
non-zero.
FAIL = it is deleted by either path, or a refusal exits 0.

Clean up: `docker rm -f t4-fake`.

This is the case that decides whether the reaper is safe to keep. If an agent ever
writes the ownership label into a plane's compose file, this guard is the only thing
between that and a deleted prod service. **Try to get around it**: a label with odd
casing, an empty compose project value (`--label com.docker.compose.project=`), the
label on a network instead of a container. Report anything that gets through.

## T5 - nothing from the live stack is ever a candidate

**Build fixtures FIRST - on a clean daemon this case is vacuous.** Both candidate
sections print `(none)`, so the leak check passes by matching nothing and the
arithmetic reconciles trivially. A tester flagged that on attempt 2. Create at least:
one owned container, one orphan, and one carrying BOTH the ownership and a compose
label, so all three buckets are non-empty before you read the report.

```powershell
docker create --name t5-owned  --label ai-stack.harness.owner=t5 alpine:3.21 true
docker create --name t5-orphan alpine:3.21 true
docker create --name t5-both   --label ai-stack.harness.owner=t5 `
  --label com.docker.compose.project=t5-fake alpine:3.21 true
$r = (.\scripts\agent-harness\reap.ps1 -Report 2>&1 6>&1 | Out-String -Width 250)
$r.Length            # must be > 0 before you conclude anything
```

**`-Width 250` is not decoration.** Bare `Out-String` wraps at the console width, so a
long line becomes two and a `-match` sweep can hit a fragment that was never a line.
That produced a phantom entry for a tester on attempt 2 (finding 12 in the sink).

Then, for every name in `docker ps -a --filter label=com.docker.compose.project
--format '{{.Names}}'` and `docker network ls --filter
label=com.docker.compose.project --format '{{.Name}}'`, check the report: the name
must either not appear at all, or appear only on a line containing `PROTECTED`.

PASS = no compose-managed name appears on any other line, the `-RemoveOrphan` hint
line names none of them, and at least one `ai-stack_*` network exists (so the guard
had something real to protect).
FAIL = any compose-managed name appears as a candidate; or the report was empty and
you concluded it was clean anyway.

Also confirm the arithmetic reconciles: `HARNESS-OWNED + ORPHANS + PROTECTED` must
equal `(docker ps -a | count) + (docker network ls | count)`. A resource classified
into no bucket is a hole in the report; one classified into two inflates the PROTECTED
count that is supposed to work as a tripwire. `t5-both` above is exactly the resource
that used to be counted twice (attempt 2 measured sum=112 over 111 resources), so with
it present this check is real. `reap.ps1` also asserts the partition itself and prints
a `BUG:` line if it fails - a run that prints one is a FAIL of this case even if your
own arithmetic agrees.

Clean up: `docker rm -f t5-owned t5-orphan t5-both`.

## T6 - a REFUSED worktree removal reaps nothing

```powershell
.\scripts\agent-harness\new-worktree.ps1 -Id t6
docker create --name t6-c --label ai-stack.harness.owner=t6 alpine:3.21 true
Set-Content .claude\worktrees\wt-t6\DIRTY.txt "x"
.\scripts\agent-harness\remove-worktree.ps1 -Id t6           # must REFUSE, exit 2
docker inspect --type container t6-c --format '{{.Name}}'    # must still exist
.\scripts\agent-harness\remove-worktree.ps1 -Id t6 -WhatIfOnly   # previews the reap
docker inspect --type container t6-c --format '{{.Name}}'    # STILL must exist
.\scripts\agent-harness\remove-worktree.ps1 -Id t6 -Force    # now reaps, then removes
docker ps -a --format '{{.Names}}' | Select-String '^t6-c$'  # gone
```

PASS = the container survives the refusal AND the preview, and is gone only after
the `-Force` run, which also removes the worktree and the branch.
FAIL = the container is deleted on a run that refused, or on `-WhatIfOnly`. That is
the ordering bug this criterion exists for: a refused removal that already ate the
containers leaves the next attempt worse off than the first.

## T7 - a dead docker daemon cannot fail a worktree removal

```powershell
.\scripts\agent-harness\new-worktree.ps1 -Id t7
$env:DOCKER_HOST = "tcp://127.0.0.1:1"
.\scripts\agent-harness\remove-worktree.ps1 -Id t7
$LASTEXITCODE                    # MUST be 0
Remove-Item Env:\DOCKER_HOST
Test-Path .claude\worktrees\wt-t7    # MUST be False
```

PASS = exit 0, the worktree is gone, and the output says out loud that reap exited 4
and resources may remain. FAIL = a non-zero exit, a surviving worktree, or silence
about the skipped reap.

Also check `reap.ps1 -Owner anything` on its own under the same `DOCKER_HOST`: it
must exit **4**, not 0. An unreachable daemon reporting "nothing to reap" is the
failure mode the script was rewritten to prevent.

## T8 - the verifier is not vacuous

Run `.\scripts\agent-harness\verify-reap.ps1`. It must print **`49 passed, 0 failed`**
(measured 2026-09-07, attempt 3). If the total differs, do not stop at the number -
check WHICH assertions ran and whether any is missing; a count is a weak assertion and
this repo has a record of hardcoded ones going stale
(`documentation/notes/u4quad-findings.md:342`). A different total with every case
present is a note; a missing case is a finding.

Then break the subject deliberately and confirm the verifier goes RED. A verifier that
passes against a broken reaper is worth nothing, and `-Script <path>` exists for this.

**The copy must live in `scripts/agent-harness/`**, not a scratch directory:
`reap.ps1` dot-sources `common.ps1` from `$PSScriptRoot`, so a copy anywhere else
dies on the dot-source before any case runs. Name it `reap.red-*.ps1` beside the
original and delete it afterwards.

Seed A - remove the compose guard in `Get-Inventory`:

```powershell
(Get-Content reap.ps1) -replace 'if \(\$compose -contains \$name\) \{ \$protection = "compose-managed - deleting it is a deploy, not a cleanup" \}', 'if ($false) { $protection = "x" }' |
  Set-Content reap.red-guard.ps1 -Encoding ascii
.\verify-reap.ps1 -Script .\reap.red-guard.ps1
```

Expect **42 passed, 7 failed** (2026-09-07, attempt 3) - the CASE 3, CASE 4 and CASE 7
compose assertions. What matters is WHICH, not the total.

Seed B - narrow `Get-OwnerAliases` to the bare id:

```powershell
(Get-Content reap.ps1) -replace 'return , @\(@\(\$bare, "\$DirPrefix\$bare"\) \| Select-Object -Unique\)', 'return , @($bare)' |
  Set-Content reap.red-alias.ps1 -Encoding ascii
.\verify-reap.ps1 -Script .\reap.red-alias.ps1
```

Expect **48 passed, 1 failed** (2026-09-07, attempt 3) - CASE 2 alone.

Seed C - revert the delete to being aimed by NAME instead of by id:

```powershell
(Get-Content reap.ps1) -replace 'Invoke-DockerCapture @\("rm", "-f", \$Row.Id\)', 'Invoke-DockerCapture @("rm", "-f", $Row.Name)' |
  Set-Content reap.red-byname.ps1 -Encoding ascii
.\verify-reap.ps1 -Script .\reap.red-byname.ps1
```

Expect **47 passed, 2 failed** - both CASE 7c assertions, and read them: the VICTIM
is destroyed and the decoy survives. That is the production-deletion behaviour this
guard exists to stop, reproduced on throwaway fixtures.

PASS = green on the real script, and both seeds fail exactly the cases named above.
FAIL = a seed that stays green (the verifier does not actually test that behaviour),
or a seed that fails cases it should not (the verifier's cases are entangled).
Report T8 on the green run alone as a plan inadequacy, not a pass.

## T9 - the findings note's measured numbers are real

`documentation/notes/harness-reap-findings-2026-09-07.md` states measurements. Re-run
them; a figure that does not reproduce is a FAIL of this case, not a rounding note.

- Finding 1: `@(& queue.ps1 -List 2>&1)` gives 0 lines and `2>&1 6>&1` gives 44.
  (The 44 will drift as queue rows are added - what must hold is **0 versus
  non-zero**. Check the sign of the claim, not the digit.)
- Finding 2 is a FOUR-ROW TABLE and every row is a separate measurement. Run all four
  against `param([CmdletBinding()] [string]$Owner="", [string]$RemoveOrphan="")`:
  the inline array literal `& $s @("-Owner","x")` (NOT a splat - fails the string
  cast); `$a=@("-Owner","x"); & $s @a` (splat a variable - `Owner=[-Owner]
  RemoveOrphan=[x]`, the trap); `& $s @{Owner="x"}` (inline hashtable, NOT a splat -
  `Owner=[System.Collections.Hashtable]`); `$h=@{Owner="x"}; & $s @h` (splat a
  hashtable variable - `Owner=[x]`, the only by-name form).

  **Attempt 1 of this item failed on exactly this.** The note used to show the inline
  literal while quoting the measurement taken with a variable, so its own example did
  not reproduce. Check that every snippet is the thing that was measured - a plausible
  snippet beside a real number is the failure mode here, not a wrong number.
- Finding 3: `docker ps --format '{{index .Labels "com.docker.compose.project"}}'`
  from PowerShell fails with `function "com" not defined`.
- Finding 4: a stopped container attached to a network is not counted by
  `{{len .Containers}}`.

## T10 - the findings note's citations resolve

Every file:line in the note must point at what the note says is there. Check each:
`observe-oracle-on-stall.ps1:139` and `:110`; `drill-personal-plane-exclusion.ps1`
803-807, 2791, 672; `prove-agent-memory-rls.ps1` 82-89, 126, 731;
`drill-dark-factory.ps1:195`; `gate-audit.ps1:123`;
`check-ob1-integration-images.ps1:188`; `dfu-done.ps1:292`;
`verify-dfu-done.ps1:100`.

FAIL = any citation points somewhere else, or the line does not support the claim
made about it. Finding 2 in particular claims those five array-splat sites are
CORRECT because they splat into an `.exe` - verify that each `$Exe`/`$PsExe` really
is an executable and not a `.ps1`, because if any one is a script the note has it
backwards.

## T11 - the findings note claims nothing it did not check

- Finding 7 says a grep over `scripts/` and `documentation/` for `bundlegen` and
  `amtest` returns nothing but the note itself. Re-run it.
- Finding 1 says the in-process capture problem has exactly ONE live instance in
  `scripts/agent-harness/*.ps1` and `scripts/checks/*.ps1`. Re-run that sweep and
  say whether one is really all there is; the note states its own search scope, so
  an instance OUTSIDE that scope is not a contradiction - an instance inside it is.
- Finding 8 lists 12 resources swept on 2026-09-07. `docker ps -a` should now show
  zero containers without a compose project label. Confirm, and confirm the count
  of running containers is unchanged at 81 - the sweep must not have cost the
  operator a service.

## T12 - `-RemoveOrphan` refuses anything that is NOT an orphan

Added at attempt 2, from a tester finding: the flag is documented as removing
UNLABELLED leftovers, and it used to remove any named resource the compose guard
allowed - including another worktree's live, labelled fixture.

```powershell
docker create --name t12-owned --label ai-stack.harness.owner=someone-else alpine:3.21 true
.\scripts\agent-harness\reap.ps1 -RemoveOrphan t12-owned
docker inspect --type container t12-owned --format '{{.Name}}'   # must still exist
```

PASS = refused, non-zero exit, the refusal names the owner `someone-else` AND the
`-Owner` command to use instead, and the container survives.
FAIL = it is deleted, or the refusal exits 0, or the message does not tell the caller
what to do instead.

Clean up: `docker rm -f t12-owned`.

## T13 - the CODE and README make no claim about themselves that is false

T9-T11 hold the findings note to account. This case holds the change's own prose to
the same standard - attempt 1 shipped a false statement about labelling inside the
change whose whole thesis is that unlabelled resources are the problem.

Check each of these against what the code actually does:

- `verify-reap.ps1`'s header describes which of its own fixtures carry the ownership
  label and which deliberately do not. Enumerate the fixtures it creates and confirm
  the header is right about every one. (Its own CASE 5 and CASE 6 need UNLABELLED
  fixtures to test anything, so "all of them are labelled" cannot be true.)
- Its `-KeepOnFailure` recovery hint must name every owner id its fixtures use, and
  say how to remove the unlabelled ones. A hint that names one owner while three
  exist reads as complete and is not.
- `README.md`'s rows for `reap.ps1` and `verify-reap.ps1`, and the "Label what you
  create" section, must describe the behaviour the code has.

PASS = every claim checks out against the code.
FAIL = any statement about the change's own behaviour that the code contradicts.

## T14 - the anchor's documentation criteria

The anchor's last acceptance criterion says README.md and MERGE-PROTOCOL.md state the
marking rule "in the place an agent actually reads before running a test", and that
PLAN.md 4.2's "state pollution stays convention" is corrected to point at the
mechanism. No earlier case checked these.

PASS = the MERGE-PROTOCOL.md rule sits in section 1 (the "before you touch anything"
section, not an appendix); README.md carries the rule; PLAN.md 4.2 no longer claims
convention covers containers and networks.
FAIL = any of the three is missing, or sits somewhere a reader would reach only after
already running their test.

## T15 - a NAME is not an IDENTITY

The most dangerous defect found in this item. Docker resolves a reference by id before
the name index, so a container NAMED with a live container's full 64-char id shadows
it - and `reap.ps1` deleted by name until attempt 2.

**Use your OWN throwaway as the victim. Never a production container.** The point is
provable entirely on fixtures you created.

```powershell
docker create --name t15-victim alpine:3.21 true
$vid = docker inspect --type container t15-victim --format '{{.Id}}'
docker create --name $vid --label ai-stack.harness.owner=t15 alpine:3.21 true
docker inspect $vid --format '{{.Name}}'      # MUST print /t15-victim - the shadow is real
$decoy = docker ps -a --no-trunc --filter label=ai-stack.harness.owner=t15 --format '{{.ID}}'
.\scripts\agent-harness\reap.ps1 -Owner t15
docker inspect --type container t15-victim --format '{{.Name}}'   # MUST still exist
docker inspect --type container $decoy                            # MUST be gone
```

PASS = the shadow is demonstrated (`{{.Name}}` prints `/t15-victim`), the reap exits 0,
**the victim survives**, and the decoy is gone.
FAIL = the victim is deleted - that is the production-deletion path, on a fixture.

Note you cannot check the decoy's fate by its name: that name resolves to the victim.
Capture the decoy's own id first, as above.

Clean up: `docker rm -f t15-victim` (and the decoy by id if it survived).

## T16 - an empty owner VALUE is labelled, not an orphan

```powershell
docker create --name t16-empty --label ai-stack.harness.owner= alpine:3.21 true
.\scripts\agent-harness\reap.ps1 -Report 2>&1 6>&1 | Out-String -Width 250
.\scripts\agent-harness\reap.ps1 -RemoveOrphan t16-empty
docker inspect --type container t16-empty --format '{{.Name}}'
```

PASS = it is NOT listed under ORPHANS, `-RemoveOrphan` refuses it, and it survives.
FAIL = it is offered as an orphan or deleted - `-RemoveOrphan`'s documented contract is
unlabelled leftovers, and a resource carrying the key with an empty value is labelled,
just badly.

Clean up: `docker rm -f t16-empty`.

## What is deliberately NOT in scope

Do not test image or volume reaping: `reap.ps1` reports both and deletes neither, by
operator decision. A case that asks it to delete an image is testing something the
artifact does not claim. Do not retrofit the ownership label into the two drill
scripts - that is named out-of-scope in the anchor and belongs to a follow-up item.
