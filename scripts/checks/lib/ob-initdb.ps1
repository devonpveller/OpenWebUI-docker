# ob-initdb.ps1 - bring up a THROWAWAY OpenBrain database on the real initdb chain.
#
# Extracted from test-quartz4-offline.ps1 when the agent-memory smoke script needed the
# same database. Copying it would have been the exact failure that harness already caught
# twice: a hardcoded chain that went stale (it stopped at 88-init-import-jobs while compose
# mounted twenty files), and docker-compose.preview.yml keeping its own copy that drifted
# eight migrations behind. A second copy here would have been the third instance.
#
# Nothing in this file asserts. It derives and it starts; the CALLER decides what a missing
# file or a failed start means, because the two callers judge them differently.

# The chain, DERIVED FROM COMPOSE - source filename and the numbered name it is mounted as,
# in the order compose lists them (which is the order initdb runs them).
function Get-ObInitChain {
    param([Parameter(Mandatory)][string]$ComposePath)
    $map = @()
    $rx = [regex]'\./(init[a-z0-9.\-]*\.sql):/docker-entrypoint-initdb\.d/([0-9a-z]+-init[a-z0-9.\-]*\.sql)'
    foreach ($m in $rx.Matches((Get-Content -Raw $ComposePath))) {
        $map += , @($m.Groups[1].Value, $m.Groups[2].Value)
    }
    return $map
}

# Stage the chain into a directory suitable for mounting at /docker-entrypoint-initdb.d.
# Returns the count staged, so a caller can tell "nothing was staged" from "it worked".
function Copy-ObInitChain {
    param(
        [Parameter(Mandatory)][array]$Chain,
        [Parameter(Mandatory)][string]$SourceDir,
        [Parameter(Mandatory)][string]$TargetDir
    )
    Remove-Item $TargetDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $TargetDir -Force | Out-Null
    $n = 0
    foreach ($m in $Chain) {
        $src = Join-Path $SourceDir $m[0]
        if (Test-Path $src) { Copy-Item $src (Join-Path $TargetDir $m[1]); $n++ }
    }
    return $n
}

# ------------------------------------------------------------------------------------------
# THE WAIT BUDGET - MEASURED, then chosen. Do not multiply the old number by a nicer one.
# ------------------------------------------------------------------------------------------
# The previous budget was a hardcoded 180s, and the drill that uses it reported a machine
# that ran out of it as "initdb did not complete in 180s" - a sentence a reader turns into
# "the boundary is broken" when it means "we never got to look".
#
# MEASURED on this machine, 2026-08-31, in the loaded state the drill must survive
# (81-84 containers of the normal stack running throughout):
#
#   28-file chain, sequential, n=4 : 8.0 / 5.5 / 5.6 / 5.7 s   (mean 6.2s)
#   28-file chain, EIGHT AT ONCE   : 11.5 / 11.8 / 12.0 / 12.2 / 12.5 / 12.7 / 13.8 / 14.0 s
#                                    (n=8, mean 12.6s - the contended case, 8 initdbs racing)
#
# So initdb is a ~6s job that degrades to ~14s under 8x self-contention: the old 180s was
# already 13x the worst measurement, and a machine that exceeds it is NOT merely busy -
# something has gone wrong that a bigger number will not fix. That is the whole reason the
# outcome below is a CLASSIFICATION and not a boolean.
#
# The ceiling is nevertheless generous (600s = 43x the contended worst), because this is a
# CEILING, not a sleep: the wait returns the instant the entrypoint prints its marker, so
# raising it costs nothing on a healthy run and only bounds a pathological one. A container
# that dies, or never starts, is detected in the same second it happens - it does not sit
# out the ceiling. Override with OB_INITDB_TIMEOUT_SEC when a slower machine needs it.
function Get-ObInitdbTimeoutSec {
    $v = $env:OB_INITDB_TIMEOUT_SEC
    if ($v) {
        $n = 0
        if ([int]::TryParse($v, [ref]$n) -and $n -gt 0) { return $n }
    }
    return 600
}

