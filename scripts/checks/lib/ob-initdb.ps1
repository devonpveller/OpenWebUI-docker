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

# Start the container and WAIT FOR THE RIGHT MARKER.
#
# "database system is ready to accept connections" appears TWICE: once for the temporary
# server postgres runs the initdb scripts against, and again for the real one. Polling for
# it returns BEFORE the migrations have run, and every verify query then reports 0 for
# everything the chain was supposed to create. (A fixed `Start-Sleep 18` had the same race,
# less visibly - it only ever passed because nothing downstream was asserted.)
# "PostgreSQL init process complete" is the entrypoint's own end-of-initdb marker.
#
# Returns $true when initdb finished within the timeout.
function Start-ObInitdb {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$InitDir,
        [string[]]$DockerArgs = @(),
        [int]$TimeoutSec = 180
    )
    docker rm -f $Name 2>$null | Out-Null
    $run = @("run", "-d", "--name", $Name,
             "-e", "POSTGRES_DB=openbrain", "-e", "POSTGRES_USER=postgres",
             "-e", "POSTGRES_PASSWORD=test",
             "-v", "${InitDir}:/docker-entrypoint-initdb.d:ro") + $DockerArgs +
             @("pgvector/pgvector:pg16")
    docker @run | Out-Null
    for ($i = 0; $i -lt [int]($TimeoutSec / 2); $i++) {
        Start-Sleep 2
        if (docker logs $Name 2>&1 | Select-String -Quiet "PostgreSQL init process complete") {
            return $true
        }
    }
    return $false
}

# initdb errors that are NOT errors: the entrypoint's own DROP ... IF EXISTS chatter.
function Get-ObInitdbErrors {
    param([Parameter(Mandatory)][string]$Name)
    return @(docker logs $Name 2>&1 | Select-String -Pattern "ERROR|FATAL" |
             Where-Object { $_ -notmatch "does not exist, skipping" })
}
