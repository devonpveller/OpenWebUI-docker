# scripts/backup/test-nas-sync.ps1
#
# Tests for the weekly NAS job and the one-time archive copy, run against LOCAL
# stand-in directories only - no SMB, no NAS, no Docker. Every path it writes is
# under a fresh directory in %TEMP% that it removes at the end (-Keep leaves it).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup\test-nas-sync.ps1
#
# -Script / -Lib / -CopyScript point the tests at other copies (the base RED run
# swaps in the 0fb1c0c backup-to-nas.ps1; the mutation runs swap in mutants).
# Exit = number of failed checks.
#
# Sections:
#   S  the job's wiring, read from its AST/text;
#   R  the pure rules in nas-sync-lib.ps1 (archive root, normalisation, arguments);
#   M  robocopy's own semantics with the lib's argument arrays on stand-in trees;
#   C  copy-archives-to-nas.ps1 run as a process against a stand-in archive root;
#   J  backup-to-nas.ps1 RUN END TO END in a child PowerShell, inside a throw-away
#      project tree, with net.exe / Resolve-DnsName / the alerter stubbed and the
#      NAS share \\192.0.2.77\backups (TEST-NET-1: not routable) MAPPED onto a local
#      directory: robocopy.exe, Get-ChildItem, Test-Path and Get-Item see the local
#      directory; any other UNC path is refused by the stubs and reported as
#      UNMAPPED-UNC (a check fails on it), so no stub path reaches a real share;
#   K  copy-archives-to-nas.ps1 under the same stubs (FAIL COPY, a rename race,
#      -Connect without and with a reachable share).

[CmdletBinding()]
param(
  [string]$Script,
  [string]$Lib,
  [string]$CopyScript,
  [switch]$Keep
)

$ErrorActionPreference = 'Stop'
if (-not $Script) { $Script = Join-Path $PSScriptRoot 'backup-to-nas.ps1' }
if (-not $Lib) { $Lib = Join-Path $PSScriptRoot 'nas-sync-lib.ps1' }
if (-not $CopyScript) { $CopyScript = Join-Path $PSScriptRoot 'copy-archives-to-nas.ps1' }

$script:pass = 0; $script:fail = 0
function Check([string]$Name, [bool]$Cond, [string]$Detail = '') {
  if ($Cond) { $script:pass++; Write-Host "  ok   $Name" }
  else { $script:fail++; Write-Host "  FAIL $Name $Detail" -ForegroundColor Red }
}
function Sha256Of([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() }
function TreeHashes([string]$root) {
  $m = @{}
  if (Test-Path -LiteralPath $root) {
    foreach ($f in Get-ChildItem -LiteralPath $root -Recurse -File -Force) { $m[$f.FullName.Substring($root.Length)] = Sha256Of $f.FullName }
  }
  return $m
}
function SameTree($a, $b) {
  if ($a.Count -ne $b.Count) { return $false }
  foreach ($k in $a.Keys) { if (-not $b.ContainsKey($k) -or $b[$k] -ne $a[$k]) { return $false } }
  return $true
}
function Rc([string[]]$argv) { & robocopy.exe @argv | Out-Null; return $LASTEXITCODE }

# Path spellings that all normalise INTO slot-A of <root>\portal (tester attempt 1, F2).
$SlotSpellings = @('\portal\x\..\slot-A', '\portal\.\slot-A', '\portal/slot-A', '\portal\slot-A.',
  '\portal\slot-A ', '\portal\\slot-A', '\archive\..\portal\slot-A', '\portal\slot-A\', '\PORTAL\SLOT-a\deep',
  '\portal\slot-A.\x', '\portal\slot-A \x', '\portal\slot-B...\x')

# ---------------------------------------------------------------- structure
Write-Host "== S: backup-to-nas.ps1 wiring ($Script)"
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$errs)
Check 'S1 the job parses under this PowerShell' ($errs.Count -eq 0) ($errs | Out-String)
$text = [System.IO.File]::ReadAllText($Script)
$robocopyCalls = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'robocopy.exe' }, $true))
$callText = @($robocopyCalls | ForEach-Object { $_.Extent.Text })
Check 'S2 the job dot-sources nas-sync-lib.ps1' ($text -match "\.\s+\(Join-Path \`$PSScriptRoot 'nas-sync-lib\.ps1'\)")
Check 'S3 exactly two robocopy calls, both splatting a lib-built argument set' (($callText.Count -eq 2) -and ($callText -contains '& robocopy.exe @mirrorArgs') -and ($callText -contains '& robocopy.exe @archiveArgs')) ($callText -join ' | ')
Check 'S4 the mirror args come from Get-NasSlotMirrorArgs over ./backups' ($text -match '\$mirrorArgs = Get-NasSlotMirrorArgs -Source \$backupSrc -Destination \$nasDest')
Check 'S5 the archive args come from Get-NasArchiveCopyArgs over ./backup to the resolved archive root' (($text -match "\`$archiveSrc = Join-Path \`$projectRoot 'backup'") -and ($text -match '\$archiveArgs = Get-NasArchiveCopyArgs -Source \$archiveSrc -Destination \$archiveDest') -and ($text -match 'Resolve-NasArchiveRoot -NasUncRoot \$NasUncRoot'))
Check 'S6 the archive root is re-judged after the IP rewrite' ($text -match 'Resolve-NasArchiveRoot -NasUncRoot \$slotRootIp -NasArchiveRoot \$archiveIp')
Check 'S7 an archive failure withholds the completion marker' ($text -match '(?s)if \(\$archiveFailed\) \{[^}]*exit 2\s*\}\s*Write-LogLine "=== NAS sync complete ==="')
$bom = [System.IO.File]::ReadAllBytes($Script)
Check 'S8 the job is ASCII without a BOM' (-not ($bom.Length -ge 3 -and $bom[0] -eq 0xEF) -and -not ($bom | Where-Object { $_ -gt 127 }))

