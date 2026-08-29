# lease.ps1 - named exclusive leases for agents sharing one environment.
#
# THE PRIMITIVE: when environments are too expensive or too stateful to clone (GPU
# planes, live DBs, a wiki vault mid-drain), mature systems serialize access instead -
# GitHub Actions `concurrency.group`, Bazel `exclusive` test tags, Kubernetes Lease
# objects. This is that primitive as one self-contained script: a name, an owner, a TTL.
# Agents hold a plane's lease while running tests that mutate it or need it stable, and
# hold the `merge` lease while landing a branch. Read-only probes need no lease.
#
# MECHANISM vs POLICY: this file is generic mechanism - it knows nothing about this
# stack. The valid lease names live in `lease-names.conf` beside it (one per line);
# unknown names are refused so a typo cannot silently fragment mutual exclusion
# ("openbrain" vs "open-brain" would be two locks and zero safety). Port this to
# another environment by replacing the conf file - or deleting it, which disables
# name validation entirely. State lives in `state/locks/` beside the script,
# overridable with AI_STACK_LEASE_DIR (the tests use that to stay hermetic).
#
# MULTI-NAME = ONE CALL: a test spanning planes must request them together
# (`-Name "frontend,open-brain"`). Names are sorted and acquired in canonical order,
# and on any failure the ones just taken are rolled back (all-or-nothing) - between
# them, those two rules remove the classic two-agent deadlock outright.
#
# Atomicity: CreateNew on the lock file - no read-then-write window. The only native
# call is git, via common.ps1, purely to locate the shared lock namespace.
#
# Usage:
#   .\lease.ps1 -Acquire -Name open-brain -Owner wt-wiki-perf -Thread <mm-root>
#   .\lease.ps1 -Acquire -Name "frontend,open-brain" -Owner wt-x   # multi, one call
#   .\lease.ps1 -Refresh -Name open-brain -Owner wt-wiki-perf      # long build: keep alive
#   .\lease.ps1 -Release -Name open-brain -Owner wt-wiki-perf
#   .\lease.ps1 -Status                                            # who holds what
#   .\lease.ps1 -Takeover -Name open-brain -Owner wt-b             # EXPIRED leases only
#
# Exit codes: 0 ok | 1 usage/config error | 3 blocked (held by someone else - WAIT)