# The last few log lines, flattened onto one line - what a timeout has to hand the reader so
# the next slow run is diagnosable instead of mysterious.
function Get-ObInitdbLogTail {
    param([Parameter(Mandatory)][string]$Name, [int]$Lines = 12)
    $t = (& docker logs --tail $Lines $Name 2>&1 | Out-String)
    $flat = (($t -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ' | ')
    if ($flat) { return $flat } else { return '(no log output)' }
}

# Start the container and WAIT FOR THE RIGHT MARKER, then say WHAT HAPPENED.
#
# "database system is ready to accept connections" appears TWICE: once for the temporary
# server postgres runs the initdb scripts against, and again for the real one. Polling for
# it returns BEFORE the migrations have run, and every verify query then reports 0 for
# everything the chain was supposed to create. (A fixed `Start-Sleep 18` had the same race,
# less visibly - it only ever passed because nothing downstream was asserted.)
# "PostgreSQL init process complete" is the entrypoint's own end-of-initdb marker.
#
# Returns a hashtable, because "false" was not enough information:
#   Ready      $true only for Outcome 'ready'
#   Outcome    'ready'         initdb finished; the marker was seen
#              'start-failed'  `docker run` itself failed - reported IMMEDIATELY, with the
#                              daemon's own message, instead of being served up as a timeout
#                              after the full ceiling. This is the shape that turns any
#                              environment problem into "did not complete in N seconds".
#              'exited'        the container left the running state before the marker (an
#                              initdb script aborted, OOM, the daemon killed it)
#              'container-gone' `docker inspect` can no longer see it (a concurrent rm, a
#                              daemon restart, a name reused by another run)
#              'timeout'       still running, still no marker, ceiling reached
#   ElapsedSec how long it actually took - printed by callers so a slow run leaves evidence
#   BudgetSec  the ceiling in force, so the reader can tell 12s-of-600 from 599s-of-600
#   Detail     the container state and the tail of its log, for everything but 'ready'
#
# NOTE ON HEALTH STATE: `pgvector/pgvector:pg16` ships NO HEALTHCHECK, so a container started
# by `docker run` here has State.Health = null and there is no health state to poll - the
# entrypoint's marker plus State.Status is the whole signal. (The drill's section 9 adds a
# healthcheck in compose and polls exactly that; this function is the plain-`docker run`
# path.) Claiming to poll a health state that does not exist would be the vacuous-check
# pattern this drill exists to catch.
function Start-ObInitdbDetailed {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$InitDir,
        [string[]]$DockerArgs = @(),
        [int]$TimeoutSec = 0
    )
    if ($TimeoutSec -le 0) { $TimeoutSec = Get-ObInitdbTimeoutSec }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $result = @{ Ready = $false; Outcome = 'timeout'; ElapsedSec = 0.0; BudgetSec = $TimeoutSec; Detail = '' }

    & docker rm -f $Name 2>&1 | Out-Null
    $run = @("run", "-d", "--name", $Name,
             "-e", "POSTGRES_DB=openbrain", "-e", "POSTGRES_USER=postgres",
             "-e", "POSTGRES_PASSWORD=test",
             "-v", "${InitDir}:/docker-entrypoint-initdb.d:ro") + $DockerArgs +
             @("pgvector/pgvector:pg16")
    $runOut = (& docker @run 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        $sw.Stop()
        $result.Outcome    = 'start-failed'
        $result.ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $result.Detail     = "docker run exited $LASTEXITCODE : $runOut"
        return $result
    }

    $lastState = 'unknown'
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        Start-Sleep -Milliseconds 1000
        if ((& docker logs $Name 2>&1 | Out-String) -match 'PostgreSQL init process complete') {
            $sw.Stop()
            $result.Ready      = $true
            $result.Outcome    = 'ready'
            $result.ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            return $result
        }
        $st = (& docker inspect --format '{{.State.Status}}/{{.State.ExitCode}}' $Name 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            $sw.Stop()
            $result.Outcome    = 'container-gone'
            $result.ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            $result.Detail     = "docker inspect can no longer see $Name : $st"
            return $result
        }
        $lastState = $st
        $status = ($st -split '/')[0]
        if ($status -ne 'running' -and $status -ne 'created' -and $status -ne 'restarting') {
            $sw.Stop()
            $result.Outcome    = 'exited'
            $result.ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            $result.Detail     = "container state status/exitcode = $st before the initdb marker; log tail: $(Get-ObInitdbLogTail -Name $Name)"
            return $result
        }
    }
    $sw.Stop()
    $result.ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $result.Detail     = "still status/exitcode = $lastState at the ceiling; log tail: $(Get-ObInitdbLogTail -Name $Name)"
    return $result
}

# Boolean wrapper, kept because four other scripts call it and judge only "did it come up".
# They inherit the fail-fast and the measured ceiling; nothing about their call sites changes.
function Start-ObInitdb {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$InitDir,
        [string[]]$DockerArgs = @(),
        [int]$TimeoutSec = 0
    )
    return (Start-ObInitdbDetailed -Name $Name -InitDir $InitDir -DockerArgs $DockerArgs -TimeoutSec $TimeoutSec).Ready
}

# initdb errors that are NOT errors: the entrypoint's own DROP ... IF EXISTS chatter.
function Get-ObInitdbErrors {
    param([Parameter(Mandatory)][string]$Name)
    return @(docker logs $Name 2>&1 | Select-String -Pattern "ERROR|FATAL" |
             Where-Object { $_ -notmatch "does not exist, skipping" })
}