if (-not (Test-Path -LiteralPath $Lib)) {
  Check 'L0 nas-sync-lib.ps1 exists' $false $Lib
  Write-Host ("RESULT: passed {0}, failed {1}" -f $script:pass, $script:fail)
  exit $script:fail
}
. $Lib

# ---------------------------------------------------------------- pure rules
Write-Host "== R: archive-root rules"
# A throw where a value is expected is a FAIL of that check, not a crash of the run.
function Try-Call([scriptblock]$Block) { try { & $Block } catch { "THREW: $($_.Exception.Message)" } }
Check 'R1 default is a sibling of the slot root' ((Try-Call { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups\ai-stack\portal' }) -eq '\\nas\backups\ai-stack\archive')
Check 'R2 the scheduled task argument shape (trailing slash) maps to ai-stack\archive' ((Try-Call { Resolve-NasArchiveRoot -NasUncRoot '\\nas-host\backups\ai-stack\portal\' }) -eq '\\nas-host\backups\ai-stack\archive')
Check 'R3 a bare share gets <share>\archive, next to the slots' ((Try-Call { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups' }) -eq '\\nas\backups\archive')
$r4 = @('\\nas\backups\ai-stack\portal\slot-A', '\\nas\backups\ai-stack\portal\SLOT-B\x', '\\nas\other\archive', 'D:\archive',
  '\\?\UNC\nas\backups\ai-stack\archive', 'archive') + @($SlotSpellings | ForEach-Object { '\\nas\backups\ai-stack' + $_ })
foreach ($bad in $r4) {
  $threw = $false; try { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups\ai-stack\portal' -NasArchiveRoot $bad | Out-Null } catch { $threw = $true }
  Check "R4 refuses archive root [$bad]" $threw
}
foreach ($sp in $SlotSpellings) {
  $threw = $false; try { Resolve-NasArchiveRoot -NasUncRoot 'C:\standin\nas\ai-stack\portal' -NasArchiveRoot ('C:\standin\nas\ai-stack' + $sp) | Out-Null } catch { $threw = $true }
  Check "R4b refuses the local spelling [C:\standin\nas\ai-stack$sp]" $threw
}
Check 'R5 an explicit root on the same share outside the slots is honoured, normalised' ((Try-Call { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups\ai-stack\portal' -NasArchiveRoot '\\nas\backups\x\..\cold/' }) -eq '\\nas\backups\cold')
$aa = Try-Call { Get-NasArchiveCopyArgs -Source 'C:\s' -Destination 'C:\d' -LogFile 'C:\l.log' }
Check 'R6 archive args: /E /XC /XN /XO /XX, no /MIR, no /PURGE, no /MOV(E)' (($aa -contains '/E') -and ($aa -contains '/XC') -and ($aa -contains '/XN') -and ($aa -contains '/XO') -and ($aa -contains '/XX') -and -not ($aa | Where-Object { $_ -match '^/(MIR|PURGE|MOV|MOVE)$' })) ($aa -join ' ')
$ma = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -LogFile 'C:\l.log' }
Check 'R7 slot args unchanged from 0fb1c0c: /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+ /NDL' (($ma -join ' ') -eq 'C:\s C:\d /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+:C:\l.log /NDL') ($ma -join ' ')
$md = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -DryRun }
$ad = Try-Call { Get-NasArchiveCopyArgs -Source 'C:\s' -Destination 'C:\d' -DryRun }
Check 'R8 -DryRun adds /L to both' (($md -contains '/L') -and ($ad -contains '/L'))
Check 'R9 ISO week parity: 2026-09-27 (week 39) -> slot-B, 2026-10-04 (week 40) -> slot-A' (((Get-NasSlotName -Date ([datetime]'2026-09-27')) -eq 'slot-B') -and ((Get-NasSlotName -Date ([datetime]'2026-10-04')) -eq 'slot-A'))
$fnText = (Get-Command Get-NasArchiveCopyArgs).Definition
Check 'R10 the archive builder refuses a set lacking /XC /XN /XO /XX (the requirement is in the code)' ($fnText -match "foreach \(\`$req in @\('/E', '/XC', '/XN', '/XO', '/XX'\)\)")

# ---------------------------------------------------------------- robocopy against stand-ins
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("cf-nas-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$proj = Join-Path $root 'proj'
$backups = Join-Path $proj 'backups'
$backup = Join-Path $proj 'backup'
$nasPortal = Join-Path $root 'nas\backups\ai-stack\portal'
$log = Join-Path $root 'robocopy.log'
New-Item -ItemType Directory -Force -Path "$backups\openwebui", "$backup\orphan-volumes-2026-09-13", "$backup\nas-archive-may2025-owui", "$backup\models" | Out-Null
$ps = (Get-Process -Id $PID).Path
try {
  $rnd = New-Object System.Random 7
  function Blob([string]$p, [int]$n) { $b = New-Object byte[] $n; $rnd.NextBytes($b); [System.IO.File]::WriteAllBytes($p, $b) }
  Blob "$backups\openwebui\owui-1.tar.gz" 40000
  Set-Content -LiteralPath "$backups\openwebui\owui-1.tar.gz.sha256" -Value ((Sha256Of "$backups\openwebui\owui-1.tar.gz") + '  /backups/owui-1.tar.gz') -Encoding ascii
  Blob "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar" 3000000
  Blob "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar" 500000
  Blob "$backup\nas-archive-may2025-owui\open-webui.tar" 700000
  Blob "$backup\nas-archive-may2025-owui\may2025-chats-export.json" 12000
  Set-Content -LiteralPath "$backup\nas-archive-may2025-owui\README.md" -Value 'stand-in' -Encoding ascii
  $sums = @('open-webui.tar', 'may2025-chats-export.json') | ForEach-Object { (Sha256Of "$backup\nas-archive-may2025-owui\$_") + " *$_" }
  [System.IO.File]::WriteAllLines("$backup\nas-archive-may2025-owui\SHA256SUMS", [string[]]$sums)
  Blob "$backup\models\models-export.json" 3000
  Set-Content -LiteralPath "$backup\generic-tar-backup.sh" -Value 'echo stand-in' -Encoding ascii
  $srcBefore = TreeHashes $backup
  $backupsBefore = TreeHashes $backups

  Write-Host "== M: slot mirror + archive pass (robocopy, local stand-in share)"
  $archive = Resolve-NasArchiveRoot -NasUncRoot $nasPortal
  Check 'M0 stand-in archive root = sibling of portal' ($archive -eq (Join-Path $root 'nas\backups\ai-stack\archive')) $archive
  $slotA = Join-Path $nasPortal 'slot-A'; $slotB = Join-Path $nasPortal 'slot-B'

  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M1 slot-A mirror ok' ($rc -lt 8) "rc=$rc"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M2 archive pass copies ./backup (rc has bit 1)' (($rc -lt 8) -and ($rc -band 1)) "rc=$rc"
  $arcAfterFirst = TreeHashes $archive
  Check 'M3 archive on the stand-in NAS = ./backup exactly' (SameTree $srcBefore $arcAfterFirst) "src=$($srcBefore.Count) nas=$($arcAfterFirst.Count)"
  Check 'M4 archives did not land in any slot' (-not (Get-ChildItem -LiteralPath $nasPortal -Recurse -File | Where-Object { $_.Name -like '*.tar' }))

  Set-Content -LiteralPath "$slotA\stray.txt" -Value 'x' -Encoding ascii
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M5 slot mirror still purges extras in its slot' ((-not (Test-Path -LiteralPath "$slotA\stray.txt")) -and ($rc -band 2)) "rc=$rc"

  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotB -LogFile $log)
  Check 'M6 slot-B mirror ok' ($rc -lt 8) "rc=$rc"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M7 an unchanged archive pass re-sends nothing (rc=0)' ($rc -eq 0) "rc=$rc"
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M8 archive tree byte-identical after mirrors of both slots' (SameTree $arcAfterFirst (TreeHashes $archive))

  Set-Content -LiteralPath "$archive\operator-note.txt" -Value 'kept' -Encoding ascii
  $gone = Join-Path $root 'moved-out.tar'
  Move-Item -LiteralPath "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar" -Destination $gone
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Move-Item -LiteralPath $gone -Destination "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  Check 'M9 archive pass keeps a file deleted locally' ((Test-Path -LiteralPath "$archive\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar") -and ($rc -lt 8)) "rc=$rc"
  Check 'M10 archive pass keeps a NAS-side extra' (Test-Path -LiteralPath "$archive\operator-note.txt")

  # F1: a CHANGED or TRUNCATED local file must never replace the NAS copy
  $nasBefore = TreeHashes $archive
  $modelsOrig = [System.IO.File]::ReadAllBytes("$backup\models\models-export.json")
  $modelsTime = (Get-Item -LiteralPath "$backup\models\models-export.json").LastWriteTimeUtc
  Add-Content -LiteralPath "$backup\models\models-export.json" -Value 'more' -Encoding ascii
  $tarPath = "$backup\nas-archive-may2025-owui\open-webui.tar"
  $tarOrig = [System.IO.File]::ReadAllBytes($tarPath); $tarTime = (Get-Item -LiteralPath $tarPath).LastWriteTimeUtc
  [System.IO.File]::WriteAllBytes($tarPath, [byte[]]@())
  $drift = @(Get-NasArchiveDrift -Source $backup -Destination $archive)
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M11 a changed local file does NOT replace the NAS copy' ((Sha256Of "$archive\models\models-export.json") -eq $nasBefore['\models\models-export.json']) "rc=$rc"
  Check 'M12 a local file truncated to 0 bytes does NOT replace the NAS copy' (((Get-Item -LiteralPath "$archive\nas-archive-may2025-owui\open-webui.tar").Length -eq 700000) -and ((Sha256Of "$archive\nas-archive-may2025-owui\open-webui.tar") -eq $nasBefore['\nas-archive-may2025-owui\open-webui.tar'])) "rc=$rc"
  Check 'M13 the whole NAS archive tree is unchanged by that pass' (SameTree $nasBefore (TreeHashes $archive))
  Check 'M14 Get-NasArchiveDrift names exactly the two differing files' ((($drift | Sort-Object) -join ',') -eq 'models\models-export.json,nas-archive-may2025-owui\open-webui.tar') ($drift -join ',')
  [System.IO.File]::WriteAllBytes("$backup\models\models-export.json", $modelsOrig); [System.IO.File]::SetLastWriteTimeUtc("$backup\models\models-export.json", $modelsTime)
  [System.IO.File]::WriteAllBytes($tarPath, $tarOrig); [System.IO.File]::SetLastWriteTimeUtc($tarPath, $tarTime)
  $drift2 = @(Get-NasArchiveDrift -Source $backup -Destination $archive); Check 'M15 no drift once the local files are restored' ($drift2.Count -eq 0) ($drift2 -join ",")

  # -------------------------------------------------------------- physical: spellings
  Write-Host "== P: a slot spelling never reaches the archive pass (local stand-in)"
  foreach ($sp in $SlotSpellings) {
    $threw = $false; try { Resolve-NasArchiveRoot -NasUncRoot $nasPortal -NasArchiveRoot ((Join-Path $root 'nas\backups\ai-stack') + $sp) | Out-Null } catch { $threw = $true }
    Check "P1 stand-in spelling refused [...ai-stack$sp]" $threw
  }

  # ------------------------------------------------------------ copy script
  Write-Host "== C: copy-archives-to-nas.ps1 against a fresh stand-in archive root"
  $dest = Join-Path $root 'nas2\backups\ai-stack\archive'
  function RunCopy([string[]]$extra) {
    $ErrorActionPreference = 'Continue'   # the child's stderr must not abort the test
    $out = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyScript -Destination $dest -Source $backup @extra 2>&1 | Out-String
    return @{ Out = $out; Rc = $LASTEXITCODE }
  }
  $r = RunCopy @('-VerifyOnly')
  Check 'C1 -VerifyOnly on an empty NAS: ABSENT, exit 1, nothing written' (($r.Rc -eq 1) -and ($r.Out -match 'ABSENT') -and -not (Test-Path -LiteralPath $dest)) "rc=$($r.Rc)"
  $r = RunCopy @()
  $verified = ([regex]::Matches($r.Out, 'VERIFIED \(copied\)')).Count
  Check 'C2 first run copies all 6 files of the two dirs, each VERIFIED, exit 0' (($r.Rc -eq 0) -and ($verified -eq 6)) "rc=$($r.Rc) verified=$verified`n$($r.Out)"
  Check 'C3 models\ is not in the default set' (-not (Test-Path -LiteralPath "$dest\models"))
  Check 'C4 no *.cf-partial left behind' (-not (Get-ChildItem -LiteralPath $dest -Recurse -Filter '*.cf-partial'))
  $f1 = Get-Item -LiteralPath "$dest\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar"
  Check 'C5 timestamp preserved' ($f1.LastWriteTimeUtc -eq (Get-Item -LiteralPath "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar").LastWriteTimeUtc)
  $destState = TreeHashes $dest
  $r = RunCopy @()
  Check 'C6 second run: all VERIFIED (already present), exit 0, NAS unchanged' (($r.Rc -eq 0) -and (([regex]::Matches($r.Out, 'already present')).Count -eq 6) -and (SameTree $destState (TreeHashes $dest))) "rc=$($r.Rc)"
  $r = RunCopy @('-VerifyOnly')
  Check 'C7 -VerifyOnly after the copy: exit 0' ($r.Rc -eq 0) "rc=$($r.Rc)"

  $listArgs = @(Get-NasArchiveCopyArgs -Source $backup -Destination $dest -DryRun) + @('/NJH', '/NJS', '/NC', '/NS', '/NP', '/FP')
  $would = @(& robocopy.exe @listArgs | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -like "$backup\*" })
  $wouldRel = @($would | ForEach-Object { $_.Substring($backup.Length) } | Sort-Object)
  Check 'C8a after the copy, the weekly pass would send ONLY models\ and *.sh - none of the 6 copied files' (($wouldRel.Count -eq 2) -and -not ($wouldRel | Where-Object { $_ -match 'orphan-volumes|nas-archive' })) ($wouldRel -join ', ')
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $dest -LogFile $log)
  $after = TreeHashes $dest
  $copiedByScript = @{}; $extra = @()
  foreach ($k in $after.Keys) { if ($k -match '^\\models\\|\.sh$') { $extra += $k } else { $copiedByScript[$k] = $after[$k] } }
  Check 'C8 the weekly archive pass then sends only what the copy did not cover and leaves the copied files as they were' (($rc -lt 8) -and ($extra.Count -eq 2) -and (SameTree $destState $copiedByScript)) "rc=$rc extra=$($extra -join ',')"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $dest -LogFile $log)
  Check 'C9 ... and the run after that is a no-op (rc=0): the next scheduled run keeps them' ($rc -eq 0) "rc=$rc"

  $victim = "$dest\nas-archive-may2025-owui\open-webui.tar"
  $fs = [System.IO.File]::Open($victim, 'Open', 'ReadWrite'); $fs.WriteByte(0x41); $fs.Close()
  $tampered = Sha256Of $victim
  $r = RunCopy @()
  Check 'C10 a differing NAS file is reported MISMATCH, exit 1, and left untouched' (($r.Rc -eq 1) -and ($r.Out -match 'MISMATCH\s+nas-archive-may2025-owui\\open-webui\.tar') -and ((Sha256Of $victim) -eq $tampered)) "rc=$($r.Rc)"

  $dest3 = Join-Path $root 'nas3\archive'
  $rot = "$backup\nas-archive-may2025-owui\may2025-chats-export.json"
  $orig = [System.IO.File]::ReadAllBytes($rot); $rotTime = (Get-Item -LiteralPath $rot).LastWriteTimeUtc
  $b = [byte[]]$orig.Clone(); $b[0] = $b[0] -bxor 0xFF; [System.IO.File]::WriteAllBytes($rot, $b)
  $saveDest = $dest; $dest = $dest3
  $r = RunCopy @()
  $dest = $saveDest
  [System.IO.File]::WriteAllBytes($rot, $orig); [System.IO.File]::SetLastWriteTimeUtc($rot, $rotTime)
  Check 'C11 a local file that no longer matches SHA256SUMS is FAIL LOCAL and not copied' (($r.Rc -eq 1) -and ($r.Out -match 'FAIL LOCAL\s+nas-archive-may2025-owui\\may2025-chats-export\.json') -and -not (Test-Path -LiteralPath "$dest3\nas-archive-may2025-owui\may2025-chats-export.json")) "rc=$($r.Rc)"

  $dest4 = Join-Path $root 'nas4\archive'
  New-Item -ItemType Directory -Force -Path "$dest4\orphan-volumes-2026-09-13" | Out-Null
  Set-Content -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial" -Value 'half' -Encoding ascii
  $saveDest = $dest; $dest = $dest4
  $r = RunCopy @('-Dirs', 'orphan-volumes-2026-09-13')
  $dest = $saveDest
  Check 'C12 a leftover .cf-partial is overwritten and the file lands VERIFIED' (($r.Rc -eq 0) -and ((Sha256Of "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar") -eq (Sha256Of "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar")) -and -not (Test-Path -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial")) "rc=$($r.Rc)"

  foreach ($sp in (@('\portal\slot-A\x') + $SlotSpellings)) {
    $bad = (Join-Path $root 'nas5\backups\ai-stack') + $sp
    $ErrorActionPreference = 'Continue'
    $null = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyScript -Destination $bad -Source $backup 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Check "C13 destination refused (exit 2) [...ai-stack$sp]" ($code -eq 2) "rc=$code"
  }
  Check 'C13z no refused spelling wrote anything' (-not (Test-Path -LiteralPath (Join-Path $root 'nas5')))

  # SHA256SUMS entry with no local file
  $ghostSrc = Join-Path $root 'ghostsrc'
  New-Item -ItemType Directory -Force -Path "$ghostSrc\g" | Out-Null
  Blob "$ghostSrc\g\real.tar" 1000
  [System.IO.File]::WriteAllLines("$ghostSrc\g\SHA256SUMS", [string[]]@("$(Sha256Of "$ghostSrc\g\real.tar") *real.tar", (('0' * 64) + ' *ghost.tar')))
  $ErrorActionPreference = 'Continue'
  $o = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyScript -Destination (Join-Path $root 'nas6\archive') -Source $ghostSrc -Dirs g 2>&1 | Out-String
  $code = $LASTEXITCODE
  $ErrorActionPreference = 'Stop'
  Check 'C14 a SHA256SUMS entry with no local file is MISSING LOCAL, exit 1 (the present file still VERIFIED)' (($code -eq 1) -and ($o -match 'MISSING LOCAL\s+g\\ghost\.tar') -and ($o -match 'real\.tar.*VERIFIED')) "rc=$code"

  # ------------------------------------------------------------ J/K: stubbed runs
  $e2e = Join-Path $root 'e2e'
  $eproj = Join-Path $e2e 'proj'
  $share = Join-Path $e2e 'share'
  New-Item -ItemType Directory -Force -Path "$eproj\scripts\backup", "$eproj\scripts\lib", "$eproj\logs", $share | Out-Null
  Copy-Item -LiteralPath $Script -Destination "$eproj\scripts\backup\backup-to-nas.ps1"
  Copy-Item -LiteralPath $Lib -Destination "$eproj\scripts\backup\nas-sync-lib.ps1"
  Copy-Item -LiteralPath $CopyScript -Destination "$eproj\scripts\backup\copy-archives-to-nas.ps1"
  Copy-Item -LiteralPath $backups -Destination "$eproj\backups" -Recurse
  Copy-Item -LiteralPath $backup -Destination "$eproj\backup" -Recurse
  $fakePw = 'cfnas-pw-NOT-REAL-4q7z'
  [System.IO.File]::WriteAllLines("$eproj\.env", [string[]]@('NAS_BACKUP_USER=cfnas-user', "NAS_BACKUP_PASSWORD=$fakePw"))
  [System.IO.File]::WriteAllText("$eproj\scripts\lib\portal-alerter-client.ps1",
    'function Send-PortalAlert { param($Severity, $Event, $LogLine) Add-Content -LiteralPath $env:CFNAS_TRACE -Value "ALERT|$Event|$LogLine"; return @{ Ok = $true; Reason = ''stub'' } }')
  $harness = Join-Path $e2e 'harness.ps1'
  [System.IO.File]::WriteAllText($harness, @'
param([string]$Target)
$ErrorActionPreference = 'Continue'
# Get-FileHash is a SCRIPT function of this module in PS 5.1: import it first, or its
# auto-import on first use would replace the stub below.
Import-Module Microsoft.PowerShell.Utility, Microsoft.PowerShell.Management
$global:CfPrefix = '\\192.0.2.77\backups'
function global:Map-CfPath([string]$p) {
  if (-not $p) { return $p }
  if ($p.ToLowerInvariant().StartsWith($global:CfPrefix.ToLowerInvariant())) { return $env:CFNAS_SHARE + $p.Substring($global:CfPrefix.Length) }
  if ($p.StartsWith('\\') -or $p.StartsWith('//')) { Add-Content -LiteralPath $env:CFNAS_TRACE -Value "UNMAPPED-UNC|$p"; return (Join-Path $env:CFNAS_SHARE '__refused_unc__') }
  return $p
}
function global:net.exe { Add-Content -LiteralPath $env:CFNAS_TRACE -Value ('NET|' + ($args -join ' ')); $global:LASTEXITCODE = [int]$env:CFNAS_RC_NET; 'The command completed successfully.' }
function global:msg.exe { Add-Content -LiteralPath $env:CFNAS_TRACE -Value 'MSGEXE' }
function global:Resolve-DnsName { param($Name, $Type, $ErrorAction) [pscustomobject]@{ IPAddress = '192.0.2.77' } }
function global:robocopy.exe {
  $k = if ($args -contains '/MIR') { 'MIR' } else { 'ARC' }
  Add-Content -LiteralPath $env:CFNAS_TRACE -Value ("ROBO-$k|" + ($args -join ' '))
  $mapped = @($args | ForEach-Object { Map-CfPath $_ })
  $forced = [Environment]::GetEnvironmentVariable("CFNAS_RC_$k")
  if ($forced) { $global:LASTEXITCODE = [int]$forced; return }
  & (Join-Path $env:SystemRoot 'System32\Robocopy.exe') @mapped | Out-Null
}
function global:Get-ChildItem {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [string]$Filter, [switch]$Recurse, [switch]$File, [switch]$Directory, [switch]$Force)
  if ($PSBoundParameters.ContainsKey('Path')) { $PSBoundParameters['Path'] = @($Path | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('LiteralPath')) { $PSBoundParameters['LiteralPath'] = @($LiteralPath | ForEach-Object { Map-CfPath $_ }) }
  Microsoft.PowerShell.Management\Get-ChildItem @PSBoundParameters
}
function global:Get-Item {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [switch]$Force)
  if ($PSBoundParameters.ContainsKey('Path')) { $PSBoundParameters['Path'] = @($Path | ForEach-Object { Map-CfPath $_ }) }
  if ($PSBoundParameters.ContainsKey('LiteralPath')) { $PSBoundParameters['LiteralPath'] = @($LiteralPath | ForEach-Object { Map-CfPath $_ }) }
  Microsoft.PowerShell.Management\Get-Item @PSBoundParameters
}
function global:Test-Path {
  [CmdletBinding()] param([Parameter(Position = 0)][string[]]$Path, [string[]]$LiteralPath, [Microsoft.PowerShell.Commands.TestPathType]$PathType = 'Any')
  $p = if ($PSBoundParameters.ContainsKey('LiteralPath')) { $LiteralPath[0] } else { $Path[0] }
  if ($env:CFNAS_SHARE_REACHABLE -and $p -ieq $global:CfPrefix) { return ($env:CFNAS_SHARE_REACHABLE -eq '1') }
  $m = Map-CfPath $p
  Microsoft.PowerShell.Management\Test-Path -LiteralPath $m -PathType $PathType
}
function global:Get-FileHash {
  [CmdletBinding()] param([string]$LiteralPath, [string]$Path, [string]$Algorithm = 'SHA256')
  $p = if ($LiteralPath) { $LiteralPath } else { $Path }
  $p = Map-CfPath $p
  if ($p.EndsWith('.cf-partial')) {
    if ($env:CFNAS_BAD_PARTIAL -eq '1') { return [pscustomobject]@{ Hash = ('F' * 64); Path = $p } }
    if ($env:CFNAS_RACE -eq '1') { [System.IO.File]::WriteAllText($p.Substring(0, $p.Length - 11), 'concurrent') }
  }
  Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $p -Algorithm $Algorithm
}
$h = @{}
foreach ($kv in ($env:CFNAS_PARAMS -split '\|')) {
  if (-not $kv) { continue }
  $p = $kv -split '=', 2
  if ($p.Count -eq 2) { $h[$p[0]] = $p[1] } else { $h[$p[0]] = $true }
}
& $Target @h
"CFEXIT=$LASTEXITCODE"
'@)

  function RunStubbed([string]$Target, [string]$Params, [hashtable]$EnvSet = @{}) {
    $trace = Join-Path $e2e ('trace-' + [guid]::NewGuid().ToString('N').Substring(0, 6) + '.txt')
    Set-Content -LiteralPath $trace -Value '' -Encoding ascii
    Get-ChildItem -LiteralPath "$eproj\logs" -File | Remove-Item -Force
    $saved = @{}
    $vars = @{ CFNAS_TRACE = $trace; CFNAS_SHARE = $share; CFNAS_PARAMS = $Params; CFNAS_RC_NET = '0'; CFNAS_RC_MIR = ''; CFNAS_RC_ARC = ''
      CFNAS_BAD_PARTIAL = ''; CFNAS_RACE = ''; CFNAS_SHARE_REACHABLE = ''; DOCKER_HOST = 'tcp://127.0.0.1:1' }
    foreach ($k in $EnvSet.Keys) { $vars[$k] = $EnvSet[$k] }
    foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
    $ErrorActionPreference = 'Continue'
    $out = & $ps -NoProfile -ExecutionPolicy Bypass -File $harness -Target $Target 2>&1 | Out-String
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    $lf = Get-ChildItem -LiteralPath "$eproj\logs" -File -Filter 'nas-sync-*.log' | Select-Object -First 1
    $logBody = if ($lf) { [System.IO.File]::ReadAllText($lf.FullName) } else { '' }
    $code = if ($out -match 'CFEXIT=(-?\d+)') { [int]$Matches[1] } else { -999 }
    return @{ Out = $out; Code = $code; Trace = @(Get-Content -LiteralPath $trace | Where-Object { $_ }); Log = $logBody }
  }
  $job = "$eproj\scripts\backup\backup-to-nas.ps1"
  $unc = '\\cfnas-fake.invalid\backups\ai-stack\portal'
  $slot = Get-NasSlotName
  $nasArc = Join-Path $share 'ai-stack\archive'
  function NoLeak($r) { (-not ($r.Trace | Where-Object { $_ -like 'UNMAPPED-UNC*' })) -and ($r.Out -notmatch [regex]::Escape($fakePw)) -and ($r.Log -notmatch [regex]::Escape($fakePw)) }

  Write-Host "== J: backup-to-nas.ps1 end to end (stubbed SMB, local stand-in share)"
  $r = RunStubbed $job "NasUncRoot=$unc"
  $kinds = @($r.Trace | ForEach-Object { ($_ -split '\|')[0] })
  Check 'J1 happy run: exit 0 and the completion marker' (($r.Code -eq 0) -and ($r.Log -match '=== NAS sync complete ===')) "code=$($r.Code)"
  Check 'J1b call order: net use /delete, net use, slot mirror, archive pass, net use /delete - and no alert' ((($kinds -join ',') -eq 'NET,NET,ROBO-MIR,ROBO-ARC,NET')) ($kinds -join ',')
  $arcCall = @($r.Trace | Where-Object { $_ -like 'ROBO-ARC|*' }) -join ''
  Check 'J1c the archive pass goes to \\<ip>\backups\ai-stack\archive with /XC /XN /XO' (($arcCall -like '*\\192.0.2.77\backups\ai-stack\archive /E /XC /XN /XO /XX*')) $arcCall
  Check 'J1d every ./backup file is on the stand-in share, the slot got ./backups' ((SameTree (TreeHashes "$eproj\backup") (TreeHashes $nasArc)) -and (SameTree (TreeHashes "$eproj\backups") (TreeHashes (Join-Path $share "ai-stack\portal\$slot"))))
  Check 'J1e no UNC path escaped the stubs; the password appears in neither output nor log' (NoLeak $r)
  Check 'J1f the slot integrity check ran against the stand-in' ($r.Log -match 'integrity check OK')

  $arcState = TreeHashes $nasArc
  $jt = "$eproj\backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  $jOrig = [System.IO.File]::ReadAllBytes($jt); $jTime = (Get-Item -LiteralPath $jt).LastWriteTimeUtc
  [System.IO.File]::WriteAllBytes($jt, [byte[]]@())
  Add-Content -LiteralPath "$eproj\backup\models\models-export.json" -Value 'changed' -Encoding ascii
  $r = RunStubbed $job "NasUncRoot=$unc"
  Check 'J2 after a local truncation + change: exit 0, marker, NAS archive tree unchanged' (($r.Code -eq 0) -and ($r.Log -match '=== NAS sync complete ===') -and (SameTree $arcState (TreeHashes $nasArc))) "code=$($r.Code)"
  Check 'J2b both files logged as WARN "differs from its NAS copy - NAS copy KEPT"' ((([regex]::Matches($r.Log, '\[WARN\] archive: local .* differs from its NAS copy')).Count -eq 2) -and ($r.Log -match 'ai-stack_llm-gateway-db-data\.tar differs') -and ($r.Log -match 'models-export\.json differs'))
  [System.IO.File]::WriteAllBytes($jt, $jOrig); [System.IO.File]::SetLastWriteTimeUtc($jt, $jTime)

  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_ARC = '8' }
  Check 'J3 archive pass rc 8: exit 2, NO completion marker' (($r.Code -eq 2) -and ($r.Log -notmatch '=== NAS sync complete ===')) "code=$($r.Code)"
  Check 'J3b ... an alert naming the archive pass, the slot integrity check still ran, the session torn down' ((@($r.Trace | Where-Object { $_ -like 'ALERT|nas-backup.failure|*archive pass robocopy exit 8*' }).Count -eq 1) -and ($r.Log -match 'verifying a sample') -and ($r.Trace[-2] -like 'NET|use*/delete*' -or $r.Trace[-1] -like 'NET|use*/delete*'))
  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_ARC = '16' }
  Check 'J3c archive pass rc 16 is a failure too (exit 2, alert)' (($r.Code -eq 2) -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -ge 1))

  $shareBefore = TreeHashes $share
  $r = RunStubbed $job "NasUncRoot=$unc|DryRun" @{ CFNAS_RC_ARC = '8' }
  Check 'J4 dry run with a failing archive pass: exit 2, no DRY RUN complete, alert' (($r.Code -eq 2) -and ($r.Log -notmatch 'DRY RUN\) complete') -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -eq 1)) "code=$($r.Code)"
  $r = RunStubbed $job "NasUncRoot=$unc|DryRun"
  Check 'J5 dry run ok: exit 0, "(DRY RUN) complete", both robocopy calls carry /L, the share unchanged' (($r.Code -eq 0) -and ($r.Log -match '=== NAS sync \(DRY RUN\) complete ===') -and (@($r.Trace | Where-Object { $_ -like 'ROBO-*' -and $_ -match ' /L ' }).Count -eq 2) -and (SameTree $shareBefore (TreeHashes $share))) "code=$($r.Code)"

  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_MIR = '8' }
  Check 'J6 slot mirror rc 8: exit 2, no archive pass, alert' (($r.Code -eq 2) -and -not ($r.Trace | Where-Object { $_ -like 'ROBO-ARC*' }) -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*' }).Count -ge 1)) "code=$($r.Code)"

  foreach ($sp in $SlotSpellings) {
    $r = RunStubbed $job ("NasUncRoot=$unc|NasArchiveRoot=\\cfnas-fake.invalid\backups\ai-stack" + $sp)
    Check "J7 archive root [...ai-stack$sp]: exit 1, alert, no net use, no robocopy" (($r.Code -eq 1) -and (@($r.Trace | Where-Object { $_ -like 'ALERT|*archive root rejected*' }).Count -eq 1) -and -not ($r.Trace | Where-Object { $_ -like 'NET|*' -or $_ -like 'ROBO-*' })) "code=$($r.Code) trace=$($r.Trace -join ' ; ')"
  }

  Rename-Item -LiteralPath "$eproj\backup" -NewName 'backup-hidden'
  $r = RunStubbed $job "NasUncRoot=$unc"
  Rename-Item -LiteralPath "$eproj\backup-hidden" -NewName 'backup'
  Check 'J8 no ./backup: WARN, archive pass skipped, run still complete' (($r.Code -eq 0) -and ($r.Log -match '\[WARN\] archive source .* does not exist') -and -not ($r.Trace | Where-Object { $_ -like 'ROBO-ARC*' }) -and ($r.Log -match '=== NAS sync complete ==='))
  $r = RunStubbed $job "NasUncRoot=$unc|NoArchive"
  Check 'J9 -NoArchive: no archive pass, run complete' (($r.Code -eq 0) -and -not ($r.Trace | Where-Object { $_ -like 'ROBO-ARC*' }) -and ($r.Log -match '=== NAS sync complete ==='))
  $r = RunStubbed $job "NasUncRoot=$unc" @{ CFNAS_RC_NET = '2' }
  Check 'J10 net use fails: exit 1, no robocopy, password not leaked' (($r.Code -eq 1) -and -not ($r.Trace | Where-Object { $_ -like 'ROBO-*' }) -and (NoLeak $r))

  Write-Host "== K: copy-archives-to-nas.ps1 under stubs"
  $cp = "$eproj\scripts\backup\copy-archives-to-nas.ps1"
  $kd = Join-Path $e2e 'kdest'
  $r = RunStubbed $cp "Destination=$kd|Source=$eproj\backup|Dirs=orphan-volumes-2026-09-13" @{ CFNAS_BAD_PARTIAL = '1' }
  $kf = "$kd\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  Check 'K1 a copy whose hash differs is FAIL COPY, exit 1, left as .cf-partial, never renamed into place' (($r.Code -eq 1) -and ($r.Out -match 'FAIL COPY\s+orphan-volumes-2026-09-13\\ai-stack_llm-gateway-db-data\.tar') -and -not (Test-Path -LiteralPath $kf) -and (Test-Path -LiteralPath "$kf.cf-partial")) "code=$($r.Code) out=$($r.Out)"
  $kd2 = Join-Path $e2e 'kdest2'
  $r = RunStubbed $cp "Destination=$kd2|Source=$eproj\backup|Dirs=orphan-volumes-2026-09-13" @{ CFNAS_RACE = '1' }
  $kf2 = "$kd2\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  Check 'K2 a final file that appears during the copy is never overwritten (FAIL COPY, exit 1)' (($r.Code -eq 1) -and ($r.Out -match 'FAIL COPY\s+.*could not rename') -and ([System.IO.File]::ReadAllText($kf2) -eq 'concurrent')) "code=$($r.Code)"
  New-Item -ItemType Directory -Force -Path "$e2e\emptysrc\none" | Out-Null
  $r = RunStubbed $cp "Destination=\\192.0.2.77\backups\ai-stack\archive|Source=$e2e\emptysrc|Dirs=none|Connect" @{ CFNAS_SHARE_REACHABLE = '0' }
  $nets = @($r.Trace | Where-Object { $_ -like 'NET|*' })
  Check 'K3 -Connect, share not reachable: net use with the .env user, then /delete of the same share' (($r.Code -eq 0) -and ($nets.Count -eq 2) -and ($nets[0] -eq "NET|use \\192.0.2.77\backups $fakePw /user:cfnas-user /persistent:no") -and ($nets[1] -eq 'NET|use \\192.0.2.77\backups /delete /yes')) "code=$($r.Code) nets=$($nets -join ' ; ')"
  Check 'K3b -Connect never prints the password' (NoLeak $r)
  $r = RunStubbed $cp "Destination=\\192.0.2.77\backups\ai-stack\archive|Source=$e2e\emptysrc|Dirs=none|Connect" @{ CFNAS_SHARE_REACHABLE = '1' }
  Check 'K4 -Connect with the share already reachable: no net use at all' (($r.Code -eq 0) -and -not ($r.Trace | Where-Object { $_ -like 'NET|*' }))
  $r = RunStubbed $cp "Destination=\\192.0.2.77\backups\ai-stack\archive|Source=$e2e\emptysrc|Dirs=none|Connect|VerifyOnly" @{ CFNAS_SHARE_REACHABLE = '0' }
  Check 'K5 -Connect -VerifyOnly: no net use (verify never opens a session)' (-not ($r.Trace | Where-Object { $_ -like 'NET|*' }))

  Write-Host "== Z: sources untouched"
  Check 'Z1 ./backup stand-in unchanged by every run above' (SameTree $srcBefore (TreeHashes $backup))
  Check 'Z2 ./backups stand-in unchanged' (SameTree $backupsBefore (TreeHashes $backups))
} catch {
  Check 'X the stand-in run finished without an unexpected error' $false ($_.Exception.Message + ' @ ' + $_.InvocationInfo.PositionMessage)
} finally {
  if ($Keep) { Write-Host "kept: $root" } else {
    try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue } catch { }
    # A mutant that lets `slot-A ` / `slot-B...` through makes robocopy create those
    # names, which only the \\?\ form can delete.
    if (Test-Path -LiteralPath $root) { cmd /c "rmdir /s /q `"\\?\$root`"" 2>&1 | Out-Null }
  }
}

Write-Host ("RESULT: passed {0}, failed {1}" -f $script:pass, $script:fail)
exit $script:fail
