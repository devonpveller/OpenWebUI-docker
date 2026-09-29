# scripts/backup/test-nas-sync.ps1
#
# Tests for the weekly NAS job's layout and the one-time archive copy, run against
# LOCAL stand-in directories only - no SMB, no NAS, no Docker. Every path it writes
# is under a fresh directory in %TEMP% that it removes at the end (-Keep leaves it).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup\test-nas-sync.ps1
#
# -Script / -Lib / -CopyScript point the tests at other copies (the base RED run
# swaps in the 0fb1c0c backup-to-nas.ps1). Exit = number of failed checks.
#
# What is proved, in robocopy's own semantics (the same argument arrays the job
# passes, built by nas-sync-lib.ps1):
#   - the slot mirror still purges extras inside its slot (it is still a mirror);
#   - the archive root that Resolve-NasArchiveRoot picks is outside both slots, and
#     two weeks of slot mirrors (A then B) leave every archive file byte-identical;
#   - the archive pass deletes nothing on the NAS even when the local file is gone;
#   - after copy-archives-to-nas.ps1 placed a file, the archive pass re-sends
#     nothing (exit 0 = "next scheduled run keeps them" without a recopy);
#   - the copy script: VERIFIED on first and second run, MISMATCH leaves the NAS
#     file untouched, a locally rotted file is not copied, -VerifyOnly writes
#     nothing, and the source tree is never modified.

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
Check 'S6 the archive dest follows the IP rewrite' ($text -match '\$archiveDest = \$archiveDest -replace')
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
foreach ($bad in @('\\nas\backups\ai-stack\portal\slot-A', '\\nas\backups\ai-stack\portal\SLOT-B\x', '\\nas\other\archive', 'D:\archive')) {
  $threw = $false; try { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups\ai-stack\portal' -NasArchiveRoot $bad | Out-Null } catch { $threw = $true }
  Check "R4 refuses archive root $bad" $threw
}
Check 'R5 an explicit root on the same share outside the slots is honoured' ((Try-Call { Resolve-NasArchiveRoot -NasUncRoot '\\nas\backups\ai-stack\portal' -NasArchiveRoot '\\nas\backups\cold\' }) -eq '\\nas\backups\cold')
$aa = Try-Call { Get-NasArchiveCopyArgs -Source 'C:\s' -Destination 'C:\d' -LogFile 'C:\l.log' }
Check 'R6 archive args: /E and /XX, no /MIR, no /PURGE, no /MOV(E)' (($aa -contains '/E') -and ($aa -contains '/XX') -and -not ($aa | Where-Object { $_ -match '^/(MIR|PURGE|MOV|MOVE)$' })) ($aa -join ' ')
$ma = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -LogFile 'C:\l.log' }
Check 'R7 slot args unchanged from 0fb1c0c: /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+ /NDL' (($ma -join ' ') -eq 'C:\s C:\d /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+:C:\l.log /NDL') ($ma -join ' ')
$md = Try-Call { Get-NasSlotMirrorArgs -Source 'C:\s' -Destination 'C:\d' -DryRun }
$ad = Try-Call { Get-NasArchiveCopyArgs -Source 'C:\s' -Destination 'C:\d' -DryRun }
Check 'R8 -DryRun adds /L to both' (($md -contains '/L') -and ($ad -contains '/L'))
Check 'R9 ISO week parity: 2026-09-27 (week 39) -> slot-B, 2026-10-04 (week 40) -> slot-A' (((Get-NasSlotName -Date ([datetime]'2026-09-27')) -eq 'slot-B') -and ((Get-NasSlotName -Date ([datetime]'2026-10-04')) -eq 'slot-A'))

# ---------------------------------------------------------------- robocopy against stand-ins
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("cf-nas-test-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$proj = Join-Path $root 'proj'
$backups = Join-Path $proj 'backups'
$backup = Join-Path $proj 'backup'
$nasPortal = Join-Path $root 'nas\backups\ai-stack\portal'
$log = Join-Path $root 'robocopy.log'
New-Item -ItemType Directory -Force -Path "$backups\openwebui", "$backup\orphan-volumes-2026-09-13", "$backup\nas-archive-may2025-owui", "$backup\models" | Out-Null
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

  # week 40: slot-A
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M1 slot-A mirror ok' ($rc -lt 8) "rc=$rc"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M2 archive pass copies ./backup (rc has bit 1)' (($rc -lt 8) -and ($rc -band 1)) "rc=$rc"
  $arcAfterFirst = TreeHashes $archive
  Check 'M3 archive on the stand-in NAS = ./backup exactly' (SameTree $srcBefore $arcAfterFirst) "src=$($srcBefore.Count) nas=$($arcAfterFirst.Count)"
  Check 'M4 archives did not land in any slot' (-not (Get-ChildItem -LiteralPath $nasPortal -Recurse -File | Where-Object { $_.Name -like '*.tar' }))

  # an extra in slot-A is purged by the next mirror -> it is still a mirror
  Set-Content -LiteralPath "$slotA\stray.txt" -Value 'x' -Encoding ascii
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M5 slot mirror still purges extras in its slot' ((-not (Test-Path -LiteralPath "$slotA\stray.txt")) -and ($rc -band 2)) "rc=$rc"

  # week 41: slot-B, then archive pass again; then week 42 slot-A again
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotB -LogFile $log)
  Check 'M6 slot-B mirror ok' ($rc -lt 8) "rc=$rc"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M7 an unchanged archive pass re-sends nothing (rc=0)' ($rc -eq 0) "rc=$rc"
  $rc = Rc (Get-NasSlotMirrorArgs -Source $backups -Destination $slotA -LogFile $log)
  Check 'M8 archive tree byte-identical after mirrors of both slots' (SameTree $arcAfterFirst (TreeHashes $archive))

  # a NAS-side extra in the archive root, and a local deletion: neither is purged
  Set-Content -LiteralPath "$archive\operator-note.txt" -Value 'kept' -Encoding ascii
  $gone = Join-Path $root 'moved-out.tar'
  Move-Item -LiteralPath "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar" -Destination $gone
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Move-Item -LiteralPath $gone -Destination "$backup\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar"
  Check 'M9 archive pass keeps a file deleted locally' ((Test-Path -LiteralPath "$archive\orphan-volumes-2026-09-13\ai-stack_llm-gateway-db-data.tar") -and ($rc -lt 8)) "rc=$rc"
  Check 'M10 archive pass keeps a NAS-side extra' (Test-Path -LiteralPath "$archive\operator-note.txt")

  # a changed local file is updated (new version replaces old; nothing else touched)
  Add-Content -LiteralPath "$backup\models\models-export.json" -Value 'more' -Encoding ascii
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $archive -LogFile $log)
  Check 'M11 a changed local file is updated on the NAS' ((Sha256Of "$archive\models\models-export.json") -eq (Sha256Of "$backup\models\models-export.json")) "rc=$rc"
  $srcBefore = TreeHashes $backup

  # ------------------------------------------------------------ copy script
  Write-Host "== C: copy-archives-to-nas.ps1 against a fresh stand-in archive root"
  $dest = Join-Path $root 'nas2\backups\ai-stack\archive'
  $ps = (Get-Process -Id $PID).Path
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

  # the weekly pass after the one-time copy: same size+time -> skipped. Ask robocopy
  # first (/L lists what it WOULD copy) - equal hashes alone cannot show a re-send.
  $listArgs = @(Get-NasArchiveCopyArgs -Source $backup -Destination $dest -DryRun) + @('/NJH', '/NJS', '/NC', '/NS', '/NP', '/FP')
  $would = @(& robocopy.exe @listArgs |
    ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -like "$backup\*" })
  $wouldRel = @($would | ForEach-Object { $_.Substring($backup.Length) } | Sort-Object)
  Check 'C8a after the copy, the weekly pass would send ONLY models\ and *.sh - none of the 6 copied files' (($wouldRel.Count -eq 2) -and -not ($wouldRel | Where-Object { $_ -match 'orphan-volumes|nas-archive' })) ($wouldRel -join ', ')
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $dest -LogFile $log)
  $after = TreeHashes $dest
  $copiedByScript = @{}; $extra = @()
  foreach ($k in $after.Keys) { if ($k -match '^\\models\\|\.sh$') { $extra += $k } else { $copiedByScript[$k] = $after[$k] } }
  Check 'C8 the weekly archive pass then sends only what the copy did not cover (models\, *.sh) and leaves the copied files as they were' (($rc -lt 8) -and ($extra.Count -eq 2) -and (SameTree $destState $copiedByScript)) "rc=$rc extra=$($extra -join ',')"
  $rc = Rc (Get-NasArchiveCopyArgs -Source $backup -Destination $dest -LogFile $log)
  Check 'C9 ... and the run after that is a no-op (rc=0): the next scheduled run keeps them' ($rc -eq 0) "rc=$rc"

  # tamper the NAS copy -> MISMATCH, left untouched
  $victim = "$dest\nas-archive-may2025-owui\open-webui.tar"
  $fs = [System.IO.File]::Open($victim, 'Open', 'ReadWrite'); $fs.WriteByte(0x41); $fs.Close()
  $tampered = Sha256Of $victim
  $r = RunCopy @()
  Check 'C10 a differing NAS file is reported MISMATCH, exit 1, and left untouched' (($r.Rc -eq 1) -and ($r.Out -match 'MISMATCH\s+nas-archive-may2025-owui\\open-webui\.tar') -and ((Sha256Of $victim) -eq $tampered)) "rc=$($r.Rc)"

  # local rot against SHA256SUMS -> not copied
  $dest3 = Join-Path $root 'nas3\archive'
  $rot = "$backup\nas-archive-may2025-owui\may2025-chats-export.json"
  $orig = [System.IO.File]::ReadAllBytes($rot)
  $b = [byte[]]$orig.Clone(); $b[0] = $b[0] -bxor 0xFF; [System.IO.File]::WriteAllBytes($rot, $b)
  $saveDest = $dest; $dest = $dest3
  $r = RunCopy @()
  $dest = $saveDest
  [System.IO.File]::WriteAllBytes($rot, $orig)
  Check 'C11 a local file that no longer matches SHA256SUMS is FAIL LOCAL and not copied' (($r.Rc -eq 1) -and ($r.Out -match 'FAIL LOCAL\s+nas-archive-may2025-owui\\may2025-chats-export\.json') -and -not (Test-Path -LiteralPath "$dest3\nas-archive-may2025-owui\may2025-chats-export.json")) "rc=$($r.Rc)"

  # a leftover partial from a killed run is replaced, then renamed
  $dest4 = Join-Path $root 'nas4\archive'
  New-Item -ItemType Directory -Force -Path "$dest4\orphan-volumes-2026-09-13" | Out-Null
  Set-Content -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial" -Value 'half' -Encoding ascii
  $saveDest = $dest; $dest = $dest4
  $r = RunCopy @('-Dirs', 'orphan-volumes-2026-09-13')
  $dest = $saveDest
  Check 'C12 a leftover .cf-partial is overwritten and the file lands VERIFIED' (($r.Rc -eq 0) -and ((Sha256Of "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar") -eq (Sha256Of "$backup\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar")) -and -not (Test-Path -LiteralPath "$dest4\orphan-volumes-2026-09-13\ai-stack_openwebui-data.tar.cf-partial")) "rc=$($r.Rc)"

  $ErrorActionPreference = 'Continue'
  $r = & $ps -NoProfile -ExecutionPolicy Bypass -File $CopyScript -Destination "$root\nas\backups\ai-stack\portal\slot-A\x" -Source $backup 2>&1 | Out-String
  $ErrorActionPreference = 'Stop'
  Check 'C13 a destination inside a slot is refused (exit 2)' ($LASTEXITCODE -eq 2)

  Write-Host "== Z: sources untouched"
  Check 'Z1 ./backup stand-in unchanged by every run above' (SameTree $srcBefore (TreeHashes $backup))
  Check 'Z2 ./backups stand-in unchanged' (SameTree $backupsBefore (TreeHashes $backups))
} catch {
  Check 'X the stand-in run finished without an unexpected error' $false $_.Exception.Message
} finally {
  if ($Keep) { Write-Host "kept: $root" } else { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ("RESULT: passed {0}, failed {1}" -f $script:pass, $script:fail)
exit $script:fail
