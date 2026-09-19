# harness-owner.ps1 - one place that knows how a check script marks the docker resources
# it creates, so `scripts/agent-harness/reap.ps1` can collect them afterwards.
#
# WHY THE MARK EXISTS, and it is NOT that these scripts clean up badly. The six this library
# is wired into all tear down on a normal exit AND on an exception - three through a
# `finally`, three through a script-level `trap` calling a `Cleanup` function. They are
# careful scripts.
#
# Do not read a count into that. This comment said "every drill here ... three and three",
# which was a claim about NINE scripts made from a survey of six, and the findings note's
# version of it was corrected twice more before it was right. The current numbers live in
# finding 5 of documentation/notes/harness-reap-findings-2026-09-07.md, with the enumeration
# they came from; the last correction happened because a grep for `} finally {` missed a
# `finally` written at column 0 and the miss was reported as a fact about the script.
#
# The gap is structural. NO in-process construct survives the process being KILLED - not
# `finally`, not `trap`, not a `Cleanup` you remembered to call on every abort path. And a
# kill is exactly what happens here: Ctrl+C, a crashed turn, or this workspace's documented
# trap of a background task being killed when a turn ends. On 2026-09-07 that had left ten
# dead containers and two orphan networks on the daemon, the oldest ten days, every one of
# them from a script whose teardown code was present and correct.
#
# So ownership is recorded at CREATION, by the daemon, in the resource itself. The label
# outlives whatever killed the creator, and cleanup then needs no cooperation from the thing
# being cleaned up. KEEP YOUR TEARDOWN - it is still the fast path and it runs on every
# ordinary exit. The label is what makes the unordinary exit recoverable.
#
# THE RULE, and it is worth stating because a half-applied convention is worse than none:
#
#   PERSISTENT resources - `docker run -d`, `docker create`, `docker network create` -
#   ARE labelled. They outlive the process and nothing else will remove them.
#
#   `docker run --rm` is NOT labelled. `--rm` is a DAEMON-side auto-remove (the container
#   carries AutoRemove; the daemon reaps it when it exits), so it survives its client being
#   killed and needs no help from us. Labelling it would add noise to `reap.ps1 -Report`
#   for resources that are already handled.
#
# Usage - one variable near the top of your script, then splice at each creation site:
#
#   . (Join-Path $PSScriptRoot "lib\harness-owner.ps1")
#   $OWNER = "my-drill-$RunId"
#   Write-Host (Format-HarnessOwnerBanner $OWNER)          # so a reader can reap by hand
#   ...
#   & docker network create (Get-HarnessOwnerLabel $OWNER) $NET
#   & docker run -d --name $C (Get-HarnessOwnerLabel $OWNER) --network $NET $IMAGE
#
# and after a run that died: `scripts\agent-harness\reap.ps1 -Owner my-drill-<runid>`.
#
# WHY ONE TOKEN (`--label=k=v`) AND NOT TWO (`--label k=v`). Because it splices into any
# call shape without caring how that call is built - the six drills assemble their
# `docker run` lines very differently, several across backtick continuations, and a
# two-token flag would have meant editing each of those constructions. Docker accepts the
# `=` form for `run`, `create` and `network create` alike (all three verified against the
# daemon).
#
# IT IS A CHOICE, NOT A NECESSITY, and the first version of this comment said otherwise:
# it claimed PowerShell space-joins an inline array into one argument, so a two-token flag
# COULD NOT work. That is false, with the sign inverted - measured on the work line,
# `& docker create --name x @("--label","k=v") alpine true` exits 0 and applies the label.
# What actually failed was a NESTED array of my own making: this file's own helper returned
# `,@("--label","k=v")` and a call site wrapped it again as `@(Get-Args ...)`, producing an
# array containing an array, which a native call flattens into one space-joined argument.
# Self-inflicted, mistaken for a language rule. See finding 9 (and the correction to
# finding 2) in documentation/notes/harness-reap-findings-2026-09-07.md.

function Get-HarnessOwnerLabelKey {
    # THE KEY IS READ, NOT COPIED. `scripts/agent-harness/harness.config.json` is the single
    # source of truth (`reap.owner_label`), and reap.ps1 reads the same value. A second
    # hardcoded spelling here would mean resources labelled under one name and reaped under
    # another - which does not fail loudly, it just makes every sweep quietly find nothing.
    #
    # The literal below is a FALLBACK, not a duplicate: it covers a checkout where the
    # config file is absent or predates the `reap` key (that key ships with reap.ps1, so any
    # branch without one has neither). A fallback that matched nothing would turn a missing
    # config into unlabelled resources; this one keeps the drills correct either way.
    $fallback = "ai-stack.harness.owner"
    $cfg = Join-Path $PSScriptRoot "..\..\agent-harness\harness.config.json"
    if (-not (Test-Path $cfg)) { return $fallback }
    try {
        $json = Get-Content -Raw -Path $cfg | ConvertFrom-Json
        $reap = $json.PSObject.Properties["reap"]
        if (-not $reap) { return $fallback }
        $key = $reap.Value.PSObject.Properties["owner_label"]
        if (-not $key -or -not $key.Value) { return $fallback }
        return [string]$key.Value
    } catch { return $fallback }
}

function Get-HarnessOwnerLabel {
    # The single `--label=k=v` token to splice into a docker command. See the header for why
    # it is one token and not two.
    #
    # An EMPTY owner THROWS rather than returning "". Returning an empty string would splice
    # a stray empty argument into the docker command line; returning nothing would silently
    # create an unlabelled resource, which is the exact outcome this file exists to prevent
    # and the one nobody would notice until a sweep came back empty. Every call site here
    # sets its owner from a constant or a run id, so a throw means a real bug, loudly.
    param([string]$Owner)
    if (-not $Owner) { throw "Get-HarnessOwnerLabel: empty owner - a resource labelled with nothing cannot be reaped" }
    return ("--label={0}={1}" -f (Get-HarnessOwnerLabelKey), $Owner)
}

function Format-HarnessOwnerBanner {
    # Printed by each drill at start-up. The point is that someone reading the OUTPUT of a
    # run that died can reap it without opening the source: the id has to be on the screen,
    # not only in a variable.
    param([string]$Owner)
    return ("  harness owner label : {0}={1}" -f (Get-HarnessOwnerLabelKey), $Owner) + "`n" +
           ("  if this run dies    : scripts\agent-harness\reap.ps1 -Owner {0}" -f $Owner)
}
