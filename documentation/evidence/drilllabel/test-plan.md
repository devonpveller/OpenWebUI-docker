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
  the OB1 gitlink, builds four images and starts ten-plus containers. Budget for it,
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

## Cases DISCHARGED by `drilllabel` at `93cec725` - do NOT re-run them here

**These six are `drilllabel`'s, not this item's, and this item's anchor puts them
out of scope.** They are demoted to `###` deliberately: `queue.ps1` parses a case
as `^##\s+(T\d+|Case\s+\d+)\b`, and `-Pass` refuses unless EVERY parsed case
reads PASS - so leaving them at `##` made this item **unpassable inside its own
anchor**. T3 alone would re-run all six drills, including the plane drill that
exports OB1 and builds four images under the `open-brain` lease, to re-prove a
commit that changes no executable line.

An attempt-2 tester found that: not a wrong answer in the plan, an item no
correct tester could ever pass. They are kept here in full because the reviewer
needs to see WHAT was discharged and where, not just that something was.

They passed at `93cec725` (drilllabel attempt 3). Re-read them only if this item's diff touches the drills - it does not.
`git diff 93cec725..HEAD --stat` names **FOUR** files: the two documentation
files, `scripts/agent-harness/README.md`, and **`scripts/agent-harness/reap.ps1`
itself** - whose changed lines are every one a comment, which is what T11
proves rather than asserts.

(This carried the figure "51". It was 51 when written and 59 at the NEXT commit,
because each round added comment lines - so the number aged every time the item
was corrected, in a sentence whose own case computes it on demand. **A count that
a command beside it derives should not also be typed out.** T11 prints it; this
sentence no longer does.)