[CmdletBinding()]
param(
    [switch]$Acquire,
    [switch]$Release,
    [switch]$Refresh,
    [switch]$Status,
    [switch]$Takeover,
    [string]$Name = "",
    [string]$Owner = "",
    [string]$Worktree = "",
    [string]$Thread = "",
    [int]$TtlMin = 0,
    [switch]$AdHoc
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

# The module OFF switch. "Off" must be inert and say so, not fail obscurely three calls
# deeper - see harness.config.json / MODULE.md.
# See the note in queue.ps1: param() binds before config is available, so 0 means "unset".
if (-not $PSBoundParameters.ContainsKey("TtlMin")) {
    $TtlMin = [int](Get-HarnessSetting "leases.default_ttl_minutes" 30)
}

$offReason = Get-HarnessDisabledReason
if ($offReason) { Write-Host "REFUSED: $offReason" -ForegroundColor Yellow; exit 2 }
# The lock namespace must be ONE per repository, shared by the main checkout and every
# worktree - see common.ps1. Anchoring it on $PSScriptRoot (as this did) meant a copy of
# this toolkit inside a worktree got its OWN gitignored lock dir, so two agents could each
# be told "ACQUIRED" for `merge` and exclude nobody. AI_STACK_LEASE_DIR still overrides.
$LockDir = if ($env:AI_STACK_LEASE_DIR) { $env:AI_STACK_LEASE_DIR }
           else { Join-Path (Get-SharedStateDir) "locks" }
$NamesFile = if ($env:AI_STACK_LEASE_NAMES_FILE) { $env:AI_STACK_LEASE_NAMES_FILE }
             else { Join-Path $PSScriptRoot (Get-HarnessSetting "leases.names_file" "lease-names.conf") }

function Now() { return [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

function Read-Lease([string]$LeaseName) {
    $f = Join-Path $LockDir "$LeaseName.json"
    if (-not (Test-Path $f)) { return $null }
    try { return Get-Content -Raw -Path $f | ConvertFrom-Json } catch { return $null }
}

function Test-Expired($L) {
    # -ge, not -gt: at exactly the TTL a lease IS expired. With -gt a ttl of 0 ("treat
    # me as already dead") never expired and a dead agent could hold the queue forever.
    return ((Now) - [int64]$L.taken_at) -ge ([int]$L.ttl_min * 60)
}

function Show-Lease([string]$LeaseName, $L) {
    if (-not $L) { Write-Host ("lease {0}: FREE" -f $LeaseName) -ForegroundColor Green; return }
    $age = (Now) - [int64]$L.taken_at
    $state = if (Test-Expired $L) { "EXPIRED" } else { "HELD" }
    $color = if ($state -eq "EXPIRED") { "Yellow" } else { "Cyan" }
    Write-Host ("lease {0}: {1} by {2} ({3}m of {4}m ttl; thread {5})" -f `
        $LeaseName, $state, $L.owner, [int]($age / 60), $L.ttl_min,
        $(if ($L.thread) { $L.thread } else { "-" })) -ForegroundColor $color
}

function Write-LeaseFile([string]$LeaseName, $Payload, [bool]$MustBeNew) {
    $f = Join-Path $LockDir "$LeaseName.json"
    $json = ($Payload | ConvertTo-Json -Depth 4)
    if ($MustBeNew) {
        # The atomic claim: CreateNew fails if the file exists - the loser of a race
        # gets an IOException, never a silent overwrite.
        $fs = [System.IO.File]::Open($f, [System.IO.FileMode]::CreateNew,
                                     [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $bytes = [System.Text.Encoding]::ASCII.GetBytes($json)
            $fs.Write($bytes, 0, $bytes.Length)
        } finally { $fs.Close() }
    } else {
        $tmp = "$f.tmp"
        $json | Set-Content -Path $tmp -Encoding ASCII
        Move-Item -Path $tmp -Destination $f -Force
    }
}

if (-not (Test-Path $LockDir)) { New-Item -ItemType Directory -Force -Path $LockDir | Out-Null }

# ---- status needs no names ---------------------------------------------------------
if ($Status -and -not $Name) {
    $files = @(Get-ChildItem -Path $LockDir -Filter "*.json" -ErrorAction SilentlyContinue)
    if (-not $files.Count) { Write-Host "no leases held - all planes free" -ForegroundColor Green; exit 0 }
    foreach ($f in ($files | Sort-Object Name)) {
        Show-Lease $f.BaseName (Read-Lease $f.BaseName)
    }
    exit 0
}

# ---- parse + validate names --------------------------------------------------------
$names = @($Name -split "[,\s]+" | Where-Object { $_ } |
           ForEach-Object { $_.ToLower() } | Sort-Object -Unique)
if (-not $names.Count) {
    Write-Host "ERROR: -Name is required (comma-separate a multi-plane set: -Name 'coder,frontend')" -ForegroundColor Red
    exit 1
}
foreach ($n in $names) {
    if ($n -notmatch '^[a-z0-9][a-z0-9._-]{0,63}$') {
        Write-Host ("ERROR: invalid lease name '{0}' (lowercase alphanumeric . _ -)" -f $n) -ForegroundColor Red
        exit 1
    }
}
if ((Test-Path $NamesFile) -and -not $AdHoc) {
    $known = @(Get-Content $NamesFile | ForEach-Object { ($_ -split "#")[0].Trim() } |
               Where-Object { $_ })
    $unknown = @($names | Where-Object { $known -notcontains $_ })
    if ($unknown.Count) {
        Write-Host ("ERROR: unknown lease name(s): {0}" -f ($unknown -join ", ")) -ForegroundColor Red
        Write-Host ("  Known ({0}): {1}" -f $NamesFile, ($known -join ", "))
        Write-Host "  A typo here would create a SECOND lock for the same plane and protect nothing."
        Write-Host "  If this is a deliberate new coordination point, pass -AdHoc (and consider adding it to the conf)."
        exit 1
    }
}

if ($Status) { foreach ($n in $names) { Show-Lease $n (Read-Lease $n) }; exit 0 }

if (-not $Owner) { Write-Host "ERROR: -Owner is required (use your worktree id)" -ForegroundColor Red; exit 1 }

# ---- acquire / takeover: sorted, all-or-nothing ------------------------------------
if ($Acquire -or $Takeover) {
    $taken = @()   # names newly claimed by THIS call - rolled back on failure
    foreach ($n in $names) {
        $existing = Read-Lease $n
        if ($existing -and $existing.owner -eq $Owner) {
            continue   # already mine from an earlier call: idempotent, not rolled back
        }
        if ($existing -and -not (Test-Expired $existing)) {
            Show-Lease $n $existing
            Write-Host ("WAIT: '{0}' is held. Poll no more than once a minute; do not force." -f $n) -ForegroundColor Yellow
            foreach ($t in $taken) { Remove-Item (Join-Path $LockDir "$t.json") -Force }
            if ($taken.Count) { Write-Host ("  (rolled back {0} - all-or-nothing, so a partial set never blocks others)" -f ($taken -join ", ")) }
            exit 3
        }
        if ($existing) {   # expired
            if (-not $Takeover) {
                Show-Lease $n $existing
                Write-Host ("'{0}' is EXPIRED. Re-run with -Takeover to claim it, AND post a note in the previous" -f $n) -ForegroundColor Yellow
                Write-Host "owner's thread - a stuck lease usually means a dead agent, and its half-done work" -ForegroundColor Yellow
                Write-Host "lives only in ITS worktree (the shared line is untouched)." -ForegroundColor Yellow
                foreach ($t in $taken) { Remove-Item (Join-Path $LockDir "$t.json") -Force }
                exit 3
            }
            Write-Host ("Taking over expired lease '{0}' from {1}." -f $n, $existing.owner) -ForegroundColor Yellow
            Remove-Item (Join-Path $LockDir "$n.json") -Force
        }
        $payload = [ordered]@{
            name = $n; owner = $Owner; worktree = $Worktree; thread = $Thread
            taken_at = Now; ttl_min = $TtlMin; pid = $PID
        }
        try {
            Write-LeaseFile $n $payload $true
        } catch [System.IO.IOException] {
            Write-Host ("WAIT: another agent claimed '{0}' a moment ago." -f $n) -ForegroundColor Yellow
            foreach ($t in $taken) { Remove-Item (Join-Path $LockDir "$t.json") -Force }
            exit 3
        }
        $taken += $n
    }
    Write-Host ("Lease(s) ACQUIRED by {0}: {1} (ttl {2}m)." -f $Owner, ($names -join ", "), $TtlMin) -ForegroundColor Green
    Write-Host "  Release as soon as the work lands - other agents may be waiting."
    exit 0
}

# ---- refresh -----------------------------------------------------------------------
if ($Refresh) {
    foreach ($n in $names) {
        $existing = Read-Lease $n
        if (-not $existing) { Write-Host ("ERROR: no lease '{0}' to refresh" -f $n) -ForegroundColor Red; exit 1 }
        if ($existing.owner -ne $Owner) {
            Show-Lease $n $existing
            Write-Host ("ERROR: '{0}' belongs to {1}, not {2}" -f $n, $existing.owner, $Owner) -ForegroundColor Red
            exit 3
        }
        $existing.taken_at = Now
        Write-LeaseFile $n $existing $false
    }
    Write-Host ("Refreshed {0} lease(s) for {1}." -f $names.Count, $Owner) -ForegroundColor Green
    exit 0
}

# ---- release -----------------------------------------------------------------------
if ($Release) {
    foreach ($n in $names) {
        $existing = Read-Lease $n
        if (-not $existing) { Write-Host ("lease '{0}' already free." -f $n) -ForegroundColor Green; continue }
        if ($existing.owner -ne $Owner) {
            Show-Lease $n $existing
            Write-Host ("ERROR: refusing to release '{0}' - it is held by {1}, you are {2}. Use -Takeover only when EXPIRED." -f $n, $existing.owner, $Owner) -ForegroundColor Red
            exit 3
        }
        Remove-Item (Join-Path $LockDir "$n.json") -Force
        Write-Host ("Released '{0}' ({1})." -f $n, $Owner) -ForegroundColor Green
    }
    exit 0
}

Write-Host "ERROR: pass one of -Acquire | -Refresh | -Release | -Status | -Takeover" -ForegroundColor Red
exit 1