(This said "three documentation files". It named four, and the omitted one was
`reap.ps1` - the `docker rm -f` tool a discharged case exercises - inside the
paragraph justifying skipping six cases on a live-host deletion tool. An
attempt-3 tester ran the command the sentence cites and read a different answer
from it. **The accurate wording already existed in T11, further down this same file** -
so this was not a thing nobody knew; it was a summary written from memory next
to the evidence that contradicted it.

That sentence originally said "forty lines below". It was 154 lines at the commit
it described and 169 at the next - a figure invented to sharpen a self-criticism,
inside the paragraph correcting an invented figure, which is what T9's own
parenthetical calls "the same defect the case is about, written into its own
rationale". **No distance ships from this file**; a direction cannot go stale.)

### T1 (DISCHARGED) - every persistent creation site is labelled, and the count is shown

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

### T2 (DISCHARGED) - the owner id is on the screen

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

### T3 (DISCHARGED) - the drills still pass

Run each of the six to completion and compare against the same script at the base
the work-line commit `177da6d` (the merge-base, i.e. this branch minus its own
commits). The label must change nothing about the verdict.

PASS = each script's pass/fail counts and exit code match the base run.
FAIL = any difference. If a script fails at BOTH commits, that is a pre-existing
condition, not this change - say so explicitly and give both outputs rather than
recording a pass or a fail.

### T4 (DISCHARGED) - a consumer that does not pass -Owner is unchanged

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

### T5 (DISCHARGED) - THE CASE THIS ITEM EXISTS FOR: a killed run is recoverable

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

**Prove the counterfactual too** — but do NOT check out the merge-base: you are barred
from mutating git in the only worktree of this branch. Use `git show 177da6d:<path>` into
a temporary copy instead, which yields the same evidence. Repeat the
kill, and confirm the same leftovers appear under **ORPHANS** with no owner. Without
that half you have not shown the change did anything.

### T6 (DISCHARGED) - the findings note's new claims

**Finding 9 was REWRITTEN in this change**, because `reap`'s own later testing
disproved its original claim. It used to assert a third PowerShell array behaviour -
that an array written INLINE in a native call is space-joined into one argument. That
is FALSE with the sign inverted, and finding 2 above carries the correction. Verify
the REWRITE, not the retracted claim:

- `& $script @("-Owner","x")` into a `.ps1` binds POSITIONALLY.
- `& $exe @argv` splatted into an `.exe` expands correctly.
- `& docker create --name x @("--label","k=v") alpine true` **exits 0 and applies the
  label** - a plain inline array expands into separate arguments. If you record this as
  failing with `unknown flag`, you have tested the retracted claim.
- The NESTED form is what actually fails: a helper returning `,@("--label","k=v")` called
  as `@(Get-Args ...)` yields an array containing an array, which a native call flattens
  into one space-joined argument and docker rejects as an unknown flag.
- `scripts/checks/lib/harness-owner.ps1`'s header must no longer carry the old false
  justification - it should state the single-token form as a CHOICE, and cite finding 9.
- `--label=k=v` as a single token is accepted by `run`, `create` and
  `network create`.

Finding 5 carries a CORRECTION block claiming all six scripts tear down on normal
exit and on exceptions, three via `finally` and three via `trap`. Verify the split
by reading each script, and verify the `trap` really does call `Cleanup`.

FAIL = any measurement that does not reproduce, or a script whose cleanup construct
is not what the note says it is.

## T11 - NOTHING ELSE MOVED, which is what makes the discharge above safe

Six cases are discharged rather than re-run. That is only honest if this item's
diff really does not reach what they cover, so prove it rather than assert it.

    git diff 93cec725..HEAD --stat
    git diff 93cec725..HEAD -- '*.ps1' '*.sh' | grep -E '^[-+][^-+]' | grep -vE '^[-+]\s*#'

**Scope the second command to `*.ps1` and `*.sh`, not to `scripts/`.** The first
draft of this case used `-- scripts/`, which sweeps in
`scripts/agent-harness/README.md` - a markdown file that lives beside the code -
and so failed this item on 23 changed lines of prose. I found that by running the
case against the item it was written for, which is the only way a new case earns
its place.

**AND A THIRD COMMAND, because the first two do not cover T6.** T1-T5 are claims
about script BEHAVIOUR, and a non-comment diff is exactly the right evidence for
them. T6 is not: it checks findings 5 and 9 of the findings note, and this item
edits that note substantially - so the grep above would accept an arbitrary rewrite
of the very findings T6 exists to hold to account. An attempt-3 tester spotted
that the discharge was safe by a command the plan did not contain, and ran it.

    for f in 5 9; do
      git show 93cec725:documentation/notes/harness-reap-findings-2026-09-07.md |
        awk "/^## $f\./,/^## $((f+1))\./" | md5sum
      awk "/^## $f\./,/^## $((f+1))\./" documentation/notes/harness-reap-findings-2026-09-07.md | md5sum
    done

PASS: the first names ONLY the two documentation files, `README.md`, and
comment-only changes to `reap.ps1`; the second prints nothing - no non-comment
line of any SCRIPT changes across the whole item; and the third gives a matching
pair of digests for each finding, so T6's subjects are byte-identical to the
commit that discharged it.
FAIL: any executable line moves, or either finding's digest differs. Then the
discharge is void and T1-T6 come back, because the reason they were safe to skip
has gone.

This is the case that makes a discharge auditable instead of a promise, and it is
cheap: THREE commands.

(Two corrections in this paragraph, both found by an attempt-4 tester and both
the same shape. It said "two commands" after a third had been added in the very
commit that wrote the line - so a tester trusting T11's own closing sentence
would run two of three and skip the digest, which is exactly the omission the
third command exists to close. And the FAIL clause stood TWICE, the new one and
the superseded original verbatim beneath it. A case that contradicts itself is
read by whoever runs it, not by whoever wrote it.)

## T12 - the header's claims about DOCKER, not just about the code

T9 holds `reap.ps1`'s header against the CODE. Nothing held it against DOCKER,
and two false statements went through four attempts because of it:

- "compose writes those when it STARTS a container" - `docker compose create`,
  never started, already carries all three runtime keys;
- "no image can supply them" - `LABEL com.docker.compose.oneoff=False` in a
  Dockerfile is inherited by every container built from it.

Both erred safe, and both sat in a safety contract read by someone deciding
whether to run a deletion tool on a live host. An attempt-4 tester found them by
building the cases; nothing in the plan asked anyone to.

For EVERY factual claim the header makes about docker's own behaviour, build the
case and measure it. They are cheap - a Dockerfile with a `LABEL` line, a
`docker compose create` that is never started - and they are the only way a
sentence about docker gets checked at all.

PASS: every docker-behaviour claim in the header reproduces, or is qualified to
what you measured.
FAIL: any claim that does not - **including one that errs safe**. A safety
contract that is wrong in the protective direction still teaches its reader a
false model of the tool, and the next person to change the guard will reason from
it.

## T13 - EVERY CLAIM THE FILES MAKE ABOUT THEMSELVES

SEVEN of nine attempts - 1, 3, 4, 5, 7, 8 and 9 - failed on a count, a distance,
a direction or a history that a changed file asserted about ITSELF.

**THIS SENTENCE HAS NOW BEEN WRONG THREE TIMES RUNNING, which is the best argument
for the case that exists.** It said "three of seven (3, 4 and 7)" and claimed to
have been checked against the queue's evidence files; it had not been. Corrected
to "five of eight (3, 4, 5, 7, 8)", it still missed
`reapdoc.attempt1.evidence.md` - "DEFECT 2 (BLOCKING): 'three testers passed it'
does not reproduce" - and attempt 9's own two blockers. Each correction was a
partial sweep presented as a complete one.

It also said "not one was found by a case until this one existed", and that is
false: attempt 3's blocker and attempt 5's BLOCKER 2 were both filed under **T10**,
whose citation half already required checking what a correction cites. T13 widens
that to claims a file makes about itself with no citation attached; it does not
invent the idea. T7/T8 test the guard, T9 holds behavioural claims against code, T10 hunts
retracted models, T11 proves nothing executable moved, T12 holds docker claims to
account - and a tester who ran exactly that set and nothing more would have PASSED
attempt 7. An attempt-7 tester named this gap; the case is theirs.

Enumerate, from the diff AND from the commit message, every:

  * COUNT - lines, paragraphs, commands, files, sites, testers, attempts, rounds
  * DISTANCE - "n lines below", "the paragraph above", "further down"
  * DIRECTION - above / below / beside / under, with no number at all
  * HISTORY - "the first time", "three rounds running", "two attempts", who passed
  * CHARACTERISATION OF A PAST ERROR - "the same move", "identical", "the same shape"

and check each against HEAD and against git.

    git diff <base>..HEAD | grep '^+' |
      grep -inE '\b(two|three|four|[0-9]+) (lines?|paragraphs?|attempts?|testers?|commands?|rules?|bullets?|sites?|images?|files?)\b|\b(above|below|beside|under|next to) (it|this|the)\b'
    # then, for each hit, OPEN the file and count. Do not trust the sentence.
    #
    # DO NOT FILTER THE OUTPUT. The first run of this case piped the sweep through
    # a `grep -v` that dropped anything containing "retract", on the reasoning that
    # such lines merely QUOTE a figure already withdrawn. One of the lines it
    # dropped was a live claim - "one line above the guard that model was retracted
    # for", and the guard is nowhere near one line away - and a tester found it
    # at hit 513 of the unfiltered sweep, from the regex above, which had caught it
    # all along. THE FILTER IS PART OF THE CHECK, and a filter written from the
    # author's belief about which hits matter reproduces the author's blind spot
    # exactly. Read every hit. There will not be many.
    # for a history claim: read the queue's own json, never the prose
    # for "the same move": quote BOTH originals out of git and compare them
    # and run the T10 positive control first - `grep -iF` aborts silently here

PASS: every one reproduces from the artifact as it stands now.
FAIL: any count is stale, any direction points the wrong way, any history
contradicts the queue, or any characterisation of a past error does not survive
reading that error's actual text.

THE RULES THIS CASE ENCODES, each bought with a failed attempt except the last,
which was bought by running the case on the change that introduced it:

  * **A DIRECTION IS A FIGURE.** "Below" is as checkable as "nine lines below",
    and attempt 7 got the direction itself backwards while also getting the
    number wrong.
  * **A FIGURE INHERITED FROM A TESTER'S REPORT IS AN INVENTED FIGURE.** It was
    measured against THEIR excerpt, not this layout. Attempt 7 transplanted three
    of them verbatim and all three were wrong here.
  * **A COUNT THAT A COMMAND BESIDE IT DERIVES SHOULD NOT ALSO BE TYPED OUT** -
    already the doctrine of T11. "Edits that note by 61 lines" had aged by the
    time a case went looking, and the correction that reported the new figure aged
    IN THE SAME COMMIT IT SHIPPED IN - which is the argument for carrying no
    number here either.
  * **FINDING ONE INSTANCE OF A SHAPE IS NOT FINDING THE SHAPE.** Running this
    case for the first time, on the change that introduced it, turned up T10
    saying "Two commands" while giving THREE - the third added by the same commit,
    which is attempt 4's "two commands" defect reproduced one case away from where
    it was corrected. Sweep mechanically; do not stop at the reported instance.
  * **AND THE SWEEP ITSELF MUST NOT BE FILTERED** - see the command above. The
    same run that found T10's miscount ALSO hid a fourth invented distance behind
    its own `grep -v`, and shipped a heading reading "THREE RULES" above four
    bullets. A case is not self-executing: it can be run in a way that reproduces
    precisely the habit it was written to break.
  * **CHECK THE CLAIM AS WRITTEN, NOT ITS CONVERSE.** Attempt 9's inverted
    direction was IN the 40-hit sweep and WAS read. The sentence says the BLOCK
    sits below a LINE; what got measured was whether the LINE sits below the
    BLOCK, which is true, so the hit was cleared green. Reading a hit is not
    checking it. Write down which thing the sentence makes the subject before
    you measure anything, because the converse of a false claim is usually true
    and will clear it.
  * **A COUNT ABOUT ANOTHER FILE IN THE SAME DIFF AGES TOO.** Attempt 9's other
    blocker was this note quoting `grep` match counts for `reap.ps1` - correct
    when written, stale two commits later because every round adds comments to
    that file. Also read, also cleared, because it had been verified at the commit
    that wrote it rather than at the tip. **VERIFY AT THE TIP, ALWAYS**: "it was
    right when I wrote it" is the definition of the defect, not a defence.

## What is deliberately NOT in scope

Do not label the `--rm` sites. Do not touch `scripts/agent-harness/` - item `reap`
is in testing there. Do not rewrite any script's `trap` into a `finally` or vice
versa; both work and neither survives a kill, so swapping them fixes nothing.

## T7 - a compose label INHERITED FROM THE IMAGE does not protect

This is what attempt 1 failed on, and the fix changes `reap.ps1` — the guard between
this tooling and 81 production containers. Test it as such.

Several `:local` images here were built BY compose, so they carry
`com.docker.compose.project` and stamp it on every container run from them.
`reap.ps1`'s original key-presence guard refused `drill-mcp-door-not-superuser.ps1`'s
own throwaway because of it.

```powershell
docker image inspect openbrain-mcp-server:local --format '{{json .Config.Labels}}'
# expect {project, service, version} - and NONE of config-hash / container-number / oneoff

docker create --name t7-inherit --label ai-stack.harness.owner=t7 openbrain-mcp-server:local sleep 1
.\scripts\agent-harness\reap.ps1 -Report 2>&1 6>&1 | Out-String -Width 250
.\scripts\agent-harness\reap.ps1 -Owner t7
```

PASS = `t7-inherit` is listed under HARNESS-OWNED (not PROTECTED), is reaped, exit 0.
FAIL = it is refused as compose-managed — the fix did not take.

**Then the half that matters more.** Confirm the exception fails CLOSED:

```powershell
docker create --name t7-runtime --label ai-stack.harness.owner=t7b `
  --label com.docker.compose.config-hash=deadbeef `
  --label com.docker.compose.container-number=1 `
  --label com.docker.compose.oneoff=False openbrain-mcp-server:local sleep 1
.\scripts\agent-harness\reap.ps1 -Owner t7b
```

PASS = REFUSED as compose-managed, non-zero exit, container survives. This one is built
from the SAME compose-labelled image, so image-inheritance is satisfied and ONLY the
runtime-key check stands between it and deletion.
FAIL = it is deleted. That is a genuinely compose-managed container being reaped.

Clean up: `docker rm -f t7-runtime` (and `t7-inherit` if it survived).

## T8 - the live stack did not become reapable

The single most important check in this plan. Run it AFTER T7.

```powershell
$r = (.\scripts\agent-harness\reap.ps1 -Report 2>&1 6>&1 | Out-String -Width 250)
# every compose-managed name must appear only on a PROTECTED line, or not at all
docker ps -aq --filter label=com.docker.compose.project | Measure-Object
docker ps -aq --filter label=com.docker.compose.config-hash | Measure-Object
```

**Compare the SETS, not the counts.** Equal counts are not equal sets, and a tester on
attempt 2 had to work around this case being written in counts. Run the difference in
both directions for each runtime key:

```powershell
$proj = @(docker ps -aq --filter label=com.docker.compose.project)
foreach ($k in 'config-hash','container-number','oneoff') {
  $rt = @(docker ps -aq --filter "label=com.docker.compose.$k")
  "only-project ($k): " + (@(Compare-Object $proj $rt | Where-Object SideIndicator -eq '<=').Count)
  "only-runtime ($k): " + (@(Compare-Object $proj $rt | Where-Object SideIndicator -eq '=>').Count)
}
```

PASS = every difference is EMPTY in both directions (so no compose-managed container
qualifies for the inheritance exception), the PROTECTED count is unchanged from before
this change, and no production container appears as a candidate.
FAIL = the counts differ — then say WHICH containers carry `project` without the runtime
keys, because each of those is one owner-label away from being reapable, and the guard
needs rethinking rather than patching.

Also seed the verifier red both ways and confirm each turns CASE 7g red:

```powershell
# restore the old key-presence guard
(Get-Content reap.ps1) -replace 'if \(\$isLabelled -and \(\$kind -eq "container"\) -and \(\$composeRuntime -notcontains \$id\) -and \(Test-ComposeInherited \$id\)\) \{', 'if ($false) {' | Set-Content reap.red-inherit.ps1 -Encoding ascii
.\verify-reap.ps1 -Script .\reap.red-inherit.ps1     # expect 65/1, "the INHERITED-label container is reaped"

# remove the fail-closed half
(Get-Content reap.ps1) -replace '\$composeRuntime -notcontains \$id', '$true' | Set-Content reap.red-noruntime.ps1 -Encoding ascii
.\verify-reap.ps1 -Script .\reap.red-noruntime.ps1   # expect 63/3, "the RUNTIME-KEYED container is REFUSED and survives"
```

Green run: **66 passed, 0 failed** (2026-09-08). Delete both copies afterwards.

FAIL = either seed stays green. The second one already did once: the runtime-keyed
fixture was built from plain `alpine`, whose image carries no compose label, so
inheritance refused it on its own and the runtime-key check was never what kept it
alive. A seed that stays green means the case is not testing the guard it names.

## T10 - FOLLOW WHAT THE CORRECTION CITES, and grep the retracted words

T6 holds the findings note to account for what the CODE now does. T9 holds the changed
file's own header to account. Neither holds a document the corrected text POINTS AT - and
that is the hole attempt 1 fell through: `reap.ps1`'s new header ends "See finding 15 in
the sink for the measurement", and finding 15 still carried, verbatim, the two sentences
the same commit had just deleted from `reap.ps1`. A reader following the corrected header's
own citation arrived at the model the correction exists to retract.

Cheap enough that there is no excuse for skipping any of them:

    # 1. every distinctive phrase the correction REMOVED, across the whole repo
    git diff <base>..HEAD -- <changed file> | grep '^-' | <pick the load-bearing phrases>
    grep -rn "<phrase>" . --include=*.md --include=*.ps1 --include=*.sh

    # 1b. AND THE MODEL, not only the phrasing. Write the retracted CLAIM out in
    #     one sentence, then list the ways it could be said, and grep for each.
    #     A phrase-grep alone WILL miss a restatement in different words - and did:
    #     the header said "no image can supply them" while the findings note said
    #     "cannot be inherited from an image because no image has them", the same
    #     model, and a clean phrase-grep passed it for two attempts. The note was
    #     the document the corrected header cited BY PATH.

    # 1c. AND PROVE THE SEARCH CAN FIND ANYTHING AT ALL, once per tool invocation
    #     form, before trusting a clean result:
    grep <same flags> "<a string you know is in the file>" <file>   # must print it

    #     A paraphrase hunt PASSES on no matches, so a broken search is
    #     indistinguishable from a clean repo. On this host `grep -iF` ABORTS
    #     (rc=134) printing nothing, with an EMPTY stderr - the "Aborted" line
    #     comes from bash's job control, so in a pipeline or `$(...)` it is
    #     invisible, and `grep -iF ... | head` reports rc=0 because that is
    #     `head`'s. A tester swept the whole repo with it and got a clean answer
    #     from a tool that had examined zero bytes.
    # FAIL: any hunt reported clean without its positive control in the evidence.

    # 2. everything the corrected text CITES - by finding number, file name or section -
    #    read each one and check it agrees with the new text, not the old

PASS: no removed phrase survives anywhere, and every document the correction cites states
the corrected model.
FAIL: any survivor. **A correction that leaves its own source standing has not been made,
it has been moved** - and the citation makes it worse than an ordinary stale sentence,
because the corrected text actively sends the reader to it.

## T9 - the CHANGED FILE's own documentation still describes it

T6 holds the findings NOTE to account. Nothing held the changed file's own header to
account, and that is exactly the hole a header contradiction slipped through: **one tester
passed it and a reviewer rejected the item for it.**

(This said "three testers". The queue records FAIL, FAIL, PASSED across `drilllabel`'s
three attempts - one pass. And the contradiction only existed from `22b9718` onward, so at
most two testers could have seen it at all. A figure invented to make a case sound better
justified is the same defect the case is about, written into its own rationale.)

`reap.ps1`'s header said "A compose-managed resource is never reaped: containers
carrying `com.docker.compose.project`" while line 298 reaped exactly those. Worse, the
header names "a prod container would not have our label anyway" as THE LAZY ARGUMENT and
refuses to rest on it — and the new comment called the ownership label "the load-bearing
gate", resting on it.

For every file whose BEHAVIOUR this branch changes, read its own header and doc comments
and ask whether they describe the code as it now is:

```powershell
git diff refactor/ai-stack-cleanup...work/drilllabel --name-only
```

For each, check specifically:

- Does any sentence state a CRITERION (what is protected, what is deleted, when) that the
  code no longer implements?
- Does any new comment rest on reasoning the surrounding file explicitly rejects? A file
  that argues against an argument and then uses it is incoherent even when both halves
  are individually defensible.
- Does `README.md`'s description of the same behaviour agree with the code and with the
  file's header?

PASS = every behavioural claim in a changed file matches that file's code.
FAIL = any file whose documentation would make a reader predict the wrong behaviour.
This is not a wording case — a safety contract that says the tool never deletes what it
now deletes is a defect, in a script that runs `docker rm -f` against a host with 81
production containers.
